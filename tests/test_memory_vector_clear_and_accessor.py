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


# ---------------------------------------------------------------------------
# Decouple clear/count from embedding availability + registration (PR #1968
# follow-up). The reviewer's case: a memory-wipe must clear ghost vectors even
# when the standalone embedding factory is down at wipe time, as long as
# ChromaDB is reachable.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_singletons():
    """Reset BOTH process singletons around every test so the module-global
    `_store` and the ChromaDB client can't leak across tests and flake by order."""
    import src.memory_vector as mv
    import src.chroma_client as cc
    mv._store = None
    cc._client = None
    yield
    mv._store = None
    cc._client = None


def _make_degraded_store(monkeypatch, data_dir):
    """ChromaDB up, embedder unavailable -> `_collection` set, `_healthy` False."""
    import src.chroma_client as cc
    import src.embeddings as emb
    import src.memory_vector as mv

    client = _FakeClient()
    monkeypatch.setattr(cc, "get_chroma_client", lambda: client)

    def _no_embed():
        raise RuntimeError("embedding endpoint unavailable")

    monkeypatch.setattr(emb, "get_embedding_client", _no_embed)
    monkeypatch.setattr(mv, "_store", None, raising=False)
    store = mv.MemoryVectorStore(str(data_dir))
    return mv, store, client


class _StubbornClient:
    """delete_collection RAISES; get_or_create_collection returns the SAME
    (still-populated) collection — models ChromaDB after a swallowed delete."""

    def __init__(self):
        self.coll = _FakeCollection()

    def get_or_create_collection(self, name, metadata=None):
        return self.coll

    def delete_collection(self, name):
        raise RuntimeError("delete failed")


class _RecreateFailsClient:
    """delete_collection succeeds; the recreate (2nd get_or_create) raises."""

    def __init__(self):
        self.coll = _FakeCollection()
        self._calls = 0

    def get_or_create_collection(self, name, metadata=None):
        self._calls += 1
        if self._calls == 1:
            return self.coll  # initial construction
        raise RuntimeError("recreate failed")

    def delete_collection(self, name):
        pass


def test_init_chroma_up_embedder_down_is_degraded_with_live_collection(monkeypatch, tmp_path):
    """A2: Chroma up but no embedder -> NOT healthy, yet `_collection` is live so
    clear/count still work, and `_model` is None."""
    _mv, store, _client = _make_degraded_store(monkeypatch, tmp_path)
    assert store.healthy is False
    assert store._collection is not None
    assert store._model is None


def test_init_chroma_down_leaves_no_collection(monkeypatch, tmp_path):
    """A3: Chroma connect fails -> not healthy and no collection at all."""
    import src.chroma_client as cc
    import src.embeddings as emb
    import src.memory_vector as mv

    monkeypatch.setattr(emb, "get_embedding_client", lambda: _FakeEmbedder())

    def _down():
        raise RuntimeError("chroma down")

    monkeypatch.setattr(cc, "get_chroma_client", _down)
    store = mv.MemoryVectorStore(str(tmp_path))
    assert store.healthy is False
    assert store._collection is None


def test_count_reflects_collection_when_embeddings_down(monkeypatch, tmp_path):
    """A4: count() reflects the LIVE collection on a degraded store (pre-fix it
    masked to 0 whenever unhealthy, hiding surviving ghosts)."""
    _mv, store, _client = _make_degraded_store(monkeypatch, tmp_path)
    store._collection.add(ids=["m1"], embeddings=[[0.0]], documents=["x"], metadatas=[{}])
    assert store.count() == 1


def test_clear_empties_when_embeddings_down(monkeypatch, tmp_path):
    """A5 (core regression): clear() empties the index on a Chroma-up/embeddings-down
    store. Pre-fix clear() was `_healthy`-gated and silently no-op'd here."""
    _mv, store, _client = _make_degraded_store(monkeypatch, tmp_path)
    store._collection.add(ids=["a", "b"], embeddings=[[0.0]] * 2, documents=["x", "y"], metadatas=[{}, {}])
    assert store.count() == 2
    assert store.clear() is True
    assert store.count() == 0


def test_clear_returns_false_when_no_collection(monkeypatch, tmp_path):
    """A6: clear() on a Chroma-down store (no collection) is a safe False, no raise."""
    import src.chroma_client as cc
    import src.memory_vector as mv

    def _down():
        raise RuntimeError("chroma down")

    monkeypatch.setattr(cc, "get_chroma_client", _down)
    store = mv.MemoryVectorStore(str(tmp_path))
    assert store._collection is None
    assert store.clear() is False


def test_double_clear_is_idempotent(monkeypatch, tmp_path):
    """A8b: clearing twice stays True and empty, no raise."""
    _mv, store = _make_healthy_store(monkeypatch, tmp_path)
    store._collection.add(ids=["a"], embeddings=[[0.0]], documents=["x"], metadatas=[{}])
    assert store.clear() is True
    assert store.clear() is True
    assert store.count() == 0


