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

from typing import Optional, Iterable, List

from fyers_apiv3 import fyersModel

from algotraders.brokers.base import BaseBrokerAPI, requireLogin

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

    def __init__(self, maxConcurrent : int) -> None:
        super().__init__(maxConcurrent = maxConcurrent)


    @property
    def brokerName(self) -> str:
        """
        Name of the broker for which the concrete method is defined,
        and can be used for logging and/or auditing purpose.

        ..versionchanged:: 2021-08-20 Added API Version Number:
            FYERS API v2 is a major change with underlying library and
            other module changes. To reflect this changes, we've
            decided to add the version number to the property to let
            the end user know which services are being used. Check
            `blog <https://fyers.in/community/blogs-gdppin8d/post/introducing-my-api-f8nksVTo2uprJcD>`_
            for more information.

        ..versionchanged:: 2023-11-09 Updated API Version Number (v3):
            FYERS API v3 was released that enforces multiple regulatory
            changes by SEBI. More details is available in the
            `documentation <https://myapi.fyers.in/docsv3>`_.
        """

        return "FYERS API v3"


    @property
    def redirectURL(self) -> str:
        """
        The API redirection URL, this value is defined when creating
        a new app, check `documentation <https://myapi.fyers.in>`_ for
        more information.
        """

        return "https://trade.fyers.in/api-login/redirect-uri/index.html"


    def login(
        self, username : str, password : str, **kwargs
    ) -> object:
        """
        Perform login operation to the Broker's API. A broker's API
        login may have different regulatory guidelines based on which
        the concrete function should be modeled. Typically, for an
        Indian Broker, SEBI is the regulatory body who provides the
        guidelines to connect using the following parameters.

        :type  username: str
        :param username: Username or Client ID which is available from
            from the Broker and is typically the Login ID.

        :type  password: str
        :param password: Password to login to access the Broker's API,
            this value may be different from the password you used to
            login to the "DEMAT" account and is sometime referred as
            the "secret key" which is provided when creating the API
            endpoint in the Broker's API Terminal.

        **Keyword Agrument(s)**

        The keyword arguments are defined typically basis of rules
        and guidelines based on regulatory body - SEBI. The arguments
        are as below:

            * **secretKey** (*str*): Secret key, this value is defined
                during creating a new application in the API page.

        **Regulatory Change(s)**

        As per the latest SEBI regulations, username is the application
        code with a secret key generated during creation of the app.
        In addition, it is now mandatory to use a two-factor authentication
        key which is implemented in the login and the authentication
        token expires on a daily basis.
        """

        session = fyersModel.SessionModel(
            client_id = username,
            secret_key = password,
            redirect_uri = self.redirectURL,
            response_type = "code",
            grant_type = "authorization_code"
        )

        authURL = session.generate_authcode()
        print("Login with this URL:\n", authURL)

        AUTH_CODE = input("Enter auth_code from redirected URL: ")
        session.set_token(AUTH_CODE)
        response = session.generate_token()

        ACCESS_TOKEN = response["access_token"]
        print("Access Token:", ACCESS_TOKEN)

        self.status = True
        return fyersModel.FyersModel(
            client_id = username, token = ACCESS_TOKEN
        )


    def logout(self) -> bool:
        """
        Performs logout operation from the FYERS API services and any
        new call will again require ``.login()`` method to be called.
        """

        return False


    @requireLogin
    async def fetchData(self, *args, **kwargs) -> Iterable:
        """
        Fetch historical/real-time data using the Broker's API for an
        underlying security at a provided granuality. The function is
        asynchronous in nature and thus provides flexibility to fetch
        multiple securities data at once.
        """

        pass


    @requireLogin
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


    @requireLogin
    def getPositions(self) -> List[dict]:
        """
        Get the current position from the FYERS DEMAT account, and
        if there is a stop signal based on the current position (like
        the maximum loss incurred etc.) this value should be called.
        """

        pass
