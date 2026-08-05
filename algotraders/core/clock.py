# -*- encoding: utf-8 -*-

"""
Injectable Clock Abstraction Anchored to Indian Market Time
-------------------------------------------------------------

Every time-dependent decision in this library goes through a clock
object rather than calling :mod:`time` or :mod:`datetime` directly. Two
reasons, both practical.

The first is correctness. Indian markets, SEBI token expiry and the
exchange trading calendar are all expressed in ``Asia/Kolkata``, while
the process running this code may sit in any timezone at all. The
original implementation converted candle timestamps with a bare
``x.timestamp()``, which silently used the machine's local zone and
produced data that was wrong by hours on any host not set to IST.
:meth:`BaseClock.now` always returns a timezone aware IST value, and
naive datetimes are rejected at the model boundary.

The second is testability. Rate limiting and retry backoff are pure
functions of elapsed time. With an injected clock their tests assert an
exact sleep sequence and finish in microseconds; with the real clock
they would need tolerances and would take minutes. Anything that sleeps
must ``await clock.sleep(...)`` rather than :func:`asyncio.sleep`, and
anything that measures must use :meth:`BaseClock.monotonic` rather than
:func:`time.monotonic`.

:NOTE: :meth:`BaseClock.monotonic` is for measuring intervals and has no
    relationship to wall clock time. Never compare it against
    :meth:`BaseClock.now`.
"""

import time
import asyncio
import datetime as dt
import zoneinfo as zi

from abc import ABC, abstractmethod
from typing import Optional

IST = zi.ZoneInfo("Asia/Kolkata")

# ? NSE and BSE regular session, used for the market-hours helpers that
# the stream supervisor needs to decide whether silence is suspicious
MARKET_OPEN = dt.time(9, 15)
MARKET_CLOSE = dt.time(15, 30)


class BaseClock(ABC):
    """
    Source of time for every component that needs one.

    Concrete implementations supply three primitives; everything else on
    this class is derived from them, so a fake only has to get those
    three right.
    """

    @abstractmethod
    def now(self) -> dt.datetime:
        """
        Return the current wall clock time as a timezone aware value in
        ``Asia/Kolkata``.

        :rtype:   dt.datetime
        :returns: Current IST time. Never naive.
        """

        pass


    @abstractmethod
    def monotonic(self) -> float:
        """
        Return a monotonically increasing counter in seconds.

        Used for interval measurement in the rate limiter, where a wall
        clock adjustment mid-run would otherwise corrupt the bucket
        accounting.
        """

        pass


    @abstractmethod
    async def sleep(self, seconds : float) -> None:
        """
        Suspend the calling coroutine for the given duration.

        :type  seconds: float
        :param seconds: How long to wait. Non positive values return
            immediately without yielding.
        """

        pass


    def tradingDate(self) -> dt.date:
        """
        Return the IST calendar date the current moment belongs to.

        Used as the freshness anchor for the instrument cache. Futures
        and options instrument tokens are flushed at every expiry, so a
        cache is considered stale the moment the trading date advances
        rather than after a rolling time to live.

        :rtype:   dt.date
        :returns: Today's date in IST.
        """

        return self.now().date()


    def nextRollover(
        self, hour : int, reference : Optional[dt.datetime] = None
    ) -> dt.datetime:
        """
        Return the next occurrence of a given IST hour boundary.

        Broker sessions expire on a wall clock boundary rather than
        after a fixed duration: Kite invalidates access tokens at 06:00
        IST regardless of when they were issued. This computes that
        moment so token expiry can be evaluated locally.

        :type  hour: int
        :param hour: Hour of the day in IST, 0 through 23.

        :type  reference: Optional[dt.datetime]
        :param reference: Moment to compute forward from. Defaults to
            the current time. A naive value is assumed to be IST.

        **Example Usage**

        A token issued at 22:00 IST expires at 06:00 the next morning.

        .. code-block:: python

            issued = dt.datetime(2026, 8, 5, 22, 0, tzinfo = IST)
            clock.nextRollover(6, reference = issued)
            # datetime(2026, 8, 6, 6, 0, tzinfo = IST)

        :raises ValueError: ``hour`` is outside 0 through 23.

        :rtype:   dt.datetime
        :returns: The next IST datetime at that hour, strictly after the
            reference moment.
        """

        if not 0 <= hour <= 23:
            raise ValueError(f"hour must be within 0 and 23, got {hour}")

        current = reference or self.now()

        if current.tzinfo is None:
            current = current.replace(tzinfo = IST)
        else:
            current = current.astimezone(IST)

        candidate = current.replace(
            hour = hour, minute = 0, second = 0, microsecond = 0
        )

        if candidate <= current:
            candidate += dt.timedelta(days = 1)

        return candidate


    def isMarketOpen(self, reference : Optional[dt.datetime] = None) -> bool:
        """
        Report whether IST wall clock time falls inside the regular
        equity session on a weekday.

        Deliberately ignores trading holidays. The library has no
        exchange calendar and inventing one would be worse than being
        approximately right: this is used to decide whether a silent
        market data feed is suspicious, not to gate order placement.

        :rtype:   bool
        :returns: ``True`` during 09:15 to 15:30 IST, Monday to Friday.
        """

        current = (reference or self.now()).astimezone(IST)

        if current.weekday() >= 5:
            return False

        return MARKET_OPEN <= current.time() <= MARKET_CLOSE


