# -*- encoding: utf-8 -*-

"""
Zerodha Kite Instrument Dump Download and Parsing
----------------------------------------------------

Where the Fyers resolver reads public CSVs, Kite requires an
authenticated ``instruments()`` call routed through the rate limiter
like any other request. That is the practical cost of Kite's design.

The value it buys is ``instrument_token``, without which no historical
request can be made at all. Those tokens are reissued by the exchanges
at **every derivative expiry**, which is precisely why the cache is
invalidated on the trading date rather than on a rolling time to live:
a dump downloaded last Thursday will resolve Friday's expiring contracts
to tokens that no longer address anything.
"""

import decimal
import datetime as dt

from typing import Any, Dict, List, Optional, Sequence

from algotraders.brokers.kite import limits, mapper
from algotraders.core.clock import BaseClock
from algotraders.errors import InstrumentMasterUnavailable
from algotraders.instruments.base import BaseInstrumentResolver
from algotraders.instruments.cache import InstrumentCache
from algotraders.logger import getLogger
from algotraders.models.enums import (
    EndpointClass, InstrumentType, OptionRight, Segment
)
from algotraders.models.instrument import Instrument, InstrumentKey

logger = getLogger(__name__)

# ? vendor instrument_type values worth distinguishing
VENDOR_EQUITY = "EQ"
VENDOR_FUTURE = "FUT"
VENDOR_CALL = "CE"
VENDOR_PUT = "PE"


