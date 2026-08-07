# -*- encoding: utf-8 -*-

"""
Broker Neutral Instrument Identity
------------------------------------

The two supported brokers do not agree on how to name a tradable thing,
and the disagreement is not cosmetic. Fyers takes a formatted string,
``NSE:SBIN-EQ``. Kite takes an opaque integer ``instrument_token`` that
has to be looked up in a downloaded dump, and it reissues those tokens
for every derivative expiry.

A library that claims to be broker agnostic therefore cannot pass
symbols around as strings. :class:`InstrumentKey` is the canonical
identity: it describes *what the contract is* in exchange terms, with no
vendor encoding at all. :class:`Instrument` pairs that identity with
whatever the current broker needs in order to trade it.

Requests are made against a resolved :class:`Instrument`, never a raw
string, which is what turns an unknown symbol into a local
``InstrumentNotFound`` instead of a rate limited round trip that comes
back with the broker's own rejection.

**Example Usage**

The canonical string form round trips, so it can be used as a cache key,
a URL path segment or a database column.

.. code-block:: python

    key = InstrumentKey.parse("NFO:NIFTY:OPT:2026-01-27:24800:CE")

    key.instrumentType   # InstrumentType.OPTION
    key.strike           # Decimal('24800')
    str(key)             # 'NFO:NIFTY:OPT:2026-01-27:24800:CE'
"""

import decimal
import datetime as dt

from typing import Any, Dict, Optional

from pydantic import Field, field_validator, model_validator

from algotraders.errors import InvalidInstrumentKey
from algotraders.models.base import AlgoTradersModel
from algotraders.models.enums import (
    DERIVATIVE_EXCHANGES, Exchange, InstrumentType, OptionRight, Segment
)

KEY_SEPARATOR = ":"


