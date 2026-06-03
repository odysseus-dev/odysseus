"""Regression tests for the admin memory-wipe vector path.

`routes/admin_wipe_routes.py` clears the memory vector index on a memory wipe via:

    from src.memory_vector import get_memory_vector_store
    mv = get_memory_vector_store()
    if mv and hasattr(mv, "clear"):
        mv.clear()

Both `get_memory_vector_store` and `MemoryVectorStore.clear` were missing, so the
import raised (swallowed by the route's broad except) and wiped memories lingered in
semantic search. These tests assert both now exist and that clear() empties the index.
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

    def encode(self, texts, normalize_embeddings=True):  # not exercised by these tests
        import numpy as np
        return np.zeros((len(texts), 3))


def _make_healthy_store(monkeypatch, data_dir):
    import src.chroma_client as cc
    import src.embeddings as emb
    import src.memory_vector as mv

    client = _FakeClient()
    monkeypatch.setattr(cc, "get_chroma_client", lambda: client)
    monkeypatch.setattr(emb, "get_embedding_client", lambda: _FakeEmbedder())
    monkeypatch.setattr(mv, "_store", None, raising=False)
    store = mv.MemoryVectorStore(str(data_dir))
    return mv, store


def test_admin_wipe_import_path_resolves_and_clear_is_callable(monkeypatch):
    """Exactly what admin_wipe does: the import must resolve and clear() must exist
    and be safe to call (no-op when unhealthy, i.e. no ChromaDB in CI)."""
    import src.memory_vector as mv
    monkeypatch.setattr(mv, "_store", None, raising=False)
    from src.memory_vector import get_memory_vector_store

    store = get_memory_vector_store()
    assert store is not None
    assert hasattr(store, "clear")
    store.clear()  # unhealthy in CI -> safe no-op, must not raise


def test_clear_empties_the_collection_when_healthy(monkeypatch, tmp_path):
    mv, store = _make_healthy_store(monkeypatch, tmp_path)
    assert store.healthy
    store._collection.add(ids=["a", "b"], embeddings=[[0.0]] * 2, documents=["x", "y"], metadatas=[{}, {}])
    assert store.count() == 2
    store.clear()
    assert store.count() == 0


def test_get_memory_vector_store_caches_when_healthy(monkeypatch, tmp_path):
    mv, _ = _make_healthy_store(monkeypatch, tmp_path)
    a = mv.get_memory_vector_store(str(tmp_path))
    b = mv.get_memory_vector_store(str(tmp_path))
    assert a.healthy
    assert a is b


def test_clear_does_not_raise_if_chroma_dies_after_init(monkeypatch, tmp_path):
    """Healthy at init, then ChromaDB becomes unreachable before clear() — clear()
    must log + return, not propagate (so the admin wipe isn't relied on to swallow
    it, and a transient outage can't 500 the handler)."""
    mv, store = _make_healthy_store(monkeypatch, tmp_path)
    assert store.healthy

    import src.chroma_client as cc

    def _down():
        raise RuntimeError("ChromaDB is not reachable")

    monkeypatch.setattr(cc, "get_chroma_client", _down)
    store.clear()  # must NOT raise


def test_admin_wipe_memory_route_invokes_vector_clear(monkeypatch):
    """Functional: DELETE /api/admin/wipe/memory must call clear() on the vector
    store (the wiring that silently no-op'd before). Mirrors test_admin_wipe_gallery's
    handler-level harness; spies on the store and no-ops the file wipe so it can't
    touch real data/."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from core.database import Base
    from fastapi import Request
    import routes.admin_wipe_routes as awr
    import src.memory_vector as mv

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine)

    monkeypatch.setattr(awr, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(awr, "require_admin", lambda r: None)
    monkeypatch.setattr(awr, "_wipe_memory_files", lambda: None)  # don't touch real data/

    cleared = {"n": 0}

    class _SpyStore:
        healthy = True

        def clear(self):
            cleared["n"] += 1

    monkeypatch.setattr(mv, "get_memory_vector_store", lambda *a, **k: _SpyStore())

    request = Request(scope={"type": "http", "headers": []})
    router = awr.setup_admin_wipe_routes(session_manager=None)
    handler = next(r for r in router.routes if r.path == "/api/admin/wipe/{kind}").endpoint

    result = handler(kind="memory", request=request)
    assert result["status"] == "deleted"
    assert result["kind"] == "memory"
    assert cleared["n"] == 1, "route did not invoke the vector store clear()"


def test_get_memory_vector_store_recovers_from_unhealthy(monkeypatch, tmp_path):
    """An initially-unhealthy store is not pinned: a later call retries and returns
    a healthy instance once the client works (no stuck-unhealthy cache)."""
    import src.chroma_client as cc
    import src.embeddings as emb
    import src.memory_vector as mv

    monkeypatch.setattr(mv, "_store", None, raising=False)
    monkeypatch.setattr(emb, "get_embedding_client", lambda: _FakeEmbedder())

    def _down():
        raise RuntimeError("down")

    monkeypatch.setattr(cc, "get_chroma_client", _down)
    first = mv.get_memory_vector_store(str(tmp_path))
    assert not first.healthy

    monkeypatch.setattr(cc, "get_chroma_client", lambda: _FakeClient())
    second = mv.get_memory_vector_store(str(tmp_path))
    assert second.healthy
    assert second is not first
