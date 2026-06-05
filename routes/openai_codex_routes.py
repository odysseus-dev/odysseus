"""Admin routes for the OpenAI Codex ChatGPT-subscription model provider."""

from fastapi import APIRouter, Request

from core.middleware import require_admin
from src.auth_helpers import get_current_user, require_user
from src import openai_codex


def setup_openai_codex_routes() -> APIRouter:
    router = APIRouter(prefix="/api/openai-codex")

    def _owner(request: Request) -> str:
        require_admin(request)
        try:
            return require_user(request)
        except Exception:
            return get_current_user(request) or ""

    @router.get("/status")
    def status(request: Request):
        return openai_codex.credential_status(_owner(request))

    @router.post("/device/start")
    async def device_start(request: Request):
        return await openai_codex.start_device_login(_owner(request))

    @router.post("/device/poll/{login_id}")
    async def device_poll(login_id: str, request: Request):
        return await openai_codex.poll_device_login(_owner(request), login_id)

    @router.post("/logout")
    def logout(request: Request):
        return openai_codex.logout(_owner(request))

    return router
