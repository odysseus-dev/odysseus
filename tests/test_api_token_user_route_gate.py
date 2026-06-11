import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src import auth_helpers


def _request(*, current_user="api", api_token=True, api_token_owner="alice"):
    return SimpleNamespace(
        state=SimpleNamespace(
            current_user=current_user,
            api_token=api_token,
            api_token_owner=api_token_owner,
        ),
        app=SimpleNamespace(
            state=SimpleNamespace(
                auth_manager=SimpleNamespace(is_configured=True),
            ),
        ),
        client=SimpleNamespace(host="203.0.113.10"),
    )


def test_require_user_rejects_api_token_pseudo_user(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    req = _request()

    with pytest.raises(HTTPException) as exc:
        auth_helpers.require_user(req)

    assert exc.value.status_code == 403


def test_require_authenticated_request_allows_api_token_owner(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    req = _request()

    assert auth_helpers.require_authenticated_request(req) == "alice"


def test_codex_as_owner_can_call_nested_user_routes(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    from routes.codex_routes import _as_owner

    req = _request()

    async def nested_handler(request):
        return auth_helpers.require_user(request)

    assert asyncio.run(_as_owner(req, "alice", nested_handler, req)) == "alice"
    assert req.state.current_user == "api"
    assert req.state.api_token is True


def test_codex_plugin_downloads_use_general_authenticated_gate():
    source = Path("routes/codex_routes.py").read_text(encoding="utf-8")

    assert "require_authenticated_request" in source
    assert source.count("require_authenticated_request(request)") == 2
