# -*- encoding: utf-8 -*-

"""
Environment Backed Configuration for the AlgoTraders Library
--------------------------------------------------------------

Credentials reach the library through :mod:`pydantic_settings`, which
resolves each field from the process environment first and from a
dotenv file second. That precedence is deliberate: a container or a
secret manager exports real values into the environment, while the
dotenv file is a local development convenience, and the deployed
configuration must always win over a file that happened to be left in
the working directory.

Every secret is a :class:`~pydantic.SecretStr`. That gives masking in
``repr``, in ``str`` and in ``model_dump_json`` for free, so a
credentials object can be logged or returned from a debug endpoint
without leaking. Reading the real value requires the explicit
``get_secret_value()`` call, which makes every disclosure a visible line
of code in review.

Adding a broker means adding one class here and one registry entry. The
class carries the exact environment variable names for that broker,
because the two supported brokers do not agree on what a credential is
called: Fyers issues a client id and a secret key, Kite an API key and
an API secret.

**Example Usage**

Resolve Fyers credentials from the environment, falling back to
``.env.local`` when it exists.

.. code-block:: python

    from algotraders.config import credentialsFor, runtimeConfig

    credentials = credentialsFor("fyers")
    paths = runtimeConfig()

    print(credentials)                       # secrets already masked
    print(credentials.clientId.get_secret_value())

:NOTE: Reading configuration never contacts a broker and never creates
    a directory. Call ``RuntimeConfig.prepare()`` when the directories
    are actually needed.
"""

import pathlib

from typing import Any, Dict, Optional, Type

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from algotraders.errors import InvalidCredentials
from algotraders.logger import describeSecret, getLogger

logger = getLogger(__name__)

# ? resolved at call time rather than baked into a model_config, so a
# test can point the loader somewhere harmless with a single monkeypatch
DEFAULT_ENV_FILE = ".env.local"


class RuntimeConfig(BaseSettings):
    """
    Filesystem locations the library writes to.

    All three default to a ``.algotraders`` directory beside the process
    working directory, which the repository ``.gitignore`` already
    covers.

    :type  tokenDirectory: pathlib.Path
    :param tokenDirectory: Where ``FileTokenStore`` keeps daily session
        tokens.

    :type  cacheDirectory: pathlib.Path
    :param cacheDirectory: Where downloaded instrument masters are
        cached between runs.

    :type  logDirectory: pathlib.Path
    :param logDirectory: Where broker SDK log files are written. This
        one matters more than it looks: left unset, the Fyers SDK writes
        ``fyersApi.log`` into whatever the working directory happens to
        be, which is how a 570,646 line log file was once produced
        inside ``examples/fyers/``.
    """

    model_config = SettingsConfigDict(
        env_file_encoding = "utf-8",
        extra = "ignore",
        case_sensitive = False,
        frozen = True,

        # ? fields are bound to broker specific environment variables via
        # validation_alias; this keeps the field name usable too, so a
        # host can pass credentials directly instead of exporting them
        populate_by_name = True
    )

    tokenDirectory : pathlib.Path = Field(
        default = pathlib.Path(".algotraders/tokens"),
        validation_alias = "ALGOTRADERS_TOKEN_DIR"
    )

    cacheDirectory : pathlib.Path = Field(
        default = pathlib.Path(".algotraders/cache"),
        validation_alias = "ALGOTRADERS_CACHE_DIR"
    )

    logDirectory : pathlib.Path = Field(
        default = pathlib.Path(".algotraders/logs"),
        validation_alias = "ALGOTRADERS_LOG_DIR"
    )


    def prepare(self) -> "RuntimeConfig":
        """
        Create every configured directory if it does not already exist.

        Kept separate from construction so that merely reading the
        configuration has no filesystem side effect.

        :rtype:   RuntimeConfig
        :returns: ``self``, so the call can be chained.
        """

        for directory in (
            self.tokenDirectory, self.cacheDirectory, self.logDirectory
        ):
            directory.mkdir(parents = True, exist_ok = True)

        return self


