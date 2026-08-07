# -*- encoding: utf-8 -*-

"""
Security Regressions Found by the Stage 2 Audit
--------------------------------------------------

Every test here pins a defect that was live in the codebase and would
have shipped. They are grouped in one module deliberately: these are not
feature tests, they are the tripwires for four specific ways this
library could have leaked or misused a credential that places real
trades.

Each docstring states the attack rather than the API, because the reason
a check exists is the part that gets lost first.
"""

import os
import json
import logging
import pathlib
import datetime as dt

import pytest

from algotraders.auth.state import MemoryLoginStateStore
from algotraders.auth.store import FileTokenStore
from algotraders.core.clock import IST
from algotraders.logger import MASK, RedactingFilter, getLogger, redactText
from algotraders.models.auth import TokenBundle, TokenPolicy
from tests.fakes.clock import FakeClock

SECRET = "9aXbQ7ZmLp0kR3"

POLICY = TokenPolicy(
    rolloverHourIST = 6,
    accessTokenLifetime = dt.timedelta(days = 1),
    supportsRefresh = True
)


def bundleFor(clientId : str, token : str = "live-token") -> TokenBundle:
    """
    Build a stored bundle for a given account.
    """

    return TokenBundle.build(
        brokerName = "fyers", clientId = clientId, accessToken = token,
        refreshToken = "refresh", policy = POLICY,
        issuedAt = dt.datetime(2026, 8, 5, 9, 0, tzinfo = IST)
    )


# ---------------------------------------------------------------------
# H3 - redaction bypasses
# ---------------------------------------------------------------------

@pytest.mark.parametrize("line", [
    "Authorization: token f8k2n4p:%s" % SECRET,
    "Authorization: Bearer %s" % SECRET,
    "Authorization: enctoken %s" % SECRET,
    "sent header Bearer %s upstream" % SECRET,
])
def test_schemePrefixedCredentialsAreMasked(line : str) -> None:
    """
    A scheme before the value used to shield it.

    The value pattern stops at whitespace, so ``Authorization: token
    <secret>`` masked the word "token" and printed the credential in
    clear. That is worse than no redaction, because the mask reads as a
    handled signal to whoever scans the log.
    """

    redacted = redactText(line)

    assert SECRET not in redacted
    assert MASK in redacted


@pytest.mark.parametrize("line", [
    "api_key=%s" % SECRET,
    "apiKey=%s" % SECRET,
    "client_id=%s" % SECRET,
    "clientId=%s" % SECRET,
    "https://kite.zerodha.com/connect/login?v=3&api_key=%s" % SECRET,
])
def test_brokerIdentifiersAreTreatedAsSecrets(line : str) -> None:
    """
    Kite's ``api_key`` is its client id, and ``config.py`` documents the
    client id as identifying a real trading account. Both were absent
    from the redaction set while being called secrets elsewhere.
    """

    assert SECRET not in redactText(line)


@pytest.mark.parametrize("line", [
    "the token is expired and it failed",
    "token refresh failed for fyers",
    "Basic auth failed",
    "enctoken rotation is not supported",
])
def test_ordinaryProseIsNotMangled(line : str) -> None:
    """
    "token" and "enctoken" are ordinary English in this domain.

    An early version of the scheme rule matched them bare, which turned
    "the token is expired" into "the token ******** expired". Redaction
    that destroys readability gets switched off, so this is a real cost
    rather than harmless over-masking.
    """

    assert redactText(line) == line


def test_redactionPreservesStructure() -> None:
    """
    Only values are replaced; keys and punctuation survive so the line
    stays useful for debugging.
    """

    redacted = redactText(
        json.dumps({"s" : "ok", "access_token" : SECRET, "code" : 200})
    )

    assert SECRET not in redacted
    assert "access_token" in redacted
    assert "200" in redacted


def test_tracebacksAreRedacted(caplog : pytest.LogCaptureFixture) -> None:
    """
    A vendor exception carries the failing request URL, and broker SDKs
    put the access token in the query string.

    ``formatException`` runs long after any filter, so while only the
    rendered message was scrubbed, ``logger.exception(...)`` was the
    widest hole in the whole module.
    """

    logger = getLogger("test.traceback")
    logger.setLevel(logging.DEBUG)

    with caplog.at_level(logging.DEBUG, logger = logger.name):
        try:
            raise RuntimeError(
                "GET /data/history?access_token=%s failed" % SECRET
            )
        except RuntimeError:
            logger.exception("vendor call failed")

    record = caplog.records[0]

    assert record.exc_text is not None
    assert SECRET not in record.exc_text


