"""Shared HTTP client for Titan VRAM scheduler (:8150)."""
from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import HTTPException

SCHEDULER_URL = os.environ.get("TITAN_SCHEDULER_URL", "http://host.docker.internal:8150").rstrip("/")

_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=30.0)


async def scheduler_request(method: str, path: str, json_body: dict | None = None) -> Any:
    url = f"{SCHEDULER_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            response = await client.request(method, url, json=json_body)
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Scheduler unreachable at {SCHEDULER_URL}: {exc}",
        ) from exc
    if response.status_code >= 400:
        try:
            detail = response.json()
        except Exception:
            detail = response.text[:500]
        raise HTTPException(status_code=response.status_code, detail=detail)
    if response.headers.get("content-type", "").startswith("application/json"):
        return response.json()
    return response.text
