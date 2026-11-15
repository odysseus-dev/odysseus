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


def test_conclusive_runner_markers_still_override_a_recorded_done():
    """A gated repo / wrong pattern fetch ends DOWNLOAD_OK + 'Fetching 0 files'
    and is a genuine failure. The durable guard must not launder it into a
    permanent success just because a client already recorded 'done'."""
    source = _read("routes/cookbook_routes.py")

    assert "_marker = classify_dead_download(full_snapshot) if task_type == \"download\" else None" in source
    assert "status, download_zero_files = _marker" in source
    # The marker branch must sit inside the persisted-terminal guard.
    guard = source.index("if _persisted_terminal:\n                # Durable terminal state")
    marker = source.index("_marker = classify_dead_download(full_snapshot)")
    assert guard < marker


def test_zero_files_classifier_is_marker_based():
    source = _read("routes/cookbook_output.py")
    assert 'if "DOWNLOAD_OK" in full_snapshot:' in source
    assert "return (\"error\", True)" in source


# -- invariant 2: zero-files must be verified against the actual cache -------


def test_zero_files_is_verified_against_the_cache_before_calling_failure():
    """HF prints "Fetching 0 files" both when nothing matched and when every
    file was already cached. Only an empty result may be reported as a
    failure; a complete cache is a success."""
    source = _read("routes/cookbook_routes.py")

    # Verification is shared after all liveness/terminal branches, including
    # Windows. Behavioral variants live in test_cookbook_windows_completion.py.
    verify = source.index("# All platforms share the final download decision.")
    assert source.index("elif is_alive or (local_win_task and full_snapshot):") < verify
    assert source.index("if _download_cache_matches(", verify) > verify


def test_gated_repo_output_is_classified_from_markers_not_guessed():
    """The classifier keys off the runner markers only; it must not infer a
    cause (gated repo, wrong pattern, empty result) from prose."""
    source = _read("routes/cookbook_output.py")
    assert 'if "DOWNLOAD_OK" in full_snapshot:' in source
    assert 'if "DOWNLOAD_FAILED" in full_snapshot:' in source
    assert "HuggingFace Token" not in source


def test_cache_probe_distinguishes_shell_from_populated_cache(tmp_path):
    """Functional: the same probe the endpoint uses must report an empty
    refs-only repo as incomplete and a populated one as complete."""
    import subprocess as sp
    import sys

    from routes.cookbook_output import HF_CACHE_COMPLETE_PROBE

    shell = tmp_path / "hub" / "models--acme--empty-shell"
    (shell / "refs").mkdir(parents=True)
    (shell / "refs" / "main").write_text("deadbeef", encoding="utf-8")

    real = tmp_path / "hub" / "models--acme--real"
    blob = real / "blobs"
    blob.mkdir(parents=True)
    (blob / "weights").write_bytes(b"\0" * 4096)
    snap = real / "snapshots" / "abc"
    snap.mkdir(parents=True)
    (snap / "model.gguf").write_bytes(b"\0" * 4096)

    def probe(repo, cache_root):
        return sp.run(
            [sys.executable, "-c", HF_CACHE_COMPLETE_PROBE, repo, str(cache_root)],
            capture_output=True, text=True,
        ).returncode

    # The probe appends /hub to the cache root itself.
    assert probe("acme/empty-shell", tmp_path) == 1
    assert probe("acme/real", tmp_path) == 0


def test_match_probe_requires_the_requested_pattern(tmp_path):
    """An unrelated already-cached file must not satisfy verification."""
    import subprocess as sp
    import sys

    from routes.cookbook_output import HF_CACHE_MATCH_PROBE

    def probe(repo, root, pat):
        return sp.run(
            [sys.executable, "-c", HF_CACHE_MATCH_PROBE, repo, str(root), pat],
            capture_output=True, text=True,
        ).returncode

    repo = tmp_path / "hub" / "models--acme--multi"
    snap = repo / "snapshots" / "abc"
    snap.mkdir(parents=True)
    (snap / "model-Q8_0.gguf").write_bytes(b"\0" * 1024)

    # Requested quant is present -> verified.
    assert probe("acme/multi", tmp_path, "*Q8_0*") == 0
    # A different quant is absent -> must fail even though the repo has a file.
    assert probe("acme/multi", tmp_path, "*Q4_K_M*") == 1
    # Empty pattern falls back to "repo has anything".
    assert probe("acme/multi", tmp_path, "") == 0


def test_corrected_terminal_outcome_is_persisted():
    """The state file must not keep a stale `done` that the backend reports as
    an error; otherwise a browser POST keeps re-sending the false success."""
    source = _read("routes/cookbook_routes.py")

    assert "if status != _task_status and (" in source
    assert "_set_task_status(session_id, status, expected_status=_task_status)" in source
