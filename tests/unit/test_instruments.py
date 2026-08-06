# -*- encoding: utf-8 -*-

"""
Instrument Cache Freshness and Resolver Degradation
------------------------------------------------------

Two behaviours carry this layer. Cache freshness must key on the trading
date rather than elapsed time, because exchanges flush derivative
instrument tokens at every expiry and a twelve hour old cache can
already be wrong. And resolution must fail locally, since the whole
point is to avoid spending a rate limited request to discover a symbol
does not exist.
"""

import decimal
import pathlib
import datetime as dt

import pytest

from algotraders.core.clock import IST
from algotraders.errors import (
    InstrumentMasterUnavailable, InstrumentNotFound
)
from algotraders.instruments.cache import InstrumentCache
from algotraders.models.enums import OptionRight, Segment
from algotraders.models.instrument import InstrumentKey
from tests.fakes.clock import FakeClock
from tests.fakes.resolver import SampleResolver

MORNING = dt.datetime(2026, 8, 5, 9, 30, tzinfo = IST)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(start = MORNING)


@pytest.fixture
def cache(tmp_path : pathlib.Path, clock : FakeClock) -> InstrumentCache:
    return InstrumentCache(tmp_path / "cache", clock)


@pytest.fixture
def resolver(
    cache : InstrumentCache, clock : FakeClock
) -> SampleResolver:
    return SampleResolver(cache = cache, clock = clock)


async def test_refreshLoadsEverySegment(
    resolver : SampleResolver
) -> None:
    """
    A first refresh downloads and indexes the whole master.
    """

    loaded = await resolver.refresh()

    assert loaded == 13
    assert resolver.loadedCount == 13
    assert resolver.isStale is False


async def test_resolvesEquityToBrokerEncoding(
    resolver : SampleResolver
) -> None:
    """
    A canonical key yields both the vendor symbol and the vendor token,
    which is what lets one caller serve both brokers.
    """

    instrument = await resolver.resolve("NSE:SBIN:EQ")

    assert instrument.brokerSymbol == "NSE:SBIN-EQ"
    assert instrument.brokerToken == "3045"
    assert instrument.lotSize == 1


async def test_resolvesOptionByEconomicDescription(
    resolver : SampleResolver
) -> None:
    """
    Strategies think in terms of an expiry, a strike and a side, not in
    vendor symbols.
    """

    instrument = await resolver.resolveOption(
        underlying = "NIFTY", expiry = dt.date(2026, 1, 27),
        strike = 24800, right = OptionRight.CALL
    )

    assert instrument.brokerSymbol == "NSE:NIFTY26JAN24800CE"
    assert instrument.lotSize == 65


async def test_floatStrikeDoesNotBreakTheLookup(
    resolver : SampleResolver
) -> None:
    """
    A float literal is converted through ``str`` before becoming a
    Decimal, so binary representation error cannot miss an exact index
    entry.
    """

    instrument = await resolver.resolveOption(
        underlying = "NIFTY", expiry = dt.date(2026, 1, 27),
        strike = 24800.0, right = OptionRight.CALL
    )

    assert instrument.key.strike == decimal.Decimal("24800")


async def test_reverseLookupBySymbolAndToken(
    resolver : SampleResolver
) -> None:
    """
    Inbound data identifies instruments the broker's way.

    A Kite binary tick carries only an integer token with no symbol
    anywhere in the frame, so the token index is the only way back to a
    canonical key.
    """

    bySymbol = await resolver.resolveBrokerSymbol("NSE:SBIN-EQ")
    byToken = await resolver.resolveBrokerToken(3045)

    assert bySymbol == byToken
    assert bySymbol.instrumentKey == "NSE:SBIN:EQ"


async def test_unknownSymbolFailsLocallyWithoutNetwork(
    resolver : SampleResolver
) -> None:
    """
    The captured Fyers log held 18,059 rejected-symbol responses, each
    costing a rate limited round trip to learn what the cached master
    already knew.
    """

    await resolver.refresh()
    resolver.downloadCalls = 0

    with pytest.raises(InstrumentNotFound) as failure:
        await resolver.resolve("NSE:NOTREAL:EQ")

    assert "NSE:NOTREAL:EQ" in str(failure.value)
    assert resolver.downloadCalls == 0


async def test_searchRanksShortestSymbolFirst(
    resolver : SampleResolver
) -> None:
    """
    Backs a type-ahead, where the exact underlying should outrank its
    derivatives.
    """

    matches = await resolver.search("NIFTY")
    symbols = [entry.key.tradingSymbol for entry in matches]

    assert symbols[0] == "NIFTY"
    assert "BANKNIFTY" in symbols


async def test_searchCanBeScopedToASegment(
    resolver : SampleResolver
) -> None:
    """
    Scoping keeps an equity picker from offering option contracts.
    """

    matches = await resolver.search("NIFTY", segment = Segment.CAPITAL)

    assert all(
        entry.key.segment is Segment.CAPITAL for entry in matches
    )


async def test_freshCacheIsServedWithoutDownloading(
    cache : InstrumentCache, clock : FakeClock
) -> None:
    """
    A cache written today short circuits entirely, which for Kite also
    saves a rate limited API call.
    """

    first = SampleResolver(cache = cache, clock = clock)
    await first.refresh()

    second = SampleResolver(cache = cache, clock = clock)
    await second.refresh()

    assert second.downloadCalls == 0
    assert second.loadedCount == 13


