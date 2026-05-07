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
"""

import math
import asyncio
import inspect
import tracemalloc

import datetime as dt

from functools import wraps
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional, Iterable, List, Tuple

from algotraders.errors import NotConnectedError

def requireLogin(func : Callable[..., Any]) -> Callable[..., Any]:
    """
    A decorator defined to wrap methods which requires a login, else
    raises an ``NotConnectedError`` when the method was not called or
    logout method was invoked in the script.

    ..versionchanged:: 2024-04-02 Refactoring with sessionManager
        A ``sessionManager`` is optional callable attribute of the
        class which is ``None`` by default.

    .. code-block:: python

        class NewBrokerAPI(BaseBrokerAPI):
            ...

            def login(...) -> ...:
                ...
                self.sessionManager = ...()


            def logout(...) -> ...:
                ...
                self.sessionManager = None

            @requireLogin
            async def fetchData(...)-> Iterable:
                ...

            @requireLogin
            def getPositions(...) -> List[dict]:
                ...

    The decorator function checks if the underlying callable is an
    asynchronous function, else calls the regular method.
    """

    useAsync = inspect.iscoroutinefunction(func)

    if useAsync:
        @wraps(func)
        async def asyncWrapper(self : "BaseBrokerAPI", *args, **kwargs) -> Any:
            status = getattr(self, "sessionManager", None)

            if not status:
                raise NotConnectedError(
                    f"Cannot call '{func.__name__}()', either login() "
                    "was not called, or logout() has been called."
                )

            return await func(*args, **kwargs)
        return asyncWrapper

    @wraps(func)
    def syncWrapper(self : "BaseBrokerAPI", *args, **kwargs) -> Any:
        status = getattr(self, "sessionManager", None)

        if not status:
            raise NotConnectedError(
                f"Cannot call '{func.__name__}()', either login() "
                "was not called, or logout() has been called."
            )

        return func(*args, **kwargs)
    return syncWrapper


class BaseBrokerAPI(ABC):
    """
    An abstract class that ensures core logic, function calls remains
    the same when switching API from different brokers. The abstract
    function also ensures correct partitions between which functions
    should be called in an asynchronous logic and which should not be
    called. The class must be inherited in the concrete definitions.

    :type  maxConcurrent: int
    :param maxConcurrent: Limit the number of concurrent jobs that
        are allowed to be run in an asynchronous manner. The value is
        passed to ``semaphore`` (a synchronous primitive that is used
        to control access to a shared resource by multiple threads)
        that provides rate limitation in a production environment.

    **Session Manager**

    A class attribute is created in the abstract base class to handle
    sessions. A session manager is typically available across all
    brokers platform which can be used to interact to execute orders,
    check current positions etc. The session manager is set to a
    callable function when ``.login()`` is called, else is always
    ``None`` (or when ``.logout()`` is called). If a broker does not
    require any session manager then it is recommended to create one
    to keep the code structure same as other brokers' services.

    ..versionchanged:: 2024-04-02 Refactored status with sessionManager
        The status object was previously designed to check if the
        broker login is done, but is now deprecated for sessionManager
        which is ``None`` by default, else when ``.login()`` is called
        set to a callable method which can then be widely used.
    """

    def __init__(self, maxConcurrent : int) -> None:
        self.semaphore = asyncio.Semaphore(maxConcurrent)
        self.sessionManager : Optional[Callable] = None


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
    def login(
        self, username : str, password : str, *args, **kwargs
    ) -> bool:
        """
        Perform login operation to the Broker's API. A broker's API
        login may have different regulatory guidelines based on which
        the concrete function should be modeled. Typically, for an
        Indian Broker, SEBI is the regulatory body who provides the
        guidelines to connect using the following parameters.

        :type  username: str
        :param username: Username or Client ID which is available from
            from the Broker and is typically the Login ID.

        :type  password: str
        :param password: Password to login to access the Broker's API,
            this value may be different from the password you used to
            login to the "DEMAT" account and is sometime referred as
            the "secret key" which is provided when creating the API
            endpoint in the Broker's API Terminal.

        **Keyword Agrument(s)**

        The concrete function should be able to handle and create any
        additional keyword argument(s) that may be required for the
        authentication process.
        """

        pass


    @abstractmethod
    def logout(self) -> bool:
        """
        Perform logout operation from the Broker's API. The function
        returns ``True`` on logout/closing API connection success,
        else returns ``False`` and retry of ``logout()`` should be
        defined in the calling function and may remain out of scope of
        the concrete class.
        """

        pass


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
            recommended to keep a buffer (of atleast 20%) for  garbage
            collection delays that may be required. Defaults to None,
            which internally calculates and puts a 20% buffer from the
            peak value.

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

        buffer = buffer or 0.20 * peak
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
