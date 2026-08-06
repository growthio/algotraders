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
import tracemalloc

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from algotraders.logger import getLogger

logger = getLogger(__name__)

# ? garbage collection is lazy, so the steady state high water mark sits
# above any single sample; 20 percent is the margin that has held up
DEFAULT_BUFFER_FRACTION = 0.20

BYTES_PER_MEGABYTE = 10 ** 6


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
    bufferMegabytes : Optional[float] = None
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

    tracemalloc.start()

    # ! initialised before the try, so a failing operation propagates its
    # own exception instead of a NameError from the finally clause
    current = 0
    peak = 0

    try:
        await operation()
        current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    currentMegabytes = current / BYTES_PER_MEGABYTE
    peakMegabytes = peak / BYTES_PER_MEGABYTE

    buffer = (
        bufferMegabytes if bufferMegabytes is not None
        else DEFAULT_BUFFER_FRACTION * peakMegabytes
    )

    perTask = peakMegabytes + buffer

    # ? a peak below the measurement floor would divide into a nonsense
    # ceiling, so anything immeasurably small yields the budget itself
    concurrency = (
        max(1, math.floor(limitMegabytes / perTask)) if perTask > 0
        else max(1, math.floor(limitMegabytes))
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
    "DEFAULT_BUFFER_FRACTION", "BYTES_PER_MEGABYTE"
]
