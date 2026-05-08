# -*- coding: utf-8 -*-
"""
NSE Option Chain ReactJS UI Launcher Module

This module exposes the ``launch`` entry point for the NSE Option Chain
ReactJS UI. When called, ``launch`` locates the bundled React source,
verifies that Node.js / npm is available, installs dependencies on first
run if needed, selects a free port, starts the Vite development server,
and opens the default browser automatically.

The React source files (``index.html``, ``package.json``, ``src/``, etc.)
are shipped alongside this Python package and do NOT need to be installed
separately by the user. Node.js and npm must be present on the target
system's PATH for the launcher to operate.

:NOTE: This module is part of :mod:`algotraders.services`. It is not a
       network-facing service — it is purely a developer / analyst tool
       that renders live NSE Option Chain data in a local browser tab.
"""

from algotraders.services.optionsui.main import main as launch

__all__ = ["launch"]
