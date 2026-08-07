# -*- encoding: utf-8 -*-

"""
Fyers Symbol Master Download and Parsing
------------------------------------------

Fyers publishes its instrument masters as public CSV files needing no
authentication, one per exchange and segment. That makes this resolver
simpler than the Kite one, which has to spend an authenticated, rate
limited API call to get the same information.

The files are **headerless** and positional, so the column indices below
are the contract. They are the part most likely to break: Fyers has been
migrating some masters toward a JSON form, and a column insertion would
silently shift every field. Parsing is therefore defensive, skipping any
row it cannot make sense of rather than failing the whole download, and
the resulting count is logged so a sudden drop is visible.

:NOTE: Column positions verified 2026-08-05. Re-verify before relying on
    them in a new deployment, and prefer adding a row count assertion in
    the host's monitoring over trusting them indefinitely.
"""

import csv
import io
import decimal
import urllib.error
import urllib.request
import datetime as dt

from typing import Any, Callable, List, Optional, Sequence

from algotraders.brokers.fyers import limits
from algotraders.core.clock import BaseClock
from algotraders.errors import InstrumentMasterUnavailable
from algotraders.instruments.base import BaseInstrumentResolver
from algotraders.instruments.cache import InstrumentCache
from algotraders.logger import getLogger
from algotraders.models.enums import (
    Exchange, InstrumentType, OptionRight, Segment
)
from algotraders.models.instrument import Instrument, InstrumentKey

logger = getLogger(__name__)

DOWNLOAD_TIMEOUT_SECONDS = 60

# ? positional columns in the public sym_details CSV. Named here so a
# vendor layout change is a single edit rather than a hunt through code
COLUMN_TOKEN = 0
COLUMN_DESCRIPTION = 1
COLUMN_LOT_SIZE = 3
COLUMN_TICK_SIZE = 4
COLUMN_ISIN = 5
COLUMN_EXPIRY = 8
COLUMN_SYMBOL_TICKER = 9
COLUMN_EXCHANGE = 10
COLUMN_UNDERLYING = 13
COLUMN_STRIKE = 15
COLUMN_OPTION_TYPE = 16

MINIMUM_COLUMNS = 17

EXCHANGE_CODES = {
    "10" : Exchange.NSE,
    "11" : Exchange.MCX,
    "12" : Exchange.BSE,
    "NSE" : Exchange.NSE,
    "BSE" : Exchange.BSE,
    "MCX" : Exchange.MCX
}


