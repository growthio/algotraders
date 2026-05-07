# -*- encoding: utf-8 -*-

"""
A Service Layer to Fetch Real-Time Options Chain Data from NSE India
--------------------------------------------------------------------

NSE India provides API endpoints to fetch option-chain data for all
tradeable securities (equity, forex, commodities) allowed under NSE
(National Stock Exchange, India). This service tries to provide an one
unified solution to fetch, store and analyze the option chain data
which will be crucial to take actions during live-market tradings.
"""

import os
import time
import yaml
import requests

import datetime as dt

import pandas as pd

from tqdm import tqdm as TQ


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
            except (requests.RequestException, ValueError) as e:
                count += 1 # track number of times the fetch fails
                print(f"{time.ctime()} : Failed to Fetch Data - {e}")

                _ = [time.sleep(1) for _ in TQ(range(waittime), desc = f"#{count + 1} Retrying...")]

        return response


    def setconfig(self, file : str = os.path.join(os.path.abspath(os.path.dirname(__file__)), "config", "nseoptions.yaml"), type : str = "index", **kwargs) -> dict:
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


class OptionChainProcessing:
    """
    A Class with Embedded Functions to Process Option Chain Data

    The option chain data fetched from NSE India is processed in this
    class. The class has functions to process the data and return
    filtered data for a given expiry date and near strike price.

    The processing engine is kept as a seperate submodule inside the
    parent thus enabling various alternate controls and processing of
    methods. In addition, this process is enables seperate control for
    seperating the premium feature of :mod:`nseoptions` package.

    :type  symbol: str
    :param symbol: Symbol to fetch the data from NSE India website.
        The symbol should be upper case (or converted internally), as
        available in https://www.nseindia.com/option-chain data.

    :type  apikey: str
    :param apikey: An API key to access the premium or freeware
        features for the :mod:`nseoptions` package. (TODO)

    :type  response: dict
    :param response: A dictionary containing the response from the NSE
        India API. The response should be an instance of the core
        module.

    :type  expiry: str or dt.date
    :param expiry: A valid expiration date of the given symbol. If the
        date is an instance of string the it must be of the date style
        :attr:`%d-%b-%Y` (example '25-Feb-2025') or can be a date.

    Keyword Arguments
    -----------------

    The keyword arguments are now available as a placeholders, and is
    not tested yet. The following keyword arguments are available:
    
        * **nstrikes** (*int*) -- The number of strike prices above and
          below the ATM to be fetched from the data. Default is 20.
    """

    def __init__(
        self,
        symbol : str,
        apikey : str,
        response : dict,
        expiry : str | dt.date,
        **kwargs
    ) -> None:
        self.symbol = symbol
        self.apikey = apikey

        self.response = response

        # expiry date is either an instance of string or date
        self.expiry = expiry if isinstance(expiry, str) else \
            expiry.strftime("%d-%b-%Y")
        
        # ? get and define the keyword arguments for the class
        self.nstrikes = kwargs.get("nstrikes", 20)
        self.multiple = kwargs.get("multiple", self.imultiple(symbol))

        # ? set class attributes from the response for various methods
        self.timestamp = dt.datetime.strptime(
            self.response["records"]["timestamp"], "%d-%b-%Y %H:%M:%S"
        )
        self.underlying = self.response["records"]["underlyingValue"]

        # ? set the strike price range for the given expiry date
        self.atm, self.lstrike, self.hstrike = self.strikerange(
            n = self.nstrikes,
            multiple = self.multiple,
            underlying = self.underlying
        )


    @staticmethod
    def strikerange(n : int, multiple : int, underlying : float) -> tuple:
        """
        A Method to Get the Strike Price Range

        The method calculates the strike price range for the given
        expiry date. The method returns a tuple of the lower, upper and
        at the money strike price for the given expiry date.
        """

        atm = round(underlying / multiple) * multiple
        low, high = atm - n * multiple, atm + n * multiple

        return atm, low, high
    

    def imultiple(self, symbol : str) -> int:
        """
        The Option Chain Default Exercise Price Multiple

        The method is kept as a placeholder and fallback for the
        default multiple for the different exercise/strike price. The
        function only exposes default multiple for the indexes as
        available in the NSE India website.

        :type  symbol: str
        :param symbol: The symbol for which the multiple is to be
            fetched. The symbol should be upper case.

        :rtype: int
        :return: The default multiple for the given symbol, else return
            50 as a default value.
        """

        values = dict(BANKNIFTY = 100, MIDCPNIFTY = 25, NIFTYNXT50 = 100)
        return values.get(symbol, 50)


    def makeclean(self, rtype : callable = pd.DataFrame, verbose : bool = False) -> object:
        """
        Core Functionality to Clean and Process the Data

        The method cleans the data and returns a processed iterable
        object of a given type. The method joins both the freeware and
        the premium features of the package and return a data.

        :type  rtype: callable
        :param rtype: A callable object that can be used to return the
            data. The default is :class:`pandas.DataFrame`.

        :type  verbose: bool
        :param verbose: Print the debug and/or other relevant information
            while fetching the data. Default is False.
        """

        data = self.response["records"]["data"]

        # ? get put-call-ratio and total aggregated traded volume
        self.tot_oi_ce = self.response["filtered"]["CE"]["totOI"]
        self.tot_oi_pe = self.response["filtered"]["PE"]["totOI"]
        self.put_call_ratio = self.tot_oi_pe / self.tot_oi_ce if self.tot_oi_ce > 0 else 0.0

        self.tot_vol_ce = self.response["filtered"]["CE"]["totVol"]
        self.tot_vol_pe = self.response["filtered"]["PE"]["totVol"]

        ocdata = []
        for item in data:
            for instrument, info in item.items():
                if instrument in ["CE", "PE"]:
                    dump = info # current line item dump
                    dump["instrumentType"] = instrument # CE/PE Key
                    ocdata.append(dump)
                else:
                    pass

        if verbose:
            print(f"{dt.datetime.now()} : Data Fetched for `{self.symbol}`")
            print(f"  >> Underlying Value   : ₹ {self.underlying:,.2f}")
            print(f"  >> Response Timestamp : {self.timestamp}")
            print(f"  >> ATM Strike Price   : ₹ {self.atm:,.2f}")
            print(f"  >> Strike Price Range : ₹ {self.lstrike:,.2f} - ₹ {self.hstrike:,.2f}")

        frame = pd.DataFrame(ocdata) # keep only ce/pe data then filter
        frame = frame[
            (frame["expiryDate"] == self.expiry)
            & (frame["strikePrice"].between(self.lstrike, self.hstrike))
        ]

        # since we already know the expiry and symbol, we can delete them
        # also identifier is not required as we will not be placing order
        dropcols = [
            "expiryDate",
            "underlying",
            "identifier",
            "underlyingValue"
        ]

        frame.drop(columns = dropcols, inplace = True)
        
        # now we can seperate the call and put data, set as attribute
        self.ce = frame[frame["instrumentType"] == "CE"].copy()
        self.pe = frame[frame["instrumentType"] == "PE"].copy()

        opchain = pd.merge(
            self.ce, self.pe, how = "inner", on = "strikePrice",
            suffixes = ("_ce", "_pe")
        )

        dropcols = ["instrumentType_ce", "instrumentType_pe"]
        opchain.drop(columns = dropcols, inplace = True)

        # mimic and return the columns as in the nse option chain
        columns = [
            "openInterest",
            "changeinOpenInterest",
            "pchangeinOpenInterest",
            "totalTradedVolume",
            "impliedVolatility",
            "lastPrice",
            "change",
            "pChange",
            "totalBuyQuantity",
            "totalSellQuantity",
            "bidQty",
            "bidprice",
            "askQty",
            "askPrice"
        ]

        cecols_ = [f"{col}_ce" for col in columns]
        pecols_ = [f"{col}_pe" for col in columns][::-1]

        opchain = opchain[cecols_ + ["strikePrice"] + pecols_]
        return opchain if rtype == pd.DataFrame else rtype(opchain)
