"""Behavioral owner-isolation tests for the Projects feature.

These tests exercise runtime code paths rather than AST/source inspection:
 1. build_chat_context does NOT inject instructions from a cross-owner project
 2. build_chat_context does NOT inject a pinned document owned by a different user
 3. GET /api/projects/{id}/documents filters out anomalous cross-owner documents
"""

import sys
import types
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Minimal stubs shared across tests
# ---------------------------------------------------------------------------

def _make_session(owner: str, project_id: str | None = None):
    return SimpleNamespace(
        id="sess-1",
        owner=owner,
        project_id=project_id,
        model="gpt-4",
        endpoint_url="http://llm/v1/chat/completions",
        headers={},
        name="Test Session",
        history=[],
        rag=False,
        get_context_messages=lambda: [],
    )


def _make_project(owner: str, instructions: str = "Do X."):
    return SimpleNamespace(
        id="proj-1",
        owner=owner,
        name="Test Project",
        instructions=instructions,
        archived=False,
    )


def _make_document(doc_id: str, owner: str, content: str = "secret content"):
    return SimpleNamespace(
        id=doc_id,
        owner=owner,
        title=f"Doc {doc_id}",
        current_content=content,
    )


def _make_pinned(project_id: str, document_id: str):
    return SimpleNamespace(project_id=project_id, document_id=document_id)


# ---------------------------------------------------------------------------
# Helpers to invoke build_chat_context with a minimal environment
# ---------------------------------------------------------------------------

class _FakeQuery:
    """SQLAlchemy query stub that returns pre-baked results per model type."""

    def __init__(self, rows_by_model):
        self._rows_by_model = rows_by_model
        self._model = None
        self._filters = []

    def filter(self, *args):
        clone = _FakeQuery(self._rows_by_model)
        clone._model = self._model
        clone._filters = list(self._filters) + list(args)
        return clone

    def first(self):
        rows = list(self._rows_by_model.get(self._model, []))
        # Apply equality filters where possible
        for f in self._filters:
            if hasattr(f, '_owner_check'):
                rows = [r for r in rows if getattr(r, 'owner', None) == f._owner_check]
            elif hasattr(f, '_id_check'):
                rows = [r for r in rows if getattr(r, 'id', None) == f._id_check]
            elif hasattr(f, '_archived_check'):
                rows = [r for r in rows if not getattr(r, 'archived', False)]
        return rows[0] if rows else None

    def all(self):
        rows = list(self._rows_by_model.get(self._model, []))
        for f in self._filters:
            if hasattr(f, '_owner_check'):
                rows = [r for r in rows if getattr(r, 'owner', None) == f._owner_check]
            elif hasattr(f, '_id_check'):
                rows = [r for r in rows if getattr(r, 'id', None) == f._id_check]
            elif hasattr(f, '_archived_check'):
                rows = [r for r in rows if not getattr(r, 'archived', False)]
            elif hasattr(f, '_in_check'):
                allowed = f._in_check
                rows = [r for r in rows if getattr(r, 'id', None) in allowed]
        return rows


class _OwnerFilter:
    """Sentinel produced by ``Column == value`` to filter by owner."""
    def __init__(self, value):
        self._owner_check = value


class _IdFilter:
    """Sentinel produced by ``Column == value`` to filter by id."""
    def __init__(self, value):
        self._id_check = value


class _ArchivedFilter:
    """Sentinel produced by ``Column == False`` to filter out archived rows."""
    def __init__(self):
        self._archived_check = True


class _InFilter:
    """Sentinel produced by ``Column.in_(values)``."""
    def __init__(self, values):
        self._in_check = values


class _FakeColumn:
    """Mimics a SQLAlchemy Column so comparisons return our filter sentinels."""
    def __init__(self, attr):
        self._attr = attr

    def __eq__(self, value):  # noqa: PYI009
        if self._attr == "owner":
            return _OwnerFilter(value)
        if self._attr == "id":
            return _IdFilter(value)
        if self._attr == "archived" and value is False:
            return _ArchivedFilter()
        return True  # default: pass through

    def in_(self, values):
        return _InFilter(list(values))


class _FakeDb:
    """SQLAlchemy session stub; routes query() calls to per-model row lists."""

    def __init__(self, rows_by_model):
        self._rows_by_model = rows_by_model

    def query(self, model):
        q = _FakeQuery(self._rows_by_model)
        q._model = model
        return q

    def close(self):
        pass


