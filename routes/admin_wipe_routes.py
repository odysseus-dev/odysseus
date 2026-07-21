"""Backward-compat shim — canonical location is routes/admin_wipe/admin_wipe_routes.py.

This module is replaced in ``sys.modules`` by the canonical module object so
that ``import routes.admin_wipe_routes``, ``from routes.admin_wipe_routes
import X``, ``importlib.import_module("routes.admin_wipe_routes")``, and the
``import ... as admin_wipe_routes`` + ``monkeypatch.setattr(admin_wipe_routes,
"SessionLocal", ...)`` / ``"require_admin"`` pattern used by
test_admin_wipe_gallery.py all operate on the *same* object the application
actually uses. Keeps existing import paths working after slice 2h
(#4082/#4071).
"""

import sys as _sys

from routes.admin_wipe import admin_wipe_routes as _canonical  # noqa: F401

_sys.modules[__name__] = _canonical
