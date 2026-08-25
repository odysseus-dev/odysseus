"""Backup routes — export/import user data (memories, presets, settings, skills, preferences)."""

import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from core.middleware import require_admin
from core.database import (
    SessionLocal,
    CalendarCal,
    CalendarEvent,
    ScheduledTask,
    TaskRun,
    Note,
)
from services.memory import MemoryStoreUnreadable
from src.auth_helpers import get_current_user
from src.settings import load_settings, save_settings, load_features, save_features

logger = logging.getLogger(__name__)

BACKUP_VERSION = 2

_CALENDAR_FIELDS = (
    "id", "name", "color", "source", "account_id", "caldav_base_url",
)
_EVENT_FIELDS = (
    "uid", "calendar_id", "summary", "description", "location", "dtstart",
    "dtend", "all_day", "is_utc", "rrule", "recurrence_exdates", "color",
    "status", "importance", "event_type", "last_pinged", "origin",
    "remote_href", "remote_etag", "caldav_sync_pending",
)
_TASK_FIELDS = (
    "id", "name", "prompt", "task_type", "action", "schedule",
    "scheduled_time", "scheduled_day", "scheduled_date", "trigger_type",
    "trigger_event", "trigger_count", "trigger_counter", "next_run",
    "last_run", "status", "output_target", "model",
    "endpoint_url", "run_count", "cron_expression", "then_task_id",
    "max_steps", "email_results", "notifications_enabled",
)
_TASK_RUN_FIELDS = (
    "id", "task_id", "started_at", "finished_at", "status", "result",
    "error", "tokens_used", "steps", "model",
)
_NOTE_FIELDS = (
    "id", "title", "content", "items", "note_type", "color", "label",
    "pinned", "archived", "due_date", "source", "sort_order", "repeat",
    "ai_classification", "ai_content_hash",
)
_DATETIME_FIELDS = {
    "dtstart", "dtend", "last_pinged", "scheduled_date", "next_run",
    "last_run", "started_at", "finished_at",
}


def _serialize_row(row, fields):
    out = {}
    for field in fields:
        value = getattr(row, field, None)
        out[field] = value.isoformat() if isinstance(value, datetime) else value
    return out


def _coerce_value(field, value):
    if field not in _DATETIME_FIELDS or value in (None, ""):
        return value
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO datetime")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _has_timezone_offset(value) -> bool:
    """Return whether an imported ISO datetime carries timezone meaning."""
    if not isinstance(value, str):
        return False
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _assign_fields(row, payload, fields):
    for field in fields:
        if field in {"id", "uid", "calendar_id", "task_id", "then_task_id"}:
            continue
        if field in payload:
            setattr(row, field, _coerce_value(field, payload[field]))


def _new_id() -> str:
    return str(uuid.uuid4())


