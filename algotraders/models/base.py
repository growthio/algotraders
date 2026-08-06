# -*- encoding: utf-8 -*-

"""
Shared Model Configuration for Every AlgoTraders DTO
------------------------------------------------------

One base class carries the configuration every data transfer object in
this library shares, so the policy lives in a single place rather than
being restated on each model.

The important piece is the alias bridge. Field names are ``camelCase``
to match the surrounding codebase, while the serialisation alias is
``snake_case``. That gives the host application both halves of what it
needs from one object::

    candle.openPrice                    # natural to read in Python
    candle.model_dump(by_alias = True)  # {"open_price": ..., ...}

The second form drops straight into a SQLAlchemy insert against columns
named ``open_price`` and ``effective_time``, which means the library
never has to know the host's schema and the host never has to write an
adapter.

Models are frozen. A candle that arrived from a broker is a historical
fact, and letting a strategy mutate one in place is a bug factory.
``extra = "forbid"`` is equally deliberate: a vendor payload that grows
a field should fail a mapper test loudly rather than be silently
dropped.
"""

import re

from typing import Any, Dict

from pydantic import BaseModel, ConfigDict

# ? two passes so acronyms survive: "redirectURL" becomes "redirect_url"
# rather than "redirect_u_r_l", and "openPrice" becomes "open_price"
_BOUNDARY_BEFORE_WORD = re.compile(r"(.)([A-Z][a-z]+)")
_BOUNDARY_AFTER_LOWER = re.compile(r"([a-z0-9])([A-Z])")


def toSnakeCase(name : str) -> str:
    """
    Convert a ``camelCase`` field name to ``snake_case``.

    Used as the alias generator for every model, which is what lets a
    dump feed the host's PostgreSQL columns directly.

    :type  name: str
    :param name: The field name to convert.

    **Example Usage**

    .. code-block:: python

        toSnakeCase("openPrice")       # 'open_price'
        toSnakeCase("redirectURL")     # 'redirect_url'
        toSnakeCase("instrumentKey")   # 'instrument_key'

    :rtype:   str
    :returns: The snake_case equivalent.
    """

    stepped = _BOUNDARY_BEFORE_WORD.sub(r"\1_\2", name)

    return _BOUNDARY_AFTER_LOWER.sub(r"\1_\2", stepped).lower()


class AlgoTradersModel(BaseModel):
    """
    Base class for every data transfer object in the library.

    Subclasses inherit frozen instances, strict extra-field rejection
    and the camelCase to snake_case alias bridge described in the module
    docstring.
    """

    model_config = ConfigDict(
        alias_generator = toSnakeCase,

        # ? both spellings validate, so a caller can build a model in
        # Python with camelCase and a mapper can feed it a snake_case
        # vendor payload without an intermediate translation step
        populate_by_name = True,

        frozen = True,
        extra = "forbid",
        str_strip_whitespace = True,
        validate_default = True
    )


    def toRecord(self) -> Dict[str, Any]:
        """
        Render the model as a ``snake_case`` mapping ready for a
        database insert.

        Equivalent to ``model_dump(by_alias = True, mode = "json")``,
        given a name that says what it is for. Enum members become their
        string values, which is what a PostgreSQL enum column expects,
        and any field excluded from serialisation such as a raw vendor
        payload stays out.

        :rtype:   Dict[str, Any]
        :returns: Column name to value mapping.
        """

        return self.model_dump(by_alias = True, mode = "json")


__all__ = ["AlgoTradersModel", "toSnakeCase"]
