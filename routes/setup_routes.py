"""First-run automatic setup API (admin only)."""
from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from core.middleware import require_admin
from src.auto_setup import AutoSetup


class ConsentBody(BaseModel):
    approve: bool
    kind: str  # the question the page showed, so an answer cannot land on a different one
    name: str


def setup_setup_routes(model_discovery, invalidate_models_cache=None):
    router = APIRouter(prefix="/api/setup")
    setup = AutoSetup(model_discovery, invalidate_models_cache)

    @router.get("/status")
    async def setup_status(request: Request):
        require_admin(request)
        return await setup.status()

    @router.post("/start")
    async def setup_start(request: Request):
        require_admin(request)
        return await setup.start()

    @router.post("/consent")
    async def setup_consent(request: Request, body: ConsentBody):
        require_admin(request)
        return await setup.respond(body.approve, body.kind, body.name)

    @router.get("/report", response_class=PlainTextResponse)
    async def setup_report(request: Request):
        require_admin(request)
        return setup.report()

    return router
