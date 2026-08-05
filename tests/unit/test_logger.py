# -*- encoding: utf-8 -*-

"""
Credential Redaction in Log Records
-------------------------------------

These tests exist because of a concrete incident: a Fyers JWT reached
this repository in cleartext, and a debug ``print(accessToken)`` was
added to the authentication path while chasing it. Redaction is the
backstop for that class of mistake, so the assertions here are about the
secret being absent rather than about the message being pretty.
"""

import json
import logging

import pytest

from algotraders.logger import (
    MASK, REDACTION_KEYS, RedactingFilter, describeSecret, getLogger,
    maskSecret, redactText
)

# ? shaped like a real Fyers token, three base64url segments after eyJ
SAMPLE_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJmeV9pZCI6IkZBSjEyNDQyIiwiZXhwIjoxNzc5MjM3MDAwfQ"
    ".KaMdibC7iAAAQ6QmZ8Dox2lDtr61w6YvPtyppCfqAjc"
)

SAMPLE_SECRET = "LQYCU172WX"


def test_maskSecretNeverRevealsMoreThanRequested() -> None:
    """
    The visible suffix is capped, and short values are masked whole.
    """

    assert maskSecret("FX0KN0G7PX-100") == f"{MASK}-100"
    assert maskSecret("FX0KN0G7PX-100", visible = 0) == MASK

    # ! a value short enough that a 4 character suffix would expose most
    # of it must be masked completely
    assert maskSecret("abcde") == MASK
    assert SAMPLE_SECRET not in maskSecret(SAMPLE_SECRET)


def test_maskSecretRendersUnsetDistinctly() -> None:
    """
    An absent credential is distinguishable from a masked one, which
    matters when reading a configuration dump.
    """

    assert maskSecret(None) == "<unset>"
    assert maskSecret("") == "<unset>"


@pytest.mark.parametrize("template", [
    "access_token={secret}",
    "accessToken = {secret}",
    '{{"refresh_token": "{secret}"}}',
    "Authorization: {secret}",
    "?request_token={secret}&api_key=public",
    "secretKey='{secret}'",
    "pin={secret}",
])
def test_redactsEveryAssignmentShape(template : str) -> None:
    """
    Credentials are masked across JSON, query strings, repr output and
    plain assignment, which is the full range of shapes the vendor SDKs
    and this library actually produce.
    """

    text = template.format(secret = SAMPLE_SECRET)
    redacted = redactText(text)

    assert SAMPLE_SECRET not in redacted
    assert MASK in redacted


def test_redactsBareJSONWebToken() -> None:
    """
    A JWT is masked on sight, even with no key name attached to it.
    """

    redacted = redactText(f"the token is {SAMPLE_JWT} and it expired")

    assert SAMPLE_JWT not in redacted
    assert "the token is" in redacted
    assert "and it expired" in redacted


def test_redactionKeepsSurroundingContext() -> None:
    """
    Only the value is replaced. Keys and structure survive, otherwise
    the log line stops being useful for debugging.
    """

    payload = json.dumps({"s": "ok", "access_token": SAMPLE_JWT, "code": 200})
    redacted = redactText(payload)

    assert SAMPLE_JWT not in redacted
    assert "access_token" in redacted
    assert '"s": "ok"' in redacted
    assert "200" in redacted


def test_filterMasksRecordAndKeepsIt(caplog : pytest.LogCaptureFixture) -> None:
    """
    The filter censors records rather than dropping them; a silently
    missing log line is its own debugging problem.
    """

    logger = getLogger("test.filter")
    logger.setLevel(logging.DEBUG)

    with caplog.at_level(logging.DEBUG, logger = logger.name):
        logger.debug("login response access_token=%s", SAMPLE_JWT)

    assert len(caplog.records) == 1
    assert SAMPLE_JWT not in caplog.text
    assert MASK in caplog.text


def test_filterHandlesLazyFormattingArguments(
    caplog : pytest.LogCaptureFixture
) -> None:
    """
    Secrets passed as %-args are caught, not just those already
    interpolated into the message.
    """

    logger = getLogger("test.args")
    logger.setLevel(logging.DEBUG)

    with caplog.at_level(logging.DEBUG, logger = logger.name):
        logger.info("resolved %s for secretKey=%s", "fyers", SAMPLE_SECRET)

    assert SAMPLE_SECRET not in caplog.text
    assert "fyers" in caplog.text


def test_getLoggerAttachesFilterToEveryChild() -> None:
    """
    The filter is attached per logger.

    A ``logging.Filter`` on a parent does not run for records that merely
    propagate up from a child, so attaching it only to the library root
    would protect nothing. This is the single easiest way to get
    redaction wrong.
    """

    child = getLogger("brokers.fyers.api")

    assert child.name == "algotraders.brokers.fyers.api"
    assert any(isinstance(entry, RedactingFilter) for entry in child.filters)


def test_getLoggerIsIdempotent() -> None:
    """
    Repeated calls must not stack duplicate filters onto one logger.
    """

    for _ in range(5):
        logger = getLogger("test.idempotent")

    filters = [
        entry for entry in logger.filters if isinstance(entry, RedactingFilter)
    ]

    assert len(filters) == 1


def test_libraryRootCarriesNullHandlerOnly() -> None:
    """
    The library never imposes handler policy on its host: a
    ``NullHandler`` and nothing else.
    """

    getLogger("test.handlers")
    root = logging.getLogger("algotraders")

    assert root.handlers
    assert all(
        isinstance(handler, logging.NullHandler) for handler in root.handlers
    )


def test_describeSecretUnwrapsPydanticSecretStr() -> None:
    """
    A ``SecretStr`` masks to a constant placeholder on its own, which
    would make every credential look identical in a repr. Unwrapping
    first keeps the disambiguating suffix.
    """

    from pydantic import SecretStr

    rendered = describeSecret("clientId", SecretStr("FX0KN0G7PX-100"))

    assert rendered == f"clientId={MASK}-100"
    assert "FX0KN0G7PX" not in rendered


def test_everyRedactionKeyIsActuallyMatched() -> None:
    """
    Guard against a key being added to the set but not matched by the
    pattern built from it.
    """

    for key in REDACTION_KEYS:
        redacted = redactText(f"{key}={SAMPLE_SECRET}")

        assert SAMPLE_SECRET not in redacted, f"{key} was not redacted"
