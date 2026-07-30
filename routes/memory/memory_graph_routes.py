# routes/memory/memory_graph_routes.py
"""Memory Graph View endpoints: read-only graph derivation plus manual
relationship (link) editing between a user's own memories.

Kept as a separate router (not folded into memory_routes.py's wildcard-heavy
router) but MUST be included in app.py before that router — see the comment
at the include_router call site. `GET /api/memory/graph` would otherwise be
swallowed by memory_routes.py's `GET /api/memory/{memory_id}` wildcard if
that router's routes were checked first.
"""
from typing import Dict, List, Optional
import logging

from fastapi import APIRouter, HTTPException, Query, Request

from services.memory import MemoryManager
from src.auth_helpers import get_current_user, require_privilege
from src.memory_graph import (
    DEFAULT_MAX_EDGES_PER_NODE,
    DEFAULT_MIN_SIMILARITY,
    build_graph,
)

logger = logging.getLogger(__name__)


def setup_memory_graph_routes(memory_manager: MemoryManager, memory_vector=None):
    """Set up Memory Graph View routes."""
    router = APIRouter(prefix="/api/memory", tags=["memory-graph"])

    def _owner(request: Request) -> Optional[str]:
        return get_current_user(request)

    def _verify_memory_owner(memory: dict, user: Optional[str]):
        """Raise 404 if user doesn't own this memory. Mirrors
        memory_routes.py's _verify_memory_owner: strict ownership so a
        legacy/null-owner memory never leaks across accounts."""
        if user is None:
            return  # Auth disabled
        if memory.get("owner") != user:
            raise HTTPException(404, "Memory not found")

    @router.get("/graph")
    def get_memory_graph(
        request: Request,
        category: Optional[List[str]] = Query(None),
        min_similarity: float = Query(DEFAULT_MIN_SIMILARITY, ge=0.0, le=1.0),
        max_edges_per_node: int = Query(DEFAULT_MAX_EDGES_PER_NODE, ge=1, le=50),
        include_session_edges: bool = Query(True),
        include_manual_edges: bool = Query(True),
        limit: int = Query(1000, ge=1, le=5000),
    ):
        """Return the caller's own memories as a derived node/edge graph."""
        user = _owner(request)
        memories = memory_manager.load(owner=user)
        return build_graph(
            memories,
            memory_vector,
            categories=category,
            min_similarity=min_similarity,
            max_edges_per_node=max_edges_per_node,
            include_session_edges=include_session_edges,
            include_manual_edges=include_manual_edges,
            limit=limit,
        )

    @router.get("/graph/{memory_id}/neighbors")
    def get_memory_graph_neighbors(
        request: Request,
        memory_id: str,
        min_similarity: float = Query(DEFAULT_MIN_SIMILARITY, ge=0.0, le=1.0),
        max_edges_per_node: int = Query(DEFAULT_MAX_EDGES_PER_NODE, ge=1, le=50),
    ):
        """Lazy drill-down: one node plus its immediate derived neighbors.

        For graphs too large to render whole (see build_graph's `limit`/
        `truncated`), the frontend can expand a single node on demand instead
        of the server ever needing to compute/return the entire graph.
        """
        user = _owner(request)
        memories = memory_manager.load(owner=user)
        target = next((m for m in memories if m.get("id") == memory_id), None)
        if target is None:
            raise HTTPException(404, "Memory not found")
        _verify_memory_owner(target, user)

        full = build_graph(
            memories,
            memory_vector,
            min_similarity=min_similarity,
            max_edges_per_node=max_edges_per_node,
            limit=len(memories) or 1,
        )
        neighbor_ids = {
            (e["target"] if e["source"] == memory_id else e["source"])
            for e in full["edges"]
            if memory_id in (e["source"], e["target"])
        }
        neighbor_ids.add(memory_id)
        nodes = [n for n in full["nodes"] if n["id"] in neighbor_ids]
        edges = [e for e in full["edges"] if e["source"] in neighbor_ids and e["target"] in neighbor_ids]
        return {"nodes": nodes, "edges": edges, "meta": {"node_count": len(nodes), "edge_count": len(edges)}}

    @router.post("/{memory_id}/links")
    def add_memory_link(request: Request, memory_id: str, target_id: str = Query(...)):
        """Create an explicit manual relationship between two of the caller's
        own memories (the Memory Graph View's "draw a link" affordance)."""
        require_privilege(request, "can_manage_memory")
        user = _owner(request)
        if target_id == memory_id:
            raise HTTPException(400, "A memory cannot link to itself")

        all_mem = memory_manager.load_all()
        source = next((m for m in all_mem if m.get("id") == memory_id), None)
        if source is None:
            raise HTTPException(404, "Memory not found")
        _verify_memory_owner(source, user)
        target = next((m for m in all_mem if m.get("id") == target_id), None)
        if target is None:
            raise HTTPException(404, "Target memory not found")
        _verify_memory_owner(target, user)

        links = list(source.get("links") or [])
        if target_id not in links:
            links.append(target_id)
        source["links"] = links
        memory_manager.save(all_mem)
        return {"ok": True, "links": links}

    @router.delete("/{memory_id}/links/{target_id}")
    def remove_memory_link(request: Request, memory_id: str, target_id: str):
        """Remove a manual relationship. Idempotent — removing a link that
        doesn't exist is not an error, matching how memory delete/pin already
        treat repeat calls as harmless in this codebase."""
        require_privilege(request, "can_manage_memory")
        user = _owner(request)
        all_mem = memory_manager.load_all()
        source = next((m for m in all_mem if m.get("id") == memory_id), None)
        if source is None:
            raise HTTPException(404, "Memory not found")
        _verify_memory_owner(source, user)

        links = list(source.get("links") or [])
        if target_id in links:
            links = [l for l in links if l != target_id]
            source["links"] = links
            memory_manager.save(all_mem)
        return {"ok": True, "links": links}

    return router
