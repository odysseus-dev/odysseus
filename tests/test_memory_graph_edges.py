"""Pure edge-derivation logic for the Memory Graph View.

No FastAPI, no Chroma — src.memory_graph.build_graph and friends operate on
plain memory-entry dicts and a duck-typed vector-store stand-in, so these are
plain table-driven unit tests.
"""
from unittest.mock import MagicMock

from src.memory_graph import (
    build_graph,
    build_manual_edges,
    build_session_edges,
    build_similarity_edges,
)


def _mem(id_, text="text", category="fact", session_id=None, links=None, uses=0, timestamp=0, pinned=False):
    entry = {
        "id": id_,
        "text": text,
        "category": category,
        "uses": uses,
        "timestamp": timestamp,
        "pinned": pinned,
    }
    if session_id is not None:
        entry["session_id"] = session_id
    if links is not None:
        entry["links"] = links
    return entry


def test_similarity_edges_respects_threshold_and_top_k():
    memories = [_mem("a", text="text-a"), _mem("b", text="text-b"), _mem("c", text="text-c")]
    # Each node's own nearest-neighbor query returns a distinct ranking, as a
    # real per-text ANN search would (a canned identical list for every node
    # would make "c" spuriously match "a"/"b" from their own query results).
    neighbor_scores = {
        "a": [{"memory_id": "b", "score": 0.9}, {"memory_id": "c", "score": 0.5}],
        "b": [{"memory_id": "a", "score": 0.9}, {"memory_id": "c", "score": 0.4}],
        "c": [{"memory_id": "a", "score": 0.5}, {"memory_id": "b", "score": 0.4}],
    }
    vec = MagicMock(healthy=True)
    vec.search.side_effect = lambda text, k, _scores=neighbor_scores: _scores[
        next(m["id"] for m in memories if m["text"] == text)
    ]
    edges = build_similarity_edges(memories, vec, min_similarity=0.8, max_edges_per_node=5)
    pairs = {frozenset((e["source"], e["target"])) for e in edges}
    assert frozenset(("a", "b")) in pairs
    assert all("c" not in p for p in pairs)  # below threshold, excluded


def test_similarity_edges_no_self_loop_and_deduped():
    memories = [_mem("a"), _mem("b")]
    vec = MagicMock(healthy=True)
    vec.search.side_effect = lambda text, k: [
        {"memory_id": "a", "score": 1.0},
        {"memory_id": "b", "score": 0.99},
    ]
    edges = build_similarity_edges(memories, vec, min_similarity=0.5)
    assert len(edges) == 1
    assert edges[0]["source"] == "a" and edges[0]["target"] == "b"


def test_similarity_edges_skips_ids_outside_scope():
    memories = [_mem("a")]
    vec = MagicMock(healthy=True)
    vec.search.side_effect = lambda text, k: [
        {"memory_id": "a", "score": 1.0},
        {"memory_id": "ghost-from-another-owner", "score": 0.95},
    ]
    edges = build_similarity_edges(memories, vec, min_similarity=0.5)
    assert edges == []


def test_similarity_edges_unhealthy_vector_store_returns_nothing():
    memories = [_mem("a"), _mem("b")]
    vec = MagicMock(healthy=False)
    assert build_similarity_edges(memories, vec) == []
    assert build_similarity_edges(memories, None) == []


def test_similarity_edges_single_memory_short_circuits_without_querying():
    vec = MagicMock(healthy=True)
    assert build_similarity_edges([_mem("a")], vec) == []
    vec.search.assert_not_called()


def test_session_edges_link_same_session_only():
    memories = [
        _mem("a", session_id="s1"),
        _mem("b", session_id="s1"),
        _mem("c", session_id="s2"),
    ]
    edges = build_session_edges(memories)
    assert len(edges) == 1
    assert {edges[0]["source"], edges[0]["target"]} == {"a", "b"}
    assert edges[0]["type"] == "session"


def test_session_edges_ignores_singleton_sessions_and_missing_session_id():
    memories = [_mem("a", session_id="solo"), _mem("b")]
    assert build_session_edges(memories) == []


def test_manual_edges_reflect_links_field_bidirectionally_deduped():
    memories = [_mem("a", links=["b"]), _mem("b", links=["a"]), _mem("c")]
    edges = build_manual_edges(memories)
    assert len(edges) == 1
    assert {edges[0]["source"], edges[0]["target"]} == {"a", "b"}
    assert edges[0]["type"] == "manual"


def test_manual_edges_ignore_self_links_and_dangling_targets():
    memories = [_mem("a", links=["a", "does-not-exist"])]
    assert build_manual_edges(memories) == []


def test_build_graph_filters_by_category():
    memories = [_mem("a", category="fact"), _mem("b", category="preference")]
    graph = build_graph(memories, categories=["fact"])
    assert [n["id"] for n in graph["nodes"]] == ["a"]
    assert graph["meta"]["total_memories"] == 1


def test_build_graph_truncates_and_keeps_most_used_recent_first():
    memories = [
        _mem("a", uses=0, timestamp=1),
        _mem("b", uses=5, timestamp=1),
        _mem("c", uses=0, timestamp=2),
    ]
    graph = build_graph(memories, limit=2)
    ids = {n["id"] for n in graph["nodes"]}
    assert ids == {"b", "c"}
    assert graph["meta"]["truncated"] is True
    assert graph["meta"]["total_memories"] == 3


def test_build_graph_combines_all_edge_types():
    memories = [
        _mem("a", session_id="s1", links=["b"]),
        _mem("b", session_id="s1"),
    ]
    vec = MagicMock(healthy=True)
    vec.search.side_effect = lambda text, k: [{"memory_id": "a", "score": 1.0}, {"memory_id": "b", "score": 0.99}]
    graph = build_graph(memories, vec, min_similarity=0.5)
    types = {e["type"] for e in graph["edges"]}
    assert types == {"similarity", "session", "manual"}
    assert graph["meta"]["node_count"] == 2


def test_build_graph_no_vector_store_skips_similarity_edges_only():
    memories = [_mem("a", session_id="s1"), _mem("b", session_id="s1")]
    graph = build_graph(memories, memory_vector=None)
    assert {e["type"] for e in graph["edges"]} == {"session"}


def test_build_graph_flags_can_disable_derived_edge_types():
    memories = [_mem("a", session_id="s1", links=["b"]), _mem("b", session_id="s1")]
    graph = build_graph(memories, include_session_edges=False, include_manual_edges=False)
    assert graph["edges"] == []
