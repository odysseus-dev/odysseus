from src.rag_vector import VectorRAG, _generate_doc_id


class FakeEmbeddingModel:
    url = "fake://embeddings"
    model = "fake"

    def encode(self, texts, normalize_embeddings=True):
        return [[1.0, 0.0] for _ in texts]


class FakeCollection:
    def __init__(self):
        self.rows = {}

    def get(self, ids=None, include=None):
        found_ids = [doc_id for doc_id in (ids or []) if doc_id in self.rows]
        result = {"ids": found_ids}
        if include and "documents" in include:
            result["documents"] = [self.rows[doc_id]["document"] for doc_id in found_ids]
        if include and "metadatas" in include:
            result["metadatas"] = [self.rows[doc_id]["metadata"] for doc_id in found_ids]
        return result

    def add(self, ids, embeddings, documents, metadatas):
        for doc_id, embedding, document, metadata in zip(ids, embeddings, documents, metadatas):
            if doc_id in self.rows:
                raise ValueError(f"duplicate id: {doc_id}")
            self.rows[doc_id] = {
                "embedding": embedding,
                "document": document,
                "metadata": metadata,
            }


def _fake_rag():
    rag = object.__new__(VectorRAG)
    rag.persist_directory = "fake"
    rag._collection = FakeCollection()
    rag._model = FakeEmbeddingModel()
    rag._healthy = True
    return rag


def test_generate_doc_id_is_namespaced_by_owner_without_churning_unowned_ids():
    text = "shared boilerplate chunk"

    assert _generate_doc_id(text) == _generate_doc_id(text, "")
    assert _generate_doc_id(text, "alice") != _generate_doc_id(text, "bob")
    assert _generate_doc_id(text, "alice") != _generate_doc_id(text)


def test_add_document_keeps_identical_chunks_for_different_owners():
    rag = _fake_rag()
    text = "byte-identical chunk that both users uploaded"

    assert rag.add_document(text, {"owner": "alice", "source": "alice.txt"})
    assert rag.add_document(text, {"owner": "bob", "source": "bob.txt"})

    assert set(rag.collection.rows) == {
        _generate_doc_id(text, "alice"),
        _generate_doc_id(text, "bob"),
    }
    assert {row["metadata"]["owner"] for row in rag.collection.rows.values()} == {
        "alice",
        "bob",
    }


def test_add_documents_batch_keeps_identical_chunks_for_different_owners():
    rag = _fake_rag()
    text = "same shared license header"

    result = rag.add_documents_batch([
        (text, {"owner": "alice", "source": "alice.md"}),
        (text, {"owner": "bob", "source": "bob.md"}),
    ])

    assert result["success"] is True
    assert result["added_count"] == 2
    assert result["failed_count"] == 0
    assert set(rag.collection.rows) == {
        _generate_doc_id(text, "alice"),
        _generate_doc_id(text, "bob"),
    }
