"""CRUD routes for scheduled tasks."""

import json
import logging
import secrets
import uuid
from datetime import datetime
from typing import Optional, Dict, Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.database import SessionLocal, ScheduledTask, TaskRun
from core.constants import internal_api_base
from src.auth_helpers import get_current_user
from src.constants import DATA_DIR, EMAIL_URGENCY_CACHE_DIR
from src.task_scheduler import compute_next_run, HOUSEKEEPING_DEFAULTS
from routes.prefs_routes import _load_for_user, _save_for_user

logger = logging.getLogger(__name__)


def _maybe_cascade_calendar_event(task) -> None:
    """Delete the linked calendar event when a cookbook_serve task is
    removed. Two lookup strategies:

      1. PRIMARY — `cookbook_event_uid` marker stashed in task.prompt
         by cookbookSchedule.js right after creating the event. Direct
         UID match, no ambiguity.

      2. FALLBACK — for tasks created before the marker was wired up
         (or when the PATCH to add the marker failed silently), scan
         the Cookbook calendar for events whose summary equals the
         task name and delete the matches.

    Best-effort throughout: errors are logged but never block the task
    deletion itself."""
    if not task or task.task_type != "action" or task.action != "cookbook_serve":
        return

    import httpx
    from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN
    headers = {INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN}
    if task.owner:
        headers["X-Odysseus-Owner"] = task.owner

    # Strategy 1: explicit UID marker in prompt.
    event_uid = ""
    if task.prompt:
        try:
            cfg = json.loads(task.prompt)
            if isinstance(cfg, dict):
                event_uid = (cfg.get("cookbook_event_uid") or "").strip()
        except Exception:
            pass

    def _try_delete(uid: str) -> bool:
        try:
            with httpx.Client(timeout=10) as client:
                r = client.delete(
                    f"{internal_api_base()}/api/calendar/events/{uid}",
                    headers=headers,
                )
                if r.status_code >= 400:
                    logger.info(
                        f"task delete: cascade calendar event {uid} returned "
                        f"HTTP {r.status_code}"
                    )
                    return False
                return True
        except Exception as e:
            logger.warning(f"task delete: cascade calendar event {uid} failed: {e}")
            return False

    if event_uid:
        _try_delete(event_uid)
        return

    # Strategy 2: scan the Cookbook calendar for matching summaries.
    # Only runs for tasks missing the marker (old tasks or PATCH failures).
    if not task.name:
        return
    try:
        with httpx.Client(timeout=10) as client:
            # Find the Cookbook calendar.
            cal_r = client.get(f"{internal_api_base()}/api/calendar/calendars", headers=headers)
            if cal_r.status_code >= 400:
                return
            cals = (cal_r.json() or {}).get("calendars", [])
            cookbook_cal = next(
                (c for c in cals if (c.get("name") or "").lower() == "cookbook"),
                None,
            )
            if not cookbook_cal:
                return
            cal_href = cookbook_cal.get("href") or cookbook_cal.get("id") or ""
            # List events in a wide window to catch recurring + upcoming.
            from datetime import datetime as _dt, timedelta as _td, timezone as _tz
            now = _dt.now(_tz.utc)
            start = (now - _td(days=30)).isoformat()
            end = (now + _td(days=365)).isoformat()
            ev_r = client.get(
                f"{internal_api_base()}/api/calendar/events",
                params={"start": start, "end": end, "calendar": cal_href},
                headers=headers,
            )
            if ev_r.status_code >= 400:
                return
            events = (ev_r.json() or {}).get("events", [])
            # Match by exact summary. Tasks named "Serve: <model>" are
            # created from the schedule modal; the event's summary mirrors
            # the task name 1:1 by design.
            target = (task.name or "").strip()
            uids_to_delete = set()
            for ev in events:
                if (ev.get("summary") or "").strip() != target:
                    continue
                uid = ev.get("uid") or ev.get("id") or ""
                # Strip the "::occurrence" suffix on recurring expansions —
                # we want to delete the MASTER once, not each instance.
                if "::" in uid:
                    uid = uid.split("::", 1)[0]
                if uid:
                    uids_to_delete.add(uid)
            for uid in uids_to_delete:
                _try_delete(uid)
            if uids_to_delete:
                logger.info(
                    f"task delete: cascade matched {len(uids_to_delete)} calendar event(s) "
                    f"by summary fallback for task {task.id} ({target!r})"
                )
    except Exception as e:
        logger.warning(f"task delete: cascade fallback scan failed: {e}")


class TaskCreate(BaseModel):
    name: Optional[str] = None
    prompt: Optional[str] = None
    task_type: str = "llm"                        # "llm" | "action" | "research"
    action: Optional[str] = None                  # builtin action name
    schedule: Optional[str] = None                # "once" | "daily" | "weekly" | "monthly" | "cron"
    scheduled_time: str = "09:00"                 # HH:MM
    scheduled_day: Optional[int] = None           # day-of-week (0=Mon) or day-of-month
    scheduled_date: Optional[str] = None          # ISO datetime for "once"
    cron_expression: Optional[str] = None         # cron string e.g. "*/5 * * * *"
    trigger_type: str = "schedule"                # "schedule" | "event" | "webhook"
    trigger_event: Optional[str] = None           # e.g. "session_created"
    trigger_count: Optional[int] = None           # fire every N events
    output_target: str = "session"
    model: Optional[str] = None
    endpoint_url: Optional[str] = None
    then_task_id: Optional[str] = None            # chain: run this task after success
    notifications_enabled: Optional[bool] = None  # None lets action-specific defaults apply


