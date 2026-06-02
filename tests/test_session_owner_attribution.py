"""Tests for token-owner session attribution (effective_user + session routes).

Proves the two properties the review asked for:
  - cookie/browser users are completely unchanged (no-op swap)
  - a bearer token for owner A can never read/verify owner B's session, and a
    bearer token with no owner does not escalate.

Follows the direct-helper + mocked-DB style of tests/test_null_owner_gates.py.
"""

import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# routes.session_routes imports several heavy modules at import time that blow up
# under conftest's sqlalchemy/* MagicMock stubs (declarative classes). Stub them
# so we can import the module and exercise _verify_session_owner with a mock DB.
_STUBS = {
    "core.database": {"Session": MagicMock(), "SessionLocal": MagicMock(),
                      "Document": MagicMock(), "GalleryImage": MagicMock()},
    "core.session_manager": {"SessionManager": MagicMock()},
    "core.models": {"ChatMessage": MagicMock()},
    "src.request_models": {"SessionResponse": MagicMock()},
}
for _name, _attrs in _STUBS.items():
    if _name not in sys.modules:
        _m = types.ModuleType(_name)
        for _k, _v in _attrs.items():
            setattr(_m, _k, _v)
        sys.modules[_name] = _m

from fastapi import HTTPException  # noqa: E402

from src.auth_helpers import effective_user  # noqa: E402
import routes.session_routes as SR  # noqa: E402


def _req(**state):
    return SimpleNamespace(state=SimpleNamespace(**state))


# --- effective_user: who a request is attributed to ------------------------

def test_cookie_user_is_unchanged():
    # The whole point: browser/cookie callers behave exactly as before.
    assert effective_user(_req(api_token=False, current_user="alice")) == "alice"


def test_bearer_token_attributes_to_its_owner():
    # A paired phone runs as the "api" pseudo-user but must act as the token owner.
    assert effective_user(_req(api_token=True, api_token_owner="alice", current_user="api")) == "alice"


def test_bearer_token_without_owner_does_not_escalate():
    # No owner on the token -> falls back to current_user ("api"), never another user.
    assert effective_user(_req(api_token=True, api_token_owner=None, current_user="api")) == "api"


# --- _verify_session_owner: bearer tokens cannot cross owners ---------------

def _session_local_returning(owner_value):
    """Mock SessionLocal whose query(...).filter(...).first() yields a row with
    the given owner (or None for 'no such session')."""
    db = MagicMock()
    row = None if owner_value is _MISSING else SimpleNamespace(owner=owner_value)
    db.query.return_value.filter.return_value.first.return_value = row
    return MagicMock(return_value=db)


_MISSING = object()


def test_bearer_owner_A_cannot_verify_owner_B_session(monkeypatch):
    monkeypatch.setattr(SR, "SessionLocal", _session_local_returning("bob"))
    req = _req(api_token=True, api_token_owner="alice", current_user="api")
    with pytest.raises(HTTPException) as exc:
        SR._verify_session_owner(req, "sid-owned-by-bob")
    assert exc.value.status_code == 404


def test_owner_can_verify_their_own_session(monkeypatch):
    monkeypatch.setattr(SR, "SessionLocal", _session_local_returning("alice"))
    req = _req(api_token=True, api_token_owner="alice", current_user="api")
    # Should not raise.
    SR._verify_session_owner(req, "sid-owned-by-alice")


def test_cookie_user_owns_their_session(monkeypatch):
    # Cookie path unchanged: alice (cookie) verifies alice's session.
    monkeypatch.setattr(SR, "SessionLocal", _session_local_returning("alice"))
    req = _req(api_token=False, current_user="alice")
    SR._verify_session_owner(req, "sid")


def test_missing_session_is_404(monkeypatch):
    monkeypatch.setattr(SR, "SessionLocal", _session_local_returning(_MISSING))
    req = _req(api_token=False, current_user="alice")
    with pytest.raises(HTTPException) as exc:
        SR._verify_session_owner(req, "nope")
    assert exc.value.status_code == 404


def test_unauthenticated_caller_rejected(monkeypatch):
    req = _req(api_token=False, current_user=None)
    with pytest.raises(HTTPException) as exc:
        SR._verify_session_owner(req, "sid")
    assert exc.value.status_code == 403
