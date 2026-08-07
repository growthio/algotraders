# -*- encoding: utf-8 -*-

"""
Dotenv Loading and Credential Isolation Between Test Tiers
------------------------------------------------------------

Two things are worth pinning here. The dotenv parser is hand rolled, so
it needs the usual malformed-input cases covered, and the isolation
between the default suite and the ``live`` suite is a security boundary
rather than a convenience: a regression that let the default suite read
``.env.local`` would put a real broker credential into every test run.
"""

import os
import pathlib

from typing import Dict

import pytest

from tests.conftest import ENVIRONMENT_FILE, loadEnvironmentFile

SAMPLE_FILE = """
# a comment line that must be skipped
ALGOTRADERS_FYERS_CLIENT_ID=SAMPLE-100

   # an indented comment, also skipped
ALGOTRADERS_FYERS_SECRET_KEY = spaced-out-secret
ALGOTRADERS_KITE_API_KEY="double-quoted"
ALGOTRADERS_KITE_API_SECRET='single-quoted'
ALGOTRADERS_TOKEN_DIR=.algotraders/tokens

a line without an equals sign is ignored
ALGOTRADERS_EMPTY=
"""


def test_parsesEveryLineShapeInTheTemplate(
    tmp_path : pathlib.Path, monkeypatch : pytest.MonkeyPatch
) -> None:
    """
    Comments, blank lines, padding and one layer of quotes are handled.
    """

    path = tmp_path / ".env.local"
    path.write_text(SAMPLE_FILE, encoding = "utf-8")

    values = loadEnvironmentFile(path, monkeypatch)

    assert values["ALGOTRADERS_FYERS_CLIENT_ID"] == "SAMPLE-100"
    assert values["ALGOTRADERS_FYERS_SECRET_KEY"] == "spaced-out-secret"
    assert values["ALGOTRADERS_KITE_API_KEY"] == "double-quoted"
    assert values["ALGOTRADERS_KITE_API_SECRET"] == "single-quoted"
    assert values["ALGOTRADERS_EMPTY"] == ""

    assert "a line without an equals sign is ignored" not in values


def test_exportsIntoTheProcessEnvironment(
    tmp_path : pathlib.Path, monkeypatch : pytest.MonkeyPatch
) -> None:
    """
    Parsed values reach :func:`os.getenv`, which is the only way the
    library ever reads configuration.
    """

    path = tmp_path / ".env.local"
    path.write_text("ALGOTRADERS_FYERS_PIN=4321\n", encoding = "utf-8")

    loadEnvironmentFile(path, monkeypatch)

    assert os.getenv("ALGOTRADERS_FYERS_PIN") == "4321"


def test_missingFileSkipsRatherThanFails(
    tmp_path : pathlib.Path, monkeypatch : pytest.MonkeyPatch
) -> None:
    """
    ``.env.local`` is git ignored and absent on a fresh clone, so a live
    run without it is a skip, not a failure.
    """

    with pytest.raises(BaseException) as failure:
        loadEnvironmentFile(tmp_path / "absent.local", monkeypatch)

    assert failure.typename == "Skipped"


def test_defaultSuiteNeverReadsTheRealEnvironmentFile(
    dummyEnvironment : Dict[str, str]
) -> None:
    """
    An unmarked test is isolated from ``.env.local`` even when the file
    exists on the developer's machine.

    This is the boundary that keeps a real Fyers or Kite secret out of
    an ordinary ``pytest`` run.
    """

    assert dummyEnvironment["ALGOTRADERS_FYERS_CLIENT_ID"] == "TESTAPP-100"
    assert dummyEnvironment["ALGOTRADERS_FYERS_SECRET_KEY"] == "test-fyers-secret"

    # ? the seeded directories live under tmp_path, so nothing a test
    # writes can land on a real token store
    for name in ("TOKEN_DIR", "CACHE_DIR", "LOG_DIR"):
        value = os.getenv(f"ALGOTRADERS_{name}", "")

        assert value
        assert not value.startswith(str(ENVIRONMENT_FILE.parent / ".algotraders"))


def test_templateIsTrackedAndDocumentsEveryVariable() -> None:
    """
    ``.env.example`` is the tracked contract for local setup, so it must
    list every variable the library reads and must never carry a value
    that looks like a real credential.
    """

    template = ENVIRONMENT_FILE.parent / ".env.example"

    assert template.is_file(), "the tracked .env.example template is missing"

    content = template.read_text(encoding = "utf-8")

    for name in (
        "ALGOTRADERS_FYERS_CLIENT_ID", "ALGOTRADERS_FYERS_SECRET_KEY",
        "ALGOTRADERS_FYERS_REDIRECT_URL", "ALGOTRADERS_FYERS_PIN",
        "ALGOTRADERS_KITE_API_KEY", "ALGOTRADERS_KITE_API_SECRET",
        "ALGOTRADERS_KITE_REDIRECT_URL", "ALGOTRADERS_TOKEN_DIR",
        "ALGOTRADERS_CACHE_DIR", "ALGOTRADERS_LOG_DIR"
    ):
        assert name in content, f"{name} is undocumented in .env.example"

    # ! a real Fyers app secret leaked through this template once already
    assert "AVNS_" not in content
    assert "FX0KN0G7PX" not in content
