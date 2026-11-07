"""S1 review guards: the OIDC surface stays wired into the app (X10, X7).

These are guard tests only — the behaviours they pin exist in app.py and
routes/auth_routes.py; breaking any of them must fail here rather than go
unnoticed.  Parsing app.py's source or driving the real route keeps the
checks honest without booting the whole application.
"""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_oidc_routes_stay_auth_exempt_in_app():
    """/api/auth/oidc/{config,login,callback} must stay in AUTH_EXEMPT_EXACT,
    or the auth middleware 401s/redirects them and the SSO flow is dead."""
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    exempt = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "AUTH_EXEMPT_EXACT":
                assert isinstance(node.value, ast.Set), (
                    "AUTH_EXEMPT_EXACT is no longer a literal set in app.py"
                )
                exempt = {element.value for element in node.value.elts}
    assert exempt is not None, "AUTH_EXEMPT_EXACT assignment not found in app.py"
    for path in (
        "/api/auth/oidc/login",
        "/api/auth/oidc/callback",
        "/api/auth/oidc/config",
    ):
        assert path in exempt, f"{path} must stay auth-exempt in app.py"


def test_status_reports_is_oidc_for_oidc_and_password_users(tmp_path):
    """/api/auth/status must carry is_oidc (true for an OIDC account, false
    for a password account) so the UI hides the right cards."""
    from core.auth import AuthManager
    from routes.auth_routes import SESSION_COOKIE, setup_auth_routes

    mgr = AuthManager(str(tmp_path / "auth.json"))
    mgr.create_user("bob", "hunter2", is_admin=False)
    mgr.create_user_oidc("alice", sub="abc123", issuer="https://idp.example.com")

    endpoint = next(
        route.endpoint for route in setup_auth_routes(mgr).routes
        if getattr(route, "path", "") == "/api/auth/status"
    )

    alice_token = mgr.create_session_trusted("alice")
    result = asyncio.run(
        endpoint(SimpleNamespace(cookies={SESSION_COOKIE: alice_token}))
    )
    assert result["authenticated"] is True
    assert result["is_oidc"] is True

    bob_token = mgr.create_session("bob", "hunter2")
    result = asyncio.run(
        endpoint(SimpleNamespace(cookies={SESSION_COOKIE: bob_token}))
    )
    assert result["authenticated"] is True
    assert result["is_oidc"] is False
