"""
event_bus.py

Lightweight event bus for triggering automation tasks based on events
like session creation, message sends, etc.
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Optional

from src.constants import AUTH_FILE

logger = logging.getLogger(__name__)

_task_scheduler = None

# Additive event sinks: async ``(event_name, owner)`` callables invoked on every
# fired event, independent of the task-scheduler path. Overlays (the companion
# mobile-push bridge) subscribe here so they react to the SAME lifecycle events
# their producers already emit — no dependence on any other delivery layer.
_event_sinks = []
# Strong refs to in-flight sink tasks so the GC can't collect one mid-delivery
# (loop.create_task holds only a weak reference).
_sink_tasks: set = set()


def set_task_scheduler(scheduler):
    """Wire up the scheduler reference (called from app.py on startup)."""
    global _task_scheduler
    _task_scheduler = scheduler


def get_task_scheduler():
    """Return the current task scheduler instance."""
    return _task_scheduler


def add_event_sink(sink):
    """Register an async ``(event_name, owner)`` sink for every fired event.

    Best-effort and isolated: a sink that raises is logged and can never break
    the task-trigger path or another sink. Used by additive consumers (companion
    mobile push) that must not own a DB row or block core event handling.
    """
    _event_sinks.append(sink)


async def _run_event_sink(sink, event_name: str, owner: Optional[str]) -> None:
    try:
        await sink(event_name, owner)
    except Exception:
        logger.warning("Event sink failed for '%s'", event_name, exc_info=True)


def _dispatch_event_sinks(event_name: str, owner: Optional[str]) -> None:
    """Fan an event out to every registered sink, tracking each task strongly."""
    if not _event_sinks:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    for sink in list(_event_sinks):
        if loop is not None:
            task = loop.create_task(_run_event_sink(sink, event_name, owner))
            _sink_tasks.add(task)
            task.add_done_callback(_sink_tasks.discard)
        else:
            # No running loop (sync caller) — deliver in a throwaway loop so the
            # sink still runs rather than being silently dropped.
            try:
                asyncio.run(_run_event_sink(sink, event_name, owner))
            except Exception:
                logger.debug("Event sink run failed for '%s'", event_name, exc_info=True)


def fire_event(event_name: str, owner: Optional[str] = None):
    """Fire an event — increments counters and triggers tasks that hit threshold.

    Safe to call from both sync and async contexts.
    """
    _dispatch_event_sinks(event_name, owner)
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_handle_event(event_name, owner))
    except RuntimeError:
        # No running loop — run in a new one (shouldn't happen in FastAPI)
        asyncio.run(_handle_event(event_name, owner))


def _resolve_event_owner(owner: Optional[str]) -> Optional[str]:
    """Resolve ownerless app events to the primary configured user.

    Some event sources run from localhost/internal code paths where request
    middleware is not present, so they cannot pass a username. Treating that as
    "all owners" made built-in tasks run once per account. Instead, route those
    events to the first admin account, matching the legacy-owner migration.
    """
    owner = (owner or "").strip()
    if owner:
        return owner

    try:
        auth_path = AUTH_FILE
        with open(auth_path, "r", encoding="utf-8") as f:
            users = (json.load(f).get("users") or {})
        for username, data in users.items():
            if data.get("is_admin") is True:
                return username
        if users:
            return next(iter(users))
    except Exception:
        logger.debug("Could not resolve ownerless event owner", exc_info=True)
    return None


async def _handle_event(event_name: str, owner: Optional[str] = None):
    """Process an event: increment counters, fire tasks that hit their threshold."""
    from core.database import SessionLocal, ScheduledTask

    resolved_owner = _resolve_event_owner(owner)
    db = SessionLocal()
    try:
        filters = [
            ScheduledTask.trigger_type == "event",
            ScheduledTask.trigger_event == event_name,
            ScheduledTask.status == "active",
        ]
        if resolved_owner:
            filters.append(ScheduledTask.owner == resolved_owner)
        else:
            filters.append(ScheduledTask.owner == None)  # noqa: E711

        tasks = db.query(ScheduledTask).filter(*filters).all()
        if not tasks:
            return

        for task in tasks:
            threshold = task.trigger_count or 1
            task.trigger_counter = (task.trigger_counter or 0) + 1

            if task.trigger_counter >= threshold:
                task.trigger_counter = 0
                # Persist the trigger before handing off to the in-memory
                # scheduler. If the process restarts while the task is queued
                # behind a model call, `next_run <= now` makes the trigger
                # survive reboot instead of losing the event after the counter
                # has already reset.
                task.next_run = datetime.utcnow()
                db.commit()
                # Fire the task
                if _task_scheduler:
                    logger.info(f"Event '{event_name}' triggered task '{task.name}' (every {threshold})")
                    await _task_scheduler.run_task_now(task.id)
                else:
                    logger.warning(f"Event triggered task '{task.name}' but no scheduler available")
            else:
                db.commit()
                logger.debug(f"Event '{event_name}': task '{task.name}' counter {task.trigger_counter}/{threshold}")

    except Exception:
        logger.exception(f"Error handling event '{event_name}'")
    finally:
        db.close()
