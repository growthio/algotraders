# -*- encoding: utf-8 -*-

"""
Broker Agnostic Core Services for the AlgoTraders Library
==========================================================

Everything in this package is broker independent by construction. It may
import :mod:`algotraders.models` and :mod:`algotraders.errors` and
nothing else from the library, which is what allows a single rate
limiter, retry helper and date range splitter to serve every broker.

The pieces are deliberately small and separately testable:

    * ``clock`` - injectable time, anchored to ``Asia/Kolkata``.
    * ``ratelimit`` - token buckets and daily quotas.
    * ``retry`` - exponential backoff with jitter.
    * ``chunking`` - splitting a date range into broker legal windows.
    * ``executor`` - the bridge from blocking vendor SDKs to asyncio.
    * ``profile`` - memory footprint sampling for concurrency sizing.
    * ``resample`` - candle aggregation for timeframes no broker serves.
"""

from algotraders.core.clock import (
    IST, MARKET_OPEN, MARKET_CLOSE,
    BaseClock, SystemClock,
    toIST, fromEpoch, toEpoch
)

from algotraders.core.ratelimit import (
    RateLimit, TokenBucket, DailyQuota, RateLimiter, RateLimitPolicy
)

from algotraders.core.retry import (
    RetryPolicy, retryAsync,
    DEFAULT_RETRYABLE, DEFAULT_RETRY_POLICY, ORDER_RETRY_POLICY
)

from algotraders.core.chunking import (
    splitDateRange, clampToNow, countChunks, DEFAULT_CHUNK_STEP
)

from algotraders.core.executor import (
    BlockingCaller, DEFAULT_MAX_CONCURRENCY, THREAD_NAME_PREFIX
)

from algotraders.core.profile import MemoryProfile, traceMemoryUsage

from algotraders.core.resample import (
    aggregateCandles, bucketFor, SUPPORTED_AGGREGATIONS
)

__all__ = [
    "IST", "MARKET_OPEN", "MARKET_CLOSE",
    "BaseClock", "SystemClock",
    "toIST", "fromEpoch", "toEpoch",

    "RateLimit", "TokenBucket", "DailyQuota", "RateLimiter",
    "RateLimitPolicy",

    "RetryPolicy", "retryAsync",
    "DEFAULT_RETRYABLE", "DEFAULT_RETRY_POLICY", "ORDER_RETRY_POLICY",

    "splitDateRange", "clampToNow", "countChunks", "DEFAULT_CHUNK_STEP",

    "BlockingCaller", "DEFAULT_MAX_CONCURRENCY", "THREAD_NAME_PREFIX",

    "MemoryProfile", "traceMemoryUsage",

    "aggregateCandles", "bucketFor", "SUPPORTED_AGGREGATIONS"
]
