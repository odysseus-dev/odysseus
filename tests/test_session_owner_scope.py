"""Tests for API token scope enforcement in _verify_session_owner.

`_verify_session_owner` (routes/session_routes.py) checks that the request
owner matches the session's stored owner.  With this change it also checks
that any bearer API token carries the ``chat`` scope (or wildcard ``*``).
Tokens with other scopes (e.g. ``documents:read``) are rejected with 403
even when the token owner matches the session owner.

Test coverage:
  - ``chat`` scope → allowed
  - ``documents:read`` scope → 403
  - ``*`` wildcard → allowed
  - no API token (browser session) → allowed (scope check skipped entirely)
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi import Request as FastAPI_Request

import routes.session_routes as session_routes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_request(*, api_token=False, api_token_owner=None, api_token_scopes=None):
    """Build a mock FastAPI Request with the given state attributes."""
    req = MagicMock(spec=FastAPI_Request)
    req.state.api_token = api_token
    req.state.api_token_owner = api_token_owner
    req.state.api_token_scopes = api_token_scopes or []
    # Simulate a cookie session user (set by auth middleware)
    req.state.current_user = None if api_token else "test-user"
    return req


def _patch_db_for_owner_match(monkeypatch, owner="test-owner"):
    """Mock SessionLocal so the DB query returns a matching owner row."""
    mock_session = MagicMock()
    mock_row = MagicMock()
    mock_row.owner = owner
    mock_session.query.return_value.filter.return_value.first.return_value = mock_row
    mock_session.__enter__.return_value = mock_session
    mock_session.__exit__.return_value = None
    mock_SessionLocal = MagicMock(return_value=mock_session)

    monkeypatch.setattr(session_routes, "SessionLocal", mock_SessionLocal)


# ---------------------------------------------------------------------------
# Scope check tests
# ---------------------------------------------------------------------------

def test_chat_scope_allowed(monkeypatch):
    """A token with 'chat' scope passes the scope gate."""
    req = _mock_request(api_token=True, api_token_owner="test-owner",
                        api_token_scopes=["chat"])
    _patch_db_for_owner_match(monkeypatch, owner="test-owner")
    # Should not raise
    session_routes._verify_session_owner(req, "s1")


def test_wildcard_scope_allowed(monkeypatch):
    """A token with '*' scope passes the scope gate."""
    req = _mock_request(api_token=True, api_token_owner="test-owner",
                        api_token_scopes=["*"])
    _patch_db_for_owner_match(monkeypatch, owner="test-owner")
    # Should not raise
    session_routes._verify_session_owner(req, "s1")


def test_chat_among_many_scopes_allowed(monkeypatch):
    """A token with multiple scopes including 'chat' passes."""
    req = _mock_request(api_token=True, api_token_owner="test-owner",
                        api_token_scopes=["email", "chat", "documents:read"])
    _patch_db_for_owner_match(monkeypatch, owner="test-owner")
    # Should not raise
    session_routes._verify_session_owner(req, "s1")


def test_non_chat_scope_rejected(monkeypatch):
    """A token scoped only for 'documents:read' gets 403."""
    req = _mock_request(api_token=True, api_token_owner="test-owner",
                        api_token_scopes=["documents:read"])
    _patch_db_for_owner_match(monkeypatch, owner="test-owner")
    with pytest.raises(HTTPException) as exc_info:
        session_routes._verify_session_owner(req, "s1")
    assert exc_info.value.status_code == 403
    assert "chat scope" in str(exc_info.value.detail)


def test_email_scope_rejected(monkeypatch):
    """A token scoped only for 'email' gets 403."""
    req = _mock_request(api_token=True, api_token_owner="test-owner",
                        api_token_scopes=["email"])
    _patch_db_for_owner_match(monkeypatch, owner="test-owner")
    with pytest.raises(HTTPException) as exc_info:
        session_routes._verify_session_owner(req, "s1")
    assert exc_info.value.status_code == 403
    assert "chat scope" in str(exc_info.value.detail)


def test_browser_cookie_skips_scope_check(monkeypatch):
    """A request without an API token (browser cookie) skips the scope gate."""
    req = _mock_request(api_token=False, api_token_scopes=None)
    _patch_db_for_owner_match(monkeypatch, owner="test-user")
    # Should not raise — scope check is only for API tokens
    session_routes._verify_session_owner(req, "s1")


def test_api_token_no_scopes_rejected(monkeypatch):
    """A token with an empty scopes list gets 403."""
    req = _mock_request(api_token=True, api_token_owner="test-owner",
                        api_token_scopes=[])
    _patch_db_for_owner_match(monkeypatch, owner="test-owner")
    with pytest.raises(HTTPException) as exc_info:
        session_routes._verify_session_owner(req, "s1")
    assert exc_info.value.status_code == 403
    assert "chat scope" in str(exc_info.value.detail)


def test_api_token_scope_preserves_owner_mismatch_check(monkeypatch):
    """Even with 'chat' scope, wrong owner still gets 404."""
    req = _mock_request(api_token=True, api_token_owner="test-owner",
                        api_token_scopes=["chat"])
    _patch_db_for_owner_match(monkeypatch, owner="other-owner")
    with pytest.raises(HTTPException) as exc_info:
        session_routes._verify_session_owner(req, "s1")
    assert exc_info.value.status_code == 404
