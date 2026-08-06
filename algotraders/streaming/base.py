# -*- encoding: utf-8 -*-

"""
Streaming Contracts: Tick Sinks and Broker Sockets
-----------------------------------------------------

Two abstractions. :class:`BaseTickSink` is where normalized ticks go,
and :class:`BaseBrokerStream` is the broker side that produces them.
Keeping them apart is what lets the host choose its own delivery without
the broker implementation knowing anything about it: an in process
queue during development, a Redis stream or a Kafka topic in production,
implemented against three methods.

:class:`BaseBrokerStream` deliberately owns a supervisor rather than
trusting the vendor's reconnect alone. Both SDKs reconnect on a dropped
socket, but neither notices the two failure modes that actually bite in
production: a socket that stays open while silently delivering nothing,
and a session whose token expired at the daily rollover. The first needs
a staleness timer, the second needs the error surfaced instead of an
infinite reconnect loop against a credential that will never work again.
"""

import asyncio
import datetime as dt

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Sequence

from algotraders.core.clock import BaseClock
from algotraders.errors import SubscriptionLimitExceeded, TokenExpiredError
from algotraders.logger import getLogger
from algotraders.models.enums import StreamMode
from algotraders.models.instrument import Instrument
from algotraders.models.market import Tick

logger = getLogger(__name__)

# ? how long a feed may stay silent during market hours before the
# supervisor concludes the socket is dead despite appearing connected
DEFAULT_STALE_AFTER = dt.timedelta(seconds = 60)

DEFAULT_RECONNECT_DELAY = 2.0
MAXIMUM_RECONNECT_DELAY = 60.0


class BaseTickSink(ABC):
    """
    Destination for normalized market data ticks.

    Three methods, so a host implements one over Redis, Kafka or its own
    websocket fan-out without touching any broker code.
    """

    @abstractmethod
    async def emit(self, tick : Tick) -> None:
        """
        Accept one tick.

        Implementations must not block indefinitely. The caller is the
        bridge draining a vendor thread's queue, and stalling here
        pushes backpressure into the socket reader, which the broker
        eventually resolves by dropping the connection.
        """

        pass


    @abstractmethod
    async def close(self) -> None:
        """
        Release any resources the sink holds.
        """

        pass


