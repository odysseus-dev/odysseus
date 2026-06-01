"""Admin-gated Codex / ChatGPT device-code auth routes."""

from fastapi import APIRouter, Request

from core.middleware import require_admin
from src.codex_auth import get_codex_auth_service


def setup_codex_auth_routes() -> APIRouter:
    router = APIRouter(prefix="/api/codex-auth", tags=["codex-auth"])

    @router.get("/status")
    async def status(request: Request):
        require_admin(request)
        return await get_codex_auth_service().status()

    @router.post("/start")
    async def start(request: Request):
        require_admin(request)
        return await get_codex_auth_service().start()

    @router.post("/cancel")
    async def cancel(request: Request):
        require_admin(request)
        return await get_codex_auth_service().cancel()

    @router.post("/logout")
    async def logout(request: Request):
        require_admin(request)
        return await get_codex_auth_service().logout()

    @router.post("/test")
    async def test(request: Request):
        require_admin(request)
        return await get_codex_auth_service().test()

    return router
