"""Operator routes — capability health for the agentic operator."""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Request

from services.operator.core import get_operator_status
from src.auth_helpers import require_authenticated_request

logger = logging.getLogger(__name__)


def setup_operator_routes() -> APIRouter:
    router = APIRouter()

    @router.get("/api/operator/status")
    def operator_status(request: Request, refresh: bool = False) -> Dict[str, Any]:
        require_authenticated_request(request)
        return get_operator_status(force=refresh)

    return router
