# -*- encoding: utf-8 -*-

"""
Retry Eligibility and Backoff Scheduling
------------------------------------------

The eligibility tests matter more than the timing ones. Retrying a
rejected order or a missing paid entitlement is not merely wasteful, it
can create a second live position, so the boundary between retryable and
terminal is asserted by exception type rather than inferred.
"""

import random

import pytest

from algotraders.core.retry import (
    DEFAULT_RETRY_POLICY, ORDER_RETRY_POLICY, RetryPolicy, retryAsync
)
from algotraders.errors import (
    DataSubscriptionRequired, InvalidOrder, OrderRejected, RateLimitExceeded,
    TransientBrokerError
)
from tests.fakes.clock import FakeClock

# ? jitter disabled so the schedule is exactly reproducible
EXACT = RetryPolicy(maxAttempts = 5, baseDelay = 0.5, jitter = 0.0)


class ScriptedOperation:
    """
    An awaitable factory that fails a set number of times, then
    succeeds. Records how many attempts were actually made.
    """

    def __init__(
        self, failures : int, error : BaseException, result : str = "ok"
    ) -> None:
        self.failures = failures
        self.error = error
        self.result = result
        self.attempts = 0


    async def __call__(self) -> str:
        self.attempts += 1

        if self.attempts <= self.failures:
            raise self.error

        return self.result


async def test_succeedsWithoutRetryingWhenTheFirstAttemptWorks() -> None:
    """
    A healthy call costs nothing extra.
    """

    clock = FakeClock()
    operation = ScriptedOperation(failures = 0, error = RuntimeError())

    assert await retryAsync(operation, EXACT, clock) == "ok"
    assert operation.attempts == 1
    assert clock.sleepCalls == []


async def test_backoffDoublesWithoutJitter() -> None:
    """
    Delays follow the documented schedule exactly.
    """

    clock = FakeClock()
    operation = ScriptedOperation(
        failures = 3, error = TransientBrokerError("gateway")
    )

    assert await retryAsync(operation, EXACT, clock) == "ok"
    assert operation.attempts == 4
    assert clock.sleepCalls == pytest.approx([0.5, 1.0, 2.0])


async def test_delayIsCappedAtMaxDelay() -> None:
    """
    Exponential growth stops at the ceiling instead of running away.
    """

    clock = FakeClock()
    policy = RetryPolicy(
        maxAttempts = 6, baseDelay = 1.0, maxDelay = 4.0, jitter = 0.0
    )
    operation = ScriptedOperation(
        failures = 5, error = TransientBrokerError("gateway")
    )

    await retryAsync(operation, policy, clock)

    assert clock.sleepCalls == pytest.approx([1.0, 2.0, 4.0, 4.0, 4.0])


async def test_exhaustedAttemptsReraiseTheOriginalError() -> None:
    """
    The caller sees the broker's failure, not a wrapper that hides it.
    """

    clock = FakeClock()
    failure = TransientBrokerError("still down")
    operation = ScriptedOperation(failures = 99, error = failure)

    with pytest.raises(TransientBrokerError) as raised:
        await retryAsync(operation, EXACT, clock)

    assert raised.value is failure
    assert operation.attempts == 5
    assert len(clock.sleepCalls) == 4


@pytest.mark.parametrize("error", [
    OrderRejected("rejected", brokerMessage = "insufficient funds"),
    DataSubscriptionRequired("historical data is a paid add-on"),
    InvalidOrder("limit order needs a limit price"),
    ValueError("programming error"),
])
async def test_nonRetryableErrorsFailImmediately(
    error : BaseException
) -> None:
    """
    A decision, a billing state and a programming bug are all final.

    Retrying an order rejection in particular can create a second live
    position, which is the worst possible outcome of a "helpful" retry.
    """

    clock = FakeClock()
    operation = ScriptedOperation(failures = 99, error = error)

    with pytest.raises(type(error)):
        await retryAsync(operation, EXACT, clock)

    assert operation.attempts == 1
    assert clock.sleepCalls == []


async def test_orderPolicyNeverRetries() -> None:
    """
    The shared order policy is a single attempt.

    A placeOrder that timed out may already have reached the exchange,
    so there is no safe automatic recovery, only reconciliation.
    """

    clock = FakeClock()
    operation = ScriptedOperation(
        failures = 99, error = TransientBrokerError("timeout")
    )

    with pytest.raises(TransientBrokerError):
        await retryAsync(operation, ORDER_RETRY_POLICY, clock)

    assert operation.attempts == 1
    assert clock.sleepCalls == []


async def test_brokerSuppliedRetryAfterOverridesTheSchedule() -> None:
    """
    The broker knows when its own window resets, so its instruction
    wins over the computed backoff.
    """

    clock = FakeClock()
    operation = ScriptedOperation(
        failures = 2,
        error = RateLimitExceeded("slow down", retryAfter = 7.5)
    )

    await retryAsync(operation, EXACT, clock)

    assert clock.sleepCalls == pytest.approx([7.5, 7.5])


async def test_jitterSpreadsTheDelayAroundTheSchedule() -> None:
    """
    Jitter keeps a throttled fleet from retrying in lockstep and
    reconverging into the burst that caused the throttling.
    """

    clock = FakeClock()
    policy = RetryPolicy(maxAttempts = 4, baseDelay = 1.0, jitter = 0.3)
    operation = ScriptedOperation(
        failures = 3, error = TransientBrokerError("busy")
    )

    await retryAsync(
        operation, policy, clock, random = random.Random(20260805)
    )

    for delay, base in zip(clock.sleepCalls, [1.0, 2.0, 4.0]):
        assert 0.7 * base <= delay <= 1.3 * base

    # ? and it must not be the bare schedule, or there is no spread
    assert clock.sleepCalls != pytest.approx([1.0, 2.0, 4.0])


async def test_onRetryHookReceivesAttemptDelayAndError() -> None:
    """
    The hook is the seam for metrics, so it gets everything a counter
    or a span would need.
    """

    clock = FakeClock()
    observed = []

    operation = ScriptedOperation(
        failures = 2, error = TransientBrokerError("busy")
    )

    await retryAsync(
        operation, EXACT, clock,
        onRetry = lambda attempt, delay, error: observed.append(
            (attempt, delay, type(error).__name__)
        )
    )

    assert observed == [
        (1, 0.5, "TransientBrokerError"), (2, 1.0, "TransientBrokerError")
    ]


def test_policyRejectsImpossibleConfiguration() -> None:
    """
    Nonsense settings fail at construction rather than at the first
    failure, when the schedule would silently misbehave.
    """

    with pytest.raises(ValueError):
        RetryPolicy(maxAttempts = 0)

    with pytest.raises(ValueError):
        RetryPolicy(baseDelay = -1.0)

    with pytest.raises(ValueError):
        RetryPolicy(jitter = 1.5)


def test_rateLimitIsRetryableByDefault() -> None:
    """
    The default policy covers exactly the transient family.
    """

    assert DEFAULT_RETRY_POLICY.shouldRetry(RateLimitExceeded(), 1) is True
    assert DEFAULT_RETRY_POLICY.shouldRetry(
        TransientBrokerError("x"), 1
    ) is True
    assert DEFAULT_RETRY_POLICY.shouldRetry(
        OrderRejected("rejected"), 1
    ) is False
