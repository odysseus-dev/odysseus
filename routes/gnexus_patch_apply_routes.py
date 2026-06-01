"""Routes for JUNIPERUS060 approved patch apply + rollback executor."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel


class ApplyPatchRequest(BaseModel):
    patch_id: str
    confirm: bool = False
    actor: str = "human"


class RollbackRequest(BaseModel):
    snapshot_id: str
    confirm: bool = False
    actor: str = "human"


def _require_admin(request: Request) -> None:
    auth_manager = getattr(request.app.state, "auth_manager", None)
    if not auth_manager:
        return
    user = getattr(request.state, "current_user", None)
    if user == "internal-tool":
        return
    if not user or user == "api" or not auth_manager.is_admin(user):
        raise HTTPException(status_code=403, detail="Admin only")


def setup_gnexus_patch_apply_routes() -> APIRouter:
    router = APIRouter(tags=["gnexus-patch-apply"])

    @router.get("/gnexus/patch-apply", response_class=HTMLResponse)
    async def patch_apply_page(request: Request):
        _require_admin(request)
        root = Path(__file__).resolve().parents[1]
        html = root / "static" / "gnexus" / "patch-apply.html"
        if not html.exists():
            raise HTTPException(status_code=404, detail="patch-apply.html not found")
        return HTMLResponse(html.read_text(encoding="utf-8"))

    @router.get("/api/gnexus/patch-apply/state")
    async def patch_apply_state(request: Request) -> Dict[str, Any]:
        _require_admin(request)
        from src.gnexus_governance.patch_apply import get_state
        return get_state()

    @router.get("/api/gnexus/patch-apply/queue")
    async def patch_apply_queue(request: Request) -> Dict[str, Any]:
        _require_admin(request)
        from src.gnexus_governance.patch_apply import list_patch_queue
        return list_patch_queue()

    @router.post("/api/gnexus/patch-apply/apply")
    async def patch_apply(request: Request, body: ApplyPatchRequest) -> Dict[str, Any]:
        _require_admin(request)
        from src.gnexus_governance.patch_apply import apply_approved_patch
        try:
            return apply_approved_patch(body.patch_id, confirm=body.confirm, actor=body.actor)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/gnexus/patch-apply/rollback")
    async def patch_rollback(request: Request, body: RollbackRequest) -> Dict[str, Any]:
        _require_admin(request)
        from src.gnexus_governance.patch_apply import restore_snapshot
        try:
            return restore_snapshot(body.snapshot_id, confirm=body.confirm, actor=body.actor)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    return router
