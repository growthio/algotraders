# -*- encoding: utf-8 -*-

"""
Reference FastAPI Host Integrating the AlgoTraders Library
------------------------------------------------------------

Shows the whole boundary in one file: how a session is created, where
the OAuth redirect is handled, and where the database work that used to
live inside ``examples/fyers/main.py`` actually belongs.

**The division of responsibility**

:mod:`algotraders` fetches, normalizes and returns. It never opens a
database connection, never blocks on input, and never persists
anything. The host owns the ORM, the schema, the transaction and the
HTTP surface. ``/ingest/candles`` below is the whole of the migration:
the old script's ``to_sql`` call becomes an upsert in the host's own
session.

Three details are worth copying rather than reinventing.

``on_conflict_do_nothing`` replaces the old ``if_exists = "append"``,
which silently duplicated every row on any re-run. A backfill is re-run
constantly, so idempotence is not optional.

The redirect route is a **GET** here because the broker performs a
browser redirect and there is no choice about that, but it immediately
forwards the code into the library rather than logging or storing it.
The route path must match what is registered in the broker's console
byte for byte.

The session provider is a dependency, so a multi-tenant host returns a
different session per authenticated user simply by changing this one
function.

:NOTE: Illustrative only. Not imported by the library, not exercised by
    its test suite, and it needs sqlalchemy, asyncpg and a database.
"""

import datetime as dt

from typing import Any, Dict, List, Optional

import fastapi

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert as postgresInsert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine

import algotraders

from algotraders.auth import BrokerSession
from algotraders.errors import AuthorizationRequired
from algotraders.models import Timeframe
from algotraders.transport.fastapi import (
    createRouter, installExceptionHandlers
)

from examples.host.models import SecurityPricesTx, toPriceRow

DATABASE_URL = "postgresql+asyncpg://user:password@localhost:5432/app"

engine = create_async_engine(DATABASE_URL, pool_pre_ping = True)
SessionMaker = async_sessionmaker(engine, expire_on_commit = False)

app = FastAPI(title = "Trading Backend")

# ? one session per process here. A multi-tenant host would key this on
# the authenticated user and hold one session per broker account
_session : Optional[BrokerSession] = None


class IngestRequest(BaseModel):
    """
    Body for a candle ingestion request.
    """

    instrumentKey : str
    timeframe : Timeframe
    start : dt.datetime
    end : dt.datetime


class IngestResult(BaseModel):
    """
    Outcome of an ingestion run.

    :type  inserted: int
    :param inserted: Rows the database actually accepted. Lower than
        ``fetched`` on a re-run, because the conflict clause discards
        what is already stored.
    """

    instrumentKey : str
    fetched : int
    inserted : int


async def getDatabase() -> AsyncSession:
    """
    Yield a database session for one request.
    """

    async with SessionMaker() as session:
        yield session


async def getSession() -> BrokerSession:
    """
    Return the broker session, creating it on first use.

    :raises AuthorizationRequired: No usable token exists. The exception
        handler installed below turns this into an HTTP 409 carrying the
        login URL, which the frontend redirects to.
    """

    global _session

    if _session is None or not _session.isAuthenticated:
        _session = await algotraders.initialize(
            broker = "fyers", maxConcurrency = 5
        )

    return _session


@app.get("/api/brokers/fyers/callback")
async def fyersCallback(
    auth_code : str = Query(..., alias = "auth_code"),
    state : Optional[str] = None
) -> RedirectResponse:
    """
    Receive the broker's redirect and complete the login.

    A GET because the broker redirects a browser here and there is no
    choice about that. The code is forwarded straight into the library
    and never logged or stored; the route path must match the redirect
    URI registered in the Fyers console exactly.

    :rtype:   RedirectResponse
    :returns: A redirect back into the frontend.
    """

    global _session

    _session = await algotraders.initialize(
        broker = "fyers", authCode = auth_code, state = state
    )

    return RedirectResponse(url = "/dashboard?connected=fyers")


@app.post("/ingest/candles", response_model = IngestResult)
async def ingestCandles(
    payload : IngestRequest,
    session : BrokerSession = Depends(getSession),
    database : AsyncSession = Depends(getDatabase)
) -> IngestResult:
    """
    Fetch candles from the broker and persist them.

    This endpoint is the migration. Everything the old script did with
    SQLAlchemy now happens here, and the library simply returns typed
    models. Chunking, rate limiting, retry and timezone normalisation
    all happen inside ``fetchCandles``.

    :rtype:   IngestResult
    :returns: How many candles were fetched and how many rows were new.
    """

    instrument = await session.resolver.resolve(payload.instrumentKey)

    candles = await session.api.fetchCandles(
        instrument = instrument, timeframe = payload.timeframe,
        dateRange = (payload.start, payload.end)
    )

    if not candles:
        return IngestResult(
            instrumentKey = payload.instrumentKey, fetched = 0, inserted = 0
        )

    rows : List[Dict[str, Any]] = [toPriceRow(candle) for candle in candles]

    # ! on_conflict_do_nothing, not append. The previous script used
    # if_exists = "append", which silently duplicated every row on any
    # re-run, and a backfill is re-run constantly
    statement = postgresInsert(SecurityPricesTx).values(rows)
    statement = statement.on_conflict_do_nothing(
        constraint = "security_prices_tx_natural_key"
    )

    result = await database.execute(statement)
    await database.commit()

    return IngestResult(
        instrumentKey = payload.instrumentKey,
        fetched = len(candles),
        inserted = result.rowcount or 0
    )


@app.get("/health")
async def health() -> Dict[str, Any]:
    """
    Report whether a broker session is currently usable.

    Deliberately does not create one: a health probe that triggers an
    OAuth flow is worse than a probe that reports the truth.
    """

    if _session is None:
        return {"broker" : None, "authenticated" : False}

    return {
        "broker" : _session.brokerName,
        "authenticated" : _session.isAuthenticated,
        "expiresAt" : (
            _session.tokenExpiresAt.isoformat()
            if _session.tokenExpiresAt else None
        )
    }


# ? the bundled router is optional. Mounting it gives the frontend the
# read surface for free; trading stays off unless explicitly enabled
app.include_router(
    createRouter(sessionProvider = getSession, enableTrading = False)
)

installExceptionHandlers(app)


__all__ = ["app", "getSession", "getDatabase", "ingestCandles"]
