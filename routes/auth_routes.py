"""Backward-compat shim — canonical location is routes/auth/auth_routes.py.

This module is replaced in ``sys.modules`` by the canonical module object so
that ``import routes.auth_routes``, ``from routes.auth_routes import X``,
``importlib.import_module("routes.auth_routes")``, the
``sys.modules.pop("routes.auth_routes")`` + re-import pattern in
test_auth_policy.py / test_auth_session_revocation.py, and the
``import ... as ar`` + ``monkeypatch.setattr(ar, ...)`` pattern in
test_rename_user_owner_sync.py / test_integrations_store_shape.py all operate
on the *same* object the application actually uses. Keeps existing import
paths working after slice 2n (#4082/#4071).
"""

import sys as _sys

from routes.auth import auth_routes as _canonical  # noqa: F401

_sys.modules[__name__] = _canonical
