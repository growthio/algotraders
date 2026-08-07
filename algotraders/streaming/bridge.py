# -*- encoding: utf-8 -*-

"""
Handing Ticks From a Vendor Thread Into the Event Loop
---------------------------------------------------------

Both broker websocket SDKs run their reader on a thread they own and
invoke a callback from it. Nothing in :mod:`asyncio` is thread safe
except :meth:`asyncio.loop.call_soon_threadsafe`, so that callback is
the one place in this library where the threading model actually
matters, and it is where a naive implementation quietly falls apart.

Three decisions carry this module.

**One wakeup per batch, not per tick.** The obvious implementation calls
``asyncio.run_coroutine_threadsafe(sink.emit(tick), loop)`` per tick,
which allocates a :class:`concurrent.futures.Future` and schedules a
callback for every single update. At Kite's 3000 instrument ceiling in
full mode that is thousands of futures a second, and the loop spends
more time on scheduling overhead than on the ticks. Instead the tick
goes onto a thread safe :class:`queue.SimpleQueue` and the loop is woken
only when a wakeup is not already pending.

**Failure on the vendor thread is swallowed, never raised.** An
exception escaping a vendor callback typically kills the reader thread,
and the socket then goes silent with no error surfaced anywhere. Dropping
a tick and counting it is strictly better than losing the feed.

**Backpressure drops rather than blocks.** Blocking the consumer pushes
back into the vendor thread, which stalls the socket and eventually gets
the connection dropped by the broker. Stale market data has no value, so
the bounded sink discards the oldest and reports the count.
"""

import queue
import asyncio
import threading

from typing import Any, Optional

from algotraders.logger import getLogger
from algotraders.models.market import Tick
from algotraders.streaming.base import BaseTickSink

logger = getLogger(__name__)

# ? ticks drained per pass before yielding, so one very busy instrument
# cannot starve the rest of the loop
DEFAULT_DRAIN_BATCH = 512


class ThreadLoopBridge:
    """
    Moves ticks from a vendor SDK thread onto the event loop.

    :type  loop: asyncio.AbstractEventLoop
    :param loop: The loop the consumer runs on. Captured explicitly
        because :func:`asyncio.get_running_loop` cannot be called from
        the vendor thread.

    :type  sink: BaseTickSink
    :param sink: Destination for normalized ticks.

    :type  drainBatch: int
    :param drainBatch: Ticks handled per pass before yielding control.
    """

    def __init__(
        self, loop : asyncio.AbstractEventLoop, sink : BaseTickSink,
        drainBatch : int = DEFAULT_DRAIN_BATCH,
        onError : Optional[Any] = None
    ) -> None:
        self.loop = loop
        self.sink = sink
        self.drainBatch = drainBatch
        self.onError = onError

        # ? SimpleQueue is lock free for the producer and safe to call
        # from any thread, unlike every asyncio primitive
        self.pending : "queue.SimpleQueue[Tick]" = queue.SimpleQueue()

        self.wakeup = asyncio.Event()
        self.droppedCount = 0
        self.wakeupCount = 0
        self.deliveredCount = 0

        self.__guard__ = threading.Lock()
        self.__wakePending__ = False
        self.__running__ = False


    def submit(self, tick : Tick) -> None:
        """
        Accept a tick from the vendor's reader thread.

        ! Called on a thread this library does not own. It must never
        raise, and it must not touch any asyncio object other than
        :meth:`call_soon_threadsafe`.

        :type  tick: Tick
        :param tick: The normalized tick to deliver.
        """

        self.pending.put(tick)

        with self.__guard__:
            if self.__wakePending__:
                # ? the loop already owes us a drain, so this tick will
                # be collected by it; no second wakeup is needed
                return

            self.__wakePending__ = True

        try:
            self.loop.call_soon_threadsafe(self.__wake__)
            self.wakeupCount += 1
        except RuntimeError:
            # ! the loop has closed, which happens routinely during
            # shutdown while the vendor thread is still running. Raising
            # here would kill the reader thread and hide the real cause
            with self.__guard__:
                self.__wakePending__ = False

            self.droppedCount += 1


    def __wake__(self) -> None:
        """
        Signal the consumer that ticks are waiting. Runs on the loop.
        """

        with self.__guard__:
            self.__wakePending__ = False

        self.wakeup.set()


    async def run(self) -> None:
        """
        Consume ticks until :meth:`stop` is called.

        Intended to be launched as a task alongside the stream and
        cancelled with it.
        """

        self.__running__ = True

        while self.__running__:
            await self.wakeup.wait()
            self.wakeup.clear()

            await self.__drain__()

        # ? one final pass so ticks queued during shutdown are not lost
        await self.__drain__()


    async def __drain__(self) -> None:
        """
        Deliver everything currently queued, yielding periodically.
        """

        handled = 0

        while True:
            try:
                tick = self.pending.get_nowait()
            except queue.Empty:
                return

            try:
                await self.sink.emit(tick)
                self.deliveredCount += 1
            except Exception as error:
                self.droppedCount += 1

                logger.warning(
                    "tick sink raised %s, dropping tick",
                    type(error).__name__
                )

                if self.onError is not None:
                    self.onError(error)

            handled += 1

            if handled >= self.drainBatch:
                # ? give the loop a chance to run other tasks before
                # continuing, so one busy feed cannot starve the process
                await asyncio.sleep(0)

                handled = 0


    async def stop(self) -> None:
        """
        Stop consuming and flush whatever is still queued.
        """

        self.__running__ = False
        self.wakeup.set()


    @property
    def queueDepth(self) -> int:
        """
        Approximate number of ticks waiting to be delivered.

        A growing depth means the consumer is falling behind the feed,
        which is the signal to widen the sink or narrow the
        subscription.
        """

        return self.pending.qsize()


    def __repr__(self) -> str:
        return (
            f"ThreadLoopBridge(delivered={self.deliveredCount}, "
            f"dropped={self.droppedCount}, wakeups={self.wakeupCount})"
        )


__all__ = ["ThreadLoopBridge", "DEFAULT_DRAIN_BATCH"]
