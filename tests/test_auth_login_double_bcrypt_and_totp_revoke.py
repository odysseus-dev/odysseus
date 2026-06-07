"""Regression tests for the double-bcrypt fix:

login must not run bcrypt twice — create_session_for_user skips the
re-verification that create_session used to perform unconditionally.
"""

import asyncio
import importlib
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from tests.helpers.import_state import clear_module


# ---------------------------------------------------------------------------
# Shared helpers (mirrors test_auth_session_revocation.py)
# ---------------------------------------------------------------------------

def _real_core_package():
    root = Path(__file__).resolve().parent.parent
    core_path = str(root / "core")
    core = sys.modules.get("core")
    if core is None:
        core = types.ModuleType("core")
        sys.modules["core"] = core
    core.__path__ = [core_path]
    clear_module("core.auth")
    return core


def _auth_module():
    _real_core_package()
    return importlib.import_module("core.auth")


def _make_manager(tmp_path):
    auth_mod = _auth_module()
    auth_mod._hash_password = lambda password: f"hash:{password}"
    auth_mod._verify_password = lambda password, hashed: hashed == f"hash:{password}"
    auth_path = tmp_path / "auth.json"
    mgr = auth_mod.AuthManager(str(auth_path))
    assert mgr.create_user("alice", "secret", is_admin=False)
    assert mgr.create_user("bob", "bobpass", is_admin=False)
    return mgr


async def _immediate_to_thread(fn, *args, **kwargs):
    return fn(*args, **kwargs)


# ---------------------------------------------------------------------------
# create_session_for_user unit tests
# ---------------------------------------------------------------------------

def test_create_session_for_user_returns_valid_token(tmp_path):
    """create_session_for_user must issue a token without re-checking creds."""
    mgr = _make_manager(tmp_path)
    token = mgr.create_session_for_user("alice")
    assert token is not None
    assert mgr.validate_token(token) is True


def test_create_session_for_user_strips_and_lowercases(tmp_path):
    """Username normalisation must match what the rest of auth uses."""
    mgr = _make_manager(tmp_path)
    token = mgr.create_session_for_user("  Alice  ")
    assert mgr.validate_token(token) is True
    assert mgr.get_username_for_token(token) == "alice"


def test_create_session_for_user_does_not_call_verify_password(tmp_path):
    """The whole point: verify_password must not be called inside
    create_session_for_user so the login route does not hash twice."""
    mgr = _make_manager(tmp_path)
    with patch.object(mgr, "verify_password", wraps=mgr.verify_password) as spy:
        mgr.create_session_for_user("alice")
        spy.assert_not_called()


def test_create_session_delegates_to_create_session_for_user(tmp_path):
    """create_session (password path) must still work and produce a valid
    token — the refactor must not break existing callers."""
    mgr = _make_manager(tmp_path)
    token = mgr.create_session("alice", "secret")
    assert token is not None
    assert mgr.validate_token(token) is True


def test_create_session_wrong_password_returns_none(tmp_path):
    """create_session must still reject wrong passwords."""
    mgr = _make_manager(tmp_path)
    assert mgr.create_session("alice", "wrong") is None


# ---------------------------------------------------------------------------
# Login route tests
# ---------------------------------------------------------------------------

def _login_endpoint(auth_manager):
    """Load the login route endpoint from a fresh import of auth_routes."""
    sys.modules.pop("routes.auth_routes", None)
    _real_core_package()
    from routes.auth_routes import LoginRequest, setup_auth_routes

    router = setup_auth_routes(auth_manager)
    for route in router.routes:
        if getattr(route, "path", None) == "/api/auth/login":
            return route.endpoint, LoginRequest
    raise AssertionError("login route not found")


def test_login_route_calls_create_session_for_user_not_create_session(monkeypatch):
    """The login route must call create_session_for_user after verifying the
    password, not create_session (which would re-run bcrypt)."""
    auth = MagicMock()
    auth.verify_password.return_value = True
    auth.totp_enabled.return_value = False
    auth.create_session_for_user.return_value = "tok123"
    monkeypatch.setattr(
        "routes.auth_routes.asyncio.to_thread",
        lambda fn, *args, **kwargs: _immediate_to_thread(fn, *args, **kwargs),
    )
    endpoint, LoginRequest = _login_endpoint(auth)
    request = SimpleNamespace(cookies={}, client=SimpleNamespace(host="127.0.0.1"))
    response = MagicMock()
    body = LoginRequest(username="alice", password="secret")

    result = asyncio.run(endpoint(body=body, request=request, response=response))

    assert result == {"ok": True, "username": "alice"}
    auth.create_session_for_user.assert_called_once_with("alice")
    auth.create_session.assert_not_called()


def test_login_route_does_not_call_create_session_for_user_on_wrong_password(monkeypatch):
    """If password verification fails, create_session_for_user must never run."""
    auth = MagicMock()
    auth.verify_password.return_value = False
    monkeypatch.setattr(
        "routes.auth_routes.asyncio.to_thread",
        lambda fn, *args, **kwargs: _immediate_to_thread(fn, *args, **kwargs),
    )
    endpoint, LoginRequest = _login_endpoint(auth)
    request = SimpleNamespace(cookies={}, client=SimpleNamespace(host="127.0.0.1"))
    response = MagicMock()
    body = LoginRequest(username="alice", password="wrong")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(body=body, request=request, response=response))

    assert exc.value.status_code == 401
    auth.create_session_for_user.assert_not_called()
    auth.create_session.assert_not_called()
