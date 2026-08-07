# -*- encoding: utf-8 -*-

"""
Token Persistence, Atomicity and On-Disk Secrecy
--------------------------------------------------

A stored token is a live bearer credential that can place orders, so
these tests cover more than round tripping. They pin that the client id
never appears in a filename, that a partially written file can never be
read back as a valid session, and that a corrupt file forces a fresh
login instead of taking down a service on its morning start-up.
"""

import os
import json
import pathlib
import datetime as dt

import pytest

from algotraders.auth.store import (
    CLIENT_HASH_LENGTH, FileTokenStore, MemoryTokenStore, hashClientId
)
from algotraders.core.clock import IST
from algotraders.models.auth import TokenBundle, TokenPolicy

CLIENT = "FX0KN0G7PX-100"
SECRET = "eyJhbGciOiJIUzI1NiJ9.live-access-token.signature"
REFRESH = "live-refresh-token"

POLICY = TokenPolicy(
    rolloverHourIST = 6,
    accessTokenLifetime = dt.timedelta(days = 1),
    refreshTokenLifetime = dt.timedelta(days = 15),
    supportsRefresh = True
)


def buildBundle(brokerName : str = "fyers") -> TokenBundle:
    """
    Construct a representative bundle for storage tests.
    """

    return TokenBundle.build(
        brokerName = brokerName, clientId = CLIENT, accessToken = SECRET,
        refreshToken = REFRESH,
        issuedAt = dt.datetime(2026, 8, 5, 9, 0, tzinfo = IST),
        policy = POLICY
    )


def test_memoryStoreRoundTrips() -> None:
    """
    Write then read returns an identical bundle.
    """

    store = MemoryTokenStore()
    bundle = buildBundle()

    assert store.read("fyers", CLIENT) is None

    store.write(bundle)

    assert store.read("fyers", CLIENT) == bundle


def test_memoryStoreSeparatesBrokersAndClients() -> None:
    """
    One process can hold several sessions without them colliding.
    """

    store = MemoryTokenStore()

    store.write(buildBundle("fyers"))
    store.write(buildBundle("kite"))

    assert store.read("fyers", CLIENT) is not None
    assert store.read("kite", CLIENT) is not None
    assert store.read("fyers", "OTHER-100") is None


def test_deleteIsIdempotent() -> None:
    """
    A logout path never has to check whether a token exists first.
    """

    store = MemoryTokenStore()

    store.delete("fyers", CLIENT)
    store.write(buildBundle())
    store.delete("fyers", CLIENT)
    store.delete("fyers", CLIENT)

    assert store.read("fyers", CLIENT) is None


def test_fileStoreRoundTripsWithSecretsIntact(
    tmp_path : pathlib.Path
) -> None:
    """
    Persistence must preserve the real token, since a masked one would
    be useless on reload.
    """

    store = FileTokenStore(tmp_path)
    store.write(buildBundle())

    restored = store.read("fyers", CLIENT)

    assert restored is not None
    assert restored.accessToken.get_secret_value() == SECRET
    assert restored.refreshToken.get_secret_value() == REFRESH
    assert restored.expiresAt == buildBundle().expiresAt


def test_filenameNeverContainsTheClientId(
    tmp_path : pathlib.Path
) -> None:
    """
    A directory listing or a shared screenshot must not disclose which
    trading account is configured.
    """

    store = FileTokenStore(tmp_path)
    store.write(buildBundle())

    written = list(tmp_path.iterdir())

    assert len(written) == 1
    assert CLIENT not in written[0].name
    assert written[0].name.startswith("fyers-")
    assert written[0].suffix == ".token"


def test_clientHashIsStableAndTruncated() -> None:
    """
    The handle is deterministic, so a session is found again on the next
    run, and short enough to keep filenames readable.
    """

    assert hashClientId(CLIENT) == hashClientId(CLIENT)
    assert hashClientId(CLIENT) != hashClientId("OTHER-100")
    assert len(hashClientId(CLIENT)) == CLIENT_HASH_LENGTH


