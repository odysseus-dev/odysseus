import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routes import backup_routes
from routes.admin_wipe import admin_wipe_routes
import src.chroma_client as chroma_client
import src.memory_vector as memory_vector
from src.chat_processor import ChatProcessor
from src.memory_vector import MemoryVectorStore


class _BrokenLane:
    name = "broken"

    def count(self):
        raise RuntimeError("chroma offline")


def test_vector_query_reports_a_per_call_outage():
    store = MemoryVectorStore.__new__(MemoryVectorStore)
    store._healthy = True
    store._lanes = [_BrokenLane()]

    rows, query_healthy = store.search_with_status("alpha", k=5)

    assert rows == []
    assert query_healthy is False
    assert store.healthy is True  # startup state did not predict this call


def test_chat_switches_to_keyword_weights_for_failed_vector_call():
    vector = SimpleNamespace(
        healthy=True,
        search_with_status=lambda query, k: ([], False),
    )
    processor = ChatProcessor(None, None, memory_vector=vector)
    memory = {
        "id": "m1",
        "text": "alpha beta",
        "timestamp": time.time(),
        "category": "fact",
    }

    assert processor._hybrid_retrieve("alpha beta", [memory], k=1) == [memory]

    healthy_empty = SimpleNamespace(
        healthy=True,
        search_with_status=lambda query, k: ([], True),
    )
    processor.memory_vector = healthy_empty
    assert processor._hybrid_retrieve("alpha beta", [memory], k=1) == []


class _MemoryManager:
    def __init__(self, rows):
        self.rows = list(rows)
        self.saves = []

    def load_all_for_update(self):
        return list(self.rows)

    def save(self, rows):
        self.rows = list(rows)
        self.saves.append(list(rows))


class _Vector:
    healthy = True

    def __init__(self, events=None, fail_clear=False):
        self.rebuilds = []
        self.events = events
        self.fail_clear = fail_clear

    def rebuild(self, rows, *, strict=False):
        assert strict is True
        self.rebuilds.append(list(rows))

    def clear(self, *, strict=False):
        assert strict is True
        if self.events is not None:
            self.events.append("vector_clear")
        if self.fail_clear:
            raise RuntimeError("partial vector clear")


class _AlwaysFailingVector(_Vector):
    def rebuild(self, rows, *, strict=False):
        assert strict is True
        self.rebuilds.append(list(rows))
        raise RuntimeError("vector rebuild unavailable")


class _Request:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


def _endpoint(router, path):
    return next(route.endpoint for route in router.routes if route.path == path)


