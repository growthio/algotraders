# -*- encoding: utf-8 -*-

"""
Every Zerodha Kite Specific Constant, in One Place
----------------------------------------------------

The same role as the Fyers ``limits.py``, but the values differ in ways
that shape the whole implementation rather than just the numbers.

Kite publishes a **per endpoint class** rate limit, so these are declared
as ``endpointLimits`` rather than the single shared quota Fyers uses. It
also caps historical span **per interval** rather than by resolution
family, from 60 days for one minute bars up to 2000 for daily, so the
same two year request costs a different number of calls on each broker.

Verified 2026-08-05 against the Kite Connect v3 documentation. **These
values move.** Re-verify before relying on them and update the date.
"""

import datetime as dt

from typing import Dict, FrozenSet, Tuple

from algotraders.core.ratelimit import RateLimit
from algotraders.models.auth import TokenPolicy
from algotraders.models.enums import (
    Capability, EndpointClass, Exchange, Segment, Timeframe
)

BROKER_NAME = "kite"

PIP_EXTRA = "kite"

# ---------------------------------------------------------------------
# Rate limits
# ---------------------------------------------------------------------
# ! unlike Fyers, Kite publishes a genuine limit per endpoint class, so
# a quote call does not consume the historical allowance. Limits are
# enforced per API KEY and aggregated across every user of that key,
# which means two workers sharing a key together exceed them
ENDPOINT_LIMITS : Dict[EndpointClass, Tuple[RateLimit, ...]] = {
    EndpointClass.QUOTE : (
        RateLimit("kite.quote", 1, 1.0),
    ),
    EndpointClass.HISTORICAL : (
        RateLimit("kite.historical", 3, 1.0),
    ),
    EndpointClass.ORDER : (
        RateLimit("kite.order", 10, 1.0),
        RateLimit("kite.orderMinute", 200, 60.0)
    ),
    EndpointClass.PORTFOLIO : (
        RateLimit("kite.portfolio", 10, 1.0),
    ),
    EndpointClass.GENERAL : (
        RateLimit("kite.general", 10, 1.0),
    )
}

# ? Kite publishes no daily cap except on order placement
DAILY_QUOTA = None

# ---------------------------------------------------------------------
# Historical data
# ---------------------------------------------------------------------
# ! capped per interval, not per resolution family. A two year one
# minute request is 13 calls here against 8 on Fyers
CHUNK_SPAN : Dict[Timeframe, dt.timedelta] = {
    Timeframe.MINUTE_1  : dt.timedelta(days = 60),
    Timeframe.MINUTE_3  : dt.timedelta(days = 100),
    Timeframe.MINUTE_5  : dt.timedelta(days = 100),
    Timeframe.MINUTE_15 : dt.timedelta(days = 200),
    Timeframe.HOUR_1    : dt.timedelta(days = 400),
    Timeframe.DAY_1     : dt.timedelta(days = 2000)
}

TIMEFRAME_MAP : Dict[Timeframe, str] = {
    Timeframe.MINUTE_1  : "minute",
    Timeframe.MINUTE_3  : "3minute",
    Timeframe.MINUTE_5  : "5minute",
    Timeframe.MINUTE_15 : "15minute",
    Timeframe.HOUR_1    : "60minute",
    Timeframe.DAY_1     : "day"
}

# ? the vendor accepts this exact format for from and to
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# ---------------------------------------------------------------------
# Instruments
# ---------------------------------------------------------------------
# ? Kite returns one dump covering an exchange, fetched through an
# authenticated call rather than a public file as with Fyers
SEGMENT_EXCHANGES : Dict[Segment, Tuple[str, ...]] = {
    Segment.CAPITAL : ("NSE", "BSE"),
    Segment.DERIVATIVE : ("NFO", "BFO"),
    Segment.CURRENCY : ("CDS", "BCD"),
    Segment.COMMODITY : ("MCX",)
}

EXCHANGE_MAP : Dict[str, Exchange] = {
    "NSE" : Exchange.NSE,
    "BSE" : Exchange.BSE,
    "NFO" : Exchange.NFO,
    "BFO" : Exchange.BFO,
    "CDS" : Exchange.CDS,
    "BCD" : Exchange.BCD,
    "MCX" : Exchange.MCX
}

# ---------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------
LOGIN_URL_TEMPLATE = "https://kite.zerodha.com/connect/login?v=3&api_key={apiKey}"

# ! access tokens expire at 06:00 IST the next day as a regulatory
# requirement, and a standard application has no usable refresh flow.
# That single fact means a Kite deployment needs an interactive login
# every morning; there is no unattended path
TOKEN_POLICY = TokenPolicy(
    rolloverHourIST = 6,
    accessTokenLifetime = dt.timedelta(days = 1),
    refreshTokenLifetime = None,
    supportsRefresh = False,
    refreshRequiresPin = False
)

# ---------------------------------------------------------------------
# Capabilities and websocket
# ---------------------------------------------------------------------
# ? no TOKEN_REFRESH, which is what an unattended deployment would need
CAPABILITIES : FrozenSet[Capability] = frozenset({
    Capability.HISTORICAL_DATA,
    Capability.LIVE_QUOTES,
    Capability.MARKET_DEPTH,
    Capability.STREAMING,
    Capability.TRADING,
    Capability.PORTFOLIO
})

WEBSOCKET_URL = "wss://ws.kite.trade"

MAX_STREAM_INSTRUMENTS = 3_000
MAX_STREAM_CONNECTIONS = 3

# ? binary packet sizes per mode, used by the tick parser
PACKET_SIZE_LTP = 8
PACKET_SIZE_QUOTE = 44
PACKET_SIZE_FULL = 184

# ? vendor exception class names. Matched by name rather than imported,
# so this module stays free of any kiteconnect dependency and can be
# read during registration without the SDK installed
TOKEN_EXCEPTIONS = frozenset({"TokenException"})
PERMISSION_EXCEPTIONS = frozenset({"PermissionException"})
TRANSIENT_EXCEPTIONS = frozenset({
    "NetworkException", "DataException", "GeneralException"
})
ORDER_EXCEPTIONS = frozenset({"OrderException"})
INPUT_EXCEPTIONS = frozenset({"InputException"})


__all__ = [
    "BROKER_NAME", "PIP_EXTRA",
    "ENDPOINT_LIMITS", "DAILY_QUOTA",
    "CHUNK_SPAN", "TIMEFRAME_MAP", "DATETIME_FORMAT",
    "SEGMENT_EXCHANGES", "EXCHANGE_MAP",
    "LOGIN_URL_TEMPLATE", "TOKEN_POLICY", "CAPABILITIES",
    "WEBSOCKET_URL", "MAX_STREAM_INSTRUMENTS", "MAX_STREAM_CONNECTIONS",
    "PACKET_SIZE_LTP", "PACKET_SIZE_QUOTE", "PACKET_SIZE_FULL",
    "TOKEN_EXCEPTIONS", "PERMISSION_EXCEPTIONS", "TRANSIENT_EXCEPTIONS",
    "ORDER_EXCEPTIONS", "INPUT_EXCEPTIONS"
]
