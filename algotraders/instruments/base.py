# -*- encoding: utf-8 -*-

"""
Resolving a Canonical Instrument Key to a Tradable Contract
--------------------------------------------------------------

This layer is what makes the library genuinely broker agnostic rather
than merely claiming to be. Fyers accepts a formatted symbol string.
Kite accepts an opaque integer ``instrument_token`` that exists only in
a downloaded dump and is reissued at every derivative expiry. A caller
cannot be asked to know which, so both are hidden behind one call.

It is also the cheapest correctness win available. The captured Fyers
error log contained 18,059 responses rejecting an unknown symbol. Every
one of those consumed a rate limited request in order to learn something
a cached instrument master already knew, and each failure arrived only
after a network round trip. Resolving locally turns all of them into an
immediate :class:`~algotraders.errors.InstrumentNotFound`.

**Degradation ladder**

Market data infrastructure has to keep working when a symbol master
download fails, so failure is graded rather than binary:

    * fresh cache present - serve it, no network call at all;
    * stale cache and download succeeds - replace and serve;
    * stale cache and download fails - warn, serve stale, set
      :attr:`BaseInstrumentResolver.isStale`;
    * no cache and download fails - raise
      :class:`~algotraders.errors.InstrumentMasterUnavailable`.

Serving a stale master is the right call for equities, whose tokens do
not change, and is flagged so a caller can refuse it for derivatives if
it wants to.
"""

import decimal
import datetime as dt

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from algotraders.core.clock import BaseClock
from algotraders.errors import (
    InstrumentMasterUnavailable, InstrumentNotFound
)
from algotraders.instruments.cache import InstrumentCache
from algotraders.logger import getLogger
from algotraders.models.enums import OptionRight, Segment
from algotraders.models.instrument import Instrument, InstrumentKey

logger = getLogger(__name__)

# ? equities and derivatives cover everything the supported brokers
# serve today; a broker needing more overrides this on its subclass
DEFAULT_SEGMENTS : Tuple[Segment, ...] = (
    Segment.CAPITAL, Segment.DERIVATIVE
)


