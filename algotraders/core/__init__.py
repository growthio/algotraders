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

__all__ = [
    "IST", "MARKET_OPEN", "MARKET_CLOSE",
    "BaseClock", "SystemClock",
    "toIST", "fromEpoch", "toEpoch"
]