def test_backup_import_rebuilds_the_complete_multi_user_corpus(monkeypatch):
    manager = _MemoryManager([
        {"id": "alice-old", "owner": "alice", "text": "alpha"},
        {"id": "bob-old", "owner": "bob", "text": "beta"},
    ])
    vector = _Vector()
    monkeypatch.setattr(backup_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(backup_routes, "get_current_user", lambda request: "alice")
    router = backup_routes.setup_backup_routes(
        manager,
        SimpleNamespace(),
        SimpleNamespace(),
        memory_vector=vector,
    )

    result = asyncio.run(_endpoint(router, "/api/import")(
        _Request({"memories": [{"id": "alice-new", "text": "gamma"}]})
    ))

    assert result["ok"] is True
    assert {row["id"] for row in vector.rebuilds[-1]} == {
        "alice-old",
        "bob-old",
        "alice-new",
    }
    assert next(row for row in vector.rebuilds[-1] if row["id"] == "alice-new")["owner"] == "alice"


def test_backup_import_preserves_optional_vector_compatibility(monkeypatch):
    manager = _MemoryManager([])
    monkeypatch.setattr(backup_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(backup_routes, "get_current_user", lambda request: "alice")
    router = backup_routes.setup_backup_routes(
        manager,
        SimpleNamespace(),
        SimpleNamespace(),
        memory_vector=None,
    )

    result = asyncio.run(_endpoint(router, "/api/import")(
        _Request({"memories": [{"id": "alice-new", "text": "gamma"}]})
    ))

    assert result["ok"] is True
    assert manager.rows == [{"id": "alice-new", "text": "gamma", "owner": "alice"}]


def test_backup_import_reports_incomplete_vector_compensation(monkeypatch):
    original = [{"id": "alice-old", "owner": "alice", "text": "alpha"}]
    manager = _MemoryManager(original)
    vector = _AlwaysFailingVector()
    monkeypatch.setattr(backup_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(backup_routes, "get_current_user", lambda request: "alice")
    router = backup_routes.setup_backup_routes(
        manager,
        SimpleNamespace(),
        SimpleNamespace(),
        memory_vector=vector,
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_endpoint(router, "/api/import")(
            _Request({"memories": [{"id": "alice-new", "text": "gamma"}]})
        ))

    assert exc.value.status_code == 503
    assert "rollback was incomplete" in exc.value.detail
    assert manager.rows == original
    assert vector.rebuilds == [
        original + [{"id": "alice-new", "text": "gamma", "owner": "alice"}],
        original,
    ]


class _Query:
    def __init__(self, events):
        self.events = events

    def count(self):
        return 2

    def delete(self):
        self.events.append("sql_delete")


class _Db:
    def __init__(self, events, fail_commit=False):
        self.events = events
        self.fail_commit = fail_commit

    def query(self, model):
        return _Query(self.events)

    def commit(self):
        self.events.append("sql_commit")
        if self.fail_commit:
            raise RuntimeError("commit failed")

    def rollback(self):
        self.events.append("sql_rollback")

    def close(self):
        self.events.append("close")


def test_admin_memory_wipe_clears_vectors_before_sql_commit(monkeypatch):
    events = []
    manager = _MemoryManager([
        {"id": "alice", "owner": "alice", "text": "alpha"},
        {"id": "bob", "owner": "bob", "text": "beta"},
    ])
    vector = _Vector(events)
    monkeypatch.setattr(admin_wipe_routes, "SessionLocal", lambda: _Db(events))
    monkeypatch.setattr(admin_wipe_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(admin_wipe_routes, "_wipe_memory_sidecars", lambda: None)
    router = admin_wipe_routes.setup_admin_wipe_routes(
        None,
        memory_manager=manager,
        memory_vector=vector,
    )

    _endpoint(router, "/api/admin/wipe/{kind}")("memory", SimpleNamespace())

    assert events.index("vector_clear") < events.index("sql_commit")
    assert manager.rows == []


def test_admin_memory_wipe_restores_full_corpus_if_commit_fails(monkeypatch):
    events = []
    original = [
        {"id": "alice", "owner": "alice", "text": "alpha"},
        {"id": "bob", "owner": "bob", "text": "beta"},
    ]
    manager = _MemoryManager(original)
    vector = _Vector(events)
    monkeypatch.setattr(
        admin_wipe_routes,
        "SessionLocal",
        lambda: _Db(events, fail_commit=True),
    )
    monkeypatch.setattr(admin_wipe_routes, "require_admin", lambda request: None)
    router = admin_wipe_routes.setup_admin_wipe_routes(
        None,
        memory_manager=manager,
        memory_vector=vector,
    )

    with pytest.raises(HTTPException) as exc:
        _endpoint(router, "/api/admin/wipe/{kind}")("memory", SimpleNamespace())

    assert exc.value.status_code == 500
    assert manager.rows == original
    assert vector.rebuilds[-1] == original
    assert "sql_rollback" in events


def test_admin_memory_wipe_compensates_a_partial_vector_clear(monkeypatch):
    events = []
    original = [
        {"id": "alice", "owner": "alice", "text": "alpha"},
        {"id": "bob", "owner": "bob", "text": "beta"},
    ]
    manager = _MemoryManager(original)
    vector = _Vector(events, fail_clear=True)
    monkeypatch.setattr(admin_wipe_routes, "SessionLocal", lambda: _Db(events))
    monkeypatch.setattr(admin_wipe_routes, "require_admin", lambda request: None)
    router = admin_wipe_routes.setup_admin_wipe_routes(
        None,
        memory_manager=manager,
        memory_vector=vector,
    )

    with pytest.raises(HTTPException):
        _endpoint(router, "/api/admin/wipe/{kind}")("memory", SimpleNamespace())

    assert "sql_commit" not in events
    assert manager.rows == original
    assert vector.rebuilds[-1] == original


def test_rebuild_reset_failure_marks_store_unhealthy(monkeypatch, tmp_path):
    store = MemoryVectorStore.__new__(MemoryVectorStore)
    store._healthy = True
    store._rebuild_marker = tmp_path / MemoryVectorStore.REBUILD_MARKER

    def fail_reset():
        raise RuntimeError("reset failed")

    monkeypatch.setattr(store, "_replace_collections", fail_reset)

    with pytest.raises(RuntimeError):
        store.rebuild([], strict=True)

    assert store.healthy is False


class _FailSecondBatchEmbedder:
    def __init__(self, dim):
        self.dim = dim
        self.calls = 0

    def get_sentence_embedding_dimension(self):
        return self.dim

    def encode(self, texts, normalize_embeddings=True):
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("second embedding batch failed")
        return [[1.0] * self.dim for _ in texts]


@pytest.mark.parametrize("strict", [False, True])
def test_failed_later_batch_is_not_adopted_after_restart(monkeypatch, tmp_path, strict):
    from tests.helpers.embedding_lanes import FakeChroma, patch_chroma
    import src.embedding_lanes as lanes

    fake = FakeChroma()
    patch_chroma(monkeypatch, fake)
    custom_clients = []
    fast_clients = []

    def custom_client():
        client = _FailSecondBatchEmbedder(768)
        custom_clients.append(client)
        return client

    def fast_client():
        client = _FailSecondBatchEmbedder(384)
        fast_clients.append(client)
        return client

    monkeypatch.setattr(lanes, "_build_custom_client", custom_client)
    monkeypatch.setattr(lanes, "_build_fastembed_client", fast_client)
    rows = [{"id": f"memory-{i}", "text": f"memory text {i}"} for i in range(101)]
    store = MemoryVectorStore(tmp_path)

    if strict:
        with pytest.raises(RuntimeError, match="second embedding batch failed"):
            store.rebuild(rows, strict=True)
    else:
        assert store.rebuild(rows) is False

    assert store.healthy is False
    assert store.count() == 0
    assert "odysseus_memories_custom" not in fake.collections
    assert "odysseus_memories_fastembed" not in fake.collections

    restarted = MemoryVectorStore(tmp_path)

    assert restarted.healthy is True
    assert restarted.count() == 0
    assert len(restarted._lanes) == 2
    assert not (tmp_path / MemoryVectorStore.REBUILD_MARKER).exists()


def test_strict_later_batch_preserves_primary_error_when_cleanup_fails(monkeypatch, tmp_path):
    from tests.helpers.embedding_lanes import FakeChroma, patch_chroma
    import src.embedding_lanes as lanes

    fake = FakeChroma()
    patch_chroma(monkeypatch, fake)
    monkeypatch.setattr(lanes, "_build_custom_client", lambda: _FailSecondBatchEmbedder(768))
    monkeypatch.setattr(lanes, "_build_fastembed_client", lambda: _FailSecondBatchEmbedder(384))
    store = MemoryVectorStore(tmp_path)
    original_delete = fake.delete_collection
    delete_calls = 0

    def fail_cleanup(name):
        nonlocal delete_calls
        delete_calls += 1
        if delete_calls > 3:
            raise RuntimeError("cleanup backend unavailable")
        original_delete(name)

    fake.delete_collection = fail_cleanup
    rows = [{"id": f"memory-{i}", "text": f"memory text {i}"} for i in range(101)]

    with pytest.raises(RuntimeError, match="second embedding batch failed"):
        store.rebuild(rows, strict=True)

    assert store.healthy is False
    assert (tmp_path / MemoryVectorStore.REBUILD_MARKER).exists()
    assert any(collection.count() == 100 for collection in fake.collections.values())

    fake.delete_collection = original_delete
    restarted = MemoryVectorStore(tmp_path)

    assert restarted.healthy is True
    assert restarted.count() == 0
    assert not (tmp_path / MemoryVectorStore.REBUILD_MARKER).exists()


def test_strict_clear_propagates_collection_delete_failure(monkeypatch):
    class FailingClient:
        def delete_collection(self, name):
            raise RuntimeError(f"backend unavailable while deleting {name}")

    store = MemoryVectorStore.__new__(MemoryVectorStore)
    store._healthy = True
    store._lanes = []
    store._collection = None
    monkeypatch.setattr(chroma_client, "get_chroma_client", lambda: FailingClient())
    monkeypatch.setattr(
        memory_vector,
        "build_embedding_lanes",
        lambda name: pytest.fail("reset must stop after a collection deletion failure"),
    )

    with pytest.raises(RuntimeError, match="backend unavailable while deleting"):
        store.clear(strict=True)

    assert store.healthy is False