class BaseBrokerCredentials(BaseSettings):
    """
    Credentials for a single broker application.

    Concrete subclasses bind the fields to that broker's environment
    variable names. The field *names* stay identical across brokers so
    every downstream consumer, the session, the authentication class and
    the resolver, can treat credentials uniformly.

    :type  clientId: SecretStr
    :param clientId: Application identifier. Fyers calls this the client
        id and formats it as ``<APPID>-100``; Kite calls it the API key.
        Treated as a secret because it identifies a real trading
        account, even though it is technically the public half.

    :type  secretKey: SecretStr
    :param secretKey: Application secret issued alongside the client id.
        This is not the DEMAT account password.

    :type  redirectURL: Optional[str]
    :param redirectURL: OAuth redirect URI. Must match the value
        registered on the broker's developer console byte for byte, and
        for a hosted application it points at the host's own callback
        route rather than at anything inside this library.

    :type  pin: Optional[SecretStr]
    :param pin: Trading PIN. Only Fyers uses it, and only on the refresh
        token flow.
    """

    model_config = SettingsConfigDict(
        env_file_encoding = "utf-8",
        extra = "ignore",
        case_sensitive = False,
        frozen = True,

        # ? fields are bound to broker specific environment variables via
        # validation_alias; this keeps the field name usable too, so a
        # host can pass credentials directly instead of exporting them
        populate_by_name = True
    )

    clientId : SecretStr
    secretKey : SecretStr
    redirectURL : Optional[str] = None
    pin : Optional[SecretStr] = None

    # ! overridden by every concrete subclass, used in error messages
    brokerName : str = "unknown"


    def __repr__(self) -> str:
        """
        Render the credentials with every secret masked.

        Pydantic already masks a ``SecretStr``, this adds the last few
        characters of the client id so an operator can tell two
        configured accounts apart in a log.
        """

        return (
            f"{type(self).__name__}("
            f"brokerName={self.brokerName!r}, "
            f"{describeSecret('clientId', self.clientId)}, "
            f"{describeSecret('secretKey', self.secretKey, visible = 0)}, "
            f"redirectURL={self.redirectURL!r})"
        )


    __str__ = __repr__


class FyersCredentials(BaseBrokerCredentials):
    """
    Fyers API v3 application credentials.

    Fyers is the only supported broker with a working refresh flow, and
    that flow needs the trading PIN, so :attr:`pin` is effectively
    required for unattended daily operation even though it is optional
    for the initial login.
    """

    brokerName : str = "fyers"

    clientId : SecretStr = Field(
        validation_alias = "ALGOTRADERS_FYERS_CLIENT_ID"
    )

    secretKey : SecretStr = Field(
        validation_alias = "ALGOTRADERS_FYERS_SECRET_KEY"
    )

    redirectURL : Optional[str] = Field(
        default = None, validation_alias = "ALGOTRADERS_FYERS_REDIRECT_URL"
    )

    pin : Optional[SecretStr] = Field(
        default = None, validation_alias = "ALGOTRADERS_FYERS_PIN"
    )


class KiteCredentials(BaseBrokerCredentials):
    """
    Zerodha Kite Connect v3 application credentials.

    Kite has no usable refresh flow for a standard application, so there
    is no PIN field: a new access token requires an interactive login
    every day after the 06:00 IST expiry.
    """

    brokerName : str = "kite"

    clientId : SecretStr = Field(
        validation_alias = "ALGOTRADERS_KITE_API_KEY"
    )

    secretKey : SecretStr = Field(
        validation_alias = "ALGOTRADERS_KITE_API_SECRET"
    )

    redirectURL : Optional[str] = Field(
        default = None, validation_alias = "ALGOTRADERS_KITE_REDIRECT_URL"
    )


CREDENTIAL_MODELS : Dict[str, Type[BaseBrokerCredentials]] = {
    "fyers" : FyersCredentials,
    "kite"  : KiteCredentials
}


def registerCredentials(
    brokerName : str, model : Type[BaseBrokerCredentials]
) -> None:
    """
    Register a credentials model for a third party broker.

    Mirrors ``registerBroker`` so an out of tree broker can be wired in
    without editing this module.

    :type  brokerName: str
    :param brokerName: Registry name of the broker, lower case.

    :type  model: Type[BaseBrokerCredentials]
    :param model: Subclass binding the broker's environment variables.
    """

    CREDENTIAL_MODELS[brokerName] = model


def resolveEnvironmentFile(
    envFile : Optional[Any] = None
) -> Optional[pathlib.Path]:
    """
    Decide which dotenv file, if any, should be consulted.

    :type  envFile: Optional[Any]
    :param envFile: Explicit path, or ``None`` to use
        :data:`DEFAULT_ENV_FILE`.

    :rtype:   Optional[pathlib.Path]
    :returns: The path when the file exists, otherwise ``None``. A
        missing dotenv file is normal and never an error, because the
        deployed configuration comes from the environment.
    """

    candidate = pathlib.Path(envFile if envFile is not None else DEFAULT_ENV_FILE)

    return candidate if candidate.is_file() else None