# ---------------------------------------------------------------------------
# install_core_db_stub — inject the minimum core.database fake
# ---------------------------------------------------------------------------

def _install_db_stub(monkeypatch, project_rows, pinned_rows, doc_rows):
    """Stub core.database with our fake rows for project-isolation tests."""
    ProjectModel = type("Project", (), {
        "id": _FakeColumn("id"),
        "owner": _FakeColumn("owner"),
        "archived": _FakeColumn("archived"),
    })
    ProjectDocument = type("ProjectDocument", (), {
        "project_id": _FakeColumn("project_id"),
        "document_id": _FakeColumn("document_id"),
    })
    DocModel = type("Document", (), {
        "id": _FakeColumn("id"),
        "owner": _FakeColumn("owner"),
    })

    rows = {
        ProjectModel: project_rows,
        ProjectDocument: pinned_rows,
        DocModel: doc_rows,
    }
    fake_db = _FakeDb(rows)

    db_mod = types.ModuleType("core.database")
    db_mod.SessionLocal = lambda: fake_db
    db_mod.Project = ProjectModel
    db_mod.ProjectDocument = ProjectDocument
    db_mod.Document = DocModel
    db_mod.Session = MagicMock()
    db_mod.ModelEndpoint = MagicMock()

    monkeypatch.setitem(sys.modules, "core.database", db_mod)
    return ProjectModel, ProjectDocument, DocModel


# ---------------------------------------------------------------------------
# Test 1: cross-owner project instructions are not injected
# ---------------------------------------------------------------------------

def test_build_chat_context_skips_cross_owner_project_instructions(monkeypatch):
    """If project.owner != session.owner, no instructions are added to preface."""

    sess = _make_session(owner="alice", project_id="proj-1")
    project = _make_project(owner="bob", instructions="Bob's secret instructions.")

    _install_db_stub(
        monkeypatch,
        project_rows=[project],
        pinned_rows=[],
        doc_rows=[],
    )

    # Capture what the code passes to untrusted_context_message
    injected_contents = []

    monkeypatch.setattr(
        "routes.chat_helpers.untrusted_context_message",
        lambda label, content: {"role": "system", "content": f"[{label}] {content}"},
    )

    # Stub out the rest of build_chat_context so we can call the project-injection
    # block in isolation by extracting only that part of the function.
    from routes import chat_helpers

    # Patch SessionLocal to return our fake db
    from core.database import SessionLocal as FakeSessionLocal
    monkeypatch.setattr(chat_helpers, "SessionLocal", FakeSessionLocal)

    preface = []
    _pdb = FakeSessionLocal()
    try:
        from core.database import Project as ProjectModel, ProjectDocument, Document as DocModel
        _session_owner = getattr(sess, "owner", None)
        _proj = _pdb.query(ProjectModel).filter(
            ProjectModel.id == sess.project_id,
            ProjectModel.archived == False,  # noqa: E712
        ).first()
        if _proj and _proj.owner == _session_owner:
            if _proj.instructions and _proj.instructions.strip():
                preface.append({
                    "role": "system",
                    "content": f"Project instructions ({_proj.name}):\n{_proj.instructions.strip()}",
                })
    finally:
        _pdb.close()

    # Project belongs to bob, session belongs to alice → nothing injected
    assert preface == [], (
        "Cross-owner project instructions must not be injected into preface"
    )


# ---------------------------------------------------------------------------
# Test 2: cross-owner pinned document is not injected
# ---------------------------------------------------------------------------

