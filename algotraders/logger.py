# -*- encoding: utf-8 -*-

"""
Logging With Credential Redaction for the AlgoTraders Library
---------------------------------------------------------------

A broker access token is a bearer credential: whoever holds it can place
orders on a live DEMAT account until it expires. Any log line carrying
one is a disclosure, and log lines travel further than most people
expect - aggregators, crash reporters, screen shares, support tickets.

This module is the answer to that. :func:`getLogger` returns a logger
with a :class:`RedactingFilter` already attached, so the safe path is
also the shortest one. Always call it instead of
:func:`logging.getLogger`.

Two implementation details are easy to get wrong and worth stating:

    * A :class:`logging.Filter` attached to a logger only runs for
      records logged **through that logger**. Records propagating up
      from a child are not filtered by the parent. Attaching the filter
      to the ``algotraders`` root would therefore protect nothing, which
      is why :func:`getLogger` attaches it to every logger it returns.
    * The library configures nothing globally. It attaches a
      :class:`logging.NullHandler` to its own root and never calls
      :func:`logging.basicConfig`, because handler policy belongs to the
      host application.

:NOTE: Redaction is a safety net, not a licence. Never pass a secret to
    a log call in the first place; this module exists for the cases
    somebody missed, and for the vendor payloads echoed back verbatim.
"""

import re
import logging
import traceback

from typing import Any, FrozenSet, Optional

ROOT_LOGGER_NAME = "algotraders"

MASK = "*" * 8

# ? every key name either SDK, either broker or this library uses for a
# credential. Matching is case insensitive, so one spelling per concept
# is enough, but both snake_case and camelCase appear in vendor payloads
#
# ! api_key and client_id are here because they ARE secrets in this
# domain: Kite's api_key is its client id, config.py documents clientId
# as identifying a real trading account, and the Kite login URL carries
# the key as a query parameter
REDACTION_KEYS : FrozenSet[str] = frozenset({
    "access_token", "accessToken",
    "refresh_token", "refreshToken",
    "auth_code", "authCode", "authorization_code", "authorizationCode",
    "request_token", "requestToken",
    "secret_key", "secretKey",
    "api_secret", "apiSecret",
    "api_key", "apiKey",
    "client_id", "clientId",
    "client_secret", "clientSecret",
    "app_id", "appId", "app_id_hash", "appIdHash",
    "session_token", "sessionToken", "id_token", "idToken",
    "checksum", "password", "pin", "token", "enctoken", "totp",
    "public_token", "publicToken",
    "Authorization"
})

# ? schemes that precede a credential in an Authorization header. The
# value pattern below stops at whitespace, so without this the scheme
# word is what gets masked and the credential survives in clear
_AUTH_SCHEMES = ("Bearer", "Basic", "token", "enctoken", "Token")

# ? matches key=value, key: value, "key": "value" and key = value across
# JSON, query strings and repr output, capturing the value to replace.
# The optional scheme group is what makes `Authorization: token <secret>`
# mask the secret rather than the word "token"
_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(['\"]?\b(?:" + "|".join(
        sorted((re.escape(key) for key in REDACTION_KEYS), key = len,
               reverse = True)
    ) + r")\b['\"]?\s*[:=]\s*)"
    r"(?:(?:" + "|".join(_AUTH_SCHEMES) + r")\s+)?"
    r"(['\"]?)([^\s,;&'\"}\])]+)"
)

# ! a bare `Bearer <token>` with no recognised key name in front of it
# still has to be masked, because vendor SDKs log request headers
# verbatim.
#
# Deliberately limited to Bearer and Basic. "token" and "enctoken" are
# ordinary English in this domain - "token expired", "token refresh
# failed" - and matching them bare turned every such line into
# "the token ********". Those two schemes are already covered by the
# optional scheme group in the assignment pattern above, which only
# fires after a recognised key like `Authorization:`.
#
# The length floor keeps prose such as "Basic auth failed" intact while
# still catching any credential worth having
_SCHEME_PATTERN = re.compile(
    r"(?i)\b(Bearer|Basic)\s+([^\s,;'\"]{8,})"
)

# ! a JSON Web Token is recognisable on its own and one already leaked
# into this repository once, so it is redacted wherever it appears even
# when it is not attached to a key the pattern above knows about
_JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")


def maskSecret(value : Optional[str], visible : int = 4) -> str:
    """
    Render a secret as a fixed width mask, optionally keeping a short
    suffix so an operator can tell two credentials apart.

    The suffix is never longer than ``visible``, and a value short
    enough that the suffix would reveal most of it is masked entirely.

    :type  value: Optional[str]
    :param value: The secret. ``None`` and empty values render as
        ``<unset>``, which is genuinely useful in a configuration dump
        and gives nothing away.

    :type  visible: int
    :param visible: Trailing characters to keep. Pass ``0`` to mask the
        value completely.

    **Example Usage**

    Identify which client id a session is using without printing it.

    .. code-block:: python

        maskSecret("FX0KN0G7PX-100")  # '********-100'
        maskSecret("short")           # '********'
        maskSecret(None)              # '<unset>'

    :rtype:   str
    :returns: The masked representation, safe to log.
    """

    if not value:
        return "<unset>"

    text = str(value)

    if visible <= 0 or len(text) <= visible * 2:
        return MASK

    return f"{MASK}{text[-visible:]}"


