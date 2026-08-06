# -*- encoding: utf-8 -*-

"""
A Scripted Stand-In for a Vendor SDK Client
---------------------------------------------

Broker API classes call methods on an SDK object. Substituting one that
returns recorded payloads is what lets the mapper and chunking logic be
tested against real vendor response shapes without a network or a
credential.

Responses are supplied as a queue rather than a single value, so a test
can assert the exact number of calls a request produced. That matters
for chunking: the whole point of splitting a two year range is that it
becomes a specific number of broker calls, and a fake that answers
identically forever cannot prove it.
"""

import json
import pathlib

from typing import Any, Callable, Dict, List, Optional, Sequence, Union

DATA_DIRECTORY = pathlib.Path(__file__).resolve().parent.parent / "data"


def loadPayload(name : str) -> Any:
    """
    Read a recorded vendor payload from ``tests/data``.

    Recorded payloads are used in preference to hand written dicts so a
    mapper test fails when the vendor response shape drifts.

    :type  name: str
    :param name: File name, with or without the ``.json`` suffix.

    :rtype:   Any
    :returns: The parsed payload.
    """

    filename = name if name.endswith(".json") else f"{name}.json"

    return json.loads(
        (DATA_DIRECTORY / filename).read_text(encoding = "utf-8")
    )


class FakeSessionManager:
    """
    Records every call and replays scripted responses.

    :type  responses: Optional[Dict[str, Any]]
    :param responses: Method name to response. A list is treated as a
        queue consumed one entry per call, with the last entry repeating
        once exhausted. An ``Exception`` instance is raised rather than
        returned, which is how a transient failure is simulated.
    """

    def __init__(
        self, responses : Optional[Dict[str, Any]] = None
    ) -> None:
        self.responses : Dict[str, Any] = dict(responses or {})
        self.calls : List[Dict[str, Any]] = []


    def callsTo(self, method : str) -> List[Dict[str, Any]]:
        """
        Every recorded call to one method, in order.

        :rtype:   List[Dict[str, Any]]
        :returns: The recorded keyword arguments per call.
        """

        return [
            entry["kwargs"] for entry in self.calls
            if entry["method"] == method
        ]


    def countOf(self, method : str) -> int:
        """
        How many times a method was called.
        """

        return len(self.callsTo(method))


    def __record__(self, method : str, **kwargs : Any) -> Any:
        """
        Record the call and return the next scripted response.
        """

        index = self.countOf(method)
        self.calls.append({"method" : method, "kwargs" : kwargs})

        scripted = self.responses.get(method)

        if scripted is None:
            return {"s" : "ok"}

        if isinstance(scripted, (list, tuple)):
            entry = scripted[min(index, len(scripted) - 1)]
        else:
            entry = scripted

        if isinstance(entry, BaseException):
            raise entry

        if callable(entry):
            return entry(**kwargs)

        return entry


    def history(self, data : Any = None) -> Any:
        return self.__record__("history", data = data)


    def quotes(self, data : Any = None) -> Any:
        return self.__record__("quotes", data = data)


    def depth(self, data : Any = None) -> Any:
        return self.__record__("depth", data = data)


    def place_order(self, data : Any = None) -> Any:
        return self.__record__("place_order", data = data)


    def modify_order(self, data : Any = None) -> Any:
        return self.__record__("modify_order", data = data)


    def cancel_order(self, data : Any = None) -> Any:
        return self.__record__("cancel_order", data = data)


    def orderbook(self, data : Any = None) -> Any:
        return self.__record__("orderbook", data = data)


    def positions(self) -> Any:
        return self.__record__("positions")


    def holdings(self) -> Any:
        return self.__record__("holdings")


def repeating(payload : Any) -> Callable[..., Any]:
    """
    Build a response callable that ignores its arguments.

    :rtype:   Callable[..., Any]
    :returns: A callable returning the payload for every call.
    """

    def responder(**kwargs : Any) -> Any:
        return payload

    return responder


def sequence(
    payloads : Sequence[Union[Any, BaseException]]
) -> List[Any]:
    """
    Build a response queue from an explicit sequence.

    :rtype:   List[Any]
    :returns: The queue, consumed one entry per call.
    """

    return list(payloads)


__all__ = [
    "FakeSessionManager", "loadPayload", "repeating", "sequence",
    "DATA_DIRECTORY"
]
