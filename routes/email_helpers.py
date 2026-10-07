"""Backward-compat shim — canonical location is routes/email/email_helpers.py.

This module is replaced in ``sys.modules`` by the canonical module object so
that ``import routes.email_helpers``, ``from routes.email_helpers import X``,
``importlib.import_module("routes.email_helpers")`` and the
``import ... as email_helpers`` + ``monkeypatch.setattr(email_helpers, ...)``
pattern used across the email tests all operate on the *same* object the
application actually uses. Source-introspection tests read the canonical file
by path.
"""

import sys as _sys

from routes.email import email_helpers as _canonical  # noqa: F401

_sys.modules[__name__] = _canonical
