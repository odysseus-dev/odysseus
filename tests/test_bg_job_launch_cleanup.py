"""Transactional cleanup coverage for detached sandbox launches."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src import bg_jobs


class _FakeProcess:
    pid = 5818

    def __init__(self):
        self.wait_calls = []

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        return 0


@pytest.fixture
def launch_context(tmp_path, monkeypatch):
    jobs_dir = tmp_path / "jobs"
    store = tmp_path / "jobs.json"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(bg_jobs, "_JOBS_DIR", jobs_dir)
    monkeypatch.setattr(bg_jobs, "_STORE", store)
    monkeypatch.setattr(bg_jobs, "sandbox_command", lambda *args, **kwargs: ["/trusted/sandbox"])
    monkeypatch.setattr(bg_jobs, "environment_for_sandbox_launcher", lambda: {})
    monkeypatch.setattr(
        bg_jobs,
        "configured_process_execution_mode",
        lambda: bg_jobs.ProcessExecutionMode.SANDBOX,
    )
    class AvailableProfile:
        def supports(self, _profile):
            return True

        def reason_for(self, _profile):
            return ""

    class AvailableCapability:
        def for_mode(self, _mode):
            return AvailableProfile()

    monkeypatch.setattr(
        bg_jobs,
        "process_capability",
        lambda: AvailableCapability(),
    )
    monkeypatch.setattr(bg_jobs, "detached_popen_kwargs", lambda: {})
    return jobs_dir, store, workspace


def _job_artifacts(jobs_dir: Path):
    return sorted(path for path in jobs_dir.glob("*.*") if path.is_file()) if jobs_dir.exists() else []


def test_sandbox_failure_after_command_file_creation_cleans_artifacts(
    launch_context,
    monkeypatch,
):
    jobs_dir, store, workspace = launch_context
    monkeypatch.setattr(
        bg_jobs,
        "sandbox_command",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("sandbox denied")),
    )

    with pytest.raises(RuntimeError, match="sandbox denied"):
        bg_jobs.launch("printf secret", session_id="chat-1", cwd=str(workspace))

    assert _job_artifacts(jobs_dir) == []
    assert not store.exists()


def test_popen_failure_cleans_artifacts(launch_context, monkeypatch):
    jobs_dir, store, workspace = launch_context

    def fail_popen(*args, **kwargs):
        raise OSError("popen denied")

    monkeypatch.setattr(bg_jobs.subprocess, "Popen", fail_popen)

    with pytest.raises(OSError, match="popen denied"):
        bg_jobs.launch("printf secret", session_id="chat-1", cwd=str(workspace))

    assert _job_artifacts(jobs_dir) == []
    assert not store.exists()


def test_save_failure_kills_untracked_process_and_cleans_everything(
    launch_context,
    monkeypatch,
):
    jobs_dir, store, workspace = launch_context
    process = _FakeProcess()
    killed = []
    monkeypatch.setattr(bg_jobs.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(bg_jobs, "_kill", lambda pid: killed.append(pid))

    def save_then_fail(jobs):
        store.write_text(json.dumps(jobs), encoding="utf-8")
        raise OSError("store unavailable")

    monkeypatch.setattr(bg_jobs, "_save", save_then_fail)

    with pytest.raises(OSError, match="store unavailable"):
        bg_jobs.launch("printf secret", session_id="chat-1", cwd=str(workspace))

    assert killed == [process.pid]
    assert process.wait_calls == [2]
    assert bg_jobs._load() == {}
    assert _job_artifacts(jobs_dir) == []


def test_repeated_blocked_launches_do_not_accumulate_artifacts(
    launch_context,
    monkeypatch,
):
    jobs_dir, store, workspace = launch_context
    monkeypatch.setattr(
        bg_jobs,
        "sandbox_command",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("blocked")),
    )

    for _ in range(5):
        with pytest.raises(RuntimeError, match="blocked"):
            bg_jobs.launch("printf blocked", session_id="chat-1", cwd=str(workspace))

    assert _job_artifacts(jobs_dir) == []
    assert not store.exists()


def test_successful_launch_persists_record_and_private_command_file(
    launch_context,
    monkeypatch,
):
    jobs_dir, store, workspace = launch_context
    process = _FakeProcess()
    monkeypatch.setattr(bg_jobs.subprocess, "Popen", lambda *args, **kwargs: process)

    record = bg_jobs.launch("printf success", session_id="chat-1", cwd=str(workspace))

    command_path = Path(record["log_path"]).with_name(f"{record['id']}.cmd.sh")
    plan_path = Path(record["log_path"]).with_name(f"{record['id']}.plan.json")
    assert record["status"] == "running"
    assert bg_jobs._load()[record["id"]]["pid"] == process.pid
    assert store.exists()
    assert command_path.read_text(encoding="utf-8") == "printf success\n"
    assert command_path.stat().st_mode & 0o777 == 0o600
    assert plan_path.stat().st_mode & 0o777 == 0o600
    assert json.loads(plan_path.read_text(encoding="utf-8"))["argv"] == ["/trusted/sandbox"]
    assert jobs_dir.stat().st_mode & 0o777 == 0o700
    assert command_path in _job_artifacts(jobs_dir)
    assert plan_path in _job_artifacts(jobs_dir)


def test_command_file_creation_is_exclusive(launch_context, monkeypatch):
    jobs_dir, store, workspace = launch_context
    process = _FakeProcess()
    monkeypatch.setattr(bg_jobs.uuid, "uuid4", lambda: type("UUID", (), {"hex": "a" * 32})())
    jobs_dir.mkdir(parents=True)
    existing = jobs_dir / ("a" * 12 + ".cmd.sh")
    existing.write_text("existing\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        bg_jobs.launch("printf replacement", session_id="chat-1", cwd=str(workspace))

    assert existing.read_text(encoding="utf-8") == "existing\n"
    assert not store.exists()


def test_detached_wrapper_writes_success_exit_artifact(tmp_path):
    log_path = tmp_path / "job.log"
    exit_path = tmp_path / "job.exit"
    plan_path = tmp_path / "job.plan.json"
    plan_bytes = json.dumps(
        {
            "version": 1,
            "argv": [sys.executable, "-I", "-c", "print('ok')"],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    plan_path.write_bytes(plan_bytes)
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            bg_jobs._DETACHED_SANDBOX_WRAPPER,
            str(plan_path),
            hashlib.sha256(plan_bytes).hexdigest(),
            str(log_path),
            str(exit_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert log_path.read_text(encoding="utf-8").strip() == "ok"
    assert exit_path.read_text(encoding="utf-8") == "0"
    assert log_path.stat().st_mode & 0o777 == 0o600
    assert exit_path.stat().st_mode & 0o777 == 0o600


def test_full_access_launch_uses_full_filesystem_profile_and_retained_network(
    launch_context,
    monkeypatch,
):
    jobs_dir, _store, workspace = launch_context
    process = _FakeProcess()
    monkeypatch.setattr(
        bg_jobs,
        "configured_process_execution_mode",
        lambda: bg_jobs.ProcessExecutionMode.FULL_ACCESS,
    )
    monkeypatch.setattr(
        bg_jobs,
        "full_access_command",
        lambda argv, **_kwargs: ["/trusted/bwrap-full", *argv],
    )
    monkeypatch.setattr(bg_jobs.subprocess, "Popen", lambda *args, **kwargs: process)

    record = bg_jobs.launch(
        "printf success",
        session_id="chat-1",
        cwd=str(workspace),
        network_profile=bg_jobs.SandboxNetworkProfile.BROKERED_ONLY,
    )

    plan_path = jobs_dir / f"{record['id']}.plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert record["execution_mode"] == "full_access"
    assert record["network_enforcement"] == "brokered_http_https"
    assert record["warning"] == bg_jobs.FULL_ACCESS_WARNING
    assert plan["argv"][:2] == ["/trusted/bwrap-full", "/bin/bash"]
    assert "Execution mode: full_access" in bg_jobs.result_text(
        {**record, "status": "done", "exit_code": 0}
    )
