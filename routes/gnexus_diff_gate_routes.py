"""Routes for Juniperus / Gnexus Diff-First Code Editing Gate."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse


def setup_gnexus_diff_gate_routes() -> APIRouter:
    router = APIRouter(tags=["gnexus-diff-gate"])

    def _require_admin(request: Request):
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

    @router.get("/gnexus/diff-gate")
    async def diff_gate_page(request: Request):
        _require_admin(request)
        page = Path("static") / "gnexus" / "diff-gate.html"
        if not page.exists():
            raise HTTPException(404, "diff gate page not found")
        return FileResponse(str(page), media_type="text/html")

    @router.get("/api/gnexus/diff-gate/state")
    async def diff_gate_state(request: Request):
        _require_admin(request)
        from src.gnexus_governance.diff_gate import get_state, list_patch_queue
        return JSONResponse({"state": get_state(), "queue": list_patch_queue()})

    @router.post("/api/gnexus/diff-gate/propose")
    async def diff_gate_propose(request: Request):
        _require_admin(request)
        body = await request.json()
        path = str(body.get("path") or "").strip()
        content = str(body.get("content") or "")
        source = str(body.get("source") or "manual_api")
        if not path:
            raise HTTPException(400, "path is required")
        from src.gnexus_governance.diff_gate import propose_write_file_diff
        return JSONResponse(propose_write_file_diff(path, content, source=source))

    @router.post("/api/gnexus/diff-gate/decision")
    async def diff_gate_decision(request: Request):
        _require_admin(request)
        body = await request.json()
        proposal_id = str(body.get("proposalId") or body.get("id") or "").strip()
        decision = str(body.get("decision") or "").strip()
        note = str(body.get("note") or "")
        actor = getattr(request.state, "current_user", None) or "operator"
        if not proposal_id:
            raise HTTPException(400, "proposalId is required")
        from src.gnexus_governance.diff_gate import decide_patch
        return JSONResponse(decide_patch(proposal_id, decision, actor=actor, note=note))

    return router
