"""Codex cookbook routes require admin for cookie-session callers.

Regression test for issue #4542: non-admin users could reach cookbook
routes (tasks, servers, output, stop, adopt, presets, etc.) through
normal cookie sessions because _scope_owner only checked login status,
not admin privileges.

After retirement of Cookbook bearer access, cookie-session callers must be
admin and API-token callers must always be rejected.
"""
import pytest
from types import SimpleNamespace
from fastapi import HTTPException

from routes.codex_routes import _require_cookbook_admin, setup_codex_routes
from src.api_token_capabilities import API_TOKEN_FORBIDDEN_ERROR


def _cookie_request(*, current_user="bob", is_admin=False):
    """Simulate a cookie-session request (no api_token)."""
    auth_mgr = SimpleNamespace(
        is_configured=True,
        is_admin=lambda user: is_admin and user == "bob",
    )
    return SimpleNamespace(
        state=SimpleNamespace(
            current_user=current_user,
            api_token=False,
        ),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=auth_mgr)),
        headers={},
    )


def _api_token_request(*, scopes=None, owner="alice"):
    """Simulate an API-token request."""
    return SimpleNamespace(
        state=SimpleNamespace(
            current_user="api",
            api_token=True,
            api_token_scopes=scopes or [],
            api_token_owner=owner,
        ),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
        headers={},
    )


class TestCookieSessionAdminGate:
    """Non-admin cookie sessions must be rejected; admin sessions allowed."""

    def test_non_admin_rejected_read(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        req = _cookie_request(is_admin=False)
        with pytest.raises(HTTPException) as exc:
            _require_cookbook_admin(req)
        assert exc.value.status_code == 403

    def test_non_admin_rejected_launch(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        req = _cookie_request(is_admin=False)
        with pytest.raises(HTTPException) as exc:
            _require_cookbook_admin(req)
        assert exc.value.status_code == 403

    def test_admin_allowed_read(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        req = _cookie_request(is_admin=True)
        owner = _require_cookbook_admin(req)
        assert owner == "bob"

    def test_admin_allowed_launch(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        req = _cookie_request(is_admin=True)
        owner = _require_cookbook_admin(req)
        assert owner == "bob"


class TestApiTokenGate:
    """API-token callers cannot reach Cookbook through retired scopes."""

    def test_token_with_retired_scope_rejected(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        req = _api_token_request(scopes=["cookbook:read"])
        with pytest.raises(HTTPException) as exc:
            _require_cookbook_admin(req)
        assert exc.value.status_code == 403
        assert exc.value.detail == API_TOKEN_FORBIDDEN_ERROR

    def test_token_missing_scope_rejected(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        req = _api_token_request(scopes=["unrelated:scope"])
        with pytest.raises(HTTPException) as exc:
            _require_cookbook_admin(req)
        assert exc.value.status_code == 403
        assert exc.value.detail == API_TOKEN_FORBIDDEN_ERROR


def test_capabilities_omit_cookbook_and_retired_scopes():
    router = setup_codex_routes()
    endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/codex/capabilities" and "GET" in route.methods
    )
    request = _api_token_request(scopes=["chat", "cookbook:read", "cookbook:launch"])

    result = endpoint(request)

    assert result["token_scopes"] == ["chat"]
    assert "cookbook" not in result["tools"]


class TestSourceCodeGate:
    """Static checks: all Cookbook routes use the cookie/admin gate."""

    def test_no_raw_scope_owner_in_cookbook_routes(self):
        from pathlib import Path
        source = Path("routes/codex_routes.py").read_text(encoding="utf-8")
        # _scope_owner should NOT appear inside cookbook route handlers.
        # Find lines between cookbook route defs that still call _scope_owner.
        in_cookbook = False
        violations = []
        for i, line in enumerate(source.splitlines(), 1):
            if "@router." in line and "/cookbook/" in line:
                in_cookbook = True
            elif "@router." in line and "/cookbook/" not in line:
                in_cookbook = False
            if in_cookbook and "_scope_owner(request" in line:
                violations.append((i, line.strip()))
        assert violations == [], (
            f"Cookbook routes still use _scope_owner instead of _require_cookbook_admin: {violations}"
        )
        assert source.count("_require_cookbook_admin(request)") == 9
        assert "_require_cookbook_scope" not in source
