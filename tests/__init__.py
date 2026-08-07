# -*- encoding: utf-8 -*-

"""
Test Suite for the AlgoTraders Broker Integration Library
==========================================================

The suite is organised into ``unit`` (one module under test per file,
no I/O of any kind) and ``integration`` (several layers wired together
against a fake broker). Recorded broker payloads live under ``data`` and
reusable doubles under ``fakes``.

Nothing here contacts a broker. See ``tests/conftest.py`` for the autouse
fixtures that enforce that.
"""
