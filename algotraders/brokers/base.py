# -*- encoding: utf-8 -*-

"""
Abstract Base Class to Connect to Broker's API and Perform Operations
---------------------------------------------------------------------

The core logic is to develop an unified system which can be integrated
with any Broker's API to perform any operations. The module is created
to be asynchronous by default to perform certain function (for example
we can fetch the historic data asynchronously for multiple securities
at a time) with rate limitation such that we do not over consume any
available resources. The rate limitation ensures parallel tasks are
queued in the system when resource is available.

REFACTOR:: GH#18 Separate Authentication Operation from ``BaseBroker``
    The base broker will handle all operational functions like data
    fetching, manipulations, etc. and a separate abstract method will
    be provided to perform authentication, login/logout and other
    related operations which does not require a asynchronous or a
    multithreading or a multiprocessing operations. These security
    feature is an one time operation which will be required only once
    during session start. Check GH#18 for more details.
"""

import math
import asyncio
import tracemalloc

import datetime as dt

from abc import ABC, abstractmethod
from typing import Any, Callable, Optional, Iterable, List, Tuple

from algotraders.errors import NotConnectedError

class BaseBrokerAuthentication(ABC):
    """
    An abstract class that ensure that the core security and broker's
    authentication feature - like ``login`` and ``logout`` operations
    are unified using the same functional call signature.

    :type  username: str
    :param username: Username or Client ID which is available from
        from the Broker and is typically the Login ID.

    :type  password: str
    :param password: Password to login to access the Broker's API, this
        value may be different from the password you used to login to
        the "DEMAT" account and is sometime referred as the "secret
        key" which is provided when creating the API endpoint in the
        Broker's API Terminal.

    NOTE: The function does not provide runtime security to mask the
        secrets (passwords, authentication tokens, etc.) from logging
        into console or when the password is called via an inherited
        function. If required, this feature must be implemented by
        the calling functions.

    REFACTOR: @requireLogin Decorator is Deprecated; Use Attributes
        Since authentication process is handled separately using a
        different functional approach, the @requireLogin decorator is
        now deprecated. API should be able to a callable attribute to
        interact with the API object.
    """

    def __init__(self, username : str, password : str) -> None:
        self.username = username
        self.password = password


    @property
    @abstractmethod
    def redirectURL(self) -> str:
        """
        Redirect URL which is provided by the Broker's
        API and the value should be validated as per documentation.
        Typically, there is no method to validate this URL, unless an
        explicit function call is provided; which must be implemented
        in the concrete class.
        """

        pass


    @abstractmethod
    def login(self, *args, **kwargs) -> Any:
        """
        Perform login operation to the Broker's API. A broker's API
        login may have different regulatory guidelines based on which
        the concrete function should be modeled. Typically, for an
        Indian Broker, SEBI is the regulatory body who provides the
        guidelines to connect using the following parameters.
        """

        pass


    @abstractmethod
    def logout(self, *args, **kwargs) -> bool:
        """
        Perform logout operation from the Broker's API. The function
        returns ``True`` on logout/closing API connection success,
        else returns ``False`` and retry of ``logout()`` should be
        defined in the calling function and may remain out of scope of
        the concrete class.
        """

        pass


