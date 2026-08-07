# -*- encoding: utf-8 -*-

"""
A Minimal Broker Standing In for a Real Implementation
--------------------------------------------------------

``initialize`` resolves a broker through the registry, which imports a
module by name and pulls callables off it. Testing that path therefore
needs a real, importable module rather than a mock, and this is it.

The stub records what was called on it so a test can assert the exact
resolution path taken: whether the stored token was reused, refreshed,
or exchanged from an authorization code. Those three branches are the
whole point of the entry point, and telling them apart from the outside
is otherwise guesswork.

..versionchanged:: 2026-08-06 Declares a Resolver and a Stream
    The stub originally registered only ``API`` and ``AUTH``. That left
    the resolver assembly in ``initialize`` completely unexercised, and
    it was broken: it passed ``cacheDirectory`` to constructors that
    require ``cache``, so every real broker raised ``TypeError`` while
    the suite stayed green. A fake that is simpler than the thing it
    stands in for tests the wrong shape, so this one now declares every
    surface a real registry entry does.
"""

import datetime as dt

from typing import Any, FrozenSet, List, Optional, Sequence

from algotraders.config import BaseBrokerCredentials
from algotraders.core.clock import BaseClock, SystemClock
from algotraders.core.ratelimit import RateLimit, RateLimitPolicy
from algotraders.instruments.base import BaseInstrumentResolver
from algotraders.instruments.cache import InstrumentCache
from algotraders.models.auth import LoginChallenge, TokenBundle, TokenPolicy
from algotraders.models.enums import Capability, Segment
from algotraders.models.instrument import Instrument, InstrumentKey

STUB_LOGIN_URL = "https://stub.example.com/connect/login?api_key=stub"

STUB_POLICY = TokenPolicy(
    rolloverHourIST = 6,
    accessTokenLifetime = dt.timedelta(days = 1),
    refreshTokenLifetime = dt.timedelta(days = 15),
    supportsRefresh = True,
    refreshRequiresPin = False
)

STUB_CAPABILITIES : FrozenSet[Capability] = frozenset({
    Capability.HISTORICAL_DATA, Capability.LIVE_QUOTES, Capability.TRADING
})


class StubSessionManager:
    """
    Stands in for a vendor SDK client object.
    """

    def __init__(self, token : str) -> None:
        self.token = token
        self.calls : List[str] = []


    def history(self, data : Any = None) -> dict:
        self.calls.append("history")

        return {"s" : "ok", "candles" : []}


class StubAuthentication:
    """
    Authentication object implementing the contract ``initialize``
    expects, with every interaction recorded.

    :type  credentials: BaseBrokerCredentials
    :param credentials: Resolved application credentials.

    :type  clock: BaseClock
    :param clock: Injected time source.
    """

    supportsRefresh = True
    capabilities = STUB_CAPABILITIES

    def __init__(
        self, credentials : BaseBrokerCredentials,
        clock : Optional[BaseClock] = None
    ) -> None:
        self.credentials = credentials
        self.clock = clock or SystemClock()

        self.beginLoginCalls = 0
        self.completeLoginCalls = 0
        self.refreshCalls = 0
        self.logoutCalls = 0
        self.builtSessionManagers : List[StubSessionManager] = []


    def beginLogin(
        self, state : Optional[str] = None
    ) -> LoginChallenge:
        """
        Produce the challenge a host turns into a browser redirect.
        """

        self.beginLoginCalls += 1

        return LoginChallenge(
            brokerName = "stub", loginURL = STUB_LOGIN_URL, state = state
        )


    async def completeLogin(
        self, authCode : str, state : Optional[str] = None
    ) -> TokenBundle:
        """
        Exchange an authorization code for a fresh token bundle.
        """

        self.completeLoginCalls += 1

        return self.__bundle__(f"access-from-{authCode}")


    async def refresh(self, bundle : TokenBundle) -> TokenBundle:
        """
        Mint a new bundle from an existing refresh token.
        """

        self.refreshCalls += 1

        return self.__bundle__("access-after-refresh")


    async def logout(self, bundle : TokenBundle) -> bool:
        """
        Pretend to revoke the token at the broker.
        """

        self.logoutCalls += 1

        return True


    def buildSessionManager(self, bundle : TokenBundle) -> StubSessionManager:
        """
        Build the vendor client the API surface will call through.
        """

        manager = StubSessionManager(bundle.accessToken.get_secret_value())
        self.builtSessionManagers.append(manager)

        return manager


    def rateLimitPolicy(self, clock : BaseClock) -> RateLimitPolicy:
        """
        Supply a generous policy, since these tests are not about rate
        limiting.
        """

        return RateLimitPolicy(
            clock = clock,
            sharedLimits = [RateLimit("stub.perSecond", 1_000, 1.0)]
        )


    def __bundle__(self, accessToken : str) -> TokenBundle:
        """
        Build a bundle for this broker under the stub token policy.
        """

        return TokenBundle.build(
            brokerName = "stub",
            clientId = self.credentials.clientId.get_secret_value(),
            accessToken = accessToken,
            refreshToken = "stub-refresh-token",
            issuedAt = self.clock.now(),
            policy = STUB_POLICY
        )


