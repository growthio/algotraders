# -*- encoding: utf-8 -*-

"""
Session Resolution Without Ever Prompting
-------------------------------------------

``initialize`` has five branches and picking the wrong one has real
consequences: an unnecessary login costs a human every morning, a missed
refresh burns the Fyers 15 day refresh window, and a silent reuse of an
expired token turns every later call into an authentication failure.
Each branch is asserted by counting what the stub authentication object
was actually asked to do.

The property underneath all of them is that nothing here ever blocks on
input. The original implementation called ``input()`` for the OAuth
code, which a FastAPI worker can never answer.
"""

import asyncio
import datetime as dt

import pytest

from algotraders.auth import BrokerSession, initialize, initializeSync
from algotraders.auth.store import MemoryTokenStore
from algotraders.brokers import registry as registryModule
from algotraders.config import FyersCredentials
from algotraders.core.clock import IST
from algotraders.errors import AuthorizationRequired, TokenExpiredError
from algotraders.models.auth import TokenBundle
from algotraders.models.enums import Capability
from tests.fakes.clock import FakeClock
from tests.fakes.stubbroker import STUB_LOGIN_URL, STUB_POLICY

CLIENT = "STUB-100"

CREDENTIALS = FyersCredentials(
    clientId = CLIENT, secretKey = "stub-secret",
    redirectURL = "https://localhost/callback"
)


@pytest.fixture(autouse = True)
def stubBroker() -> None:
    """
    Register the stub broker for the duration of a test and remove it
    afterwards, so registry state never leaks between tests.
    """

    registryModule.registerBroker(
        "stub",
        API = "StubAPI", AUTH = "StubAuthentication",
        PATH = "tests.fakes.stubbroker", REQUIRES = []
    )

    try:
        yield
    finally:
        registryModule.BrokerRegistry.pop("stub", None)


@pytest.fixture
def clock() -> FakeClock:
    """
    A clock parked at a weekday morning inside market hours.
    """

    return FakeClock(start = dt.datetime(2026, 8, 5, 9, 30, tzinfo = IST))


def storedBundle(clock : FakeClock, issuedAt : dt.datetime) -> TokenBundle:
    """
    Build a bundle as if a previous session had persisted it.
    """

    return TokenBundle.build(
        brokerName = "stub", clientId = CLIENT,
        accessToken = "previously-stored-token",
        refreshToken = "stub-refresh-token",
        issuedAt = issuedAt, policy = STUB_POLICY
    )


async def test_coldStartRaisesAuthorizationRequired(
    clock : FakeClock
) -> None:
    """
    With no stored token and no authorization code, the library hands
    the login back to the host instead of prompting.

    This is the branch that replaces the original ``input()`` call.
    """

    store = MemoryTokenStore()

    with pytest.raises(AuthorizationRequired) as failure:
        await initialize(
            broker = "stub", credentials = CREDENTIALS,
            tokenStore = store, clock = clock, state = "csrf-token"
        )

    assert failure.value.loginURL == STUB_LOGIN_URL
    assert failure.value.brokerName == "stub"
    assert failure.value.state == "csrf-token"


async def test_authorizationCodeCompletesTheLogin(
    clock : FakeClock
) -> None:
    """
    The second call, made by the host's callback route, exchanges the
    code and persists the resulting session.
    """

    store = MemoryTokenStore()

    session = await initialize(
        broker = "stub", credentials = CREDENTIALS, tokenStore = store,
        clock = clock, authCode = "code-from-redirect"
    )

    assert isinstance(session, BrokerSession)
    assert session.isAuthenticated
    assert session.auth.completeLoginCalls == 1
    assert session.auth.refreshCalls == 0

    stored = store.read("stub", CLIENT)

    assert stored is not None
    assert stored.accessToken.get_secret_value() == (
        "access-from-code-from-redirect"
    )


async def test_liveStoredTokenIsReusedWithoutNetworkCalls(
    clock : FakeClock
) -> None:
    """
    A session already established today is reused as is.

    Neither a login nor a refresh may happen, since both cost a broker
    round trip and a refresh consumes part of a limited window.
    """

    store = MemoryTokenStore()
    store.write(storedBundle(clock, clock.now()))

    session = await initialize(
        broker = "stub", credentials = CREDENTIALS, tokenStore = store,
        clock = clock
    )

    assert session.auth.beginLoginCalls == 0
    assert session.auth.completeLoginCalls == 0
    assert session.auth.refreshCalls == 0
    assert session.bundle.accessToken.get_secret_value() == (
        "previously-stored-token"
    )


