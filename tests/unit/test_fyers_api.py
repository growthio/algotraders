# -*- encoding: utf-8 -*-

"""
Fyers Data Surface: Chunking, Rate Limiting and Date Handling
---------------------------------------------------------------

These tests drive the real :class:`FyersAPI` against a scripted vendor
client, so they exercise the chunker, the mapper and the rate limited
caller together without a network or a credential.

The chunk count assertions are the substance. A two year one minute
request is not one broker call and is not twelve; it is exactly eight,
because Fyers caps an intraday range at 100 days. The old example
hard coded monthly buckets and got twelve regardless of the real limit.
"""

import datetime as dt

import pytest

from algotraders.brokers.fyers.api import FyersAPI
from algotraders.brokers.fyers import limits
from algotraders.core.clock import IST
from algotraders.core.executor import BlockingCaller
from algotraders.core.ratelimit import RateLimitPolicy
from algotraders.errors import (
    RateLimitExceeded, TransientBrokerError, UnsupportedTimeframe
)
from algotraders.models.enums import Timeframe
from algotraders.models.instrument import Instrument, InstrumentKey
from tests.fakes.clock import FakeClock
from tests.fakes.session import FakeSessionManager, loadPayload, repeating

EQUITY = Instrument(
    key = InstrumentKey.parse("NSE:SBIN:EQ"),
    brokerSymbol = "NSE:SBIN-EQ", brokerToken = "3045",
    lotSize = 1, source = "fyers"
)

NOW = dt.datetime(2026, 8, 5, 15, 30, tzinfo = IST)


