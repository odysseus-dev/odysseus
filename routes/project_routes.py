"""Project routes — CRUD, settings, sessions, resources, memory.

All routes are owner-scoped. The feature gate (`FEATURES.projects_enabled`)
returns 404 when the flag is OFF. Destructive endpoints require explicit
confirmation (X-Confirm-Name) per spec §7.
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from src.auth_helpers import effective_user
from src.settings import load_features
from services.project import get_project_service
from services.project.service import (
    ProjectLimitReached,
    ProjectNameConflict,
    ProjectNotFound,
)


def _features_enabled() -> bool:
    return bool(load_features().get("projects_enabled", False))


def _owner(request: Request) -> str:
    """Resolve the owner. Reads from request state in production; accepts
    X-Owner header in tests so the FastAPI TestClient doesn't need auth.

    Note: we read the header directly via ``request.headers.get`` instead
    of binding it as a FastAPI ``Header`` dependency — calling this as
    a plain function (which the route handlers do) would otherwise pass
    the literal ``Header(None)`` sentinel object into SQL queries."""
    real = effective_user(request)
    if real:
        return real
    header_val = request.headers.get("x-owner")
    if header_val:
        return header_val
    return ""


def setup_project_routes(app, project_service=None, memory_service=None) -> APIRouter:
    svc = project_service or get_project_service()
    router = APIRouter(prefix="/api/projects", tags=["projects"])

    # ──────────────────────────────── CRUD ────────────────────────────────

    @router.get("")
    def list_projects(request: Request):
        if not _features_enabled():
            raise HTTPException(404)
        owner = _owner(request)
        return [p.__dict__ for p in svc.list_for_owner(owner)]

    @router.post("")
    def create_project(request: Request, body: dict):
        if not _features_enabled():
            raise HTTPException(404)
        owner = _owner(request)
        if not owner:
            raise HTTPException(401, "owner required")
        try:
            proj = svc.create(
                owner=owner,
                name=body["name"],
                icon=body.get("icon"),
                description=body.get("description"),
                memory_mode=body.get("memory_mode", "isolated"),
            )
        except ProjectNameConflict as e:
            raise HTTPException(409, {"error": "duplicate_name", "name": str(e)})
        except ProjectLimitReached as e:
            raise HTTPException(409, {"error": "limit_reached",
                                       "current": e.current, "max": e.maximum})
        except ValueError as e:
            raise HTTPException(422, {"error": "validation", "detail": str(e)})
        return proj.__dict__

    @router.get("/{pid}")
    def get_project(request: Request, pid: str):
        if not _features_enabled():
            raise HTTPException(404)
        try:
            return svc.get(pid, _owner(request)).__dict__
        except ProjectNotFound:
            raise HTTPException(404, {"error": "project_not_found"})

    @router.patch("/{pid}")
    def patch_project(request: Request, pid: str, body: dict):
        if not _features_enabled():
            raise HTTPException(404)
        try:
            return svc.update_settings(pid, _owner(request), **body).__dict__
        except ProjectNotFound:
            raise HTTPException(404, {"error": "project_not_found"})
        except ValueError as e:
            raise HTTPException(422, {"error": "validation", "detail": str(e)})

    @router.delete("/{pid}")
    def delete_project(
        request: Request, pid: str,
        x_confirm_name: Optional[str] = Header(default=None, alias="X-Confirm-Name"),
    ):
        if not _features_enabled():
            raise HTTPException(404)
        try:
            proj = svc.get(pid, _owner(request))
        except ProjectNotFound:
            raise HTTPException(404, {"error": "project_not_found"})
        if (x_confirm_name or "").strip().lower() != proj.name.strip().lower():
            raise HTTPException(400, {"error": "name_mismatch"})
        svc.delete(pid, _owner(request))
        return {"ok": True}

    # ──────────────────────────────── Settings ─────────────────────────────

    MAX_PROMPT_CHARS = 4000
    MAX_INSTRUCTIONS_CHARS = 2000

    @router.get("/{pid}/settings")
    def get_settings(request: Request, pid: str):
        if not _features_enabled():
            raise HTTPException(404)
        try:
            proj = svc.get(pid, _owner(request))
        except ProjectNotFound:
            raise HTTPException(404, {"error": "project_not_found"})
        return {
            "custom_prompt": proj.custom_prompt,
            "custom_instructions": proj.custom_instructions,
            "prompt_override_mode": proj.prompt_override_mode,
            "instructions_override_mode": proj.instructions_override_mode,
            "memory_mode": proj.memory_mode,
            "snapshot_meta": proj.snapshot_meta,
        }

    @router.put("/{pid}/settings")
    def put_settings(request: Request, pid: str, body: dict):
        if not _features_enabled():
            raise HTTPException(404)
        owner = _owner(request)
        if "custom_prompt" in body and body["custom_prompt"] is not None:
            if len(body["custom_prompt"]) > MAX_PROMPT_CHARS:
                raise HTTPException(422, {
                    "error": "field_too_long",
                    "field": "custom_prompt",
                    "max": MAX_PROMPT_CHARS,
                })
        if "custom_instructions" in body and body["custom_instructions"] is not None:
            if len(body["custom_instructions"]) > MAX_INSTRUCTIONS_CHARS:
                raise HTTPException(422, {
                    "error": "field_too_long",
                    "field": "custom_instructions",
                    "max": MAX_INSTRUCTIONS_CHARS,
                })
        # memory_mode is intentionally NOT settable after creation.
        body.pop("memory_mode", None)
        try:
            proj = svc.update_settings(pid, owner, **body)
        except ProjectNotFound:
            raise HTTPException(404, {"error": "project_not_found"})
        except ValueError as e:
            raise HTTPException(422, {"error": "validation", "detail": str(e)})
        return {
            "custom_prompt": proj.custom_prompt,
            "custom_instructions": proj.custom_instructions,
            "prompt_override_mode": proj.prompt_override_mode,
            "instructions_override_mode": proj.instructions_override_mode,
            "memory_mode": proj.memory_mode,
            "snapshot_meta": proj.snapshot_meta,
        }

    # ───────────────────────────── Sessions (T20) ─────────────────────────────

    from sqlalchemy import select
    from core.database import Session as DbSession, SessionLocal

    def _load_session(sid: str, pid: str, owner: str):
        with SessionLocal() as db:
            row = db.execute(
                select(DbSession).where(DbSession.id == sid, DbSession.owner == owner)
            ).scalar_one_or_none()
        if row is None or row.project_id != pid:
            raise HTTPException(404, {"error": "session_not_found"})
        return row

    @router.get("/{pid}/sessions")
    def list_sessions(request: Request, pid: str):
        if not _features_enabled():
            raise HTTPException(404)
        try:
            svc.get(pid, _owner(request))
        except ProjectNotFound:
            raise HTTPException(404, {"error": "project_not_found"})
        with SessionLocal() as db:
            rows = db.execute(
                select(DbSession).where(
                    DbSession.project_id == pid,
                    DbSession.owner == _owner(request),
                )
            ).scalars().all()
        return [r.to_dict() for r in rows]

    @router.post("/{pid}/sessions")
    def create_session(request: Request, pid: str, body: dict):
        if not _features_enabled():
            raise HTTPException(404)
        try:
            proj = svc.get(pid, _owner(request))
        except ProjectNotFound:
            raise HTTPException(404, {"error": "project_not_found"})
        # Insert a Session row with project_id pinned. The rest of the
        # session columns match the existing shape (see routes/session_routes.py).
        import uuid
        sid = body.get("id") or f"sess_{uuid.uuid4().hex[:12]}"
        row = DbSession(
            id=sid,
            name=body.get("name", "New chat"),
            endpoint_url=body.get("endpoint_url", ""),
            model=body.get("model", ""),
            owner=_owner(request),
            project_id=pid,
            rag=bool(body.get("rag", False)),
            headers=body.get("headers", {}),
            message_count=0,
            total_input_tokens=0,
            total_output_tokens=0,
        )
        # SQLAlchemy TimestampMixin defaults fill created_at/updated_at.
        with SessionLocal() as db:
            db.add(row)
            db.commit()
            db.refresh(row)
        return row.to_dict()

    @router.get("/{pid}/sessions/{sid}")
    def get_session(request: Request, pid: str, sid: str):
        if not _features_enabled():
            raise HTTPException(404)
        return _load_session(sid, pid, _owner(request)).to_dict()

    @router.delete("/{pid}/sessions/{sid}")
    def delete_session(request: Request, pid: str, sid: str):
        if not _features_enabled():
            raise HTTPException(404)
        row = _load_session(sid, pid, _owner(request))
        with SessionLocal() as db:
            db.delete(row)
            db.commit()
        return {"ok": True}

    @router.patch("/{pid}/sessions/{sid}")
    def patch_session(request: Request, pid: str, sid: str, body: dict):
        if not _features_enabled():
            raise HTTPException(404)
        row = _load_session(sid, pid, _owner(request))
        for k in ("name", "endpoint_url", "model", "rag", "headers"):
            if k in body:
                setattr(row, k, body[k])
        with SessionLocal() as db:
            db.merge(row)
            db.commit()
        return row.to_dict()

    # ───────────────────────────── Messages (T21) ─────────────────────────────

    @router.post("/{pid}/sessions/{sid}/messages")
    async def post_message(
        request: Request, pid: str, sid: str, body: dict,
    ):
        if not _features_enabled():
            raise HTTPException(404)
        row = _load_session(sid, pid, _owner(request))
        global_ms = getattr(request.app.state, "memory_service", None)
        ctx = svc.open_context(pid, _owner(request), global_memory_service=global_ms)
        # Forward the project_ctx so the chat pipeline can swap memory_service + RAG.
        body = {"project_ctx": ctx, **body}
        pipeline = getattr(request.app.state, "project_chat_pipeline", None)
        if pipeline is None:
            raise HTTPException(501, {"error": "chat_pipeline_unavailable"})
        return await pipeline(row, body, ctx=ctx)

    app.include_router(router)
    return router
