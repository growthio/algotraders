# -*- encoding: utf-8 -*-

"""
Shared Fixtures and Guard Rails for the AlgoTraders Test Suite
---------------------------------------------------------------

The suite is built on one rule: a test never talks to a broker. Broker
access tokens are bearer credentials that can place real orders on a
live DEMAT account, and an accidental live call during a test run is
both a monetary risk and a fast way to burn the daily rate limit.

Two autouse fixtures enforce that rule structurally rather than by
convention. :func:`noNetwork` replaces ``socket.socket.connect`` with a
raising stub, so any code path that reaches for the network fails loudly
and points at the fake it should have used instead. The companion
:func:`dummyEnvironment` scrubs every ``ALGOTRADERS_*`` variable that may
be exported in the developer's shell and substitutes throwaway values
rooted in a temporary directory, which stops a real token store from
being read or, worse, overwritten by a test.

:NOTE: Tests marked ``live`` are excluded by ``addopts`` in
    ``pyproject.toml`` and are additionally exempted from the network
    block. They are opt-in via ``pytest -m live`` and must never run in
    continuous integration.
"""

import os
import sys
import socket
import pathlib
import importlib
import contextlib

from typing import Any, Dict, Iterator, FrozenSet, Callable, ContextManager

import pytest

# ? broker SDKs are optional extras, the library must import and behave
# without them. Tests pin the list so a new broker is added in one place.
VENDOR_PACKAGES : FrozenSet[str] = frozenset({"fyers_apiv3", "kiteconnect"})

BLOCKED_NETWORK_MESSAGE = (
    "network access is blocked inside the test suite; use a fake or a "
    "recorded payload under tests/data instead of a live broker call"
)

# ? loopback stays reachable so asyncio's self-pipe and any local test
# server keep working. No broker lives on 127.0.0.1
LOOPBACK_HOSTS : FrozenSet[str] = frozenset({
    "127.0.0.1", "::1", "localhost", "0.0.0.0", ""
})

# ? only the "live" suite reads this, every other test runs against
# scrubbed throwaway values and never sees a real credential
ENVIRONMENT_FILE = pathlib.Path(__file__).resolve().parent.parent / ".env.local"


def loadEnvironmentFile(
    path : pathlib.Path, monkeypatch : pytest.MonkeyPatch
) -> Dict[str, str]:
    """
    Parse a ``KEY=value`` dotenv file and export it for one test.

    The library deliberately has no dotenv dependency: configuration is
    read through :func:`os.getenv` so a production host can inject
    credentials from its own secret manager rather than a file on disk.
    That leaves local ``live`` runs with nothing to load ``.env.local``,
    which is what this helper covers. The parser is intentionally small
    and handles only what the template uses - comments, blank lines and
    a single optional layer of surrounding quotes.

    :type  path: pathlib.Path
    :param path: Dotenv file to read. A missing file skips the test
        rather than failing it, since ``.env.local`` is git ignored and
        will legitimately be absent on a fresh clone.

    :type  monkeypatch: pytest.MonkeyPatch
    :param monkeypatch: Used to export the values, so the process
        environment is restored when the test finishes.

    :rtype:   Dict[str, str]
    :returns: The parsed ``name -> value`` mapping that was exported.
    """

    if not path.is_file():
        pytest.skip(
            f"live tests need {path.name}; copy .env.example to it and "
            "fill in your own broker credentials"
        )

    values : Dict[str, str] = {}

    for line in path.read_text(encoding = "utf-8").splitlines():
        entry = line.strip()

        if not entry or entry.startswith("#") or "=" not in entry:
            continue

        name, _, value = entry.partition("=")
        values[name.strip()] = value.strip().strip('"').strip("'")

    for name, value in values.items():
        monkeypatch.setenv(name, value)

    return values


