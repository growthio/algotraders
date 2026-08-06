# -*- encoding: utf-8 -*-

"""
Optional HTTP Transport for the AlgoTraders Library
=====================================================

The library is consumed in process by a host application, so an HTTP
surface is a convenience rather than the product. This package holds it,
and nothing here is imported unless a caller reaches for it.

:NOTE: This module imports **no** web framework. ``fastapi`` is imported
    only inside :mod:`algotraders.transport.fastapi`, so a deployment
    that mounts nothing never needs the ``server`` extra installed, and
    ``import algotraders`` stays free of it.
"""

__all__ = ["fastapi"]
