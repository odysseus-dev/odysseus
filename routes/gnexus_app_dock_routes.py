"""Gnexus App Dock routes for JUNIPERUS020."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from src.gnexus_governance.app_scanner import scan_workspace, write_registry
from src.gnexus_governance.launch_queue import propose_launch

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = r"C:\Users\iamcy\CymaticsDev"

class LaunchProposalRequest(BaseModel):
    app_id: str
    command_key: str = "start"

def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default

def setup_gnexus_app_dock_routes() -> APIRouter:
    router = APIRouter(tags=["gnexus-app-dock"])

    @router.get("/gnexus/app-dock")
    async def app_dock_page():
        page = REPO_ROOT / "static" / "gnexus" / "app-dock.html"
        return FileResponse(str(page), media_type="text/html")

    @router.get("/api/gnexus/app-dock/state")
    async def app_dock_state():
        registry_path = REPO_ROOT / "data" / "gnexus" / "app-registry.json"
        state_path = REPO_ROOT / "data" / "gnexus" / "mission-control" / "app-dock-state.json"
        launch_queue_path = REPO_ROOT / "data" / "gnexus" / "app-dock" / "launch-queue.json"
        runtime_sessions_path = REPO_ROOT / "data" / "gnexus" / "app-dock" / "runtime-sessions.json"
        return JSONResponse({
            "state": _read_json(state_path, {}),
            "registry": _read_json(registry_path, {"apps": []}),
            "launchQueue": _read_json(launch_queue_path, {"items": []}),
            "runtimeSessions": _read_json(runtime_sessions_path, {"items": []}),
            "runtimeStartEnabled": False,
            "launchApprovalRequired": True,
        })

    @router.post("/api/gnexus/app-dock/scan")
    async def app_dock_scan():
        registry = write_registry(str(REPO_ROOT), WORKSPACE_ROOT, max_depth=5)
        return JSONResponse({"ok": True, "registry": registry})

    @router.post("/api/gnexus/app-dock/propose-launch")
    async def app_dock_propose_launch(req: LaunchProposalRequest, request: Request):
        user = getattr(request.state, "current_user", None) or "user"
        result = propose_launch(str(REPO_ROOT), req.app_id, req.command_key, requested_by=str(user))
        status = 200 if result.get("ok") else 400
        return JSONResponse(result, status_code=status)

    return router
