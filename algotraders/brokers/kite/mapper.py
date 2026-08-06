# -*- encoding: utf-8 -*-

"""
Translating Kite Payloads To and From the Canonical Models
-------------------------------------------------------------

Unlike Fyers, the Kite SDK **does** raise on failure, with a typed
exception per class of problem. That is better behaviour, but it moves
the work rather than removing it: those exceptions have to be classified
onto the same recovery actions the rest of the library speaks.

The classification is done by exception **class name** rather than by
importing ``kiteconnect.exceptions``. Keeping this module free of any
vendor import is what lets the registry read Kite's capabilities and
rate limits during registration on a machine where the SDK is not
installed at all.

One mapping matters more than the others.
``PermissionException`` on a historical call means the account has not
bought the historical data add on, which is a billing action rather than
a transient fault. Retrying it burns rate limit for something that can
never succeed, so it becomes
:class:`~algotraders.errors.DataSubscriptionRequired`.
"""

import datetime as dt

from typing import Any, Dict, List, Optional

from algotraders.brokers.kite import limits
from algotraders.errors import (
    DataSubscriptionRequired, InvalidOrder, OrderRejected, RateLimitExceeded,
    TokenExpiredError, TransientBrokerError, UnsupportedTimeframe
)
from algotraders.logger import getLogger
from algotraders.models.enums import (
    OrderSide, OrderStatus, OrderType, ProductType, Timeframe, Validity
)
from algotraders.models.instrument import Instrument
from algotraders.models.market import Candle, DepthLevel, MarketDepth, Quote
from algotraders.models.orders import OrderRequest, OrderResponse
from algotraders.models.portfolio import Holding, Position
from algotraders.models.timezone import toIST

logger = getLogger(__name__)

SOURCE = limits.BROKER_NAME

ORDER_SIDE_MAP : Dict[OrderSide, str] = {
    OrderSide.BUY : "BUY",
    OrderSide.SELL : "SELL"
}

ORDER_TYPE_MAP : Dict[OrderType, str] = {
    OrderType.MARKET : "MARKET",
    OrderType.LIMIT : "LIMIT",
    OrderType.STOP_LOSS : "SL",
    OrderType.STOP_LOSS_MARKET : "SL-M"
}

ORDER_TYPE_REVERSE : Dict[str, OrderType] = {
    value : key for key, value in ORDER_TYPE_MAP.items()
}

