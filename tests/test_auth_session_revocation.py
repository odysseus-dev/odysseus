"""Regression tests for password-change session revocation."""

import asyncio
import importlib
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from tests.helpers.import_state import clear_module


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
    assert mgr.create_user("alice", "old-password", is_admin=False)
    assert mgr.create_user("bob", "bob-password", is_admin=False)
    return mgr


async def _immediate_to_thread(fn, *args, **kwargs):
    return fn(*args, **kwargs)


def test_revoke_user_sessions_preserves_current_and_persists(tmp_path):
    mgr = _make_manager(tmp_path)
    current = mgr.create_session("alice", "old-password")
    other = mgr.create_session("alice", "old-password")
    bob = mgr.create_session("bob", "bob-password")

    revoked = mgr.revoke_user_sessions("alice", except_token=current)

    assert revoked == 1
    assert mgr.validate_token(current) is True
    assert mgr.validate_token(other) is False
    assert mgr.validate_token(bob) is True


def test_wrong_current_password_does_not_revoke_sessions(tmp_path):
    mgr = _make_manager(tmp_path)
    current = mgr.create_session("alice", "old-password")
    other = mgr.create_session("alice", "old-password")

    assert mgr.change_password("alice", "wrong-password", "new-password") is False

    assert mgr.validate_token(current) is True
    assert mgr.validate_token(other) is True


def test_password_change_allows_new_password_and_blocks_old_password(tmp_path):
    mgr = _make_manager(tmp_path)

    assert mgr.change_password("alice", "old-password", "new-password") is True

    assert mgr.create_session("alice", "old-password") is None
    assert mgr.create_session("alice", "new-password") is not None


def test_create_session_trusted_rejects_username_renamed_after_verification(tmp_path):
    mgr = _make_manager(tmp_path)
    assert mgr.create_user("admin", "admin-password", is_admin=True)

    assert mgr.verify_password("alice", "old-password") is True
    assert mgr.rename_user("alice", "alice2", "admin") is True

    assert mgr.create_session_trusted("alice") is None


def _change_password_endpoint(auth_manager):
    sys.modules.pop("routes.auth_routes", None)
    _real_core_package()
    from routes.auth_routes import ChangePasswordRequest, setup_auth_routes

    router = setup_auth_routes(auth_manager)
    for route in router.routes:
        if getattr(route, "path", None) == "/api/auth/change-password":
            return route.endpoint, ChangePasswordRequest
    raise AssertionError("change-password route not found")


def _login_endpoint(auth_manager):
    sys.modules.pop("routes.auth_routes", None)
    _real_core_package()
    from routes.auth_routes import LoginRequest, setup_auth_routes

    router = setup_auth_routes(auth_manager)
    for route in router.routes:
        if getattr(route, "path", None) == "/api/auth/login":
            return route.endpoint, LoginRequest
    raise AssertionError("login route not found")


def test_login_route_does_not_set_cookie_when_trusted_session_rejects_stale_user(monkeypatch):
    auth = MagicMock()
    auth.verify_password.return_value = True
    auth.totp_enabled.return_value = False
    auth.create_session_trusted.return_value = None
    endpoint, LoginRequest = _login_endpoint(auth)
    monkeypatch.setattr(
        "routes.auth_routes.asyncio.to_thread",
        lambda fn, *args, **kwargs: _immediate_to_thread(fn, *args, **kwargs),
    )
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    response = MagicMock()
    body = LoginRequest(username="alice", password="old-password")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(body=body, request=request, response=response))

    assert exc.value.status_code == 401
    response.set_cookie.assert_not_called()


def test_change_password_route_revokes_other_sessions_after_success(monkeypatch):
    auth = MagicMock()
    auth.get_username_for_token.return_value = "alice"
    auth.change_password.return_value = True
    endpoint, ChangePasswordRequest = _change_password_endpoint(auth)
    monkeypatch.setattr(
        "routes.auth_routes.asyncio.to_thread",
        lambda fn, *args, **kwargs: _immediate_to_thread(fn, *args, **kwargs),
    )
    request = SimpleNamespace(cookies={"odysseus_session": "current-token"})
    body = ChangePasswordRequest(current_password="old-password", new_password="new-password")

    result = asyncio.run(endpoint(body=body, request=request))

    assert result == {"ok": True}
    auth.change_password.assert_called_once_with("alice", "old-password", "new-password")
    auth.revoke_user_sessions.assert_called_once_with("alice", "current-token")


def test_change_password_route_wrong_password_does_not_revoke(monkeypatch):
    auth = MagicMock()
    auth.get_username_for_token.return_value = "alice"
    auth.change_password.return_value = False
    endpoint, ChangePasswordRequest = _change_password_endpoint(auth)
    monkeypatch.setattr(
        "routes.auth_routes.asyncio.to_thread",
        lambda fn, *args, **kwargs: _immediate_to_thread(fn, *args, **kwargs),
    )
    request = SimpleNamespace(cookies={"odysseus_session": "current-token"})
    body = ChangePasswordRequest(current_password="wrong-password", new_password="new-password")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(body=body, request=request))

    assert exc.value.status_code == 400
    auth.revoke_user_sessions.assert_not_called()
