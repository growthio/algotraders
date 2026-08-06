# -*- encoding: utf-8 -*-

"""
Zerodha Kite Connect v3 Data and Trading Surface
---------------------------------------------------

Structurally the mirror of the Fyers surface, which is the point: two
brokers with genuinely different APIs reaching the same normalized
result is what proves the abstraction rather than merely asserting it.

The differences that matter are all pushed into ``limits.py`` and
``mapper.py``, with one exception visible here.

**Historical data is keyed on an integer instrument token, not a
symbol.** ``historical_data`` takes the numeric ``instrument_token`` from
the downloaded instrument dump; no symbol string is accepted anywhere.
That single fact is the reason the instrument resolution layer exists,
and why a request here reads ``instrument.brokerToken`` rather than
``instrument.brokerSymbol``.
"""

import datetime as dt

from typing import Any, Dict, List, Optional, Sequence, Tuple

from algotraders.brokers.base import BaseBrokerAPI
from algotraders.brokers.kite import limits, mapper
from algotraders.core.chunking import clampToNow, splitDateRange
from algotraders.errors import InstrumentNotFound, UnsupportedTimeframe
from algotraders.logger import getLogger
from algotraders.models.enums import EndpointClass, Timeframe
from algotraders.models.instrument import Instrument
from algotraders.models.market import Candle, Quote
from algotraders.models.orders import OrderRequest, OrderResponse
from algotraders.models.portfolio import Holding, Position
from algotraders.models.timezone import toIST

logger = getLogger(__name__)

# ? the vendor caps a quote batch at 500 instruments
QUOTE_BATCH_SIZE = 500


