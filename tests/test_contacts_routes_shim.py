"""Regression test for the contacts route shim (slice 2e, #4082/#4071).

The backward-compat shim at ``routes/contacts_routes.py`` uses ``sys.modules``
replacement so the legacy import path and the canonical ``routes.contacts.*``
path resolve to the *same* module object. This is required because:

* ``test_carddav_password_encryption.py`` uses string-targeted
  ``monkeypatch.setattr("routes.contacts_routes.SETTINGS_FILE", ...)`` which
  must reach the canonical module to take effect;
* ``test_contacts_add_null_name.py`` / ``test_contacts_carddav_security.py``
  use ``import routes.contacts_routes as cr`` + ``setattr(cr, ...)``;
* the module owns mutable state (``_contact_cache``) that must be shared
  across import paths.
"""

import importlib

import routes.contacts_routes as _shim_contacts  # noqa: F401


def test_legacy_and_canonical_contacts_module_are_same_object():
    """``import routes.contacts_routes`` must alias the canonical module."""
    legacy = importlib.import_module("routes.contacts_routes")
    canonical = importlib.import_module("routes.contacts.contacts_routes")
    assert legacy is canonical, (
        "routes.contacts_routes shim must resolve to the canonical "
        "routes.contacts.contacts_routes module object"
    )


def test_string_targeted_monkeypatch_reaches_canonical(monkeypatch):
    """String-targeted ``monkeypatch.setattr`` via the legacy path must reach
    the canonical module.

    ``test_carddav_password_encryption.py`` patches
    ``"routes.contacts_routes.SETTINGS_FILE"`` as a fixture setup; for that
    to take effect at runtime, the legacy module name and the canonical
    module must be identical.
    """
    canonical = importlib.import_module("routes.contacts.contacts_routes")

    sentinel = object()
    monkeypatch.setattr("routes.contacts_routes.setup_contacts_routes", sentinel)
    assert canonical.setup_contacts_routes is sentinel, (
        "string-targeted monkeypatch via legacy path did not reach the canonical module"
    )
