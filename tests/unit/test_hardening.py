# -*- encoding: utf-8 -*-

"""
Remaining Audit Findings: Validation, Resources and Reporting
----------------------------------------------------------------

The tail of the audit. Individually none of these would have taken a
deployment down; together they are the difference between a library that
mostly works and one that behaves predictably at its edges.

Grouped by what they protect: orders that the exchange would refuse,
resources that leak, and figures an operator reads and acts on.
"""

import asyncio
import decimal
import datetime as dt

import pytest

from pydantic import ValidationError

from algotraders.core.chunking import splitDateRange
from algotraders.core.clock import IST
from algotraders.core.executor import BlockingCaller
from algotraders.core.profile import traceMemoryUsage
from algotraders.core.ratelimit import RateLimit, RateLimitPolicy, TokenBucket
from algotraders.models.auth import SessionStatus, TokenBundle
from algotraders.models.enums import (
    EndpointClass, Exchange, InstrumentType, OrderSide, OrderType,
    ProductType, Segment
)
from algotraders.models.instrument import Instrument, InstrumentKey
from algotraders.models.orders import OrderRequest
from tests.fakes.clock import FakeClock

MOMENT = dt.datetime(2026, 8, 5, 10, 0, tzinfo = IST)

TICKED = Instrument(
    key = InstrumentKey.parse("NSE:SBIN:EQ"), brokerSymbol = "NSE:SBIN-EQ",
    lotSize = 1, tickSize = decimal.Decimal("0.05"), source = "fyers"
)

NIFTY = Instrument(
    key = InstrumentKey.parse("NFO:NIFTY:FUT:2026-01-27"),
    brokerSymbol = "NSE:NIFTY26JANFUT", lotSize = 65, source = "fyers"
)


# ---------------------------------------------------------------------
# order validation the module claimed to provide
# ---------------------------------------------------------------------

@pytest.mark.parametrize("side, limitPrice, triggerPrice, valid", [
    (OrderSide.BUY,  101.0, 100.0, True),   # pay up to trigger or better
    (OrderSide.BUY,  100.0, 100.0, True),   # equal is legal
    (OrderSide.BUY,   99.0, 100.0, False),  # cannot fill
    (OrderSide.SELL,  99.0, 100.0, True),
    (OrderSide.SELL, 100.0, 100.0, True),
    (OrderSide.SELL, 101.0, 100.0, False),
])
def test_stopLossDirectionIsValidated(
    side : OrderSide, limitPrice : float, triggerPrice : float, valid : bool
) -> None:
    """
    A buy stop triggers on the way up and must be willing to pay at
    least the trigger; a sell stop is the mirror.

    Only the presence of the two prices was checked, so an order that
    can never fill passed local validation and was refused by the
    exchange.
    """

    def build() -> OrderRequest:
        return OrderRequest(
            instrument = TICKED, side = side, quantity = 10,
            orderType = OrderType.STOP_LOSS,
            limitPrice = limitPrice, triggerPrice = triggerPrice
        )

    if valid:
        assert build().orderType is OrderType.STOP_LOSS
    else:
        with pytest.raises(ValidationError):
            build()


def test_offTickPriceIsRejected() -> None:
    """
    ``tickSize`` was carried on every instrument and never checked, so
    an order priced off tick validated locally and was refused by the
    exchange - the same failure class as a bad lot multiple.
    """

    with pytest.raises(ValidationError) as failure:
        OrderRequest(
            instrument = TICKED, side = OrderSide.BUY, quantity = 10,
            orderType = OrderType.LIMIT, limitPrice = 100.03
        )

    assert "tick size" in str(failure.value)


def test_onTickPriceIsAccepted() -> None:
    """
    The check runs in Decimal. A float modulo against 0.05 reports a
    non-zero remainder for prices that are perfectly valid, which would
    reject good orders.
    """

    for price in (100.05, 100.10, 100.15, 812.35, 0.05):
        order = OrderRequest(
            instrument = TICKED, side = OrderSide.BUY, quantity = 10,
            orderType = OrderType.LIMIT, limitPrice = price
        )

        assert order.limitPrice == price


def test_unknownTickSizeSkipsTheCheck() -> None:
    """
    An instrument without a tick size does not get an invented one.
    """

    order = OrderRequest(
        instrument = NIFTY, side = OrderSide.BUY, quantity = 65,
        orderType = OrderType.LIMIT, limitPrice = 24_800.37
    )

    assert order.limitPrice == 24_800.37


@pytest.mark.parametrize("productType", [
    ProductType.COVER, ProductType.BRACKET
])
def test_unrepresentableProductTypesAreRefused(
    productType : ProductType
) -> None:
    """
    Cover and bracket orders carry a mandatory stop loss leg that
    ``OrderRequest`` has no field for.

    Accepting one sends an order the broker rejects while looking
    locally valid, so refusing is the honest answer.
    """

    with pytest.raises(ValidationError) as failure:
        OrderRequest(
            instrument = TICKED, side = OrderSide.BUY, quantity = 10,
            productType = productType
        )

    assert "stop loss leg" in str(failure.value)


