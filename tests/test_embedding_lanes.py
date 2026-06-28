import pytest

from src.embedding_lanes import (
    EmbeddingLane,
    LANE_CUSTOM,
    LANE_FASTEMBED,
    build_embedding_lanes,
)


class FakeEmbedder:
    def __init__(self, dim, model, url):
        self.dim = dim
        self.model = model
        self.url = url

    def get_sentence_embedding_dimension(self):
        return self.dim

    def encode(self, texts, normalize_embeddings=True):
        return [[float(i + 1)] * self.dim for i, _ in enumerate(texts)]


class FailingEmbedder(FakeEmbedder):
    def encode(self, texts, normalize_embeddings=True):
        raise RuntimeError("embedding endpoint rate limited")


class FakeCollection:
    def __init__(self, name, metadata=None):
        self.name = name
        self.metadata = metadata or {}
        self.rows = {}
        self.dim = None

    def count(self):
        return len(self.rows)

    def add(self, ids, embeddings, documents=None, metadatas=None):
        self._check_dim(embeddings)
        documents = documents or [None] * len(ids)
        metadatas = metadatas or [{}] * len(ids)
        for row_id, emb, doc, meta in zip(ids, embeddings, documents, metadatas):
            self.rows[row_id] = {"embedding": emb, "document": doc, "metadata": meta}

    def upsert(self, ids, embeddings, documents=None, metadatas=None):
        self.add(ids, embeddings, documents=documents, metadatas=metadatas)

    def get(self, ids=None, include=None, where=None, limit=None):
        selected = list(self.rows.items())
        if ids is not None:
            id_set = set(ids)
            selected = [(row_id, row) for row_id, row in selected if row_id in id_set]
        if where:
            selected = [
                (row_id, row)
                for row_id, row in selected
                if all(row["metadata"].get(k) == v for k, v in where.items())
            ]
        if limit is not None:
            selected = selected[:limit]
        return {
            "ids": [row_id for row_id, _ in selected],
            "documents": [row["document"] for _, row in selected],
            "metadatas": [row["metadata"] for _, row in selected],
            "embeddings": [row["embedding"] for _, row in selected],
        }

    def query(self, query_embeddings, n_results, where=None, include=None):
        self._check_dim(query_embeddings)
        rows = self.get(where=where)
        ids = rows["ids"][:n_results]
        docs = rows["documents"][:n_results]
        metas = rows["metadatas"][:n_results]
        return {
            "ids": [ids],
            "documents": [docs],
            "metadatas": [metas],
            "distances": [[0.1 + i * 0.01 for i in range(len(ids))]],
        }

    def delete(self, ids):
        for row_id in ids:
            self.rows.pop(row_id, None)

    def _check_dim(self, embeddings):
        if not embeddings:
            return
        dim = len(embeddings[0])
        if self.dim is None:
            self.dim = dim
        elif self.dim != dim:
            raise RuntimeError(f"Collection expecting embedding with dimension of {self.dim}, got {dim}")


class FakeChroma:
    def __init__(self):
        self.collections = {}
        self.deleted = []
        self.fail_next_add_for = {}

    def get_or_create_collection(self, name, metadata=None):
        if name not in self.collections:
            self.collections[name] = FakeCollection(name, metadata=metadata)
            if self.fail_next_add_for.get(name, 0) > 0:
                original_add = self.collections[name].add

                def fail_once(*args, **kwargs):
                    self.fail_next_add_for[name] -= 1
                    self.collections[name].add = original_add
                    raise RuntimeError("chroma write failed")

                self.collections[name].add = fail_once
        elif metadata is not None:
            self.collections[name].metadata = metadata
        return self.collections[name]

    def get_collection(self, name):
        if name not in self.collections:
            raise KeyError(name)
        return self.collections[name]

    def delete_collection(self, name):
        self.deleted.append(name)
        self.collections.pop(name, None)


def _patch_chroma(monkeypatch, fake):
    import src.chroma_client as chroma_client

    monkeypatch.setattr(chroma_client, "get_chroma_client", lambda: fake)


def test_build_embedding_lanes_keeps_custom_and_fastembed_dimensions_separate(monkeypatch):
    fake = FakeChroma()
    _patch_chroma(monkeypatch, fake)

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
    _patch_chroma(monkeypatch, fake)

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
    _patch_chroma(monkeypatch, fake)

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
    _patch_chroma(monkeypatch, fake)

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
    _patch_chroma(monkeypatch, fake)

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
    _patch_chroma(monkeypatch, fake)

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
    _patch_chroma(monkeypatch, fake)

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


