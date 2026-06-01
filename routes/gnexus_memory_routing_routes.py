from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from src.gnexus_governance.memory_routing import load_memory_routing_state, propose_route


def setup_gnexus_memory_routing_routes() -> APIRouter:
    router = APIRouter(tags=["gnexus-memory-routing"])

    @router.get("/gnexus/memory-routing", response_class=HTMLResponse)
    async def memory_routing_page(request: Request):
        root = Path(__file__).resolve().parents[1]
        page = root / "static" / "gnexus" / "memory-routing.html"
        if page.exists():
            return HTMLResponse(page.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>Gnexus Memory Routing</h1><p>memory-routing.html not found.</p>", status_code=200)

    @router.get("/api/gnexus/memory-routing/state")
    async def memory_routing_state():
        return JSONResponse(load_memory_routing_state())

    @router.post("/api/gnexus/memory-routing/propose")
    async def memory_routing_propose(payload: dict):
        task = str(payload.get("task") or "")
        project_id = str(payload.get("projectId") or "")
        return JSONResponse(propose_route(task=task, project_id=project_id))

    return router
