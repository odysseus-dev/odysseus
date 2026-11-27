"""Regression tests for email route API-token scopes and session boundaries."""

from types import SimpleNamespace
import pytest
from fastapi import HTTPException

from routes.email import email_helpers, email_routes


def _request(*, api_token=False, scopes=None, owner="alice"):
    return SimpleNamespace(
        state=SimpleNamespace(
            api_token=api_token,
            api_token_scopes=scopes or [],
            api_token_owner=owner,
            user=owner,
        ),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
        client=SimpleNamespace(host="127.0.0.1"),
    )


def _endpoint_dep(path: str, method: str):
    router = email_routes.setup_email_routes()
    for route in router.routes:
        if getattr(route, "path", None) == path and method.upper() in getattr(route, "methods", set()):
            for dep in route.dependant.dependencies:
                if dep.name == "owner":
                    return dep.call
    raise AssertionError(f"owner dependency not found for {method} {path}")


def test_core_lazy_session_manager_attribute():
    import core

    cls = getattr(core, "SessionManager")
    assert cls.__name__ == "SessionManager"
    with pytest.raises(AttributeError):
        getattr(core, "DoesNotExistAttribute123")


def test_scoped_api_token_enforces_email_scopes(monkeypatch):
    monkeypatch.setattr(email_helpers, "_require_auth", lambda req: "alice")

    # Token without email:read is rejected on read endpoints
    with pytest.raises(HTTPException) as exc:
        email_helpers.require_email_read(_request(api_token=True, scopes=["todos:read"]), account_id=None)
    assert exc.value.status_code == 403

    # Token with email:read succeeds on read endpoints, fails on draft/send
    assert email_helpers.require_email_read(_request(api_token=True, scopes=["email:read"]), account_id=None) == "alice"
    with pytest.raises(HTTPException) as exc:
        email_helpers.require_email_draft(_request(api_token=True, scopes=["email:read"]), account_id=None)
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        email_helpers.require_email_send(_request(api_token=True, scopes=["email:draft"]), account_id=None)
    assert exc.value.status_code == 403

    # Matching draft/send scopes succeed
    assert email_helpers.require_email_draft(_request(api_token=True, scopes=["email:draft"]), account_id=None) == "alice"
    assert email_helpers.require_email_send(_request(api_token=True, scopes=["email:send"]), account_id=None) == "alice"


def test_delegated_api_token_blocked_from_account_and_destructive_routes(monkeypatch):
    monkeypatch.setattr(email_helpers, "_require_auth", lambda req: "alice")

    with pytest.raises(HTTPException) as exc:
        email_helpers.require_email_browser_session(
            _request(api_token=True, scopes=["email:read", "email:draft", "email:send"])
        )
    assert exc.value.status_code == 403

    # Interactive browser session succeeds
    assert email_helpers.require_email_browser_session(_request(api_token=False)) == "alice"


@pytest.mark.parametrize("path,method,expected_dep", [
    ("/api/email/list", "GET", email_helpers.require_email_read),
    ("/api/email/search", "GET", email_helpers.require_email_read),
    ("/api/email/draft", "POST", email_helpers.require_email_draft),
    ("/api/email/ai-reply", "POST", email_helpers.require_email_draft),
    ("/api/email/send", "POST", email_helpers.require_email_send),
    ("/api/email/schedule", "POST", email_helpers.require_email_send),
    ("/api/email/accounts", "GET", email_helpers.require_email_browser_session),
    ("/api/email/accounts/{account_id}", "DELETE", email_helpers.require_email_browser_session),
    ("/api/email/delete-permanent/{uid}", "DELETE", email_helpers.require_email_browser_session),
    ("/api/email/oauth/google/authorize", "GET", email_helpers.require_email_browser_session),
])
def test_email_routes_bind_expected_authorization_dependencies(path, method, expected_dep):
    assert _endpoint_dep(path, method) is expected_dep
