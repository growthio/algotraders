# -*- encoding: utf-8 -*-

"""
Exception Hierarchy for the AlgoTraders Broker Integration Library
-------------------------------------------------------------------

Every error the library raises descends from :class:`AlgoTradersError`,
so a host application can wrap an entire call in one ``except`` clause
without also swallowing unrelated failures from its own stack.

Below that root the tree is shaped by **what the caller should do next**
rather than by where the failure happened, because that is the only
question an exception has to answer at a service boundary:

    * :class:`AuthorizationRequired` - send the user to a login URL.
    * :class:`TokenExpiredError` - refresh, then retry once.
    * :class:`RateLimitExceeded` - back off and retry later.
    * :class:`TransientBrokerError` - retry with backoff.
    * :class:`OrderRejected` - do not retry, surface the broker message.
    * :class:`DataSubscriptionRequired` - tell the user to subscribe.

That mapping is what lets ``transport/fastapi.py`` translate each error
into the right HTTP status without inspecting messages.

:NOTE: :class:`BrokerError` and its two original subclasses keep their
    public names and constructor signatures. They were re-parented under
    :class:`AlgoTradersError`, which is source compatible for any caller
    that was already catching them.
"""

from typing import Any, List, Optional, Sequence


class AlgoTradersError(Exception):
    """
    Root of every exception raised by :mod:`algotraders`.

    Catch this to trap anything the library can raise. Never raise it
    directly; always raise the most specific subclass available, because
    the subclass is what tells the caller how to recover.
    """

    pass


class MissingDependency(AlgoTradersError):
    """
    An optional feature was used without installing the extra that
    provides it.

    :type  extraName: str
    :param extraName: Name of the extra as declared in
        ``pyproject.toml``, for example ``server`` or ``fyers``.

    :type  packages: Sequence[str]
    :param packages: Distributions the extra pulls in, listed so the
        message can name what is actually missing.
    """

    def __init__(self, extraName : str, packages : Sequence[str] = ()) -> None:
        self.extraName = extraName
        self.packages = list(packages)

        detail = f" (provides {', '.join(self.packages)})" if packages else ""

        super().__init__(
            f"this feature needs the '{extraName}' extra{detail}; "
            f"install it with: pip install algotraders[{extraName}]"
        )


class BrokerError(AlgoTradersError):
    """
    A base error related to any broker services, for example when a
    broker is not registered or proper libraries are not available in
    the system. Any broker related custom error should be inherited
    from this base error class.
    """

    pass


class UnavailableBroker(BrokerError):
    """
    Raises an exception when the requested broker is not available in
    the system and a user may request one using GH issues.

    Distinct from :class:`BrokerConfigurationError`: the name is simply
    unknown, so no amount of installing packages will help.
    """

    pass


class BrokerConfigurationError(BrokerError):
    """
    Raises when the broker is available but proper configuration is
    not available in the system - for example required library is not
    present.

    :type  name: str
    :param name: Registered broker name that was requested.

    :type  requirements: list
    :param requirements: Distributions the broker declares in its
        registry entry point.
    """

    def __init__(self, name : str, requirements : list) -> None:
        self.name = name
        self.requirements = requirements

        # ? joined rather than interpolated as a list repr, which
        # rendered as "but ['fyers-apiv3>=3.1.7'] not met"
        packages = ", ".join(str(entry) for entry in requirements)

        super().__init__(
            f"broker {name} is registered but its requirements are not "
            f"installed: {packages}. Install it with "
            f"pip install algotraders[{name}]"
        )


class TransientBrokerError(BrokerError):
    """
    A broker call failed in a way that is worth retrying.

    Covers gateway errors, truncated responses and the empty body that
    both SDKs return as a JSON decode failure when an edge throttles the
    request. It is deliberately **not** raised for anything that changes
    account state, because a retry there risks a duplicate order.
    """

    pass


class RateLimitExceeded(TransientBrokerError):
    """
    The broker rejected the call for exceeding a published rate limit.

    :type  retryAfter: Optional[float]
    :param retryAfter: Seconds the broker asked the caller to wait, when
        it supplies one. The retry helper honours this in preference to
        its own exponential backoff, up to its own ceiling.
    """

    # ? read by the retry helper. A subclass sets this False to opt out
    # of retrying entirely without having to be removed from every
    # policy's retryOn tuple
    retryable = True

    def __init__(
        self, message : str = "broker rate limit exceeded",
        retryAfter : Optional[float] = None
    ) -> None:
        self.retryAfter = retryAfter

        super().__init__(message)


class DailyQuotaExhausted(RateLimitExceeded):
    """
    The application's daily call budget is spent.

    Distinct from an ordinary rate limit because the recovery is
    different in kind: a per second limit clears in a second, while this
    clears at the next IST midnight. Retrying it means sleeping for
    hours inside a request handler, so it is marked non-retryable and
    the caller is expected to reschedule rather than wait.
    """

    retryable = False


