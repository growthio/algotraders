# -*- encoding: utf-8 -*-

"""
Correctness Regressions Found by the Stage 3 Audit
-----------------------------------------------------

Six defects that produced wrong behaviour rather than a crash, which is
the harder kind to notice. Each one here had a plausible-looking
outcome: a refresh that reported success, an aggregate that returned a
number, a limiter that admitted a call. The tests state the wrong
outcome rather than the API, because that is what makes a regression
recognisable.
"""

import asyncio
import datetime as dt

import pytest

from algotraders.core.clock import IST
from algotraders.core.ratelimit import (
    DailyQuota, RateLimit, RateLimiter, RateLimitPolicy, TokenBucket
)
from algotraders.core.resample import aggregateCandles
from algotraders.core.retry import RetryPolicy, retryAsync
from algotraders.errors import (
    DailyQuotaExhausted, RateLimitExceeded, UnsupportedTimeframe
)
from algotraders.models.auth import TokenBundle, TokenPolicy
from algotraders.models.enums import EndpointClass, Timeframe
from algotraders.models.market import Candle
from tests.fakes.clock import FakeClock

MOMENT = dt.datetime(2026, 8, 5, 10, 0, tzinfo = IST)

POLICY = TokenPolicy(
    rolloverHourIST = 6,
    accessTokenLifetime = dt.timedelta(days = 1),
    supportsRefresh = True
)


@pytest.fixture
def clock() -> FakeClock:
    """
    A clock parked at a weekday morning inside market hours.
    """

    return FakeClock(start = MOMENT)


@pytest.fixture
def stubBroker() -> None:
    """
    Register the fake broker and remove it afterwards, so registry state
    never leaks between tests.
    """

    from algotraders.brokers import registry as registryModule

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


def candle(
    day : int, volume : int, source : str = "fyers",
    timeframe : Timeframe = Timeframe.DAY_1
) -> Candle:
    """
    Build one daily bar for the aggregation tests.
    """

    return Candle(
        instrumentKey = "NSE:SBIN:EQ", timeframe = timeframe,
        timestamp = dt.datetime(2026, 1, day, 9, 15, tzinfo = IST),
        openPrice = 100.0, highPrice = 102.0, lowPrice = 99.0,
        closePrice = 101.0, volume = volume, source = source
    )


# ---------------------------------------------------------------------
# H1 - a refreshed token has to reach the vendor client
# ---------------------------------------------------------------------

async def test_refreshRebindsTheVendorClient(
    stubBroker : None, clock : FakeClock
) -> None:
    """
    ``buildSessionManager`` bakes the access token into the vendor
    client at construction.

    Updating only ``self.bundle`` left the API calling the broker with
    the token that had just expired: the refresh reported success,
    ``status()`` reported a healthy session, and every request failed
    authentication until a human noticed - at the daily rollover, which
    is exactly when unattended operation is supposed to work.
    """

    from algotraders.auth import initialize
    from algotraders.auth.store import MemoryTokenStore
    from algotraders.config import FyersCredentials

    credentials = FyersCredentials(
        clientId = "STUB-100", secretKey = "s",
        redirectURL = "https://localhost/cb"
    )

    session = await initialize(
        broker = "stub", credentials = credentials,
        tokenStore = MemoryTokenStore(), clock = clock, authCode = "code"
    )

    before = session.api.sessionManager

    clock.advanceTo(dt.datetime(2026, 8, 6, 7, 0, tzinfo = IST))

    assert await session.refreshIfNeeded() is True

    assert session.api.sessionManager is not before
    assert session.api.sessionManager.token == "access-after-refresh"
    assert session.bundle.accessToken.get_secret_value() == (
        "access-after-refresh"
    )

    await session.close()


# ---------------------------------------------------------------------
# H6 - aggregation must not fold across timeframes or brokers
# ---------------------------------------------------------------------

def test_mixedTimeframesAreRejected() -> None:
    """
    Passing 1D and 1m bars for the same day summed both into one month,
    doubling the volume with nothing about the result looking wrong.
    """

    mixed = [
        candle(5, 1_000),
        candle(5, 1_000, timeframe = Timeframe.MINUTE_1)
    ]

    with pytest.raises(UnsupportedTimeframe):
        aggregateCandles(mixed, Timeframe.MONTH_1)


