# -*- encoding: utf-8 -*-

"""
Position, Holding and Margin Records
--------------------------------------

The distinction between a position and a holding is not cosmetic and
both brokers enforce it. A :class:`Position` is intraday or derivative
exposure that lives in the trading book and is squared off or carried
under margin. A :class:`Holding` is settled stock sitting in the demat
account. Selling one is a different instruction from selling the other,
and conflating them produces a short sale where a delivery sale was
intended.

Sign convention is uniform here: a short position carries a negative
:attr:`Position.quantity`. Vendors disagree on this, some reporting a
positive quantity alongside a direction flag, so the mappers normalise
it and every consumer above can rely on the sign alone.
"""

from typing import Any, Dict, Optional

from pydantic import Field

from algotraders.models.base import AlgoTradersModel
from algotraders.models.enums import ProductType


class Position(AlgoTradersModel):
    """
    Open exposure in the trading book.

    :type  quantity: int
    :param quantity: Net quantity. **Negative when short.** This is the
        normalised convention; broker payloads that use a separate
        direction flag are converted by the mapper.

    :type  averagePrice: float
    :param averagePrice: Weighted average entry price for the net
        position.

    :type  lastPrice: Optional[float]
    :param lastPrice: Latest traded price, when the broker includes it
        in the position payload.

    :type  realisedProfit: Optional[float]
    :param realisedProfit: Booked profit or loss from quantity already
        closed today.

    :type  unrealisedProfit: Optional[float]
    :param unrealisedProfit: Mark to market on the quantity still open.
    """

    instrumentKey : str = Field(min_length = 1)
    productType : ProductType = ProductType.INTRADAY
    quantity : int
    buyQuantity : Optional[int] = Field(default = None, ge = 0)
    sellQuantity : Optional[int] = Field(default = None, ge = 0)
    averagePrice : float
    lastPrice : Optional[float] = None
    realisedProfit : Optional[float] = None
    unrealisedProfit : Optional[float] = None
    multiplier : float = 1.0
    source : str = Field(min_length = 1)
    raw : Dict[str, Any] = Field(default_factory = dict, exclude = True)


    @property
    def isShort(self) -> bool:
        """
        Whether this is a short position.
        """

        return self.quantity < 0


    @property
    def isFlat(self) -> bool:
        """
        Whether the position is fully closed.

        Both brokers keep zero quantity rows in the response for the
        rest of the session, so a caller listing "open positions" has to
        filter on this rather than trusting the row count.
        """

        return self.quantity == 0


    @property
    def exposure(self) -> Optional[float]:
        """
        Absolute market value of the open quantity.

        :rtype:   Optional[float]
        :returns: Value at the last traded price, or ``None`` when the
            broker did not supply one.
        """

        if self.lastPrice is None:
            return None

        return abs(self.quantity) * self.lastPrice * self.multiplier


    @property
    def totalProfit(self) -> Optional[float]:
        """
        Combined realised and unrealised profit, when either is known.
        """

        if self.realisedProfit is None and self.unrealisedProfit is None:
            return None

        return (self.realisedProfit or 0.0) + (self.unrealisedProfit or 0.0)


class Holding(AlgoTradersModel):
    """
    Settled stock held in the demat account.

    :type  quantity: int
    :param quantity: Total holding.

    :type  availableQuantity: Optional[int]
    :param availableQuantity: Portion free to sell. Shares pledged for
        margin, or bought today and not yet settled, are counted in
        :attr:`quantity` but are not sellable, and a sell order for the
        difference is rejected by the exchange.

    :type  averagePrice: float
    :param averagePrice: Weighted average acquisition price.
    """

    instrumentKey : str = Field(min_length = 1)
    quantity : int = Field(ge = 0)
    availableQuantity : Optional[int] = Field(default = None, ge = 0)
    pledgedQuantity : Optional[int] = Field(default = None, ge = 0)
    averagePrice : float = Field(ge = 0)
    lastPrice : Optional[float] = None
    isin : Optional[str] = None
    source : str = Field(min_length = 1)
    raw : Dict[str, Any] = Field(default_factory = dict, exclude = True)


    @property
    def investedValue(self) -> float:
        """
        Cost basis of the holding.
        """

        return self.quantity * self.averagePrice


    @property
    def currentValue(self) -> Optional[float]:
        """
        Market value at the last traded price, when known.
        """

        if self.lastPrice is None:
            return None

        return self.quantity * self.lastPrice


    @property
    def unrealisedProfit(self) -> Optional[float]:
        """
        Paper profit or loss against the cost basis.
        """

        current = self.currentValue

        return None if current is None else current - self.investedValue


class Margin(AlgoTradersModel):
    """
    Funds and margin availability for a trading segment.

    :type  segment: str
    :param segment: Broker's own segment label, kept as free text
        because the two vendors partition funds differently and
        flattening them into one enum would lose information.

    :type  availableCash: float
    :param availableCash: Cash free to deploy.

    :type  utilisedMargin: Optional[float]
    :param utilisedMargin: Margin currently blocked against open
        positions.
    """

    segment : str = Field(min_length = 1)
    availableCash : float
    availableMargin : Optional[float] = None
    utilisedMargin : Optional[float] = None
    openingBalance : Optional[float] = None
    payin : Optional[float] = None
    payout : Optional[float] = None
    source : str = Field(min_length = 1)
    raw : Dict[str, Any] = Field(default_factory = dict, exclude = True)


__all__ = ["Position", "Holding", "Margin"]
