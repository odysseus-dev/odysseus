"""MCP gateway pool admin routes — /api/mcp/gateways/*.

Exposes the round-robin gateway pool from :mod:`src.mcp_gateway` so an operator
can add endpoints, inspect routing health, and clear a dead entry without
restarting the server. All routes are admin-gated: adding a gateway changes
where Odysseus sends privileged tool calls.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.middleware import require_admin
from src.mcp_gateway import GatewayExhausted, GatewayRejected, get_pool

logger = logging.getLogger(__name__)


class AddGatewayRequest(BaseModel):
    base_url: str = Field(..., description="HTTPS base URL of the MCP API gateway")
    discover: bool = Field(True, description="Fetch the gateway manifest on add")


def setup_mcp_gateway_routes() -> APIRouter:
    router = APIRouter(prefix="/api/mcp/gateways", tags=["mcp-gateways"])

    def _endpoint_or_404(index: int):
        """Resolve a path index to an endpoint, raising 404 when out of range."""
        eps = get_pool().endpoints
        if index < 0 or index >= len(eps):
            raise HTTPException(status_code=404, detail=f"no gateway at index {index}")
        return eps[index]

    # Declared before the indexed routes so /health is never parsed as an index.
    @router.get("/health")
    async def pool_health(request: Request):
        """Aggregate routing health. Safe to poll from a monitor."""
        require_admin(request)
        pool = get_pool()
        status = pool.status()
        try:
            pool.acquire()
            status["routable"] = True
        except GatewayExhausted:
            status["routable"] = False
        return status

    @router.get("")
    async def list_gateways(request: Request):
        """Current pool membership plus per-endpoint routing state."""
        require_admin(request)
        return get_pool().status()

    @router.post("")
    async def add_gateway(request: Request, body: AddGatewayRequest):
        """Add a gateway endpoint to the round-robin pool."""
        require_admin(request)
        pool = get_pool()
        try:
            ep = pool.add(body.base_url)
        except GatewayRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        manifest: Optional[dict] = None
        if body.discover:
            try:
                manifest = await pool.discover(ep.base_url)
            except Exception as exc:
                # Discovery is best-effort: a gateway that does not publish a
                # manifest is still usable via explicit paths.
                logger.warning("gateway manifest discovery failed for %s: %s", ep.base_url, exc)
                manifest = {"error": f"{type(exc).__name__}: {exc}"}

        return {"added": ep.base_url, "manifest": manifest, "pool": pool.status()}

    @router.get("/{index}/manifest")
    async def get_manifest(request: Request, index: int, refresh: bool = False):
        """Read the endpoint map a gateway advertises, optionally re-fetching."""
        require_admin(request)
        ep = _endpoint_or_404(index)
        pool = get_pool()
        if refresh or not ep.manifest:
            try:
                await pool.discover(ep.base_url)
            except Exception as exc:
                raise HTTPException(
                    status_code=502, detail=f"manifest fetch failed: {exc}"
                ) from exc
        return {"base_url": ep.base_url, "manifest": ep.manifest}

    @router.post("/{index}/revive")
    async def revive_gateway(request: Request, index: int):
        """Clear cooldown and dead flag for an endpoint."""
        require_admin(request)
        ep = _endpoint_or_404(index)
        pool = get_pool()
        if not pool.revive(ep.base_url):
            raise HTTPException(status_code=409, detail=f"could not revive {ep.base_url}")
        return {"revived": ep.base_url, "pool": pool.status()}

    @router.delete("/{index}")
    async def remove_gateway(request: Request, index: int):
        """Remove an endpoint by pool index (see GET / for membership)."""
        require_admin(request)
        ep = _endpoint_or_404(index)
        pool = get_pool()
        pool.remove(ep.base_url)
        return {"removed": ep.base_url, "pool": pool.status()}

    return router