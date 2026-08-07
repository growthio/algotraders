# -*- encoding: utf-8 -*-

"""
Translating Fyers Payloads To and From the Canonical Models
--------------------------------------------------------------

The Fyers SDK does not raise on failure. It returns a dictionary, and a
caller that does not inspect it treats every failure as success. That is
not a hypothetical: in the captured run, 382,452 responses were the SDK
swallowing a JSON decode error against an empty body and returning
``{"error": "Expecting value: line 1 column 1 (char 0)"}``. Code that
only checked ``response["s"] == "error"`` read those as fine and passed
``None`` downstream, where it surfaced much later as an unrelated
``TypeError``.

:func:`ensureSuccess` therefore inverts the test. A response is accepted
only when it is a dictionary whose ``s`` field is exactly ``"ok"``.
Everything else is classified and raised, mapped onto the recovery the
caller should attempt: throttling to
:class:`~algotraders.errors.RateLimitExceeded`, an unknown symbol to
:class:`~algotraders.errors.InstrumentNotFound`, a truncated body to
:class:`~algotraders.errors.TransientBrokerError`.
"""

import datetime as dt

from typing import Any, Dict, Iterable, List, Optional, Sequence

from algotraders.brokers.fyers import limits
from algotraders.core.clock import fromEpoch
from algotraders.errors import (
    InstrumentNotFound, InvalidOrder, OrderRejected, RateLimitExceeded,
    TokenExpiredError, TransientBrokerError, UnsupportedTimeframe
)
from algotraders.logger import getLogger
from algotraders.models.enums import (
    OrderSide, OrderStatus, OrderType, ProductType, Timeframe, Validity
)
from algotraders.models.instrument import Instrument
from algotraders.models.market import Candle, MarketDepth, DepthLevel, Quote
from algotraders.models.orders import OrderRequest, OrderResponse
from algotraders.models.portfolio import Holding, Position

logger = getLogger(__name__)

SOURCE = limits.BROKER_NAME

# ? vendor order encodings. Fyers uses integers for side and type
ORDER_SIDE_MAP : Dict[OrderSide, int] = {
    OrderSide.BUY : 1,
    OrderSide.SELL : -1
}

ORDER_SIDE_REVERSE : Dict[int, OrderSide] = {
    1 : OrderSide.BUY, -1 : OrderSide.SELL
}

ORDER_TYPE_MAP : Dict[OrderType, int] = {
    OrderType.LIMIT : 1,
    OrderType.MARKET : 2,
    OrderType.STOP_LOSS_MARKET : 3,
    OrderType.STOP_LOSS : 4
}

ORDER_TYPE_REVERSE : Dict[int, OrderType] = {
    value : key for key, value in ORDER_TYPE_MAP.items()
}

PRODUCT_TYPE_MAP : Dict[ProductType, str] = {
    ProductType.INTRADAY : "INTRADAY",
    ProductType.DELIVERY : "CNC",
    ProductType.MARGIN : "MARGIN",
    ProductType.COVER : "CO",
    ProductType.BRACKET : "BO"
}

PRODUCT_TYPE_REVERSE : Dict[str, ProductType] = {
    value : key for key, value in PRODUCT_TYPE_MAP.items()
}

VALIDITY_MAP : Dict[Validity, str] = {
    Validity.DAY : "DAY",
    Validity.IOC : "IOC"
}

# ? Fyers reports order state as an integer; 6 is the resting state
ORDER_STATUS_MAP : Dict[int, OrderStatus] = {
    1 : OrderStatus.CANCELLED,
    2 : OrderStatus.COMPLETE,
    3 : OrderStatus.PARTIALLY_FILLED,
    4 : OrderStatus.PENDING,
    5 : OrderStatus.REJECTED,
    6 : OrderStatus.OPEN,
    7 : OrderStatus.EXPIRED
}


def toBrokerTimeframe(timeframe : Timeframe) -> str:
    """
    Convert a canonical timeframe to the Fyers ``resolution`` value.

    :raises UnsupportedTimeframe: Fyers does not serve the timeframe.
        ``1M`` and ``1Y`` exist in the host's database enum but neither
        broker offers them.

    :rtype:   str
    :returns: The vendor resolution code, for example ``"5"`` or ``"D"``.
    """

    resolution = limits.TIMEFRAME_MAP.get(timeframe)

    if resolution is None:
        raise UnsupportedTimeframe(
            brokerName = SOURCE, timeframe = timeframe,
            supported = list(limits.TIMEFRAME_MAP)
        )

    return resolution


