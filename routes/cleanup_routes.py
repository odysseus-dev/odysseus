"""Backward-compat shim — canonical location is routes/cleanup/cleanup_routes.py.

This module is replaced in ``sys.modules`` by the canonical module object so
that ``import routes.cleanup_routes``, ``from routes.cleanup_routes import X``,
``importlib.import_module("routes.cleanup_routes")``, and the string-targeted
``monkeypatch.setattr("routes.cleanup_routes.get_cleanup_preview", ...)`` /
``"routes.cleanup_routes.get_current_user"`` / ``"routes.cleanup_routes.
cleanup_sessions"`` pattern used by test_cleanup_owner_scope.py all operate
on the *same* object the application actually uses. Keeps existing import
paths working after slice 2g (#4082/#4071).
"""

import sys as _sys

from routes.cleanup import cleanup_routes as _canonical  # noqa: F401

_sys.modules[__name__] = _canonical
