# -*- encoding: utf-8 -*-

"""
Fyers Data Websocket Integration
----------------------------------

Wraps ``fyers_apiv3.FyersWebsocket.data_ws.FyersDataSocket``. The SDK
takes callbacks and runs its reader on its own thread, so every message
crosses into the event loop through
:class:`~algotraders.streaming.bridge.ThreadLoopBridge`.

The access token for the socket is formatted ``appId:accessToken``,
which is different from the REST client's, and getting it wrong produces
a connection that opens and then closes with no useful diagnostic.

:NOTE: Roughly 200 symbols per connection. The cap is enforced locally
    by :meth:`BaseBrokerStream.subscribe`, because the vendor silently
    discards the overflow and the caller would otherwise sit waiting for
    ticks that are never coming.
"""

import asyncio
import datetime as dt

from typing import Any, Dict, Optional, Sequence

from fyers_apiv3.FyersWebsocket import data_ws

from algotraders.brokers.fyers import limits
from algotraders.config import configureVendorLogging
from algotraders.core.clock import BaseClock
from algotraders.logger import getLogger
from algotraders.models.enums import StreamMode
from algotraders.models.market import DepthLevel, MarketDepth, Tick
from algotraders.streaming.base import BaseBrokerStream, BaseTickSink
from algotraders.streaming.bridge import ThreadLoopBridge

logger = getLogger(__name__)

# ? the vendor's own names for what it will push
DATA_TYPE_SYMBOL = "SymbolUpdate"
DATA_TYPE_DEPTH = "DepthUpdate"

MODE_DATA_TYPE : Dict[StreamMode, str] = {
    StreamMode.LTP : DATA_TYPE_SYMBOL,
    StreamMode.QUOTE : DATA_TYPE_SYMBOL,
    StreamMode.FULL : DATA_TYPE_DEPTH
}


