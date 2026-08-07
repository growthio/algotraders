# -*- encoding: utf-8 -*-

"""
Canonical Enumerations Shared Across Every Broker
---------------------------------------------------

These enums are the vocabulary the rest of the library speaks. A broker
mapper translates vendor codes into them on the way in and back out on
the way out, so nothing above the broker package ever sees a raw ``1``
meaning buy or a ``"NFO-OPT"`` meaning an option contract.

Every member is a :class:`str` subclass, which makes the values JSON
serialisable, usable directly as FastAPI response fields, and comparable
against plain strings without an explicit conversion.

:NOTE: :class:`Timeframe` values are load bearing. They match the host
    application's ``private.timeframe_value`` PostgreSQL enum exactly,
    so a candle can be written straight to that column. A test asserts
    the equality; changing a value here is a database migration.
"""

from enum import Enum


class Timeframe(str, Enum):
    """
    Candle granularity, using the host database's canonical spellings.

    ``MONTH_1`` and ``YEAR_1`` exist because the host's PostgreSQL enum
    defines them, not because a broker serves them. Neither Fyers nor
    Kite does. Requesting one raises ``UnsupportedTimeframe`` unless the
    caller opts into aggregation from daily candles.
    """

    MINUTE_1  = "1m"
    MINUTE_3  = "3m"
    MINUTE_5  = "5m"
    MINUTE_15 = "15m"
    HOUR_1    = "1h"
    DAY_1     = "1D"
    MONTH_1   = "1M"
    YEAR_1    = "1Y"


class Exchange(str, Enum):
    """
    Trading venue as identified in a symbol.

    ``NFO`` and ``BFO`` are the derivative segments of NSE and BSE. They
    are separate exchanges as far as both broker APIs are concerned,
    which is why they appear here rather than as a flag on ``NSE``.
    """

    NSE   = "NSE"
    BSE   = "BSE"
    NFO   = "NFO"
    BFO   = "BFO"
    MCX   = "MCX"
    CDS   = "CDS"
    BCD   = "BCD"
    NCDEX = "NCDEX"


class Segment(str, Enum):
    """
    Market segment, used to pick which instrument master file to load.

    Fyers publishes one CSV per segment and Kite filters its instrument
    dump by exchange, so this is the axis along which the instrument
    cache is partitioned.
    """

    CAPITAL    = "CM"
    DERIVATIVE = "FO"
    CURRENCY   = "CD"
    COMMODITY  = "COM"


class InstrumentType(str, Enum):
    """
    What kind of contract an instrument is.
    """

    EQUITY   = "EQ"
    INDEX    = "IDX"
    FUTURE   = "FUT"
    OPTION   = "OPT"
    CURRENCY = "CUR"
    COMMODITY = "COM"


class OptionRight(str, Enum):
    """
    Option side. Only meaningful when the instrument type is
    :attr:`InstrumentType.OPTION`.
    """

    CALL = "CE"
    PUT  = "PE"


class OrderSide(str, Enum):
    """
    Direction of an order.

    Both brokers encode this numerically and disagree on the encoding,
    which is exactly the kind of detail the mapper exists to absorb.
    """

    BUY  = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """
    How the order should be priced and triggered.

    ``STOP_LOSS`` carries both a trigger and a limit price;
    ``STOP_LOSS_MARKET`` carries a trigger only. Confusing the two is a
    common and expensive mistake, so ``OrderRequest`` validates the
    price combination for each.
    """

    MARKET           = "MARKET"
    LIMIT            = "LIMIT"
    STOP_LOSS        = "SL"
    STOP_LOSS_MARKET = "SL-M"


class ProductType(str, Enum):
    """
    Position product, which determines margin treatment and whether the
    position auto-squares off at the end of the session.
    """

    INTRADAY = "INTRADAY"
    DELIVERY = "DELIVERY"
    MARGIN   = "MARGIN"
    COVER    = "COVER"
    BRACKET  = "BRACKET"


class Validity(str, Enum):
    """
    How long an unfilled order remains live.
    """

    DAY = "DAY"
    IOC = "IOC"


class OrderStatus(str, Enum):
    """
    Lifecycle state of an order as reported by the broker.

    ``UNKNOWN`` exists deliberately. Vendors add states without notice,
    and mapping an unrecognised one onto ``REJECTED`` or ``COMPLETE``
    would be a dangerous guess about whether an order is live.
    """

    PENDING           = "PENDING"
    OPEN              = "OPEN"
    PARTIALLY_FILLED  = "PARTIALLY_FILLED"
    COMPLETE          = "COMPLETE"
    CANCELLED         = "CANCELLED"
    REJECTED          = "REJECTED"
    EXPIRED           = "EXPIRED"
    UNKNOWN           = "UNKNOWN"


class StreamMode(str, Enum):
    """
    How much detail a websocket subscription should deliver.

    Bandwidth is the reason this is a choice: Kite sends 8 bytes per
    tick in ``LTP`` mode and 184 in ``FULL``, and at 3000 instruments
    that difference decides whether the feed keeps up.
    """

    LTP   = "LTP"
    QUOTE = "QUOTE"
    FULL  = "FULL"


class EndpointClass(str, Enum):
    """
    Rate limit bucket an API call is charged against.

    Kite publishes a separate limit per class, so every call has to
    declare which one it belongs to. Fyers publishes a single app wide
    quota, and its policy maps every class onto the same shared buckets.
    """

    HISTORICAL = "HISTORICAL"
    QUOTE      = "QUOTE"
    ORDER      = "ORDER"
    PORTFOLIO  = "PORTFOLIO"
    GENERAL    = "GENERAL"


class Capability(str, Enum):
    """
    Optional features a broker implementation may or may not provide.

    Declared on the registry entry point so a host can ask what a
    session supports instead of calling a method and handling the
    resulting ``NotImplementedError``.
    """

    HISTORICAL_DATA = "HISTORICAL_DATA"
    LIVE_QUOTES     = "LIVE_QUOTES"
    MARKET_DEPTH    = "MARKET_DEPTH"
    STREAMING       = "STREAMING"
    ORDER_STREAM    = "ORDER_STREAM"
    TRADING         = "TRADING"
    PORTFOLIO       = "PORTFOLIO"
    TOKEN_REFRESH   = "TOKEN_REFRESH"
    BASKET_ORDERS   = "BASKET_ORDERS"


# ? the exact tuple stored in the host's private.timeframe_value enum,
# duplicated here so a test can assert the two never drift apart
TIMEFRAME_DATABASE_VALUES = (
    "1m", "3m", "5m", "15m", "1h", "1D", "1M", "1Y"
)

# ? timeframes no supported broker serves natively, kept separate so the
# mapper can produce one consistent error rather than each broker
# inventing its own message
AGGREGATE_ONLY_TIMEFRAMES = frozenset({Timeframe.MONTH_1, Timeframe.YEAR_1})

# ? every venue that trades derivatives, including the commodity and
# currency ones. NCDEX was missing, so it fell through to the capital
# segment and resolved against the wrong instrument master
DERIVATIVE_EXCHANGES = frozenset({
    Exchange.NFO, Exchange.BFO, Exchange.MCX, Exchange.NCDEX,
    Exchange.CDS, Exchange.BCD
})


__all__ = [
    "Timeframe", "Exchange", "Segment", "InstrumentType", "OptionRight",
    "OrderSide", "OrderType", "ProductType", "Validity", "OrderStatus",
    "StreamMode", "EndpointClass", "Capability",
    "TIMEFRAME_DATABASE_VALUES", "AGGREGATE_ONLY_TIMEFRAMES",
    "DERIVATIVE_EXCHANGES"
]
