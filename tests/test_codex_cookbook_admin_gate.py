"""Codex cookbook routes require admin for cookie-session callers.

Regression test for issue #4542: non-admin users could reach cookbook
routes (tasks, servers, output, stop, adopt, presets, etc.) through
normal cookie sessions because _scope_owner only checked login status,
not admin privileges.

After the fix, cookie-session callers must be admin; API-token callers
are still governed by scope checks only.
"""
import pytest
from types import SimpleNamespace
from fastapi import HTTPException

from routes.codex_routes import _require_cookbook_scope


COOKBOOK_READ_SCOPES = {"cookbook:read", "cookbook:launch"}
COOKBOOK_LAUNCH_SCOPES = {"cookbook:launch"}


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
            _require_cookbook_scope(req, COOKBOOK_READ_SCOPES)
        assert exc.value.status_code == 403

    def test_non_admin_rejected_launch(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        req = _cookie_request(is_admin=False)
        with pytest.raises(HTTPException) as exc:
            _require_cookbook_scope(req, COOKBOOK_LAUNCH_SCOPES)
        assert exc.value.status_code == 403

    def test_admin_allowed_read(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        req = _cookie_request(is_admin=True)
        owner = _require_cookbook_scope(req, COOKBOOK_READ_SCOPES)
        assert owner == "bob"

    def test_admin_allowed_launch(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        req = _cookie_request(is_admin=True)
        owner = _require_cookbook_scope(req, COOKBOOK_LAUNCH_SCOPES)
        assert owner == "bob"


class TestApiTokenScopeGate:
    """API-token callers are governed by scope, not admin status."""

    def test_token_with_scope_allowed(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        req = _api_token_request(scopes=["cookbook:read"])
        owner = _require_cookbook_scope(req, COOKBOOK_READ_SCOPES)
        assert owner == "alice"

    def test_token_missing_scope_rejected(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        req = _api_token_request(scopes=["unrelated:scope"])
        with pytest.raises(HTTPException) as exc:
            _require_cookbook_scope(req, COOKBOOK_READ_SCOPES)
        assert exc.value.status_code == 403


class TestSourceCodeGate:
    """Static checks: all cookbook routes use _require_cookbook_scope."""

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
            f"Cookbook routes still use _scope_owner instead of _require_cookbook_scope: {violations}"
        )
