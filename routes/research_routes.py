"""Backward-compat shim — canonical location is routes/research/research_routes.py.

This module is replaced in ``sys.modules`` by the canonical module object so
that ``import routes.research_routes``, ``from routes.research_routes import X``,
``importlib.import_module("routes.research_routes")``, and
``monkeypatch.setattr("routes.research_routes.ATTR", ...)`` (string-targeted
patch used by ``test_research_owner_scope_routes.py``) all operate on the
*same* object the application actually uses. Keeps existing import paths
working after slice 2b (#4082/#4071). Source-introspection tests read the
canonical file by path.
"""

import sys as _sys

from routes.research import research_routes as _canonical  # noqa: F401

_sys.modules[__name__] = _canonical
