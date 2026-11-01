"""Backward-compat shim — canonical location is routes/vault/vault_routes.py.

This module is replaced in ``sys.modules`` by the canonical module object so
that ``import routes.vault_routes``, ``from routes.vault_routes import X``,
and the ``import ... as vr`` + ``monkeypatch.setattr(vr, ...)`` pattern used
by test_vault_password_not_in_argv.py all operate on the *same* object.
Keeps existing import paths working after slice 2k (#4082/#4071).
"""

import sys as _sys

from routes.vault import vault_routes as _canonical  # noqa: F401

_sys.modules[__name__] = _canonical
