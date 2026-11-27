"""Session listings expose the persisted `thinking_mode`.

GET /api/sessions (and the archive listing) omitted it, so the composer's
reasoning-effort picker, which reads it from that list, showed Default after
every reload or list refetch even though the effort was stored.
"""
import sys
import tempfile
import types
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import Session as DbSession

_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(
    f"sqlite:///{_TMPDB.name}",
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)

CHATGPT_MODEL = "gpt-6-astra"  # has advertised reasoning levels without a network fetch


def _route(router, path, method="GET"):
    for r in router.routes:
        if r.path == path and method in getattr(r, "methods", set()):
            return r.endpoint
    raise AssertionError(f"route not found: {path}")


def _stub_multipart_if_missing(monkeypatch):
    try:
        import python_multipart  # noqa: F401
        return
    except ImportError:
        pass
    stub = types.ModuleType("python_multipart")
    stub.__version__ = "0.0.20"
    monkeypatch.setitem(sys.modules, "python_multipart", stub)


def _seed(thinking_mode, archived=False):
    sid = str(uuid.uuid4())
    db = _TS()
    try:
        db.query(DbSession).delete()
        db.add(DbSession(id=sid, owner="alice", name="chat", endpoint_url="http://localhost",
                         model=CHATGPT_MODEL, archived=archived, message_count=2,
                         thinking_mode=thinking_mode))
        db.commit()
    finally:
        db.close()
    return sid


@pytest.fixture
def session_router(monkeypatch):
    import routes.session_routes as sr

    _stub_multipart_if_missing(monkeypatch)
    monkeypatch.setattr(sr, "SessionLocal", _TS)
    monkeypatch.setattr(sr, "effective_user", lambda request: "alice")
    manager = MagicMock()
    manager.get_sessions_for_user.return_value = {}
    return sr.setup_session_routes(manager, {})


def test_session_list_exposes_persisted_thinking_mode(session_router):
    _seed("effort:high")
    request = SimpleNamespace(query_params={})
    res = _route(session_router, "/api/sessions")(request=request)
    entries = res["sessions"] if isinstance(res, dict) else res
    assert [e["thinking_mode"] for e in entries] == ["effort:high"]


def test_session_list_reports_off_when_nothing_stored(session_router):
    _seed(None)
    request = SimpleNamespace(query_params={})
    res = _route(session_router, "/api/sessions")(request=request)
    entries = res["sessions"] if isinstance(res, dict) else res
    assert [e["thinking_mode"] for e in entries] == ["off"]


def test_archived_list_exposes_persisted_thinking_mode(session_router):
    _seed("effort:low", archived=True)
    res = _route(session_router, "/api/sessions/archived")(request=None)
    assert [s["thinking_mode"] for s in res["sessions"]] == ["effort:low"]
