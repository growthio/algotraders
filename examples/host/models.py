# -*- encoding: utf-8 -*-

"""
Host Side Persistence: SQLAlchemy Models and a Token Store
------------------------------------------------------------

Everything in this file belongs to the **host application**, not to
:mod:`algotraders`. It is here as a reference for wiring the two
together, and it is deliberately the only place a table, a DSN or a
session maker appears anywhere in this repository.

Two pieces matter.

``SecurityPricesTx`` mirrors the table the old example wrote to
directly. The column names are exactly what ``Candle.toRecord()``
produces, apart from three the host names differently, which the
ingestion endpoint renames in one dict comprehension.

``PostgresTokenStore`` is the production answer to ``FileTokenStore``.
It is roughly thirty lines because ``BaseTokenStore`` is three
synchronous methods, and it removes both problems the file store has:
multi worker uvicorn races, and POSIX file modes that mean nothing on
Windows.

:NOTE: This module is illustrative and is not imported by the library or
    exercised by its test suite. It needs ``sqlalchemy`` and a database,
    neither of which :mod:`algotraders` depends on.
"""

import json
import datetime as dt

from typing import Any, Dict, Optional

import sqlalchemy as sa

from sqlalchemy.dialects.postgresql import ENUM as PostgresEnum
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from algotraders.auth import BaseTokenStore
from algotraders.models import TokenBundle

SCHEMA = "private"

# ! create_type = False: the enum already exists in the database and
# SQLAlchemy must not try to create it again. Its members are the same
# strings as the Timeframe enum, which is why a candle can be inserted
# without translating the value
TIMEFRAME_ENUM = PostgresEnum(
    "1m", "3m", "5m", "15m", "1h", "1D", "1M", "1Y",
    name = "timeframe_value", schema = SCHEMA, create_type = False
)


class Base(DeclarativeBase):
    """
    Declarative base for the host's own models.
    """

    pass


class SecurityPricesTx(Base):
    """
    Historical candle storage.

    The unique constraint is what makes ingestion idempotent. The old
    script used ``if_exists = "append"``, which silently duplicated
    every row on any re-run, and a backfill is re-run constantly.
    """

    __tablename__ = "security_prices_tx"
    __table_args__ = (
        sa.UniqueConstraint(
            "exchange_symbol_ext", "effective_time", "timeframe",
            "ticker_data_source_id", name = "security_prices_tx_natural_key"
        ),
        {"schema" : SCHEMA}
    )

    id = sa.Column(sa.BigInteger, primary_key = True, autoincrement = True)

    exchange_symbol_ext = sa.Column(sa.Text, nullable = False, index = True)
    effective_time = sa.Column(
        sa.DateTime(timezone = True), nullable = False, index = True
    )
    timeframe = sa.Column(TIMEFRAME_ENUM, nullable = False)
    ticker_data_source_id = sa.Column(sa.Text, nullable = False)

    open_price = sa.Column(sa.Numeric(18, 4), nullable = False)
    high_price = sa.Column(sa.Numeric(18, 4), nullable = False)
    low_price = sa.Column(sa.Numeric(18, 4), nullable = False)
    close_price = sa.Column(sa.Numeric(18, 4), nullable = False)
    volume = sa.Column(sa.BigInteger, nullable = False)
    open_interest = sa.Column(sa.BigInteger, nullable = True)


class BrokerSessionToken(Base):
    """
    Table backing :class:`PostgresTokenStore`.

    :NOTE: These rows are live bearer credentials. Restrict the table to
        the application role, and prefer a column level encryption
        extension or a dedicated secret manager if the threat model
        includes a database dump.
    """

    __tablename__ = "broker_session_tokens"
    __table_args__ = (
        sa.UniqueConstraint(
            "broker_name", "client_id", name = "broker_session_natural_key"
        ),
        {"schema" : SCHEMA}
    )

    id = sa.Column(sa.BigInteger, primary_key = True, autoincrement = True)

    broker_name = sa.Column(sa.Text, nullable = False)
    client_id = sa.Column(sa.Text, nullable = False)
    payload = sa.Column(sa.JSON, nullable = False)
    expires_at = sa.Column(sa.DateTime(timezone = True), nullable = True)
    updated_at = sa.Column(
        sa.DateTime(timezone = True), nullable = False,
        server_default = sa.func.now()
    )


