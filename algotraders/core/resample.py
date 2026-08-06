# -*- encoding: utf-8 -*-

"""
Folding Candles Into Timeframes No Broker Serves
--------------------------------------------------

The host's ``private.timeframe_value`` enum includes ``1M`` and ``1Y``,
but neither Fyers nor Kite serves a monthly or yearly bar. Rather than
quietly dropping those values or quietly inventing them, the library
refuses by default and offers this as an explicit opt in.

Aggregation is only correct in one direction and only from an
unambiguous base. Daily bars have a defined boundary that both brokers
agree on, so monthly and yearly rollups derived from them are sound.
Intraday rollups are not offered: hourly bucket anchoring differs
between brokers, so folding minute bars would produce series that
silently disagree depending on which broker supplied them.

:NOTE: A rollup is a derived figure. The resulting candles keep the
    ``source`` of the bars they were built from, so a consumer can still
    tell where the data came from, but they were never sent by that
    broker. If exactness matters, prefer a SQL window over the stored
    daily bars in the host, which is a one line query.
"""

import datetime as dt

from typing import Dict, List, Tuple

from algotraders.core.clock import toIST
from algotraders.errors import UnsupportedTimeframe
from algotraders.logger import getLogger
from algotraders.models.enums import Timeframe
from algotraders.models.market import Candle

logger = getLogger(__name__)

# ? the only rollups that are unambiguous, and the base each folds from
SUPPORTED_AGGREGATIONS : Dict[Timeframe, Timeframe] = {
    Timeframe.MONTH_1 : Timeframe.DAY_1,
    Timeframe.YEAR_1 : Timeframe.DAY_1
}


def bucketFor(moment : dt.datetime, timeframe : Timeframe) -> Tuple[int, ...]:
    """
    Return the calendar bucket a moment belongs to.

    Buckets are computed on the IST calendar, so a bar stamped just
    before midnight lands in the correct month rather than being pushed
    forward by a UTC conversion.

    :raises UnsupportedTimeframe: The timeframe is not an aggregation
        target.

    :rtype:   Tuple[int, ...]
    :returns: ``(year,)`` for yearly, ``(year, month)`` for monthly.
    """

    local = toIST(moment)

    if timeframe is Timeframe.MONTH_1:
        return (local.year, local.month)

    if timeframe is Timeframe.YEAR_1:
        return (local.year,)

    raise UnsupportedTimeframe(
        brokerName = "aggregation", timeframe = timeframe,
        supported = list(SUPPORTED_AGGREGATIONS)
    )


def aggregateCandles(
    candles : List[Candle], timeframe : Timeframe
) -> List[Candle]:
    """
    Fold daily candles into monthly or yearly bars.

    Open is the first bar's open, close the last bar's close, high and
    low the extremes, volume the sum. Open interest is deliberately
    dropped: it is a point in time figure rather than a flow, so summing
    it across a month would be meaningless.

    :type  candles: List[Candle]
    :param candles: Source bars. Need not be sorted; they are ordered
        internally. Mixed instruments are grouped and folded separately.

    :type  timeframe: Timeframe
    :param timeframe: Target granularity, ``1M`` or ``1Y``.

    **Example Usage**

    .. code-block:: python

        daily = await api.fetchCandles(instrument, Timeframe.DAY_1, span)
        monthly = aggregateCandles(daily, Timeframe.MONTH_1)

    :raises UnsupportedTimeframe: The target is not a supported rollup.

    :rtype:   List[Candle]
    :returns: Aggregated candles in ascending time order, stamped at the
        first constituent bar's timestamp.
    """

    if timeframe not in SUPPORTED_AGGREGATIONS:
        raise UnsupportedTimeframe(
            brokerName = "aggregation", timeframe = timeframe,
            supported = list(SUPPORTED_AGGREGATIONS)
        )

    if not candles:
        return []

    grouped : Dict[Tuple[str, Tuple[int, ...]], List[Candle]] = {}

    for candle in sorted(candles, key = lambda entry: entry.timestamp):
        key = (candle.instrumentKey, bucketFor(candle.timestamp, timeframe))
        grouped.setdefault(key, []).append(candle)

    aggregated = [
        _foldBucket(bucket, timeframe) for bucket in grouped.values()
    ]

    logger.debug(
        "aggregated %d candles into %d %s bars",
        len(candles), len(aggregated), timeframe.value
    )

    return sorted(aggregated, key = lambda entry: entry.timestamp)


def _foldBucket(bucket : List[Candle], timeframe : Timeframe) -> Candle:
    """
    Collapse one calendar bucket of candles into a single bar.
    """

    first = bucket[0]
    last = bucket[-1]

    return Candle(
        instrumentKey = first.instrumentKey,
        timeframe = timeframe,
        timestamp = first.timestamp,
        openPrice = first.openPrice,
        highPrice = max(candle.highPrice for candle in bucket),
        lowPrice = min(candle.lowPrice for candle in bucket),
        closePrice = last.closePrice,
        volume = sum(candle.volume for candle in bucket),
        openInterest = None,
        source = first.source
    )


__all__ = ["aggregateCandles", "bucketFor", "SUPPORTED_AGGREGATIONS"]
