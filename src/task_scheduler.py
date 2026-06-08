"""Background scheduler for ScheduledTask execution."""

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Tuple

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Return naive UTC for task DB fields without using deprecated APIs."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Shared TTL cache (singleflight) ────────────────────────────────────────
# Multiple scheduled tasks firing in the same minute often need the same
# external data (Miniflux unreads, MCP tool snapshots, etc.). This cache
# deduplicates those fetches — in-flight requests for the same key await the
# same underlying coroutine, and completed results are reused until TTL expiry.
_shared_cache: Dict[Tuple, Tuple[float, Any]] = {}
_shared_cache_pending: Dict[Tuple, asyncio.Future] = {}
_shared_cache_lock = asyncio.Lock()


async def _cached(key: Tuple, ttl: float, fetch: Callable[[], Awaitable[Any]]) -> Any:
    """Return a cached result for `key` if fresh, else call `fetch()` and store.

    Concurrent callers for the same missing key share one `fetch()` call.
    Exceptions propagate to every waiter and do not poison the cache.
    """
    now = time.monotonic()
    async with _shared_cache_lock:
        entry = _shared_cache.get(key)
        if entry and entry[0] > now:
            return entry[1]
        fut = _shared_cache_pending.get(key)
        if fut is not None:
            pending = fut
            owner = False
        else:
            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            _shared_cache_pending[key] = fut
            pending = fut
            owner = True
    if not owner:
        return await pending
    try:
        val = await fetch()
        async with _shared_cache_lock:
            _shared_cache[key] = (time.monotonic() + ttl, val)
            _shared_cache_pending.pop(key, None)
        pending.set_result(val)
        return val
    except Exception as e:
        async with _shared_cache_lock:
            _shared_cache_pending.pop(key, None)
        pending.set_exception(e)
        raise


