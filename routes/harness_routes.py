"""Explicit rewind of files the agent changed."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from src.auth_helpers import effective_user
from src.harness_checkpoint import rewind_latest


def setup_harness_routes() -> APIRouter:
    router = APIRouter(prefix="/api/harness", tags=["harness"])

    @router.post("/rewind")
    async def rewind(request: Request):
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON object required")
        session_id = str(body.get("session_id") or "").strip()
        if not session_id:
            raise HTTPException(400, "session_id required")
        owner = effective_user(request) or ""
        return rewind_latest(owner, session_id)

    return router
