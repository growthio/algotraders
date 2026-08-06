# -*- encoding: utf-8 -*-

"""
Local Order Validation Before Anything Reaches a Broker
---------------------------------------------------------

Every case below is a rejection that costs a millisecond locally and
would cost money if it reached the exchange. The lot size table in
particular guards the most common fat finger in Indian derivatives: NIFTY
trades in lots of 65 as of the 2026 contract specification, so an order
typed as "100" is not 100 units of exposure, it is an invalid order that
a careless adapter might round into something far larger than intended.
"""

import datetime as dt

import pytest
from pydantic import ValidationError

from algotraders.core.clock import IST
from algotraders.models.enums import (
    OrderSide, OrderStatus, OrderType, ProductType
)
from algotraders.models.instrument import Instrument, InstrumentKey
from algotraders.models.orders import OrderRequest, OrderResponse, Trade

MOMENT = dt.datetime(2026, 8, 5, 9, 20, tzinfo = IST)

EQUITY = Instrument(
    key = InstrumentKey.parse("NSE:SBIN:EQ"),
    brokerSymbol = "NSE:SBIN-EQ", lotSize = 1, source = "fyers"
)

NIFTY_FUTURE = Instrument(
    key = InstrumentKey.parse("NFO:NIFTY:FUT:2026-01-27"),
    brokerSymbol = "NSE:NIFTY26JANFUT", lotSize = 65, source = "fyers"
)

UNKNOWN_LOT = Instrument(
    key = InstrumentKey.parse("NSE:SBIN:EQ"),
    brokerSymbol = "NSE:SBIN-EQ", source = "fyers"
)


def buildOrder(**overrides : object) -> OrderRequest:
    """
    Construct a valid market order, overriding fields per test.
    """

    fields = dict(
        instrument = EQUITY, side = OrderSide.BUY, quantity = 10,
        orderType = OrderType.MARKET
    )
    fields.update(overrides)

    return OrderRequest(**fields)


def test_marketOrderNeedsNoPrices() -> None:
    """
    The simplest valid order carries neither limit nor trigger.
    """

    order = buildOrder()

    assert order.orderType is OrderType.MARKET
    assert order.productType is ProductType.INTRADAY
    assert order.notionalValue is None


@pytest.mark.parametrize("orderType, limitPrice, triggerPrice, valid", [
    (OrderType.MARKET,           None,  None, True),
    (OrderType.MARKET,           100.0, None, False),  # market with a limit
    (OrderType.MARKET,           None,  99.0, False),  # market with a trigger
    (OrderType.LIMIT,            100.0, None, True),
    (OrderType.LIMIT,            None,  None, False),  # limit with no price
    (OrderType.LIMIT,            100.0, 99.0, False),  # limit with a trigger
    (OrderType.STOP_LOSS,        100.0, 99.0, True),
    (OrderType.STOP_LOSS,        None,  99.0, False),  # SL with no limit
    (OrderType.STOP_LOSS,        100.0, None, False),  # SL with no trigger
    (OrderType.STOP_LOSS_MARKET, None,  99.0, True),
    (OrderType.STOP_LOSS_MARKET, 100.0, 99.0, False),  # SL-M with a limit
    (OrderType.STOP_LOSS_MARKET, None,  None, False),  # SL-M with no trigger
])
def test_priceCombinationsPerOrderType(
    orderType : OrderType, limitPrice : float, triggerPrice : float,
    valid : bool
) -> None:
    """
    Each order type requires exactly the prices it can act on.

    The stop loss rows are the ones that matter: a protective exit
    submitted with the wrong price fields either never triggers or
    executes at a price the trader did not choose.
    """

    def build() -> OrderRequest:
        return buildOrder(
            orderType = orderType, limitPrice = limitPrice,
            triggerPrice = triggerPrice
        )

    if valid:
        assert build().orderType is orderType
    else:
        with pytest.raises(ValidationError):
            build()


def test_quantityMustBePositive() -> None:
    """
    A zero or negative quantity is never a valid instruction.
    """

    for quantity in (0, -5):
        with pytest.raises(ValidationError):
            buildOrder(quantity = quantity)


