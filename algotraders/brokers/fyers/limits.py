# -*- encoding: utf-8 -*-

"""
Every Fyers Specific Constant, in One Place
---------------------------------------------

Rate limits, chunk spans, symbol master URLs, timeframe codes and the
token policy all live here rather than being scattered through the
implementation. The reason is maintenance: a broker can change any of
these unilaterally and without notice, and when that happens the fix
should be a one line edit in a file whose only job is to hold them.

Verified 2026-08-05 against the Fyers API v3 documentation. **These
values move.** Re-verify before relying on them and update the date.
"""

import datetime as dt

from typing import Dict, FrozenSet, Tuple

from algotraders.core.ratelimit import RateLimit
from algotraders.models.auth import TokenPolicy
from algotraders.models.enums import Capability, Segment, Timeframe

BROKER_NAME = "fyers"

PIP_EXTRA = "fyers"

# ---------------------------------------------------------------------
# Rate limits
# ---------------------------------------------------------------------
# ! Fyers publishes ONE application wide quota covering every endpoint,
# not a limit per endpoint class. These are passed as sharedLimits so a
# single set of buckets is contended by historical, quote and order
# calls alike. Splitting them per class enforced the quota several times
# over and is what produced 170,132 HTTP 429 responses in the captured
# run that motivated this refactor.
SHARED_LIMITS : Tuple[RateLimit, ...] = (
    RateLimit("fyers.perSecond", 10, 1.0),
    RateLimit("fyers.perMinute", 200, 60.0)
)

DAILY_QUOTA = 100_000

DAILY_QUOTA_NAME = "fyers.perDay"

# ---------------------------------------------------------------------
# Historical data
# ---------------------------------------------------------------------
# ? Fyers caps by resolution family rather than per resolution: every
# intraday resolution shares a 100 day ceiling, and only daily differs
INTRADAY_CHUNK_SPAN = dt.timedelta(days = 100)
DAILY_CHUNK_SPAN = dt.timedelta(days = 366)

CHUNK_SPAN : Dict[Timeframe, dt.timedelta] = {
    Timeframe.MINUTE_1  : INTRADAY_CHUNK_SPAN,
    Timeframe.MINUTE_3  : INTRADAY_CHUNK_SPAN,
    Timeframe.MINUTE_5  : INTRADAY_CHUNK_SPAN,
    Timeframe.MINUTE_15 : INTRADAY_CHUNK_SPAN,
    Timeframe.HOUR_1    : INTRADAY_CHUNK_SPAN,
    Timeframe.DAY_1     : DAILY_CHUNK_SPAN
}

# ? the vendor "resolution" parameter. Minutes are sent as a bare number
# in a string and daily as "D"; there is no monthly or yearly resolution
TIMEFRAME_MAP : Dict[Timeframe, str] = {
    Timeframe.MINUTE_1  : "1",
    Timeframe.MINUTE_3  : "3",
    Timeframe.MINUTE_5  : "5",
    Timeframe.MINUTE_15 : "15",
    Timeframe.HOUR_1    : "60",
    Timeframe.DAY_1     : "D"
}

# ---------------------------------------------------------------------
# Symbol masters
# ---------------------------------------------------------------------
# ? public CSVs, no authentication required. Fyers has been migrating
# some of these toward a _sym_master.json form, so verify before relying
# on the CSV shape in a new deployment
SYMBOL_MASTER_BASE = "https://public.fyers.in/sym_details"

SYMBOL_MASTER_URLS : Dict[Segment, Tuple[str, ...]] = {
    Segment.CAPITAL : (
        f"{SYMBOL_MASTER_BASE}/NSE_CM.csv",
        f"{SYMBOL_MASTER_BASE}/BSE_CM.csv"
    ),
    Segment.DERIVATIVE : (
        f"{SYMBOL_MASTER_BASE}/NSE_FO.csv",
        f"{SYMBOL_MASTER_BASE}/BSE_FO.csv"
    ),
    Segment.CURRENCY : (
        f"{SYMBOL_MASTER_BASE}/NSE_CD.csv",
    ),
    Segment.COMMODITY : (
        f"{SYMBOL_MASTER_BASE}/MCX_COM.csv",
    )
}

# ---------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------
AUTH_BASE_URL = "https://api-t1.fyers.in/api/v3"

REFRESH_TOKEN_URL = f"{AUTH_BASE_URL}/validate-refresh-token"

DEFAULT_REDIRECT_URL = (
    "https://trade.fyers.in/api-login/redirect-uri/index.html"
)

# ! access tokens die at the SEBI daily cutoff regardless of when they
# were issued, so a token minted at 22:00 is dead by the next morning
# rather than 24 hours later. Refresh tokens last 15 days and need the
# trading PIN, which is what makes unattended daily operation possible
TOKEN_POLICY = TokenPolicy(
    rolloverHourIST = 6,
    accessTokenLifetime = dt.timedelta(days = 1),
    refreshTokenLifetime = dt.timedelta(days = 15),
    supportsRefresh = True,
    refreshRequiresPin = True
)

# ---------------------------------------------------------------------
# Capabilities and vendor codes
# ---------------------------------------------------------------------
CAPABILITIES : FrozenSet[Capability] = frozenset({
    Capability.HISTORICAL_DATA,
    Capability.LIVE_QUOTES,
    Capability.MARKET_DEPTH,
    Capability.STREAMING,
    Capability.ORDER_STREAM,
    Capability.TRADING,
    Capability.PORTFOLIO,
    Capability.TOKEN_REFRESH
})

# ? vendor error codes worth distinguishing. -50 means the requested
# range exceeded the span cap, which the chunker now prevents; -300 is
# an unknown symbol, which the instrument resolver now catches locally
ERROR_RANGE_TOO_LARGE = -50
ERROR_INVALID_SYMBOL = -300
ERROR_RATE_LIMIT = 429

# ? roughly 200 symbols per data socket connection
MAX_STREAM_SYMBOLS = 200


__all__ = [
    "BROKER_NAME", "PIP_EXTRA",
    "SHARED_LIMITS", "DAILY_QUOTA", "DAILY_QUOTA_NAME",
    "CHUNK_SPAN", "INTRADAY_CHUNK_SPAN", "DAILY_CHUNK_SPAN",
    "TIMEFRAME_MAP",
    "SYMBOL_MASTER_BASE", "SYMBOL_MASTER_URLS",
    "AUTH_BASE_URL", "REFRESH_TOKEN_URL", "DEFAULT_REDIRECT_URL",
    "TOKEN_POLICY", "CAPABILITIES",
    "ERROR_RANGE_TOO_LARGE", "ERROR_INVALID_SYMBOL", "ERROR_RATE_LIMIT",
    "MAX_STREAM_SYMBOLS"
]
