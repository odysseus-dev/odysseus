"""Tests for bg_jobs.kill and the manage_bg_jobs agent tool.

Process-free: the store/dir are redirected to tmp, _pid_alive is forced True so
seeded "running" jobs stay running through refresh(), and _kill is stubbed so no
real signal is sent. Jobs are scoped to a chat (session_id), which is the main
invariant under test.
"""
import asyncio
import json
import time

import pytest

from src import bg_jobs
from src.agent_tools.bg_job_tools import ManageBgJobsTool


@pytest.fixture
def store(tmp_path, monkeypatch):
    jobs_dir = tmp_path / "bg_jobs"
    jobs_dir.mkdir()
    monkeypatch.setattr(bg_jobs, "_STORE", tmp_path / "bg_jobs.json")
    monkeypatch.setattr(bg_jobs, "_JOBS_DIR", jobs_dir)
    monkeypatch.setattr(bg_jobs, "_pid_alive", lambda pid: True)
    killed: list = []
    monkeypatch.setattr(bg_jobs, "_kill", lambda pid: killed.append(pid))
    return {"dir": jobs_dir, "killed": killed}


def _seed(session_id="sess-a", status="running", job_id="job0001", output="", pid=4321):
    rec = {
        "id": job_id, "session_id": session_id, "command": "sleep 60",
        "status": status, "pid": pid, "started_at": time.time(),
        "ended_at": None if status == "running" else time.time(),
        "exit_code": None if status == "running" else 0,
        "max_runtime_s": 3600, "followed_up": False,
        "log_path": str(bg_jobs._JOBS_DIR / f"{job_id}.log"),
        "exit_path": str(bg_jobs._JOBS_DIR / f"{job_id}.exit"),
    }
    if output:
        (bg_jobs._JOBS_DIR / f"{job_id}.log").write_text(output, encoding="utf-8")
    jobs = bg_jobs._load()
    jobs[job_id] = rec
    bg_jobs._save(jobs)
    return rec


def _run(args, session_id="sess-a"):
    return asyncio.run(ManageBgJobsTool().execute(json.dumps(args), {"session_id": session_id, "owner": None}))


# ── bg_jobs.kill ────────────────────────────────────────────────────────────

def test_kill_marks_killed_and_suppresses_followup(store):
    _seed(job_id="job0001", pid=4321)
    rec = bg_jobs.kill("job0001")
    assert rec["status"] == "failed"
    assert rec["killed"] is True
    assert rec["exit_code"] == -1
    # followed_up True so the monitor won't ALSO auto-continue a deliberate kill.
    assert rec["followed_up"] is True
    assert store["killed"] == [4321]


def test_kill_unknown_job_returns_none(store):
    assert bg_jobs.kill("nope") is None


def test_kill_finished_job_is_noop(store):
    _seed(job_id="done01", status="done")
    rec = bg_jobs.kill("done01")
    assert rec["status"] == "done"
    assert store["killed"] == []  # no signal sent to an already-finished job


def test_result_text_reports_killed(store):
    rec = _seed(job_id="job0001")
    bg_jobs.kill("job0001")
    assert "killed" in bg_jobs.result_text(bg_jobs.get("job0001")).lower()


# ── manage_bg_jobs tool ─────────────────────────────────────────────────────

def test_no_session_is_rejected(store):
    out = asyncio.run(ManageBgJobsTool().execute('{"action":"list"}', {"session_id": None}))
    assert "error" in out


def test_list_empty(store):
    assert "No background jobs" in _run({"action": "list"})["output"]


def test_list_scoped_to_session(store):
    _seed(session_id="sess-a", job_id="aaaa")
    _seed(session_id="sess-b", job_id="bbbb")
    out = _run({"action": "list"}, session_id="sess-a")["output"]
    assert "aaaa" in out and "bbbb" not in out


def test_output_returns_captured_log(store):
    _seed(job_id="job0001", output="hello from the job\n")
    out = _run({"action": "output", "job_id": "job0001"})["output"]
    assert "hello from the job" in out


def test_output_cross_session_denied(store):
    _seed(session_id="sess-a", job_id="job0001", output="secret")
    out = _run({"action": "output", "job_id": "job0001"}, session_id="sess-b")
    assert "error" in out and "secret" not in out.get("error", "")


def test_kill_via_tool(store):
    _seed(job_id="job0001", pid=999)
    out = _run({"action": "kill", "job_id": "job0001"})
    assert "Killed" in out["output"]
    assert store["killed"] == [999]
    assert bg_jobs.get("job0001")["killed"] is True


def test_kill_cross_session_denied(store):
    _seed(session_id="sess-a", job_id="job0001")
    out = _run({"action": "kill", "job_id": "job0001"}, session_id="sess-b")
    assert "error" in out
    assert store["killed"] == []  # never touched another chat's job


def test_kill_requires_job_id(store):
    assert "error" in _run({"action": "kill"})


def test_unknown_action(store):
    assert "error" in _run({"action": "frobnicate"})


def test_action_aliases(store):
    _seed(job_id="job0001", output="aliased")
    # 'read' aliases to output, 'jobs' to list, 'stop' to kill
    assert "aliased" in _run({"action": "read", "job_id": "job0001"})["output"]
    assert "job0001" in _run({"action": "jobs"})["output"]
    assert "Killed" in _run({"action": "stop", "job_id": "job0001"})["output"]


# ── intent classifier: short bg-job commands must not be dropped as low-signal ─
# A short imperative ("kill that job") otherwise trips the low-signal gate, which
# skips tool retrieval entirely and never surfaces manage_bg_jobs (the live bug
# this feature hit). These lock in that bg-job control reaches the files domain.


@pytest.mark.parametrize("msg", [
    "stop the job",
    "kill that job",
    "Now kill that background job.",
    "is the job done?",
    "check the job output",
    "list my jobs",
    "kill the bg task",
])
def test_bg_job_commands_are_not_low_signal(msg):
    from src.agent_loop import _classify_agent_request, _DOMAIN_TOOL_MAP
    r = _classify_agent_request([{"role": "user", "content": msg}], msg)
    assert r["low_signal"] is False
    assert "files" in r["domains"]
    # files domain seeds manage_bg_jobs, so it gets offered to the model.
    assert "manage_bg_jobs" in _DOMAIN_TOOL_MAP["files"]


@pytest.mark.parametrize("msg", [
    "run this in the background",   # launching, not managing
    "find me a job listing",        # unrelated use of "job"
])
def test_non_bg_messages_do_not_trip_files_domain(msg):
    from src.agent_loop import _classify_agent_request
    r = _classify_agent_request([{"role": "user", "content": msg}], msg)
    assert "files" not in r["domains"]
