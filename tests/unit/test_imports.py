# -*- encoding: utf-8 -*-

"""
Import Time Guarantees for the AlgoTraders Package
---------------------------------------------------

The registry advertises lazy broker loading, which is the reason
``pyproject.toml`` keeps the broker SDKs as optional extras. That promise
is only worth something if it is checked: a stray top level
``from fyers_apiv3 import fyersModel`` anywhere in the package would make
``import algotraders`` fail outright for a Kite-only user, and the
failure would look like a packaging bug rather than a layering mistake.

These tests reimport the package inside an interpreter where the SDKs
cannot be resolved, which is the only way to catch that regression.
"""

import sys

from typing import Any, Callable, ContextManager

import pytest

import algotraders

VENDOR_PACKAGES = ("fyers_apiv3", "kiteconnect")


def test_importsWithoutVendorSDK(
    vendorSDKBlocked : Callable[..., ContextManager[Any]]
) -> None:
    """
    The package imports cleanly when no broker SDK is installed.
    """

    with vendorSDKBlocked() as package:
        assert package.__version__
        assert package.brokers.availableBrokers() == ["fyers"]


def test_noVendorSDKLoadedAtImportTime(
    vendorSDKBlocked : Callable[..., ContextManager[Any]]
) -> None:
    """
    Importing the package must not pull a broker SDK into
    :data:`sys.modules` as a side effect.
    """

    with vendorSDKBlocked() as package:
        assert package is sys.modules["algotraders"]

        for name in VENDOR_PACKAGES:
            assert name not in sys.modules


def test_publicSurfaceIsExported() -> None:
    """
    The documented top level surface stays importable and stable.
    """

    assert algotraders.__version__.startswith("v")

    for name in algotraders.__all__:
        assert hasattr(algotraders, name), f"missing export: {name}"


@pytest.mark.parametrize("module", [
    "algotraders",
    "algotraders.api",
    "algotraders.brokers",
    "algotraders.brokers.base",
    "algotraders.brokers.registry",
    "algotraders.errors",
    "algotraders.strategies"
])
def test_moduleImportsWithoutVendorSDK(
    vendorSDKBlocked : Callable[..., ContextManager[Any]], module : str
) -> None:
    """
    Every non broker specific module imports with the SDKs hidden.

    Broker implementation modules are deliberately excluded: those are
    allowed - and expected - to import their vendor SDK at module scope,
    because the registry only reaches them after resolving the extra.
    """

    import importlib

    with vendorSDKBlocked():
        assert importlib.import_module(module) is not None
