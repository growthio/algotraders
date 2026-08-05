# -*- encoding: utf-8 -*-

"""
Clock Behaviour and Indian Market Time Handling
-------------------------------------------------

The timezone assertions here are not academic. The original candle
conversion used a bare ``x.timestamp()``, which reads the host's local
zone, so the same broker payload produced different timestamps on a
developer laptop and on a UTC container. Every test below pins IST
explicitly and several run under a deliberately wrong local timezone.
"""

import time
import datetime as dt

import pytest

from algotraders.core.clock import (
    IST, BaseClock, SystemClock, fromEpoch, toEpoch, toIST
)
from tests.fakes.clock import FakeClock


def test_systemClockReturnsAwareISTTime() -> None:
    """
    Wall clock readings are always timezone aware and in IST.
    """

    clock = SystemClock()
    current = clock.now()

    assert current.tzinfo is not None
    assert current.utcoffset() == dt.timedelta(hours = 5, minutes = 30)


def test_systemClockMonotonicAdvances() -> None:
    """
    The monotonic counter moves forward and tracks the real one.
    """

    clock = SystemClock()

    # ! the Windows monotonic counter ticks at roughly 15.6 ms, so a
    # shorter sleep can legitimately register as zero elapsed time
    first = clock.monotonic()
    time.sleep(0.05)
    second = clock.monotonic()

    assert second > first


async def test_systemClockSkipsNonPositiveSleep() -> None:
    """
    A zero or negative wait returns without a trip through the loop.
    """

    clock = SystemClock()

    started = time.monotonic()
    await clock.sleep(0)
    await clock.sleep(-5)

    assert time.monotonic() - started < 0.05


def test_toISTTreatsNaiveInputAsIndianTime() -> None:
    """
    Naive datetimes are read as IST, not UTC.

    Every naive datetime in this domain is an Indian market timestamp,
    whether typed by an operator or parsed from a broker payload.
    Assuming UTC would shift data by five and a half hours.
    """

    naive = dt.datetime(2026, 8, 5, 9, 15)
    converted = toIST(naive)

    assert converted.tzinfo is not None
    assert converted.hour == 9
    assert converted.minute == 15


def test_toISTConvertsRatherThanRelabels() -> None:
    """
    An aware datetime in another zone is converted, keeping the instant.
    """

    utcValue = dt.datetime(2026, 8, 5, 3, 45, tzinfo = dt.timezone.utc)
    converted = toIST(utcValue)

    assert converted.hour == 9
    assert converted.minute == 15
    assert converted == utcValue


def test_epochRoundTripIsTimezoneStable() -> None:
    """
    Epoch conversion does not depend on the host timezone.

    This is the exact defect the helpers replace: ``fromtimestamp``
    without an explicit tz silently uses local time.
    """

    moment = dt.datetime(2026, 8, 5, 9, 15, tzinfo = IST)

    seconds = toEpoch(moment)
    restored = fromEpoch(seconds)

    assert restored == moment
    assert restored.utcoffset() == dt.timedelta(hours = 5, minutes = 30)


def test_toEpochReadsNaiveInputAsIST() -> None:
    """
    A naive datetime converts through IST, matching :func:`toIST`.
    """

    assert toEpoch(dt.datetime(2026, 8, 5, 9, 15)) == toEpoch(
        dt.datetime(2026, 8, 5, 9, 15, tzinfo = IST)
    )


def test_tradingDateFollowsISTNotUTC() -> None:
    """
    Just after IST midnight the trading date has advanced even though
    the UTC date has not, which is what the instrument cache keys on.
    """

    clock = FakeClock(start = dt.datetime(2026, 8, 6, 0, 30, tzinfo = IST))

    assert clock.tradingDate() == dt.date(2026, 8, 6)
    assert clock.now().astimezone(dt.timezone.utc).date() == dt.date(2026, 8, 5)


def test_nextRolloverCrossesMidnight() -> None:
    """
    A token issued at 22:00 IST expires at 06:00 the next morning, which
    is the Kite regulatory cutoff.
    """

    clock = FakeClock(start = dt.datetime(2026, 8, 5, 22, 0, tzinfo = IST))
    rollover = clock.nextRollover(6)

    assert rollover == dt.datetime(2026, 8, 6, 6, 0, tzinfo = IST)


