"""Integration tests for DELETE /api/admin/wipe/memory — the full route path:
DB rows deleted AND the ChromaDB vector index cleared AND an honest `vector_cleared`
signal in the response.

These exercise the real route handler (`routes/admin_wipe_routes.py`) against an
in-memory SQLite DB and a REAL `MemoryVectorStore` over a fake ChromaDB client.
`TestClient`/httpx isn't available in this environment, so — like the existing
`test_admin_wipe_memory_route_invokes_vector_clear` — we invoke the endpoint function
directly; that still drives the entire handler body (DB delete + commit, file wipe,
vector clear, response shape).
"""
import pytest


class _FakeCollection:
    def __init__(self):
        self._ids = []

    def count(self):
        return len(self._ids)

    def get(self, ids=None):
        if ids is None:
            return {"ids": list(self._ids)}
        return {"ids": [i for i in ids if i in self._ids]}

    def add(self, ids, embeddings=None, documents=None, metadatas=None):
        self._ids.extend(ids)

    def delete(self, ids=None):
        for i in (ids or []):
            if i in self._ids:
                self._ids.remove(i)


class _FakeClient:
    def __init__(self):
        self.collections = {}

    def get_or_create_collection(self, name, metadata=None):
        return self.collections.setdefault(name, _FakeCollection())

    def delete_collection(self, name):
        self.collections.pop(name, None)


class _FakeEmbedder:
    url = "fake://embed"

    def encode(self, texts, normalize_embeddings=True):
        import numpy as np
        return np.zeros((len(texts), 3))


@pytest.fixture(autouse=True)
def _reset_singletons():
    import src.memory_vector as mv
    import src.chroma_client as cc
    mv._store = None
    cc._client = None
    yield
    mv._store = None
    cc._client = None


def _wipe_handler(monkeypatch):
    """Build the wipe route over an in-memory SQLite DB with admin + file-wipe
    stubbed out, and return (handler, TestSessionLocal)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from core.database import Base
    import routes.admin_wipe_routes as awr

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine)

    monkeypatch.setattr(awr, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(awr, "require_admin", lambda r: None)
    monkeypatch.setattr(awr, "_wipe_memory_files", lambda: None)

    router = awr.setup_admin_wipe_routes(session_manager=None)
    handler = next(r for r in router.routes if r.path == "/api/admin/wipe/{kind}").endpoint
    return handler, TestSessionLocal


def _seed_memories(SessionLocal, n):
    from core.database import Memory
    db = SessionLocal()
    for i in range(n):
        db.add(Memory(id=f"m{i}", text=f"memory {i}"))
    db.commit()
    db.close()


def _request():
    from fastapi import Request
    return Request(scope={"type": "http", "headers": []})


def test_wipe_memory_clears_db_and_vectors_when_embeddings_down(monkeypatch, tmp_path):
    """C12 (reviewer's reproduction, end to end): embeddings down at wipe time, a
    ghost vector present, nothing registered. The wipe must delete the DB rows AND
    clear the vector index AND report vector_cleared=True. Pre-fix the route returned
    success while the vector lingered."""
    import src.chroma_client as cc
    import src.embeddings as emb
    import src.memory_vector as mv
    from core.database import Memory

    client = _FakeClient()
    ghost = client.get_or_create_collection(mv.MemoryVectorStore.COLLECTION_NAME)
    ghost.add(ids=["g"], embeddings=[[0.0]], documents=["x"], metadatas=[{}])
    monkeypatch.setattr(cc, "get_chroma_client", lambda: client)

    def _no_embed():
        raise RuntimeError("embedding endpoint unavailable")

    monkeypatch.setattr(emb, "get_embedding_client", _no_embed)

    handler, SessionLocal = _wipe_handler(monkeypatch)
    _seed_memories(SessionLocal, 3)

    result = handler(kind="memory", request=_request())

    assert result["status"] == "deleted"
    assert result["count"] == 3
    assert result["vector_cleared"] is True
    # DB rows gone
    db = SessionLocal()
    assert db.query(Memory).count() == 0
    db.close()
    # vectors gone
    assert client.get_or_create_collection(mv.MemoryVectorStore.COLLECTION_NAME).count() == 0


def test_wipe_memory_best_effort_when_chroma_down(monkeypatch):
    """C13: ChromaDB unreachable -> route still 200/deleted, DB rows cleared,
    vector_cleared=False, no 500."""
    import src.chroma_client as cc
    from core.database import Memory

    def _down():
        raise RuntimeError("chroma down")

    monkeypatch.setattr(cc, "get_chroma_client", _down)

    handler, SessionLocal = _wipe_handler(monkeypatch)
    _seed_memories(SessionLocal, 2)

    result = handler(kind="memory", request=_request())

    assert result["status"] == "deleted"
    assert result["count"] == 2
    assert result["vector_cleared"] is False
    db = SessionLocal()
    assert db.query(Memory).count() == 0
    db.close()


def test_wipe_memory_uses_registered_store(monkeypatch, tmp_path):
    """C14: a healthy app store is registered; embeddings are down at wipe time. The
    route's accessor returns the registered store (not a fresh unhealthy one) and
    clears it -> vector_cleared=True."""
    import src.chroma_client as cc
    import src.embeddings as emb
    import src.memory_vector as mv

    client = _FakeClient()
    monkeypatch.setattr(cc, "get_chroma_client", lambda: client)

    app_store = mv.MemoryVectorStore(str(tmp_path), embedding_model=_FakeEmbedder())
    assert app_store.healthy
    app_store._collection.add(ids=["m1"], embeddings=[[0.0]], documents=["x"], metadatas=[{}])
    mv.set_memory_vector_store(app_store)

    def _no_embed():
        raise RuntimeError("embedding endpoint unavailable")

    monkeypatch.setattr(emb, "get_embedding_client", _no_embed)

    handler, SessionLocal = _wipe_handler(monkeypatch)
    _seed_memories(SessionLocal, 1)

    result = handler(kind="memory", request=_request())

    assert result["vector_cleared"] is True
    assert app_store.count() == 0


def test_wipe_memory_never_500s_on_vector_failure(monkeypatch):
    """C14b: if resolving the vector store raises, the wipe must still return
    200/deleted with vector_cleared=False (no NameError/500 on an already-committed
    DB delete)."""
    import src.memory_vector as mv
    from core.database import Memory

    def _boom(*a, **k):
        raise RuntimeError("accessor blew up")

    monkeypatch.setattr(mv, "get_memory_vector_store", _boom)

    handler, SessionLocal = _wipe_handler(monkeypatch)
    _seed_memories(SessionLocal, 2)

    result = handler(kind="memory", request=_request())

    assert result["status"] == "deleted"
    assert result["count"] == 2
    assert result["vector_cleared"] is False
    db = SessionLocal()
    assert db.query(Memory).count() == 0
    db.close()
