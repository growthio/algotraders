# -*- encoding: utf-8 -*-

"""
Custom Error Definition for the AlgoTraders Module
"""

class BrokerError(Exception):
    """
    A base error related to any broker services, for example when a
    broker is not registered or proper libraries are not available in
    the system. Any broker related custom error should be inherited
    from this base error class.
    """

    pass


class UnavailableBroker(BrokerError):
    """
    Raises an exception when the requested broker is not available in
    the system and a user may request one using GH issues.
    """

    pass


class BrokerConfigurationError(BrokerError):
    """
    Raises when the broker is available but proper configuration is
    not available in the system - for example required library is not
    present.
    """

    def __init__(self, name : str, requirements : list) -> None:
        super().__init__(
            f"Broker {name} is available, but {requirements} not met."
        )


class NotConnectedError(RuntimeError):
    """
    The error is raised when a script tries to execute a function that
    requires a login which was not called or logout method was called.
    """

    pass
