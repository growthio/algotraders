# -*- encoding: utf-8 -*-

"""
A Mountable APIRouter Over a Broker Session
---------------------------------------------

Exposes the library's read surface, and optionally its trading surface,
as routes a host application mounts with ``app.include_router(...)``.
It is a convenience: the host can equally well call the session
directly, and for ingestion it should, because persistence belongs to
the host's ORM and not to an HTTP round trip through its own process.

Two design points are worth stating because they look like arbitrary
choices and are not.

**The router takes a session provider, not a session.** Lifecycle and
multi-tenancy belong to the host: a provider can return a different
session per authenticated user, refresh an expired one, or raise
``AuthorizationRequired`` to trigger a login. A bound session would fix
one broker account for the whole process.

**The login callback is a POST, not a GET redirect handler.** OAuth
codes in a query string end up in proxy logs, access logs and browser
history. The host owns the actual GET redirect route, because it has to
match what is registered with the broker anyway, and forwards the code
here in a request body. ``/candles`` is a POST for the same reason plus
a practical one: a date range and a list of instruments do not belong in
a URL.
"""

import datetime as dt

from typing import Any, Awaitable, Callable, List, Optional, Sequence

from algotraders.auth.state import (
    BaseLoginStateStore, MemoryLoginStateStore
)
from algotraders.errors import (
    AlgoTradersError, AuthorizationRequired, DataSubscriptionRequired,
    InstrumentNotFound, InvalidLoginState, InvalidOrder, MissingDependency,
    OrderRejected, RateLimitExceeded, TokenExpiredError, UnsupportedTimeframe
)
from algotraders.logger import getLogger
from algotraders.models.auth import LoginChallenge, SessionStatus
from algotraders.models.base import AlgoTradersModel
from algotraders.models.enums import Timeframe
from algotraders.models.instrument import Instrument
from algotraders.models.market import Candle, Quote
from algotraders.models.orders import OrderRequest, OrderResponse
from algotraders.models.portfolio import Holding, Position

logger = getLogger(__name__)

try:
    from fastapi import APIRouter, Depends, HTTPException, Query, status
    from fastapi.responses import JSONResponse
except ImportError as error:  # pragma: no cover - exercised by a test
    raise MissingDependency("server", packages = ["fastapi"]) from error


class CandleRequest(AlgoTradersModel):
    """
    Body for a historical candle request.

    :type  instrumentKeys: List[str]
    :param instrumentKeys: Canonical keys to fetch.

    :type  allowAggregation: bool
    :param allowAggregation: Permit synthesising a timeframe the broker
        does not serve. Defaults to refusing, matching the library.
    """

    instrumentKeys : List[str]
    timeframe : Timeframe
    start : dt.datetime
    end : dt.datetime
    allowAggregation : bool = False


class QuoteRequest(AlgoTradersModel):
    """
    Body for a quote request.
    """

    instrumentKeys : List[str]


class CallbackRequest(AlgoTradersModel):
    """
    Body carrying an OAuth code back from the host's redirect route.

    :type  authCode: str
    :param authCode: The broker's code. Fyers calls it an auth code and
        Kite a request token; both are short lived and single use.
    """

    authCode : str
    state : Optional[str] = None


class RefreshResult(AlgoTradersModel):
    """
    Outcome of an instrument master refresh.

    :type  stale: bool
    :param stale: Whether the resolver is serving a cache it could not
        renew. Worth surfacing, because derivative tokens are reissued
        at every expiry and a stale master resolves them wrongly.
    """

    refreshed : int
    stale : bool


SessionProvider = Callable[..., Awaitable[Any]]


