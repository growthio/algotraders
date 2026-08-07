# -*- encoding: utf-8 -*-

"""
Exponential Backoff With Jitter for Transient Broker Failures
---------------------------------------------------------------

Retrying is only safe when the operation is idempotent, which for a
broker API is a per endpoint judgement rather than a global setting.
Fetching candles again is harmless. Placing an order again is not: a
``placeOrder`` that timed out may already have reached the exchange, and
a retry then produces two positions where the trader intended one. That
is why :data:`ORDER_RETRY_POLICY` exists and why order endpoints are
wired to it rather than to the default.

What is retried is decided by exception type, not by inspecting messages.
:class:`~algotraders.errors.TransientBrokerError` and its subclass
:class:`~algotraders.errors.RateLimitExceeded` are retryable by
construction; :class:`~algotraders.errors.OrderRejected` and
:class:`~algotraders.errors.DataSubscriptionRequired` deliberately are
not, since a rejection is a decision and a missing paid entitlement is a
billing action.

Jitter is not decoration. Without it, a fleet of workers throttled at the
same instant retries at the same instant, reconverging into the burst
that caused the throttling. The delay is multiplied by a random factor
around one, and the random source is injectable so tests stay exact.
"""

import random as randomModule

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Tuple, Type

from algotraders.core.clock import BaseClock
from algotraders.errors import RateLimitExceeded, TransientBrokerError
from algotraders.logger import getLogger

logger = getLogger(__name__)

DEFAULT_RETRYABLE : Tuple[Type[BaseException], ...] = (
    RateLimitExceeded, TransientBrokerError
)


@dataclass(frozen = True)
class RetryPolicy:
    """
    How many times to retry and how long to wait between attempts.

    :type  maxAttempts: int
    :param maxAttempts: Total attempts including the first. ``1``
        disables retrying entirely, which is the correct setting for any
        endpoint that changes account state.

    :type  baseDelay: float
    :param baseDelay: Delay before the second attempt, in seconds. Each
        subsequent delay doubles.

    :type  maxDelay: float
    :param maxDelay: Ceiling on the computed delay, before jitter.

    :type  jitter: float
    :param jitter: Fractional randomisation, so ``0.3`` spreads each
        delay across plus or minus 30 percent. Set to ``0`` for a fully
        deterministic schedule.

    :type  retryOn: Tuple[Type[BaseException], ...]
    :param retryOn: Exception types worth retrying. Anything else
        propagates on the first attempt.
    """

    maxAttempts : int = 5
    baseDelay : float = 0.5
    maxDelay : float = 30.0
    jitter : float = 0.3
    retryOn : Tuple[Type[BaseException], ...] = field(
        default = DEFAULT_RETRYABLE
    )


    def __post_init__(self) -> None:
        if self.maxAttempts < 1:
            raise ValueError("maxAttempts must be at least 1")

        if self.baseDelay < 0 or self.maxDelay < 0:
            raise ValueError("delays must not be negative")

        if not 0 <= self.jitter <= 1:
            raise ValueError("jitter must be a fraction between 0 and 1")


    def shouldRetry(self, error : BaseException, attempt : int) -> bool:
        """
        Whether a failure on the given attempt warrants another try.

        :type  attempt: int
        :param attempt: One based attempt number that just failed.
        """

        if attempt >= self.maxAttempts:
            return False

        # ! an error may opt out of retrying regardless of the policy.
        # DailyQuotaExhausted subclasses RateLimitExceeded, so a plain
        # isinstance test against retryOn would schedule a multi-hour
        # sleep for something that cannot clear before IST midnight
        if not getattr(error, "retryable", True):
            return False

        return isinstance(error, self.retryOn)


    def delayFor(
        self, attempt : int, error : Optional[BaseException] = None,
        random : Optional[randomModule.Random] = None
    ) -> float:
        """
        Compute the wait before the next attempt.

        A broker supplied ``retryAfter`` always wins over the computed
        schedule. The broker knows when its own window resets, and
        guessing shorter simply earns another rejection.

        :type  attempt: int
        :param attempt: One based attempt number that just failed.

        :type  error: Optional[BaseException]
        :param error: The failure, inspected for ``retryAfter``.

        :type  random: Optional[randomModule.Random]
        :param random: Source of jitter. Injectable so a test can seed
            it and assert an exact delay sequence.

        :rtype:   float
        :returns: Seconds to wait.
        """

        retryAfter = getattr(error, "retryAfter", None)

        if retryAfter is not None:
            # ! capped. An uncapped broker hint is a denial of service on
            # ourselves: a spent daily quota reports tens of thousands of
            # seconds, and honouring that verbatim pinned a coroutine for
            # days while holding a semaphore slot
            return min(self.maxDelay, max(0.0, float(retryAfter)))

        delay = min(self.maxDelay, self.baseDelay * (2 ** (attempt - 1)))

        if not self.jitter:
            return delay

        source = random or randomModule
        factor = source.uniform(1.0 - self.jitter, 1.0 + self.jitter)

        return max(0.0, delay * factor)


# ! order endpoints never auto retry. A placement that timed out may
# already be live at the exchange, and a second attempt is a double fill
ORDER_RETRY_POLICY = RetryPolicy(maxAttempts = 1)

DEFAULT_RETRY_POLICY = RetryPolicy()


async def retryAsync(
    operation : Callable[[], Awaitable[Any]],
    policy : RetryPolicy,
    clock : BaseClock,
    description : str = "broker call",
    onRetry : Optional[Callable[[int, float, BaseException], None]] = None,
    random : Optional[randomModule.Random] = None
) -> Any:
    """
    Run an awaitable operation, retrying transient failures.

    :type  operation: Callable[[], Awaitable[Any]]
    :param operation: Zero argument factory returning a fresh awaitable.
        A factory rather than a coroutine object, because a coroutine
        cannot be awaited twice and a retry needs a new one.

    :type  policy: RetryPolicy
    :param policy: Attempt count and backoff schedule.

    :type  clock: BaseClock
    :param clock: Time source, so waits are testable.

    :type  description: str
    :param description: Label used in the retry log line.

    :type  onRetry: Optional[Callable[[int, float, BaseException], None]]
    :param onRetry: Hook called with the attempt number, the delay and
        the failure before each retry. Useful for metrics.

    **Example Usage**

    .. code-block:: python

        candles = await retryAsync(
            lambda : caller.call(EndpointClass.HISTORICAL, fetch, symbol),
            policy = DEFAULT_RETRY_POLICY, clock = clock
        )

    :raises BaseException: The final failure, re-raised unchanged once
        attempts are exhausted or the error is not retryable.

    :rtype:   Any
    :returns: Whatever the operation returned.
    """

    attempt = 0

    while True:
        attempt += 1

        try:
            return await operation()
        except BaseException as error:
            if not policy.shouldRetry(error, attempt):
                if attempt > 1:
                    logger.warning(
                        "%s failed after %d attempts: %s",
                        description, attempt, type(error).__name__
                    )

                raise

            delay = policy.delayFor(attempt, error = error, random = random)

            logger.info(
                "%s attempt %d/%d failed with %s, retrying in %.2fs",
                description, attempt, policy.maxAttempts,
                type(error).__name__, delay
            )

            if onRetry is not None:
                onRetry(attempt, delay, error)

            await clock.sleep(delay)


__all__ = [
    "RetryPolicy", "retryAsync", "DEFAULT_RETRYABLE",
    "DEFAULT_RETRY_POLICY", "ORDER_RETRY_POLICY"
]
