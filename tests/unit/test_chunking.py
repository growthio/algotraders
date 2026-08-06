# -*- encoding: utf-8 -*-

"""
Date Range Splitting Against Broker Span Caps
-----------------------------------------------

The chunk counts asserted here are the ones the two brokers actually
require, which is why the same two year request produces a different
number of calls per broker: Fyers allows 100 days of one minute data per
request while Kite allows 60.
"""

import datetime as dt

import pytest

from algotraders.core.chunking import (
    clampToNow, countChunks, splitDateRange
)
from algotraders.core.clock import IST

START = dt.datetime(2024, 1, 1, 9, 15, tzinfo = IST)
END = dt.datetime(2026, 1, 1, 15, 30, tzinfo = IST)


def test_shortRangeYieldsOneWindow() -> None:
    """
    A range already inside the cap is passed through untouched, so the
    common case costs exactly one request.
    """

    end = START + dt.timedelta(days = 30)
    windows = splitDateRange(START, end, dt.timedelta(days = 100))

    assert windows == [(START, end)]


def test_rangeEqualToTheCapIsNotSplit() -> None:
    """
    The boundary is inclusive; exactly 100 days is still one request.
    """

    end = START + dt.timedelta(days = 100)
    windows = splitDateRange(START, end, dt.timedelta(days = 100))

    assert len(windows) == 1


def test_windowsAreContiguousAndNonOverlapping() -> None:
    """
    Every window starts after the previous one ends, so no bar is
    requested twice and none is skipped.
    """

    windows = splitDateRange(START, END, dt.timedelta(days = 100))

    for (_, previousEnd), (nextStart, _) in zip(windows, windows[1:]):
        assert nextStart > previousEnd
        assert nextStart - previousEnd == dt.timedelta(seconds = 1)


def test_noWindowExceedsTheCap() -> None:
    """
    The property the broker actually enforces: every request is legal.
    """

    span = dt.timedelta(days = 100)
    windows = splitDateRange(START, END, span)

    for first, last in windows:
        assert last - first <= span


def test_coverageSpansTheWholeRequestedRange() -> None:
    """
    Splitting loses nothing at either end.
    """

    windows = splitDateRange(START, END, dt.timedelta(days = 100))

    assert windows[0][0] == START
    assert windows[-1][1] == END


@pytest.mark.parametrize("days, expected", [
    (100, 8),   # fyers, every intraday resolution
    (60, 13),   # kite, one minute bars
    (200, 4),   # kite, fifteen minute bars
    (400, 2),   # kite, hourly bars
    (2000, 1),  # kite, daily bars
])
def test_chunkCountsMatchTheBrokerCaps(days : int, expected : int) -> None:
    """
    The same two year request costs a different number of calls per
    broker, which is why the span table lives in each broker's
    ``limits.py`` rather than being a single constant.
    """

    assert countChunks(START, END, dt.timedelta(days = days)) == expected


def test_naiveInputIsReadAsIST() -> None:
    """
    A naive datetime is an Indian market timestamp in this domain, and
    the windows come back timezone aware either way.
    """

    windows = splitDateRange(
        dt.datetime(2024, 1, 1, 9, 15), dt.datetime(2024, 3, 1, 15, 30),
        dt.timedelta(days = 100)
    )

    assert windows[0][0].tzinfo is not None
    assert windows[0][0].hour == 9


def test_reversedRangeIsRejected() -> None:
    """
    An end before the start is a caller bug, not something to silently
    swap and paper over.
    """

    with pytest.raises(ValueError):
        splitDateRange(END, START, dt.timedelta(days = 100))


def test_nonPositiveSpanIsRejected() -> None:
    """
    A zero span would loop forever building empty windows.
    """

    with pytest.raises(ValueError):
        splitDateRange(START, END, dt.timedelta(0))


def test_singleInstantRangeYieldsOneWindow() -> None:
    """
    Start equal to end is legal and costs one request.
    """

    windows = splitDateRange(START, START, dt.timedelta(days = 100))

    assert windows == [(START, START)]


def test_clampTrimsAFutureEndDate() -> None:
    """
    Brokers reject a future range end rather than returning what they
    have, so "the last two years" written as today plus one would fail
    every request without this.
    """

    now = dt.datetime(2026, 8, 5, 12, 0, tzinfo = IST)
    first, last = clampToNow(
        START, dt.datetime(2027, 1, 1, tzinfo = IST), now
    )

    assert first == START
    assert last == now


def test_clampLeavesAPastRangeAlone() -> None:
    """
    A range wholly in the past is untouched.
    """

    now = dt.datetime(2026, 8, 5, 12, 0, tzinfo = IST)

    assert clampToNow(START, END, now) == (START, END)


def test_clampRejectsAWhollyFutureRange() -> None:
    """
    Trimming cannot rescue a range that has not happened yet.
    """

    now = dt.datetime(2024, 1, 1, tzinfo = IST)

    with pytest.raises(ValueError):
        clampToNow(
            dt.datetime(2026, 1, 1, tzinfo = IST),
            dt.datetime(2026, 6, 1, tzinfo = IST), now
        )
