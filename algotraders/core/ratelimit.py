# -*- encoding: utf-8 -*-

"""
Asynchronous Rate Limiting With Shared Buckets and Daily Quotas
-----------------------------------------------------------------

This module exists because of a measured failure. A captured Fyers run
produced 570,646 error lines, of which 170,132 were HTTP 429 and a
further 382,452 were JSON decode failures against empty bodies returned
by an edge that was throttling the caller. Roughly 97 percent of all
failures were rate limiting. The original code had none: it held an
``asyncio.Semaphore`` around a blocking call and hoped.

Three design decisions follow from that, and each one is a trap if
reversed.

**Buckets are shared instances, not per endpoint copies.** Fyers
publishes a single application wide quota of 10 per second, 200 per
minute and 100,000 per day covering every endpoint. Building a separate
bucket for historical calls and another for quotes enforces that quota
several times over and sends far more traffic than the broker allows.
:class:`RateLimitPolicy` constructs the shared buckets once and hands the
*same objects* to every per class limiter. Kite is the opposite case,
publishing a genuine per class limit, and both shapes compose from the
same primitives.

**Admission is serialised.** :meth:`RateLimiter.acquire` holds one lock
while it computes the wait, sleeps and consumes. Without that, N
coroutines all observe the same free token, all wake together and all
consume it, which is precisely the thundering herd that produces a burst
of 429s at every refill boundary. Serialising the decision costs
nothing, since the request itself runs outside the lock, and it makes
the wait sequence deterministic enough to assert exactly.

**Everything reads the injected clock.** No ``time.monotonic``, no
``asyncio.sleep``. That is what lets the tests below assert an exact
sleep sequence and finish in milliseconds instead of minutes.

:NOTE: An in-process limiter is correct for a single owner process.
    Broker limits are enforced per API key, so two uvicorn workers each
    running their own limiter will together exceed the published quota.
    Run ingestion from one process, or implement a distributed limiter
    behind the same interface.
"""

import asyncio
import datetime as dt

from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence

from algotraders.core.clock import BaseClock
from algotraders.errors import DailyQuotaExhausted, RateLimitExceeded
from algotraders.logger import getLogger
from algotraders.models.enums import EndpointClass

logger = getLogger(__name__)

# ! guards against a refill calculation that never satisfies the request,
# which would otherwise spin forever inside acquire()
MAX_ACQUIRE_ROUNDS = 8

# ! sleeping for exactly timeToAvailable() credits back exactly the
# deficit, but only in exact arithmetic. In binary floating point the
# balance can land at 0.9999999999999998, which reads as "not yet" and
# sends acquire() round again for a meaningless ~1e-16 second sleep.
# Token accounting below a nanosecond is noise, so treat it as satisfied
TOKEN_EPSILON = 1e-9


@dataclass(frozen = True)
class RateLimit:
    """
    One published rate limit: a number of calls per time window.

    :type  name: str
    :param name: Identifier used in logs and in the limiter snapshot,
        conventionally ``<broker>.<window>`` such as ``fyers.perSecond``.

    :type  capacity: int
    :param capacity: Calls permitted within the interval.

    :type  interval: float
    :param interval: Window length in seconds.
    """

    name : str
    capacity : int
    interval : float


    def __post_init__(self) -> None:
        if self.capacity <= 0:
            raise ValueError(f"{self.name}: capacity must be positive")

        if self.interval <= 0:
            raise ValueError(f"{self.name}: interval must be positive")


    @property
    def refillRate(self) -> float:
        """
        Tokens restored per second.
        """

        return self.capacity / self.interval


