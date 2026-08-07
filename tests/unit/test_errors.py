# -*- encoding: utf-8 -*-

"""
Exception Hierarchy Contract
------------------------------

The tree is shaped by what a caller should do next, so these tests
assert the inheritance relationships a host application will actually
rely on when writing ``except`` clauses, plus the fields the FastAPI
transport reads when turning an error into an HTTP response.
"""

import pytest

from algotraders import errors
from algotraders.errors import (
    AlgoTradersError, AuthenticationError, AuthorizationRequired, BrokerError,
    BrokerConfigurationError, DataError, DataSubscriptionRequired,
    InstrumentError, InstrumentNotFound, InvalidCredentials, InvalidOrder,
    MissingDependency, OrderError, OrderRejected, RateLimitExceeded,
    StreamError, SubscriptionLimitExceeded, TokenExpiredError,
    TransientBrokerError, UnavailableBroker, UnsupportedTimeframe
)


@pytest.mark.parametrize("failure", [
    BrokerError, UnavailableBroker, BrokerConfigurationError,
    TransientBrokerError, RateLimitExceeded, AuthenticationError,
    InvalidCredentials, AuthorizationRequired, TokenExpiredError,
    InstrumentError, InstrumentNotFound, DataError, UnsupportedTimeframe,
    DataSubscriptionRequired, OrderError, InvalidOrder, OrderRejected,
    StreamError, SubscriptionLimitExceeded, MissingDependency
])
def test_everyErrorDescendsFromTheRoot(failure : type) -> None:
    """
    One ``except AlgoTradersError`` traps everything the library raises.
    """

    assert issubclass(failure, AlgoTradersError)


def test_existingBrokerErrorsKeepTheirPlace() -> None:
    """
    The two original errors were re-parented, not replaced. Any caller
    already catching ``BrokerError`` keeps working.
    """

    assert issubclass(UnavailableBroker, BrokerError)
    assert issubclass(BrokerConfigurationError, BrokerError)

    failure = BrokerConfigurationError(
        name = "fyers", requirements = ["fyers_apiv3"]
    )

    assert isinstance(failure, BrokerError)
    assert failure.name == "fyers"
    assert failure.requirements == ["fyers_apiv3"]


def test_rateLimitIsRetryableButOrderRejectionIsNot() -> None:
    """
    The retry helper keys off ``TransientBrokerError``, so this
    relationship is what stops a rejected order being resent.
    """

    assert issubclass(RateLimitExceeded, TransientBrokerError)
    assert not issubclass(OrderRejected, TransientBrokerError)
    assert not issubclass(DataSubscriptionRequired, TransientBrokerError)


def test_rateLimitCarriesRetryAfter() -> None:
    """
    A broker supplied delay is preserved so backoff can honour it in
    preference to its own exponential schedule.
    """

    assert RateLimitExceeded(retryAfter = 2.5).retryAfter == 2.5
    assert RateLimitExceeded().retryAfter is None


def test_authorizationRequiredCarriesTheLoginURL() -> None:
    """
    This is the error the transport turns into an HTTP 409, so the login
    URL and state must survive on the exception itself.
    """

    failure = AuthorizationRequired(
        loginURL = "https://kite.zerodha.com/connect/login?v=3&api_key=x",
        brokerName = "kite", state = "abc123"
    )

    assert failure.loginURL.startswith("https://")
    assert failure.brokerName == "kite"
    assert failure.state == "abc123"
    assert "initialize(authCode" in str(failure)


def test_missingDependencyNamesTheInstallCommand() -> None:
    """
    The message has to be copy-pasteable, because it is the only thing
    standing between a user and a bare ``ModuleNotFoundError``.
    """

    failure = MissingDependency("server", packages = ["fastapi"])
    message = str(failure)

    assert "pip install algotraders[server]" in message
    assert "fastapi" in message
    assert failure.extraName == "server"


def test_unsupportedTimeframeListsWhatIsAvailable() -> None:
    """
    Refusing ``1M`` is only useful if the message says what the broker
    does serve.
    """

    failure = UnsupportedTimeframe(
        brokerName = "fyers", timeframe = "1M", supported = ["1m", "1D"]
    )
    message = str(failure)

    assert "fyers" in message
    assert "1M" in message
    assert "1m" in message and "1D" in message


def test_instrumentNotFoundIdentifiesTheKey() -> None:
    """
    Resolution failures name the key that failed, since this replaces
    18,059 rate limited round trips that each learned the same thing.
    """

    failure = InstrumentNotFound("NSE:NOTREAL-EQ", brokerName = "fyers")

    assert "NSE:NOTREAL-EQ" in str(failure)
    assert failure.key == "NSE:NOTREAL-EQ"


def test_orderRejectedPreservesTheBrokerMessage() -> None:
    """
    The broker's own wording is the only actionable detail an end user
    receives, so it is kept verbatim rather than summarised.
    """

    failure = OrderRejected(
        "order rejected", brokerMessage = "insufficient funds", code = -99
    )

    assert "insufficient funds" in str(failure)
    assert failure.brokerMessage == "insufficient funds"
    assert failure.code == -99


def test_invalidCredentialsListsUnsetVariables() -> None:
    """
    Configuration failures name the environment variables to set.
    """

    failure = InvalidCredentials(
        "could not resolve fyers credentials",
        brokerName = "fyers", missing = ["ALGOTRADERS_FYERS_CLIENT_ID"]
    )

    assert "ALGOTRADERS_FYERS_CLIENT_ID" in str(failure)
    assert failure.brokerName == "fyers"


def test_subscriptionLimitReportsBothNumbers() -> None:
    """
    Enforced locally, because the vendor silently drops the overflow and
    leaves the caller believing it is receiving ticks that never arrive.
    """

    failure = SubscriptionLimitExceeded("kite", limit = 3000, requested = 3001)
    message = str(failure)

    assert "3,000" in message
    assert "3,001" in message


def test_everyExportedNameResolves() -> None:
    """
    ``__all__`` and the module contents cannot drift apart.
    """

    for name in errors.__all__:
        assert hasattr(errors, name), f"{name} is exported but missing"
        assert issubclass(getattr(errors, name), Exception)