def test_lotSizeSuggestionRoundsUpAndIsNeverZero() -> None:
    """
    ``round()`` is banker's rounding: lot size 2 with quantity 5
    suggested 4, silently rounding the trader **down**. A sub-lot
    quantity suggested 0, which fails the ``gt = 0`` constraint.
    """

    with pytest.raises(ValidationError) as failure:
        OrderRequest(
            instrument = NIFTY, side = OrderSide.BUY, quantity = 10
        )

    message = str(failure.value)

    assert "use 65 instead" in message
    assert "use 0 instead" not in message


def test_notionalValueUsesTheTriggerForStopLossMarket() -> None:
    """
    An SL-M carries only a trigger price, and reporting ``None`` threw
    away the one estimate available.
    """

    order = OrderRequest(
        instrument = TICKED, side = OrderSide.SELL, quantity = 10,
        orderType = OrderType.STOP_LOSS_MARKET, triggerPrice = 99.50
    )

    assert order.notionalValue == pytest.approx(995.0)


# ---------------------------------------------------------------------
# resources
# ---------------------------------------------------------------------

async def test_callerRefusesWorkAfterClose() -> None:
    """
    ``__aexit__`` closes unconditionally, so reusing a caller past its
    context manager is easy. It used to surface as the pool's own
    "cannot schedule new futures after shutdown", which is not
    retryable and explains nothing.
    """

    clock = FakeClock()
    caller = BlockingCaller(
        RateLimitPolicy(clock = clock), clock, maxConcurrency = 1
    )

    await caller.call(EndpointClass.GENERAL, lambda : "ok")
    await caller.close()

    with pytest.raises(RuntimeError) as failure:
        await caller.call(EndpointClass.GENERAL, lambda : "ok")

    assert "closed" in str(failure.value)


async def test_closeDoesNotTouchTheDefaultExecutor() -> None:
    """
    ``close`` used ``run_in_executor(None, ...)``, which lazily
    instantiates the interpreter's default pool - the very thing this
    class exists to avoid - purely to perform one shutdown, and it
    deadlocks if the host has saturated that pool with work waiting on
    us.
    """

    clock = FakeClock()
    loop = asyncio.get_running_loop()

    caller = BlockingCaller(
        RateLimitPolicy(clock = clock), clock, maxConcurrency = 1
    )

    await caller.call(EndpointClass.GENERAL, lambda : "ok")

    before = getattr(loop, "_default_executor", None)

    await caller.close()

    # ? closing must not have instantiated the loop's default pool
    assert getattr(loop, "_default_executor", None) is before

    with pytest.raises(RuntimeError):
        caller.executor.submit(lambda : None)


async def test_concurrentProfilesDoNotCorruptEachOther() -> None:
    """
    ``tracemalloc`` is process global. Two overlapping profiles had the
    first to finish call ``stop()``, so the second measured zero and
    recommended a concurrency equal to the whole memory budget -
    recreating the ``maxConcurrency = 2_500`` mistake this library
    exists to prevent.
    """

    async def work() -> None:
        payload = [object() for _ in range(20_000)]

        await asyncio.sleep(0)

        assert payload

    profiles = await asyncio.gather(*(
        traceMemoryUsage(work, limitMegabytes = 2_048) for _ in range(4)
    ))

    for profile in profiles:
        # ! every profile measured something. Before the lock, whichever
        # finished second read (0, 0) because the first had already
        # called tracemalloc.stop()
        assert profile.peakMegabytes > 0
        assert 1 <= profile.recommendedConcurrency <= 64


async def test_recommendationIsCapped() -> None:
    """
    A cheap operation divides into a large budget and yields tens of
    thousands, and the docstring invites the caller to feed that
    straight into ``BlockingCaller``.

    Broker rate limits bind long before local memory does, so a large
    answer is always wrong even when the arithmetic is right - it is
    the ``maxConcurrency = 2_500`` mistake arrived at by division
    instead of by guesswork.
    """

    async def cheap() -> None:
        return None

    profile = await traceMemoryUsage(
        cheap, limitMegabytes = 64_000, bufferMegabytes = 0
    )

    assert profile.recommendedConcurrency <= 64


# ---------------------------------------------------------------------
# figures an operator reads
# ---------------------------------------------------------------------

