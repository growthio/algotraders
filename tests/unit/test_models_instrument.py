# -*- encoding: utf-8 -*-

"""
Instrument Identity, Canonical Key Grammar and Round Tripping
---------------------------------------------------------------

The canonical key is used as a cache key, a database column and the join
between price history sourced from different brokers, so the round trip
property matters more than any single formatting choice: whatever
``__str__`` produces, ``parse`` must accept and reconstruct exactly.
"""

import decimal
import datetime as dt

import pytest
from pydantic import ValidationError

from algotraders.models.enums import (
    Exchange, InstrumentType, OptionRight, Segment
)
from algotraders.models.instrument import Instrument, InstrumentKey

EQUITY = "NSE:SBIN:EQ"
INDEX = "NSE:NIFTY 50:IDX"
FUTURE = "NFO:NIFTY:FUT:2026-01-27"
OPTION = "NFO:NIFTY:OPT:2026-01-27:24800:CE"


@pytest.mark.parametrize("canonical", [EQUITY, INDEX, FUTURE, OPTION])
def test_keyRoundTripsThroughItsStringForm(canonical : str) -> None:
    """
    Parsing a rendered key reproduces the original object.

    This is the property everything else depends on; without it a cached
    instrument could never be looked up again.
    """

    key = InstrumentKey.parse(canonical)

    assert str(key) == canonical
    assert InstrumentKey.parse(str(key)) == key


def test_parsesOptionComponents() -> None:
    """
    Every component of an option key survives parsing with the right
    type.
    """

    key = InstrumentKey.parse(OPTION)

    assert key.exchange is Exchange.NFO
    assert key.tradingSymbol == "NIFTY"
    assert key.instrumentType is InstrumentType.OPTION
    assert key.expiry == dt.date(2026, 1, 27)
    assert key.strike == decimal.Decimal("24800")
    assert key.optionRight is OptionRight.CALL


def test_parseAcceptsAnExistingKey() -> None:
    """
    Callers taking ``Union[str, InstrumentKey]`` normalise with one call.
    """

    key = InstrumentKey.parse(OPTION)

    assert InstrumentKey.parse(key) is key


def test_strikeUsesDecimalNotFloat() -> None:
    """
    A float strike would not reliably round trip through a cache key.
    """

    key = InstrumentKey.parse("NFO:NIFTY:OPT:2026-01-27:24800.50:PE")

    assert isinstance(key.strike, decimal.Decimal)
    assert key.strike == decimal.Decimal("24800.50")
    assert str(key).endswith("24800.5:PE")


def test_equivalentStrikeSpellingsProduceOneKey() -> None:
    """
    ``24800`` and ``Decimal('24800.00')`` are the same strike and must
    not produce two distinct cache entries.
    """

    first = InstrumentKey(
        exchange = Exchange.NFO, tradingSymbol = "NIFTY",
        instrumentType = InstrumentType.OPTION,
        expiry = dt.date(2026, 1, 27), strike = decimal.Decimal("24800.00"),
        optionRight = OptionRight.CALL
    )

    assert str(first) == OPTION


def test_symbolIsUppercasedOnConstruction() -> None:
    """
    Case is normalised so ``sbin`` and ``SBIN`` resolve identically.
    """

    assert InstrumentKey.parse("NSE:sbin:EQ").tradingSymbol == "SBIN"


def test_symbolRejectsTheKeySeparator() -> None:
    """
    A colon inside the symbol would produce a key that cannot be parsed
    back, so it is refused at construction.
    """

    with pytest.raises(ValidationError):
        InstrumentKey(
            exchange = Exchange.NSE, tradingSymbol = "BAD:SYMBOL",
            instrumentType = InstrumentType.EQUITY
        )


@pytest.mark.parametrize("canonical", [
    "NSE",                                   # too few components
    "NSE:SBIN",                              # missing type
    "NFO:NIFTY:OPT:2026-01-27:24800",        # strike without a right
    "NFO:NIFTY:OPT:2026-01-27:24800:CE:XX",  # trailing junk
])
def test_malformedKeysAreRejected(canonical : str) -> None:
    """
    A key that does not match the grammar fails loudly rather than
    parsing into something subtly wrong.
    """

    with pytest.raises((ValueError, ValidationError)):
        InstrumentKey.parse(canonical)


