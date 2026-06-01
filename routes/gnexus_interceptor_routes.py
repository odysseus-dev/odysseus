"""Gnexus interceptor state routes for Juniperus."""
from __future__ import annotations

from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse


def setup_gnexus_interceptor_routes() -> APIRouter:
    router = APIRouter(tags=["gnexus-interceptor"])

    def _repo_root() -> Path:
        return Path(__file__).resolve().parents[1]

    @router.get("/gnexus/interceptor")
    async def gnexus_interceptor_page(request: Request):
        page = _repo_root() / "static" / "gnexus" / "interceptor.html"
        if page.exists():
            return FileResponse(str(page), media_type="text/html")
        return JSONResponse({"error": "interceptor page not found"}, status_code=404)

    @router.get("/api/gnexus/interceptor/state")
    async def gnexus_interceptor_state(request: Request):
        from src.gnexus_governance.operation_guard import get_interceptor_state
        return JSONResponse(get_interceptor_state())

    return router
