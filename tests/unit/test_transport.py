# -*- encoding: utf-8 -*-

"""
FastAPI Router Behaviour and Error Translation
-------------------------------------------------

The status code mapping is the payoff for shaping the exception tree
around recovery actions rather than around where a failure happened. A
frontend can act on 409 versus 401 versus 402 without parsing a single
message string, and these tests pin that contract.

The other assertion worth calling out is that ``POST /orders`` is absent
by default. Mounting this router with defaults must not be able to place
a trade, and absent is safer than guarded: the route does not appear in
the OpenAPI schema at all.
"""

import datetime as dt

import pytest

pytest.importorskip("fastapi", reason = "requires the optional server extra")

from fastapi import FastAPI                                    # noqa: E402
from fastapi.testclient import TestClient                      # noqa: E402

from algotraders.core.clock import IST                         # noqa: E402
from algotraders.errors import (                               # noqa: E402
    AuthorizationRequired, DataSubscriptionRequired, InstrumentNotFound,
    OrderRejected, RateLimitExceeded, TokenExpiredError,
    UnsupportedTimeframe
)
from algotraders.models.auth import SessionStatus              # noqa: E402
from algotraders.models.enums import Timeframe                 # noqa: E402
from algotraders.models.instrument import (                    # noqa: E402
    Instrument, InstrumentKey
)
from algotraders.models.market import Candle                   # noqa: E402
from algotraders.transport.fastapi import (                    # noqa: E402
    createRouter, installExceptionHandlers
)

MOMENT = dt.datetime(2026, 8, 5, 9, 15, tzinfo = IST)

SBIN = Instrument(
    key = InstrumentKey.parse("NSE:SBIN:EQ"),
    brokerSymbol = "NSE:SBIN-EQ", brokerToken = "3045", lotSize = 1,
    source = "fyers"
)


class StubResolver:
    """
    Resolver returning one known instrument and failing on anything
    else.
    """

    def __init__(self) -> None:
        self.isStale = False
        self.refreshCalls = 0


    async def resolve(self, key : object) -> Instrument:
        if str(key) != "NSE:SBIN:EQ":
            raise InstrumentNotFound(key, brokerName = "fyers")

        return SBIN


    async def search(self, name : str, limit : int = 25) -> list:
        return [SBIN] if name.upper() in "SBIN" else []


    async def refresh(self, force : bool = False) -> int:
        self.refreshCalls += 1

        return 13


class StubAPI:
    """
    API surface returning fixed data or raising on cue.
    """

    def __init__(self, failure : BaseException = None) -> None:
        self.failure = failure
        self.placed = []


    def __check__(self) -> None:
        if self.failure is not None:
            raise self.failure


    async def fetchCandles(self, **kwargs : object) -> list:
        self.__check__()

        return [Candle(
            instrumentKey = "NSE:SBIN:EQ", timeframe = Timeframe.MINUTE_1,
            timestamp = MOMENT, openPrice = 800.0, highPrice = 805.0,
            lowPrice = 799.0, closePrice = 803.0, volume = 1_000,
            source = "fyers"
        )]


    async def fetchQuote(self, instruments : object) -> list:
        self.__check__()

        return []


    async def fetchPositions(self) -> list:
        self.__check__()

        return []


    async def fetchHoldings(self) -> list:
        self.__check__()

        return []


    async def fetchOrders(self) -> list:
        self.__check__()

        return []


    async def placeOrder(self, request : object) -> object:
        self.__check__()
        self.placed.append(request)

        from algotraders.models.orders import OrderResponse

        return OrderResponse(orderId = "9001", source = "fyers")


class StubSession:
    """
    Minimal stand-in for a BrokerSession.
    """

    def __init__(
        self, failure : BaseException = None, resolver : object = None
    ) -> None:
        self.brokerName = "fyers"
        self.api = StubAPI(failure)
        self.resolver = StubResolver() if resolver is None else resolver
        self.tokenStore = None
        self.bundle = None


    def status(self) -> SessionStatus:
        return SessionStatus(
            brokerName = "fyers", clientId = "********-100",
            isAuthenticated = True, capabilities = ["HISTORICAL_DATA"]
        )


