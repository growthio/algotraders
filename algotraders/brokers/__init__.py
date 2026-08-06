# -*- encoding: utf-8 -*-

"""
An Unified Interface to Connect to a Broker's API
-------------------------------------------------

The module tries to provide an unified interface to connect with any
Exchanges API to get real-time data, execute buy/sell signal based on
different strategies. The ``brokers`` are connected using an abstract
base class that provides unified functions.

Registration happens at import time but stores only strings, so
importing this module imports no vendor SDK. The broker module is
resolved lazily by :func:`getBrokerInterface` at the moment somebody
asks for that broker, which is what lets a Kite only deployment install
``algotraders[kite]`` and never need ``fyers-apiv3`` at all.

:NOTE: ``limits.py`` carries no vendor import, so reading a broker's
    capabilities and rate limits here is safe even when its SDK is
    absent. Never import a broker's ``auth`` or ``api`` module from this
    file; that would defeat the lazy loading entirely.
"""

from algotraders.brokers.fyers import limits as fyersLimits

from algotraders.brokers.registry import (
    Broker, BrokerRegistry, BrokerRegistryEntryPoint,
    registerBroker, unregisterBroker, availableBrokers,
    resolveBrokerName, missingRequirements, getBrokerInterface,
    brokerCapabilities
)

registerBroker(
    "fyers",
    API = "FyersAPI", AUTH = "FyersAuthentication",
    RESOLVER = "FyersInstrumentResolver",
    PATH = "algotraders.brokers.fyers", REQUIRES = ["fyers_apiv3"],
    CAPABILITIES = fyersLimits.CAPABILITIES, EXTRA = fyersLimits.PIP_EXTRA
)

__all__ = [
    "Broker", "BrokerRegistry", "BrokerRegistryEntryPoint",
    "registerBroker", "unregisterBroker", "availableBrokers",
    "resolveBrokerName", "missingRequirements", "getBrokerInterface",
    "brokerCapabilities"
]
