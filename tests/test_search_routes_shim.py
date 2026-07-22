"""Regression test for the search route shim (slice 2j, #4082/#4071).

The backward-compat shim at ``routes/search_routes.py`` uses ``sys.modules``
replacement so the legacy import path and the canonical ``routes.search.*``
path resolve to the *same* module object.
"""

import importlib

import routes.search_routes as _shim_search  # noqa: F401


def test_legacy_and_canonical_search_module_are_same_object():
    """``import routes.search_routes`` must alias the canonical module."""
    legacy = importlib.import_module("routes.search_routes")
    canonical = importlib.import_module("routes.search.search_routes")
    assert legacy is canonical, (
        "routes.search_routes shim must resolve to the canonical "
        "routes.search.search_routes module object"
    )
