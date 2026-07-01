from src.embedding_lanes import (
    EmbeddingLane,
    LANE_FASTEMBED,
)
from tests.helpers.embedding_lanes import (
    FakeChroma,
    FakeCollection,
    FakeEmbedder,
    FailingEmbedder,
    patch_chroma,
)


def test_vector_rag_writes_both_lanes_and_falls_back_to_fastembed(monkeypatch):
    fake = FakeChroma()
    patch_chroma(monkeypatch, fake)

    import src.embedding_lanes as lanes

    monkeypatch.setattr(lanes, "_build_custom_client", lambda: None)
    monkeypatch.setattr(lanes, "_build_fastembed_client", lambda: FakeEmbedder(384, "mini", "local://fastembed"))

    from src.rag_vector import VectorRAG

    rag = VectorRAG()
    assert rag.add_document("session search belongs in tools", {"source": "/tmp/a.md", "owner": "alice"})
    assert "odysseus_rag_custom" not in fake.collections
    assert fake.collections["odysseus_rag_fastembed"].count() == 1

    results = rag.search("session search", k=3, owner="alice")
    assert results[0]["document"] == "session search belongs in tools"
    assert results[0]["embedding_lane"] == LANE_FASTEMBED


def test_vector_rag_batch_index_continues_when_custom_lane_fails(monkeypatch, tmp_path):
    fake = FakeChroma()
    patch_chroma(monkeypatch, fake)

    import src.embedding_lanes as lanes

    monkeypatch.setattr(lanes, "_build_custom_client", lambda: FailingEmbedder(768, "nomic", "http://embeddings/v1"))
    monkeypatch.setattr(lanes, "_build_fastembed_client", lambda: FakeEmbedder(384, "mini", "local://fastembed"))

    from src.rag_vector import VectorRAG

    rag = VectorRAG(persist_directory=str(tmp_path))
    result = rag.add_documents_batch([
        ("batch fallback document", {"source": "/tmp/a.md", "owner": "alice"}),
    ])

    assert result["success"]
    assert result["added_count"] == 1
    assert fake.collections["odysseus_rag_custom"].count() == 0
    assert fake.collections["odysseus_rag_fastembed"].count() == 1


def test_vector_rag_batch_index_reports_failure_when_all_lanes_fail(monkeypatch, tmp_path):
    fake = FakeChroma()
    patch_chroma(monkeypatch, fake)

    import src.embedding_lanes as lanes

    monkeypatch.setattr(lanes, "_build_custom_client", lambda: FailingEmbedder(768, "nomic", "http://embeddings/v1"))
    monkeypatch.setattr(lanes, "_build_fastembed_client", lambda: FailingEmbedder(384, "mini", "local://fastembed"))

    from src.rag_vector import VectorRAG

    rag = VectorRAG(persist_directory=str(tmp_path))
    result = rag.add_documents_batch([
        ("batch outage document", {"source": "/tmp/a.md", "owner": "alice"}),
    ])

    assert not result["success"]
    assert fake.collections["odysseus_rag_custom"].count() == 0
    assert fake.collections["odysseus_rag_fastembed"].count() == 0


def test_rag_rebuild_does_not_reimport_legacy_collection(monkeypatch, tmp_path):
    fake = FakeChroma()
    legacy = fake.get_or_create_collection("odysseus_rag", metadata={"hnsw:space": "cosine"})
    legacy.add(
        ids=["stale-doc"],
        embeddings=[[0.0] * 384],
        documents=["stale legacy document"],
        metadatas=[{"source": "/tmp/stale.md"}],
    )
    inactive_custom = fake.get_or_create_collection("odysseus_rag_custom", metadata={"embedding_lane": "custom"})
    inactive_custom.add(
        ids=["stale-custom-doc"],
        embeddings=[[0.0] * 768],
        documents=["stale inactive custom document"],
        metadatas=[{"source": "/tmp/stale.md"}],
    )
    patch_chroma(monkeypatch, fake)

    import src.embedding_lanes as lanes

    monkeypatch.setattr(lanes, "_build_custom_client", lambda: None)
    monkeypatch.setattr(lanes, "_build_fastembed_client", lambda: FakeEmbedder(384, "mini", "local://fastembed"))

    from src.rag_vector import VectorRAG

    rag = VectorRAG(persist_directory=str(tmp_path))
    assert fake.collections["odysseus_rag_fastembed"].count() == 1

    assert rag.rebuild_index()

    assert "odysseus_rag" not in fake.collections
    assert "odysseus_rag_custom" not in fake.collections
    assert fake.collections["odysseus_rag_fastembed"].count() == 0
    assert rag.search("stale legacy", k=3) == []


