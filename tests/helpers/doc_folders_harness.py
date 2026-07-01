"""In-process harness for the document-folders route tests.

Each ``DocFoldersHarness`` builds a fresh file-backed sqlite DB, binds it onto
``routes.document_routes`` (SessionLocal + a request-driven ``get_current_user``)
and exposes the folder / library / patch endpoints as plain callables plus seed
helpers. A fresh DB per harness keeps the tests order-independent.

The library/list endpoints declare FastAPI ``Query(...)`` defaults, which are
sentinel objects (not ``None``) when the coroutine is called directly, so the
wrappers here pass every query param explicitly.
"""
import asyncio
import uuid
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import core.database as cdb
from core.database import Document, DocumentFolder
from core.database import Session as DbSession
from routes.document_helpers import DocumentPatch


def make_memory_db():
    """Build a fresh in-memory sqlite bound to one shared connection.

    A file-backed temp DB fsyncs per DDL statement, which makes create_all of
    the full metadata cost seconds on a busy disk; an in-memory StaticPool DB
    avoids that and is fully isolated per call. The global PRAGMA listener in
    core.database still fires on the single connection, so foreign_keys=ON (and
    thus the ondelete=SET NULL backstop) is enforced here too.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    cdb.Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return SessionLocal, engine


class _Req:
    """Minimal stand-in for a Starlette Request used by the handlers."""

    def __init__(self, user=None, body=None):
        self._user = user
        self._body = {} if body is None else body

    async def json(self):
        return self._body


def _route(router, path, method):
    for r in router.routes:
        if r.path == path and method in getattr(r, "methods", set()):
            return r.endpoint
    raise AssertionError(f"route not found: {method} {path}")


class DocFoldersHarness:
    def __init__(self, monkeypatch):
        import routes.document_routes as dr

        self.dr = dr
        self.SessionLocal, self.engine = make_memory_db()
        monkeypatch.setattr(dr, "SessionLocal", self.SessionLocal)
        monkeypatch.setattr(
            dr, "get_current_user", lambda request: getattr(request, "_user", None)
        )
        router = dr.setup_document_routes(MagicMock(), None)
        self._create = _route(router, "/api/document-folders", "POST")
        self._list = _route(router, "/api/document-folders", "GET")
        self._patch = _route(router, "/api/document-folders/{folder_id}", "PATCH")
        self._delete = _route(router, "/api/document-folders/{folder_id}", "DELETE")
        self._move_docs = _route(router, "/api/document-folders/move-documents", "POST")
        self._library = _route(router, "/api/documents/library", "GET")
        self._patch_doc = _route(router, "/api/document/{doc_id}", "PATCH")
        self._list_docs = _route(router, "/api/documents/{session_id}", "GET")

    # ---- endpoint wrappers (synchronous, raise HTTPException as-is) ----

    def create_folder(self, user, name, parent_id=None):
        body = {"name": name}
        if parent_id is not None:
            body["parent_id"] = parent_id
        return asyncio.run(self._create(_Req(user=user, body=body)))

    def list_session_docs(self, user, session_id):
        return asyncio.run(self._list_docs(_Req(user=user), session_id=session_id))

    def list_folders(self, user, archived=False):
        return asyncio.run(self._list(_Req(user=user), archived=archived))

    def patch_folder(self, user, folder_id, body):
        return asyncio.run(
            self._patch(_Req(user=user, body=body), folder_id=folder_id)
        )

    def rename_folder(self, user, folder_id, name):
        return self.patch_folder(user, folder_id, {"name": name})

    def move_folder(self, user, folder_id, parent_id):
        return self.patch_folder(user, folder_id, {"parent_id": parent_id})

    def delete_folder(self, user, folder_id):
        return asyncio.run(self._delete(_Req(user=user), folder_id=folder_id))

    def move_documents(self, user, document_ids, folder_id):
        return asyncio.run(
            self._move_docs(_Req(
                user=user, body={"document_ids": document_ids, "folder_id": folder_id}))
        )

    def library(self, user, **params):
        defaults = dict(
            search=None, language=None, folder_id=None, recursive=False,
            unfiled=False, sort="recent", offset=0, limit=20, archived=False,
        )
        defaults.update(params)
        return asyncio.run(self._library(_Req(user=user), **defaults))

    def patch_doc(self, user, doc_id, body):
        return asyncio.run(
            self._patch_doc(_Req(user=user), doc_id=doc_id, req=DocumentPatch(**body))
        )

    # ---- seed helpers ----

    def seed_session(self, owner, session_id=None):
        sid = session_id or str(uuid.uuid4())
        db = self.SessionLocal()
        try:
            db.add(DbSession(id=sid, owner=owner, name="s",
                             endpoint_url="http://x", model="m"))
            db.commit()
        finally:
            db.close()
        return sid

    def seed_doc(self, owner, folder_id=None, archived=False, title="doc",
                 language="markdown", content="body", is_active=True,
                 session_id=None):
        did = str(uuid.uuid4())
        db = self.SessionLocal()
        try:
            db.add(Document(id=did, owner=owner, title=title, language=language,
                            current_content=content, version_count=1,
                            is_active=is_active, archived=archived,
                            folder_id=folder_id, session_id=session_id))
            db.commit()
        finally:
            db.close()
        return did

    def seed_folder(self, owner, name, parent_id=None):
        fid = str(uuid.uuid4())
        db = self.SessionLocal()
        try:
            db.add(DocumentFolder(id=fid, owner=owner, name=name, parent_id=parent_id))
            db.commit()
        finally:
            db.close()
        return fid

    def get_doc_folder_id(self, doc_id):
        db = self.SessionLocal()
        try:
            return db.query(Document).filter(Document.id == doc_id).first().folder_id
        finally:
            db.close()

    def folder_parent_id(self, folder_id):
        db = self.SessionLocal()
        try:
            f = db.query(DocumentFolder).filter(DocumentFolder.id == folder_id).first()
            return f.parent_id if f else None
        finally:
            db.close()

    def folder_exists(self, folder_id):
        db = self.SessionLocal()
        try:
            return (
                db.query(DocumentFolder)
                .filter(DocumentFolder.id == folder_id)
                .first()
                is not None
            )
        finally:
            db.close()
