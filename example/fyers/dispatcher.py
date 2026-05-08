# -*- coding: utf-8 -*-
"""
Script to Download Historic Data using Fyers API (v3) Service

An example script that uses the :mod:`algotraders.brokers.fyers` to
download historic security data for a given time period using the
asynchronous function implementation with rate limitations.
"""

# --- standard library ---
import asyncio
import calendar
import datetime as dt
import zoneinfo as zi

# --- third-party ---
import numpy as np
import pandas as pd
from tqdm.asyncio import tqdm

# --- local / internal ---
import algotraders


def symbols() -> np.ndarray:
    """
    Load and Return Valid EQ Symbols from CSV

    Reads the ``symbols.csv`` file and returns the unique exchange symbols
    filtered to ``EQ`` series instruments only.

    :rtype:  np.ndarray
    :return: Array of valid equity exchange symbols.
    """
    data = pd.read_csv("symbols.csv")
    valids = data[data["symbol_series_code"] == "EQ"]
    return ["HDFCBANK", "SBIN"]  # valids["exchange_symbol"].unique()[:2]


def dispatch(symbols : np.ndarray) -> list:
    """
    Build Dispatch Task List for All Symbols and Date Ranges

    Generates a flat list of ``(start, end, symbol)`` tuples covering
    every calendar month from 2017 through 2026 for each supplied symbol.

    :type  symbols: np.ndarray
    :param symbols: Array of exchange symbol strings.

    :rtype:  list
    :return: List of ``(start_datetime, end_datetime, symbol_string)`` tuples.
    """
    dates = [
        (
            dt.datetime(year, month, 1, 9, 15),
            dt.datetime(year, month, calendar.monthrange(year, month)[1], 15, 30)
        )
        for year in range(2017, 2018) for month in range(7, 13)
    ]

    return [
        (start, end, f"NSE:{symbol}-EQ")
        for start, end in dates
        for symbol in symbols
    ]


async def main() -> list:
    """
    Fetch Historic Candle Data for All Dispatched Tasks

    Gathers all asynchronous ``broker.fetchData`` tasks and collects
    ``candles`` from each valid response. Responses that do not contain
    the ``candles`` key are silently skipped. Each candle row is extended
    with the corresponding ``symbol`` value.

    :rtype:  list
    :return: Flat list of candle rows, each row as a list of OHLCV values
             followed by the symbol string.
    """
    dispatched = dispatch(symbols = symbols())
    print(dispatched)
    tasks = [
        broker.fetchData(
            symbol = symbol, timeframe = "1", dateRange = (start, end)
        )
        for start, end, symbol in dispatched
    ]

    responses = await tqdm.gather(*tasks, desc = "Fetching Candle Data")

    candles = []
    for (_, _, symbol), response in zip(dispatched, responses):
        try:
            candles.extend([list(row) + [symbol] for row in response["candles"]])
        except KeyError:
            pass

    return candles


if __name__ == "__main__":
    broker = algotraders.brokers.getBrokerInterface(
        "fyers", maxConcurrent = 1
    )
    broker.login(username = "", password = "")

    # ? Get the Symbols from the File, and Create Load Dispatcher
    response = asyncio.run(main())
    response = pd.DataFrame(
        response, columns = [
            "timestamp", "open", "high", "low", "close", "volume", "symbol"
        ]
    )

    response["timestamp"] = response["timestamp"].apply(
        lambda x : str(dt.datetime.fromtimestamp(x, tz = zi.ZoneInfo("Asia/Kolkata")))
    )

    print(response["symbol"].unique())
    print(response["timestamp"].min(), response["timestamp"].max())
    print(response)
