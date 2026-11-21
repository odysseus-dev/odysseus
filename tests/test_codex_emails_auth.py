"""Regression tests for #6649: /api/codex/emails Query defaults and API token error masking."""

import inspect
import json
from pathlib import Path

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.params import Param
from fastapi.testclient import TestClient
from starlette.requests import Request

import app as app_module
from routes import codex_routes
from routes.email import email_routes


def _endpoint(router, method: str, path: str):
    for route in router.routes:
        if route.path == path and method in route.methods:
            return route.endpoint
    raise AssertionError(f"{method} {path} route not found")


def _api_request(path: str, scopes: list[str]) -> Request:
    request = Request({
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
        "state": {},
        "app": FastAPI(),
    })
    request.state.current_user = "api"
    request.state.api_token = True
    request.state.api_token_owner = "alice"
    request.state.api_token_scopes = scopes
    return request


@pytest.mark.asyncio
async def test_codex_list_emails_forwards_clean_defaults_to_email_list_endpoint(monkeypatch, tmp_path):
    """Direct invocation of email_routes /api/email/list from codex_routes must not pass Query() objects."""
    email_router = email_routes.setup_email_routes()
    email_list_ep = _endpoint(email_router, "GET", "/api/email/list")

    for name, param in inspect.signature(email_list_ep).parameters.items():
        if name == "owner":
            continue
        assert not isinstance(param.default, Param), f"{name} still uses a FastAPI Param default"

    monkeypatch.setattr(email_routes, "DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ODYSSEUS_EMAIL_FIXTURE", "1")
    fixture_file = Path(tmp_path) / "fixture_email_messages.json"
    fixture_file.write_text(
        json.dumps([
            {
                "uid": "101",
                "folder": "INBOX",
                "owner": "alice",
                "subject": "Hello from inbox",
                "from": "sender@example.com",
                "to": "alice@example.com",
                "date": "2026-10-09T12:00:00Z",
            }
        ]),
        encoding="utf-8",
    )

    codex_router = codex_routes.setup_codex_routes(email_router=email_router)
    codex_list_ep = _endpoint(codex_router, "GET", "/api/codex/emails")

    result = await codex_list_ep(_api_request("/api/codex/emails", ["email:read"]))
    assert result["total"] == 1
    assert result["emails"][0]["uid"] == "101"


def test_bearer_token_middleware_does_not_mask_downstream_route_exception_as_401(monkeypatch):
    """Valid bearer tokens must propagate downstream route errors rather than returning 401."""
    raw_token = "ody_tok_testsecretvalue1234567890"
    prefix = raw_token[:8]
    token_hash = bcrypt.hashpw(raw_token.encode(), bcrypt.gensalt(rounds=4)).decode()

    monkeypatch.setattr(app_module, "LOCALHOST_BYPASS", False)
    monkeypatch.setattr(type(app_module.auth_manager), "is_configured", property(lambda self: True))
    monkeypatch.setattr(app_module, "_refresh_token_cache", lambda: None)
    monkeypatch.setattr(app_module.app.state, "_token_cache_dirty", False, raising=False)
    monkeypatch.setattr(
        app_module,
        "_token_cache",
        {prefix: [("tok-1", token_hash, "alice", ["email:read"])]},
    )

    @app_module.app.get("/api/codex/_test_boom_6649")
    async def _boom():
        raise TypeError("simulated downstream handler crash")

    try:
        client = TestClient(app_module.app, raise_server_exceptions=False)
        resp = client.get(
            "/api/codex/_test_boom_6649",
            headers={"Authorization": f"Bearer {raw_token}"},
        )
        assert resp.status_code == 500
    finally:
        app_module.app.router.routes = [
            r for r in app_module.app.router.routes if getattr(r, "path", None) != "/api/codex/_test_boom_6649"
        ]
