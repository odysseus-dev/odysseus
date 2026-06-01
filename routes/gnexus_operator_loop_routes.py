"""Routes for Juniperus Full Operator Loop."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse

from src.gnexus_governance.operator_loop import (
    create_operation_plan,
    initialize_operator_loop_files,
    load_state,
)


def _require_admin_or_local(request: Request):
    auth_manager = getattr(request.app.state, "auth_manager", None)
    if not auth_manager:
        return
    user = getattr(request.state, "current_user", None)
    if user == "internal-tool":
        return
    if not user or user == "api":
        raise HTTPException(403, "Admin only")
    if not auth_manager.is_admin(user):
        raise HTTPException(403, "Admin only")


def setup_gnexus_operator_loop_routes() -> APIRouter:
    router = APIRouter(tags=["gnexus-operator-loop"])

    @router.get("/gnexus/operator-loop", response_class=HTMLResponse)
    async def operator_loop_page(request: Request):
        _require_admin_or_local(request)
        page = Path("static") / "gnexus" / "operator-loop.html"
        if not page.exists():
            raise HTTPException(404, "operator-loop.html not found")
        return HTMLResponse(page.read_text(encoding="utf-8"))

    @router.get("/api/gnexus/operator-loop/state")
    async def operator_loop_state(request: Request):
        _require_admin_or_local(request)
        initialize_operator_loop_files()
        return load_state()

    @router.post("/api/gnexus/operator-loop/plan")
    async def operator_loop_plan(request: Request, body: dict):
        _require_admin_or_local(request)
        initialize_operator_loop_files()
        intent = str(body.get("intent") or "").strip()
        if not intent:
            raise HTTPException(400, "intent is required")
        app_id = body.get("appId")
        operation_type = str(body.get("operationType") or "inspect_plan")
        requested_by = str(getattr(request.state, "current_user", None) or "local-user")
        return create_operation_plan(
            intent=intent,
            app_id=str(app_id) if app_id else None,
            operation_type=operation_type,
            requested_by=requested_by,
        )

    return router
