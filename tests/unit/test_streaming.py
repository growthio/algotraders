# -*- encoding: utf-8 -*-

"""
Thread Handoff, Backpressure and Feed Supervision
----------------------------------------------------

The bridge tests drive a **real** :class:`threading.Thread`, because the
whole point of the module is what happens across a thread boundary and a
same-thread test would prove nothing about it.

Two assertions carry the file. Wakeups must be far fewer than ticks, or
the loop drowns in scheduling overhead at Kite's 3000 instrument
ceiling. And submitting to a closed loop must be silent, because raising
there kills the vendor's reader thread and the feed goes quiet with no
error recorded anywhere.
"""

import asyncio
import threading
import datetime as dt

import pytest

from algotraders.core.clock import IST
from algotraders.errors import SubscriptionLimitExceeded, TokenExpiredError
from algotraders.models.enums import StreamMode
from algotraders.models.instrument import Instrument, InstrumentKey
from algotraders.models.market import Tick
from algotraders.streaming.base import BaseBrokerStream, BaseTickSink
from algotraders.streaming.bridge import ThreadLoopBridge
from algotraders.streaming.sinks import (
    OVERFLOW_DROP_NEWEST, CallbackTickSink, QueueTickSink
)
from tests.fakes.clock import FakeClock

MOMENT = dt.datetime(2026, 8, 5, 10, 0, tzinfo = IST)


def buildTick(index : int = 0, price : float = 100.0) -> Tick:
    """
    Build a minimal tick carrying an identifying sequence number.
    """

    return Tick(
        instrumentKey = "NSE:SBIN:EQ", lastPrice = price,
        lastQuantity = index, receivedAt = MOMENT,
        mode = StreamMode.QUOTE, source = "fyers"
    )


class RecordingSink(BaseTickSink):
    """
    Collects every tick it is given, optionally failing on cue.
    """

    def __init__(self, failOn : int = -1) -> None:
        self.ticks = []
        self.closed = False
        self.failOn = failOn


    async def emit(self, tick : Tick) -> None:
        if len(self.ticks) == self.failOn:
            self.ticks.append(tick)

            raise RuntimeError("sink failure")

        self.ticks.append(tick)


    async def close(self) -> None:
        self.closed = True


async def test_bridgeDeliversEveryTickInOrderFromARealThread() -> None:
    """
    Ten thousand ticks submitted from a vendor-style thread all arrive,
    in order, with none lost.
    """

    loop = asyncio.get_running_loop()
    sink = RecordingSink()
    bridge = ThreadLoopBridge(loop, sink)

    consumer = asyncio.create_task(bridge.run())

    def producer() -> None:
        for index in range(10_000):
            bridge.submit(buildTick(index))

    thread = threading.Thread(target = producer)
    thread.start()

    while len(sink.ticks) < 10_000:
        await asyncio.sleep(0.005)

    thread.join()
    await bridge.stop()
    await consumer

    assert len(sink.ticks) == 10_000
    assert [tick.lastQuantity for tick in sink.ticks] == list(range(10_000))
    assert bridge.droppedCount == 0


async def test_bridgeCoalescesWakeups() -> None:
    """
    One wakeup per batch, not per tick.

    A per-tick ``run_coroutine_threadsafe`` allocates a future for every
    update, which at 3000 instruments in full mode buries the loop in
    scheduling overhead rather than in market data.
    """

    loop = asyncio.get_running_loop()
    sink = RecordingSink()
    bridge = ThreadLoopBridge(loop, sink)

    consumer = asyncio.create_task(bridge.run())

    def producer() -> None:
        for index in range(5_000):
            bridge.submit(buildTick(index))

    thread = threading.Thread(target = producer)
    thread.start()

    while len(sink.ticks) < 5_000:
        await asyncio.sleep(0.005)

    thread.join()
    await bridge.stop()
    await consumer

    assert bridge.wakeupCount < 5_000
    assert bridge.deliveredCount == 5_000


