"""Backward-compat shim — canonical location is routes/contacts/contacts_routes.py.

This module is replaced in ``sys.modules`` by the canonical module object so
that ``import routes.contacts_routes``, ``from routes.contacts_routes import X``,
``importlib.import_module("routes.contacts_routes")``, and string-targeted
monkeypatches all operate on the same object the application actually uses.
"""

import sys as _sys

from routes.contacts import contacts_routes as _canonical  # noqa: F401

_sys.modules[__name__] = _canonical
