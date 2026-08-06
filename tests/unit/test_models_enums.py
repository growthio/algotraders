# -*- encoding: utf-8 -*-

"""
Canonical Enumeration Contracts
---------------------------------

The ``Timeframe`` assertions here are effectively a schema test. Those
values are written into a PostgreSQL column typed as
``private.timeframe_value``, so changing one is a database migration
rather than a rename, and the test is what makes that consequence
visible at the moment somebody tries.
"""

from enum import Enum

import pytest

from algotraders.models import enums
from algotraders.models.enums import (
    AGGREGATE_ONLY_TIMEFRAMES, Capability, EndpointClass, Exchange,
    InstrumentType, OptionRight, OrderSide, OrderStatus, OrderType,
    ProductType, Segment, StreamMode, TIMEFRAME_DATABASE_VALUES, Timeframe,
    Validity
)


def test_timeframeMatchesTheDatabaseEnumExactly() -> None:
    """
    The canonical values equal the host's ``private.timeframe_value``
    type, in order.

    A mismatch means a candle insert fails at runtime with an opaque
    PostgreSQL enum error, so it is pinned here instead.
    """

    assert tuple(member.value for member in Timeframe) == (
        "1m", "3m", "5m", "15m", "1h", "1D", "1M", "1Y"
    )

    assert TIMEFRAME_DATABASE_VALUES == tuple(
        member.value for member in Timeframe
    )


def test_aggregateOnlyTimeframesAreTheUnservedOnes() -> None:
    """
    Monthly and yearly bars exist in the database enum but no supported
    broker serves them.
    """

    assert AGGREGATE_ONLY_TIMEFRAMES == {Timeframe.MONTH_1, Timeframe.YEAR_1}

    for timeframe in (Timeframe.MINUTE_1, Timeframe.DAY_1, Timeframe.HOUR_1):
        assert timeframe not in AGGREGATE_ONLY_TIMEFRAMES


@pytest.mark.parametrize("enumeration", [
    Timeframe, Exchange, Segment, InstrumentType, OptionRight, OrderSide,
    OrderType, ProductType, Validity, OrderStatus, StreamMode,
    EndpointClass, Capability
])
def test_everyEnumIsStringBacked(enumeration : type) -> None:
    """
    Members are ``str`` subclasses, which is what makes them JSON
    serialisable and usable as FastAPI response fields without a
    converter.
    """

    assert issubclass(enumeration, str)
    assert issubclass(enumeration, Enum)

    for member in enumeration:
        assert isinstance(member.value, str)
        assert member == member.value


@pytest.mark.parametrize("enumeration", [
    Timeframe, Exchange, Segment, InstrumentType, OptionRight, OrderSide,
    OrderType, ProductType, Validity, OrderStatus, StreamMode,
    EndpointClass, Capability
])
def test_enumValuesAreUnique(enumeration : type) -> None:
    """
    Duplicate values would silently alias two members onto one, which
    for something like ``OrderType`` would be a trading defect.
    """

    values = [member.value for member in enumeration]

    assert len(values) == len(set(values))


def test_orderTypesCoverTheStopLossDistinction() -> None:
    """
    Stop loss and stop loss market are separate members.

    Collapsing them is a classic and expensive mistake: one carries a
    limit price and one does not, and confusing them turns a protective
    exit into an order that may never fill.
    """

    assert OrderType.STOP_LOSS.value == "SL"
    assert OrderType.STOP_LOSS_MARKET.value == "SL-M"
    assert OrderType.STOP_LOSS is not OrderType.STOP_LOSS_MARKET


def test_orderStatusCarriesAnUnknownMember() -> None:
    """
    Vendors add lifecycle states without notice, and guessing whether an
    unmapped one means live or dead is how duplicate positions happen.
    """

    assert OrderStatus.UNKNOWN in OrderStatus


def test_derivativeExchangesAreSeparateVenues() -> None:
    """
    NFO and BFO are distinct exchanges to both broker APIs, not flags on
    NSE and BSE.
    """

    assert Exchange.NFO in enums.DERIVATIVE_EXCHANGES
    assert Exchange.BFO in enums.DERIVATIVE_EXCHANGES
    assert Exchange.NSE not in enums.DERIVATIVE_EXCHANGES


def test_everyExportedNameResolves() -> None:
    """
    ``__all__`` and the module contents cannot drift apart.
    """

    for name in enums.__all__:
        assert hasattr(enums, name), f"{name} is exported but missing"
