# -*- encoding: utf-8 -*-

"""
Converting Vendor Socket Messages Into Normalized Ticks
---------------------------------------------------------

Both stream implementations expose their message-to-tick conversion as
an ordinary method so it can be tested without opening a socket. That
matters more than it sounds: the conversion runs on the vendor's reader
thread, where an exception silences the entire feed, so every shape the
vendor can send has to be handled or explicitly ignored.
"""

import datetime as dt

import pytest

from algotraders.brokers.kite.stream import KiteStream
from algotraders.core.clock import IST
from algotraders.models.enums import StreamMode
from algotraders.streaming.sinks import QueueTickSink
from tests.fakes.clock import FakeClock

MOMENT = dt.datetime(2026, 8, 5, 10, 0, tzinfo = IST)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(start = MOMENT)


@pytest.fixture
def kiteStream(clock : FakeClock) -> KiteStream:
    stream = KiteStream(
        sessionManager = None, sink = QueueTickSink(), clock = clock,
        apiKey = "key", accessToken = "token"
    )
    stream.tokenKeys[779521] = "NSE:SBIN:EQ"

    return stream


def test_kiteVendorTickResolvesTheCanonicalKey(
    kiteStream : KiteStream
) -> None:
    """
    The SDK hands back an integer token; the recorded map turns it into
    a canonical key without an await on the reader thread.
    """

    tick = kiteStream.__fromVendorTick__({
        "instrument_token" : 779521, "last_price" : 814.0,
        "volume_traded" : 100_000,
        "ohlc" : {"open" : 810.0, "high" : 816.0, "low" : 808.0,
                  "close" : 805.0}
    }, MOMENT)

    assert tick is not None
    assert tick.instrumentKey == "NSE:SBIN:EQ"
    assert tick.lastPrice == 814.0
    assert tick.dayHigh == 816.0
    assert tick.mode is StreamMode.QUOTE
    assert tick.source == "kite"


def test_kiteUnknownTokenFallsBackToTheNumber(
    kiteStream : KiteStream
) -> None:
    """
    An unmapped token still produces a usable tick.

    Dropping it would silently lose an instrument the caller believes it
    subscribed to, which is worse than a key that needs resolving later.
    """

    tick = kiteStream.__fromVendorTick__(
        {"instrument_token" : 424242, "last_price" : 100.0}, MOMENT
    )

    assert tick is not None
    assert tick.instrumentKey == "424242"
    assert tick.mode is StreamMode.LTP


def test_kiteTickWithoutAPriceIsIgnored(kiteStream : KiteStream) -> None:
    """
    A connection acknowledgement carries no price and is not a tick.
    """

    assert kiteStream.__fromVendorTick__({"instrument_token" : 1}, MOMENT) is None
    assert kiteStream.__fromVendorTick__({"last_price" : 1.0}, MOMENT) is None


def test_kiteFullModeCarriesDepth(kiteStream : KiteStream) -> None:
    """
    Depth presence is what distinguishes full mode from quote mode.
    """

    tick = kiteStream.__fromVendorTick__({
        "instrument_token" : 779521, "last_price" : 814.0,
        "volume_traded" : 1_000,
        "depth" : {
            "buy" : [{"price" : 813.5, "quantity" : 100, "orders" : 3}],
            "sell" : [{"price" : 814.5, "quantity" : 150, "orders" : 4}]
        }
    }, MOMENT)

    assert tick is not None
    assert tick.mode is StreamMode.FULL
    assert tick.depth is not None
    assert tick.depth.spread == pytest.approx(1.0)


def test_kiteCallbackNeverRaisesOnBadInput(
    kiteStream : KiteStream
) -> None:
    """
    The callback runs on the vendor's reader thread, where an escaping
    exception kills the thread and the feed goes silent with nothing
    logged anywhere.
    """

    kiteStream.__onTicks__(None, None)
    kiteStream.__onTicks__(None, "not a list")
    kiteStream.__onTicks__(None, [{"garbage" : True}])

    assert kiteStream.lastTickAt is None


def test_kiteCallbackRecordsTheLastTickTime(
    kiteStream : KiteStream
) -> None:
    """
    Every delivered tick resets the staleness timer the supervisor
    reads.
    """

    kiteStream.__onTicks__(None, [
        {"instrument_token" : 779521, "last_price" : 814.0}
    ])

    assert kiteStream.lastTickAt == MOMENT


def test_fyersTickConversion(clock : FakeClock) -> None:
    """
    The Fyers socket sends JSON, so its conversion is a field mapping
    rather than a binary decode.
    """

    pytest.importorskip(
        "fyers_apiv3", reason = "requires the optional fyers extra"
    )

    from algotraders.brokers.fyers.stream import FyersStream

    stream = FyersStream(
        sessionManager = None, sink = QueueTickSink(), clock = clock,
        accessToken = "app:token"
    )

    tick = stream.toTick({
        "symbol" : "NSE:SBIN-EQ", "ltp" : 814.0,
        "vol_traded_today" : 100_000, "open_price" : 810.0,
        "high_price" : 816.0, "low_price" : 808.0,
        "prev_close_price" : 805.0, "exch_feed_time" : 1_785_901_500
    })

    assert tick is not None
    assert tick.instrumentKey == "NSE:SBIN-EQ"
    assert tick.lastPrice == 814.0
    assert tick.volume == 100_000
    assert tick.source == "fyers"
    assert tick.exchangeTimestamp is not None
    assert tick.exchangeTimestamp.utcoffset() == dt.timedelta(
        hours = 5, minutes = 30
    )


def test_fyersIgnoresNonTickMessages(clock : FakeClock) -> None:
    """
    Connection acknowledgements and anything without a price are not
    ticks.
    """

    pytest.importorskip(
        "fyers_apiv3", reason = "requires the optional fyers extra"
    )

    from algotraders.brokers.fyers.stream import FyersStream

    stream = FyersStream(
        sessionManager = None, sink = QueueTickSink(), clock = clock
    )

    assert stream.toTick(None) is None
    assert stream.toTick("connected") is None
    assert stream.toTick({"symbol" : "NSE:SBIN-EQ"}) is None
    assert stream.toTick({"ltp" : 100.0}) is None


def test_fyersCallbackAbsorbsFailures(clock : FakeClock) -> None:
    """
    Same reader-thread rule as Kite: never raise out of the callback.
    """

    pytest.importorskip(
        "fyers_apiv3", reason = "requires the optional fyers extra"
    )

    from algotraders.brokers.fyers.stream import FyersStream

    stream = FyersStream(
        sessionManager = None, sink = QueueTickSink(), clock = clock
    )

    stream.__onMessage__(None)
    stream.__onMessage__({"symbol" : "X", "ltp" : "not a number"})
    stream.__onClose__("bye")
    stream.__onError__("boom")

    assert stream.isConnected is False
