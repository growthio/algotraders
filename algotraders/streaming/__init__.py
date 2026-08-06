# -*- encoding: utf-8 -*-

"""
Live Market Data Streaming
============================

Normalizes broker websocket feeds into :class:`~algotraders.models.market.Tick`
objects and delivers them to a pluggable sink.

The layer exists because both vendor SDKs run their socket reader on a
thread they own, and everything in :mod:`asyncio` except
``call_soon_threadsafe`` is unsafe to touch from there.
:class:`ThreadLoopBridge` is the single crossing point, and getting it
wrong is not subtle: a per-tick future allocation melts under Kite's
3000 instrument ceiling, and an exception escaping a vendor callback
kills the reader thread and silences the feed with no error anywhere.

Sinks are the seam for the host. Ship a Redis or Kafka implementation by
subclassing :class:`BaseTickSink`; it is two methods.
"""

from algotraders.streaming.base import (
    BaseTickSink, BaseBrokerStream,
    DEFAULT_STALE_AFTER, DEFAULT_RECONNECT_DELAY, MAXIMUM_RECONNECT_DELAY
)

from algotraders.streaming.sinks import (
    QueueTickSink, CallbackTickSink, DEFAULT_QUEUE_SIZE,
    OVERFLOW_DROP_OLDEST, OVERFLOW_DROP_NEWEST, OVERFLOW_BLOCK,
    OVERFLOW_POLICIES
)

from algotraders.streaming.bridge import (
    ThreadLoopBridge, DEFAULT_DRAIN_BATCH
)

__all__ = [
    "BaseTickSink", "BaseBrokerStream",
    "DEFAULT_STALE_AFTER", "DEFAULT_RECONNECT_DELAY",
    "MAXIMUM_RECONNECT_DELAY",

    "QueueTickSink", "CallbackTickSink", "DEFAULT_QUEUE_SIZE",
    "OVERFLOW_DROP_OLDEST", "OVERFLOW_DROP_NEWEST", "OVERFLOW_BLOCK",
    "OVERFLOW_POLICIES",

    "ThreadLoopBridge", "DEFAULT_DRAIN_BATCH"
]
