# -*- encoding: utf-8 -*-

"""
Abstract Base Class to Connect to Broker's API and Perform Operations
---------------------------------------------------------------------

The core logic is to develop an unified system which can be integrated
with any Broker's API to perform any operations. The contract is split
by **capability** rather than being one large interface, because the two
supported brokers do not offer the same surface and a broker that only
serves data should not be forced to stub out order placement.

    * :class:`BaseBrokerAuthentication` - login, refresh, logout.
    * :class:`BaseBrokerClient` - shared plumbing, no capability.
    * :class:`BaseBrokerData` - candles and quotes.
    * :class:`BaseBrokerTrading` - orders, positions, holdings.
    * :class:`BaseBrokerAPI` - the union, for full brokers.

..versionchanged:: 2026-08-06 Capability Split and Async Portfolio Calls
    ``getPositions()`` was synchronous while performing network I/O,
    which cannot exist in an async library: called from a coroutine it
    blocks the event loop for the whole round trip. It is replaced by
    ``fetchPositions()``. ``fetchData`` becomes ``fetchCandles`` and
    returns normalized :class:`~algotraders.models.market.Candle`
    objects instead of a raw vendor payload.

..versionchanged:: 2026-08-06 Rate Limiting Moves Off the ABC
    The old base held an ``asyncio.Semaphore`` and expected concrete
    implementations to acquire it around a blocking SDK call, which
    bounded nothing. Every vendor call now goes through an injected
    :class:`~algotraders.core.executor.BlockingCaller`, which composes
    retry, rate limiting, a semaphore and a real thread pool.

:NOTE: A concrete method here must never call a vendor SDK directly.
    Route it through ``self.caller.call(...)`` with an explicit
    :class:`~algotraders.models.enums.EndpointClass`, or the call is
    both unlimited and blocking.
"""

import datetime as dt

from abc import ABC, abstractmethod
from typing import (
    Any, AsyncIterator, FrozenSet, List, Optional, Sequence, Tuple
)

from algotraders.config import BaseBrokerCredentials
from algotraders.core.clock import BaseClock
from algotraders.core.executor import BlockingCaller
from algotraders.core.ratelimit import RateLimitPolicy
from algotraders.errors import TokenExpiredError
from algotraders.logger import getLogger, maskSecret
from algotraders.models.auth import LoginChallenge, TokenBundle, TokenPolicy
from algotraders.models.enums import Capability, Timeframe
from algotraders.models.instrument import Instrument
from algotraders.models.market import Candle, Quote
from algotraders.models.orders import OrderRequest, OrderResponse
from algotraders.models.portfolio import Holding, Position

logger = getLogger(__name__)


