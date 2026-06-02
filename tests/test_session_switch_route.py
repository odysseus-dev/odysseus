"""Issue #1186 — route-level regression for the mid-chat model switch.

Drives the real ``PATCH /api/session/{sid}`` handler via TestClient against a
temporary SQLite DB, proving:
  - keyed -> keyed: the new endpoint's key replaces the old one, and persists;
  - keyed -> unknown URL: stale Authorization is cleared (not inferred);
  - the URL match is owner-scoped (never resolves another user's key).

Mirrors tests/test_document_close_clears_active_route.py: point DATABASE_URL at a
throwaway file BEFORE importing core.database so every path uses one consistent
engine. Each test uses a UNIQUE endpoint URL so no cross-test collision.
"""

import os
import tempfile
import uuid

_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_TMPDB.name}"

from types import SimpleNamespace  # noqa: E402

from fastapi import APIRouter, FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core.database import Base, engine, SessionLocal, ModelEndpoint  # noqa: E402
from core.database import Session as DbSession  # noqa: E402
import routes.session_routes as sroutes  # noqa: E402
from routes.session_routes import setup_session_routes  # noqa: E402

Base.metadata.create_all(engine)


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
    # session_routes uses a MODULE-LEVEL router; setup_session_routes re-decorates
    # it, so calling it per-test would accumulate duplicate routes (the first,
    # bound to an earlier session_manager, would then handle every request). Reset
    # to a fresh router so each app gets handlers bound to THIS test's manager.
    sroutes.router = APIRouter(prefix="/api", tags=["sessions"])
    app = FastAPI()

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.current_user = "tester"
        return await call_next(request)

    app.include_router(setup_session_routes(sm, {"REQUEST_TIMEOUT": 5}))
    return TestClient(app)


def _seed_endpoint(owner, base, key):
    db = SessionLocal()
    try:
        db.add(ModelEndpoint(id="ep-" + uuid.uuid4().hex[:8], name="ep",
                             base_url=base, api_key=key, owner=owner, is_enabled=True))
        db.commit()
    finally:
        db.close()


def _seed_session(sm, owner="tester"):
    sid = "sess-" + uuid.uuid4().hex[:8]
    db = SessionLocal()
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
    db = SessionLocal()
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
