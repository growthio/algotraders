# -*- encoding: utf-8 -*-

"""
Custom Errors/Warnings for AlgoTraders Module
==============================================

Re-exports the whole exception hierarchy so callers import from one
place. See :mod:`algotraders.errors.errors` for the tree and for how
each class maps onto a recovery action.
"""

from algotraders.errors.errors import (
    AlgoTradersError,
    MissingDependency,

    BrokerError,
    UnavailableBroker,
    BrokerConfigurationError,
    TransientBrokerError,
    RateLimitExceeded,

    AuthenticationError,
    InvalidCredentials,
    AuthorizationRequired,
    TokenExpiredError,

    InstrumentError,
    InstrumentNotFound,
    InstrumentMasterUnavailable,

    DataError,
    UnsupportedTimeframe,
    DataSubscriptionRequired,

    OrderError,
    InvalidOrder,
    OrderRejected,

    StreamError,
    SubscriptionLimitExceeded
)

__all__ = [
    "AlgoTradersError",
    "MissingDependency",

    "BrokerError",
    "UnavailableBroker",
    "BrokerConfigurationError",
    "TransientBrokerError",
    "RateLimitExceeded",

    "AuthenticationError",
    "InvalidCredentials",
    "AuthorizationRequired",
    "TokenExpiredError",

    "InstrumentError",
    "InstrumentNotFound",
    "InstrumentMasterUnavailable",

    "DataError",
    "UnsupportedTimeframe",
    "DataSubscriptionRequired",

    "OrderError",
    "InvalidOrder",
    "OrderRejected",

    "StreamError",
    "SubscriptionLimitExceeded"
]
