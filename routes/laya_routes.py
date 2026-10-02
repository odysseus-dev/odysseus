"""laya integration routes.

Everything here is **admin-only**: it reveals deployment detail (resident
checkpoints, device, audit trail) and exposes operational controls (pause/resume,
manual run), matching how other operational surfaces (/api/db/stats) are gated.
All data endpoints read the fail-open service and the LayaRun audit log, so they
return 200 with ``reachable: false`` when laya is disabled or down — never 5xx
because of laya.

Surface:
  GET  /api/laya/health   liveness passthrough
  GET  /api/laya/status   full monitoring snapshot (health + config + summary)
  GET  /api/laya/runs     recent audit rows (redacted)
  POST /api/laya/pause    runtime pause (no decisions run)
  POST /api/laya/resume   undo pause
  POST /api/laya/run      manual one-off decision (observe-only, audited)
  GET  /laya/admin        the admin monitoring panel (HTML)
"""

import logging
import os
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from core.constants import BASE_DIR
from core.middleware import require_admin
from services.laya import get_laya_service, is_paused, set_paused
from services.laya import audit, policy

logger = logging.getLogger(__name__)

# Wiring state per capability. Advisory decisions laya *can* make vs. what Odysseus
# actually calls today. "shadow" = computed + logged, never acted on.
CAPABILITY_STATE = {
    "route": "shadow",      # wired into /api/chat, observe-only (M2)
    "guard": "available",   # implemented, not yet wired
    "moderate": "available",
    "triage": "available",
}


class ManualRunRequest(BaseModel):
    capability: str
    text: str


def setup_laya_routes() -> APIRouter:
    router = APIRouter(tags=["laya"])

    @router.get("/api/laya/health")
    async def laya_health(request: Request) -> Dict[str, Any]:
        require_admin(request)
        return (await get_laya_service().health()).to_dict()

    @router.get("/api/laya/status")
    async def laya_status(request: Request) -> Dict[str, Any]:
        require_admin(request)
        service = get_laya_service()
        cfg = service.config
        health = await service.health()
        # guard reflects its live activation mode (off | warn | block)
        capabilities = dict(CAPABILITY_STATE)
        capabilities["guard"] = cfg.guard_mode if cfg.guard_mode != "off" else "available"
        return {
            "enabled": service.enabled,
            "paused": is_paused(),
            "active": service.active,
            "health": health.to_dict(),
            "capabilities": capabilities,
            "config": {
                "url": cfg.url,
                "api_key_set": bool(cfg.api_key),   # never return the key itself
                "timeout": cfg.timeout,
                "retries": cfg.retries,
                "thresholds": {
                    "route_min_confidence": policy.route_min_confidence(),
                    "guard": policy.guard_threshold(),
                    "moderation": policy.moderation_threshold(),
                },
            },
            "summary": audit.summary(),
        }

    @router.get("/api/laya/runs")
    async def laya_runs(request: Request, limit: int = 50, capability: str = "") -> Dict[str, Any]:
        require_admin(request)
        cap = capability.strip() or None
        if cap and cap not in CAPABILITY_STATE:
            raise HTTPException(400, f"unknown capability '{cap}'")
        return {"runs": audit.recent(limit=limit, capability=cap)}

    @router.post("/api/laya/pause")
    async def laya_pause(request: Request) -> Dict[str, Any]:
        require_admin(request)
        return {"paused": set_paused(True)}

    @router.post("/api/laya/resume")
    async def laya_resume(request: Request) -> Dict[str, Any]:
        require_admin(request)
        return {"paused": set_paused(False)}

    @router.post("/api/laya/run")
    async def laya_manual_run(request: Request, body: ManualRunRequest) -> Dict[str, Any]:
        """Run one decision by hand for testing. Observe-only (shadow=True): a
        manual run never acts on the result, it just exercises the engine."""
        require_admin(request)
        cap = body.capability.strip()
        text = (body.text or "").strip()
        if cap not in CAPABILITY_STATE:
            raise HTTPException(400, f"unknown capability '{cap}'")
        if not text:
            raise HTTPException(400, "text is required")
        service = get_laya_service()
        if not service.enabled:
            raise HTTPException(409, "laya is disabled (set LAYA_ENABLED=true)")
        if is_paused():
            raise HTTPException(409, "laya is paused; resume before running")
        method = getattr(service, cap)
        decision = await method(text, owner=None, shadow=True)
        return decision.to_dict()

    @router.get("/laya/admin")
    async def laya_admin_panel(request: Request) -> HTMLResponse:
        require_admin(request)
        path = os.path.join(BASE_DIR, "static", "laya-admin.html")
        try:
            with open(path, "r", encoding="utf-8") as f:
                html = f.read()
        except FileNotFoundError:
            raise HTTPException(404, "laya admin panel not found")
        nonce = getattr(request.state, "csp_nonce", "")
        html = html.replace("{{CSP_NONCE}}", nonce)
        return HTMLResponse(html)

    return router
