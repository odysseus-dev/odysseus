"""Local-LLM-Router status API — read-only routing plan for the model picker."""

from fastapi import APIRouter, Query, Request

from src.auth_helpers import get_current_user
from src.local_llm_router_routing import describe_local_llm_router_status


def setup_local_llm_router_routes() -> APIRouter:
    router = APIRouter(prefix="/api/local-llm-router", tags=["local-llm-router"])

    @router.get("/status")
    async def local_llm_router_status(
        request: Request,
        endpoint_url: str = Query(""),
    ):
        owner = get_current_user(request)
        return describe_local_llm_router_status(endpoint_url, owner=owner)

    return router