class BaseInstrumentResolver(ABC):
    """
    Maps canonical instrument keys onto one broker's encoding.

    Subclasses supply two things: how to download and parse that
    broker's master, and how to render an instrument as the symbol the
    broker's API expects. Everything else, caching, indexing, lookup and
    the degradation ladder, is shared.

    :type  brokerName: str
    :param brokerName: Registry name, used to partition the cache.

    :type  cache: InstrumentCache
    :param cache: Where masters are stored between runs.

    :type  clock: BaseClock
    :param clock: Time source, used for trading-day freshness.

    :type  segments: Optional[Sequence[Segment]]
    :param segments: Segments this resolver loads. Defaults to capital
        and derivatives.
    """

    def __init__(
        self, brokerName : str, cache : InstrumentCache, clock : BaseClock,
        segments : Optional[Sequence[Segment]] = None
    ) -> None:
        self.brokerName = brokerName
        self.cache = cache
        self.clock = clock
        self.segments : Tuple[Segment, ...] = tuple(
            segments or DEFAULT_SEGMENTS
        )

        self.isStale = False

        self.__loaded__ : Dict[Segment, List[Instrument]] = {}
        self.__byKey__ : Dict[str, Instrument] = {}
        self.__bySymbol__ : Dict[str, Instrument] = {}
        self.__byToken__ : Dict[str, Instrument] = {}
        self.__byOption__ : Dict[
            Tuple[str, dt.date, decimal.Decimal, OptionRight], Instrument
        ] = {}


    @abstractmethod
    async def downloadMaster(self, segment : Segment) -> List[Instrument]:
        """
        Fetch and parse one segment's instrument master.

        Implementations differ substantially: Fyers exposes public CSVs
        needing no authentication, while Kite requires an authenticated
        call routed through the rate limiter like any other request.

        :raises InstrumentMasterUnavailable: The master could not be
            retrieved. Callers rely on this to trigger the stale-cache
            fallback, so network failures must be translated into it
            rather than propagating raw.

        :rtype:   List[Instrument]
        :returns: Every instrument in the segment.
        """

        pass


    @abstractmethod
    def toBrokerSymbol(self, instrument : Instrument) -> str:
        """
        Render an instrument as the symbol this broker's API expects.

        :rtype:   str
        :returns: For example ``NSE:SBIN-EQ`` for Fyers.
        """

        pass


    async def refresh(
        self, segments : Optional[Sequence[Segment]] = None,
        force : bool = False
    ) -> int:
        """
        Ensure the requested segments are loaded, downloading if needed.

        Safe and cheap to call on every start-up: a cache written today
        short circuits without any network access.

        :type  segments: Optional[Sequence[Segment]]
        :param segments: Segments to load. Defaults to all configured.

        :type  force: bool
        :param force: Download even when the cache is fresh. Needed
            after an expiry, when derivative tokens have been reissued
            mid-session.

        :raises InstrumentMasterUnavailable: A segment has neither a
            usable cache nor a successful download.

        :rtype:   int
        :returns: Total instruments now loaded across those segments.
        """

        targets = tuple(segments or self.segments)
        total = 0

        for segment in targets:
            total += len(await self.__loadSegment__(segment, force = force))

        self.__reindex__()

        return total


    async def __loadSegment__(
        self, segment : Segment, force : bool = False
    ) -> List[Instrument]:
        """
        Apply the degradation ladder for a single segment.
        """

        if not force and segment in self.__loaded__:
            return self.__loaded__[segment]

        if not force and self.cache.isFresh(self.brokerName, segment):
            cached = self.cache.read(self.brokerName, segment)

            if cached:
                logger.debug(
                    "using fresh %s %s cache, %d instruments",
                    self.brokerName, segment.value, len(cached)
                )

                self.__loaded__[segment] = cached

                return cached

        try:
            instruments = await self.downloadMaster(segment)

            self.cache.write(self.brokerName, segment, instruments)
            self.__loaded__[segment] = instruments

            return instruments
        except Exception as error:
            stale = self.cache.read(self.brokerName, segment)

            if stale:
                # ! a stale master beats no master. Equity tokens are
                # stable; derivative tokens are not, hence the flag
                self.isStale = True

                logger.warning(
                    "%s %s master download failed (%s), serving a stale "
                    "cache of %d instruments",
                    self.brokerName, segment.value, type(error).__name__,
                    len(stale)
                )

                self.__loaded__[segment] = stale

                return stale

            raise InstrumentMasterUnavailable(
                f"no {self.brokerName} {segment.value} instrument master "
                f"is available and the download failed: {error}"
            ) from error


    def __reindex__(self) -> None:
        """
        Rebuild the lookup indices from everything currently loaded.

        Built eagerly after a load rather than lazily per query, because
        a resolver is typically hit thousands of times in a backfill and
        a linear scan per lookup would dominate the run.
        """

        self.__byKey__.clear()
        self.__bySymbol__.clear()
        self.__byToken__.clear()
        self.__byOption__.clear()

        for instruments in self.__loaded__.values():
            for instrument in instruments:
                key = instrument.key

                self.__byKey__[str(key)] = instrument
                self.__bySymbol__[instrument.brokerSymbol.upper()] = instrument

                if instrument.brokerToken:
                    self.__byToken__[str(instrument.brokerToken)] = instrument

                if (
                    key.expiry is not None
                    and key.strike is not None
                    and key.optionRight is not None
                ):
                    self.__byOption__[(
                        key.tradingSymbol, key.expiry, key.strike,
                        key.optionRight
                    )] = instrument


    async def __ensureLoaded__(self) -> None:
        """
        Load the configured segments if nothing has been loaded yet.
        """

        if not self.__loaded__:
            await self.refresh()


    async def resolve(
        self, key : Union[str, InstrumentKey]
    ) -> Instrument:
        """
        Resolve a canonical key to a tradable instrument.

        :type  key: Union[str, InstrumentKey]
        :param key: Canonical key, as an object or its string form.

        **Example Usage**

        .. code-block:: python

            instrument = await resolver.resolve("NSE:SBIN:EQ")

            instrument.brokerSymbol   # 'NSE:SBIN-EQ' on Fyers
            instrument.brokerToken    # '779521' on Kite

        :raises InstrumentNotFound: No instrument matches. Raised
            locally, without a network call, which is the point.

        :rtype:   Instrument
        :returns: The resolved instrument.
        """

        await self.__ensureLoaded__()

        canonical = str(InstrumentKey.parse(key))
        instrument = self.__byKey__.get(canonical)

        if instrument is None:
            raise InstrumentNotFound(canonical, brokerName = self.brokerName)

        return instrument


    async def resolveBrokerSymbol(self, brokerSymbol : str) -> Instrument:
        """
        Reverse lookup from a vendor symbol back to an instrument.

        Needed on the inbound path: a websocket tick or an order update
        identifies its instrument the broker's way, and it has to become
        a canonical key before it can be stored.

        :raises InstrumentNotFound: The symbol is unknown.

        :rtype:   Instrument
        :returns: The resolved instrument.
        """

        await self.__ensureLoaded__()

        instrument = self.__bySymbol__.get(brokerSymbol.upper())

        if instrument is None:
            raise InstrumentNotFound(
                brokerSymbol, brokerName = self.brokerName
            )

        return instrument


    async def resolveBrokerToken(self, brokerToken : Any) -> Instrument:
        """
        Reverse lookup from a vendor numeric token.

        This is the Kite streaming path: binary tick packets carry only
        an ``instrument_token``, with no symbol anywhere in the frame.

        :raises InstrumentNotFound: The token is unknown, which for
            derivatives usually means the master predates an expiry and
            needs a forced refresh.

        :rtype:   Instrument
        :returns: The resolved instrument.
        """

        await self.__ensureLoaded__()

        instrument = self.__byToken__.get(str(brokerToken))

        if instrument is None:
            raise InstrumentNotFound(
                brokerToken, brokerName = self.brokerName
            )

        return instrument


    async def resolveOption(
        self, underlying : str, expiry : dt.date,
        strike : Union[int, float, str, decimal.Decimal],
        right : OptionRight
    ) -> Instrument:
        """
        Resolve an option contract by its economic description.

        Strategies think in terms of "the 24800 call expiring on the
        27th", not in vendor symbols, so this is the lookup an options
        strategy actually needs.

        :type  strike: Union[int, float, str, decimal.Decimal]
        :param strike: Strike price. Converted through ``Decimal`` via
            ``str`` so that a float literal cannot introduce a binary
            representation error into an exact index lookup.

        :raises InstrumentNotFound: No such contract exists, commonly
            because the strike is outside the listed range or the expiry
            is not a trading holiday adjusted date.

        :rtype:   Instrument
        :returns: The resolved option.
        """

        await self.__ensureLoaded__()

        exact = decimal.Decimal(str(strike))
        candidate = self.__byOption__.get(
            (underlying.upper(), expiry, exact, right)
        )

        if candidate is None:
            raise InstrumentNotFound(
                f"{underlying} {expiry.isoformat()} {exact} {right.value}",
                brokerName = self.brokerName
            )

        return candidate


    async def search(
        self, name : str, segment : Optional[Segment] = None,
        limit : int = 25
    ) -> List[Instrument]:
        """
        Find instruments whose symbol or name contains a substring.

        Backs a type-ahead in the host's frontend. Matching is case
        insensitive and results are sorted so the shortest, and
        therefore usually the most relevant, symbol comes first.

        :type  limit: int
        :param limit: Maximum results to return.

        :rtype:   List[Instrument]
        :returns: Matching instruments, best first.
        """

        await self.__ensureLoaded__()

        needle = name.strip().upper()

        if not needle:
            return []

        matches = [
            instrument
            for instrument in self.__byKey__.values()
            if (
                (segment is None or instrument.key.segment is segment)
                and (
                    needle in instrument.key.tradingSymbol
                    or needle in (instrument.name or "").upper()
                )
            )
        ]

        matches.sort(
            key = lambda entry: (
                len(entry.key.tradingSymbol), entry.key.tradingSymbol
            )
        )

        return matches[:limit]


    @property
    def loadedCount(self) -> int:
        """
        Total instruments currently held in memory.
        """

        return sum(len(entries) for entries in self.__loaded__.values())


    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(broker={self.brokerName!r}, "
            f"loaded={self.loadedCount}, stale={self.isStale})"
        )


__all__ = ["BaseInstrumentResolver", "DEFAULT_SEGMENTS"]