def test_memory_vector_store_writes_both_lanes_and_prefers_custom(monkeypatch):
    fake = FakeChroma()
    _patch_chroma(monkeypatch, fake)

    import src.embedding_lanes as lanes

    monkeypatch.setattr(lanes, "_build_custom_client", lambda: FakeEmbedder(768, "nomic", "http://embeddings/v1"))
    monkeypatch.setattr(lanes, "_build_fastembed_client", lambda: FakeEmbedder(384, "mini", "local://fastembed"))

    from src.memory_vector import MemoryVectorStore

    store = MemoryVectorStore("data")
    store.add("mem-1", "Nicholai likes direct memory systems")

    assert fake.collections["odysseus_memories_custom"].count() == 1
    assert fake.collections["odysseus_memories_fastembed"].count() == 1

    results = store.search("direct memory", k=5)
    assert results[0]["memory_id"] == "mem-1"
    assert results[0]["embedding_lane"] == LANE_CUSTOM


def test_memory_search_merges_fallback_only_results_before_limit():
    custom_collection = FakeCollection("odysseus_memories_custom", metadata={"embedding_lane": "custom"})
    fast_collection = FakeCollection("odysseus_memories_fastembed", metadata={"embedding_lane": "fastembed"})
    custom_collection.add(
        ids=["old-1", "old-2"],
        embeddings=[[0.0] * 768, [0.0] * 768],
        documents=["older custom memory", "another custom memory"],
        metadatas=[{"source": "memory"}, {"source": "memory"}],
    )
    fast_collection.add(
        ids=["fallback-only"],
        embeddings=[[0.0] * 384],
        documents=["fallback only relevant memory"],
        metadatas=[{"source": "memory"}],
    )

    custom_collection.query = lambda **_kwargs: {
        "ids": [["old-1", "old-2"]],
        "distances": [[0.20, 0.21]],
    }
    fast_collection.query = lambda **_kwargs: {
        "ids": [["fallback-only"]],
        "distances": [[0.05]],
    }

    custom_lane = EmbeddingLane(
        name=LANE_CUSTOM,
        client=FakeEmbedder(768, "nomic", "http://embeddings/v1"),
        collection=custom_collection,
        collection_name="odysseus_memories_custom",
        model="nomic",
        url="http://embeddings/v1",
        dimension=768,
        fingerprint="custom",
    )
    fast_lane = EmbeddingLane(
        name=LANE_FASTEMBED,
        client=FakeEmbedder(384, "mini", "local://fastembed"),
        collection=fast_collection,
        collection_name="odysseus_memories_fastembed",
        model="mini",
        url="local://fastembed",
        dimension=384,
        fingerprint="fast",
    )

    from src.memory_vector import MemoryVectorStore

    store = MemoryVectorStore.__new__(MemoryVectorStore)
    store._lanes = [custom_lane, fast_lane]
    store._healthy = True

    results = store.search("fallback relevant", k=2)

    assert [row["memory_id"] for row in results] == ["fallback-only", "old-1"]


def test_vector_rag_writes_both_lanes_and_falls_back_to_fastembed(monkeypatch):
    fake = FakeChroma()
    _patch_chroma(monkeypatch, fake)

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
    _patch_chroma(monkeypatch, fake)

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
    _patch_chroma(monkeypatch, fake)

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


def test_tool_index_indexes_and_retrieves_from_available_lanes(monkeypatch):
    fake = FakeChroma()
    _patch_chroma(monkeypatch, fake)

    import src.embedding_lanes as lanes

    monkeypatch.setattr(lanes, "_build_custom_client", lambda: FakeEmbedder(768, "nomic", "http://embeddings/v1"))
    monkeypatch.setattr(lanes, "_build_fastembed_client", lambda: FakeEmbedder(384, "mini", "local://fastembed"))

    from src.tool_index import ToolIndex

    index = ToolIndex()
    index.index_builtin_tools()

    assert fake.collections["odysseus_tool_index_custom"].count() > 0
    assert fake.collections["odysseus_tool_index_fastembed"].count() > 0
    assert "bash" in index.retrieve("run a shell command", k=10)


def test_tool_index_builtin_indexing_fails_when_all_lanes_fail():
    custom_lane = EmbeddingLane(
        name=LANE_CUSTOM,
        client=FailingEmbedder(768, "nomic", "http://embeddings/v1"),
        collection=FakeCollection("odysseus_tool_index_custom", metadata={"embedding_lane": "custom"}),
        collection_name="odysseus_tool_index_custom",
        model="nomic",
        url="http://embeddings/v1",
        dimension=768,
        fingerprint="custom",
    )
    fast_lane = EmbeddingLane(
        name=LANE_FASTEMBED,
        client=FailingEmbedder(384, "mini", "local://fastembed"),
        collection=FakeCollection("odysseus_tool_index_fastembed", metadata={"embedding_lane": "fastembed"}),
        collection_name="odysseus_tool_index_fastembed",
        model="mini",
        url="local://fastembed",
        dimension=384,
        fingerprint="fast",
    )

    from src.tool_index import ToolIndex

    index = ToolIndex.__new__(ToolIndex)
    index._lanes = [custom_lane, fast_lane]
    index._healthy = True

    with pytest.raises(RuntimeError, match="all embedding lanes"):
        index.index_builtin_tools()
    assert not index.healthy


