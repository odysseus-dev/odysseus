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
from typing import Optional, Callable, Dict, List, Any

logger = logging.getLogger(__name__)

_task_scheduler = None
_event_handlers: Dict[str, List[Callable]] = {}  # Maps event names to lists of handler functions


def set_task_scheduler(scheduler):
    """Wire up the scheduler reference (called from app.py on startup)."""
    global _task_scheduler
    _task_scheduler = scheduler


def get_task_scheduler():
    """Return the current task scheduler instance."""
    return _task_scheduler


def subscribe(event_name: str, handler: Callable):
    """Subscribe a handler function to an event."""
    if event_name not in _event_handlers:
        _event_handlers[event_name] = []
    _event_handlers[event_name].append(handler)
    logger.debug(f"Subscribed handler {handler.__name__} to event '{event_name}'")


def fire_event(event_name: str, owner: Optional[str] = None, **kwargs):
    """Fire an event — increments counters and triggers tasks that hit threshold.

    Safe to call from both sync and async contexts.
    """
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_handle_event(event_name, owner, **kwargs))
    except RuntimeError:
        # No running loop — run in a new one (shouldn't happen in FastAPI)
        asyncio.run(_handle_event(event_name, owner, **kwargs))


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
        from src.constants import DATA_DIR

        auth_path = os.path.join(DATA_DIR, "auth.json")
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


async def _handle_event(event_name: str, owner: Optional[str] = None, **kwargs):
    """Process an event: increment counters, fire tasks that hit their threshold,
    and call registered event handlers."""
    from core.database import SessionLocal, ScheduledTask

    resolved_owner = _resolve_event_owner(owner)
    db = SessionLocal()
    try:
        # Handle scheduled tasks that are triggered by this event
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
        if tasks:
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
                        if task.next_run and task.next_run > datetime.utcnow():
                            logger.info(
                                f"Event '{event_name}' reached task '{task.name}', "
                                f"but it is already deferred until {task.next_run}"
                            )
                            continue
                        logger.info(f"Event '{event_name}' triggered task '{task.name}' (every {threshold})")
                        await _task_scheduler.run_task_now(task.id)
                    else:
                        logger.warning(f"Event triggered task '{task.name}' but no scheduler available")
                else:
                    db.commit()
                    logger.debug(f"Event '{event_name}': task '{task.name}' counter {task.trigger_counter}/{threshold}")
        else:
            # No tasks matched, but we still need to commit if we opened a session
            db.commit()

        # Call registered event handlers for this event
        if event_name in _event_handlers:
            for handler in _event_handlers[event_name]:
                try:
                    # Pass the owner and any additional kwargs to the handler
                    if asyncio.iscoroutinefunction(handler):
                        await handler(owner=resolved_owner, **kwargs)
                    else:
                        handler(owner=resolved_owner, **kwargs)
                except Exception as e:
                    logger.exception(f"Error in handler '{handler.__name__}' for event '{event_name}': {e}")

    except Exception:
        logger.exception(f"Error handling event '{event_name}'")
    finally:
        db.close()
