# -*- encoding: utf-8 -*-

"""
FYERS Securities Private Limited Authentication
-------------------------------------------------

Fyers uses an OAuth authorization code flow. The user authenticates in a
browser, the broker redirects to the registered URI with a code, and
that code is exchanged for an access token plus a refresh token.

..versionchanged:: 2026-08-06 The Interactive Prompt Is Gone
    The previous implementation called ``webbrowser.open()`` and then
    blocked on ``input()`` waiting for the code to be pasted into a
    console. A FastAPI worker has neither a browser nor a console, so
    that path could never run server side. :meth:`beginLogin` now
    returns the URL and stops; the host redirects, receives the code on
    its own callback route, and passes it to :meth:`completeLogin`.

**Regulatory Change(s)**

Per SEBI, an access token is valid for a single trading day. Fyers
additionally issues a refresh token valid for 15 days which, combined
with the account's trading PIN, mints a new access token without user
interaction. That refresh path is what allows an unattended daily
ingestion job; without it a human would have to log in every morning.

:NOTE: ``appIdHash`` is ``sha256(f"{clientId}:{secretKey}")``, colon
    separated. Concatenating without the colon produces a 401 that looks
    exactly like bad credentials and has cost people hours. The initial
    token exchange goes through the SDK precisely so this is not hand
    rolled; only the refresh endpoint, which the SDK does not wrap,
    computes it here.
"""

import json
import asyncio
import hashlib
import urllib.error
import urllib.request

from typing import Any, Dict, Optional

from fyers_apiv3 import fyersModel

from algotraders.brokers.base import BaseBrokerAuthentication
from algotraders.brokers.fyers import limits
from algotraders.config import (
    BaseBrokerCredentials, configureVendorLogging
)
from algotraders.core.clock import BaseClock
from algotraders.core.ratelimit import RateLimitPolicy
from algotraders.errors import InvalidCredentials, TokenExpiredError
from algotraders.logger import getLogger
from algotraders.models.auth import LoginChallenge, TokenBundle

logger = getLogger(__name__)

REFRESH_TIMEOUT_SECONDS = 30


def appIdHash(clientId : str, secretKey : str) -> str:
    """
    Compute the Fyers application hash used by the refresh endpoint.

    :type  clientId: str
    :param clientId: Application client id.

    :type  secretKey: str
    :param secretKey: Application secret.

    :rtype:   str
    :returns: Hex SHA-256 of ``clientId:secretKey``, colon separated.
    """

    return hashlib.sha256(
        f"{clientId}:{secretKey}".encode("utf-8")
    ).hexdigest()