class TaskUpdate(BaseModel):
    name: Optional[str] = None
    prompt: Optional[str] = None
    task_type: Optional[str] = None
    action: Optional[str] = None
    schedule: Optional[str] = None
    scheduled_time: Optional[str] = None
    scheduled_day: Optional[int] = None
    scheduled_date: Optional[str] = None
    cron_expression: Optional[str] = None
    trigger_type: Optional[str] = None
    trigger_event: Optional[str] = None
    trigger_count: Optional[int] = None
    output_target: Optional[str] = None
    model: Optional[str] = None
    endpoint_url: Optional[str] = None
    then_task_id: Optional[str] = None
    notifications_enabled: Optional[bool] = None


def _display_task_name(t: ScheduledTask) -> str:
    defs = HOUSEKEEPING_DEFAULTS.get(t.action) if t.action else None
    if defs and (t.name or "") in set(defs.get("legacy_names") or []):
        return defs["name"]
    return t.name


def _task_to_dict(t: ScheduledTask, include_last_run_result: bool = False) -> dict:
    defs = HOUSEKEEPING_DEFAULTS.get(t.action) if t.action else None
    d = {
        "id": t.id,
        "name": _display_task_name(t),
        "prompt": t.prompt,
        "task_type": t.task_type or "llm",
        "action": t.action,
        "schedule": t.schedule,
        "scheduled_time": t.scheduled_time,
        "scheduled_day": t.scheduled_day,
        "scheduled_date": t.scheduled_date.isoformat() + "Z" if t.scheduled_date else None,
        "cron_expression": t.cron_expression,
        "trigger_type": t.trigger_type or "schedule",
        "trigger_event": t.trigger_event,
        "trigger_count": t.trigger_count,
        "trigger_counter": t.trigger_counter or 0,
        "next_run": t.next_run.isoformat() + "Z" if t.next_run else None,
        "last_run": t.last_run.isoformat() + "Z" if t.last_run else None,
        "status": t.status,
        "output_target": t.output_target,
        "session_id": t.session_id,
        "crew_member_id": getattr(t, "crew_member_id", None),
        "model": t.model,
        "endpoint_url": t.endpoint_url,
        "run_count": t.run_count or 0,
        "then_task_id": t.then_task_id,
        "notifications_enabled": bool(getattr(t, "notifications_enabled", True)),
        "webhook_token": t.webhook_token if (t.trigger_type or "schedule") == "webhook" else None,
        "created_at": t.created_at.isoformat() + "Z" if t.created_at else None,
        "updated_at": t.updated_at.isoformat() + "Z" if t.updated_at else None,
    }
    # Built-in housekeeping tasks (identified by their action) are flagged so
    # the UI can mark them and offer "revert to default" once altered.
    d["is_builtin"] = defs is not None
    if defs:
        default_names = {defs["name"], *set(defs.get("legacy_names") or [])}
        d["is_modified"] = (
            (t.name or "") not in default_names
            or (t.schedule or "") != (defs["schedule"] or "")
            or (t.scheduled_time or "") != (defs["scheduled_time"] or "")
            or (t.cron_expression or "") != (defs["cron_expression"] or "")
        )
    else:
        d["is_modified"] = False
    if include_last_run_result and t.runs:
        last = t.runs[0]  # ordered desc by started_at
        d["last_run_status"] = last.status
        d["last_run_result"] = (last.result or last.error or "")[:500]
    return d


def _run_to_dict(r: TaskRun) -> dict:
    return {
        "id": r.id,
        "task_id": r.task_id,
        "started_at": r.started_at.isoformat() + "Z" if r.started_at else None,
        "finished_at": r.finished_at.isoformat() + "Z" if r.finished_at else None,
        "status": r.status,
        "result": r.result,
        "error": r.error,
        "tokens_used": r.tokens_used,
        "model": r.model,
    }


def _run_research_id(task: ScheduledTask) -> str:
    if (task.task_type or "llm") == "research" and task.session_id:
        return task.session_id
    return ""


def _resolve_run_endpoint(db, task: ScheduledTask, run: TaskRun) -> str:
    """Best-effort endpoint URL for reopening a task run in chat."""
    if getattr(task, "endpoint_url", None):
        return task.endpoint_url or ""

    try:
        if getattr(task, "session_id", None):
            from core.database import Session as DbSession
            sess = db.query(DbSession).filter(DbSession.id == task.session_id).first()
            if sess and sess.endpoint_url:
                return sess.endpoint_url or ""
    except Exception:
        pass

    model = (getattr(run, "model", None) or getattr(task, "model", None) or "").strip()
    if not model:
        return ""

    try:
        from core.database import ModelEndpoint
        eps = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all()
        for ep in eps:
            cached = []
            if ep.cached_models:
                try:
                    cached = json.loads(ep.cached_models) or []
                except Exception:
                    cached = []
            if model in cached:
                return ep.base_url or ""
    except Exception:
        pass
    return ""


