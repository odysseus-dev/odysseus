"""Regression test for the admin_wipe route shim (slice 2h, #4082/#4071).

The backward-compat shim at ``routes/admin_wipe_routes.py`` uses
``sys.modules`` replacement so the legacy import path and the canonical
``routes.admin_wipe.*`` path resolve to the *same* module object. This is
required because ``test_admin_wipe_gallery.py`` does
``import routes.admin_wipe_routes`` followed by
``monkeypatch.setattr(routes.admin_wipe_routes, "SessionLocal", ...)`` and
``"require_admin"`` — for those patches to take effect at runtime, the legacy
module object and the canonical one must be identical.
"""

import importlib

import routes.admin_wipe_routes as _shim_admin_wipe  # noqa: F401


def test_legacy_and_canonical_admin_wipe_module_are_same_object():
    """``import routes.admin_wipe_routes`` must alias the canonical module."""
    legacy = importlib.import_module("routes.admin_wipe_routes")
    canonical = importlib.import_module("routes.admin_wipe.admin_wipe_routes")
    assert legacy is canonical, (
        "routes.admin_wipe_routes shim must resolve to the canonical "
        "routes.admin_wipe.admin_wipe_routes module object"
    )
