"""Issue #1186 — route-level regression for the mid-chat model switch.

Drives the real ``PATCH /api/session/{sid}`` handler via TestClient, proving:
  - keyed -> keyed: the new endpoint's key replaces the old one, and persists;
  - keyed -> unknown URL: stale Authorization is cleared (not inferred);
  - the URL match is owner-scoped (never resolves another user's key).

Binds a DEDICATED temporary SQLite engine and patches the route module's
``SessionLocal`` to it (rather than ``DATABASE_URL`` at import), so the test never
touches the real dev DB, is import-order independent, and won't contend for the
dev DB's locks (which could hang). A fresh router is reset per app because
session_routes uses a module-level router that setup_session_routes re-decorates.
"""

import tempfile
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient

import core.database as cdb
import routes.session_routes as sroutes
from core.database import ModelEndpoint
from core.database import Session as DbSession

_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(
    f"sqlite:///{_TMPDB.name}",
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)


@pytest.fixture(autouse=True)
def _bind_db(monkeypatch):
    monkeypatch.setattr(sroutes, "SessionLocal", _TS)
    yield


class FakeSessionManager:
    def __init__(self):
        self.sessions = {}

    def get_session(self, sid):
        if sid not in self.sessions:
            raise KeyError(sid)
        return self.sessions[sid]

    def update_session_name(self, *a, **k):
        pass


def _client(sm):
    # session_routes uses a MODULE-LEVEL router that setup_session_routes
    # re-decorates; reset it so each app's handlers bind to THIS test's manager
    # (otherwise repeated setup calls accumulate duplicate routes).
    sroutes.router = APIRouter(prefix="/api", tags=["sessions"])
    app = FastAPI()

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.current_user = "tester"
        return await call_next(request)

    app.include_router(sroutes.setup_session_routes(sm, {"REQUEST_TIMEOUT": 5}))
    return TestClient(app)


def _seed_endpoint(owner, base, key):
    db = _TS()
    try:
        db.add(ModelEndpoint(id="ep-" + uuid.uuid4().hex[:8], name="ep",
                             base_url=base, api_key=key, owner=owner, is_enabled=True))
        db.commit()
    finally:
        db.close()


def _seed_session(sm, owner="tester"):
    sid = "sess-" + uuid.uuid4().hex[:8]
    db = _TS()
    try:
        db.add(DbSession(id=sid, owner=owner, name="s", model="old-model",
                         endpoint_url="https://api.groq.com/openai/v1",
                         headers={"Authorization": "Bearer OLD_GROQ"}))
        db.commit()
    finally:
        db.close()
    sm.sessions[sid] = SimpleNamespace(
        model="old-model",
        endpoint_url="https://api.groq.com/openai/v1",
        headers={"Authorization": "Bearer OLD_GROQ"},
    )
    return sid


def _db_headers(sid):
    db = _TS()
    try:
        return db.query(DbSession).filter(DbSession.id == sid).first().headers
    finally:
        db.close()


def test_switch_keyed_to_keyed_uses_new_key_and_persists():
    sm = FakeSessionManager()
    client = _client(sm)
    url = "https://k2k.example/v1"
    _seed_endpoint("tester", url, "CEREB_KEY")
    sid = _seed_session(sm)

    r = client.patch(f"/api/session/{sid}", data={"model": "gpt-oss-120b", "endpoint_url": url})
    assert r.status_code == 200, r.text

    assert sm.sessions[sid].headers.get("Authorization") == "Bearer CEREB_KEY"
    assert "OLD_GROQ" not in str(sm.sessions[sid].headers)
    assert _db_headers(sid).get("Authorization") == "Bearer CEREB_KEY"  # persisted


def test_switch_to_unknown_url_clears_stale_key():
    sm = FakeSessionManager()
    client = _client(sm)
    sid = _seed_session(sm)

    r = client.patch(f"/api/session/{sid}",
                     data={"model": "m", "endpoint_url": "https://nomatch.example/v1"})
    assert r.status_code == 200, r.text
    assert "Authorization" not in sm.sessions[sid].headers
    assert "Authorization" not in _db_headers(sid)


def test_url_match_is_owner_scoped():
    sm = FakeSessionManager()
    client = _client(sm)
    url = "https://scoped.example/v1"
    # Same URL, but the endpoint belongs to a DIFFERENT user — its key must not leak.
    _seed_endpoint("someone_else", url, "OTHER_KEY")
    sid = _seed_session(sm)

    r = client.patch(f"/api/session/{sid}", data={"model": "m", "endpoint_url": url})
    assert r.status_code == 200, r.text
    assert "OTHER_KEY" not in str(sm.sessions[sid].headers)
    assert "Authorization" not in sm.sessions[sid].headers
