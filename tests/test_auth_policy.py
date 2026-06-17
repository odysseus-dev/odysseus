"""Tests for auth policy endpoint and password length validation."""

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
    return mgr


async def _immediate_to_thread(fn, *args, **kwargs):
    return fn(*args, **kwargs)


# ── AuthManager.policy() ───────────────────────────────────────────────


def test_policy_returns_password_min_length(tmp_path):
    mgr = _make_manager(tmp_path)
    policy = mgr.policy()
    assert policy["password_min_length"] == 8


def test_policy_returns_reserved_usernames(tmp_path):
    mgr = _make_manager(tmp_path)
    policy = mgr.policy()
    assert "internal-tool" in policy["reserved_usernames"]
    assert "api" in policy["reserved_usernames"]
    assert "demo" in policy["reserved_usernames"]
    assert "system" in policy["reserved_usernames"]
    assert isinstance(policy["reserved_usernames"], list)


def test_policy_returns_signup_enabled(tmp_path):
    mgr = _make_manager(tmp_path)
    policy = mgr.policy()
    assert policy["signup_enabled"] is False  # default


def test_policy_returns_session_days(tmp_path):
    mgr = _make_manager(tmp_path)
    policy = mgr.policy()
    assert policy["session_days"] == 7


# ── GET /api/auth/policy endpoint ──────────────────────────────────────


def _policy_endpoint(auth_manager):
    sys.modules.pop("routes.auth_routes", None)
    _real_core_package()
    from routes.auth_routes import setup_auth_routes

    router = setup_auth_routes(auth_manager)
    for route in router.routes:
        if getattr(route, "path", None) == "/api/auth/policy":
            return route.endpoint
    raise AssertionError("policy route not found")


def test_policy_endpoint_returns_dict(tmp_path):
    mgr = _make_manager(tmp_path)
    endpoint = _policy_endpoint(mgr)
    result = asyncio.run(endpoint())
    assert isinstance(result, dict)
    assert "password_min_length" in result
    assert "reserved_usernames" in result
    assert "signup_enabled" in result
    assert "session_days" in result


def test_policy_endpoint_values_match_manager(tmp_path):
    mgr = _make_manager(tmp_path)
    endpoint = _policy_endpoint(mgr)
    result = asyncio.run(endpoint())
    assert result == mgr.policy()


# ── Password length validation ─────────────────────────────────────────


def _setup_endpoint(auth_manager):
    sys.modules.pop("routes.auth_routes", None)
    _real_core_package()
    from routes.auth_routes import SetupRequest, setup_auth_routes

    router = setup_auth_routes(auth_manager)
    for route in router.routes:
        if getattr(route, "path", None) == "/api/auth/setup":
            return route.endpoint, SetupRequest
    raise AssertionError("setup route not found")


def _signup_endpoint(auth_manager):
    sys.modules.pop("routes.auth_routes", None)
    _real_core_package()
    from routes.auth_routes import SignupRequest, setup_auth_routes

    router = setup_auth_routes(auth_manager)
    for route in router.routes:
        if getattr(route, "path", None) == "/api/auth/signup":
            return route.endpoint, SignupRequest
    raise AssertionError("signup route not found")


def _change_password_endpoint(auth_manager):
    sys.modules.pop("routes.auth_routes", None)
    _real_core_package()
    from routes.auth_routes import ChangePasswordRequest, setup_auth_routes

    router = setup_auth_routes(auth_manager)
    for route in router.routes:
        if getattr(route, "path", None) == "/api/auth/change-password":
            return route.endpoint, ChangePasswordRequest
    raise AssertionError("change-password route not found")


def test_setup_rejects_short_password(tmp_path):
    mgr = _make_manager(tmp_path)
    endpoint, SetupRequest = _setup_endpoint(mgr)
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    body = SetupRequest(username="admin", password="short")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(body=body, request=request))

    assert exc.value.status_code == 400
    assert "8 characters" in exc.value.detail


def test_signup_rejects_short_password(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.create_user("admin", "admin-password", is_admin=True)
    mgr.signup_enabled = True
    endpoint, SignupRequest = _signup_endpoint(mgr)
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    body = SignupRequest(username="newuser", password="short")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(body=body, request=request))

    assert exc.value.status_code == 400
    assert "8 characters" in exc.value.detail


def test_change_password_rejects_short_password(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.create_user("alice", "old-password", is_admin=False)
    endpoint, ChangePasswordRequest = _change_password_endpoint(mgr)
    request = SimpleNamespace(
        cookies={"odysseus_session": "current-token"},
        client=SimpleNamespace(host="127.0.0.1"),
    )
    # Mock get_username_for_token to return alice
    mgr.get_username_for_token = MagicMock(return_value="alice")
    body = ChangePasswordRequest(current_password="old-password", new_password="short")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(body=body, request=request))

    assert exc.value.status_code == 400
    assert "8 characters" in exc.value.detail


def test_setup_accepts_exactly_min_length_password(tmp_path):
    mgr = _make_manager(tmp_path)
    endpoint, SetupRequest = _setup_endpoint(mgr)
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    body = SetupRequest(username="admin", password="12345678")

    result = asyncio.run(endpoint(body=body, request=request))

    assert result == {"ok": True, "message": "Admin account created"}


def test_setup_rejects_seven_char_password(tmp_path):
    mgr = _make_manager(tmp_path)
    endpoint, SetupRequest = _setup_endpoint(mgr)
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    body = SetupRequest(username="admin", password="1234567")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(body=body, request=request))

    assert exc.value.status_code == 400