class SystemClock(BaseClock):
    """
    The real clock, backed by the operating system.

    This is the default everywhere. Production code never constructs a
    different implementation; tests inject ``FakeClock`` instead.
    """

    def now(self) -> dt.datetime:
        """
        Return the current time converted to IST.
        """

        return dt.datetime.now(tz = IST)


    def monotonic(self) -> float:
        """
        Return :func:`time.monotonic`, which is unaffected by system
        clock adjustments.
        """

        return time.monotonic()


    async def sleep(self, seconds : float) -> None:
        """
        Await :func:`asyncio.sleep`, skipping the call entirely for a
        non positive duration so a zero wait does not force a needless
        trip through the event loop.
        """

        if seconds > 0:
            await asyncio.sleep(seconds)


def toIST(value : dt.datetime) -> dt.datetime:
    """
    Convert a datetime to ``Asia/Kolkata``, treating a naive value as
    already being IST.

    Assuming IST for naive input is the safe reading in this codebase:
    every naive datetime in the trading domain, whether typed by an
    operator or parsed from a broker payload, is an Indian market
    timestamp. Assuming UTC instead would silently shift data by five
    and a half hours.

    :type  value: dt.datetime
    :param value: The datetime to convert.

    :rtype:   dt.datetime
    :returns: The same moment expressed in IST, always timezone aware.
    """

    if value.tzinfo is None:
        return value.replace(tzinfo = IST)

    return value.astimezone(IST)


def fromEpoch(seconds : float) -> dt.datetime:
    """
    Convert a Unix epoch value to a timezone aware IST datetime.

    Broker candle payloads carry epoch seconds. Converting them with a
    bare :meth:`datetime.fromtimestamp` uses the host's local zone,
    which is the defect this helper exists to prevent.

    :type  seconds: float
    :param seconds: Seconds since the Unix epoch, UTC based.

    :rtype:   dt.datetime
    :returns: The corresponding IST datetime.
    """

    return dt.datetime.fromtimestamp(seconds, tz = dt.timezone.utc).astimezone(IST)


def toEpoch(value : dt.datetime) -> int:
    """
    Convert a datetime to whole epoch seconds, normalising to IST first.

    :type  value: dt.datetime
    :param value: The datetime to convert. Naive values are read as IST.

    :rtype:   int
    :returns: Seconds since the Unix epoch, truncated.
    """

    return int(toIST(value).timestamp())


__all__ = [
    "IST", "MARKET_OPEN", "MARKET_CLOSE",
    "BaseClock", "SystemClock",
    "toIST", "fromEpoch", "toEpoch"
]