async def test_cacheGoesStaleOnTheNextTradingDay(
    cache : InstrumentCache, clock : FakeClock
) -> None:
    """
    Freshness is anchored to the trading date, not to elapsed time.

    Exchanges flush derivative instrument tokens at expiry, so a cache
    written yesterday evening is stale this morning even though a 24
    hour time to live would still consider it valid.
    """

    first = SampleResolver(cache = cache, clock = clock)
    await first.refresh()

    assert cache.isFresh("sample", Segment.CAPITAL) is True

    clock.advanceTo(dt.datetime(2026, 8, 6, 8, 0, tzinfo = IST))

    assert cache.isFresh("sample", Segment.CAPITAL) is False

    second = SampleResolver(cache = cache, clock = clock)
    await second.refresh()

    assert second.downloadCalls == 2


async def test_cacheWrittenLateEveningIsStaleShortlyAfterMidnight(
    cache : InstrumentCache, clock : FakeClock
) -> None:
    """
    Ten minutes of elapsed time is enough when it crosses IST midnight.
    """

    clock.advanceTo(dt.datetime(2026, 8, 5, 23, 55, tzinfo = IST))

    resolver = SampleResolver(cache = cache, clock = clock)
    await resolver.refresh()

    clock.advance(600)

    assert cache.isFresh("sample", Segment.CAPITAL) is False


async def test_staleCacheIsServedWhenTheDownloadFails(
    cache : InstrumentCache, clock : FakeClock
) -> None:
    """
    A stale master beats no master, and the resolver flags itself so a
    caller can refuse it for derivatives if it wants to.
    """

    healthy = SampleResolver(cache = cache, clock = clock)
    await healthy.refresh()

    clock.advanceTo(dt.datetime(2026, 8, 6, 8, 0, tzinfo = IST))

    broken = SampleResolver(
        cache = cache, clock = clock, failDownload = True
    )
    await broken.refresh()

    assert broken.isStale is True
    assert broken.loadedCount == 13

    instrument = await broken.resolve("NSE:SBIN:EQ")

    assert instrument.brokerSymbol == "NSE:SBIN-EQ"


async def test_noCacheAndFailedDownloadRaises(
    cache : InstrumentCache, clock : FakeClock
) -> None:
    """
    With nothing to serve, the failure is explicit rather than an empty
    resolver that reports every symbol as unknown.
    """

    resolver = SampleResolver(
        cache = cache, clock = clock, failDownload = True
    )

    with pytest.raises(InstrumentMasterUnavailable):
        await resolver.refresh()


async def test_forcedRefreshBypassesAFreshCache(
    cache : InstrumentCache, clock : FakeClock
) -> None:
    """
    Needed after an expiry, when derivative tokens are reissued during
    a session that already holds a cache written the same day.
    """

    resolver = SampleResolver(cache = cache, clock = clock)

    await resolver.refresh()
    await resolver.refresh(force = True)

    assert resolver.downloadCalls == 4


async def test_corruptCacheFileIsDiscarded(
    cache : InstrumentCache, clock : FakeClock
) -> None:
    """
    A truncated master must invalidate the whole file.

    Partially loading it would resolve some symbols and mysteriously
    fail others, which is far harder to diagnose than a clean refetch.
    """

    resolver = SampleResolver(cache = cache, clock = clock)
    await resolver.refresh()

    path = cache.pathFor("sample", Segment.CAPITAL)
    path.write_text('{"key": {"exchange": "NSE"', encoding = "utf-8")

    assert cache.read("sample", Segment.CAPITAL) is None


def test_cacheRecordsProvenanceMetadata(
    cache : InstrumentCache, clock : FakeClock
) -> None:
    """
    The sidecar records where a master came from and when, so an
    operator can tell which URL produced a suspect cache.
    """

    from algotraders.models.instrument import Instrument

    instrument = Instrument(
        key = InstrumentKey.parse("NSE:SBIN:EQ"),
        brokerSymbol = "NSE:SBIN-EQ", source = "sample"
    )

    cache.write(
        "sample", Segment.CAPITAL, [instrument],
        sourceURL = "https://public.fyers.in/sym_details/NSE_CM.csv"
    )

    record = cache.metadata("sample", Segment.CAPITAL)

    assert record is not None
    assert record["rowCount"] == 1
    assert record["tradingDate"] == "2026-08-05"
    assert record["sourceURL"].endswith("NSE_CM.csv")


def test_missingCacheReportsNotFresh(cache : InstrumentCache) -> None:
    """
    A cold start is not fresh and not an error.
    """

    assert cache.isFresh("sample", Segment.CAPITAL) is False
    assert cache.read("sample", Segment.CAPITAL) is None
    assert cache.age("sample", Segment.CAPITAL) is None


async def test_invalidateForcesTheNextDownload(
    cache : InstrumentCache, clock : FakeClock
) -> None:
    """
    Dropping a segment is how a caller recovers from a master it knows
    to be wrong.
    """

    resolver = SampleResolver(cache = cache, clock = clock)
    await resolver.refresh()

    cache.invalidate("sample", Segment.CAPITAL)

    assert cache.isFresh("sample", Segment.CAPITAL) is False
    assert cache.read("sample", Segment.CAPITAL) is None
