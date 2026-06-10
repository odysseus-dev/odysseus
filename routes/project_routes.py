"""Project routes — CRUD for Projects, session/document membership, and memory synthesis."""

import uuid
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Form, HTTPException, Request

from core.database import (
    SessionLocal,
    Session as DbSession,
    ChatMessage as DbChatMessage,
    Document as DocModel,
)
from src.auth_helpers import get_current_user

logger = logging.getLogger(__name__)


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _project_to_dict(p) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description or "",
        "instructions": p.instructions or "",
        "owner": p.owner,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        "archived": p.archived,
    }


def _get_project_or_404(db, project_id: str, owner: Optional[str]):
    """Load a Project row, enforcing owner scoping. Raises 404 on miss."""
    from core.database import Project as ProjectModel
    q = db.query(ProjectModel).filter(
        ProjectModel.id == project_id,
        ProjectModel.archived == False,  # noqa: E712
    )
    if owner:
        q = q.filter(ProjectModel.owner == owner)
    proj = q.first()
    if not proj:
        raise HTTPException(404, "Project not found")
    return proj


def setup_project_routes(session_manager=None) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["projects"])

    # ── GET /api/projects ──────────────────────────────────────────────── #
    @router.get("/projects")
    def list_projects(request: Request):
        """List the current user's active (non-archived) projects."""
        from core.database import Project as ProjectModel
        owner = get_current_user(request)
        db = SessionLocal()
        try:
            q = db.query(ProjectModel).filter(ProjectModel.archived == False)  # noqa: E712
            if owner:
                q = q.filter(ProjectModel.owner == owner)
            projects = q.order_by(ProjectModel.created_at.desc()).all()
            return [_project_to_dict(p) for p in projects]
        finally:
            db.close()

    # ── POST /api/projects ─────────────────────────────────────────────── #
    @router.post("/projects")
    async def create_project(
        request: Request,
        name: str = Form(...),
        description: str = Form(""),
        instructions: str = Form(""),
    ):
        """Create a new project."""
        from core.database import Project as ProjectModel
        owner = get_current_user(request)
        if not name or not name.strip():
            raise HTTPException(400, "Project name is required")
        now = _utcnow()
        proj = ProjectModel(
            id=str(uuid.uuid4()),
            name=name.strip(),
            description=description.strip(),
            instructions=instructions.strip() or None,
            owner=owner,
            created_at=now,
            updated_at=now,
            archived=False,
        )
        db = SessionLocal()
        try:
            db.add(proj)
            db.commit()
            db.refresh(proj)
            return _project_to_dict(proj)
        finally:
            db.close()

    # ── GET /api/projects/{project_id} ─────────────────────────────────── #
    @router.get("/projects/{project_id}")
    def get_project(request: Request, project_id: str):
        """Get project details, including pinned doc IDs and memories."""
        from core.database import Project as ProjectModel, ProjectDocument, ProjectMemory
        owner = get_current_user(request)
        db = SessionLocal()
        try:
            proj = _get_project_or_404(db, project_id, owner)
            result = _project_to_dict(proj)

            # Pinned document IDs
            pinned = db.query(ProjectDocument).filter(
                ProjectDocument.project_id == project_id
            ).all()
            result["pinned_document_ids"] = [pd.document_id for pd in pinned]

            # Memories (newest first)
            memories = db.query(ProjectMemory).filter(
                ProjectMemory.project_id == project_id
            ).order_by(ProjectMemory.synthesized_at.desc()).all()
            result["memories"] = [
                {
                    "id": m.id,
                    "content": m.content,
                    "synthesized_at": m.synthesized_at.isoformat() if m.synthesized_at else None,
                    "session_count": m.session_count,
                }
                for m in memories
            ]

            return result
        finally:
            db.close()

    # ── PATCH /api/projects/{project_id} ───────────────────────────────── #
    @router.patch("/projects/{project_id}")
    async def update_project(
        request: Request,
        project_id: str,
        name: Optional[str] = Form(None),
        description: Optional[str] = Form(None),
        instructions: Optional[str] = Form(None),
    ):
        """Update project name, description, or instructions."""
        owner = get_current_user(request)
        db = SessionLocal()
        try:
            proj = _get_project_or_404(db, project_id, owner)
            if name is not None:
                proj.name = name.strip()
            if description is not None:
                proj.description = description.strip()
            if instructions is not None:
                proj.instructions = instructions.strip() or None
            proj.updated_at = _utcnow()
            db.commit()
            db.refresh(proj)
            return _project_to_dict(proj)
        finally:
            db.close()

    # ── DELETE /api/projects/{project_id} ──────────────────────────────── #
    @router.delete("/projects/{project_id}")
    def delete_project(request: Request, project_id: str):
        """Soft-delete a project (sets archived=True)."""
        owner = get_current_user(request)
        db = SessionLocal()
        try:
            proj = _get_project_or_404(db, project_id, owner)
            proj.archived = True
            proj.updated_at = _utcnow()
            db.commit()
            return {"ok": True, "id": project_id}
        finally:
            db.close()

    # ── GET /api/projects/{project_id}/sessions ────────────────────────── #
    @router.get("/projects/{project_id}/sessions")
    def list_project_sessions(request: Request, project_id: str):
        """List sessions that belong to this project."""
        owner = get_current_user(request)
        db = SessionLocal()
        try:
            _get_project_or_404(db, project_id, owner)
            q = db.query(DbSession).filter(
                DbSession.project_id == project_id,
                DbSession.archived == False,  # noqa: E712
            )
            if owner:
                q = q.filter(DbSession.owner == owner)
            sess_list = q.order_by(DbSession.last_accessed.desc()).all()
            return [s.to_dict() for s in sess_list]
        finally:
            db.close()

    # ── POST /api/projects/{project_id}/sessions/{session_id} ─────────── #
    @router.post("/projects/{project_id}/sessions/{session_id}")
    def add_session_to_project(request: Request, project_id: str, session_id: str):
        """Assign a session to this project."""
        owner = get_current_user(request)
        db = SessionLocal()
        try:
            _get_project_or_404(db, project_id, owner)
            q = db.query(DbSession).filter(DbSession.id == session_id)
            if owner:
                q = q.filter(DbSession.owner == owner)
            sess = q.first()
            if not sess:
                raise HTTPException(404, "Session not found")
            sess.project_id = project_id
            db.commit()
            return {"ok": True, "session_id": session_id, "project_id": project_id}
        finally:
            db.close()

    # ── DELETE /api/projects/{project_id}/sessions/{session_id} ───────── #
    @router.delete("/projects/{project_id}/sessions/{session_id}")
    def remove_session_from_project(request: Request, project_id: str, session_id: str):
        """Remove a session from this project (sets project_id=None)."""
        owner = get_current_user(request)
        db = SessionLocal()
        try:
            _get_project_or_404(db, project_id, owner)
            q = db.query(DbSession).filter(
                DbSession.id == session_id,
                DbSession.project_id == project_id,
            )
            if owner:
                q = q.filter(DbSession.owner == owner)
            sess = q.first()
            if not sess:
                raise HTTPException(404, "Session not found in project")
            sess.project_id = None
            db.commit()
            return {"ok": True, "session_id": session_id}
        finally:
            db.close()

    # ── GET /api/projects/{project_id}/documents ───────────────────────── #
    @router.get("/projects/{project_id}/documents")
    def list_project_documents(request: Request, project_id: str):
        """List documents pinned to this project."""
        from core.database import ProjectDocument
        owner = get_current_user(request)
        db = SessionLocal()
        try:
            _get_project_or_404(db, project_id, owner)
            pinned = db.query(ProjectDocument).filter(
                ProjectDocument.project_id == project_id
            ).all()
            doc_ids = [pd.document_id for pd in pinned]
            docs = db.query(DocModel).filter(DocModel.id.in_(doc_ids)).all()
            return [
                {
                    "id": d.id,
                    "title": d.title,
                    "language": d.language,
                    "current_content": d.current_content,
                    "created_at": d.created_at.isoformat() if d.created_at else None,
                }
                for d in docs
            ]
        finally:
            db.close()

    # ── POST /api/projects/{project_id}/documents/{document_id} ───────── #
    @router.post("/projects/{project_id}/documents/{document_id}")
    def pin_document(request: Request, project_id: str, document_id: str):
        """Pin a document to this project."""
        from core.database import ProjectDocument
        owner = get_current_user(request)
        db = SessionLocal()
        try:
            _get_project_or_404(db, project_id, owner)
            # Verify doc belongs to owner
            q = db.query(DocModel).filter(DocModel.id == document_id)
            if owner:
                q = q.filter(DocModel.owner == owner)
            doc = q.first()
            if not doc:
                raise HTTPException(404, "Document not found")
            # Upsert
            existing = db.query(ProjectDocument).filter(
                ProjectDocument.project_id == project_id,
                ProjectDocument.document_id == document_id,
            ).first()
            if not existing:
                pd = ProjectDocument(
                    project_id=project_id,
                    document_id=document_id,
                    pinned_at=_utcnow(),
                )
                db.add(pd)
                db.commit()
            return {"ok": True, "project_id": project_id, "document_id": document_id}
        finally:
            db.close()

    # ── DELETE /api/projects/{project_id}/documents/{document_id} ─────── #
    @router.delete("/projects/{project_id}/documents/{document_id}")
    def unpin_document(request: Request, project_id: str, document_id: str):
        """Unpin a document from this project."""
        from core.database import ProjectDocument
        owner = get_current_user(request)
        db = SessionLocal()
        try:
            _get_project_or_404(db, project_id, owner)
            pd = db.query(ProjectDocument).filter(
                ProjectDocument.project_id == project_id,
                ProjectDocument.document_id == document_id,
            ).first()
            if pd:
                db.delete(pd)
                db.commit()
            return {"ok": True}
        finally:
            db.close()

    # ── POST /api/projects/{project_id}/synthesize ─────────────────────── #
    @router.post("/projects/{project_id}/synthesize")
    async def synthesize_memories(request: Request, project_id: str):
        """Synthesize cross-session insights for the project using the LLM."""
        from core.database import ProjectMemory
        owner = get_current_user(request)
        db = SessionLocal()
        try:
            proj = _get_project_or_404(db, project_id, owner)

            # Gather sessions in this project
            q = db.query(DbSession).filter(
                DbSession.project_id == project_id,
                DbSession.archived == False,  # noqa: E712
            )
            if owner:
                q = q.filter(DbSession.owner == owner)
            sess_list = q.all()

            if not sess_list:
                raise HTTPException(400, "No sessions in project to synthesize from")

            # Gather last 20 messages per session
            session_summaries = []
            for s in sess_list:
                msgs = (
                    db.query(DbChatMessage)
                    .filter(DbChatMessage.session_id == s.id)
                    .order_by(DbChatMessage.timestamp.desc())
                    .limit(20)
                    .all()
                )
                msgs = list(reversed(msgs))
                if not msgs:
                    continue
                lines = []
                for m in msgs:
                    role = m.role or "user"
                    content = m.content or ""
                    if isinstance(content, list):
                        content = " ".join(
                            c.get("text", "") for c in content
                            if isinstance(c, dict) and c.get("type") == "text"
                        )
                    lines.append(f"{role}: {str(content)[:300]}")
                session_summaries.append(
                    f"--- Session: {s.name or s.id} ---\n" + "\n".join(lines)
                )

            if not session_summaries:
                raise HTTPException(400, "No messages found in project sessions")

            n = len(sess_list)
            combined = "\n\n".join(session_summaries)[:12000]

            synthesis_prompt = (
                f"You are analyzing {n} conversation session(s) in project '{proj.name}'. "
                "Extract key facts, decisions, preferences, recurring topics, and insights "
                "that would be useful context for future conversations in this project. "
                "Return a concise bulleted list. Focus on what is genuinely useful, not what is obvious."
            )

            # Resolve a utility endpoint
            try:
                from src.endpoint_resolver import resolve_endpoint
                ep_url, ep_model, ep_headers = resolve_endpoint("utility", owner=owner)
            except Exception:
                ep_url, ep_model, ep_headers = None, None, None

            if not ep_url or not ep_model:
                raise HTTPException(503, "No LLM endpoint configured for synthesis")

            from src.llm_core import llm_call_async
            result_text = await llm_call_async(
                ep_url,
                ep_model,
                [
                    {"role": "system", "content": synthesis_prompt},
                    {"role": "user", "content": combined},
                ],
                temperature=0.3,
                max_tokens=1024,
                headers=ep_headers,
                timeout=120,
            )

            memory = ProjectMemory(
                id=str(uuid.uuid4()),
                project_id=project_id,
                content=result_text.strip(),
                synthesized_at=_utcnow(),
                session_count=n,
            )
            db.add(memory)
            db.commit()
            db.refresh(memory)

            return {
                "id": memory.id,
                "project_id": memory.project_id,
                "content": memory.content,
                "synthesized_at": memory.synthesized_at.isoformat() if memory.synthesized_at else None,
                "session_count": memory.session_count,
            }
        finally:
            db.close()

    # ── GET /api/projects/{project_id}/memories ────────────────────────── #
    @router.get("/projects/{project_id}/memories")
    def list_memories(request: Request, project_id: str):
        """List synthesized memories for a project, newest first."""
        from core.database import ProjectMemory
        owner = get_current_user(request)
        db = SessionLocal()
        try:
            _get_project_or_404(db, project_id, owner)
            memories = (
                db.query(ProjectMemory)
                .filter(ProjectMemory.project_id == project_id)
                .order_by(ProjectMemory.synthesized_at.desc())
                .all()
            )
            return [
                {
                    "id": m.id,
                    "project_id": m.project_id,
                    "content": m.content,
                    "synthesized_at": m.synthesized_at.isoformat() if m.synthesized_at else None,
                    "session_count": m.session_count,
                }
                for m in memories
            ]
        finally:
            db.close()

    return router
