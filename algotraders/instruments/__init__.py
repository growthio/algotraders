# -*- encoding: utf-8 -*-

"""
Instrument Resolution and Symbol Master Caching
================================================

Turns a broker neutral :class:`~algotraders.models.instrument.InstrumentKey`
into whatever the current broker needs in order to trade it: a formatted
symbol for Fyers, a numeric ``instrument_token`` for Kite.

Resolution happens locally against a cached symbol master, so an unknown
symbol fails immediately rather than after a rate limited round trip.
See :mod:`algotraders.instruments.base` for the degradation ladder that
governs what happens when a master download fails.
"""

from algotraders.instruments.cache import (
    InstrumentCache, DATA_SUFFIX, META_SUFFIX
)

from algotraders.instruments.base import (
    BaseInstrumentResolver, DEFAULT_SEGMENTS
)

__all__ = [
    "InstrumentCache", "DATA_SUFFIX", "META_SUFFIX",
    "BaseInstrumentResolver", "DEFAULT_SEGMENTS"
]
