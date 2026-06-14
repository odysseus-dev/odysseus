"""list_sessions must return only the authenticated user's sessions.

Regression for the enrichment query at routes/session_routes.py:265 which
previously fetched rows for all owners on every GET /api/sessions call.
"""
import sys
import tempfile
import types
import uuid

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
    endpoint = next(r.endpoint for r in router.routes
                    if getattr(r, "path", "") == "/api/sessions"
                    and "GET" in getattr(r, "methods", set()))

    result = endpoint(request=MagicMock())
    returned_ids = {s["id"] for s in result}
    assert alice_id in returned_ids
    assert bob_id not in returned_ids