def _restore_database_domains(db, body, owner) -> list[str]:
    """Merge v2 calendar/task/note data without crossing owner boundaries."""
    restored = []

    calendar_payload = body.get("calendar")
    if isinstance(calendar_payload, dict):
        calendar_map = {}
        calendar_rows = calendar_payload.get("calendars")
        for payload in calendar_rows if isinstance(calendar_rows, list) else []:
            if not isinstance(payload, dict) or not payload.get("id") or not payload.get("name"):
                continue
            source_id = str(payload["id"])
            existing = db.query(CalendarCal).filter(CalendarCal.id == source_id).first()
            if existing is not None and existing.owner != owner:
                target_id = _new_id()
                existing = None
            else:
                target_id = source_id
            row = existing or CalendarCal(id=target_id, owner=owner, name=str(payload["name"]))
            if existing is None:
                db.add(row)
            row.owner = owner
            _assign_fields(row, payload, _CALENDAR_FIELDS)
            # JSON backup is portable user data, not a credential/config
            # backup. Imported calendars are local until the owner explicitly
            # configures and syncs a CalDAV account again.
            row.source = "local"
            row.account_id = None
            row.caldav_base_url = None
            calendar_map[source_id] = target_id

        event_rows = calendar_payload.get("events")
        event_count = 0
        for payload in event_rows if isinstance(event_rows, list) else []:
            if not isinstance(payload, dict):
                continue
            source_uid = str(payload.get("uid") or "")
            target_calendar = calendar_map.get(str(payload.get("calendar_id") or ""))
            if not source_uid or not target_calendar:
                continue
            existing = db.query(CalendarEvent).filter(CalendarEvent.uid == source_uid).first()
            if existing is not None and existing.calendar_id != target_calendar:
                source_uid = _new_id()
                existing = None
            row = existing or CalendarEvent(uid=source_uid, calendar_id=target_calendar)
            if existing is None:
                db.add(row)
            row.calendar_id = target_calendar
            _assign_fields(row, payload, _EVENT_FIELDS)
            # Aware timestamps are normalized to naive UTC for SQLite by
            # _coerce_value().  Keep the companion semantic flag aligned so
            # readers do not reinterpret that UTC wall time as legacy local
            # time, even if an imported payload omitted or cleared is_utc.
            if any(_has_timezone_offset(payload.get(field)) for field in ("dtstart", "dtend")):
                row.is_utc = True
            row.origin = None
            row.remote_href = None
            row.remote_etag = None
            row.caldav_sync_pending = None
            event_count += 1
        restored.append(f"{len(calendar_map)} calendars, {event_count} events")

    task_payload = body.get("tasks")
    if isinstance(task_payload, dict):
        task_map = {}
        deferred_links = []
        task_rows = task_payload.get("scheduled")
        for payload in task_rows if isinstance(task_rows, list) else []:
            if not isinstance(payload, dict) or not payload.get("id"):
                continue
            source_id = str(payload["id"])
            existing = db.query(ScheduledTask).filter(ScheduledTask.id == source_id).first()
            if existing is not None and existing.owner != owner:
                target_id = _new_id()
                existing = None
            else:
                target_id = source_id
            row = existing or ScheduledTask(id=target_id, owner=owner)
            if existing is None:
                db.add(row)
            row.owner = owner
            _assign_fields(row, payload, _TASK_FIELDS)
            # Sessions, crew members, and characters are outside this backup
            # domain and are not remapped. Never bind a portable task to a
            # live row merely because its source ID exists in this instance.
            row.session_id = None
            row.crew_member_id = None
            row.character_id = None
            # Webhook tokens are path credentials and are intentionally not
            # portable.  Clear a pre-existing same-owner token as well as
            # leaving newly created tasks tokenless.
            row.webhook_token = None
            # Break any existing link until every imported task row exists.
            # A forward reference otherwise autoflushes before its target has
            # been inserted and violates the self-referential FK.
            row.then_task_id = None
            task_map[source_id] = target_id
            deferred_links.append((row, payload.get("then_task_id")))

        if task_map:
            db.flush()
        for row, source_then_id in deferred_links:
            row.then_task_id = task_map.get(str(source_then_id)) if source_then_id else None
        if deferred_links:
            db.flush()

        run_rows = task_payload.get("runs")
        run_count = 0
        for payload in run_rows if isinstance(run_rows, list) else []:
            if not isinstance(payload, dict):
                continue
            source_id = str(payload.get("id") or "")
            target_task = task_map.get(str(payload.get("task_id") or ""))
            if not source_id or not target_task:
                continue
            existing = db.query(TaskRun).filter(TaskRun.id == source_id).first()
            if existing is not None and existing.task_id != target_task:
                source_id = _new_id()
                existing = None
            row = existing or TaskRun(id=source_id, task_id=target_task)
            if existing is None:
                db.add(row)
            row.task_id = target_task
            _assign_fields(row, payload, _TASK_RUN_FIELDS)
            run_count += 1
        restored.append(f"{len(task_map)} tasks, {run_count} task runs")

    notes_payload = body.get("notes")
    if isinstance(notes_payload, list):
        note_count = 0
        for payload in notes_payload:
            if not isinstance(payload, dict) or not payload.get("id"):
                continue
            source_id = str(payload["id"])
            existing = db.query(Note).filter(Note.id == source_id).first()
            if existing is not None and existing.owner != owner:
                source_id = _new_id()
                existing = None
            row = existing or Note(id=source_id, owner=owner)
            if existing is None:
                db.add(row)
            row.owner = owner
            _assign_fields(row, payload, _NOTE_FIELDS)
            # Session/agent and upload IDs are not part of this format. Keep
            # restored note text, but detach those non-portable references.
            row.session_id = None
            row.agent_session_id = None
            row.image_url = None
            note_count += 1
        restored.append(f"{note_count} notes")

    return restored


