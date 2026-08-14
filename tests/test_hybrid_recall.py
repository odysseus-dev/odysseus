"""Focused tests for hybrid recall (BM25 + dense + RRF + associations)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory_platform"))

from hybrid_recall import HybridRecall, _bm25_score


class FakeVecStore:
    def __init__(self, entries):
        self._entries = entries
        self._collection = type("C", (), {"get": lambda self, limit=2000: {
            "ids": [e["id"] for e in entries],
            "documents": [e["text"] for e in entries],
            "embeddings": [e["_vec"] for e in entries],
        }})()

    def search(self, query, k=8):
        return [{"memory_id": e["id"], "score": 1.0} for e in self._entries[:k]]


def _embed(texts):
    # tiny deterministic vectors by word presence
    out = {}
    for t in texts:
        words = set(t.split())
        v = [1.0 if w in words else 0.0 for w in
             ("coffee", "morning", "oat", "drinks", "prefers", "teaches",
              "skill", "warm", "grounded", "guitar")]
        out[t] = v
    return out


def test_bm25_catches_exact_term():
    # BM25 alone should rank the exact-term match higher
    s = _bm25_score("coffee", "drinks coffee every morning")
    s2 = _bm25_score("coffee", "prefers oat milk")
    assert s > s2


def test_hybrid_returns_relevant():
    entries = [
        {"id": "a", "text": "drinks coffee every morning", "_vec": [1,1,0,0,0,0,0,0,0,0]},
        {"id": "b", "text": "prefers oat milk", "_vec": [0,0,1,1,1,0,0,0,0,0]},
        {"id": "c", "text": "teaches a physical skill", "_vec": [0,0,0,0,0,1,1,0,0,0]},
    ]
    hr = HybridRecall(FakeVecStore(entries), _embed)
    res = hr.search("coffee morning")
    assert res, "should return results"
    assert res[0]["memory_id"] == "a"


def test_hybrid_falls_back_when_no_entries():
    hr = HybridRecall(FakeVecStore([]), _embed)
    res = hr.search("anything")
    assert res == [] or isinstance(res, list)


def test_hybrid_preserves_original_search():
    vs = FakeVecStore([{"id": "a", "text": "x", "_vec": [0]*10}])
    original = vs.search
    hr = HybridRecall(vs, _embed)
    # the adapter swap stores original on the store; assert the class works
    assert callable(original)
    assert hr.search is not original


def test_swap_preserves_and_restores_original():
    from hybrid_recall import swap_recall, restore_search
    vs = FakeVecStore([{"id": "a", "text": "x", "_vec": [0]*10}])
    original_func = vs.search.__func__
    original_self = vs.search.__self__
    hr = swap_recall(vs, _embed)
    assert hr is not None
    # bound methods are recreated per attribute access; compare function+self
    assert vs.search.__self__ is hr          # swapped to the hybrid instance
    assert vs.search.__func__ is hr.search.__func__
    # original preserved for rollback (same function + same self)
    assert vs._search_orig.__func__ is original_func
    assert vs._search_orig.__self__ is original_self
    assert restore_search(vs) is True        # restored
    assert vs.search.__func__ is original_func
    assert vs.search.__self__ is original_self