def ensureSuccess(response : Any, context : str = "fyers call") -> Dict[str, Any]:
    """
    Validate a Fyers SDK response, raising a typed error on failure.

    Accepts only a dictionary whose ``s`` field is exactly ``"ok"``.
    Anything else is classified and raised. The strictness is
    deliberate: the SDK returns rather than raises, so a permissive
    check here converts a broker failure into a confusing ``None``
    several layers away.

    :type  response: Any
    :param response: Whatever the SDK returned.

    :type  context: str
    :param context: Description used in the error message.

    :raises RateLimitExceeded: The request was throttled, either
        explicitly with code 429 or implicitly through the empty body
        that an edge returns while throttling.

    :raises InstrumentNotFound: The symbol was rejected as unknown.

    :raises TokenExpiredError: The session is no longer accepted.

    :raises TransientBrokerError: The response was not a usable
        dictionary, which covers the SDK's swallowed decode failures.

    :rtype:   Dict[str, Any]
    :returns: The validated payload.
    """

    if not isinstance(response, dict):
        raise TransientBrokerError(
            f"{context}: expected a dict response, got "
            f"{type(response).__name__}"
        )

    status = response.get("s")

    if status == "ok":
        return response

    code = response.get("code")
    message = str(response.get("message") or response.get("error") or response)

    # ! the SDK swallows a JSON decode failure and returns it as a
    # plain "error" key with no status. 382,452 of these appeared in the
    # captured run, every one of them an edge throttling the caller
    if status is None and "error" in response:
        raise TransientBrokerError(
            f"{context}: malformed broker response ({message})"
        )

    if code == limits.ERROR_RATE_LIMIT or "limit" in message.lower():
        raise RateLimitExceeded(f"{context}: {message}")

    if code == limits.ERROR_INVALID_SYMBOL:
        raise InstrumentNotFound(message, brokerName = SOURCE)

    if "token" in message.lower() and "invalid" in message.lower():
        raise TokenExpiredError(f"{context}: {message}")

    raise TransientBrokerError(f"{context}: {message} (code {code})")


def toCandles(
    payload : Dict[str, Any], instrument : Instrument, timeframe : Timeframe
) -> List[Candle]:
    """
    Convert a validated ``/data/history`` payload into candles.

    Fyers returns a bare positional array per bar,
    ``[epoch, open, high, low, close, volume]``, carrying no symbol and
    no timezone. Both are supplied here from the request that produced
    it, and the epoch is converted with an explicit zone rather than
    :meth:`datetime.fromtimestamp`, which reads host local time and was
    the source of timestamps that were hours wrong off an IST machine.

    :rtype:   List[Candle]
    :returns: Candles in the order the broker returned them.
    """

    rows : Iterable[Sequence[Any]] = payload.get("candles") or []
    key = instrument.instrumentKey

    candles : List[Candle] = []

    for row in rows:
        if len(row) < 6:
            logger.debug("skipping malformed candle row: %r", row)

            continue

        candles.append(Candle(
            instrumentKey = key,
            timeframe = timeframe,
            timestamp = fromEpoch(float(row[0])),
            openPrice = float(row[1]),
            highPrice = float(row[2]),
            lowPrice = float(row[3]),
            closePrice = float(row[4]),
            volume = int(row[5]),
            openInterest = int(row[6]) if len(row) > 6 else None,
            source = SOURCE
        ))

    return candles


def toQuote(
    entry : Dict[str, Any], instrument : Instrument, timestamp : dt.datetime
) -> Quote:
    """
    Convert one element of a ``/data/quotes`` response into a quote.

    :rtype:   Quote
    :returns: The normalized snapshot, retaining the vendor payload for
        audit under the excluded ``raw`` field.
    """

    values = entry.get("v", entry)

    return Quote(
        instrumentKey = instrument.instrumentKey,
        lastPrice = float(values.get("lp") or 0.0),
        previousClose = _optionalFloat(values.get("prev_close_price")),
        change = _optionalFloat(values.get("ch")),
        changePercent = _optionalFloat(values.get("chp")),
        dayOpen = _optionalFloat(values.get("open_price")),
        dayHigh = _optionalFloat(values.get("high_price")),
        dayLow = _optionalFloat(values.get("low_price")),
        volume = _optionalInt(values.get("volume")),
        openInterest = _optionalInt(values.get("oi")),
        upperCircuit = _optionalFloat(values.get("upper_ckt")),
        lowerCircuit = _optionalFloat(values.get("lower_ckt")),
        depth = toDepth(values),
        timestamp = timestamp,
        source = SOURCE,
        raw = dict(entry)
    )


def toDepth(values : Dict[str, Any]) -> Optional[MarketDepth]:
    """
    Extract the five level order book from a quote payload, when the
    broker included one.

    :rtype:   Optional[MarketDepth]
    :returns: The book, or ``None`` for a quote without depth.
    """

    book = values.get("depth") or values.get("d")

    if not isinstance(book, dict):
        return None

    def levels(side : str) -> List[DepthLevel]:
        return [
            DepthLevel(
                price = float(level.get("price", 0.0)),
                quantity = int(level.get("volume", 0) or 0),
                orders = _optionalInt(level.get("ord"))
            )
            for level in book.get(side, []) or []
        ]

    return MarketDepth(buy = levels("buy"), sell = levels("sell"))


