# -*- encoding: utf-8 -*-

"""
FYERS API v3 Market Data Surface
----------------------------------

Implements historical candles and quotes. Trading follows in its own
change, which is why this class derives from
:class:`~algotraders.brokers.base.BaseBrokerData` rather than the full
API union.

Two behaviours here are the substance of the refactor.

**Chunking happens inside the call.** A caller asks for whatever range
it wants and the implementation splits it into windows Fyers accepts,
100 days for intraday resolutions and 366 for daily. The old example
script did this by hand with monthly buckets, producing twelve requests
per symbol whatever the real limit was, and fanning them out at 2500 way
concurrency.

**Dates are sent as ``yyyy-mm-dd`` strings, not epochs.** The previous
code converted with a bare ``x.timestamp()``, which reads the host's
local timezone and produced ranges that were hours off on any machine
not set to IST. Formatting an explicit IST date removes the ambiguity
entirely.
"""

import datetime as dt

from typing import Any, Dict, List, Sequence, Tuple

from algotraders.brokers.base import BaseBrokerData
from algotraders.brokers.fyers import limits, mapper
from algotraders.core.chunking import clampToNow, splitDateRange
from algotraders.core.clock import toIST
from algotraders.errors import UnsupportedTimeframe
from algotraders.logger import getLogger
from algotraders.models.enums import EndpointClass, Timeframe
from algotraders.models.instrument import Instrument
from algotraders.models.market import Candle, Quote

logger = getLogger(__name__)

# ? the vendor accepts either epoch seconds ("0") or yyyy-mm-dd ("1");
# the string form is used because it carries no timezone ambiguity
DATE_FORMAT = "1"

# ? quote requests are batched, and the vendor caps a batch at 50
QUOTE_BATCH_SIZE = 50


