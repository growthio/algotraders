# -*- encoding: utf-8 -*-

"""
Core Module for Algorithim Strategies Implementation
----------------------------------------------------

The backbone of succesful algorithimic based trading depends on data
backed and historically backtested strategies which is the core of any
automatic trading setup. All rules and functionalities are to be
developed and publically exposed using this submodule.

:NOTE: This submodule is a deliberately empty extension point. Strategy
    logic belongs either to the host application or to a package built
    on top of :mod:`algotraders`, because a strategy needs capital, risk
    and portfolio context that a broker integration library has no
    business owning.
"""