def test_clear_false_when_delete_swallowed_but_collection_populated(monkeypatch, tmp_path):
    """A8c (the "vector_cleared can't lie" guard): a swallowed delete_collection +
    recreate-returns-populated must yield False, not True. A naive "didn't throw"
    clear() would return True while ghosts remain."""
    import src.chroma_client as cc
    import src.embeddings as emb
    import src.memory_vector as mv

    client = _StubbornClient()
    monkeypatch.setattr(cc, "get_chroma_client", lambda: client)
    monkeypatch.setattr(emb, "get_embedding_client", lambda: _FakeEmbedder())
    store = mv.MemoryVectorStore(str(tmp_path))
    assert store.healthy
    store._collection.add(ids=["ghost"], embeddings=[[0.0]], documents=["x"], metadatas=[{}])
    assert store.count() == 1
    assert store.clear() is False
    assert store.count() == 1


def test_clear_false_when_recreate_raises_midway(monkeypatch, tmp_path):
    """A8d: delete succeeds, recreate raises -> clear() returns False, no propagation."""
    import src.chroma_client as cc
    import src.embeddings as emb
    import src.memory_vector as mv

    client = _RecreateFailsClient()
    monkeypatch.setattr(cc, "get_chroma_client", lambda: client)
    monkeypatch.setattr(emb, "get_embedding_client", lambda: _FakeEmbedder())
    store = mv.MemoryVectorStore(str(tmp_path))
    assert store.healthy
    assert store.clear() is False


def test_rebuild_with_entries_and_no_model_preserves_data(monkeypatch, tmp_path):
    """A8e (data-loss guard): rebuild([entries]) on a model-less store must NOT
    delete the existing collection (which would be silent data loss); it's a logged
    no-op leaving vectors intact."""
    _mv, store, _client = _make_degraded_store(monkeypatch, tmp_path)
    assert store._model is None
    store._collection.add(ids=["keep"], embeddings=[[0.0]], documents=["x"], metadatas=[{}])
    assert store.count() == 1
    store.rebuild([{"id": "new", "text": "hello"}])
    assert store.count() == 1


def test_registered_store_reused_when_embeddings_down(monkeypatch, tmp_path):
    """B9 (reviewer's case): the app registered a healthy store built with an
    embedder; embeddings are down at wipe time. The accessor returns the REGISTERED
    store (not an unhealthy second store), and clear() empties it."""
    import src.chroma_client as cc
    import src.embeddings as emb
    import src.memory_vector as mv

    client = _FakeClient()
    monkeypatch.setattr(cc, "get_chroma_client", lambda: client)

    app_store = mv.MemoryVectorStore(str(tmp_path), embedding_model=_FakeEmbedder())
    assert app_store.healthy
    app_store._collection.add(ids=["m1"], embeddings=[[0.0]], documents=["x"], metadatas=[{}])
    assert app_store.count() == 1
    mv.set_memory_vector_store(app_store)

    def _no_embed():
        raise RuntimeError("embedding endpoint unavailable")

    monkeypatch.setattr(emb, "get_embedding_client", _no_embed)

    got = mv.get_memory_vector_store()
    assert got is app_store
    assert got.healthy
    assert got.clear() is True
    assert app_store.count() == 0


def test_accessor_clears_when_unregistered_and_embeddings_down(monkeypatch, tmp_path):
    """B10 (startup-degraded gap that registration alone misses): nothing registered,
    embeddings down, but Chroma up with a pre-seeded ghost. The freshly-built accessor
    store still has a collection and clears it."""
    import src.chroma_client as cc
    import src.embeddings as emb
    import src.memory_vector as mv

    client = _FakeClient()
    coll = client.get_or_create_collection(mv.MemoryVectorStore.COLLECTION_NAME)
    coll.add(ids=["ghost"], embeddings=[[0.0]], documents=["x"], metadatas=[{}])

    monkeypatch.setattr(cc, "get_chroma_client", lambda: client)

    def _no_embed():
        raise RuntimeError("down")

    monkeypatch.setattr(emb, "get_embedding_client", _no_embed)

    store = mv.get_memory_vector_store(str(tmp_path))
    assert store._collection is not None
    assert store.healthy is False
    assert store.count() == 1
    assert store.clear() is True
    assert store.count() == 0


def test_set_memory_vector_store_registers_for_accessor(monkeypatch, tmp_path):
    """D15 (thin wiring assertion): set_memory_vector_store(s) makes the accessor
    return exactly s. The app_initializer call site sits inside `if memory_vector.healthy:`
    so only healthy stores are registered (verified by inspection / the route tests)."""
    _mv, store = _make_healthy_store(monkeypatch, tmp_path)
    import src.memory_vector as mv
    mv.set_memory_vector_store(store)
    assert mv.get_memory_vector_store() is store