class KiteInstrumentResolver(BaseInstrumentResolver):
    """
    Resolves canonical keys against the Kite instrument dump.

    :type  sessionManager: Any
    :param sessionManager: Authenticated ``KiteConnect`` client. Unlike
        Fyers, the dump is not public.

    :type  caller: Any
    :param caller: Rate limited caller, since fetching the dump consumes
        a real API call.
    """

    def __init__(
        self, cache : InstrumentCache, clock : BaseClock,
        sessionManager : Any = None, caller : Any = None,
        segments : Optional[Sequence[Segment]] = None, **extra : Any
    ) -> None:
        super().__init__(
            brokerName = limits.BROKER_NAME, cache = cache, clock = clock,
            segments = segments
        )

        self.sessionManager = sessionManager
        self.caller = caller


    async def downloadMaster(self, segment : Segment) -> List[Instrument]:
        """
        Fetch and parse the instrument dump for one segment.

        :raises InstrumentMasterUnavailable: No exchange in the segment
            could be fetched. Individual exchange failures are tolerated
            as long as one succeeds, so a BSE outage does not take NSE
            resolution down with it.

        :rtype:   List[Instrument]
        :returns: Every instrument in the segment.
        """

        if self.sessionManager is None:
            raise InstrumentMasterUnavailable(
                "the kite instrument dump requires an authenticated "
                "session; none was supplied to the resolver"
            )

        exchanges = limits.SEGMENT_EXCHANGES.get(segment, ())

        if not exchanges:
            return []

        instruments : List[Instrument] = []
        failures : List[str] = []

        for exchange in exchanges:
            try:
                rows = await self.__fetch__(exchange)
                parsed = self.parseDump(rows)

                logger.info(
                    "parsed %d kite instruments from %s",
                    len(parsed), exchange
                )

                instruments.extend(parsed)
            except Exception as error:
                logger.warning(
                    "kite %s dump unavailable: %s",
                    exchange, type(error).__name__
                )

                failures.append(exchange)

        if not instruments:
            raise InstrumentMasterUnavailable(
                f"no kite {segment.value} instrument dump could be "
                f"fetched (tried {len(exchanges)}, all failed)"
            )

        if failures:
            logger.warning(
                "%d of %d kite exchanges failed, resolution may be "
                "incomplete", len(failures), len(exchanges)
            )

        return instruments


    async def __fetch__(self, exchange : str) -> List[Dict[str, Any]]:
        """
        Call ``instruments(exchange)`` through the rate limiter.
        """

        if self.caller is None:
            import asyncio

            return await asyncio.to_thread(self.__instruments__, exchange)

        return await self.caller.call(
            EndpointClass.GENERAL, self.__instruments__, exchange
        )


    def __instruments__(self, exchange : str) -> List[Dict[str, Any]]:
        """
        Call the vendor instruments endpoint and translate any failure.
        """

        try:
            return self.sessionManager.instruments(exchange)
        except Exception as error:
            raise mapper.translateException(
                error, f"instruments {exchange}"
            ) from error


    def parseDump(self, rows : Sequence[Dict[str, Any]]) -> List[Instrument]:
        """
        Convert instrument dump rows into canonical instruments.

        Rows that cannot be interpreted are skipped rather than aborting
        the dump, because one unusual contract should not cost the whole
        exchange's resolution.

        :rtype:   List[Instrument]
        :returns: Successfully parsed instruments.
        """

        instruments : List[Instrument] = []
        skipped = 0

        for row in rows or []:
            try:
                instrument = self.__parseRow__(row)
            except Exception:
                skipped += 1

                continue

            if instrument is None:
                skipped += 1
            else:
                instruments.append(instrument)

        if skipped:
            logger.debug("skipped %d unparseable kite dump rows", skipped)

        return instruments


    def __parseRow__(self, row : Dict[str, Any]) -> Optional[Instrument]:
        """
        Build an instrument from one dump row.

        :rtype:   Optional[Instrument]
        :returns: The instrument, or ``None`` when the row describes
            something outside the supported model.
        """

        exchange = limits.EXCHANGE_MAP.get(
            str(row.get("exchange") or "").upper()
        )

        if exchange is None:
            return None

        vendorType = str(row.get("instrument_type") or "").upper()
        symbol = str(row.get("tradingsymbol") or "").strip()
        underlying = str(row.get("name") or "").strip().upper() or symbol

        if not symbol:
            return None

        expiry = _parseExpiry(row.get("expiry"))
        strike = _parseDecimal(row.get("strike"))

        if vendorType in (VENDOR_CALL, VENDOR_PUT):
            if expiry is None or strike is None:
                return None

            key = InstrumentKey(
                exchange = exchange, tradingSymbol = underlying,
                instrumentType = InstrumentType.OPTION,
                expiry = expiry, strike = strike,
                optionRight = OptionRight(vendorType)
            )
        elif vendorType == VENDOR_FUTURE:
            if expiry is None:
                return None

            key = InstrumentKey(
                exchange = exchange, tradingSymbol = underlying,
                instrumentType = InstrumentType.FUTURE, expiry = expiry
            )
        elif vendorType == VENDOR_EQUITY:
            key = InstrumentKey(
                exchange = exchange, tradingSymbol = symbol,
                instrumentType = InstrumentType.EQUITY
            )
        elif not vendorType and row.get("segment") == "INDICES":
            key = InstrumentKey(
                exchange = exchange, tradingSymbol = symbol,
                instrumentType = InstrumentType.INDEX
            )
        else:
            return None

        return Instrument(
            key = key,
            brokerSymbol = symbol,
            # ! the whole reason this resolver exists: historical calls
            # take this integer and accept no symbol string at all
            brokerToken = str(row.get("instrument_token") or "") or None,
            name = str(row.get("name") or "") or None,
            lotSize = _parseInt(row.get("lot_size")),
            tickSize = _parseDecimal(row.get("tick_size")),
            source = limits.BROKER_NAME
        )


    def toBrokerSymbol(self, instrument : Instrument) -> str:
        """
        Render an instrument as the Kite trading symbol.

        :rtype:   str
        :returns: The bare trading symbol, for example ``SBIN``. Note
            that quote calls need it prefixed with the exchange while
            order calls do not, which the API surface handles.
        """

        return instrument.brokerSymbol or instrument.key.tradingSymbol


def _parseExpiry(value : Any) -> Optional[dt.date]:
    """
    Parse the expiry column, which arrives as a date, a datetime or an
    ISO string depending on SDK version.
    """

    if value in (None, ""):
        return None

    if isinstance(value, dt.datetime):
        return value.date()

    if isinstance(value, dt.date):
        return value

    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _parseDecimal(value : Any) -> Optional[decimal.Decimal]:
    """
    Parse a decimal column, treating zero and blank as absent.
    """

    if value in (None, ""):
        return None

    try:
        parsed = decimal.Decimal(str(value))
    except (decimal.InvalidOperation, TypeError):
        return None

    return parsed if parsed > 0 else None


def _parseInt(value : Any) -> Optional[int]:
    """
    Parse an integer column, treating zero and blank as absent.
    """

    if value in (None, ""):
        return None

    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return None

    return parsed if parsed > 0 else None


__all__ = ["KiteInstrumentResolver"]