class KiteAPI(BaseBrokerAPI):
    """
    Data and trading access for Zerodha Kite Connect v3.

    :type  sessionManager: Any
    :param sessionManager: Authenticated ``KiteConnect`` client.

    :type  caller: BlockingCaller
    :param caller: The only permitted route to the vendor SDK, applying
        Kite's per endpoint rate limits.
    """

    brokerName = limits.BROKER_NAME

    async def fetchCandles(
        self, instrument : Instrument, timeframe : Timeframe,
        dateRange : Tuple[dt.datetime, dt.datetime],
        allowAggregation : bool = False, **kwargs : Any
    ) -> List[Candle]:
        """
        Fetch historical candles, splitting the range per interval cap.

        :raises InstrumentNotFound: The instrument carries no
            ``brokerToken``. Kite cannot be queried by symbol, so an
            unresolved instrument is a hard failure here rather than a
            request the broker would reject.

        :raises UnsupportedTimeframe: Kite cannot serve the timeframe
            and aggregation was not requested.

        :raises DataSubscriptionRequired: The account has not bought the
            historical data add on.

        :rtype:   List[Candle]
        :returns: Candles in ascending time order.
        """

        if timeframe not in limits.TIMEFRAME_MAP:
            if not allowAggregation:
                raise UnsupportedTimeframe(
                    brokerName = self.brokerName, timeframe = timeframe,
                    supported = list(limits.TIMEFRAME_MAP)
                )

            return await self.__aggregate__(instrument, timeframe, dateRange)

        if not instrument.brokerToken:
            raise InstrumentNotFound(
                f"{instrument.instrumentKey} has no kite instrument_token; "
                "refresh the instrument master before requesting history",
                brokerName = self.brokerName
            )

        interval = mapper.toBrokerTimeframe(timeframe)
        token = int(instrument.brokerToken)

        start, end = clampToNow(dateRange[0], dateRange[1], self.clock.now())
        windows = splitDateRange(start, end, limits.CHUNK_SPAN[timeframe])

        logger.debug(
            "fetching kite token %d %s in %d window(s)",
            token, timeframe.value, len(windows)
        )

        collected : List[Candle] = []

        for windowStart, windowEnd in windows:
            rows = await self.caller.call(
                EndpointClass.HISTORICAL, self.__history__,
                token, interval, windowStart, windowEnd,
                bool(kwargs.get("continuous", False)),
                bool(kwargs.get("openInterest", False))
            )

            collected.extend(mapper.toCandles(rows, instrument, timeframe))

        return _deduplicate(collected)


    def __history__(
        self, token : int, interval : str, start : dt.datetime,
        end : dt.datetime, continuous : bool, openInterest : bool
    ) -> List[Dict[str, Any]]:
        """
        Call the vendor historical endpoint and translate any failure.

        Translation happens inside the retried operation so a transient
        network fault is classified before the retry decision, matching
        the Fyers surface.
        """

        try:
            return self.sessionManager.historical_data(
                instrument_token = token,
                from_date = toIST(start).strftime(limits.DATETIME_FORMAT),
                to_date = toIST(end).strftime(limits.DATETIME_FORMAT),
                interval = interval,
                continuous = continuous,
                oi = openInterest
            )
        except Exception as error:
            raise mapper.translateException(
                error, f"historical {token}"
            ) from error


    async def fetchQuote(
        self, instruments : Sequence[Instrument]
    ) -> List[Quote]:
        """
        Fetch current snapshots, batching to limit request count.

        Kite allows one quote call per second, the tightest limit it
        publishes, so batching is essential rather than an optimisation.

        :rtype:   List[Quote]
        :returns: One quote per instrument the broker returned.
        """

        if not instruments:
            return []

        byKey : Dict[str, Instrument] = {}

        for instrument in instruments:
            byKey[
                f"{instrument.key.exchange.value}:"
                f"{instrument.brokerSymbol}"
            ] = instrument

        keys = list(byKey)
        quotes : List[Quote] = []
        timestamp = self.clock.now()

        for index in range(0, len(keys), QUOTE_BATCH_SIZE):
            batch = keys[index:index + QUOTE_BATCH_SIZE]

            payload = await self.caller.call(
                EndpointClass.QUOTE, self.__quotes__, batch
            )

            for identifier, entry in (payload or {}).items():
                instrument = byKey.get(identifier)

                if instrument is None:
                    continue

                quotes.append(mapper.toQuote(entry, instrument, timestamp))

        return quotes


    def __quotes__(self, keys : Sequence[str]) -> Dict[str, Any]:
        """
        Call the vendor quote endpoint and translate any failure.
        """

        try:
            return self.sessionManager.quote(list(keys))
        except Exception as error:
            raise mapper.translateException(error, "quote") from error


    async def __aggregate__(
        self, instrument : Instrument, timeframe : Timeframe,
        dateRange : Tuple[dt.datetime, dt.datetime]
    ) -> List[Candle]:
        """
        Fold daily candles into a timeframe Kite does not serve.
        """

        from algotraders.core.resample import aggregateCandles

        daily = await self.fetchCandles(
            instrument, Timeframe.DAY_1, dateRange
        )

        return aggregateCandles(daily, timeframe)


    # -----------------------------------------------------------------
    # Trading
    # -----------------------------------------------------------------


    async def placeOrder(self, request : OrderRequest) -> OrderResponse:
        """
        Submit an order to the exchange.

        :raises OrderRejected: Zerodha refused the order.

        :rtype:   OrderResponse
        :returns: An acknowledgement carrying the order id. Kite returns
            only the id at placement, so the remaining fields are echoed
            from the request rather than invented.
        """

        payload = mapper.toOrderPayload(request)

        orderId = await self.caller.call(
            EndpointClass.ORDER, self.__placeOrder__, payload
        )

        logger.info(
            "placed %s %s x%d as kite order %s",
            request.side.value, request.instrument.brokerSymbol,
            request.quantity, orderId
        )

        return OrderResponse(
            orderId = str(orderId),
            instrumentKey = request.instrument.instrumentKey,
            side = request.side,
            orderType = request.orderType,
            productType = request.productType,
            quantity = request.quantity,
            limitPrice = request.limitPrice,
            triggerPrice = request.triggerPrice,
            tag = request.tag,
            source = mapper.SOURCE
        )


    def __placeOrder__(self, payload : Dict[str, Any]) -> Any:
        """
        Call the vendor placement endpoint and translate any failure.
        """

        try:
            return self.sessionManager.place_order(**payload)
        except Exception as error:
            raise mapper.translateException(error, "place_order") from error


    async def modifyOrder(
        self, orderId : str, quantity : Optional[int] = None,
        limitPrice : Optional[float] = None,
        triggerPrice : Optional[float] = None, **kwargs : Any
    ) -> OrderResponse:
        """
        Amend a working order, leaving omitted fields unchanged.
        """

        payload : Dict[str, Any] = {
            "variety" : kwargs.pop("variety", "regular"), "order_id" : orderId
        }

        if quantity is not None:
            payload["quantity"] = quantity

        if limitPrice is not None:
            payload["price"] = float(limitPrice)

        if triggerPrice is not None:
            payload["trigger_price"] = float(triggerPrice)

        payload.update(kwargs)

        result = await self.caller.call(
            EndpointClass.ORDER, self.__modifyOrder__, payload
        )

        return OrderResponse(
            orderId = str(result or orderId), source = mapper.SOURCE
        )


    def __modifyOrder__(self, payload : Dict[str, Any]) -> Any:
        """
        Call the vendor modify endpoint and translate any failure.
        """

        try:
            return self.sessionManager.modify_order(**payload)
        except Exception as error:
            raise mapper.translateException(error, "modify_order") from error


    async def cancelOrder(self, orderId : str) -> OrderResponse:
        """
        Cancel a working order.
        """

        result = await self.caller.call(
            EndpointClass.ORDER, self.__cancelOrder__, orderId
        )

        return OrderResponse(
            orderId = str(result or orderId),
            status = mapper.ORDER_STATUS_MAP["CANCELLED"],
            source = mapper.SOURCE
        )


    def __cancelOrder__(self, orderId : str) -> Any:
        """
        Call the vendor cancel endpoint and translate any failure.
        """

        try:
            return self.sessionManager.cancel_order(
                variety = "regular", order_id = orderId
            )
        except Exception as error:
            raise mapper.translateException(error, "cancel_order") from error


    async def fetchOrders(self) -> List[OrderResponse]:
        """
        Retrieve the day's order book.
        """

        rows = await self.caller.call(
            EndpointClass.PORTFOLIO, self.__orders__
        )

        return [mapper.toOrderResponse(row) for row in rows or []]


    def __orders__(self) -> List[Dict[str, Any]]:
        """
        Call the vendor order book endpoint and translate any failure.
        """

        try:
            return self.sessionManager.orders()
        except Exception as error:
            raise mapper.translateException(error, "orders") from error


    async def fetchPositions(self) -> List[Position]:
        """
        Retrieve open positions in the trading book.

        Kite splits its response into ``net`` and ``day``; the net book
        is what corresponds to open exposure.

        :rtype:   List[Position]
        :returns: Positions with short exposure carried as negative.
        """

        payload = await self.caller.call(
            EndpointClass.PORTFOLIO, self.__positions__
        )

        rows = (payload or {}).get("net") or []

        return [
            mapper.toPosition(row, await self.__canonicalKey__(row))
            for row in rows
        ]


    def __positions__(self) -> Dict[str, Any]:
        """
        Call the vendor positions endpoint and translate any failure.
        """

        try:
            return self.sessionManager.positions()
        except Exception as error:
            raise mapper.translateException(error, "positions") from error


    async def fetchHoldings(self) -> List[Holding]:
        """
        Retrieve settled stock in the demat account.
        """

        rows = await self.caller.call(
            EndpointClass.PORTFOLIO, self.__holdings__
        )

        return [
            mapper.toHolding(row, await self.__canonicalKey__(row))
            for row in rows or []
        ]


    def __holdings__(self) -> List[Dict[str, Any]]:
        """
        Call the vendor holdings endpoint and translate any failure.
        """

        try:
            return self.sessionManager.holdings()
        except Exception as error:
            raise mapper.translateException(error, "holdings") from error


    async def __canonicalKey__(self, row : Dict[str, Any]) -> str:
        """
        Resolve a vendor portfolio row back to a canonical key.

        Falls back to the vendor symbol when resolution fails, so one
        delisted holding cannot cost the whole portfolio.
        """

        symbol = str(row.get("tradingsymbol") or "unknown")

        if self.resolver is None:
            return symbol

        try:
            instrument = await self.resolver.resolveBrokerSymbol(symbol)

            return instrument.instrumentKey
        except Exception:
            logger.debug("could not resolve kite symbol %r", symbol)

            return symbol


def _deduplicate(candles : List[Candle]) -> List[Candle]:
    """
    Sort candles and drop duplicates at chunk boundaries.

    :rtype:   List[Candle]
    :returns: Ascending, one candle per timestamp.
    """

    unique : Dict[Tuple[str, str, dt.datetime], Candle] = {}

    for candle in candles:
        unique[
            (candle.instrumentKey, candle.timeframe.value, candle.timestamp)
        ] = candle

    return sorted(unique.values(), key = lambda entry: entry.timestamp)


__all__ = ["KiteAPI", "QUOTE_BATCH_SIZE"]