def toOrderPayload(request : OrderRequest) -> Dict[str, Any]:
    """
    Render a validated order request as the Fyers placement payload.

    :raises InvalidOrder: The order type has no Fyers equivalent.

    :rtype:   Dict[str, Any]
    :returns: The vendor payload, using vendor key names.
    """

    vendorType = ORDER_TYPE_MAP.get(request.orderType)

    if vendorType is None:
        raise InvalidOrder(
            f"fyers does not support order type {request.orderType.value}"
        )

    payload : Dict[str, Any] = {
        "symbol" : request.instrument.brokerSymbol,
        "qty" : request.quantity,
        "type" : vendorType,
        "side" : ORDER_SIDE_MAP[request.side],
        "productType" : PRODUCT_TYPE_MAP[request.productType],
        "limitPrice" : float(request.limitPrice or 0),
        "stopPrice" : float(request.triggerPrice or 0),
        "validity" : VALIDITY_MAP[request.validity],
        "disclosedQty" : int(request.disclosedQuantity or 0),
        "offlineOrder" : False
    }

    if request.tag:
        payload["orderTag"] = request.tag

    return payload


def toOrderResponse(
    payload : Dict[str, Any], orderId : Optional[str] = None
) -> OrderResponse:
    """
    Convert a placement acknowledgement or order book row.

    :raises OrderRejected: The payload describes a refused order.

    :rtype:   OrderResponse
    :returns: The normalized order state.
    """

    identifier = str(
        orderId or payload.get("id") or payload.get("orderNumStatus") or ""
    )

    status = ORDER_STATUS_MAP.get(
        _optionalInt(payload.get("status")) or -1, OrderStatus.UNKNOWN
    )

    if status is OrderStatus.REJECTED:
        raise OrderRejected(
            "fyers rejected the order",
            brokerMessage = payload.get("message"),
            code = payload.get("status")
        )

    return OrderResponse(
        orderId = identifier or "unknown",
        status = status,
        side = ORDER_SIDE_REVERSE.get(_optionalInt(payload.get("side")) or 0),
        orderType = ORDER_TYPE_REVERSE.get(
            _optionalInt(payload.get("type")) or -1
        ),
        productType = PRODUCT_TYPE_REVERSE.get(
            str(payload.get("productType") or "")
        ),
        quantity = _optionalInt(payload.get("qty")),
        filledQuantity = _optionalInt(payload.get("filledQty")) or 0,
        pendingQuantity = _optionalInt(payload.get("remainingQuantity")),
        averagePrice = _optionalFloat(payload.get("tradedPrice")),
        limitPrice = _optionalFloat(payload.get("limitPrice")),
        triggerPrice = _optionalFloat(payload.get("stopPrice")),
        statusMessage = payload.get("message"),
        tag = payload.get("orderTag"),
        source = SOURCE,
        raw = dict(payload)
    )


def toPosition(payload : Dict[str, Any], instrumentKey : str) -> Position:
    """
    Convert a Fyers position row.

    Fyers reports ``netQty`` already signed, so a short position arrives
    negative and matches the canonical convention without adjustment.

    :rtype:   Position
    :returns: The normalized position.
    """

    return Position(
        instrumentKey = instrumentKey,
        productType = PRODUCT_TYPE_REVERSE.get(
            str(payload.get("productType") or ""), ProductType.INTRADAY
        ),
        quantity = _optionalInt(payload.get("netQty")) or 0,
        buyQuantity = _optionalInt(payload.get("buyQty")),
        sellQuantity = _optionalInt(payload.get("sellQty")),
        averagePrice = _optionalFloat(payload.get("netAvg")) or 0.0,
        lastPrice = _optionalFloat(payload.get("ltp")),
        realisedProfit = _optionalFloat(payload.get("realized_profit")),
        unrealisedProfit = _optionalFloat(payload.get("unrealized_profit")),
        source = SOURCE,
        raw = dict(payload)
    )


def toHolding(payload : Dict[str, Any], instrumentKey : str) -> Holding:
    """
    Convert a Fyers holding row.

    :rtype:   Holding
    :returns: The normalized holding.
    """

    quantity = _optionalInt(payload.get("quantity")) or 0

    return Holding(
        instrumentKey = instrumentKey,
        quantity = quantity,
        availableQuantity = _optionalInt(payload.get("remainingQuantity")),
        averagePrice = _optionalFloat(payload.get("costPrice")) or 0.0,
        lastPrice = _optionalFloat(payload.get("ltp")),
        isin = payload.get("isin"),
        source = SOURCE,
        raw = dict(payload)
    )


def _optionalFloat(value : Any) -> Optional[float]:
    """
    Coerce a vendor value to ``float``, tolerating absence and junk.
    """

    if value is None or value == "":
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optionalInt(value : Any) -> Optional[int]:
    """
    Coerce a vendor value to ``int``, tolerating absence and junk.
    """

    if value is None or value == "":
        return None

    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


__all__ = [
    "SOURCE", "toBrokerTimeframe", "ensureSuccess", "toCandles", "toQuote",
    "toDepth", "toOrderPayload", "toOrderResponse", "toPosition",
    "toHolding",
    "ORDER_SIDE_MAP", "ORDER_TYPE_MAP", "PRODUCT_TYPE_MAP",
    "VALIDITY_MAP", "ORDER_STATUS_MAP"
]
