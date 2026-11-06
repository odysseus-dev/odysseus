import uuid
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from sqlalchemy import func

from core.database import Project, Session as DbSession, ChatMessage as DbChatMessage
from core.database import SessionLocal, utcnow_naive
from src.auth_helpers import effective_user, owner_filter
from src.request_models import ProjectResponse


def _project_query(db, user: str | None, *, include_archived: bool = False):
    q = db.query(Project)
    q = owner_filter(q, Project, user)
    if not include_archived:
        q = q.filter(Project.archived == False)  # noqa: E712
    return q


def _get_project_or_404(db, project_id: str, user: str | None, *, include_archived: bool = False) -> Project:
    q = _project_query(db, user, include_archived=include_archived).filter(Project.id == project_id)
    project = q.first()
    if project is None:
        raise HTTPException(404, "Project not found")
    return project


def _verify_session_for_project(db, session_id: str, user: str | None) -> DbSession:
    q = db.query(DbSession).filter(DbSession.id == session_id)
    q = owner_filter(q, DbSession, user)
    session = q.first()
    if session is None:
        raise HTTPException(404, "Session not found")
    return session


def _project_session_query(db, project_id: str, user: str | None):
    query = db.query(DbSession).filter(
        DbSession.project_id == project_id,
        DbSession.archived == False,  # noqa: E712
    )
    return owner_filter(query, DbSession, user)


def _project_payload(
    db,
    project: Project,
    user: str | None,
    *,
    include_sessions: bool = False,
) -> dict[str, Any]:
    activity_order = func.coalesce(
        DbSession.last_message_at,
        DbSession.updated_at,
        DbSession.created_at,
    ).desc()
    session_q = _project_session_query(db, project.id, user)
    session_count = session_q.count()
    payload = project.to_dict()
    payload["session_count"] = session_count
    if include_sessions:
        payload["sessions"] = [
            {
                "id": s.id,
                "name": s.name,
                "model": s.model,
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                "last_message_at": s.last_message_at.isoformat() if s.last_message_at else None,
                "message_count": s.message_count or 0,
            }
            for s in session_q.order_by(activity_order).all()
        ]
    return payload


