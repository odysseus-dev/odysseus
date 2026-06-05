"""Admin-gated Codex CLI model-provider status routes."""

from __future__ import annotations

from fastapi import APIRouter, Request

from core.middleware import require_admin
from src import codex_model_provider


def setup_codex_model_provider_routes() -> APIRouter:
    router = APIRouter(prefix="/api/codex-model-provider", tags=["codex-model-provider"])

    @router.get("/status")
    async def status(request: Request):
        require_admin(request)
        return await codex_model_provider.provider_status()

    return router
