import sys
import types
from unittest.mock import MagicMock
import pytest
from fastapi import HTTPException
from types import SimpleNamespace

@pytest.fixture
def doc_routes_mod(monkeypatch):
    class _DBStub(types.ModuleType):
        def __getattr__(self, name):
            return MagicMock()

    db_stub = _DBStub("core.database")
    monkeypatch.setitem(sys.modules, "core.database", db_stub)

    monkeypatch.delitem(sys.modules, "routes.document_routes", raising=False)
    import routes.document_routes as mod
    return mod

class _FakeDocument:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.committed = False
        self.refreshed = False

class _FakeDb:
    def __init__(self, doc):
        self.doc = doc
        self.committed = False
        self.refreshed = False

    def query(self, model):
        self.model = model
        return self

    def filter(self, *clauses):
        self.clauses = clauses
        return self

    def first(self):
        return self.doc

    def commit(self):
        self.committed = True

    def refresh(self, obj):
        self.refreshed = True

    def close(self):
        pass

def test_patch_document_clears_active_id_on_unlink(monkeypatch, doc_routes_mod):
    mod = doc_routes_mod
    doc = _FakeDocument(id="doc-123", session_id="session-456", title="Test Doc", language="python", is_active=True, owner="alice", current_content="hello", version_count=1, created_at=None, updated_at=None)

    fake_db = _FakeDb(doc)
    monkeypatch.setattr(mod, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(mod, "get_current_user", lambda req: "alice")
    monkeypatch.setattr(mod, "_verify_doc_owner", lambda db, d, user: None)
    monkeypatch.setattr(mod, "_doc_to_dict", lambda d: {"id": d.id, "session_id": d.session_id})

    from src.tool_implementations import set_active_document, get_active_document
    set_active_document("doc-123")
    assert get_active_document() == "doc-123"

    router = mod.setup_document_routes(MagicMock())
    patch_handler = None
    for route in router.routes:
        if route.path == "/api/document/{doc_id}" and "PATCH" in route.methods:
            patch_handler = route.endpoint
            break

    assert patch_handler is not None

    from routes.document_helpers import DocumentPatch
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)))
    
    # We run the async function using standard asyncio since the handler is async
    import asyncio
    asyncio.run(patch_handler(request=req, doc_id="doc-123", req=DocumentPatch(session_id="")))

    assert get_active_document() is None
    assert doc.session_id is None

def test_patch_document_does_not_clear_active_id_on_metadata_only_patch(monkeypatch, doc_routes_mod):
    mod = doc_routes_mod
    doc = _FakeDocument(id="doc-123", session_id="session-456", title="Test Doc", language="python", is_active=True, owner="alice", current_content="hello", version_count=1, created_at=None, updated_at=None)

    fake_db = _FakeDb(doc)
    monkeypatch.setattr(mod, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(mod, "get_current_user", lambda req: "alice")
    monkeypatch.setattr(mod, "_verify_doc_owner", lambda db, d, user: None)
    monkeypatch.setattr(mod, "_doc_to_dict", lambda d: {"id": d.id, "session_id": d.session_id})

    from src.tool_implementations import set_active_document, get_active_document
    set_active_document("doc-123")
    assert get_active_document() == "doc-123"

    router = mod.setup_document_routes(MagicMock())
    patch_handler = None
    for route in router.routes:
        if route.path == "/api/document/{doc_id}" and "PATCH" in route.methods:
            patch_handler = route.endpoint
            break

    from routes.document_helpers import DocumentPatch
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)))
    
    import asyncio
    asyncio.run(patch_handler(request=req, doc_id="doc-123", req=DocumentPatch(title="New Title")))

    assert get_active_document() == "doc-123"
    assert doc.title == "New Title"

def test_delete_document_clears_active_id(monkeypatch, doc_routes_mod):
    mod = doc_routes_mod
    doc = _FakeDocument(id="doc-123", session_id="session-456", title="Test Doc", language="python", is_active=True, owner="alice", current_content="hello", version_count=1, created_at=None, updated_at=None)

    fake_db = _FakeDb(doc)
    monkeypatch.setattr(mod, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(mod, "get_current_user", lambda req: "alice")
    monkeypatch.setattr(mod, "_verify_doc_owner", lambda db, d, user: None)

    from src.tool_implementations import set_active_document, get_active_document
    set_active_document("doc-123")
    assert get_active_document() == "doc-123"

    router = mod.setup_document_routes(MagicMock())
    delete_handler = None
    for route in router.routes:
        if route.path == "/api/document/{doc_id}" and "DELETE" in route.methods:
            delete_handler = route.endpoint
            break

    assert delete_handler is not None

    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)))
    
    import asyncio
    asyncio.run(delete_handler(request=req, doc_id="doc-123"))

    assert get_active_document() is None
    assert doc.is_active is False
