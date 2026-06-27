import pytest

from src.embedding_lanes import (
    LANE_CUSTOM,
    LANE_FASTEMBED,
    build_embedding_lanes,
)
from tests.helpers.embedding_lanes import (
    FakeChroma,
    FakeEmbedder,
    FailingEmbedder,
    patch_chroma,
)


def test_build_embedding_lanes_keeps_custom_and_fastembed_dimensions_separate(monkeypatch):
    fake = FakeChroma()
    patch_chroma(monkeypatch, fake)

    import src.embedding_lanes as lanes

    monkeypatch.setattr(
        lanes,
        "_build_custom_client",
        lambda: FakeEmbedder(768, "nomic-embed-text", "http://embeddings/v1"),
    )
    monkeypatch.setattr(
        lanes,
        "_build_fastembed_client",
        lambda: FakeEmbedder(384, "sentence-transformers/all-MiniLM-L6-v2", "local://fastembed"),
    )

    built = build_embedding_lanes("odysseus_memories")

    assert [lane.name for lane in built] == [LANE_CUSTOM, LANE_FASTEMBED]
    assert built[0].collection_name == "odysseus_memories_custom"
    assert built[0].dimension == 768
    assert built[1].collection_name == "odysseus_memories_fastembed"
    assert built[1].dimension == 384

    built[0].collection.add(ids=["custom"], embeddings=built[0].encode(["a"]), documents=["a"])
    built[1].collection.add(ids=["fast"], embeddings=built[1].encode(["a"]), documents=["a"])

    with pytest.raises(RuntimeError, match="dimension"):
        built[0].collection.query(query_embeddings=built[1].encode(["bad"]), n_results=1)


def test_build_embedding_lanes_recreates_only_custom_when_fingerprint_changes(monkeypatch):
    fake = FakeChroma()
    old_custom = fake.get_or_create_collection(
        "odysseus_rag_custom",
        metadata={
            "embedding_lane": "custom",
            "embedding_dimension": 768,
            "embedding_fingerprint": "old",
        },
    )
    old_custom.add(ids=["old"], embeddings=[[0.0] * 768], documents=["old"])
    fast = fake.get_or_create_collection(
        "odysseus_rag_fastembed",
        metadata={
            "embedding_lane": "fastembed",
            "embedding_dimension": 384,
        },
    )
    fast.add(ids=["fast"], embeddings=[[0.0] * 384], documents=["fast"])
    patch_chroma(monkeypatch, fake)

    import src.embedding_lanes as lanes

    monkeypatch.setattr(lanes, "_build_custom_client", lambda: FakeEmbedder(1024, "bge-large", "http://embeddings/v1"))
    monkeypatch.setattr(lanes, "_build_fastembed_client", lambda: FakeEmbedder(384, "sentence-transformers/all-MiniLM-L6-v2", "local://fastembed"))

    built = build_embedding_lanes("odysseus_rag")

    assert "odysseus_rag_custom" in fake.deleted
    assert fake.collections["odysseus_rag_custom"].count() == 1
    assert len(fake.collections["odysseus_rag_custom"].rows["old"]["embedding"]) == 1024
    assert fake.collections["odysseus_rag_fastembed"].count() == 1
    assert built[0].dimension == 1024


def test_lane_reset_reembeds_existing_documents_on_fingerprint_change(monkeypatch):
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
    patch_chroma(monkeypatch, fake)

    import src.embedding_lanes as lanes

    monkeypatch.setattr(lanes, "_build_custom_client", lambda: FakeEmbedder(768, "nomic", "http://embeddings/v1"))

    def fail_fastembed():
        raise RuntimeError("fastembed missing")

    monkeypatch.setattr(lanes, "_build_fastembed_client", fail_fastembed)

    built = build_embedding_lanes("odysseus_memories")

    assert [lane.name for lane in built] == [LANE_CUSTOM]
    assert "odysseus_memories_custom" in fake.deleted
    rebuilt = fake.collections["odysseus_memories_custom"]
    assert rebuilt.count() == 1
    assert rebuilt.get()["ids"] == ["existing-memory"]
    assert len(rebuilt.rows["existing-memory"]["embedding"]) == 768


def test_lane_reset_keeps_existing_collection_when_reembed_fails(monkeypatch):
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
    patch_chroma(monkeypatch, fake)

    import src.embedding_lanes as lanes

    monkeypatch.setattr(lanes, "_build_custom_client", lambda: FailingEmbedder(768, "nomic", "http://embeddings/v1"))
    monkeypatch.setattr(lanes, "_build_fastembed_client", lambda: FakeEmbedder(384, "mini", "local://fastembed"))

    built = build_embedding_lanes("odysseus_memories")

    assert [lane.name for lane in built] == [LANE_FASTEMBED]
    assert "odysseus_memories_custom" not in fake.deleted
    assert fake.collections["odysseus_memories_custom"].count() == 1
    assert len(fake.collections["odysseus_memories_custom"].rows["existing-memory"]["embedding"]) == 384


