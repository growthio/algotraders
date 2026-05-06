# -*- encoding: utf-8 -*-

"""
Main File to Fetch NSE India Options Chain Data
"""

import time
import yaml
import requests

from tqdm import tqdm as TQ

from nseoptions import CONFIG


class NSEOptionChain:
    """
    National Stock Exchange (NSE) Option Chain Data Extraction Module

    The NSE Option Chain is a free to use data source to analyze the
    Indian option chain for various symbols. The details of usage and
    terms and condition are available at the NSE India website.

    The core function aims to fetch the data for a particular symbol
    or an index for a said expiration date. In addition, the module
    returns only relevant (near ATM) strike prices for the analysis.

    :type  symbol: str
    :param symbol: Symbol to fetch the data from NSE India website.
        The symbol should be upper case (or converted internally), as
        available in https://www.nseindia.com/option-chain data.

    :type  expiry: str or dt.date
    :param expiry: A valid expiration date of the given symbol. If the
        date is an instance of string the it must be of the date style
        :attr:`%d-%b-%Y` or can be a date.
    
    Keyword Arguments
    -----------------

    The keyword arguments are defined for additional controls, and it
    is recommended to use the default settings (since the module is
    currently under development and the keyword arguments aren't yet
    fully tested).

        * **verbose** (*bool*) - Print the debug and/or other relevant
          information while fetching the data. Default is True.

        * **multiple** (*int*) - The multiple of the strike price for
          a symbol or an index value. The default value is set for the
          indexes like NIFTY, BANKNIFTY, etc. To set the multiple for
          other symbols set the value, defaults to 50.

        * **nstrikes** (*int*) - The number of strike prices above and
          below the ATM to be fetched from the data. Default is 20.

        * **verify** (*bool*) - SSL certificate validation to get the
          data from the internet. Always recommended to be True, but
          in case of legacy/restricted systems this option comes in
          handy. Defaults to False.
    """

    def __init__(self, symbol : str, **kwargs) -> None:
        self.symbol = symbol.upper()

        # keyword arguments parsed from cli/object initialization
        self.verify = kwargs.get("verify", False)


    def response(self, waittime : int = 10) -> dict:
        """
        Session Object for the NSE Option Chain Data

        The function returns a session object to fetch the data from the
        NSE India website. The session object is used to fetch the data
        from the website using the API URI.

        The session object is created using the `requests` module and
        the headers are set to mimic a browser request to the website.
        """

        fetched, count = False, 0
        while not fetched:
            try:
                session = requests.Session().get(
                    self.NSE_API_URI,
                    headers = self.URI_HEADER,
                    verify = self.verify
                )
                response = session.json()

                fetched = True # exit the loop if json is fetched
            except Exception as e:
                count += 1 # track number of times the fetch fails
                print(f"{time.ctime()} : Failed to Fetch Data - {e}")

                _ = [time.sleep(1) for _ in TQ(range(waittime), desc = f"#{count + 1} Retrying...")]

        return response


    def setconfig(self, file : str = CONFIG, type : str = "index", **kwargs) -> dict:
        """
        Configuration Data for the NSE Option Chain Module

        The default configuration is stored under the `config` folder
        at the base of the module. However, the user can set and update
        any part of the configuration by passing the file path or
        passing the individual values.

        :type  file: str
        :param file: The file path to the configuration file. The file
            should be a YAML file with the required configuration.

        :type  type: str
        :param type: The type of the configuration to be fetched. The
            default is `index` for the index options chain data. The
            other option is `stock` for the stock options chain data.

        Keyword Arguments
        -----------------

        The keyword arguments are defined to update a part of the
        configuration value. For example, if an end user wants to only
        change the header value, then the user can pass the header which
        will overwrite the default header section with the new value.

            * **header** (*dict*) - The header information to be passed
                while fetching the data from the NSE India website.

            * **apiuri** (*str*) - The API URI to fetch the data from
                the NSE India website. The default is set to None.
        """

        header = kwargs.get("header", None)
        apiuri = kwargs.get("apiuri", None)

        # todo: check if file exists, and file is not None
        config = yaml.load(open(file, "r"), Loader = yaml.FullLoader)

        # ? update any part of the configuration if passed by enduser
        config["config"]["header"] = header if header else config["config"]["header"]

        # ! set the global header for the model, will not change on run
        self.URI_HEADER = config["config"]["header"]

        # ! the global url for the model is two part, either use the
        # default or set the new one as given by the end user argument
        configuri = config["config"]["uri"]
        self.NSE_API_URI = apiuri if apiuri else \
            configuri["base"] + configuri["type"][type]

        self.NSE_API_URI = self.NSE_API_URI.format(symbol = self.symbol)

        return config # return everything for debugging, developer usage