class FyersAuthentication(BaseBrokerAuthentication):
    """
    Implementation of concrete method for any security services that
    is required for FYERS API services - login, refresh and session
    construction.

    :type  credentials: BaseBrokerCredentials
    :param credentials: Fyers application credentials. ``clientId`` is
        the app id ending in ``-100``, ``secretKey`` is the app secret
        and not the DEMAT account password, and ``pin`` is the trading
        PIN needed only by the refresh flow.

    :type  clock: BaseClock
    :param clock: Injected time source, used for token expiry.
    """

    tokenPolicy = limits.TOKEN_POLICY
    capabilities = limits.CAPABILITIES

    def __init__(
        self, credentials : BaseBrokerCredentials,
        clock : Optional[BaseClock] = None, **extra : Any
    ) -> None:
        super().__init__(credentials = credentials, clock = clock)


    @property
    def redirectURL(self) -> str:
        """
        The API redirection URL, which is defined when creating a new
        app in the Fyers dashboard.

        Read from configuration rather than hard coded, because it must
        match the app registration byte for byte and a hosted
        application registers its own callback route.
        """

        return self.credentials.redirectURL or limits.DEFAULT_REDIRECT_URL


    def __sessionModel__(self, **extra : Any) -> Any:
        """
        Build the SDK session object used for token operations.
        """

        return fyersModel.SessionModel(
            client_id = self.credentials.clientId.get_secret_value(),
            secret_key = self.credentials.secretKey.get_secret_value(),
            redirect_uri = self.redirectURL,
            response_type = "code",
            grant_type = "authorization_code",
            **extra
        )


    def beginLogin(self, state : Optional[str] = None) -> LoginChallenge:
        """
        Produce the Fyers login URL for the end user's browser.

        Performs no I/O and never opens a browser. Handing the URL back
        is what lets a web backend drive the flow.

        :rtype:   LoginChallenge
        :returns: The login URL, the echoed state and the redirect URI
            the broker will call.
        """

        session = self.__sessionModel__(state = state) if state else (
            self.__sessionModel__()
        )

        return LoginChallenge(
            brokerName = limits.BROKER_NAME,
            loginURL = session.generate_authcode(),
            state = state,
            redirectURL = self.redirectURL
        )


    async def completeLogin(
        self, authCode : str, state : Optional[str] = None
    ) -> TokenBundle:
        """
        Exchange the authorization code for a session token.

        :type  authCode: str
        :param authCode: Code the broker returned to the host's redirect
            URI. Short lived and single use.

        :raises InvalidCredentials: Fyers rejected the exchange, which
            in practice means a stale code, a redirect URI that does not
            match the registration, or a wrong secret.

        :rtype:   TokenBundle
        :returns: The new session, with expiry derived from the token
            policy.
        """

        def exchange() -> Dict[str, Any]:
            session = self.__sessionModel__()
            session.set_token(authCode)

            return session.generate_token()

        # ? one call per day at session start, so the default executor is
        # acceptable here; the rate limited caller does not exist yet
        response = await asyncio.to_thread(exchange)

        return self.__bundleFrom__(response, context = "token exchange")


    async def refresh(self, bundle : TokenBundle) -> TokenBundle:
        """
        Mint a new access token from the stored refresh token.

        This is the call that makes unattended daily operation possible.
        It needs the trading PIN, so a deployment without
        ``ALGOTRADERS_FYERS_PIN`` set will fall back to an interactive
        login every morning.

        :raises TokenExpiredError: No refresh token is held, or the
            broker refused to renew.

        :raises InvalidCredentials: The trading PIN is not configured.

        :rtype:   TokenBundle
        :returns: The renewed session.
        """

        if bundle.refreshToken is None:
            raise TokenExpiredError(
                "no fyers refresh token is stored; an interactive login "
                "is required"
            )

        if self.credentials.pin is None:
            raise InvalidCredentials(
                "refreshing a fyers session requires the trading PIN",
                brokerName = limits.BROKER_NAME,
                missing = ["ALGOTRADERS_FYERS_PIN"]
            )

        payload = {
            "grant_type" : "refresh_token",
            "appIdHash" : appIdHash(
                self.credentials.clientId.get_secret_value(),
                self.credentials.secretKey.get_secret_value()
            ),
            "refresh_token" : bundle.refreshToken.get_secret_value(),
            "pin" : self.credentials.pin.get_secret_value()
        }

        response = await asyncio.to_thread(self.__postRefresh__, payload)

        renewed = self.__bundleFrom__(response, context = "token refresh")

        # ? Fyers does not reissue the refresh token on renewal, so the
        # original is carried forward with its own original lifetime
        return renewed.model_copy(update = {
            "refreshToken" : bundle.refreshToken,
            "refreshExpiresAt" : bundle.refreshExpiresAt
        })


    def __postRefresh__(self, payload : Dict[str, Any]) -> Dict[str, Any]:
        """
        POST the refresh request, which the SDK does not wrap.

        Uses :mod:`urllib` rather than adding an HTTP client to the core
        dependencies for a single call made once a day.
        """

        request = urllib.request.Request(
            limits.REFRESH_TOKEN_URL,
            data = json.dumps(payload).encode("utf-8"),
            headers = {"Content-Type" : "application/json"},
            method = "POST"
        )

        try:
            with urllib.request.urlopen(
                request, timeout = REFRESH_TIMEOUT_SECONDS
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise TokenExpiredError(
                f"fyers refused the refresh request ({error.code})"
            ) from error
        except (urllib.error.URLError, ValueError) as error:
            raise TokenExpiredError(
                f"fyers refresh request failed: {type(error).__name__}"
            ) from error


    def __bundleFrom__(
        self, response : Dict[str, Any], context : str
    ) -> TokenBundle:
        """
        Build a token bundle from a Fyers token response.

        :raises InvalidCredentials: The response carried no access
            token, which is how the broker signals a rejected exchange.
        """

        if not isinstance(response, dict) or not response.get("access_token"):
            message = (
                response.get("message") if isinstance(response, dict)
                else str(response)
            )

            raise InvalidCredentials(
                f"fyers {context} failed: {message}",
                brokerName = limits.BROKER_NAME
            )

        return TokenBundle.build(
            brokerName = limits.BROKER_NAME,
            clientId = self.credentials.clientId.get_secret_value(),
            accessToken = response["access_token"],
            refreshToken = response.get("refresh_token"),
            issuedAt = self.clock.now(),
            policy = self.tokenPolicy
        )


    async def logout(self, bundle : TokenBundle) -> bool:
        """
        Fyers exposes no token revocation endpoint, so the local token
        is simply discarded by the caller.

        :rtype:   bool
        :returns: Always ``False``, since nothing was revoked broker
            side and claiming otherwise would be misleading.
        """

        logger.debug("fyers has no revocation endpoint, discarding locally")

        return False


    def buildSessionManager(self, bundle : TokenBundle) -> Any:
        """
        Construct the ``FyersModel`` client every API call goes through.

        :NOTE: ``log_path`` is always supplied. Left unset the SDK
            writes ``fyersApi.log`` into the process working directory,
            which is how a 570,646 line log file ended up inside
            ``examples/fyers/`` in this repository.

        :rtype:   Any
        :returns: The configured vendor client.
        """

        return fyersModel.FyersModel(
            client_id = self.credentials.clientId.get_secret_value(),
            token = bundle.accessToken.get_secret_value(),
            is_async = False,
            log_path = configureVendorLogging()
        )


    def rateLimitPolicy(self, clock : BaseClock) -> RateLimitPolicy:
        """
        Build the Fyers rate limit configuration.

        Every limit is passed as a **shared** limit, because Fyers
        publishes one application wide quota rather than a limit per
        endpoint class.

        :rtype:   RateLimitPolicy
        :returns: The policy, with one set of buckets contended by every
            endpoint class.
        """

        return RateLimitPolicy(
            clock = clock,
            sharedLimits = limits.SHARED_LIMITS,
            dailyQuota = limits.DAILY_QUOTA,
            quotaName = limits.DAILY_QUOTA_NAME
        )


__all__ = ["FyersAuthentication", "appIdHash"]
