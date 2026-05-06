# -*- encoding: utf-8 -*-

"""
Option Chain Data Processing Module

The module contains methods to process the option chain data and
return filtered data along with various other metrics which may be
essential for the end user. The module is designed to be used in
conjunction with the core module of the package.

The data processing module is kept seperate from the core module to
ensure and integrate a clear seperation of concerns. In addition, the
processing module will act as a layer between premium and freeware
features of the package.
"""

import datetime as dt

import pandas as pd

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
        self.put_call_ratio = self.tot_oi_pe / self.tot_oi_ce

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
