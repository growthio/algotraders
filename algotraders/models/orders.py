# -*- encoding: utf-8 -*-

"""
Order Request and Response Models With Local Safety Checks
------------------------------------------------------------

:class:`OrderRequest` validates before anything reaches a broker. That
placement is the entire point: an order rejected locally costs a
millisecond, while an order accepted by the exchange and then found to
be wrong costs money and cannot be undone.

The checks encode rules that are easy to state and easy to get wrong
under pressure. A limit order without a limit price is meaningless. A
stop loss needs both a trigger and a limit, while a stop loss market
order must not carry a limit at all. Disclosed quantity above total
quantity is nonsense. And derivative quantity has to be a whole multiple
of the exchange lot size, which is the single most common way to fat
finger a NIFTY order, because the lot size changes and a stale mental
model produces an order fifteen times larger than intended.

None of this replaces the risk limits a trading system needs. Position
sizing, daily loss caps and exposure ceilings depend on portfolio state
that this library deliberately does not hold; they belong to the host.
What lives here is the subset that can be decided from the order alone.
"""

import datetime as dt

from typing import Any, Dict, Optional

from pydantic import Field, model_validator

from algotraders.models.base import AlgoTradersModel
from algotraders.models.enums import (
    OrderSide, OrderStatus, OrderType, ProductType, Validity
)
from algotraders.models.instrument import Instrument
from algotraders.models.market import requireAwareIST

from pydantic import field_validator

# ? order types that carry a limit price, and those that carry a trigger
LIMIT_PRICED_TYPES = frozenset({OrderType.LIMIT, OrderType.STOP_LOSS})
TRIGGERED_TYPES = frozenset({OrderType.STOP_LOSS, OrderType.STOP_LOSS_MARKET})


class OrderRequest(AlgoTradersModel):
    """
    An order to be placed, validated locally before dispatch.

    :type  instrument: Instrument
    :param instrument: The resolved contract to trade. A full instrument
        rather than a symbol string, so the broker adapter has the
        vendor encoding it needs and the validator has the lot size.

    :type  quantity: int
    :param quantity: Number of units. For derivatives this is the total
        quantity, not the number of lots, matching what both broker APIs
        expect.

    :type  limitPrice: Optional[float]
    :param limitPrice: Required for ``LIMIT`` and ``SL``, forbidden
        otherwise.

    :type  triggerPrice: Optional[float]
    :param triggerPrice: Required for ``SL`` and ``SL-M``, forbidden
        otherwise.

    :type  disclosedQuantity: Optional[int]
    :param disclosedQuantity: Portion of the order visible in the order
        book. Must not exceed :attr:`quantity`.

    :type  tag: Optional[str]
    :param tag: Free text label echoed back by the broker on the order
        and its updates. Both vendors cap this at 20 characters. Worth
        setting on every order: it is the only reliable way to reconcile
        a placement whose response was lost in flight, which matters
        because order calls are never automatically retried.

    :type  clientOrderId: Optional[str]
    :param clientOrderId: Caller supplied idempotency handle, recorded
        by the host before dispatch so an interrupted placement can be
        reconciled afterwards.
    """

    instrument : Instrument
    side : OrderSide
    quantity : int = Field(gt = 0)
    orderType : OrderType = OrderType.MARKET
    productType : ProductType = ProductType.INTRADAY
    validity : Validity = Validity.DAY
    limitPrice : Optional[float] = Field(default = None, gt = 0)
    triggerPrice : Optional[float] = Field(default = None, gt = 0)
    disclosedQuantity : Optional[int] = Field(default = None, ge = 0)
    tag : Optional[str] = Field(default = None, max_length = 20)
    clientOrderId : Optional[str] = None


    @model_validator(mode = "after")
    def validatePricing(self) -> "OrderRequest":
        """
        Enforce the price fields that each order type requires.

        :raises ValueError: The price combination cannot be executed as
            described.
        """

        needsLimit = self.orderType in LIMIT_PRICED_TYPES
        needsTrigger = self.orderType in TRIGGERED_TYPES

        if needsLimit and self.limitPrice is None:
            raise ValueError(
                f"{self.orderType.value} orders require a limitPrice"
            )

        if not needsLimit and self.limitPrice is not None:
            raise ValueError(
                f"{self.orderType.value} orders must not carry a limitPrice; "
                "a market order fills at the prevailing price"
            )

        if needsTrigger and self.triggerPrice is None:
            raise ValueError(
                f"{self.orderType.value} orders require a triggerPrice"
            )

        if not needsTrigger and self.triggerPrice is not None:
            raise ValueError(
                f"{self.orderType.value} orders must not carry a triggerPrice"
            )

        return self


    @model_validator(mode = "after")
    def validateQuantity(self) -> "OrderRequest":
        """
        Check disclosed quantity and lot size multiples.

        The lot size rule only applies when the resolved instrument
        knows its lot size. An unknown lot size is not treated as one:
        silently accepting the order is better than inventing a
        constraint, and the broker will reject it anyway.

        :raises ValueError: Quantity is not tradable as described.
        """

        if (
            self.disclosedQuantity is not None
            and self.disclosedQuantity > self.quantity
        ):
            raise ValueError(
                f"disclosedQuantity {self.disclosedQuantity} exceeds "
                f"quantity {self.quantity}"
            )

        lotSize = self.instrument.lotSize

        if lotSize and self.quantity % lotSize:
            lots = self.quantity / lotSize

            raise ValueError(
                f"quantity {self.quantity} is not a multiple of the "
                f"{self.instrument.key.tradingSymbol} lot size {lotSize} "
                f"({lots:.2f} lots); use {round(lots) * lotSize} instead"
            )

        return self


    @property
    def lots(self) -> Optional[int]:
        """
        Order size expressed in lots, when the lot size is known.
        """

        lotSize = self.instrument.lotSize

        return self.quantity // lotSize if lotSize else None


    @property
    def notionalValue(self) -> Optional[float]:
        """
        Approximate order value, based on the limit price.

        ``None`` for a market order, since there is no price to compute
        against until it fills.
        """

        if self.limitPrice is None:
            return None

        return self.limitPrice * self.quantity


