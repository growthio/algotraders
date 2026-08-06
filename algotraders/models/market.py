# -*- encoding: utf-8 -*-

"""
Normalized Market Data Records
--------------------------------

What a broker returns and what a database wants to store are different
shapes. Fyers sends a bare positional array,
``[epoch, o, h, l, c, volume]``, with no symbol attached. Kite sends
objects with ISO timestamps and an optional open interest column. Both
are converted here into one record that carries its own identity and
provenance.

Two fields deserve explanation.

``timestamp`` must be timezone aware. The original implementation used
``datetime.fromtimestamp`` with no zone, which silently read the host
machine's locale and produced candles that were hours wrong on any
container not set to IST. Naive values are rejected outright rather than
assumed, because a wrong timestamp on price data is invisible until it
corrupts a backtest.

``source`` is mandatory on every record. Candles for the same instrument
and timeframe are not interchangeable across brokers: hourly buckets are
anchored differently, and volume can disagree. Without provenance a
table mixing two sources produces a chart nobody can explain.
"""

import datetime as dt

from typing import Any, Dict, List, Optional

from pydantic import Field, field_validator

from algotraders.core.clock import toIST
from algotraders.models.base import AlgoTradersModel
from algotraders.models.enums import StreamMode, Timeframe


def requireAwareIST(value : Optional[dt.datetime]) -> Optional[dt.datetime]:
    """
    Validate that a datetime carries a timezone and normalise it to IST.

    :type  value: Optional[dt.datetime]
    :param value: The value under validation.

    :raises ValueError: The datetime is naive. Guessing a zone for
        market data is how silent multi-hour errors are introduced, so
        the caller is required to be explicit.

    :rtype:   Optional[dt.datetime]
    :returns: The same instant expressed in ``Asia/Kolkata``.
    """

    if value is None:
        return None

    if value.tzinfo is None:
        raise ValueError(
            "market data timestamps must be timezone aware; a naive value "
            "would be silently read as host local time"
        )

    return toIST(value)


class Candle(AlgoTradersModel):
    """
    One OHLCV bar for an instrument at a given granularity.

    Dumped with ``by_alias = True`` this produces exactly the column
    names the host's ``private.security_prices_tx`` table uses, so
    ingestion is an insert rather than a translation.

    :type  instrumentKey: str
    :param instrumentKey: Canonical instrument key in string form.

    :type  timeframe: Timeframe
    :param timeframe: Bar granularity. The enum value matches the host's
        ``private.timeframe_value`` PostgreSQL type.

    :type  timestamp: dt.datetime
    :param timestamp: Bar open time, timezone aware, in IST.

    :type  volume: int
    :param volume: Traded quantity. Zero is legitimate for an illiquid
        contract and is not treated as missing data.

    :type  openInterest: Optional[int]
    :param openInterest: Open interest, derivatives only, and only when
        the broker was asked for it.

    :type  source: str
    :param source: Broker the bar came from.
    """

    instrumentKey : str = Field(min_length = 1)
    timeframe : Timeframe
    timestamp : dt.datetime
    openPrice : float
    highPrice : float
    lowPrice : float
    closePrice : float
    volume : int = Field(ge = 0)
    openInterest : Optional[int] = Field(default = None, ge = 0)
    source : str = Field(min_length = 1)

    _normaliseTimestamp = field_validator("timestamp")(requireAwareIST)


    @property
    def isConsistent(self) -> bool:
        """
        Whether the bar satisfies the usual OHLC ordering.

        Reported rather than enforced. A broker occasionally emits a bar
        where the high is below the open, and discarding real data over
        it would leave a silent hole in the series; the caller decides
        whether to keep it.
        """

        return (
            self.lowPrice <= self.openPrice <= self.highPrice
            and self.lowPrice <= self.closePrice <= self.highPrice
        )


class DepthLevel(AlgoTradersModel):
    """
    A single price level on one side of the order book.

    :type  orders: Optional[int]
    :param orders: Number of resting orders at this level. Fyers omits
        it, Kite supplies it.
    """

    price : float
    quantity : int = Field(ge = 0)
    orders : Optional[int] = Field(default = None, ge = 0)


