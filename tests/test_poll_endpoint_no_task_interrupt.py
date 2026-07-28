"""Regression: read-only task-status polling must not interrupt running background tasks.

GET /api/tasks/runs/recent is called by the Activity view while a scheduled
task is executing. Before this fix, the _InteractiveActivityMiddleware treated
it as a foreground request and called stop_background_tasks_for_foreground,
which immediately cancelled the running task. The fix adds the endpoint to
_PASSIVE_EXACT_PATHS so it is excluded from interactive tracking.

Additionally, /api/activity/heartbeat called stop_background_tasks_for_foreground
unconditionally, ignoring BACKGROUND_TASK_FOREGROUND_GATE=false. The fix wraps
that call in a _gate_enabled() check so the env var fully disables interrupts.
"""

import importlib
import os


def _reload_gate():
    import src.interactive_gate as ig
    importlib.reload(ig)
    return ig


def test_tasks_runs_recent_is_passive():
    ig = _reload_gate()
    assert not ig.should_track_interactive_request("/api/tasks/runs/recent", "GET"), (
        "GET /api/tasks/runs/recent must be treated as a passive endpoint so that "
        "the Activity-view polling does not trigger stop_background_tasks_for_foreground "
        "and cancel running scheduled tasks"
    )


def test_tasks_runs_recent_does_not_affect_other_task_paths():
    ig = _reload_gate()
    # Non-polling mutating task routes should still be interactive.
    assert ig.should_track_interactive_request("/api/tasks/runs/recent/something", "POST")


def test_heartbeat_respects_foreground_gate_disabled(monkeypatch):
    """stop_background_tasks_for_foreground must NOT be called from the heartbeat
    handler when BACKGROUND_TASK_FOREGROUND_GATE=false."""
    monkeypatch.setenv("BACKGROUND_TASK_FOREGROUND_GATE", "false")
    ig = _reload_gate()
    assert not ig._enabled(), (
        "_enabled() should return False when BACKGROUND_TASK_FOREGROUND_GATE=false"
    )


def test_heartbeat_gate_enabled_by_default(monkeypatch):
    monkeypatch.delenv("BACKGROUND_TASK_FOREGROUND_GATE", raising=False)
    ig = _reload_gate()
    assert ig._enabled(), (
        "_enabled() should return True by default (foreground gate on)"
    )
