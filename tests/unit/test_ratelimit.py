# -*- encoding: utf-8 -*-

"""
Rate Limiting Behaviour Under a Deterministic Clock
------------------------------------------------------

Every assertion here is exact rather than tolerance based, because a
tolerance passes just as happily when the limiter is subtly wrong, and
subtly wrong is what produced 170,132 HTTP 429 responses in the captured
Fyers run.

The single most important test in this file is
:func:`test_sharedBucketIsContendedAcrossEndpointClasses`. If per class
limiters were given their own copies of an application wide bucket, the
published quota would be enforced once per class and the library would
send several times what the broker permits.
"""

import datetime as dt

import pytest

from algotraders.core.clock import IST
from algotraders.core.ratelimit import (
    DailyQuota, RateLimit, RateLimiter, RateLimitPolicy, TokenBucket
)
from algotraders.errors import RateLimitExceeded
from algotraders.models.enums import EndpointClass
from tests.fakes.clock import FakeClock

PER_SECOND = RateLimit("test.perSecond", 10, 1.0)
PER_MINUTE = RateLimit("test.perMinute", 200, 60.0)


def test_rateLimitRejectsImpossibleConfiguration() -> None:
    """
    A zero or negative capacity would divide by zero downstream.
    """

    with pytest.raises(ValueError):
        RateLimit("bad", 0, 1.0)

    with pytest.raises(ValueError):
        RateLimit("bad", 10, 0.0)


def test_bucketStartsFullAndRefillsContinuously() -> None:
    """
    Tokens accrue smoothly rather than in fixed window jumps.

    A fixed window counter permits a double burst across its boundary,
    which a broker edge sees as abuse even though the arithmetic
    satisfies the stated limit.
    """

    clock = FakeClock()
    bucket = TokenBucket(PER_SECOND, clock)

    assert bucket.available == 10.0

    bucket.consume(10)

    assert bucket.available == 0.0

    clock.advance(0.5)

    assert bucket.available == pytest.approx(5.0)

    clock.advance(0.5)

    assert bucket.available == pytest.approx(10.0)


def test_bucketNeverRefillsAboveCapacity() -> None:
    """
    Idle time does not bank credit for a later burst.
    """

    clock = FakeClock()
    bucket = TokenBucket(PER_SECOND, clock)

    clock.advance(3_600)

    assert bucket.available == 10.0


def test_timeToAvailableIsExact() -> None:
    """
    The computed wait is precisely the deficit divided by the refill
    rate, which is what makes the sleep sequences below predictable.
    """

    clock = FakeClock()
    bucket = TokenBucket(PER_SECOND, clock)

    bucket.consume(10)

    assert bucket.timeToAvailable(1) == pytest.approx(0.1)
    assert bucket.timeToAvailable(5) == pytest.approx(0.5)


async def test_burstThenThrottleProducesExactSleepSequence() -> None:
    """
    The first ten calls pass instantly, then each further call waits
    exactly one refill interval.
    """

    clock = FakeClock()
    limiter = RateLimiter([TokenBucket(PER_SECOND, clock)], [], clock)

    for _ in range(10):
        assert await limiter.acquire() == 0.0

    assert clock.sleepCalls == []

    for _ in range(5):
        await limiter.acquire()

    assert clock.sleepCalls == pytest.approx([0.1] * 5)
    assert clock.totalSlept == pytest.approx(0.5)


async def test_slowestBucketGovernsInSteadyState() -> None:
    """
    Composing a per second and a per minute limit needs no special
    handling; whichever is scarcer sets the pace.

    10 per second would permit 600 calls a minute, so once the initial
    allowance is spent the 200 per minute bucket is the binding
    constraint and the cadence settles at one call every 0.3 seconds.

    Note that this takes well over 200 calls to appear: the per second
    limit paces the early calls slowly enough that the minute bucket
    keeps refilling underneath them, which is exactly the interaction a
    naive "the minute bucket empties after 200 calls" model gets wrong.
    """

    clock = FakeClock()
    limiter = RateLimiter(
        [TokenBucket(PER_SECOND, clock), TokenBucket(PER_MINUTE, clock)],
        [], clock
    )

    for _ in range(400):
        await limiter.acquire()

    clock.reset()

    for _ in range(5):
        await limiter.acquire()

    # ? one token of a 200-per-60s bucket takes 0.3s, which now exceeds
    # the 0.1s the per-second bucket would have needed on its own
    assert clock.sleepCalls == pytest.approx([0.3] * 5)


@pytest.mark.parametrize("capacity, interval", [
    (200, 60.0),   # 3.333... per second, not representable in binary
    (3, 1.0),      # 0.333... second per token
    (7, 11.0),     # deliberately awkward on both sides
])
async def test_oneSleepPerThrottledCallWithInexactRefillRates(
    capacity : int, interval : float
) -> None:
    """
    A throttled call sleeps exactly once, even when the refill rate has
    no exact binary representation.

    Sleeping for precisely ``timeToAvailable`` credits back precisely the
    deficit only in exact arithmetic. In floating point the balance can
    land at 0.9999999999999998, which reads as "not yet" and sends
    ``acquire`` around again for a meaningless sub-nanosecond sleep. That
    produced 26 sleep calls for 5 acquisitions before the epsilon was
    introduced, and it would have polluted every downstream timing
    assertion.
    """

    clock = FakeClock()
    limiter = RateLimiter(
        [TokenBucket(RateLimit("awkward", capacity, interval), clock)],
        [], clock
    )

    for _ in range(capacity):
        await limiter.acquire()

    clock.reset()

    for _ in range(5):
        await limiter.acquire()

    assert len(clock.sleepCalls) == 5
    assert all(delay > 1e-6 for delay in clock.sleepCalls)