def test_lane_reset_keeps_existing_collection_when_preserve_read_fails(monkeypatch):
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

    def fail_get(*_args, **_kwargs):
        raise RuntimeError("chroma read failed")

    old_custom.get = fail_get
    patch_chroma(monkeypatch, fake)

    import src.embedding_lanes as lanes

    monkeypatch.setattr(lanes, "_build_custom_client", lambda: FakeEmbedder(768, "nomic", "http://embeddings/v1"))

    def fail_fastembed():
        raise RuntimeError("fastembed missing")

    monkeypatch.setattr(lanes, "_build_fastembed_client", fail_fastembed)

    built = build_embedding_lanes("odysseus_memories")

    assert built == []
    assert "odysseus_memories_custom" not in fake.deleted
    assert "odysseus_memories_custom" in fake.collections


def test_lane_reset_restores_existing_collection_when_rewrite_fails(monkeypatch):
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
    fake.fail_next_add_for["odysseus_memories_custom"] = 1
    patch_chroma(monkeypatch, fake)

    import src.embedding_lanes as lanes

    monkeypatch.setattr(lanes, "_build_custom_client", lambda: FakeEmbedder(768, "nomic", "http://embeddings/v1"))

    def fail_fastembed():
        raise RuntimeError("fastembed missing")

    monkeypatch.setattr(lanes, "_build_fastembed_client", fail_fastembed)

    built = build_embedding_lanes("odysseus_memories")

    assert built == []
    restored = fake.collections["odysseus_memories_custom"]
    assert restored.count() == 1
    assert restored.get()["ids"] == ["existing-memory"]
    assert len(restored.rows["existing-memory"]["embedding"]) == 384


def test_build_embedding_lanes_uses_fastembed_when_custom_unavailable(monkeypatch):
    fake = FakeChroma()
    patch_chroma(monkeypatch, fake)

    import src.embedding_lanes as lanes

    def fail_custom():
        raise RuntimeError("down")

    monkeypatch.setattr(lanes, "_build_custom_client", fail_custom)
    monkeypatch.setattr(lanes, "_build_fastembed_client", lambda: FakeEmbedder(384, "mini", "local://fastembed"))

    built = build_embedding_lanes("odysseus_tool_index")

    assert [lane.name for lane in built] == [LANE_FASTEMBED]
    assert built[0].collection_name == "odysseus_tool_index_fastembed"


def test_custom_lane_preserves_default_embedding_client_probe(monkeypatch):
    import src.embedding_lanes as lanes
    import src.embeddings as embeddings

    embeddings.reset_http_embed_state()
    monkeypatch.setattr(lanes, "_load_custom_endpoint", lambda: {})

    calls = []

    class DefaultClient(FakeEmbedder):
        def __init__(self, url=None, model=None, api_key=None):
            calls.append({"url": url, "model": model, "api_key": api_key})
            super().__init__(768, model or "all-minilm:l6-v2", url or "http://localhost:11434/v1/embeddings")

    monkeypatch.setattr(embeddings, "EmbeddingClient", DefaultClient)

    client = lanes._build_custom_client()

    assert calls == [{"url": None, "model": None, "api_key": None}]
    assert client.url == "http://localhost:11434/v1/embeddings"
    embeddings.reset_http_embed_state()


def test_custom_lane_uses_http_down_latch(monkeypatch):
    import src.embedding_lanes as lanes
    import src.embeddings as embeddings

    embeddings.reset_http_embed_state()
    calls = []

    class DownClient:
        def __init__(self, url=None, model=None, api_key=None):
            calls.append({"url": url, "model": model, "api_key": api_key})

        def get_sentence_embedding_dimension(self):
            raise RuntimeError("endpoint down")

    class LocalFastEmbed(FakeEmbedder):
        def __init__(self):
            super().__init__(384, "mini", "local://fastembed")

    monkeypatch.setattr(embeddings, "EmbeddingClient", DownClient)
    monkeypatch.setattr(embeddings, "FastEmbedClient", LocalFastEmbed)

    with pytest.raises(RuntimeError, match="HTTP embedding lane unavailable"):
        lanes._build_custom_client()
    with pytest.raises(RuntimeError, match="HTTP embedding lane unavailable"):
        lanes._build_custom_client()

    assert calls == [{"url": None, "model": None, "api_key": None}]
    embeddings.reset_http_embed_state()
