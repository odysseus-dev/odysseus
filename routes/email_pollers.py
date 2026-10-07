"""Backward-compat shim — canonical location is routes/email/email_pollers.py.

This module is replaced in ``sys.modules`` by the canonical module object so
that ``import routes.email_pollers``, ``from routes.email_pollers import X``,
``importlib.import_module("routes.email_pollers")`` and the
``import ... as email_pollers`` + ``monkeypatch.setattr(email_pollers, ...)``
pattern used across the email tests all operate on the *same* object the
application actually uses. Source-introspection tests read the canonical file
by path.
"""

import sys as _sys

from routes.email import email_pollers as _canonical  # noqa: F401

_sys.modules[__name__] = _canonical
