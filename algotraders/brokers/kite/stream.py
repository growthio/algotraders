# -*- encoding: utf-8 -*-

"""
Zerodha Kite Websocket and Binary Tick Decoding
--------------------------------------------------

Kite streams market data as **binary frames**, not JSON. A frame carries
a packet count followed by length prefixed packets, and a packet's
length alone identifies its mode: 8 bytes is last traded price, 44 adds
the day's aggregates, 184 adds the five level order book.

Three details in the encoding are easy to get wrong and each produces
plausible-looking but wrong prices.

**Prices are integers scaled by segment.** Equity and derivative prices
arrive in paise and divide by 100, but currency derivatives divide by
10,000,000 and BSE currency by 10,000. Using one divisor everywhere
yields a USDINR quote off by five orders of magnitude, which is obvious,
and an NSE quote that happens to be right, which is why the bug survives.

**The segment is encoded in the instrument token.** The low byte of the
token identifies the exchange segment, so the divisor is derived from
the token itself rather than needing a lookup.

**A packet carries no symbol.** Only the integer token, which is why the
instrument resolver's token index exists and why an unresolvable token
is dropped rather than guessed at.
"""

import struct
import asyncio
import datetime as dt

from typing import Any, Dict, List, Optional, Sequence

from algotraders.brokers.kite import limits
from algotraders.core.clock import BaseClock
from algotraders.logger import getLogger
from algotraders.models.enums import StreamMode
from algotraders.models.market import DepthLevel, MarketDepth, Tick
from algotraders.models.timezone import fromEpoch
from algotraders.streaming.base import BaseBrokerStream, BaseTickSink
from algotraders.streaming.bridge import ThreadLoopBridge

logger = getLogger(__name__)

# ? low byte of instrument_token identifies the exchange segment
SEGMENT_NSE_CM = 1
SEGMENT_NSE_FO = 2
SEGMENT_NSE_CD = 3
SEGMENT_BSE_CM = 4
SEGMENT_BSE_FO = 5
SEGMENT_BSE_CD = 6
SEGMENT_MCX_FO = 7
SEGMENT_INDICES = 9

# ! one divisor per segment. A single global divisor gives correct NSE
# prices and silently wrong currency prices, which is why this table
# exists rather than a constant
PRICE_DIVISORS : Dict[int, float] = {
    SEGMENT_NSE_CD : 10_000_000.0,
    SEGMENT_BSE_CD : 10_000.0
}

DEFAULT_PRICE_DIVISOR = 100.0

DEPTH_ENTRY_SIZE = 12
DEPTH_LEVELS_PER_SIDE = 5

MODE_NAMES : Dict[StreamMode, str] = {
    StreamMode.LTP : "ltp",
    StreamMode.QUOTE : "quote",
    StreamMode.FULL : "full"
}


def segmentOf(instrumentToken : int) -> int:
    """
    Extract the exchange segment encoded in an instrument token.

    :rtype:   int
    :returns: The segment identifier, the token's low byte.
    """

    return instrumentToken & 0xFF


def priceDivisorFor(instrumentToken : int) -> float:
    """
    Return the scaling divisor for prices on a token's segment.

    :rtype:   float
    :returns: 100 for equity and derivatives, 10,000,000 for NSE
        currency, 10,000 for BSE currency.
    """

    return PRICE_DIVISORS.get(segmentOf(instrumentToken), DEFAULT_PRICE_DIVISOR)


def splitPackets(frame : bytes) -> List[bytes]:
    """
    Split a binary websocket frame into individual packets.

    The frame is a big endian ``int16`` packet count followed by, per
    packet, an ``int16`` length and that many bytes.

    :type  frame: bytes
    :param frame: The raw binary frame.

    :rtype:   List[bytes]
    :returns: The packets. A frame of one byte is the server's keepalive
        heartbeat and yields nothing.
    """

    if len(frame) < 2:
        # ? a single byte frame is the heartbeat, sent every few seconds
        # when there is nothing to stream, and is safely ignored
        return []

    count = struct.unpack(">H", frame[0:2])[0]
    packets : List[bytes] = []
    cursor = 2

    for _ in range(count):
        if cursor + 2 > len(frame):
            break

        length = struct.unpack(">H", frame[cursor:cursor + 2])[0]
        cursor += 2

        if cursor + length > len(frame):
            break

        packets.append(frame[cursor:cursor + length])
        cursor += length

    return packets


