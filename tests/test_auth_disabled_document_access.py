"""Regression tests for auth-disabled document access (PR #4623).

Validates that the _auth_disabled() bypass in _verify_doc_owner and
list_documents restores single-user / no-auth mode WITHOUT weakening the
authenticated path.  Three pinned directions:

  1. AUTH_DISABLED + None user -> list_documents + doc read SUCCEEDS
     (the bug being fixed).
  2. AUTH_ENABLED  + None user -> still 403.
  3. AUTH_ENABLED  + wrong owner -> _verify_doc_owner still raises 404/403.

Route handlers are called directly (same pattern as
test_document_session_owner_scope.py) so coverage lands on the real
closures without spinning up middleware.
"""

import tempfile
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from tests.helpers.import_state import clear_fake_database_modules

clear_fake_database_modules()

import core.database as cdb
import routes.document_routes as droutes
from core.database import Document
from core.database import Session as DbSession
from routes.document_helpers import _verify_doc_owner, _owner_session_filter

_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(
    f"sqlite:///{_TMPDB.name}",
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)


# ------------------------------------------------------------------ helpers


def _req(user=None):
    """Build a minimal fake Request whose state.current_user returns *user*."""
    return SimpleNamespace(state=SimpleNamespace(current_user=user))


def _endpoint(method, path):
    """Resolve a route endpoint from the document router."""
    router = droutes.setup_document_routes(MagicMock(), None)
    for route in router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise RuntimeError(f"{method} {path} not found")


def _bind_test_db():
    previous = droutes.SessionLocal
    droutes.SessionLocal = _TS
    return previous


def _seed(owner="alice"):
    """Create one session + one owned document. Returns (session_id, doc_id)."""
    session_id = f"{owner}-" + uuid.uuid4().hex[:8]
    doc_id = str(uuid.uuid4())
    db = _TS()
    try:
        db.add(DbSession(
            id=session_id, owner=owner, name=owner,
            model="m", endpoint_url="http://x",
        ))
        db.add(Document(
            id=doc_id,
            session_id=session_id,
            title=f"{owner} doc",
            language="markdown",
            current_content=f"{owner} body",
            version_count=1,
            is_active=True,
            owner=owner,
        ))
        db.commit()
        return session_id, doc_id
    finally:
        db.close()


# ------------------------------------------------------ 1. auth DISABLED +
#                                                      None user -> succeeds


@pytest.mark.asyncio
async def test_list_documents_allows_none_user_when_auth_disabled(monkeypatch):
    """AUTH_ENABLED=false + user=None must NOT raise 403 on list_documents."""
    monkeypatch.setenv("AUTH_ENABLED", "false")
    previous = _bind_test_db()
    try:
        list_docs = _endpoint("GET", "/api/documents/{session_id}")
        session_id, doc_id = _seed()

        # Must succeed — this is the bug fix.
        rows = await list_docs(_req(None), session_id)
        ids = [row["id"] for row in rows]
        assert doc_id in ids, "own doc must be visible in auth-disabled mode"
    finally:
        droutes.SessionLocal = previous


@pytest.mark.asyncio
async def test_get_document_allows_none_user_when_auth_disabled(monkeypatch):
    """AUTH_ENABLED=false + user=None must NOT raise 403 on get_document."""
    monkeypatch.setenv("AUTH_ENABLED", "false")
    previous = _bind_test_db()
    try:
        get_doc = _endpoint("GET", "/api/document/{doc_id}")
        _session_id, doc_id = _seed()

        # Must succeed — _verify_doc_owner bypasses when auth is disabled.
        result = await get_doc(_req(None), doc_id)
        assert result["id"] == doc_id
    finally:
        droutes.SessionLocal = previous


def test_verify_doc_owner_allows_none_user_when_auth_disabled(monkeypatch):
    """_verify_doc_owner with user=None + AUTH_ENABLED=false must pass."""
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _session_id, doc_id = _seed()
    db = _TS()
    try:
        doc = db.query(Document).filter(Document.id == doc_id).first()
        # Must NOT raise — the bypass allows single-user access.
        _verify_doc_owner(db, doc, None)
    finally:
        db.close()


