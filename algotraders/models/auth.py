# -*- encoding: utf-8 -*-

"""
Session Token and Login Challenge Models
------------------------------------------

:class:`TokenBundle` holds live bearer credentials, so it is built
around one rule: **serialising a bundle must never disclose a token, and
persisting one must.** Those are contradictory requirements, so they are
given separate methods rather than one configurable behaviour.

``model_dump()`` and ``model_dump_json()`` mask every secret, which is
what makes it safe for a bundle to appear in a log line, an error
context or a debug endpoint. Writing to a token store instead goes
through :meth:`TokenBundle.toStorage`, whose name makes the disclosure
explicit at every call site, and every such call site is reviewable by
grep.

Expiry is computed rather than trusted. A broker's stated lifetime and
its regulatory cutoff are different constraints and the earlier one
wins: a Fyers token issued at 22:00 with a nominal 24 hour life is dead
at the SEBI daily rollover the next morning, not at 22:00 tomorrow.
"""

import datetime as dt

from typing import Any, Dict, Optional

from pydantic import Field, SecretStr, field_validator

from algotraders.core.clock import IST, toIST
from algotraders.models.base import AlgoTradersModel
from algotraders.models.market import requireAwareIST


class TokenPolicy(AlgoTradersModel):
    """
    How a particular broker's session tokens age.

    Declared per broker in its ``limits.py`` so the rules sit next to
    the rate limits rather than being scattered through the auth code.

    :type  rolloverHourIST: Optional[int]
    :param rolloverHourIST: Wall clock hour in IST at which every token
        dies regardless of when it was issued. Kite uses 06:00 as a
        regulatory requirement. ``None`` means no wall clock cutoff.

    :type  accessTokenLifetime: Optional[dt.timedelta]
    :param accessTokenLifetime: Nominal validity from issue.

    :type  supportsRefresh: bool
    :param supportsRefresh: Whether an expired access token can be
        renewed without an interactive login. Fyers can, Kite cannot,
        and that single difference decides whether a deployment needs a
        human every morning.

    :type  refreshRequiresPin: bool
    :param refreshRequiresPin: Whether the refresh call needs the
        trading PIN as well as the refresh token.
    """

    rolloverHourIST : Optional[int] = Field(default = None, ge = 0, le = 23)
    accessTokenLifetime : Optional[dt.timedelta] = None
    refreshTokenLifetime : Optional[dt.timedelta] = None
    supportsRefresh : bool = False
    refreshRequiresPin : bool = False


