"""Owner-scope tests for the read-only companion bridge.

Mirrors the direct-helper style of tests/test_null_owner_gates.py: exercise the
small pure helpers against mock request state and owner values, so the scoping
rule can't silently regress. A bearer token for owner A must never see owner B's
rows, and legacy null-owner rows must not widen a token's access.
"""

import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# core.database instantiates SQLAlchemy declarative classes at import time, which
# blows up under conftest's sqlalchemy MagicMock stubs. companion.routes only
# imports it lazily inside the /models handler, but stub it defensively so the
# import is robust regardless of collection order.
if "core.database" not in sys.modules:
    _db = types.ModuleType("core.database")
    _db.SessionLocal = MagicMock()
    _db.ModelEndpoint = MagicMock()
    sys.modules["core.database"] = _db

from companion.routes import token_owner, owner_can_see


def _request(**state):
    return SimpleNamespace(state=SimpleNamespace(**state))


# --- token_owner: who a request is attributed to ---------------------------

def test_token_owner_bearer_resolves_to_token_owner():
    # A paired bearer caller runs as the "api" pseudo-user, but must attribute
    # to the token's real owner.
    req = _request(api_token=True, api_token_owner="alice", current_user="api")
    assert token_owner(req) == "alice"


def test_token_owner_cookie_uses_logged_in_user():
    req = _request(api_token=False, current_user="alice")
    assert token_owner(req) == "alice"


def test_token_owner_none_when_unresolved():
    req = _request(api_token=True, api_token_owner=None, current_user="api")
    assert token_owner(req) is None


# --- owner_can_see: the read-scope rule ------------------------------------

def test_owner_sees_their_own_rows():
    assert owner_can_see("alice", "alice") is True


def test_null_owner_shared_rows_are_visible():
    # Legacy shared rows (owner is None) are visible to everyone by design...
    assert owner_can_see(None, "alice") is True


def test_null_owner_does_not_widen_access_to_others_rows():
    # ...but a null-owner row must not be a backdoor to another OWNER's rows.
    assert owner_can_see("bob", "alice") is False


def test_cross_owner_is_blocked():
    assert owner_can_see("bob", "alice") is False
    assert owner_can_see("alice", "bob") is False


def test_unauthenticated_owner_sees_only_shared_rows():
    # owner=None (no resolved caller): only null-owner shared rows are visible,
    # never any owned row.
    assert owner_can_see(None, None) is True
    assert owner_can_see("alice", None) is False
