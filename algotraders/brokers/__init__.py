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
    "FYERS API v2",
    modulePath = "algotraders.brokers.fyers.py",
    unifiedClass = "FyersAPI",
    requirements = ["fyers_apiv2"]
)

__all__ = [
    "registerBroker", "availableBrokers", "getBrokerInterface"
]