def test_tool_index_retrieval_continues_when_custom_lane_query_fails():
    custom_collection = FakeCollection("odysseus_tool_index_custom", metadata={"embedding_lane": "custom"})
    fast_collection = FakeCollection("odysseus_tool_index_fastembed", metadata={"embedding_lane": "fastembed"})
    fast_collection.add(
        ids=["builtin_bash"],
        embeddings=[[0.0] * 384],
        documents=["Tool: bash\nRun shell commands"],
        metadatas=[{"tool_name": "bash", "tool_type": "builtin"}],
    )

    def fail_query(*_args, **_kwargs):
        raise RuntimeError("custom endpoint down")

    custom_collection.add(
        ids=["builtin_python"],
        embeddings=[[0.0] * 768],
        documents=["Tool: python\nRun Python"],
        metadatas=[{"tool_name": "python", "tool_type": "builtin"}],
    )
    custom_collection.query = fail_query

    custom_lane = EmbeddingLane(
        name=LANE_CUSTOM,
        client=FakeEmbedder(768, "nomic", "http://embeddings/v1"),
        collection=custom_collection,
        collection_name="odysseus_tool_index_custom",
        model="nomic",
        url="http://embeddings/v1",
        dimension=768,
        fingerprint="custom",
    )
    fast_lane = EmbeddingLane(
        name=LANE_FASTEMBED,
        client=FakeEmbedder(384, "mini", "local://fastembed"),
        collection=fast_collection,
        collection_name="odysseus_tool_index_fastembed",
        model="mini",
        url="local://fastembed",
        dimension=384,
        fingerprint="fast",
    )

    from src.tool_index import ToolIndex

    index = ToolIndex.__new__(ToolIndex)
    index._lanes = [custom_lane, fast_lane]

    assert index.retrieve("run shell", k=5) == ["bash"]


def test_tool_index_merges_fallback_tool_results_before_limit():
    custom_collection = FakeCollection("odysseus_tool_index_custom", metadata={"embedding_lane": "custom"})
    fast_collection = FakeCollection("odysseus_tool_index_fastembed", metadata={"embedding_lane": "fastembed"})
    custom_collection.add(
        ids=["builtin_one", "builtin_two"],
        embeddings=[[0.0] * 768, [0.0] * 768],
        documents=["Tool: one", "Tool: two"],
        metadatas=[
            {"tool_name": "one", "tool_type": "builtin"},
            {"tool_name": "two", "tool_type": "builtin"},
        ],
    )
    fast_collection.add(
        ids=["mcp_current"],
        embeddings=[[0.0] * 384],
        documents=["Tool: current MCP"],
        metadatas=[{"tool_name": "current_mcp", "tool_type": "mcp"}],
    )

    custom_collection.query = lambda **_kwargs: {
        "ids": [["builtin_one", "builtin_two"]],
        "metadatas": [[
            {"tool_name": "one", "tool_type": "builtin"},
            {"tool_name": "two", "tool_type": "builtin"},
        ]],
        "distances": [[0.20, 0.21]],
    }
    fast_collection.query = lambda **_kwargs: {
        "ids": [["mcp_current"]],
        "metadatas": [[{"tool_name": "current_mcp", "tool_type": "mcp"}]],
        "distances": [[0.05]],
    }

    custom_lane = EmbeddingLane(
        name=LANE_CUSTOM,
        client=FakeEmbedder(768, "nomic", "http://embeddings/v1"),
        collection=custom_collection,
        collection_name="odysseus_tool_index_custom",
        model="nomic",
        url="http://embeddings/v1",
        dimension=768,
        fingerprint="custom",
    )
    fast_lane = EmbeddingLane(
        name=LANE_FASTEMBED,
        client=FakeEmbedder(384, "mini", "local://fastembed"),
        collection=fast_collection,
        collection_name="odysseus_tool_index_fastembed",
        model="mini",
        url="local://fastembed",
        dimension=384,
        fingerprint="fast",
    )

    from src.tool_index import ToolIndex

    index = ToolIndex.__new__(ToolIndex)
    index._lanes = [custom_lane, fast_lane]

    assert index.retrieve("current mcp", k=2) == ["current_mcp", "one"]


