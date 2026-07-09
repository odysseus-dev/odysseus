"""Regression tests for passive browser keepalives vs scheduled tasks (#5294)."""
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def test_tasks_runs_recent_is_passive_foreground_path():
    from src.interactive_gate import _PASSIVE_EXACT_PATHS

    assert "/api/tasks/runs/recent" in _PASSIVE_EXACT_PATHS


def test_heartbeat_endpoint_does_not_cancel_background_tasks():
    app_src = (_REPO / "app.py").read_text(encoding="utf-8")
    start = app_src.index('@app.post("/api/activity/heartbeat")')
    chunk = app_src[start : start + 900]
    assert "mark_browser_activity" not in chunk
    assert "stop_background_tasks_for_foreground" not in chunk