def test_sourcesAreFoldedSeparately() -> None:
    """
    A Fyers bar and a Kite bar for the same day used to become one bar
    with doubled volume, attributed to whichever arrived first.

    That is the provenance loss the mandatory ``source`` field exists to
    prevent.
    """

    folded = aggregateCandles(
        [candle(5, 1_000, source = "fyers"), candle(5, 1_000, source = "kite")],
        Timeframe.MONTH_1
    )

    assert len(folded) == 2
    assert {bar.source for bar in folded} == {"fyers", "kite"}
    assert all(bar.volume == 1_000 for bar in folded)


def test_singleSourceAggregationStillWorks() -> None:
    """
    The stricter grouping must not break the normal path.
    """

    folded = aggregateCandles(
        [candle(5, 1_000), candle(6, 500)], Timeframe.MONTH_1
    )

    assert len(folded) == 1
    assert folded[0].volume == 1_500


# ---------------------------------------------------------------------
# H7 - a broker hint must not become a multi-day sleep
# ---------------------------------------------------------------------

async def test_retryAfterIsCappedAtMaxDelay() -> None:
    """
    An uncapped broker hint is a denial of service on ourselves.

    A spent daily quota reports tens of thousands of seconds; honouring
    that verbatim pinned a coroutine for days while it held a semaphore
    slot, which is the opposite of what a rate limiter is for.
    """

    clock = FakeClock()
    policy = RetryPolicy(maxAttempts = 3, maxDelay = 30.0, jitter = 0.0)

    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1

        if attempts < 3:
            raise RateLimitExceeded("slow down", retryAfter = 52_800.0)

        return "ok"

    assert await retryAsync(operation, policy, clock) == "ok"
    assert clock.sleepCalls == [30.0, 30.0]


async def test_exhaustedDailyQuotaIsNotRetried() -> None:
    """
    A per second limit clears in a second; a daily quota clears at the
    next IST midnight.

    Retrying the second one means sleeping for hours inside a request
    handler, so it opts out of retrying entirely even though it
    subclasses the retryable type.
    """

    clock = FakeClock()
    attempts = 0

    async def operation() -> None:
        nonlocal attempts
        attempts += 1

        raise DailyQuotaExhausted("spent", retryAfter = 52_800.0)

    with pytest.raises(DailyQuotaExhausted):
        await retryAsync(
            operation, RetryPolicy(maxAttempts = 5, jitter = 0.0), clock
        )

    assert attempts == 1
    assert clock.sleepCalls == []


async def test_quotaExhaustionRaisesTheNonRetryableType(
    clock : FakeClock
) -> None:
    """
    The limiter raises the type the retry helper will refuse, not the
    generic one it would happily sleep on.
    """

    limiter = RateLimiter(
        [], [DailyQuota("test.perDay", 1, clock)], clock
    )

    await limiter.acquire()

    with pytest.raises(DailyQuotaExhausted):
        await limiter.acquire()


# ---------------------------------------------------------------------
# H8 - shared buckets need a shared lock
# ---------------------------------------------------------------------

async def test_endpointClassesContendOneLock(clock : FakeClock) -> None:
    """
    Limiters share bucket objects across endpoint classes.

    With a lock per limiter, five classes in flight each took an
    uncontended lock, all observed the same free token, all slept the
    same interval and all consumed it - admitting five calls where one
    was permitted. That is the thundering herd the lock exists to stop,
    reintroduced one level up.
    """

    policy = RateLimitPolicy(
        clock = clock, sharedLimits = [RateLimit("shared", 1, 1.0)]
    )

    classes = [
        EndpointClass.HISTORICAL, EndpointClass.QUOTE, EndpointClass.ORDER,
        EndpointClass.PORTFOLIO, EndpointClass.GENERAL
    ]

    limiters = [policy.limiterFor(entry) for entry in classes]

    # ! the same lock object, or the serialisation is per class only
    assert len({id(entry.lock) for entry in limiters}) == 1
    assert len({id(entry.buckets[0]) for entry in limiters}) == 1

    await asyncio.gather(*(entry.acquire() for entry in limiters))

    # ? one free, then four paced at a full interval each
    assert len(clock.sleepCalls) == 4
    assert clock.totalSlept == pytest.approx(4.0)


