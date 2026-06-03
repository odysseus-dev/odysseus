# routes/graph_routes.py
"""HTTP API for the user knowledge graph (Kuzu-backed)."""

from fastapi import APIRouter, HTTPException, Request
from typing import Optional
import logging

from services.memory import GraphStore
from src.auth_helpers import get_current_user

logger = logging.getLogger(__name__)


def setup_graph_routes(graph_store: Optional[GraphStore]):
    """Wire up /api/graph endpoints. The router still mounts even when the
    graph is unhealthy — endpoints respond 503 so callers can detect the
    degraded state without crashing the server at startup."""
    router = APIRouter(prefix="/api/graph", tags=["graph"])

    def _owner(request: Request) -> Optional[str]:
        return get_current_user(request)

    def _require_healthy():
        if graph_store is None or not graph_store.healthy:
            raise HTTPException(503, "Graph store unavailable (kuzu missing or DB error)")

    @router.get("/stats")
    def graph_stats(request: Request):
        """Return per-label node counts and per-relation edge counts for the
        current user. Cheap; safe to poll from the UI."""
        _require_healthy()
        return graph_store.stats(owner=_owner(request))

    @router.get("/entities")
    def list_entities(request: Request, label: Optional[str] = None, limit: int = 200):
        """List entities the graph knows about for this user.

        ?label=Person filters to one entity type. Without the filter you get
        every entity across all labels, capped at `limit`.
        """
        _require_healthy()
        limit = max(1, min(int(limit or 200), 1000))
        return {"entities": graph_store.entities(owner=_owner(request), label=label, limit=limit)}

    @router.get("/neighborhood")
    def neighborhood(request: Request, depth: int = 1, limit: int = 50):
        """Return triples around the User node, suitable for chat-context
        injection or front-end visualization. Depth is clamped to [1, 3]."""
        _require_healthy()
        return {
            "triples": graph_store.neighborhood(
                owner=_owner(request), depth=int(depth), limit=int(limit),
            ),
        }

    return router