class FyersInstrumentResolver(BaseInstrumentResolver):
    """
    Resolves canonical instrument keys against the Fyers symbol masters.

    :type  cache: InstrumentCache
    :param cache: Where downloaded masters are stored between runs.

    :type  clock: BaseClock
    :param clock: Time source, used for trading-day cache freshness.

    :type  fetcher: Optional[Callable[[str], bytes]]
    :param fetcher: Downloads one URL and returns its bytes. Injectable
        so tests read a recorded master from ``tests/data`` with no
        network access at all.
    """

    def __init__(
        self, cache : InstrumentCache, clock : BaseClock,
        fetcher : Optional[Callable[[str], bytes]] = None,
        segments : Optional[Sequence[Segment]] = None, **extra : Any
    ) -> None:
        super().__init__(
            brokerName = limits.BROKER_NAME, cache = cache, clock = clock,
            segments = segments
        )

        self.fetcher = fetcher or _downloadURL


    async def downloadMaster(self, segment : Segment) -> List[Instrument]:
        """
        Download and parse every master file for one segment.

        Fyers splits a segment across exchanges, so NSE and BSE capital
        markets are two files that combine into one segment here.

        :raises InstrumentMasterUnavailable: No file for the segment
            could be downloaded. Individual file failures are tolerated
            as long as at least one succeeds, because a BSE outage
            should not take NSE resolution down with it.

        :rtype:   List[Instrument]
        :returns: Every instrument parsed from the segment.
        """

        import asyncio

        urls = limits.SYMBOL_MASTER_URLS.get(segment, ())

        if not urls:
            return []

        instruments : List[Instrument] = []
        failures : List[str] = []

        for url in urls:
            try:
                payload = await asyncio.to_thread(self.fetcher, url)
                parsed = self.parseMaster(payload)

                logger.info(
                    "parsed %d instruments from %s", len(parsed), url
                )

                instruments.extend(parsed)
            except Exception as error:
                logger.warning(
                    "fyers master %s unavailable: %s",
                    url, type(error).__name__
                )

                failures.append(url)

        if not instruments:
            raise InstrumentMasterUnavailable(
                f"no fyers {segment.value} master could be downloaded "
                f"(tried {len(urls)}, all failed)"
            )

        if failures:
            logger.warning(
                "%d of %d fyers %s masters failed, resolution may be "
                "incomplete", len(failures), len(urls), segment.value
            )

        return instruments


    def parseMaster(self, payload : bytes) -> List[Instrument]:
        """
        Parse a headerless Fyers symbol master CSV.

        Rows that cannot be interpreted are skipped rather than aborting
        the file, because one malformed contract should not cost the
        entire exchange's resolution.

        :type  payload: bytes
        :param payload: Raw CSV content.

        :rtype:   List[Instrument]
        :returns: Successfully parsed instruments.
        """

        text = payload.decode("utf-8", errors = "replace")
        instruments : List[Instrument] = []
        skipped = 0

        for row in csv.reader(io.StringIO(text)):
            if len(row) < MINIMUM_COLUMNS:
                skipped += 1

                continue

            try:
                instrument = self.__parseRow__(row)
            except Exception:
                skipped += 1

                continue

            if instrument is not None:
                instruments.append(instrument)

        if skipped:
            logger.debug("skipped %d unparseable master rows", skipped)

        return instruments


    def __parseRow__(self, row : List[str]) -> Optional[Instrument]:
        """
        Build an instrument from one positional CSV row.

        :rtype:   Optional[Instrument]
        :returns: The instrument, or ``None`` when the row describes
            something outside the supported model.
        """

        ticker = row[COLUMN_SYMBOL_TICKER].strip()

        if not ticker or ":" not in ticker:
            return None

        exchangeCode, _, remainder = ticker.partition(":")
        exchange = EXCHANGE_CODES.get(
            exchangeCode.strip().upper()
        ) or _exchangeFrom(row[COLUMN_EXCHANGE])

        if exchange is None:
            return None

        optionType = row[COLUMN_OPTION_TYPE].strip().upper()
        expiry = _parseExpiry(row[COLUMN_EXPIRY])
        strike = _parseDecimal(row[COLUMN_STRIKE])
        underlying = (row[COLUMN_UNDERLYING] or "").strip().upper()

        if optionType in ("CE", "PE") and expiry and strike is not None:
            key = InstrumentKey(
                exchange = exchange,
                tradingSymbol = underlying or remainder.split("2")[0],
                instrumentType = InstrumentType.OPTION,
                expiry = expiry, strike = strike,
                optionRight = OptionRight(optionType)
            )
        elif optionType in ("XX", "FUT") and expiry:
            key = InstrumentKey(
                exchange = exchange,
                tradingSymbol = underlying or remainder.replace("-FUT", ""),
                instrumentType = InstrumentType.FUTURE,
                expiry = expiry
            )
        elif remainder.upper().endswith("-EQ"):
            key = InstrumentKey(
                exchange = exchange,
                tradingSymbol = remainder[:-3],
                instrumentType = InstrumentType.EQUITY
            )
        elif remainder.upper().endswith("-INDEX"):
            key = InstrumentKey(
                exchange = exchange,
                tradingSymbol = remainder[:-6],
                instrumentType = InstrumentType.INDEX
            )
        else:
            return None

        return Instrument(
            key = key,
            brokerSymbol = ticker,
            brokerToken = (row[COLUMN_TOKEN] or "").strip() or None,
            name = (row[COLUMN_DESCRIPTION] or "").strip() or None,
            lotSize = _parseInt(row[COLUMN_LOT_SIZE]),
            tickSize = _parseDecimal(row[COLUMN_TICK_SIZE]),
            isin = (row[COLUMN_ISIN] or "").strip() or None,
            source = limits.BROKER_NAME
        )


    def toBrokerSymbol(self, instrument : Instrument) -> str:
        """
        Render an instrument as the Fyers ticker.

        Prefers the ticker recorded in the master, since deriving it
        from the canonical key cannot reproduce vendor specific contract
        naming for derivatives.

        :rtype:   str
        :returns: For example ``NSE:SBIN-EQ``.
        """

        if instrument.brokerSymbol:
            return instrument.brokerSymbol

        key = instrument.key

        if key.instrumentType is InstrumentType.EQUITY:
            return f"{key.exchange.value}:{key.tradingSymbol}-EQ"

        if key.instrumentType is InstrumentType.INDEX:
            return f"{key.exchange.value}:{key.tradingSymbol}-INDEX"

        return f"{key.exchange.value}:{key.tradingSymbol}"


def _downloadURL(url : str) -> bytes:
    """
    Fetch a URL with :mod:`urllib`, keeping the core dependency free.
    """

    try:
        with urllib.request.urlopen(
            url, timeout = DOWNLOAD_TIMEOUT_SECONDS
        ) as response:
            return response.read()
    except (urllib.error.URLError, OSError) as error:
        raise InstrumentMasterUnavailable(
            f"could not download {url}: {type(error).__name__}"
        ) from error


def _exchangeFrom(value : str) -> Optional[Exchange]:
    """
    Map a numeric or textual exchange column to the canonical enum.
    """

    return EXCHANGE_CODES.get((value or "").strip().upper())


def _parseExpiry(value : str) -> Optional[dt.date]:
    """
    Parse the expiry column, which carries an epoch or is empty.
    """

    text = (value or "").strip()

    if not text or text in ("0", "-1"):
        return None

    try:
        return dt.datetime.fromtimestamp(
            int(float(text)), tz = dt.timezone.utc
        ).date()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _parseDecimal(value : str) -> Optional[decimal.Decimal]:
    """
    Parse a decimal column, treating zero and blank as absent.
    """

    text = (value or "").strip()

    if not text:
        return None

    try:
        parsed = decimal.Decimal(text)
    except decimal.InvalidOperation:
        return None

    return parsed if parsed > 0 else None


def _parseInt(value : str) -> Optional[int]:
    """
    Parse an integer column, treating zero and blank as absent.
    """

    text = (value or "").strip()

    if not text:
        return None

    try:
        parsed = int(float(text))
    except (TypeError, ValueError):
        return None

    return parsed if parsed > 0 else None


__all__ = ["FyersInstrumentResolver"]
