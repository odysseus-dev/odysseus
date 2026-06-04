# routes/codex_runtime_routes.py
"""Admin routes for the Odysseus Codex Runtime."""

from fastapi import APIRouter, Request

from core.middleware import require_admin
from src.codex_runtime import (
    codex_runtime_probe,
    codex_runtime_status,
    ensure_codex_runtime_endpoint_registered,
)


def setup_codex_runtime_routes() -> APIRouter:
    router = APIRouter(prefix="/api/codex-runtime", tags=["codex-runtime"])

    @router.get("/status")
    def status(request: Request):
        require_admin(request)
        return codex_runtime_status()

    @router.post("/reconcile")
    def reconcile(request: Request):
        require_admin(request)
        registration = ensure_codex_runtime_endpoint_registered()
        data = codex_runtime_status()
        data["endpoint_registration"] = registration
        return data

    @router.post("/probe")
    def probe(request: Request):
        require_admin(request)
        return codex_runtime_probe()

    return router
