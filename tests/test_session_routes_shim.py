"""Regression test for the session route shim (slice 2q, #4082/#4071).

The backward-compat shim at ``routes/session_routes.py`` uses ``sys.modules``
replacement so the legacy import path and the canonical ``routes.session.*``
path resolve to the *same* module object. This is required because tests
replace ``session_routes.router`` with a fresh APIRouter before calling
``setup_session_routes``, patch ``SessionLocal``/``effective_user``/
``_verify_session_owner`` via setattr on the legacy module object, use the
``from routes import session_routes`` parent-attribute form, and read
``inspect.getsource`` of the module.
"""

import importlib

import routes.session_routes as _shim_session  # noqa: F401


def test_legacy_and_canonical_session_module_are_same_object():
    legacy = importlib.import_module("routes.session_routes")
    canonical = importlib.import_module("routes.session.session_routes")
    assert legacy is canonical
