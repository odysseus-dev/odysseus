"""Odysseus proxy routes for Titan VRAM scheduler (:8150)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from titan.scheduler_client import scheduler_request


def setup_scheduler_routes() -> APIRouter:
    router = APIRouter(prefix="/api/titan/scheduler", tags=["titan-scheduler"])

    @router.get("/status")
    async def scheduler_status(request: Request):
        return await scheduler_request("GET", "/v1/status")

    @router.get("/health")
    async def scheduler_health(request: Request):
        return await scheduler_request("GET", "/health")

    @router.post("/ensure-llm")
    async def scheduler_ensure_llm(request: Request, body: dict | None = None):
        from src.auth_helpers import effective_user

        if not effective_user(request):
            raise HTTPException(401, "Authentication required")
        return await scheduler_request("POST", "/v1/external/ensure-llm", body or {})

    @router.get("/external/jobs")
    async def external_jobs_list(request: Request):
        return await scheduler_request("GET", "/v1/external/jobs")

    @router.post("/external/jobs")
    async def external_jobs_submit(request: Request, body: dict):
        from src.auth_helpers import effective_user

        if not effective_user(request):
            raise HTTPException(401, "Authentication required")
        return await scheduler_request("POST", "/v1/external/jobs", body or {})

    @router.get("/external/jobs/{job_id}")
    async def external_job_detail(request: Request, job_id: str):
        return await scheduler_request("GET", f"/v1/external/jobs/{job_id}")

    @router.get("/pipelines")
    async def pipelines_list(request: Request):
        return await scheduler_request("GET", "/v1/pipelines")

    @router.put("/pipelines")
    async def pipelines_replace(request: Request, body: dict):
        from src.auth_helpers import effective_user

        if not effective_user(request):
            raise HTTPException(401, "Authentication required")
        return await scheduler_request("PUT", "/v1/pipelines", body)

    @router.post("/pipelines")
    async def pipelines_create(request: Request, body: dict):
        from src.auth_helpers import effective_user

        if not effective_user(request):
            raise HTTPException(401, "Authentication required")
        return await scheduler_request("POST", "/v1/pipelines", body)

    @router.delete("/pipelines/{name}")
    async def pipelines_delete(request: Request, name: str):
        from src.auth_helpers import effective_user

        if not effective_user(request):
            raise HTTPException(401, "Authentication required")
        return await scheduler_request("DELETE", f"/v1/pipelines/{name}")

    @router.get("/config")
    async def scheduler_config_get(request: Request):
        return await scheduler_request("GET", "/v1/config")

    @router.put("/config")
    async def scheduler_config_put(request: Request, body: dict):
        from src.auth_helpers import effective_user

        if not effective_user(request):
            raise HTTPException(401, "Authentication required")
        return await scheduler_request("PUT", "/v1/config", body)

    @router.get("/llm/models")
    async def scheduler_llm_models(request: Request):
        data = await scheduler_request("GET", "/v1/llm/v1/models")
        return JSONResponse(data, headers={"Cache-Control": "no-store"})

    @router.post("/llm/chat")
    async def scheduler_llm_chat(request: Request, body: dict):
        from src.auth_helpers import effective_user

        if not effective_user(request):
            raise HTTPException(401, "Authentication required")
        return await scheduler_request("POST", "/v1/llm/v1/chat/completions", body)

    return router
