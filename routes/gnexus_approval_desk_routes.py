from __future__ import annotations

from pathlib import Path
from typing import Dict, Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from src.gnexus_governance.approval_desk import list_state, propose, decide

BASE_DIR = Path(__file__).resolve().parents[1]


def setup_gnexus_approval_desk_routes() -> APIRouter:
    router = APIRouter(tags=["gnexus-approval-desk"])

    @router.get("/gnexus/approval-desk")
    async def gnexus_approval_desk_page():
        page = BASE_DIR / "static" / "gnexus" / "approval-desk.html"
        if not page.exists():
            raise HTTPException(status_code=404, detail="Approval desk page not found")
        return FileResponse(str(page))

    @router.get("/api/gnexus/approval-desk/state")
    async def gnexus_approval_desk_state() -> Dict[str, Any]:
        return list_state()

    @router.post("/api/gnexus/approval-desk/propose")
    async def gnexus_approval_desk_propose(payload: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "queued", "item": propose(payload)}

    @router.post("/api/gnexus/approval-desk/decide")
    async def gnexus_approval_desk_decide(payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            record = decide(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"status": "recorded", "decision": record}

    return router