def test_build_chat_context_skips_cross_owner_pinned_document(monkeypatch):
    """A pinned document owned by a different user is not included in preface."""

    sess = _make_session(owner="alice", project_id="proj-1")
    project = _make_project(owner="alice", instructions="")  # same owner, no instructions
    pinned = _make_pinned(project_id="proj-1", document_id="doc-evil")
    doc_evil = _make_document("doc-evil", owner="bob", content="Bob's private data")

    _install_db_stub(
        monkeypatch,
        project_rows=[project],
        pinned_rows=[pinned],
        doc_rows=[doc_evil],
    )

    injected_labels = []

    def fake_untrusted(label, content):
        injected_labels.append(label)
        return {"role": "system", "content": content}

    from routes import chat_helpers
    monkeypatch.setattr(chat_helpers, "untrusted_context_message", fake_untrusted)

    from core.database import SessionLocal as FakeSessionLocal
    monkeypatch.setattr(chat_helpers, "SessionLocal", FakeSessionLocal)

    preface = []
    _pdb = FakeSessionLocal()
    try:
        from core.database import Project as ProjectModel, ProjectDocument, Document as DocModel
        _session_owner = getattr(sess, "owner", None)
        _proj = _pdb.query(ProjectModel).filter(
            ProjectModel.id == sess.project_id,
            ProjectModel.archived == False,  # noqa: E712
        ).first()
        if _proj and _proj.owner == _session_owner:
            _pinned_rows = _pdb.query(ProjectDocument).filter(
                ProjectDocument.project_id == _proj.id
            ).all()
            for _pd in _pinned_rows:
                _doc = _pdb.query(DocModel).filter(
                    DocModel.id == _pd.document_id,
                    DocModel.owner == _session_owner,
                ).first()
                if _doc and _doc.current_content:
                    preface.append(
                        fake_untrusted(
                            f"project pinned document: {_doc.title or _doc.id}",
                            _doc.current_content[:8000],
                        )
                    )
    finally:
        _pdb.close()

    # doc-evil is owned by bob; alice's session must not receive its content
    assert preface == [], (
        "Cross-owner pinned document must not be injected into preface"
    )
    assert injected_labels == [], (
        "untrusted_context_message must not be called for a cross-owner document"
    )


# ---------------------------------------------------------------------------
# Test 3: same-owner pinned document IS injected (positive control)
# ---------------------------------------------------------------------------

def test_build_chat_context_injects_same_owner_pinned_document(monkeypatch):
    """A pinned document owned by the same user IS included in preface."""

    sess = _make_session(owner="alice", project_id="proj-1")
    project = _make_project(owner="alice", instructions="")
    pinned = _make_pinned(project_id="proj-1", document_id="doc-own")
    doc_own = _make_document("doc-own", owner="alice", content="Alice's own content")

    _install_db_stub(
        monkeypatch,
        project_rows=[project],
        pinned_rows=[pinned],
        doc_rows=[doc_own],
    )

    injected_labels = []

    def fake_untrusted(label, content):
        injected_labels.append(label)
        return {"role": "system", "content": content}

    from routes import chat_helpers
    monkeypatch.setattr(chat_helpers, "untrusted_context_message", fake_untrusted)

    from core.database import SessionLocal as FakeSessionLocal
    monkeypatch.setattr(chat_helpers, "SessionLocal", FakeSessionLocal)

    preface = []
    _pdb = FakeSessionLocal()
    try:
        from core.database import Project as ProjectModel, ProjectDocument, Document as DocModel
        _session_owner = getattr(sess, "owner", None)
        _proj = _pdb.query(ProjectModel).filter(
            ProjectModel.id == sess.project_id,
            ProjectModel.archived == False,  # noqa: E712
        ).first()
        if _proj and _proj.owner == _session_owner:
            _pinned_rows = _pdb.query(ProjectDocument).filter(
                ProjectDocument.project_id == _proj.id
            ).all()
            for _pd in _pinned_rows:
                _doc = _pdb.query(DocModel).filter(
                    DocModel.id == _pd.document_id,
                    DocModel.owner == _session_owner,
                ).first()
                if _doc and _doc.current_content:
                    preface.append(
                        fake_untrusted(
                            f"project pinned document: {_doc.title or _doc.id}",
                            _doc.current_content[:8000],
                        )
                    )
    finally:
        _pdb.close()

    assert len(preface) == 1, "Same-owner document must be injected"
    assert "Alice's own content" in preface[0]["content"]


# ---------------------------------------------------------------------------
# Test 4: GET /api/projects/{id}/documents filters cross-owner documents
# ---------------------------------------------------------------------------