class BaseBrokerAuthentication(ABC):
    """
    An abstract class that ensure that the core security and broker's
    authentication feature - like ``login`` and ``logout`` operations
    are unified using the same functional call signature.

    The login flow is deliberately split in two. :meth:`beginLogin` is
    synchronous and produces a URL; :meth:`completeLogin` is a coroutine
    that exchanges the code the user's browser returned. Nothing in
    between happens inside this library, because the step in the middle
    is a human authenticating in a browser and a server process cannot
    wait on that.

    :type  credentials: BaseBrokerCredentials
    :param credentials: Application credentials for this broker.

    :type  clock: BaseClock
    :param clock: Injected time source, so token expiry arithmetic is
        testable and always evaluated in IST.
    """

    tokenPolicy : TokenPolicy = TokenPolicy()
    capabilities : FrozenSet[Capability] = frozenset()

    def __init__(
        self, credentials : BaseBrokerCredentials,
        clock : Optional[BaseClock] = None
    ) -> None:
        from algotraders.core.clock import SystemClock

        self.credentials = credentials
        self.clock = clock or SystemClock()


    @property
    def brokerName(self) -> str:
        """
        Registry name of the broker this authenticates.
        """

        return self.credentials.brokerName


    @property
    def supportsRefresh(self) -> bool:
        """
        Whether an expired session can be renewed without a human.

        Fyers can, through a refresh token and a PIN. Kite cannot for a
        standard application, which is why a Kite deployment needs an
        interactive login every morning.
        """

        return self.tokenPolicy.supportsRefresh


    @abstractmethod
    def beginLogin(self, state : Optional[str] = None) -> LoginChallenge:
        """
        Produce the URL the end user must visit to authenticate.

        Synchronous and free of I/O: it only formats a URL from the
        credentials, so a host can call it from a request handler
        without awaiting anything.

        :type  state: Optional[str]
        :param state: Opaque value round tripped through the broker, so
            the host can tie a callback to the request that began it and
            reject one it never initiated.

        :rtype:   LoginChallenge
        :returns: The login URL and echoed state.
        """

        pass


    @abstractmethod
    async def completeLogin(
        self, authCode : str, state : Optional[str] = None
    ) -> TokenBundle:
        """
        Exchange an authorization code for a session token.

        :type  authCode: str
        :param authCode: The code the broker returned to the host's
            registered redirect URI. Fyers calls this an auth code and
            Kite a request token; both are short lived and single use.

        :raises InvalidCredentials: The broker rejected the exchange.

        :rtype:   TokenBundle
        :returns: The new session, with expiry computed from
            :attr:`tokenPolicy`.
        """

        pass


    async def refresh(self, bundle : TokenBundle) -> TokenBundle:
        """
        Renew an expired access token without user interaction.

        The default implementation refuses, which is correct for any
        broker without a refresh flow. Overriding it is what makes an
        unattended deployment possible.

        :raises TokenExpiredError: This broker cannot refresh.

        :rtype:   TokenBundle
        :returns: The renewed session.
        """

        raise TokenExpiredError(
            f"{self.brokerName} does not support unattended token refresh; "
            "an interactive login is required"
        )


    async def logout(self, bundle : TokenBundle) -> bool:
        """
        Revoke a session at the broker.

        Defaults to reporting failure rather than raising, because not
        every broker exposes a revocation endpoint and a logout path
        should not break over it. Fyers has none; Kite does.

        :rtype:   bool
        :returns: Whether the broker confirmed revocation.
        """

        return False


    @abstractmethod
    def buildSessionManager(self, bundle : TokenBundle) -> Any:
        """
        Construct the vendor SDK client every API call goes through.

        :type  bundle: TokenBundle
        :param bundle: The active session token.

        :rtype:   Any
        :returns: The vendor client object, whatever shape it takes.
        """

        pass


    @abstractmethod
    def rateLimitPolicy(self, clock : BaseClock) -> RateLimitPolicy:
        """
        Build this broker's published rate limit configuration.

        Defined on the authentication object because it is a property of
        the application credentials rather than of any one endpoint, and
        because the session needs it before any API surface exists.

        :rtype:   RateLimitPolicy
        :returns: The policy, with shared buckets constructed once.
        """

        pass


    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(broker={self.brokerName!r}, "
            f"clientId="
            f"{maskSecret(self.credentials.clientId.get_secret_value())})"
        )


class BaseBrokerClient(ABC):
    """
    Shared plumbing for every broker API surface.

    Holds the vendor client, the rate limited caller, the instrument
    resolver and the clock. Carries no capability of its own; it exists
    so the data and trading interfaces do not each restate the
    constructor.

    :type  sessionManager: Any
    :param sessionManager: Vendor SDK client from
        :meth:`BaseBrokerAuthentication.buildSessionManager`.

    :type  caller: BlockingCaller
    :param caller: The **only** permitted route to a vendor call. It
        composes retry, rate limiting, a semaphore and a thread pool.

    :type  resolver: Any
    :param resolver: Instrument resolver, used to turn canonical keys
        into vendor symbols and tokens.
    """

    brokerName : str = "unknown"

    def __init__(
        self, sessionManager : Any, caller : BlockingCaller,
        clock : Optional[BaseClock] = None,
        credentials : Optional[BaseBrokerCredentials] = None,
        resolver : Any = None, session : Any = None, **extra : Any
    ) -> None:
        from algotraders.core.clock import SystemClock

        self.sessionManager = sessionManager
        self.caller = caller
        self.clock = clock or SystemClock()
        self.credentials = credentials
        self.resolver = resolver
        self.session = session


    def __repr__(self) -> str:
        """
        Render without touching credentials or any token.

        The previous implementation read ``self.redirectURL``, which
        lived on the authentication class and not here, so calling
        ``repr`` on an API object raised ``AttributeError``.
        """

        return f"{type(self).__name__}(broker={self.brokerName!r})"


