from pathlib import Path
from datetime import datetime
import json

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse


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


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _json_file(path: Path, fallback):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"error": str(exc), "path": str(path)}
    return fallback


def setup_gnexus_governance_routes() -> APIRouter:
    router = APIRouter(tags=["gnexus-governance"])

    @router.get("/gnexus/governance", response_class=HTMLResponse)
    async def governance_console(request: Request):
        _require_admin(request)
        page = _repo_root() / "static" / "gnexus" / "governance-console.html"
        if not page.exists():
            raise HTTPException(404, "governance console not installed")
        return HTMLResponse(page.read_text(encoding="utf-8"))

    @router.get("/api/gnexus/governance/state")
    async def governance_state(request: Request):
        _require_admin(request)
        base = _repo_root() / "data" / "gnexus"
        return {
            "status": "JUNIPERUS_GOVERNANCE_ROUTES_ACTIVE",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "workspace": _json_file(_repo_root() / "config" / "gnexus.workspace.example.json", {}),
            "policy": _json_file(_repo_root() / "config" / "gnexus.policy.example.json", {}),
            "missionControl": _json_file(base / "mission-control" / "governance-state.json", {}),
            "projects": _json_file(base / "project-registry.json", {"items": []}),
            "apps": _json_file(base / "app-registry.json", {"items": []}),
            "approvals": _json_file(base / "approval-queue.json", {"items": []}),
            "receipts": _json_file(base / "operation-receipts.json", {"items": []})
        }

    @router.post("/api/gnexus/governance/approvals/{approval_id}/approve")
    async def approve_item(request: Request, approval_id: str):
        _require_admin(request)
        from src.gnexus_governance.approval_queue import update_status
        queue = _repo_root() / "data" / "gnexus" / "approval-queue.json"
        item = update_status(str(queue), approval_id, "APPROVED_NO_EXECUTION_ATTACHED")
        if not item:
            raise HTTPException(404, "approval not found")
        return {"status": "approved_no_execution_attached", "item": item}

    @router.post("/api/gnexus/governance/approvals/{approval_id}/deny")
    async def deny_item(request: Request, approval_id: str):
        _require_admin(request)
        from src.gnexus_governance.approval_queue import update_status
        queue = _repo_root() / "data" / "gnexus" / "approval-queue.json"
        item = update_status(str(queue), approval_id, "DENIED")
        if not item:
            raise HTTPException(404, "approval not found")
        return {"status": "denied", "item": item}

    return router
