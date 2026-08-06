# -*- encoding: utf-8 -*-

"""
Fyers Order Placement, Amendment and Portfolio Retrieval
-----------------------------------------------------------

The assertion that matters most here is
:func:`test_orderPlacementIsNeverRetried`. Every other broker call in
this library retries a transient failure, and for market data that is
plainly correct. For an order it is not: a placement that timed out may
already have reached the exchange, and a second attempt produces a
second live position. There is no safe automatic recovery from that,
only reconciliation against the order tag.
"""

import datetime as dt

import pytest

from algotraders.brokers.fyers import limits
from algotraders.brokers.fyers.api import FyersAPI
from algotraders.core.clock import IST
from algotraders.core.executor import BlockingCaller
from algotraders.core.ratelimit import RateLimitPolicy
from algotraders.errors import OrderRejected, TransientBrokerError
from algotraders.models.enums import (
    EndpointClass, OrderSide, OrderStatus, OrderType, ProductType
)
from algotraders.models.instrument import Instrument, InstrumentKey
from algotraders.models.orders import OrderRequest
from tests.fakes.clock import FakeClock
from tests.fakes.session import FakeSessionManager, repeating

NOW = dt.datetime(2026, 8, 5, 10, 0, tzinfo = IST)

EQUITY = Instrument(
    key = InstrumentKey.parse("NSE:SBIN:EQ"),
    brokerSymbol = "NSE:SBIN-EQ", lotSize = 1, source = "fyers"
)

NIFTY_FUTURE = Instrument(
    key = InstrumentKey.parse("NFO:NIFTY:FUT:2026-01-27"),
    brokerSymbol = "NSE:NIFTY26JANFUT", lotSize = 65, source = "fyers"
)


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


def marketOrder(**overrides : object) -> OrderRequest:
    """
    Build a valid market order, overriding fields per test.
    """

    fields = dict(
        instrument = EQUITY, side = OrderSide.BUY, quantity = 10,
        orderType = OrderType.MARKET, productType = ProductType.INTRADAY
    )
    fields.update(overrides)

    return OrderRequest(**fields)


async def test_placeOrderSendsTheMappedPayload(clock : FakeClock) -> None:
    """
    A validated request reaches the vendor in its own encoding.
    """

    sessionManager = FakeSessionManager({
        "place_order" : repeating({"s" : "ok", "id" : "25080500099001"})
    })
    api = buildAPI(clock, sessionManager)

    response = await api.placeOrder(
        marketOrder(
            instrument = NIFTY_FUTURE, quantity = 130,
            orderType = OrderType.LIMIT, limitPrice = 24_800.0,
            tag = "orb-entry"
        )
    )

    payload = sessionManager.callsTo("place_order")[0]["data"]

    assert payload["symbol"] == "NSE:NIFTY26JANFUT"
    assert payload["qty"] == 130
    assert payload["side"] == 1
    assert payload["type"] == 1
    assert payload["orderTag"] == "orb-entry"
    assert response.orderId == "25080500099001"

    await api.caller.close()


async def test_orderPlacementIsNeverRetried(clock : FakeClock) -> None:
    """
    A transient failure on placement makes exactly one attempt.

    Retrying is unsafe: the order may already be live at the exchange
    and a second attempt is a double fill. Reconciliation against the
    order tag is the documented recovery, not an automatic retry.
    """

    sessionManager = FakeSessionManager({
        "place_order" : repeating(
            {"API" : "/orders", "error" : "Expecting value: line 1 column 1"}
        )
    })
    api = buildAPI(clock, sessionManager)

    with pytest.raises(TransientBrokerError):
        await api.placeOrder(marketOrder())

    assert sessionManager.countOf("place_order") == 1
    assert clock.sleepCalls == []

    await api.caller.close()


async def test_marketDataStillRetries(clock : FakeClock) -> None:
    """
    The no-retry rule is scoped to order endpoints, not global.
    """

    assert api_policy_attempts(clock) > 1


def api_policy_attempts(clock : FakeClock) -> int:
    """
    Read the retry budget the executor applies to historical calls.
    """

    api = buildAPI(clock, FakeSessionManager())

    return api.caller.policyFor(EndpointClass.HISTORICAL).maxAttempts


async def test_rejectedOrderRaisesWithBrokerMessage(
    clock : FakeClock
) -> None:
    """
    A refusal surfaces the broker's own wording, which is the only
    actionable detail the end user gets.
    """

    sessionManager = FakeSessionManager({
        "place_order" : repeating({
            "s" : "ok", "id" : "1", "status" : 5,
            "message" : "RED: Insufficient funds"
        })
    })
    api = buildAPI(clock, sessionManager)

    with pytest.raises(OrderRejected) as failure:
        await api.placeOrder(marketOrder())

    assert failure.value.brokerMessage == "RED: Insufficient funds"

    await api.caller.close()


