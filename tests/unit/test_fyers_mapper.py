# -*- encoding: utf-8 -*-

"""
Fyers Payload Translation and Failure Classification
-------------------------------------------------------

The most consequential test in this file is
:func:`test_swallowedDecodeFailureIsNotTreatedAsSuccess`. The Fyers SDK
does not raise: it returns a dictionary, and in the captured run 382,452
of those dictionaries were a swallowed JSON decode error against an
empty body returned by an edge that was throttling us. Code that only
checked ``s == "error"`` read every one of them as a success and passed
``None`` downstream, where it resurfaced much later as an unrelated
``TypeError``.
"""

import datetime as dt

import pytest

from algotraders.brokers.fyers import limits, mapper
from algotraders.core.clock import IST
from algotraders.errors import (
    InstrumentNotFound, InvalidOrder, OrderRejected, RateLimitExceeded,
    TransientBrokerError, UnsupportedTimeframe
)
from algotraders.models.enums import (
    OrderSide, OrderStatus, OrderType, ProductType, Timeframe
)
from algotraders.models.instrument import Instrument, InstrumentKey
from algotraders.models.orders import OrderRequest
from tests.fakes.session import loadPayload

EQUITY = Instrument(
    key = InstrumentKey.parse("NSE:SBIN:EQ"),
    brokerSymbol = "NSE:SBIN-EQ", brokerToken = "3045",
    lotSize = 1, source = "fyers"
)

NIFTY_FUTURE = Instrument(
    key = InstrumentKey.parse("NFO:NIFTY:FUT:2026-01-27"),
    brokerSymbol = "NSE:NIFTY26JANFUT", lotSize = 65, source = "fyers"
)


@pytest.mark.parametrize("timeframe, resolution", [
    (Timeframe.MINUTE_1, "1"),
    (Timeframe.MINUTE_3, "3"),
    (Timeframe.MINUTE_5, "5"),
    (Timeframe.MINUTE_15, "15"),
    (Timeframe.HOUR_1, "60"),
    (Timeframe.DAY_1, "D"),
])
def test_timeframeMapsToVendorResolution(
    timeframe : Timeframe, resolution : str
) -> None:
    """
    Canonical timeframes translate to the documented vendor codes.
    """

    assert mapper.toBrokerTimeframe(timeframe) == resolution


@pytest.mark.parametrize("timeframe", [Timeframe.MONTH_1, Timeframe.YEAR_1])
def test_unservedTimeframeRaisesWithAlternatives(
    timeframe : Timeframe
) -> None:
    """
    Monthly and yearly exist in the host database enum but Fyers serves
    neither, and the refusal lists what it does serve.
    """

    with pytest.raises(UnsupportedTimeframe) as failure:
        mapper.toBrokerTimeframe(timeframe)

    message = str(failure.value)

    assert "fyers" in message
    assert "1D" in message


def test_successfulResponsePassesThrough() -> None:
    """
    Only an explicit ``s == "ok"`` is accepted.
    """

    payload = loadPayload("fyers_history_ok")

    assert mapper.ensureSuccess(payload) is payload


def test_swallowedDecodeFailureIsNotTreatedAsSuccess() -> None:
    """
    The 382,452 occurrence case: the SDK returns its own decode error as
    a plain dict with no status field at all.

    A permissive check reads this as success and hands ``None`` to the
    caller, which is how a throttling incident turned into a confusing
    ``TypeError`` far from its cause.
    """

    payload = loadPayload("fyers_history_garbage")

    assert payload.get("s") is None

    with pytest.raises(TransientBrokerError) as failure:
        mapper.ensureSuccess(payload, context = "history")

    assert "malformed" in str(failure.value)


def test_throttlingMapsToRateLimitExceeded() -> None:
    """
    A 429 becomes the retryable type the backoff helper keys on.
    """

    with pytest.raises(RateLimitExceeded):
        mapper.ensureSuccess(loadPayload("fyers_history_429"))