class StubAPI:
    """
    Placeholder data surface, constructed by ``initialize`` when the
    registry entry names one.

    Mirrors the real ``BaseBrokerClient`` constructor rather than
    swallowing everything into ``**kwargs``, so a test can assert that
    the assembly actually handed it a resolver.
    """

    def __init__(
        self, sessionManager : Any = None, caller : Any = None,
        clock : Optional[BaseClock] = None,
        credentials : Optional[BaseBrokerCredentials] = None,
        resolver : Any = None, session : Any = None, **extra : Any
    ) -> None:
        self.sessionManager = sessionManager
        self.caller = caller
        self.clock = clock
        self.credentials = credentials
        self.resolver = resolver
        self.session = session
        self.extra = extra


class StubInstrumentResolver(BaseInstrumentResolver):
    """
    Resolver over a fixed two-instrument master.

    Deliberately declares the same required ``cache`` and ``clock``
    parameters as the real broker resolvers, with **no** ``**kwargs``
    catch-all. That is the point: a stub that accepts anything would
    have absorbed the wrong keyword set and hidden the defect this
    exists to catch.
    """

    def __init__(
        self, cache : InstrumentCache, clock : BaseClock,
        segments : Optional[Sequence[Segment]] = None,
        brokerName : str = "stub"
    ) -> None:
        super().__init__(
            brokerName = brokerName, cache = cache, clock = clock,
            segments = segments
        )

        self.downloadCalls = 0


    async def downloadMaster(self, segment : Segment) -> List[Instrument]:
        """
        Return a fixed master, one instrument per supported segment.
        """

        self.downloadCalls += 1

        if segment is not Segment.CAPITAL:
            return []

        return [
            Instrument(
                key = InstrumentKey.parse("NSE:SBIN:EQ"),
                brokerSymbol = "NSE:SBIN-EQ", brokerToken = "3045",
                lotSize = 1, source = "stub"
            ),
            Instrument(
                key = InstrumentKey.parse("NSE:TCS:EQ"),
                brokerSymbol = "NSE:TCS-EQ", brokerToken = "11536",
                lotSize = 1, source = "stub"
            )
        ]


    def toBrokerSymbol(self, instrument : Instrument) -> str:
        """
        Render the vendor symbol recorded in the master.
        """

        return instrument.brokerSymbol


class StubStream:
    """
    Placeholder streaming surface.

    Carries ``accessToken`` because both real streams do: a websocket
    authenticates with the raw token rather than the vendor client
    object, so a token refresh has to reach it separately. A fake
    without that field cannot catch a rebind that misses it.
    """

    def __init__(
        self, sessionManager : Any = None, sink : Any = None,
        clock : Optional[BaseClock] = None, resolver : Any = None,
        accessToken : str = "", **extra : Any
    ) -> None:
        self.sessionManager = sessionManager
        self.sink = sink
        self.clock = clock
        self.resolver = resolver
        self.accessToken = accessToken

        self.started = False
        self.stopped = False


    async def start(self) -> None:
        """
        Record that the stream was asked to connect.
        """

        self.started = True


    async def stop(self) -> None:
        """
        Record that the stream was shut down.
        """

        self.stopped = True
