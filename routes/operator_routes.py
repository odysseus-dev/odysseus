"""Operator routes — capability health + SpecTracer ingest for the agentic operator."""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from services.operator.core import get_operator_status
from services.operator.tracer import MAX_BUNDLE_BYTES, store_trace
from src.auth_helpers import require_authenticated_request

logger = logging.getLogger(__name__)


def setup_operator_routes() -> APIRouter:
    router = APIRouter()

    @router.get("/api/operator/status")
    def operator_status(request: Request, refresh: bool = False) -> Dict[str, Any]:
        require_authenticated_request(request)
        return get_operator_status(force=refresh)

    @router.post("/api/operator/spec-trace")
    async def spec_trace_ingest(request: Request) -> Dict[str, Any]:
        require_authenticated_request(request)

        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > MAX_BUNDLE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Bundle exceeds {MAX_BUNDLE_BYTES // 1024} KB — reduce capture depth",
            )

        try:
            bundle = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Body must be a JSON context bundle")
        if not isinstance(bundle, dict) or not bundle:
            raise HTTPException(status_code=400, detail="Body must be a non-empty JSON object")

        try:
            result = store_trace(bundle)
        except ValueError as exc:
            if str(exc) == "too_large":
                raise HTTPException(
                    status_code=413,
                    detail=f"Bundle exceeds {MAX_BUNDLE_BYTES // 1024} KB — reduce capture depth",
                )
            raise HTTPException(status_code=400, detail=str(exc))

        return {"ok": True, "trace_id": result["trace_id"]}

    return router