class PostgresTokenStore(BaseTokenStore):
    """
    Production token store backed by the host's own database.

    Replaces ``FileTokenStore``, which is a development convenience: it
    is split brain under multi worker uvicorn, where each worker
    refreshes independently and they race on the write, and its 0600
    file mode does nothing on Windows.

    A single row per broker and client, updated in place, gives every
    worker one view and makes the refresh naturally serialisable with a
    row lock if that is ever needed.

    :type  sessionFactory: sessionmaker
    :param sessionFactory: Synchronous session factory. Synchronous
        because :class:`BaseTokenStore` is a synchronous interface: a
        token is read once per session rather than per request, so an
        async interface would buy nothing and force every implementer
        into an event loop.
    """

    def __init__(self, sessionFactory : sessionmaker) -> None:
        self.sessionFactory = sessionFactory


    def read(
        self, brokerName : str, clientId : str
    ) -> Optional[TokenBundle]:
        """
        Load the stored bundle for one broker session.
        """

        with self.sessionFactory() as session:
            row = session.execute(
                sa.select(BrokerSessionToken).where(
                    BrokerSessionToken.broker_name == brokerName,
                    BrokerSessionToken.client_id == clientId
                )
            ).scalar_one_or_none()

        if row is None:
            return None

        try:
            return TokenBundle.fromStorage(row.payload)
        except Exception:
            # ? a payload written by an older library version is treated
            # as absent, which forces a clean login rather than failing
            # the morning start-up
            return None


    def write(self, bundle : TokenBundle) -> None:
        """
        Insert or update the bundle, keyed on broker and client.
        """

        from sqlalchemy.dialects.postgresql import insert as postgresInsert

        record = bundle.toStorage()

        statement = postgresInsert(BrokerSessionToken).values(
            broker_name = bundle.brokerName,
            client_id = bundle.clientId,
            payload = record,
            expires_at = bundle.expiresAt
        ).on_conflict_do_update(
            constraint = "broker_session_natural_key",
            set_ = {
                "payload" : record,
                "expires_at" : bundle.expiresAt,
                "updated_at" : sa.func.now()
            }
        )

        with self.sessionFactory() as session:
            session.execute(statement)
            session.commit()


    def delete(self, brokerName : str, clientId : str) -> None:
        """
        Remove a stored bundle, ignoring a missing row.
        """

        with self.sessionFactory() as session:
            session.execute(
                sa.delete(BrokerSessionToken).where(
                    BrokerSessionToken.broker_name == brokerName,
                    BrokerSessionToken.client_id == clientId
                )
            )
            session.commit()


def toPriceRow(candle : Any) -> Dict[str, Any]:
    """
    Rename a candle record into the host's column names.

    Five of the eight columns already match what ``Candle.toRecord()``
    produces. Only three differ, and they are renamed here rather than
    in the library, because the library has no business knowing this
    schema exists.

    :type  candle: Any
    :param candle: A :class:`~algotraders.models.market.Candle`.

    :rtype:   Dict[str, Any]
    :returns: A row ready for an insert.
    """

    record = candle.toRecord()

    return {
        "exchange_symbol_ext" : record.pop("instrument_key"),
        "effective_time" : record.pop("timestamp"),
        "ticker_data_source_id" : record.pop("source").upper(),
        **record
    }


__all__ = [
    "Base", "SecurityPricesTx", "BrokerSessionToken", "PostgresTokenStore",
    "toPriceRow", "TIMEFRAME_ENUM", "SCHEMA"
]
