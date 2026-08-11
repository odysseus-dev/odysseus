"""memory_graph.py

Derives a node/edge graph over a user's own memory entries for the Memory
Graph View. Nothing here is persisted beyond the optional manual `links`
field already carried on a memory entry (see routes/memory/memory_graph_routes.py)
— edges are computed fresh from MemoryManager entries and MemoryVectorStore
similarity search on every call.
"""

from typing import Any, Dict, List, Optional

DEFAULT_MIN_SIMILARITY = 0.75
DEFAULT_MAX_EDGES_PER_NODE = 5
DEFAULT_LIMIT = 1000


def _node_from_entry(entry: Dict) -> Dict:
    return {
        "id": entry.get("id"),
        "text": entry.get("text", ""),
        "category": entry.get("category", "fact"),
        "pinned": bool(entry.get("pinned", False)),
        "uses": int(entry.get("uses", 0) or 0),
        "timestamp": entry.get("timestamp"),
        "session_id": entry.get("session_id"),
    }


def _sorted_pair(a: str, b: str) -> tuple:
    return (a, b) if a <= b else (b, a)


def build_similarity_edges(
    memories: List[Dict],
    memory_vector,
    *,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
    max_edges_per_node: int = DEFAULT_MAX_EDGES_PER_NODE,
) -> List[Dict]:
    """Derive semantic-similarity edges via one nearest-neighbor query per node.

    Deliberately per-node top-k (via the vector store's own ANN search),
    never O(n^2) pairwise comparison, so this stays cheap as memory count
    grows — see docs/memory-graph-design.md, "Performance considerations".
    """
    if not memory_vector or not getattr(memory_vector, "healthy", False):
        return []

    ids_in_scope = {m["id"] for m in memories if m.get("id")}
    if len(ids_in_scope) < 2:
        return []

    edges = []
    seen_pairs = set()
    for mem in memories:
        mid = mem.get("id")
        text = (mem.get("text") or "").strip()
        if not mid or not text:
            continue
        try:
            results = memory_vector.search(text, k=max_edges_per_node + 1)
        except Exception:
            continue
        for row in results:
            other_id = row.get("memory_id")
            score = row.get("score", 0.0)
            if not other_id or other_id == mid or other_id not in ids_in_scope:
                continue
            if score < min_similarity:
                continue
            pair = _sorted_pair(mid, other_id)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            edges.append({
                "source": pair[0],
                "target": pair[1],
                "type": "similarity",
                "weight": round(float(score), 4),
            })
    return edges


def build_session_edges(memories: List[Dict]) -> List[Dict]:
    """Derive edges between memories extracted from the same chat session."""
    by_session: Dict[str, List[str]] = {}
    for mem in memories:
        sid = mem.get("session_id")
        mid = mem.get("id")
        if sid and mid:
            by_session.setdefault(sid, []).append(mid)

    edges = []
    seen_pairs = set()
    for ids in by_session.values():
        if len(ids) < 2:
            continue
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                pair = _sorted_pair(ids[i], ids[j])
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                edges.append({"source": pair[0], "target": pair[1], "type": "session", "weight": 1.0})
    return edges


def build_manual_edges(memories: List[Dict]) -> List[Dict]:
    """Derive edges from user-authored explicit links (entry['links'])."""
    ids_in_scope = {m["id"] for m in memories if m.get("id")}
    edges = []
    seen_pairs = set()
    for mem in memories:
        mid = mem.get("id")
        links = mem.get("links") or []
        if not mid or not isinstance(links, list):
            continue
        for target_id in links:
            if not target_id or target_id == mid or target_id not in ids_in_scope:
                continue
            pair = _sorted_pair(mid, target_id)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            edges.append({"source": pair[0], "target": pair[1], "type": "manual", "weight": 1.0})
    return edges


def build_graph(
    memories: List[Dict],
    memory_vector=None,
    *,
    categories: Optional[List[str]] = None,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
    max_edges_per_node: int = DEFAULT_MAX_EDGES_PER_NODE,
    include_session_edges: bool = True,
    include_manual_edges: bool = True,
    limit: int = DEFAULT_LIMIT,
) -> Dict[str, Any]:
    """Build a `{nodes, edges, meta}` graph for one owner's already-filtered
    (owner-scoped) memory list. Caller is responsible for owner scoping —
    this function has no concept of ownership, only the entries it's given.
    """
    filtered = memories
    if categories:
        cat_set = set(categories)
        filtered = [m for m in filtered if m.get("category", "fact") in cat_set]

    total = len(filtered)
    truncated = total > limit
    if truncated:
        # Keep the most-used / most-recent memories rather than an arbitrary
        # slice, so truncation drops the least-referenced tail first.
        filtered = sorted(
            filtered,
            key=lambda m: (int(m.get("uses", 0) or 0), m.get("timestamp", 0) or 0),
            reverse=True,
        )[:limit]

    nodes = [_node_from_entry(m) for m in filtered]

    edges: List[Dict] = []
    if memory_vector is not None:
        edges.extend(build_similarity_edges(
            filtered, memory_vector,
            min_similarity=min_similarity,
            max_edges_per_node=max_edges_per_node,
        ))
    if include_session_edges:
        edges.extend(build_session_edges(filtered))
    if include_manual_edges:
        edges.extend(build_manual_edges(filtered))

    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "total_memories": total,
            "truncated": truncated,
        },
    }
