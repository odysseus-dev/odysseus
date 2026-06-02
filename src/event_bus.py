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
_webhook_manager = None

# Internal event-bus names → public outbound-webhook event names. Only events
# that are NOT already emitted as webhooks at their own rich-data call sites
# belong here: ``session_created`` / ``message_sent`` are deliberately absent
# because session_routes.py and chat_helpers.py already fire ``session.created``
# and ``chat.message`` directly — bridging them here too would double-deliver.
_WEBHOOK_EVENT_NAMES = {
    "research_completed": "research.completed",
    "document_created": "document.created",
    "memory_added": "memory.added",
    "email_received": "email.received",
    "skill_added": "skill.added",
}


def set_task_scheduler(scheduler):
    """Wire up the scheduler reference (called from app.py on startup)."""
    global _task_scheduler
    _task_scheduler = scheduler


def set_webhook_manager(manager):
    """Wire up the outbound webhook manager (called from app.py on startup).

    Lets the event bus mirror lifecycle events to subscribed webhooks without
    threading ``webhook_manager`` through the many scattered routes/handlers
    that emit them — they all already funnel through ``fire_event``.
    """
    global _webhook_manager
    _webhook_manager = manager


def _dispatch_webhook(event_name: str, owner: Optional[str]) -> None:
    """Mirror an internal event to any matching outbound webhooks.

    Best-effort and non-blocking: ``fire_and_forget`` schedules delivery, and a
    failure here must never break the event's primary task-trigger path below.
    """
    manager = _webhook_manager
    if manager is None:
        return
    webhook_event = _WEBHOOK_EVENT_NAMES.get(event_name)
    if webhook_event is None:
        return
    try:
        manager.fire_and_forget(webhook_event, {"owner": (owner or "").strip() or None})
    except Exception:
        logger.debug("Webhook dispatch failed for event '%s'", event_name, exc_info=True)


def get_task_scheduler():
    """Return the current task scheduler instance."""
    return _task_scheduler


def fire_event(event_name: str, owner: Optional[str] = None):
    """Fire an event — increments counters and triggers tasks that hit threshold.

    Safe to call from both sync and async contexts.
    """
    _dispatch_webhook(event_name, owner)
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
