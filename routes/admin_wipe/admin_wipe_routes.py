"""Admin Danger Zone — per-category wipes.

Each endpoint is admin-only and truncates exactly one domain so the
user can selectively reset memory / skills / notes / etc. without
nuking everything. The catch-all `chats` endpoint mirrors the
existing /api/sessions/all so the Danger Zone speaks one URL pattern.

URL shape: DELETE /api/admin/wipe/{kind}
Kinds: chats, memory, skills, notes, tasks, documents, gallery, calendar.
"""

import logging
import os
import shutil
from fastapi import APIRouter, HTTPException, Request

from core.middleware import require_admin
from core.database import (
    SessionLocal,
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

logger = logging.getLogger(__name__)


def _wipe_memory_sidecars():
    """Drop derived memory state after the authoritative stores are empty."""
    for name in ("memory_tidy_state.json",):
        p = os.path.join(DATA_DIR, name)
        if not os.path.exists(p):
            continue
        try:
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


def setup_admin_wipe_routes(session_manager, memory_manager=None, memory_vector=None):
    """The session_manager is passed in so we can also clear its
    in-memory cache when wiping chats — without it the DB is empty
    but the next /api/sessions returns stale entries."""
    router = APIRouter(prefix="/api/admin")

    @router.delete("/wipe/{kind}")
    def wipe(kind: str, request: Request):
        require_admin(request)
        kind = (kind or "").strip().lower()

        db = SessionLocal()
        try:
            if kind == "chats":
                count = db.query(DbSession).count()
                db.query(DbChatMessage).delete()
                db.query(DbSession).delete()
                db.commit()
                try:
                    session_manager.sessions.clear()
                except Exception:
                    pass
                return {"status": "deleted", "kind": kind, "count": count}

            if kind == "memory":
                if memory_manager is None or memory_vector is None:
                    raise HTTPException(503, "Memory stores are not available for a safe wipe")
                if not getattr(memory_vector, "healthy", False):
                    raise HTTPException(503, "Memory vector store is unavailable; nothing was deleted")

                original_memories = memory_manager.load_all_for_update()
                count = db.query(Memory).count()
                db.query(Memory).delete()

                try:
                    # Clear vectors before committing SQL. Keep the clear in
                    # the compensation boundary because a backend can fail
                    # after deleting only some lanes.
                    memory_vector.clear(strict=True)
                    memory_manager.save([])
                    db.commit()
                except Exception as e:
                    # Restore the full multi-user corpus, never only the active
                    # request owner's slice. A failed compensation is surfaced
                    # because silently leaving split stores would be worse.
                    restore_errors = []
                    try:
                        memory_manager.save(original_memories)
                    except Exception as restore_error:
                        restore_errors.append(f"JSON restore failed: {restore_error}")
                    try:
                        memory_vector.rebuild(original_memories, strict=True)
                    except Exception as restore_error:
                        restore_errors.append(f"vector restore failed: {restore_error}")
                    if restore_errors:
                        raise RuntimeError(
                            f"Memory wipe failed ({e}); " + "; ".join(restore_errors)
                        ) from e
                    raise
                _wipe_memory_sidecars()
                return {"status": "deleted", "kind": kind, "count": count}

            if kind == "skills":
                # Skills live as SKILL.md files under data/skills/. Drop
                # the entire directory; the SkillsManager re-creates the
                # tree on next write.
                skills_dir = SKILLS_DIR
                count = 0
                if os.path.isdir(skills_dir):
                    # Count SKILL.md files for the response — quick walk.
                    for _, _, files in os.walk(skills_dir):
                        count += sum(1 for f in files if f == "SKILL.md")
                    _rmtree_quiet(skills_dir)
                # Legacy fallback file
                legacy = SKILLS_FILE
                if os.path.exists(legacy):
                    try:
                        os.remove(legacy)
                    except OSError:
                        pass
                return {"status": "deleted", "kind": kind, "count": count}

            if kind == "notes":
                count = db.query(Note).count()
                db.query(Note).delete()
                db.commit()
                return {"status": "deleted", "kind": kind, "count": count}

            if kind == "tasks":
                # TaskRun rows reference tasks via FK — clear them first.
                db.query(TaskRun).delete()
                count = db.query(ScheduledTask).count()
                db.query(ScheduledTask).delete()
                db.commit()
                return {"status": "deleted", "kind": kind, "count": count}

            if kind == "documents":
                # DocumentVersion FKs Document — clear children first.
                db.query(DocumentVersion).delete()
                count = db.query(Document).count()
                db.query(Document).delete()
                db.commit()
                return {"status": "deleted", "kind": kind, "count": count}

            if kind == "gallery":
                count = db.query(GalleryImage).count() + db.query(GalleryAlbum).count()
                db.query(GalleryImage).delete()
                db.query(GalleryAlbum).delete()
                db.commit()
                # Also drop the upload dir so disk doesn't keep orphans.
                _rmtree_quiet(GALLERY_DIR)
                _rmtree_quiet(GALLERY_UPLOADS_DIR)
                return {"status": "deleted", "kind": kind, "count": count}

            if kind == "calendar":
                # Events FK calendars — clear children first, then both.
                db.query(CalendarEvent).delete()
                count = db.query(CalendarCal).count()
                db.query(CalendarCal).delete()
                db.commit()
                return {"status": "deleted", "kind": kind, "count": count}

            raise HTTPException(400, f"Unknown wipe kind: {kind!r}")
        except HTTPException:
            db.rollback()
            raise
        except Exception as e:
            db.rollback()
            logger.exception(f"Wipe {kind} failed")
            raise HTTPException(500, f"Wipe {kind} failed: {e}")
        finally:
            db.close()

    return router
