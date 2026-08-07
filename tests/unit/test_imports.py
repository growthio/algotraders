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

import re
import sys
import pathlib

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

        # ? every registered broker is discoverable even though none of
        # their SDKs can be imported, which is the whole promise of
        # storing entry points as strings
        assert package.brokers.availableBrokers() == ["fyers", "kite"]


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


# ? the documented layering: package prefix -> what it may import from
# inside this library. Enforced rather than merely written down, because
# a violation shows up as a circular import at a distance and the stack
# trace points at the innocent party
LAYERING = {
    "algotraders/models" : {"errors", "models"},
    "algotraders/core" : {"errors", "models", "core"},
    "algotraders/instruments" : {"errors", "models", "core", "instruments"},
    "algotraders/streaming" : {
        "errors", "models", "core", "instruments", "streaming"
    },

    # ! `auth` is the composition root, not a peer layer. `initialize()`
    # assembles a session out of every surface a broker registers, so it
    # necessarily reaches upward into `brokers` and `instruments`. That
    # is a deliberate exception, and it is why the entry point lives in
    # exactly one module rather than being spread around
    "algotraders/auth" : {
        "errors", "models", "core", "auth", "brokers", "instruments"
    }
}

IMPORT_PATTERN = re.compile(
    r"^\s*(?:from|import)\s+algotraders\.([a-zA-Z_]+)", re.MULTILINE
)


@pytest.mark.parametrize("package, permitted", sorted(LAYERING.items()))
def test_layeringRulesAreRespected(
    package : str, permitted : set
) -> None:
    """
    Imports flow one way only, from the bottom of the graph upwards.

    This exists because the rule was violated the moment it was only
    documented: ``models/market.py`` reached into ``core.clock`` for a
    timezone helper, and the resulting cycle surfaced as an
    ``ImportError`` in an unrelated test module, blaming the wrong file.

    ``config`` and ``logger`` are top level modules rather than packages
    and sit below everything, so they are always allowed.
    """

    root = pathlib.Path(algotraders.__file__).resolve().parent.parent
    folder = root / package

    if not folder.is_dir():
        # ? packages arrive phase by phase; the rule applies to whatever
        # exists rather than demanding the whole tree up front
        pytest.skip(f"{package} does not exist yet")

    allowed = permitted | {"config", "logger"}
    violations = []

    for source in folder.rglob("*.py"):
        text = source.read_text(encoding = "utf-8")

        for imported in IMPORT_PATTERN.findall(text):
            if imported not in allowed:
                violations.append(
                    f"{source.relative_to(root)} imports algotraders.{imported}"
                )

    assert not violations, (
        f"{package} may only import {sorted(allowed)}; found:\n  "
        + "\n  ".join(violations)
    )


def test_noVendorSDKImportOutsideBrokerPackages() -> None:
    """
    Only broker implementation modules may import a vendor SDK, and only
    at their own module scope.

    A stray ``from fyers_apiv3 import ...`` anywhere else would make
    ``import algotraders`` fail outright for a Kite only user, and the
    failure would look like a packaging bug rather than a layering
    mistake.
    """

    root = pathlib.Path(algotraders.__file__).resolve().parent
    offenders = []

    for source in root.rglob("*.py"):
        relative = source.relative_to(root).as_posix()

        if relative.startswith("brokers/fyers/") or relative.startswith(
            "brokers/kite/"
        ):
            continue

        text = source.read_text(encoding = "utf-8")

        for vendor in VENDOR_PACKAGES:
            if re.search(rf"^\s*(?:from|import)\s+{vendor}\b", text,
                         re.MULTILINE):
                offenders.append(f"{relative} imports {vendor}")

    assert not offenders, "vendor SDK imported outside a broker package:\n  " \
        + "\n  ".join(offenders)


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
