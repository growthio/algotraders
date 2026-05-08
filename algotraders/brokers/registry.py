# -*- encoding: utf-8 -*-

"""
Register a New Broker into the System and Perform Lazy Loading
--------------------------------------------------------------

The unified implementation allows defining multiple broker services to
the system and let the end user choose their service as required. It
may be noted here that at one time only ONE broker instance should be
created and underlying methods be called as we do not want to place
multiple orders to different brokers using the same logic.

The registry service implements a *dot* service layer which will be
able to display all the registered brokers in :mod:`algotraders`. In
addition, it also controls, checks and validates the runtime
requirements for a particular service without the need to explictly
install all the API library available from different brokers.

For example, suppose we have the following brokers implemented into
our system - ``zerodha``, ``fyers``, ``angelone`` and the enduser has
decided to use ``angelone``, then only related libraries required by
``angelone`` should be available in the system without raising an
``ImportError`` for the other Brokers' API libraries.
"""

import pathlib
import importlib

from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Union

from algotraders.brokers.base import (
    BaseBrokerAPI, BaseBrokerAuthentication
)
from algotraders.errors import (
    BrokerError, UnavailableBroker, BrokerConfigurationError
)

@dataclass(frozen = True)
class BrokerRegistryEntryPoint:
    """
    Register a new broker in the system using an entry point which
    validates if required services are available. The entry point is
    always a frozen data class which is called during init-time option
    registrations when the module is imported.

    :param API: Returns the callable API object which handles all
        types of operations - fetching data, executing orders, etc.

    :param AUTH: Authentication class for login, logout and other
        services required by the broker.

    :param PATH: Path to the file where the ``API`` and ``AUTH``
        callables are defined.

    :param modulePath: Path to the submodule path which is required
        by the enduser. The module path refers to name of the ``.py``
        file under ``algotraders/modules`` directory.

    :param unifiedClass: Name of the unified class which is to be
        imported as per the enduser's requirement.

    :param REQUIRES: List of external library/module required by the
        selected broker - if the requirement is not satisfied then
        ``BrokerConfigurationError`` is raised.
    """

    API  : Callable
    AUTH : Callable
    PATH : Union[str, pathlib.Path]

    # ! Check Module Requirements, Raise Custom Error
    REQUIRES : List[str]


BrokerRegistry: dict[str, BrokerRegistryEntryPoint] = {}


def registerBroker(brokerName : str, **kwargs) -> None:
    """
    Register a new broker in the system during module initialization
    as per the endusers' selected broker service.

    :type  brokerName: str
    :param brokerName: Globally identifiable (and SEO compatible name)
        name which uniquely identifies the broker. It is recommended
        to keep the name of the broker in lower case, and named as per
        the filename in which the broker API is defined.

    **Keyword Arguments**

    The keyword arguments are passed directly to the entry point data
    class to create an instance and register a new available broker.
    Check :cls:`BrokerRegistryEntryPoint` documentation for details.

    :raises TypeError: The keyword arguments must match all the keys
        of the ``BrokerRegistryEntryPoint`` frozen data class.
    """

    BrokerRegistry[brokerName] = BrokerRegistryEntryPoint(**kwargs)


def availableBrokers() -> List[str]:
    """
    Lists the available/registered brokers in the system. The function
    returns a list of names of the brokers, other details are detailed
    in the registry end point.
    """

    return sorted(BrokerRegistry.keys())


def getBrokerInterface(brokerName : str) -> dict[str, Callable]:
    """
    Check the system if the selected broker is available and if all
    the required libraries are installed, then finally return the
    implemented class for all type of logic implementation.

    :type  brokerName: str
    :param brokerName: Name of the broker as registered in the system,
        if unsure check the list of available brokers using method.
    """

    available = availableBrokers()
    if brokerName not in available:
        raise UnavailableBroker(
            f"Broker {brokerName} is not registered, please raise a "
            "ticket at https://github.com/growthio/algotraders/issues/new"
        )

    entryPoint = BrokerRegistry[brokerName]
    try:
        module = importlib.import_module(entryPoint.PATH)
    except ImportError:
        raise BrokerConfigurationError(
            name = brokerName, requirements = entryPoint.REQUIRES
        )
    except Exception as err:
        raise BrokerError(f"Unbounded Broker Error: {err}")

    api : Callable[..., BaseBrokerAPI] = getattr(
        module, entryPoint.API
    )

    auth : Callable[..., BaseBrokerAuthentication] = getattr(
        module, entryPoint.AUTH
    )

    return dict(API = api, AUTH = auth)
