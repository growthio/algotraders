# -*- encoding: utf-8 -*-

"""
Initialization time option registration and expose callables at the
module level for the :mod:`algotraders` module. The file allow to
expose publically available toolkits for the module.

Everything re-exported here is public and stable. Anything reachable
only by importing a submodule directly is internal and may change
without notice.
"""

from algotraders import errors
from algotraders import models
from algotraders import brokers
from algotraders import strategies

from algotraders.auth import BrokerSession, initialize, initializeSync

from algotraders.brokers import (
    availableBrokers, getBrokerInterface, registerBroker
)

__all__ = [
    "errors", "models", "brokers", "strategies",
    "BrokerSession", "initialize", "initializeSync",
    "availableBrokers", "getBrokerInterface", "registerBroker"
]