class TokenBundle(AlgoTradersModel):
    """
    A broker session's credentials and their computed lifetime.

    :type  brokerName: str
    :param brokerName: Broker the session belongs to.

    :type  clientId: str
    :param clientId: Account the session authenticates. Stored in clear
        because the token store keys on it and it is not itself a
        credential, though it is masked in ``__repr__``.

    :type  accessToken: SecretStr
    :param accessToken: The bearer token. Anyone holding this can trade
        the account until it expires.

    :type  refreshToken: Optional[SecretStr]
    :param refreshToken: Longer lived renewal credential, when the
        broker issues one.

    :type  issuedAt: dt.datetime
    :param issuedAt: When the token was minted, timezone aware.

    :type  expiresAt: Optional[dt.datetime]
    :param expiresAt: When it stops working. A *hint*, not an authority:
        brokers have moved their cutoffs before, so the vendor's own
        rejection is what ultimately invalidates a bundle.
    """

    brokerName : str = Field(min_length = 1)
    clientId : str = Field(min_length = 1)
    accessToken : SecretStr
    refreshToken : Optional[SecretStr] = None
    issuedAt : dt.datetime
    expiresAt : Optional[dt.datetime] = None
    refreshExpiresAt : Optional[dt.datetime] = None
    scope : Optional[str] = None
    metadata : Dict[str, Any] = Field(default_factory = dict)

    _normaliseIssuedAt = field_validator("issuedAt")(requireAwareIST)
    _normaliseExpiresAt = field_validator("expiresAt")(requireAwareIST)
    _normaliseRefreshExpiry = field_validator("refreshExpiresAt")(
        requireAwareIST
    )


    def isExpired(self, now : dt.datetime) -> bool:
        """
        Whether the access token should be treated as dead.

        A bundle with no recorded expiry is treated as **live**. The
        broker is the authority, and refusing to use a token merely
        because its lifetime was not recorded would force an
        unnecessary interactive login every morning.

        :type  now: dt.datetime
        :param now: Current time, supplied by the caller's clock rather
            than read from the system, so expiry is testable.

        :rtype:   bool
        :returns: ``True`` when the token has passed its expiry.
        """

        if self.expiresAt is None:
            return False

        return toIST(now) >= self.expiresAt


    def canRefresh(self, now : dt.datetime) -> bool:
        """
        Whether an unattended refresh is still possible.

        Requires a refresh token that has not itself expired. Fyers
        refresh tokens last 15 days, so a deployment left idle over a
        long break needs an interactive login on return.
        """

        if self.refreshToken is None:
            return False

        if self.refreshExpiresAt is None:
            return True

        return toIST(now) < self.refreshExpiresAt


    def secondsRemaining(self, now : dt.datetime) -> Optional[float]:
        """
        Time left before expiry, in seconds.

        :rtype:   Optional[float]
        :returns: Remaining seconds, never negative, or ``None`` when no
            expiry was recorded.
        """

        if self.expiresAt is None:
            return None

        return max(0.0, (self.expiresAt - toIST(now)).total_seconds())


    @classmethod
    def build(
        cls, brokerName : str, clientId : str, accessToken : str,
        issuedAt : dt.datetime, policy : Optional[TokenPolicy] = None,
        refreshToken : Optional[str] = None, **extra : Any
    ) -> "TokenBundle":
        """
        Construct a bundle, computing expiry from the broker's policy.

        Expiry is the **earlier** of the nominal lifetime and the next
        wall clock rollover. That ordering is the whole reason this
        helper exists: a token issued at 22:00 IST under a 24 hour
        nominal life and a 06:00 cutoff expires in eight hours, not
        twenty four, and treating it as valid until the next evening
        means every call after dawn fails.

        :type  policy: Optional[TokenPolicy]
        :param policy: The broker's ageing rules. Without one, no expiry
            is recorded and the bundle relies on the broker to reject it.

        **Example Usage**

        .. code-block:: python

            policy = TokenPolicy(
                rolloverHourIST = 6,
                accessTokenLifetime = dt.timedelta(days = 1)
            )

            bundle = TokenBundle.build(
                brokerName = "kite", clientId = "AB1234",
                accessToken = token, issuedAt = clock.now(), policy = policy
            )

        :rtype:   TokenBundle
        :returns: The constructed bundle.
        """

        issued = toIST(issuedAt)
        candidates = []

        if policy is not None:
            if policy.accessTokenLifetime is not None:
                candidates.append(issued + policy.accessTokenLifetime)

            if policy.rolloverHourIST is not None:
                candidates.append(_nextRollover(issued, policy.rolloverHourIST))

        refreshExpiry = None

        if (
            policy is not None
            and refreshToken is not None
            and policy.refreshTokenLifetime is not None
        ):
            refreshExpiry = issued + policy.refreshTokenLifetime

        return cls(
            brokerName = brokerName,
            clientId = clientId,
            accessToken = SecretStr(accessToken),
            refreshToken = (
                SecretStr(refreshToken) if refreshToken is not None else None
            ),
            issuedAt = issued,
            expiresAt = min(candidates) if candidates else None,
            refreshExpiresAt = refreshExpiry,
            **extra
        )


    def toStorage(self) -> Dict[str, Any]:
        """
        Render the bundle with secrets in clear, for persistence only.

        **This discloses live credentials.** It is a separate, explicitly
        named method rather than a flag on ``model_dump`` so that every
        disclosure is greppable and obvious in review. Call it only when
        writing to a token store, and never when logging, returning an
        API response, or formatting an error.

        :rtype:   Dict[str, Any]
        :returns: A JSON serialisable mapping including the raw tokens.
        """

        record = self.model_dump(mode = "json")

        record["accessToken"] = self.accessToken.get_secret_value()
        record["refreshToken"] = (
            self.refreshToken.get_secret_value()
            if self.refreshToken is not None else None
        )

        return record


    @classmethod
    def fromStorage(cls, record : Dict[str, Any]) -> "TokenBundle":
        """
        Rebuild a bundle from :meth:`toStorage` output.

        :type  record: Dict[str, Any]
        :param record: Previously stored mapping.

        :rtype:   TokenBundle
        :returns: The reconstructed bundle.
        """

        return cls.model_validate(record)


    def __repr__(self) -> str:
        """
        Render without any token material.

        ``SecretStr`` already masks on its own; the client id is masked
        here as well so a shared log or screenshot does not expose the
        account number.
        """

        from algotraders.logger import maskSecret

        return (
            f"TokenBundle(brokerName={self.brokerName!r}, "
            f"clientId={maskSecret(self.clientId)}, "
            f"issuedAt={self.issuedAt.isoformat()}, "
            f"expiresAt="
            f"{self.expiresAt.isoformat() if self.expiresAt else None})"
        )


    __str__ = __repr__


class LoginChallenge(AlgoTradersModel):
    """
    Everything the caller needs to start an interactive broker login.

    Produced when no usable session exists. The host turns this into a
    redirect for the browser and hands the returned code back through
    ``initialize(authCode = ...)``.

    :type  loginURL: str
    :param loginURL: Broker hosted URL the end user must visit.

    :type  state: Optional[str]
    :param state: Opaque value echoed back on the callback, used to tie
        the response to the request that started it and to reject a
        callback the host never initiated.
    """

    brokerName : str = Field(min_length = 1)
    loginURL : str = Field(min_length = 1)
    state : Optional[str] = None
    redirectURL : Optional[str] = None


class SessionStatus(AlgoTradersModel):
    """
    Safe summary of a live session, suitable for an API response.

    Carries no token and a masked client id by construction, so it can
    be returned to a frontend without further filtering.
    """

    brokerName : str = Field(min_length = 1)
    clientId : str = Field(min_length = 1)
    isAuthenticated : bool = False
    expiresAt : Optional[dt.datetime] = None
    secondsRemaining : Optional[float] = None
    canRefresh : bool = False
    capabilities : list = Field(default_factory = list)


def _nextRollover(reference : dt.datetime, hour : int) -> dt.datetime:
    """
    Next occurrence of an IST wall clock hour, strictly after the
    reference moment.

    Duplicates :meth:`BaseClock.nextRollover` deliberately: ``models``
    may not import ``core`` beyond the timezone helpers, and a token
    bundle has to be constructible without a clock in hand.

    :rtype:   dt.datetime
    :returns: The next IST datetime at that hour.
    """

    current = toIST(reference)
    candidate = current.replace(
        hour = hour, minute = 0, second = 0, microsecond = 0
    )

    if candidate <= current:
        candidate += dt.timedelta(days = 1)

    return candidate.astimezone(IST)


__all__ = [
    "TokenBundle", "TokenPolicy", "LoginChallenge", "SessionStatus"
]
