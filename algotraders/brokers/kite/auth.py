# -*- encoding: utf-8 -*-

"""
Zerodha Kite Connect v3 Authentication
----------------------------------------

Kite uses a redirect flow: the user visits a login URL, authenticates,
and Zerodha calls the registered redirect URI with a ``request_token``
which is exchanged for an access token.

**The fact that shapes every deployment**

A standard Kite Connect application has no usable refresh flow, and the
access token expires at 06:00 IST the next day as a regulatory
requirement. There is therefore no unattended path: somebody has to log
in through a browser every trading morning. That is a Zerodha
constraint, not a limitation of this library, and pretending otherwise
would produce a system that silently stops working at dawn.

:meth:`KiteAuthentication.refresh` is deliberately left unimplemented so
``initialize`` raises
:class:`~algotraders.errors.AuthorizationRequired` with a login URL,
which the host turns into a redirect.

:NOTE: The session exchange checksum is ``sha256(apiKey + requestToken +
    apiSecret)``, and it is never computed here. ``generate_session``
    does it inside the SDK, which is the only way to be sure a future
    change to the scheme does not silently break authentication.
"""

import asyncio

from typing import Any, Dict, Optional

from kiteconnect import KiteConnect

from algotraders.brokers.base import BaseBrokerAuthentication
from algotraders.brokers.kite import limits, mapper
from algotraders.config import BaseBrokerCredentials
from algotraders.core.clock import BaseClock
from algotraders.core.ratelimit import RateLimitPolicy
from algotraders.errors import InvalidCredentials, TokenExpiredError
from algotraders.logger import getLogger
from algotraders.models.auth import LoginChallenge, TokenBundle

logger = getLogger(__name__)


class KiteAuthentication(BaseBrokerAuthentication):
    """
    Concrete authentication for Zerodha Kite Connect v3.

    :type  credentials: BaseBrokerCredentials
    :param credentials: Kite application credentials. ``clientId`` is
        the API key and ``secretKey`` the API secret.

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
    def apiKey(self) -> str:
        """
        The Kite API key, which doubles as the application identifier.
        """

        return self.credentials.clientId.get_secret_value()


    def beginLogin(self, state : Optional[str] = None) -> LoginChallenge:
        """
        Produce the Kite login URL for the end user's browser.

        Built by the SDK rather than formatted here, so a change to the
        URL scheme arrives with an SDK upgrade instead of silently
        producing a broken redirect.

        :rtype:   LoginChallenge
        :returns: The login URL and echoed state.
        """

        client = KiteConnect(api_key = self.apiKey)

        return LoginChallenge(
            brokerName = limits.BROKER_NAME,
            loginURL = client.login_url(),
            state = state,
            redirectURL = self.credentials.redirectURL
        )


    async def completeLogin(
        self, authCode : str, state : Optional[str] = None
    ) -> TokenBundle:
        """
        Exchange a ``request_token`` for an access token.

        :type  authCode: str
        :param authCode: The ``request_token`` Zerodha appended to the
            redirect URI. Valid for minutes only and single use.

        :raises InvalidCredentials: Zerodha refused the exchange, which
            in practice means an expired or already used request token,
            or a mismatched API secret.

        :rtype:   TokenBundle
        :returns: The new session, expiring at the next 06:00 IST.
        """

        def exchange() -> Dict[str, Any]:
            client = KiteConnect(api_key = self.apiKey)

            return client.generate_session(
                authCode, api_secret = self.credentials.secretKey.
                get_secret_value()
            )

        try:
            # ? once per day at session start, so the default executor is
            # acceptable; the rate limited caller does not exist yet
            response = await asyncio.to_thread(exchange)
        except Exception as error:
            translated = mapper.translateException(error, "kite login")

            if isinstance(translated, TokenExpiredError):
                raise InvalidCredentials(
                    f"kite rejected the request token: {error}",
                    brokerName = limits.BROKER_NAME
                ) from error

            raise translated from error

        if not isinstance(response, dict) or not response.get("access_token"):
            raise InvalidCredentials(
                "kite returned no access token",
                brokerName = limits.BROKER_NAME
            )

        return TokenBundle.build(
            brokerName = limits.BROKER_NAME,
            clientId = self.apiKey,
            accessToken = response["access_token"],
            issuedAt = self.clock.now(),
            policy = self.tokenPolicy,
            metadata = {
                "userId" : str(response.get("user_id") or ""),
                "publicToken" : ""
            }
        )


    async def refresh(self, bundle : TokenBundle) -> TokenBundle:
        """
        Kite offers no unattended renewal for a standard application.

        :raises TokenExpiredError: Always. The caller falls back to an
            interactive login, which is the only path Zerodha provides.
        """

        raise TokenExpiredError(
            "kite access tokens expire at 06:00 IST and a standard "
            "application cannot refresh them; an interactive login is "
            "required each trading day"
        )


    async def logout(self, bundle : TokenBundle) -> bool:
        """
        Invalidate the access token at Zerodha.

        Kite does expose revocation, unlike Fyers.

        :rtype:   bool
        :returns: Whether the broker confirmed revocation.
        """

        def revoke() -> Any:
            client = KiteConnect(api_key = self.apiKey)
            client.set_access_token(bundle.accessToken.get_secret_value())

            return client.invalidate_access_token()

        try:
            await asyncio.to_thread(revoke)

            return True
        except Exception as error:
            logger.warning(
                "kite token revocation failed: %s", type(error).__name__
            )

            return False


    def buildSessionManager(self, bundle : TokenBundle) -> Any:
        """
        Construct the ``KiteConnect`` client every API call goes
        through.

        :rtype:   Any
        :returns: The authenticated vendor client.
        """

        client = KiteConnect(api_key = self.apiKey)
        client.set_access_token(bundle.accessToken.get_secret_value())

        return client


    def rateLimitPolicy(self, clock : BaseClock) -> RateLimitPolicy:
        """
        Build the Kite rate limit configuration.

        Declared as per endpoint limits rather than shared ones, since
        Zerodha publishes a genuine limit per class: one quote per
        second, three historical, ten orders.

        :rtype:   RateLimitPolicy
        :returns: The policy, with independent buckets per class.
        """

        return RateLimitPolicy(
            clock = clock,
            endpointLimits = limits.ENDPOINT_LIMITS,
            dailyQuota = limits.DAILY_QUOTA
        )


__all__ = ["KiteAuthentication"]
