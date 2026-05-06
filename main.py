# -*- encoding: utf-8 -*-

"""
CLI/Main Module Interface to the NSE Options Package

The `main.py` act as a standalone file which can be started from the
terminal or command prompt. The file is set to run iteratively and
fetch the option chain data for the given symbol and expiry date for
the entire day (when the market is open, or as per requirement).

@author:  Debmalya Pramanik
@version: v0.0.1
"""

import os     # miscellaneous os interfaces
import sys    # configuring python runtime environment
import json   # module to manipulate json object in python
import time   # library for time manipulation, and logging
import shutil # module for high level file operations like copy

import argparse # argument parser for additional controls

# use `datetime` to control and preceive the environment
# in addition `pandas` also provides date time functionalities
import datetime as dt

from tqdm import tqdm as TQ # progress bar for loops
from uuid import uuid4 as UUID # unique identifier for objs

import numpy as np
import pandas as pd

pd.set_option('display.max_rows', 50) # max. rows to show
pd.set_option('display.max_columns', 15) # max. cols to show
np.set_printoptions(precision = 3, threshold = 15) # set np options
pd.options.display.float_format = '{:,.3f}'.format # float precisions

import xlwings as xw # https://www.xlwings.org/

# ! please download the prettify file from gist/github
# https://gist.github.com/ZenithClown/c6b4c51de4d4dac564ecbe0e178955cb
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "modules", "prettify"))
import prettify # noqa: F401, F403 # pyright: ignore[reportMissingImports]

# ! append the root (this file) directory to the system path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import nseoptions # https://github.com/iTraders/nseoptions

def writejson(response : dict, symbol : str, timestamp : dt.datetime | str, outdir : str) -> bool:
    timestamp = str(timestamp).replace(":", "") # time is now formatted
    filename = os.path.join(outdir, f"{symbol} #{str(UUID())[:7].upper()} at {timestamp}.json")

    with open(filename, "w") as f:
        json.dump(response, f, indent = 2, sort_keys = False, default = str)

    return True


def writefile(file : str, opchain : pd.DataFrame, model : object) -> bool:
    wb = xw.Book(file) # open the copied template file

    # populate the sheets with sheet selection, and defining output cell, like:
    wb.sheets["Option Chain"]["A12"].options(header = False, index = False).value = opchain

    # write other important metric/information value from the objects
    wb.sheets["Option Chain"]["O1"].options(transpose = True).value = [model.underlying, model.timestamp]

    # attributes related to the calculation or put-call-ratio
    wb.sheets["Option Chain"]["O3"].value = model.put_call_ratio

    wb.sheets["Option Chain"]["A1"].value = model.tot_oi_ce
    wb.sheets["Option Chain"]["AC1"].value = model.tot_oi_pe

    wb.sheets["Option Chain"]["D1"].value = model.tot_vol_ce
    wb.sheets["Option Chain"]["Z1"].value = model.tot_vol_pe

    return True # ! do not save the file, com error is raised


if __name__ == "__main__":
    symbol = input("Enter the Symbol [NIFTY]: ").upper() or "NIFTY"
    expiry = input("Enter the Expiry (DD-MMM-YYYY): ")

    # ..versionadded:: 2025-10-15 - CLI Argument Parse for Controls
    parser = argparse.ArgumentParser(description = "ARGPARSE Enabled")
    parser.add_argument(
        "--no-verify",
        dest = "verify",
        action = "store_false",
        help = "SSL Verification, Defaults to False."
    )

    # ? get arguments from the argparse controller - use in forward
    args = parser.parse_args()

    API = nseoptions.NSEOptionChain(
        symbol, verify = args.verify
    ) # main api object, loop on
    _ = API.setconfig() # configuration settings for the API, onetime

    prettify.textAlign("NSE Options Chain Data Fetcher", align = "center")
    prettify.textAlign("Author: Debmalya Pramanik", align = "center")

    # ? create a file for the session, use the base template file
    # continuously overwrite on the file with the latest changed data
    today, fileid = dt.datetime.now().date(), str(UUID()).upper()[:3]
    filename = f"{today} #{fileid} OP {symbol} for {expiry}.xlsx"
    filename = os.path.join(".", "output", filename)

    template = os.path.join(".", "template", "NSE Option Chain.xlsx")
    shutil.copy(template, filename) # make a copy of the template file

    # also create internal directory to track and save all json response
    responsedir = os.path.join(".", "output", str(today))
    os.makedirs(responsedir, exist_ok = True)

    print(f"{time.ctime()} : Staring API Collection")
    print(f"  >> Output File Path: {filename}", end = "\n\n")

    while True:
        # ! the application need to be forced close with CTRL+C
        # ! or by directly closing the terminal or command prompt
        response = API.response(waittime = 20) # auto retry if failed

        # ? create the model object, this is on the fly, processing
        model = nseoptions.processing.OptionChainProcessing(
            symbol, apikey = "", response = response, expiry = expiry
        )

        opchain = model.makeclean(verbose = True)

        writefile(file = filename, opchain = opchain, model = model)
        writejson(response, symbol, timestamp = model.timestamp, outdir = responsedir)

        _ = [time.sleep(1) for _ in TQ(range(30), desc = "Waiting to Refresh...")]