class FyersStream(BaseBrokerStream):
    """
    Live market data over the Fyers data websocket.

    :type  sessionManager: Any
    :param sessionManager: Unused for the socket, which authenticates
        with its own token format, but accepted so every broker surface
        is constructed identically.

    :type  accessToken: str
    :param accessToken: Already formatted as ``appId:accessToken``.
    """

    brokerName = limits.BROKER_NAME
    maxInstrumentsPerConnection = limits.MAX_STREAM_SYMBOLS

    def __init__(
        self, sessionManager : Any, sink : BaseTickSink, clock : BaseClock,
        accessToken : str = "", resolver : Any = None,
        loop : Optional[asyncio.AbstractEventLoop] = None,
        socketFactory : Optional[Any] = None, **extra : Any
    ) -> None:
        super().__init__(
            sessionManager = sessionManager, sink = sink, clock = clock,
            resolver = resolver, loop = loop, **extra
        )

        self.accessToken = accessToken
        self.socketFactory = socketFactory or data_ws.FyersDataSocket
        self.socket : Any = None
        self.bridge : Optional[ThreadLoopBridge] = None

        self.__consumer__ : Optional[asyncio.Task] = None


    async def connect(self) -> None:
        """
        Open the data socket and start the tick consumer.

        The socket's own reconnect is enabled as well as the supervisor
        in the base class: the vendor handles a dropped connection,
        while the supervisor handles the harder case of a connection
        that stays open and stops delivering.
        """

        if self.isConnected:
            return

        loop = self.loop or asyncio.get_running_loop()
        self.loop = loop

        self.bridge = ThreadLoopBridge(
            loop = loop, sink = self.sink, onError = self.onError
        )
        self.__consumer__ = asyncio.create_task(self.bridge.run())

        self.socket = self.socketFactory(
            access_token = self.accessToken,
            log_path = configureVendorLogging(),
            litemode = False,
            write_to_file = False,
            reconnect = True,
            on_connect = self.__onOpen__,
            on_close = self.__onClose__,
            on_error = self.__onError__,
            on_message = self.__onMessage__
        )

        # ? connect() blocks while the handshake completes, so it goes
        # off the loop like every other blocking vendor call
        await asyncio.to_thread(self.socket.connect)

        self.isConnected = True

        logger.info("fyers data socket connected")


    async def disconnect(self) -> None:
        """
        Close the socket and stop the consumer, tolerating a socket that
        is already gone.
        """

        self.isConnected = False

        if self.socket is not None:
            try:
                await asyncio.to_thread(self.socket.close_connection)
            except Exception as error:
                logger.debug(
                    "fyers socket close raised %s", type(error).__name__
                )

            self.socket = None

        if self.bridge is not None:
            await self.bridge.stop()

        if self.__consumer__ is not None:
            self.__consumer__.cancel()

            try:
                await self.__consumer__
            except asyncio.CancelledError:
                pass

            self.__consumer__ = None


    async def sendSubscription(
        self, symbols : Sequence[str], mode : StreamMode, subscribe : bool
    ) -> None:
        """
        Subscribe or unsubscribe on the vendor socket.
        """

        if self.socket is None:
            raise RuntimeError("fyers socket is not connected")

        dataType = MODE_DATA_TYPE.get(mode, DATA_TYPE_SYMBOL)
        action = self.socket.subscribe if subscribe else (
            self.socket.unsubscribe
        )

        await asyncio.to_thread(
            action, symbols = list(symbols), data_type = dataType
        )


    def __onOpen__(self) -> None:
        """
        Vendor callback, on its own thread, when the socket opens.
        """

        logger.debug("fyers socket open")


    def __onClose__(self, message : Any = None) -> None:
        """
        Vendor callback, on its own thread, when the socket closes.
        """

        self.isConnected = False

        logger.info("fyers socket closed: %s", message)


    def __onError__(self, message : Any = None) -> None:
        """
        Vendor callback, on its own thread, for a socket error.
        """

        logger.warning("fyers socket error: %s", message)


    def __onMessage__(self, message : Any) -> None:
        """
        Vendor callback delivering one update, on the reader thread.

        ! Nothing here may raise. An exception escaping a vendor callback
        typically kills the reader thread, after which the socket goes
        silent with no error surfaced anywhere.
        """

        try:
            tick = self.toTick(message)

            if tick is None:
                return

            self.recordTick(tick)

            if self.bridge is not None:
                self.bridge.submit(tick)
        except Exception as error:
            logger.warning(
                "discarding unparseable fyers tick: %s", type(error).__name__
            )


    def toTick(self, message : Any) -> Optional[Tick]:
        """
        Convert a vendor socket message into a normalized tick.

        :type  message: Any
        :param message: Whatever the SDK handed the callback.

        :rtype:   Optional[Tick]
        :returns: The tick, or ``None`` for a message carrying no price,
            such as a connection acknowledgement.
        """

        if not isinstance(message, dict):
            return None

        symbol = message.get("symbol") or message.get("s")
        price = message.get("ltp") or message.get("lp")

        if not symbol or price is None:
            return None

        received = self.clock.now()
        exchangeTime = _exchangeTimestamp(message)

        return Tick(
            instrumentKey = str(symbol),
            lastPrice = float(price),
            lastQuantity = _optionalInt(message.get("last_traded_qty")),
            averagePrice = _optionalFloat(message.get("avg_trade_price")),
            volume = _optionalInt(message.get("vol_traded_today")),
            buyQuantity = _optionalInt(message.get("tot_buy_qty")),
            sellQuantity = _optionalInt(message.get("tot_sell_qty")),
            dayOpen = _optionalFloat(message.get("open_price")),
            dayHigh = _optionalFloat(message.get("high_price")),
            dayLow = _optionalFloat(message.get("low_price")),
            dayClose = _optionalFloat(message.get("prev_close_price")),
            openInterest = _optionalInt(message.get("oi")),
            exchangeTimestamp = exchangeTime,
            receivedAt = received,
            mode = (
                StreamMode.FULL if message.get("bids") else StreamMode.QUOTE
            ),
            depth = _depthFrom(message),
            source = limits.BROKER_NAME
        )


def _exchangeTimestamp(message : Dict[str, Any]) -> Optional[dt.datetime]:
    """
    Read the exchange timestamp, which arrives as epoch seconds.
    """

    from algotraders.models.timezone import fromEpoch

    value = message.get("exch_feed_time") or message.get("last_traded_time")

    if not value:
        return None

    try:
        return fromEpoch(float(value))
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _depthFrom(message : Dict[str, Any]) -> Optional[MarketDepth]:
    """
    Build the order book from a depth update, when one is present.
    """

    bids = message.get("bids")
    asks = message.get("ask") or message.get("asks")

    if not bids and not asks:
        return None

    def levels(entries : Any) -> list:
        return [
            DepthLevel(
                price = float(entry.get("price", 0.0)),
                quantity = int(entry.get("volume", 0) or 0),
                orders = _optionalInt(entry.get("ord"))
            )
            for entry in (entries or [])
            if isinstance(entry, dict)
        ]

    return MarketDepth(buy = levels(bids), sell = levels(asks))


def _optionalFloat(value : Any) -> Optional[float]:
    """
    Coerce a vendor value to ``float``, tolerating absence and junk.
    """

    if value is None or value == "":
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optionalInt(value : Any) -> Optional[int]:
    """
    Coerce a vendor value to ``int``, tolerating absence and junk.
    """

    if value is None or value == "":
        return None

    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


__all__ = ["FyersStream", "MODE_DATA_TYPE"]
