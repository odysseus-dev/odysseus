"""Issue #1186 — route-level regression for the mid-chat model switch.

Proves the real ``PATCH /api/session/{sid}`` handler:
  - keyed -> keyed: the new endpoint's key replaces the old one, and persists;
  - keyed -> unknown URL: stale Authorization is cleared (not inferred);
  - the URL match is owner-scoped (never resolves another user's key).

Calls the sync route handler DIRECTLY (extracted from the router) rather than via
Starlette's TestClient — TestClient's middleware-app + threadpool hung in the
maintainer's environment (same pattern fixed on #1238/#1282). A direct call with a
minimal fake request keeps the real coverage (handler + DB + owner routing) and
completes reliably.
"""

import tempfile
import uuid
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from fastapi import APIRouter

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
sroutes.SessionLocal = _TS  # handlers resolve SessionLocal at call time


class FakeSessionManager:
    def __init__(self):
        self.sessions = {}

    def get_session(self, sid):
        if sid not in self.sessions:
            raise KeyError(sid)
        return self.sessions[sid]

    def update_session_name(self, *a, **k):
        pass


def _patch_handler(sm):
    # session_routes uses a module-level router that setup re-decorates; reset it
    # so we extract THIS sm's handler (no duplicate-route accumulation).
    sroutes.router = APIRouter(prefix="/api", tags=["sessions"])
    router = sroutes.setup_session_routes(sm, {"REQUEST_TIMEOUT": 5})
    for r in router.routes:
        if getattr(r, "path", None) == "/api/session/{sid}" and "PATCH" in getattr(r, "methods", set()):
            return r.endpoint
    raise RuntimeError("PATCH /api/session/{sid} not found")


def _req():
    return SimpleNamespace(state=SimpleNamespace(current_user="tester"))


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


def _switch(handler, sid, model, endpoint_url):
    # Pass every param explicitly so the Form(None) defaults are overridden.
    return handler(_req(), sid, name=None, folder=None,
                   model=model, endpoint_url=endpoint_url, endpoint_id=None)


def test_switch_keyed_to_keyed_uses_new_key_and_persists():
    sm = FakeSessionManager()
    handler = _patch_handler(sm)
    url = "https://k2k.example/v1"
    _seed_endpoint("tester", url, "CEREB_KEY")
    sid = _seed_session(sm)

    _switch(handler, sid, "gpt-oss-120b", url)
    assert sm.sessions[sid].headers.get("Authorization") == "Bearer CEREB_KEY"
    assert "OLD_GROQ" not in str(sm.sessions[sid].headers)
    assert _db_headers(sid).get("Authorization") == "Bearer CEREB_KEY"  # persisted


def test_switch_to_unknown_url_clears_stale_key():
    sm = FakeSessionManager()
    handler = _patch_handler(sm)
    sid = _seed_session(sm)

    _switch(handler, sid, "m", "https://nomatch.example/v1")
    assert "Authorization" not in sm.sessions[sid].headers
    assert "Authorization" not in _db_headers(sid)


def test_url_match_is_owner_scoped():
    sm = FakeSessionManager()
    handler = _patch_handler(sm)
    url = "https://scoped.example/v1"
    _seed_endpoint("someone_else", url, "OTHER_KEY")  # different user's endpoint
    sid = _seed_session(sm)

    _switch(handler, sid, "m", url)
    assert "OTHER_KEY" not in str(sm.sessions[sid].headers)
    assert "Authorization" not in sm.sessions[sid].headers