def test_bucketDebtIsVisible() -> None:
    """
    ``available`` clamps at zero, so a bucket four seconds behind read
    identically to one that was momentarily empty. ``balance`` exposes
    the debt.
    """

    clock = FakeClock()
    bucket = TokenBucket(RateLimit("test", 10, 1.0), clock)

    bucket.consume(14)

    assert bucket.available == 0.0
    assert bucket.balance == pytest.approx(-4.0)


def test_sessionStatusMasksTheClientIdItself() -> None:
    """
    The class was documented as safe to return to a frontend "by
    construction", and masking happened at the one call site that built
    one. A second call site would have published a real account number.
    """

    status = SessionStatus(
        brokerName = "fyers", clientId = "FX0KN0G7PX-100"
    )

    assert "FX0KN0G7PX" not in status.clientId
    assert "FX0KN0G7PX" not in status.model_dump_json()
    assert status.clientId.endswith("-100")


def test_tokenMetadataIsNotSerialised() -> None:
    """
    A free-form dict is the one field on a bundle that could carry a
    credential, and Kite already puts a token-shaped value there.
    """

    bundle = TokenBundle.build(
        brokerName = "kite", clientId = "AB1234", accessToken = "t",
        issuedAt = MOMENT, metadata = {"publicToken" : "leaked-value"}
    )

    assert bundle.metadata["publicToken"] == "leaked-value"
    assert "leaked-value" not in bundle.model_dump_json()


def test_missingRequirementsMessageIsReadable() -> None:
    """
    The message rendered a raw list repr, brackets and quotes included.
    """

    from algotraders.errors import BrokerConfigurationError

    message = str(BrokerConfigurationError(
        name = "fyers", requirements = ["fyers-apiv3>=3.1.7"]
    ))

    assert "['" not in message
    assert "pip install algotraders[fyers]" in message


# ---------------------------------------------------------------------
# boundaries
# ---------------------------------------------------------------------

def test_negativeChunkStepIsRejected() -> None:
    """
    ``maxSpan`` was validated and ``step`` was not, so a negative step
    made the cursor non-increasing and the window list grew until the
    process died.
    """

    with pytest.raises(ValueError):
        splitDateRange(
            MOMENT - dt.timedelta(days = 10), MOMENT,
            maxSpan = dt.timedelta(days = 1), step = dt.timedelta(days = -1)
        )


def test_symbolLengthIsCheckedAfterCaseFolding() -> None:
    """
    ``max_length`` runs before the validator, and case folding can
    lengthen a string, so 64 ligature characters passed the constraint
    and were stored as 128.
    """

    with pytest.raises(ValidationError):
        InstrumentKey(
            exchange = Exchange.NSE, tradingSymbol = "ﬁ" * 64,
            instrumentType = InstrumentType.EQUITY
        )


def test_ncdexResolvesToTheCommoditySegment() -> None:
    """
    NCDEX was absent from ``DERIVATIVE_EXCHANGES``, so it fell through
    to the capital segment and would have resolved against the wrong
    instrument master.
    """

    from algotraders.models.enums import DERIVATIVE_EXCHANGES

    assert Exchange.NCDEX in DERIVATIVE_EXCHANGES

    key = InstrumentKey(
        exchange = Exchange.NCDEX, tradingSymbol = "GUARSEED10",
        instrumentType = InstrumentType.FUTURE,
        expiry = dt.date(2026, 2, 20)
    )

    assert key.segment is Segment.COMMODITY


def test_brokerNameIsNotAnEnvironmentSurface(
    monkeypatch : pytest.MonkeyPatch
) -> None:
    """
    ``brokerName`` was a settings field with no alias, so with
    case-insensitive matching a bare ``BROKERNAME`` export rewrote it on
    every credentials object.
    """

    from algotraders.config import credentialsFor

    monkeypatch.setenv("BROKERNAME", "attacker")
    monkeypatch.setenv("PIN", "9999")

    credentials = credentialsFor("fyers")

    assert credentials.brokerName == "fyers"

    # ? and Kite, which has no PIN concept, does not acquire one
    kite = credentialsFor("kite")

    assert kite.brokerName == "kite"
    assert kite.pin is None


def test_credentialFailureDoesNotChainTheInput() -> None:
    """
    A pydantic ``ValidationError`` echoes the offending ``input_value``,
    so a wrongly typed secret was printed verbatim in the chained
    traceback - which the redacting filter never sees, because it does
    not reach ``__cause__``.
    """

    from algotraders.config import credentialsFor
    from algotraders.errors import InvalidCredentials

    with pytest.raises(InvalidCredentials) as failure:
        credentialsFor("fyers", clientId = 1234567890)

    # ! the chained ValidationError is dropped entirely. Its
    # `input_value` echo is what leaked, and no filter reaches
    # __cause__ during traceback formatting
    assert failure.value.__cause__ is None
    assert "1234567890" not in str(failure.value)
    assert "ALGOTRADERS_FYERS_CLIENT_ID" in str(failure.value)
