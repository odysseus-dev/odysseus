# routes/project_routes.py
"""Projects API — group chat sessions into named projects with persistent tracking.

Projects are user-owned, and sessions are assigned to a project via the
session's ``folder`` field prefixed with ``[project/<slug>]``.
"""

import uuid
import logging
from typing import Dict, Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.database import SessionLocal, Project, Session as DbSession, utcnow_naive
from src.auth_helpers import effective_user, owner_filter, require_user

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ProjectCreate(BaseModel):
    name: str                          # human-readable name
    description: Optional[str] = ""


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class ProjectAssign(BaseModel):
    """Assign (or remove) a session from a project."""
    session_id: str
    project_id: Optional[str] = None   # None = remove from project


class ProjectSessionInfo(BaseModel):
    """Minimal session info returned in project detail."""
    id: str
    name: str
    model: str
    updated_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(name: str) -> str:
    """Turn a display name into a kebab-case slug."""
    import re
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug[:64] or str(uuid.uuid4())[:8]


def _project_to_dict(p: Project) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        q = db.query(DbSession).filter(DbSession.folder == f"project:{p.slug}")
        sessions = q.all()
        session_count = len(sessions)
    finally:
        db.close()
    return {
        "id": p.id,
        "name": p.name,
        "slug": p.slug,
        "description": p.description or "",
        "session_count": session_count,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def _session_to_project_info(s: DbSession) -> Dict[str, Any]:
    return {
        "id": s.id,
        "name": s.name,
        "model": s.model,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


# ---------------------------------------------------------------------------
# Route setup
# ---------------------------------------------------------------------------

def setup_project_routes():
    router = APIRouter(prefix="/api/projects", tags=["projects"])

    @router.get("/")
    def list_projects(request: Request):
        """List all projects for the current user."""
        user = effective_user(request)
        db = SessionLocal()
        try:
            q = db.query(Project)
            q = owner_filter(q, Project, user, include_shared=False)
            projects = q.order_by(Project.updated_at.desc()).all()
        finally:
            db.close()
        return [_project_to_dict(p) for p in projects]

    @router.post("/", response_model=dict)
    def create_project(request: Request, body: ProjectCreate):
        """Create a new project."""
        user = effective_user(request)
        slug = _slugify(body.name)
        db = SessionLocal()
        try:
            # Check for duplicate slug among this user's projects
            dup = owner_filter(db.query(Project), Project, user, include_shared=False).filter(
                Project.slug == slug
            ).first()
            if dup:
                # Append a number to make it unique
                slug = f"{slug}-{uuid.uuid4().hex[:4]}"
            p = Project(
                id=uuid.uuid4().hex,
                owner=user,
                name=body.name,
                slug=slug,
                description=body.description,
            )
            db.add(p)
            db.commit()
            db.refresh(p)
        except Exception:
            db.rollback()
            raise HTTPException(400, "Failed to create project")
        finally:
            db.close()
        return _project_to_dict(p)

    @router.patch("/{project_id}", response_model=dict)
    def update_project(request: Request, project_id: str, body: ProjectUpdate):
        """Update a project's name or description."""
        user = effective_user(request)
        db = SessionLocal()
        try:
            p = owner_filter(db.query(Project), Project, user, include_shared=False).filter(
                Project.id == project_id
            ).first()
            if not p:
                raise HTTPException(404, "Project not found")
            if body.name is not None:
                new_slug = _slugify(body.name)
                if new_slug != p.slug:
                    # Check collision
                    dup = owner_filter(db.query(Project), Project, user, include_shared=False).filter(
                        Project.slug == new_slug
                    ).first()
                    if dup:
                        new_slug = f"{new_slug}-{uuid.uuid4().hex[:4]}"
                    # Update session folders
                    for s in db.query(DbSession).filter(DbSession.folder == f"project:{p.slug}").all():
                        s.folder = f"project:{new_slug}"
                    p.slug = new_slug
                p.name = body.name
            if body.description is not None:
                p.description = body.description
            p.updated_at = utcnow_naive()
            db.commit()
            db.refresh(p)
        except HTTPException:
            raise
        except Exception:
            db.rollback()
            raise HTTPException(400, "Failed to update project")
        finally:
            db.close()
        return _project_to_dict(p)

    @router.delete("/{project_id}")
    def delete_project(request: Request, project_id: str):
        """Delete a project. Associated sessions are unassigned."""
        user = effective_user(request)
        db = SessionLocal()
        try:
            p = owner_filter(db.query(Project), Project, user, include_shared=False).filter(
                Project.id == project_id
            ).first()
            if not p:
                raise HTTPException(404, "Project not found")
            # Unassign sessions
            for s in db.query(DbSession).filter(DbSession.folder == f"project:{p.slug}").all():
                s.folder = None
            db.delete(p)
            db.commit()
        except HTTPException:
            raise
        except Exception:
            db.rollback()
            raise HTTPException(400, "Failed to delete project")
        finally:
            db.close()
        return {"ok": True}

    @router.get("/{project_id}/sessions")
    def get_project_sessions(request: Request, project_id: str):
        """List sessions belonging to a project."""
        user = effective_user(request)
        db = SessionLocal()
        try:
            p = owner_filter(db.query(Project), Project, user, include_shared=False).filter(
                Project.id == project_id
            ).first()
            if not p:
                raise HTTPException(404, "Project not found")
            sessions = owner_filter(
                db.query(DbSession).filter(DbSession.folder == f"project:{p.slug}"),
                DbSession, user
            ).order_by(DbSession.updated_at.desc()).all()
        finally:
            db.close()
        return [_session_to_project_info(s) for s in sessions]

    @router.post("/{project_id}/assign")
    def assign_session(request: Request, project_id: str, body: dict):
        """Assign a session to this project.

        body can be {"session_id": "..."} or a list of session IDs.
        """
        user = effective_user(request)
        db = SessionLocal()
        try:
            p = owner_filter(db.query(Project), Project, user, include_shared=False).filter(
                Project.id == project_id
            ).first()
            if not p:
                raise HTTPException(404, "Project not found")
            session_ids = body.get("session_ids") or [body.get("session_id")]
            if not session_ids:
                raise HTTPException(400, "Provide session_id or session_ids")
            assigned = 0
            for sid in session_ids:
                s = owner_filter(db.query(DbSession), DbSession, user).filter(
                    DbSession.id == sid
                ).first()
                if s:
                    s.folder = f"project:{p.slug}"
                    assigned += 1
            p.updated_at = utcnow_naive()
            db.commit()
        except HTTPException:
            raise
        except Exception:
            db.rollback()
            raise HTTPException(400, "Failed to assign sessions")
        finally:
            db.close()
        return {"ok": True, "assigned": assigned}

    @router.post("/{project_id}/unassign")
    def unassign_session(request: Request, project_id: str, body: dict):
        """Remove sessions from this project."""
        user = effective_user(request)
        db = SessionLocal()
        try:
            p = owner_filter(db.query(Project), Project, user, include_shared=False).filter(
                Project.id == project_id
            ).first()
            if not p:
                raise HTTPException(404, "Project not found")
            session_ids = body.get("session_ids") or [body.get("session_id")]
            if not session_ids:
                raise HTTPException(400, "Provide session_id or session_ids")
            unassigned = 0
            for sid in session_ids:
                s = owner_filter(db.query(DbSession), DbSession, user).filter(
                    DbSession.id == sid, DbSession.folder == f"project:{p.slug}"
                ).first()
                if s:
                    s.folder = None
                    unassigned += 1
            db.commit()
        except HTTPException:
            raise
        except Exception:
            db.rollback()
            raise HTTPException(400, "Failed to unassign sessions")
        finally:
            db.close()
        return {"ok": True, "unassigned": unassigned}

    return router