class BaseBrokerStream(ABC):
    """
    A broker's live market data socket.

    Concrete implementations supply four things: how to connect, how to
    disconnect, how to send a subscription, and how to translate a
    vendor message into a :class:`~algotraders.models.market.Tick`.
    Reconnection, staleness detection and subscription replay are shared
    here.

    :type  sink: BaseTickSink
    :param sink: Where ticks are delivered.

    :type  resolver: Any
    :param resolver: Instrument resolver, used to turn the broker's own
        symbol or numeric token in an inbound message back into a
        canonical key.

    :type  staleAfter: dt.timedelta
    :param staleAfter: Silence during market hours beyond which the
        socket is treated as dead and restarted.

    :type  onError: Optional[Callable[[BaseException], None]]
    :param onError: Notified of failures the stream cannot recover from,
        principally token expiry.
    """

    brokerName : str = "unknown"
    maxInstrumentsPerConnection : int = 1_000

    def __init__(
        self, sessionManager : Any, sink : BaseTickSink, clock : BaseClock,
        resolver : Any = None,
        loop : Optional[asyncio.AbstractEventLoop] = None,
        staleAfter : dt.timedelta = DEFAULT_STALE_AFTER,
        onError : Optional[Callable[[BaseException], None]] = None,
        **extra : Any
    ) -> None:
        self.sessionManager = sessionManager
        self.sink = sink
        self.clock = clock
        self.resolver = resolver
        self.loop = loop
        self.staleAfter = staleAfter
        self.onError = onError

        self.subscriptions : Dict[str, StreamMode] = {}
        self.lastTickAt : Optional[dt.datetime] = None
        self.isConnected = False
        self.reconnectCount = 0

        self.__supervisor__ : Optional[asyncio.Task] = None
        self.__stopping__ = False


    @abstractmethod
    async def connect(self) -> None:
        """
        Open the vendor socket. Must be idempotent.
        """

        pass


    @abstractmethod
    async def disconnect(self) -> None:
        """
        Close the vendor socket, tolerating an already closed one.
        """

        pass


    @abstractmethod
    async def sendSubscription(
        self, symbols : Sequence[str], mode : StreamMode, subscribe : bool
    ) -> None:
        """
        Issue a subscribe or unsubscribe to the vendor socket.

        :type  subscribe: bool
        :param subscribe: ``True`` to subscribe, ``False`` to remove.
        """

        pass


    async def start(self) -> None:
        """
        Connect and begin supervising the feed.
        """

        self.__stopping__ = False

        await self.connect()

        if self.__supervisor__ is None:
            self.__supervisor__ = asyncio.create_task(self.__supervise__())


    async def stop(self) -> None:
        """
        Stop supervising and close the socket.
        """

        self.__stopping__ = True

        if self.__supervisor__ is not None:
            self.__supervisor__.cancel()

            try:
                await self.__supervisor__
            except asyncio.CancelledError:
                pass

            self.__supervisor__ = None

        await self.disconnect()
        await self.sink.close()


    async def subscribe(
        self, instruments : Sequence[Instrument],
        mode : StreamMode = StreamMode.QUOTE
    ) -> None:
        """
        Subscribe to live updates for the given instruments.

        :raises SubscriptionLimitExceeded: The request would exceed the
            broker's per connection ceiling. Enforced locally, because
            the vendor silently drops the overflow and leaves the caller
            believing it is receiving ticks that never arrive.
        """

        symbols = [instrument.brokerSymbol for instrument in instruments]
        total = len(set(self.subscriptions) | set(symbols))

        if total > self.maxInstrumentsPerConnection:
            raise SubscriptionLimitExceeded(
                brokerName = self.brokerName,
                limit = self.maxInstrumentsPerConnection,
                requested = total
            )

        await self.sendSubscription(symbols, mode, subscribe = True)

        for symbol in symbols:
            self.subscriptions[symbol] = mode

        logger.info(
            "%s subscribed to %d instrument(s) in %s mode",
            self.brokerName, len(symbols), mode.value
        )


    async def unsubscribe(
        self, instruments : Sequence[Instrument]
    ) -> None:
        """
        Remove instruments from the live subscription.
        """

        symbols = [instrument.brokerSymbol for instrument in instruments]

        await self.sendSubscription(
            symbols, StreamMode.QUOTE, subscribe = False
        )

        for symbol in symbols:
            self.subscriptions.pop(symbol, None)


    async def __supervise__(self) -> None:
        """
        Restart the socket when it goes silent during market hours.

        The vendor SDKs handle a *dropped* socket. They do not handle a
        socket that remains open while delivering nothing, which is the
        failure that leaves a strategy trading on a frozen price.
        """

        delay = DEFAULT_RECONNECT_DELAY

        while not self.__stopping__:
            await self.clock.sleep(self.staleAfter.total_seconds())

            if self.__stopping__:
                return

            if not self.clock.isMarketOpen():
                # ? silence outside market hours is expected, not a fault
                continue

            if not self.__isStale__():
                delay = DEFAULT_RECONNECT_DELAY

                continue

            logger.warning(
                "%s feed silent for over %s during market hours, "
                "restarting", self.brokerName, self.staleAfter
            )

            try:
                await self.__restart__()

                delay = DEFAULT_RECONNECT_DELAY
            except TokenExpiredError as error:
                # ! a dead session will never reconnect, so surface it
                # instead of looping forever against a bad credential
                logger.error(
                    "%s session expired, stopping the feed",
                    self.brokerName
                )

                if self.onError is not None:
                    self.onError(error)

                self.__stopping__ = True

                return
            except Exception as error:
                logger.warning(
                    "%s reconnect failed (%s), retrying in %.1fs",
                    self.brokerName, type(error).__name__, delay
                )

                await self.clock.sleep(delay)

                delay = min(MAXIMUM_RECONNECT_DELAY, delay * 2)


    def __isStale__(self) -> bool:
        """
        Whether the feed has been silent longer than permitted.
        """

        if self.lastTickAt is None:
            return True

        return (self.clock.now() - self.lastTickAt) > self.staleAfter


    async def __restart__(self) -> None:
        """
        Reconnect and replay the exact prior subscription set.

        Replay is the part that is easy to forget: a reconnected socket
        with no subscriptions looks perfectly healthy and delivers
        nothing at all.
        """

        self.reconnectCount += 1

        await self.disconnect()
        await self.connect()

        byMode : Dict[StreamMode, List[str]] = {}

        for symbol, mode in self.subscriptions.items():
            byMode.setdefault(mode, []).append(symbol)

        for mode, symbols in byMode.items():
            await self.sendSubscription(symbols, mode, subscribe = True)

        logger.info(
            "%s feed restarted, replayed %d subscription(s)",
            self.brokerName, len(self.subscriptions)
        )


    def recordTick(self, tick : Tick) -> None:
        """
        Note that a tick arrived, resetting the staleness timer.

        Called by the concrete implementation for every delivered tick.
        """

        self.lastTickAt = tick.receivedAt


    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(broker={self.brokerName!r}, "
            f"connected={self.isConnected}, "
            f"subscriptions={len(self.subscriptions)})"
        )


__all__ = [
    "BaseTickSink", "BaseBrokerStream",
    "DEFAULT_STALE_AFTER", "DEFAULT_RECONNECT_DELAY",
    "MAXIMUM_RECONNECT_DELAY"
]