class VendorImportBlocker:
    """
    A :mod:`sys.meta_path` finder that hides the optional broker SDKs
    from the interpreter for the duration of a test.

    The registry promises lazy loading - a missing ``fyers_apiv3`` must
    surface only when somebody asks for the Fyers broker, never at
    ``import algotraders`` time. Proving that promise needs an
    interpreter where the SDK is genuinely absent, and uninstalling a
    package mid-suite is not an option, so the finder raises
    ``ImportError`` for the blocked roots instead.

    :type  blocked: FrozenSet[str]
    :param blocked: Top level package names to hide. A dotted submodule
        of a blocked root is hidden as well.
    """

    def __init__(self, blocked : FrozenSet[str]) -> None:
        self.blocked = blocked


    def find_spec(
        self, fullname : str, path : Any = None, target : Any = None
    ) -> None:
        """
        Raise ``ImportError`` when the requested module belongs to a
        blocked root, otherwise defer to the remaining finders by
        returning ``None``.
        """

        if fullname.split(".")[0] in self.blocked:
            raise ImportError(
                f"module {fullname} is blocked by the test suite"
            )

        return None


def purgeModules(roots : FrozenSet[str]) -> Dict[str, Any]:
    """
    Evict every already imported module belonging to the given root
    packages and return what was evicted so a caller can restore it.

    Python caches modules in :data:`sys.modules`, so a package imported
    by an earlier test would otherwise be handed back from cache and the
    import guard under test would never execute.

    :type  roots: FrozenSet[str]
    :param roots: Top level package names to evict.

    :rtype:   Dict[str, Any]
    :returns: The evicted ``name -> module`` mapping.
    """

    evicted = {
        name : module for name, module in sys.modules.items()
        if name.split(".")[0] in roots
    }

    for name in evicted:
        del sys.modules[name]

    return evicted


@pytest.fixture(autouse = True)
def noNetwork(
    request : pytest.FixtureRequest, monkeypatch : pytest.MonkeyPatch
) -> None:
    """
    Block outbound socket connections for every test not marked
    ``live``.

    Patching at the :class:`socket.socket` level catches every HTTP
    client, vendor SDK and websocket library at once, which is far more
    reliable than trying to enumerate and stub each of them.
    """

    if "live" in request.keywords:
        return

    originalConnect = socket.socket.connect
    originalConnectEx = socket.socket.connect_ex

    def isLoopback(address : Any) -> bool:
        # ! asyncio builds its self-pipe over a loopback socketpair on
        # Windows, so blocking every connect breaks the event loop
        # itself rather than the broker call we care about
        host = address[0] if isinstance(address, (tuple, list)) else address

        return host in LOOPBACK_HOSTS

    def blockedConnect(self : socket.socket, address : Any) -> Any:
        if isLoopback(address):
            return originalConnect(self, address)

        raise RuntimeError(f"{BLOCKED_NETWORK_MESSAGE} (target: {address})")

    def blockedConnectEx(self : socket.socket, address : Any) -> Any:
        if isLoopback(address):
            return originalConnectEx(self, address)

        raise RuntimeError(f"{BLOCKED_NETWORK_MESSAGE} (target: {address})")

    monkeypatch.setattr(socket.socket, "connect", blockedConnect)
    monkeypatch.setattr(socket.socket, "connect_ex", blockedConnectEx)

    # ! socket.connect is not the only door. On Windows the Proactor
    # loop reaches the network through ConnectEx, so an async HTTP
    # client bypassed the guard entirely and the suite had no network
    # block at all on this platform
    import asyncio

    originalCreateConnection = asyncio.BaseEventLoop.create_connection
    originalSockConnect = asyncio.BaseEventLoop.sock_connect

    async def blockedCreateConnection(
        self : Any, *args : Any, **kwargs : Any
    ) -> Any:
        host = kwargs.get("host") or (args[1] if len(args) > 1 else None)

        if host in LOOPBACK_HOSTS or host is None:
            return await originalCreateConnection(self, *args, **kwargs)

        raise RuntimeError(f"{BLOCKED_NETWORK_MESSAGE} (target: {host})")

    async def blockedSockConnect(
        self : Any, sock : Any, address : Any
    ) -> Any:
        if isLoopback(address):
            return await originalSockConnect(self, sock, address)

        raise RuntimeError(f"{BLOCKED_NETWORK_MESSAGE} (target: {address})")

    monkeypatch.setattr(
        asyncio.BaseEventLoop, "create_connection", blockedCreateConnection
    )
    monkeypatch.setattr(
        asyncio.BaseEventLoop, "sock_connect", blockedSockConnect
    )


