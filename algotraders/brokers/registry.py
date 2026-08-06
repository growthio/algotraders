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

**Why the registry stays a dictionary**

:class:`Broker` exists for ergonomics: it gives autocomplete and catches
a typo at import time rather than at first call. It deliberately does
**not** replace the registry, because an enum is closed and a third
party broker distributed as a separate package could then never register
itself, which is the entire point of having a registry. Every public
entry point therefore accepts ``Union[str, Broker]``.

..versionchanged:: 2026-08-06 Requirements Are Actually Checked
    ``REQUIRES`` was previously interpolated into an error message and
    never inspected, so a broker whose SDK was missing failed with
    whatever ``ImportError`` happened to surface first. It is now
    checked with :func:`importlib.util.find_spec` before the import is
    attempted, and the resulting error names the pip extra to install.
"""

import importlib
import importlib.util

from enum import Enum
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Optional, Union

from algotraders.errors import (
    BrokerConfigurationError, BrokerError, UnavailableBroker
)
from algotraders.logger import getLogger
from algotraders.models.enums import Capability

logger = getLogger(__name__)


class Broker(str, Enum):
    """
    Built in brokers, for autocomplete and typo safety.

    Not the runtime registry. A broker registered by a third party
    package is perfectly usable by name without appearing here.
    """

    FYERS = "fyers"
    KITE  = "kite"


@dataclass(frozen = True)
class BrokerRegistryEntryPoint:
    """
    Register a new broker in the system using an entry point which
    validates if required services are available. The entry point is
    always a frozen data class which is called during init-time option
    registrations when the module is imported.

    Every callable is stored as a **string**, not as an object. That is
    what makes the loading lazy: nothing in the broker module, and
    therefore nothing in its vendor SDK, is imported until somebody
    actually asks for that broker.

    :param API: Name of the callable API class which handles all types
        of operations - fetching data, executing orders, etc.

    :param AUTH: Name of the authentication class for login, logout and
        other services required by the broker.

    :param PATH: Importable module path where the callables are defined.

    :param REQUIRES: List of external library/module required by the
        selected broker - if the requirement is not satisfied then
        ``BrokerConfigurationError`` is raised.

    :param RESOLVER: Name of the instrument resolver class, when the
        broker ships one.

    :param STREAM: Name of the websocket streaming class, when the
        broker ships one.

    :param CAPABILITIES: What the implementation actually supports, so a
        host can ask before calling.

    :param EXTRA: Name of the pip extra that installs ``REQUIRES``, used
        to make the failure message actionable.
    """

    API  : str
    AUTH : str
    PATH : str

    # ! Check Module Requirements, Raise Custom Error
    REQUIRES : List[str]

    RESOLVER : Optional[str] = None
    STREAM : Optional[str] = None
    CAPABILITIES : FrozenSet[Capability] = frozenset()
    EXTRA : str = ""


BrokerRegistry : Dict[str, BrokerRegistryEntryPoint] = {}


def registerBroker(brokerName : str, **kwargs : Any) -> None:
    """
    Register a new broker in the system during module initialization
    as per the endusers' selected broker service.

    :type  brokerName: str
    :param brokerName: Globally identifiable (and SEO compatible name)
        name which uniquely identifies the broker. It is recommended
        to keep the name of the broker in lower case, and named as per
        the directory in which the broker API is defined.

    **Keyword Arguments**

    The keyword arguments are passed directly to the entry point data
    class to create an instance and register a new available broker.
    Check :class:`BrokerRegistryEntryPoint` documentation for details.

    :raises TypeError: The keyword arguments must match the fields of
        the ``BrokerRegistryEntryPoint`` frozen data class.
    """

    name = brokerName.strip().lower()

    if name in BrokerRegistry:
        logger.warning("re-registering broker %s, replacing entry point", name)

    BrokerRegistry[name] = BrokerRegistryEntryPoint(**kwargs)


def unregisterBroker(brokerName : str) -> None:
    """
    Remove a broker from the registry, ignoring an unknown name.

    Exists mainly so a test can register a fake broker and clean up
    afterwards without reaching into module state directly.
    """

    BrokerRegistry.pop(resolveBrokerName(brokerName), None)


def availableBrokers() -> List[str]:
    """
    Lists the available/registered brokers in the system. The function
    returns a list of names of the brokers, other details are detailed
    in the registry end point.
    """

    return sorted(BrokerRegistry.keys())


def resolveBrokerName(broker : Union[str, Broker]) -> str:
    """
    Normalise a broker identifier to its registry key.

    Accepts a :class:`Broker` member or a plain string in any case, so
    every public entry point can take either without repeating this.

    :rtype:   str
    :returns: The lower case registry name.
    """

    return str(getattr(broker, "value", broker)).strip().lower()


def missingRequirements(entryPoint : BrokerRegistryEntryPoint) -> List[str]:
    """
    Report which of a broker's declared requirements are not installed.

    Uses :func:`importlib.util.find_spec`, which resolves a module
    without executing it, so probing for an absent SDK costs nothing and
    has no import side effects.

    :rtype:   List[str]
    :returns: The unimportable distributions, in declaration order.
    """

    missing : List[str] = []

    for requirement in entryPoint.REQUIRES:
        try:
            if importlib.util.find_spec(requirement) is None:
                missing.append(requirement)
        except (ImportError, ValueError):
            # ? find_spec raises rather than returning None when a
            # parent package is itself missing
            missing.append(requirement)

    return missing


def getBrokerInterface(
    broker : Union[str, Broker]
) -> Dict[str, Any]:
    """
    Check the system if the selected broker is available and if all
    the required libraries are installed, then finally return the
    implemented classes for all type of logic implementation.

    :type  broker: Union[str, Broker]
    :param broker: Name of the broker as registered in the system, or a
        :class:`Broker` member. If unsure, check
        :func:`availableBrokers`.

    :raises UnavailableBroker: The name is not registered at all, which
        no amount of installing packages will fix.

    :raises BrokerConfigurationError: The broker is registered but its
        SDK is not installed. The message names the pip extra.

    **Example Usage**

    .. code-block:: python

        interface = getBrokerInterface(Broker.FYERS)

        authentication = interface["AUTH"](credentials = credentials)

    :rtype:   Dict[str, Any]
    :returns: Mapping with ``AUTH``, ``API``, ``RESOLVER``, ``STREAM``
        and ``CAPABILITIES``. The optional surfaces are ``None`` when
        the broker does not ship them.
    """

    brokerName = resolveBrokerName(broker)

    if brokerName not in BrokerRegistry:
        raise UnavailableBroker(
            f"Broker {brokerName} is not registered, please raise a "
            "ticket at https://github.com/growthio/algotraders/issues/new"
        )

    entryPoint = BrokerRegistry[brokerName]

    # ! checked before importing, so a missing SDK produces an
    # actionable message instead of whichever ImportError surfaces first
    missing = missingRequirements(entryPoint)

    if missing:
        raise BrokerConfigurationError(
            name = brokerName, requirements = missing
        )

    try:
        module = importlib.import_module(entryPoint.PATH)
    except ImportError as error:
        raise BrokerConfigurationError(
            name = brokerName, requirements = entryPoint.REQUIRES
        ) from error
    except Exception as error:
        raise BrokerError(
            f"Unbounded Broker Error: {error}"
        ) from error

    return {
        "AUTH" : getattr(module, entryPoint.AUTH),
        "API" : getattr(module, entryPoint.API),
        "RESOLVER" : (
            getattr(module, entryPoint.RESOLVER)
            if entryPoint.RESOLVER else None
        ),
        "STREAM" : (
            getattr(module, entryPoint.STREAM) if entryPoint.STREAM else None
        ),
        "CAPABILITIES" : entryPoint.CAPABILITIES
    }


def brokerCapabilities(
    broker : Union[str, Broker]
) -> FrozenSet[Capability]:
    """
    Report what a registered broker supports, without importing it.

    Capabilities live on the entry point rather than on the class, so
    this answers without pulling in a vendor SDK.

    :raises UnavailableBroker: The broker is not registered.

    :rtype:   FrozenSet[Capability]
    :returns: The declared capabilities.
    """

    brokerName = resolveBrokerName(broker)

    if brokerName not in BrokerRegistry:
        raise UnavailableBroker(f"Broker {brokerName} is not registered")

    return BrokerRegistry[brokerName].CAPABILITIES


__all__ = [
    "Broker", "BrokerRegistryEntryPoint", "BrokerRegistry",
    "registerBroker", "unregisterBroker", "availableBrokers",
    "resolveBrokerName", "missingRequirements", "getBrokerInterface",
    "brokerCapabilities"
]