def setup_task_routes(task_scheduler) -> APIRouter:
    router = APIRouter(prefix="/api/tasks", tags=["tasks"])

    def _owner(request: Request):
        return get_current_user(request)

    async def _generate_task_name(prompt: str, owner: Optional[str] = None) -> str:
        """Use LLM to generate a short task name from the prompt."""
        try:
            from src.llm_core import llm_call_async
            from core.database import Session as DbSession
            db = SessionLocal()
            try:
                q = db.query(DbSession).filter(
                    DbSession.endpoint_url.isnot(None),
                    DbSession.model.isnot(None),
                )
                if owner:
                    q = q.filter(DbSession.owner == owner)
                recent = q.order_by(DbSession.created_at.desc()).first()
                if not recent:
                    return prompt[:50].strip()
                url, model = recent.endpoint_url, recent.model
                headers = recent.headers or {}
            finally:
                db.close()

            result = await llm_call_async(
                url=url, model=model,
                messages=[
                    {"role": "system", "content": "Generate a short title (3-5 words, no quotes) for this scheduled task. Reply with ONLY the title, nothing else."},
                    {"role": "user", "content": prompt[:500]},
                ],
                max_tokens=20,
                headers=headers,
                timeout=15,
            )
            title = result.strip().strip('"\'').strip()
            return title[:60] if title else prompt[:50].strip()
        except Exception:
            first = prompt.split('\n')[0].split('.')[0].strip()
            return first[:50] if first else "Untitled Task"

    @router.get("")
    async def list_tasks(request: Request, status: Optional[str] = None,
                         include_last_run: bool = False):
        user = _owner(request)
        if user:
            await task_scheduler.ensure_defaults(user)
        else:
            db_seed = SessionLocal()
            try:
                owners = {
                    row[0] for row in db_seed.query(ScheduledTask.owner)
                    .filter(ScheduledTask.task_type == "action")
                    .filter(ScheduledTask.action.in_(list(HOUSEKEEPING_DEFAULTS.keys())))
                    .all()
                    if row[0]
                }
            finally:
                db_seed.close()
            for owner in owners:
                await task_scheduler.ensure_defaults(owner)
        db = SessionLocal()
        try:
            q = db.query(ScheduledTask)
            if user:
                q = q.filter(ScheduledTask.owner == user)
            if status:
                q = q.filter(ScheduledTask.status == status)
            tasks = q.order_by(ScheduledTask.created_at.desc()).all()
            return {"tasks": [_task_to_dict(t, include_last_run_result=include_last_run) for t in tasks]}
        finally:
            db.close()

    @router.get("/onboarding")
    async def get_tasks_onboarding(request: Request):
        user = _owner(request)
        prefs = _load_for_user(user) or {}
        return {
            "opened": bool(prefs.get("tasks_opened")),
            "enabled": bool(prefs.get("tasks_enabled")),
        }

    @router.post("/onboarding")
    async def update_tasks_onboarding(request: Request, body: dict):
        user = _owner(request)
        prefs = _load_for_user(user) or {}
        prefs["tasks_opened"] = True
        enable = bool(body.get("enabled"))
        if enable:
            prefs["tasks_enabled"] = True
        _save_for_user(user, prefs)
        if user:
            await task_scheduler.ensure_defaults(user)

        resumed = 0
        if enable:
            db = SessionLocal()
            try:
                tasks = db.query(ScheduledTask).filter(
                    ScheduledTask.owner == user,
                    ScheduledTask.task_type == "action",
                    ScheduledTask.action.in_(list(HOUSEKEEPING_DEFAULTS.keys())),
                ).all()
                for task in tasks:
                    defs = HOUSEKEEPING_DEFAULTS.get(task.action or "")
                    if defs and defs.get("ship_paused"):
                        continue
                    if task.status == "active":
                        continue
                    task.status = "active"
                    if (task.trigger_type or "schedule") == "schedule":
                        task.next_run = compute_next_run(
                            task.schedule,
                            task.scheduled_time,
                            task.scheduled_day,
                            task.scheduled_date,
                            cron_expression=task.cron_expression,
                        )
                    resumed += 1
                db.commit()
            finally:
                db.close()
        return {"ok": True, "opened": True, "enabled": bool(prefs.get("tasks_enabled")), "resumed": resumed}

    # Actions that execute shell/SSH commands — restricted to admins.
    # Non-admin users cannot create tasks with these action types via the
    # API. See review CRIT-C.
    _ADMIN_ONLY_ACTIONS = {"run_local", "run_script", "ssh_command"}

    def _is_admin(user: str | None) -> bool:
        if not user:
            return False
        # In-process tool-loopback marker — AuthMiddleware validated
        # the internal token + loopback client before stamping this,
        # so treat as admin-equivalent.
        if user == "internal-tool":
            return True
        try:
            from core.auth import AuthManager
            auth = AuthManager()
            if not auth.is_configured:
                # Unconfigured single-user deploy: trust the local owner.
                return True
            return bool(auth.is_admin(user))
        except Exception:
            return False

    def _validate_then_task_id(db, then_task_id: Optional[str], user: Optional[str], current_task_id: Optional[str] = None) -> Optional[str]:
        target_id = (then_task_id or "").strip()
        if not target_id:
            return None
        if current_task_id and target_id == current_task_id:
            raise HTTPException(400, "Task cannot chain to itself")
        q = db.query(ScheduledTask).filter(ScheduledTask.id == target_id)
        if user:
            q = q.filter(ScheduledTask.owner == user)
        target = q.first()
        if not target:
            raise HTTPException(404, "Chained task not found")
        return target.id

    @router.post("")
    async def create_task(request: Request, req: TaskCreate):
        user = _owner(request)

        # Validate
        if req.task_type in ("llm", "research") and not req.prompt:
            raise HTTPException(400, "Prompt is required for LLM/research tasks")
        if req.task_type == "action" and not req.action:
            raise HTTPException(400, "Action name is required for action tasks")
        # Block shell-executing action types for non-admins. action_run_local
        # uses subprocess.run(shell=True) and ssh_command / run_script run
        # arbitrary commands.
        if req.task_type == "action" and req.action in _ADMIN_ONLY_ACTIONS and not _is_admin(user):
            raise HTTPException(403, f"Action '{req.action}' requires admin privileges")
        if req.trigger_type == "schedule" and not req.schedule:
            raise HTTPException(400, "Schedule is required for schedule-triggered tasks")
        if req.trigger_type == "schedule" and req.schedule == "cron" and not req.cron_expression:
            raise HTTPException(400, "Cron expression is required for cron schedule")
        if req.trigger_type == "schedule" and req.schedule == "cron" and req.cron_expression:
            try:
                from croniter import croniter
                croniter(req.cron_expression)
            except Exception:
                raise HTTPException(400, "Invalid cron expression")
        if req.trigger_type == "event" and not req.trigger_event:
            raise HTTPException(400, "Event name is required for event-triggered tasks")
        if req.trigger_type == "event" and not req.trigger_count:
            raise HTTPException(400, "Trigger count is required for event-triggered tasks")

        # Auto-generate name
        name = req.name
        if not name:
            if req.task_type == "action":
                from src.builtin_actions import BUILTIN_ACTION_INFO
                name = BUILTIN_ACTION_INFO.get(req.action, req.action or "Action Task")
            elif req.prompt:
                name = await _generate_task_name(req.prompt, owner=user)
            else:
                name = "Untitled Task"

        # Compute next_run for schedule-triggered tasks
        next_run = None
        sched_date = None
        if req.trigger_type == "schedule":
            if req.schedule == "once" and req.scheduled_date:
                try:
                    sched_date = datetime.fromisoformat(req.scheduled_date.replace("Z", "+00:00")).replace(tzinfo=None)
                except ValueError:
                    raise HTTPException(400, "Invalid scheduled_date format")
            next_run = compute_next_run(
                req.schedule, req.scheduled_time,
                req.scheduled_day, sched_date,
                cron_expression=req.cron_expression,
            )

        # Generate webhook token if needed
        webhook_token = None
        if req.trigger_type == "webhook":
            webhook_token = secrets.token_urlsafe(32)

        task_id = str(uuid.uuid4())
        db = SessionLocal()
        try:
            then_task_id = _validate_then_task_id(db, req.then_task_id, user)
            notifications_enabled = (
                False if req.task_type == "action" and req.notifications_enabled is None
                else bool(req.notifications_enabled) if req.notifications_enabled is not None
                else True
            )
            # Validate chained task belongs to same owner
            if req.then_task_id:
                chain_target = db.query(ScheduledTask).filter(
                    ScheduledTask.id == req.then_task_id
                ).first()
                if not chain_target:
                    raise HTTPException(400, "Chained task not found")
                if chain_target.owner != user:
                    raise HTTPException(403, "Cannot chain to another user's task")
            task = ScheduledTask(
                id=task_id,
                owner=user,
                name=name,
                prompt=req.prompt,
                task_type=req.task_type,
                action=req.action,
                schedule=req.schedule,
                scheduled_time=req.scheduled_time,
                scheduled_day=req.scheduled_day,
                scheduled_date=sched_date,
                cron_expression=req.cron_expression,
                trigger_type=req.trigger_type,
                trigger_event=req.trigger_event,
                trigger_count=req.trigger_count,
                trigger_counter=0,
                next_run=next_run,
                status="active" if (req.trigger_type in ("event", "webhook") or next_run) else "completed",
                output_target=req.output_target,
                model=req.model or None,
                endpoint_url=req.endpoint_url or None,
                then_task_id=then_task_id,
                webhook_token=webhook_token,
                notifications_enabled=notifications_enabled,
            )
            db.add(task)
            db.commit()
            db.refresh(task)
            return _task_to_dict(task)
        finally:
            db.close()

    @router.get("/notifications")
    async def get_notifications(request: Request):
        """Return and clear pending task-run notifications for the
        current user. Anonymous callers get nothing (prevents
        cross-tenant drain — see review CRIT-B)."""
        user = _owner(request)
        if not user:
            return {"notifications": []}
        notes = task_scheduler.pop_notifications(owner=user)
        return {"notifications": notes}

    @router.post("/{task_id}/clear-cache")
    async def clear_task_cache(request: Request, task_id: str):
        """Clear derived cache for one built-in task."""
        user = _owner(request)
        db = SessionLocal()
        try:
            task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
            if not task:
                raise HTTPException(404, "Task not found")
            if user and task.owner != user:
                raise HTTPException(403, "Access denied")
            action = task.action or ""
        finally:
            db.close()

        cache_tables = {
            "summarize_emails": ("email_summaries",),
            "draft_email_replies": ("email_ai_replies",),
            "extract_email_events": ("email_calendar_extractions",),
            "learn_sender_signatures": ("sender_signatures",),
            "check_email_urgency": ("email_tags", "email_urgency_alerts"),
        }
        tables = cache_tables.get(action)
        if not tables:
            raise HTTPException(400, "This task has no clearable cache")

        import sqlite3
        from pathlib import Path
        from routes.email_helpers import SCHEDULED_DB, OWNER_SCOPED_EMAIL_CACHE_TABLES, _email_cache_owner_clause

        cleared = {}
        conn = sqlite3.connect(SCHEDULED_DB)
        try:
            for table in tables:
                try:
                    if table == "email_tags" and user:
                        before = conn.execute(
                            "SELECT COUNT(*) FROM email_tags WHERE owner = ? OR owner = ''",
                            (user,),
                        ).fetchone()[0]
                        conn.execute("DELETE FROM email_tags WHERE owner = ? OR owner = ''", (user,))
                    elif table in OWNER_SCOPED_EMAIL_CACHE_TABLES and user:
                        owner_clause, owner_params = _email_cache_owner_clause(user)
                        before = conn.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE {owner_clause}",
                            owner_params,
                        ).fetchone()[0]
                        conn.execute(f"DELETE FROM {table} WHERE {owner_clause}", owner_params)
                    else:
                        before = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                        conn.execute(f"DELETE FROM {table}")
                    cleared[table] = int(before or 0)
                except sqlite3.OperationalError:
                    cleared[table] = 0
            conn.commit()
        finally:
            conn.close()

        removed_files = 0
        if action == "check_email_urgency":
            cache_dir = Path(EMAIL_URGENCY_CACHE_DIR)
            if cache_dir.exists():
                for child in cache_dir.glob("*.json"):
                    try:
                        child.unlink()
                        removed_files += 1
                    except Exception:
                        pass
            owner_slug = "".join(c if (c.isalnum() or c in "-_.@") else "_" for c in (user or "default"))
            for state_path in [Path(DATA_DIR) / f"email_urgency_state_{owner_slug}.json"]:
                try:
                    if state_path.exists():
                        state_path.unlink()
                        removed_files += 1
                except Exception:
                    pass

        return {"ok": True, "action": action, "cleared": cleared, "files": removed_files}

    @router.get("/{task_id}")
    async def get_task(request: Request, task_id: str):
        user = _owner(request)
        db = SessionLocal()
        try:
            task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
            if not task:
                raise HTTPException(404, "Task not found")
            if user and task.owner != user:
                raise HTTPException(403, "Access denied")
            return _task_to_dict(task)
        finally:
            db.close()

    @router.put("/{task_id}")
    async def update_task(request: Request, task_id: str, req: TaskUpdate):
        user = _owner(request)
        db = SessionLocal()
        try:
            task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
            if not task:
                raise HTTPException(404, "Task not found")
            if user and task.owner != user:
                raise HTTPException(403, "Access denied")

            if req.name is not None:
                task.name = req.name
            if req.prompt is not None:
                task.prompt = req.prompt
            if req.task_type is not None:
                task.task_type = req.task_type
            if req.action is not None:
                # Same admin-only gate as create — see CRIT-C.
                if req.action in _ADMIN_ONLY_ACTIONS and not _is_admin(user):
                    raise HTTPException(403, f"Action '{req.action}' requires admin privileges")
                task.action = req.action
            if req.output_target is not None:
                task.output_target = req.output_target
            if req.model is not None:
                task.model = req.model or None
            if req.endpoint_url is not None:
                task.endpoint_url = req.endpoint_url or None
            if req.trigger_type is not None:
                # Generate webhook token when switching to webhook trigger
                if req.trigger_type == "webhook" and not task.webhook_token:
                    task.webhook_token = secrets.token_urlsafe(32)
                task.trigger_type = req.trigger_type
            if req.trigger_event is not None:
                task.trigger_event = req.trigger_event
            if req.trigger_count is not None:
                task.trigger_count = req.trigger_count
            if req.then_task_id is not None:
                task.then_task_id = _validate_then_task_id(db, req.then_task_id, user, current_task_id=task.id)
            if req.notifications_enabled is not None:
                task.notifications_enabled = bool(req.notifications_enabled)
            if req.cron_expression is not None:
                if req.cron_expression:
                    try:
                        from croniter import croniter
                        croniter(req.cron_expression)
                    except Exception:
                        raise HTTPException(400, "Invalid cron expression")
                task.cron_expression = req.cron_expression or None

            # Recompute next_run if schedule changed
            schedule_changed = False
            if req.schedule is not None:
                task.schedule = req.schedule
                schedule_changed = True
            if req.scheduled_time is not None:
                task.scheduled_time = req.scheduled_time
                schedule_changed = True
            if req.scheduled_day is not None:
                task.scheduled_day = req.scheduled_day
                schedule_changed = True
            if req.scheduled_date is not None:
                try:
                    task.scheduled_date = datetime.fromisoformat(
                        req.scheduled_date.replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                except ValueError:
                    raise HTTPException(400, "Invalid scheduled_date format")
                schedule_changed = True

            if req.cron_expression is not None:
                schedule_changed = True

            if schedule_changed and task.status == "active" and (task.trigger_type or "schedule") == "schedule":
                task.next_run = compute_next_run(
                    task.schedule, task.scheduled_time,
                    task.scheduled_day, task.scheduled_date,
                    cron_expression=task.cron_expression,
                )

            db.commit()
            db.refresh(task)
            return _task_to_dict(task)
        finally:
            db.close()

    @router.delete("/{task_id}")
    async def delete_task(request: Request, task_id: str):
        user = _owner(request)
        db = SessionLocal()
        try:
            task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
            if not task:
                raise HTTPException(404, "Task not found")
            if user and task.owner != user:
                raise HTTPException(403, "Access denied")
            # Cascade: cookbook_serve tasks may have a linked calendar
            # event (created via the "Create event in calendar" toggle
            # in the schedule modal). If so, delete the calendar event
            # too so the calendar doesn't end up holding a phantom event
            # for a task that no longer exists.
            _maybe_cascade_calendar_event(task)
            db.delete(task)
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    @router.post("/{task_id}/pause")
    async def pause_task(request: Request, task_id: str):
        user = _owner(request)
        db = SessionLocal()
        try:
            task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
            if not task:
                raise HTTPException(404, "Task not found")
            if user and task.owner != user:
                raise HTTPException(403, "Access denied")
            task.status = "paused"
            db.commit()
            return {"ok": True, "status": "paused"}
        finally:
            db.close()

    @router.post("/{task_id}/resume")
    async def resume_task(request: Request, task_id: str):
        user = _owner(request)
        db = SessionLocal()
        try:
            task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
            if not task:
                raise HTTPException(404, "Task not found")
            if user and task.owner != user:
                raise HTTPException(403, "Access denied")
            task.status = "active"
            if (task.trigger_type or "schedule") == "schedule":
                task.next_run = compute_next_run(
                    task.schedule, task.scheduled_time,
                    task.scheduled_day, task.scheduled_date,
                    cron_expression=task.cron_expression,
                )
            db.commit()
            return {"ok": True, "status": "active", "next_run": task.next_run.isoformat() + "Z" if task.next_run else None}
        finally:
            db.close()

    @router.post("/{task_id}/revert")
    async def revert_task(request: Request, task_id: str):
        """Reset a built-in (housekeeping) task to its default config."""
        user = _owner(request)
        db = SessionLocal()
        try:
            task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
            if not task:
                raise HTTPException(404, "Task not found")
            if user and task.owner != user:
                raise HTTPException(403, "Access denied")
            defs = HOUSEKEEPING_DEFAULTS.get(task.action) if task.action else None
            if not defs:
                raise HTTPException(400, "Not a built-in task")
            task.name = defs["name"]
            task.schedule = defs["schedule"]
            task.scheduled_time = defs["scheduled_time"]
            task.scheduled_day = None
            task.scheduled_date = None
            task.cron_expression = defs["cron_expression"]
            task.trigger_type = defs.get("trigger_type", "schedule")
            task.trigger_event = defs.get("trigger_event")
            task.trigger_count = defs.get("trigger_count")
            task.trigger_counter = 0
            task.prompt = None
            task.model = None
            task.endpoint_url = None
            task.status = "paused" if defs.get("ship_paused") else "active"
            task.next_run = None
            if task.trigger_type == "schedule":
                task.next_run = compute_next_run(
                    defs["schedule"], defs["scheduled_time"], None, None,
                    cron_expression=defs["cron_expression"],
                )
            db.commit()
            db.refresh(task)
            return {"ok": True, "task": _task_to_dict(task)}
        finally:
            db.close()

    @router.post("/{task_id}/run")
    async def run_task_now(request: Request, task_id: str, force: bool = False):
        user = _owner(request)
        db = SessionLocal()
        try:
            task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
            if not task:
                raise HTTPException(404, "Task not found")
            if user and task.owner != user:
                raise HTTPException(403, "Access denied")
        finally:
            db.close()
        started = await task_scheduler.run_task_now(task_id, force=force)
        if not started:
            raise HTTPException(409, "Task is already running")
        return {"ok": True, "message": "Task triggered" + (" in parallel" if force else "")}

    @router.post("/{task_id}/stop")
    async def stop_task_now(request: Request, task_id: str):
        user = _owner(request)
        db = SessionLocal()
        try:
            task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
            if not task:
                raise HTTPException(404, "Task not found")
            if user and task.owner != user:
                raise HTTPException(403, "Access denied")
        finally:
            db.close()
        stopped = await task_scheduler.stop_task(task_id)
        if not stopped:
            raise HTTPException(404, "Task is not running")
        return {"ok": True, "message": "Task stopped"}

    @router.get("/runs/recent")
    async def list_recent_runs(request: Request, limit: int = 50):
        """Recent task runs across ALL tasks for this owner. Drives the Activity view."""
        user = _owner(request)
        limit = max(1, min(limit, 200))
        db = SessionLocal()
        try:
            q = db.query(TaskRun, ScheduledTask).join(
                ScheduledTask, TaskRun.task_id == ScheduledTask.id
            )
            if user:
                # Strict owner scope — was previously OR'ing in `owner IS NULL`
                # rows for "legacy single-user" back-compat, but that leaks any
                # legacy/migrated task's full result text to every authenticated
                # user. _migrate_assign_legacy_owner runs on startup to claim
                # legacy rows for the admin, so the OR-NULL path is no longer
                # needed for any sane deploy.
                q = q.filter(ScheduledTask.owner == user)
            # Pull a little extra before de-duping. When auth is bypassed on a
            # local browser session, legacy/default tasks from multiple owners
            # can be visible together; the built-in urgent-email scanner then
            # produces several identical "no email accounts configured" rows in
            # the same minute. Keep the task records intact, but collapse those
            # duplicate Activity rows for display.
            rows = q.order_by(TaskRun.started_at.desc()).limit(limit * 3).all()
            deduped = []
            seen_urgency_rows = set()
            for r, t in rows:
                if (t.action or "") == "check_email_urgency":
                    ts = r.started_at.replace(second=0, microsecond=0) if r.started_at else None
                    text = (r.result or r.error or "").strip()
                    key = (ts, r.status or "", text)
                    if key in seen_urgency_rows:
                        continue
                    seen_urgency_rows.add(key)
                deduped.append((r, t))
                if len(deduped) >= limit:
                    break
            return {
                "runs": [
                    {
                        **_run_to_dict(r),
                        "task_name": _display_task_name(t),
                        "task_type": t.task_type or "llm",
                        "action": t.action,
                        # Model + endpoint the task ran on, so the Activity
                        # view's "Open in chat" can reuse the same model.
                        "model": r.model or t.model or "",
                        "endpoint_url": _resolve_run_endpoint(db, t, r),
                        "session_id": t.session_id or "",
                        "research_id": _run_research_id(t),
                        # Where the task delivered its result — the Activity tab
                        # uses this to filter notification rows in/out.
                        "output_target": t.output_target or "session",
                    }
                    for r, t in deduped
                ]
            }
        finally:
            db.close()

    @router.get("/{task_id}/runs")
    async def list_runs(request: Request, task_id: str, limit: int = 20, offset: int = 0):
        user = _owner(request)
        db = SessionLocal()
        try:
            task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
            if not task:
                raise HTTPException(404, "Task not found")
            if user and task.owner != user:
                raise HTTPException(403, "Access denied")
            runs = db.query(TaskRun).filter(TaskRun.task_id == task_id)\
                .order_by(TaskRun.started_at.desc())\
                .offset(offset).limit(limit).all()
            total = db.query(TaskRun).filter(TaskRun.task_id == task_id).count()
            return {"runs": [_run_to_dict(r) for r in runs], "total": total}
        finally:
            db.close()

    @router.get("/meta/output-targets")
    async def list_output_targets(request: Request):
        """List available output targets — only delivery/send tools, not all MCP tools."""
        _owner(request)
        targets = [
            {"value": "session", "label": "Session", "description": "Save result to a chat session"},
            {"value": "notification", "label": "Notification", "description": "Push a browser notification with the result (also saved to the session for history)"},
            {"value": "email", "label": "Email me", "description": "Send result through your configured SMTP account"},
        ]
        # Only include tools whose NAME clearly indicates an outbound delivery
        # action — match by verb in the tool name, not by any mention of "email"
        # in the description (which falsely picked up search_email, list_email,
        # etc.). Also exclude read/search/list tools whose names happen to start
        # with a delivery verb.
        _DELIVERY_VERBS = ("send", "notify", "post", "publish", "draft", "dispatch", "deliver")
        _NON_DELIVERY = (
            "search", "list", "get", "find", "read", "fetch", "view",
            "tag", "label", "move", "archive", "delete", "mark", "schedule",
        )
        try:
            from src.tool_utils import get_mcp_manager
            mcp = get_mcp_manager()
            if mcp:
                for tool in mcp.get_all_tools():
                    name_lower = tool.get("name", "").lower()
                    if any(x in name_lower for x in _NON_DELIVERY):
                        continue
                    if not any(v in name_lower for v in _DELIVERY_VERBS):
                        continue
                    targets.append({
                        "value": tool["qualified_name"],
                        "label": f"{tool['server_name']} → {tool['name']}",
                        "description": tool.get("description", ""),
                    })
        except Exception:
            pass
        return {"targets": targets}

    @router.get("/meta/actions")
    async def list_actions(request: Request):
        """List available built-in actions."""
        user = _owner(request)
        from src.builtin_actions import BUILTIN_ACTION_INFO
        return {"actions": [
            {"name": name, "description": desc}
            for name, desc in BUILTIN_ACTION_INFO.items()
            if name not in _ADMIN_ONLY_ACTIONS or _is_admin(user)
        ]}

    @router.get("/meta/events")
    async def list_events(request: Request):
        """List available event triggers."""
        _owner(request)
        return {"events": [
            {"name": "session_created", "description": "Fires when a new chat session is created"},
            {"name": "message_sent", "description": "Fires when a user sends a message"},
            {"name": "document_created", "description": "Fires when a document is created"},
            {"name": "memory_added", "description": "Fires when a memory is added"},
            {"name": "research_completed", "description": "Fires when a research report completes"},
            {"name": "email_received", "description": "Fires when new inbox mail is observed"},
            {"name": "skill_added", "description": "Fires when a new skill is created"},
        ]}

    @router.post("/{task_id}/webhook/{token}")
    async def webhook_trigger(task_id: str, token: str):
        """Unauthenticated endpoint — the token IS the auth."""
        db = SessionLocal()
        try:
            task = db.query(ScheduledTask).filter(
                ScheduledTask.id == task_id,
                ScheduledTask.webhook_token == token,
                ScheduledTask.status == "active",
            ).first()
            if not task:
                raise HTTPException(404, "Not found")
        finally:
            db.close()
        started = await task_scheduler.run_task_now(task_id)
        if not started:
            raise HTTPException(409, "Task is already running")
        return {"ok": True, "message": "Task triggered via webhook"}

    @router.post("/{task_id}/webhook-regenerate")
    async def regenerate_webhook(request: Request, task_id: str):
        user = _owner(request)
        db = SessionLocal()
        try:
            task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
            if not task:
                raise HTTPException(404, "Task not found")
            if user and task.owner != user:
                raise HTTPException(403, "Access denied")
            task.webhook_token = secrets.token_urlsafe(32)
            db.commit()
            return {"ok": True, "webhook_token": task.webhook_token}
        finally:
            db.close()

    # --- PARSE NATURAL LANGUAGE → TASK DRAFT (AI) ---
    @router.post("/parse")
    async def parse_task(request: Request) -> Dict[str, Any]:
        """Turn a free-form description ("every weekday at 7am research the top
        AI news and summarize it") into a structured task draft the frontend
        can pre-fill the form with. Returns a draft only — the user reviews and
        saves it, so a misread schedule never goes live unreviewed."""
        from src.endpoint_resolver import resolve_endpoint
        from src.llm_core import llm_call_async
        from src.text_helpers import strip_think as _strip_think
        import json as _json, re as _re
        from datetime import datetime as _dt

        body = await request.json()
        desc = (body.get("description") or "").strip()
        if not desc:
            return {"success": False, "message": "Nothing to parse"}
        user = _owner(request)

        now = _dt.now()
        # Give the model the current date/time + weekday so relative phrasing
        # ("tomorrow", "every Monday", "in an hour") resolves correctly.
        ctx = now.strftime("%Y-%m-%d %H:%M (%A)")
        sys = (
            "You convert a user's description of a recurring or one-off task into "
            "STRICT JSON for a task scheduler. The current local date/time is "
            f"{ctx}. Output ONLY a JSON object, no prose, no markdown fences.\n\n"
            "Schema (omit fields you can't infer):\n"
            "{\n"
            '  "task_type": "llm" | "research",  // "research" if it asks to research/investigate/find out; else "llm"\n'
            '  "name": "short 3-6 word title",\n'
            '  "prompt": "the instruction the AI should run on schedule (or the research question)",\n'
            '  "schedule": "daily" | "weekly" | "monthly" | "once" | "cron",\n'
            '  "scheduled_time": "HH:MM",        // 24h LOCAL time\n'
            '  "scheduled_day": 0,               // weekly: 0=Mon..6=Sun; monthly: 1..31\n'
            '  "scheduled_date": "YYYY-MM-DDTHH:MM",  // only for "once"\n'
            '  "cron_expression": "m h dom mon dow",  // only if schedule is "cron"\n'
            '  "output_target": "session" | "email" | "notification"  // use email when the user asks to email the result\n'
            "}\n\n"
            "Rules: default schedule to 'daily' if a time is given without a frequency. "
            "Default scheduled_time to '09:00' if none is stated. For 'every weekday' "
            "use cron '0 H * * 1-5'. Keep the prompt actionable and self-contained."
        )
        try:
            url, model, headers = resolve_endpoint("utility", owner=user or None)
            if not url:
                url, model, headers = resolve_endpoint("default", owner=user or None)
            if not (url and model):
                return {"success": False, "message": "No model endpoint configured"}
            raw = await llm_call_async(
                url=url, model=model,
                messages=[{"role": "system", "content": sys},
                          {"role": "user", "content": desc[:1000]}],
                temperature=0.2, max_tokens=400, headers=headers, timeout=45,
            )
            text = _strip_think(raw or "", prose=False, prompt_echo=False).strip()
            if text.startswith("```"):
                text = text.strip("`")
                if text.lower().startswith("json"):
                    text = text[4:].lstrip()
            # Pull the first {...} block in case the model added stray text.
            m = _re.search(r"\{.*\}", text, _re.S)
            draft = _json.loads(m.group(0) if m else text)
            if not isinstance(draft, dict):
                raise ValueError("not an object")
            # Whitelist + light validation so the frontend gets clean fields.
            out: Dict[str, Any] = {}
            if draft.get("task_type") in ("llm", "research"):
                out["task_type"] = draft["task_type"]
            else:
                out["task_type"] = "llm"
            for k in ("name", "prompt", "cron_expression", "scheduled_date"):
                if isinstance(draft.get(k), str) and draft[k].strip():
                    out[k] = draft[k].strip()
            if draft.get("schedule") in ("daily", "weekly", "monthly", "once", "cron"):
                out["schedule"] = draft["schedule"]
            else:
                out["schedule"] = "daily"
            st = draft.get("scheduled_time")
            if isinstance(st, str) and _re.match(r"^\d{1,2}:\d{2}$", st.strip()):
                out["scheduled_time"] = st.strip()
            if isinstance(draft.get("scheduled_day"), int):
                out["scheduled_day"] = draft["scheduled_day"]
            if draft.get("output_target") in ("session", "email", "notification"):
                out["output_target"] = draft["output_target"]
            out["trigger_type"] = "schedule"
            if not out.get("prompt"):
                return {"success": False, "message": "Could not extract a task instruction"}
            return {"success": True, "draft": out}
        except Exception as e:
            logger.error(f"parse_task failed: {e}")
            return {"success": False, "message": str(e)}

    return router
