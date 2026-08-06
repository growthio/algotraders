# -*- encoding: utf-8 -*-

"""
Symbol Master Parsing for Both Brokers
----------------------------------------

The parsers are the part of a broker integration most likely to break
without warning. Fyers publishes a **headerless** positional CSV, so a
single inserted column silently shifts every field; Kite returns
dictionaries, which is safer, but its expiry arrives as a date, a
datetime or a string depending on SDK version.

Both parsers are deliberately defensive, skipping a row they cannot
interpret rather than failing the whole download, because one unusual
contract should not cost an entire exchange's resolution. These tests
pin that behaviour along with the field mapping itself.
"""

import decimal
import datetime as dt

import pytest

from algotraders.brokers.fyers.symbols import FyersInstrumentResolver
from algotraders.brokers.kite.symbols import KiteInstrumentResolver
from algotraders.core.clock import IST
from algotraders.instruments.cache import InstrumentCache
from algotraders.models.enums import (
    Exchange, InstrumentType, OptionRight, Segment
)
from tests.fakes.clock import FakeClock

# ? 2026-01-27 00:00 UTC, the expiry Fyers encodes as epoch seconds
EXPIRY_EPOCH = 1_769_472_000
EXPIRY_DATE = dt.date(2026, 1, 27)


def fyersRow(**overrides : object) -> list:
    """
    Build one positional Fyers master row with 18 columns.
    """

    row = [""] * 18

    row[0] = "101000000003045"      # fytoken
    row[1] = "STATE BANK OF INDIA"  # description
    row[3] = "1"                    # lot size
    row[4] = "0.05"                 # tick size
    row[5] = "INE062A01020"         # isin
    row[8] = "-1"                   # expiry, -1 for cash
    row[9] = "NSE:SBIN-EQ"          # ticker
    row[10] = "NSE"                 # exchange
    row[13] = "SBIN"                # underlying
    row[15] = "0"                   # strike
    row[16] = "XX"                  # option type

    for index, value in overrides.items():
        row[int(index)] = str(value)

    return row


def toCSV(rows : list) -> bytes:
    """
    Render positional rows as the headerless CSV Fyers publishes.
    """

    return "\n".join(",".join(row) for row in rows).encode("utf-8")


@pytest.fixture
def fyersResolver(tmp_path : object) -> FyersInstrumentResolver:
    clock = FakeClock(start = dt.datetime(2026, 8, 5, 9, 30, tzinfo = IST))

    return FyersInstrumentResolver(
        cache = InstrumentCache(tmp_path, clock), clock = clock
    )


@pytest.fixture
def kiteResolver(tmp_path : object) -> KiteInstrumentResolver:
    clock = FakeClock(start = dt.datetime(2026, 8, 5, 9, 30, tzinfo = IST))

    return KiteInstrumentResolver(
        cache = InstrumentCache(tmp_path, clock), clock = clock
    )


# ---------------------------------------------------------------------
# Fyers positional CSV
# ---------------------------------------------------------------------

def test_fyersParsesAnEquityRow(
    fyersResolver : FyersInstrumentResolver
) -> None:
    """
    Every documented column lands in the right field.
    """

    instruments = fyersResolver.parseMaster(toCSV([fyersRow()]))

    assert len(instruments) == 1

    instrument = instruments[0]

    assert instrument.key.exchange is Exchange.NSE
    assert instrument.key.tradingSymbol == "SBIN"
    assert instrument.key.instrumentType is InstrumentType.EQUITY
    assert instrument.brokerSymbol == "NSE:SBIN-EQ"
    assert instrument.brokerToken == "101000000003045"
    assert instrument.lotSize == 1
    assert instrument.isin == "INE062A01020"
    assert instrument.source == "fyers"