def test_unknownSymbolMapsToInstrumentNotFound() -> None:
    """
    Code -300 is a resolution failure, not a transient one, so retrying
    it would waste the rate limit for no possible benefit.
    """

    with pytest.raises(InstrumentNotFound):
        mapper.ensureSuccess(loadPayload("fyers_history_bad_symbol"))


def test_nonDictResponseIsRejected() -> None:
    """
    A string or ``None`` from the SDK is a broker failure, not data.
    """

    for response in (None, "", [], "server error"):
        with pytest.raises(TransientBrokerError):
            mapper.ensureSuccess(response)


def test_candlesConvertWithISTTimestamps() -> None:
    """
    Positional vendor rows become fully identified candles.

    The epoch is converted with an explicit zone. Using
    ``datetime.fromtimestamp`` without one reads host local time, which
    is what produced timestamps hours out on non-IST machines.
    """

    payload = loadPayload("fyers_history_ok")
    candles = mapper.toCandles(payload, EQUITY, Timeframe.MINUTE_1)

    assert len(candles) == 5

    first = candles[0]

    assert first.instrumentKey == "NSE:SBIN:EQ"
    assert first.timeframe is Timeframe.MINUTE_1
    assert first.source == "fyers"
    assert first.openPrice == 812.5
    assert first.closePrice == 814.0
    assert first.volume == 125430

    assert first.timestamp.utcoffset() == dt.timedelta(hours = 5, minutes = 30)
    assert first.timestamp == dt.datetime(
        2026, 8, 5, 9, 15, tzinfo = IST
    )


def test_candlesAreOneMinuteApart() -> None:
    """
    Sanity check that the whole series decodes, not just the first bar.
    """

    candles = mapper.toCandles(
        loadPayload("fyers_history_ok"), EQUITY, Timeframe.MINUTE_1
    )

    for previous, following in zip(candles, candles[1:]):
        assert following.timestamp - previous.timestamp == dt.timedelta(
            minutes = 1
        )


def test_malformedCandleRowIsSkippedNotFatal() -> None:
    """
    One short row must not cost the whole response.
    """

    payload = {"s" : "ok", "candles" : [
        [1785901500, 812.5, 815.0, 811.25, 814.0, 125430],
        [1785901560, 814.0],
        [1785901620, 816.0, 816.5, 814.25, 814.75, 76540]
    ]}

    assert len(mapper.toCandles(payload, EQUITY, Timeframe.MINUTE_1)) == 2


def test_emptyCandleListIsValid() -> None:
    """
    A market holiday returns no bars, which is data rather than an
    error.
    """

    assert mapper.toCandles(
        {"s" : "ok", "candles" : []}, EQUITY, Timeframe.DAY_1
    ) == []


@pytest.mark.parametrize("orderType, vendorCode", [
    (OrderType.LIMIT, 1),
    (OrderType.MARKET, 2),
    (OrderType.STOP_LOSS_MARKET, 3),
    (OrderType.STOP_LOSS, 4),
])
def test_orderTypeMapsToVendorCode(
    orderType : OrderType, vendorCode : int
) -> None:
    """
    Fyers encodes order type numerically, and getting 3 and 4 the wrong
    way round swaps a stop loss for a stop loss market order.
    """

    assert mapper.ORDER_TYPE_MAP[orderType] == vendorCode


def test_orderPayloadCarriesEveryRequiredField() -> None:
    """
    A validated request renders to the vendor placement payload.
    """

    request = OrderRequest(
        instrument = NIFTY_FUTURE, side = OrderSide.BUY, quantity = 130,
        orderType = OrderType.LIMIT, limitPrice = 24_800.0,
        productType = ProductType.INTRADAY, tag = "orb-entry"
    )

    payload = mapper.toOrderPayload(request)

    assert payload["symbol"] == "NSE:NIFTY26JANFUT"
    assert payload["qty"] == 130
    assert payload["side"] == 1
    assert payload["type"] == 1
    assert payload["limitPrice"] == 24_800.0
    assert payload["productType"] == "INTRADAY"
    assert payload["orderTag"] == "orb-entry"