def buildAPI(
    clock : FakeClock, sessionManager : FakeSessionManager
) -> FyersAPI:
    """
    Wire a real FyersAPI over a scripted vendor client.
    """

    policy = RateLimitPolicy(
        clock = clock, sharedLimits = limits.SHARED_LIMITS,
        dailyQuota = limits.DAILY_QUOTA,
        quotaName = limits.DAILY_QUOTA_NAME
    )

    return FyersAPI(
        sessionManager = sessionManager,
        caller = BlockingCaller(policy, clock, maxConcurrency = 2),
        clock = clock
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(start = NOW)


@pytest.fixture
def sessionManager() -> FakeSessionManager:
    return FakeSessionManager({
        "history" : repeating(loadPayload("fyers_history_ok"))
    })


async def test_shortRangeIssuesOneCall(
    clock : FakeClock, sessionManager : FakeSessionManager
) -> None:
    """
    A range inside the cap costs exactly one broker call.
    """

    api = buildAPI(clock, sessionManager)

    candles = await api.fetchCandles(
        EQUITY, Timeframe.MINUTE_1,
        (NOW - dt.timedelta(days = 5), NOW)
    )

    assert sessionManager.countOf("history") == 1
    assert len(candles) == 5

    await api.caller.close()


async def test_twoYearMinuteRangeSplitsIntoEightCalls(
    clock : FakeClock, sessionManager : FakeSessionManager
) -> None:
    """
    Fyers caps intraday history at 100 days, so two years is eight
    calls.

    The old example hard coded monthly buckets and issued twelve per
    symbol per year regardless of the real limit.
    """

    api = buildAPI(clock, sessionManager)

    await api.fetchCandles(
        EQUITY, Timeframe.MINUTE_1,
        (dt.datetime(2024, 8, 5, tzinfo = IST), NOW)
    )

    assert sessionManager.countOf("history") == 8

    await api.caller.close()


async def test_dailyRangeUsesTheLargerCap(
    clock : FakeClock, sessionManager : FakeSessionManager
) -> None:
    """
    Daily bars allow 366 days per request, so the same two year range
    costs two calls rather than eight.
    """

    api = buildAPI(clock, sessionManager)

    await api.fetchCandles(
        EQUITY, Timeframe.DAY_1,
        (dt.datetime(2024, 8, 5, tzinfo = IST), NOW)
    )

    assert sessionManager.countOf("history") == 2

    await api.caller.close()


async def test_datesAreSentAsISTStringsNotEpochs(
    clock : FakeClock, sessionManager : FakeSessionManager
) -> None:
    """
    The request carries ``yyyy-mm-dd`` in IST.

    The previous implementation sent epoch seconds derived from a bare
    ``timestamp()`` call, which read host local time and shifted the
    requested window by hours on any non-IST machine.
    """

    api = buildAPI(clock, sessionManager)

    await api.fetchCandles(
        EQUITY, Timeframe.MINUTE_1,
        (dt.datetime(2026, 8, 1, 9, 15, tzinfo = IST), NOW)
    )

    payload = sessionManager.callsTo("history")[0]["data"]

    assert payload["date_format"] == "1"
    assert payload["range_from"] == "2026-08-01"
    assert payload["range_to"] == "2026-08-05"
    assert payload["symbol"] == "NSE:SBIN-EQ"
    assert payload["resolution"] == "1"

    await api.caller.close()


async def test_futureEndDateIsClampedToNow(
    clock : FakeClock, sessionManager : FakeSessionManager
) -> None:
    """
    Fyers rejects a forward looking range rather than returning what it
    has, so a request for "up to tomorrow" is trimmed locally.
    """

    api = buildAPI(clock, sessionManager)

    await api.fetchCandles(
        EQUITY, Timeframe.DAY_1,
        (dt.datetime(2026, 8, 1, tzinfo = IST),
         dt.datetime(2027, 1, 1, tzinfo = IST))
    )

    payload = sessionManager.callsTo("history")[0]["data"]

    assert payload["range_to"] == "2026-08-05"

    await api.caller.close()


async def test_candlesAreDeduplicatedAcrossChunkBoundaries(
    clock : FakeClock, sessionManager : FakeSessionManager
) -> None:
    """
    Every chunk returns the same recorded payload here, so a naive
    concatenation would report forty bars instead of five.

    Brokers do occasionally return a bar just outside the range they
    were given, which is why deduplication is not merely a test
    artefact.
    """

    api = buildAPI(clock, sessionManager)

    candles = await api.fetchCandles(
        EQUITY, Timeframe.MINUTE_1,
        (dt.datetime(2024, 8, 5, tzinfo = IST), NOW)
    )

    assert sessionManager.countOf("history") == 8
    assert len(candles) == 5

    timestamps = [candle.timestamp for candle in candles]

    assert timestamps == sorted(timestamps)

    await api.caller.close()


async def test_throttledResponseSurfacesAsRateLimitExceeded(
    clock : FakeClock
) -> None:
    """
    A 429 from the vendor is retried and then raised, rather than being
    silently mapped to an empty result.
    """

    sessionManager = FakeSessionManager({
        "history" : repeating(loadPayload("fyers_history_429"))
    })
    api = buildAPI(clock, sessionManager)

    with pytest.raises(RateLimitExceeded):
        await api.fetchCandles(
            EQUITY, Timeframe.MINUTE_1, (NOW - dt.timedelta(days = 1), NOW)
        )

    await api.caller.close()


async def test_malformedResponseIsNotSilentlyEmpty(
    clock : FakeClock
) -> None:
    """
    The swallowed decode failure raises instead of yielding no candles.

    An empty list would look like a market holiday and would be written
    into the database as a genuine absence of data.
    """

    sessionManager = FakeSessionManager({
        "history" : repeating(loadPayload("fyers_history_garbage"))
    })
    api = buildAPI(clock, sessionManager)

    with pytest.raises(TransientBrokerError):
        await api.fetchCandles(
            EQUITY, Timeframe.MINUTE_1, (NOW - dt.timedelta(days = 1), NOW)
        )

    await api.caller.close()


async def test_transientFailureIsRetriedThenSucceeds(
    clock : FakeClock
) -> None:
    """
    A single bad response does not fail the whole backfill.
    """

    sessionManager = FakeSessionManager({
        "history" : [
            loadPayload("fyers_history_garbage"),
            loadPayload("fyers_history_ok")
        ]
    })
    api = buildAPI(clock, sessionManager)

    candles = await api.fetchCandles(
        EQUITY, Timeframe.MINUTE_1, (NOW - dt.timedelta(days = 1), NOW)
    )

    assert len(candles) == 5
    assert sessionManager.countOf("history") == 2

    await api.caller.close()


async def test_rateLimitPacesTheChunks(
    clock : FakeClock, sessionManager : FakeSessionManager
) -> None:
    """
    Chunked requests are charged against the shared Fyers buckets.

    Ten calls per second are free; the eleventh waits, which is the
    behaviour that was entirely absent when 170,132 rejections were
    recorded.
    """

    api = buildAPI(clock, sessionManager)

    await api.fetchCandles(
        EQUITY, Timeframe.MINUTE_1,
        (dt.datetime(2022, 1, 1, tzinfo = IST), NOW)
    )

    assert sessionManager.countOf("history") == 17
    assert len(clock.sleepCalls) == 7

    await api.caller.close()


async def test_unsupportedTimeframeRefusesByDefault(
    clock : FakeClock, sessionManager : FakeSessionManager
) -> None:
    """
    Monthly bars are refused rather than silently synthesised, because
    fabricating a bar the broker never sent is worse than failing.
    """

    api = buildAPI(clock, sessionManager)

    with pytest.raises(UnsupportedTimeframe):
        await api.fetchCandles(
            EQUITY, Timeframe.MONTH_1,
            (dt.datetime(2026, 1, 1, tzinfo = IST), NOW)
        )

    assert sessionManager.countOf("history") == 0

    await api.caller.close()


async def test_aggregationIsOptInAndFoldsFromDaily(
    clock : FakeClock, sessionManager : FakeSessionManager
) -> None:
    """
    Opting in fetches daily bars and folds them, so the caller knows the
    monthly series is derived rather than broker supplied.
    """

    api = buildAPI(clock, sessionManager)

    candles = await api.fetchCandles(
        EQUITY, Timeframe.MONTH_1,
        (dt.datetime(2026, 8, 1, tzinfo = IST), NOW),
        allowAggregation = True
    )

    assert sessionManager.callsTo("history")[0]["data"]["resolution"] == "D"
    assert len(candles) == 1
    assert candles[0].timeframe is Timeframe.MONTH_1

    await api.caller.close()


async def test_quotesAreBatchedIntoOneCall(clock : FakeClock) -> None:
    """
    Quote endpoints carry the tightest limits, so instruments are
    batched rather than fetched one at a time.
    """

    sessionManager = FakeSessionManager({
        "quotes" : repeating({
            "s" : "ok",
            "d" : [
                {"n" : "NSE:SBIN-EQ", "v" : {"lp" : 814.0, "volume" : 100}}
            ]
        })
    })
    api = buildAPI(clock, sessionManager)

    other = EQUITY.model_copy(update = {"brokerSymbol" : "NSE:TCS-EQ"})
    quotes = await api.fetchQuote([EQUITY, other])

    assert sessionManager.countOf("quotes") == 1
    assert "NSE:SBIN-EQ" in sessionManager.callsTo("quotes")[0]["data"]["symbols"]
    assert len(quotes) == 1
    assert quotes[0].lastPrice == 814.0

    await api.caller.close()


async def test_emptyQuoteRequestMakesNoCall(
    clock : FakeClock, sessionManager : FakeSessionManager
) -> None:
    """
    Asking for nothing costs nothing, including no rate limit token.
    """

    api = buildAPI(clock, sessionManager)

    assert await api.fetchQuote([]) == []
    assert sessionManager.countOf("quotes") == 0

    await api.caller.close()


def test_representationDoesNotTouchAuthAttributes(
    clock : FakeClock, sessionManager : FakeSessionManager
) -> None:
    """
    ``repr`` used to read ``self.redirectURL``, which lives on the
    authentication class, so calling it on an API object raised
    ``AttributeError``.
    """

    api = buildAPI(clock, sessionManager)

    assert "fyers" in repr(api)