class AuthenticationError(AlgoTradersError):
    """
    Base class for every credential and session failure.
    """

    pass


class InvalidCredentials(AuthenticationError):
    """
    Credentials are missing, malformed, or rejected by the broker.

    :type  message: str
    :param message: Human readable summary of what went wrong.

    :type  brokerName: Optional[str]
    :param brokerName: Broker the credentials were resolved for.

    :type  missing: Sequence[str]
    :param missing: Environment variable names that could not be read,
        so the message can tell the operator exactly what to set.
    """

    def __init__(
        self, message : str, brokerName : Optional[str] = None,
        missing : Sequence[str] = ()
    ) -> None:
        self.brokerName = brokerName
        self.missing = list(missing)

        if self.missing:
            message = f"{message} (unset: {', '.join(self.missing)})"

        super().__init__(message)


class AuthorizationRequired(AuthenticationError):
    """
    No usable session exists and the broker needs an interactive login.

    This is the error that replaces the original blocking ``input()``
    prompt. The library cannot complete an OAuth flow on its own, so it
    hands the caller everything needed to start one and steps aside. A
    FastAPI host turns this into an HTTP 409 carrying ``loginURL``, the
    frontend redirects, and the resulting code comes back through
    ``initialize(authCode = ...)``.

    :type  loginURL: str
    :param loginURL: Broker hosted URL the end user must visit.

    :type  brokerName: Optional[str]
    :param brokerName: Broker the login belongs to.

    :type  state: Optional[str]
    :param state: Opaque value echoed back by the broker, used to tie
        the callback to the request that started it.
    """

    def __init__(
        self, loginURL : str, brokerName : Optional[str] = None,
        state : Optional[str] = None
    ) -> None:
        self.loginURL = loginURL
        self.brokerName = brokerName
        self.state = state

        broker = f"{brokerName} " if brokerName else ""

        super().__init__(
            f"{broker}session requires an interactive login; send the "
            "user to loginURL and pass the returned code back to "
            "initialize(authCode = ...)"
        )


class InvalidLoginState(AuthenticationError):
    """
    A login callback carried a ``state`` that was never issued, has
    already been redeemed, or has expired.

    This is the failure mode of a forged callback. An attacker who
    completes a login on their own account and then fires the resulting
    code at somebody else's callback route would otherwise rebind that
    host's session to the attacker's broker account. Treat it as hostile
    traffic rather than a user error: never retry it, never fall back to
    completing the login without a state.
    """

    def __init__(
        self, brokerName : Optional[str] = None,
        message : Optional[str] = None
    ) -> None:
        self.brokerName = brokerName

        broker = f"{brokerName} " if brokerName else ""

        super().__init__(
            message or (
                f"{broker}login callback carried an unrecognised or expired "
                "state; the request was not initiated by this session"
            )
        )


class TokenExpiredError(AuthenticationError):
    """
    The stored access token is no longer accepted by the broker.

    Raised both by the local expiry check and by the vendor error
    mapper. The second path matters more: a stored expiry timestamp is
    only ever a hint, since Kite has moved its 06:00 IST cutoff before,
    so mapping the broker's own rejection back to this type is what lets
    the token store heal itself.
    """

    pass


class InstrumentError(AlgoTradersError):
    """
    Base class for symbol and instrument resolution failures.
    """

    pass


class InstrumentNotFound(InstrumentError):
    """
    A symbol could not be resolved against the broker's instrument
    master.

    Raised locally, before any network call. The captured Fyers error
    log contained 18,059 responses rejecting an unknown symbol, each of
    which consumed a rate limited request to learn something the cached
    instrument master already knew.

    :type  key: Any
    :param key: The instrument key or symbol string that failed.

    :type  brokerName: Optional[str]
    :param brokerName: Broker whose master was searched.
    """

    def __init__(self, key : Any, brokerName : Optional[str] = None) -> None:
        self.key = key
        self.brokerName = brokerName

        broker = f" for {brokerName}" if brokerName else ""

        super().__init__(f"no instrument matches {key!r}{broker}")


class InvalidInstrumentKey(InstrumentError):
    """
    A canonical instrument key could not be parsed.

    Separate from :class:`InstrumentNotFound`: the string is malformed
    rather than merely unknown. These arrive in request bodies, so the
    distinction is the difference between a 400 and a 404, and both beat
    the 500 that an unmapped ``decimal.InvalidOperation`` used to
    produce.
    """

    pass


class InstrumentMasterUnavailable(InstrumentError):
    """
    The instrument master could not be downloaded and no cached copy
    exists to fall back on.

    A *stale* cache is not an error; the resolver serves it and flags
    itself. This is raised only when there is nothing at all to serve.
    """

    pass