def setup_backup_routes(
    memory_manager,
    preset_manager,
    skills_manager,
    memory_vector=None,
) -> APIRouter:
    router = APIRouter(tags=["backup"])

    @router.get("/api/export")
    async def export_data(request: Request):
        """Export all user data as a downloadable JSON file."""
        require_admin(request)
        user = get_current_user(request)

        # Memories (filtered by owner when auth is enabled)
        memories = memory_manager.load(owner=user)

        # Presets (shared across users — export all)
        presets = preset_manager.get_all()

        # Skills (filtered by owner when auth is enabled)
        skills = skills_manager.load(owner=user)

        # Settings
        settings = load_settings()

        # Feature flags
        features = load_features()

        # User preferences
        from routes.prefs_routes import _load_for_user
        preferences = _load_for_user(user)

        db = SessionLocal()
        try:
            calendars = db.query(CalendarCal).filter(CalendarCal.owner == user).all()
            calendar_ids = [calendar.id for calendar in calendars]
            events = (
                db.query(CalendarEvent)
                .filter(CalendarEvent.calendar_id.in_(calendar_ids))
                .all()
                if calendar_ids else []
            )
            tasks = db.query(ScheduledTask).filter(ScheduledTask.owner == user).all()
            task_ids = [task.id for task in tasks]
            task_runs = (
                db.query(TaskRun).filter(TaskRun.task_id.in_(task_ids)).all()
                if task_ids else []
            )
            notes = db.query(Note).filter(Note.owner == user).all()
        finally:
            db.close()

        export_data = {
            "version": BACKUP_VERSION,
            "exported_at": datetime.now().isoformat(),
            "exported_by": user,
            "memories": memories,
            "presets": presets,
            "skills": skills,
            "settings": settings,
            "features": features,
            "preferences": preferences,
            "calendar": {
                "calendars": [_serialize_row(row, _CALENDAR_FIELDS) for row in calendars],
                "events": [_serialize_row(row, _EVENT_FIELDS) for row in events],
            },
            "tasks": {
                "scheduled": [_serialize_row(row, _TASK_FIELDS) for row in tasks],
                "runs": [_serialize_row(row, _TASK_RUN_FIELDS) for row in task_runs],
                # Webhook path credentials are deliberately omitted. Restored
                # tasks receive no imported token; regenerate it explicitly.
                "webhook_tokens_included": False,
            },
            "notes": [_serialize_row(row, _NOTE_FIELDS) for row in notes],
        }

        filename = f"odysseus_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        return Response(
            content=json.dumps(export_data, indent=2, ensure_ascii=False),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    @router.post("/api/import")
    async def import_data(request: Request):
        """Import user data from a previously exported JSON file. Merges with existing data."""
        require_admin(request)
        user = get_current_user(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Invalid JSON")

        if not isinstance(body, dict):
            raise HTTPException(400, "Expected a JSON object")

        version = body.get("version", 1)
        if not isinstance(version, int) or version < 1 or version > BACKUP_VERSION:
            raise HTTPException(400, f"Unsupported backup version: {version!r}")

        imported = []

        # ── Memories ──
        if "memories" in body and isinstance(body["memories"], list):
            # Strict load: importing on top of an unreadable store would write
            # only the incoming rows back and drop everything already saved.
            try:
                existing = memory_manager.load_all_for_update()
            except MemoryStoreUnreadable as e:
                logger.error("Refusing to import memories: %s", e)
                raise HTTPException(
                    503, "Memory store is temporarily unreadable — nothing was imported."
                )
            original = list(existing)
            # Dedup against THIS user's own memories only. Using every tenant's
            # rows (load_all) meant a memory whose text matched any other
            # user's was silently skipped, so the importing user lost their own
            # data. The full store is still saved back below.
            existing_texts = {e.get("text", "").strip().lower()
                              for e in existing if e.get("owner") == user}
            added = 0
            for mem in body["memories"]:
                if not isinstance(mem, dict) or not mem.get("text"):
                    continue
                if mem["text"].strip().lower() in existing_texts:
                    continue  # skip duplicates
                # Assign owner when auth is enabled
                if user and not mem.get("owner"):
                    mem["owner"] = user
                existing.append(mem)
                existing_texts.add(mem["text"].strip().lower())
                added += 1
            if added and memory_vector is not None and not getattr(memory_vector, "healthy", False):
                raise HTTPException(
                    503,
                    "Memory vector store is unavailable — nothing was imported.",
                )

            # Preserve the optional-vector route contract used by lightweight
            # deployments and import callers.  Such callers still persist the
            # authoritative JSON store; vector synchronization is performed
            # only when a vector dependency was injected.
            memory_manager.save(existing)
            try:
                # The vector collection is shared by every owner. Rebuilding
                # only the importing user's rows would erase every other
                # tenant, so always use the complete persisted corpus.
                if added and memory_vector is not None:
                    memory_vector.rebuild(existing, strict=True)
            except Exception as e:
                logger.error("Memory vector rebuild failed; rolling back import: %s", e)
                restore_errors = []
                try:
                    memory_manager.save(original)
                except Exception as restore_error:
                    restore_errors.append(f"JSON restore failed: {restore_error}")
                try:
                    memory_vector.rebuild(original, strict=True)
                except Exception as restore_error:
                    restore_errors.append(f"vector restore failed: {restore_error}")
                if restore_errors:
                    logger.critical(
                        "Memory import compensation failed: %s",
                        "; ".join(restore_errors),
                    )
                    detail = (
                        "Memory vector rebuild failed and rollback was incomplete — "
                        "persisted memory and vector state may be inconsistent."
                    )
                else:
                    detail = "Memory vector rebuild failed — the import was rolled back."
                raise HTTPException(
                    503,
                    detail,
                )
            imported.append(f"{added} memories")

        # ── Skills ──
        if "skills" in body and isinstance(body["skills"], list):
            existing = skills_manager.load_all()
            # Dedup against THIS user's own skills only. Using every tenant's
            # rows (load_all) meant a skill whose id/name/title matched any
            # other user's was silently skipped, so the importing user lost
            # their own data — same cross-tenant bug fixed for memories above.
            # The full store is still saved back below.
            own = [s for s in existing if s.get("owner") == user]
            existing_names = {s.get("name") for s in own if s.get("name")}
            existing_ids = {s.get("id") for s in own if s.get("id")}
            existing_titles = {
                (s.get("title") or s.get("description") or "").strip().lower()
                for s in own
            }
            added = 0
            for skill in body["skills"]:
                if not isinstance(skill, dict):
                    continue
                title = (
                    skill.get("title") or skill.get("description")
                    or skill.get("name") or ""
                ).strip()
                if not title:
                    continue
                sid = skill.get("id") or skill.get("name")
                if sid and sid in existing_ids:
                    continue
                nm = skill.get("name")
                if nm and nm in existing_names:
                    continue
                if title.lower() in existing_titles:
                    continue
                owner = skill.get("owner")
                if user and not owner:
                    owner = user
                # Skills live on disk as SKILL.md files; the old JSON-era
                # skills_manager.save() no longer exists. Write each new skill
                # via add_skill (source="user" skips auto-dedup — this is an
                # explicit backup restore).
                result = skills_manager.add_skill(
                    title=title,
                    name=skill.get("name"),
                    description=skill.get("description"),
                    problem=skill.get("problem", ""),
                    solution=skill.get("solution", ""),
                    steps=skill.get("steps"),
                    tags=skill.get("tags"),
                    source="user",
                    teacher_model=skill.get("teacher_model"),
                    confidence=skill.get("confidence", 0.8),
                    owner=owner,
                    category=skill.get("category", "general"),
                    when_to_use=skill.get("when_to_use"),
                    procedure=skill.get("procedure"),
                    pitfalls=skill.get("pitfalls"),
                    verification=skill.get("verification"),
                    platforms=skill.get("platforms"),
                    requires_toolsets=skill.get("requires_toolsets"),
                    fallback_for_toolsets=skill.get("fallback_for_toolsets"),
                    status=skill.get("status", "draft"),
                    version=skill.get("version", "1.0.0"),
                )
                if result.get("_deduped"):
                    continue
                if result.get("name"):
                    existing_names.add(result["name"])
                if result.get("id"):
                    existing_ids.add(result["id"])
                existing_titles.add(title.lower())
                added += 1
            imported.append(f"{added} skills")

        # ── Presets ──
        if "presets" in body and isinstance(body["presets"], dict):
            current = preset_manager.get_all()
            for key, value in body["presets"].items():
                if isinstance(value, dict):
                    current[key] = value
                elif isinstance(value, list):
                    current[key] = value
            preset_manager.save(current)
            imported.append("presets")

        # ── Settings ──
        if "settings" in body and isinstance(body["settings"], dict):
            current = load_settings()
            current.update(body["settings"])
            save_settings(current)
            imported.append("settings")

        # ── Features ──
        if "features" in body and isinstance(body["features"], dict):
            current = load_features()
            current.update(body["features"])
            save_features(current)
            imported.append("features")

        # ── Preferences ──
        if "preferences" in body and isinstance(body["preferences"], dict):
            from routes.prefs_routes import _load_for_user, _save_for_user
            current = _load_for_user(user)
            current.update(body["preferences"])
            _save_for_user(user, current)
            imported.append("preferences")

        # ── Calendar, tasks, task runs, notes (v2) ──
        if any(key in body for key in ("calendar", "tasks", "notes")):
            db = SessionLocal()
            try:
                restored = _restore_database_domains(db, body, user)
                db.commit()
            except (TypeError, ValueError) as e:
                db.rollback()
                raise HTTPException(400, f"Invalid v2 backup data: {e}")
            except Exception:
                db.rollback()
                logger.exception("Database-domain backup import failed")
                raise HTTPException(500, "Calendar/task/note import failed; nothing was committed")
            finally:
                db.close()
            imported.extend(restored)

        if not imported:
            return {"ok": False, "message": "No recognized data found in the file"}

        return {"ok": True, "imported": imported, "message": f"Imported: {', '.join(imported)}"}

    return router
