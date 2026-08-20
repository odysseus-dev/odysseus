"""Backward-compat shim — canonical location is routes/session/session_routes.py.

This module is replaced in ``sys.modules`` by the canonical module object so
that ``import routes.session_routes``, ``from routes.session_routes import
X``, ``importlib.import_module("routes.session_routes")``, the
``from routes import session_routes`` parent-attribute form, the
``import ... as sr`` + ``monkeypatch.setattr(sr, "SessionLocal", ...)``
pattern, and the ``session_routes.router`` replacement pattern used by
test_history_compact_tool_calls.py / test_history_display_model_hydration.py
all operate on the *same* object the application actually uses. This also
keeps ``inspect.getsource`` (test_session_routes_utcnow.py) reading the
canonical source. Keeps existing import paths working after slice 2q
(#4082/#4071). Source-introspection tests read the canonical file by path.
"""

import sys as _sys

from routes.session import session_routes as _canonical  # noqa: F401

_sys.modules[__name__] = _canonical
