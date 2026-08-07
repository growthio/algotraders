# -*- encoding: utf-8 -*-

"""
Bridging Blocking Vendor SDKs Into the Event Loop
---------------------------------------------------

Both broker SDKs are synchronous. ``FyersModel.history()`` and
``KiteConnect.historical_data()`` block the calling thread on a network
round trip. The original implementation wrapped one of those in
``async with self.semaphore`` and declared the method ``async``, which
made the concurrency imaginary: every task ran to completion on the
event loop thread, one after another, while the semaphore bounded
nothing at all. ``maxConcurrency = 2_500`` in the example script is what
that misunderstanding looks like in production.

:class:`BlockingCaller` is the single place a vendor call may be made,
and it composes four concerns in a fixed order:

    1. retry, so a transient failure re-enters the whole sequence and
       re-acquires rate limit tokens rather than skipping them;
    2. rate limiting, charged against the endpoint's bucket;
    3. a semaphore, bounding in-flight work;
    4. an **explicit** thread pool executor.

The fourth item is the one that is easy to get wrong.
:func:`asyncio.to_thread` and ``run_in_executor(None, ...)`` both use the
interpreter's default executor, which is capped at ``min(32, cpu + 4)``
threads. With a semaphore of 2500 over a 32 thread default pool, the
semaphore admits 2500 coroutines that then queue invisibly behind 32
threads, holding their arguments and partial results in memory the whole
time. Sizing the pool to ``maxConcurrency`` is what makes the semaphore
mean something.

:NOTE: The caller owns a thread pool and must be closed. Use it as an
    async context manager, or call :meth:`BlockingCaller.close` from the
    session's own shutdown path.
"""

import asyncio
import functools
import concurrent.futures as cf

from typing import Any, Callable, Optional

from algotraders.core.clock import BaseClock
from algotraders.core.ratelimit import RateLimitPolicy
from algotraders.core.retry import (
    ORDER_RETRY_POLICY, RetryPolicy, retryAsync
)
from algotraders.logger import getLogger
from algotraders.models.enums import EndpointClass

logger = getLogger(__name__)

DEFAULT_MAX_CONCURRENCY = 5

THREAD_NAME_PREFIX = "algotraders"