def parsePacket(
    packet : bytes, receivedAt : dt.datetime,
    instrumentKey : Optional[str] = None
) -> Optional[Tick]:
    """
    Decode one binary packet into a normalized tick.

    :type  packet: bytes
    :param packet: A single packet from :func:`splitPackets`.

    :type  receivedAt: dt.datetime
    :param receivedAt: When this process saw the frame.

    :type  instrumentKey: Optional[str]
    :param instrumentKey: Canonical key, when the caller resolved the
        token. Falls back to the token as a string, so a tick is never
        silently discarded for want of a lookup.

    :rtype:   Optional[Tick]
    :returns: The tick, or ``None`` for a packet too short to carry a
        price.
    """

    if len(packet) < limits.PACKET_SIZE_LTP:
        return None

    token, rawPrice = struct.unpack(">ii", packet[0:8])
    divisor = priceDivisorFor(token)

    key = instrumentKey or str(token)
    lastPrice = rawPrice / divisor

    if len(packet) < limits.PACKET_SIZE_QUOTE:
        return Tick(
            instrumentKey = key, lastPrice = lastPrice,
            receivedAt = receivedAt, mode = StreamMode.LTP,
            source = limits.BROKER_NAME
        )

    (
        lastQuantity, averagePrice, volume, buyQuantity, sellQuantity,
        dayOpen, dayHigh, dayLow, dayClose
    ) = struct.unpack(">iiiiiiiii", packet[8:44])

    mode = (
        StreamMode.FULL if len(packet) >= limits.PACKET_SIZE_FULL
        else StreamMode.QUOTE
    )

    exchangeTimestamp : Optional[dt.datetime] = None
    openInterest : Optional[int] = None
    depth : Optional[MarketDepth] = None

    if mode is StreamMode.FULL:
        (
            _lastTradedTime, rawOpenInterest, _oiHigh, _oiLow, exchangeEpoch
        ) = struct.unpack(">iiiii", packet[44:64])

        openInterest = max(0, rawOpenInterest)

        if exchangeEpoch > 0:
            exchangeTimestamp = fromEpoch(exchangeEpoch)

        depth = _parseDepth(packet[64:184], divisor)

    return Tick(
        instrumentKey = key,
        lastPrice = lastPrice,
        lastQuantity = max(0, lastQuantity),
        averagePrice = averagePrice / divisor,
        volume = max(0, volume),
        buyQuantity = max(0, buyQuantity),
        sellQuantity = max(0, sellQuantity),
        dayOpen = dayOpen / divisor,
        dayHigh = dayHigh / divisor,
        dayLow = dayLow / divisor,
        dayClose = dayClose / divisor,
        openInterest = openInterest,
        exchangeTimestamp = exchangeTimestamp,
        receivedAt = receivedAt,
        mode = mode,
        depth = depth,
        source = limits.BROKER_NAME
    )


def _parseDepth(payload : bytes, divisor : float) -> Optional[MarketDepth]:
    """
    Decode the 120 byte market depth block.

    Ten entries of twelve bytes: five bids then five asks, each an
    ``int32`` quantity, an ``int32`` price, an ``int16`` order count and
    two bytes of padding.

    :rtype:   Optional[MarketDepth]
    :returns: The book, or ``None`` when the block is truncated.
    """

    required = DEPTH_ENTRY_SIZE * DEPTH_LEVELS_PER_SIDE * 2

    if len(payload) < required:
        return None

    buy : List[DepthLevel] = []
    sell : List[DepthLevel] = []

    for index in range(DEPTH_LEVELS_PER_SIDE * 2):
        offset = index * DEPTH_ENTRY_SIZE
        quantity, price, orders = struct.unpack(
            ">iih", payload[offset:offset + 10]
        )

        level = DepthLevel(
            price = price / divisor,
            quantity = max(0, quantity),
            orders = max(0, orders)
        )

        if index < DEPTH_LEVELS_PER_SIDE:
            buy.append(level)
        else:
            sell.append(level)

    return MarketDepth(buy = buy, sell = sell)


