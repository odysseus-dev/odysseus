# tests/test_memory_vector_collection_name.py
from src.memory_vector import MemoryVectorStore


def test_memory_vector_store_accepts_collection_name(monkeypatch):
    """Project-scoped stores pass a custom collection name; existing callers
    keep using the default `odysseus_memories` for backwards compatibility."""
    captured = {}

    def fake_build(base_name):
        captured["base_name"] = base_name
        return []

    monkeypatch.setattr("src.memory_vector.build_embedding_lanes", fake_build)

    # Default behavior: still uses the historical name.
    MemoryVectorStore(data_dir="/tmp/x")
    assert captured["base_name"] == "odysseus_memories"

    # Custom: project passes its own name.
    MemoryVectorStore(data_dir="/tmp/y", collection_name="project_resources_prj_1")
    assert captured["base_name"] == "project_resources_prj_1"
