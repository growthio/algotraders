# -*- encoding: utf-8 -*-

"""
Zerodha Kite Mapping, Chunking and Binary Tick Decoding
---------------------------------------------------------

Kite is the second broker, and its job in this codebase is to prove the
abstraction rather than merely add coverage. The assertions that do that
are the ones where Kite and Fyers legitimately disagree: the same two
year request costs a different number of calls, historical is keyed on
an integer token rather than a symbol, and a permission failure means
"buy the add on" rather than "retry".

``kiteconnect`` is not required to run any of this. Only ``auth.py``
imports the SDK, so the mapper, the chunk table, the API surface and the
binary decoder are all testable without it.
"""

import struct
import datetime as dt

import pytest

from algotraders.brokers.kite import limits, mapper
from algotraders.brokers.kite.api import KiteAPI
from algotraders.brokers.kite.stream import (
    DEFAULT_PRICE_DIVISOR, parsePacket, priceDivisorFor, segmentOf,
    splitPackets
)
from algotraders.core.chunking import countChunks
from algotraders.core.clock import IST
from algotraders.core.executor import BlockingCaller
from algotraders.core.ratelimit import RateLimitPolicy
from algotraders.errors import (
    DataSubscriptionRequired, InstrumentNotFound, OrderRejected,
    TokenExpiredError, TransientBrokerError, UnsupportedTimeframe
)
from algotraders.models.enums import (
    OrderSide, OrderStatus, OrderType, ProductType, StreamMode, Timeframe
)
from algotraders.models.instrument import Instrument, InstrumentKey
from algotraders.models.orders import OrderRequest
from tests.fakes.clock import FakeClock

NOW = dt.datetime(2026, 8, 5, 15, 30, tzinfo = IST)

SBIN = Instrument(
    key = InstrumentKey.parse("NSE:SBIN:EQ"),
    brokerSymbol = "SBIN", brokerToken = "779521", lotSize = 1,
    source = "kite"
)

UNRESOLVED = Instrument(
    key = InstrumentKey.parse("NSE:SBIN:EQ"),
    brokerSymbol = "SBIN", source = "kite"
)


class KiteError(Exception):
    """Base for the fake vendor exceptions used below."""


class TokenException(KiteError):
    """Mirrors kiteconnect.exceptions.TokenException by name."""


class PermissionException(KiteError):
    """Mirrors kiteconnect.exceptions.PermissionException by name."""


class NetworkException(KiteError):
    """Mirrors kiteconnect.exceptions.NetworkException by name."""


class OrderException(KiteError):
    """Mirrors kiteconnect.exceptions.OrderException by name."""


class FakeKiteClient:
    """
    Records calls and replays scripted responses or exceptions.
    """

    def __init__(self, **responses : object) -> None:
        self.responses = dict(responses)
        self.calls = []


    def __answer__(self, method : str, **kwargs : object) -> object:
        self.calls.append({"method" : method, "kwargs" : kwargs})

        entry = self.responses.get(method)

        if isinstance(entry, BaseException):
            raise entry

        return entry if entry is not None else []


    def countOf(self, method : str) -> int:
        return sum(1 for call in self.calls if call["method"] == method)


    def historical_data(self, **kwargs : object) -> object:
        return self.__answer__("historical_data", **kwargs)


    def quote(self, keys : object) -> object:
        return self.__answer__("quote", keys = keys)


    def place_order(self, **kwargs : object) -> object:
        return self.__answer__("place_order", **kwargs)

    def orders(self) -> object:
        return self.__answer__("orders")


    def positions(self) -> object:
        return self.__answer__("positions")


    def holdings(self) -> object:
        return self.__answer__("holdings")


