# -*- encoding: utf-8 -*-

"""
Zerodha Kite Connect v3 Broker Integration
============================================

Kite Connect is Zerodha's paid API. Two facts drive every design
decision in this package and both differ from Fyers.

**Historical data is keyed on an integer instrument_token.** No endpoint
accepts a symbol string, and the tokens are reissued by the exchanges at
every derivative expiry. That is the reason the instrument resolution
layer exists at all, and why the instrument cache is invalidated per
trading day rather than on a rolling time to live.

**There is no unattended token refresh.** Access tokens expire at 06:00
IST the next day as a regulatory requirement, and a standard application
cannot renew one. A Kite deployment therefore needs an interactive login
every trading morning; the library surfaces that as
``AuthorizationRequired`` with a login URL rather than pretending
otherwise.

Historical data is additionally a paid add on, so an account without it
fails every historical call with a permission error. That maps to
``DataSubscriptionRequired``, which is deliberately not retryable: it is
a billing action, not a transient fault.

:NOTE: Concrete classes are resolved lazily, so reading ``limits`` during
    broker registration does not require ``kiteconnect`` to be installed.
"""

from typing import Any

from algotraders.brokers.kite import limits

__all__ = [
    "limits", "KiteAuthentication", "KiteAPI", "KiteInstrumentResolver",
    "KiteStream"
]

# ? name to defining submodule, resolved on first attribute access
_LAZY_EXPORTS = {
    "KiteAuthentication" : "algotraders.brokers.kite.auth",
    "KiteAPI" : "algotraders.brokers.kite.api",
    "KiteInstrumentResolver" : "algotraders.brokers.kite.symbols",
    "KiteStream" : "algotraders.brokers.kite.stream"
}


def __getattr__(name : str) -> Any:
    """
    Resolve a concrete Kite class on first access.

    :raises AttributeError: The name is not exported.

    :rtype:   Any
    :returns: The requested class.
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