def test_owner_session_filter_noops_for_none_user_when_auth_disabled(monkeypatch):
    """_owner_session_filter with user=None + AUTH_ENABLED=false returns query unchanged."""
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _session_id, doc_id = _seed()
    db = _TS()
    try:
        q = db.query(Document).filter(Document.id == doc_id)
        result = _owner_session_filter(q, None)
        # Filter was a no-op; document is still reachable.
        assert result.first().id == doc_id
    finally:
        db.close()


# ------------------------------------------------------ 2. auth ENABLED +
#                                                      None user -> 403


@pytest.mark.asyncio
async def test_list_documents_rejects_none_user_when_auth_enabled(monkeypatch):
    """AUTH_ENABLED=true (default) + user=None must raise 403."""
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    previous = _bind_test_db()
    try:
        list_docs = _endpoint("GET", "/api/documents/{session_id}")
        session_id, _doc_id = _seed()

        with pytest.raises(HTTPException) as exc:
            await list_docs(_req(None), session_id)

        assert exc.value.status_code == 403
    finally:
        droutes.SessionLocal = previous


@pytest.mark.asyncio
async def test_get_document_rejects_none_user_when_auth_enabled(monkeypatch):
    """AUTH_ENABLED=true (default) + user=None must raise 403 via _verify_doc_owner."""
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    previous = _bind_test_db()
    try:
        get_doc = _endpoint("GET", "/api/document/{doc_id}")
        _session_id, doc_id = _seed()

        with pytest.raises(HTTPException) as exc:
            await get_doc(_req(None), doc_id)

        assert exc.value.status_code == 403
    finally:
        droutes.SessionLocal = previous


def test_verify_doc_owner_rejects_none_user_when_auth_enabled(monkeypatch):
    """_verify_doc_owner with user=None + AUTH_ENABLED=true must raise 403."""
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    _session_id, doc_id = _seed()
    db = _TS()
    try:
        doc = db.query(Document).filter(Document.id == doc_id).first()
        with pytest.raises(HTTPException) as exc:
            _verify_doc_owner(db, doc, None)
        assert exc.value.status_code == 403
    finally:
        db.close()


# ------------------------------------------ 3. auth ENABLED + wrong owner ->
#                                                 _verify_doc_owner raises 404


def test_verify_doc_owner_rejects_wrong_owner_when_auth_enabled(monkeypatch):
    """_verify_doc_owner with a mismatched owner must raise 404 (not 403).

    This confirms the authenticated path is untouched by the no-auth bypass."""
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    session_id, doc_id = _seed(owner="alice")
    db = _TS()
    try:
        doc = db.query(Document).filter(Document.id == doc_id).first()
        with pytest.raises(HTTPException) as exc:
            _verify_doc_owner(db, doc, "bob")  # bob != alice
        assert exc.value.status_code == 404
    finally:
        db.close()


@pytest.mark.asyncio
async def test_get_document_rejects_wrong_owner(monkeypatch):
    """GET /api/document/{doc_id} with wrong authenticated user -> 404."""
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    previous = _bind_test_db()
    try:
        get_doc = _endpoint("GET", "/api/document/{doc_id}")
        _session_id, doc_id = _seed(owner="alice")

        with pytest.raises(HTTPException) as exc:
            await get_doc(_req("bob"), doc_id)

        assert exc.value.status_code == 404
    finally:
        droutes.SessionLocal = previous


@pytest.mark.asyncio
async def test_list_documents_hides_wrong_owner_docs(monkeypatch):
    """list_documents for alice must not show bob's documents."""
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    previous = _bind_test_db()
    try:
        list_docs = _endpoint("GET", "/api/documents/{session_id}")

        # Seed alice's session with a doc
        alice_session, alice_doc = _seed(owner="alice")
        # Create bob's session+doc in the SAME session so ownership filter kicks in
        bob_session = "bob-" + uuid.uuid4().hex[:8]
        bob_doc = str(uuid.uuid4())
        db = _TS()
        try:
            db.add(DbSession(id=bob_session, owner="bob", name="bob", model="m", endpoint_url="http://x"))
            db.add(Document(
                id=bob_doc, session_id=alice_session,  # same session!
                title="bob doc", language="markdown", current_content="bob body",
                version_count=1, is_active=True, owner="bob",
            ))
            db.commit()
        finally:
            db.close()

        rows = await list_docs(_req("alice"), alice_session)
        ids = [row["id"] for row in rows]
        assert alice_doc in ids
        assert bob_doc not in ids, "wrong-owner docs must be hidden"
    finally:
        droutes.SessionLocal = previous