class KiteStream(BaseBrokerStream):
    """
    Live market data over the Kite websocket.

    :type  apiKey: str
    :param apiKey: Kite API key, used for the socket handshake.

    :type  accessToken: str
    :param accessToken: The day's access token.

    :NOTE: 3000 instruments per connection and 3 connections per API
        key. Both caps are per key and aggregated across every user of
        it, so two processes sharing a key share the ceiling.
    """

    brokerName = limits.BROKER_NAME
    maxInstrumentsPerConnection = limits.MAX_STREAM_INSTRUMENTS

    def __init__(
        self, sessionManager : Any, sink : BaseTickSink, clock : BaseClock,
        apiKey : str = "", accessToken : str = "", resolver : Any = None,
        loop : Optional[asyncio.AbstractEventLoop] = None,
        tickerFactory : Optional[Any] = None, **extra : Any
    ) -> None:
        super().__init__(
            sessionManager = sessionManager, sink = sink, clock = clock,
            resolver = resolver, loop = loop, **extra
        )

        self.apiKey = apiKey
        self.accessToken = accessToken
        self.tickerFactory = tickerFactory
        self.ticker : Any = None
        self.bridge : Optional[ThreadLoopBridge] = None

        # ? token to canonical key, filled at subscribe time so the
        # binary path never has to await a resolver lookup
        self.tokenKeys : Dict[int, str] = {}

        self.__consumer__ : Optional[asyncio.Task] = None


    async def connect(self) -> None:
        """
        Open the websocket and start the tick consumer.
        """

        if self.isConnected:
            return

        loop = self.loop or asyncio.get_running_loop()
        self.loop = loop

        self.bridge = ThreadLoopBridge(
            loop = loop, sink = self.sink, onError = self.onError
        )
        self.__consumer__ = asyncio.create_task(self.bridge.run())

        factory = self.tickerFactory or self.__defaultTicker__
        self.ticker = factory(
            api_key = self.apiKey, access_token = self.accessToken
        )

        self.ticker.on_ticks = self.__onTicks__
        self.ticker.on_connect = self.__onConnect__
        self.ticker.on_close = self.__onClose__
        self.ticker.on_error = self.__onError__

        await asyncio.to_thread(self.ticker.connect, threaded = True)

        self.isConnected = True

        logger.info("kite websocket connected")


    def __defaultTicker__(self, **kwargs : Any) -> Any:
        """
        Build the vendor ticker, importing the SDK only when needed.
        """

        from kiteconnect import KiteTicker

        return KiteTicker(**kwargs)


    async def disconnect(self) -> None:
        """
        Close the websocket and stop the consumer.
        """

        self.isConnected = False

        if self.ticker is not None:
            try:
                await asyncio.to_thread(self.ticker.close)
            except Exception as error:
                logger.debug(
                    "kite ticker close raised %s", type(error).__name__
                )

            self.ticker = None

        if self.bridge is not None:
            await self.bridge.stop()

        if self.__consumer__ is not None:
            self.__consumer__.cancel()

            try:
                await self.__consumer__
            except asyncio.CancelledError:
                pass

            self.__consumer__ = None


    async def subscribe(
        self, instruments : Sequence[Any],
        mode : StreamMode = StreamMode.QUOTE
    ) -> None:
        """
        Subscribe by instrument token, recording the token to key map.

        Overrides the base implementation because Kite subscribes by
        integer token rather than by symbol, and the binary tick path
        needs the reverse mapping without an await.
        """

        for instrument in instruments:
            if instrument.brokerToken:
                self.tokenKeys[int(instrument.brokerToken)] = (
                    instrument.instrumentKey
                )

        await super().subscribe(instruments, mode = mode)


    async def sendSubscription(
        self, symbols : Sequence[str], mode : StreamMode, subscribe : bool
    ) -> None:
        """
        Issue a subscribe or unsubscribe on the vendor ticker.
        """

        if self.ticker is None:
            raise RuntimeError("kite ticker is not connected")

        tokens = [
            token for token, key in self.tokenKeys.items()
            if key in set(symbols) or str(token) in set(symbols)
        ]

        if not tokens:
            return

        if subscribe:
            await asyncio.to_thread(self.ticker.subscribe, tokens)
            await asyncio.to_thread(
                self.ticker.set_mode, MODE_NAMES[mode], tokens
            )
        else:
            await asyncio.to_thread(self.ticker.unsubscribe, tokens)


    def __onConnect__(self, ws : Any = None, response : Any = None) -> None:
        """
        Vendor callback when the socket opens, on its own thread.
        """

        logger.debug("kite websocket open")


    def __onClose__(
        self, ws : Any = None, code : Any = None, reason : Any = None
    ) -> None:
        """
        Vendor callback when the socket closes, on its own thread.
        """

        self.isConnected = False

        logger.info("kite websocket closed: %s %s", code, reason)


    def __onError__(
        self, ws : Any = None, code : Any = None, reason : Any = None
    ) -> None:
        """
        Vendor callback for a socket error, on its own thread.
        """

        logger.warning("kite websocket error: %s %s", code, reason)


    def __onTicks__(self, ws : Any, ticks : Any) -> None:
        """
        Vendor callback delivering parsed ticks, on the reader thread.

        The SDK parses the binary itself and hands back dictionaries, so
        this converts those. :func:`parsePacket` remains available and
        tested for anyone decoding a raw frame directly.

        ! Nothing here may raise: an exception escaping a vendor callback
        kills the reader thread and silences the feed.
        """

        try:
            received = self.clock.now()

            for entry in ticks or []:
                tick = self.__fromVendorTick__(entry, received)

                if tick is None:
                    continue

                self.recordTick(tick)

                if self.bridge is not None:
                    self.bridge.submit(tick)
        except Exception as error:
            logger.warning(
                "discarding unparseable kite tick batch: %s",
                type(error).__name__
            )


    def __fromVendorTick__(
        self, entry : Dict[str, Any], receivedAt : dt.datetime
    ) -> Optional[Tick]:
        """
        Convert one SDK parsed tick dictionary into a normalized tick.
        """

        token = entry.get("instrument_token")

        if token is None or entry.get("last_price") is None:
            return None

        ohlc = entry.get("ohlc") or {}
        depth = entry.get("depth")

        return Tick(
            instrumentKey = self.tokenKeys.get(int(token), str(token)),
            lastPrice = float(entry["last_price"]),
            lastQuantity = entry.get("last_traded_quantity"),
            averagePrice = entry.get("average_traded_price"),
            volume = entry.get("volume_traded"),
            buyQuantity = entry.get("total_buy_quantity"),
            sellQuantity = entry.get("total_sell_quantity"),
            dayOpen = ohlc.get("open"),
            dayHigh = ohlc.get("high"),
            dayLow = ohlc.get("low"),
            dayClose = ohlc.get("close"),
            openInterest = entry.get("oi"),
            exchangeTimestamp = _asDatetime(entry.get("exchange_timestamp")),
            receivedAt = receivedAt,
            mode = _modeFrom(entry),
            depth = _depthFromVendor(depth),
            source = limits.BROKER_NAME
        )