async def test_expiredTokenIsRefreshedAndPersisted(
    clock : FakeClock
) -> None:
    """
    Yesterday's session is renewed automatically, which is what makes an
    unattended Fyers deployment possible.
    """

    store = MemoryTokenStore()
    store.write(
        storedBundle(clock, dt.datetime(2026, 8, 4, 9, 0, tzinfo = IST))
    )

    session = await initialize(
        broker = "stub", credentials = CREDENTIALS, tokenStore = store,
        clock = clock
    )

    assert session.auth.refreshCalls == 1
    assert session.auth.completeLoginCalls == 0

    stored = store.read("stub", CLIENT)

    assert stored is not None
    assert stored.accessToken.get_secret_value() == "access-after-refresh"


async def test_autoRefreshCanBeDisabled(clock : FakeClock) -> None:
    """
    Turning off automatic refresh forces the interactive path, which a
    host may want when re-establishing consent deliberately.
    """

    store = MemoryTokenStore()
    store.write(
        storedBundle(clock, dt.datetime(2026, 8, 4, 9, 0, tzinfo = IST))
    )

    with pytest.raises(AuthorizationRequired):
        await initialize(
            broker = "stub", credentials = CREDENTIALS, tokenStore = store,
            clock = clock, autoRefresh = False
        )


async def test_expiredRefreshWindowFallsBackToLogin(
    clock : FakeClock
) -> None:
    """
    Past the refresh token's own lifetime, the only way back is a human.

    Fyers refresh tokens last 15 days, so a deployment idle over a long
    break must ask for a login rather than loop on a refresh that can no
    longer succeed.
    """

    store = MemoryTokenStore()
    store.write(
        storedBundle(clock, dt.datetime(2026, 7, 1, 9, 0, tzinfo = IST))
    )

    with pytest.raises(AuthorizationRequired):
        await initialize(
            broker = "stub", credentials = CREDENTIALS, tokenStore = store,
            clock = clock
        )


async def test_sessionCarriesBrokerCapabilities(clock : FakeClock) -> None:
    """
    A host can ask what the broker supports rather than calling and
    handling the resulting failure.
    """

    session = await initialize(
        broker = "stub", credentials = CREDENTIALS,
        tokenStore = MemoryTokenStore(), clock = clock,
        authCode = "code"
    )

    assert session.supports(Capability.HISTORICAL_DATA)
    assert not session.supports(Capability.ORDER_STREAM)


async def test_sessionBuildsTheApiSurface(clock : FakeClock) -> None:
    """
    The registry's API class is constructed and wired to the vendor
    client and the rate limited caller.
    """

    session = await initialize(
        broker = "stub", credentials = CREDENTIALS,
        tokenStore = MemoryTokenStore(), clock = clock, authCode = "code"
    )

    assert session.api is not None
    assert session.api.caller is session.caller
    assert session.api.sessionManager.token == "access-from-code"

    await session.close()


async def test_refreshIfNeededIsANoOpWhileLive(clock : FakeClock) -> None:
    """
    Callers can invoke it before any batch of work without tracking
    expiry themselves.
    """

    session = await initialize(
        broker = "stub", credentials = CREDENTIALS,
        tokenStore = MemoryTokenStore(), clock = clock, authCode = "code"
    )

    assert await session.refreshIfNeeded() is False
    assert session.auth.refreshCalls == 0


async def test_refreshIfNeededRenewsAfterExpiry(clock : FakeClock) -> None:
    """
    Crossing the rollover triggers exactly one refresh.
    """

    store = MemoryTokenStore()
    session = await initialize(
        broker = "stub", credentials = CREDENTIALS, tokenStore = store,
        clock = clock, authCode = "code"
    )

    clock.advanceTo(dt.datetime(2026, 8, 6, 7, 0, tzinfo = IST))

    assert await session.refreshIfNeeded() is True
    assert session.auth.refreshCalls == 1
    assert store.read("stub", CLIENT) == session.bundle


async def test_refreshIfNeededRaisesWhenRenewalIsImpossible(
    clock : FakeClock
) -> None:
    """
    Without a usable refresh token the caller is told plainly that a
    login is required, rather than being handed a dead session.
    """

    store = MemoryTokenStore()
    session = await initialize(
        broker = "stub", credentials = CREDENTIALS, tokenStore = store,
        clock = clock, authCode = "code"
    )

    session.bundle = session.bundle.model_copy(
        update = {"refreshToken" : None}
    )
    clock.advanceTo(dt.datetime(2026, 8, 6, 7, 0, tzinfo = IST))

    with pytest.raises(TokenExpiredError):
        await session.refreshIfNeeded()