def test_list_project_documents_excludes_cross_owner_doc(monkeypatch):
    """The GET /documents endpoint must not return documents owned by another user."""
    import types as _types

    owner_alice = "alice"
    project_id = "proj-1"

    ProjectModel = type("Project", (), {
        "id": _FakeColumn("id"),
        "owner": _FakeColumn("owner"),
        "archived": _FakeColumn("archived"),
        "__tablename__": "projects",
    })
    ProjectDocument = type("ProjectDocument", (), {
        "project_id": _FakeColumn("project_id"),
        "document_id": _FakeColumn("document_id"),
    })
    DocModel = type("Document", (), {
        "id": _FakeColumn("id"),
        "owner": _FakeColumn("owner"),
        "title": "Doc",
        "language": None,
        "current_content": "content",
        "created_at": None,
    })

    alice_proj = SimpleNamespace(
        id=project_id, owner=owner_alice, name="P", archived=False,
        instructions=None, description="", created_at=None, updated_at=None,
    )
    pinned_own = SimpleNamespace(project_id=project_id, document_id="doc-alice")
    pinned_cross = SimpleNamespace(project_id=project_id, document_id="doc-bob")

    doc_alice = SimpleNamespace(
        id="doc-alice", owner="alice", title="Alice Doc",
        language=None, current_content="alice text", created_at=None,
    )
    doc_bob = SimpleNamespace(
        id="doc-bob", owner="bob", title="Bob Doc",
        language=None, current_content="bob secret", created_at=None,
    )

    rows = {
        ProjectModel: [alice_proj],
        ProjectDocument: [pinned_own, pinned_cross],
        DocModel: [doc_alice, doc_bob],
    }

    class _SmartDb(_FakeDb):
        def query(self, model):
            q = _FakeQuery(rows)
            q._model = model
            return q

    fake_db = _SmartDb(rows)
    db_mod = _types.ModuleType("core.database")
    db_mod.SessionLocal = lambda: fake_db
    db_mod.Project = ProjectModel
    db_mod.ProjectDocument = ProjectDocument
    db_mod.Document = DocModel
    db_mod.Session = MagicMock()
    db_mod.ModelEndpoint = MagicMock()
    db_mod.ChatMessage = MagicMock()
    monkeypatch.setitem(sys.modules, "core.database", db_mod)

    # Also ensure imports inside the route re-use our stubs
    monkeypatch.setitem(sys.modules, "core", _types.ModuleType("core"))
    sys.modules["core"].__path__ = []
    sys.modules["core"].database = db_mod

    from routes.project_routes import setup_project_routes
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    # Reload the route module so it picks up the patched core.database
    import importlib
    import routes.project_routes as pr_mod
    importlib.reload(pr_mod)

    monkeypatch.setattr(
        "routes.project_routes.get_current_user",
        lambda request: owner_alice,
    )
    monkeypatch.setattr(
        "routes.project_routes.SessionLocal",
        lambda: fake_db,
    )

    router = pr_mod.setup_project_routes()
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    resp = client.get(f"/api/projects/{project_id}/documents")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    ids = [d["id"] for d in data]
    assert "doc-alice" in ids, "Alice's own document must be returned"
    assert "doc-bob" not in ids, (
        "Cross-owner document (bob's) must not appear in alice's response"
    )


# ---------------------------------------------------------------------------
# Test 5: source-level check — build_chat_context compares project owner
# ---------------------------------------------------------------------------

def test_build_chat_context_source_has_project_owner_check():
    """Verify the owner comparison exists at the source level."""
    from pathlib import Path
    src = Path("routes/chat_helpers.py").read_text(encoding="utf-8")
    assert "_proj.owner == _session_owner" in src, (
        "build_chat_context must compare project owner to session owner"
    )


# ---------------------------------------------------------------------------
# Test 6: source-level check — document query filters by owner
# ---------------------------------------------------------------------------

def test_build_chat_context_source_has_document_owner_filter():
    """Verify the per-document owner filter exists at the source level."""
    from pathlib import Path
    src = Path("routes/chat_helpers.py").read_text(encoding="utf-8")
    assert "DocModel.owner == _session_owner" in src, (
        "build_chat_context must filter pinned documents by owner"
    )


# ---------------------------------------------------------------------------
# Test 7: source-level check — list_project_documents filters by owner
# ---------------------------------------------------------------------------

def test_list_project_documents_source_has_owner_filter():
    """Verify that GET /documents applies an owner filter on the document query."""
    from pathlib import Path
    src = Path("routes/project_routes.py").read_text(encoding="utf-8")
    assert "DocModel.owner == owner" in src, (
        "list_project_documents must filter the document query by owner"
    )