def buildAPI(clock : FakeClock, client : FakeKiteClient) -> KiteAPI:
    """
    Wire a real KiteAPI over a scripted vendor client.
    """

    policy = RateLimitPolicy(
        clock = clock, endpointLimits = limits.ENDPOINT_LIMITS
    )

    return KiteAPI(
        sessionManager = client,
        caller = BlockingCaller(policy, clock, maxConcurrency = 2),
        clock = clock
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(start = NOW)


def candleRow(minute : int) -> dict:
    """
    Build one SDK style historical row.
    """

    return {
        "date" : dt.datetime(2026, 8, 5, 9, 15 + minute, tzinfo = IST),
        "open" : 800.0, "high" : 805.0, "low" : 799.0,
        "close" : 803.0, "volume" : 1_000
    }


# ---------------------------------------------------------------------
# Timeframes and chunk spans
# ---------------------------------------------------------------------

@pytest.mark.parametrize("timeframe, interval", [
    (Timeframe.MINUTE_1, "minute"),
    (Timeframe.MINUTE_3, "3minute"),
    (Timeframe.MINUTE_5, "5minute"),
    (Timeframe.MINUTE_15, "15minute"),
    (Timeframe.HOUR_1, "60minute"),
    (Timeframe.DAY_1, "day"),
])
def test_timeframeMapsToKiteInterval(
    timeframe : Timeframe, interval : str
) -> None:
    """
    Kite spells intervals as words where Fyers uses bare numbers.
    """

    assert mapper.toBrokerTimeframe(timeframe) == interval


def test_monthlyTimeframeIsRefused() -> None:
    """
    Neither broker serves monthly bars.
    """

    with pytest.raises(UnsupportedTimeframe):
        mapper.toBrokerTimeframe(Timeframe.MONTH_1)


def test_chunkSpansDifferFromFyers() -> None:
    """
    Kite caps per interval, Fyers by resolution family.

    The same two year one minute request is 13 calls here against 8 on
    Fyers, which is exactly why the span table lives per broker.
    """

    from algotraders.brokers.fyers import limits as fyersLimits

    start = dt.datetime(2024, 8, 5, tzinfo = IST)

    assert countChunks(
        start, NOW, limits.CHUNK_SPAN[Timeframe.MINUTE_1]
    ) == 13
    assert countChunks(
        start, NOW, fyersLimits.CHUNK_SPAN[Timeframe.MINUTE_1]
    ) == 8

    assert limits.CHUNK_SPAN[Timeframe.MINUTE_15] == dt.timedelta(days = 200)
    assert limits.CHUNK_SPAN[Timeframe.DAY_1] == dt.timedelta(days = 2000)


# ---------------------------------------------------------------------
# Exception translation
# ---------------------------------------------------------------------

def test_tokenExceptionMapsToExpiredSession() -> None:
    """
    A dead session is distinguishable from any other failure.
    """

    translated = mapper.translateException(
        TokenException("Incorrect api_key or access_token"), "quote"
    )

    assert isinstance(translated, TokenExpiredError)


def test_historicalPermissionMeansSubscriptionRequired() -> None:
    """
    Kite bills historical data separately, so a permission failure there
    is a billing action rather than a transient fault.

    Retrying it would burn rate limit on something that can never
    succeed, which is why it is deliberately not a TransientBrokerError.
    """

    translated = mapper.translateException(
        PermissionException("insufficient permission"), "historical 779521"
    )

    assert isinstance(translated, DataSubscriptionRequired)
    assert not isinstance(translated, TransientBrokerError)
    assert "paid add-on" in str(translated)


def test_networkExceptionIsRetryable() -> None:
    """
    A genuine transport fault is worth another attempt.
    """

    translated = mapper.translateException(
        NetworkException("connection reset"), "orders"
    )

    assert isinstance(translated, TransientBrokerError)


def test_orderExceptionCarriesTheBrokerMessage() -> None:
    """
    A refusal surfaces Zerodha's own wording.
    """

    translated = mapper.translateException(
        OrderException("Insufficient funds"), "place_order"
    )

    assert isinstance(translated, OrderRejected)
    assert translated.brokerMessage == "Insufficient funds"


def test_unknownExceptionPassesThroughUnchanged() -> None:
    """
    An unrecognised failure is not disguised as something familiar.
    """

    original = ValueError("programming error")

    assert mapper.translateException(original, "quote") is original


# ---------------------------------------------------------------------
# Historical data
# ---------------------------------------------------------------------

async def test_historicalUsesTheIntegerTokenNotASymbol(
    clock : FakeClock
) -> None:
    """
    The single fact that makes the instrument layer mandatory.

    Kite's historical endpoint accepts an instrument_token and no symbol
    string anywhere, so a request built from a symbol simply cannot be
    made.
    """

    client = FakeKiteClient(historical_data = [candleRow(0), candleRow(1)])
    api = buildAPI(clock, client)

    await api.fetchCandles(
        SBIN, Timeframe.MINUTE_1, (NOW - dt.timedelta(days = 5), NOW)
    )

    sent = client.calls[0]["kwargs"]

    assert sent["instrument_token"] == 779521
    assert isinstance(sent["instrument_token"], int)
    assert "SBIN" not in str(sent)
    assert sent["interval"] == "minute"
    assert sent["from_date"] == "2026-07-31 15:30:00"

    await api.caller.close()


async def test_unresolvedInstrumentFailsBeforeAnyCall(
    clock : FakeClock
) -> None:
    """
    Without a token there is nothing to send, so this fails locally
    rather than as a broker rejection.
    """

    client = FakeKiteClient()
    api = buildAPI(clock, client)

    with pytest.raises(InstrumentNotFound):
        await api.fetchCandles(
            UNRESOLVED, Timeframe.MINUTE_1, (NOW - dt.timedelta(days = 1), NOW)
        )

    assert client.countOf("historical_data") == 0

    await api.caller.close()


async def test_twoYearMinuteRangeSplitsIntoThirteenCalls(
    clock : FakeClock
) -> None:
    """
    The 60 day cap on one minute bars is enforced by the chunker.
    """

    client = FakeKiteClient(historical_data = [candleRow(0)])
    api = buildAPI(clock, client)

    await api.fetchCandles(
        SBIN, Timeframe.MINUTE_1,
        (dt.datetime(2024, 8, 5, tzinfo = IST), NOW)
    )

    assert client.countOf("historical_data") == 13

    await api.caller.close()


async def test_candlesNormalizeToIST(clock : FakeClock) -> None:
    """
    Rows become candles carrying full identity and provenance.
    """

    client = FakeKiteClient(historical_data = [candleRow(0), candleRow(1)])
    api = buildAPI(clock, client)

    candles = await api.fetchCandles(
        SBIN, Timeframe.MINUTE_1, (NOW - dt.timedelta(days = 1), NOW)
    )

    assert len(candles) == 2
    assert candles[0].source == "kite"
    assert candles[0].instrumentKey == "NSE:SBIN:EQ"
    assert candles[0].timestamp.utcoffset() == dt.timedelta(
        hours = 5, minutes = 30
    )

    await api.caller.close()


async def test_historicalRateLimitIsThreePerSecond(
    clock : FakeClock
) -> None:
    """
    Kite's per class limit is applied, and it is tighter than Fyers'
    shared ten per second.
    """

    client = FakeKiteClient(historical_data = [candleRow(0)])
    api = buildAPI(clock, client)

    await api.fetchCandles(
        SBIN, Timeframe.MINUTE_1,
        (dt.datetime(2024, 8, 5, tzinfo = IST), NOW)
    )

    # ? 13 calls, three free then paced at one every third of a second
    assert len(clock.sleepCalls) == 10

    await api.caller.close()


async def test_quoteAndHistoricalDoNotShareABudget(
    clock : FakeClock
) -> None:
    """
    Unlike Fyers, Kite publishes a genuine per class limit, so a quote
    does not consume the historical allowance.
    """

    policy = RateLimitPolicy(
        clock = clock, endpointLimits = limits.ENDPOINT_LIMITS
    )

    from algotraders.models.enums import EndpointClass

    historical = policy.limiterFor(EndpointClass.HISTORICAL)
    quote = policy.limiterFor(EndpointClass.QUOTE)

    assert historical.buckets[0] is not quote.buckets[0]

    for _ in range(3):
        await historical.acquire()

    await quote.acquire()

    assert clock.sleepCalls == []


# ---------------------------------------------------------------------
# Trading and portfolio
# ---------------------------------------------------------------------

def test_orderPayloadUsesKiteVocabulary() -> None:
    """
    Kite spells products MIS/CNC/NRML where Fyers uses INTRADAY/CNC.
    """

    payload = mapper.toOrderPayload(OrderRequest(
        instrument = SBIN, side = OrderSide.BUY, quantity = 10,
        orderType = OrderType.LIMIT, limitPrice = 800.0,
        productType = ProductType.INTRADAY, tag = "entry"
    ))

    assert payload["tradingsymbol"] == "SBIN"
    assert payload["exchange"] == "NSE"
    assert payload["transaction_type"] == "BUY"
    assert payload["order_type"] == "LIMIT"
    assert payload["product"] == "MIS"
    assert payload["price"] == 800.0
    assert payload["tag"] == "entry"


def test_marketOrderCarriesNoPrice() -> None:
    """
    A price key on a market order would be rejected by the exchange.
    """

    payload = mapper.toOrderPayload(OrderRequest(
        instrument = SBIN, side = OrderSide.SELL, quantity = 10
    ))

    assert "price" not in payload
    assert "trigger_price" not in payload
    assert payload["transaction_type"] == "SELL"


async def test_orderPlacementIsNeverRetried(clock : FakeClock) -> None:
    """
    Same rule as Fyers: a timed out placement may already be live.
    """

    client = FakeKiteClient(place_order = NetworkException("timeout"))
    api = buildAPI(clock, client)

    with pytest.raises(TransientBrokerError):
        await api.placeOrder(OrderRequest(
            instrument = SBIN, side = OrderSide.BUY, quantity = 1
        ))

    assert client.countOf("place_order") == 1

    await api.caller.close()


async def test_positionsReadTheNetBook(clock : FakeClock) -> None:
    """
    Kite splits its response into net and day; net is open exposure.
    """

    client = FakeKiteClient(positions = {
        "net" : [{
            "tradingsymbol" : "SBIN", "quantity" : -10,
            "average_price" : 810.0, "last_price" : 805.0,
            "product" : "MIS", "unrealised" : 50.0
        }],
        "day" : []
    })
    api = buildAPI(clock, client)

    positions = await api.fetchPositions()

    assert len(positions) == 1
    assert positions[0].isShort is True
    assert positions[0].productType is ProductType.INTRADAY

    await api.caller.close()


async def test_holdingsCombinePledgedQuantity(clock : FakeClock) -> None:
    """
    Pledged stock is part of the holding but is not sellable, so the two
    figures are reported separately.
    """

    client = FakeKiteClient(holdings = [{
        "tradingsymbol" : "SBIN", "quantity" : 60,
        "collateral_quantity" : 40, "average_price" : 800.0,
        "last_price" : 814.0, "isin" : "INE062A01020"
    }])
    api = buildAPI(clock, client)

    holdings = await api.fetchHoldings()

    assert holdings[0].quantity == 100
    assert holdings[0].availableQuantity == 60
    assert holdings[0].pledgedQuantity == 40

    await api.caller.close()


def test_orderStatusMapsVendorStrings() -> None:
    """
    Kite reports status as text, unlike the Fyers integer.
    """

    assert mapper.toOrderResponse(
        {"order_id" : "1", "status" : "COMPLETE"}
    ).status is OrderStatus.COMPLETE

    assert mapper.toOrderResponse(
        {"order_id" : "2", "status" : "TRIGGER PENDING"}
    ).status is OrderStatus.PENDING

    assert mapper.toOrderResponse(
        {"order_id" : "3", "status" : "SOMETHING NEW"}
    ).status is OrderStatus.UNKNOWN


# ---------------------------------------------------------------------
# Binary tick decoding
# ---------------------------------------------------------------------

def buildLtpPacket(token : int, price : int) -> bytes:
    """
    Build an 8 byte last traded price packet.
    """

    return struct.pack(">ii", token, price)


def buildQuotePacket(token : int, price : int) -> bytes:
    """
    Build a 44 byte quote packet.
    """

    return struct.pack(
        ">iiiiiiiiiii", token, price, 25, price, 100_000, 500, 400,
        79_500, 81_500, 79_000, 80_500
    )


def buildFrame(packets : list) -> bytes:
    """
    Wrap packets in the vendor frame envelope.
    """

    frame = struct.pack(">H", len(packets))

    for packet in packets:
        frame += struct.pack(">H", len(packet)) + packet

    return frame


def test_heartbeatFrameYieldsNoPackets() -> None:
    """
    A one byte frame is the server keepalive and must not be decoded.
    """

    assert splitPackets(b"\x00") == []
    assert splitPackets(b"") == []


def test_frameSplitsIntoPackets() -> None:
    """
    Multiple instruments arrive in one frame.
    """

    packets = splitPackets(buildFrame([
        buildLtpPacket(779521, 81_400), buildLtpPacket(408065, 150_000)
    ]))

    assert len(packets) == 2
    assert all(len(packet) == 8 for packet in packets)


def test_truncatedFrameStopsCleanly() -> None:
    """
    A partial frame yields what it can rather than raising, since a
    struct error inside the reader thread would kill the socket.
    """

    frame = buildFrame([buildLtpPacket(779521, 81_400)])

    assert splitPackets(frame[:-3]) == []


def test_ltpPacketDecodesWithPaiseDivisor() -> None:
    """
    Equity prices arrive in paise and divide by 100.
    """

    tick = parsePacket(
        buildLtpPacket(779521, 81_400), receivedAt = NOW,
        instrumentKey = "NSE:SBIN:EQ"
    )

    assert tick is not None
    assert tick.lastPrice == pytest.approx(814.0)
    assert tick.mode is StreamMode.LTP
    assert tick.instrumentKey == "NSE:SBIN:EQ"
    assert tick.source == "kite"


def test_quotePacketDecodesDayAggregates() -> None:
    """
    The 44 byte packet adds the day's open, high, low and close.
    """

    tick = parsePacket(buildQuotePacket(779521, 81_400), receivedAt = NOW)

    assert tick is not None
    assert tick.mode is StreamMode.QUOTE
    assert tick.volume == 100_000
    assert tick.dayHigh == pytest.approx(815.0)
    assert tick.dayLow == pytest.approx(790.0)
    assert tick.buyQuantity == 500


def test_currencySegmentUsesADifferentDivisor() -> None:
    """
    Currency derivatives scale by 10,000,000, not 100.

    A single global divisor produces correct NSE prices and a USDINR
    quote wrong by five orders of magnitude, which is exactly the kind
    of bug that survives testing on equities alone.
    """

    # ? low byte 3 marks the NSE currency segment
    currencyToken = (12345 << 8) | 3

    assert segmentOf(currencyToken) == 3
    assert priceDivisorFor(currencyToken) == 10_000_000.0
    assert priceDivisorFor(779521) == DEFAULT_PRICE_DIVISOR

    tick = parsePacket(
        buildLtpPacket(currencyToken, 875_000_000), receivedAt = NOW
    )

    assert tick is not None
    assert tick.lastPrice == pytest.approx(87.5)


def test_packetWithoutAKeyFallsBackToTheToken() -> None:
    """
    A packet carries no symbol, so an unresolved token still produces a
    usable tick rather than being discarded.
    """

    tick = parsePacket(buildLtpPacket(779521, 81_400), receivedAt = NOW)

    assert tick is not None
    assert tick.instrumentKey == "779521"


def test_shortPacketIsIgnored() -> None:
    """
    Anything under eight bytes cannot carry a price.
    """

    assert parsePacket(b"\x00\x01", receivedAt = NOW) is None


def test_streamCapsMatchThePublishedLimits() -> None:
    """
    3000 instruments per connection and 3 connections per API key.
    """

    assert limits.MAX_STREAM_INSTRUMENTS == 3_000
    assert limits.MAX_STREAM_CONNECTIONS == 3


def test_kiteDeclaresNoRefreshCapability() -> None:
    """
    The absence is the point: a Kite deployment needs a human every
    trading morning, and the capability set says so honestly.
    """

    from algotraders.models.enums import Capability

    assert Capability.TOKEN_REFRESH not in limits.CAPABILITIES
    assert limits.TOKEN_POLICY.supportsRefresh is False
    assert limits.TOKEN_POLICY.rolloverHourIST == 6
