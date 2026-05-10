# -*- encoding: utf-8 -*-

"""
Main Script to Download Historical Data using FYERS API (v3) Services
---------------------------------------------------------------------

An example script that uses the :mod:`algotraders.brokers.fyers` to
download historic security data for a given time period using the
asynchronous function implementation with rate limitations. Currently,
the example is designed with static symbols list (TODO), but the
approach is designed such that this can be extended for any symbols.
"""

import asyncio
import calendar
import datetime as dt
import zoneinfo as zi
from typing import Callable

import algotraders
import pandas as pd
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as pg_ENUM
from tqdm import tqdm

def dispatch(symbols : list[str]) -> list:
    """
    Builds load dispatcher for the asynchronous function operations
    which takes in all the input symbols and creates a monthly bucket
    that can be unpacked to API asynchronous data calling module.
    """

    dates = [
        (
            dt.datetime(year, month, 1, 9, 15),
            dt.datetime(year, month, calendar.monthrange(year, month)[1], 15, 30)
        )
        for year in range(2018, 2019) for month in range(1, 13)
    ]

    return [
        (start, end, f"NSE:{symbol}-EQ")
        for start, end in dates for symbol in symbols
    ]


async def main(api : Callable) -> list[dict]:
    """
    Gathers all asynchronous calls to get the historcal data from the
    underlying API and then returns a list of dictionary that can be
    readily converted to a :mod:`pd.DataFrame` so that the data can
    be easily stored in a database for file object.
    """

    dispatched = dispatch(
        symbols = pd.read_csv("symbols.csv")["exchange_symbol"].unique()
    )
    tasks = [
        asyncio.create_task(
            api.fetchData(
                symbol = symbol, timeframe = "1", dateRange = (start, end)
            )
        )
        for start, end, symbol in dispatched
    ]

    with tqdm(total = len(tasks), desc = "Fetching Data") as pbar:
        for task in tasks:
            task.add_done_callback(lambda _ : pbar.update())
        responses = await asyncio.gather(*tasks, return_exceptions = True)

    candleData = []
    for (*_, symbol), response in zip(dispatched, responses):
        if isinstance(response, Exception) or not isinstance(response, dict):
            continue # no response; skip the object
        elif isinstance(response, dict) and "candles" not in response.keys():
            continue # no valid candle data; need to be logged
        else:
            candleData.extend(
                [list(row) + [symbol] for row in response["candles"]]
            )

    return candleData


if __name__ == "__main__":
    broker = algotraders.brokers.getBrokerInterface("fyers")

    # set credentials, and get the token to initialize services
    # and, login to use the services (generates access token)
    session = broker["AUTH"](
        username = "", password = ""
    ).login(accessToken = open("token", "r").read().strip())

    # create api object, this will be used for all other operations
    api = broker["API"](
        sessionManager = session, maxConcurrency = 2_500
    )

    responses = asyncio.run(main(api = api))
    responses = pd.DataFrame(responses, columns = [
        "effective_time", "open_price", "high_price", "low_price",
        "close_price", "volume", "exchange_symbol_ext"
    ])

    responses["timeframe"] = "1m"
    responses["ticker_data_source_id"] = "FYERS"
    responses["effective_time"] = responses["effective_time"].apply(
        lambda x : dt.datetime.fromtimestamp(x, tz = zi.ZoneInfo("Asia/Kolkata"))
    )

    # ? create engine object, and then insert the data into table
    engine = sa.create_engine(
        ""
    )

    # ! create_type=False: the ENUM already exists in DB, don't recreate it
    timeframe_type = pg_ENUM(
        "1m", "3m", "5m", "15m", "1h", "1D", "1M", "1Y",
        name = "timeframe_value",
        schema = "private",
        create_type = False
    )

    print(f"Trying to Insert {responses.shape[0]:,} Records.")
    responses.to_parquet("data.parquet", index = False)
    responses.to_sql(
        "security_prices_tx", engine, schema = "private",
        index = False, if_exists = "append",
        dtype = {"timeframe" : timeframe_type}
    )
