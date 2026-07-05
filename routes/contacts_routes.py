"""Backward-compat shim — canonical location is routes/contacts/contacts_routes.py.

This module is replaced in ``sys.modules`` by the canonical module object so
that ``import routes.contacts_routes``, ``from routes.contacts_routes import X``,
``importlib.import_module("routes.contacts_routes")``, and the string-targeted
``monkeypatch.setattr("routes.contacts_routes.SETTINGS_FILE", ...)`` pattern
used by test_carddav_password_encryption.py / test_contacts_carddav_security.py
— plus the ``import ... as contacts_routes`` + ``setattr(...)`` pattern in
test_contacts_add_null_name.py — all operate on the *same* object the
application actually uses. This also keeps ``_contact_cache`` (mutable module
state) identical across import paths. Keeps existing import paths working
after slice 2e (#4082/#4071). No source-introspection tests read this file
by path.
"""

import sys as _sys

from routes.contacts import contacts_routes as _canonical  # noqa: F401

_sys.modules[__name__] = _canonical