def test_sellSideIsEncodedAsNegativeOne() -> None:
    """
    Direction is a signed integer for Fyers, not a string.
    """

    request = OrderRequest(
        instrument = EQUITY, side = OrderSide.SELL, quantity = 10
    )

    assert mapper.toOrderPayload(request)["side"] == -1


def test_orderResponseMapsVendorStatus() -> None:
    """
    Vendor status 6 is a working order.
    """

    response = mapper.toOrderResponse({
        "id" : "25080500012345", "status" : 6, "qty" : 130,
        "filledQty" : 0, "side" : 1, "type" : 1,
        "productType" : "INTRADAY", "limitPrice" : 24800.0
    })

    assert response.orderId == "25080500012345"
    assert response.status is OrderStatus.OPEN
    assert response.isOpen is True
    assert response.side is OrderSide.BUY


def test_rejectedOrderRaisesWithBrokerMessage() -> None:
    """
    A rejection is a decision, surfaced with the broker's own wording
    because that is the only actionable detail the user receives.
    """

    with pytest.raises(OrderRejected) as failure:
        mapper.toOrderResponse({
            "id" : "1", "status" : 5, "message" : "RED: Insufficient funds"
        })

    assert failure.value.brokerMessage == "RED: Insufficient funds"


def test_unknownVendorStatusIsNotGuessed() -> None:
    """
    An unmapped state becomes ``UNKNOWN`` rather than being assumed
    complete or dead, since guessing between live and finished is how
    duplicate positions happen.
    """

    response = mapper.toOrderResponse({"id" : "1", "status" : 99})

    assert response.status is OrderStatus.UNKNOWN
    assert response.isTerminal is False


def test_positionKeepsShortQuantityNegative() -> None:
    """
    Fyers reports ``netQty`` already signed, matching the canonical
    convention without adjustment.
    """

    position = mapper.toPosition({
        "netQty" : -65, "netAvg" : 24_750.0, "ltp" : 24_700.0,
        "productType" : "INTRADAY", "realized_profit" : 0.0,
        "unrealized_profit" : 3_250.0
    }, instrumentKey = "NFO:NIFTY:FUT:2026-01-27")

    assert position.quantity == -65
    assert position.isShort is True
    assert position.totalProfit == pytest.approx(3_250.0)


def test_holdingSeparatesSellableQuantity() -> None:
    """
    Pledged or unsettled stock counts toward the holding but cannot be
    sold, and an order for the difference is rejected by the exchange.
    """

    holding = mapper.toHolding({
        "quantity" : 100, "remainingQuantity" : 60,
        "costPrice" : 800.0, "ltp" : 814.0, "isin" : "INE062A01020"
    }, instrumentKey = "NSE:SBIN:EQ")

    assert holding.quantity == 100
    assert holding.availableQuantity == 60
    assert holding.investedValue == pytest.approx(80_000.0)
    assert holding.unrealisedProfit == pytest.approx(1_400.0)


def test_unsupportedOrderTypeRaisesInvalidOrder() -> None:
    """
    An order type with no vendor equivalent fails before dispatch.
    """

    request = OrderRequest(
        instrument = EQUITY, side = OrderSide.BUY, quantity = 1
    )

    original = dict(mapper.ORDER_TYPE_MAP)

    try:
        mapper.ORDER_TYPE_MAP.pop(OrderType.MARKET)

        with pytest.raises(InvalidOrder):
            mapper.toOrderPayload(request)
    finally:
        mapper.ORDER_TYPE_MAP.clear()
        mapper.ORDER_TYPE_MAP.update(original)


def test_chunkSpansMatchThePublishedCaps() -> None:
    """
    100 days for every intraday resolution, 366 for daily.
    """

    assert limits.CHUNK_SPAN[Timeframe.MINUTE_1] == dt.timedelta(days = 100)
    assert limits.CHUNK_SPAN[Timeframe.HOUR_1] == dt.timedelta(days = 100)
    assert limits.CHUNK_SPAN[Timeframe.DAY_1] == dt.timedelta(days = 366)