def test_installRedactionCoversVendorLoggers(
    caplog : pytest.LogCaptureFixture
) -> None:
    """
    Records from ``fyers_apiv3`` or ``kiteconnect`` never pass through an
    ``algotraders`` logger, so a per-logger filter cannot see them. A
    handler level filter is the only placement that covers the process.
    """

    from algotraders.logger import installRedaction

    handler = logging.StreamHandler()
    installRedaction(handler)

    record = logging.LogRecord(
        name = "kiteconnect", level = logging.INFO, pathname = __file__,
        lineno = 1, msg = "access_token=%s", args = (SECRET,), exc_info = None
    )

    # ? since 3.12 Filterer.filter returns the record itself on success
    # rather than True, so this asserts "passed" rather than identity
    assert handler.filter(record)
    assert SECRET not in record.getMessage()

    # ? idempotent, so repeated setup does not stack filters
    installRedaction(handler)

    assert len([
        entry for entry in handler.filters
        if isinstance(entry, RedactingFilter)
    ]) == 1


# ---------------------------------------------------------------------
# C4 - token store identity
# ---------------------------------------------------------------------

def test_substitutedTokenFileIsRejected(tmp_path : pathlib.Path) -> None:
    """
    The filename is a hash index, not a proof of ownership.

    Reproduced before the fix: asking for ``TEST-100`` returned
    ``ATTACKER-999``'s bundle and token. Anyone able to write into the
    token directory could make this process trade their account.
    """

    store = FileTokenStore(tmp_path)

    store.write(bundleFor("ATTACKER-999", token = "attacker-token"))

    attacker = store.pathFor("fyers", "ATTACKER-999")
    victim = store.pathFor("fyers", "TEST-100")

    attacker.replace(victim)

    assert store.read("fyers", "TEST-100") is None


def test_brokerMismatchIsRejected(tmp_path : pathlib.Path) -> None:
    """
    A Kite bundle must not satisfy a request for a Fyers session, even
    if it lands at the right path.
    """

    store = FileTokenStore(tmp_path)
    bundle = bundleFor("TEST-100")

    record = bundle.toStorage()
    record["brokerName"] = "kite"

    path = store.pathFor("fyers", "TEST-100")
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text(json.dumps(record), encoding = "utf-8")

    assert store.read("fyers", "TEST-100") is None


def test_matchingBundleStillLoads(tmp_path : pathlib.Path) -> None:
    """
    The identity check must not break the normal path.
    """

    store = FileTokenStore(tmp_path)
    store.write(bundleFor("TEST-100"))

    loaded = store.read("fyers", "TEST-100")

    assert loaded is not None
    assert loaded.clientId == "TEST-100"
    assert loaded.accessToken.get_secret_value() == "live-token"


@pytest.mark.skipif(
    os.name == "nt", reason = "POSIX directory modes are not enforced on Windows"
)
def test_tokenDirectoryIsOwnerOnly(tmp_path : pathlib.Path) -> None:
    """
    A world readable directory undoes the 0600 on the files inside it,
    and a world writable one invites the substitution above.
    """

    directory = tmp_path / "tokens"
    store = FileTokenStore(directory)

    store.write(bundleFor("TEST-100"))

    assert directory.stat().st_mode & 0o077 == 0


# ---------------------------------------------------------------------
# C3 - OAuth login state
# ---------------------------------------------------------------------

def test_stateIsUnguessableAndUnique() -> None:
    """
    The whole defence is that only this server knows what it issued.
    """

    store = MemoryLoginStateStore(clock = FakeClock())
    minted = {store.mint("fyers") for _ in range(64)}

    assert len(minted) == 64
    assert all(len(value) >= 32 for value in minted)


def test_stateIsSingleUse() -> None:
    """
    A state that survives redemption can be replayed from a captured
    callback URL.
    """

    store = MemoryLoginStateStore(clock = FakeClock())
    state = store.mint("fyers")

    assert store.consume("fyers", state) is True
    assert store.consume("fyers", state) is False


def test_unknownStateIsRejected() -> None:
    """
    This is the forged callback: an attacker completes a login on their
    own account and fires the resulting code at this host. They never
    saw the value this session issued.
    """

    store = MemoryLoginStateStore(clock = FakeClock())
    store.mint("fyers")

    assert store.consume("fyers", "attacker-supplied") is False
    assert store.consume("fyers", None) is False
    assert store.consume("fyers", "") is False


def test_stateIsScopedToOneBroker() -> None:
    """
    A state minted for Fyers must not redeem a Kite callback.
    """

    store = MemoryLoginStateStore(clock = FakeClock())
    state = store.mint("fyers")

    assert store.consume("kite", state) is False


def test_stateExpires() -> None:
    """
    The window only has to cover a human completing a browser login; a
    long window is a long replay opportunity.
    """

    clock = FakeClock()
    store = MemoryLoginStateStore(ttl = dt.timedelta(minutes = 10),
                                  clock = clock)
    state = store.mint("fyers")

    clock.advance(11 * 60)

    assert store.consume("fyers", state) is False


def test_expiredStatesAreEvicted() -> None:
    """
    A login endpoint is reachable before authentication in most hosts,
    so an unbounded store is a memory sink for anyone calling it in a
    loop.
    """

    clock = FakeClock()
    store = MemoryLoginStateStore(ttl = dt.timedelta(minutes = 1),
                                  clock = clock)

    for _ in range(100):
        store.mint("fyers")

    assert len(store.outstanding) == 100

    clock.advance(120)
    store.mint("fyers")

    assert len(store.outstanding) == 1
