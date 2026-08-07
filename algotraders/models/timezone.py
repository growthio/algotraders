# -*- encoding: utf-8 -*-

"""
Indian Market Timezone Primitives
-----------------------------------

Timezone conversion sits at the very bottom of the dependency graph
because both ends of the library need it. The models validate that every
timestamp is timezone aware, and :mod:`algotraders.core.clock` builds
its whole notion of "now" on the same zone.

It lives under ``models`` rather than ``core`` to keep the documented
layering honest: ``models`` may import only ``errors``, so a model that
reached into ``core`` for a timezone helper would invert the dependency
and, in practice, produce a circular import the moment ``core`` grew a
module that touched a model.

:NOTE: Naive datetimes are interpreted as IST throughout. Every naive
    datetime in this domain, whether typed by an operator or parsed from
    a broker payload, is an Indian market timestamp. Assuming UTC would
    silently shift market data by five and a half hours.
"""

import datetime as dt
import zoneinfo as zi

IST = zi.ZoneInfo("Asia/Kolkata")

# ? NSE and BSE regular equity session, in IST
MARKET_OPEN = dt.time(9, 15)
MARKET_CLOSE = dt.time(15, 30)


def toIST(value : dt.datetime) -> dt.datetime:
    """
    Convert a datetime to ``Asia/Kolkata``, reading a naive value as
    already being IST.

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
    which is the defect this helper exists to prevent: the same payload
    decoded on a developer laptop and on a UTC container produced
    timestamps hours apart.

    :type  seconds: float
    :param seconds: Seconds since the Unix epoch, UTC based.

    :rtype:   dt.datetime
    :returns: The corresponding IST datetime.
    """

    return dt.datetime.fromtimestamp(
        seconds, tz = dt.timezone.utc
    ).astimezone(IST)


def toEpoch(value : dt.datetime) -> int:
    """
    Convert a datetime to whole epoch seconds, normalising to IST first.

    :type  value: dt.datetime
    :param value: The datetime to convert. Naive values are read as IST.

    :rtype:   int
    :returns: Seconds since the Unix epoch, truncated.
    """

    return int(toIST(value).timestamp())


__all__ = ["IST", "MARKET_OPEN", "MARKET_CLOSE", "toIST", "fromEpoch", "toEpoch"]
