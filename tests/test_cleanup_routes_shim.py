"""Regression test for the cleanup route shim (slice 2g, #4082/#4071).

The backward-compat shim at ``routes/cleanup_routes.py`` uses ``sys.modules``
replacement so the legacy import path and the canonical ``routes.cleanup.*``
path resolve to the *same* module object. This is required because
``test_cleanup_owner_scope.py`` uses string-targeted
``monkeypatch.setattr("routes.cleanup_routes.get_cleanup_preview", ...)`` and
``monkeypatch.delitem(sys.modules, "routes.cleanup_routes")`` + re-import —
for those patches to take effect at runtime, the legacy module object and
the canonical one must be identical.
"""

import importlib

import routes.cleanup_routes as _shim_cleanup  # noqa: F401


def test_legacy_and_canonical_cleanup_module_are_same_object():
    """``import routes.cleanup_routes`` must alias the canonical module."""
    legacy = importlib.import_module("routes.cleanup_routes")
    canonical = importlib.import_module("routes.cleanup.cleanup_routes")
    assert legacy is canonical, (
        "routes.cleanup_routes shim must resolve to the canonical "
        "routes.cleanup.cleanup_routes module object"
    )