async def test_unsatisfiableRequestIsRejectedNotConsumed(
    clock : FakeClock
) -> None:
    """
    A bucket never holds more than its capacity, so a larger request can
    never be satisfied.

    Unchecked, it burned every retry round and then consumed anyway,
    which is a quota violation dressed up as a successful acquire.
    """

    limiter = RateLimiter(
        [TokenBucket(RateLimit("small", 10, 1.0), clock)], [], clock
    )

    with pytest.raises(ValueError):
        await limiter.acquire(tokens = 15)

    assert clock.sleepCalls == []


# ---------------------------------------------------------------------
# naive `now` and NaN
# ---------------------------------------------------------------------

def test_naiveNowIsRejectedByExpiryChecks() -> None:
    """
    A host on a UTC container calling ``isExpired(datetime.utcnow())``
    got a naive value read as IST, so a token that died at 06:00 IST
    reported live for another five and a half hours.
    """

    bundle = TokenBundle.build(
        brokerName = "fyers", clientId = "X-100", accessToken = "t",
        issuedAt = MOMENT, policy = POLICY
    )

    with pytest.raises(ValueError):
        bundle.isExpired(dt.datetime(2026, 8, 6, 1, 30))

    with pytest.raises(ValueError):
        bundle.secondsRemaining(dt.datetime(2026, 8, 6, 1, 30))


def test_utcNowIsHandledCorrectlyWhenAware() -> None:
    """
    The aware equivalent of the same moment resolves properly.
    """

    bundle = TokenBundle.build(
        brokerName = "fyers", clientId = "X-100", accessToken = "t",
        issuedAt = MOMENT, policy = POLICY
    )

    # ? 01:30 UTC is 07:00 IST, past the 06:00 rollover
    assert bundle.isExpired(
        dt.datetime(2026, 8, 6, 1, 30, tzinfo = dt.timezone.utc)
    ) is True


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonFinitePricesAreRejected(value : float) -> None:
    """
    A NaN price serialised as the bare token ``nan``, which is not valid
    JSON, and made ``max()``/``min()`` order dependent so the same
    candles aggregated to different highs depending on input ordering.
    """

    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Candle(
            instrumentKey = "NSE:SBIN:EQ", timeframe = Timeframe.DAY_1,
            timestamp = MOMENT, openPrice = value, highPrice = 102.0,
            lowPrice = 99.0, closePrice = 101.0, volume = 10,
            source = "fyers"
        )


# ---------------------------------------------------------------------
# H5 - an expired session must close the socket
# ---------------------------------------------------------------------

async def test_expiredTokenClosesTheSocket() -> None:
    """
    Abandoning the supervisor left the vendor SDK's own reconnect
    hammering the broker with a credential that will never be accepted
    again.
    """

    from algotraders.errors import TokenExpiredError
    from tests.unit.test_streaming import FakeStream, RecordingSink

    clock = FakeClock(start = MOMENT)
    observed = []

    stream = FakeStream(
        sessionManager = None, sink = RecordingSink(), clock = clock,
        onError = observed.append,
        failConnect = TokenExpiredError("expired at 06:00 IST")
    )

    await stream.__supervise__()

    assert stream.disconnectCalls >= 1
    assert stream.isConnected is False
    assert len(observed) == 1


async def test_supervisorCanBeRestartedAfterItStops() -> None:
    """
    A supervisor that returned left a finished task in place, and the
    ``is None`` guard meant a later ``start()`` silently ran with no
    staleness detection at all.
    """

    from tests.unit.test_streaming import FakeStream, RecordingSink

    clock = FakeClock(start = MOMENT)
    stream = FakeStream(
        sessionManager = None, sink = RecordingSink(), clock = clock
    )

    await stream.start()
    first = stream.__supervisor__

    first.cancel()

    try:
        await first
    except asyncio.CancelledError:
        pass

    await stream.start()

    assert stream.__supervisor__ is not first
    assert not stream.__supervisor__.done()

    await stream.stop()
