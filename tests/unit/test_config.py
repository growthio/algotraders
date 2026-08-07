# -*- encoding: utf-8 -*-

"""
Environment Backed Credential Resolution
------------------------------------------

Two properties matter here beyond "the values load". Precedence has to
put the process environment ahead of the dotenv file, because a deployed
container must never be overridden by a stray file in its working
directory. And a credentials object has to survive being printed: the
whole point of :class:`~pydantic.SecretStr` is that a debug log or an
error page cannot leak an app secret.
"""

import pathlib

from typing import Dict

import pytest

from algotraders import config as configModule
from algotraders.config import (
    BaseBrokerCredentials, FyersCredentials, KiteCredentials, RuntimeConfig,
    credentialsFor, registerCredentials, resolveEnvironmentFile,
    runtimeConfig
)
from algotraders.errors import InvalidCredentials


def test_resolvesFyersCredentialsFromEnvironment(
    dummyEnvironment : Dict[str, str]
) -> None:
    """
    Fields map onto the documented ``ALGOTRADERS_FYERS_*`` variables.
    """

    credentials = credentialsFor("fyers")

    assert isinstance(credentials, FyersCredentials)
    assert credentials.brokerName == "fyers"
    assert credentials.clientId.get_secret_value() == "TESTAPP-100"
    assert credentials.secretKey.get_secret_value() == "test-fyers-secret"
    assert credentials.pin.get_secret_value() == "1234"


def test_resolvesKiteCredentialsFromDifferentVariableNames(
    dummyEnvironment : Dict[str, str]
) -> None:
    """
    Kite names its credentials differently, but the field names stay
    identical so downstream code never branches on the broker.
    """

    credentials = credentialsFor("kite")

    assert isinstance(credentials, KiteCredentials)
    assert credentials.clientId.get_secret_value() == "test-kite-key"
    assert credentials.secretKey.get_secret_value() == "test-kite-secret"

    # ? Kite has no refresh flow, so it has no PIN to carry
    assert credentials.pin is None


def test_environmentBeatsDotenvFile(
    tmp_path : pathlib.Path, monkeypatch : pytest.MonkeyPatch
) -> None:
    """
    A deployed environment variable must win over a dotenv file.

    Getting this backwards would let a forgotten ``.env.local`` in a
    container image silently override real production credentials.
    """

    envFile = tmp_path / ".env.local"
    envFile.write_text(
        "ALGOTRADERS_FYERS_CLIENT_ID=FROM-FILE\n"
        "ALGOTRADERS_FYERS_SECRET_KEY=file-secret\n",
        encoding = "utf-8"
    )

    # ? the secret key is cleared so the file is the only source for it,
    # which proves both halves of the precedence rule in one assertion
    monkeypatch.setenv("ALGOTRADERS_FYERS_CLIENT_ID", "FROM-ENVIRONMENT")
    monkeypatch.delenv("ALGOTRADERS_FYERS_SECRET_KEY", raising = False)

    credentials = credentialsFor("fyers", envFile = envFile)

    assert credentials.clientId.get_secret_value() == "FROM-ENVIRONMENT"
    assert credentials.secretKey.get_secret_value() == "file-secret"


def test_dotenvFillsGapsLeftByTheEnvironment(
    tmp_path : pathlib.Path, monkeypatch : pytest.MonkeyPatch
) -> None:
    """
    Values absent from the environment are read from the dotenv file,
    which is what makes local development work at all.
    """

    for name in ("ALGOTRADERS_FYERS_CLIENT_ID", "ALGOTRADERS_FYERS_SECRET_KEY"):
        monkeypatch.delenv(name, raising = False)

    envFile = tmp_path / ".env.local"
    envFile.write_text(
        "ALGOTRADERS_FYERS_CLIENT_ID=FROM-FILE-100\n"
        "ALGOTRADERS_FYERS_SECRET_KEY=file-secret\n",
        encoding = "utf-8"
    )

    credentials = credentialsFor("fyers", envFile = envFile)

    assert credentials.clientId.get_secret_value() == "FROM-FILE-100"


def test_missingDotenvFileIsNotAnError(
    tmp_path : pathlib.Path, dummyEnvironment : Dict[str, str]
) -> None:
    """
    A dotenv file is optional. Production has none, and requiring one
    would break every containerised deployment.
    """

    assert resolveEnvironmentFile(tmp_path / "absent.local") is None

    credentials = credentialsFor("fyers", envFile = tmp_path / "absent.local")

    assert credentials.clientId.get_secret_value() == "TESTAPP-100"


def test_missingCredentialsNameTheEnvironmentVariables(
    monkeypatch : pytest.MonkeyPatch, tmp_path : pathlib.Path
) -> None:
    """
    The failure tells the operator which variables to set, using the
    variable names rather than the internal pydantic field names.
    """

    monkeypatch.setattr(configModule, "DEFAULT_ENV_FILE", str(tmp_path / "none"))

    for name in ("ALGOTRADERS_FYERS_CLIENT_ID", "ALGOTRADERS_FYERS_SECRET_KEY"):
        monkeypatch.delenv(name, raising = False)

    with pytest.raises(InvalidCredentials) as failure:
        credentialsFor("fyers")

    message = str(failure.value)

    assert "ALGOTRADERS_FYERS_CLIENT_ID" in message
    assert "ALGOTRADERS_FYERS_SECRET_KEY" in message