class InstrumentKey(AlgoTradersModel):
    """
    Exchange level identity of a tradable contract.

    Deliberately carries nothing vendor specific. Two brokers looking at
    the same NIFTY option produce the same key, which is what makes the
    key usable as a stable database identifier and as the join column
    between price history sourced from different brokers.

    :type  exchange: Exchange
    :param exchange: Venue the contract trades on.

    :type  tradingSymbol: str
    :param tradingSymbol: Exchange trading symbol, uppercased. For a
        derivative this is the underlying, ``NIFTY`` rather than the
        full contract name, because expiry and strike are separate
        fields here.

    :type  instrumentType: InstrumentType
    :param instrumentType: Contract kind.

    :type  expiry: Optional[dt.date]
    :param expiry: Contract expiry. Required for futures and options,
        forbidden otherwise.

    :type  strike: Optional[decimal.Decimal]
    :param strike: Option strike price. ``Decimal`` rather than
        ``float`` so that a strike is compared and rendered exactly;
        ``24800.5`` as a float would not reliably round trip through a
        cache key.

    :type  optionRight: Optional[OptionRight]
    :param optionRight: Call or put. Required for options, forbidden
        otherwise.
    """

    exchange : Exchange
    tradingSymbol : str = Field(min_length = 1, max_length = 64)
    instrumentType : InstrumentType = InstrumentType.EQUITY
    expiry : Optional[dt.date] = None
    strike : Optional[decimal.Decimal] = None
    optionRight : Optional[OptionRight] = None


    @field_validator("tradingSymbol")
    @classmethod
    def normaliseSymbol(cls, value : str) -> str:
        """
        Uppercase the trading symbol and reject embedded separators.

        A colon would silently corrupt the canonical string form, so it
        is refused at construction rather than producing a key that
        cannot be parsed back.
        """

        symbol = value.strip().upper()

        if KEY_SEPARATOR in symbol:
            raise ValueError(
                f"trading symbol may not contain {KEY_SEPARATOR!r}: {value!r}"
            )

        # ! re-checked after upper(). The max_length constraint is a
        # core-schema check that runs BEFORE this validator, and case
        # folding can lengthen a string - "ﬁ".upper() is "FI" - so 64
        # ligature characters passed the constraint and were stored as
        # 128
        if len(symbol) > 64:
            raise ValueError(
                f"trading symbol is {len(symbol)} characters after "
                f"normalisation, which exceeds the 64 character limit"
            )

        return symbol


    @model_validator(mode = "after")
    def validateContractShape(self) -> "InstrumentKey":
        """
        Enforce that the optional fields match the instrument type.

        An option without a strike, or an equity carrying an expiry, is
        a malformed identity. Catching it here means the resolver and
        every mapper downstream can assume the combination is coherent.

        :raises ValueError: The field combination is impossible.
        """

        if self.instrumentType is InstrumentType.OPTION:
            if self.expiry is None or self.strike is None:
                raise ValueError("an option key needs both expiry and strike")

            if self.optionRight is None:
                raise ValueError("an option key needs a call or put right")

        elif self.instrumentType is InstrumentType.FUTURE:
            if self.expiry is None:
                raise ValueError("a futures key needs an expiry")

            if self.strike is not None or self.optionRight is not None:
                raise ValueError("a futures key carries no strike or right")

        else:
            if self.expiry is not None:
                raise ValueError(
                    f"{self.instrumentType.value} instruments do not expire"
                )

            if self.strike is not None or self.optionRight is not None:
                raise ValueError(
                    f"{self.instrumentType.value} instruments have no strike"
                )

        return self


    @property
    def segment(self) -> Segment:
        """
        Market segment this contract belongs to.

        Derived rather than stored, because it is a function of the
        exchange and always agrees with it. The resolver uses it to pick
        which instrument master file to download.
        """

        # ? commodity and currency are tested first because they are
        # also members of DERIVATIVE_EXCHANGES; ordering is what makes
        # the more specific answer win, and a test pins each mapping
        if self.exchange in (Exchange.MCX, Exchange.NCDEX):
            return Segment.COMMODITY

        if self.exchange in (Exchange.CDS, Exchange.BCD):
            return Segment.CURRENCY

        if self.exchange in DERIVATIVE_EXCHANGES:
            return Segment.DERIVATIVE

        return Segment.CAPITAL


    @property
    def isDerivative(self) -> bool:
        """
        Whether this contract expires, and therefore whether its cached
        broker token has a limited lifetime.
        """

        return self.instrumentType in (
            InstrumentType.FUTURE, InstrumentType.OPTION
        )


    def __str__(self) -> str:
        """
        Render the canonical colon separated form.

        The grammar is
        ``<exchange>:<symbol>:<type>[:<expiry>[:<strike>:<right>]]``,
        with trailing components present only when the instrument type
        requires them.
        """

        parts = [
            self.exchange.value, self.tradingSymbol, self.instrumentType.value
        ]

        if self.expiry is not None:
            parts.append(self.expiry.isoformat())

        if self.strike is not None and self.optionRight is not None:
            parts.append(_renderStrike(self.strike))
            parts.append(self.optionRight.value)

        return KEY_SEPARATOR.join(parts)


    @classmethod
    def parse(cls, value : Any) -> "InstrumentKey":
        """
        Build a key from its canonical string form.

        Accepts an existing :class:`InstrumentKey` unchanged, so callers
        that take ``Union[str, InstrumentKey]`` can normalise with a
        single call.

        :type  value: Any
        :param value: Canonical string, or an existing key.

        :raises ValueError: The string does not match the grammar.

        :rtype:   InstrumentKey
        :returns: The parsed key.
        """

        if isinstance(value, cls):
            return value

        parts = str(value).split(KEY_SEPARATOR)

        # ! arity is checked before anything is parsed, so a wrong shape
        # reports the component count rather than an unrelated date error
        if len(parts) not in (3, 4, 6):
            raise InvalidInstrumentKey(
                f"instrument key has {len(parts)} components, expected "
                f"3, 4 or 6: {value!r}"
            )

        fields : Dict[str, Any] = {
            "exchange" : parts[0].strip().upper(),
            "tradingSymbol" : parts[1],
            "instrumentType" : parts[2].strip().upper()
        }

        # ! every parse failure below becomes InvalidInstrumentKey. These
        # strings arrive in request bodies, and Decimal raises
        # InvalidOperation - an ArithmeticError, not a ValueError - which
        # matched no exception handler and surfaced as a 500 with a stack
        # trace instead of a 400
        try:
            if len(parts) >= 4:
                fields["expiry"] = dt.date.fromisoformat(parts[3].strip())

            if len(parts) == 6:
                fields["strike"] = _parseStrike(parts[4].strip())
                fields["optionRight"] = parts[5].strip().upper()

            return cls(**fields)
        except InvalidInstrumentKey:
            raise
        except Exception as error:
            raise InvalidInstrumentKey(
                f"could not parse instrument key {value!r}: {error}"
            ) from error