class DataError(AlgoTradersError):
    """
    Base class for market data request failures.
    """

    pass


class UnsupportedTimeframe(DataError):
    """
    The requested canonical timeframe has no broker equivalent.

    ``1M`` and ``1Y`` exist in the host's ``private.timeframe_value``
    enum but neither Fyers nor Kite serves them, so the default is to
    fail rather than to quietly synthesise candles the broker never
    sent. Callers that want the rollup pass ``allowAggregation = True``.

    :type  brokerName: str
    :param brokerName: Broker that was asked for the timeframe.

    :type  timeframe: Any
    :param timeframe: The canonical timeframe that was requested.

    :type  supported: Sequence[Any]
    :param supported: Timeframes the broker does serve, listed so the
        message is actionable rather than merely negative.
    """

    def __init__(
        self, brokerName : str, timeframe : Any, supported : Sequence[Any] = ()
    ) -> None:
        self.brokerName = brokerName
        self.timeframe = timeframe
        self.supported : List[Any] = list(supported)

        detail = ""

        if self.supported:
            names = ", ".join(
                str(getattr(value, "value", value)) for value in self.supported
            )
            detail = f"; {brokerName} supports {names}"

        super().__init__(
            f"{brokerName} cannot serve timeframe "
            f"{getattr(timeframe, 'value', timeframe)}{detail}"
        )


class DataSubscriptionRequired(DataError):
    """
    The account lacks a paid data entitlement the call depends on.

    Kite Connect bills historical data as an add on, and an account
    without it fails every historical request with a permission error.
    That is a billing action, not a transient fault, so it must never be
    retried.
    """

    pass


class OrderError(AlgoTradersError):
    """
    Base class for order placement and management failures.
    """

    pass


class InvalidOrder(OrderError):
    """
    An order was rejected before reaching the broker.

    Raised by the broker mappers when an order type has no vendor
    equivalent.

    :NOTE: Constructing an ``OrderRequest`` with impossible fields
        raises :class:`pydantic.ValidationError`, **not** this. The
        model validators run inside pydantic, which wraps any
        ``ValueError`` they raise. That is the right behaviour - it
        gives per-field detail and FastAPI turns it into a 422
        automatically - but it does mean ``except InvalidOrder`` will
        not catch a malformed request. Catch ``ValidationError`` for
        construction and this for dispatch.
    """

    pass


class OrderRejected(OrderError):
    """
    The broker refused the order.

    Never retried. A rejection is a decision, and re-sending an order
    that may already have reached the exchange risks a double fill.

    :type  message: str
    :param message: Summary of the rejection raised by this library.

    :type  brokerMessage: Optional[str]
    :param brokerMessage: The broker's own rejection text, preserved
        verbatim because it is the only actionable detail the end user
        gets.

    :type  code: Optional[Any]
    :param code: Vendor error code, when the broker supplies one.
    """

    def __init__(
        self, message : str, brokerMessage : Optional[str] = None,
        code : Optional[Any] = None
    ) -> None:
        self.brokerMessage = brokerMessage
        self.code = code

        if brokerMessage:
            message = f"{message}: {brokerMessage}"

        super().__init__(message)


class StreamError(AlgoTradersError):
    """
    Base class for market data streaming failures.
    """

    pass


class SubscriptionLimitExceeded(StreamError):
    """
    A subscription request exceeded the broker's per connection cap.

    Enforced locally rather than left to the vendor, which silently
    drops the overflow and leaves the caller believing it is receiving
    ticks that never arrive.

    :type  brokerName: str
    :param brokerName: Broker the subscription was issued against.

    :type  limit: int
    :param limit: Maximum instruments the broker allows per connection.

    :type  requested: int
    :param requested: How many the caller asked for.
    """

    def __init__(self, brokerName : str, limit : int, requested : int) -> None:
        self.brokerName = brokerName
        self.limit = limit
        self.requested = requested

        super().__init__(
            f"{brokerName} allows {limit:,} instruments per connection, "
            f"{requested:,} requested"
        )


__all__ = [
    "AlgoTradersError", "MissingDependency",

    "BrokerError", "UnavailableBroker", "BrokerConfigurationError",
    "TransientBrokerError", "RateLimitExceeded", "DailyQuotaExhausted",

    "AuthenticationError", "InvalidCredentials", "AuthorizationRequired",
    "InvalidLoginState", "TokenExpiredError",

    "InstrumentError", "InstrumentNotFound", "InvalidInstrumentKey",
    "InstrumentMasterUnavailable",

    "DataError", "UnsupportedTimeframe", "DataSubscriptionRequired",

    "OrderError", "InvalidOrder", "OrderRejected",

    "StreamError", "SubscriptionLimitExceeded"
]