def test_derivativeQuantityMustBeALotMultiple() -> None:
    """
    NIFTY trades in lots of 65, so 100 units is not a tradable size.
    """

    with pytest.raises(ValidationError) as failure:
        buildOrder(instrument = NIFTY_FUTURE, quantity = 100)

    message = str(failure.value)

    assert "lot size 65" in message

    # ? the message suggests the nearest valid size, because the whole
    # point is to stop somebody re-typing a second wrong number
    assert "130" in message


def test_validLotMultipleIsAccepted() -> None:
    """
    Exact multiples pass, and lot count is exposed for order sizing.
    """

    order = buildOrder(instrument = NIFTY_FUTURE, quantity = 195)

    assert order.lots == 3


def test_unknownLotSizeDoesNotInventAConstraint() -> None:
    """
    An unresolved lot size is not treated as one.

    Silently accepting is better than fabricating a rule; the broker
    rejects a genuinely bad size anyway.
    """

    order = buildOrder(instrument = UNKNOWN_LOT, quantity = 7)

    assert order.quantity == 7
    assert order.lots is None


def test_disclosedQuantityCannotExceedTotal() -> None:
    """
    Showing more than the order contains is nonsense.
    """

    with pytest.raises(ValidationError):
        buildOrder(quantity = 10, disclosedQuantity = 50)

    assert buildOrder(quantity = 10, disclosedQuantity = 5).disclosedQuantity == 5


def test_tagIsCappedAtVendorLimit() -> None:
    """
    Both vendors cap the order tag at 20 characters, and an over-long
    tag is silently truncated by the broker, which breaks the
    reconciliation it exists to support.
    """

    assert buildOrder(tag = "a" * 20).tag is not None

    with pytest.raises(ValidationError):
        buildOrder(tag = "a" * 21)


def test_notionalValueUsesTheLimitPrice() -> None:
    """
    Order value is computable only when a price is known.
    """

    order = buildOrder(orderType = OrderType.LIMIT, limitPrice = 250.0)

    assert order.notionalValue == pytest.approx(2500.0)


def test_negativePricesAreRejected() -> None:
    """
    A non positive price cannot be sent to an exchange.
    """

    for price in (0, -1.0):
        with pytest.raises(ValidationError):
            buildOrder(orderType = OrderType.LIMIT, limitPrice = price)


def test_responseTerminalAndOpenStatesArePartitioned() -> None:
    """
    Open and terminal are mutually exclusive, and ``UNKNOWN`` is neither.

    Treating an unmapped vendor state as terminal would stop a caller
    polling an order that is still live at the exchange.
    """

    def response(status : OrderStatus) -> OrderResponse:
        return OrderResponse(
            orderId = "1", status = status, source = "fyers"
        )

    for status in (
        OrderStatus.COMPLETE, OrderStatus.CANCELLED,
        OrderStatus.REJECTED, OrderStatus.EXPIRED
    ):
        assert response(status).isTerminal is True
        assert response(status).isOpen is False

    for status in (
        OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED
    ):
        assert response(status).isOpen is True
        assert response(status).isTerminal is False

    unknown = response(OrderStatus.UNKNOWN)

    assert unknown.isTerminal is False
    assert unknown.isOpen is False


def test_responseDefaultsToUnknownStatus() -> None:
    """
    An unmapped response never claims to know the order's fate.
    """

    assert OrderResponse(
        orderId = "1", source = "kite"
    ).status is OrderStatus.UNKNOWN


def test_responseKeepsRawPayloadOutOfSerialisation() -> None:
    """
    The vendor blob is retained for audit but excluded from any dump.
    """

    response = OrderResponse(
        orderId = "1", source = "fyers", raw = {"vendorNote": "secret sauce"}
    )

    assert response.raw["vendorNote"] == "secret sauce"
    assert "secret sauce" not in response.model_dump_json()


def test_tradeComputesFillValue() -> None:
    """
    A single order can fill in many trades at different prices, so the
    per-fill value is what cost accounting needs.
    """

    trade = Trade(
        tradeId = "T1", orderId = "O1", instrumentKey = "NSE:SBIN:EQ",
        side = OrderSide.BUY, quantity = 10, price = 250.5,
        tradedAt = MOMENT, source = "fyers"
    )

    assert trade.value == pytest.approx(2505.0)


def test_orderRequestIsFrozen() -> None:
    """
    A validated order cannot be mutated after the checks have run,
    which would otherwise let a caller bypass every rule above.
    """

    order = buildOrder()

    with pytest.raises(ValidationError):
        order.quantity = 100000
