# -*- encoding: utf-8 -*-

"""
Public Surface and Request Boundary Hardening
------------------------------------------------

Stage 4 of the audit. Two kinds of gap: a documented capability that was
unreachable, and request shapes an HTTP caller could use to spend an
account's whole broker quota from a single call.
"""

import datetime as dt

import pytest

from algotraders.auth import initialize
from algotraders.auth.store import MemoryTokenStore
from algotraders.brokers import registry as registryModule
from algotraders.config import FyersCredentials
from algotraders.core.clock import IST
from algotraders.models.enums import Timeframe
from tests.fakes.clock import FakeClock

MOMENT = dt.datetime(2026, 8, 5, 10, 0, tzinfo = IST)

CREDENTIALS = FyersCredentials(
    clientId = "STUB-100", secretKey = "s",
    redirectURL = "https://localhost/cb"
)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(start = MOMENT)


@pytest.fixture
def stubBroker() -> None:
    """
    Register the fake broker, including its stream, and clean up.
    """

    registryModule.registerBroker(
        "stub",
        API = "StubAPI", AUTH = "StubAuthentication",
        RESOLVER = "StubInstrumentResolver", STREAM = "StubStream",
        PATH = "tests.fakes.stubbroker", REQUIRES = []
    )

    try:
        yield
    finally:
        registryModule.BrokerRegistry.pop("stub", None)


async def buildSession(clock : FakeClock) -> object:
    """
    Authenticate a stub session through the real entry point.
    """

    return await initialize(
        broker = "stub", credentials = CREDENTIALS,
        tokenStore = MemoryTokenStore(), clock = clock, authCode = "code"
    )


# ---------------------------------------------------------------------
# session.stream()
# ---------------------------------------------------------------------

async def test_streamIsReachableFromTheSession(
    stubBroker : None, clock : FakeClock
) -> None:
    """
    ``session.stream()`` was documented in CLAUDE.md as public API and
    did not exist.

    Both broker stream classes were complete, tested and registered, and
    nothing ever read ``interface["STREAM"]`` - the entire streaming
    layer was unreachable from the public surface.
    """

    session = await buildSession(clock)

    stream = await session.stream(autoStart = False)

    assert stream is not None
    assert stream.clock is clock
    assert stream.resolver is session.resolver

    await session.close()


async def test_streamIsBuiltOnceAndCached(
    stubBroker : None, clock : FakeClock
) -> None:
    """
    A second call returns the same stream rather than opening a second
    socket against the same per-connection subscription cap.
    """

    session = await buildSession(clock)

    first = await session.stream(autoStart = False)
    second = await session.stream(autoStart = False)

    assert first is second

    await session.close()


async def test_streamIsNotBuiltDuringInitialize(
    stubBroker : None, clock : FakeClock
) -> None:
    """
    Authenticating must not open a websocket. A caller that only wants
    historical data should never hold a live socket it did not ask for.
    """

    session = await buildSession(clock)

    assert session.streamer is None
    assert session.streamFactory is not None

    await session.close()


async def test_closeShutsDownAnOpenStream(
    stubBroker : None, clock : FakeClock
) -> None:
    """
    A session that opened a socket has to close it, or a long lived host
    leaks one connection per session against a three-per-key cap.
    """

    session = await buildSession(clock)
    await session.stream(autoStart = False)

    assert session.streamer is not None

    await session.close()

    assert session.streamer is None


async def test_rebindReachesTheStreamToken(
    stubBroker : None, clock : FakeClock
) -> None:
    """
    The socket authenticates with the raw token rather than the client
    object, so a refresh has to update it separately or the stream keeps
    presenting the dead credential.
    """

    session = await buildSession(clock)
    stream = await session.stream(autoStart = False)

    assert stream.accessToken == "access-from-code"

    clock.advanceTo(dt.datetime(2026, 8, 6, 7, 0, tzinfo = IST))
    await session.refreshIfNeeded()

    assert stream.accessToken == "access-after-refresh"

    await session.close()


