# -*- encoding: utf-8 -*-

"""
Broker Registry Behaviour and Lazy Loading Contract
-----------------------------------------------------

The registry is the seam that lets a Kite-only deployment skip the Fyers
SDK entirely. Three behaviours carry that contract and each is pinned
here: an unregistered name fails with a distinct error, a registered
broker whose SDK is absent fails with a *different* error that names the
missing requirement, and neither failure happens until somebody actually
asks for that broker.

:NOTE: ``BrokerRegistry`` is module level mutable state. Every test that
    registers a broker must restore the original mapping, which the
    :func:`registrySnapshot` fixture handles.
"""

import importlib

from typing import Any, Callable, ContextManager, Iterator

import pytest

from algotraders.brokers import registry as registryModule
from algotraders.brokers import availableBrokers, getBrokerInterface
from algotraders.errors import UnavailableBroker, BrokerConfigurationError


@pytest.fixture
def registrySnapshot() -> Iterator[None]:
    """
    Restore :data:`BrokerRegistry` after a test mutates it, so global
    registration state never leaks between tests.
    """

    original = dict(registryModule.BrokerRegistry)

    try:
        yield
    finally:
        registryModule.BrokerRegistry.clear()
        registryModule.BrokerRegistry.update(original)


def test_availableBrokersIsSorted() -> None:
    """
    The listing is sorted so the output is stable for a CLI or an API
    response body.
    """

    available = availableBrokers()

    assert "fyers" in available
    assert available == sorted(available)


def test_unknownBrokerRaisesUnavailableBroker() -> None:
    """
    An unregistered name is a caller error, distinct from a broker whose
    dependency is merely missing.
    """

    with pytest.raises(UnavailableBroker) as failure:
        getBrokerInterface("definitely-not-a-broker")

    assert "definitely-not-a-broker" in str(failure.value)


def test_registerBrokerAddsEntryPoint(registrySnapshot : None) -> None:
    """
    Registration stores the entry point verbatim and makes the broker
    discoverable through :func:`availableBrokers`.
    """

    registryModule.registerBroker(
        "dummy",
        API = "DummyAPI", AUTH = "DummyAuthentication",
        PATH = "tests.fakes.dummy", REQUIRES = ["dummy_sdk"]
    )

    assert "dummy" in availableBrokers()

    entryPoint = registryModule.BrokerRegistry["dummy"]

    assert entryPoint.API == "DummyAPI"
    assert entryPoint.AUTH == "DummyAuthentication"
    assert entryPoint.REQUIRES == ["dummy_sdk"]


def test_registerBrokerRejectsIncompleteEntryPoint(
    registrySnapshot : None
) -> None:
    """
    The frozen data class has no defaults, so a partially specified
    broker fails at registration rather than at first use.
    """

    with pytest.raises(TypeError):
        registryModule.registerBroker("broken", API = "OnlyTheAPI")


def test_missingBrokerSDKRaisesConfigurationError(
    vendorSDKBlocked : Callable[..., ContextManager[Any]]
) -> None:
    """
    A registered broker whose SDK is not installed reports the missing
    requirement instead of surfacing a raw ``ImportError``.
    """

    with vendorSDKBlocked():
        brokers = importlib.import_module("algotraders.brokers")
        errors = importlib.import_module("algotraders.errors")

        with pytest.raises(errors.BrokerConfigurationError) as failure:
            brokers.getBrokerInterface("fyers")

        message = str(failure.value)

        assert "fyers" in message
        assert "fyers_apiv3" in message


def test_getBrokerInterfaceReturnsAuthAndAPI() -> None:
    """
    A resolvable broker yields both callables, since authentication and
    the API surface are separate concrete classes.
    """

    pytest.importorskip(
        "fyers_apiv3", reason = "requires the optional fyers extra"
    )

    interface = getBrokerInterface("fyers")

    assert callable(interface["API"])
    assert callable(interface["AUTH"])


def test_brokerConfigurationErrorNamesRequirements() -> None:
    """
    The error message is the only place ``REQUIRES`` is surfaced today,
    so it must carry every listed package.
    """

    failure = BrokerConfigurationError(
        name = "fyers", requirements = ["fyers_apiv3", "another_sdk"]
    )

    assert "fyers" in str(failure)
    assert "fyers_apiv3" in str(failure)
    assert "another_sdk" in str(failure)
