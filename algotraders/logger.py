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

from typing import Any, FrozenSet, Optional

ROOT_LOGGER_NAME = "algotraders"

MASK = "*" * 8

# ? every key name either SDK, either broker or this library uses for a
# credential. Matching is case insensitive, so one spelling per concept
# is enough, but both snake_case and camelCase appear in vendor payloads
REDACTION_KEYS : FrozenSet[str] = frozenset({
    "access_token", "accessToken",
    "refresh_token", "refreshToken",
    "auth_code", "authCode", "authorization_code", "authorizationCode",
    "request_token", "requestToken",
    "secret_key", "secretKey",
    "api_secret", "apiSecret",
    "app_id_hash", "appIdHash",
    "checksum", "password", "pin", "token", "enctoken",
    "public_token", "publicToken",
    "Authorization"
})

# ? matches key=value, key: value, "key": "value" and key = value across
# JSON, query strings and repr output, capturing the value to replace
_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(['\"]?\b(?:" + "|".join(
        sorted((re.escape(key) for key in REDACTION_KEYS), key = len,
               reverse = True)
    ) + r")\b['\"]?\s*[:=]\s*)(['\"]?)([^\s,;&'\"}\])]+)"
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

    redacted = _ASSIGNMENT_PATTERN.sub(replaceAssignment, text)

    return _JWT_PATTERN.sub(MASK, redacted)


class RedactingFilter(logging.Filter):
    """
    A logging filter that masks credentials in the rendered message.

    The filter renders the record, redacts, and writes the result back
    as a literal message with the arguments cleared. Rendering eagerly
    costs the lazy-formatting optimisation, but only for records that
    actually contained something secret, and a leaked token is worth far
    more than the microseconds saved.
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

        return True


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
