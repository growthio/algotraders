# -*- encoding: utf-8 -*-

"""
Session Assembly and the Non Interactive Login Entry Point
------------------------------------------------------------

:func:`initialize` is the one call a host application makes to obtain a
working broker session. It replaces the original design, where
authentication blocked on ``input()`` waiting for an OAuth code pasted
into a console. A FastAPI worker has no console and nobody to type into
it, so that path could never have run server side.

The replacement inverts control. When no usable session exists,
:func:`initialize` does not prompt and does not wait; it raises
:class:`~algotraders.errors.AuthorizationRequired` carrying the broker's
login URL. The host turns that into an HTTP 409, the frontend redirects
the user, the broker calls the host's own registered callback route, and
the host calls :func:`initialize` again with ``authCode`` set. The
library never owns a browser, a prompt or an HTTP listener.

Resolution proceeds in a fixed order, and every step is skipped as soon
as a usable token is in hand:

    1. read the stored bundle for this broker and client;
    2. if it is live, use it;
    3. if it is expired but refreshable, refresh and persist;
    4. if an ``authCode`` was supplied, exchange it and persist;
    5. otherwise raise ``AuthorizationRequired`` with a login URL.

Step three is what makes an unattended Fyers deployment possible and
step five is what makes Kite honest about needing a human each morning.

**Example Usage**

Called from a FastAPI startup hook or an ingestion job.

.. code-block:: python

    import algotraders

    session = await algotraders.initialize(
        broker = "fyers", maxConcurrency = 5
    )

    candles = await session.api.fetchCandles(instrument, timeframe, span)

Handling the login redirect in the host.

.. code-block:: python

    try:
        session = await algotraders.initialize(broker = "fyers")
    except algotraders.errors.AuthorizationRequired as challenge:
        return RedirectResponse(challenge.loginURL, status_code = 409)
"""

import asyncio
import inspect
import datetime as dt

from typing import Any, Callable, Dict, FrozenSet, Optional, Union

from algotraders.auth.store import (
    BaseTokenStore, FileTokenStore
)
from algotraders.config import (
    BaseBrokerCredentials, credentialsFor, runtimeConfig
)
from algotraders.core.clock import BaseClock, SystemClock
from algotraders.core.executor import (
    DEFAULT_MAX_CONCURRENCY, BlockingCaller
)
from algotraders.core.ratelimit import RateLimitPolicy
from algotraders.core.retry import RetryPolicy
from algotraders.errors import (
    AuthorizationRequired, MissingDependency, TokenExpiredError
)
from algotraders.instruments.cache import InstrumentCache
from algotraders.logger import getLogger, maskSecret
from algotraders.models.auth import SessionStatus, TokenBundle
from algotraders.models.enums import Capability

logger = getLogger(__name__)


