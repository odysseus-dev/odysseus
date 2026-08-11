"""Backward-compat shim — canonical location is routes/auth/api_token_routes.py.

This module is replaced in ``sys.modules`` by the canonical module object so
that ``import routes.api_token_routes``, ``from routes.api_token_routes
import X``, and the ``monkeypatch.delitem(sys.modules,
"routes.api_token_routes")`` + re-import pattern in test_api_token_routes.py
all operate on the *same* object. Keeps existing import paths working after
slice 2n (#4082/#4071).
"""

import sys as _sys

from routes.auth import api_token_routes as _canonical  # noqa: F401

_sys.modules[__name__] = _canonical
