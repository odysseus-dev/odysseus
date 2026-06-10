r"""Regression: VectorRAG._keyword_search_fallback must score by whole-word
match, not substring containment.

The primary hybrid search path scores keyword overlap with whole-word sets
(``set(query.lower().split()) & set(doc.lower().split())``). The degraded
keyword fallback used ``sum(1 for w in query_words if w in doc_lower)`` —
substring containment — so a short query word like "ai" matched *inside*
unrelated words ("maintain", "available"), surfacing irrelevant documents
whenever the vector path errored and fell back to keyword search.

The fix matches on word boundaries (``\bword\b``), which kills the false
positive while still matching a word adjacent to punctuation ("safety" in
"safety.") that a naive ``str.split()`` set would miss.
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


def test_short_word_not_matched_as_substring():
    """"ai" must match the whole word "ai", not the "ai" inside "maintain"."""
    store = _store([
        ("hit", "ai model training", {}),
        ("miss", "how to maintain the available server", {}),
    ])
    results = store._keyword_search_fallback("ai", k=10, owner=None)
    ids = {r["id"] for r in results}
    assert ids == {"hit"}        # substring match inside "maintain" must NOT appear


def test_whole_word_match_still_works():
    store = _store([
        ("hit", "the quick brown fox jumps", {}),
        ("miss", "a lazy dog sleeps", {}),
    ])
    results = store._keyword_search_fallback("brown fox", k=10, owner=None)
    ids = {r["id"] for r in results}
    assert ids == {"hit"}


def test_word_adjacent_to_punctuation_still_matches():
    r"""Guard the design choice: \b matches "safety" in "safety." — a naive
    set(doc.split()) would tokenise "safety." and miss it (recall regression)."""
    store = _store([
        ("hit", "workplace safety.", {}),
        ("miss", "fire drill schedule", {}),
    ])
    results = store._keyword_search_fallback("safety", k=10, owner=None)
    ids = {r["id"] for r in results}
    assert ids == {"hit"}