class BlockingCaller:
    """
    Runs blocking broker SDK calls without stalling the event loop.

    :type  policy: RateLimitPolicy
    :param policy: Supplies the limiter for each endpoint class.

    :type  clock: BaseClock
    :param clock: Time source for rate limiting and backoff.

    :type  maxConcurrency: int
    :param maxConcurrency: Simultaneous in-flight calls. Also the thread
        pool size, so the semaphore and the pool cannot disagree.
        Defaults to a deliberately small number: broker limits, not
        local CPU, are the binding constraint, and a high value simply
        converts into rate limit waits.

    :type  retryPolicy: Optional[RetryPolicy]
    :param retryPolicy: Backoff schedule for transient failures.
        Endpoint class ``ORDER`` always overrides this with a single
        attempt, regardless of what is passed here.

    :type  executor: Optional[cf.ThreadPoolExecutor]
    :param executor: Pool to run blocking calls in. One sized to
        ``maxConcurrency`` is created when omitted. Passing the process
        default executor defeats the point of the class.

    **Example Usage**

    .. code-block:: python

        async with BlockingCaller(policy, clock, maxConcurrency = 5) as caller:
            payload = await caller.call(
                EndpointClass.HISTORICAL,
                session.history, data = request
            )
    """

    def __init__(
        self, policy : RateLimitPolicy, clock : BaseClock,
        maxConcurrency : int = DEFAULT_MAX_CONCURRENCY,
        retryPolicy : Optional[RetryPolicy] = None,
        executor : Optional[cf.ThreadPoolExecutor] = None
    ) -> None:
        if maxConcurrency < 1:
            raise ValueError("maxConcurrency must be at least 1")

        self.policy = policy
        self.clock = clock
        self.maxConcurrency = maxConcurrency
        self.retryPolicy = retryPolicy or RetryPolicy()

        self.semaphore = asyncio.Semaphore(maxConcurrency)

        # ! sized to the semaphore on purpose. The default executor caps
        # at min(32, cpu + 4), so without this the semaphore admits work
        # that then queues invisibly behind a thread pool it never sees
        self.__ownsExecutor__ = executor is None
        self.__closed__ = False
        self.executor = executor or cf.ThreadPoolExecutor(
            max_workers = maxConcurrency,
            thread_name_prefix = THREAD_NAME_PREFIX
        )


    def policyFor(self, endpointClass : EndpointClass) -> RetryPolicy:
        """
        Retry policy governing the given endpoint class.

        Order endpoints are forced to a single attempt here rather than
        relying on every call site to remember. A ``placeOrder`` that
        timed out may already have reached the exchange, so a retry
        risks a second position.

        :rtype:   RetryPolicy
        :returns: The policy to apply.
        """

        if endpointClass is EndpointClass.ORDER:
            return ORDER_RETRY_POLICY

        return self.retryPolicy


    async def call(
        self, endpointClass : EndpointClass,
        function : Callable[..., Any], *args : Any, **kwargs : Any
    ) -> Any:
        """
        Execute a blocking vendor call under the full pipeline.

        :type  endpointClass: EndpointClass
        :param endpointClass: Bucket the call is charged against. Every
            call must declare one; that is what keeps an application
            wide quota honest.

        :type  function: Callable[..., Any]
        :param function: The blocking SDK method to invoke.

        :raises RateLimitExceeded: A daily quota is exhausted.

        :rtype:   Any
        :returns: Whatever the vendor call returned, untouched. Mapping
            it onto a DTO is the broker mapper's job, not this one's.
        """

        # ! a closed caller fails with a sentence rather than the raw
        # "cannot schedule new futures after shutdown" from the pool,
        # which is not in DEFAULT_RETRYABLE and propagates unexplained.
        # __aexit__ closes unconditionally, so reusing a caller after
        # one `async with` block is an easy mistake to make
        if self.__closed__:
            raise RuntimeError(
                "this BlockingCaller is closed; build a new session "
                "rather than reusing one past its context manager"
            )

        limiter = self.policy.limiterFor(endpointClass)
        bound = functools.partial(function, *args, **kwargs)

        async def attempt() -> Any:
            # ? tokens are acquired inside the retried operation so a
            # retry pays for itself rather than jumping the queue
            await limiter.acquire()

            async with self.semaphore:
                loop = asyncio.get_running_loop()

                return await loop.run_in_executor(self.executor, bound)

        label = getattr(function, "__name__", "call")

        return await retryAsync(
            attempt,
            policy = self.policyFor(endpointClass),
            clock = self.clock,
            description = f"{endpointClass.value} {label}"
        )


    async def close(self) -> None:
        """
        Shut down the thread pool, if this caller created it.

        An externally supplied executor is left alone, since its
        lifetime belongs to whoever passed it in.
        """

        self.__closed__ = True

        if not self.__ownsExecutor__:
            return

        # ! not run_in_executor(None, ...). That lazily instantiates the
        # interpreter's DEFAULT pool - the very thing this class exists
        # to avoid - purely to perform one shutdown, and it deadlocks if
        # the host has saturated that pool with work waiting on us.
        #
        # cancel_futures drops queued calls that have not started;
        # wait = False returns immediately, and ThreadPoolExecutor's own
        # atexit hook still joins the threads that are already running
        self.executor.shutdown(wait = False, cancel_futures = True)


    async def __aenter__(self) -> "BlockingCaller":
        return self


    async def __aexit__(self, *exception : Any) -> None:
        await self.close()


    def __repr__(self) -> str:
        return (
            f"BlockingCaller(maxConcurrency={self.maxConcurrency}, "
            f"retryAttempts={self.retryPolicy.maxAttempts})"
        )


__all__ = [
    "BlockingCaller", "DEFAULT_MAX_CONCURRENCY", "THREAD_NAME_PREFIX"
]