def _modeFrom(entry : Dict[str, Any]) -> StreamMode:
    """
    Infer the subscription mode from which fields are populated.
    """

    if entry.get("depth"):
        return StreamMode.FULL

    if entry.get("volume_traded") is not None:
        return StreamMode.QUOTE

    return StreamMode.LTP


def _depthFromVendor(book : Any) -> Optional[MarketDepth]:
    """
    Convert the SDK's parsed depth dictionary.
    """

    if not isinstance(book, dict):
        return None

    def levels(side : str) -> List[DepthLevel]:
        return [
            DepthLevel(
                price = float(level.get("price", 0.0)),
                quantity = int(level.get("quantity", 0) or 0),
                orders = level.get("orders")
            )
            for level in book.get(side, []) or []
        ]

    return MarketDepth(buy = levels("buy"), sell = levels("sell"))


def _asDatetime(value : Any) -> Optional[dt.datetime]:
    """
    Normalise a vendor timestamp, tolerating absence.
    """

    from algotraders.models.timezone import toIST

    if isinstance(value, dt.datetime):
        return toIST(value)

    return None


__all__ = [
    "KiteStream", "splitPackets", "parsePacket", "segmentOf",
    "priceDivisorFor", "PRICE_DIVISORS", "DEFAULT_PRICE_DIVISOR",
    "MODE_NAMES"
]