def createRouter(
    sessionProvider : SessionProvider,
    prefix : str = "/algotraders",
    tags : Optional[List[str]] = None,
    dependencies : Optional[Sequence[Any]] = None,
    enableTrading : bool = False,
    stateStore : Optional[BaseLoginStateStore] = None,
    allowUnauthenticated : bool = False
) -> "APIRouter":
    """
    Build a router exposing the session's surface.

    :type  sessionProvider: SessionProvider
    :param sessionProvider: FastAPI dependency returning a
        :class:`~algotraders.auth.session.BrokerSession`.

    :type  dependencies: Optional[Sequence[Any]]
    :param dependencies: Router level FastAPI dependencies, which is
        where authentication belongs. **Required.** These routes expose
        an authenticated broker session: positions, holdings, the order
        book and a login URL carrying the application's client id. A
        router mounted without an auth dependency publishes all of that
        to anyone who can reach the port.

    :type  enableTrading: bool
    :param enableTrading: Whether order routes are registered. Defaults
        to ``False``, so mounting this router cannot place a trade
        unless somebody explicitly asked for that. Registration is
        skipped entirely rather than guarded at request time, so an
        unarmed deployment returns 404 rather than 403 and the route
        does not appear in the OpenAPI schema at all.

    :type  stateStore: Optional[BaseLoginStateStore]
    :param stateStore: Mints and validates the one-time OAuth ``state``.
        Defaults to an in-process store, which fails closed under
        multiple workers; supply a shared implementation for those.

    :type  allowUnauthenticated: bool
    :param allowUnauthenticated: Escape hatch for a deployment that
        genuinely terminates auth in front of this router, for example
        behind an authenticating gateway on a private network. Requires
        saying so explicitly, because the safe default has to be the one
        you get by forgetting.

    :raises ValueError: No ``dependencies`` were supplied and
        ``allowUnauthenticated`` was not set.

    **Example Usage**

    .. code-block:: python

        from fastapi import Depends
        from algotraders.transport.fastapi import createRouter

        async def getSession() -> BrokerSession:
            return await algotraders.initialize(broker = "fyers")

        app.include_router(createRouter(
            sessionProvider = getSession,
            dependencies = [Depends(requireLoggedInUser)]
        ))

    :rtype:   APIRouter
    :returns: The configured router.
    """

    # ! fail at construction, not at request time. A router that silently
    # serves an authenticated broker session to anonymous callers is the
    # kind of default that ships and is discovered later
    if not dependencies and not allowUnauthenticated:
        raise ValueError(
            "createRouter needs authentication: pass dependencies = "
            "[Depends(yourAuthDependency)], or set allowUnauthenticated "
            "= True if auth is genuinely terminated in front of this "
            "router. These routes expose positions, holdings, orders "
            "and a login URL carrying your client id"
        )

    if allowUnauthenticated and not dependencies:
        logger.warning(
            "algotraders router mounted at %s with no authentication; "
            "every route is reachable by anyone who can reach the port",
            prefix
        )

    loginState = stateStore or MemoryLoginStateStore()

    router = APIRouter(
        prefix = prefix,
        tags = tags or ["algotraders"],
        dependencies = list(dependencies or [])
    )

    @router.get("/brokers", response_model = List[str])
    async def listBrokers() -> List[str]:
        """
        Every broker registered in this process.
        """

        from algotraders.brokers import availableBrokers

        return availableBrokers()


    @router.get("/session", response_model = SessionStatus)
    async def readSession(
        session : Any = Depends(sessionProvider)
    ) -> SessionStatus:
        """
        Current session state, with the client id masked and no token.
        """

        return session.status()


    @router.post("/session/login", response_model = LoginChallenge)
    async def beginLogin(
        session : Any = Depends(sessionProvider)
    ) -> LoginChallenge:
        """
        Produce the broker login URL for a browser redirect.

        The ``state`` is minted here rather than accepted from the
        caller. A caller-supplied value proves nothing: the point is
        that only this server knows what it issued.
        """

        state = loginState.mint(session.brokerName)

        return session.auth.beginLogin(state = state)


    @router.post("/session/callback", response_model = SessionStatus)
    async def completeLogin(
        payload : CallbackRequest,
        session : Any = Depends(sessionProvider)
    ) -> SessionStatus:
        """
        Exchange an OAuth code for a session.

        A POST rather than a GET redirect handler, so the code travels
        in a body instead of a query string that proxies would log.

        The state is validated and burned **before** the code is
        exchanged. Without that, an attacker who completes a login on
        their own account and fires the resulting code at this route
        rebinds the session to their broker account, and every
        subsequent order trades their book.
        """

        if not loginState.consume(session.brokerName, payload.state):
            raise InvalidLoginState(brokerName = session.brokerName)

        bundle = await session.auth.completeLogin(
            payload.authCode, state = payload.state
        )

        if session.tokenStore is not None:
            session.tokenStore.write(bundle)

        session.bundle = bundle

        return session.status()


    @router.post("/instruments/refresh", response_model = RefreshResult)
    async def refreshInstruments(
        force : bool = False, session : Any = Depends(sessionProvider)
    ) -> RefreshResult:
        """
        Reload the instrument master.

        ``force`` bypasses a cache written today, which is what a caller
        needs after an expiry reissues derivative tokens mid-session.
        """

        _requireResolver(session)

        refreshed = await session.resolver.refresh(force = force)

        return RefreshResult(
            refreshed = refreshed, stale = session.resolver.isStale
        )


    @router.get("/instruments/search", response_model = List[Instrument])
    async def searchInstruments(
        name : str = Query(..., min_length = 1),
        limit : int = Query(25, ge = 1, le = 200),
        session : Any = Depends(sessionProvider)
    ) -> List[Instrument]:
        """
        Substring search over the instrument master, for a type-ahead.
        """

        _requireResolver(session)

        return await session.resolver.search(name, limit = limit)


    @router.post("/candles", response_model = List[Candle])
    async def fetchCandles(
        payload : CandleRequest,
        session : Any = Depends(sessionProvider)
    ) -> List[Candle]:
        """
        Historical candles for one or more instruments.

        A POST because the body carries a date range and a list of keys.
        Chunking, rate limiting and retry all happen inside the library.
        """

        _requireResolver(session)

        collected : List[Candle] = []

        for key in payload.instrumentKeys:
            instrument = await session.resolver.resolve(key)

            collected.extend(await session.api.fetchCandles(
                instrument = instrument, timeframe = payload.timeframe,
                dateRange = (payload.start, payload.end),
                allowAggregation = payload.allowAggregation
            ))

        return collected


    @router.post("/quotes", response_model = List[Quote])
    async def fetchQuotes(
        payload : QuoteRequest,
        session : Any = Depends(sessionProvider)
    ) -> List[Quote]:
        """
        Current snapshots for a set of instruments.
        """

        _requireResolver(session)

        instruments = [
            await session.resolver.resolve(key)
            for key in payload.instrumentKeys
        ]

        return await session.api.fetchQuote(instruments)


    @router.get("/positions", response_model = List[Position])
    async def fetchPositions(
        session : Any = Depends(sessionProvider)
    ) -> List[Position]:
        """
        Open positions in the trading book.
        """

        return await session.api.fetchPositions()


    @router.get("/holdings", response_model = List[Holding])
    async def fetchHoldings(
        session : Any = Depends(sessionProvider)
    ) -> List[Holding]:
        """
        Settled stock in the demat account.
        """

        return await session.api.fetchHoldings()


    @router.get("/orders", response_model = List[OrderResponse])
    async def fetchOrders(
        session : Any = Depends(sessionProvider)
    ) -> List[OrderResponse]:
        """
        The day's order book.
        """

        return await session.api.fetchOrders()


    if enableTrading:
        # ! registered only on explicit opt in. Mounting this router
        # with defaults cannot place a trade, and the route is absent
        # from the OpenAPI schema rather than merely refusing at runtime
        @router.post("/orders", response_model = OrderResponse)
        async def placeOrder(
            request : OrderRequest,
            session : Any = Depends(sessionProvider)
        ) -> OrderResponse:
            """
            Place an order. Validated locally before dispatch and never
            retried automatically.
            """

            return await session.api.placeOrder(request)


        @router.delete("/orders/{orderId}", response_model = OrderResponse)
        async def cancelOrder(
            orderId : str, session : Any = Depends(sessionProvider)
        ) -> OrderResponse:
            """
            Cancel a working order.
            """

            return await session.api.cancelOrder(orderId)

    return router