def test_legacy_collection_backfills_fastembed_lane(monkeypatch):
    fake = FakeChroma()
    legacy = fake.get_or_create_collection("odysseus_memories", metadata={"hnsw:space": "cosine"})
    legacy.add(
        ids=["legacy-memory"],
        embeddings=[[0.0] * 384],
        documents=["legacy memory row"],
        metadatas=[{"source": "memory"}],
    )
    _patch_chroma(monkeypatch, fake)

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
    _patch_chroma(monkeypatch, fake)

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
    _patch_chroma(monkeypatch, fake)

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
    _patch_chroma(monkeypatch, fake)

    import src.embedding_lanes as lanes

    monkeypatch.setattr(lanes, "_build_custom_client", lambda: None)
    monkeypatch.setattr(lanes, "_build_fastembed_client", lambda: FakeEmbedder(384, "mini", "local://fastembed"))

    from src.memory_vector import MemoryVectorStore

    store = MemoryVectorStore("data")

    assert store.count() == 2
    assert set(fake.collections["odysseus_memories_fastembed"].get()["ids"]) == {"legacy-1", "legacy-2"}


def test_memory_rebuild_does_not_reimport_legacy_collection(monkeypatch):
    fake = FakeChroma()
    legacy = fake.get_or_create_collection("odysseus_memories", metadata={"hnsw:space": "cosine"})
    legacy.add(
        ids=["stale-memory"],
        embeddings=[[0.0] * 384],
        documents=["stale legacy memory"],
        metadatas=[{"source": "memory"}],
    )
    inactive_custom = fake.get_or_create_collection("odysseus_memories_custom", metadata={"embedding_lane": "custom"})
    inactive_custom.add(
        ids=["stale-custom"],
        embeddings=[[0.0] * 768],
        documents=["stale inactive custom memory"],
        metadatas=[{"source": "memory"}],
    )
    _patch_chroma(monkeypatch, fake)

    import src.embedding_lanes as lanes

    monkeypatch.setattr(lanes, "_build_custom_client", lambda: None)
    monkeypatch.setattr(lanes, "_build_fastembed_client", lambda: FakeEmbedder(384, "mini", "local://fastembed"))

    from src.memory_vector import MemoryVectorStore

    store = MemoryVectorStore("data")
    assert fake.collections["odysseus_memories_fastembed"].count() == 1

    store.rebuild([{"id": "current-memory", "text": "current rebuilt memory"}])

    assert "odysseus_memories" not in fake.collections
    assert "odysseus_memories_custom" not in fake.collections
    assert fake.collections["odysseus_memories_fastembed"].count() == 1
    assert fake.collections["odysseus_memories_fastembed"].get()["ids"] == ["current-memory"]


def test_memory_remove_deletes_inactive_lane_collection(monkeypatch):
    fake = FakeChroma()
    custom_collection = fake.get_or_create_collection("odysseus_memories_custom", metadata={"embedding_lane": "custom"})
    fast_collection = fake.get_or_create_collection("odysseus_memories_fastembed", metadata={"embedding_lane": "fastembed"})
    custom_collection.add(
        ids=["mem-1"],
        embeddings=[[0.0] * 768],
        documents=["custom stale memory"],
        metadatas=[{"source": "memory"}],
    )
    fast_collection.add(
        ids=["mem-1"],
        embeddings=[[0.0] * 384],
        documents=["fast memory"],
        metadatas=[{"source": "memory"}],
    )
    _patch_chroma(monkeypatch, fake)

    fast_lane = EmbeddingLane(
        name=LANE_FASTEMBED,
        client=FakeEmbedder(384, "mini", "local://fastembed"),
        collection=fast_collection,
        collection_name="odysseus_memories_fastembed",
        model="mini",
        url="local://fastembed",
        dimension=384,
        fingerprint="fast",
    )

    from src.memory_vector import MemoryVectorStore

    store = MemoryVectorStore.__new__(MemoryVectorStore)
    store._lanes = [fast_lane]
    store._healthy = True

    store.remove("mem-1")

    assert custom_collection.count() == 0
    assert fast_collection.count() == 0


def test_memory_rebuild_continues_when_custom_lane_fails(monkeypatch):
    fake = FakeChroma()
    _patch_chroma(monkeypatch, fake)

    import src.embedding_lanes as lanes

    monkeypatch.setattr(lanes, "_build_custom_client", lambda: FailingEmbedder(768, "nomic", "http://embeddings/v1"))
    monkeypatch.setattr(lanes, "_build_fastembed_client", lambda: FakeEmbedder(384, "mini", "local://fastembed"))

    from src.memory_vector import MemoryVectorStore

    store = MemoryVectorStore("data")
    store.rebuild([{"id": "current-memory", "text": "current rebuilt memory"}])

    assert fake.collections["odysseus_memories_custom"].count() == 0
    assert fake.collections["odysseus_memories_fastembed"].count() == 1
    assert fake.collections["odysseus_memories_fastembed"].get()["ids"] == ["current-memory"]


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
    _patch_chroma(monkeypatch, fake)

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
    _patch_chroma(monkeypatch, fake)

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
    _patch_chroma(monkeypatch, fake)

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