PRODUCT_TYPE_MAP : Dict[ProductType, str] = {
    ProductType.INTRADAY : "MIS",
    ProductType.DELIVERY : "CNC",
    ProductType.MARGIN : "NRML",
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

ORDER_STATUS_MAP : Dict[str, OrderStatus] = {
    "COMPLETE" : OrderStatus.COMPLETE,
    "CANCELLED" : OrderStatus.CANCELLED,
    "REJECTED" : OrderStatus.REJECTED,
    "OPEN" : OrderStatus.OPEN,
    "TRIGGER PENDING" : OrderStatus.PENDING,
    "AMO REQ RECEIVED" : OrderStatus.PENDING,
    "PUT ORDER REQ RECEIVED" : OrderStatus.PENDING,
    "VALIDATION PENDING" : OrderStatus.PENDING,
    "OPEN PENDING" : OrderStatus.PENDING,
    "MODIFY VALIDATION PENDING" : OrderStatus.PENDING,
    "CANCEL PENDING" : OrderStatus.PENDING
}


def toBrokerTimeframe(timeframe : Timeframe) -> str:
    """
    Convert a canonical timeframe to the Kite ``interval`` value.

    :raises UnsupportedTimeframe: Kite does not serve the timeframe.

    :rtype:   str
    :returns: The vendor interval, for example ``"5minute"``.
    """

    interval = limits.TIMEFRAME_MAP.get(timeframe)

    if interval is None:
        raise UnsupportedTimeframe(
            brokerName = SOURCE, timeframe = timeframe,
            supported = list(limits.TIMEFRAME_MAP)
        )

    return interval


def translateException(
    error : BaseException, endpoint : str = "kite call"
) -> BaseException:
    """
    Map a vendor exception onto this library's recovery vocabulary.

    Classified by class name so this module needs no vendor import. The
    name is stable across SDK versions in a way the module path is not.

    :type  error: BaseException
    :param error: The exception the SDK raised.

    :type  endpoint: str
    :param endpoint: Description used in the resulting message, and the
        signal that distinguishes a historical permission failure from
        any other.

    :rtype:   BaseException
    :returns: The translated exception, ready to raise.
    """

    name = type(error).__name__
    message = str(error)

    if name in limits.TOKEN_EXCEPTIONS:
        return TokenExpiredError(f"{endpoint}: {message}")

    if name in limits.PERMISSION_EXCEPTIONS:
        # ! historical data is a paid Kite add on. Without it every
        # historical call fails permanently, so retrying is pure waste
        if "historical" in endpoint.lower():
            return DataSubscriptionRequired(
                "kite historical data is a paid add-on and this account "
                f"is not subscribed: {message}"
            )

        return DataSubscriptionRequired(f"{endpoint}: {message}")

    if "too many requests" in message.lower() or "rate" in message.lower():
        return RateLimitExceeded(f"{endpoint}: {message}")

    if name in limits.ORDER_EXCEPTIONS:
        return OrderRejected(f"{endpoint} rejected", brokerMessage = message)

    if name in limits.INPUT_EXCEPTIONS:
        return InvalidOrder(f"{endpoint}: {message}")

    if name in limits.TRANSIENT_EXCEPTIONS:
        return TransientBrokerError(f"{endpoint}: {message}")

    return error


def toCandles(
    rows : List[Dict[str, Any]], instrument : Instrument,
    timeframe : Timeframe
) -> List[Candle]:
    """
    Convert a ``historical_data`` response into candles.

    The SDK returns dictionaries with a ``date`` already parsed into an
    aware :class:`datetime`, which is friendlier than the Fyers
    positional array, but it is normalised to IST anyway so candles from
    both brokers compare directly.

    :rtype:   List[Candle]
    :returns: Candles in the order the broker returned them.
    """

    key = instrument.instrumentKey
    candles : List[Candle] = []

    for row in rows or []:
        stamp = row.get("date")

        if stamp is None:
            continue

        candles.append(Candle(
            instrumentKey = key,
            timeframe = timeframe,
            timestamp = toIST(stamp),
            openPrice = float(row["open"]),
            highPrice = float(row["high"]),
            lowPrice = float(row["low"]),
            closePrice = float(row["close"]),
            volume = int(row.get("volume") or 0),
            openInterest = _optionalInt(row.get("oi")),
            source = SOURCE
        ))

    return candles


def toQuote(
    payload : Dict[str, Any], instrument : Instrument,
    timestamp : dt.datetime
) -> Quote:
    """
    Convert one entry of a ``quote`` response.

    :rtype:   Quote
    :returns: The normalized snapshot.
    """

    ohlc = payload.get("ohlc") or {}

    return Quote(
        instrumentKey = instrument.instrumentKey,
        lastPrice = float(payload.get("last_price") or 0.0),
        previousClose = _optionalFloat(ohlc.get("close")),
        change = _optionalFloat(payload.get("net_change")),
        dayOpen = _optionalFloat(ohlc.get("open")),
        dayHigh = _optionalFloat(ohlc.get("high")),
        dayLow = _optionalFloat(ohlc.get("low")),
        volume = _optionalInt(payload.get("volume")),
        openInterest = _optionalInt(payload.get("oi")),
        upperCircuit = _optionalFloat(payload.get("upper_circuit_limit")),
        lowerCircuit = _optionalFloat(payload.get("lower_circuit_limit")),
        depth = toDepth(payload.get("depth")),
        timestamp = timestamp,
        source = SOURCE,
        raw = dict(payload)
    )


def toDepth(book : Any) -> Optional[MarketDepth]:
    """
    Extract the five level order book from a quote payload.

    :rtype:   Optional[MarketDepth]
    :returns: The book, or ``None`` when the quote carried none.
    """

    if not isinstance(book, dict):
        return None

    def levels(side : str) -> List[DepthLevel]:
        return [
            DepthLevel(
                price = float(level.get("price", 0.0)),
                quantity = int(level.get("quantity", 0) or 0),
                orders = _optionalInt(level.get("orders"))
            )
            for level in book.get(side, []) or []
        ]

    return MarketDepth(buy = levels("buy"), sell = levels("sell"))


def toOrderPayload(request : OrderRequest) -> Dict[str, Any]:
    """
    Render a validated order request as Kite placement keyword
    arguments.

    :raises InvalidOrder: The order type has no Kite equivalent.

    :rtype:   Dict[str, Any]
    :returns: Keyword arguments for ``place_order``.
    """

    vendorType = ORDER_TYPE_MAP.get(request.orderType)

    if vendorType is None:
        raise InvalidOrder(
            f"kite does not support order type {request.orderType.value}"
        )

    key = request.instrument.key

    payload : Dict[str, Any] = {
        "variety" : "regular",
        "exchange" : key.exchange.value,
        "tradingsymbol" : request.instrument.brokerSymbol,
        "transaction_type" : ORDER_SIDE_MAP[request.side],
        "quantity" : request.quantity,
        "product" : PRODUCT_TYPE_MAP[request.productType],
        "order_type" : vendorType,
        "validity" : VALIDITY_MAP[request.validity]
    }

    if request.limitPrice is not None:
        payload["price"] = float(request.limitPrice)

    if request.triggerPrice is not None:
        payload["trigger_price"] = float(request.triggerPrice)

    if request.disclosedQuantity:
        payload["disclosed_quantity"] = int(request.disclosedQuantity)

    if request.tag:
        payload["tag"] = request.tag

    return payload


def toOrderResponse(
    payload : Dict[str, Any], orderId : Optional[str] = None
) -> OrderResponse:
    """
    Convert an order book row or a placement acknowledgement.

    :rtype:   OrderResponse
    :returns: The normalized order state. A rejected row is reported
        rather than raised, so a caller listing the order book sees the
        whole book.
    """

    status = ORDER_STATUS_MAP.get(
        str(payload.get("status") or "").upper(), OrderStatus.UNKNOWN
    )

    return OrderResponse(
        orderId = str(orderId or payload.get("order_id") or "unknown"),
        status = status,
        instrumentKey = payload.get("tradingsymbol"),
        side = (
            OrderSide(payload["transaction_type"])
            if payload.get("transaction_type") in ("BUY", "SELL") else None
        ),
        orderType = ORDER_TYPE_REVERSE.get(
            str(payload.get("order_type") or "")
        ),
        productType = PRODUCT_TYPE_REVERSE.get(
            str(payload.get("product") or "")
        ),
        quantity = _optionalInt(payload.get("quantity")),
        filledQuantity = _optionalInt(payload.get("filled_quantity")) or 0,
        pendingQuantity = _optionalInt(payload.get("pending_quantity")),
        averagePrice = _optionalFloat(payload.get("average_price")),
        limitPrice = _optionalFloat(payload.get("price")),
        triggerPrice = _optionalFloat(payload.get("trigger_price")),
        statusMessage = payload.get("status_message"),
        tag = payload.get("tag"),
        placedAt = _optionalDatetime(payload.get("order_timestamp")),
        updatedAt = _optionalDatetime(payload.get("exchange_update_timestamp")),
        source = SOURCE,
        raw = dict(payload)
    )


def toPosition(payload : Dict[str, Any], instrumentKey : str) -> Position:
    """
    Convert a Kite position row.

    ``quantity`` arrives already signed, so a short position is negative
    and matches the canonical convention without adjustment.

    :rtype:   Position
    :returns: The normalized position.
    """

    return Position(
        instrumentKey = instrumentKey,
        productType = PRODUCT_TYPE_REVERSE.get(
            str(payload.get("product") or ""), ProductType.INTRADAY
        ),
        quantity = _optionalInt(payload.get("quantity")) or 0,
        buyQuantity = _optionalInt(payload.get("buy_quantity")),
        sellQuantity = _optionalInt(payload.get("sell_quantity")),
        averagePrice = _optionalFloat(payload.get("average_price")) or 0.0,
        lastPrice = _optionalFloat(payload.get("last_price")),
        realisedProfit = _optionalFloat(payload.get("realised")),
        unrealisedProfit = _optionalFloat(payload.get("unrealised")),
        multiplier = _optionalFloat(payload.get("multiplier")) or 1.0,
        source = SOURCE,
        raw = dict(payload)
    )


def toHolding(payload : Dict[str, Any], instrumentKey : str) -> Holding:
    """
    Convert a Kite holding row.

    :rtype:   Holding
    :returns: The normalized holding.
    """

    quantity = _optionalInt(payload.get("quantity")) or 0
    pledged = _optionalInt(payload.get("collateral_quantity")) or 0

    return Holding(
        instrumentKey = instrumentKey,
        quantity = quantity + pledged,
        availableQuantity = quantity,
        pledgedQuantity = pledged or None,
        averagePrice = _optionalFloat(payload.get("average_price")) or 0.0,
        lastPrice = _optionalFloat(payload.get("last_price")),
        isin = payload.get("isin"),
        source = SOURCE,
        raw = dict(payload)
    )


def _optionalDatetime(value : Any) -> Optional[dt.datetime]:
    """
    Normalise a vendor timestamp to IST, tolerating absence.
    """

    if value is None:
        return None

    if isinstance(value, dt.datetime):
        return toIST(value)

    try:
        return toIST(dt.datetime.fromisoformat(str(value)))
    except ValueError:
        return None


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
    "SOURCE", "toBrokerTimeframe", "translateException", "toCandles",
    "toQuote", "toDepth", "toOrderPayload", "toOrderResponse",
    "toPosition", "toHolding",
    "ORDER_SIDE_MAP", "ORDER_TYPE_MAP", "PRODUCT_TYPE_MAP",
    "VALIDITY_MAP", "ORDER_STATUS_MAP"
]
