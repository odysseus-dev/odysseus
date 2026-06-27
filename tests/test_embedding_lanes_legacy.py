from tests.helpers.embedding_lanes import (
    FakeChroma,
    FakeEmbedder,
    FailingEmbedder,
    patch_chroma,
)


def test_legacy_collection_backfills_fastembed_lane(monkeypatch):
    fake = FakeChroma()
    legacy = fake.get_or_create_collection("odysseus_memories", metadata={"hnsw:space": "cosine"})
    legacy.add(
        ids=["legacy-memory"],
        embeddings=[[0.0] * 384],
        documents=["legacy memory row"],
        metadatas=[{"source": "memory"}],
    )
    patch_chroma(monkeypatch, fake)

    import src.embedding_lanes as lanes

    monkeypatch.setattr(lanes, "_build_custom_client", lambda: None)
    monkeypatch.setattr(lanes, "_build_fastembed_client", lambda: FakeEmbedder(384, "mini", "local://fastembed"))

    from src.memory_vector import MemoryVectorStore

    store = MemoryVectorStore("data")

    assert store.count() == 1
    assert fake.collections["odysseus_memories"].count() == 1
    assert fake.collections["odysseus_memories_fastembed"].count() == 1


def test_legacy_collection_backfills_custom_only_lane(monkeypatch):
    fake = FakeChroma()
    legacy = fake.get_or_create_collection("odysseus_memories", metadata={"hnsw:space": "cosine"})
    legacy.add(
        ids=["legacy-memory"],
        embeddings=[[0.0] * 384],
        documents=["legacy memory row"],
        metadatas=[{"source": "memory"}],
    )
    patch_chroma(monkeypatch, fake)

    import src.embedding_lanes as lanes

    monkeypatch.setattr(lanes, "_build_custom_client", lambda: FakeEmbedder(768, "nomic", "http://embeddings/v1"))

    def fail_fastembed():
        raise RuntimeError("fastembed missing")

    monkeypatch.setattr(lanes, "_build_fastembed_client", fail_fastembed)

    from src.memory_vector import MemoryVectorStore

    store = MemoryVectorStore("data")

    assert store.count() == 1
    assert "odysseus_memories_fastembed" not in fake.collections
    assert fake.collections["odysseus_memories_custom"].count() == 1
    assert len(fake.collections["odysseus_memories_custom"].rows["legacy-memory"]["embedding"]) == 768


def test_legacy_migration_continues_when_custom_backfill_fails(monkeypatch):
    fake = FakeChroma()
    legacy = fake.get_or_create_collection("odysseus_memories", metadata={"hnsw:space": "cosine"})
    legacy.add(
        ids=["legacy-memory"],
        embeddings=[[0.0] * 384],
        documents=["legacy memory row"],
        metadatas=[{"source": "memory"}],
    )
    patch_chroma(monkeypatch, fake)

    import src.embedding_lanes as lanes

    monkeypatch.setattr(lanes, "_build_custom_client", lambda: FailingEmbedder(768, "nomic", "http://embeddings/v1"))
    monkeypatch.setattr(lanes, "_build_fastembed_client", lambda: FakeEmbedder(384, "mini", "local://fastembed"))

    from src.memory_vector import MemoryVectorStore

    store = MemoryVectorStore("data")

    assert store.healthy
    assert fake.collections["odysseus_memories_custom"].count() == 0
    assert fake.collections["odysseus_memories_fastembed"].count() == 1


def test_legacy_migration_resumes_partial_lane_backfill(monkeypatch):
    fake = FakeChroma()
    legacy = fake.get_or_create_collection("odysseus_memories", metadata={"hnsw:space": "cosine"})
    legacy.add(
        ids=["legacy-1", "legacy-2"],
        embeddings=[[0.0] * 384, [0.0] * 384],
        documents=["legacy memory one", "legacy memory two"],
        metadatas=[{"source": "memory"}, {"source": "memory"}],
    )
    partial = fake.get_or_create_collection("odysseus_memories_fastembed", metadata={"embedding_lane": "fastembed"})
    partial.add(
        ids=["legacy-1"],
        embeddings=[[0.0] * 384],
        documents=["legacy memory one"],
        metadatas=[{"source": "memory"}],
    )
    patch_chroma(monkeypatch, fake)

    import src.embedding_lanes as lanes

    monkeypatch.setattr(lanes, "_build_custom_client", lambda: None)
    monkeypatch.setattr(lanes, "_build_fastembed_client", lambda: FakeEmbedder(384, "mini", "local://fastembed"))

    from src.memory_vector import MemoryVectorStore

    store = MemoryVectorStore("data")

    assert store.count() == 2
    assert set(fake.collections["odysseus_memories_fastembed"].get()["ids"]) == {"legacy-1", "legacy-2"}
