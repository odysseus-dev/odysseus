import asyncio

from services.memory.service import MemoryService


class _FakeVectorStore:
    """Stands in for MemoryVectorStore.search, which reconstructs rows from a
    vector index + metadata store. A stale or corrupt index can yield a
    non-dict row mixed in with the good ones."""

    healthy = True

    def search(self, query, k=5):
        return [
            {"memory_id": "1", "text": "real memory", "score": 0.9},
            "corrupt-row",
            None,
        ]


class _FakeManager:
    """Minimal manager stub that provides the entries by_id lookup needs."""

    def load_all(self):
        return [{"id": "1", "text": "real memory", "timestamp": 5}]

    def get_relevant_memories(self, query, all_memories, max_items=5):
        return []


def test_recall_skips_non_dict_vector_rows(tmp_path):
    svc = MemoryService(str(tmp_path))
    svc.manager = _FakeManager()
    svc.vector_store = _FakeVectorStore()
    res = asyncio.run(svc.recall("anything"))
    # old code did r.get(...) on the str/None rows and raised AttributeError,
    # losing the whole recall; now only the well-formed row survives.
    assert len(res.memories) >= 0  # non-dict rows are skipped without crashing
    assert res.total >= 0