def test_rag_remove_directory_deletes_inactive_lane_collection(monkeypatch, tmp_path):
    fake = FakeChroma()
    legacy_collection = fake.get_or_create_collection("odysseus_rag", metadata={"hnsw:space": "cosine"})
    custom_collection = fake.get_or_create_collection("odysseus_rag_custom", metadata={"embedding_lane": "custom"})
    fast_collection = fake.get_or_create_collection("odysseus_rag_fastembed", metadata={"embedding_lane": "fastembed"})
    source = str(tmp_path / "docs" / "note.md")
    directory = str(tmp_path / "docs")
    legacy_collection.add(
        ids=["legacy-doc"],
        embeddings=[[0.0] * 384],
        documents=["legacy stale doc"],
        metadatas=[{"source": source}],
    )
    custom_collection.add(
        ids=["custom-doc"],
        embeddings=[[0.0] * 768],
        documents=["custom stale doc"],
        metadatas=[{"source": source}],
    )
    fast_collection.add(
        ids=["fast-doc"],
        embeddings=[[0.0] * 384],
        documents=["fast current doc"],
        metadatas=[{"source": source}],
    )
    patch_chroma(monkeypatch, fake)

    fast_lane = EmbeddingLane(
        name=LANE_FASTEMBED,
        client=FakeEmbedder(384, "mini", "local://fastembed"),
        collection=fast_collection,
        collection_name="odysseus_rag_fastembed",
        model="mini",
        url="local://fastembed",
        dimension=384,
        fingerprint="fast",
    )

    from src.rag_vector import VectorRAG

    rag = VectorRAG.__new__(VectorRAG)
    rag._lanes = [fast_lane]
    rag._collection = fast_collection
    rag._healthy = True

    result = rag.remove_directory(directory)

    assert result["success"]
    assert result["removed_count"] == 3
    assert legacy_collection.count() == 0
    assert custom_collection.count() == 0
    assert fast_collection.count() == 0


def test_rag_delete_by_source_deletes_inactive_lane_collection(monkeypatch, tmp_path):
    fake = FakeChroma()
    legacy_collection = fake.get_or_create_collection("odysseus_rag", metadata={"hnsw:space": "cosine"})
    custom_collection = fake.get_or_create_collection("odysseus_rag_custom", metadata={"embedding_lane": "custom"})
    fast_collection = fake.get_or_create_collection("odysseus_rag_fastembed", metadata={"embedding_lane": "fastembed"})
    source = str(tmp_path / "docs" / "note.md")
    legacy_collection.add(
        ids=["legacy-doc"],
        embeddings=[[0.0] * 384],
        documents=["legacy stale doc"],
        metadatas=[{"source": source}],
    )
    custom_collection.add(
        ids=["shared-doc"],
        embeddings=[[0.0] * 768],
        documents=["custom stale doc"],
        metadatas=[{"source": source}],
    )
    fast_collection.add(
        ids=["shared-doc"],
        embeddings=[[0.0] * 384],
        documents=["fast current doc"],
        metadatas=[{"source": source}],
    )
    patch_chroma(monkeypatch, fake)

    fast_lane = EmbeddingLane(
        name=LANE_FASTEMBED,
        client=FakeEmbedder(384, "mini", "local://fastembed"),
        collection=fast_collection,
        collection_name="odysseus_rag_fastembed",
        model="mini",
        url="local://fastembed",
        dimension=384,
        fingerprint="fast",
    )

    from src.rag_vector import VectorRAG

    rag = VectorRAG.__new__(VectorRAG)
    rag._lanes = [fast_lane]
    rag._collection = fast_collection
    rag._healthy = True

    assert rag.delete_by_source(source) == 2
    assert legacy_collection.count() == 0
    assert custom_collection.count() == 0
    assert fast_collection.count() == 0


def test_vector_rag_uses_keyword_fallback_when_all_lanes_query_fail():
    collection = FakeCollection("odysseus_rag_fastembed", metadata={"embedding_lane": "fastembed"})
    collection.add(
        ids=["doc-1"],
        embeddings=[[0.0] * 384],
        documents=["fallback keyword document"],
        metadatas=[{"source": "/tmp/doc.md"}],
    )

    def fail_query(*_args, **_kwargs):
        raise RuntimeError("embedding query down")

    collection.query = fail_query
    lane = EmbeddingLane(
        name=LANE_FASTEMBED,
        client=FakeEmbedder(384, "mini", "local://fastembed"),
        collection=collection,
        collection_name="odysseus_rag_fastembed",
        model="mini",
        url="local://fastembed",
        dimension=384,
        fingerprint="fp",
    )

    from src.rag_vector import VectorRAG

    rag = VectorRAG.__new__(VectorRAG)
    rag._lanes = [lane]
    rag._collection = collection
    rag._healthy = True

    results = rag.search("fallback keyword", k=3)

    assert results[0]["id"] == "doc-1"
    assert results[0]["search_type"] == "keyword_fallback"
