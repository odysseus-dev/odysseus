"""Admin app log viewer — list and tail logs under logs/."""

from fastapi import APIRouter, HTTPException, Request

from core.app_logs import DEFAULT_TAIL_LINES, enumerate_logs, tail_log
from core.middleware import require_admin


def setup_admin_logs_routes() -> APIRouter:
    router = APIRouter(tags=["admin-logs"])

    @router.get("/api/admin/logs")
    async def list_app_logs(request: Request):
        require_admin(request)
        return {"logs": enumerate_logs()}

    @router.get("/api/admin/logs/{name}")
    async def get_app_log_tail(request: Request, name: str, tail: int = DEFAULT_TAIL_LINES):
        require_admin(request)
        result = tail_log(name, lines=tail)
        if result is None:
            raise HTTPException(404, "Log not found")
        return result

    return router
