# -*- encoding: utf-8 -*-

"""
Broker Authentication, Token Persistence and Session Assembly
==============================================================

Three pieces live here. :class:`BaseTokenStore` and its two shipped
implementations persist the day's session token. :class:`BrokerSession`
is the handle a host application holds. :func:`initialize` assembles one
without ever prompting for input, which is what makes the library usable
from a web backend at all.

See :mod:`algotraders.auth.session` for the resolution order and for how
an interactive login is handed back to the host as a redirect rather
than being attempted in process.
"""

from algotraders.auth.store import (
    BaseTokenStore, MemoryTokenStore, FileTokenStore,
    hashClientId, TOKEN_FILE_SUFFIX, CLIENT_HASH_LENGTH
)

from algotraders.auth.state import (
    BaseLoginStateStore, MemoryLoginStateStore,
    STATE_BYTES, DEFAULT_STATE_TTL
)

from algotraders.auth.session import (
    BrokerSession, initialize, initializeSync
)

__all__ = [
    "BaseTokenStore", "MemoryTokenStore", "FileTokenStore",
    "hashClientId", "TOKEN_FILE_SUFFIX", "CLIENT_HASH_LENGTH",
    "BaseLoginStateStore", "MemoryLoginStateStore",
    "STATE_BYTES", "DEFAULT_STATE_TTL",
    "BrokerSession", "initialize", "initializeSync"
]
