"""Backward-compat shim — canonical location is routes/search/search_routes.py.

This module is replaced in ``sys.modules`` by the canonical module object so
that ``import routes.search_routes`` and ``from routes.search_routes import X``
keep resolving to the canonical module. Keeps existing import paths working
after slice 2j (#4082/#4071).
"""

import sys as _sys

from routes.search import search_routes as _canonical  # noqa: F401

_sys.modules[__name__] = _canonical
