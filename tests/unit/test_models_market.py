# -*- encoding: utf-8 -*-

"""
Market Data Records, Timezone Discipline and the ORM Bridge
--------------------------------------------------------------

Two properties are load bearing. Naive timestamps must be refused,
because the original implementation silently read host local time and
produced candles hours out on any container not set to IST. And
``toRecord()`` must emit ``snake_case``, because that dump is what the
host inserts into ``private.security_prices_tx`` without an adapter.
"""

import datetime as dt

import pytest
from pydantic import ValidationError

from algotraders.core.clock import IST
from algotraders.models.enums import StreamMode, Timeframe
from algotraders.models.market import (
    Candle, DepthLevel, MarketDepth, Quote, Tick
)

MOMENT = dt.datetime(2026, 8, 5, 9, 15, tzinfo = IST)


def buildCandle(**overrides : object) -> Candle:
    """
    Construct a valid candle, overriding individual fields per test.
    """

    fields = dict(
        instrumentKey = "NSE:SBIN:EQ", timeframe = Timeframe.MINUTE_1,
        timestamp = MOMENT, openPrice = 100.0, highPrice = 101.0,
        lowPrice = 99.5, closePrice = 100.5, volume = 1000, source = "fyers"
    )
    fields.update(overrides)

    return Candle(**fields)


def test_candleDumpsToDatabaseColumnNames() -> None:
    """
    ``toRecord`` produces the column names the host's price table uses,
    which is what makes ingestion an insert rather than a translation.
    """

    record = buildCandle().toRecord()

    for column in (
        "instrument_key", "open_price", "high_price", "low_price",
        "close_price", "volume", "open_interest", "source", "timeframe"
    ):
        assert column in record

    # ? the enum serialises to the bare value the PostgreSQL enum expects
    assert record["timeframe"] == "1m"
    assert record["open_price"] == 100.0


def test_candleAcceptsBothFieldAndAliasSpellings() -> None:
    """
    A mapper can feed a snake_case vendor payload straight in, while
    Python callers keep camelCase.
    """

    fromAlias = Candle(
        instrument_key = "NSE:SBIN:EQ", timeframe = "1m", timestamp = MOMENT,
        open_price = 100.0, high_price = 101.0, low_price = 99.5,
        close_price = 100.5, volume = 1000, source = "fyers"
    )

    assert fromAlias == buildCandle()


def test_naiveTimestampIsRejected() -> None:
    """
    Guessing a timezone for market data is how silent multi-hour errors
    are introduced, so a naive value fails outright.
    """

    with pytest.raises(ValidationError) as failure:
        buildCandle(timestamp = dt.datetime(2026, 8, 5, 9, 15))

    assert "timezone aware" in str(failure.value)


def test_awareTimestampIsNormalisedToIST() -> None:
    """
    A UTC timestamp is converted rather than relabelled, preserving the
    instant while making every stored candle comparable.
    """

    utc = dt.datetime(2026, 8, 5, 3, 45, tzinfo = dt.timezone.utc)
    candle = buildCandle(timestamp = utc)

    assert candle.timestamp == utc
    assert candle.timestamp.hour == 9
    assert candle.timestamp.utcoffset() == dt.timedelta(hours = 5, minutes = 30)


def test_zeroVolumeIsValid() -> None:
    """
    An illiquid contract legitimately prints no volume; that is data,
    not a missing value.
    """

    assert buildCandle(volume = 0).volume == 0


def test_negativeVolumeIsRejected() -> None:
    """
    Negative traded quantity is impossible and signals a mapper bug.
    """

    with pytest.raises(ValidationError):
        buildCandle(volume = -1)


def test_ohlcConsistencyIsReportedNotEnforced() -> None:
    """
    An inconsistent bar is flagged but still constructible.

    Brokers occasionally emit one, and discarding real data over it
    would leave a silent hole in the series, so the caller decides.
    """

    assert buildCandle().isConsistent is True

    suspicious = buildCandle(highPrice = 99.0)

    assert suspicious.isConsistent is False


def test_candleIsFrozen() -> None:
    """
    A candle is a historical fact, not scratch space for a strategy.
    """

    with pytest.raises(ValidationError):
        buildCandle().closePrice = 200.0


def test_tickComputesFeedLatency() -> None:
    """
    The gap between the exchange stamp and local receipt is the only
    feed latency measurement available to the host.
    """

    tick = Tick(
        instrumentKey = "NSE:SBIN:EQ", lastPrice = 100.0,
        exchangeTimestamp = MOMENT,
        receivedAt = MOMENT + dt.timedelta(milliseconds = 250),
        mode = StreamMode.QUOTE, source = "kite"
    )

    assert tick.feedLatency == dt.timedelta(milliseconds = 250)


def test_tickWithoutExchangeTimestampHasNoLatency() -> None:
    """
    Latency is unknowable when the broker sends no exchange stamp, and
    reporting zero would be a lie.
    """

    tick = Tick(
        instrumentKey = "NSE:SBIN:EQ", lastPrice = 100.0,
        receivedAt = MOMENT, mode = StreamMode.LTP, source = "kite"
    )

    assert tick.feedLatency is None


def test_tickSupportsMinimalLTPMode() -> None:
    """
    Kite's LTP mode carries eight bytes, so every field beyond the price
    has to be optional rather than the consumer branching on mode.
    """

    tick = Tick(
        instrumentKey = "NSE:SBIN:EQ", lastPrice = 100.0,
        receivedAt = MOMENT, mode = StreamMode.LTP, source = "kite"
    )

    assert tick.volume is None
    assert tick.depth is None


def test_marketDepthComputesSpread() -> None:
    """
    Best ask minus best bid, from the top of each side.
    """

    depth = MarketDepth(
        buy = [DepthLevel(price = 99.5, quantity = 100, orders = 3)],
        sell = [DepthLevel(price = 100.0, quantity = 150)]
    )

    assert depth.spread == pytest.approx(0.5)


def test_emptyBookHasNoSpread() -> None:
    """
    Before the open, or at a circuit limit, one side can be empty and a
    computed spread would be meaningless.
    """

    assert MarketDepth().spread is None
    assert MarketDepth(
        buy = [DepthLevel(price = 99.5, quantity = 10)]
    ).spread is None


def test_rawVendorPayloadStaysOutOfSerialisation() -> None:
    """
    The untouched vendor blob is available in process for audit logging
    but must never reach an API response body or a database column.
    """

    quote = Quote(
        instrumentKey = "NSE:SBIN:EQ", lastPrice = 100.0, timestamp = MOMENT,
        source = "fyers", raw = {"vendorOnly": "internal detail"}
    )

    assert quote.raw == {"vendorOnly": "internal detail"}
    assert "raw" not in quote.model_dump()
    assert "internal detail" not in quote.model_dump_json()
    assert "raw" not in quote.toRecord()
