import os
import tempfile
from unittest import mock

import pytest

from src.skill_vector import SkillVectorStore

# Helper dummy lane that satisfies the minimal interface used by SkillVectorStore
class DummyCollection:
    def __init__(self):
        self.store = {}
        self._count = 0

    def add(self, ids, embeddings, documents, metadatas):
        for i, sid in enumerate(ids):
            self.store[sid] = {
                "embedding": embeddings[i],
                "document": documents[i],
                "metadata": metadatas[i],
            }
        self._count += len(ids)

    def get(self, ids):
        # Return empty ids list if not present
        found = [sid for sid in ids if sid in self.store]
        return {"ids": found}

    def delete(self, ids):
        for sid in ids:
            self.store.pop(sid, None)
        self._count = max(0, self._count - len(ids))

    def query(self, query_embeddings, n_results, include):
        # Very naive cosine similarity: return first stored ids with dummy distance 0.1
        ids = list(self.store.keys())[:n_results]
        distances = [0.1] * len(ids)
        return {"ids": [ids], "distances": [distances]}

    def count(self):
        return self._count

class DummyLane:
    def __init__(self, name="fastembed"):
        self.name = name
        self.collection = DummyCollection()

    def encode(self, texts):
        # Return a list of zero‑vectors matching length of texts
        return [[0.0] * 384 for _ in texts]

    def count(self):
        return self.collection.count()

# ---------------------------------------------------------------------------
# Ideal (sanity) scenario
# ---------------------------------------------------------------------------
def test_skill_vector_ideal(monkeypatch):
    # Patch the lane builder to return a single dummy lane
    monkeypatch.setattr('src.skill_vector.build_embedding_lanes', lambda _: [DummyLane()])

    with tempfile.TemporaryDirectory() as td:
        sv = SkillVectorStore(td)
        assert sv.healthy is True
        # Add a couple of dummy skills
        sv.add("skill1", "when to use skill one")
        sv.add("skill2", "when to use skill two")
        assert sv.count() == 2
        # Rebuild from a skill list – this should clear and re‑add
        sv.rebuild([
            {"name": "skill1", "when_to_use": "when to use skill one"},
            {"name": "skill2", "when_to_use": "when to use skill two"},
        ])
        assert sv.count() == 2
        # Search should return both ids (order not important) and a valid score
        results = sv.search("skill one", k=5)
        ids = {r["skill_id"] for r in results}
        assert ids == {"skill1", "skill2"}
        for r in results:
            assert 0.0 <= r["score"] <= 1.0

# ---------------------------------------------------------------------------
# Degradation (fallback) scenario – simulate unhealthy store
# ---------------------------------------------------------------------------
def test_skill_vector_degraded(monkeypatch):
    # Force the store to be unhealthy
    with tempfile.TemporaryDirectory() as td:
        sv = SkillVectorStore(td)
        # Manually mark as unhealthy
        sv._healthy = False
        # Count and search should gracefully return empty structures
        assert sv.count() == 0
        assert sv.search("anything") == []
        # Rebuild should be a no‑op and not raise
        sv.rebuild([])  # should not explode
        assert sv.count() == 0
