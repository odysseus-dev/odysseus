# tests/test_project_rag.py
from unittest.mock import MagicMock

from services.project.rag import ProjectRagAdapter


def test_query_uses_per_project_collection():
    fake_collection = MagicMock()
    fake_collection.query.return_value = {
        "ids": [["c1", "c2"]],
        "documents": [["alpha", "beta"]],
        "metadatas": [[{"resource_id": "rsr_a", "chunk_index": 0},
                       {"resource_id": "rsr_a", "chunk_index": 1}]],
    }
    client = MagicMock()
    client.get_or_create_collection.return_value = fake_collection

    adapter = ProjectRagAdapter(project_id="prj_z", chroma_client=client)

    chunks = adapter.query("what?", top_k=2)

    # Verify the collection name includes the project id.
    args, kwargs = client.get_or_create_collection.call_args
    assert args[0] == "project_resources_prj_z"

    assert len(chunks) == 2
    assert chunks[0].source == "rsr_a"
    assert chunks[0].chunk_index == 0
    assert chunks[0].text == "alpha"


def test_delete_chunks_uses_resource_id_filter():
    fake_collection = MagicMock()
    client = MagicMock()
    client.get_or_create_collection.return_value = fake_collection
    adapter = ProjectRagAdapter(project_id="prj_q", chroma_client=client)
    adapter.delete_chunks("rsr_42")
    fake_collection.delete.assert_called_once_with(where={"resource_id": "rsr_42"})