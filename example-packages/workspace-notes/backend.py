"""
Workspace Notes — backend.py

Demonstrates both package extension hooks:
  • register_db(engine, SessionLocal, Base)  — adds a custom DB table
  • register_routes(app)                     — registers FastAPI routes for the table

Notes are scoped per workspace_id and owner.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import Column, String, Text, DateTime, Index
from sqlalchemy.orm import Session

# ── DB hook ───────────────────────────────────────────────────────────────────

_SessionLocal = None
_WorkspaceNote = None


def register_db(engine, SessionLocal, Base):
    """
    Called once by PackageManager when this package loads.
    Define your SQLAlchemy models here and call Base.metadata.create_all()
    to ensure the tables exist without needing manual migrations.
    """
    global _SessionLocal, _WorkspaceNote
    _SessionLocal = SessionLocal

    class WorkspaceNote(Base):
        __tablename__ = "workspace_notes"
        __table_args__ = (
            Index("ix_workspace_notes_workspace_id", "workspace_id"),
            Index("ix_workspace_notes_owner", "owner"),
            # Avoid duplicate table registration if the package reloads
            {"extend_existing": True},
        )

        id          = Column(String, primary_key=True)
        workspace_id = Column(String, nullable=False)
        owner       = Column(String, nullable=True)
        title       = Column(String, nullable=False, default="")
        content     = Column(Text, nullable=False, default="")
        created_at  = Column(DateTime, default=datetime.utcnow)
        updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    _WorkspaceNote = WorkspaceNote
    # Create the table if it doesn't exist yet (idempotent)
    Base.metadata.create_all(bind=engine, tables=[WorkspaceNote.__table__])


# ── Routes hook ───────────────────────────────────────────────────────────────

def register_routes(app):
    router = APIRouter(prefix="/api/workspace-notes", tags=["workspace-notes"])

    def _db():
        db = _SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def _user(request: Request) -> Optional[str]:
        from src.auth_helpers import get_current_user
        return get_current_user(request)

    class NoteCreate(BaseModel):
        workspace_id: str
        title: str = ""
        content: str

    class NoteUpdate(BaseModel):
        title: Optional[str] = None
        content: Optional[str] = None

    @router.get("/{workspace_id}")
    def list_notes(workspace_id: str, request: Request, db: Session = Depends(_db)):
        """List all notes for a workspace."""
        if _WorkspaceNote is None:
            raise HTTPException(503, "DB not initialised")
        user = _user(request)
        q = db.query(_WorkspaceNote).filter(_WorkspaceNote.workspace_id == workspace_id)
        if user:
            q = q.filter(_WorkspaceNote.owner == user)
        notes = q.order_by(_WorkspaceNote.updated_at.desc()).all()
        return {"notes": [_note_dict(n) for n in notes]}

    @router.post("")
    def create_note(body: NoteCreate, request: Request, db: Session = Depends(_db)):
        """Create a note in a workspace."""
        if _WorkspaceNote is None:
            raise HTTPException(503, "DB not initialised")
        import uuid
        user = _user(request)
        note = _WorkspaceNote(
            id=str(uuid.uuid4()),
            workspace_id=body.workspace_id,
            owner=user,
            title=body.title,
            content=body.content,
        )
        db.add(note)
        db.commit()
        db.refresh(note)
        return _note_dict(note)

    @router.patch("/{note_id}")
    def update_note(note_id: str, body: NoteUpdate, request: Request, db: Session = Depends(_db)):
        """Update a note's title or content."""
        if _WorkspaceNote is None:
            raise HTTPException(503, "DB not initialised")
        user = _user(request)
        note = db.query(_WorkspaceNote).filter(_WorkspaceNote.id == note_id).first()
        if not note:
            raise HTTPException(404, "Note not found")
        if user and note.owner != user:
            raise HTTPException(403, "Not your note")
        if body.title is not None:
            note.title = body.title
        if body.content is not None:
            note.content = body.content
        note.updated_at = datetime.utcnow()
        db.commit()
        return _note_dict(note)

    @router.delete("/{note_id}")
    def delete_note(note_id: str, request: Request, db: Session = Depends(_db)):
        """Delete a note."""
        if _WorkspaceNote is None:
            raise HTTPException(503, "DB not initialised")
        user = _user(request)
        note = db.query(_WorkspaceNote).filter(_WorkspaceNote.id == note_id).first()
        if not note:
            raise HTTPException(404, "Note not found")
        if user and note.owner != user:
            raise HTTPException(403, "Not your note")
        db.delete(note)
        db.commit()
        return {"ok": True}

    app.include_router(router)


# ── Config hook ───────────────────────────────────────────────────────────────

def register_config():
    """Declare default config values — stored once per package load if not already set."""
    return {
        "auto_note_on_workspace_create": False,
        "default_note_title": "Getting Started",
    }


# ── Event hooks ───────────────────────────────────────────────────────────────

def on_startup():
    import logging
    logging.getLogger(__name__).info("[workspace-notes] loaded and active")


def on_workspace_created(ws_id: str, name: str, owner: str):
    """Auto-create a welcome note when a workspace is created (if configured)."""
    if _WorkspaceNote is None or _SessionLocal is None:
        return
    try:
        from src.package_manager import get_manager
        pm = get_manager()
        if not pm:
            return
        cfg = pm.get_config("workspace-notes", owner=owner or "")
        if not cfg.get("auto_note_on_workspace_create"):
            return
        title = cfg.get("default_note_title", "Getting Started")
        import uuid
        db = _SessionLocal()
        try:
            db.add(_WorkspaceNote(
                id=str(uuid.uuid4()),
                workspace_id=ws_id,
                owner=owner,
                title=title,
                content=f"Workspace **{name}** created. Add your notes here.",
            ))
            db.commit()
        finally:
            db.close()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"[workspace-notes] on_workspace_created: {e}")


def _note_dict(n):
    return {
        "id": n.id,
        "workspace_id": n.workspace_id,
        "title": n.title,
        "content": n.content,
        "owner": n.owner,
        "created_at": n.created_at.isoformat() if n.created_at else None,
        "updated_at": n.updated_at.isoformat() if n.updated_at else None,
    }