class MarketDepth(AlgoTradersModel):
    """
    Aggregated order book, five levels per side on both brokers.

    :type  buy: List[DepthLevel]
    :param buy: Bid levels, best first.

    :type  sell: List[DepthLevel]
    :param sell: Ask levels, best first.
    """

    buy : List[DepthLevel] = Field(default_factory = list)
    sell : List[DepthLevel] = Field(default_factory = list)


    @property
    def spread(self) -> Optional[float]:
        """
        Difference between the best ask and the best bid.

        :rtype:   Optional[float]
        :returns: The spread, or ``None`` when either side is empty,
            which happens at a circuit limit or before the open.
        """

        if not self.buy or not self.sell:
            return None

        return self.sell[0].price - self.buy[0].price


class Tick(AlgoTradersModel):
    """
    A single streamed market data update.

    Most fields are optional because the payload depends on the
    subscription mode: Kite's ``LTP`` mode carries eight bytes and fills
    in ``lastPrice`` alone, while ``FULL`` carries depth as well.
    Modelling that as one type with optional fields keeps the consumer
    from branching on broker and mode.

    :type  receivedAt: dt.datetime
    :param receivedAt: When this process saw the tick. Distinct from
        :attr:`exchangeTimestamp`, and the difference between the two is
        the only feed latency measurement available to the host.

    :type  mode: StreamMode
    :param mode: Subscription mode the tick arrived under, which tells a
        consumer which fields it can rely on.
    """

    instrumentKey : str = Field(min_length = 1)
    lastPrice : float
    lastQuantity : Optional[int] = Field(default = None, ge = 0)
    averagePrice : Optional[float] = None
    volume : Optional[int] = Field(default = None, ge = 0)
    buyQuantity : Optional[int] = Field(default = None, ge = 0)
    sellQuantity : Optional[int] = Field(default = None, ge = 0)
    dayOpen : Optional[float] = None
    dayHigh : Optional[float] = None
    dayLow : Optional[float] = None
    dayClose : Optional[float] = None
    openInterest : Optional[int] = Field(default = None, ge = 0)
    exchangeTimestamp : Optional[dt.datetime] = None
    receivedAt : dt.datetime
    mode : StreamMode = StreamMode.QUOTE
    depth : Optional[MarketDepth] = None
    source : str = Field(min_length = 1)

    _normaliseExchangeTime = field_validator("exchangeTimestamp")(
        requireAwareIST
    )
    _normaliseReceivedAt = field_validator("receivedAt")(requireAwareIST)


    @property
    def feedLatency(self) -> Optional[dt.timedelta]:
        """
        Time between the exchange stamping the tick and this process
        receiving it.

        :rtype:   Optional[dt.timedelta]
        :returns: The delay, or ``None`` when the broker sent no
            exchange timestamp.
        """

        if self.exchangeTimestamp is None:
            return None

        return self.receivedAt - self.exchangeTimestamp


class Quote(AlgoTradersModel):
    """
    A point in time snapshot, as returned by a request/response quote
    call rather than by the stream.

    :type  raw: Dict[str, Any]
    :param raw: The untouched vendor payload. Excluded from
        serialisation, so it never reaches a FastAPI response body or a
        database column, but available in process for audit logging and
        for debugging a mapper against what the broker actually sent.
    """

    instrumentKey : str = Field(min_length = 1)
    lastPrice : float
    previousClose : Optional[float] = None
    change : Optional[float] = None
    changePercent : Optional[float] = None
    dayOpen : Optional[float] = None
    dayHigh : Optional[float] = None
    dayLow : Optional[float] = None
    volume : Optional[int] = Field(default = None, ge = 0)
    openInterest : Optional[int] = Field(default = None, ge = 0)
    upperCircuit : Optional[float] = None
    lowerCircuit : Optional[float] = None
    depth : Optional[MarketDepth] = None
    timestamp : dt.datetime
    source : str = Field(min_length = 1)
    raw : Dict[str, Any] = Field(default_factory = dict, exclude = True)

    _normaliseTimestamp = field_validator("timestamp")(requireAwareIST)


__all__ = [
    "Candle", "Tick", "Quote", "DepthLevel", "MarketDepth", "requireAwareIST"
]
