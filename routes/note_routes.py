# routes/note_routes.py
"""Google Keep-style notes / checklists API."""

import json
import uuid
import logging
from typing import Dict, Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.database import SessionLocal  # patchable test seam
from core.database import get_db_session, Note
from src.auth_helpers import get_current_user
from src.constants import DATA_DIR
from sqlalchemy.orm.attributes import flag_modified

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class NoteCreate(BaseModel):
    title: str = ""
    content: Optional[str] = None
    items: Optional[list] = None
    note_type: str = "note"
    color: Optional[str] = None
    label: Optional[str] = None
    pinned: bool = False
    due_date: Optional[str] = None
    source: str = "user"
    session_id: Optional[str] = None
    image_url: Optional[str] = None
    repeat: Optional[str] = "none"
    sort_order: Optional[int] = None


class NoteUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    items: Optional[list] = None
    note_type: Optional[str] = None
    color: Optional[str] = None
    label: Optional[str] = None
    pinned: Optional[bool] = None
    archived: Optional[bool] = None
    due_date: Optional[str] = None
    image_url: Optional[str] = None
    repeat: Optional[str] = None
    sort_order: Optional[int] = None
    agent_session_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _note_to_dict(note: Note) -> Dict[str, Any]:
    items = None
    if note.items:
        try:
            items = json.loads(note.items)
        except (json.JSONDecodeError, TypeError):
            items = None
    ai_cls = None
    raw_ai = getattr(note, "ai_classification", None)
    if raw_ai:
        try:
            ai_cls = json.loads(raw_ai)
        except (json.JSONDecodeError, TypeError):
            ai_cls = None
    return {
        "id": note.id,
        "owner": note.owner,
        "title": note.title,
        "content": note.content,
        "items": items,
        "note_type": note.note_type,
        "color": note.color,
        "label": note.label,
        "pinned": note.pinned,
        "archived": note.archived,
        "due_date": note.due_date,
        "source": note.source,
        "session_id": note.session_id,
        "sort_order": note.sort_order or 0,
        "image_url": note.image_url,
        "repeat": note.repeat or "none",
        "ai_classification": ai_cls,
        "ai_content_hash": getattr(note, "ai_content_hash", None),
        "agent_session_id": getattr(note, "agent_session_id", None),
        "created_at": note.created_at.isoformat() if note.created_at else None,
        "updated_at": note.updated_at.isoformat() if note.updated_at else None,
    }


def _reminder_text_from_note(note: Note) -> tuple[str, str]:
    """Return the reminder title/body from a stored note row."""
    title = (note.title or "Note reminder").strip() or "Note reminder"
    if note.items:
        try:
            items = json.loads(note.items)
        except (json.JSONDecodeError, TypeError):
            items = None
        if isinstance(items, list):
            pending: list[str] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("done") or item.get("checked"):
                    continue
                text = str(item.get("text") or "").strip()
                if text:
                    pending.append(text)
            if pending:
                shown = "\n".join(f"- {text}" for text in pending[:8])
                extra = f"\n...and {len(pending) - 8} more" if len(pending) > 8 else ""
                return title, f"Pending ({len(pending)}):\n{shown}{extra}"
            return title, f"{len(items)} item{'s' if len(items) != 1 else ''}"
    return title, (note.content or "").strip()[:400]



# ---------------------------------------------------------------------------
# Reminder dispatch â€” re-exported from src.services.note_reminder so route
# handlers and background tasks (built-in actions) share one implementation.
# ---------------------------------------------------------------------------
from src.services.note_reminder import dispatch_reminder, set_scheduler_ref


# dispatch_reminder and set_scheduler_ref re-exported from src.services.note_reminder (imported above)



# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def setup_note_routes(task_scheduler=None):
    # Wire the scheduler into src.services.note_reminder so dispatch_reminder
    # can push in-app browser notifications via the scheduler's queue.
    set_scheduler_ref(task_scheduler)

    router = APIRouter(prefix="/api/notes", tags=["notes"])

    def _owner(request: Request) -> Optional[str]:
        return get_current_user(request)

    def _is_admin_or_single_user(request: Request, user: str | None) -> bool:
        if user == "internal-tool":
            return True
        if not user:
            # require_user() already admitted this request, which only happens
            # for auth-disabled, loopback-bypass, or unconfigured single-user
            # modes. There is no separate non-admin account boundary there.
            return True
        try:
            from core.auth import AuthManager
            auth_mgr = getattr(request.app.state, "auth_manager", None) or AuthManager()
            if not getattr(auth_mgr, "is_configured", True):
                return True
            return bool(auth_mgr.is_admin(user))
        except Exception:
            return False

    # --- LIST ---
    @router.get("")
    def list_notes(
        request: Request,
        archived: Optional[bool] = None,
        label: Optional[str] = None,
    ):
        user = _owner(request)
        with get_db_session(SessionLocal) as db:
            q = db.query(Note)
            if user is not None:
                q = q.filter(Note.owner == user)
            if archived is not None:
                q = q.filter(Note.archived == archived)
            else:
                q = q.filter(Note.archived == False)
            if label:
                q = q.filter(Note.label == label)
            # Archived view: most recently archived first. Active view: pin + manual order.
            if archived is True:
                notes = q.order_by(Note.updated_at.desc()).all()
            else:
                notes = q.order_by(Note.pinned.desc(), Note.sort_order.asc(), Note.updated_at.desc()).all()
            return {"notes": [_note_to_dict(n) for n in notes]}

    # --- CREATE ---
    @router.post("")
    def create_note(request: Request, body: NoteCreate):
        user = _owner(request)
        with get_db_session(SessionLocal) as db:
            note = Note(
                id=str(uuid.uuid4()),
                owner=user,
                title=body.title,
                content=body.content,
                items=json.dumps(body.items) if body.items is not None else None,
                note_type=body.note_type,
                color=body.color,
                label=body.label,
                pinned=body.pinned,
                due_date=body.due_date,
                source=body.source,
                session_id=body.session_id,
                image_url=body.image_url,
                repeat=body.repeat or "none",
                sort_order=body.sort_order if body.sort_order is not None else 0,
            )
            db.add(note)
            db.commit()
            db.refresh(note)
            return _note_to_dict(note)

    # --- GET ONE ---
    @router.get("/{note_id}")
    def get_note(request: Request, note_id: str):
        user = _owner(request)
        with get_db_session(SessionLocal) as db:
            note = db.query(Note).filter(Note.id == note_id).first()
            if not note:
                raise HTTPException(404, "Note not found")
            # SECURITY: strict ownership â€” previously `note.owner and note.owner != user`
            # let any user touch a row whose owner field was null/empty.
            if user is not None and note.owner != user:
                raise HTTPException(404, "Note not found")
            return _note_to_dict(note)

    # --- UPDATE ---
    @router.put("/{note_id}")
    def update_note(request: Request, note_id: str, body: NoteUpdate):
        user = _owner(request)
        with get_db_session(SessionLocal) as db:
            note = db.query(Note).filter(Note.id == note_id).first()
            if not note:
                raise HTTPException(404, "Note not found")
            # SECURITY: strict ownership â€” previously `note.owner and note.owner != user`
            # let any user touch a row whose owner field was null/empty.
            if user is not None and note.owner != user:
                raise HTTPException(404, "Note not found")

            if body.title is not None:
                note.title = body.title
            if body.content is not None:
                note.content = body.content
            if body.items is not None:
                note.items = json.dumps(body.items)
                flag_modified(note, "items")
            if body.note_type is not None:
                note.note_type = body.note_type
            if body.color is not None:
                note.color = body.color
            if body.label is not None:
                note.label = body.label
            if body.pinned is not None:
                note.pinned = body.pinned
            if body.archived is not None:
                note.archived = body.archived
            if body.due_date is not None:
                note.due_date = body.due_date
            if body.image_url is not None:
                note.image_url = body.image_url
            if body.repeat is not None:
                note.repeat = body.repeat
            if body.sort_order is not None:
                note.sort_order = body.sort_order
            if body.agent_session_id is not None:
                note.agent_session_id = body.agent_session_id

            db.commit()
            db.refresh(note)
            return _note_to_dict(note)

    # --- DELETE ---
    @router.delete("/{note_id}")
    def delete_note(request: Request, note_id: str):
        user = _owner(request)
        with get_db_session(SessionLocal) as db:
            note = db.query(Note).filter(Note.id == note_id).first()
            if not note:
                raise HTTPException(404, "Note not found")
            # SECURITY: strict ownership â€” previously `note.owner and note.owner != user`
            # let any user touch a row whose owner field was null/empty.
            if user is not None and note.owner != user:
                raise HTTPException(404, "Note not found")
            db.delete(note)
            db.commit()
            return {"ok": True}

    # --- TOGGLE PIN ---
    @router.post("/{note_id}/pin")
    def toggle_pin(request: Request, note_id: str):
        user = _owner(request)
        with get_db_session(SessionLocal) as db:
            note = db.query(Note).filter(Note.id == note_id).first()
            if not note:
                raise HTTPException(404, "Note not found")
            # SECURITY: strict ownership â€” previously `note.owner and note.owner != user`
            # let any user touch a row whose owner field was null/empty.
            if user is not None and note.owner != user:
                raise HTTPException(404, "Note not found")
            note.pinned = not note.pinned
            db.commit()
            return {"ok": True, "pinned": note.pinned}

    # --- TOGGLE ARCHIVE ---
    @router.post("/{note_id}/archive")
    def toggle_archive(request: Request, note_id: str):
        user = _owner(request)
        with get_db_session(SessionLocal) as db:
            note = db.query(Note).filter(Note.id == note_id).first()
            if not note:
                raise HTTPException(404, "Note not found")
            # SECURITY: strict ownership â€” previously `note.owner and note.owner != user`
            # let any user touch a row whose owner field was null/empty.
            if user is not None and note.owner != user:
                raise HTTPException(404, "Note not found")
            note.archived = not note.archived
            db.commit()
            return {"ok": True, "archived": note.archived}

    # --- TOGGLE CHECKLIST ITEM ---
    @router.post("/{note_id}/items/{index}/toggle")
    def toggle_item(request: Request, note_id: str, index: int):
        user = _owner(request)
        with get_db_session(SessionLocal) as db:
            note = db.query(Note).filter(Note.id == note_id).first()
            if not note:
                raise HTTPException(404, "Note not found")
            # SECURITY: strict ownership â€” previously `note.owner and note.owner != user`
            # let any user touch a row whose owner field was null/empty.
            if user is not None and note.owner != user:
                raise HTTPException(404, "Note not found")
            if not note.items:
                raise HTTPException(400, "Note has no checklist items")
            items = json.loads(note.items)
            if index < 0 or index >= len(items):
                raise HTTPException(400, f"Item index {index} out of range")
            items[index]["done"] = not items[index].get("done", False)
            note.items = json.dumps(items)
            flag_modified(note, "items")
            db.commit()
            return {"ok": True, "items": items}

    # --- FIRE REMINDER ---
    @router.post("/fire-reminder")
    async def fire_reminder(request: Request):
        """Dispatch a reminder according to user settings.

        Called by the frontend when a reminder fires. Optionally generates an
        LLM synthesis line and/or sends an email through configured SMTP.
        Returns {synthesis, email_sent}.
        """
        # Gate against anonymous callers â€” LLM synthesis can burn tokens.
        from src.auth_helpers import require_user as _ru
        user = _ru(request)
        body = await request.json()
        note_id = str(body.get("note_id") or "").strip()
        if not note_id:
            raise HTTPException(400, "note_id required")

        caller = _owner(request)
        is_test = note_id.startswith("test-")
        is_admin = _is_admin_or_single_user(request, user or caller)
        _override: dict = {}
        if is_test:
            if not is_admin:
                raise HTTPException(403, "Admin only")
            title = (body.get("title") or "Test Reminder").strip() or "Test Reminder"
            note_body = (body.get("body") or "").strip()
            # Optional overrides let the admin settings test button pass the
            # current UI values directly so it never races a pending save.
            if body.get("channel"):
                _override["reminder_channel"] = body["channel"]
            if body.get("webhook_integration_id"):
                _override["reminder_webhook_integration_id"] = body["webhook_integration_id"]
            if body.get("webhook_payload_template"):
                _override["reminder_webhook_payload_template"] = body["webhook_payload_template"]
        else:
            with get_db_session(SessionLocal) as db:
                note = db.query(Note).filter(Note.id == note_id).first()
                if not note:
                    raise HTTPException(404, "Note not found")
                if caller is not None and note.owner != caller:
                    raise HTTPException(404, "Note not found")
                title, note_body = _reminder_text_from_note(note)

        return await dispatch_reminder(
            title=title, note_body=note_body, note_id=note_id,
            owner=caller or "",
            queue_browser=False,
            settings_override=_override or None,
        )

    # --- REORDER NOTES ---
    @router.post("/reorder")
    async def reorder_notes(request: Request):
        """Update sort_order for a list of note IDs in the order provided."""
        user = _owner(request)
        body = await request.json()
        ids = body.get("ids", [])
        if not isinstance(ids, list):
            raise HTTPException(400, "ids must be a list")
        # v2 review HIGH-12: drop the legacy `(owner == user) | (owner ==
        # None)` OR which let an authenticated user silently reorder
        # every legacy-null-owner note belonging to other accounts. In
        # an unconfigured (single-user) auth deploy the OR is still safe
        # because there's no second user to attack; we keep that branch
        # explicit and gated on AuthManager.is_configured.
        try:
            from core.auth import AuthManager
            _allow_null = not AuthManager().is_configured
        except Exception:
            _allow_null = False
        with get_db_session(SessionLocal) as db:
            for i, nid in enumerate(ids):
                q = db.query(Note).filter(Note.id == nid)
                if user is not None:
                    if _allow_null:
                        q = q.filter((Note.owner == user) | (Note.owner == None))  # noqa: E711
                    else:
                        q = q.filter(Note.owner == user)
                note = q.first()
                if note:
                    note.sort_order = i
            db.commit()
            return {"ok": True, "count": len(ids)}

    return router
