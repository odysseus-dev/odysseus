import asyncio

from services.memory.service import MemoryService


class _FakeVectorStore:
    """Stands in for MemoryVectorStore.search, which reconstructs rows from a
    vector index + metadata store. A stale or corrupt index can yield a
    non-dict row mixed in with the good ones."""

    def search(self, query, k=5):
        return [
            {"id": "1", "text": "real memory", "timestamp": 5},
            "corrupt-row",
            None,
        ]


def test_recall_skips_non_dict_vector_rows(tmp_path):
    svc = MemoryService(str(tmp_path))
    svc.vector_store = _FakeVectorStore()
    res = asyncio.run(svc.recall("anything"))
    # old code did r.get(...) on the str/None rows and raised AttributeError,
    # losing the whole recall; now only the well-formed row survives.
    assert [m.id for m in res.memories] == ["1"]
    assert res.total == 1
