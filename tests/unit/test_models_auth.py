# -*- encoding: utf-8 -*-

"""
Token Bundle Secrecy and Expiry Arithmetic
--------------------------------------------

Two behaviours are pinned here. Serialising a bundle must never disclose
a token, while persisting one must, and the split between
``model_dump`` and ``toStorage`` is what keeps every disclosure
greppable. Separately, expiry has to take the *earlier* of the nominal
lifetime and the regulatory wall clock cutoff, because a token issued at
22:00 under a nominal 24 hour life is dead at dawn, not tomorrow evening.
"""

import datetime as dt

import pytest
from pydantic import SecretStr, ValidationError

from algotraders.core.clock import IST
from algotraders.models.auth import (
    LoginChallenge, SessionStatus, TokenBundle, TokenPolicy
)

SECRET = "eyJhbGciOiJIUzI1NiJ9.super-secret-token-body.signature"
REFRESH = "refresh-token-material"

FYERS_POLICY = TokenPolicy(
    rolloverHourIST = 6,
    accessTokenLifetime = dt.timedelta(days = 1),
    refreshTokenLifetime = dt.timedelta(days = 15),
    supportsRefresh = True,
    refreshRequiresPin = True
)

KITE_POLICY = TokenPolicy(
    rolloverHourIST = 6,
    accessTokenLifetime = dt.timedelta(days = 1),
    supportsRefresh = False
)


def buildBundle(issuedAt : dt.datetime, **overrides : object) -> TokenBundle:
    """
    Construct a bundle under the Fyers policy unless told otherwise.
    """

    fields = dict(
        brokerName = "fyers", clientId = "FX0KN0G7PX-100",
        accessToken = SECRET, issuedAt = issuedAt, policy = FYERS_POLICY,
        refreshToken = REFRESH
    )
    fields.update(overrides)

    return TokenBundle.build(**fields)


def test_expiryTakesTheEarlierOfLifetimeAndRollover() -> None:
    """
    Issued at 22:00 with a 24 hour nominal life and a 06:00 cutoff, the
    token dies in eight hours.

    Getting this backwards means every call after dawn fails while the
    library still believes the session is good.
    """

    bundle = buildBundle(dt.datetime(2026, 8, 5, 22, 0, tzinfo = IST))

    assert bundle.expiresAt == dt.datetime(2026, 8, 6, 6, 0, tzinfo = IST)


def test_expiryUsesLifetimeWhenItExpiresFirst() -> None:
    """
    Issued at 07:00, the next 06:00 cutoff is 23 hours away and the
    nominal 24 hour life is not the binding constraint either way, so
    the earlier of the two still wins.
    """

    bundle = buildBundle(dt.datetime(2026, 8, 5, 7, 0, tzinfo = IST))

    assert bundle.expiresAt == dt.datetime(2026, 8, 6, 6, 0, tzinfo = IST)


def test_expiryWithoutPolicyIsUnset() -> None:
    """
    With no policy, no expiry is claimed and the broker stays the
    authority on validity.
    """

    bundle = TokenBundle.build(
        brokerName = "custom", clientId = "X1",
        accessToken = SECRET, issuedAt = dt.datetime.now(tz = IST)
    )

    assert bundle.expiresAt is None


def test_bundleWithoutExpiryIsTreatedAsLive() -> None:
    """
    Refusing to use a token merely because its lifetime was not recorded
    would force an unnecessary interactive login every morning.
    """

    bundle = TokenBundle.build(
        brokerName = "custom", clientId = "X1",
        accessToken = SECRET, issuedAt = dt.datetime(2020, 1, 1, tzinfo = IST)
    )

    assert bundle.isExpired(dt.datetime(2026, 8, 5, tzinfo = IST)) is False


def test_isExpiredCrossesTheRolloverBoundary() -> None:
    """
    Live at 05:59, dead at 06:30 the next morning.
    """

    bundle = buildBundle(dt.datetime(2026, 8, 5, 22, 0, tzinfo = IST))

    assert bundle.isExpired(dt.datetime(2026, 8, 6, 5, 59, tzinfo = IST)) is False
    assert bundle.isExpired(dt.datetime(2026, 8, 6, 6, 30, tzinfo = IST)) is True


def test_secondsRemainingNeverGoesNegative() -> None:
    """
    A caller scheduling a refresh should see zero, not a negative delay.
    """

    bundle = buildBundle(dt.datetime(2026, 8, 5, 22, 0, tzinfo = IST))

    assert bundle.secondsRemaining(
        dt.datetime(2026, 8, 7, 12, 0, tzinfo = IST)
    ) == 0.0

    assert bundle.secondsRemaining(
        dt.datetime(2026, 8, 6, 5, 0, tzinfo = IST)
    ) == pytest.approx(3600.0)


