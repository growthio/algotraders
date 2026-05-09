# -*- encoding: utf-8 -*-

"""
An Unified Interface to Connect to a Broker's API
-------------------------------------------------

The module tries to provide an unified interface to connect with any
Exchanges API to get real-time data, execute buy/sell signal based on
different strategies. The ``brokers`` are connected using an abstract
base class that provides unified functions.
"""

from algotraders.brokers.registry import (
    registerBroker, availableBrokers, getBrokerInterface
)

registerBroker(
    "fyers",
    API = "FyersAPI", AUTH = "FyersAuthentication",
    PATH = "algotraders.brokers.fyers", REQUIRES = ["fyers_apiv3"]
)

__all__ = [
    "registerBroker", "availableBrokers", "getBrokerInterface"
]
