# -*- encoding: utf-8 -*-

"""
A Module to Fetch Options Chain Data from NSE India

NSE India (https://www.nseindia.com/) provides publically available
API to get Options Chain Data for a traded symbol (index/stocks). The
module aims to fetch and parse the data to be used in other projects.

@author: Debmalya Pramanik
@copywright: 2024; Debmalya Pramanik
"""

# ? package follows https://peps.python.org/pep-0440/
# ? https://python-semver.readthedocs.io/en/latest/advanced/convert-pypi-to-semver.html
__version__ = "v0.0.1.dev0"

# ? register root of the package, this is required to import config
import os

ROOT = os.path.abspath(os.path.dirname(__file__))
CONFIG = os.path.join(ROOT, "config", "default.yaml")

# init-time options registrations
from nseoptions import processing # noqa: F401, F403
from nseoptions.core import NSEOptionChain # noqa: F401, F403
