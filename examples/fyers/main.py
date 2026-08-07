# -*- encoding: utf-8 -*-

"""
Main Script to Download Historical Data using FYERS API (v3) Services
---------------------------------------------------------------------

An example script that uses :mod:`algotraders` to download historic
security data for a given time period and write it to stdout as JSON
lines.

..versionchanged:: 2026-08-06 The Database Is Gone, and So Is dispatch()
    The previous version did two things that belonged somewhere else.
    It opened a SQLAlchemy engine and called ``to_sql``, which made an
    integration library aware of a table; persistence now lives in the
    host application, and ``examples/host/`` shows how.

    It also carried a ``dispatch()`` helper that hand-built monthly date
    buckets and fanned them out at ``maxConcurrency = 2_500``. That
    existed only to work around a missing chunker: it issued twelve
    requests per symbol per year regardless of what Fyers actually
    permits, and the imaginary concurrency queued invisibly behind a
    blocking SDK call. Range splitting is now internal to
    ``fetchCandles`` and sized from the broker's published caps, so the
    helper is deleted rather than moved.

**Usage**

.. code-block:: shell

    $ export ALGOTRADERS_FYERS_CLIENT_ID=XXXXXXXXXX-100
    $ export ALGOTRADERS_FYERS_SECRET_KEY=...
    $ python examples/fyers/main.py NSE:SBIN:EQ NSE:TCS:EQ > candles.jsonl

On the first run of the day there is no stored token, so the script
prints a login URL and exits. Visit it, authenticate, then re-run with
the code the broker returned:

.. code-block:: shell

    $ python examples/fyers/main.py --auth-code <code> NSE:SBIN:EQ

:NOTE: Output uses the ``snake_case`` alias spelling, so it can be piped
    into a loader without further translation.
"""

import sys
import json
import asyncio
import argparse
import datetime as dt

from typing import List, Optional

import algotraders

from algotraders.errors import AlgoTradersError, AuthorizationRequired
from algotraders.models import Timeframe


def parseArguments(argv : Optional[List[str]] = None) -> argparse.Namespace:
    """
    Parse the command line.

    :type  argv: Optional[List[str]]
    :param argv: Arguments to parse. Reads ``sys.argv`` when omitted.

    :rtype:   argparse.Namespace
    :returns: The parsed arguments.
    """

    parser = argparse.ArgumentParser(
        description = "Download Fyers historical candles as JSON lines."
    )

    parser.add_argument(
        "instruments", nargs = "+",
        help = "canonical instrument keys, for example NSE:SBIN:EQ"
    )
    parser.add_argument(
        "--timeframe", default = "1m",
        choices = [member.value for member in Timeframe],
        help = "candle granularity (default: 1m)"
    )
    parser.add_argument(
        "--days", type = int, default = 30,
        help = "how many days back to fetch (default: 30)"
    )
    parser.add_argument(
        "--auth-code", dest = "authCode", default = None,
        help = "authorization code returned by the broker redirect"
    )
    parser.add_argument(
        "--concurrency", type = int, default = 5,
        help = "simultaneous broker calls (default: 5)"
    )

    return parser.parse_args(argv)


async def main(argv : Optional[List[str]] = None) -> int:
    """
    Fetch candles for every requested instrument and print them.

    :rtype:   int
    :returns: A process exit status. ``2`` means an interactive login is
        needed, which is a normal first-run outcome rather than a fault.
    """

    arguments = parseArguments(argv)

    try:
        session = await algotraders.initialize(
            broker = "fyers", authCode = arguments.authCode,
            maxConcurrency = arguments.concurrency
        )
    except AuthorizationRequired as challenge:
        print(
            "No usable session. Open the URL below, authenticate, then "
            "re-run with --auth-code <code>.\n", file = sys.stderr
        )
        print(challenge.loginURL, file = sys.stderr)

        return 2

    end = session.clock.now()
    start = end - dt.timedelta(days = arguments.days)
    timeframe = Timeframe(arguments.timeframe)

    async with session:
        # ? one refresh populates the instrument master. Resolution is
        # local afterwards, so an unknown symbol fails immediately
        # instead of costing a rate limited round trip to find out
        await session.resolver.refresh()

        for key in arguments.instruments:
            instrument = await session.resolver.resolve(key)

            candles = await session.api.fetchCandles(
                instrument = instrument, timeframe = timeframe,
                dateRange = (start, end)
            )

            for candle in candles:
                print(json.dumps(candle.toRecord()))

            print(f"{key}: {len(candles):,} candles", file = sys.stderr)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except AlgoTradersError as error:
        print(f"{type(error).__name__}: {error}", file = sys.stderr)

        raise SystemExit(1)