class BaseBrokerData(BaseBrokerClient, ABC):
    """
    Market data capability: historical candles and point in time quotes.
    """

    @abstractmethod
    async def fetchCandles(
        self, instrument : Instrument, timeframe : Timeframe,
        dateRange : Tuple[dt.datetime, dt.datetime],
        allowAggregation : bool = False, **kwargs : Any
    ) -> List[Candle]:
        """
        Fetch historical candles for one instrument.

        Implementations must split the requested range into windows the
        broker accepts, dispatch them through
        :attr:`BaseBrokerClient.caller`, and merge the results
        deduplicated on ``(instrumentKey, timeframe, timestamp)`` and
        sorted ascending. A caller asking for two years of one minute
        data is making one impossible request that has to become many
        legal ones, and doing that here rather than at the call site is
        what removed the hand rolled monthly bucketing from the example.

        :type  instrument: Instrument
        :param instrument: Resolved contract. A full instrument rather
            than a symbol string, so the implementation has the vendor
            encoding it needs.

        :type  dateRange: Tuple[dt.datetime, dt.datetime]
        :param dateRange: Inclusive start and end. Naive values are read
            as IST.

        :type  allowAggregation: bool
        :param allowAggregation: Permit synthesising a timeframe the
            broker does not serve by folding daily candles. Defaults to
            refusing, because fabricating a bar the broker never sent is
            worse than an explicit failure.

        :raises UnsupportedTimeframe: The broker cannot serve the
            timeframe and aggregation was not permitted.

        :rtype:   List[Candle]
        :returns: Candles in ascending time order.
        """

        pass


    @abstractmethod
    async def fetchQuote(
        self, instruments : Sequence[Instrument]
    ) -> List[Quote]:
        """
        Fetch current market snapshots.

        :type  instruments: Sequence[Instrument]
        :param instruments: Contracts to quote. Implementations should
            batch these into as few vendor calls as the broker allows,
            since quote endpoints carry the tightest rate limits.

        :rtype:   List[Quote]
        :returns: One quote per resolvable instrument.
        """

        pass


    async def iterCandles(
        self, instrument : Instrument, timeframe : Timeframe,
        dateRange : Tuple[dt.datetime, dt.datetime], **kwargs : Any
    ) -> AsyncIterator[Candle]:
        """
        Yield candles one at a time instead of materialising the list.

        For a multi year one minute backfill the full result is millions
        of objects, and holding them all before the caller writes any is
        how an ingestion job runs out of memory. Concrete for every
        broker, since it is only a wrapper over
        :meth:`fetchCandles`.

        :rtype:   AsyncIterator[Candle]
        :returns: An async iterator over the candles.
        """

        for candle in await self.fetchCandles(
            instrument, timeframe, dateRange, **kwargs
        ):
            yield candle


class BaseBrokerTrading(BaseBrokerClient, ABC):
    """
    Trading capability: order lifecycle and portfolio state.

    Every method here moves real money. Implementations must charge
    order calls against :attr:`EndpointClass.ORDER`, which the executor
    forces to a single attempt, because a placement that timed out may
    already have reached the exchange.
    """

    @abstractmethod
    async def placeOrder(self, request : OrderRequest) -> OrderResponse:
        """
        Submit an order to the exchange.

        The request has already passed local validation, so an
        implementation does not re-check price and lot rules; it maps
        and dispatches.

        :raises OrderRejected: The broker refused the order.

        :rtype:   OrderResponse
        :returns: The broker's acknowledgement, carrying the order id
            needed for any later modify or cancel.
        """

        pass


    @abstractmethod
    async def modifyOrder(
        self, orderId : str, quantity : Optional[int] = None,
        limitPrice : Optional[float] = None,
        triggerPrice : Optional[float] = None, **kwargs : Any
    ) -> OrderResponse:
        """
        Amend a working order. Omitted fields are left unchanged.
        """

        pass


    @abstractmethod
    async def cancelOrder(self, orderId : str) -> OrderResponse:
        """
        Cancel a working order.
        """

        pass


    @abstractmethod
    async def fetchOrders(self) -> List[OrderResponse]:
        """
        Retrieve the day's order book.
        """

        pass


    @abstractmethod
    async def fetchPositions(self) -> List[Position]:
        """
        Retrieve open positions in the trading book.

        Replaces the previous synchronous ``getPositions``. A method
        that performs network I/O cannot be synchronous in an async
        library: called from a coroutine it blocks the event loop for
        the entire round trip.

        :rtype:   List[Position]
        :returns: Positions with short exposure normalized to a negative
            quantity. Both brokers keep zero quantity rows in the
            response for the rest of the session, so a caller wanting
            only live exposure filters on ``isFlat``.
        """

        pass


    @abstractmethod
    async def fetchHoldings(self) -> List[Holding]:
        """
        Retrieve settled stock in the demat account.
        """

        pass


class BaseBrokerAPI(BaseBrokerData, BaseBrokerTrading, ABC):
    """
    Convenience union for brokers implementing both capabilities.

    Both supported brokers do, but the split above is what lets a data
    only integration exist without stubbing out order placement, and
    what lets Phase 7 add trading to Fyers as a separate reviewable
    change.
    """

    pass


__all__ = [
    "BaseBrokerAuthentication", "BaseBrokerClient", "BaseBrokerData",
    "BaseBrokerTrading", "BaseBrokerAPI"
]