class TokenBucket:
    """
    A continuously refilling token bucket.

    Chosen over a fixed window counter because a fixed window permits a
    double burst across its boundary: 10 calls at 0.99 seconds and 10
    more at 1.01 seconds satisfies "10 per second" twice while sending
    20 calls in 20 milliseconds, which a broker edge sees as abuse.

    :type  limit: RateLimit
    :param limit: The limit this bucket enforces.

    :type  clock: BaseClock
    :param clock: Time source. Uses ``monotonic`` only, so a system
        clock adjustment cannot corrupt the accounting.
    """

    def __init__(self, limit : RateLimit, clock : BaseClock) -> None:
        self.limit = limit
        self.clock = clock

        self.tokens = float(limit.capacity)
        self.updatedAt = clock.monotonic()


    def __refill__(self) -> None:
        """
        Credit tokens for time elapsed since the last update.
        """

        now = self.clock.monotonic()
        elapsed = now - self.updatedAt

        if elapsed <= 0:
            return

        self.tokens = min(
            float(self.limit.capacity),
            self.tokens + elapsed * self.limit.refillRate
        )
        self.updatedAt = now


    def timeToAvailable(self, tokens : int = 1) -> float:
        """
        Seconds to wait before the bucket can supply the given tokens.

        :rtype:   float
        :returns: ``0.0`` when the tokens are already available.
        """

        self.__refill__()

        if self.tokens + TOKEN_EPSILON >= tokens:
            return 0.0

        return (tokens - self.tokens) / self.limit.refillRate


    def consume(self, tokens : int = 1) -> None:
        """
        Deduct tokens.

        Deliberately permits the balance to go negative. The caller has
        already waited on :meth:`timeToAvailable`, and clamping at zero
        here would quietly forgive an over-consumption instead of
        charging it back over the following window.
        """

        self.__refill__()
        self.tokens -= tokens


    @property
    def available(self) -> float:
        """
        Tokens currently available, refilling first.
        """

        self.__refill__()

        return max(0.0, self.tokens)


    def __repr__(self) -> str:
        return (
            f"TokenBucket({self.limit.name}, "
            f"{self.limit.capacity}/{self.limit.interval}s, "
            f"available={self.available:.2f})"
        )


class DailyQuota:
    """
    A calendar day call budget, reset on the IST date boundary.

    Not a rolling 24 hour window. Fyers publishes 100,000 requests per
    day and resets on its own calendar, so a rolling window would refuse
    calls the broker would have accepted and would keep accepting calls
    after the broker's counter had already reset.

    :type  capacity: int
    :param capacity: Calls permitted per calendar day.

    :type  clock: BaseClock
    :param clock: Time source, read through ``tradingDate`` so the
        rollover happens in IST rather than in host local time.
    """

    def __init__(self, name : str, capacity : int, clock : BaseClock) -> None:
        self.name = name
        self.capacity = capacity
        self.clock = clock

        self.used = 0
        self.date = clock.tradingDate()


    def __roll__(self) -> None:
        """
        Reset the counter when the IST date has advanced.
        """

        today = self.clock.tradingDate()

        if today != self.date:
            logger.debug(
                "daily quota %s reset for %s, previous usage %d",
                self.name, today, self.used
            )

            self.date = today
            self.used = 0


    @property
    def remaining(self) -> int:
        """
        Calls left in the current day.
        """

        self.__roll__()

        return max(0, self.capacity - self.used)


    def wouldExceed(self, tokens : int = 1) -> bool:
        """
        Whether consuming the given tokens would breach the budget.
        """

        return tokens > self.remaining


    def consume(self, tokens : int = 1) -> None:
        """
        Charge the given tokens against today's budget.
        """

        self.__roll__()
        self.used += tokens


    def secondsUntilReset(self) -> float:
        """
        Seconds until the IST calendar day rolls over.

        Reported on the exception raised when the budget is exhausted,
        so a caller can schedule a resumption rather than spin.
        """

        now = self.clock.now()
        midnight = (now + dt.timedelta(days = 1)).replace(
            hour = 0, minute = 0, second = 0, microsecond = 0
        )

        return max(0.0, (midnight - now).total_seconds())


