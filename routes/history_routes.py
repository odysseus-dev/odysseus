"""Backward-compat shim — canonical location is routes/history/history_routes.py.

This module is replaced in ``sys.modules`` by the canonical module object so
that ``import routes.history_routes``, ``from routes.history_routes import X``,
``importlib.import_module("routes.history_routes")``, and the
``import ... as history_routes`` + ``monkeypatch.setattr(history_routes, ...)``
pattern used by test_history_compact_tool_calls.py / test_fork_session_metadata.py
all operate on the *same* object the application actually uses. Keeps existing
import paths working after slice 2d (#4082/#4071). Source-introspection tests
read the canonical file by path.
"""

import sys as _sys

from routes.history import history_routes as _canonical  # noqa: F401

_sys.modules[__name__] = _canonical
