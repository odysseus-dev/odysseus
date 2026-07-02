"""Backward-compat shim — canonical location is routes/memory/memory_routes.py.

This module is replaced in ``sys.modules`` by the canonical module object so
that ``import routes.memory_routes``, ``from routes.memory_routes import X``,
``importlib.import_module("routes.memory_routes")``, and
``monkeypatch.setattr(routes.memory_routes, "ATTR", ...)`` (used by
test_memory_routes_session_owner.py and test_memory_owner_isolation.py via
``import ... as mr`` + ``setattr(mr, ...)``) all operate on the *same* object
the application actually uses. Keeps existing import paths working after
slice 2c (#4082/#4071). Source-introspection tests read the canonical file
by path.
"""

import sys as _sys

from routes.memory import memory_routes as _canonical  # noqa: F401

_sys.modules[__name__] = _canonical