class BrokerSession:
    """
    A live, authenticated connection to one broker.

    Holds the credentials, the current token, the authentication object
    and whatever API, resolver and streaming surfaces the broker
    implementation provides. Everything a caller needs hangs off this.

    :type  brokerName: str
    :param brokerName: Registry name of the broker.

    :type  auth: Any
    :param auth: The broker's authentication object, used for refresh
        and logout.

    :type  api: Any
    :param api: The broker's data and trading surface.

    :type  resolver: Any
    :param resolver: The broker's instrument resolver.

    :type  bundle: TokenBundle
    :param bundle: The session's current token.
    """

    def __init__(
        self, brokerName : str, credentials : BaseBrokerCredentials,
        auth : Any, bundle : TokenBundle,
        api : Any = None, resolver : Any = None,
        caller : Optional[BlockingCaller] = None,
        clock : Optional[BaseClock] = None,
        tokenStore : Optional[BaseTokenStore] = None,
        capabilities : FrozenSet[Capability] = frozenset()
    ) -> None:
        self.brokerName = brokerName
        self.credentials = credentials
        self.auth = auth
        self.bundle = bundle
        self.api = api
        self.resolver = resolver
        self.caller = caller

        # ? built lazily by stream(), because opening a socket is not
        # something initialize() should do on the caller's behalf
        self.streamFactory : Any = None
        self.streamer : Any = None
        self.clock = clock or SystemClock()
        self.tokenStore = tokenStore
        self.capabilities = frozenset(capabilities)


    @property
    def clientId(self) -> str:
        """
        Broker client id this session authenticates.
        """

        return self.bundle.clientId


    @property
    def tokenExpiresAt(self) -> Optional[dt.datetime]:
        """
        When the current token stops working, if known.
        """

        return self.bundle.expiresAt


    @property
    def isAuthenticated(self) -> bool:
        """
        Whether the held token is still believed to be valid.

        A local answer only. The broker remains the authority, and a
        rejected call maps to
        :class:`~algotraders.errors.TokenExpiredError` regardless of
        what this reports.
        """

        return not self.bundle.isExpired(self.clock.now())


    def supports(self, capability : Capability) -> bool:
        """
        Whether the broker implementation provides a given capability.

        Lets a host ask before calling, rather than calling and handling
        the resulting failure.
        """

        return capability in self.capabilities


    def status(self) -> SessionStatus:
        """
        Safe summary suitable for returning to a frontend.

        Carries no token, and the client id is masked, so it needs no
        further filtering at the API boundary.

        :rtype:   SessionStatus
        :returns: The session's current state.
        """

        now = self.clock.now()

        return SessionStatus(
            brokerName = self.brokerName,
            clientId = maskSecret(self.bundle.clientId),
            isAuthenticated = self.isAuthenticated,
            expiresAt = self.bundle.expiresAt,
            secondsRemaining = self.bundle.secondsRemaining(now),
            canRefresh = self.bundle.canRefresh(now),
            capabilities = sorted(
                capability.value for capability in self.capabilities
            )
        )


    async def refreshIfNeeded(self) -> bool:
        """
        Renew the token when it has expired and renewal is possible.

        Safe to call before any batch of work. It does nothing when the
        token is still live, so a caller need not track expiry itself.

        :raises TokenExpiredError: The token has expired and cannot be
            refreshed without an interactive login.

        :rtype:   bool
        :returns: ``True`` when a refresh actually happened.
        """

        now = self.clock.now()

        if not self.bundle.isExpired(now):
            return False

        if not self.bundle.canRefresh(now):
            raise TokenExpiredError(
                f"{self.brokerName} session expired and cannot be "
                "refreshed; an interactive login is required"
            )

        logger.info("refreshing expired %s session", self.brokerName)

        self.rebind(await self.auth.refresh(self.bundle))

        if self.tokenStore is not None:
            self.tokenStore.write(self.bundle)

        return True


    def rebind(self, bundle : TokenBundle) -> Any:
        """
        Adopt a new token across every surface holding the old one.

        ! This is not bookkeeping. ``buildSessionManager`` bakes the
        access token into the vendor client at construction, so updating
        ``self.bundle`` alone leaves the API and the resolver calling the
        broker with the token that just expired. A refresh would appear
        to succeed, ``status()`` would report a healthy session, and
        every subsequent request would fail authentication until a human
        noticed - precisely at the daily rollover, when unattended
        operation is the whole point.

        :type  bundle: TokenBundle
        :param bundle: The newly issued token.

        :rtype:   Any
        :returns: The rebuilt vendor client.
        """

        self.bundle = bundle
        manager = self.auth.buildSessionManager(bundle)

        for surface in (self.api, self.resolver, self.streamer):
            if surface is not None and hasattr(surface, "sessionManager"):
                surface.sessionManager = manager

        # ? the socket authenticates with the raw token rather than the
        # client object, so it needs the new value separately
        if self.streamer is not None and hasattr(self.streamer, "accessToken"):
            self.streamer.accessToken = bundle.accessToken.get_secret_value()

        return manager


    async def stream(
        self, sink : Any = None, autoStart : bool = True, **kwargs : Any
    ) -> Any:
        """
        Open a live market data stream for this session.

        Built on first use rather than during ``initialize``, because
        opening a websocket is not something a caller implicitly asked
        for by authenticating. Repeated calls return the same stream.

        :type  sink: Any
        :param sink: Where ticks are delivered. Defaults to a bounded
            :class:`~algotraders.streaming.sinks.QueueTickSink`, which
            drops the oldest tick when a consumer falls behind rather
            than stalling the socket.

        :type  autoStart: bool
        :param autoStart: Connect immediately. Pass ``False`` to
            subscribe before any data arrives.

        **Example Usage**

        .. code-block:: python

            stream = await session.stream()
            await stream.subscribe([instrument], mode = StreamMode.QUOTE)

            while True:
                tick = await stream.sink.get()

        :raises MissingDependency: The broker registers no stream class.

        :rtype:   Any
        :returns: The broker's stream, already connected unless
            ``autoStart`` was disabled.
        """

        from algotraders.streaming.sinks import QueueTickSink

        if self.streamer is not None:
            return self.streamer

        if self.streamFactory is None:
            raise MissingDependency(
                self.brokerName,
                packages = [f"{self.brokerName} streaming support"]
            )

        self.streamer = self.streamFactory(**_acceptedArguments(
            self.streamFactory,
            dict(
                sessionManager = self.auth.buildSessionManager(self.bundle),
                sink = sink or QueueTickSink(),
                clock = self.clock,
                resolver = self.resolver,
                credentials = self.credentials,
                accessToken = self.bundle.accessToken.get_secret_value(),
                apiKey = self.credentials.clientId.get_secret_value(),
                session = self,
                **kwargs
            )
        ))

        if autoStart:
            await self.streamer.start()

        return self.streamer


    async def close(self) -> None:
        """
        Release resources held by the session.

        Shuts down the thread pool and closes any open stream.
        Deliberately does **not** revoke the broker token: the token is
        valid for the rest of the trading day and another process, or
        the next request in this one, will want it. Revocation is
        :meth:`logout`.
        """

        if self.streamer is not None:
            try:
                await self.streamer.stop()
            except Exception as error:
                logger.debug(
                    "stream shutdown raised %s", type(error).__name__
                )

            self.streamer = None

        if self.caller is not None:
            await self.caller.close()


    async def logout(self) -> bool:
        """
        Revoke the token at the broker and forget it locally.

        :rtype:   bool
        :returns: Whether the broker confirmed revocation. Kite supports
            this; Fyers does not expose an endpoint for it, in which
            case the local bundle is still discarded.
        """

        revoked = False

        try:
            if hasattr(self.auth, "logout"):
                revoked = bool(await self.auth.logout(self.bundle))
        finally:
            if self.tokenStore is not None:
                self.tokenStore.delete(self.brokerName, self.bundle.clientId)

        return revoked


    async def __aenter__(self) -> "BrokerSession":
        return self


    async def __aexit__(self, *exception : Any) -> None:
        await self.close()


    def __repr__(self) -> str:
        """
        Render without any token material and with a masked client id.
        """

        return (
            f"BrokerSession(broker={self.brokerName!r}, "
            f"clientId={maskSecret(self.bundle.clientId)}, "
            f"authenticated={self.isAuthenticated}, "
            f"expiresAt="
            f"{self.bundle.expiresAt.isoformat() if self.bundle.expiresAt else None})"
        )