def test_missingFileReadsAsAbsent(tmp_path : pathlib.Path) -> None:
    """
    A cold start is not an error.
    """

    assert FileTokenStore(tmp_path).read("fyers", CLIENT) is None


def test_corruptFileIsDiscardedRatherThanRaising(
    tmp_path : pathlib.Path
) -> None:
    """
    A truncated token file forces a fresh login instead of taking down
    the service on its morning start-up.
    """

    store = FileTokenStore(tmp_path)
    store.write(buildBundle())

    path = store.pathFor("fyers", CLIENT)
    path.write_text('{"brokerName": "fyers", "trunc', encoding = "utf-8")

    assert store.read("fyers", CLIENT) is None


def test_writeIsAtomicAndLeavesNoTemporaryFile(
    tmp_path : pathlib.Path
) -> None:
    """
    Only the final token file survives a write.

    A leftover temporary would hold a live credential under a name no
    cleanup path knows about.
    """

    store = FileTokenStore(tmp_path)
    store.write(buildBundle())
    store.write(buildBundle())

    names = sorted(entry.name for entry in tmp_path.iterdir())

    assert len(names) == 1
    assert not names[0].endswith(".tmp")


def test_failedWriteRemovesTheTemporaryFile(
    tmp_path : pathlib.Path, monkeypatch : pytest.MonkeyPatch
) -> None:
    """
    A write that fails part way through must not strand a temporary
    file containing the token.
    """

    store = FileTokenStore(tmp_path)

    def explodingReplace(*args : object, **kwargs : object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", explodingReplace)

    with pytest.raises(OSError):
        store.write(buildBundle())

    assert list(tmp_path.iterdir()) == []


def test_overwriteReplacesRatherThanAppends(
    tmp_path : pathlib.Path
) -> None:
    """
    A refreshed token replaces the previous one cleanly.
    """

    store = FileTokenStore(tmp_path)
    store.write(buildBundle())

    refreshed = TokenBundle.build(
        brokerName = "fyers", clientId = CLIENT,
        accessToken = "second-access-token", refreshToken = REFRESH,
        issuedAt = dt.datetime(2026, 8, 6, 9, 0, tzinfo = IST),
        policy = POLICY
    )

    store.write(refreshed)
    restored = store.read("fyers", CLIENT)

    assert restored is not None
    assert restored.accessToken.get_secret_value() == "second-access-token"


@pytest.mark.skipif(
    os.name == "nt", reason = "POSIX file modes are not enforced on Windows"
)
def test_tokenFileIsOwnerReadableOnly(tmp_path : pathlib.Path) -> None:
    """
    On POSIX the token file is not readable by group or other.
    """

    store = FileTokenStore(tmp_path)
    store.write(buildBundle())

    mode = store.pathFor("fyers", CLIENT).stat().st_mode

    assert mode & 0o077 == 0


def test_storedPayloadIsReadableJSON(tmp_path : pathlib.Path) -> None:
    """
    The on-disk format stays inspectable, so an operator can check an
    expiry without importing the library.
    """

    store = FileTokenStore(tmp_path)
    store.write(buildBundle())

    record = json.loads(
        store.pathFor("fyers", CLIENT).read_text(encoding = "utf-8")
    )

    assert record["brokerName"] == "fyers"
    assert record["accessToken"] == SECRET
    assert "expiresAt" in record


def test_fromEnvironmentUsesConfiguredDirectory(
    tmpTokenDir : pathlib.Path
) -> None:
    """
    The store lands where ``ALGOTRADERS_TOKEN_DIR`` points, which is how
    the suite keeps every write inside a temporary directory.
    """

    store = FileTokenStore.fromEnvironment()

    assert store.directory == tmpTokenDir


def test_directoryIsCreatedOnDemand(tmp_path : pathlib.Path) -> None:
    """
    A first run needs no manual setup step.
    """

    nested = tmp_path / "deeply" / "nested" / "tokens"
    store = FileTokenStore(nested)

    assert not nested.exists()

    store.write(buildBundle())

    assert nested.is_dir()
