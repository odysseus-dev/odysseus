"""Regression test for the compare route shim (slice 2i, #4082/#4071).

The backward-compat shim at ``routes/compare_routes.py`` uses ``sys.modules``
replacement so the legacy import path and the canonical ``routes.compare.*``
path resolve to the *same* module object. This is required because
``test_endpoint_owner_scope_followup.py`` uses ``import routes.compare_routes
as cr`` followed by ``monkeypatch.setattr(cr, "SessionLocal", ...)`` /
``"_owned_endpoint_by_url"`` / ``"_owned_endpoint_by_id"`` — for those patches
to take effect at runtime, the legacy module object and the canonical one
must be identical.
"""

import importlib

import routes.compare_routes as _shim_compare  # noqa: F401


def test_legacy_and_canonical_compare_module_are_same_object():
    """``import routes.compare_routes`` must alias the canonical module."""
    legacy = importlib.import_module("routes.compare_routes")
    canonical = importlib.import_module("routes.compare.compare_routes")
    assert legacy is canonical, (
        "routes.compare_routes shim must resolve to the canonical "
        "routes.compare.compare_routes module object"
    )