def _parseStrike(text : str) -> decimal.Decimal:
    """
    Parse a strike, rejecting the values that break an exact key.

    ``NaN`` never compares equal to itself, so a key containing one can
    never be looked up again. Infinity and a value beyond the decimal
    context's precision both explode later during rendering rather than
    here, which is a far worse place to find out.

    :raises InvalidInstrumentKey: The strike is not a usable finite
        number.

    :rtype:   decimal.Decimal
    :returns: The parsed strike.
    """

    try:
        strike = decimal.Decimal(text)
    except (decimal.InvalidOperation, ValueError, TypeError) as error:
        raise InvalidInstrumentKey(
            f"strike {text!r} is not a number"
        ) from error

    if not strike.is_finite():
        raise InvalidInstrumentKey(f"strike {text!r} is not finite")

    if strike <= 0:
        raise InvalidInstrumentKey(f"strike {text!r} must be positive")

    return strike


def _renderStrike(strike : decimal.Decimal) -> str:
    """
    Render a strike without a trailing ``.0`` so that ``24800`` and
    ``Decimal('24800.00')`` produce the same canonical key.

    :rtype:   str
    :returns: The shortest exact rendering of the strike.
    """

    # ! neither normalize() nor quantize() is safe here. quantize()
    # raises InvalidOperation once the significand exceeds the context
    # precision, and normalize() silently ROUNDS to it, so a strike with
    # more digits than the context allows came back altered and the key
    # no longer round tripped.
    #
    # Formatting with "f" expands any exponent without consulting the
    # context at all, so trailing zeros are stripped textually instead
    text = format(strike, "f")

    if "." in text:
        text = text.rstrip("0").rstrip(".")

    return text


class Instrument(AlgoTradersModel):
    """
    A resolved contract, ready to trade with one specific broker.

    Pairs the broker neutral :class:`InstrumentKey` with the vendor
    encoding needed to actually call the API, plus the exchange metadata
    that order validation depends on.

    :type  key: InstrumentKey
    :param key: Canonical identity.

    :type  brokerSymbol: str
    :param brokerSymbol: Vendor formatted symbol, for example
        ``NSE:SBIN-EQ`` for Fyers.

    :type  brokerToken: Optional[str]
    :param brokerToken: Vendor numeric identifier, held as a string so
        that brokers using non numeric handles fit the same field. Kite
        historical requests are keyed on this and not on any symbol.

    :type  lotSize: Optional[int]
    :param lotSize: Exchange lot size. Order quantity must be a multiple
        of it, which ``OrderRequest`` enforces when the value is known.

    :type  tickSize: Optional[decimal.Decimal]
    :param tickSize: Minimum price increment.

    :type  source: str
    :param source: Broker the resolution came from. Mandatory, because
        ``brokerSymbol`` and ``brokerToken`` are meaningless without it.
    """

    key : InstrumentKey
    brokerSymbol : str = Field(min_length = 1)
    brokerToken : Optional[str] = None
    name : Optional[str] = None
    lotSize : Optional[int] = Field(default = None, gt = 0)
    tickSize : Optional[decimal.Decimal] = Field(default = None, gt = 0)
    isin : Optional[str] = None
    source : str = Field(min_length = 1)


    @property
    def instrumentKey(self) -> str:
        """
        Canonical key in string form, which is what every price and
        order record stores rather than the nested object.
        """

        return str(self.key)


    def __str__(self) -> str:
        return f"{self.instrumentKey} ({self.source}:{self.brokerSymbol})"


__all__ = ["InstrumentKey", "Instrument", "KEY_SEPARATOR"]
