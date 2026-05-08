# -*- encoding: utf-8 -*-

"""
Script to Download Historic Data using Fyers API (v3) Service
-------------------------------------------------------------

An example script that uses the :mod:`algotraders.brokers.fyers` to
download historic security data for a given time period using the
asynchronous function implementation with rate limitations.
"""

import asyncio
import algotraders
import pandas as pd
import datetime as dt
import zoneinfo as zi

if __name__ == "__main__":
    broker = algotraders.brokers.getBrokerInterface("fyers")

    brokerAuth = broker["AUTH"](username = "FX0KN0G7PX-100", password = "LQYCU172WX")
    session = brokerAuth.login() # perform login operation

    brokerApi = broker["API"](
        sessionManager = session, maxConcurrency = 1
    )

    # ? Check the concurrency for a 10GB Memory System
    concurrency = asyncio.run(brokerApi.traceMemoryUsage(
        limit = 10_000, symbol = "NSE:HDFCBANK-EQ", timeframe = "1", dateRange = (
            dt.datetime(2026, 4, 1, 9, 15), dt.datetime(2026, 4, 30, 15, 30)
        )
    ))
    print(f"Recommended Max. Concurrency: {concurrency:,} Services")

    # ? Method to Get the Historic Data for 1 Month at 1 Minute Timeframe
    response = asyncio.run(brokerApi.fetchData(
        symbol = "NSE:HDFCBANK-EQ", timeframe = "1", dateRange = (
            dt.datetime(2026, 4, 1, 9, 15), dt.datetime(2026, 4, 30, 15, 30)
        )
    ))

    response = pd.DataFrame(
        response["candles"], columns = [
            "timestamp", "open", "high", "low", "close", "volume"
        ]
    )

    response["timestamp"] = response["timestamp"].apply(
        lambda x : str(dt.datetime.fromtimestamp(x, tz = zi.ZoneInfo("Asia/Kolkata")))
    )
    print(response)
