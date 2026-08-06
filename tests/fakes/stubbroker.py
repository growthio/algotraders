# -*- encoding: utf-8 -*-

"""
A Minimal Broker Standing In for a Real Implementation
--------------------------------------------------------

``initialize`` resolves a broker through the registry, which imports a
module by name and pulls callables off it. Testing that path therefore
needs a real, importable module rather than a mock, and this is it.

The stub records what was called on it so a test can assert the exact
resolution path taken: whether the stored token was reused, refreshed,
or exchanged from an authorization code. Those three branches are the
whole point of the entry point, and telling them apart from the outside
is otherwise guesswork.
"""

import datetime as dt

from typing import Any, FrozenSet, List, Optional

from algotraders.config import BaseBrokerCredentials
from algotraders.core.clock import BaseClock, SystemClock
from algotraders.core.ratelimit import RateLimit, RateLimitPolicy
from algotraders.models.auth import LoginChallenge, TokenBundle, TokenPolicy
from algotraders.models.enums import Capability

STUB_LOGIN_URL = "https://stub.example.com/connect/login?api_key=stub"

STUB_POLICY = TokenPolicy(
    rolloverHourIST = 6,
    accessTokenLifetime = dt.timedelta(days = 1),
    refreshTokenLifetime = dt.timedelta(days = 15),
    supportsRefresh = True,
    refreshRequiresPin = False
)

STUB_CAPABILITIES : FrozenSet[Capability] = frozenset({
    Capability.HISTORICAL_DATA, Capability.LIVE_QUOTES, Capability.TRADING
})


class StubSessionManager:
    """
    Stands in for a vendor SDK client object.
    """

    def __init__(self, token : str) -> None:
        self.token = token
        self.calls : List[str] = []


    def history(self, data : Any = None) -> dict:
        self.calls.append("history")

        return {"s" : "ok", "candles" : []}


class StubAuthentication:
    """
    Authentication object implementing the contract ``initialize``
    expects, with every interaction recorded.

    :type  credentials: BaseBrokerCredentials
    :param credentials: Resolved application credentials.

    :type  clock: BaseClock
    :param clock: Injected time source.
    """

    supportsRefresh = True
    capabilities = STUB_CAPABILITIES

    def __init__(
        self, credentials : BaseBrokerCredentials,
        clock : Optional[BaseClock] = None
    ) -> None:
        self.credentials = credentials
        self.clock = clock or SystemClock()

        self.beginLoginCalls = 0
        self.completeLoginCalls = 0
        self.refreshCalls = 0
        self.logoutCalls = 0
        self.builtSessionManagers : List[StubSessionManager] = []


    def beginLogin(
        self, state : Optional[str] = None
    ) -> LoginChallenge:
        """
        Produce the challenge a host turns into a browser redirect.
        """

        self.beginLoginCalls += 1

        return LoginChallenge(
            brokerName = "stub", loginURL = STUB_LOGIN_URL, state = state
        )


    async def completeLogin(
        self, authCode : str, state : Optional[str] = None
    ) -> TokenBundle:
        """
        Exchange an authorization code for a fresh token bundle.
        """

        self.completeLoginCalls += 1

        return self.__bundle__(f"access-from-{authCode}")


    async def refresh(self, bundle : TokenBundle) -> TokenBundle:
        """
        Mint a new bundle from an existing refresh token.
        """

        self.refreshCalls += 1

        return self.__bundle__("access-after-refresh")


    async def logout(self, bundle : TokenBundle) -> bool:
        """
        Pretend to revoke the token at the broker.
        """

        self.logoutCalls += 1

        return True


    def buildSessionManager(self, bundle : TokenBundle) -> StubSessionManager:
        """
        Build the vendor client the API surface will call through.
        """

        manager = StubSessionManager(bundle.accessToken.get_secret_value())
        self.builtSessionManagers.append(manager)

        return manager


    def rateLimitPolicy(self, clock : BaseClock) -> RateLimitPolicy:
        """
        Supply a generous policy, since these tests are not about rate
        limiting.
        """

        return RateLimitPolicy(
            clock = clock,
            sharedLimits = [RateLimit("stub.perSecond", 1_000, 1.0)]
        )


    def __bundle__(self, accessToken : str) -> TokenBundle:
        """
        Build a bundle for this broker under the stub token policy.
        """

        return TokenBundle.build(
            brokerName = "stub",
            clientId = self.credentials.clientId.get_secret_value(),
            accessToken = accessToken,
            refreshToken = "stub-refresh-token",
            issuedAt = self.clock.now(),
            policy = STUB_POLICY
        )


class StubAPI:
    """
    Placeholder data surface, constructed by ``initialize`` when the
    registry entry names one.
    """

    def __init__(self, **kwargs : Any) -> None:
        self.kwargs = kwargs
        self.sessionManager = kwargs.get("sessionManager")
        self.caller = kwargs.get("caller")