def buildClient(
    failure : BaseException = None, enableTrading : bool = False,
    resolver : object = None
) -> TestClient:
    """
    Build a TestClient over a router bound to a stub session.
    """

    session = StubSession(failure, resolver = resolver)

    async def provider() -> StubSession:
        if isinstance(failure, AuthorizationRequired):
            raise failure

        return session

    app = FastAPI()
    app.include_router(
        createRouter(
            sessionProvider = provider, enableTrading = enableTrading
        )
    )
    installExceptionHandlers(app)

    client = TestClient(app, raise_server_exceptions = False)
    client.session = session

    return client


def test_listsRegisteredBrokers() -> None:
    """
    The broker listing needs no session and no broker SDK.
    """

    response = buildClient().get("/algotraders/brokers")

    assert response.status_code == 200
    assert "fyers" in response.json()
    assert "kite" in response.json()


def test_sessionStatusCarriesNoToken() -> None:
    """
    The status route is safe to expose to a frontend by construction.
    """

    response = buildClient().get("/algotraders/session")

    assert response.status_code == 200

    body = response.json()

    assert body["is_authenticated"] is True
    assert "accessToken" not in response.text
    assert "access_token" not in response.text


def test_candlesReturnSnakeCaseForTheORM() -> None:
    """
    Response bodies use the alias spelling, which is the same shape the
    host inserts into its price table.
    """

    response = buildClient().post("/algotraders/candles", json = {
        "instrumentKeys" : ["NSE:SBIN:EQ"], "timeframe" : "1m",
        "start" : "2026-08-01T09:15:00+05:30",
        "end" : "2026-08-05T15:30:00+05:30"
    })

    assert response.status_code == 200

    candle = response.json()[0]

    assert candle["open_price"] == 800.0
    assert candle["instrument_key"] == "NSE:SBIN:EQ"
    assert candle["timeframe"] == "1m"


def test_unauthenticatedSessionReturns409WithLoginURL() -> None:
    """
    409 rather than 401: the credentials are fine, a human simply has to
    establish the session. The frontend redirects to ``loginURL``.
    """

    client = buildClient(failure = AuthorizationRequired(
        loginURL = "https://kite.zerodha.com/connect/login?v=3&api_key=x",
        brokerName = "kite", state = "csrf"
    ))

    response = client.get("/algotraders/session")

    assert response.status_code == 409

    body = response.json()

    assert body["loginURL"].startswith("https://")
    assert body["state"] == "csrf"


def test_expiredTokenReturns401() -> None:
    """
    A dead session is a genuine authentication failure.
    """

    client = buildClient(failure = TokenExpiredError("expired at 06:00 IST"))
    response = client.get("/algotraders/positions")

    assert response.status_code == 401


def test_rateLimitReturns429WithRetryAfter() -> None:
    """
    The broker's own delay is forwarded as a standard header so a client
    can back off without parsing the body.
    """

    client = buildClient(
        failure = RateLimitExceeded("slow down", retryAfter = 42.0)
    )
    response = client.get("/algotraders/positions")

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "42"


def test_unknownInstrumentReturns404() -> None:
    """
    Resolution failures are a missing resource, not a server fault.
    """

    response = buildClient().post("/algotraders/candles", json = {
        "instrumentKeys" : ["NSE:NOTREAL:EQ"], "timeframe" : "1m",
        "start" : "2026-08-01T09:15:00+05:30",
        "end" : "2026-08-05T15:30:00+05:30"
    })

    assert response.status_code == 404


def test_missingDataSubscriptionReturns402() -> None:
    """
    402 is unusual but exactly right: the request is valid and the
    account has not paid for this data.

    Distinguishing it from a 403 or a retryable 502 is what lets the
    frontend say "subscribe" instead of "try again".
    """

    client = buildClient(
        failure = DataSubscriptionRequired("historical is a paid add-on")
    )
    response = client.get("/algotraders/positions")

    assert response.status_code == 402


def test_unsupportedTimeframeReturns422() -> None:
    """
    A request the broker cannot serve is a validation problem.
    """

    client = buildClient(failure = UnsupportedTimeframe(
        brokerName = "fyers", timeframe = Timeframe.MONTH_1,
        supported = [Timeframe.DAY_1]
    ))
    response = client.get("/algotraders/positions")

    assert response.status_code == 422


def test_orderRejectionSurfacesTheBrokerMessage() -> None:
    """
    The broker's own wording reaches the client, since it is the only
    actionable detail the end user gets.
    """

    client = buildClient(
        failure = OrderRejected(
            "rejected", brokerMessage = "RED: Insufficient funds"
        ),
        enableTrading = True
    )

    response = client.post("/algotraders/orders", json = {
        "instrument" : SBIN.model_dump(mode = "json"),
        "side" : "BUY", "quantity" : 10, "orderType" : "MARKET"
    })

    assert response.status_code == 422
    assert response.json()["brokerMessage"] == "RED: Insufficient funds"


