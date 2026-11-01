"""Regression test for the vault route shim (slice 2k, #4082/#4071)."""

import importlib

import routes.vault_routes as _shim_vault  # noqa: F401


def test_legacy_and_canonical_vault_module_are_same_object():
    legacy = importlib.import_module("routes.vault_routes")
    canonical = importlib.import_module("routes.vault.vault_routes")
    assert legacy is canonical
