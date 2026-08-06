# -*- encoding: utf-8 -*-

"""
Shipped Tick Sink Implementations
-----------------------------------

Two sinks cover local development and in-process consumption.
:class:`QueueTickSink` is the default and hands ticks to an
:class:`asyncio.Queue` the host reads from; :class:`CallbackTickSink`
suits a host that already has somewhere to put them.

Both are bounded, and the overflow policy is the interesting part.
Blocking a market data feed is worse than losing ticks: the backpressure
travels into the vendor's reader thread, the socket stops being drained,
and the broker eventually drops the connection, which costs every
instrument rather than one. Stale quotes have no value anyway, so the
default is to discard the oldest and count it.

The dropped counter is not decoration. It is the only signal that a
consumer is too slow, and a host should surface it as a metric rather
than discovering the gap later in its stored data.
"""

import asyncio

from typing import Awaitable, Callable, List, Optional

from algotraders.logger import getLogger
from algotraders.models.market import Tick
from algotraders.streaming.base import BaseTickSink

logger = getLogger(__name__)

DEFAULT_QUEUE_SIZE = 10_000

OVERFLOW_DROP_OLDEST = "dropOldest"
OVERFLOW_DROP_NEWEST = "dropNewest"
OVERFLOW_BLOCK = "block"

OVERFLOW_POLICIES = (
    OVERFLOW_DROP_OLDEST, OVERFLOW_DROP_NEWEST, OVERFLOW_BLOCK
)


class QueueTickSink(BaseTickSink):
    """
    Delivers ticks into a bounded :class:`asyncio.Queue`.

    :type  maxSize: int
    :param maxSize: Queue capacity. Bounded on purpose: an unbounded
        queue turns a slow consumer into unbounded memory growth, which
        fails later and far less obviously than a dropped tick.

    :type  overflow: str
    :param overflow: What to do when full. ``dropOldest`` keeps the
        freshest prices and is the default; ``dropNewest`` preserves
        arrival order; ``block`` applies real backpressure and should
        only be used when the consumer is guaranteed fast, because it
        can stall the socket.
    """

    def __init__(
        self, maxSize : int = DEFAULT_QUEUE_SIZE,
        overflow : str = OVERFLOW_DROP_OLDEST
    ) -> None:
        if overflow not in OVERFLOW_POLICIES:
            raise ValueError(
                f"overflow must be one of {OVERFLOW_POLICIES}, "
                f"got {overflow!r}"
            )

        self.queue : "asyncio.Queue[Optional[Tick]]" = asyncio.Queue(
            maxsize = maxSize
        )
        self.overflow = overflow
        self.droppedCount = 0
        self.emittedCount = 0


    async def emit(self, tick : Tick) -> None:
        """
        Queue a tick, applying the overflow policy when full.
        """

        if self.overflow == OVERFLOW_BLOCK:
            await self.queue.put(tick)
            self.emittedCount += 1

            return

        try:
            self.queue.put_nowait(tick)
            self.emittedCount += 1

            return
        except asyncio.QueueFull:
            pass

        if self.overflow == OVERFLOW_DROP_NEWEST:
            self.droppedCount += 1

            return

        # ? dropOldest: make room by discarding the stalest price, which
        # is the one least likely to matter to a live strategy
        try:
            self.queue.get_nowait()
            self.droppedCount += 1
        except asyncio.QueueEmpty:
            pass

        try:
            self.queue.put_nowait(tick)
            self.emittedCount += 1
        except asyncio.QueueFull:
            self.droppedCount += 1


    async def get(self) -> Optional[Tick]:
        """
        Await the next tick.

        :rtype:   Optional[Tick]
        :returns: The next tick, or ``None`` once the sink is closed,
            which lets a consumer loop terminate cleanly.
        """

        return await self.queue.get()


    async def drain(self) -> List[Tick]:
        """
        Take everything currently queued without waiting.

        Suits a consumer that batches writes to a database rather than
        handling ticks one at a time.

        :rtype:   List[Tick]
        :returns: Every tick available now, oldest first.
        """

        collected : List[Tick] = []

        while True:
            try:
                entry = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                return collected

            if entry is not None:
                collected.append(entry)


    async def close(self) -> None:
        """
        Signal end of stream by queueing a sentinel.

        A consumer blocked on :meth:`get` receives ``None`` and can exit
        rather than hanging forever on a feed that has stopped.
        """

        try:
            self.queue.put_nowait(None)
        except asyncio.QueueFull:
            logger.debug("queue full at close, consumer may be stalled")


    @property
    def depth(self) -> int:
        """
        Ticks currently waiting. A rising depth means the consumer is
        falling behind the feed.
        """

        return self.queue.qsize()


    def __repr__(self) -> str:
        return (
            f"QueueTickSink(depth={self.depth}, "
            f"emitted={self.emittedCount}, dropped={self.droppedCount})"
        )


class CallbackTickSink(BaseTickSink):
    """
    Delivers each tick to a caller supplied coroutine.

    Suits a host that already has a destination, for example writing
    into its own websocket fan-out or an in-memory last-price table.

    :type  callback: Callable[[Tick], Awaitable[None]]
    :param callback: Invoked once per tick. A callback that raises is
        logged and counted rather than propagated, because an exception
        escaping here would tear down the feed for every instrument over
        one bad update.
    """

    def __init__(
        self, callback : Callable[[Tick], Awaitable[None]],
        onClose : Optional[Callable[[], Awaitable[None]]] = None
    ) -> None:
        self.callback = callback
        self.onClose = onClose
        self.emittedCount = 0
        self.droppedCount = 0


    async def emit(self, tick : Tick) -> None:
        """
        Invoke the callback, absorbing any failure it raises.
        """

        try:
            await self.callback(tick)
            self.emittedCount += 1
        except Exception as error:
            self.droppedCount += 1

            logger.warning(
                "tick callback raised %s, continuing", type(error).__name__
            )


    async def close(self) -> None:
        """
        Invoke the optional close hook.
        """

        if self.onClose is not None:
            await self.onClose()


    def __repr__(self) -> str:
        return (
            f"CallbackTickSink(emitted={self.emittedCount}, "
            f"dropped={self.droppedCount})"
        )


__all__ = [
    "QueueTickSink", "CallbackTickSink", "DEFAULT_QUEUE_SIZE",
    "OVERFLOW_DROP_OLDEST", "OVERFLOW_DROP_NEWEST", "OVERFLOW_BLOCK",
    "OVERFLOW_POLICIES"
]
