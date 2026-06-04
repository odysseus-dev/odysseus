"""Document library search must escape LIKE wildcards.

documents_library wrapped each search token as "%{tok}%" and passed it to
ilike with no escaping, so a literal "%" or "_" in the query acted as a SQL
wildcard: searching "a_b" also matched "axbxc" and "%" matched everything.
list_archived_sessions already escapes these; the document search did not.
"""
import tempfile
import uuid

from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import Document

_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(f"sqlite:///{_TMPDB.name}", connect_args={"check_same_thread": False}, poolclass=NullPool)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)


def _route(router, path):
    for r in router.routes:
        if r.path == path and "GET" in getattr(r, "methods", set()):
            return r.endpoint
    raise AssertionError(path)


@pytest.fixture
def library(monkeypatch):
    import routes.document_routes as dr
    monkeypatch.setattr(dr, "SessionLocal", _TS, raising=False)
    monkeypatch.setattr(dr, "get_current_user", lambda request: "u1", raising=False)
    return _route(dr.setup_document_routes(MagicMock()), "/api/documents/library")


def _seed(*titles):
    db = _TS()
    try:
        db.query(Document).delete()
        for t in titles:
            db.add(Document(id=str(uuid.uuid4()), owner="u1", title=t, current_content="x", is_active=True))
        db.commit()
    finally:
        db.close()


def test_underscore_is_literal_not_wildcard(library):
    import asyncio
    _seed("a_b note", "axbxc note")
    res = asyncio.run(library(
        request=None, search="a_b", language=None, sort="recent",
        offset=0, limit=50, archived=False,
    ))
    titles = {it["title"] for it in res["documents"]}
    assert titles == {"a_b note"}


def test_plain_token_still_matches(library):
    import asyncio
    _seed("hello world", "goodbye")
    res = asyncio.run(library(
        request=None, search="hello", language=None, sort="recent",
        offset=0, limit=50, archived=False,
    ))
    assert {it["title"] for it in res["documents"]} == {"hello world"}