def test_nextRolloverIsStrictlyForward() -> None:
    """
    Sitting exactly on the boundary yields the *next* one, never the
    current instant, so an expiry check can never conclude that an
    already-dead token is still valid.
    """

    clock = FakeClock(start = dt.datetime(2026, 8, 5, 6, 0, tzinfo = IST))

    assert clock.nextRollover(6) == dt.datetime(2026, 8, 6, 6, 0, tzinfo = IST)


def test_nextRolloverBeforeBoundaryStaysSameDay() -> None:
    """
    Issued at 05:00, a token expires at 06:00 the same morning, one hour
    later rather than a day and an hour.
    """

    clock = FakeClock(start = dt.datetime(2026, 8, 5, 5, 0, tzinfo = IST))

    assert clock.nextRollover(6) == dt.datetime(2026, 8, 5, 6, 0, tzinfo = IST)


def test_nextRolloverRejectsImpossibleHour() -> None:
    """
    An out of range hour is a programming error, not a silent no-op.
    """

    clock = FakeClock()

    with pytest.raises(ValueError):
        clock.nextRollover(24)


@pytest.mark.parametrize("moment, expected", [
    (dt.datetime(2026, 8, 5, 9, 14, tzinfo = IST), False),   # pre-open
    (dt.datetime(2026, 8, 5, 9, 15, tzinfo = IST), True),    # open
    (dt.datetime(2026, 8, 5, 12, 0, tzinfo = IST), True),    # midday
    (dt.datetime(2026, 8, 5, 15, 30, tzinfo = IST), True),   # close
    (dt.datetime(2026, 8, 5, 15, 31, tzinfo = IST), False),  # post-close
    (dt.datetime(2026, 8, 8, 12, 0, tzinfo = IST), False),   # saturday
    (dt.datetime(2026, 8, 9, 12, 0, tzinfo = IST), False),   # sunday
])
def test_marketHoursBoundaries(moment : dt.datetime, expected : bool) -> None:
    """
    The regular equity session runs 09:15 to 15:30 IST on weekdays.
    """

    assert FakeClock(start = moment).isMarketOpen() is expected


async def test_fakeClockRecordsSleepsWithoutWaiting() -> None:
    """
    Sleeping is instant and recorded, which is what makes the rate limit
    tests both exact and fast.
    """

    clock = FakeClock()
    started = time.monotonic()

    await clock.sleep(0.5)
    await clock.sleep(120)

    assert clock.sleepCalls == [0.5, 120]
    assert clock.totalSlept == 120.5
    assert clock.monotonic() == 120.5
    assert time.monotonic() - started < 0.5


async def test_fakeClockAdvancesWallAndMonotonicTogether() -> None:
    """
    Both readings move by the same amount, so code mixing the two stays
    self consistent under test.
    """

    clock = FakeClock(start = dt.datetime(2026, 8, 5, 9, 15, tzinfo = IST))

    await clock.sleep(3600)

    assert clock.now() == dt.datetime(2026, 8, 5, 10, 15, tzinfo = IST)
    assert clock.monotonic() == 3600


def test_fakeClockAdvanceToRejectsGoingBackwards() -> None:
    """
    Time only moves forward. A test needing an earlier moment should
    build a new clock rather than rewind a shared one.
    """

    clock = FakeClock(start = dt.datetime(2026, 8, 5, 12, 0, tzinfo = IST))

    with pytest.raises(ValueError):
        clock.advanceTo(dt.datetime(2026, 8, 5, 11, 0, tzinfo = IST))


def test_fakeClockAdvanceToCrossesMidnight() -> None:
    """
    Jumping across an IST midnight advances the trading date, without
    the test computing a second count by hand.
    """

    clock = FakeClock(start = dt.datetime(2026, 8, 5, 23, 0, tzinfo = IST))
    clock.advanceTo(dt.datetime(2026, 8, 6, 1, 0, tzinfo = IST))

    assert clock.tradingDate() == dt.date(2026, 8, 6)
    assert clock.monotonic() == 7200


def test_fakeClockSatisfiesTheAbstractInterface() -> None:
    """
    The fake is a real subclass, so adding an abstract method upstream
    breaks it loudly instead of letting it drift out of sync.
    """

    assert isinstance(FakeClock(), BaseClock)
