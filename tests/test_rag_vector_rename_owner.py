from src.rag_vector import VectorRAG


class _FakeCollection:
    def __init__(self, docs):
        self._docs = {
            doc_id: {"document": document, "metadata": dict(metadata)}
            for doc_id, document, metadata in docs
        }

    def count(self):
        return len(self._docs)

    def get(self, where=None, include=None):
        rows = []
        for doc_id, row in self._docs.items():
            metadata = row["metadata"]
            if where and any(metadata.get(key) != value for key, value in where.items()):
                continue
            rows.append((doc_id, row))
        return {
            "ids": [doc_id for doc_id, _row in rows],
            "documents": [row["document"] for _doc_id, row in rows],
            "metadatas": [row["metadata"] for _doc_id, row in rows],
        }

    def update(self, ids, metadatas):
        for doc_id, metadata in zip(ids, metadatas):
            self._docs[doc_id]["metadata"] = dict(metadata)


def _store(collection):
    store = VectorRAG.__new__(VectorRAG)
    store._collection = collection
    store._lanes = []
    store._healthy = True
    return store


def test_rename_owner_updates_metadata_used_by_owner_filtered_search(tmp_path):
    old_dir = tmp_path / "alice"
    new_dir = tmp_path / "alice2"
    old_file = old_dir / "note.txt"
    new_file = new_dir / "note.txt"
    collection = _FakeCollection([
        (
            "doc-old",
            "private vector note",
            {
                "owner": "alice",
                "source": str(old_file),
                "directory": str(old_dir),
            },
        ),
        (
            "doc-other",
            "other vector note",
            {
                "owner": "bob",
                "source": str(tmp_path / "bob" / "note.txt"),
            },
        ),
    ])
    store = _store(collection)

    result = store.rename_owner(
        "alice",
        "alice2",
        path_map={str(old_file): str(new_file)},
        path_prefixes=[(str(old_dir), str(new_dir))],
    )

    assert result["success"] is True
    assert result["updated_count"] == 1
    assert store._keyword_search_fallback("private", k=10, owner="alice") == []
    renamed = store._keyword_search_fallback("private", k=10, owner="alice2")
    assert [row["id"] for row in renamed] == ["doc-old"]
    assert renamed[0]["metadata"]["owner"] == "alice2"
    assert renamed[0]["metadata"]["source"] == str(new_file)
    assert renamed[0]["metadata"]["directory"] == str(new_dir)
    assert store._keyword_search_fallback("other", k=10, owner="bob")[0]["id"] == "doc-other"
