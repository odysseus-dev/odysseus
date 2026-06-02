"""Issue #1160 — route-level regression for clearing the active-document pointer.

Drives the REAL `PATCH /api/document/{id}` (session_id="") and
`DELETE /api/document/{id}` handlers through TestClient against a temporary
SQLite DB, proving that closing a document's tab (detach or delete) clears the
in-memory active-document pointer under the actual owner/session routing — not
just the helper in isolation.
"""

import os
import tempfile
import uuid

# Point the DB at a throwaway file BEFORE importing core.database (engine is
# created at import from DATABASE_URL).
_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_TMPDB.name}"

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

from core.database import Base, engine, SessionLocal, Document  # noqa: E402
from core.database import Session as DbSession  # noqa: E402
from routes.document_routes import setup_document_routes  # noqa: E402
from src.tool_implementations import set_active_document, get_active_document  # noqa: E402

Base.metadata.create_all(engine)


def _app():
    app = FastAPI()

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.current_user = "tester"
        return await call_next(request)

    app.include_router(setup_document_routes(MagicMock(), None))
    return TestClient(app)


def _make_doc():
    """Create a real session + an active document linked to it (owner 'tester')."""
    sid = "s-" + uuid.uuid4().hex[:8]
    db = SessionLocal()
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
    client = _app()
    doc_id = _make_doc()
    set_active_document(doc_id)
    r = client.patch(f"/api/document/{doc_id}", json={"session_id": ""})
    assert r.status_code == 200, r.text
    assert get_active_document() is None


def test_delete_clears_active_document():
    client = _app()
    doc_id = _make_doc()
    set_active_document(doc_id)
    r = client.delete(f"/api/document/{doc_id}")
    assert r.status_code == 200, r.text
    assert get_active_document() is None


def test_unlinking_a_different_doc_leaves_pointer():
    client = _app()
    active_id = _make_doc()
    other_id = _make_doc()
    set_active_document(active_id)
    r = client.patch(f"/api/document/{other_id}", json={"session_id": ""})
    assert r.status_code == 200, r.text
    assert get_active_document() == active_id
