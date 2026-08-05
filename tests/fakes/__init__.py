# -*- encoding: utf-8 -*-

"""
Reusable Test Doubles for the AlgoTraders Suite
------------------------------------------------

Fakes live here rather than inside individual test modules so that the
behaviour they encode is written once and shared. A fake that drifts
from the interface it stands in for is worse than no fake at all, so
each one subclasses the real abstract base and is therefore caught by
``TypeError`` the moment an abstract method is added upstream.
"""

from tests.fakes.clock import FakeClock

__all__ = ["FakeClock"]
