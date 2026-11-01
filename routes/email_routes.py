"""Backward-compat shim — canonical location is routes/email/email_routes.py.

This module is replaced in ``sys.modules`` by the canonical module object so
that ``import routes.email_routes``, ``from routes.email_routes import X``,
``importlib.import_module("routes.email_routes")`` and the
``import ... as email_routes`` + ``monkeypatch.setattr(email_routes, ...)``
pattern used across the email tests all operate on the *same* object the
application actually uses. Source-introspection tests read the canonical file
by path.
"""

import sys as _sys

from routes.email import email_routes as _canonical  # noqa: F401

_sys.modules[__name__] = _canonical