async def test_modifyOrderOmitsUnchangedFields(clock : FakeClock) -> None:
    """
    Only what the caller supplied is sent, so an amendment cannot
    accidentally reset a price it did not mean to touch.
    """

    sessionManager = FakeSessionManager({
        "modify_order" : repeating({"s" : "ok", "id" : "9001"})
    })
    api = buildAPI(clock, sessionManager)

    await api.modifyOrder("9001", limitPrice = 815.5)

    payload = sessionManager.callsTo("modify_order")[0]["data"]

    assert payload == {"id" : "9001", "limitPrice" : 815.5}

    await api.caller.close()


async def test_cancelOrderUsesTheOrderId(clock : FakeClock) -> None:
    """
    Cancellation is keyed on the broker's identifier.
    """

    sessionManager = FakeSessionManager({
        "cancel_order" : repeating({"s" : "ok", "id" : "9001", "status" : 1})
    })
    api = buildAPI(clock, sessionManager)

    response = await api.cancelOrder("9001")

    assert sessionManager.callsTo("cancel_order")[0]["data"] == {"id" : "9001"}
    assert response.status is OrderStatus.CANCELLED
    assert response.isTerminal is True

    await api.caller.close()


async def test_orderBookSurvivesARejectedRow(clock : FakeClock) -> None:
    """
    One rejection must not hide the rest of the day's orders.

    The caller asked for the book, so a rejected row is reported as a
    row rather than raised as an exception over the whole request.
    """

    sessionManager = FakeSessionManager({
        "orderbook" : repeating({"s" : "ok", "orderBook" : [
            {"id" : "1", "status" : 6, "qty" : 10},
            {"id" : "2", "status" : 5, "message" : "RED: rejected"},
            {"id" : "3", "status" : 2, "qty" : 5, "filledQty" : 5}
        ]})
    })
    api = buildAPI(clock, sessionManager)

    orders = await api.fetchOrders()

    assert len(orders) == 3
    assert orders[0].status is OrderStatus.OPEN
    assert orders[1].statusMessage == "RED: rejected"
    assert orders[2].status is OrderStatus.COMPLETE

    await api.caller.close()


async def test_positionsNormalizeShortExposure(clock : FakeClock) -> None:
    """
    A short position carries a negative quantity, which is the
    convention every consumer above relies on.
    """

    sessionManager = FakeSessionManager({
        "positions" : repeating({"s" : "ok", "netPositions" : [
            {
                "symbol" : "NSE:NIFTY26JANFUT", "netQty" : -65,
                "netAvg" : 24_750.0, "ltp" : 24_700.0,
                "productType" : "INTRADAY", "unrealized_profit" : 3_250.0
            },
            {
                "symbol" : "NSE:SBIN-EQ", "netQty" : 0, "netAvg" : 810.0,
                "ltp" : 814.0, "productType" : "INTRADAY"
            }
        ]})
    })
    api = buildAPI(clock, sessionManager)

    positions = await api.fetchPositions()

    assert positions[0].isShort is True
    assert positions[0].quantity == -65

    # ? Fyers keeps closed rows for the rest of the session, so a caller
    # wanting live exposure has to filter rather than trust the count
    assert positions[1].isFlat is True

    await api.caller.close()


async def test_holdingsSeparateSellableQuantity(clock : FakeClock) -> None:
    """
    Pledged or unsettled stock counts toward the holding but cannot be
    sold, and an order for the difference is rejected by the exchange.
    """

    sessionManager = FakeSessionManager({
        "holdings" : repeating({"s" : "ok", "holdings" : [{
            "symbol" : "NSE:SBIN-EQ", "quantity" : 100,
            "remainingQuantity" : 60, "costPrice" : 800.0, "ltp" : 814.0
        }]})
    })
    api = buildAPI(clock, sessionManager)

    holdings = await api.fetchHoldings()

    assert holdings[0].quantity == 100
    assert holdings[0].availableQuantity == 60
    assert holdings[0].unrealisedProfit == pytest.approx(1_400.0)

    await api.caller.close()


async def test_portfolioFallsBackToTheVendorSymbol(
    clock : FakeClock
) -> None:
    """
    An unresolvable symbol degrades to the vendor spelling rather than
    dropping the row.

    Losing an entire portfolio because one delisted holding no longer
    appears in the symbol master would be a poor trade.
    """

    sessionManager = FakeSessionManager({
        "holdings" : repeating({"s" : "ok", "holdings" : [{
            "symbol" : "NSE:DELISTED-EQ", "quantity" : 5,
            "costPrice" : 10.0
        }]})
    })
    api = buildAPI(clock, sessionManager)

    holdings = await api.fetchHoldings()

    assert holdings[0].instrumentKey == "NSE:DELISTED-EQ"

    await api.caller.close()


async def test_ordersAreChargedAgainstTheSharedQuota(
    clock : FakeClock
) -> None:
    """
    Order calls contend with market data on the one Fyers app quota, so
    a burst of placements is paced like any other call.
    """

    sessionManager = FakeSessionManager({
        "place_order" : repeating({"s" : "ok", "id" : "1"})
    })
    api = buildAPI(clock, sessionManager)

    for _ in range(12):
        await api.placeOrder(marketOrder())

    assert sessionManager.countOf("place_order") == 12
    assert len(clock.sleepCalls) == 2

    await api.caller.close()
