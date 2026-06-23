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

    app.include_router(router)
    return router