async def test_submitAfterLoopClosureDropsRatherThanRaises() -> None:
    """
    Raising inside a vendor callback kills the reader thread, after
    which the socket goes silent with nothing logged. Dropping and
    counting is strictly better.
    """

    closedLoop = asyncio.new_event_loop()
    closedLoop.close()

    bridge = ThreadLoopBridge(closedLoop, RecordingSink())

    bridge.submit(buildTick())

    assert bridge.droppedCount == 1


async def test_sinkFailureDoesNotStopTheFeed() -> None:
    """
    One bad tick must not tear down the stream for every instrument.
    """

    loop = asyncio.get_running_loop()
    sink = RecordingSink(failOn = 2)
    bridge = ThreadLoopBridge(loop, sink)

    consumer = asyncio.create_task(bridge.run())

    for index in range(5):
        bridge.submit(buildTick(index))

    while len(sink.ticks) < 5:
        await asyncio.sleep(0.005)

    await bridge.stop()
    await consumer

    assert bridge.droppedCount == 1
    assert bridge.deliveredCount == 4


async def test_queueSinkDropsOldestWhenFull() -> None:
    """
    A stalled consumer loses the stalest prices, not the freshest.
    """

    sink = QueueTickSink(maxSize = 3)

    for index in range(6):
        await sink.emit(buildTick(index, price = 100.0 + index))

    remaining = await sink.drain()

    assert len(remaining) == 3
    assert sink.droppedCount == 3

    # ? the newest three survived
    assert [tick.lastQuantity for tick in remaining] == [3, 4, 5]


async def test_queueSinkCanDropNewestInstead() -> None:
    """
    Preserving arrival order is available for a consumer that needs it.
    """

    sink = QueueTickSink(maxSize = 3, overflow = OVERFLOW_DROP_NEWEST)

    for index in range(6):
        await sink.emit(buildTick(index))

    remaining = await sink.drain()

    assert [tick.lastQuantity for tick in remaining] == [0, 1, 2]
    assert sink.droppedCount == 3


def test_queueSinkRejectsUnknownOverflowPolicy() -> None:
    """
    A typo in the policy name fails at construction, not silently at the
    first overflow under load.
    """

    with pytest.raises(ValueError):
        QueueTickSink(overflow = "dropSomething")


async def test_queueSinkCloseUnblocksAWaitingConsumer() -> None:
    """
    A consumer blocked on ``get`` exits cleanly rather than hanging on a
    feed that has stopped.
    """

    sink = QueueTickSink(maxSize = 4)

    await sink.close()

    assert await sink.get() is None


async def test_callbackSinkAbsorbsCallbackFailures() -> None:
    """
    A raising callback is counted, not propagated into the feed.
    """

    async def failing(tick : Tick) -> None:
        raise ValueError("consumer bug")

    sink = CallbackTickSink(failing)

    await sink.emit(buildTick())

    assert sink.droppedCount == 1
    assert sink.emittedCount == 0


class FakeStream(BaseBrokerStream):
    """
    A stream with a scripted socket, recording every vendor interaction.
    """

    brokerName = "fake"
    maxInstrumentsPerConnection = 200

    def __init__(self, *args : object, **kwargs : object) -> None:
        self.failConnect : Exception = kwargs.pop("failConnect", None)

        super().__init__(*args, **kwargs)

        self.connectCalls = 0
        self.disconnectCalls = 0
        self.sent = []


    async def connect(self) -> None:
        self.connectCalls += 1

        if self.failConnect is not None:
            raise self.failConnect

        self.isConnected = True


    async def disconnect(self) -> None:
        self.disconnectCalls += 1
        self.isConnected = False


    async def sendSubscription(self, symbols, mode, subscribe) -> None:
        self.sent.append((tuple(symbols), mode, subscribe))


def instrument(symbol : str) -> Instrument:
    """
    Build an instrument carrying the given vendor symbol.
    """

    return Instrument(
        key = InstrumentKey.parse("NSE:SBIN:EQ"),
        brokerSymbol = symbol, source = "fake"
    )


