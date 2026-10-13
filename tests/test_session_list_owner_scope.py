"""list_sessions must return only the authenticated user's sessions.

Regression for the enrichment query at routes/session_routes.py:265 which
previously fetched rows for all owners on every GET /api/sessions call.
"""
import sys
import tempfile
import types
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import ChatMessage as DbMessage
from core.database import Session as DbSession

_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(
    f"sqlite:///{_TMPDB.name}",
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)


def _stub_multipart_if_missing(monkeypatch):
    try:
        import python_multipart  # noqa: F401
        return
    except ImportError:
        pass
    stub = types.ModuleType("python_multipart")
    stub.__version__ = "0.0.20"
    monkeypatch.setitem(sys.modules, "python_multipart", stub)


def test_list_sessions_excludes_other_users_sessions(monkeypatch):
    import routes.session_routes as sr
    from unittest.mock import MagicMock

    _stub_multipart_if_missing(monkeypatch)
    monkeypatch.setattr(sr, "SessionLocal", _TS)
    monkeypatch.setattr(sr, "effective_user", lambda request: "alice")

    alice_id = str(uuid.uuid4())
    bob_id = str(uuid.uuid4())
    db = _TS()
    try:
        db.query(DbSession).delete()
        db.add(DbSession(id=alice_id, owner="alice", name="alice session",
                         endpoint_url="http://localhost", model="gpt-4", archived=False))
        db.add(DbSession(id=bob_id, owner="bob", name="bob session",
                         endpoint_url="http://localhost", model="gpt-4", archived=False))
        db.commit()
    finally:
        db.close()

    alice_session = MagicMock(id=alice_id, name="alice session",
                              model="gpt-4", endpoint_url="http://localhost",
                              rag=False, archived=False)
    sm = MagicMock()
    sm.get_sessions_for_user.return_value = {alice_id: alice_session}
    router = sr.setup_session_routes(sm, {})
    endpoint = [
        r.endpoint for r in router.routes
        if getattr(r, "path", "") == "/api/sessions"
        and "GET" in getattr(r, "methods", set())
    ][-1]

    result = endpoint(request=MagicMock())
    returned_ids = {s["id"] for s in result}
    assert alice_id in returned_ids
    assert bob_id not in returned_ids


def test_list_sessions_uses_db_when_memory_cache_misses(monkeypatch):
    import routes.session_routes as sr
    from unittest.mock import MagicMock

    _stub_multipart_if_missing(monkeypatch)
    monkeypatch.setattr(sr, "SessionLocal", _TS)
    monkeypatch.setattr(sr, "effective_user", lambda request: "alice")

    visible_id = str(uuid.uuid4())
    hidden_id = str(uuid.uuid4())
    bob_id = str(uuid.uuid4())
    db = _TS()
    try:
        db.query(DbMessage).delete()
        db.query(DbSession).delete()
        db.add(DbSession(id=visible_id, owner="alice", name="visible persisted session",
                         endpoint_url="http://localhost", model="gpt-4", archived=False,
                         message_count=2))
        db.add(DbSession(id=hidden_id, owner="alice", name="SFT trace batch alice email 001",
                         endpoint_url="http://localhost", model="gpt-4", archived=False,
                         message_count=2))
        db.add(DbSession(id=bob_id, owner="bob", name="bob session",
                         endpoint_url="http://localhost", model="gpt-4", archived=False,
                         message_count=2))
        db.commit()
    finally:
        db.close()

    sm = MagicMock()
    sm.get_sessions_for_user.return_value = {}
    router = sr.setup_session_routes(sm, {})
    endpoint = [
        r.endpoint for r in router.routes
        if getattr(r, "path", "") == "/api/sessions"
        and "GET" in getattr(r, "methods", set())
    ][-1]

    result = endpoint(request=MagicMock())
    returned_ids = {s["id"] for s in result}
    assert visible_id in returned_ids
    assert hidden_id not in returned_ids
    assert bob_id not in returned_ids