class RateLimiter:
    """
    Admission control across several buckets and daily quotas.

    A call is admitted only when every bucket can supply it, so
    composing a per second and a per minute limit needs no special
    handling: the slowest one governs.

    :type  buckets: Sequence[TokenBucket]
    :param buckets: Buckets that must all admit the call. These may be
        shared with other limiters, which is how an application wide
        quota is enforced once rather than once per endpoint class.

    :type  quotas: Sequence[DailyQuota]
    :param quotas: Daily budgets. Exhaustion raises rather than waits,
        because blocking for hours is never the right behaviour.
    """

    def __init__(
        self, buckets : Sequence[TokenBucket],
        quotas : Sequence[DailyQuota], clock : BaseClock,
        lock : Optional[asyncio.Lock] = None
    ) -> None:
        self.buckets = list(buckets)
        self.quotas = list(quotas)
        self.clock = clock

        # ! one lock serialises the whole decide-sleep-consume sequence,
        # which is what prevents every waiter waking onto the same token.
        #
        # It is injected because limiters SHARE bucket objects across
        # endpoint classes. A lock per limiter left each class free to
        # observe and consume the same token concurrently, so five
        # classes in flight admitted five calls where one was permitted -
        # the very thundering herd this lock exists to stop, reintroduced
        # one level up
        self.lock = lock or asyncio.Lock()


    async def acquire(self, tokens : int = 1) -> float:
        """
        Wait until the call is permitted, then charge it.

        :type  tokens: int
        :param tokens: Cost of the call, normally one.

        :raises RateLimitExceeded: A daily quota is exhausted. The
            exception carries ``retryAfter`` set to the seconds
            remaining until the IST midnight reset.

        :rtype:   float
        :returns: Total seconds spent waiting, which the caller can log
            or use to size its own concurrency.
        """

        # ! a bucket can never hold more than its capacity, so a request
        # larger than the smallest bucket is unsatisfiable by
        # construction. Left unchecked it burned every retry round and
        # then consumed anyway, silently exceeding the published limit
        smallest = min(
            (bucket.limit.capacity for bucket in self.buckets), default = tokens
        )

        if tokens > smallest:
            raise ValueError(
                f"cannot acquire {tokens} tokens; the smallest bucket "
                f"holds at most {smallest}"
            )

        async with self.lock:
            for quota in self.quotas:
                if quota.wouldExceed(tokens):
                    raise DailyQuotaExhausted(
                        f"daily quota {quota.name} exhausted "
                        f"({quota.capacity:,} calls)",
                        retryAfter = quota.secondsUntilReset()
                    )

            waited = 0.0
            admitted = False

            for _ in range(MAX_ACQUIRE_ROUNDS):
                delay = max(
                    (bucket.timeToAvailable(tokens) for bucket in self.buckets),
                    default = 0.0
                )

                if delay <= 0:
                    admitted = True

                    break

                await self.clock.sleep(delay)
                waited += delay

            if not admitted:
                # ! never fall through to consume(). Doing so charged the
                # call after failing to make room for it, which is a
                # quota violation dressed up as a successful acquire
                raise RateLimitExceeded(
                    f"could not acquire {tokens} token(s) within "
                    f"{MAX_ACQUIRE_ROUNDS} rounds after waiting "
                    f"{waited:.2f}s",
                    retryAfter = waited or None
                )

            for bucket in self.buckets:
                bucket.consume(tokens)

            for quota in self.quotas:
                quota.consume(tokens)

            return waited


    def snapshot(self) -> Dict[str, float]:
        """
        Current availability across every bucket and quota.

        Intended for a health endpoint or a debug log; it takes no lock
        and is therefore a sample rather than a consistent view.

        :rtype:   Dict[str, float]
        :returns: Limit name to remaining capacity.
        """

        reading : Dict[str, float] = {
            bucket.limit.name : bucket.available for bucket in self.buckets
        }

        for quota in self.quotas:
            reading[quota.name] = float(quota.remaining)

        return reading


