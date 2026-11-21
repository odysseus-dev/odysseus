"""HTTP surface for the generic DAG orchestrator."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.auth_helpers import effective_user

from .dag import (
    MAX_ATTACHMENT_BYTES,
    DagError,
    create_run,
    execute_run,
    list_runs,
    load_run,
    save_attachment,
    save_run,
)


class AttachmentSpec(BaseModel):
    id: str = Field(..., pattern=r"^[0-9a-f]{32}$")
    name: str = Field("adjunto", max_length=160)
    bytes: int = Field(0, ge=0, le=MAX_ATTACHMENT_BYTES)


class TaskSpec(BaseModel):
    id: str = Field(..., max_length=80)
    prompt: str = Field(..., max_length=20000)
    depends_on: List[str] = Field(default_factory=list, max_length=50)
    system: str = Field("", max_length=4000)
    model: str = Field("", max_length=200)
    endpoint_url: str = Field("", max_length=500)
    endpoint_id: str = Field("", max_length=120)
    max_tokens: int = Field(800, ge=32, le=8000)
    retries: int = Field(0, ge=0, le=5)
    timeout: int = Field(180, ge=10, le=1800)
    attachments: List[AttachmentSpec] = Field(default_factory=list, max_length=10)
    # Visual-editor position; the executor ignores it.
    x: float = Field(0, ge=0, le=20000)
    y: float = Field(0, ge=0, le=20000)


class AttachmentUpload(BaseModel):
    name: str = Field("adjunto", max_length=160)
    content: str = Field(..., max_length=MAX_ATTACHMENT_BYTES)


class RunRequest(BaseModel):
    name: str = Field("DAG", max_length=120)
    tasks: List[TaskSpec] = Field(..., min_length=1, max_length=50)
    model: str = Field("", max_length=200)
    endpoint_url: str = Field("", max_length=500)
    endpoint_id: str = Field("", max_length=120)
    system: str = Field("", max_length=4000)
    max_parallel: int = Field(2, ge=1, le=8)


_ACTIVE: Dict[str, asyncio.Task] = {}


def _owned(run: Optional[Dict[str, Any]], owner: Optional[str]) -> Optional[Dict[str, Any]]:
    if run is None:
        return None
    if owner and run.get("owner") not in (None, owner):
        return None
    return run


def setup_orchestrator_routes():
    router = APIRouter(prefix="/api/orchestrator", tags=["orchestrator"])

    @router.post("/attachments")
    async def upload_attachment(payload: AttachmentUpload, request: Request):
        owner = effective_user(request)
        if len(payload.content.encode("utf-8")) > MAX_ATTACHMENT_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"El adjunto supera {MAX_ATTACHMENT_BYTES // 1000} KB",
            )
        try:
            return save_attachment(payload.name, payload.content, owner)
        except DagError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/run")
    async def start_run(payload: RunRequest, request: Request):
        owner = effective_user(request)
        try:
            run = create_run(payload.model_dump(), owner)
        except DagError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        task = asyncio.create_task(execute_run(run))
        _ACTIVE[run["id"]] = task

        def _done(_):
            _ACTIVE.pop(run["id"], None)

        task.add_done_callback(_done)
        return {
            "id": run["id"],
            "name": run["name"],
            "status": run["status"],
            "order": run["order"],
            "max_parallel": run["max_parallel"],
        }

    @router.get("/runs")
    async def runs(request: Request):
        return {"items": list_runs(effective_user(request), limit=100)}

    @router.get("/runs/{run_id}")
    async def run_detail(run_id: str, request: Request):
        run = _owned(load_run(run_id), effective_user(request))
        if run is None:
            raise HTTPException(status_code=404, detail="Run no encontrado")
        return run

    @router.post("/runs/{run_id}/cancel")
    async def cancel_run(run_id: str, request: Request):
        run = _owned(load_run(run_id), effective_user(request))
        if run is None:
            raise HTTPException(status_code=404, detail="Run no encontrado")
        if run.get("status") not in {"running", "pending"}:
            return {"id": run_id, "status": run.get("status"), "cancelled": False}
        run["cancel_requested"] = True
        save_run(run)
        task = _ACTIVE.get(run_id)
        if task is not None:
            task.cancel()
        return {"id": run_id, "status": "cancelling", "cancelled": True}

    @router.get("/runs/{run_id}/events")
    async def run_events(run_id: str, request: Request):
        owner = effective_user(request)
        if _owned(load_run(run_id), owner) is None:
            raise HTTPException(status_code=404, detail="Run no encontrado")

        async def gen():
            last = ""
            while True:
                run = _owned(load_run(run_id), owner)
                if run is None:
                    yield 'data: {"error":"run no encontrado"}\n\n'
                    return
                snapshot = {
                    "id": run.get("id"),
                    "status": run.get("status"),
                    "updated_at": run.get("updated_at"),
                    "tasks": {
                        tid: {
                            "status": t.get("status"),
                            "output": t.get("output"),
                            "error": t.get("error"),
                            "attempts": t.get("attempts"),
                        }
                        for tid, t in (run.get("tasks") or {}).items()
                    },
                }
                encoded = json.dumps(snapshot, ensure_ascii=False)
                if encoded != last:
                    last = encoded
                    yield f"data: {encoded}\n\n"
                if run.get("status") not in {"running", "pending"}:
                    return
                await asyncio.sleep(0.5)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    return router