def test_optionWithoutStrikeIsRejected() -> None:
    """
    An option is not identified without its strike and right.
    """

    with pytest.raises(ValidationError):
        InstrumentKey(
            exchange = Exchange.NFO, tradingSymbol = "NIFTY",
            instrumentType = InstrumentType.OPTION,
            expiry = dt.date(2026, 1, 27)
        )


def test_equityWithExpiryIsRejected() -> None:
    """
    Equities do not expire. Accepting the field would let a malformed
    key resolve against the wrong instrument master.
    """

    with pytest.raises(ValidationError):
        InstrumentKey(
            exchange = Exchange.NSE, tradingSymbol = "SBIN",
            instrumentType = InstrumentType.EQUITY,
            expiry = dt.date(2026, 1, 27)
        )


def test_futureRejectsStrike() -> None:
    """
    A futures contract has no strike price.
    """

    with pytest.raises(ValidationError):
        InstrumentKey(
            exchange = Exchange.NFO, tradingSymbol = "NIFTY",
            instrumentType = InstrumentType.FUTURE,
            expiry = dt.date(2026, 1, 27), strike = decimal.Decimal("100")
        )


@pytest.mark.parametrize("canonical, segment", [
    (EQUITY, Segment.CAPITAL),
    (OPTION, Segment.DERIVATIVE),
    ("MCX:GOLD:FUT:2026-02-05", Segment.COMMODITY),
    ("CDS:USDINR:FUT:2026-02-05", Segment.CURRENCY),
])
def test_segmentIsDerivedFromExchange(
    canonical : str, segment : Segment
) -> None:
    """
    Segment selects which instrument master file to download, and it is
    always a function of the exchange rather than stored separately.
    """

    assert InstrumentKey.parse(canonical).segment is segment


@pytest.mark.parametrize("canonical, expected", [
    (EQUITY, False), (INDEX, False), (FUTURE, True), (OPTION, True)
])
def test_isDerivativeFlagsExpiringContracts(
    canonical : str, expected : bool
) -> None:
    """
    Derivative tokens are reissued at every expiry, which is why the
    instrument cache is invalidated per trading day.
    """

    assert InstrumentKey.parse(canonical).isDerivative is expected


def test_instrumentExposesCanonicalKeyAsString() -> None:
    """
    Price and order records store the string key, not the nested object.
    """

    instrument = Instrument(
        key = InstrumentKey.parse(EQUITY), brokerSymbol = "NSE:SBIN-EQ",
        brokerToken = "3045", lotSize = 1, source = "fyers"
    )

    assert instrument.instrumentKey == EQUITY
    assert "NSE:SBIN-EQ" in str(instrument)


def test_instrumentRequiresProvenance() -> None:
    """
    ``brokerSymbol`` and ``brokerToken`` are meaningless without knowing
    which broker issued them, so ``source`` is mandatory.
    """

    with pytest.raises(ValidationError):
        Instrument(
            key = InstrumentKey.parse(EQUITY), brokerSymbol = "NSE:SBIN-EQ",
            source = ""
        )


def test_instrumentIsFrozen() -> None:
    """
    A resolved instrument is a fact from the master, not scratch space.
    """

    instrument = Instrument(
        key = InstrumentKey.parse(EQUITY), brokerSymbol = "NSE:SBIN-EQ",
        source = "fyers"
    )

    with pytest.raises(ValidationError):
        instrument.brokerSymbol = "NSE:TATA-EQ"


def test_instrumentRejectsUnknownFields() -> None:
    """
    A vendor payload that grows a field should fail a mapper test rather
    than be silently dropped.
    """

    with pytest.raises(ValidationError):
        Instrument(
            key = InstrumentKey.parse(EQUITY), brokerSymbol = "NSE:SBIN-EQ",
            source = "fyers", unexpectedVendorField = 1
        )
