from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routes.api_token_routes import ALLOWED_SCOPES, TOKEN_PROFILES
from routes.openclaw_bridge_routes import _scope_owner, openclaw_session_id


def _request(*, api_token=True, scopes=None, owner="alice", current_user="browser-user"):
    state = SimpleNamespace(
        api_token=api_token,
        api_token_scopes=scopes or [],
        api_token_owner=owner,
        current_user=current_user,
    )
    return SimpleNamespace(state=state)


def test_openclaw_bridge_token_profile_is_read_only_by_default():
    assert "chat" in TOKEN_PROFILES["openclaw_bridge"]
    assert "converge:read" in TOKEN_PROFILES["openclaw_bridge"]
    assert "workflows:trigger" not in TOKEN_PROFILES["openclaw_bridge"]
    assert {"chat", "converge:read", "workflows:trigger"}.issubset(ALLOWED_SCOPES)


def test_scope_owner_uses_api_token_owner_when_scope_matches():
    req = _request(scopes=["chat"], owner="bridge-owner")
    assert _scope_owner(req, {"chat"}) == "bridge-owner"


def test_scope_owner_rejects_missing_scope():
    req = _request(scopes=["chat"], owner="bridge-owner")
    with pytest.raises(HTTPException) as exc:
        _scope_owner(req, {"workflows:trigger"})
    assert exc.value.status_code == 403


def test_openclaw_session_id_is_predictable_and_sanitized():
    assert openclaw_session_id("C123", "1700000000.000100") == "openclaw:slack:C123:1700000000.000100"
    assert openclaw_session_id("C 123", "thread/with spaces") == "openclaw:slack:C-123:thread-with-spaces"
    assert openclaw_session_id(session_id="custom:session") == "custom:session"
