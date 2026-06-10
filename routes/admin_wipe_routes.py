"""Admin Danger Zone â€” per-category wipes.

Each endpoint is admin-only and truncates exactly one domain so the
user can selectively reset memory / skills / notes / etc. without
nuking everything. The catch-all `chats` endpoint mirrors the
existing /api/sessions/all so the Danger Zone speaks one URL pattern.

URL shape: DELETE /api/admin/wipe/{kind}
Kinds: chats, memory, skills, notes, tasks, documents, gallery, calendar.
"""

import json
import logging
import os
import shutil
from fastapi import APIRouter, HTTPException, Request

from core.middleware import require_admin
from core.database import SessionLocal  # patchable test seam
from core.database import (
    get_db_session,
    Session as DbSession,
    ChatMessage as DbChatMessage,
    Memory,
    Note,
    ScheduledTask,
    TaskRun,
    Document,
    DocumentVersion,
    GalleryImage,
    GalleryAlbum,
    CalendarEvent,
    CalendarCal,
)
from src.constants import DATA_DIR, SKILLS_DIR, SKILLS_FILE, GALLERY_DIR, GALLERY_UPLOADS_DIR
from src.audit_log import audit_event
from src.auth_helpers import get_current_user

logger = logging.getLogger(__name__)


def _wipe_memory_files():
    """Blank memory.json + drop the per-owner tidy-state sidecar so the
    next audit doesn't try to diff against gone memories."""
    for name in ("memory.json", "memory_tidy_state.json"):
        p = os.path.join(DATA_DIR, name)
        if not os.path.exists(p):
            continue
        try:
            if name == "memory.json":
                with open(p, "w", encoding="utf-8") as f:
                    json.dump([], f)
            else:
                os.remove(p)
        except OSError as e:
            logger.warning(f"Could not reset {name}: {e}")


def _rmtree_quiet(path: str):
    """rmtree that doesn't crash if the path doesn't exist."""
    if os.path.isdir(path):
        try:
            shutil.rmtree(path)
        except OSError as e:
            logger.warning(f"Could not remove {path}: {e}")


def setup_admin_wipe_routes(session_manager):
    """The session_manager is passed in so we can also clear its
    in-memory cache when wiping chats â€” without it the DB is empty
    but the next /api/sessions returns stale entries."""
    router = APIRouter(prefix="/api/admin")

    @router.delete("/wipe/{kind}")
    def wipe(kind: str, request: Request):
        require_admin(request)
        actor = get_current_user(request) or "anon"
        kind = (kind or "").strip().lower()

        try:
            with get_db_session(SessionLocal) as db:
                if kind == "chats":
                    count = db.query(DbSession).count()
                    db.query(DbChatMessage).delete()
                    db.query(DbSession).delete()
                    # get_db_session auto-commits
                elif kind == "memory":
                    count = db.query(Memory).count()
                    db.query(Memory).delete()
                    # get_db_session auto-commits
                elif kind == "skills":
                    count = 0  # skills are file-only; handled below
                elif kind == "notes":
                    count = db.query(Note).count()
                    db.query(Note).delete()
                    # get_db_session auto-commits
                elif kind == "tasks":
                    db.query(TaskRun).delete()
                    count = db.query(ScheduledTask).count()
                    db.query(ScheduledTask).delete()
                    # get_db_session auto-commits
                elif kind == "documents":
                    db.query(DocumentVersion).delete()
                    count = db.query(Document).count()
                    db.query(Document).delete()
                    # get_db_session auto-commits
                elif kind == "gallery":
                    count = db.query(GalleryImage).count() + db.query(GalleryAlbum).count()
                    db.query(GalleryImage).delete()
                    db.query(GalleryAlbum).delete()
                    # get_db_session auto-commits
                elif kind == "calendar":
                    db.query(CalendarEvent).delete()
                    count = db.query(CalendarCal).count()
                    db.query(CalendarCal).delete()
                    # get_db_session auto-commits
                else:
                    raise HTTPException(400, f"Unknown wipe kind: {kind!r}")
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Wipe {kind} failed")
            raise HTTPException(500, f"Wipe {kind} failed: {e}")

        # Post-DB side-effects (after session committed and closed)
        if kind == "chats":
            try:
                session_manager.sessions.clear()
            except Exception:
                pass
            audit_event(actor, "bulk_wipe_chats", f"count={count}", level="WARNING")
            return {"status": "deleted", "kind": kind, "count": count}

        if kind == "memory":
            _wipe_memory_files()
            try:
                from src.memory_vector import get_memory_vector_store
                mv = get_memory_vector_store()
                if mv and hasattr(mv, "clear"):
                    mv.clear()
            except Exception as e:
                logger.info(f"Memory vector clear skipped: {e}")
            audit_event(actor, "bulk_wipe_memory", f"count={count}", level="WARNING")
            return {"status": "deleted", "kind": kind, "count": count}

        if kind == "skills":
            skills_dir = SKILLS_DIR
            if os.path.isdir(skills_dir):
                for _, _, files in os.walk(skills_dir):
                    count += sum(1 for f in files if f == "SKILL.md")
                _rmtree_quiet(skills_dir)
            legacy = SKILLS_FILE
            if os.path.exists(legacy):
                try:
                    os.remove(legacy)
                except OSError:
                    pass
            audit_event(actor, "bulk_wipe_skills", f"count={count}", level="WARNING")
            return {"status": "deleted", "kind": kind, "count": count}

        if kind == "notes":
            audit_event(actor, "bulk_wipe_notes", f"count={count}", level="WARNING")
            return {"status": "deleted", "kind": kind, "count": count}

        if kind == "tasks":
            audit_event(actor, "bulk_wipe_tasks", f"count={count}", level="WARNING")
            return {"status": "deleted", "kind": kind, "count": count}

        if kind == "documents":
            audit_event(actor, "bulk_wipe_documents", f"count={count}", level="WARNING")
            return {"status": "deleted", "kind": kind, "count": count}

        if kind == "gallery":
            _rmtree_quiet(GALLERY_DIR)
            _rmtree_quiet(GALLERY_UPLOADS_DIR)
            audit_event(actor, "bulk_wipe_gallery", f"count={count}", level="WARNING")
            return {"status": "deleted", "kind": kind, "count": count}

        if kind == "calendar":
            audit_event(actor, "bulk_wipe_calendar", f"count={count}", level="WARNING")
            return {"status": "deleted", "kind": kind, "count": count}

        raise HTTPException(400, f"Unknown wipe kind: {kind!r}")

    return router
