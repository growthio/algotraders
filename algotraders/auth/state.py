# -*- encoding: utf-8 -*-

"""
Single Use Login State for the OAuth Redirect Flow
-----------------------------------------------------

The ``state`` parameter is the only defence a redirect flow has against
an attacker completing a login on somebody else's behalf. Both broker
authentication classes accepted a ``state`` and then discarded it, and
nothing was ever stored to compare a callback against, so the parameter
was decorative.

**The attack that closes.** An attacker begins a login against the
host's own ``client_id``, authenticates as *themselves*, and captures
the ``auth_code`` the broker hands back. They then fire that code at the
victim host's callback route - a ``GET`` in a typical integration, so an
``<img>`` tag in an email is enough. The host exchanges the code, binds
its session to the **attacker's** broker account and writes the
attacker's token to its store. Every order the victim places from that
point trades the attacker's book, and every position the victim reads is
the attacker's.

A state value that is minted server-side, stored, and required to come
back unchanged makes the forged callback fail: the attacker never saw
the value the victim's session issued.

:NOTE: :class:`MemoryLoginStateStore` is per process. Under multi worker
    uvicorn a callback can land on a different worker than the one that
    minted the state, which fails closed - the login is refused, not
    accepted. That is the correct direction to fail, but a multi worker
    host should supply a shared implementation over Redis or its own
    database; the interface is three methods.
"""

import secrets
import datetime as dt

from abc import ABC, abstractmethod
from typing import Dict, Optional

from algotraders.core.clock import BaseClock, SystemClock
from algotraders.logger import getLogger

logger = getLogger(__name__)

# ? 32 bytes of CSPRNG rendered url-safe. Long enough that guessing is
# not a strategy, short enough to sit in a query string comfortably
STATE_BYTES = 32

DEFAULT_STATE_TTL = dt.timedelta(minutes = 10)


class BaseLoginStateStore(ABC):
    """
    Mints and validates the one-time ``state`` value in a login flow.

    Three methods, so a host can implement one over Redis or a database
    table when a single process is not enough.
    """

    @abstractmethod
    def mint(self, brokerName : str) -> str:
        """
        Create and record a fresh state value.

        :rtype:   str
        :returns: An unguessable value to hand to the broker.
        """

        pass


    @abstractmethod
    def consume(self, brokerName : str, state : Optional[str]) -> bool:
        """
        Validate a returned state and burn it.

        Must be single use: a state that has already been redeemed is
        rejected, otherwise a captured callback URL could be replayed.

        :rtype:   bool
        :returns: ``True`` only when the value was outstanding for this
            broker and had not expired.
        """

        pass


    @abstractmethod
    def discard(self, brokerName : str) -> None:
        """
        Drop every outstanding state for a broker, used on logout.
        """

        pass


class MemoryLoginStateStore(BaseLoginStateStore):
    """
    In-process login state, with a time to live.

    :type  ttl: dt.timedelta
    :param ttl: How long a minted state stays redeemable. Short on
        purpose: the window only has to cover a human completing a
        browser login, and a long window is a long replay opportunity.

    :type  clock: BaseClock
    :param clock: Time source, so expiry is testable.
    """

    def __init__(
        self, ttl : dt.timedelta = DEFAULT_STATE_TTL,
        clock : Optional[BaseClock] = None
    ) -> None:
        self.ttl = ttl
        self.clock = clock or SystemClock()

        self.outstanding : Dict[str, dt.datetime] = {}


    def mint(self, brokerName : str) -> str:
        """
        Create a CSPRNG state value and record its expiry.
        """

        self.__evict__()

        state = secrets.token_urlsafe(STATE_BYTES)
        self.outstanding[f"{brokerName}:{state}"] = self.clock.now() + self.ttl

        return state


    def consume(self, brokerName : str, state : Optional[str]) -> bool:
        """
        Validate and burn a returned state value.
        """

        if not state:
            return False

        self.__evict__()

        # ! pop, not read. A state that survives redemption can be
        # replayed from a captured callback URL
        expiry = self.outstanding.pop(f"{brokerName}:{state}", None)

        if expiry is None:
            logger.warning(
                "rejected a %s login callback carrying an unknown or "
                "already-redeemed state", brokerName
            )

            return False

        return self.clock.now() < expiry


    def discard(self, brokerName : str) -> None:
        """
        Drop every outstanding state for one broker.
        """

        prefix = f"{brokerName}:"

        for key in [
            entry for entry in self.outstanding if entry.startswith(prefix)
        ]:
            self.outstanding.pop(key, None)


    def __evict__(self) -> None:
        """
        Remove expired entries so the mapping cannot grow without bound.

        A login endpoint is reachable before authentication in most
        hosts, so an unbounded store here would be a memory sink for
        anyone willing to call it in a loop.
        """

        now = self.clock.now()

        for key in [
            entry for entry, expiry in self.outstanding.items() if expiry <= now
        ]:
            self.outstanding.pop(key, None)


    def __repr__(self) -> str:
        return f"MemoryLoginStateStore(outstanding={len(self.outstanding)})"


__all__ = [
    "BaseLoginStateStore", "MemoryLoginStateStore",
    "STATE_BYTES", "DEFAULT_STATE_TTL"
]
