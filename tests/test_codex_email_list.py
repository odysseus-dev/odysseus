"""Email integration calls use ordinary defaults while retaining HTTP aliases."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def email_client(tmp_path, monkeypatch):
    from routes.email import email_routes
    from routes.codex_routes import setup_codex_routes

    monkeypatch.setenv("ODYSSEUS_EMAIL_FIXTURE", "1")
    monkeypatch.setattr(email_routes, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(email_routes, "_start_poller", lambda: None)
    monkeypatch.setattr(email_routes, "_POOL_HOOKS", {})
    (tmp_path / "fixture_email_messages.json").write_text(json.dumps({"messages": [
        {"owner": "alice", "uid": "10", "folder": "INBOX", "subject": "Picnic",
         "from": "Sender <sender@example.com>", "from_address": "sender@example.com"},
        {"owner": "bob", "uid": "20", "folder": "INBOX", "subject": "Private"},
    ]}))

    app = FastAPI()
    app.state.scopes = ["email:read"]

    @app.middleware("http")
    async def verified_integration_identity(request, call_next):
        request.state.current_user = "api"
        request.state.api_token = True
        request.state.api_token_owner = "alice"
        request.state.api_token_scopes = app.state.scopes
        return await call_next(request)

    email_router = email_routes.setup_email_routes()
    app.include_router(email_router)
    app.include_router(setup_codex_routes(email_router=email_router))
    app.dependency_overrides[email_routes.require_owner] = lambda: "alice"
    with TestClient(app) as client:
        yield client


def test_codex_email_listing_calls_real_handler_with_defaults(email_client):
    response = email_client.get("/api/codex/emails")
    assert response.status_code == 200, response.text
    assert [row["subject"] for row in response.json()["emails"]] == ["Picnic"]


def test_codex_email_listing_still_requires_email_scope(email_client):
    email_client.app.state.scopes = ["chat"]
    assert email_client.get("/api/codex/emails").status_code == 403


def test_email_list_http_alias_and_date_validation_are_preserved(email_client):
    response = email_client.get("/api/email/list", params={"from": "nobody@example.com", "_": "fresh"})
    assert response.status_code == 200
    assert response.json()["emails"] == []
    for params in ({"date_from": "invalid"}, {"date_to": "invalid"},
                   {"date_from": "2026-10-22", "date_to": "2026-10-21"}):
        response = email_client.get("/api/email/list", params=params)
        assert response.status_code == 400, response.text