@pytest.fixture(autouse = True)
def dummyEnvironment(
    request : pytest.FixtureRequest, tmp_path : pathlib.Path,
    monkeypatch : pytest.MonkeyPatch
) -> Dict[str, str]:
    """
    Replace the whole ``ALGOTRADERS_*`` environment with throwaway
    values anchored to a per-test temporary directory.

    Scrubbing first matters more than seeding: a developer running the
    suite locally very likely has real Fyers or Kite credentials
    exported, and without this the token store fixtures would read - and
    could overwrite - that developer's live session file.

    Tests marked ``live`` invert this. They are opt-in, they genuinely
    need a broker, so they load ``.env.local`` instead of being scrubbed.

    :rtype:   Dict[str, str]
    :returns: The environment mapping that was installed, so a test can
        assert against the same values it was configured with.
    """

    if "live" in request.keywords:
        return loadEnvironmentFile(ENVIRONMENT_FILE, monkeypatch)

    for name in list(os.environ):
        if name.startswith("ALGOTRADERS_"):
            monkeypatch.delenv(name, raising = False)

    values = {
        "ALGOTRADERS_FYERS_CLIENT_ID"    : "TESTAPP-100",
        "ALGOTRADERS_FYERS_SECRET_KEY"   : "test-fyers-secret",
        "ALGOTRADERS_FYERS_REDIRECT_URL" : "https://localhost/callback",
        "ALGOTRADERS_FYERS_PIN"          : "1234",
        "ALGOTRADERS_KITE_API_KEY"       : "test-kite-key",
        "ALGOTRADERS_KITE_API_SECRET"    : "test-kite-secret",
        "ALGOTRADERS_KITE_REDIRECT_URL"  : "https://localhost/callback",
        "ALGOTRADERS_TOKEN_DIR"          : str(tmp_path / "tokens"),
        "ALGOTRADERS_CACHE_DIR"          : str(tmp_path / "cache"),
        "ALGOTRADERS_LOG_DIR"            : str(tmp_path / "logs")
    }

    for name, value in values.items():
        monkeypatch.setenv(name, value)

    return values


@pytest.fixture
def tmpTokenDir(dummyEnvironment : Dict[str, str]) -> pathlib.Path:
    """
    Return the temporary token directory configured for this test, with
    the directory already created on disk.
    """

    directory = pathlib.Path(dummyEnvironment["ALGOTRADERS_TOKEN_DIR"])
    directory.mkdir(parents = True, exist_ok = True)

    return directory


@pytest.fixture
def vendorSDKBlocked() -> Callable[..., ContextManager[Any]]:
    """
    Return a context manager factory that reimports :mod:`algotraders`
    in an interpreter where the broker SDKs cannot be imported.

    The factory accepts an optional set of roots to block, defaulting to
    every known vendor package, and yields the freshly imported
    :mod:`algotraders` module.

    **Example Usage**

    Assert that the package imports cleanly with no broker SDK present.

    .. code-block:: python

        def test_importsWithoutVendorSDK(vendorSDKBlocked) -> None:
            with vendorSDKBlocked() as package:
                assert package.brokers.availableBrokers() == ["fyers"]
    """

    @contextlib.contextmanager
    def factory(
        blocked : FrozenSet[str] = VENDOR_PACKAGES
    ) -> Iterator[Any]:
        blocker = VendorImportBlocker(blocked)
        evicted = purgeModules(blocked | {"algotraders"})

        sys.meta_path.insert(0, blocker)

        try:
            yield importlib.import_module("algotraders")
        finally:
            sys.meta_path.remove(blocker)

            # ! the reimported package must not outlive the block, or a
            # later test inherits modules built against a hidden SDK
            purgeModules({"algotraders"})
            sys.modules.update(evicted)

    return factory
