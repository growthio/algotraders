#!/usr/bin/env python

import os
import sys

from setuptools import setup
from setuptools import find_packages

sys.path.append(
    os.path.join(os.path.dirname(__file__))
)

import algotraders

setup(
    name = "algotraders",
    version = algotraders.__version__,
    description = "Unified Algorithimic Tradng using Broker's API",
    long_description = open("README.md", "r").read(),
    long_description_content_type = "text/markdown",
    url = "https://github.com/PyUtility/algotraders",
    packages = find_packages(
        exclude = ["tests*", "examples*"]
    ),
    classifiers = [
        "Development Status :: 1 - Planning",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)"
    ],
    python_requires = ">=3.12"
)
