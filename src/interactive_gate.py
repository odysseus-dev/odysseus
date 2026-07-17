"""Foreground activity gate for background work.

Background tasks are allowed to run only after normal UI/API traffic has
settled. This keeps scheduled jobs and email pollers from competing with the
user opening Odysseus, Cookbook, email, documents, notes, or other panels.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import os
import time


_ACTIVE_REQUESTS = 0
_LAST_ACTIVITY = 0.0
_LAST_BROWSER_ACTIVITY = 0.0
_COND: asyncio.Condition | None = None
_COND_LOOP: asyncio.AbstractEventLoop | None = None


def _enabled() -> bool:
    return os.getenv("BACKGROUND_TASK_FOREGROUND_GATE", "true").lower() not in {"0", "false", "no", "off"}


def _quiet_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("BACKGROUND_TASK_QUIET_MS", "1500")) / 1000.0)
    except Exception:
        return 1.5


def _max_wait_seconds() -> float:
    """0 means wait indefinitely until the UI is quiet."""
    try:
        return max(0.0, float(os.getenv("BACKGROUND_TASK_MAX_WAIT_SECONDS", "0")))
    except Exception:
        return 0.0


def _browser_active_seconds() -> float:
    """How long a visible Odysseus browser heartbeat blocks background tasks."""
    try:
        return max(0.0, float(os.getenv("BACKGROUND_TASK_BROWSER_ACTIVE_SECONDS", "45")))
    except Exception:
        return 45.0


def _condition() -> asyncio.Condition:
    global _COND, _COND_LOOP
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if _COND is None or _COND_LOOP is not loop:
        _COND = asyncio.Condition()
        _COND_LOOP = loop
    return _COND


_PASSIVE_EXACT_PATHS = {
    "/api/activity/heartbeat",
    "/api/client-perf",
    "/api/tasks/notifications",
    "/api/research/active",
    "/api/email/urgency-state",
}

_PASSIVE_PREFIXES = (
    "/api/chat/stream_status",
    "/api/health",
    "/api/prefs",
)


def should_track_interactive_request(path: str, method: str = "GET") -> bool:
    if not _enabled():
        return False
    if (method or "").upper() == "OPTIONS":
        return False
    if path in _PASSIVE_EXACT_PATHS:
        return False
    if any(path.startswith(prefix) for prefix in _PASSIVE_PREFIXES):
        return False
    return True


async def mark_browser_activity() -> None:
    """Record that an authenticated browser tab is visibly using Odysseus."""
    global _LAST_BROWSER_ACTIVITY
    if not _enabled():
        return
    cond = _condition()
    async with cond:
        _LAST_BROWSER_ACTIVITY = time.monotonic()
        cond.notify_all()


def _has_recent_browser_activity(now: float | None = None) -> bool:
    ttl = _browser_active_seconds()
    if ttl <= 0 or _LAST_BROWSER_ACTIVITY <= 0:
        return False
    return ((now if now is not None else time.monotonic()) - _LAST_BROWSER_ACTIVITY) < ttl


def has_foreground_activity(now: float | None = None) -> bool:
    """Return True when foreground browser/model work should stop background jobs.

    Passive polling endpoints are excluded by should_track_interactive_request,
    so active/recent request tracking is safe to use here. This matters during
    initial page load: the heartbeat may not have landed yet, but the user is
    already waiting on real UI requests.
    """
    if not _enabled():
        return False
    t = now if now is not None else time.monotonic()
    if _ACTIVE_REQUESTS > 0:
        return True
    if _LAST_ACTIVITY > 0 and (t - _LAST_ACTIVITY) < _quiet_seconds():
        return True
    return _has_recent_browser_activity(t) or _has_active_chat_stream()


def _has_active_chat_stream() -> bool:
    """Best-effort check for foreground model work that outlives HTTP requests.

    Chat/agent streams are detached from the browser SSE so a stream can keep
    running after the request that started it has returned. Background LLM
    tasks must still wait for those runs; otherwise helpers like email
    auto-translate compete with the user's active chat on the same local model.
    """
    try:
        from routes import chat_routes as _chat_routes
        active_streams = getattr(_chat_routes, "_active_streams", {}) or {}
        if active_streams:
            return True
    except Exception:
        pass
    try:
        from src import agent_runs
        runs = getattr(agent_runs, "_RUNS", {}) or {}
        return any(getattr(run, "status", None) == "running" for run in runs.values())
    except Exception:
        return False


@asynccontextmanager
async def track_interactive_request(path: str = "", method: str = ""):
    global _ACTIVE_REQUESTS, _LAST_ACTIVITY
    if not _enabled():
        yield
        return

    cond = _condition()
    async with cond:
        _ACTIVE_REQUESTS += 1
        _LAST_ACTIVITY = time.monotonic()
        cond.notify_all()
    try:
        yield
    finally:
        async with cond:
            _ACTIVE_REQUESTS = max(0, _ACTIVE_REQUESTS - 1)
            _LAST_ACTIVITY = time.monotonic()
            cond.notify_all()


async def wait_for_interactive_quiet(label: str = "") -> bool:
    """Wait until foreground requests have stopped for the configured window.

    Returns True if the caller had to wait at all. The label is intentionally
    only for future logging/debugging so callers can keep their code simple.
    """
    if not _enabled():
        return False

    quiet = _quiet_seconds()
    max_wait = _max_wait_seconds()
    deadline = time.monotonic() + max_wait if max_wait > 0 else None
    cond = _condition()
    waited = False

    while True:
        async with cond:
            now = time.monotonic()
            quiet_remaining = quiet - (now - _LAST_ACTIVITY)
            active_stream = _has_active_chat_stream()
            browser_active = _has_recent_browser_activity(now)
            if _ACTIVE_REQUESTS <= 0 and quiet_remaining <= 0 and not active_stream and not browser_active:
                return waited

            waited = True
            timeout = 0.25 if (_ACTIVE_REQUESTS > 0 or active_stream or browser_active) else min(max(quiet_remaining, 0.05), 0.5)
            if deadline is not None:
                remaining = deadline - now
                if remaining <= 0:
                    return waited
                timeout = min(timeout, remaining)
            try:
                await asyncio.wait_for(cond.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass
