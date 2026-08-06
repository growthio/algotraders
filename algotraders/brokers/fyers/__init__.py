# -*- encoding: utf-8 -*-

"""
FYERS Securities Private Limited Broker's API Integration
==========================================================

Fyers API, released on late 2019, is the company's first major step
towards providing zero-cost trading APIs for retail traders. The
API can be used to fetch historical data, and execute buy/sell orders
of securities using a FYERS DEMAT account.

..versionchanged:: 2021-08-20 FYERS API v2 was Released:
    The version release was done before any implementation of v1 logic
    into our broker services. The details are documented as a process
    note, and no changes are yet made.

..versionchanged:: 2023-11-09 FYERS API v3 was Released:
    A stable and improved v3 version was released before implementation
    of the broker services. The details are documented as a process
    note, and this enforces more regulations as directed by SEBI.

..versionchanged:: 2026-08-06 Split Into a Package:
    ``brokers/fyers.py`` becomes ``brokers/fyers/``. Everything the
    broker can change unilaterally - rate limits, chunk spans, symbol
    master URLs, timeframe codes, the token policy - now lives in
    ``limits.py``, so a vendor change is a one line edit rather than a
    hunt through the implementation.

**Why the exports are lazy**

``auth`` and ``api`` import ``fyers_apiv3`` at module scope, which is
permitted for a broker implementation but must not happen at
``import algotraders`` time. The registry needs to read this broker's
capabilities and rate limits from ``limits`` during registration, and
``limits`` deliberately imports no vendor code, but importing it would
normally execute this file and pull the SDK in with it.

A module level ``__getattr__`` (PEP 562) breaks that chain: submodules
stay importable directly, while the concrete classes resolve only when
something actually reaches for them, which is exactly when
``getBrokerInterface`` calls :func:`getattr` on this module.

:NOTE: ``tests/unit/test_imports.py`` asserts that importing the package
    with ``fyers_apiv3`` hidden still succeeds, so a regression here
    fails CI rather than review.
"""

from typing import Any

from algotraders.brokers.fyers import limits

__all__ = [
    "limits", "FyersAuthentication", "FyersAPI", "FyersInstrumentResolver",
    "appIdHash"
]

# ? name to defining submodule, resolved on first attribute access
_LAZY_EXPORTS = {
    "FyersAuthentication" : "algotraders.brokers.fyers.auth",
    "appIdHash" : "algotraders.brokers.fyers.auth",
    "FyersAPI" : "algotraders.brokers.fyers.api",
    "FyersInstrumentResolver" : "algotraders.brokers.fyers.symbols"
}


def __getattr__(name : str) -> Any:
    """
    Resolve a concrete Fyers class on first access.

    :raises AttributeError: The name is not exported, matching the
        behaviour of ordinary module attribute access.

    :rtype:   Any
    :returns: The requested class or function.
    """

    module = _LAZY_EXPORTS.get(name)

    if module is None:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        )

    import importlib

    return getattr(importlib.import_module(module), name)


def __dir__() -> list:
    """
    Report the lazy exports so autocomplete and :func:`dir` still work.
    """

    return sorted(__all__)
