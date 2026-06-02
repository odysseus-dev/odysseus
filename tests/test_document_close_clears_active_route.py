"""Issue #1160 — route-level regression for clearing the active-document pointer.

Drives the REAL ``PATCH /api/document/{id}`` (session_id="") and
``DELETE /api/document/{id}`` handlers via TestClient, proving that closing a
document's tab (detach or delete) clears the in-memory active-document pointer
under the actual owner/session routing — not just the helper in isolation.

Binds a DEDICATED temporary SQLite engine and patches the route module's
``SessionLocal`` to it (rather than setting ``DATABASE_URL`` at import time), so
the test never touches the real dev DB, is independent of import order, and does
not contend for the dev DB's locks (which could hang).
"""

import tempfile
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

import core.database as cdb
import routes.document_routes as droutes
from core.database import Document
from core.database import Session as DbSession
from src.tool_implementations import set_active_document, get_active_document

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
    monkeypatch.setattr(droutes, "SessionLocal", _TS)
    yield


def _client():
    app = FastAPI()

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.current_user = "tester"
        return await call_next(request)

    app.include_router(droutes.setup_document_routes(MagicMock(), None))
    return TestClient(app)


def _make_doc():
    """Create a real session + an active document linked to it (owner 'tester')."""
    sid = "s-" + uuid.uuid4().hex[:8]
    db = _TS()
    try:
        db.add(DbSession(id=sid, owner="tester", name="s", model="m", endpoint_url="http://x"))
        doc = Document(
            id=str(uuid.uuid4()), session_id=sid, title="t",
            language="markdown", current_content="hi", version_count=1,
            is_active=True, owner="tester",
        )
        db.add(doc)
        db.commit()
        return doc.id
    finally:
        db.close()


def test_patch_unlink_clears_active_document():
    client = _client()
    doc_id = _make_doc()
    set_active_document(doc_id)
    r = client.patch(f"/api/document/{doc_id}", json={"session_id": ""})
    assert r.status_code == 200, r.text
    assert get_active_document() is None


def test_delete_clears_active_document():
    client = _client()
    doc_id = _make_doc()
    set_active_document(doc_id)
    r = client.delete(f"/api/document/{doc_id}")
    assert r.status_code == 200, r.text
    assert get_active_document() is None


def test_unlinking_a_different_doc_leaves_pointer():
    client = _client()
    active_id = _make_doc()
    other_id = _make_doc()
    set_active_document(active_id)
    r = client.patch(f"/api/document/{other_id}", json={"session_id": ""})
    assert r.status_code == 200, r.text
    assert get_active_document() == active_id