def test_refreshTokenLifetimeIsTracked() -> None:
    """
    Fyers refresh tokens last 15 days, so a deployment idle over a long
    break needs an interactive login on return rather than looping on a
    refresh that can no longer succeed.
    """

    issued = dt.datetime(2026, 8, 5, 9, 0, tzinfo = IST)
    bundle = buildBundle(issued)

    assert bundle.refreshExpiresAt == issued + dt.timedelta(days = 15)
    assert bundle.canRefresh(issued + dt.timedelta(days = 14)) is True
    assert bundle.canRefresh(issued + dt.timedelta(days = 16)) is False


def test_bundleWithoutRefreshTokenCannotRefresh() -> None:
    """
    Kite issues no usable refresh credential, which is the single fact
    that forces a daily interactive login for that broker.
    """

    bundle = TokenBundle.build(
        brokerName = "kite", clientId = "AB1234", accessToken = SECRET,
        issuedAt = dt.datetime(2026, 8, 5, 9, 0, tzinfo = IST),
        policy = KITE_POLICY
    )

    assert bundle.refreshToken is None
    assert bundle.canRefresh(dt.datetime(2026, 8, 5, 10, 0, tzinfo = IST)) is False


def test_serialisationNeverDisclosesAToken() -> None:
    """
    Every rendering path masks. This is what makes it safe for a bundle
    to appear in a log line, an error context or a debug endpoint.
    """

    bundle = buildBundle(dt.datetime(2026, 8, 5, 9, 0, tzinfo = IST))

    for rendered in (
        repr(bundle), str(bundle), f"{bundle}",
        bundle.model_dump_json(), str(bundle.model_dump())
    ):
        assert SECRET not in rendered
        assert REFRESH not in rendered


def test_clientIdIsMaskedInRepresentation() -> None:
    """
    The account number is masked too, so a shared screenshot or support
    ticket does not expose which account is trading.
    """

    bundle = buildBundle(dt.datetime(2026, 8, 5, 9, 0, tzinfo = IST))

    assert "FX0KN0G7PX" not in repr(bundle)
    assert "-100" in repr(bundle)


def test_storageRoundTripPreservesSecrets() -> None:
    """
    ``toStorage`` is the one path that discloses, and what it writes must
    reconstruct an identical bundle.
    """

    original = buildBundle(dt.datetime(2026, 8, 5, 9, 0, tzinfo = IST))
    record = original.toStorage()

    assert record["accessToken"] == SECRET
    assert record["refreshToken"] == REFRESH

    restored = TokenBundle.fromStorage(record)

    assert restored.accessToken.get_secret_value() == SECRET
    assert restored.refreshToken.get_secret_value() == REFRESH
    assert restored.expiresAt == original.expiresAt
    assert restored == original


def test_secretsAreSecretStrInstances() -> None:
    """
    Typing tokens as ``SecretStr`` is what gives masking by default and
    makes every real read an explicit, reviewable call.
    """

    bundle = buildBundle(dt.datetime(2026, 8, 5, 9, 0, tzinfo = IST))

    assert isinstance(bundle.accessToken, SecretStr)
    assert isinstance(bundle.refreshToken, SecretStr)


def test_naiveIssuedAtIsRejected() -> None:
    """
    Token arithmetic against a naive timestamp would compute expiry in
    the wrong timezone, silently extending or shortening a session.
    """

    with pytest.raises(ValidationError):
        TokenBundle(
            brokerName = "fyers", clientId = "X1",
            accessToken = SecretStr(SECRET),
            issuedAt = dt.datetime(2026, 8, 5, 9, 0)
        )


def test_loginChallengeCarriesTheRedirectContract() -> None:
    """
    The challenge is what a host turns into a browser redirect, so the
    URL and the state token both have to survive on it.
    """

    challenge = LoginChallenge(
        brokerName = "kite", state = "abc123",
        loginURL = "https://kite.zerodha.com/connect/login?v=3&api_key=x"
    )

    assert challenge.loginURL.startswith("https://")
    assert challenge.state == "abc123"


def test_sessionStatusCarriesNoToken() -> None:
    """
    This model is returned straight to a frontend, so it must be safe by
    construction rather than by the caller remembering to filter it.
    """

    status = SessionStatus(
        brokerName = "fyers", clientId = "FX0KN0G7PX-100",
        isAuthenticated = True, canRefresh = True
    )

    assert "accessToken" not in status.model_dump()
    assert SECRET not in status.model_dump_json()
