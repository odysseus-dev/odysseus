"""Regression: VectorRAG._keyword_search_fallback must not leak owner-less docs
across users.

The primary hybrid search filters with ChromaDB ``where={"owner": owner}``,
which returns only documents whose ``owner == owner`` (documents with no owner
are excluded). The keyword fallback used
``if doc_owner and doc_owner != owner: continue``, so a document with a
missing/empty owner fell through the guard and was returned to whichever user
issued the query — a cross-user leak whenever the primary path errored and fell
back to keyword search.
"""
from src.rag_vector import VectorRAG


class _FakeCollection:
    def __init__(self, docs):
        # docs: list of (id, text, metadata)
        self._docs = docs

    def count(self):
        return len(self._docs)

    def get(self, include=None):
        return {
            "ids": [d[0] for d in self._docs],
            "documents": [d[1] for d in self._docs],
            "metadatas": [d[2] for d in self._docs],
        }


def _store(docs):
    store = VectorRAG.__new__(VectorRAG)
    store._collection = _FakeCollection(docs)
    return store


def test_ownerless_doc_not_leaked_to_user():
    store = _store([
        ("a", "alice secret project", {"owner": "alice"}),
        ("b", "bob secret project", {"owner": "bob"}),
        ("c", "ownerless secret project", {}),          # no owner key
    ])
    results = store._keyword_search_fallback("secret project", k=10, owner="alice")
    ids = {r["id"] for r in results}
    assert ids == {"a"}          # only alice's doc
    assert "b" not in ids        # another user's doc excluded (already was)
    assert "c" not in ids        # owner-less doc must NOT leak (the fix)


def test_no_owner_filter_returns_all():
    store = _store([
        ("a", "shared note", {"owner": "alice"}),
        ("c", "shared note", {}),
    ])
    results = store._keyword_search_fallback("shared note", k=10, owner=None)
    ids = {r["id"] for r in results}
    assert ids == {"a", "c"}     # no owner requested → no filtering
