"""U7: Odysseus stores opaque 9router connection projections, never provider tokens.

Covers AE5, R16–R20, R23, F4, KTD3, KTD6, KTD8. Live 9router PKCE in a
browser is honest-skipped; the completion contract is enforced here.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, ModelEndpoint, ProviderAuthSession
import routes.chatgpt_subscription_routes as csr


_REPO = Path(__file__).resolve().parent.parent
_SECRET_ACCESS = "sk-live-access-token-SHOULD-NOT-PERSIST"
_SECRET_REFRESH = "rt-live-refresh-token-SHOULD-NOT-PERSIST"


def _mem_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(
        csr.chatgpt_subscription,
        "_database_handles",
        lambda: (ProviderAuthSession, TestSessionLocal, lambda: None),
    )
    return TestSessionLocal


def _projection(connection_id="conn-9r-alice", status="usable", entitlement="codex"):
    return {
        "connection_id": connection_id,
        "status": status,
        "entitlement": entitlement,
        "label": "9router",
    }


def test_ae5_successful_connect_stores_opaque_id_without_provider_tokens(monkeypatch, caplog):
    TestSessionLocal = _mem_db(monkeypatch)
    caplog.set_level(logging.DEBUG)

    res = csr._provision_connection(
        {
            **_projection(),
            "access_token": _SECRET_ACCESS,
            "refresh_token": _SECRET_REFRESH,
            "api_key": "sk-should-also-be-dropped",
        },
        "alice",
    )

    assert res["connection_id"] == "conn-9r-alice"
    assert res["status"] == "usable"
    assert res["owner"] == "alice"
    for forbidden in ("access_token", "refresh_token", "api_key", "base_url"):
        assert forbidden not in res

    db = TestSessionLocal()
    try:
        auth = db.query(ProviderAuthSession).one()
        assert auth.owner == "alice"
        assert auth.connection_id == "conn-9r-alice"
        assert auth.status == "usable"
        assert auth.entitlement == "codex"
        assert auth.access_token is None
        assert auth.refresh_token is None
        assert db.query(ModelEndpoint).count() == 0
    finally:
        db.close()

    logged = caplog.text
    assert _SECRET_ACCESS not in logged
    assert _SECRET_REFRESH not in logged


def test_oauth_failure_shows_product_error_and_stores_no_refresh_token(monkeypatch):
    TestSessionLocal = _mem_db(monkeypatch)
    request = SimpleNamespace()
    pending = {"owner": "alice", "oauth_error": "access_denied"}

    outcome = csr._poll_device_flow(request, pending)

    assert outcome.status == "failed"
    assert outcome.error
    db = TestSessionLocal()
    try:
        rows = db.query(ProviderAuthSession).all()
        assert rows == []
        leftover = [
            row.refresh_token
            for row in rows
            if getattr(row, "refresh_token", None)
        ]
        assert leftover == []
    finally:
        db.close()


def test_device_flow_completion_never_exchanges_code_verifier_into_db(monkeypatch):
    TestSessionLocal = _mem_db(monkeypatch)

    def _forbidden_exchange(*_args, **_kwargs):
        raise AssertionError("Odysseus must not exchange code_verifier or store tokens")

    monkeypatch.setattr(csr.chatgpt_subscription, "exchange_authorization_code", _forbidden_exchange)
    monkeypatch.setattr(csr.chatgpt_subscription, "poll_device_auth", lambda *_a, **_k: {
        "authorization_code": "ac-should-be-ignored",
        "code_verifier": "cv-should-be-ignored",
        "access_token": _SECRET_ACCESS,
        "refresh_token": _SECRET_REFRESH,
    })

    providers = [
        {
            "id": "conn-9r-alice",
            "name": "Codex",
            "provider": "codex",
            "testStatus": "valid",
            "accessToken": _SECRET_ACCESS,
            "refreshToken": _SECRET_REFRESH,
            "apiKey": "sk-leaked",
            "baseUrl": "https://chatgpt.com/backend-api/codex",
        }
    ]
    monkeypatch.setattr(csr, "_list_redacted_providers", lambda: providers)

    outcome = csr._poll_device_flow(
        SimpleNamespace(),
        {"owner": "alice", "device_auth_id": "d", "user_code": "u"},
    )

    assert outcome.status == "authorized"
    assert outcome.endpoint["connection_id"] == "conn-9r-alice"
    db = TestSessionLocal()
    try:
        auth = db.query(ProviderAuthSession).one()
        assert auth.access_token is None
        assert auth.refresh_token is None
        assert auth.connection_id == "conn-9r-alice"
    finally:
        db.close()


def test_two_owners_cannot_use_each_others_connection_id(monkeypatch):
    _mem_db(monkeypatch)
    csr._provision_connection(_projection("conn-shared-looking"), "alice")

    with pytest.raises(csr.chatgpt_subscription.ChatGPTSubscriptionAuthNotFound):
        csr.chatgpt_subscription.get_owner_connection("conn-shared-looking", "bob")

    alice = csr.chatgpt_subscription.get_owner_connection("conn-shared-looking", "alice")
    assert alice["connection_id"] == "conn-shared-looking"
    assert alice["owner"] == "alice"
    assert "access_token" not in alice
    assert "refresh_token" not in alice
    assert "api_key" not in alice


def test_resolve_runtime_credentials_never_returns_upstream_tokens(monkeypatch):
    TestSessionLocal = _mem_db(monkeypatch)
    monkeypatch.setattr(csr.chatgpt_subscription, "_database_handles", lambda: (
        ProviderAuthSession, TestSessionLocal, lambda: None,
    ))
    db = TestSessionLocal()
    try:
        db.add(ProviderAuthSession(
            id="auth-legacy",
            provider=csr.chatgpt_subscription.CHATGPT_SUBSCRIPTION_PROVIDER,
            owner="alice",
            label="legacy",
            base_url="https://chatgpt.com/backend-api/codex",
            access_token=_SECRET_ACCESS,
            refresh_token=_SECRET_REFRESH,
            auth_mode="chatgpt",
            connection_id="conn-legacy",
            status="usable",
        ))
        db.commit()
    finally:
        db.close()

    creds = csr.chatgpt_subscription.resolve_runtime_credentials("auth-legacy", owner="alice")
    assert creds.get("api_key") in (None, "")
    assert creds.get("access_token") in (None, "", None)
    dumped = json.dumps(creds)
    assert _SECRET_ACCESS not in dumped
    assert _SECRET_REFRESH not in dumped


def test_curated_chat_routes_omit_raw_urls_and_keys():
    from services.ninerouter.metadata import build_curated_chat_routes

    routes = build_curated_chat_routes([
        {
            "id": "p1",
            "name": "Codex",
            "apiKey": "sk-secret",
            "accessToken": "AT",
            "refreshToken": "RT",
            "baseUrl": "https://api.openai.com/v1",
            "aliases": ["fast", "best"],
        }
    ])
    ids = [row["id"] for row in routes]
    assert ids[0] == "automatic"
    assert "fast" in ids
    assert "best" in ids
    assert "balanced" not in ids
    blob = json.dumps(routes)
    assert "sk-secret" not in blob
    assert "https://api.openai.com" not in blob
    assert "api_key" not in blob
    assert "base_url" not in blob
    assert "accessToken" not in blob
    for row in routes:
        assert set(row) <= {"id", "label"}


def test_curated_picker_source_has_no_raw_key_fields():
    picker = (_REPO / "static" / "js" / "modelPicker.js").read_text(encoding="utf-8")
    assert "/api/chat-routes" in picker
    assert "api_key" not in picker
    assert "apiKey" not in picker
    assert "item.url" not in picker


def test_metadata_client_allowlists_health_providers_usage_only():
    from services.ninerouter.metadata import (
        NineRouterMetadataClient,
        NineRouterMetadataError,
    )

    seen = []

    def fetch(path, headers):
        seen.append(path)
        return {"ok": True}

    client = NineRouterMetadataClient(base_url="http://9router:20128", fetch=fetch)
    assert client.get("/api/health") == {"ok": True}
    assert client.get("/api/providers") == {"ok": True}
    assert client.get("/api/usage/stats") == {"ok": True}
    assert seen == ["/api/health", "/api/providers", "/api/usage/stats"]
    with pytest.raises(NineRouterMetadataError, match="allowlist"):
        client.get("/v1/chat/completions")
    with pytest.raises(NineRouterMetadataError, match="allowlist"):
        client.get("/v1/models")
    with pytest.raises(NineRouterMetadataError, match="allowlist"):
        client.get("/api/oauth/codex/authorize")
    with pytest.raises(NineRouterMetadataError, match="allowlist"):
        client.get("/api/providers/validate")
    assert "/v1/chat/completions" not in seen


def test_start_device_flow_opens_upstream_idp_not_9router_dashboard(monkeypatch):
    monkeypatch.setattr(csr, "get_current_user", lambda _request: "alice")

    class Fake:
        def start_oauth(self, provider, redirect_uri):
            assert provider
            assert "callback" in redirect_uri
            return {"authorization_url": "https://auth.openai.com/authorize?client_id=x"}

    monkeypatch.setattr(csr, "NineRouterConnectClient", lambda: Fake())
    start = csr._start_device_flow(SimpleNamespace(), {})
    redirect = start.response.get("redirect_url") or start.response.get("verification_uri")
    assert redirect.startswith("https://auth.openai.com/")
    assert "/dashboard/providers" not in redirect
    assert start.pending["owner"] == "alice"
    assert "code_verifier" not in start.pending


@pytest.mark.skip(reason="live 9router PKCE needs a browser user; unit contract covers completion")
def test_live_9router_pkce_browser_hop():
    raise AssertionError("must stay skipped without an interactive browser user")