def compute_next_run(schedule: str, scheduled_time: str,
                     scheduled_day: int = None,
                     scheduled_date: datetime = None,
                     after: datetime = None,
                     cron_expression: str = None,
                     tz_name: str = None) -> datetime | None:
    """Compute the next run datetime (stored as naive UTC) based on schedule type.

    If `tz_name` is provided (IANA zone, e.g. "America/New_York"), `scheduled_time` /
    `scheduled_day` are interpreted as local wall-clock time in that zone and
    the result is converted to naive UTC for DB storage. If `tz_name` is None,
    the legacy behavior (`scheduled_time` interpreted as naive-UTC wall clock)
    is preserved so existing tasks don't shift.
    """
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        ZoneInfo = None

    tz = None
    if tz_name and ZoneInfo is not None:
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = None

    # "now" used for comparisons. When tz is set we work entirely in local tz
    # and convert to UTC at the end. Otherwise we use naive UTC (legacy).
    if tz is not None:
        now_utc = after or _utcnow()
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)
        now = now_utc.astimezone(tz)
    else:
        now = after or _utcnow()

    def _to_utc_naive(dt: datetime) -> datetime:
        """Convert a tz-aware datetime to naive UTC for DB storage."""
        if dt.tzinfo is None:
            return dt
        return dt.astimezone(timezone.utc).replace(tzinfo=None)

    if schedule == "cron" and cron_expression:
        try:
            from croniter import croniter
            cron = croniter(cron_expression, now)
            nxt = cron.get_next(datetime)
            if tz is not None and nxt.tzinfo is None:
                nxt = nxt.replace(tzinfo=tz)
            return _to_utc_naive(nxt) if tz is not None else nxt
        except Exception as e:
            logger.warning(f"Invalid cron expression '{cron_expression}': {e}")
            return None

    if schedule == "once":
        if scheduled_date and scheduled_date > (_to_utc_naive(now) if tz is not None else now):
            return scheduled_date
        return None

    if not scheduled_time:
        return None

    # Parse HH:MM — fail closed on malformed input (no colon, non-numeric,
    # out-of-range) the same way an invalid cron expression does above, so a
    # bad value like "9" or "9am" returns None instead of raising IndexError/
    # ValueError out of the create route (a 500) or the scheduler loop.
    parts = scheduled_time.split(":")
    try:
        hour, minute = int(parts[0]), int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("hour/minute out of range")
    except (ValueError, IndexError):
        logger.warning(f"Invalid scheduled_time '{scheduled_time}'")
        return None

    if schedule == "daily":
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return _to_utc_naive(candidate) if tz is not None else candidate

    if schedule == "weekly":
        day = scheduled_day if scheduled_day is not None else 0  # 0=Monday
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        days_ahead = day - candidate.weekday()
        if days_ahead < 0 or (days_ahead == 0 and candidate <= now):
            days_ahead += 7
        candidate += timedelta(days=days_ahead)
        return _to_utc_naive(candidate) if tz is not None else candidate

    if schedule == "monthly":
        day = scheduled_day if scheduled_day is not None else 1
        try:
            candidate = now.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
        except ValueError:
            # Short month: clamp to its last day (mirrors the next-month
            # clamp below) instead of silently skipping the whole month.
            if now.month == 12:
                last = now.replace(year=now.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                last = now.replace(month=now.month + 1, day=1) - timedelta(days=1)
            candidate = last.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            if now.month == 12:
                next_month = now.replace(year=now.year + 1, month=1, day=1)
            else:
                next_month = now.replace(month=now.month + 1, day=1)
            try:
                candidate = next_month.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
            except ValueError:
                if next_month.month == 12:
                    last = next_month.replace(year=next_month.year + 1, month=1, day=1) - timedelta(days=1)
                else:
                    last = next_month.replace(month=next_month.month + 1, day=1) - timedelta(days=1)
                candidate = last.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return _to_utc_naive(candidate) if tz is not None else candidate

    return None


def _resolve_task_timezone(db, task) -> str | None:
    """Look up the IANA timezone name for a task via its linked CrewMember, if any."""
    if not getattr(task, "crew_member_id", None):
        return None
    try:
        from core.database import CrewMember
        cm = db.query(CrewMember).filter(CrewMember.id == task.crew_member_id).first()
        if cm and cm.timezone:
            return cm.timezone
    except Exception:
        pass
    return None


# Built-in "housekeeping" tasks seeded for every owner, keyed by action.
# These are the canonical defaults — used both to seed and to revert a
# built-in task the user has altered. schedule "daily" uses scheduled_time;
# "cron" uses cron_expression.
HOUSEKEEPING_DEFAULTS = {
    "tidy_sessions":        {"name": "Chat Sessions Tidy",       "trigger_type": "event", "trigger_event": "session_created", "trigger_count": 5, "schedule": None, "scheduled_time": None, "cron_expression": None, "legacy_names": ["Tidy Chat Sessions"]},
    "tidy_documents":       {"name": "Documents Tidy",           "trigger_type": "event", "trigger_event": "document_created", "trigger_count": 5, "schedule": None, "scheduled_time": None, "cron_expression": None, "legacy_names": ["Tidy Documents"]},
    "consolidate_memory":   {"name": "Memory Tidy",              "trigger_type": "event", "trigger_event": "memory_added", "trigger_count": 5, "schedule": None, "scheduled_time": None, "cron_expression": None, "legacy_names": ["Tidy Memory"]},
    "tidy_research":        {"name": "Research Tidy",            "trigger_type": "event", "trigger_event": "research_completed", "trigger_count": 5, "schedule": None, "scheduled_time": None, "cron_expression": None, "legacy_names": ["Tidy Research"]},
    "summarize_emails":     {"name": "Email (Summary)",          "schedule": "cron",  "scheduled_time": None,    "cron_expression": "0 */2 * * *", "ship_paused": True, "legacy_names": ["Tidy Email (Summary)"]},
    "draft_email_replies":  {"name": "Email AI Auto Reply",      "schedule": "cron",  "scheduled_time": None,    "cron_expression": "0 */2 * * *", "ship_paused": True, "legacy_names": ["Tidy Email (Replies)", "AI Auto Reply"]},
    "extract_email_events": {"name": "Email Calendar Events",    "schedule": "cron",  "scheduled_time": None,    "cron_expression": "0 */1 * * *", "ship_paused": True, "legacy_names": ["Email → Calendar Events"]},
    "classify_events":      {"name": "Calendar Classify Events", "schedule": "cron",  "scheduled_time": None,    "cron_expression": "0 6,18 * * *", "ship_paused": True, "legacy_names": ["Classify Calendar Events"]},
    "check_email_urgency":   {"name": "Email Tags",               "schedule": "cron",  "scheduled_time": None,    "cron_expression": "0 * * * *", "ship_paused": True, "old_cron_expressions": ["*/15 * * * *"], "legacy_names": ["Email Triage", "Urgent Email"]},
    "audit_skills":          {"name": "Skills Audit",             "trigger_type": "event", "trigger_event": "skill_added", "trigger_count": 5, "schedule": None, "scheduled_time": None, "cron_expression": None, "legacy_names": ["Audit Skills"]},
}

RETIRED_HOUSEKEEPING_ACTIONS = frozenset({
    "tidy_calendar",
    "tidy_email_inbox",
    "mark_email_boundaries",
})


def _digest_windows(now):
    """(label, start, end) buckets for the calendar check-in digest.

    The windows are contiguous so no event is dropped between buckets — an
    earlier version started the 30-day window at now+8d while the week window
    ended at now+7d, so events ~7-8 days out fell into no bucket.
    """
    return [
        ("today_tomorrow", now, now + timedelta(days=2)),
        ("this_week", now + timedelta(days=2), now + timedelta(days=7)),
        ("next_30_days", now + timedelta(days=7), now + timedelta(days=30)),
    ]


class TaskScheduler:
    def __init__(self, session_manager):
        self._session_manager = session_manager
        self._running = False
        self._task = None
        self._executing = set()  # task IDs currently running OR queued behind the semaphore
        # Guards mutations of _executing. _check_due_tasks runs in the loop
        # coroutine; trigger_task() can be called from request handlers; the
        # event bus fires from background tasks. Without this lock long-running
        # tasks could be double-dispatched.
        self._executing_lock = asyncio.Lock()
        self._pending_notifications = []  # completed task notifications
        self._task_defer_counts = {}
        # Strict serial execution — exactly one task runs at a time. Anything
        # else (manual trigger, scheduled dispatch, task chain) waits behind
        # the semaphore as "queued" and starts when the current run finishes.
        # This is a hard guarantee, not configurable.
        self._run_semaphore = asyncio.Semaphore(1)
        self._concurrency_cap = 1
        self._task_handles = {}

    def _set_run_progress(self, run_id: str, message: str):
        """Persist short live progress text for Activity while a run is active."""
        if not run_id:
            return
        try:
            from core.database import SessionLocal, TaskRun
            db = SessionLocal()
            try:
                run = db.query(TaskRun).filter(TaskRun.id == run_id).first()
                if run and run.status in ("queued", "running"):
                    run.result = (message or "")[:4000]
                    db.commit()
            finally:
                db.close()
        except Exception:
            logger.debug("Task progress update failed", exc_info=True)

    def _mark_run_aborted(self, task_id: str, run_id: str | None = None, message: str = "Stopped by user") -> bool:
        """Mark an active run as aborted. Used by stop/cancel paths."""
        try:
            from core.database import SessionLocal, TaskRun
            db = SessionLocal()
            try:
                q = db.query(TaskRun)
                if run_id:
                    q = q.filter(TaskRun.id == run_id)
                else:
                    q = q.filter(
                        TaskRun.task_id == task_id,
                        TaskRun.status.in_(("queued", "running")),
                    ).order_by(TaskRun.started_at.desc())
                run = q.first()
                if not run or run.status not in ("queued", "running"):
                    return False
                run.status = "aborted"
                run.error = message
                run.result = run.result or message
                run.finished_at = _utcnow()
                db.commit()
                return True
            finally:
                db.close()
        except Exception:
            logger.debug("Task abort marker failed for %s", task_id, exc_info=True)
            return False

    def add_notification(self, task_name: str, status: str, task_id: str = None, owner: str = None, body: str = None):
        """Store a notification about a completed task run. Tagged with the
        task's owner so `pop_notifications` can return only that user's
        notifications and prevent cross-tenant drain. `body` is the result
        text — populated when output_target='notification' so the client can
        show a rich browser Notification, not just a toast."""
        self._pending_notifications.append({
            "task_name": task_name,
            "status": status,
            "task_id": task_id,
            "owner": owner,
            "body": (body[:500] + "…") if body and len(body) > 500 else body,
            "timestamp": _utcnow().isoformat() + "Z",
        })
        # Cap at 50 to avoid unbounded growth
        if len(self._pending_notifications) > 50:
            self._pending_notifications = self._pending_notifications[-50:]

    def pop_notifications(self, owner: str = None) -> list:
        """Return and clear pending notifications.

        When `owner` is set, only matching notifications are returned (and
        cleared). Notifications stored before owner-tagging existed (or
        from owner-less tasks) are included when the caller is anonymous
        or when no owner filter is given — preserves backward behaviour
        for the legacy single-user deploy.
        """
        if owner is None:
            notes = self._pending_notifications[:]
            self._pending_notifications.clear()
            return notes
        # Strict owner scope — used to OR-in null-owner notifications for
        # "legacy single-user" compat but that leaked notification bodies to
        # any authenticated user once a second account existed.
        keep, take = [], []
        for n in self._pending_notifications:
            if n.get("owner") == owner:
                take.append(n)
            else:
                keep.append(n)
        self._pending_notifications = keep
        return take

    async def start(self):
        # On startup, mark any leftover "running" task_runs as errored. Without
        # this, a server crash leaves rows stuck running indefinitely and the
        # _executing in-memory set forgets them, so the UI shows phantoms.
        try:
            from core.database import SessionLocal, TaskRun
            db = SessionLocal()
            try:
                # Zombies from a prior server crash. Tagged "aborted" (not
                # "error") so the Activity view + error-rate stats don't
                # falsely blame the task for what was an infrastructure event.
                stale = db.query(TaskRun).filter(
                    TaskRun.status.in_(("running", "queued"))
                ).all()
                if stale:
                    now = _utcnow()
                    for r in stale:
                        old_status = r.status or "running"
                        r.status = "aborted"
                        r.error = "Server restarted while task was " + old_status
                        r.finished_at = now
                    db.commit()
                    logger.info(f"Cleared {len(stale)} stale task_runs from previous run")
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"Could not clear stale task_runs on startup: {e}")

        # Advance next_run for active tasks whose next_run is already in the
        # past. Without this, a restart hits _check_due_tasks() with an empty
        # in-process _executing set, and the same overdue task fires once per
        # poll until it completes.
        try:
            from core.database import SessionLocal as _SL, ScheduledTask as _ST
            db = _SL()
            try:
                now = _utcnow()
                overdue = db.query(_ST).filter(
                    _ST.status == "active",
                    _ST.next_run.isnot(None),
                    _ST.next_run < now,
                ).all()
                if overdue:
                    for t in overdue:
                        t.next_run = now + timedelta(seconds=60)
                    db.commit()
                    logger.info(
                        "Pushed next_run forward by 60s for %d overdue active tasks on startup",
                        len(overdue),
                    )
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"Could not advance overdue next_run on startup: {e}")

        # Defense-in-depth dedupe sweep: for any owner with >1 rows where
        # is_default_assistant=True, keep the oldest and demote the rest +
        # delete their orphaned check-in tasks. This is the safety net for
        # the synthetic-owner seeding bug (we cleaned a manual instance of
        # it, but a stale code path or DB import could recreate it).
        try:
            from core.database import SessionLocal, CrewMember, ScheduledTask
            db = SessionLocal()
            try:
                from sqlalchemy import func
                groups = db.query(CrewMember.owner, func.count(CrewMember.id).label("n")).filter(
                    CrewMember.is_default_assistant == True,  # noqa: E712
                ).group_by(CrewMember.owner).having(func.count(CrewMember.id) > 1).all()
                for owner, n in groups:
                    rows = db.query(CrewMember).filter(
                        CrewMember.owner == owner,
                        CrewMember.is_default_assistant == True,  # noqa: E712
                    ).order_by(CrewMember.created_at.asc()).all()
                    keep = rows[0]
                    losers = rows[1:]
                    loser_ids = [r.id for r in losers]
                    # Delete the orphaned tasks tied to the loser crews — they
                    # are duplicates of the keeper's check-ins.
                    n_tasks = db.query(ScheduledTask).filter(
                        ScheduledTask.crew_member_id.in_(loser_ids)
                    ).delete(synchronize_session=False)
                    for r in losers:
                        db.delete(r)
                    db.commit()
                    logger.warning(
                        "Default-assistant dedupe: owner=%r had %d rows, kept %s, "
                        "dropped %d crew + %d orphan tasks",
                        owner, n, keep.id, len(losers), n_tasks,
                    )
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"Could not dedupe default-assistant rows on startup: {e}")

        self._running = True
        self._task = asyncio.create_task(self._loop())
        # Internal background scanner that isn't a user-facing "task" — pure
        # infra (no LLM), shouldn't clutter the Tasks UI, fires on its own
        # cadence inside the scheduler process.
        #
        # Calendar event reminders are represented as Notes by the calendar UI,
        # so the Notes scanner is the single reminder dispatch path. Running the
        # old event scanner too caused duplicate emails/notifications for the
        # same calendar event.
        self._note_pings_task = asyncio.create_task(self._note_pings_loop())
        logger.info(f"Task scheduler started (concurrency cap: {self._concurrency_cap})")
        # Audit clusters: show any minute-of-day where >1 active scheduled
        # tasks land. Helps spot "all my tasks fire at 9am" patterns the user
        # may want to spread out.
        try:
            from core.database import SessionLocal, ScheduledTask
            db = SessionLocal()
            try:
                rows = db.query(ScheduledTask).filter(
                    ScheduledTask.status == "active",
                    ScheduledTask.trigger_type == "schedule",
                    ScheduledTask.next_run.isnot(None),
                ).all()
                buckets: Dict[str, list] = {}
                for r in rows:
                    if not r.next_run:
                        continue
                    key = r.next_run.strftime("%H:%M")
                    buckets.setdefault(key, []).append(r.name or r.id)
                clusters = {k: v for k, v in buckets.items() if len(v) > 1}
                if clusters:
                    summary = ", ".join(f"{k} ({len(v)})" for k, v in sorted(clusters.items()))
                    logger.info(f"Task scheduling clusters (>1 task/minute): {summary}")
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"Cluster audit skipped: {e}")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        for attr in ("_note_pings_task", "_event_pings_task"):
            t = getattr(self, attr, None)
            if t:
                t.cancel()
                try: await t
                except asyncio.CancelledError: pass
        logger.info("Task scheduler stopped")

    async def _note_pings_loop(self):
        """Built-in note-due scanner — ticks every 60s inside the scheduler.
        Pure infra (no LLM), doesn't surface in the Tasks UI. Iterates
        per-owner so cache pruning in `action_ping_notes` (which removes
        cache entries for notes not in the current scan's seen_ids) doesn't
        cross-delete other users' entries (review C4).
        """
        await asyncio.sleep(30)
        from src.builtin_actions import action_ping_notes, TaskNoop
        while self._running:
            owners = self._known_task_owners()
            for ow in (owners or [""]):
                try:
                    await action_ping_notes(owner=ow)
                except TaskNoop:
                    pass
                except Exception as e:
                    logger.warning(f"ping_notes background scanner errored for owner={ow!r}: {e}")
            await asyncio.sleep(60)  # 1 min

    async def _event_pings_loop(self):
        """Built-in calendar-event scanner — same recipe as note pings. Runs
        every 10 min, fires reminders via dispatch_reminder. Not a user task.
        Iterates per-owner so each user only gets their own calendar pings
        (passing owner="" globally would email User B's events to User A's
        configured SMTP "from" address — see review C3).
        """
        await asyncio.sleep(90)
        from src.builtin_actions import action_ping_events, TaskNoop
        while self._running:
            owners = self._known_task_owners()
            for ow in (owners or [""]):
                try:
                    await action_ping_events(owner=ow)
                except TaskNoop:
                    pass
                except Exception as e:
                    logger.warning(f"ping_events background scanner errored for owner={ow!r}: {e}")
            await asyncio.sleep(600)  # 10 min

    def _known_task_owners(self) -> list:
        """Distinct non-empty owners that background scanners should visit.

        Scheduled tasks used to be the only owner source. Calendar reminders
        are stored as Notes, though, so an account with due notes but no task
        rows could get the browser reminder while the backend email/ntfy
        scanner never ran for that owner.
        """
        from core.database import SessionLocal, ScheduledTask, Note
        db = SessionLocal()
        try:
            owners = set()
            for r in db.query(ScheduledTask.owner).distinct().all():
                if r[0]:
                    owners.add(r[0])
            note_q = db.query(Note.owner).filter(
                Note.due_date.isnot(None),
                Note.due_date != "",
                Note.archived == False,  # noqa: E712
            ).distinct()
            for r in note_q.all():
                if r[0]:
                    owners.add(r[0])
            return sorted(owners)
        except Exception:
            return []
        finally:
            db.close()

    async def _loop(self):
        await asyncio.sleep(10)
        while self._running:
            try:
                await self._check_due_tasks()
            except Exception:
                logger.exception("Error in task scheduler loop")
            # Sleep until the next scheduled run, capped at 60s. A `* * * * *`
            # cron task previously fired up to ~60s late because we always
            # slept the full minute; now the loop wakes near the boundary.
            sleep_for = 60.0
            try:
                from core.database import SessionLocal as _SL, ScheduledTask as _ST
                _db = _SL()
                try:
                    next_run = _db.query(_ST.next_run).filter(
                        _ST.status == "active",
                        _ST.next_run.isnot(None),
                    ).order_by(_ST.next_run.asc()).first()
                    if next_run and next_run[0]:
                        delta = (next_run[0] - _utcnow()).total_seconds()
                        sleep_for = max(1.0, min(60.0, delta))
                finally:
                    _db.close()
            except Exception:
                pass
            await asyncio.sleep(sleep_for)

    async def _check_due_tasks(self):
        from core.database import SessionLocal, ScheduledTask
        db = SessionLocal()
        try:
            now = _utcnow()
            async with self._executing_lock:
                # Snapshot under the lock so we don't race with mid-iteration adds.
                executing_snapshot = set(self._executing)
                # Scheduled tasks and deferred event tasks both use next_run.
                due = db.query(ScheduledTask).filter(
                    ScheduledTask.status == "active",
                    ScheduledTask.next_run <= now,
                    ScheduledTask.id.notin_(executing_snapshot) if executing_snapshot else True,
                ).all()
                to_dispatch = []
                for task in due:
                    if task.id in self._executing:
                        continue
                    self._executing.add(task.id)
                    to_dispatch.append(task.id)
            for task_id in to_dispatch:
                asyncio.create_task(self._execute_task(task_id))
        finally:
            db.close()

    async def _execute_task(self, task_id: str, *, bypass_model_slot: bool = False, release_executing: bool = True):
        # Create the run record with status="queued" BEFORE waiting on the
        # semaphore so the UI can show that a manually-triggered task is in
        # line behind another. Once we acquire the slot, flip to "running"
        # and hand off to _execute_task_locked.
        from core.database import SessionLocal, TaskRun
        current = asyncio.current_task()
        if current:
            self._task_handles[task_id] = current
        run_id = str(uuid.uuid4())
        _q_db = SessionLocal()
        try:
            run = TaskRun(
                id=run_id,
                task_id=task_id,
                started_at=_utcnow(),
                status="queued",
                result="Queued — waiting for a free slot…",
            )
            _q_db.add(run)
            _q_db.commit()
        except Exception:
            logger.exception(f"Failed to create queued run row for task {task_id}")
        finally:
            _q_db.close()

        try:
            if bypass_model_slot or not self._task_needs_model_slot(task_id):
                await self._execute_task_locked(task_id, run_id, release_executing=release_executing)
                return

            async with self._run_semaphore:
                await self._execute_task_locked(task_id, run_id, release_executing=release_executing)
        except asyncio.CancelledError:
            # If cancellation happens while queued behind the semaphore,
            # _execute_task_locked never runs and cannot update the Activity row.
            self._mark_run_aborted(task_id, run_id)
            raise
        finally:
            handle = self._task_handles.get(task_id)
            if handle is current:
                self._task_handles.pop(task_id, None)
            if release_executing:
                async with self._executing_lock:
                    self._executing.discard(task_id)

    async def _execute_task_locked(self, task_id: str, run_id: str, *, release_executing: bool = True):
        from core.database import SessionLocal, ScheduledTask, TaskRun

        db = SessionLocal()
        try:
            task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
            if not task or task.status != "active":
                # Task was paused/deleted while queued — record that outcome
                # so the run row doesn't sit as "queued" forever.
                stale = db.query(TaskRun).filter(TaskRun.id == run_id).first()
                if stale and stale.status == "queued":
                    stale.status = "skipped"
                    stale.finished_at = _utcnow()
                    stale.error = f"Task no longer active (status={task.status if task else 'deleted'})"
                    db.commit()
                return

            # Flip the run from queued → running. Reset started_at to the
            # actual execution start so queue wait time is visible from
            # created_at vs started_at if we ever surface that.
            run = db.query(TaskRun).filter(TaskRun.id == run_id).first()
            if run:
                run.status = "running"
                run.started_at = _utcnow()
                run.result = "Starting…"
                db.commit()
            else:
                # Defensive: row may have been wiped; recreate so the rest of
                # the code can look it up by run_id without crashing.
                run = TaskRun(
                    id=run_id,
                    task_id=task.id,
                    started_at=_utcnow(),
                    status="running",
                    result="Starting…",
                )
                db.add(run)
                db.commit()

            task_type = task.task_type or "llm"

            from src.builtin_actions import TaskDeferred, TaskNoop

            # Cleared each run so an action task (no model) doesn't inherit a
            # previous llm/research run's model. The executors set it once the
            # model is resolved.
            self._last_run_model = None
            try:
                if task_type == "action":
                    result, success = await self._execute_action(task, run_id=run_id)
                    run.status = "success" if success else "error"
                    run.result = result
                    if not success:
                        run.error = result
                elif task_type == "research":
                    result = await self._execute_research_task(task, db)
                    run.status = "success"
                    run.result = result
                else:
                    # LLM task — use agent loop for tool access
                    result = await self._execute_llm_task(task, db)
                    run.status = "success"
                    run.result = result
                # Record which model actually ran (resolved inside the executor).
                if getattr(self, "_last_run_model", None):
                    run.model = self._last_run_model
                if run.status == "success":
                    await self._deliver_task_result(task, result, db, model=getattr(self, "_last_run_model", None))
            except TaskDeferred as defer:
                count = self._task_defer_counts.get(task_id, 0) + 1
                self._task_defer_counts[task_id] = count
                delay_seconds = int(getattr(defer, "delay_seconds", 20 * 60) or (20 * 60))
                if count > 2:
                    delay_seconds = max(delay_seconds, 40 * 60)
                when = _utcnow() + timedelta(seconds=delay_seconds)
                logger.info(
                    "Task '%s' deferred for %ss after %s quiet-window hit(s): %s",
                    task.name, delay_seconds, count, defer,
                )
                run_obj = db.query(TaskRun).filter(TaskRun.id == run_id).first()
                if run_obj:
                    db.delete(run_obj)
                task.next_run = when
                db.commit()
                return
            except asyncio.CancelledError:
                logger.info("Task '%s' stopped by user", task.name)
                run_obj = db.query(TaskRun).filter(TaskRun.id == run_id).first()
                if run_obj:
                    run_obj.status = "aborted"
                    run_obj.error = "Stopped by user"
                    run_obj.result = run_obj.result or "Stopped by user"
                    run_obj.finished_at = _utcnow()
                task.last_run = _utcnow()
                if (task.trigger_type or "schedule") == "schedule":
                    task.next_run = compute_next_run(
                        task.schedule, task.scheduled_time,
                        task.scheduled_day, task.scheduled_date,
                        after=_utcnow(),
                        cron_expression=task.cron_expression,
                        tz_name=_resolve_task_timezone(db, task),
                    )
                else:
                    task.next_run = None
                db.commit()
                return
            except TaskNoop as noop:
                # Action reported "nothing to do". Mark the run as `skipped`
                # with the reason in `result` so it surfaces in Activity as a
                # slim "skipped — <reason>" row instead of vanishing silently.
                # (Previous behavior was `db.delete(run)`, which made the user
                # think queued tasks had been dropped on the floor.)
                logger.info(f"Task '{task.name}' no-op: {noop}")
                run.status = "skipped"
                run.result = str(noop)
                run.finished_at = _utcnow()
                task.last_run = _utcnow()
                if (task.trigger_type or "schedule") == "schedule":
                    task.next_run = compute_next_run(
                        task.schedule, task.scheduled_time,
                        task.scheduled_day, task.scheduled_date,
                        after=_utcnow(),
                        cron_expression=task.cron_expression,
                        tz_name=_resolve_task_timezone(db, task),
                    )
                else:
                    task.next_run = None
                db.commit()
                return

            run.finished_at = _utcnow()

            # Update task
            task.last_run = _utcnow()
            task.run_count = (task.run_count or 0) + 1
            self._task_defer_counts.pop(task_id, None)

            # Compute next run only for schedule-triggered tasks
            if (task.trigger_type or "schedule") == "schedule":
                task.next_run = compute_next_run(
                    task.schedule, task.scheduled_time,
                    task.scheduled_day, task.scheduled_date,
                    after=_utcnow(),
                    cron_expression=task.cron_expression,
                    tz_name=_resolve_task_timezone(db, task),
                )
                if task.next_run is None and task.schedule == "once":
                    task.status = "completed"
            else:
                task.next_run = None

            db.commit()
            logger.info(f"Task '{task.name}' completed (run {run_id})")
            output = task.output_target or "session"
            # Per-task notification gate. Default True (notifications_enabled
            # defaults to True at column level), but skip when the user has
            # explicitly turned them off for this task — quiets chatty
            # housekeeping cron tasks without disabling them entirely.
            should_notify = (
                (task.task_type or "llm") in {"llm", "research"}
                and getattr(task, "notifications_enabled", True)
            )
            if should_notify:
                self.add_notification(
                    task.name,
                    run.status,
                    task_id,
                    owner=task.owner,
                    body=run.result if output == "notification" else None,
                )

            # Log result to the assistant chat so all task activity is visible.
            # Skip skipped/error rows — user shouldn't see "skipped: …" noise
            # for cron tasks that no-op'd, or duplicate error spam for tasks
            # that already fired an error notification above.
            if run.status == "success":
                self._log_to_assistant(db, task, run.result or "[success]")

            # Task chaining — trigger the next task on success
            if run.status == "success" and task.then_task_id:
                chain_id = task.then_task_id
                chain_task = db.query(ScheduledTask).filter(ScheduledTask.id == chain_id).first()
                if not chain_task or chain_task.owner != task.owner:
                    logger.warning(
                        "Skipping chain from %r: target task %s is missing or not owned by %r",
                        task.name, chain_id, task.owner,
                    )
                elif not self._has_chain_cycle(db, chain_id, owner=task.owner):
                    logger.info(f"Chaining: '{task.name}' → task {chain_id}")
                    asyncio.create_task(self._run_chained(chain_id))
                else:
                    logger.warning(f"Skipping chain from '{task.name}': cycle detected")

        except Exception as exec_exc:
            logger.exception(f"Task {task_id} execution error")
            # Fetch the task's owner so the error notification reaches
            # the same user the success notification would have.
            _owner = None
            try:
                _t = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
                _owner = _t.owner if _t else None
            except Exception:
                pass
            _should_notify_error = False
            try:
                _t_for_notify = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
                _should_notify_error = (
                    bool(_t_for_notify)
                    and (_t_for_notify.task_type or "llm") in {"llm", "research"}
                    and getattr(_t_for_notify, "notifications_enabled", True)
                )
            except Exception:
                _should_notify_error = False
            if _should_notify_error:
                self.add_notification(f"Task {task_id}", "error", task_id, owner=_owner)
            try:
                # Persist the actual exception message so the UI can show it
                err_text = f"{type(exec_exc).__name__}: {exec_exc}"
                run_obj = db.query(TaskRun).filter(TaskRun.id == run_id).first()
                if run_obj and run_obj.status in ("running", "success"):
                    run_obj.status = "error"
                    run_obj.error = err_text[:2000]
                    run_obj.finished_at = _utcnow()
                # Advance next_run even on failure so a broken task doesn't
                # busy-loop the scheduler every tick with a stale past date.
                task_obj = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
                if task_obj and (task_obj.trigger_type or "schedule") == "schedule":
                    task_obj.last_run = _utcnow()
                    try:
                        task_obj.next_run = compute_next_run(
                            task_obj.schedule, task_obj.scheduled_time,
                            task_obj.scheduled_day, task_obj.scheduled_date,
                            after=_utcnow(),
                            cron_expression=task_obj.cron_expression,
                            tz_name=_resolve_task_timezone(db, task_obj),
                        )
                    except Exception:
                        pass
                try:
                    db.commit()
                except Exception as commit_err:
                    # Commit failed — without a fallback the run row stays
                    # "running" forever AND next_run stays in the past, so the
                    # scheduler busy-loops dispatching the same task every tick
                    # until restart. Force the recovery in a fresh session.
                    logger.warning("Task %s error-path commit failed: %s — falling back", task_id, commit_err)
                    try:
                        db.rollback()
                    except Exception:
                        pass
                    from datetime import timedelta as _td
                    _recover_db = SessionLocal()
                    try:
                        _r = _recover_db.query(TaskRun).filter(TaskRun.id == run_id).first()
                        if _r and _r.status in ("running", "queued"):
                            _r.status = "aborted"
                            _r.error = f"commit_failed: {type(commit_err).__name__}: {commit_err}"[:2000]
                            _r.finished_at = _utcnow()
                        _t = _recover_db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
                        if _t and (_t.trigger_type or "schedule") == "schedule":
                            # Push next_run forward 5min as a safe stall so the
                            # scheduler doesn't immediately re-dispatch.
                            _t.next_run = _utcnow() + _td(minutes=5)
                            _t.last_run = _utcnow()
                        _recover_db.commit()
                    except Exception as recover_err:
                        logger.error("Task %s recovery commit ALSO failed: %s", task_id, recover_err)
                    finally:
                        _recover_db.close()
            except Exception:
                logger.exception("Task %s error-path failed unexpectedly", task_id)
        finally:
            db.close()
            handle = self._task_handles.get(task_id)
            if handle is asyncio.current_task():
                self._task_handles.pop(task_id, None)
            if release_executing:
                async with self._executing_lock:
                    self._executing.discard(task_id)



    # Built-in housekeeping actions whose output is pure infra (no user-facing
    # content) — don't pollute the assistant chat session with their summaries.
    # Activity log + reminder email already carry everything the user needs.
    _SILENT_ACTIONS = frozenset({
        "check_email_urgency",
        "learn_sender_signatures",
        "summarize_emails",
        "draft_email_replies",
        "extract_email_events",
        "classify_events",
        "tidy_sessions",
        "tidy_documents",
        "consolidate_memory",
        "tidy_research",
        "test_skills",
        "audit_skills",
    })

    _MODEL_BACKED_ACTIONS = frozenset({
        "summarize_emails",
        "draft_email_replies",
        "extract_email_events",
        "classify_events",
        "learn_sender_signatures",
        "check_email_urgency",
        "test_skills",
        "audit_skills",
        "consolidate_memory",
    })

    def _task_needs_model_slot(self, task_id: str) -> bool:
        """Only LLM/research/model-backed actions should wait in the model
        queue. Pure housekeeping actions can run immediately."""
        from core.database import SessionLocal, ScheduledTask

        db = SessionLocal()
        try:
            task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
            if not task:
                return True
            task_type = getattr(task, "task_type", "") or "llm"
            if task_type != "action":
                return True
            return (getattr(task, "action", "") or "") in self._MODEL_BACKED_ACTIONS
        finally:
            db.close()

    def _log_to_assistant(self, db, task, result_text: str):
        """Log a task result to the assistant's chat session."""
        # Don't double-log check-ins (they already save directly)
        if "check-in" in (task.name or "").lower():
            return
        # Built-in housekeeping noise stays out of the chat.
        if (getattr(task, "action", "") or "") in self._SILENT_ACTIONS:
            return
        from src.assistant_log import log_to_assistant
        log_to_assistant(
            task.owner,
            result_text[:1000],
            category=(task.name or "Task"),
        )

    async def _execute_action(self, task, run_id: str | None = None) -> tuple:
        """Execute a built-in action (no LLM needed)."""
        from src.builtin_actions import BUILTIN_ACTIONS

        action_fn = BUILTIN_ACTIONS.get(task.action)
        if not action_fn:
            return f"Unknown action: {task.action}", False

        from src.builtin_actions import TaskNoop
        try:
            # Pass task prompt as script/command for ssh_command/run_script actions.
            def _progress(message: str):
                self._set_run_progress(run_id, message)

            kwargs = {"owner": task.owner, "task_name": task.name, "progress_cb": _progress}
            if task.action in ("run_script", "run_local", "ssh_command") and task.prompt:
                kwargs["script" if task.action in ("run_script", "run_local") else "command"] = task.prompt
            # cookbook_serve carries its JSON config in task.prompt — feed it
            # through as `command` so action_cookbook_serve can json.loads it.
            elif task.action == "cookbook_serve" and task.prompt:
                kwargs["command"] = task.prompt
            result, success = await action_fn(**kwargs)
            return result, success
        except TaskNoop:
            # Bubble up so _execute_task_locked can drop the run row silently.
            raise
        except Exception as e:
            logger.error(f"Action '{task.action}' failed: {e}")
            return str(e), False

    # ── Check-in source discovery ──
    # Pattern-based: if an MCP server has a tool matching a pattern, it becomes
    # a check-in source. Add new patterns here to support new integrations —
    # no code changes needed elsewhere.
    CHECKIN_MCP_PATTERNS = [
        {"detect": "list_emails",   "section": "Email",    "tool": "list_emails",
         "args": {"mailbox": "INBOX", "limit": 10, "unread_only": True},
         "label_from_identity": True,
         "formatter": "_format_email_output"},
        {"detect": "search_emails", "section": "Email",    "tool": "search_emails",
         "args": {"query": "is:unread", "limit": 10},
         "label_from_identity": True,
         "formatter": "_format_email_output"},
        {"detect": "get_feed",      "section": "RSS",      "tool": "get_feed",
         "args": {},
         "label_from_identity": False},
        {"detect": "list_feeds",    "section": "RSS",      "tool": "list_feeds",
         "args": {},
         "label_from_identity": False},
        {"detect": "list_messages", "section": "Messages", "tool": "list_messages",
         "args": {"limit": 10},
         "label_from_identity": True},
    ]

    @staticmethod
    def _format_email_output(raw: str) -> str:
        """Clean up raw MCP email list output into readable format."""
        import re as _re
        lines = []
        for line in raw.split("\n"):
            line = line.strip()
            if not line:
                continue
            # Skip header lines like "📬 [INBOX] 856 emails..."
            if line.startswith(("\U0001f4ec", "📬", "No emails", "---", "Page ")):
                continue
            # Skip "more pages available" etc
            if "page" in line.lower() and "/" in line:
                continue
            # Parse: [1778] Re: Subject From: Name | Date
            m = _re.match(r'\[?\d+\]?\s*(?:↩️\s*|📎\s*|🔵\s*|⭐\s*)?(.+?)(?:\s*From:\s*(.+?))?(?:\s*\|\s*(\S+))?$', line)
            if m:
                subject = m.group(1).strip().rstrip('|').strip()
                sender = (m.group(2) or "").strip().rstrip('|').strip()
                if sender:
                    lines.append(f"- {sender} — {subject}")
                else:
                    lines.append(f"- {subject}")
            elif line.startswith("[") or line.startswith("-"):
                # Generic cleanup
                cleaned = _re.sub(r'^\[?\d+\]?\s*(?:↩️\s*|📎\s*)?', '', line.lstrip('- '))
                if cleaned.strip():
                    lines.append(f"- {cleaned.strip()}")
        if not lines:
            return "No unread emails"
        return "\n".join(lines[:10])

    async def _execute_checkin(self, task, crew, db, session_id: str,
                               endpoint_url: str, model: str) -> str:
        """Gather raw data from all integrations, hand it to the LLM to write the check-in."""
        from src.tool_implementations import do_manage_notes
        from src.agent_tools import get_mcp_manager

        tz_name = _resolve_task_timezone(db, task)
        try:
            if tz_name:
                from zoneinfo import ZoneInfo
                from datetime import timezone, timedelta
                now = _utcnow().replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tz_name))
            else:
                from datetime import timedelta
                now = _utcnow()
            time_str = now.strftime("%A, %B %d %Y, %H:%M")
        except Exception:
            from datetime import timedelta
            now = _utcnow()
            time_str = now.strftime("%H:%M UTC")

        raw = {}

        # Calendar: today+tomorrow, this week, month ahead
        # Pull directly from DB so we can include event_type and importance.
        try:
            from core.database import SessionLocal as _SL, CalendarEvent as _CE
            _db = _SL()
            try:
                for label, start, end in _digest_windows(now):
                    # Strip timezone for naive DB comparison
                    _s = start.replace(tzinfo=None) if start.tzinfo else start
                    _e = end.replace(tzinfo=None) if end.tzinfo else end
                    evs = _db.query(_CE).filter(
                        _CE.dtstart >= _s,
                        _CE.dtstart <= _e,
                        _CE.status != "cancelled",
                    ).order_by(_CE.dtstart).all()
                    if not evs:
                        continue
                    # Group by importance for richer output
                    by_imp = {"critical": [], "high": [], "normal": [], "low": []}
                    for ev in evs:
                        imp = (ev.importance or "normal").lower()
                        by_imp.setdefault(imp, []).append(ev)
                    lines = []
                    for tier in ("critical", "high", "normal", "low"):
                        items = by_imp.get(tier, [])
                        if not items:
                            continue
                        marker = {"critical": "[!!]", "high": "[!]", "normal": "  ", "low": " ·"}[tier]
                        for ev in items:
                            t = ev.dtstart.strftime("%a %b %d %H:%M")
                            tag = f" ({ev.event_type})" if ev.event_type else ""
                            loc = f" @ {ev.location}" if ev.location else ""
                            lines.append(f"{marker} {t} — {ev.summary}{tag}{loc}")
                    if lines:
                        raw[f"calendar_{label}"] = "\n".join(lines)
            finally:
                _db.close()
        except Exception as e:
            raw["calendar"] = f"Error: {e}"

        # Notes/Tasks
        try:
            r = await do_manage_notes(json.dumps({"action": "list"}), owner=task.owner)
            raw["notes_tasks"] = r.get("results") or r.get("response") or "No notes"
        except Exception as e:
            raw["notes_tasks"] = f"Error: {e}"

        # Auto-discover API integrations (Miniflux RSS, etc.).
        try:
            import httpx
            from src.integrations import load_integrations
            for integ in load_integrations():
                if not integ.get("enabled"):
                    continue
                preset = integ.get("preset", "")
                base_url = integ.get("base_url", "").rstrip("/")
                api_key = integ.get("api_key", "")
                if not base_url:
                    continue

                # Build auth headers
                headers = {}
                if integ.get("auth_type") == "header" and api_key:
                    headers[integ.get("auth_header", "X-Auth-Token")] = api_key
                elif integ.get("auth_type") == "bearer" and api_key:
                    headers["Authorization"] = f"Bearer {api_key}"

                # Miniflux: fetch unread entries (cached 3 min across tasks)
                if preset == "miniflux":
                    async def _fetch_miniflux(_base=base_url, _headers=dict(headers)):
                        async with httpx.AsyncClient(timeout=10) as client:
                            resp = await client.get(
                                f"{_base}/v1/entries",
                                params={"status": "unread", "limit": 15, "order": "published_at", "direction": "desc"},
                                headers=_headers,
                            )
                            if resp.status_code != 200:
                                return None
                            entries = resp.json().get("entries", []) or []
                            if not entries:
                                return None
                            lines = []
                            for e in entries[:15]:
                                title = e.get("title", "?")
                                feed = (e.get("feed") or {}).get("title", "?")
                                url = e.get("url", "")
                                lines.append(f"- [{feed}] {title} — {url}")
                            return "\n".join(lines)
                    try:
                        val = await _cached(("miniflux_unread", base_url), 180, _fetch_miniflux)
                        if val:
                            raw["rss_miniflux_unread"] = val
                    except Exception as e:
                        logger.warning(f"Miniflux fetch failed: {e}")
        except Exception as e:
            logger.warning(f"Integrations discovery failed: {e}")

        # Auto-discover MCP sources
        mcp = get_mcp_manager()
        if mcp:
            discovered = set()
            for server_id, tools in mcp._tools.items():
                if mcp.is_builtin(server_id):
                    continue
                conn = mcp._connections.get(server_id, {})
                if conn.get("status") != "connected":
                    continue
                identity = conn.get("identity", "")
                tool_names = {t["name"] for t in tools}
                for pattern in self.CHECKIN_MCP_PATTERNS:
                    if pattern["detect"] not in tool_names:
                        continue
                    key = f"{pattern['section']}_{server_id}"
                    if key in discovered:
                        continue
                    discovered.add(key)
                    label = f"{pattern['section']} ({identity})" if identity else pattern["section"]
                    qualified = f"mcp__{server_id}__{pattern['tool']}"
                    args = dict(pattern.get("args", {}))
                    args["account"] = "default"
                    try:
                        # Cache 3 min: different scheduled tasks firing at the
                        # same minute share the same MCP snapshot.
                        async def _call_mcp(_q=qualified, _args=args):
                            return await mcp.call_tool(_q, _args)
                        cache_key = ("mcp_snapshot", qualified, json.dumps(args, sort_keys=True))
                        result = await _cached(cache_key, 180, _call_mcp)
                        if result.get("exit_code", 0) != 0:
                            continue
                        content = result.get("stdout") or result.get("output") or ""
                        if content.strip():
                            raw[label] = content[:3000]
                    except Exception:
                        pass

        # Build the data dump and hand it to the LLM
        data_dump = f"Current time: {time_str}\n\n"
        for key, val in raw.items():
            data_dump += f"--- {key} ---\n{val}\n\n"

        context = (
            data_dump +
            f"---\n\n{task.prompt}\n\n"
            "Write the check-in. YOU decide what matters, what to skip, how to format. "
            "Only show future events. Calendar events are pre-tagged with importance: "
            "[!!] critical, [!] high, plain = normal, ' ·' = low. "
            "GROUP your output by importance — lead with critical/high, then normal, "
            "skip low entirely unless explicitly relevant. Mention event type (work/health/travel/etc) "
            "where it adds context (e.g. 'leave 1h early for travel'). "
            "Flag anything coming up that needs prep (birthdays, deadlines, holidays). "
            "Use tools to take action if needed. Keep it concise — no raw data dumps."
        )

        return await self._run_agent_loop(
            endpoint_url, model, task, session_id,
            system_prompt=(crew.personality or "").strip() if crew else None,
            disabled_tools=None, relevant_tools=None,
            override_user_message=context,
        )

    async def _execute_llm_task(self, task, db) -> str:
        """Execute an LLM task with full tool access via the agent loop."""
        from core.database import Session as DbSession, ChatMessage, CrewMember

        # If this task is wired to a CrewMember (personal assistant, custom
        # crew), prefer the crew member's persona/model/endpoint as overrides.
        crew = None
        if getattr(task, "crew_member_id", None):
            try:
                crew = db.query(CrewMember).filter(CrewMember.id == task.crew_member_id).first()
            except Exception:
                crew = None

        # Determine endpoint + model
        endpoint_url = task.endpoint_url
        model = task.model
        if (not endpoint_url or not model) and crew:
            endpoint_url = endpoint_url or crew.endpoint_url
            model = model or crew.model
        if not endpoint_url or not model:
            endpoint_url, model = self._resolve_defaults(db, task.owner)
        if not endpoint_url or not model:
            raise RuntimeError("No model/endpoint configured")
        # Record the resolved model so _execute_task_locked can persist it on
        # the run (tasks rarely pin a model, so this is the only record of
        # which model actually produced the output).
        self._last_run_model = model

        # Ensure a session exists for output
        session_id = task.session_id
        if not session_id:
            session_id = str(uuid.uuid4())
            sess = DbSession(
                id=session_id,
                name=f"[Task] {task.name}",
                endpoint_url=endpoint_url,
                model=model,
                owner=task.owner,
                folder="Tasks",
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
            db.add(sess)
            task.session_id = session_id
            db.commit()
            if self._session_manager:
                try:
                    self._session_manager.sessions[session_id] = self._session_manager._db_to_session(sess)
                except Exception:
                    pass

        # For assistant check-ins: call each tool directly and post results
        # as separate messages. More reliable than hoping the model calls tools.
        is_checkin = crew and crew.is_default_assistant and "check-in" in (task.name or "").lower()
        if is_checkin:
            return await self._execute_checkin(task, crew, db, session_id, endpoint_url, model)

        # Build system prompt: crew member persona overrides the default.
        system_prompt = (
            (crew.personality or "").strip()
            if crew and crew.personality
            else "You are a helpful assistant executing a scheduled task. Use available tools to complete the task thoroughly."
        )
        # Inject current time so the model knows what's past vs upcoming
        tz_name = _resolve_task_timezone(db, task)
        try:
            if tz_name:
                from zoneinfo import ZoneInfo
                from datetime import timezone
                now_local = _utcnow().replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tz_name))
                time_str = now_local.strftime("%A, %B %d %Y, %H:%M %Z")
            else:
                time_str = _utcnow().strftime("%A, %B %d %Y, %H:%M UTC")
        except Exception:
            time_str = _utcnow().strftime("%A, %B %d %Y, %H:%M UTC")
        system_prompt = f"Current time: {time_str}\n\n{system_prompt}"

        # Compute tool filter from CrewMember.enabled_tools if set
        disabled_tools = None
        if crew and crew.enabled_tools:
            try:
                enabled = json.loads(crew.enabled_tools)
                if isinstance(enabled, list) and enabled:
                    from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS
                    all_tools = set(BUILTIN_TOOL_DESCRIPTIONS.keys())
                    disabled_tools = all_tools - set(enabled)
            except Exception:
                pass

        # RAG-select relevant tools for this prompt + always-available assistant tools.
        # Without this, all 40+ tools get sent and models hit their tool limit.
        relevant_tools = None
        try:
            from src.tool_index import get_tool_index, ASSISTANT_ALWAYS_AVAILABLE
            tool_idx = get_tool_index()
            if tool_idx:
                rag_tools = tool_idx.get_tools_for_query(task.prompt or "", k=8)
                relevant_tools = (rag_tools | ASSISTANT_ALWAYS_AVAILABLE)
                if disabled_tools:
                    relevant_tools -= disabled_tools
                logger.info(f"[assistant] RAG selected {len(rag_tools)} tools + {len(ASSISTANT_ALWAYS_AVAILABLE)} always-available = {len(relevant_tools)} total for '{task.name}'")
        except Exception as e:
            logger.warning(f"[assistant] RAG tool selection failed, using all: {e}")

        # Try using the agent loop for full tool access
        try:
            result = await self._run_agent_loop(
                endpoint_url, model, task, session_id,
                system_prompt=system_prompt, disabled_tools=disabled_tools,
                relevant_tools=relevant_tools,
            )
        except Exception as e:
            logger.warning(f"Agent loop failed for task '{task.name}', falling back to simple call: {e}")
            from src.llm_core import llm_call_async
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task.prompt},
            ]
            result = await llm_call_async(url=endpoint_url, model=model, messages=messages, timeout=120)

        # Strip the model's chain-of-thought before saving/delivering. Task
        # output is LLM-only, so prose=True (which also removes untagged
        # "The user wants me to…" reasoning) is safe here — without this the
        # thinking leaked into the saved result.
        try:
            from src.text_helpers import strip_think
            result = strip_think(result or "", prose=True, prompt_echo=True).strip() or result
        except Exception:
            pass

        return result

    async def _deliver_task_result(self, task, result: str, db, model: str = None):
        """Deliver a completed task result according to output_target.

        This is intentionally shared by LLM/research/action tasks so built-in
        actions cannot drift into hidden delivery paths that disagree with the
        task's visible output target.
        """
        from core.database import Session as DbSession, ChatMessage, CrewMember

        output = task.output_target or "session"
        if (
            output == "session"
            and (getattr(task, "task_type", "") or "") == "action"
            and (getattr(task, "action", "") or "") in self._SILENT_ACTIONS
        ):
            return
        if output.startswith("mcp__"):
            await self._deliver_via_mcp(output, task, result)
            return

        if self._is_email_output_target(output):
            await self._deliver_via_email(output, task, result)
            return

        if output != "session":
            return

        endpoint_url = task.endpoint_url
        model_name = model or task.model
        crew = None
        if getattr(task, "crew_member_id", None):
            try:
                crew = db.query(CrewMember).filter(CrewMember.id == task.crew_member_id).first()
            except Exception:
                crew = None
        if (not endpoint_url or not model_name) and crew:
            endpoint_url = endpoint_url or crew.endpoint_url
            model_name = model_name or crew.model
        if not endpoint_url or not model_name:
            try:
                resolved_url, resolved_model = self._resolve_defaults(db, task.owner)
                endpoint_url = endpoint_url or resolved_url
                model_name = model_name or resolved_model
            except Exception:
                pass

        session_id = task.session_id
        if not session_id:
            session_id = str(uuid.uuid4())
            sess = DbSession(
                id=session_id,
                name=f"[Task] {task.name}",
                endpoint_url=endpoint_url or "",
                model=model_name or "",
                owner=task.owner,
                folder="Tasks",
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
            db.add(sess)
            task.session_id = session_id
            db.commit()
            if self._session_manager:
                try:
                    self._session_manager.sessions[session_id] = self._session_manager._db_to_session(sess)
                except Exception:
                    pass

        meta = {}
        if model_name:
            meta["model"] = model_name
        if crew and crew.is_default_assistant:
            meta.update({"source": "cron", "task_id": task.id, "task_name": task.name})
        msg_meta = json.dumps(meta)
        user_content = task.prompt or f"[Task] {task.name}"
        user_msg = ChatMessage(
            id=str(uuid.uuid4()),
            session_id=session_id,
            role="user",
            content=user_content,
            timestamp=_utcnow(),
            meta_data=msg_meta,
        )
        assistant_msg = ChatMessage(
            id=str(uuid.uuid4()),
            session_id=session_id,
            role="assistant",
            content=result or "",
            timestamp=_utcnow(),
            meta_data=msg_meta,
        )
        db.add(user_msg)
        db.add(assistant_msg)
        db.commit()

        if self._session_manager:
            try:
                from core.models import ChatMessage as MemMsg
                sess_obj = self._session_manager.get_session(session_id)
                sess_obj.history.append(MemMsg(role="user", content=user_msg.content, metadata=meta))
                sess_obj.history.append(MemMsg(role="assistant", content=assistant_msg.content, metadata=meta))
            except Exception:
                pass

    @staticmethod
    def _is_email_output_target(output: str) -> bool:
        target = (output or "").strip()
        if target in {"email", "email:self"}:
            return True
        if target.startswith("email:"):
            return True
        return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", target))

    async def _deliver_via_email(self, output: str, task, result: str):
        """Send task output through the app's configured SMTP account.

        Supported output_target values:
        - email / email:self: send to the account's From address
        - email:name@example.com or raw name@example.com: send there
        """
        from email.message import EmailMessage

        target = (output or "").strip()
        explicit = ""
        if target.startswith("email:"):
            explicit = target.split(":", 1)[1].strip()
        elif "@" in target:
            explicit = target

        try:
            from routes.email_routes import _resolve_send_config
            from routes.email_helpers import _send_smtp_message

            cfg = _resolve_send_config(owner=task.owner or "")
            to_addr = explicit or cfg.get("from_address") or cfg.get("smtp_user") or ""
            if not to_addr:
                raise RuntimeError("No email recipient resolved for task output")

            from_addr = cfg.get("from_address") or cfg.get("smtp_user") or to_addr
            msg = EmailMessage()
            msg["From"] = from_addr
            msg["To"] = to_addr
            msg["Subject"] = f"[Task] {task.name}"
            msg["X-Odysseus-Origin"] = "odysseus-ui"
            msg["X-Odysseus-Kind"] = "task"
            msg["X-Odysseus-Ref"] = str(task.id)
            msg.set_content(result or "")
            _send_smtp_message(cfg, from_addr, [to_addr], msg.as_string(), timeout=30)
            logger.info("Task %s emailed result to %s (%sb)", task.id, to_addr, len(result or ""))
        except Exception as e:
            logger.error("Task %s email delivery failed: %s", task.id, e, exc_info=True)
            raise

    async def _run_agent_loop(self, endpoint_url: str, model: str, task, session_id: str,
                              system_prompt: str | None = None,
                              disabled_tools: set | None = None,
                              relevant_tools: set | None = None,
                              override_user_message: str | None = None) -> str:
        """Run the full agent loop with tool access, collecting the final text."""
        from src.agent_loop import stream_agent_loop

        system_content = system_prompt or "You are a helpful assistant executing a scheduled task. Use available tools to complete the task thoroughly."
        user_content = override_user_message or task.prompt
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]

        # Resolve headers from the endpoint's API key
        headers = {}
        try:
            from core.database import SessionLocal, ModelEndpoint
            from src.endpoint_resolver import normalize_base, build_headers
            from src.auth_helpers import owner_filter
            db2 = SessionLocal()
            try:
                ep_q = db2.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
                ep_q = owner_filter(ep_q, ModelEndpoint, task.owner or None)
                eps = ep_q.all()
                for ep in eps:
                    if normalize_base(ep.base_url) in endpoint_url or endpoint_url in normalize_base(ep.base_url):
                        headers = build_headers(ep.api_key, normalize_base(ep.base_url))
                        break
            finally:
                db2.close()
        except Exception:
            pass
        full_text = ""
        tool_results = []

        # Honor per-task max_steps (defense against runaway agent loops).
        # Falls back to 20 if not set — the historical default.
        _task_max_rounds = task.max_steps if task.max_steps and task.max_steps > 0 else 20
        # Tasks are background workloads — they share the Utility model's
        # fallback chain (Settings → Utility Model → Fallbacks). A downed
        # primary endpoint won't silently yield `(no output)` — same recipe
        # chat uses but with the utility list (`utility_model_fallbacks`).
        try:
            from src.endpoint_resolver import resolve_utility_fallback_candidates
            _task_fallbacks = resolve_utility_fallback_candidates(owner=task.owner or None)
        except Exception:
            _task_fallbacks = []
        async for event_str in stream_agent_loop(
            endpoint_url=endpoint_url,
            model=model,
            messages=messages,
            max_rounds=_task_max_rounds,
            session_id=session_id,
            owner=task.owner,
            headers=headers,
            disabled_tools=disabled_tools,
            relevant_tools=relevant_tools,
            fallbacks=_task_fallbacks,
        ):
            if event_str.startswith("data: ") and not event_str.startswith("data: [DONE]"):
                try:
                    data = json.loads(event_str[6:])
                    # Capture text from all event types, not just delta
                    if "delta" in data:
                        full_text += data["delta"]
                    elif data.get("type") == "tool_output":
                        # Tool results — capture summary so we have SOMETHING even
                        # if the model never produces a final text response
                        tool_summary = data.get("stdout") or data.get("output") or data.get("result") or ""
                        if isinstance(tool_summary, str) and tool_summary.strip():
                            tool_results.append(f"[{data.get('tool', '?')}] {tool_summary[:500]}")
                except (json.JSONDecodeError, KeyError):
                    pass

        # Grace summarization — if the model exhausted rounds on tool calls
        # without producing a final text response, do one last LLM call
        # asking it to summarize what it did. Guarantees output.
        if not full_text.strip():
            try:
                from src.llm_core import llm_call_async_with_fallback
                from src.endpoint_resolver import resolve_utility_fallback_candidates
                grace_context = "You ran out of steps. "
                if tool_results:
                    grace_context += "Here's what your tools returned:\n" + "\n".join(tool_results[-5:])
                else:
                    grace_context += "No tool results were captured."
                grace_context += "\n\nSummarize what you accomplished and what's still pending. Be concise."
                _grace_candidates = [(endpoint_url, model, headers)] + resolve_utility_fallback_candidates(owner=task.owner or None)
                full_text = await llm_call_async_with_fallback(
                    _grace_candidates,
                    messages=[
                        {"role": "system", "content": system_content},
                        {"role": "user", "content": grace_context},
                    ],
                    timeout=30,
                )
                full_text = (full_text or "").strip()
            except Exception as e:
                logger.warning(f"Grace summarization failed: {e}")
                if tool_results:
                    full_text = "\n".join(tool_results[-5:])

        return full_text or "(no output)"

    async def _execute_research_task(self, task, db) -> str:
        """Execute a deep research task using DeepResearcher."""
        from core.database import Session as DbSession, ChatMessage
        from src.deep_research import DeepResearcher
        from src.research_handler import RESEARCH_DATA_DIR, ResearchHandler
        from src.research_utils import strip_thinking
        from src.settings import get_setting

        # Resolve endpoint/model: research settings > task settings > session defaults
        endpoint_url = task.endpoint_url
        model = task.model
        headers = {}
        headers_from_resolver = False

        if not endpoint_url or not model:
            try:
                from src.endpoint_resolver import resolve_endpoint
                ep_url, ep_model, ep_headers = resolve_endpoint(
                    "research",
                    endpoint_url or None,
                    model or None,
                    None,
                    owner=task.owner or None,
                )
                endpoint_url = ep_url or endpoint_url
                model = ep_model or model
                if ep_headers is not None:
                    headers = ep_headers
                    headers_from_resolver = True
            except Exception:
                pass

        if not endpoint_url or not model:
            endpoint_url, model = self._resolve_defaults(db, task.owner)
        if not endpoint_url or not model:
            raise RuntimeError("No model/endpoint configured for research")
        # Record the resolved model for the run record (see _execute_task_locked).
        self._last_run_model = model

        # Resolve headers
        try:
            from core.database import ModelEndpoint
            from src.endpoint_resolver import normalize_base, build_headers
            from src.auth_helpers import owner_filter
            db2 = db
            if not headers_from_resolver:
                ep_q = db2.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
                ep_q = owner_filter(ep_q, ModelEndpoint, task.owner or None)
                eps = ep_q.all()
                for ep in eps:
                    if normalize_base(ep.base_url) in endpoint_url or endpoint_url in normalize_base(ep.base_url):
                        headers = build_headers(ep.api_key, normalize_base(ep.base_url))
                        break
        except Exception:
            pass

        max_tokens = int(get_setting("research_max_tokens", 8192))
        extraction_timeout = int(get_setting("research_extraction_timeout_seconds", 90) or 90)
        extraction_concurrency = int(get_setting("research_extraction_concurrency", 3) or 3)

        researcher = DeepResearcher(
            llm_endpoint=endpoint_url,
            llm_model=model,
            llm_headers=headers,
            max_rounds=8,
            max_time=600,  # 10 min for scheduled research
            max_report_tokens=max_tokens,
            extraction_timeout=extraction_timeout,
            extraction_concurrency=extraction_concurrency,
        )

        started_ts = time.time()
        report = await researcher.research(task.prompt)
        completed_ts = time.time()
        try:
            stats = researcher.get_stats() or {}
        except Exception:
            stats = {}

        # Ensure a session exists for output
        session_id = task.session_id
        if not session_id:
            session_id = str(uuid.uuid4())
            sess = DbSession(
                id=session_id,
                name=f"[Research] {task.name}",
                endpoint_url=endpoint_url,
                model=model,
                owner=task.owner,
                folder="Tasks",
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
            db.add(sess)
            task.session_id = session_id
            db.commit()
            if self._session_manager:
                try:
                    self._session_manager.sessions[session_id] = self._session_manager._db_to_session(sess)
                except Exception:
                    pass

        # Persist scheduled research in the same on-disk shape used by the
        # Research panel. Without this, task research had Markdown output but
        # no Library entry and no visual report route to open.
        try:
            RESEARCH_DATA_DIR.mkdir(parents=True, exist_ok=True)
            findings = getattr(researcher, "findings", []) or []
            payload = {
                "query": task.prompt or task.name or "Scheduled research",
                "status": "done",
                "result": report,
                "raw_report": strip_thinking(report or ""),
                "sources": ResearchHandler._extract_sources(findings),
                "raw_findings": ResearchHandler._extract_raw_findings(findings),
                "stats": stats,
                "category": "scheduled",
                "started_at": started_ts,
                "completed_at": completed_ts,
                "owner": task.owner or "",
                "task_id": task.id,
                "task_name": task.name,
            }
            (RESEARCH_DATA_DIR / f"{session_id}.json").write_text(json.dumps(payload), encoding="utf-8")
            try:
                from src.event_bus import fire_event
                fire_event("research_completed", task.owner or None)
            except Exception:
                logger.debug("research_completed event dispatch failed", exc_info=True)
        except Exception as e:
            logger.warning("Failed to persist task research report %s: %s", session_id, e)

        return report

    async def _run_chained(self, task_id: str):
        """Run a chained task. Acquires _executing membership the same way
        run_task_now does so an overlapping scheduler tick can't double-dispatch
        the same task while the chain run is in flight."""
        async with self._executing_lock:
            if task_id in self._executing:
                return  # already in flight (manual trigger, scheduler tick, or another chain)
            self._executing.add(task_id)
        await self._execute_task(task_id)

    def _has_chain_cycle(self, db, start_id: str, max_depth: int = 10, owner: str | None = None) -> bool:
        """Detect cycles in task chains."""
        from core.database import ScheduledTask
        visited = set()
        current = start_id
        for _ in range(max_depth):
            if current in visited:
                return True
            visited.add(current)
            task = db.query(ScheduledTask).filter(ScheduledTask.id == current).first()
            if owner is not None and task and task.owner != owner:
                return True
            if not task or not task.then_task_id:
                return False
            current = task.then_task_id
        return True  # too deep, treat as cycle

    def _resolve_defaults(self, db, owner):
        """Find the first available endpoint + model from an existing session."""
        from core.database import Session as DbSession
        try:
            recent = db.query(DbSession).filter(
                DbSession.endpoint_url.isnot(None),
                DbSession.model.isnot(None),
                *([DbSession.owner == owner] if owner else []),
            ).order_by(DbSession.created_at.desc()).first()
            if recent:
                return recent.endpoint_url, recent.model
        except Exception:
            pass
        return None, None

    async def _deliver_via_mcp(self, tool_name: str, task, result: str):
        """Send the task result via an MCP tool (e.g. Gmail send).

        Resolves a recipient (so email-style tools have a 'to') by trying the
        configured From address first (the `daily_brief` pattern — email
        yourself) then falling back to the task owner. Common recipient field
        names (to / recipient / email / address) are all populated so we don't
        have to special-case each tool's schema; the MCP tool ignores keys it
        doesn't recognise.
        """
        from src.agent_tools import get_mcp_manager
        mcp = get_mcp_manager()
        if not mcp:
            logger.warning(f"Task {task.id}: MCP manager not available for delivery")
            return

        # Resolve recipient — prefer the configured email From (the established
        # "email yourself" pattern from daily_brief), fall back to task.owner.
        # `_get_email_config()` is the single source of truth that handles both
        # the legacy `email_from` setting and the per-account DB rows.
        recipient = None
        try:
            from routes.email_helpers import _get_email_config
            cfg = _get_email_config() or {}
            recipient = cfg.get("from_address") or None
        except Exception as _e:
            logger.debug(f"_deliver_via_mcp: email config lookup failed: {_e}")
        if not recipient and task.owner and "@" in str(task.owner):
            recipient = task.owner

        args = {
            "subject": f"[Task] {task.name}",
            "body": result,
            "headers": {
                "X-Odysseus-Origin": "odysseus-ui",
                "X-Odysseus-Kind": "task",
                "X-Odysseus-Ref": str(task.id),
            },
        }
        if recipient:
            # Cover the common field names so we work across MCP servers (Gmail,
            # generic SMTP, Slack DMs, etc.) without having to hard-code each.
            args["to"] = recipient
            args["recipient"] = recipient
            args["email"] = recipient
            args["address"] = recipient
        else:
            logger.warning(
                f"Task {task.id}: no recipient resolved for MCP delivery via {tool_name} — "
                "set an email From address in Settings or give the task an owner email."
            )
        try:
            mcp_result = await mcp.call_tool(tool_name, args)
            stderr = mcp_result.get("stderr", "")
            stdout = mcp_result.get("stdout", "")
            body_len = len(result or "")
            exit_code = mcp_result.get("exit_code", 0)
            if exit_code != 0:
                logger.warning(
                    f"Task {task.id} MCP delivery FAILED via {tool_name}: "
                    f"exit={exit_code} stderr={stderr[:400]!r} stdout={stdout[:400]!r}"
                )
            else:
                # Include the MCP tool's own stdout (e.g. email_server returns
                # "Sent email to ... with subject ...") + the body size so a
                # silent SMTP failure is easier to spot in the logs.
                logger.info(
                    f"Task {task.id} delivered via MCP tool {tool_name} "
                    f"(to={recipient or '<unset>'}, body={body_len}b, reply={stdout[:200]!r})"
                )
        except Exception as e:
            logger.error(f"Task {task.id} MCP delivery failed: {e}")

    async def run_task_now(self, task_id: str, *, force: bool = False):
        """Manually trigger a task execution."""
        if force:
            asyncio.create_task(self._execute_task(task_id, bypass_model_slot=True, release_executing=False))
            return True
        async with self._executing_lock:
            if task_id in self._executing:
                return False
            self._executing.add(task_id)
        asyncio.create_task(self._execute_task(task_id))
        return True

    async def stop_task(self, task_id: str) -> bool:
        """Request cancellation of a running/queued task and mark its run aborted."""
        handle = self._task_handles.get(task_id)
        stopped = False
        if handle and not handle.done():
            handle.cancel()
            stopped = True
        async with self._executing_lock:
            if task_id in self._executing:
                self._executing.discard(task_id)
                stopped = True

        stopped = self._mark_run_aborted(task_id) or stopped
        return stopped

    async def ensure_defaults(self, owner: str):
        """Create default housekeeping tasks for this owner (idempotent per action)."""
        from core.database import SessionLocal, ScheduledTask
        try:
            from routes.prefs_routes import _load_for_user
            _prefs = _load_for_user(owner) or {}
        except Exception:
            _prefs = {}
        tasks_enabled = bool(_prefs.get("tasks_enabled"))
        tasks_opened = bool(_prefs.get("tasks_opened"))

        db = SessionLocal()
        try:
            # Normalize old built-ins that were created before `task_type` /
            # `action` were reliable. Match by current or legacy name so stale
            # rows cannot keep running as scheduled LLM tasks forever.
            name_to_action = {}
            for action, defs in HOUSEKEEPING_DEFAULTS.items():
                name_to_action[defs["name"]] = action
                for legacy in defs.get("legacy_names") or []:
                    name_to_action[legacy] = action
            possible_names = list(name_to_action.keys())
            legacy_named = db.query(ScheduledTask).filter(
                ScheduledTask.owner == owner,
                ScheduledTask.name.in_(possible_names),
            ).all()
            for task in legacy_named:
                action = name_to_action.get(task.name)
                if not action:
                    continue
                task.task_type = "action"
                task.action = action

            from core.database import TaskRun
            retired_ids = [
                row[0] for row in db.query(ScheduledTask.id).filter(
                    ScheduledTask.owner == owner,
                    ScheduledTask.task_type == "action",
                    ScheduledTask.action.in_(list(RETIRED_HOUSEKEEPING_ACTIONS)),
                ).all()
            ]
            if retired_ids:
                db.query(TaskRun).filter(TaskRun.task_id.in_(retired_ids)).delete(synchronize_session=False)
            retired_count = db.query(ScheduledTask).filter(
                ScheduledTask.owner == owner,
                ScheduledTask.task_type == "action",
                ScheduledTask.action.in_(list(RETIRED_HOUSEKEEPING_ACTIONS)),
            ).delete(synchronize_session=False)
            # Sweep orphan TaskRun rows (parent task deleted previously) so
            # retired actions stop showing in Activity. Only runs when at least
            # one live task exists — avoids wiping run history on a fresh DB.
            try:
                live_ids = {row[0] for row in db.query(ScheduledTask.id).all()}
                if live_ids:
                    db.query(TaskRun).filter(~TaskRun.task_id.in_(list(live_ids))).delete(synchronize_session=False)
            except Exception:
                pass
            existing_actions = {
                row[0] for row in db.query(ScheduledTask.action).filter(
                    ScheduledTask.owner == owner,
                    ScheduledTask.task_type == "action",
                ).all() if row[0]
            }
            renamed = []
            builtin_tasks = db.query(ScheduledTask).filter(
                ScheduledTask.owner == owner,
                ScheduledTask.task_type == "action",
                ScheduledTask.action.in_(list(HOUSEKEEPING_DEFAULTS.keys())),
            ).all()
            by_action = {}
            for task in builtin_tasks:
                by_action.setdefault(task.action, []).append(task)
            removed_dupes = []
            kept_ids = set()
            for action, tasks in by_action.items():
                defs = HOUSEKEEPING_DEFAULTS.get(action)
                if not defs:
                    continue
                desired_trigger = defs.get("trigger_type", "schedule")

                def _score(candidate):
                    matches_default = (
                        (candidate.trigger_type or "schedule") == desired_trigger
                        and (candidate.trigger_event or None) == defs.get("trigger_event")
                        and (candidate.trigger_count or 1) == (defs.get("trigger_count") or 1)
                        and (candidate.schedule or None) == defs.get("schedule")
                        and (candidate.scheduled_time or None) == defs.get("scheduled_time")
                        and (candidate.cron_expression or None) == defs.get("cron_expression")
                    )
                    created = candidate.created_at or datetime.min
                    created_key = (created.toordinal(), created.hour, created.minute, created.second, created.microsecond)
                    return (1 if matches_default else 0, 1 if candidate.status == "active" else 0, created_key)

                keep = sorted(tasks, key=_score, reverse=True)[0]
                kept_ids.add(keep.id)
                for dupe in tasks:
                    if dupe.id == keep.id:
                        continue
                    db.delete(dupe)
                    removed_dupes.append(action)

            for task in [t for t in builtin_tasks if t.id in kept_ids]:
                defs = HOUSEKEEPING_DEFAULTS.get(task.action)
                if not defs:
                    continue
                legacy_names = set(defs.get("legacy_names") or [])
                if (task.name or "") in legacy_names:
                    task.name = defs["name"]
                    renamed.append(task.action)
                normalized = False
                desired_trigger = defs.get("trigger_type", "schedule")
                if task.action == "check_email_urgency":
                    old_crons = set(defs.get("old_cron_expressions") or [])
                    if task.schedule == "cron" and (task.cron_expression or "") in old_crons:
                        task.cron_expression = defs["cron_expression"]
                        task.next_run = compute_next_run(
                            defs["schedule"], defs["scheduled_time"], None, None,
                            after=_utcnow(), cron_expression=defs["cron_expression"],
                            tz_name=_resolve_task_timezone(db, task),
                        )
                        normalized = True
                if desired_trigger == "event" and (
                    (task.trigger_type or "schedule") != "event"
                    or task.trigger_event != defs.get("trigger_event")
                    or (task.trigger_count or 1) != (defs.get("trigger_count") or 1)
                    or task.schedule is not None
                    or task.scheduled_time is not None
                    or task.scheduled_date is not None
                    or task.cron_expression is not None
                ):
                    task.trigger_type = "event"
                    task.trigger_event = defs.get("trigger_event")
                    task.trigger_count = defs.get("trigger_count") or 1
                    task.trigger_counter = 0
                    task.schedule = defs.get("schedule")
                    task.scheduled_time = defs.get("scheduled_time")
                    task.scheduled_day = None
                    task.scheduled_date = None
                    task.cron_expression = defs.get("cron_expression")
                    normalized = True
                if normalized:
                    renamed.append(task.action)
                ships_paused = bool(defs.get("ship_paused"))
                if not tasks_enabled and not tasks_opened:
                    if ships_paused and task.status == "active":
                        task.status = "paused"
                    elif not ships_paused and task.status == "paused":
                        task.status = "active"
                        if (task.trigger_type or "schedule") == "schedule":
                            task.next_run = compute_next_run(
                                task.schedule, task.scheduled_time,
                                task.scheduled_day, task.scheduled_date,
                                after=_utcnow(), cron_expression=task.cron_expression,
                                tz_name=_resolve_task_timezone(db, task),
                            )
                # Built-in housekeeping/action jobs should not create browser
                # task notifications; user AI/research tasks still can.
                task.notifications_enabled = False
                if (task.output_target or "session") == "session":
                    task.output_target = defs.get("output_target", "none")
            seeded = []
            for action, defs in HOUSEKEEPING_DEFAULTS.items():
                if action in existing_actions:
                    continue
                trigger_type = defs.get("trigger_type", "schedule")
                next_run = None
                if trigger_type == "schedule":
                    next_run = compute_next_run(
                        defs["schedule"], defs["scheduled_time"], None, None,
                        after=_utcnow(), cron_expression=defs["cron_expression"],
                    )
                ships_paused = bool(defs.get("ship_paused"))
                task = ScheduledTask(
                    id=str(uuid.uuid4())[:8],
                    owner=owner,
                    name=defs["name"],
                    task_type="action",
                    action=action,
                    trigger_type=trigger_type,
                    trigger_event=defs.get("trigger_event"),
                    trigger_count=defs.get("trigger_count"),
                    trigger_counter=0,
                    schedule=defs["schedule"],
                    scheduled_time=defs["scheduled_time"],
                    cron_expression=defs["cron_expression"],
                    next_run=next_run,
                    # Most built-ins are active by default. The invasive
                    # AI/email/calendar tasks opt into a paused starting state
                    # via ship_paused so users can enable them deliberately.
                    status="paused" if ships_paused else "active",
                    output_target=defs.get("output_target", "none"),
                    notifications_enabled=False,
                )
                db.add(task)
                seeded.append(action)
            if seeded or renamed or removed_dupes or retired_count:
                logger.info(
                    "Housekeeping defaults for %s: seeded=%s renamed=%s deduped=%s retired=%s",
                    owner, seeded, sorted(set(renamed)), sorted(set(removed_dupes)), retired_count,
                )
            # Always commit — the orphan-run sweep above may have produced
            # pending deletes even when no defaults changed.
            db.commit()
        except Exception as e:
            logger.warning(f"Failed to create default tasks: {e}")
        finally:
            db.close()
        # Always ensure the personal assistant exists (independent of other tasks).
        try:
            await self.ensure_assistant_defaults(owner)
        except Exception as e:
            logger.warning(f"Failed to seed assistant for {owner}: {e}")

    async def ensure_assistant_defaults(self, owner: str):
        """Create the personal-assistant CrewMember, its pinned session, and three
        daily check-in ScheduledTasks for this owner — idempotent on is_default_assistant."""
        # Hard-reject synthetic owners. Without this, AuthMiddleware-stamped
        # values like 'internal-tool' (loopback agent-tool callbacks) or 'api'
        # (bearer-token integrations) would get a real assistant + 3 daily
        # check-ins seeded, which then double-fire alongside the human user's
        # check-ins. This was the root cause of the duplicate 'Morning check-in'
        # rows we had to manually clean up.
        if not owner or owner in {"internal-tool", "api", "demo", "system"}:
            logger.info(f"ensure_assistant_defaults: skip synthetic owner {owner!r}")
            return
        from core.database import SessionLocal, CrewMember, ScheduledTask
        from core.database import Session as DbSession

        db = SessionLocal()
        try:
            existing = db.query(CrewMember).filter(
                CrewMember.owner == owner,
                CrewMember.is_default_assistant == True,  # noqa: E712
            ).first()
            if existing:
                return  # already seeded

            # Resolve a default model/endpoint from any existing session so the
            # assistant has something to call. The user can change this later.
            endpoint_url, model = self._resolve_defaults(db, owner)

            default_personality = (
                "You are the user's personal assistant. Concise, warm, a little dry. "
                "Never waste time with fluff. Default to English. Only match the other language when replying to a non-English email.\n\n"

                "CORE RULE: You MUST use your tools to take action — do not describe what you would do. "
                "Never say 'I would check your calendar' — actually call manage_calendar. "
                "Never say 'I can look that up' — actually call web_search or search_chats. "
                "If you have a tool for it, use it. No hypotheticals, no promises, only actions and results.\n\n"

                "DECISION FRAMEWORK — follow these rules, not just tool descriptions:\n\n"

                "CONTEXT GATHERING (before any response involving a specific person):\n"
                "1. resolve_contact if you only have a name and need their email\n"
                "2. search_chats for recent conversations mentioning them or their topic\n"
                "3. manage_memory to check stored facts about them\n"
                "Skip steps you already have answers for. Don't search for the user themselves.\n\n"

                "EMAIL HANDLING:\n"
                "- If a document is open in the editor, that IS the email. Use update_document to write the reply.\n"
                "- BEFORE drafting any reply: gather context (steps above) about the sender and topic.\n"
                "- When an email mentions a date/meeting: check calendar for conflicts, add if clear.\n"
                "- When an email asks a question you can't answer from context: say so honestly. Never fabricate.\n"
                "- Skip automated/marketing emails in check-ins. Only surface human-sent, actionable ones.\n"
                "- Never duplicate information the user already saw in a previous check-in.\n\n"

                "ESCALATION LADDER (when you need info you don't have):\n"
                "1. search_chats (fast, free)\n"
                "2. manage_memory (fast, free)\n"
                "3. web_search (medium cost)\n"
                "4. trigger_research (expensive, async — only for complex multi-source questions)\n"
                "Stop as soon as you have a sufficient answer.\n\n"

                "'SEND TO [NAME]' FLOW:\n"
                "1. resolve_contact to find their email\n"
                "2. If a document is open, use its content as the body\n"
                "3. Draft the email in a document (create_document with language='email')\n"
                "4. Tell the user to review — NEVER auto-send\n\n"

                "SELF-IMPROVEMENT — use manage_memory constantly:\n"
                "- When the user corrects you, IMMEDIATELY store the correction as a memory.\n"
                "- After every check-in or task, store new facts you learned (contacts, preferences, patterns).\n"
                "- Before responding about a person or topic, search_chats and manage_memory FIRST.\n"
                "- Build knowledge over time: who people are, what projects are active, how the user likes things done.\n"
                "- If something failed or you got corrected, store WHY so you never repeat it.\n"
                "- When you figure out a multi-step workflow that works, save it as a SKILL using manage_skills.\n"
                "  A skill is a reusable procedure. Next time, recall the skill instead of figuring it out again.\n"
                "- Before starting a complex task, check manage_skills for an existing procedure.\n\n"

                "AUTONOMY RULES:\n"
                "- Auto-add calendar events from clear meeting invitations (mention what you added)\n"
                "- Auto-draft email replies (cached for when user clicks Reply)\n"
                "- NEVER send emails without explicit user instruction\n"
                "- NEVER delete anything without explicit instruction\n"
                "- If uncertain, ask rather than guess"
            )

            # Create the singleton session first (CrewMember.session_id links to it).
            session_id = str(uuid.uuid4())
            sess = DbSession(
                id=session_id,
                name="Assistant",
                endpoint_url=endpoint_url or "",
                model=model or "",
                owner=owner,
                is_important=True,
                mode="agent",
                folder="Assistant",
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
            db.add(sess)
            db.flush()

            # Create the assistant CrewMember.
            crew_id = str(uuid.uuid4())
            assistant = CrewMember(
                id=crew_id,
                owner=owner,
                name="Assistant",
                avatar=None,
                user_name=None,
                personality=default_personality,
                model=model,
                endpoint_url=endpoint_url,
                greeting=None,
                enabled_tools=json.dumps([
                    "manage_calendar", "manage_notes", "manage_tasks", "manage_memory",
                    "list_email_accounts", "list_emails", "read_email", "send_email", "reply_to_email", "archive_email",
                    "mark_email_read", "delete_email", "resolve_contact",
                    "search_chats", "web_search", "web_fetch", "read_file",
                    "create_document", "update_document", "edit_document",
                    "generate_image", "trigger_research",
                    "download_model", "serve_model", "list_served_models", "stop_served_model",
                    "edit_image",
                ]),
                session_id=session_id,
                is_active=True,
                sort_order=0,
                is_default_assistant=True,
                timezone=None,  # user picks in settings; None = legacy UTC behavior
            )
            db.add(assistant)

            # Link the session back to the crew member so UI can resolve either way.
            sess.crew_member_id = crew_id

            # No auto-seeded check-in tasks. The old behaviour created three
            # daily ScheduledTasks (Morning/Midday/Evening) under every new
            # owner, which was intrusive and ran under whatever account was
            # marked is_default globally. Users now create their own
            # recurring tasks from the Tasks UI.

            db.commit()
            logger.info(f"Seeded personal assistant (crew {crew_id}) for owner={owner}")
        except Exception as e:
            logger.exception(f"ensure_assistant_defaults({owner}) failed: {e}")
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            db.close()