class RateLimitPolicy:
    """
    A broker's complete rate limit configuration.

    Builds the buckets once and hands the shared ones to every per class
    limiter, which is the single most important behaviour in this
    module. Constructing a fresh bucket per endpoint class would enforce
    an application wide quota once per class and send several times the
    permitted traffic.

    :type  sharedLimits: Sequence[RateLimit]
    :param sharedLimits: Limits that apply across every endpoint. Fyers
        publishes only these.

    :type  endpointLimits: Optional[Mapping[EndpointClass, Sequence[RateLimit]]]
    :param endpointLimits: Additional limits that apply to one class
        only. Kite publishes a separate limit per class.

    :type  dailyQuota: Optional[int]
    :param dailyQuota: Calls permitted per IST calendar day, if any.

    **Example Usage**

    The Fyers shape, one application wide quota shared by everything.

    .. code-block:: python

        policy = RateLimitPolicy(
            clock = clock,
            sharedLimits = [
                RateLimit("fyers.perSecond", 10, 1.0),
                RateLimit("fyers.perMinute", 200, 60.0)
            ],
            dailyQuota = 100_000, quotaName = "fyers.perDay"
        )

        await policy.limiterFor(EndpointClass.HISTORICAL).acquire()
    """

    def __init__(
        self, clock : BaseClock,
        sharedLimits : Sequence[RateLimit] = (),
        endpointLimits : Optional[
            Mapping[EndpointClass, Sequence[RateLimit]]
        ] = None,
        dailyQuota : Optional[int] = None,
        quotaName : str = "daily"
    ) -> None:
        self.clock = clock

        # ! built once, deliberately shared by every limiter below
        self.sharedBuckets : List[TokenBucket] = [
            TokenBucket(limit, clock) for limit in sharedLimits
        ]

        self.quotas : List[DailyQuota] = (
            [DailyQuota(quotaName, dailyQuota, clock)]
            if dailyQuota is not None else []
        )

        self.endpointBuckets : Dict[EndpointClass, List[TokenBucket]] = {
            endpointClass : [TokenBucket(limit, clock) for limit in limits]
            for endpointClass, limits in (endpointLimits or {}).items()
        }

        # ! one lock for the whole policy, handed to every limiter it
        # builds, because they contend the same shared bucket objects
        self.__lock__ = asyncio.Lock()

        self.__limiters__ : Dict[EndpointClass, RateLimiter] = {}


    def limiterFor(self, endpointClass : EndpointClass) -> RateLimiter:
        """
        Return the limiter governing calls of the given class.

        Cached, so repeated calls yield the same limiter and therefore
        the same lock. A fresh limiter per call would defeat the
        serialised admission that prevents the thundering herd.

        :rtype:   RateLimiter
        :returns: The limiter for that endpoint class.
        """

        if endpointClass not in self.__limiters__:
            self.__limiters__[endpointClass] = RateLimiter(
                buckets = (
                    self.sharedBuckets
                    + self.endpointBuckets.get(endpointClass, [])
                ),
                quotas = self.quotas,
                clock = self.clock,
                lock = self.__lock__
            )

        return self.__limiters__[endpointClass]


    def snapshot(self) -> Dict[str, float]:
        """
        Availability across every bucket the policy owns.
        """

        reading = {
            bucket.limit.name : bucket.available
            for bucket in self.sharedBuckets
        }

        for buckets in self.endpointBuckets.values():
            for bucket in buckets:
                reading[bucket.limit.name] = bucket.available

        for quota in self.quotas:
            reading[quota.name] = float(quota.remaining)

        return reading


__all__ = [
    "RateLimit", "TokenBucket", "DailyQuota", "RateLimiter",
    "RateLimitPolicy", "MAX_ACQUIRE_ROUNDS"
]