def test_tradingRoutesAreAbsentByDefault() -> None:
    """
    Mounting the router with defaults cannot place a trade.

    Absent rather than guarded: the route is missing from the OpenAPI
    schema entirely, so it cannot be discovered and called by mistake.
    """

    client = buildClient(enableTrading = False)

    response = client.post("/algotraders/orders", json = {
        "instrument" : SBIN.model_dump(mode = "json"),
        "side" : "BUY", "quantity" : 10, "orderType" : "MARKET"
    })

    # ? 405 rather than 404 because GET /orders is registered, so the
    # path exists and only the method is missing. Either way no order
    # can be placed, which is the property being asserted
    assert response.status_code == 405

    schema = client.get("/openapi.json").json()

    assert "post" not in schema["paths"].get("/algotraders/orders", {})
    assert "get" in schema["paths"]["/algotraders/orders"]


def test_tradingRoutesAppearWhenEnabled() -> None:
    """
    Explicit opt in registers placement and cancellation.
    """

    client = buildClient(enableTrading = True)

    response = client.post("/algotraders/orders", json = {
        "instrument" : SBIN.model_dump(mode = "json"),
        "side" : "BUY", "quantity" : 10, "orderType" : "MARKET"
    })

    assert response.status_code == 200
    assert response.json()["order_id"] == "9001"
    assert len(client.session.api.placed) == 1


def test_instrumentRefreshReportsStaleness() -> None:
    """
    A caller needs to know it is being served a master that could not be
    renewed, since derivative tokens are reissued at every expiry.
    """

    response = buildClient().post("/algotraders/instruments/refresh")

    assert response.status_code == 200
    assert response.json() == {"refreshed" : 13, "stale" : False}


def test_searchBacksATypeAhead() -> None:
    """
    Substring search returns full instruments, not bare strings.
    """

    response = buildClient().get(
        "/algotraders/instruments/search", params = {"name" : "SBIN"}
    )

    assert response.status_code == 200
    assert response.json()[0]["broker_symbol"] == "NSE:SBIN-EQ"


def test_missingResolverReturns501() -> None:
    """
    A broker without an instrument resolver is an unimplemented
    capability rather than a bad request.
    """

    class NoResolverSession(StubSession):
        def __init__(self) -> None:
            super().__init__()
            self.resolver = None

    session = NoResolverSession()

    async def provider() -> object:
        return session

    app = FastAPI()
    app.include_router(createRouter(sessionProvider = provider))
    installExceptionHandlers(app)

    response = TestClient(app).post("/algotraders/instruments/refresh")

    assert response.status_code == 501


def test_routerPrefixIsConfigurable() -> None:
    """
    A host mounting several brokers needs distinct prefixes.
    """

    async def provider() -> object:
        return StubSession()

    app = FastAPI()
    app.include_router(
        createRouter(sessionProvider = provider, prefix = "/broker/fyers")
    )

    assert TestClient(app).get("/broker/fyers/brokers").status_code == 200


def test_transportRaisesMissingDependencyWithoutFastAPI(
    monkeypatch : pytest.MonkeyPatch
) -> None:
    """
    Importing the transport without the extra names the pip command.

    A bare ``ModuleNotFoundError: fastapi`` leaves the reader guessing
    which extra provides it.
    """

    import sys
    import subprocess

    # ! run in a subprocess rather than purging sys.modules in place.
    # Evicting the whole algotraders package mid-suite leaves every
    # already-imported test module holding stale class objects, and the
    # resulting failures appear in unrelated tests
    script = (
        "import sys\n"
        "class Blocker:\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        if fullname.split('.')[0] == 'fastapi':\n"
        "            raise ImportError('blocked')\n"
        "        return None\n"
        "sys.meta_path.insert(0, Blocker())\n"
        "from algotraders.errors import MissingDependency\n"
        "try:\n"
        "    import algotraders.transport.fastapi\n"
        "except MissingDependency as error:\n"
        "    print(error)\n"
        "    sys.exit(0)\n"
        "sys.exit(1)\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output = True, text = True
    )

    assert completed.returncode == 0, completed.stderr
    assert "pip install algotraders[server]" in completed.stdout
