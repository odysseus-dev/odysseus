"""BFF route tests: 9router connect via Odysseus, never ModelEndpoint keys.

Agents: inject NineRouterConnectClient via monkeypatch. Catalog fails closed
when connect raises NineRouterConnectError. API-key and OAuth persist only
opaque ProviderAuthSession projections (connection_id, no tokens).
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base, ModelEndpoint, ProviderAuthSession
from services.ninerouter.connect import NineRouterConnectError
import routes.ninerouter_connection_routes as ncr
import src.chatgpt_subscription as chatgpt_subscription


def _app(monkeypatch, connect):
    """In-memory DB + fake connect client + authenticated owner alice."""
    # StaticPool: sqlite :memory: is per-connection without it (SQLAlchemy 2 +
    # --noconftest). Guest image tests share this helper contract.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(
        chatgpt_subscription,
        "_database_handles",
        lambda: (ProviderAuthSession, TestSessionLocal, lambda: None),
    )
    monkeypatch.setattr(ncr, "get_current_user", lambda _request: "alice")
    monkeypatch.setattr(ncr, "NineRouterConnectClient", lambda: connect)
    app = FastAPI()
    app.include_router(ncr.setup_ninerouter_connection_routes())
    return app, TestSessionLocal


class FakeConnect:
    """Stub connect client; hooks override list/delete side effects."""

    def __init__(self, **hooks):
        self.hooks = hooks

    def list_providers(self):
        return self.hooks.get("list", [{"id": "conn-1", "name": "OpenAI", "status": "usable"}])

    def create_api_key(self, provider, api_key):
        assert api_key == "sk-once"
        return {"id": "conn-new", "status": "usable", "name": provider}

    def start_oauth(self, provider, redirect_uri):
        assert "oauth/callback" in redirect_uri
        return {"authorization_url": "https://auth.openai.com/authorize?x=1"}

    def complete_oauth(self, provider, code, state=None):
        return {"id": "conn-oauth", "status": "usable", "entitlement": provider}

    def delete_connection(self, connection_id):
        self.hooks["deleted"] = connection_id


def test_catalog_redacted_when_9router_ok(monkeypatch):
    app, _ = _app(monkeypatch, FakeConnect())
    data = TestClient(app).get("/api/ninerouter/connections").json()
    assert data["ok"] is True
    assert data["providers"][0]["id"] == "conn-1"


def test_catalog_unhealthy_when_9router_down(monkeypatch):
    class Down(FakeConnect):
        def list_providers(self):
            raise NineRouterConnectError("down")

    app, _ = _app(monkeypatch, Down())
    data = TestClient(app).get("/api/ninerouter/connections").json()
    assert data["ok"] is False
    assert data["providers"] == []


def test_api_key_connect_stores_projection_not_key(monkeypatch):
    app, Session = _app(monkeypatch, FakeConnect())
    res = TestClient(app).post(
        "/api/ninerouter/connections",
        data={"provider": "openai", "api_key": "sk-once"},
    )
    body = res.json()
    assert res.status_code == 200
    assert body["connection_id"] == "conn-new"
    assert "api_key" not in body
    db = Session()
    try:
        assert db.query(ModelEndpoint).count() == 0
        auth = db.query(ProviderAuthSession).one()
        assert auth.connection_id == "conn-new"
        assert auth.access_token is None
        assert auth.owner == "alice"
    finally:
        db.close()


def test_oauth_callback_discards_token_query_params(monkeypatch):
    app, Session = _app(monkeypatch, FakeConnect())
    res = TestClient(app).get(
        "/api/ninerouter/connections/oauth/callback",
        params={
            "code": "abc",
            "provider": "codex",
            "access_token": "sk-leak",
            "refresh_token": "rt-leak",
        },
    )
    assert res.status_code == 200
    assert "sk-leak" not in res.text
    db = Session()
    try:
        auth = db.query(ProviderAuthSession).one()
        assert auth.connection_id == "conn-oauth"
        assert auth.access_token is None
    finally:
        db.close()


def test_oauth_start_returns_idp_not_dashboard(monkeypatch):
    app, _ = _app(monkeypatch, FakeConnect())
    data = TestClient(app).post(
        "/api/ninerouter/connections/oauth/start",
        data={"provider": "codex"},
    ).json()
    assert "auth.openai.com" in data["authorization_url"]
    assert "/dashboard/providers" not in data["authorization_url"]


def test_delete_connection_calls_connect_client(monkeypatch):
    fake = FakeConnect()
    app, _ = _app(monkeypatch, fake)
    res = TestClient(app).delete("/api/ninerouter/connections/conn-1")
    assert res.status_code == 200
    assert fake.hooks["deleted"] == "conn-1"
