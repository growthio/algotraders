# -*- encoding: utf-8 -*-

"""
Deterministic Clock for Time Dependent Tests
----------------------------------------------

:class:`FakeClock` makes elapsed time an input rather than something the
test has to wait for. Sleeping advances the clock instantly and records
the duration, so a rate limiter test can assert the exact sequence of
waits it produced instead of measuring wall time and hoping the machine
was not busy.

That exactness is the point. A tolerance based assertion on a rate
limiter passes just as happily when the limiter is subtly wrong, and a
suite that genuinely sleeps for the intervals under test would take
minutes rather than milliseconds.
"""

import datetime as dt

from typing import List, Optional

from algotraders.core.clock import IST, BaseClock

# ? a Wednesday inside the regular NSE session, so isMarketOpen() is True
# by default and a test has to opt in to the awkward cases
DEFAULT_START = dt.datetime(2026, 8, 5, 9, 15, tzinfo = IST)


class FakeClock(BaseClock):
    """
    A clock that only moves when a test tells it to.

    :type  start: Optional[dt.datetime]
    :param start: Initial wall clock reading. Defaults to a weekday
        inside market hours. A naive value is interpreted as IST.

    :type  monotonicStart: float
    :param monotonicStart: Initial monotonic counter value. Non zero
        defaults are useful for proving that code measures intervals
        rather than treating the counter as an absolute time.
    """

    def __init__(
        self, start : Optional[dt.datetime] = None,
        monotonicStart : float = 0.0
    ) -> None:
        current = start or DEFAULT_START

        if current.tzinfo is None:
            current = current.replace(tzinfo = IST)

        self.current = current.astimezone(IST)
        self.elapsed = monotonicStart

        # ! every sleep duration in call order, which is what rate limit
        # and backoff tests assert against
        self.sleepCalls : List[float] = []


    def now(self) -> dt.datetime:
        """
        Return the current fake wall clock reading in IST.
        """

        return self.current


    def monotonic(self) -> float:
        """
        Return the current fake monotonic counter in seconds.
        """

        return self.elapsed


    async def sleep(self, seconds : float) -> None:
        """
        Record the requested duration and advance instantly.

        Non positive durations are recorded but do not move the clock
        backwards, which mirrors :class:`SystemClock` treating them as a
        no-op.
        """

        self.sleepCalls.append(seconds)

        if seconds > 0:
            self.advance(seconds)


    def advance(self, seconds : float) -> None:
        """
        Move both the wall clock and the monotonic counter forward.

        :type  seconds: float
        :param seconds: How far to advance. Fractional values are kept,
            so sub second rate limit windows can be exercised exactly.
        """

        self.elapsed += seconds
        self.current += dt.timedelta(seconds = seconds)


    def advanceTo(self, moment : dt.datetime) -> None:
        """
        Jump the wall clock to a specific moment, advancing the
        monotonic counter by the same interval.

        Useful for crossing an IST midnight or a token rollover boundary
        without computing the second count by hand.

        :raises ValueError: ``moment`` is in the past. Time only moves
            forward, and a test that needs otherwise wants a new clock.
        """

        target = moment if moment.tzinfo else moment.replace(tzinfo = IST)
        target = target.astimezone(IST)

        delta = (target - self.current).total_seconds()

        if delta < 0:
            raise ValueError(
                f"cannot move the clock backwards, {target} precedes "
                f"{self.current}"
            )

        self.advance(delta)


    @property
    def totalSlept(self) -> float:
        """
        Total of every duration passed to :meth:`sleep`.

        A convenient single-number assertion for tests that care how
        long an operation would have blocked overall rather than about
        the individual waits.
        """

        return sum(self.sleepCalls)


    def reset(self) -> None:
        """
        Clear the recorded sleep history, leaving the clock where it is.
        """

        self.sleepCalls.clear()
