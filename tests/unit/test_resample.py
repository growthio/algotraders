# -*- encoding: utf-8 -*-

"""
Candle Aggregation for Timeframes No Broker Serves
-----------------------------------------------------

Aggregation is opt in because a rolled up bar was never sent by the
broker. These tests pin that the arithmetic is right where it is
offered, and that it refuses everything it cannot do soundly.
"""

import datetime as dt

import pytest

from algotraders.core.clock import IST
from algotraders.core.resample import (
    SUPPORTED_AGGREGATIONS, aggregateCandles, bucketFor
)
from algotraders.errors import UnsupportedTimeframe
from algotraders.models.enums import Timeframe
from algotraders.models.market import Candle


def dailyCandle(
    day : dt.date, openPrice : float, high : float, low : float,
    close : float, volume : int, key : str = "NSE:SBIN:EQ"
) -> Candle:
    """
    Build one daily bar stamped at the IST market open.
    """

    return Candle(
        instrumentKey = key, timeframe = Timeframe.DAY_1,
        timestamp = dt.datetime(
            day.year, day.month, day.day, 9, 15, tzinfo = IST
        ),
        openPrice = openPrice, highPrice = high, lowPrice = low,
        closePrice = close, volume = volume, source = "fyers"
    )


AUGUST = [
    dailyCandle(dt.date(2026, 8, 3), 800.0, 810.0, 795.0, 805.0, 1_000),
    dailyCandle(dt.date(2026, 8, 4), 805.0, 822.0, 803.0, 818.0, 1_500),
    dailyCandle(dt.date(2026, 8, 5), 818.0, 820.0, 790.0, 812.0, 2_000)
]

SEPTEMBER = [
    dailyCandle(dt.date(2026, 9, 1), 812.0, 830.0, 811.0, 828.0, 900)
]


def test_monthlyRollupTakesFirstOpenAndLastClose() -> None:
    """
    Open comes from the first bar, close from the last, high and low
    from the extremes, and volume is summed.
    """

    monthly = aggregateCandles(AUGUST, Timeframe.MONTH_1)

    assert len(monthly) == 1

    bar = monthly[0]

    assert bar.openPrice == 800.0
    assert bar.closePrice == 812.0
    assert bar.highPrice == 822.0
    assert bar.lowPrice == 790.0
    assert bar.volume == 4_500
    assert bar.timeframe is Timeframe.MONTH_1


def test_rollupIsStampedAtTheFirstConstituentBar() -> None:
    """
    The bucket carries a real timestamp from the data rather than a
    synthesised month boundary that never traded.
    """

    bar = aggregateCandles(AUGUST, Timeframe.MONTH_1)[0]

    assert bar.timestamp == AUGUST[0].timestamp


def test_separateMonthsBecomeSeparateBars() -> None:
    """
    Bucketing is by calendar month, not by input order.
    """

    monthly = aggregateCandles(AUGUST + SEPTEMBER, Timeframe.MONTH_1)

    assert len(monthly) == 2
    assert monthly[0].timestamp < monthly[1].timestamp


def test_unsortedInputIsOrderedInternally() -> None:
    """
    Open and close depend on ordering, so the fold cannot trust the
    caller to have sorted its input.
    """

    shuffled = [AUGUST[2], AUGUST[0], AUGUST[1]]
    bar = aggregateCandles(shuffled, Timeframe.MONTH_1)[0]

    assert bar.openPrice == 800.0
    assert bar.closePrice == 812.0


def test_instrumentsAreFoldedSeparately() -> None:
    """
    A mixed list must not merge two securities into one bar.
    """

    other = dailyCandle(
        dt.date(2026, 8, 4), 100.0, 105.0, 99.0, 104.0, 50,
        key = "NSE:TCS:EQ"
    )

    monthly = aggregateCandles(AUGUST + [other], Timeframe.MONTH_1)
    keys = {bar.instrumentKey for bar in monthly}

    assert keys == {"NSE:SBIN:EQ", "NSE:TCS:EQ"}


def test_yearlyRollupSpansEveryMonth() -> None:
    """
    Yearly folds the same way, one bucket per calendar year.
    """

    yearly = aggregateCandles(AUGUST + SEPTEMBER, Timeframe.YEAR_1)

    assert len(yearly) == 1
    assert yearly[0].volume == 5_400
    assert yearly[0].closePrice == 828.0


def test_openInterestIsDroppedNotSummed() -> None:
    """
    Open interest is a point in time figure rather than a flow, so a
    monthly sum of it would be meaningless.
    """

    withInterest = [
        candle.model_copy(update = {"openInterest" : 1_000})
        for candle in AUGUST
    ]

    assert aggregateCandles(
        withInterest, Timeframe.MONTH_1
    )[0].openInterest is None


def test_bucketBoundariesAreComputedInIST() -> None:
    """
    A bar stamped just before IST midnight on the last of the month must
    not be pushed into the next month by a UTC conversion.
    """

    lateNight = dt.datetime(2026, 8, 31, 23, 45, tzinfo = IST)

    assert bucketFor(lateNight, Timeframe.MONTH_1) == (2026, 8)
    assert bucketFor(lateNight, Timeframe.YEAR_1) == (2026,)


@pytest.mark.parametrize("timeframe", [
    Timeframe.MINUTE_1, Timeframe.MINUTE_5, Timeframe.HOUR_1,
    Timeframe.DAY_1
])
def test_intradayTargetsAreRefused(timeframe : Timeframe) -> None:
    """
    Only monthly and yearly are offered.

    Hourly bucket anchoring differs between brokers, so folding minute
    bars would produce series that silently disagree depending on which
    broker supplied the input.
    """

    assert timeframe not in SUPPORTED_AGGREGATIONS

    with pytest.raises(UnsupportedTimeframe):
        aggregateCandles(AUGUST, timeframe)


def test_emptyInputYieldsEmptyOutput() -> None:
    """
    Nothing in, nothing out, without a spurious empty bar.
    """

    assert aggregateCandles([], Timeframe.MONTH_1) == []


def test_provenanceSurvivesTheRollup() -> None:
    """
    The derived bar still records which broker supplied the underlying
    data, so a mixed source table stays explicable.
    """

    assert aggregateCandles(AUGUST, Timeframe.MONTH_1)[0].source == "fyers"