def redactText(text : str) -> str:
    """
    Replace every credential-looking value in a string with the mask.

    Applied to fully rendered log messages, so it has to cope with
    whatever shape the value arrived in: a vendor JSON blob, a query
    string, a ``repr`` or a plain sentence.

    :type  text: str
    :param text: The rendered message.

    :rtype:   str
    :returns: The message with secret values masked. Keys, punctuation
        and structure are preserved so the line stays readable.
    """

    def replaceAssignment(match : "re.Match[str]") -> str:
        return f"{match.group(1)}{match.group(2)}{MASK}"

    def replaceScheme(match : "re.Match[str]") -> str:
        return f"{match.group(1)} {MASK}"

    redacted = _ASSIGNMENT_PATTERN.sub(replaceAssignment, text)
    redacted = _SCHEME_PATTERN.sub(replaceScheme, redacted)

    return _JWT_PATTERN.sub(MASK, redacted)


class RedactingFilter(logging.Filter):
    """
    A logging filter that masks credentials in the rendered message.

    The filter renders the record, redacts, and writes the result back
    as a literal message with the arguments cleared. Rendering eagerly
    costs the lazy-formatting optimisation, but only for records that
    actually contained something secret, and a leaked token is worth far
    more than the microseconds saved.

    Exception text is redacted too. A vendor exception routinely carries
    the failing request URL, and a broker SDK puts the access token in
    the query string, so ``logger.exception(...)`` was the single widest
    hole while only the rendered message was being scrubbed.
    """

    def filter(self, record : logging.LogRecord) -> bool:
        """
        Redact the record in place and always allow it through.

        :rtype:   bool
        :returns: Always ``True``. This filter censors records, it never
            drops them, because a silently missing log line is its own
            debugging problem.
        """

        try:
            rendered = record.getMessage()
        except Exception:  # pragma: no cover - defensive, bad %-args
            return True

        redacted = redactText(rendered)

        if redacted != rendered:
            record.msg = redacted
            record.args = ()

        self.__redactException__(record)

        return True


    def __redactException__(self, record : logging.LogRecord) -> None:
        """
        Render and scrub the traceback attached to a record.

        ``Formatter.formatException`` runs long after any filter, so the
        traceback has to be pre-rendered into ``exc_text`` here for the
        redaction to reach it. Formatters reuse ``exc_text`` when it is
        already populated, so this both scrubs and short circuits.
        """

        if record.exc_info and not record.exc_text:
            try:
                record.exc_text = "".join(
                    traceback.format_exception(*record.exc_info)
                )
            except Exception:  # pragma: no cover - defensive
                record.exc_text = None

        if record.exc_text:
            record.exc_text = redactText(record.exc_text)

        if record.stack_info:
            record.stack_info = redactText(record.stack_info)


def installRedaction(handler : logging.Handler) -> logging.Handler:
    """
    Attach credential redaction to a handler.

    :func:`getLogger` covers everything this library logs, but nothing
    it can do reaches records emitted by ``fyers_apiv3``, ``kiteconnect``
    or ``urllib3`` - and those are exactly the loggers that echo vendor
    payloads. A handler level filter is the only placement that sees
    every record in the process, so a host that wants blanket coverage
    installs it on its own handlers.

    :type  handler: logging.Handler
    :param handler: The handler to protect.

    **Example Usage**

    .. code-block:: python

        import logging
        from algotraders.logger import installRedaction

        handler = logging.StreamHandler()
        logging.getLogger().addHandler(installRedaction(handler))

    :rtype:   logging.Handler
    :returns: The same handler, so the call can be chained.
    """

    if not any(
        isinstance(entry, RedactingFilter) for entry in handler.filters
    ):
        handler.addFilter(RedactingFilter())

    return handler


def getLogger(name : Optional[str] = None) -> logging.Logger:
    """
    Return a library logger with credential redaction attached.

    Use this everywhere in place of :func:`logging.getLogger`. Names are
    normalised under the ``algotraders`` root, so passing ``__name__``
    from inside the package and passing a bare ``"fyers.api"`` both land
    somewhere sensible.

    :type  name: Optional[str]
    :param name: Logger name. ``None`` returns the library root.

    **Example Usage**

    The usual module level pattern.

    .. code-block:: python

        from algotraders.logger import getLogger

        logger = getLogger(__name__)
        logger.debug("history response: %s", response)

    :rtype:   logging.Logger
    :returns: A logger carrying exactly one :class:`RedactingFilter`.
    """

    root = logging.getLogger(ROOT_LOGGER_NAME)

    # ? a NullHandler keeps the "no handlers could be found" warning away
    # without imposing any handler policy on the host application
    if not any(
        isinstance(handler, logging.NullHandler) for handler in root.handlers
    ):
        root.addHandler(logging.NullHandler())

    if not name or name == ROOT_LOGGER_NAME:
        logger = root
    elif name.startswith(f"{ROOT_LOGGER_NAME}."):
        logger = logging.getLogger(name)
    else:
        logger = logging.getLogger(f"{ROOT_LOGGER_NAME}.{name}")

    # ! attach per logger, a filter on the parent never sees records that
    # merely propagate up from a child
    if not any(
        isinstance(entry, RedactingFilter) for entry in logger.filters
    ):
        logger.addFilter(RedactingFilter())

    return logger


def describeSecret(name : str, value : Any, visible : int = 4) -> str:
    """
    Format a ``name=masked`` pair for use inside a ``__repr__``.

    :type  name: str
    :param name: Attribute name to show.

    :type  value: Any
    :param value: The secret. A pydantic ``SecretStr`` is unwrapped
        before masking, so the mask reflects the real value rather than
        the placeholder ``SecretStr`` already prints.

    :rtype:   str
    :returns: A string such as ``clientId=********-100``.
    """

    if hasattr(value, "get_secret_value"):
        value = value.get_secret_value()

    return f"{name}={maskSecret(value, visible = visible)}"
