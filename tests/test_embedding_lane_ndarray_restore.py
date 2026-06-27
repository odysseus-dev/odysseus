"""Embedding-lane reset must restore rows even when chromadb returns the
preserved embeddings as a numpy ndarray.

Real chromadb returns collection.get(include=["embeddings"]) as a numpy
ndarray. The restore-after-failed-rewrite path used `embeddings or []` and a
bare `if ... and embeddings:`, both of which raise
"truth value of an array ... is ambiguous" on an ndarray — aborting the
restore and wiping the collection the reset was meant to preserve.

This mirrors test_lane_reset_restores_existing_collection_when_rewrite_fails
in test_embedding_lanes.py, but the preserved embeddings come back as ndarray.
"""
import numpy as np

from src.embedding_lanes import build_embedding_lanes
from tests.helpers.embedding_lanes import FakeChroma, FakeEmbedder, patch_chroma


def test_lane_reset_restores_when_chroma_returns_numpy_embeddings(monkeypatch):
    fake = FakeChroma()
    old_custom = fake.get_or_create_collection(
        "odysseus_memories_custom",
        metadata={
            "embedding_lane": "custom",
            "embedding_dimension": 384,
            "embedding_fingerprint": "old",
        },
    )
    old_custom.add(
        ids=["existing-memory"],
        embeddings=[[0.0] * 384],
        documents=["existing custom memory"],
        metadatas=[{"source": "memory"}],
    )

    # Make the preserved embeddings come back as a numpy ndarray, like real
    # chromadb does.
    real_get = old_custom.get

    def ndarray_get(*args, **kwargs):
        result = real_get(*args, **kwargs)
        result["embeddings"] = np.array(result["embeddings"])
        return result

    old_custom.get = ndarray_get

    # Force the post-reset rewrite to fail so the restore branch runs.
    fake.fail_next_add_for["odysseus_memories_custom"] = 1
    patch_chroma(monkeypatch, fake)

    import src.embedding_lanes as lanes

    monkeypatch.setattr(lanes, "_build_custom_client", lambda: FakeEmbedder(768, "nomic", "http://embeddings/v1"))

    def fail_fastembed():
        raise RuntimeError("fastembed missing")

    monkeypatch.setattr(lanes, "_build_fastembed_client", fail_fastembed)

    built = build_embedding_lanes("odysseus_memories")

    # Both lanes are unavailable, but the existing row must survive — not be
    # wiped by an ndarray-truthiness crash in the restore path.
    assert built == []
    restored = fake.collections["odysseus_memories_custom"]
    assert restored.count() == 1
    assert restored.get()["ids"] == ["existing-memory"]
    assert len(restored.rows["existing-memory"]["embedding"]) == 384