def test_fyersParsesAnOptionRow(
    fyersResolver : FyersInstrumentResolver
) -> None:
    """
    Expiry arrives as epoch seconds and strike as a bare number.
    """

    instruments = fyersResolver.parseMaster(toCSV([fyersRow(**{
        "8" : EXPIRY_EPOCH, "9" : "NSE:NIFTY26JAN24800CE",
        "13" : "NIFTY", "15" : "24800", "16" : "CE", "3" : "65"
    })]))

    assert len(instruments) == 1

    key = instruments[0].key

    assert key.instrumentType is InstrumentType.OPTION
    assert key.tradingSymbol == "NIFTY"
    assert key.expiry == EXPIRY_DATE
    assert key.strike == decimal.Decimal("24800")
    assert key.optionRight is OptionRight.CALL
    assert key.segment is Segment.CAPITAL or key.segment is Segment.DERIVATIVE
    assert instruments[0].lotSize == 65


def test_fyersParsesAFuturesRow(
    fyersResolver : FyersInstrumentResolver
) -> None:
    """
    A futures row has an expiry but no strike or right.
    """

    instruments = fyersResolver.parseMaster(toCSV([fyersRow(**{
        "8" : EXPIRY_EPOCH, "9" : "NSE:NIFTY26JANFUT",
        "13" : "NIFTY", "15" : "0", "16" : "XX", "3" : "65"
    })]))

    key = instruments[0].key

    assert key.instrumentType is InstrumentType.FUTURE
    assert key.expiry == EXPIRY_DATE
    assert key.strike is None


def test_fyersParsesAnIndexRow(
    fyersResolver : FyersInstrumentResolver
) -> None:
    """
    Index tickers end in ``-INDEX`` and never expire.
    """

    instruments = fyersResolver.parseMaster(toCSV([fyersRow(**{
        "9" : "NSE:NIFTY50-INDEX", "13" : "NIFTY50", "3" : "0"
    })]))

    assert instruments[0].key.instrumentType is InstrumentType.INDEX
    assert instruments[0].lotSize is None


def test_fyersSkipsShortRowsWithoutFailing(
    fyersResolver : FyersInstrumentResolver
) -> None:
    """
    A truncated row is skipped, not fatal.

    A single malformed contract must not cost the whole exchange's
    resolution, since the file has tens of thousands of rows.
    """

    payload = toCSV([fyersRow(), ["too", "short"], fyersRow(**{
        "9" : "NSE:TCS-EQ", "13" : "TCS", "0" : "1010000000011536"
    })])

    instruments = fyersResolver.parseMaster(payload)

    assert len(instruments) == 2
    assert {entry.key.tradingSymbol for entry in instruments} == {
        "SBIN", "TCS"
    }


def test_fyersSkipsUnrecognisedTickerShapes(
    fyersResolver : FyersInstrumentResolver
) -> None:
    """
    A ticker matching no known suffix is skipped rather than guessed.
    """

    assert fyersResolver.parseMaster(toCSV([fyersRow(**{
        "9" : "NSE:SOMETHING-WEIRD", "16" : "XX", "8" : "-1"
    })])) == []


def test_fyersHandlesAnEmptyFile(
    fyersResolver : FyersInstrumentResolver
) -> None:
    """
    An empty download yields no instruments and no exception.
    """

    assert fyersResolver.parseMaster(b"") == []


def test_fyersRendersABrokerSymbol(
    fyersResolver : FyersInstrumentResolver
) -> None:
    """
    The master's own ticker is preferred, since a derivative contract
    name cannot be reconstructed from the canonical key.
    """

    instrument = fyersResolver.parseMaster(toCSV([fyersRow()]))[0]

    assert fyersResolver.toBrokerSymbol(instrument) == "NSE:SBIN-EQ"


# ---------------------------------------------------------------------
# Kite instrument dump
# ---------------------------------------------------------------------

def kiteRow(**overrides : object) -> dict:
    """
    Build one Kite instrument dump row.
    """

    row = {
        "instrument_token" : 779521,
        "exchange_token" : 3045,
        "tradingsymbol" : "SBIN",
        "name" : "STATE BANK OF INDIA",
        "expiry" : "",
        "strike" : 0.0,
        "tick_size" : 0.05,
        "lot_size" : 1,
        "instrument_type" : "EQ",
        "segment" : "NSE",
        "exchange" : "NSE"
    }
    row.update(overrides)

    return row