class FyersAPI(BaseBrokerData):
    """
    Implementation of concrete method to access FYERS API services for
    fetching historical and real time market data.

    :type  sessionManager: Any
    :param sessionManager: ``FyersModel`` client built by
        :class:`~algotraders.brokers.fyers.auth.FyersAuthentication`.

    :type  caller: BlockingCaller
    :param caller: The only permitted route to the vendor SDK. It runs
        the blocking call on a worker thread under the shared Fyers rate
        limit buckets.
    """

    brokerName = limits.BROKER_NAME

    async def fetchCandles(
        self, instrument : Instrument, timeframe : Timeframe,
        dateRange : Tuple[dt.datetime, dt.datetime],
        allowAggregation : bool = False, **kwargs : Any
    ) -> List[Candle]:
        """
        Fetch historical candles, splitting the range as required.

        :type  instrument: Instrument
        :param instrument: Resolved contract. Its ``brokerSymbol`` is
            sent to the vendor.

        :type  dateRange: Tuple[dt.datetime, dt.datetime]
        :param dateRange: Inclusive bounds. A future end is trimmed to
            now, since Fyers rejects a forward looking range rather than
            returning what it has.

        **Keyword Arguments**

        Passed through to the vendor to refine the response.

            * **contFlag** (*int*): Continuity flag for futures, joining
                consecutive contracts into one series. Defaults to 1.

        :raises UnsupportedTimeframe: Fyers cannot serve the timeframe
            and aggregation was not requested.

        :rtype:   List[Candle]
        :returns: Candles in ascending time order, deduplicated across
            chunk boundaries.
        """

        if timeframe not in limits.TIMEFRAME_MAP:
            if not allowAggregation:
                raise UnsupportedTimeframe(
                    brokerName = self.brokerName, timeframe = timeframe,
                    supported = list(limits.TIMEFRAME_MAP)
                )

            return await self.__aggregate__(instrument, timeframe, dateRange)

        resolution = mapper.toBrokerTimeframe(timeframe)
        contFlag = int(kwargs.get("contFlag", 1))

        start, end = clampToNow(
            dateRange[0], dateRange[1], self.clock.now()
        )

        windows = splitDateRange(
            start, end, limits.CHUNK_SPAN[timeframe]
        )

        logger.debug(
            "fetching %s %s in %d window(s)",
            instrument.brokerSymbol, timeframe.value, len(windows)
        )

        collected : List[Candle] = []

        for windowStart, windowEnd in windows:
            validated = await self.caller.call(
                EndpointClass.HISTORICAL,
                self.__history__,
                {
                    "symbol" : instrument.brokerSymbol,
                    "resolution" : resolution,
                    "date_format" : DATE_FORMAT,
                    "range_from" : _formatDate(windowStart),
                    "range_to" : _formatDate(windowEnd),
                    "cont_flag" : contFlag
                }
            )

            collected.extend(
                mapper.toCandles(validated, instrument, timeframe)
            )

        return _deduplicate(collected)


    def __history__(self, request : Dict[str, Any]) -> Dict[str, Any]:
        """
        Call the vendor history endpoint and validate the response.

        ! Validation runs **inside** the retried operation on purpose.
        The Fyers SDK returns a throttling failure rather than raising
        it, so validating after ``caller.call`` returned would put the
        classification outside the retry boundary and a 429 would never
        be retried, defeating the reason it is mapped to a retryable
        type at all.

        :raises RateLimitExceeded: The request was throttled.

        :rtype:   Dict[str, Any]
        :returns: The validated vendor payload.
        """

        return mapper.ensureSuccess(
            self.sessionManager.history(data = request),
            context = f"history {request.get('symbol')}"
        )


    async def fetchQuote(
        self, instruments : Sequence[Instrument]
    ) -> List[Quote]:
        """
        Fetch current snapshots, batching to limit request count.

        Quote endpoints carry the tightest limits of any broker call, so
        batching is not an optimisation here but a requirement.

        :rtype:   List[Quote]
        :returns: One quote per instrument the broker returned data for.
        """

        if not instruments:
            return []

        bySymbol : Dict[str, Instrument] = {
            instrument.brokerSymbol : instrument for instrument in instruments
        }

        symbols = list(bySymbol)
        quotes : List[Quote] = []
        timestamp = self.clock.now()

        for index in range(0, len(symbols), QUOTE_BATCH_SIZE):
            batch = symbols[index:index + QUOTE_BATCH_SIZE]

            validated = await self.caller.call(
                EndpointClass.QUOTE, self.__quotes__, ",".join(batch)
            )

            for entry in validated.get("d") or []:
                symbol = entry.get("n") or entry.get("symbol")
                instrument = bySymbol.get(str(symbol))

                if instrument is None:
                    logger.debug("unexpected quote symbol %r", symbol)

                    continue

                quotes.append(mapper.toQuote(entry, instrument, timestamp))

        return quotes


    def __quotes__(self, symbols : str) -> Dict[str, Any]:
        """
        Call the vendor quotes endpoint and validate the response.

        Validated inside the retried operation for the same reason as
        :meth:`__history__`.

        :rtype:   Dict[str, Any]
        :returns: The validated vendor payload.
        """

        return mapper.ensureSuccess(
            self.sessionManager.quotes(data = {"symbols" : symbols}),
            context = "quotes"
        )


    async def __aggregate__(
        self, instrument : Instrument, timeframe : Timeframe,
        dateRange : Tuple[dt.datetime, dt.datetime]
    ) -> List[Candle]:
        """
        Fold daily candles into a timeframe the broker does not serve.

        Only reached when the caller explicitly opted in, because
        synthesising a bar the broker never sent is a decision the
        caller has to make knowingly.
        """

        from algotraders.core.resample import aggregateCandles

        daily = await self.fetchCandles(
            instrument, Timeframe.DAY_1, dateRange
        )

        return aggregateCandles(daily, timeframe)


def _formatDate(moment : dt.datetime) -> str:
    """
    Render a datetime as the ``yyyy-mm-dd`` string Fyers expects, in
    IST.
    """

    return toIST(moment).strftime("%Y-%m-%d")


def _deduplicate(candles : List[Candle]) -> List[Candle]:
    """
    Sort candles and drop duplicates at chunk boundaries.

    Brokers occasionally return a bar just outside the range they were
    given, so adjacent windows can overlap by one bar even though the
    requested ranges do not.

    :rtype:   List[Candle]
    :returns: Ascending, one candle per timestamp.
    """

    unique : Dict[Tuple[str, str, dt.datetime], Candle] = {}

    for candle in candles:
        unique[
            (candle.instrumentKey, candle.timeframe.value, candle.timestamp)
        ] = candle

    return sorted(unique.values(), key = lambda entry: entry.timestamp)


__all__ = ["FyersAPI", "DATE_FORMAT", "QUOTE_BATCH_SIZE"]
