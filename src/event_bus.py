"""
event_bus.py

Lightweight event bus for triggering automation tasks based on events
like session creation, message sends, etc. Supports payload propagation:
callers pass structured data (e.g., email UID, subject, from) that gets
stored on the triggered ScheduledTask and injected into the agent context
when the task executes.
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

from src.constants import AUTH_FILE

logger = logging.getLogger(__name__)

_task_scheduler = None


def set_task_scheduler(scheduler):
    """Wire up the scheduler reference (called from app.py on startup)."""
    global _task_scheduler
    _task_scheduler = scheduler


def get_task_scheduler():
    """Return the current task scheduler instance."""
    return _task_scheduler


def fire_event(event_name: str, owner: Optional[str] = None, payload: Optional[Dict[str, Any]] = None):
    """Fire an event — increments counters and triggers tasks that hit threshold.

    Args:
        event_name: Name of the event (e.g. "email_received", "session_created")
        owner: Username of the owner/triggering user
        payload: Optional structured data carried with the event. Stored on
                 the triggered ScheduledTask and injected into the agent's
                 context when the task executes. Examples:
                 - email_received: {"uid": "90186", "subject": "...", "from": "...", "account": "gmail"}
                 - session_created: {"session_id": "abc123", "model": "..."}

    Safe to call from both sync and async contexts.
    """
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_handle_event(event_name, owner, payload))
    except RuntimeError:
        # No running loop — run in a new one (shouldn't happen in FastAPI)
        asyncio.run(_handle_event(event_name, owner, payload))


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


async def _handle_event(event_name: str, owner: Optional[str] = None, payload: Optional[Dict[str, Any]] = None):
    """Process an event: increment counters, fire tasks that hit their threshold.

    If a payload is provided, it is serialised to JSON and stored on each
    matching task's ``trigger_payload`` column. The payload overwrites any
    previous value so the task always receives the *latest* event context.
    """
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

        # Serialise the payload once so we can store it on every matching task
        payload_json = json.dumps(payload) if payload is not None else None

        for task in tasks:
            threshold = task.trigger_count or 1
            task.trigger_counter = (task.trigger_counter or 0) + 1

            # Store the event payload so the task handler can access it.
            # Overwrites the previous payload so the task always gets the
            # *most recent* event context at the time the counter hit.
            if payload_json is not None:
                task.trigger_payload = payload_json

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
