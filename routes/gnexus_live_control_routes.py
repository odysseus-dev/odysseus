"""Routes for the Gnexus live-control finalizer room."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from src.gnexus_governance.live_control import finalize_local_closeout, load_live_control_state


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def setup_gnexus_live_control_routes() -> APIRouter:
    router = APIRouter(tags=["gnexus-live-control"])

    @router.get("/gnexus/live-control")
    async def live_control_page(request: Request):
        html = _repo_root() / "static" / "gnexus" / "live-control.html"
        return FileResponse(str(html), media_type="text/html")

    @router.get("/api/gnexus/live-control/state")
    async def live_control_state(request: Request):
        return JSONResponse(load_live_control_state(_repo_root()))

    @router.post("/api/gnexus/live-control/finalize-local")
    async def live_control_finalize_local(request: Request):
        # Local finalizer only. This does not enable external live activation.
        return JSONResponse(finalize_local_closeout(_repo_root()))

    return router