async def test_brokerWithoutAStreamSaysSo(
    clock : FakeClock
) -> None:
    """
    A broker registering no STREAM reports a missing capability rather
    than an AttributeError on None.
    """

    from algotraders.errors import MissingDependency

    registryModule.registerBroker(
        "nostream",
        API = "StubAPI", AUTH = "StubAuthentication",
        PATH = "tests.fakes.stubbroker", REQUIRES = []
    )

    try:
        session = await initialize(
            broker = "nostream", credentials = CREDENTIALS,
            tokenStore = MemoryTokenStore(), clock = clock, authCode = "code"
        )

        with pytest.raises(MissingDependency):
            await session.stream()

        await session.close()
    finally:
        registryModule.BrokerRegistry.pop("nostream", None)


# ---------------------------------------------------------------------
# request boundaries
# ---------------------------------------------------------------------

def test_candleRequestBoundsTheInstrumentCount() -> None:
    """
    Unbounded, one request for 5,000 keys over sixteen years of one
    minute data expands to roughly 290,000 rate limited broker calls: it
    pins a worker for hours and spends the account's whole daily quota,
    from a single HTTP request.
    """

    pytest.importorskip("fastapi", reason = "requires the server extra")

    from pydantic import ValidationError
    from algotraders.transport.fastapi import CandleRequest

    with pytest.raises(ValidationError):
        CandleRequest(
            instrumentKeys = [f"NSE:S{index}:EQ" for index in range(51)],
            timeframe = Timeframe.MINUTE_1,
            start = MOMENT - dt.timedelta(days = 1), end = MOMENT
        )

    with pytest.raises(ValidationError):
        CandleRequest(
            instrumentKeys = [], timeframe = Timeframe.MINUTE_1,
            start = MOMENT - dt.timedelta(days = 1), end = MOMENT
        )


def test_candleRequestBoundsTheSpanPerTimeframe() -> None:
    """
    A minute-level request over sixteen years is refused; the same span
    is fine at daily resolution.
    """

    pytest.importorskip("fastapi", reason = "requires the server extra")

    from pydantic import ValidationError
    from algotraders.transport.fastapi import CandleRequest

    with pytest.raises(ValidationError):
        CandleRequest(
            instrumentKeys = ["NSE:SBIN:EQ"], timeframe = Timeframe.MINUTE_1,
            start = MOMENT - dt.timedelta(days = 366 * 16), end = MOMENT
        )

    allowed = CandleRequest(
        instrumentKeys = ["NSE:SBIN:EQ"], timeframe = Timeframe.DAY_1,
        start = MOMENT - dt.timedelta(days = 366 * 16), end = MOMENT
    )

    assert allowed.timeframe is Timeframe.DAY_1


def test_candleRequestRejectsAReversedRange() -> None:
    """
    An end before the start is a caller error, not something to silently
    swap.
    """

    pytest.importorskip("fastapi", reason = "requires the server extra")

    from pydantic import ValidationError
    from algotraders.transport.fastapi import CandleRequest

    with pytest.raises(ValidationError):
        CandleRequest(
            instrumentKeys = ["NSE:SBIN:EQ"], timeframe = Timeframe.DAY_1,
            start = MOMENT, end = MOMENT - dt.timedelta(days = 5)
        )


def test_unhandledErrorsDoNotLeakOperatorDetail() -> None:
    """
    The catch-all returned ``str(error)``, and those messages carry
    detail that has no business crossing the boundary:
    ``InvalidCredentials`` names environment variables and
    ``BrokerError`` interpolates absolute site-packages paths.
    """

    pytest.importorskip("fastapi", reason = "requires the server extra")

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from algotraders.errors import BrokerError
    from algotraders.transport.fastapi import (
        createRouter, installExceptionHandlers
    )

    leak = "/absolute/site-packages/path and ALGOTRADERS_FYERS_SECRET_KEY"

    class Broken:
        brokerName = "fyers"
        resolver = None

        def status(self) -> object:
            raise BrokerError(leak)

    async def provider() -> object:
        return Broken()

    app = FastAPI()
    app.include_router(createRouter(
        sessionProvider = provider,
        dependencies = [Depends(lambda : "user")]
    ))
    installExceptionHandlers(app)

    response = TestClient(
        app, raise_server_exceptions = False
    ).get("/algotraders/session")

    assert response.status_code == 502
    assert leak not in response.text
    assert "site-packages" not in response.text

    # ? the type name is still returned, which is enough to triage
    assert response.json()["type"] == "BrokerError"
