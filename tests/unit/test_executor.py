# -*- encoding: utf-8 -*-

"""
Blocking Call Bridge, Thread Pool Sizing and Endpoint Policy
--------------------------------------------------------------

The tests that matter here are the ones that would have caught the
original defect. A blocking call must genuinely leave the event loop
thread, the pool it lands in must be the one sized to ``maxConcurrency``
rather than the interpreter default, and an order endpoint must make
exactly one attempt however the caller configured retrying.
"""

import time
import asyncio
import threading
import concurrent.futures as cf

import pytest

from algotraders.core.executor import (
    THREAD_NAME_PREFIX, BlockingCaller
)
from algotraders.core.ratelimit import RateLimit, RateLimitPolicy
from algotraders.core.retry import RetryPolicy
from algotraders.errors import OrderRejected, TransientBrokerError
from algotraders.models.enums import EndpointClass
from tests.fakes.clock import FakeClock


def buildCaller(clock : FakeClock, **overrides : object) -> BlockingCaller:
    """
    Construct a caller over a generous shared limit unless overridden.
    """

    policy = RateLimitPolicy(
        clock = clock, sharedLimits = [RateLimit("test.perSecond", 100, 1.0)]
    )
    fields = dict(policy = policy, clock = clock, maxConcurrency = 3)
    fields.update(overrides)

    return BlockingCaller(**fields)


async def test_blockingCallRunsOffTheEventLoopThread() -> None:
    """
    The vendor call executes on a worker thread.

    This is the whole point of the class: the original code awaited
    nothing and ran every "concurrent" fetch inline on the loop thread.
    """

    clock = FakeClock()
    loopThread = threading.current_thread().name

    async with buildCaller(clock) as caller:
        observed = await caller.call(
            EndpointClass.HISTORICAL, lambda : threading.current_thread().name
        )

    assert observed != loopThread
    assert observed.startswith(THREAD_NAME_PREFIX)


async def test_usesTheOwnExecutorNotTheProcessDefault() -> None:
    """
    Work lands in a pool sized to ``maxConcurrency``.

    ``asyncio.to_thread`` would put it in the default executor, capped
    at ``min(32, cpu + 4)``, where a large semaphore admits work that
    then queues invisibly and holds memory the whole time.
    """

    clock = FakeClock()

    async with buildCaller(clock, maxConcurrency = 2) as caller:
        assert caller.executor._max_workers == 2

        name = await caller.call(
            EndpointClass.GENERAL, lambda : threading.current_thread().name
        )

    assert THREAD_NAME_PREFIX in name


async def test_concurrencyIsBoundedByTheSemaphore() -> None:
    """
    No more than ``maxConcurrency`` calls are ever in flight.
    """

    clock = FakeClock()
    active = 0
    peak = 0
    guard = threading.Lock()

    def blockingWork() -> None:
        nonlocal active, peak

        with guard:
            active += 1
            peak = max(peak, active)

        time.sleep(0.02)

        with guard:
            active -= 1

    async with buildCaller(clock, maxConcurrency = 3) as caller:
        await asyncio.gather(*(
            caller.call(EndpointClass.GENERAL, blockingWork)
            for _ in range(12)
        ))

    assert peak <= 3


async def test_argumentsReachTheVendorCall() -> None:
    """
    Positional and keyword arguments are forwarded unchanged.
    """

    clock = FakeClock()

    def vendorCall(first : int, second : int, scale : int = 1) -> int:
        return (first + second) * scale

    async with buildCaller(clock) as caller:
        result = await caller.call(
            EndpointClass.GENERAL, vendorCall, 2, 3, scale = 10
        )

    assert result == 50


async def test_rateLimitIsChargedPerCall() -> None:
    """
    Every call passes through the limiter for its endpoint class.
    """

    clock = FakeClock()
    policy = RateLimitPolicy(
        clock = clock, sharedLimits = [RateLimit("tiny", 2, 1.0)]
    )

    async with BlockingCaller(policy, clock, maxConcurrency = 1) as caller:
        for _ in range(4):
            await caller.call(EndpointClass.GENERAL, lambda : None)

    # ? two free, then two paced at half a second each
    assert clock.sleepCalls == pytest.approx([0.5, 0.5])


async def test_transientFailureIsRetried() -> None:
    """
    A retryable vendor error re-enters the whole pipeline.
    """

    clock = FakeClock()
    attempts = 0

    def flaky() -> str:
        nonlocal attempts
        attempts += 1

        if attempts < 3:
            raise TransientBrokerError("gateway")

        return "ok"

    policy = RetryPolicy(maxAttempts = 5, baseDelay = 0.5, jitter = 0.0)

    async with buildCaller(clock, retryPolicy = policy) as caller:
        assert await caller.call(EndpointClass.HISTORICAL, flaky) == "ok"

    assert attempts == 3


async def test_orderEndpointNeverRetries() -> None:
    """
    Order placement makes exactly one attempt whatever the caller
    configured, because a timed out placement may already be live at the
    exchange and a retry is a double fill.
    """

    clock = FakeClock()
    attempts = 0

    def flakyOrder() -> None:
        nonlocal attempts
        attempts += 1

        raise TransientBrokerError("timeout")

    generous = RetryPolicy(maxAttempts = 5, baseDelay = 0.5, jitter = 0.0)

    async with buildCaller(clock, retryPolicy = generous) as caller:
        assert caller.policyFor(EndpointClass.ORDER).maxAttempts == 1

        with pytest.raises(TransientBrokerError):
            await caller.call(EndpointClass.ORDER, flakyOrder)

    assert attempts == 1


async def test_rejectionPropagatesUnchanged() -> None:
    """
    A broker decision reaches the caller with its message intact.
    """

    clock = FakeClock()

    def rejected() -> None:
        raise OrderRejected("order rejected", brokerMessage = "margin short")

    async with buildCaller(clock) as caller:
        with pytest.raises(OrderRejected) as failure:
            await caller.call(EndpointClass.ORDER, rejected)

    assert failure.value.brokerMessage == "margin short"


async def test_externalExecutorIsNotShutDown() -> None:
    """
    A pool passed in belongs to whoever passed it and must survive the
    caller being closed.
    """

    clock = FakeClock()
    external = cf.ThreadPoolExecutor(max_workers = 2)

    try:
        caller = buildCaller(clock, executor = external)
        await caller.call(EndpointClass.GENERAL, lambda : None)
        await caller.close()

        # ? still usable, so it was not shut down underneath the owner
        assert external.submit(lambda : 42).result() == 42
    finally:
        external.shutdown(wait = True)


async def test_ownExecutorIsShutDownOnClose() -> None:
    """
    A pool the caller created is cleaned up, so a long lived host does
    not leak threads per session.
    """

    clock = FakeClock()
    caller = buildCaller(clock)

    await caller.call(EndpointClass.GENERAL, lambda : None)
    await caller.close()

    with pytest.raises(RuntimeError):
        caller.executor.submit(lambda : None)


def test_rejectsImpossibleConcurrency() -> None:
    """
    Zero concurrency would deadlock on the first call.
    """

    clock = FakeClock()
    policy = RateLimitPolicy(clock = clock)

    with pytest.raises(ValueError):
        BlockingCaller(policy, clock, maxConcurrency = 0)