def runtimeConfig(envFile : Optional[Any] = None) -> RuntimeConfig:
    """
    Load the runtime directory configuration.

    :type  envFile: Optional[Any]
    :param envFile: Dotenv file to fall back on. Defaults to
        :data:`DEFAULT_ENV_FILE`.

    :rtype:   RuntimeConfig
    :returns: The resolved configuration. Directories are not created.
    """

    return RuntimeConfig(_env_file = resolveEnvironmentFile(envFile))


def credentialsFor(
    brokerName : str, envFile : Optional[Any] = None, **overrides : Any
) -> BaseBrokerCredentials:
    """
    Resolve one broker's credentials from the environment.

    Values are taken from the process environment first, then from the
    dotenv file, then from any explicit ``overrides`` passed here, which
    exist so a host application holding credentials in its own database
    can bypass the environment entirely.

    :type  brokerName: str
    :param brokerName: Registry name of the broker, for example
        ``"fyers"``.

    :type  envFile: Optional[Any]
    :param envFile: Dotenv file to fall back on. Defaults to
        :data:`DEFAULT_ENV_FILE`, and is ignored when absent.

    **Keyword Arguments**

    Any credential field may be supplied directly, which takes
    precedence over both the environment and the dotenv file.

        * **clientId** (*str*): Application identifier.
        * **secretKey** (*str*): Application secret.
        * **redirectURL** (*str*): OAuth redirect URI.
        * **pin** (*str*): Trading PIN, Fyers only.

    :raises InvalidCredentials: The broker has no registered credentials
        model, or one or more required values could not be resolved. The
        message names the missing environment variables rather than the
        pydantic field names, because the variables are what an operator
        actually has to set.

    :rtype:   BaseBrokerCredentials
    :returns: A frozen, fully populated credentials object.
    """

    model = CREDENTIAL_MODELS.get(brokerName)

    if model is None:
        raise InvalidCredentials(
            f"no credentials model registered for broker {brokerName!r}; "
            f"known brokers are {sorted(CREDENTIAL_MODELS)}",
            brokerName = brokerName
        )

    try:
        credentials = model(
            _env_file = resolveEnvironmentFile(envFile), **overrides
        )
    except Exception as error:
        missing = _missingVariables(model, error)

        raise InvalidCredentials(
            f"could not resolve {brokerName} credentials",
            brokerName = brokerName, missing = missing
        ) from error

    logger.debug("resolved credentials: %r", credentials)

    return credentials


def _missingVariables(
    model : Type[BaseBrokerCredentials], error : Exception
) -> list:
    """
    Translate a pydantic validation failure into environment variable
    names.

    A ``ValidationError`` reports field names, but the operator sets
    environment variables, so the two have to be mapped back before the
    message is useful.

    :rtype:   list
    :returns: Environment variable names that need to be set, sorted.
    """

    fields = {
        entry["loc"][0]
        for entry in getattr(error, "errors", lambda : [])()
        if entry.get("loc")
    }

    names = set()

    for field in fields:
        definition = model.model_fields.get(str(field))
        alias = getattr(definition, "validation_alias", None)

        names.add(str(alias) if alias else str(field))

    return sorted(names)


def configureVendorLogging(config : Optional[RuntimeConfig] = None) -> str:
    """
    Return the directory broker SDKs must be told to log into.

    Both vendor SDKs accept a log path and default to the process
    working directory when it is omitted. Always route them through
    here rather than letting them choose.

    :type  config: Optional[RuntimeConfig]
    :param config: Configuration to read. Loaded from the environment
        when omitted.

    :rtype:   str
    :returns: An existing directory path, as a string, because both SDKs
        expect ``str`` rather than :class:`pathlib.Path`.
    """

    resolved = config or runtimeConfig()
    resolved.logDirectory.mkdir(parents = True, exist_ok = True)

    return str(resolved.logDirectory)


__all__ = [
    "DEFAULT_ENV_FILE",
    "RuntimeConfig",
    "BaseBrokerCredentials",
    "FyersCredentials",
    "KiteCredentials",
    "CREDENTIAL_MODELS",
    "registerCredentials",
    "resolveEnvironmentFile",
    "runtimeConfig",
    "credentialsFor",
    "configureVendorLogging"
]