def test_kiteParsesAnEquityRowWithItsToken(
    kiteResolver : KiteInstrumentResolver
) -> None:
    """
    The instrument token is the whole point: no historical call can be
    made without it.
    """

    instruments = kiteResolver.parseDump([kiteRow()])

    assert len(instruments) == 1

    instrument = instruments[0]

    assert instrument.brokerToken == "779521"
    assert instrument.brokerSymbol == "SBIN"
    assert instrument.key.instrumentType is InstrumentType.EQUITY
    assert instrument.source == "kite"


def test_kiteParsesAnOptionRow(
    kiteResolver : KiteInstrumentResolver
) -> None:
    """
    Options are keyed on the underlying name, not the contract symbol.
    """

    instruments = kiteResolver.parseDump([kiteRow(
        tradingsymbol = "NIFTY26JAN24800CE", name = "NIFTY",
        expiry = "2026-01-27", strike = 24800.0, instrument_type = "CE",
        exchange = "NFO", lot_size = 65, instrument_token = 13567490
    )])

    key = instruments[0].key

    assert key.exchange is Exchange.NFO
    assert key.tradingSymbol == "NIFTY"
    assert key.instrumentType is InstrumentType.OPTION
    assert key.expiry == EXPIRY_DATE
    assert key.strike == decimal.Decimal("24800.0")
    assert key.optionRight is OptionRight.CALL
    assert key.segment is Segment.DERIVATIVE


@pytest.mark.parametrize("expiry", [
    "2026-01-27",
    dt.date(2026, 1, 27),
    dt.datetime(2026, 1, 27, 15, 30),
])
def test_kiteAcceptsEveryExpirySpelling(
    kiteResolver : KiteInstrumentResolver, expiry : object
) -> None:
    """
    The SDK returns expiry as a string, a date or a datetime depending
    on version, and all three have to resolve to the same contract.
    """

    instruments = kiteResolver.parseDump([kiteRow(
        tradingsymbol = "NIFTY26JANFUT", name = "NIFTY", expiry = expiry,
        instrument_type = "FUT", exchange = "NFO", lot_size = 65
    )])

    assert instruments[0].key.expiry == EXPIRY_DATE


def test_kiteSkipsUnknownExchanges(
    kiteResolver : KiteInstrumentResolver
) -> None:
    """
    An exchange outside the supported set is skipped rather than mapped
    onto a plausible neighbour.
    """

    assert kiteResolver.parseDump([kiteRow(exchange = "NASDAQ")]) == []


def test_kiteSkipsAnOptionMissingItsStrike(
    kiteResolver : KiteInstrumentResolver
) -> None:
    """
    An option row without a strike cannot identify a contract.
    """

    assert kiteResolver.parseDump([kiteRow(
        instrument_type = "CE", expiry = "2026-01-27", strike = 0.0,
        exchange = "NFO"
    )]) == []


def test_kiteSkipsMalformedRowsButKeepsTheRest(
    kiteResolver : KiteInstrumentResolver
) -> None:
    """
    One bad row does not cost the exchange.
    """

    instruments = kiteResolver.parseDump([
        kiteRow(),
        {"garbage" : True},
        kiteRow(tradingsymbol = "TCS", instrument_token = 2953217)
    ])

    assert len(instruments) == 2


def test_kiteRequiresAnAuthenticatedSession(
    kiteResolver : KiteInstrumentResolver
) -> None:
    """
    Unlike Fyers, the Kite dump is not public, so a resolver built
    without a session says so plainly instead of failing at the socket.
    """

    from algotraders.errors import InstrumentMasterUnavailable
    import asyncio

    with pytest.raises(InstrumentMasterUnavailable) as failure:
        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            kiteResolver.downloadMaster(Segment.CAPITAL)
        )

    assert "authenticated session" in str(failure.value)


def test_kiteRendersABrokerSymbol(
    kiteResolver : KiteInstrumentResolver
) -> None:
    """
    Kite orders take the bare trading symbol, unlike the Fyers form.
    """

    instrument = kiteResolver.parseDump([kiteRow()])[0]

    assert kiteResolver.toBrokerSymbol(instrument) == "SBIN"
