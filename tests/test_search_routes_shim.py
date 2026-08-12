"""Regression test for the search route shim (slice 2j, #4082/#4071)."""

import importlib

import routes.search_routes as _shim_search  # noqa: F401


def test_legacy_and_canonical_search_module_are_same_object():
    legacy = importlib.import_module("routes.search_routes")
    canonical = importlib.import_module("routes.search.search_routes")
    assert legacy is canonical
