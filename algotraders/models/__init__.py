# -*- encoding: utf-8 -*-

"""
Normalized Data Contract Between AlgoTraders and Its Host
==========================================================

Every model here is a plain pydantic v2 object with no broker knowledge,
so it can be used directly as a FastAPI ``response_model`` and dumped
straight into a SQLAlchemy insert.

Field names are ``camelCase`` to match the surrounding codebase, and the
serialisation alias is ``snake_case``, which is what the host's
PostgreSQL columns are called. Both spellings validate, so a mapper can
feed a vendor payload in either shape::

    candle.openPrice                    # reading in Python
    candle.toRecord()                   # {"open_price": ..., ...}

This package may import :mod:`algotraders.errors` and the timezone
helpers from :mod:`algotraders.core.clock`, and nothing else from the
library. Keeping it at the bottom of the dependency graph is what allows
a host to import the contract without pulling in a broker SDK, a rate
limiter or an event loop.
"""

from algotraders.models.timezone import (
    IST, MARKET_OPEN, MARKET_CLOSE, toIST, fromEpoch, toEpoch
)

from algotraders.models.base import AlgoTradersModel, toSnakeCase

from algotraders.models.enums import (
    Timeframe, Exchange, Segment, InstrumentType, OptionRight,
    OrderSide, OrderType, ProductType, Validity, OrderStatus,
    StreamMode, EndpointClass, Capability,
    TIMEFRAME_DATABASE_VALUES, AGGREGATE_ONLY_TIMEFRAMES,
    DERIVATIVE_EXCHANGES
)

from algotraders.models.instrument import (
    InstrumentKey, Instrument, KEY_SEPARATOR
)

from algotraders.models.market import (
    Candle, Tick, Quote, DepthLevel, MarketDepth, requireAwareIST
)

from algotraders.models.orders import (
    OrderRequest, OrderResponse, Trade,
    LIMIT_PRICED_TYPES, TRIGGERED_TYPES
)

from algotraders.models.portfolio import Position, Holding, Margin

from algotraders.models.auth import (
    TokenBundle, TokenPolicy, LoginChallenge, SessionStatus
)

__all__ = [
    "IST", "MARKET_OPEN", "MARKET_CLOSE", "toIST", "fromEpoch", "toEpoch",

    "AlgoTradersModel", "toSnakeCase",

    "Timeframe", "Exchange", "Segment", "InstrumentType", "OptionRight",
    "OrderSide", "OrderType", "ProductType", "Validity", "OrderStatus",
    "StreamMode", "EndpointClass", "Capability",
    "TIMEFRAME_DATABASE_VALUES", "AGGREGATE_ONLY_TIMEFRAMES",
    "DERIVATIVE_EXCHANGES",

    "InstrumentKey", "Instrument", "KEY_SEPARATOR",

    "Candle", "Tick", "Quote", "DepthLevel", "MarketDepth",
    "requireAwareIST",

    "OrderRequest", "OrderResponse", "Trade",
    "LIMIT_PRICED_TYPES", "TRIGGERED_TYPES",

    "Position", "Holding", "Margin",

    "TokenBundle", "TokenPolicy", "LoginChallenge", "SessionStatus"
]