def setup_project_routes(session_manager=None) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["projects"])

    @router.get("/projects")
    def list_projects(request: Request, include_archived: bool = False) -> list[ProjectResponse]:
        user = effective_user(request)
        db = SessionLocal()
        try:
            projects = (
                _project_query(db, user, include_archived=include_archived)
                .order_by(Project.is_pinned.desc(), Project.updated_at.desc())
                .all()
            )
            return [_project_payload(db, p, user) for p in projects]
        finally:
            db.close()

    @router.post("/projects", response_model=ProjectResponse)
    def create_project(
        request: Request,
        name: str = Form(""),
        description: str = Form(""),
        instructions: str = Form(""),
    ):
        user = effective_user(request)
        clean_name = (name or "").strip()
        if not clean_name:
            raise HTTPException(400, "Project name is required")
        db = SessionLocal()
        try:
            project = Project(
                id=str(uuid.uuid4()),
                owner=user,
                name=clean_name[:200],
                description=(description or "").strip(),
                instructions=(instructions or "").strip(),
                brief="",
                archived=False,
                is_pinned=False,
                created_at=utcnow_naive(),
                updated_at=utcnow_naive(),
            )
            db.add(project)
            db.commit()
            db.refresh(project)
            return _project_payload(db, project, user)
        except HTTPException:
            raise
        except Exception as exc:
            db.rollback()
            raise HTTPException(500, "Failed to create project") from exc
        finally:
            db.close()

    @router.get("/projects/{project_id}")
    def get_project(request: Request, project_id: str) -> dict[str, Any]:
        user = effective_user(request)
        db = SessionLocal()
        try:
            project = _get_project_or_404(db, project_id, user, include_archived=True)
            return _project_payload(db, project, user, include_sessions=True)
        finally:
            db.close()

    @router.patch("/projects/{project_id}", response_model=ProjectResponse)
    def update_project(
        request: Request,
        project_id: str,
        name: str = Form(None),
        description: str = Form(None),
        instructions: str = Form(None),
        brief: str = Form(None),
        archived: str = Form(None),
        is_pinned: str = Form(None),
    ):
        user = effective_user(request)
        db = SessionLocal()
        try:
            project = _get_project_or_404(db, project_id, user, include_archived=True)
            if name is not None:
                clean_name = (name or "").strip()
                if not clean_name:
                    raise HTTPException(400, "Project name is required")
                project.name = clean_name[:200]
            if description is not None:
                project.description = (description or "").strip()
            if instructions is not None:
                project.instructions = (instructions or "").strip()
            if brief is not None:
                project.brief = (brief or "").strip()
            if archived is not None:
                project.archived = str(archived).lower() == "true"
            if is_pinned is not None:
                project.is_pinned = str(is_pinned).lower() == "true"
            project.updated_at = utcnow_naive()
            db.commit()
            db.refresh(project)
            return _project_payload(db, project, user)
        except HTTPException:
            raise
        except Exception as exc:
            db.rollback()
            raise HTTPException(500, "Failed to update project") from exc
        finally:
            db.close()

    @router.delete("/projects/{project_id}")
    def archive_project(request: Request, project_id: str) -> dict[str, Any]:
        user = effective_user(request)
        db = SessionLocal()
        try:
            project = _get_project_or_404(db, project_id, user, include_archived=True)
            project.archived = True
            project.updated_at = utcnow_naive()
            db.commit()
            return {"status": "ok", "archived": True}
        finally:
            db.close()

    @router.post("/projects/{project_id}/restore", response_model=ProjectResponse)
    def restore_project(request: Request, project_id: str):
        user = effective_user(request)
        db = SessionLocal()
        try:
            project = _get_project_or_404(db, project_id, user, include_archived=True)
            project.archived = False
            project.updated_at = utcnow_naive()
            db.commit()
            db.refresh(project)
            return _project_payload(db, project, user)
        finally:
            db.close()

    @router.post("/projects/{project_id}/sessions/{session_id}")
    def assign_session_to_project(request: Request, project_id: str, session_id: str) -> dict[str, Any]:
        user = effective_user(request)
        db = SessionLocal()
        try:
            project = _get_project_or_404(db, project_id, user)
            session = _verify_session_for_project(db, session_id, user)
            session.project_id = project.id
            session.updated_at = utcnow_naive()
            project.updated_at = utcnow_naive()
            db.commit()
            if session_manager is not None and session_id in getattr(session_manager, "sessions", {}):
                session_manager.sessions[session_id].project_id = project.id
            return {"status": "ok", "session_id": session_id, "project_id": project.id}
        except HTTPException:
            raise
        except Exception as exc:
            db.rollback()
            raise HTTPException(500, "Failed to assign session to project") from exc
        finally:
            db.close()

    @router.delete("/projects/{project_id}/sessions/{session_id}")
    def remove_session_from_project(request: Request, project_id: str, session_id: str) -> dict[str, Any]:
        user = effective_user(request)
        db = SessionLocal()
        try:
            _get_project_or_404(db, project_id, user, include_archived=True)
            session = _verify_session_for_project(db, session_id, user)
            if session.project_id != project_id:
                return {"status": "ok", "session_id": session_id, "project_id": None}
            session.project_id = None
            session.updated_at = utcnow_naive()
            db.commit()
            if session_manager is not None and session_id in getattr(session_manager, "sessions", {}):
                session_manager.sessions[session_id].project_id = None
            return {"status": "ok", "session_id": session_id, "project_id": None}
        except HTTPException:
            raise
        except Exception as exc:
            db.rollback()
            raise HTTPException(500, "Failed to remove session from project") from exc
        finally:
            db.close()

    @router.post("/projects/{project_id}/brief/refresh", response_model=ProjectResponse)
    def refresh_project_brief(request: Request, project_id: str):
        user = effective_user(request)
        db = SessionLocal()
        try:
            project = _get_project_or_404(db, project_id, user)
            session_rows = (
                _project_session_query(db, project.id, user)
                .order_by(func.coalesce(
                    DbSession.last_message_at,
                    DbSession.updated_at,
                    DbSession.created_at,
                ).desc())
                .limit(20)
                .all()
            )
            if not session_rows:
                raise HTTPException(400, "Project has no chats to summarize")

            session_ids = [s.id for s in session_rows]
            message_rows = (
                db.query(DbChatMessage, DbSession.name)
                .join(DbSession, DbChatMessage.session_id == DbSession.id)
                .filter(
                    DbChatMessage.session_id.in_(session_ids),
                    DbChatMessage.role.in_(("user", "assistant")),
                )
                .order_by(DbChatMessage.timestamp.desc())
                .limit(80)
                .all()
            )
            transcript = "\n".join(
                f"[{session_name or 'Untitled'}] {msg.role.upper()}: {(msg.content or '')[:1500]}"
                for msg, session_name in reversed(message_rows)
            )
            if not transcript.strip():
                raise HTTPException(400, "Project has no chat text to summarize")

            from src.endpoint_resolver import resolve_endpoint
            from src.llm_core import llm_call

            url, model, headers = resolve_endpoint("utility", owner=user)
            if not url or not model:
                fallback = next((s for s in session_rows if s.endpoint_url and s.model), None)
                if fallback is not None:
                    url, model, headers = fallback.endpoint_url, fallback.model, fallback.headers or {}
            if not url or not model:
                raise HTTPException(503, "No model configured for project brief refresh")

            prompt = (
                "Create a concise rolling project brief from these related chats. "
                "Capture established decisions, unresolved questions, current tasks, "
                "important terminology, and files or artifacts mentioned. "
                "Write compact prose and do not invent facts.\n\n"
                f"Project: {project.name}\n"
                f"Description: {project.description or ''}\n\n"
                f"Chats:\n{transcript}"
            )
            brief = llm_call(
                url,
                model,
                [{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=1200,
                headers=headers,
                timeout=90,
            ).strip()
            project.brief = brief
            project.updated_at = utcnow_naive()
            db.commit()
            db.refresh(project)
            return _project_payload(db, project, user)
        except HTTPException:
            raise
        except Exception as exc:
            db.rollback()
            raise HTTPException(500, "Failed to refresh project brief") from exc
        finally:
            db.close()

    return router