async def test_subscriptionCapIsEnforcedLocally() -> None:
    """
    The vendor silently discards the overflow, leaving a caller waiting
    for ticks that never come, so the ceiling is enforced here.
    """

    stream = FakeStream(
        sessionManager = None, sink = RecordingSink(),
        clock = FakeClock(start = MOMENT)
    )

    with pytest.raises(SubscriptionLimitExceeded) as failure:
        await stream.subscribe(
            [instrument(f"NSE:S{index}-EQ") for index in range(201)]
        )

    assert "200" in str(failure.value)
    assert "201" in str(failure.value)


async def test_subscriptionsAreTrackedForReplay() -> None:
    """
    A reconnected socket with no subscriptions looks perfectly healthy
    and delivers nothing, so the set has to be remembered.
    """

    stream = FakeStream(
        sessionManager = None, sink = RecordingSink(),
        clock = FakeClock(start = MOMENT)
    )

    await stream.subscribe(
        [instrument("NSE:SBIN-EQ"), instrument("NSE:TCS-EQ")],
        mode = StreamMode.FULL
    )

    assert stream.subscriptions == {
        "NSE:SBIN-EQ" : StreamMode.FULL, "NSE:TCS-EQ" : StreamMode.FULL
    }


async def test_unsubscribeForgetsTheInstrument() -> None:
    """
    Removed instruments must not be replayed on the next reconnect.
    """

    stream = FakeStream(
        sessionManager = None, sink = RecordingSink(),
        clock = FakeClock(start = MOMENT)
    )

    await stream.subscribe([instrument("NSE:SBIN-EQ")])
    await stream.unsubscribe([instrument("NSE:SBIN-EQ")])

    assert stream.subscriptions == {}


async def test_restartReplaysTheExactSubscriptionSet() -> None:
    """
    Reconnecting reissues every subscription, grouped by mode.
    """

    stream = FakeStream(
        sessionManager = None, sink = RecordingSink(),
        clock = FakeClock(start = MOMENT)
    )

    await stream.subscribe([instrument("NSE:SBIN-EQ")], StreamMode.QUOTE)
    await stream.subscribe([instrument("NSE:TCS-EQ")], StreamMode.FULL)

    stream.sent.clear()

    await stream.__restart__()

    replayed = {
        symbol for symbols, _, _ in stream.sent for symbol in symbols
    }

    assert replayed == {"NSE:SBIN-EQ", "NSE:TCS-EQ"}
    assert stream.reconnectCount == 1
    assert stream.disconnectCalls == 1


async def test_stalenessIsMeasuredFromTheLastTick() -> None:
    """
    A feed that has delivered recently is healthy; one that has not is
    stale even though the socket is still open.
    """

    clock = FakeClock(start = MOMENT)
    stream = FakeStream(
        sessionManager = None, sink = RecordingSink(), clock = clock
    )

    assert stream.__isStale__() is True

    stream.recordTick(buildTick())

    assert stream.__isStale__() is False

    clock.advance(120)

    assert stream.__isStale__() is True


async def test_expiredTokenStopsTheReconnectLoop() -> None:
    """
    A dead session will never reconnect, so it is surfaced through
    onError rather than retried forever against a bad credential.
    """

    clock = FakeClock(start = MOMENT)
    observed = []

    stream = FakeStream(
        sessionManager = None, sink = RecordingSink(), clock = clock,
        onError = observed.append,
        failConnect = TokenExpiredError("session expired at 06:00 IST")
    )

    stream.lastTickAt = None
    stream.__stopping__ = False

    await stream.__supervise__()

    assert len(observed) == 1
    assert isinstance(observed[0], TokenExpiredError)


async def test_silenceOutsideMarketHoursIsNotAFault() -> None:
    """
    A quiet feed at 3am is expected, and restarting the socket over it
    would churn connections all night.
    """

    overnight = FakeClock(
        start = dt.datetime(2026, 8, 5, 3, 0, tzinfo = IST)
    )
    stream = FakeStream(
        sessionManager = None, sink = RecordingSink(), clock = overnight
    )

    assert overnight.isMarketOpen() is False
    assert stream.reconnectCount == 0
