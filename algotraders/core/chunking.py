# -*- encoding: utf-8 -*-

"""
Splitting a Date Range Into Broker Legal Windows
--------------------------------------------------

Both brokers cap how much history a single historical request may span,
and both reject anything larger outright. Fyers allows 100 days for
intraday resolutions and 366 for daily. Kite caps per interval, from 60
days for one minute bars up to 2000 for daily. A caller asking for two
years of one minute data is therefore making one impossible request that
has to become many legal ones.

The original code worked around this by hand: the example script built
monthly buckets in a ``dispatch()`` helper and fanned them out at 2500
way concurrency. That produced twelve requests per symbol whatever the
broker's real limit was, and it is why the chunking logic now lives here
instead of in the caller.

This module is deliberately free of any broker or model import. It knows
about datetimes and a maximum span, nothing else, which keeps it
testable in isolation and lets each broker supply its own per timeframe
span table from its ``limits.py``.

:NOTE: Broker ranges are inclusive at both ends, so consecutive chunks
    are separated by a small step to avoid requesting the boundary
    instant twice. Duplicate candles are still deduplicated downstream
    on ``(instrumentKey, timeframe, timestamp)``, because a broker
    occasionally returns a bar just outside the range it was given.
"""

import datetime as dt

from typing import List, Optional, Tuple

from algotraders.core.clock import IST, toIST

# ? one second between chunks: small enough that no intraday bar is
# skipped, large enough that the boundary instant is not fetched twice
DEFAULT_CHUNK_STEP = dt.timedelta(seconds = 1)


def splitDateRange(
    start : dt.datetime, end : dt.datetime, maxSpan : dt.timedelta,
    step : dt.timedelta = DEFAULT_CHUNK_STEP,
    timezone : Optional[dt.tzinfo] = None
) -> List[Tuple[dt.datetime, dt.datetime]]:
    """
    Split an inclusive date range into windows no longer than
    ``maxSpan``.

    :type  start: dt.datetime
    :param start: Range start, inclusive. Naive values are read as IST.

    :type  end: dt.datetime
    :param end: Range end, inclusive.

    :type  maxSpan: dt.timedelta
    :param maxSpan: Largest window the broker accepts for the timeframe
        being requested.

    :type  step: dt.timedelta
    :param step: Gap inserted between consecutive windows so the
        boundary instant is not requested twice.

    :type  timezone: Optional[dt.tzinfo]
    :param timezone: Zone to normalise into. Defaults to IST, which is
        what both supported brokers operate in.

    **Example Usage**

    Two years of one minute Fyers data becomes eight legal requests.

    .. code-block:: python

        windows = splitDateRange(
            start = dt.datetime(2024, 1, 1, tzinfo = IST),
            end = dt.datetime(2026, 1, 1, tzinfo = IST),
            maxSpan = dt.timedelta(days = 100)
        )

        len(windows)  # 8

    :raises ValueError: ``start`` is after ``end``, or ``maxSpan`` is
        not positive. Both indicate a caller bug rather than a
        recoverable condition.

    :rtype:   List[Tuple[dt.datetime, dt.datetime]]
    :returns: Contiguous, non overlapping windows covering the range in
        ascending order. A range already within ``maxSpan`` yields
        exactly one window equal to the input.
    """

    if maxSpan <= dt.timedelta(0):
        raise ValueError(f"maxSpan must be positive, got {maxSpan}")

    zone = timezone or IST

    first = toIST(start).astimezone(zone)
    last = toIST(end).astimezone(zone)

    if first > last:
        raise ValueError(
            f"start {first.isoformat()} is after end {last.isoformat()}"
        )

    windows : List[Tuple[dt.datetime, dt.datetime]] = []
    cursor = first

    while cursor <= last:
        boundary = min(cursor + maxSpan, last)
        windows.append((cursor, boundary))

        if boundary >= last:
            break

        cursor = boundary + step

    return windows


def clampToNow(
    start : dt.datetime, end : dt.datetime, now : dt.datetime
) -> Tuple[dt.datetime, dt.datetime]:
    """
    Trim a requested range so it does not extend into the future.

    Brokers reject a future ``range_to`` rather than returning what they
    have, so a caller asking for "the last two years" with a naive
    ``today + 1`` end date would fail every request. Clamping locally
    turns that into a successful call.

    :type  now: dt.datetime
    :param now: Current time, from the caller's clock.

    :raises ValueError: The whole range lies in the future, which cannot
        be satisfied by trimming.

    :rtype:   Tuple[dt.datetime, dt.datetime]
    :returns: The possibly trimmed range, normalised to IST.
    """

    first = toIST(start)
    last = min(toIST(end), toIST(now))

    if first > last:
        raise ValueError(
            f"requested range starts at {first.isoformat()}, which is in "
            "the future; no historical data exists for it"
        )

    return first, last


def countChunks(
    start : dt.datetime, end : dt.datetime, maxSpan : dt.timedelta
) -> int:
    """
    Number of requests a range will cost, without building the windows.

    Useful for a progress bar, for a rate limit budget estimate, or for
    refusing an accidental request for a decade of one minute bars
    before it consumes a day's quota.

    :rtype:   int
    :returns: How many broker calls the range requires.
    """

    return len(splitDateRange(start, end, maxSpan))


__all__ = [
    "splitDateRange", "clampToNow", "countChunks", "DEFAULT_CHUNK_STEP"
]
