"""Backward-compat shim — canonical location is routes/webhook/webhook_routes.py.

This module is replaced in ``sys.modules`` by the canonical module object so
that ``import routes.webhook_routes``, ``from routes.webhook_routes import X``,
``importlib.import_module("routes.webhook_routes")``, and the
``__import__("routes.webhook_routes", fromlist=[...])`` + ``setattr(wh_mod,
...)`` pattern used by test_null_owner_gates.py all operate on the *same*
object. Keeps existing import paths working after slice 2l (#4082/#4071).
Source-introspection tests read the canonical file by path.
"""

import sys as _sys

from routes.webhook import webhook_routes as _canonical  # noqa: F401

_sys.modules[__name__] = _canonical