class BaseBrokerAPI(ABC):
    """
    An abstract class that ensures core logic, function calls remains
    the same when switching API from different brokers. The abstract
    function also ensures correct partitions between which functions
    should be called in an asynchronous logic and which should not be
    called. The class must be inherited in the concrete definitions.

    :type  sessionManager: Callable
    :param sessionManager: A callable object that can connect to the
        Broker's API to perform operations, which is typically
        available acorss all brokers platforms.

    :type  maxConcurrent: int
    :param maxConcurrent: Limit the number of concurrent jobs that
        are allowed to be run in an asynchronous manner. The value is
        passed to ``semaphore`` (a synchronous primitive that is used
        to control access to a shared resource by multiple threads)
        that provides rate limitation in a production environment.

    :type  webSocket: Optional[Callable]
    :type  webSocket: A bidirectional communication protocol between
        the hosted machine and the API interface that can perform
        various jobs using a persistent TCP connection protocol.
    """

    def __init__(
        self, sessionManager : Callable, maxConcurrency : int,
        webSocket : Optional[Callable] = None
    ) -> None:
        self.sessionManager = sessionManager
        self.maxConcurrency = maxConcurrency

        # ? concurrency primitives to  manage access
        self.semaphore = asyncio.Semaphore(maxConcurrency)

        # ? get websocket, typically for real-time data and ordering
        self.webSocket = webSocket


    @abstractmethod
    async def fetchData(
        self, symbol : str, dateRange : Tuple[dt.datetime, dt.datetime],
        timeframe : str = "1m", *args, **kwargs
    ) -> Iterable:
        """
        Fetch historical/real-time data using the Broker's API for an
        underlying security at a provided granuality. The function is
        asynchronous in nature and thus provides flexibility to fetch
        multiple securities data at once.

        :type  symbol: str
        :param symbol: Symbol name or an identifiable value as in the
            Broker's API documentation for which historic data needs
            to be fetched. For example, "HDFCBANK-EQ:NSE" may be used
            to fetch data for "HDFCBANK" (equity) listed under NSE.
            The main reason behind creating an asynchronous function
            to fetch data is to process multiple symbols at one time
            as capable by the system, check "memory footprint" note
            for additional details.

        :type  dateRange: Tuple[dt.datetime, dt.datetime]
        :param dateRange: Time period range (both end inclusive) for which
            the historic data needs to be fetched. Broker's API may
            require formatting the value (for example using epoch time
            or passing string) which must be handled in the concrete
            method of the interface.

        :type  timeframe: str
        :param timeframe: Timeframe or granularity or resolution for
            which data needs to be fetched. For example, "1m" (default)
            may be used to refer to minute level candle data values.

        Typically, in a production environment, we want to have a
        rate limitation based on the available resources (or limit to
        a certain value such that billing is limited, etc.). To handle
        the memory limit first calculate the expected maximum memory
        footprint per task and then provide a rate limitation on the
        concurrent tasks using :class:`asyncio.Semaphore` methods.

        .. code-block:: python

            ...
            async def fetchData(self, ...) -> Iterable:
                ...

                async with self.semaphore:
                    ...

        **Understanding Memory Footprint**

        Determining the memory footprint of a Python task is
        notoriously tricky. Unlike languages like C or Rust where you
        have strict control over memory allocation, Python objects
        carry significant overhead (metadata, reference counts, garbage
        collection hooks), and memory is often freed lazily.

        To provide ease of usage, a ``tracemalloc`` is integrated into
        the abstract base function which can be used to run one single
        task and check the memory usage and then derive the maximum
        concurrency limit safely in the final production application.

        .. code-block:: python

            broker = SomeBroker(maxConcurrent = 1)  # example concrete
            broker.traceMemoryUsage(limit = 20_000) # say, 20 GB limit

            >> Current Memory : ... MB
            >> Peak Memory    : ... MB # this is your baseline footprint

        **Return Value**

        The function is designed to return an ``Iterable`` value which
        must be be processed using an external function to reduce
        memory dependency. An efficient production system should use
        an asynchronous queue to process all incoming data into the
        required output format.

        :rtype:   Iterable
        :returns: Returns an API response typically in a JSON (dict)
            format which should be passed to a processing engine (ETL)
            for data transformations, loading and/or storing.
        """

        pass


    async def traceMemoryUsage(
        self, limit : float, buffer : Optional[float] = None, *args, **kwargs
    ) -> int:
        """
        Trace the memory usage required to fetch historical/real-time
        data by the application and return an integer which should
        be the value of the maximum concurrent job that is allowed to
        be run in an production environment.

        :type  limit: float
        :param limit: The maximum available memory available (in MB)
            which is used to calculate the maximum concurrent jobs.

        :type  buffer: float
        :param buffer: The ``tracemalloc`` calculates the current and
            the peak memory usage, which is the baseline value. It is
            recommended to keep a buffer (of atleast 20%) for garbage
            collection delays that may be required. Defaults to None,
            which internally calculates and puts a 20% buffer from the
            peak value. If a custom value is passed, then it must be
            in MB terms.

        **Deep Profiling**

        If your task is highly complex (e.g., utilizing C-extensions
        like NumPy, Pandas, or external ML libraries) and you are
        hunting down exactly what line is eating all your (say 20GB)
        RAM, :mod:`tracemalloc` sometimes misses memory allocated
        directly by C libraries.

        For production code, to get the exact traceback, one can also
        use the :mod:`filprofiler` for catching OOM crashes.

        .. code-block:: python

            # pip install filprofiler
            fil-profile run script.py # get report for OOM debugging

        It generates an interactive SVG flamegraph in your browser
        that shows exactly which function, and which line of code,
        allocated the peak memory.

        :rtype:   int
        :returns: Returns the maximum recommended concurrent jobs that
            should be allowed to run in the production system.
        """

        tracemalloc.start()

        try:
            _ = await self.fetchData(*args, **kwargs)
            current, peak = tracemalloc.get_traced_memory()

            print(f"Current Memory : {current / 10**6:,.2f} MB")
            print(f"Peak Memory    : {peak / 10**6:,.2f} MB")
        finally:
            # stop tracemalloc to prevent memory leaks in the profiler
            tracemalloc.stop()

        buffer = buffer or 0.20 * (peak / 10**6)
        return math.floor(limit / ((peak / 10**6) + buffer))


    @abstractmethod
    async def executeOrder(self, *args, **kwargs) -> Optional[object]:
        """
        Execute buy/sell of securities through the exchange using the
        Broker's API. An enduser must validate and continuously monitor
        the status of executed orders and proper logging of the values
        must be done in the production system for compliance and deep
        auditing purposes. This function involves monetary risks and
        endusers should be aware of the potential risks, limitations,
        and aware of the market condition. The authors of the script
        and/or module is not responsible for any profit/loss incurred
        from the usage of this function.

        **Fat-Finger/Boundary Checks**

        A financial system (in production environment) must ensure that
        safety checks are implemented to enforce strict boundary
        validations (max trade size, order value limits, daily rate
        limits) before delegating to the concrete API call. These rules
        must be developed in the strategies and from where the order
        is placed, and then the function should be called.
        """

        pass


    @abstractmethod
    def getPositions(self) -> List[dict]:
        """
        Get the current position from the DEMAT account. The value can
        be used to determine the next steps of action or to provide
        guard to maximum allowed stop loss for the day, etc.
        """

        pass


    def __repr__(self) -> str:
        return (
            f"Object ID: {id(self)}; "
            f"Broker Name: {self.__class__.__name__}; "
            f"Redirect URL: {self.redirectURL}"
        )