async def test_logoutRevokesAndForgets(clock : FakeClock) -> None:
    """
    Logout clears the stored token as well as revoking it, so a later
    start does not resurrect a dead session.
    """

    store = MemoryTokenStore()
    session = await initialize(
        broker = "stub", credentials = CREDENTIALS, tokenStore = store,
        clock = clock, authCode = "code"
    )

    assert await session.logout() is True
    assert store.read("stub", CLIENT) is None


async def test_closeDoesNotRevokeTheToken(clock : FakeClock) -> None:
    """
    Closing releases local resources only. The token stays valid for the
    rest of the trading day and another worker will want it.
    """

    store = MemoryTokenStore()
    session = await initialize(
        broker = "stub", credentials = CREDENTIALS, tokenStore = store,
        clock = clock, authCode = "code"
    )

    await session.close()

    assert session.auth.logoutCalls == 0
    assert store.read("stub", CLIENT) is not None


async def test_sessionWorksAsAnAsyncContextManager(
    clock : FakeClock
) -> None:
    """
    The thread pool is released on exit without the caller remembering.
    """

    async with await initialize(
        broker = "stub", credentials = CREDENTIALS,
        tokenStore = MemoryTokenStore(), clock = clock, authCode = "code"
    ) as session:
        assert session.isAuthenticated

    with pytest.raises(RuntimeError):
        session.caller.executor.submit(lambda : None)


async def test_statusIsSafeToReturnToAFrontend(clock : FakeClock) -> None:
    """
    The status summary carries no token and a masked client id, so it
    needs no filtering at the API boundary.
    """

    session = await initialize(
        broker = "stub", credentials = CREDENTIALS,
        tokenStore = MemoryTokenStore(), clock = clock, authCode = "code"
    )

    status = session.status()
    rendered = status.model_dump_json()

    assert status.isAuthenticated is True
    assert "access-from-code" not in rendered
    assert CLIENT not in rendered
    assert "HISTORICAL_DATA" in rendered

    await session.close()


async def test_representationLeaksNothing(clock : FakeClock) -> None:
    """
    A session appears in log lines and tracebacks, so its repr must be
    safe by construction.
    """

    session = await initialize(
        broker = "stub", credentials = CREDENTIALS,
        tokenStore = MemoryTokenStore(), clock = clock, authCode = "code"
    )

    rendered = repr(session)

    assert "access-from-code" not in rendered
    assert "stub-secret" not in rendered
    assert CLIENT not in rendered

    await session.close()


async def test_noTokenReachesTheLogs(
    clock : FakeClock, caplog : pytest.LogCaptureFixture
) -> None:
    """
    A full initialize at DEBUG level emits no token material anywhere.
    """

    import logging

    with caplog.at_level(logging.DEBUG, logger = "algotraders"):
        session = await initialize(
            broker = "stub", credentials = CREDENTIALS,
            tokenStore = MemoryTokenStore(), clock = clock,
            authCode = "code"
        )

        await session.close()

    assert "access-from-code" not in caplog.text
    assert "stub-secret" not in caplog.text
    assert "stub-refresh-token" not in caplog.text


def test_initializeSyncRefusesInsideAnEventLoop() -> None:
    """
    A web backend must never call the blocking wrapper.

    ``asyncio.run`` refuses to start inside a running loop, and blocking
    a request worker on a broker round trip is exactly what the async
    design exists to avoid, so the failure is explicit.
    """

    async def attempt() -> None:
        with pytest.raises(RuntimeError) as failure:
            initializeSync(broker = "stub", credentials = CREDENTIALS)

        assert "await algotraders.initialize" in str(failure.value)

    asyncio.run(attempt())


def test_initializeSyncWorksOutsideALoop() -> None:
    """
    Scripts and examples get a blocking entry point that does work.
    """

    session = initializeSync(
        broker = "stub", credentials = CREDENTIALS,
        tokenStore = MemoryTokenStore(),
        clock = FakeClock(start = dt.datetime(2026, 8, 5, 9, 30, tzinfo = IST)),
        authCode = "code"
    )

    assert session.isAuthenticated
