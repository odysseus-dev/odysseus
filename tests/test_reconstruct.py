"""Tests for reconstruct.py — active memory reconstruction (MRAgent).

Verifies the reconstruction-vs-static claim with a deterministic mock graph:
- seeds come from static recall
- the association walk reaches items static top-k cannot
- branches are PRUNED when accumulated evidence drops below the floor
- hop depth is bounded (no combinatorial explosion)
- compare() reports the measured gain

All tests isolate to temp stores; never touch real memory.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "memory_platform"))
import reconstruct  # noqa: E402


def test_seed_from_static(monkeypatch):
    """Reconstruction starts from the static-recall seeds (the cues)."""
    monkeypatch.setattr(reconstruct, "_seed",
                        lambda db, q, n=3: [{"id": 1, "text": "a seed fact about "
                                             "authority and the persona", "score": 0.9}])
    db = None
    res = reconstruct.reconstruct(db, "persona authority", hops=0)
    assert res["seeds"] == 1
    assert res["items"], "seed must be in the reconstruction"


def test_assoc_walk_reaches_new_items(monkeypatch):
    """The association walk reaches items static top-k cannot — this is the
    'reconstructed, not retrieved' gain."""
    monkeypatch.setattr(reconstruct, "_seed", lambda db, q, n=3:
                        [{"id": 1, "text": "persona authority seed", "score": 0.9}])
    monkeypatch.setattr(reconstruct, "_assocs", lambda db, ids, ms:
                        {2: [{"from": 1, "text": "related authority memory entry",
                              "strength": 0.8}],
                         3: [{"from": 1, "text": "another related entry",
                              "strength": 0.6}]})
    db = None
    res = reconstruct.reconstruct(db, "persona authority", hops=2)
    assert res["reconstructed"] == 3  # seed + 2 associations
    texts = [i["text"] for i in res["items"]]
    assert any("authority memory" in t for t in texts)


def test_branch_pruned_below_floor(monkeypatch):
    """A weak association that drops accumulated evidence below the floor is
    PRUNED — reconstruction does not chase every link (no explosion)."""
    monkeypatch.setattr(reconstruct, "_seed", lambda db, q, n=3:
                        [{"id": 1, "text": "seed", "score": 0.1}])
    # association strength 0.1 * decay 0.6 = 0.06 < PRUNE_FLOOR 0.25 -> pruned
    monkeypatch.setattr(reconstruct, "_assocs", lambda db, ids, ms:
                        {2: [{"from": 1, "text": "weak link", "strength": 0.1}]})
    db = None
    res = reconstruct.reconstruct(db, "q", hops=1)
    assert res["reconstructed"] == 1  # only the seed survives


def test_hops_bounded(monkeypatch):
    """Hop depth is capped — a fully-connected graph cannot explode."""
    monkeypatch.setattr(reconstruct, "_seed", lambda db, q, n=3:
                        [{"id": 1, "text": "seed", "score": 0.9}])
    monkeypatch.setattr(reconstruct, "_assocs", lambda db, ids, ms:
                        {i + 10: [{"from": ids[0], "text": f"hop item {i}",
                                   "strength": 0.9}] for i in range(10)})
    db = None
    res = reconstruct.reconstruct(db, "q", hops=1)
    assert res["reconstructed"] <= 11  # 1 seed + 10 one-hop items, no re-walk


def test_compare_reports_gain(monkeypatch):
    """compare() measures reconstruction vs static and reports the gain."""
    monkeypatch.setattr(reconstruct, "reconstruct", lambda db, q, hops=2, min_strength=0.3:
                        {"items": [{"text": "a", "evidence": 0.8, "path": [1, 9]},
                                   {"text": "b", "evidence": 0.7, "path": [2, 8]}]})
    monkeypatch.setattr(reconstruct, "_seed", lambda db, q, n=3: [])
    # Patch memory_store.recall to return a static set {1,2} via a fake module.
    class _FakeStore:
        @staticmethod
        def recall(db, query, budget=8, min_score=0.0):
            return ([{"id": 1, "text": "static a"}, {"id": 2, "text": "static b"}],
                    [0.9, 0.8])
    monkeypatch.setattr(reconstruct, "_store_module", _FakeStore)
    db = None
    res = reconstruct.compare(db, "q")
    assert res["static_returned"] == 2
    assert res["newly_reached"] == 2  # reconstruction paths end at 9 and 8