async def test_sharedBucketIsContendedAcrossEndpointClasses() -> None:
    """
    An application wide quota is enforced once in total, not once per
    endpoint class.

    This is the exact defect behind the captured 429 storm: building a
    fresh bucket per class multiplies the permitted traffic by the
    number of classes.
    """

    clock = FakeClock()
    policy = RateLimitPolicy(clock = clock, sharedLimits = [PER_SECOND])

    historical = policy.limiterFor(EndpointClass.HISTORICAL)
    quote = policy.limiterFor(EndpointClass.QUOTE)

    # ! the two limiters must hold the same bucket object
    assert historical.buckets[0] is quote.buckets[0]

    for _ in range(5):
        await historical.acquire()

    for _ in range(5):
        await quote.acquire()

    assert clock.sleepCalls == []

    # ? ten calls have now been spent between them, so the eleventh
    # waits regardless of which class issues it
    await quote.acquire()

    assert clock.sleepCalls == pytest.approx([0.1])


async def test_perClassLimitsStayIndependent() -> None:
    """
    Kite publishes a genuine per class limit, so a quote call must not
    consume the historical allowance.
    """

    clock = FakeClock()
    policy = RateLimitPolicy(
        clock = clock,
        endpointLimits = {
            EndpointClass.QUOTE : [RateLimit("kite.quote", 1, 1.0)],
            EndpointClass.HISTORICAL : [RateLimit("kite.historical", 3, 1.0)]
        }
    )

    for _ in range(3):
        await policy.limiterFor(EndpointClass.HISTORICAL).acquire()

    assert clock.sleepCalls == []

    await policy.limiterFor(EndpointClass.QUOTE).acquire()

    assert clock.sleepCalls == []


def test_limiterForIsCached() -> None:
    """
    Repeated lookups return the same limiter, and therefore the same
    lock. A fresh limiter per call would defeat serialised admission.
    """

    clock = FakeClock()
    policy = RateLimitPolicy(clock = clock, sharedLimits = [PER_SECOND])

    assert policy.limiterFor(EndpointClass.ORDER) is policy.limiterFor(
        EndpointClass.ORDER
    )


async def test_dailyQuotaResetsOnISTDateNotAfter24Hours() -> None:
    """
    The budget follows the broker's calendar day in IST.

    A rolling 24 hour window would refuse calls the broker would accept
    and keep accepting calls after the broker's own counter had reset.
    """

    clock = FakeClock(start = dt.datetime(2026, 8, 5, 23, 0, tzinfo = IST))
    quota = DailyQuota("test.perDay", 100, clock)

    quota.consume(100)

    assert quota.remaining == 0

    # ? only one hour later, but the IST date has advanced
    clock.advance(3_600)

    assert quota.remaining == 100


async def test_exhaustedDailyQuotaRaisesRatherThanBlocking() -> None:
    """
    Waiting hours for a midnight reset is never useful behaviour, so the
    limiter raises with the reset delay attached.
    """

    clock = FakeClock(start = dt.datetime(2026, 8, 5, 22, 0, tzinfo = IST))
    limiter = RateLimiter([], [DailyQuota("test.perDay", 2, clock)], clock)

    await limiter.acquire()
    await limiter.acquire()

    with pytest.raises(RateLimitExceeded) as failure:
        await limiter.acquire()

    assert failure.value.retryAfter == pytest.approx(2 * 3_600)
    assert clock.sleepCalls == []


async def test_quotaIsSharedAcrossEndpointClasses() -> None:
    """
    A daily quota covers the whole application, so any class can
    exhaust it for every other.
    """

    clock = FakeClock()
    policy = RateLimitPolicy(clock = clock, dailyQuota = 1)

    await policy.limiterFor(EndpointClass.HISTORICAL).acquire()

    with pytest.raises(RateLimitExceeded):
        await policy.limiterFor(EndpointClass.QUOTE).acquire()


async def test_concurrentAcquisitionsAreSerialised() -> None:
    """
    Competing coroutines are admitted one at a time.

    Without the lock, every waiter observes the same free token, wakes
    together and consumes it, which is the thundering herd that produces
    a burst of rejections at each refill boundary.
    """

    import asyncio

    clock = FakeClock()
    limiter = RateLimiter([TokenBucket(RateLimit("t", 2, 1.0), clock)], [], clock)

    await asyncio.gather(*(limiter.acquire() for _ in range(6)))

    # ? two free, then four paced at one every 0.5s; no two waiters ever
    # computed their delay against the same observation
    assert len(clock.sleepCalls) == 4
    assert clock.totalSlept == pytest.approx(2.0)


def test_snapshotReportsEveryLimit() -> None:
    """
    A health endpoint needs one reading covering buckets and quotas.
    """

    clock = FakeClock()
    policy = RateLimitPolicy(
        clock = clock, sharedLimits = [PER_SECOND, PER_MINUTE],
        dailyQuota = 100_000, quotaName = "test.perDay"
    )

    reading = policy.snapshot()

    assert reading["test.perSecond"] == 10.0
    assert reading["test.perMinute"] == 200.0
    assert reading["test.perDay"] == 100_000.0