async def initialize(
    broker : Union[str, Any],
    credentials : Optional[BaseBrokerCredentials] = None,
    tokenStore : Optional[BaseTokenStore] = None,
    authCode : Optional[str] = None,
    state : Optional[str] = None,
    maxConcurrency : int = DEFAULT_MAX_CONCURRENCY,
    clock : Optional[BaseClock] = None,
    retryPolicy : Optional[RetryPolicy] = None,
    envFile : Optional[Any] = None,
    autoRefresh : bool = True,
    authFactory : Optional[Callable[..., Any]] = None
) -> BrokerSession:
    """
    Obtain a working broker session, without ever prompting.

    :type  broker: Union[str, Any]
    :param broker: Registry name of the broker, or a ``Broker`` enum
        member.

    :type  credentials: Optional[BaseBrokerCredentials]
    :param credentials: Explicit credentials. Resolved from the
        environment when omitted, which is the normal path.

    :type  tokenStore: Optional[BaseTokenStore]
    :param tokenStore: Where the day's token lives. Defaults to a
        :class:`~algotraders.auth.store.FileTokenStore`, which is a
        development convenience; a production host passes its own.

    :type  authCode: Optional[str]
    :param authCode: OAuth authorization code or Kite request token,
        supplied by the host's callback route. Present only on the
        second call, immediately after a redirect.

    :type  state: Optional[str]
    :param state: Opaque value to round trip through the login, so the
        host can tie a callback to the request that began it.

    :type  maxConcurrency: int
    :param maxConcurrency: Simultaneous broker calls, and the size of
        the thread pool blocking SDK calls run in.

    :type  autoRefresh: bool
    :param autoRefresh: Whether an expired token may be refreshed
        automatically. Set ``False`` to force an explicit login.

    :type  authFactory: Optional[Callable[..., Any]]
    :param authFactory: Builds the authentication object from
        credentials. Defaults to resolving the broker through the
        registry; injectable for tests and third party brokers.

    :raises AuthorizationRequired: No usable session exists and none can
        be established without a human. Carries ``loginURL``, which the
        host redirects to.

    :raises InvalidCredentials: Credentials could not be resolved.

    :rtype:   BrokerSession
    :returns: An authenticated, ready to use session.
    """

    from algotraders.brokers import getBrokerInterface

    brokerName = getattr(broker, "value", broker)

    resolvedClock = clock or SystemClock()
    resolvedCredentials = credentials or credentialsFor(
        brokerName, envFile = envFile
    )

    store = tokenStore or FileTokenStore.fromEnvironment()

    interface = getBrokerInterface(brokerName)
    factory = authFactory or interface["AUTH"]

    auth = factory(
        credentials = resolvedCredentials, clock = resolvedClock
    )

    clientId = resolvedCredentials.clientId.get_secret_value()
    bundle = store.read(brokerName, clientId)
    now = resolvedClock.now()

    if bundle is not None and not bundle.isExpired(now):
        logger.debug("reusing stored %s session", brokerName)
    elif (
        bundle is not None
        and autoRefresh
        and bundle.canRefresh(now)
        and getattr(auth, "supportsRefresh", True)
    ):
        logger.info("refreshing expired %s session", brokerName)

        bundle = await auth.refresh(bundle)
        store.write(bundle)
    elif authCode is not None:
        logger.info("completing %s login from supplied authorization code",
                    brokerName)

        bundle = await auth.completeLogin(authCode, state = state)
        store.write(bundle)
    else:
        challenge = auth.beginLogin(state = state)

        raise AuthorizationRequired(
            loginURL = challenge.loginURL, brokerName = brokerName,
            state = challenge.state
        )

    policy : RateLimitPolicy = auth.rateLimitPolicy(resolvedClock)

    caller = BlockingCaller(
        policy = policy, clock = resolvedClock,
        maxConcurrency = maxConcurrency, retryPolicy = retryPolicy
    )

    sessionManager = auth.buildSessionManager(bundle)

    session = BrokerSession(
        brokerName = brokerName,
        credentials = resolvedCredentials,
        auth = auth,
        bundle = bundle,
        caller = caller,
        clock = resolvedClock,
        tokenStore = store,
        capabilities = frozenset(getattr(auth, "capabilities", frozenset()))
    )

    # ! everything past this point owns a thread pool. A factory that
    # raises must not strand it, so the assembly is wrapped rather than
    # left to the caller to clean up after an exception it never saw
    try:
        # ? the resolver is built FIRST because the API surface needs it:
        # every portfolio row arrives carrying a vendor symbol and has to
        # be resolved back to a canonical key before the host can join it
        # against anything it already stored
        session.resolver = _buildOptional(
            interface.get("RESOLVER"),
            cache = InstrumentCache(
                directory = runtimeConfig(envFile).cacheDirectory,
                clock = resolvedClock
            ),
            clock = resolvedClock,
            session = session, sessionManager = sessionManager,
            caller = caller, credentials = resolvedCredentials
        )

        session.api = _buildOptional(
            interface.get("API"), session = session,
            sessionManager = sessionManager, caller = caller,
            clock = resolvedClock, credentials = resolvedCredentials,
            resolver = session.resolver
        )

        # ? kept as a factory, not built. Opening a websocket is a
        # separate decision from authenticating, so session.stream()
        # constructs it on first use
        session.streamFactory = interface.get("STREAM")
    except BaseException:
        await caller.close()

        raise

    logger.info("initialized %s session for %s", brokerName,
                maskSecret(clientId))

    return session