class OrderResponse(AlgoTradersModel):
    """
    Broker acknowledgement or current state of an order.

    Returned by placement, modification, cancellation and the order book
    query alike, so a caller polling for a fill compares against the
    same shape it got at dispatch.

    :type  orderId: str
    :param orderId: Broker assigned identifier, the handle for every
        later modify or cancel.

    :type  status: OrderStatus
    :param status: Lifecycle state. An unrecognised vendor state maps to
        ``UNKNOWN`` rather than being guessed at, because guessing
        between "live" and "dead" is how duplicate positions happen.

    :type  raw: Dict[str, Any]
    :param raw: Untouched vendor payload, excluded from serialisation
        but retained in process for audit logging.
    """

    orderId : str = Field(min_length = 1)
    status : OrderStatus = OrderStatus.UNKNOWN
    instrumentKey : Optional[str] = None
    side : Optional[OrderSide] = None
    orderType : Optional[OrderType] = None
    productType : Optional[ProductType] = None
    quantity : Optional[int] = Field(default = None, ge = 0)
    filledQuantity : int = Field(default = 0, ge = 0)
    pendingQuantity : Optional[int] = Field(default = None, ge = 0)
    averagePrice : Optional[float] = None
    limitPrice : Optional[float] = None
    triggerPrice : Optional[float] = None
    statusMessage : Optional[str] = None
    tag : Optional[str] = None
    placedAt : Optional[dt.datetime] = None
    updatedAt : Optional[dt.datetime] = None
    source : str = Field(min_length = 1)
    raw : Dict[str, Any] = Field(default_factory = dict, exclude = True)

    _normalisePlacedAt = field_validator("placedAt")(requireAwareIST)
    _normaliseUpdatedAt = field_validator("updatedAt")(requireAwareIST)


    @property
    def isTerminal(self) -> bool:
        """
        Whether the order has reached a state it cannot leave.

        ``UNKNOWN`` is deliberately not terminal: an unmapped vendor
        state must keep being polled rather than being written off.
        """

        return self.status in (
            OrderStatus.COMPLETE, OrderStatus.CANCELLED,
            OrderStatus.REJECTED, OrderStatus.EXPIRED
        )


    @property
    def isOpen(self) -> bool:
        """
        Whether the order is still working at the exchange.
        """

        return self.status in (
            OrderStatus.PENDING, OrderStatus.OPEN,
            OrderStatus.PARTIALLY_FILLED
        )


class Trade(AlgoTradersModel):
    """
    An individual fill against an order.

    A single order can fill in many trades at different prices, which is
    why the average price on :class:`OrderResponse` is not enough for
    accurate cost accounting.
    """

    tradeId : str = Field(min_length = 1)
    orderId : str = Field(min_length = 1)
    instrumentKey : str = Field(min_length = 1)
    side : OrderSide
    quantity : int = Field(gt = 0)
    price : float = Field(gt = 0)
    tradedAt : Optional[dt.datetime] = None
    source : str = Field(min_length = 1)
    raw : Dict[str, Any] = Field(default_factory = dict, exclude = True)

    _normaliseTradedAt = field_validator("tradedAt")(requireAwareIST)


    @property
    def value(self) -> float:
        """
        Cash value of this fill, before charges.
        """

        return self.price * self.quantity


__all__ = [
    "OrderRequest", "OrderResponse", "Trade",
    "LIMIT_PRICED_TYPES", "TRIGGERED_TYPES"
]
