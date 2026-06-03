"""Regression for issue #1662 — RAG doc ids were derived from the chunk text
alone, so when two owners indexed a byte-identical chunk the second owner's add
collided with the first's id, early-returned, and was never stored under their
owner. Their owner-filtered search then silently omitted it.

The id is now scoped by owner (empty owner reproduces the legacy text-only id so
the unowned/base index is unchanged). rag_vector needs ChromaDB + an embedding
backend to construct normally, so the behavioural test builds an instance via
__new__ with a fake collection and a stubbed _embed — no live services.
"""
from src import rag_vector
from src.rag_vector import _generate_doc_id


def test_doc_id_is_owner_scoped_and_legacy_compatible():
    text = "the same chunk of text"
    # Empty owner == legacy text-only id (base index keeps its existing ids).
    assert _generate_doc_id(text, "") == _generate_doc_id(text)
    assert _generate_doc_id(text).startswith("doc_")
    # Different owners -> different ids for identical text (the fix).
    assert _generate_doc_id(text, "alice") != _generate_doc_id(text, "bob")
    # Owned id differs from the legacy/base id, and is deterministic.
    assert _generate_doc_id(text, "alice") != _generate_doc_id(text)
    assert _generate_doc_id(text, "alice") == _generate_doc_id(text, "alice")


class _FakeCollection:
    def __init__(self):
        self.added_ids = []
        self._store = set()

    def get(self, ids=None, **kw):
        return {"ids": [i for i in (ids or []) if i in self._store]}

    def add(self, ids, embeddings, documents, metadatas):
        for i in ids:
            self._store.add(i)
            self.added_ids.append(i)


def _rag_with_fake():
    r = rag_vector.VectorRAG.__new__(rag_vector.VectorRAG)
    r._collection = _FakeCollection()
    r._healthy = True
    r._model = object()
    r._embed = lambda texts: [[0.0, 0.0] for _ in texts]
    return r


def test_identical_chunk_two_owners_both_stored():
    r = _rag_with_fake()
    text = "shared knowledge base chunk"
    assert r.add_document(text, {"owner": "alice", "source": "a.txt"})
    assert r.add_document(text, {"owner": "bob", "source": "b.txt"})
    # Both owners' copies stored under distinct ids (pre-fix: bob collided with
    # alice's id and was dropped, leaving a single stored id).
    assert len(set(r._collection.added_ids)) == 2, r._collection.added_ids


def test_same_owner_identical_chunk_still_deduped():
    r = _rag_with_fake()
    text = "shared knowledge base chunk"
    assert r.add_document(text, {"owner": "alice", "source": "a.txt"})
    assert r.add_document(text, {"owner": "alice", "source": "a2.txt"})
    # Same owner + identical text is still one id (dedup preserved).
    assert len(set(r._collection.added_ids)) == 1, r._collection.added_ids
