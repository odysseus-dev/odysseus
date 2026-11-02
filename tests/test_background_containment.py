"""Detached Bash uses the same boundary; restart and kill retain ownership."""
import asyncio
import os
import time
from collections import namedtuple

import pytest

from src import bg_jobs, containment, process_ownership, process_reaper, tool_execution
from src.tool_execution import NO_TOOL_SECURITY_CONTEXT
from tests.runtime_evidence_helpers import server_authorized_executor
from tests.process_resource_helpers import launch, get, kill


@pytest.fixture
def jobs(tmp_path, monkeypatch):
    from src.agent_runtime import process_resources
    monkeypatch.setattr(process_resources, "_LAUNCH_DIR", tmp_path / "private" / "launches")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(bg_jobs, "_JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(bg_jobs, "_STORE", tmp_path / "jobs.json")
    monkeypatch.setattr(containment, "_store_path", lambda: tmp_path / "grants.json")
    monkeypatch.setattr(containment, "CONTAINMENT_MODE", containment.MODE_REPORT_ONLY)
    monkeypatch.setattr(containment, "MECHANISMS", tuple(m for m in containment.MECHANISMS if m.name == "process_group"))
    monkeypatch.setattr(tool_execution, "_owner_is_admin", lambda owner: True)
    launched = []
    yield workspace, launched
    for record in launched:
        current = get(record["id"])
        if current and current["status"] == "running":
            kill(record["id"])
        proc = bg_jobs._LIVE_PROCS.pop(record["pid"], None)
        if proc:
            proc.wait(timeout=8)


def finished(job_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        record = get(job_id)
        if record["status"] != "running":
            return record
        time.sleep(0.03)
    pytest.fail("background job did not finish")


def test_detached_execution_owns_boundary_and_reports_death(jobs):
    path, launched = jobs
    record = launch("printf captured", "chat", cwd=str(path))
    launched.append(record)
    result = finished(record["id"])
    assert result["output"] == "captured"
    assert result["exit_code"] == 0
    assert result["containment"]["mechanism"] == "process_group"
    assert result["containment"]["contained"] is False
    assert result["teardown"]["dead"] is True


def test_supervisor_setup_failure_closes_unstarted_grant(jobs):
    import json
    import subprocess
    import sys
    from pathlib import Path
    path, _ = jobs
    spec = containment.agent_spec(str(path), dict(os.environ), 5)
    grant = containment.acquire(spec, owner="failed-supervisor")
    payload = {
        "store_path": str(containment._store_path()),
        "grant": {**grant.to_dict(), "owner": grant.owner},
        "spec": {"workspace": str(path), "env": dict(spec.env), "wall_clock_s": 5,
                 "required": sorted(spec.required)},
        "command": "printf effect > must-not-exist",
        "log_path": str(path / "missing-directory" / "job.log"),
        "result_path": str(path / "result.json"), "exit_path": str(path / "exit"),
    }
    worker = Path(containment.__file__).with_name("containment_worker.py")
    result = subprocess.run([sys.executable, str(worker)], input=json.dumps(payload),
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0  # Supervisor publishes the failed job result.
    assert "KeyError" in result.stderr  # Legacy unlinked payload fails before execution.
    assert not (path / "must-not-exist").exists()
    assert containment.active_grants() == []
    assert (path / "exit").read_text() == "1"
    report = json.loads((path / "result.json").read_text())
    assert report["containment"]["executed"] is False
    assert report["containment"]["contained"] is False
    assert report["teardown"]["dead"] is True


async def test_bg_marker_refuses_without_spawning_and_authority_still_gates(jobs, monkeypatch):
    path, _ = jobs
    monkeypatch.setattr(containment, "CONTAINMENT_MODE", containment.MODE_ENFORCING)
    monkeypatch.setattr(bg_jobs.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("uncontained bg spawn"))
    block = namedtuple("Block", "tool_type content")("bash", "#!bg\nprintf unsafe")
    execute = server_authorized_executor(tool_execution.execute_tool_block)
    _, result = await execute(block, session_id="chat", owner="alice", workspace=str(path),
                              security_context=NO_TOOL_SECURITY_CONTEXT)
    assert result["containment"]["executed"] is False
    assert "bg_job_id" not in result
    _, denied = await tool_execution.execute_tool_block(
        block, session_id="chat", owner="alice", workspace=str(path),
        security_context=NO_TOOL_SECURITY_CONTEXT, request_authority=None,
    )
    assert denied["failure_kind"] == "request_authority_denied"


def test_detached_supervisor_enforces_timeout(jobs):
    path, launched = jobs
    record = launch("sleep 60", "chat", cwd=str(path), max_runtime_s=1)
    launched.append(record)
    result = finished(record["id"])
    assert result["timed_out"] is True
    assert result["teardown"]["dead"] is True


def test_restart_keeps_verified_background_supervisor(jobs):
    path, launched = jobs
    record = launch("sleep 60", "chat", cwd=str(path))
    launched.append(record)
    report = process_reaper.reap_containment_grants()
    assert report["background_kept"] == 1
    killed = kill(record["id"])
    assert killed["killed"] is True
    assert killed["teardown"]["dead"] is True


def test_kill_never_marks_a_foreign_pid_killed(jobs, monkeypatch):
    record = {"id": "stale", "status": "running", "pid": 12345, "start_token": "old",
              "session_id": "chat", "started_at": time.time(), "exit_path": "missing"}
    bg_jobs._save({"stale": record})
    monkeypatch.setattr(process_ownership, "verify", lambda *args: process_ownership.FOREIGN)
    monkeypatch.setattr(bg_jobs, "_kill", lambda *args, **kwargs: pytest.fail("foreign process signalled"))
    result = bg_jobs._kill_record(record)  # Service cleanup still refuses foreign identity.
    assert result.dead is False


def test_running_detached_output_and_concurrent_grants_are_preserved(jobs):
    path, launched = jobs
    for number in range(3):
        launched.append(launch(f"printf job-{number}; sleep 0.3", "chat", cwd=str(path)))
    for number, record in enumerate(launched):
        assert finished(record["id"])["output"] == f"job-{number}"
    grants = containment._load_records()
    assert {record["containment_id"] for record in launched} <= grants.keys()
    assert all(grants[record["containment_id"]]["release"]["dead"] for record in launched)


def test_detached_output_is_available_while_running(jobs):
    path, launched = jobs
    record = launch("printf progress; sleep 5", "chat", cwd=str(path))
    launched.append(record)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        current = get(record["id"])
        if "progress" in current["output"]:
            assert current["status"] == "running"
            return
        time.sleep(0.03)
    pytest.fail("detached stdout was unavailable until completion")
