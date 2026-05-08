# -*- encoding: utf-8 -*-

"""
FYERS Securities Private Limite Broker's API Integration
--------------------------------------------------------

Fyers API, released on late 2019, is the company's first major step
towards providing zero-cost trading APIs for retail traders. The
API can be used to fetch historical data, and execute buy/sell orders
of securities using a FYERS DEMAT account.

..versionchanged:: 2021-08-20 FYERS API v2 was Released:
    The version release was done before any implementation of v1 logic
    into our broker services. The details are documented as a process
    note, and no changes are yet made.

..versionchanged:: 2023-11-09 FYERS API v3 was Released:
    A stable and improved v3 version was released before implementation
    of the broker services. The details are documented as a process
    note, and this enforces more regulations as directed by SEBI.
"""

import webbrowser
import datetime as dt

from typing import Any, Callable, Optional, Iterable, List, Tuple

from fyers_apiv3 import fyersModel

from algotraders.brokers.base import (
    BaseBrokerAuthentication, BaseBrokerAPI
)

class FyersAuthentication(BaseBrokerAuthentication):
    """
    Implementation of concrete method for any security services that
    is required for FYERS API services - ``.login()`` operation.

    :type  username: str
    :param username: Username or Client ID which is available from
        from the Broker and is typically the Login ID.

    :type  password: str
    :param password: Password to login to access the Broker's API, this
        value may be different from the password you used to login to
        the "DEMAT" account and is sometime referred as the "secret
        key" which is provided when creating the API endpoint in the
        Broker's API Terminal.
    """

    def __init__(self, username : str, password : str) -> None:
        self.username = username
        self.password = password


    @property
    def redirectURL(self) -> str:
        """
        The API redirection URL, this value is defined when creating
        a new app, check `documentation <https://myapi.fyers.in>`_ for
        more information.
        """

        return "https://trade.fyers.in/api-login/redirect-uri/index.html"


    def login(self, accessToken: Optional[str] = None) -> Any:
        """
        Perform login operation to the Broker's API. A broker's API
        login may have different regulatory guidelines based on which
        the concrete function should be modeled. Typically, for an
        Indian Broker, SEBI is the regulatory body who provides the
        guidelines to connect using the following parameters.

        :type  accessToken: Optional[str]
        :param accessToken: As per SEBI guidelines, an access token is
            valid for one day. This means that any running services
            must be switched off and on the next day. However, if the
            API is closed then it can be restarted using the same
            access token for the day.

        **Regulatory Change(s)**

        As per the latest SEBI regulations, username is the application
        code with a secret key generated during creation of the app.
        In addition, it is now mandatory to use a two-factor authentication
        key which is implemented in the login and the authentication
        token expires on a daily basis.
        """

        client_id, secret_key = self.username, self.password

        def generateAccessToken() -> str:
            session = fyersModel.sessionModel(
                client_id = client_id, secret_key = secret_key,
                redirect_uri = self.redirectURL, response_type = "code",
                grant_type = "authorization_code"
            )

            authURL = session.generate_authcode()
            webbrowser.open(authURL)

            # ! this uses no authentication, and directly pasted
            authorizationCode = input(
                "Login and Enter the Authorication Code: "
            )

            # ? add the authorization code to get the access token
            session.set_token(authorizationCode)
            response = session.generate_token()
            return response["access_token"]

        accessToken = accessToken or generateAccessToken()
        return fyersModel.FyersModel(
                username = self.username, token = accessToken
            )


    def logout(self) -> bool:
        """
        The function should ideally be used to terminate the service
        such that any secret key (or tokens) generated after login is
        revoked. However, as on date this is not yet available.
        """

        pass


class FyersAPI(BaseBrokerAPI):
    """
    Implementation of concrete method to access FYERS API services for
    different financial decisions including fetching historical data,
    execute buy/sell of orders, etc.

    :type  maxConcurrent: int
    :param maxConcurrent: Limit the number of concurrent jobs that
        are allowed to be run in an asynchronous manner. The value is
        passed to ``semaphore`` (a synchronous primitive that is used
        to control access to a shared resource by multiple threads)
        that provides rate limitation in a production environment.
    """

    def __init__(
        self, sessionManager : Callable, maxConcurrency : int,
        webSocket : Optional[Callable] = None
    ) -> None:
        super().__init__(
            sessionManager = sessionManager,
            maxConcurrency = maxConcurrency, webSocket = webSocket
        )


    async def fetchData(
        self, symbol : str, dateRange : Tuple[dt.datetime, dt.datetime],
        timeframe : str = "1m", *_, **kwargs
    ) -> Iterable:
        """
        Fetch historical/real-time data using the Broker's API for an
        underlying security at a provided granuality. The function is
        asynchronous in nature and thus provides flexibility to fetch
        multiple securities data at once.

        :type  symbol: str
        :param symbol: FYERS API v3 uses standard naming convention
            as ``<exchange>:<symbol>-<class>`` as symbol to fetch the
            underlying security data.

        :type  dateRange: Tuple[dt.datetime, dt.datetime]
        :param dateRange: Time period range (both end inclusive) for which
            the historic data needs to be fetched. Broker's API may
            require formatting the value (for example using epoch time
            or passing string) which must be handled in the concrete
            method of the interface. The tuple value is formatted and
            passed to ``range_from`` and ``range_to`` attribute of the
            session manager object.

        :type  timeframe: str
        :param timeframe: Timeframe or granularity or resolution for
            which data needs to be fetched. For example, "1m" (default)
            may be used to refer to minute level candle data values.
            This value is passed to ``resolution`` argument to define
            the data resolution.

        **Keyword Arguments**

        To further format or refine the historical data, the following
        data flags are defined (with defaults) to control the response
        as below:

            * **date_format** (*str*): Format of the data, which can
                be used to change between epoch time (0, default) or
                 using the standard value (1) to get the data.

            * **cont_flag** (*int*): Continuity flag, defaults to 1.
                Check API documentation for more details.
        """

        dateRange = tuple(map(
            lambda x : str(int(x.timestamp())), dateRange
        )) # type: ignore

        data = {
            "symbol" : symbol, "resolution" : timeframe,
            "range_from" : dateRange[0], "range_to" : dateRange[1],
            "date_format" : kwargs.get("date_format", "0"),
            "cont_flag" : kwargs.get("date_format", 1),
        }

        return self.sessionManager.history(data = data) # type: ignore


    async def executeOrder(self, *args, **kwargs) -> Optional[object]:
        """
        Execute buy/sell of securities through the exchange using the
        Broker's API. An enduser must validate and continuously monitor
        the status of executed orders and proper logging of the values
        must be done in the production system for compliance and deep
        auditing purposes. This function involves monetary risks and
        endusers should be aware of the potential risks, limitations,
        and aware of the market condition. The authors of the script
        and/or module is not responsible for any profit/loss incurred
        from the usage of this function.

        **Fat-Finger/Boundary Checks**

        A financial system (in production environment) must ensure that
        safety checks are implemented to enforce strict boundary
        validations (max trade size, order value limits, daily rate
        limits) before delegating to the concrete API call. These rules
        must be developed in the strategies and from where the order
        is placed, and then the function should be called.
        """

        pass


    def getPositions(self) -> List[dict]:
        """
        Get the current position from the FYERS DEMAT account, and
        if there is a stop signal based on the current position (like
        the maximum loss incurred etc.) this value should be called.
        """

        pass