def test_list_sessions_returns_persisted_cwd(monkeypatch):
    import routes.session_routes as sr
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    _stub_multipart_if_missing(monkeypatch)
    monkeypatch.setattr(sr, "SessionLocal", _TS)
    monkeypatch.setattr(sr, "effective_user", lambda request: "alice")

    sid = str(uuid.uuid4())
    db = _TS()
    try:
        db.query(DbMessage).delete()
        db.query(DbSession).delete()
        db.add(DbSession(
            id=sid,
            owner="alice",
            name="workspace session",
            endpoint_url="http://localhost",
            model="gpt-4",
            archived=False,
            cwd="/home/pewds/odysseus-tui",
        ))
        db.commit()
    finally:
        db.close()

    session = SimpleNamespace(
        id=sid,
        name="workspace session",
        model="gpt-4",
        endpoint_url="http://localhost",
        rag=False,
        archived=False,
    )
    sm = MagicMock()
    sm.get_sessions_for_user.return_value = {sid: session}
    router = sr.setup_session_routes(sm, {})
    endpoint = [
        r.endpoint for r in router.routes
        if getattr(r, "path", "") == "/api/sessions"
        and "GET" in getattr(r, "methods", set())
    ][-1]

    result = endpoint(request=MagicMock())

    assert result[0]["cwd"] == "/home/pewds/odysseus-tui"


def test_patch_session_updates_persisted_cwd(monkeypatch):
    import routes.session_routes as sr
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    _stub_multipart_if_missing(monkeypatch)
    monkeypatch.setattr(sr, "SessionLocal", _TS)
    monkeypatch.setattr(sr, "effective_user", lambda request: "alice")
    monkeypatch.setattr(sr, "_verify_session_owner", lambda request, sid: None)

    sid = str(uuid.uuid4())
    db = _TS()
    try:
        db.query(DbMessage).delete()
        db.query(DbSession).delete()
        db.add(DbSession(
            id=sid,
            owner="alice",
            name="workspace session",
            endpoint_url="http://localhost",
            model="gpt-4",
            archived=False,
        ))
        db.commit()
    finally:
        db.close()

    session = SimpleNamespace(id=sid, cwd=None)
    sm = MagicMock()
    sm.get_session.return_value = session
    router = sr.setup_session_routes(sm, {})
    endpoint = [
        r.endpoint for r in router.routes
        if getattr(r, "path", "") == "/api/session/{sid}"
        and "PATCH" in getattr(r, "methods", set())
    ][-1]

    result = endpoint(
        request=MagicMock(),
        sid=sid,
        name=None,
        folder=None,
        model=None,
        endpoint_url=None,
        endpoint_id=None,
        cwd="/home/pewds/odysseus-tui",
    )

    assert result["cwd"] == "/home/pewds/odysseus-tui"
    db = _TS()
    try:
        row = db.query(DbSession).filter(DbSession.id == sid).first()
        assert row.cwd == "/home/pewds/odysseus-tui"
    finally:
        db.close()


def test_auto_sort_skip_llm_cleans_owner_stamped_sessions_when_auth_disabled(monkeypatch):
    import routes.session_routes as sr
    from unittest.mock import MagicMock

    _stub_multipart_if_missing(monkeypatch)
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setattr(sr, "SessionLocal", _TS)
    monkeypatch.setattr(sr, "effective_user", lambda request: None)

    sid = str(uuid.uuid4())
    old_time = cdb.utcnow_naive() - timedelta(hours=2)
    db = _TS()
    try:
        db.query(DbMessage).delete()
        db.query(DbSession).delete()
        db.add(DbSession(
            id=sid,
            owner="alice",
            name="New chat",
            endpoint_url="http://localhost",
            model="gpt-4",
            archived=False,
            message_count=1,
            created_at=old_time,
            updated_at=old_time,
            last_message_at=old_time,
            last_accessed=old_time,
        ))
        db.add(DbMessage(
            id="m-" + uuid.uuid4().hex,
            session_id=sid,
            role="user",
            content="hi",
            timestamp=old_time,
        ))
        db.commit()
    finally:
        db.close()

    session = MagicMock(id=sid, name="New chat", model="gpt-4", endpoint_url="http://localhost", rag=False, archived=False)
    sm = MagicMock()
    sm.get_sessions_for_user.return_value = {sid: session}
    router = sr.setup_session_routes(sm, {})
    endpoint = next(r.endpoint for r in router.routes
                    if getattr(r, "path", "") == "/api/sessions/auto-sort"
                    and "POST" in getattr(r, "methods", set()))

    result = endpoint(request=MagicMock(), skip_llm=True)

    assert result["deleted_throwaway"] == 1
    db = _TS()
    try:
        assert db.query(DbSession).filter(DbSession.id == sid).first() is None
    finally:
        db.close()
