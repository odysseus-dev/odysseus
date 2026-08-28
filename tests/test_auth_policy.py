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


# ── Login "remember me" cookie lifetime ────────────────────────────────


class _CapturingResponse:
    """Stand-in for fastapi.Response that records set_cookie kwargs."""

    def __init__(self):
        self.cookie_kwargs = None

    def set_cookie(self, **kwargs):
        self.cookie_kwargs = kwargs


def _login_endpoint(auth_manager):
    sys.modules.pop("routes.auth_routes", None)
    _real_core_package()
    from routes.auth_routes import LoginRequest, setup_auth_routes

    router = setup_auth_routes(auth_manager)
    for route in router.routes:
        if getattr(route, "path", None) == "/api/auth/login":
            return route.endpoint, LoginRequest
    raise AssertionError("login route not found")


def _login_request(scheme="http", forwarded_proto=None):
    """Stand-in for fastapi.Request carrying the fields login reads: the
    client host (rate limiter), and the URL scheme plus `X-Forwarded-Proto`
    (cookie Secure flag)."""
    headers = {}
    if forwarded_proto is not None:
        headers["x-forwarded-proto"] = forwarded_proto
    return SimpleNamespace(
        client=SimpleNamespace(host="127.0.0.1"),
        url=SimpleNamespace(scheme=scheme),
        headers=headers,
    )


def test_remember_cookie_max_age_matches_token_ttl(tmp_path):
    auth_mod = _auth_module()
    mgr = _make_manager(tmp_path)
    mgr.create_user("alice", "alice-password", is_admin=False)
    endpoint, LoginRequest = _login_endpoint(mgr)
    request = _login_request()
    response = _CapturingResponse()
    body = LoginRequest(username="alice", password="alice-password", remember=True)

    result = asyncio.run(endpoint(body=body, request=request, response=response))

    assert result == {"ok": True, "username": "alice"}
    # The persistent cookie must outlive neither more nor less than the token.
    assert response.cookie_kwargs["max_age"] == auth_mod.TOKEN_TTL


def test_no_remember_omits_cookie_max_age(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.create_user("bob", "bob-password", is_admin=False)
    endpoint, LoginRequest = _login_endpoint(mgr)
    request = _login_request()
    response = _CapturingResponse()
    body = LoginRequest(username="bob", password="bob-password", remember=False)

    asyncio.run(endpoint(body=body, request=request, response=response))

    # Without "remember", the cookie is a session cookie (no max_age).
    assert "max_age" not in response.cookie_kwargs


# ── Session cookie Secure flag ─────────────────────────────────────────


def _login_secure_flag(tmp_path, scheme, forwarded_proto=None):
    """Log a user in over ``scheme`` and return the cookie's Secure flag."""
    mgr = _make_manager(tmp_path)
    mgr.create_user("carol", "carol-password", is_admin=False)
    endpoint, LoginRequest = _login_endpoint(mgr)
    response = _CapturingResponse()
    body = LoginRequest(username="carol", password="carol-password")
    request = _login_request(scheme, forwarded_proto)

    asyncio.run(endpoint(body=body, request=request, response=response))

    return response.cookie_kwargs["secure"]


def test_https_login_marks_cookie_secure_without_config(tmp_path, monkeypatch):
    monkeypatch.delenv("SECURE_COOKIES", raising=False)

    # A session token handed out over HTTPS must not be allowed to travel
    # back in cleartext just because nobody set an env var.
    assert _login_secure_flag(tmp_path, "https") is True


def test_plain_http_login_leaves_cookie_insecure_without_config(tmp_path, monkeypatch):
    monkeypatch.delenv("SECURE_COOKIES", raising=False)

    # Marking it Secure here would make the browser drop the cookie and
    # break login on a plain-HTTP install.
    assert _login_secure_flag(tmp_path, "http") is False


def test_forwarded_proto_https_marks_cookie_secure(tmp_path, monkeypatch):
    monkeypatch.delenv("SECURE_COOKIES", raising=False)

    # A terminator that is not on an address uvicorn trusts leaves the
    # connection scheme as http, so the header is the only signal there.
    assert _login_secure_flag(tmp_path, "http", forwarded_proto="https") is True
    # Chained proxies send a list; the client-facing hop is the first entry.
    assert _login_secure_flag(tmp_path, "http", forwarded_proto="https, http") is True


def test_forwarded_proto_http_leaves_plain_http_login_insecure(tmp_path, monkeypatch):
    monkeypatch.delenv("SECURE_COOKIES", raising=False)

    # The header is not a downgrade switch: either signal saying https is
    # enough, the same test core/middleware.py applies before sending HSTS.
    assert _login_secure_flag(tmp_path, "http", forwarded_proto="http") is False
    assert _login_secure_flag(tmp_path, "https", forwarded_proto="http") is True


def test_empty_secure_cookies_still_derives_from_scheme(tmp_path, monkeypatch):
    # docker-compose injects `SECURE_COOKIES=${SECURE_COOKIES:-}`, which sets
    # the variable to "" when the host has not defined it. Empty means
    # unconfigured, not "off".
    monkeypatch.setenv("SECURE_COOKIES", "")

    assert _login_secure_flag(tmp_path, "https") is True


def test_secure_cookies_true_forces_secure_on_plain_http(tmp_path, monkeypatch):
    # The documented knob keeps working for a proxy whose scheme the app
    # cannot see, e.g. one that is not on a trusted loopback address.
    monkeypatch.setenv("SECURE_COOKIES", "true")

    assert _login_secure_flag(tmp_path, "http") is True


def test_secure_cookies_false_forces_insecure_on_https(tmp_path, monkeypatch):
    # The escape hatch for an install still answering on both HTTP and
    # HTTPS: an explicit false wins over the request scheme.
    monkeypatch.setenv("SECURE_COOKIES", "false")

    assert _login_secure_flag(tmp_path, "https") is False
