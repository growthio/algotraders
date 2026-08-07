# -*- encoding: utf-8 -*-

"""
A Concrete Instrument Resolver Backed by a Local Sample Master
----------------------------------------------------------------

:class:`BaseInstrumentResolver` is abstract, and its two abstract
methods are exactly the broker specific parts, so testing the shared
machinery needs a concrete subclass. This one parses the CSV under
``tests/data`` and can be told to fail its download on demand, which is
how the degradation ladder is exercised without touching a network.
"""

import csv
import decimal
import pathlib
import datetime as dt

from typing import List, Optional, Sequence

from algotraders.core.clock import BaseClock
from algotraders.errors import InstrumentMasterUnavailable
from algotraders.instruments.base import BaseInstrumentResolver
from algotraders.instruments.cache import InstrumentCache
from algotraders.models.enums import (
    Exchange, InstrumentType, OptionRight, Segment
)
from algotraders.models.instrument import Instrument, InstrumentKey

SAMPLE_MASTER = (
    pathlib.Path(__file__).resolve().parent.parent / "data" / "sample_master.csv"
)


class SampleResolver(BaseInstrumentResolver):
    """
    Resolver reading a fixed CSV, with an injectable failure switch.

    :type  sourcePath: Optional[pathlib.Path]
    :param sourcePath: Master file to parse. Defaults to the bundled
        sample.

    :type  failDownload: bool
    :param failDownload: When true, ``downloadMaster`` raises as though
        the network were unavailable.
    """

    def __init__(
        self, cache : InstrumentCache, clock : BaseClock,
        sourcePath : Optional[pathlib.Path] = None,
        failDownload : bool = False,
        segments : Optional[Sequence[Segment]] = None,
        brokerName : str = "sample"
    ) -> None:
        super().__init__(
            brokerName = brokerName, cache = cache, clock = clock,
            segments = segments
        )

        self.sourcePath = sourcePath or SAMPLE_MASTER
        self.failDownload = failDownload
        self.downloadCalls = 0


    async def downloadMaster(self, segment : Segment) -> List[Instrument]:
        """
        Parse the sample CSV, returning only rows for this segment.
        """

        self.downloadCalls += 1

        if self.failDownload:
            raise InstrumentMasterUnavailable(
                f"simulated network failure for {segment.value}"
            )

        instruments : List[Instrument] = []

        with self.sourcePath.open("r", encoding = "utf-8", newline = "") as fh:
            for row in csv.DictReader(fh):
                instrument = self.__parseRow__(row)

                if instrument.key.segment is segment:
                    instruments.append(instrument)

        return instruments


    def toBrokerSymbol(self, instrument : Instrument) -> str:
        """
        Render the Fyers style ``<exchange>:<symbol>-<class>`` form.
        """

        key = instrument.key

        if key.instrumentType is InstrumentType.EQUITY:
            return f"{key.exchange.value}:{key.tradingSymbol}-EQ"

        return f"{key.exchange.value}:{key.tradingSymbol}"


    def __parseRow__(self, row : dict) -> Instrument:
        """
        Build an instrument from one CSV row.
        """

        instrumentType = InstrumentType(row["instrument_type"])

        expiry = (
            dt.date.fromisoformat(row["expiry"]) if row.get("expiry") else None
        )
        strike = (
            decimal.Decimal(row["strike"]) if row.get("strike") else None
        )
        right = (
            OptionRight(row["option_right"]) if row.get("option_right")
            else None
        )

        key = InstrumentKey(
            exchange = Exchange(row["exchange"]),
            tradingSymbol = row["trading_symbol"],
            instrumentType = instrumentType,
            expiry = expiry, strike = strike, optionRight = right
        )

        return Instrument(
            key = key,
            brokerSymbol = row["broker_symbol"],
            brokerToken = row.get("broker_token") or None,
            name = row.get("name") or None,
            lotSize = int(row["lot_size"]) if row.get("lot_size") else None,
            source = self.brokerName
        )
