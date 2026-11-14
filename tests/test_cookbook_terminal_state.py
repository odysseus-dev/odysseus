"""Durable terminal states in the Cookbook task status endpoint.

A task whose outcome is already recorded in ``data/cookbook_state.json`` must
keep that outcome. The status endpoint used to re-derive the status from the
retained pane text for any local task that had a stored snapshot, so a log that
merely contained the word "error" could flip a completed download back to
error — and a session reusing the name could flip a finished task back to
running.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_persisted_terminal_short_circuits_status_derivation():
    source = _read("routes/cookbook_routes.py")

    # The guard must exist and assign the persisted status verbatim.
    assert "if _persisted_terminal:" in source
    assert "status = _task_status" in source

    guard = source.index("if _persisted_terminal:\n                # Durable terminal state")
    derive = source.index("elif is_alive or (local_win_task and full_snapshot):")
    assert guard < derive, "the durable guard must run before any re-derivation"


def test_error_word_in_stored_log_cannot_downgrade_a_completion():
    """The derivation branch is what turned a stored "error" substring into an
    error status; it must not run for a persisted terminal."""
    source = _read("routes/cookbook_routes.py")

    branch = source[source.index("elif is_alive or (local_win_task and full_snapshot):"):]
    branch = branch[:branch.index("else:")]
    assert 'has_error = "error" in lower' in branch
    # That branch is now only reachable when the state is not terminal.
    assert "if _persisted_terminal:" in source


def test_diagnosis_cannot_override_a_persisted_terminal():
    source = _read("routes/cookbook_routes.py")

    assert "if diagnosis and not _persisted_terminal and status in" in source


def test_disconnect_is_not_completion_on_the_frontend():
    source = _read("static/js/cookbookRunning.js")

    # A failed status fetch returns before any reconciliation happens.
    assert "const res = await _fetchWithTimeout('/api/cookbook/tasks/status'" in source
    assert "if (!res.ok) return;" in source


# -- behavioural: the state POST must not let a browser override a failure ----

import json

import httpx
import pytest
from fastapi import FastAPI

import routes.cookbook_routes as routes


def _disk_task(sid, status):
    return {
        "sessionId": sid,
        "type": "download",
        "status": status,
        "modelId": "org/model",
        "remoteHost": "",
        "platform": "windows",
        "output": "",
        "payload": {"repo_id": "org/model"},
    }


@pytest.mark.asyncio
async def test_browser_done_cannot_override_a_recorded_failure(monkeypatch, tmp_path):
    """An unverified browser-derived success must not mask a genuine failure.

    The backend recorded `error` for this download. A stale browser tab POSTs
    its own copy claiming `done`; the server must keep its outcome.
    """
    state_file = tmp_path / "cookbook_state.json"
    state_file.write_text(json.dumps({
        "tasks": [_disk_task("cookbook-1", "error")],
        "env": {"servers": []},
    }), encoding="utf-8")
    monkeypatch.setattr(routes, "_COOKBOOK_STATE_PATH", state_file)
    monkeypatch.setattr(routes, "TMUX_LOG_DIR", tmp_path)
    monkeypatch.setenv("AUTH_ENABLED", "false")

    app = FastAPI()
    app.include_router(routes.setup_cookbook_routes())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/cookbook/state", json={
            "env": {"servers": []},
            "tasks": [_disk_task("cookbook-1", "done")],
        })

    assert resp.status_code == 200, resp.text
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    by_id = {t["sessionId"]: t for t in saved["tasks"]}
    assert by_id["cookbook-1"]["status"] == "error"


@pytest.mark.asyncio
async def test_a_genuine_success_still_round_trips(monkeypatch, tmp_path):
    """The guard must not freeze legitimately advancing tasks."""
    state_file = tmp_path / "cookbook_state.json"
    state_file.write_text(json.dumps({
        "tasks": [_disk_task("cookbook-2", "running")],
        "env": {"servers": []},
    }), encoding="utf-8")
    monkeypatch.setattr(routes, "_COOKBOOK_STATE_PATH", state_file)
    monkeypatch.setattr(routes, "TMUX_LOG_DIR", tmp_path)
    monkeypatch.setenv("AUTH_ENABLED", "false")

    app = FastAPI()
    app.include_router(routes.setup_cookbook_routes())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/cookbook/state", json={
            "env": {"servers": []},
            "tasks": [_disk_task("cookbook-2", "done")],
        })

    assert resp.status_code == 200, resp.text
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    by_id = {t["sessionId"]: t for t in saved["tasks"]}
    assert by_id["cookbook-2"]["status"] == "done"
