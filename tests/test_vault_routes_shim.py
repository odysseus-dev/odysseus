"""Regression test for the vault route shim (slice 2k, #4082/#4071).

The backward-compat shim at ``routes/vault_routes.py`` uses ``sys.modules``
replacement so the legacy import path and the canonical ``routes.vault.*``
path resolve to the *same* module object. This is required because
``test_vault_password_not_in_argv.py`` does ``import routes.vault_routes as
vr`` followed by ``monkeypatch.setattr(vr, "VAULT_FILE", ...)`` and reads
``vr.__file__`` for source introspection — for those to work correctly, the
legacy module object and the canonical one must be identical.
"""

import importlib

import routes.vault_routes as _shim_vault  # noqa: F401


def test_legacy_and_canonical_vault_module_are_same_object():
    """``import routes.vault_routes`` must alias the canonical module."""
    legacy = importlib.import_module("routes.vault_routes")
    canonical = importlib.import_module("routes.vault.vault_routes")
    assert legacy is canonical, (
        "routes.vault_routes shim must resolve to the canonical "
        "routes.vault.vault_routes module object"
    )
