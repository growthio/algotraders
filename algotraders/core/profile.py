# -*- encoding: utf-8 -*-

"""
Memory Footprint Sampling for Sizing Concurrency
--------------------------------------------------

Choosing ``maxConcurrency`` by intuition is how a historical backfill
ends up holding thousands of partially parsed responses and dies to the
OOM killer. This module measures one call's peak allocation and derives
a concurrency ceiling from an available memory budget.

It was previously a concrete method on the broker ABC, which was wrong
on three counts. It made every broker implementation inherit a
profiling tool it never used; it read ``peak`` after a ``try/finally``
where an exception left the name unbound, so a failing fetch raised
``NameError`` and hid the real error; and its own docstring example
called an ``async def`` synchronously. All three are fixed here, and it
is now a free function taking a coroutine factory.

:NOTE: :mod:`tracemalloc` only sees allocations made through the Python
    allocator. A vendor SDK that buffers inside a C extension will
    under-report, so treat the result as a floor and keep the buffer.
"""

import math
import asyncio
import tracemalloc

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from algotraders.logger import getLogger

logger = getLogger(__name__)

# ! tracemalloc has one process-wide state, so overlapping profiles
# corrupt each other's readings. Serialised rather than documented away
_PROFILE_LOCK = asyncio.Lock()

# ? garbage collection is lazy, so the steady state high water mark sits
# above any single sample; 20 percent is the margin that has held up
DEFAULT_BUFFER_FRACTION = 0.20

BYTES_PER_MEGABYTE = 10 ** 6

# ! a ceiling on what this function will ever recommend. Broker rate
# limits bind long before local memory does, so a large answer here is
# always wrong even when the arithmetic is right
MAXIMUM_RECOMMENDED_CONCURRENCY = 64


@dataclass(frozen = True)
class MemoryProfile:
    """
    Result of profiling a single operation.

    :type  currentMegabytes: float
    :param currentMegabytes: Allocation still held when the operation
        finished.

    :type  peakMegabytes: float
    :param peakMegabytes: High water mark during the operation. This is
        the number that governs concurrency, since every in-flight task
        can hit its peak simultaneously.

    :type  recommendedConcurrency: int
    :param recommendedConcurrency: Tasks that fit in the supplied
        budget, at least one.
    """

    currentMegabytes : float
    peakMegabytes : float
    bufferMegabytes : float
    limitMegabytes : float
    recommendedConcurrency : int


    def __str__(self) -> str:
        return (
            f"peak {self.peakMegabytes:,.2f} MB + buffer "
            f"{self.bufferMegabytes:,.2f} MB within {self.limitMegabytes:,.0f} "
            f"MB -> maxConcurrency {self.recommendedConcurrency}"
        )


async def traceMemoryUsage(
    operation : Callable[[], Awaitable[Any]],
    limitMegabytes : float,
    bufferMegabytes : Optional[float] = None,
    maximumConcurrency : int = MAXIMUM_RECOMMENDED_CONCURRENCY
) -> MemoryProfile:
    """
    Measure one operation's peak allocation and derive a safe
    concurrency ceiling.

    :type  operation: Callable[[], Awaitable[Any]]
    :param operation: Zero argument factory returning the awaitable to
        profile. Run it against a representative request, since a one
        day candle fetch and a hundred day one differ by two orders of
        magnitude.

    :type  limitMegabytes: float
    :param limitMegabytes: Memory budget to divide up, in megabytes.

    :type  bufferMegabytes: Optional[float]
    :param bufferMegabytes: Headroom added to the measured peak. Omit to
        use 20 percent of the peak.

    **Example Usage**

    Size concurrency against a 2 GB budget using a real request.

    .. code-block:: python

        profile = await traceMemoryUsage(
            lambda : api.fetchCandles(instrument, Timeframe.MINUTE_1, span),
            limitMegabytes = 2_048
        )

        print(profile)
        caller = BlockingCaller(
            policy, clock, maxConcurrency = profile.recommendedConcurrency
        )

    :raises ValueError: ``limitMegabytes`` is not positive.

    :rtype:   MemoryProfile
    :returns: The measurement and the derived ceiling.
    """

    if limitMegabytes <= 0:
        raise ValueError("limitMegabytes must be positive")

    # ! tracemalloc is process global. Two overlapping profiles had the
    # first to finish call stop(), so the second read (0, 0), computed a
    # zero footprint and recommended a concurrency equal to the entire
    # memory budget - recreating the maxConcurrency = 2_500 mistake this
    # library exists to prevent. The lock serialises them, and stopping
    # is conditional so a host that was already tracing keeps its trace
    async with _PROFILE_LOCK:
        alreadyTracing = tracemalloc.is_tracing()

        if not alreadyTracing:
            tracemalloc.start()

        # ! initialised before the try, so a failing operation
        # propagates its own exception instead of a NameError from the
        # finally clause
        current = 0
        peak = 0

        try:
            await operation()
            current, peak = tracemalloc.get_traced_memory()
        finally:
            if not alreadyTracing:
                tracemalloc.stop()

    currentMegabytes = current / BYTES_PER_MEGABYTE
    peakMegabytes = peak / BYTES_PER_MEGABYTE

    buffer = (
        bufferMegabytes if bufferMegabytes is not None
        else DEFAULT_BUFFER_FRACTION * peakMegabytes
    )

    perTask = peakMegabytes + buffer

    # ! an unmeasurably small peak means the measurement failed, not
    # that the operation is free. Returning floor(limitMegabytes) here
    # recommended a concurrency of 2048 for a 2 GB budget, which the
    # docstring then invites the caller to feed straight into
    # BlockingCaller. One is the only safe answer
    if perTask <= 0:
        logger.warning(
            "measured a zero memory footprint, which usually means the "
            "operation allocated nothing observable; recommending a "
            "concurrency of 1 rather than guessing"
        )

        concurrency = 1
    else:
        # ! capped. Memory is not the binding constraint here, broker
        # rate limits are: a cheap operation divides into a large budget
        # and yields tens of thousands, and the docstring invites the
        # caller to feed that straight into BlockingCaller. That is the
        # maxConcurrency = 2_500 mistake this library exists to prevent,
        # arrived at by arithmetic instead of by guesswork
        concurrency = max(
            1, min(maximumConcurrency, math.floor(limitMegabytes / perTask))
        )

    profile = MemoryProfile(
        currentMegabytes = currentMegabytes,
        peakMegabytes = peakMegabytes,
        bufferMegabytes = buffer,
        limitMegabytes = limitMegabytes,
        recommendedConcurrency = concurrency
    )

    logger.info("memory profile: %s", profile)

    return profile


__all__ = [
    "MemoryProfile", "traceMemoryUsage",
    "DEFAULT_BUFFER_FRACTION", "BYTES_PER_MEGABYTE",
    "MAXIMUM_RECOMMENDED_CONCURRENCY"
]