def installExceptionHandlers(app : Any) -> None:
    """
    Translate library errors into HTTP responses.

    Registered on the application rather than the router, because
    FastAPI resolves exception handlers at application scope only.

    The mapping is the payoff for shaping the exception tree around
    recovery actions: each status is read off the type, with no message
    inspection anywhere.

    :type  app: Any
    :param app: The FastAPI application to register handlers on.

    **Example Usage**

    .. code-block:: python

        app = FastAPI()
        app.include_router(createRouter(sessionProvider = getSession))
        installExceptionHandlers(app)
    """

    @app.exception_handler(AuthorizationRequired)
    async def onAuthorizationRequired(
        request : Any, error : AuthorizationRequired
    ) -> Any:
        # ? 409 rather than 401: the credentials are fine, the session
        # simply has to be established by a human first
        return JSONResponse(
            status_code = status.HTTP_409_CONFLICT,
            content = {
                "detail" : str(error),
                "loginURL" : error.loginURL,
                "state" : error.state,
                "brokerName" : error.brokerName
            }
        )

    @app.exception_handler(InvalidLoginState)
    async def onInvalidLoginState(
        request : Any, error : InvalidLoginState
    ) -> Any:
        # ? 400, and deliberately terse. A forged callback should learn
        # nothing about why it was refused
        logger.warning("rejected a login callback with an invalid state")

        return JSONResponse(
            status_code = status.HTTP_400_BAD_REQUEST,
            content = {"detail" : "invalid or expired login state"}
        )

    @app.exception_handler(TokenExpiredError)
    async def onTokenExpired(request : Any, error : TokenExpiredError) -> Any:
        return JSONResponse(
            status_code = status.HTTP_401_UNAUTHORIZED,
            content = {"detail" : str(error)}
        )

    @app.exception_handler(RateLimitExceeded)
    async def onRateLimited(request : Any, error : RateLimitExceeded) -> Any:
        headers = {}

        if error.retryAfter is not None:
            headers["Retry-After"] = str(int(error.retryAfter))

        return JSONResponse(
            status_code = status.HTTP_429_TOO_MANY_REQUESTS,
            content = {"detail" : str(error)}, headers = headers
        )

    @app.exception_handler(InstrumentNotFound)
    async def onInstrumentNotFound(
        request : Any, error : InstrumentNotFound
    ) -> Any:
        return JSONResponse(
            status_code = status.HTTP_404_NOT_FOUND,
            content = {"detail" : str(error)}
        )

    @app.exception_handler(DataSubscriptionRequired)
    async def onSubscriptionRequired(
        request : Any, error : DataSubscriptionRequired
    ) -> Any:
        # ? 402 is unusual but exactly right: the request is valid and
        # the account simply has not paid for this data
        return JSONResponse(
            status_code = status.HTTP_402_PAYMENT_REQUIRED,
            content = {"detail" : str(error)}
        )

    @app.exception_handler(UnsupportedTimeframe)
    async def onUnsupportedTimeframe(
        request : Any, error : UnsupportedTimeframe
    ) -> Any:
        return JSONResponse(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            content = {"detail" : str(error)}
        )

    @app.exception_handler(InvalidOrder)
    async def onInvalidOrder(request : Any, error : InvalidOrder) -> Any:
        return JSONResponse(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            content = {"detail" : str(error)}
        )

    @app.exception_handler(OrderRejected)
    async def onOrderRejected(request : Any, error : OrderRejected) -> Any:
        return JSONResponse(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            content = {
                "detail" : str(error), "brokerMessage" : error.brokerMessage
            }
        )

    @app.exception_handler(AlgoTradersError)
    async def onLibraryError(request : Any, error : AlgoTradersError) -> Any:
        # ! last resort. The message is returned because these are
        # operator facing errors carrying no credential material, which
        # the redacting logger and SecretStr models both guarantee
        logger.warning(
            "unhandled library error: %s", type(error).__name__
        )

        return JSONResponse(
            status_code = status.HTTP_502_BAD_GATEWAY,
            content = {"detail" : str(error)}
        )


def _requireResolver(session : Any) -> None:
    """
    Fail clearly when the broker ships no instrument resolver.

    :raises HTTPException: 501, since this is a missing capability
        rather than a bad request.
    """

    if getattr(session, "resolver", None) is None:
        raise HTTPException(
            status_code = status.HTTP_501_NOT_IMPLEMENTED,
            detail = f"{session.brokerName} provides no instrument resolver"
        )


__all__ = [
    "createRouter", "installExceptionHandlers",
    "CandleRequest", "QuoteRequest", "CallbackRequest", "RefreshResult"
]
