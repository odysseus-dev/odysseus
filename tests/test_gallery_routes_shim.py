"""Regression test for the gallery route shim (slice 2a, #4082/#4071).

The backward-compat shims at ``routes/gallery_routes.py`` and
``routes/gallery_helpers.py`` use ``sys.modules`` replacement so the legacy
import path and the canonical ``routes.gallery.*`` path resolve to the *same*
module object. This test pins that contract: if the shim is ever changed to a
plain ``from ... import *`` (or removed), these assertions catch it before the
monkeypatch-based gallery tests silently start patching the wrong module.
"""

import importlib

import routes.gallery_routes as _shim_routes  # noqa: F401
import routes.gallery_helpers as _shim_helpers  # noqa: F401


def test_legacy_and_canonical_route_module_are_same_object():
    """``import routes.gallery_routes`` must alias the canonical module."""
    legacy = importlib.import_module("routes.gallery_routes")
    canonical = importlib.import_module("routes.gallery.gallery_routes")
    assert legacy is canonical, (
        "routes.gallery_routes shim must resolve to the canonical "
        "routes.gallery.gallery_routes module object"
    )


def test_legacy_and_canonical_helpers_module_are_same_object():
    """``import routes.gallery_helpers`` must alias the canonical module."""
    legacy = importlib.import_module("routes.gallery_helpers")
    canonical = importlib.import_module("routes.gallery.gallery_helpers")
    assert legacy is canonical, (
        "routes.gallery_helpers shim must resolve to the canonical "
        "routes.gallery.gallery_helpers module object"
    )


def test_monkeypatch_via_legacy_path_affects_canonical(monkeypatch):
    """Patching through the legacy path must reach the canonical module.

    Several gallery tests do ``import routes.gallery_routes as gr`` followed by
    ``monkeypatch.setattr(gr, "get_current_user", ...)``. For that to take
    effect at runtime, the legacy module object and the canonical one must be
    identical.
    """
    legacy = importlib.import_module("routes.gallery_routes")
    canonical = importlib.import_module("routes.gallery.gallery_routes")

    sentinel = object()
    monkeypatch.setattr(legacy, "setup_gallery_routes", sentinel)
    assert canonical.setup_gallery_routes is sentinel, (
        "monkeypatch via legacy path did not reach the canonical module"
    )