def initializeSync(*args : Any, **kwargs : Any) -> BrokerSession:
    """
    Blocking wrapper around :func:`initialize`, for scripts only.

    A web backend must never call this: :func:`asyncio.run` refuses to
    start inside a running event loop, and blocking a request worker on
    a broker round trip is exactly what the async design avoids.

    :raises RuntimeError: Called from inside a running event loop.

    :rtype:   BrokerSession
    :returns: An authenticated session.
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(initialize(*args, **kwargs))

    raise RuntimeError(
        "initializeSync cannot run inside an event loop; await "
        "algotraders.initialize(...) instead"
    )


def _buildOptional(factory : Any, **kwargs : Any) -> Any:
    """
    Instantiate an optional broker surface, tolerating its absence.

    Not every broker ships a resolver or a trading surface, and a
    registry entry may legitimately omit either.

    Arguments the factory cannot accept are dropped rather than passed,
    so a broker whose constructor takes a narrower set than the assembly
    offers still builds. A *missing required* argument still raises
    ``TypeError``, which is the failure worth keeping loud.

    :rtype:   Any
    :returns: The constructed object, or ``None`` when no factory was
        registered.
    """

    if factory is None:
        return None

    return factory(**_acceptedArguments(factory, kwargs))


def _acceptedArguments(
    factory : Any, offered : Dict[str, Any]
) -> Dict[str, Any]:
    """
    Narrow a keyword mapping to what a callable will actually accept.

    ..versionchanged:: 2026-08-06 Added After a Silent Assembly Failure
        ``initialize`` previously handed every broker surface one fixed
        keyword set. That set drifted from the resolver constructors and
        produced a ``TypeError`` for every real broker, which no test
        caught because the only fake broker registered no resolver.
        Filtering removes the coupling between the assembly site and
        each constructor's exact signature.

    :type  factory: Any
    :param factory: The callable about to be invoked.

    :type  offered: Dict[str, Any]
    :param offered: Everything the assembly is willing to supply.

    :rtype:   Dict[str, Any]
    :returns: The subset the callable declares. Returned unchanged when
        the callable takes ``**kwargs`` or cannot be introspected.
    """

    try:
        parameters = inspect.signature(factory).parameters.values()
    except (TypeError, ValueError):
        # ? a C extension or a builtin with no signature; hand it
        # everything and let it complain if it disagrees
        return offered

    if any(
        entry.kind is inspect.Parameter.VAR_KEYWORD for entry in parameters
    ):
        return offered

    accepted = {
        entry.name for entry in parameters
        if entry.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY
        )
    }

    return {
        name : value for name, value in offered.items() if name in accepted
    }


__all__ = ["BrokerSession", "initialize", "initializeSync"]