def test_unknownBrokerReportsWhatIsRegistered() -> None:
    """
    An unregistered broker fails with an actionable list rather than a
    bare ``KeyError``.
    """

    with pytest.raises(InvalidCredentials) as failure:
        credentialsFor("not-a-broker")

    assert "fyers" in str(failure.value)


def test_credentialsNeverLeakThroughRepresentation(
    dummyEnvironment : Dict[str, str]
) -> None:
    """
    Neither ``repr``, ``str`` nor ``model_dump_json`` may expose a
    secret. This is the property that makes it safe to log a
    credentials object or return it from a diagnostic endpoint.
    """

    credentials = credentialsFor("fyers")

    for rendered in (
        repr(credentials), str(credentials),
        credentials.model_dump_json(), f"{credentials}"
    ):
        assert "test-fyers-secret" not in rendered
        assert "TESTAPP-100" not in rendered

    # ? the masked client id suffix survives so two configured accounts
    # remain distinguishable in a log
    assert "-100" in repr(credentials)


def test_secretsAreNotLoggedDuringResolution(
    dummyEnvironment : Dict[str, str], caplog : pytest.LogCaptureFixture
) -> None:
    """
    The debug line emitted while resolving credentials carries nothing
    sensitive, even at the most verbose level.
    """

    import logging

    with caplog.at_level(logging.DEBUG, logger = "algotraders.config"):
        credentialsFor("fyers")

    assert "test-fyers-secret" not in caplog.text


def test_overridesBypassTheEnvironmentEntirely(
    dummyEnvironment : Dict[str, str]
) -> None:
    """
    A host holding credentials in its own database can pass them
    directly, without exporting anything into the process environment.
    """

    credentials = credentialsFor(
        "fyers", clientId = "DIRECT-100", secretKey = "direct-secret"
    )

    assert credentials.clientId.get_secret_value() == "DIRECT-100"
    assert credentials.secretKey.get_secret_value() == "direct-secret"


def test_runtimeConfigDefaultsUnderIgnoredDirectory(
    monkeypatch : pytest.MonkeyPatch, tmp_path : pathlib.Path
) -> None:
    """
    With nothing configured, every path lands under ``.algotraders``,
    which the repository ``.gitignore`` already covers.
    """

    monkeypatch.setattr(configModule, "DEFAULT_ENV_FILE", str(tmp_path / "none"))

    for name in (
        "ALGOTRADERS_TOKEN_DIR", "ALGOTRADERS_CACHE_DIR", "ALGOTRADERS_LOG_DIR"
    ):
        monkeypatch.delenv(name, raising = False)

    config = runtimeConfig()

    assert config.tokenDirectory == pathlib.Path(".algotraders/tokens")
    assert config.cacheDirectory == pathlib.Path(".algotraders/cache")
    assert config.logDirectory == pathlib.Path(".algotraders/logs")


def test_runtimeConfigReadsEnvironment(
    dummyEnvironment : Dict[str, str]
) -> None:
    """
    Configured directories are honoured, which is how the test suite
    keeps every write inside ``tmp_path``.
    """

    config = runtimeConfig()

    assert str(config.tokenDirectory) == dummyEnvironment["ALGOTRADERS_TOKEN_DIR"]


def test_readingConfigurationHasNoFilesystemSideEffect(
    dummyEnvironment : Dict[str, str]
) -> None:
    """
    Construction creates nothing. Directories appear only on an explicit
    ``prepare()``, so importing the library cannot litter a filesystem.
    """

    config = runtimeConfig()

    assert not config.tokenDirectory.exists()

    config.prepare()

    assert config.tokenDirectory.is_dir()
    assert config.cacheDirectory.is_dir()
    assert config.logDirectory.is_dir()


def test_thirdPartyBrokerCanRegisterCredentials(
    monkeypatch : pytest.MonkeyPatch
) -> None:
    """
    An out of tree broker plugs in without editing ``config.py``,
    mirroring how ``registerBroker`` works for the broker itself.
    """

    from pydantic import Field, SecretStr
    from pydantic_settings import SettingsConfigDict

    class CustomCredentials(BaseBrokerCredentials):
        model_config = SettingsConfigDict(extra = "ignore", frozen = True)

        brokerName : str = "custom"
        clientId : SecretStr = Field(validation_alias = "CUSTOM_ID")
        secretKey : SecretStr = Field(validation_alias = "CUSTOM_SECRET")

    monkeypatch.setitem(
        configModule.CREDENTIAL_MODELS, "custom", CustomCredentials
    )
    monkeypatch.setenv("CUSTOM_ID", "custom-id")
    monkeypatch.setenv("CUSTOM_SECRET", "custom-secret")

    credentials = credentialsFor("custom")

    assert credentials.brokerName == "custom"
    assert credentials.clientId.get_secret_value() == "custom-id"

    assert callable(registerCredentials)


def test_runtimeConfigIsFrozen(dummyEnvironment : Dict[str, str]) -> None:
    """
    Configuration is immutable once resolved, so no component can
    reconfigure the process behind another's back.
    """

    config = runtimeConfig()

    with pytest.raises(Exception):
        config.tokenDirectory = pathlib.Path("/tmp/elsewhere")

    assert isinstance(config, RuntimeConfig)
