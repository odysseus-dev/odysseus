"""Routes for Gnexus verifier / repair / rollback loop."""
from __future__ import annotations
from pathlib import Path
from typing import Any, Dict
from fastapi import APIRouter, Body
from fastapi.responses import HTMLResponse
def _repo_root() -> Path: return Path(__file__).resolve().parents[1]
def setup_gnexus_verifier_loop_routes() -> APIRouter:
    router = APIRouter(tags=["gnexus-verifier-loop"])
    @router.get("/gnexus/verifier-loop", response_class=HTMLResponse)
    async def gnexus_verifier_loop_page():
        page = _repo_root() / "static" / "gnexus" / "verifier-loop.html"
        return HTMLResponse(page.read_text(encoding="utf-8"))
    @router.get("/api/gnexus/verifier-loop/state")
    async def gnexus_verifier_loop_state() -> Dict[str, Any]:
        from src.gnexus_governance.verifier_loop import state
        return state()
    @router.post("/api/gnexus/verifier-loop/verification-request")
    async def gnexus_create_verification_request(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
        from src.gnexus_governance.verifier_loop import create_verification_request
        return create_verification_request(str(payload.get("changeId") or payload.get("change_id") or "manual-change"), str(payload.get("targetPath") or payload.get("target_path") or ""), str(payload.get("verifierHint") or payload.get("verifier_hint") or ""), str(payload.get("requestedBy") or payload.get("requested_by") or "juniperus"), payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {})
    @router.post("/api/gnexus/verifier-loop/verification-result")
    async def gnexus_record_verification_result(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
        from src.gnexus_governance.verifier_loop import record_verification_result
        return record_verification_result(str(payload.get("requestId") or payload.get("request_id") or "manual-request"), bool(payload.get("passed", False)), str(payload.get("summary") or "No summary provided"), str(payload.get("outputExcerpt") or payload.get("output_excerpt") or ""), payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {})
    @router.post("/api/gnexus/verifier-loop/repair-item")
    async def gnexus_create_repair_item(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
        from src.gnexus_governance.verifier_loop import create_repair_item
        return create_repair_item(str(payload.get("sourceId") or payload.get("source_id") or "manual-source"), str(payload.get("severity") or "repair_required"), str(payload.get("summary") or "Manual repair item"), str(payload.get("suggestedNextStep") or payload.get("suggested_next_step") or ""), payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {})
    @router.post("/api/gnexus/verifier-loop/rollback-request")
    async def gnexus_create_rollback_request(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
        from src.gnexus_governance.verifier_loop import create_rollback_request
        return create_rollback_request(str(payload.get("changeId") or payload.get("change_id") or "manual-change"), str(payload.get("snapshotId") or payload.get("snapshot_id") or ""), str(payload.get("reason") or "Manual rollback request"), str(payload.get("requestedBy") or payload.get("requested_by") or "juniperus"), payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {})
    return router
