"""Archive browser model filter must be a CONTAINS match, not suffix-only.

list_archived_sessions filtered with DbSession.model.ilike(f"%{model}") - a
suffix match. Filtering by "gpt-4" therefore returned "openai/gpt-4" but
silently DROPPED "gpt-4o" (contains but does not end with the value), and
over-matched models that merely share the suffix. The sibling name filter
already uses a wildcard-escaped contains match.
"""
import tempfile
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


def _route(router, path, method="GET"):
    for r in router.routes:
        if r.path == path and method in getattr(r, "methods", set()):
            return r.endpoint
    raise AssertionError(f"route not found: {path}")


@pytest.fixture
def archived_endpoint(monkeypatch):
    import routes.session_routes as sr
    from unittest.mock import MagicMock

    monkeypatch.setattr(sr, "SessionLocal", _TS)
    monkeypatch.setattr(sr, "effective_user", lambda request: "alice")
    router = sr.setup_session_routes(MagicMock(), {})
    return _route(router, "/api/sessions/archived")


def _seed(owner, *models):
    db = _TS()
    try:
        db.query(DbSession).delete()
        for m in models:
            db.add(DbSession(id=str(uuid.uuid4()), owner=owner, name=f"chat {m}",
                             model=m, archived=True))
        db.commit()
    finally:
        db.close()


def test_contains_match_returns_all_models_sharing_the_substring(archived_endpoint):
    _seed("alice", "openai/gpt-4", "gpt-4o", "claude-3")
    res = archived_endpoint(request=None, model="gpt-4")
    got = {s["model"] for s in res["sessions"]}
    assert got == {"openai/gpt-4", "gpt-4o"}


def test_exact_full_model_still_matches(archived_endpoint):
    _seed("alice", "openai/gpt-4", "gpt-4o")
    res = archived_endpoint(request=None, model="openai/gpt-4")
    assert {s["model"] for s in res["sessions"]} == {"openai/gpt-4"}


def test_wildcard_in_filter_is_escaped(archived_endpoint):
    _seed("alice", "gpt-4o", "gpt_4o")
    res = archived_endpoint(request=None, model="gpt_4")
    assert {s["model"] for s in res["sessions"]} == {"gpt_4o"}
