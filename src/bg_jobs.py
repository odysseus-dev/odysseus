"""Background job execution for the agent's `bash` tool.

Long commands (installs, ffmpeg, model downloads) should NOT block the chat
stream — a multi-minute held SSE connection is fragile (model-stops-early,
timeouts, tab suspend). Instead we launch them **detached** and let an
always-on monitor re-invoke the agent when they finish ("auto-continue").

Design goals:
  * Restart-safe: status is derived from an on-disk exit-code file, not a live
    PID, so a uvicorn restart never loses a job or its result.
  * Idempotent follow-up: a job stays {done, followed_up: False} until the
    agent has actually been re-invoked, so completion can never silently
    "do nothing" — the monitor retries on the next tick.
  * Bounded: a hard max-runtime marks a runaway job failed and STILL triggers
    a follow-up ("timed out"), so you always hear back.

This module only owns launch + state. The monitor / agent re-invocation lives
in the caller (so this stays import-light and unit-testable).
"""

from __future__ import annotations

import json
import os
import sys
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.atomic_io import atomic_write_json, store_transaction
from core.platform_compat import (
    detached_popen_kwargs,
    kill_process_tree,
    pid_alive,
)

from src import process_ownership
from src.constants import BG_JOBS_DIR, BG_JOBS_FILE

_JOBS_DIR = Path(BG_JOBS_DIR)
_STORE = Path(BG_JOBS_FILE)

# A job that runs longer than this is presumed stuck and reaped (the agent
# still gets a "timed out" follow-up so nothing hangs forever).
DEFAULT_MAX_RUNTIME_S = 3600  # 1 hour
# Cap how much captured output we keep / feed back to the model.
_MAX_OUTPUT_CHARS = 16000
# How long a finished-and-followed-up job (record + its .sh/.cmd.sh/.log/.exit
# files) is kept before pruning, so neither the store nor data/bg_jobs/ grows
# without bound. The agent has already consumed the result by then.
_RETENTION_S = 3600  # 1 hour after follow-up
_LIVE_PROCS: dict[int, subprocess.Popen] = {}


def _load() -> Dict[str, Dict[str, Any]]:
    try:
        if _STORE.exists():
            data = json.loads(_STORE.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                return {}
            return {str(job_id): rec for job_id, rec in data.items() if isinstance(rec, dict)}
    except Exception:
        pass
    return {}


def _save(jobs: Dict[str, Dict[str, Any]]) -> None:
    atomic_write_json(str(_STORE), jobs, indent=2)


def _pid_alive(pid: Optional[int]) -> bool:
    # Delegates to the platform-safe probe. NB: a bare os.kill(pid, 0) is unsafe
    # on Windows — CPython routes it to TerminateProcess, which would KILL the
    # job we're only trying to check. core.platform_compat.pid_alive handles
    # both OSes correctly.
    return pid_alive(pid)


@store_transaction(lambda: _STORE)
def launch(command: str, session_id: str, cwd: Optional[str] = None,
           max_runtime_s: int = DEFAULT_MAX_RUNTIME_S, env: Optional[dict] = None) -> Dict[str, Any]:
    """Launch `command` detached. Returns the job record (status='running').

    A trusted detached supervisor owns the shared containment runner, output,
    wall clock and exit metadata, independently of the request/server lifetime.
    """
    from src.agent_runtime.process_resources import require_launch, active_process_operation, publish_launch, launch_path, validate_launch_spec
    from src.agent_runtime.authority import active_request_authority, save_background_authority
    from src.agent_runtime.resources import ProcessResource, BackgroundJobResource
    from src.process_lifecycle import ProcessIdentity
    cwd = cwd or os.getcwd()
    launch_resource = require_launch("bash", cwd=cwd)
    bound = active_process_operation()
    from src.tool_execution import _split_bg_marker
    marked, proposed = _split_bg_marker(bound.operation.input)
    if command != (proposed if marked else bound.operation.input).strip() or session_id != launch_resource.thread_id:
        raise ValueError("Background launch operation or session changed")
    authority = active_request_authority()
    if authority is None or (authority.owner, authority.request_id) != (launch_resource.owner, launch_resource.request_id):
        raise ValueError("Background launch authority changed")
    _JOBS_DIR.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex[:12]
    log_path = _JOBS_DIR / f"{job_id}.log"
    exit_path = _JOBS_DIR / f"{job_id}.exit"

    from src import containment
    from src.agent_tools.subprocess_tools import _owned_spec, _replace_workspace_alias
    spec = _owned_spec(cwd or os.getcwd(), env, max_runtime_s)
    validate_launch_spec(launch_resource, spec)
    grant = containment.acquire(spec, owner=f"bg:{session_id}")
    bounded_command = command
    if containment.FILESYSTEM not in grant.enforced:
        bounded_command = _replace_workspace_alias(command, grant.workspace)
    result_path = _JOBS_DIR / f"{job_id}.result.json"
    payload = {
        "store_path": str(containment._store_path().resolve()),
        "grant": {**grant.to_dict(), "owner": grant.owner},
        "spec": {
            "workspace": spec.workspace, "env": dict(spec.env), "wall_clock_s": spec.wall_clock_s,
            "required": sorted(spec.required), "network": spec.network,
            "readonly_extra": list(spec.readonly_extra), "writable_extra": list(spec.writable_extra),
            "max_output_bytes": spec.max_output_bytes,
        },
        "command": bounded_command, "log_path": str(log_path.resolve()),
        "result_path": str(result_path.resolve()), "exit_path": str(exit_path.resolve()),
    }
    try:
        with open(log_path, "ab") as bootstrap_log:
            proc = subprocess.Popen(
                [sys.executable, str(Path(containment.__file__).with_name("containment_worker.py"))],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=bootstrap_log,
                cwd=str(Path(containment.__file__).resolve().parent.parent),
                **detached_popen_kwargs(),
            )
    except BaseException:
        containment.release(grant, grace_s=0)
        raise

    rec = {
        "id": job_id,
        "session_id": session_id,
        "command": command,
        "status": "running",       # running | done | failed
        "pid": proc.pid,
        "started_at": time.time(),
        "ended_at": None,
        "exit_code": None,
        "max_runtime_s": max_runtime_s,
        "followed_up": False,       # has the agent been re-invoked with the result?
        "log_path": str(log_path),
        "exit_path": str(exit_path),
        "result_path": str(result_path),
        "containment_id": grant.id,
        "containment": {**grant.to_dict(), "contained": False, "enforced": [], "pending": True, "executed": False},
        "pgid": None if os.name == "nt" else proc.pid,
        # Identity, not just a slot. The pid above is reused by the kernel, and
        # this record outlives the process and the server; the token is what a
        # later run compares before it signals anything. See
        # src/process_ownership.py.
        "start_token": process_ownership.capture(proc.pid)["start_token"],
    }
    try:
        supervisor = ProcessResource("native:bg_jobs", launch_resource.owner, launch_resource.request_id,
            launch_resource.thread_id, ProcessIdentity(proc.pid, rec["start_token"], rec["pgid"]),
            "supervisor", job_id, grant.id)
        supervisor.validate()
        resource = BackgroundJobResource("native:bg_jobs", job_id, launch_resource.generation,
            launch_resource.owner, launch_resource.request_id, launch_resource.thread_id, grant.id, (supervisor,))
        rec["resource_identity"] = resource.to_dict()
        rec["launch_resource"] = launch_resource.to_dict()
        containment._update_record(grant.id, lifetime="background", supervisor_pid=proc.pid,
                                   supervisor_token=rec["start_token"], launch_generation=resource.generation)
        jobs = _load()
        jobs[job_id] = rec
        _save(jobs)
        publish_launch(launch_resource, authority, grant.id, job=resource, processes=(supervisor,))
        save_background_authority(job_id, authority, resource=resource)
        payload.update(job_store=str(_STORE.resolve()), job_id=job_id,
                       launch_path=str(launch_path(resource.generation)),
                       authority_path=str(_JOBS_DIR / (job_id + ".authority.json")),
                       resource_identity=resource.to_dict(), launch_resource=launch_resource.to_dict())
        # The supervisor cannot execute until the identity and job record are durable.
        proc.stdin.write(json.dumps(payload).encode("utf-8"))
        proc.stdin.close()
    except BaseException:
        # EOF closes the unreleased worker even if identity observation failed.
        if proc.stdin is not None and not proc.stdin.closed:
            proc.stdin.close()
        kill_process_tree(proc.pid, start_token=rec["start_token"], pgid=rec["pgid"], require_identity=True)
        proc.wait(timeout=5)
        containment.release(grant, grace_s=0)
        raise
    _LIVE_PROCS[proc.pid] = proc
    return rec


def _read_output(rec: Dict[str, Any]) -> str:
    try:
        txt = Path(rec["log_path"]).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    if len(txt) > _MAX_OUTPUT_CHARS:
        # Keep head + tail — the interesting bits are usually at both ends.
        head = txt[: _MAX_OUTPUT_CHARS // 2]
        tail = txt[-_MAX_OUTPUT_CHARS // 2:]
        txt = head + "\n…[truncated]…\n" + tail
    return txt


def _prune(jobs: Dict[str, Dict[str, Any]], now: float) -> bool:
    """Drop records (and their on-disk files) for jobs that finished, were
    followed up, and are older than the retention window. Mutates `jobs`."""
    stale = [jid for jid, rec in jobs.items()
             if rec.get("status") in {"done", "failed"}
             and (rec.get("followed_up") or rec.get("followup_state") == "terminal_unfollowable")
             and rec.get("ended_at")
             and (rec.get("teardown") or {}).get("dead") is not False
             and (now - rec["ended_at"]) > _RETENTION_S]
    for jid in stale:
        rec = jobs.pop(jid)
        from src.agent_runtime.process_resources import job_from_record, retire_launch
        from src.agent_runtime.resources import ProcessLaunchResource
        try:
            resource = job_from_record(rec)
            retire_launch(ProcessLaunchResource.from_dict(rec["launch_resource"]),
                          resource.containment_id, job=resource)
        except (ValueError, TypeError, OSError):
            # Malformed/replaced publications never become deletion authority.
            pass
        for p in _JOBS_DIR.glob(f"{jid}.*"):   # .sh .cmd.sh .log .exit
            try:
                p.unlink()
            except Exception:
                pass
    return bool(stale)


@store_transaction(lambda: _STORE)
def refresh(job_id=None) -> Dict[str, Dict[str, Any]]:
    """Reconcile every running job against disk. Marks done/failed (incl.
    timeout). Idempotent — safe to call from a poll loop. Returns the store."""
    jobs = _load()
    for pid, proc in list(_LIVE_PROCS.items()):
        if job_id is not None:
            selected = jobs.get(job_id, {})
            # Historical numeric PIDs can name a replacement child's cached
            # handle. Targeted reads may poll only the frozen live incarnation;
            # independent service maintenance may still reap completed handles.
            if (selected.get("status") != "running"
                    or pid != selected.get("pid")
                    or process_ownership.verify(pid, selected.get("start_token"))
                    != process_ownership.OWNED):
                continue
        if proc.poll() is not None:
            _LIVE_PROCS.pop(pid, None)
    changed = False
    now = time.time()
    for jid, rec in jobs.items():
        if job_id is not None and jid != job_id:
            continue
        if rec.get("status") != "running":
            continue
        exit_path = Path(rec.get("exit_path", ""))
        if exit_path.exists():
            try:
                code = int(exit_path.read_text(encoding="utf-8", errors="replace").strip() or "1")
            except Exception:
                code = 1
            rec["exit_code"] = code
            rec["status"] = "done" if code == 0 else "failed"
            rec["ended_at"] = now
            if rec.get("result_path"):
                try:
                    report = json.loads(Path(rec["result_path"]).read_text(encoding="utf-8"))
                    # Result publication is not an identity producer. It cannot
                    # overwrite ownership, generations, PIDs, paths or authority.
                    if rec.get("resource_identity") and report.get("resource_identity") != rec["resource_identity"]:
                        raise ValueError("Result/job linkage mismatch")
                    if report.get("containment", {}).get("id") != rec.get("containment_id"):
                        raise ValueError("Result/receipt linkage mismatch")
                    for key in ("containment", "teardown", "output_truncated", "timed_out", "error", "failure_kind"):
                        if key in report:
                            rec[key] = report[key]
                except (OSError, ValueError):
                    rec["status"], rec["exit_code"] = "failed", 1
                    rec["result_unavailable"] = True
            changed = True
        elif (now - rec.get("started_at", now)) > rec.get("max_runtime_s", DEFAULT_MAX_RUNTIME_S):
            # Runaway / stuck — reap it but STILL surface a follow-up.
            outcome = _kill_record(rec)
            rec["teardown"] = outcome.to_dict()
            if outcome.dead:
                rec["status"] = "failed"
                rec["exit_code"] = -1
                rec["ended_at"] = now
            else:
                rec["kill_failed"] = True
            rec["timed_out"] = True
            changed = True
        elif not _pid_alive(rec.get("pid")) and not exit_path.exists():
            # Process vanished without writing an exit code (killed, OOM,
            # crash). Don't leave it "running" forever.
            rec["status"] = "failed"
            rec["exit_code"] = -1
            rec["ended_at"] = now
            rec["died"] = True
            changed = True
    if job_id is None and _prune(jobs, now):
        changed = True
    if changed:
        _save(jobs)
    return jobs


def _kill(pid: Optional[int], **kwargs):
    # Cross-platform process-tree teardown (POSIX killpg / Windows taskkill /T).
    return kill_process_tree(pid, **kwargs)


def _kill_record(rec):
    from src import containment
    verdict = process_ownership.verify(rec.get("pid"), rec.get("start_token"))
    if verdict in (process_ownership.FOREIGN, process_ownership.UNVERIFIABLE):
        return containment.ReleaseOutcome(dead=False, escalated=False, ownership=verdict)
    if rec.get("containment_id"):
        record = containment._load_records().get(rec["containment_id"])
        if record and record.get("pid"):
            outcome = containment.reap_record(record)
            if not outcome.dead:
                return outcome
    outcome = _kill(rec.get("pid"), start_token=rec.get("start_token"),
                    pgid=rec.get("pgid"), require_identity=True)
    proc = _LIVE_PROCS.get(rec.get("pid"))
    if proc and outcome.dead:
        proc.wait(timeout=5)
        _LIVE_PROCS.pop(proc.pid, None)
    if outcome.dead and rec.get("containment_id"):
        record = containment._load_records().get(rec["containment_id"])
        if record and not record.get("pid"):
            containment.reap_record(record)
    return outcome


def pending_followups() -> List[Dict[str, Any]]:
    """Finished jobs the agent hasn't been re-invoked for yet. The monitor
    drains these; valid continuations acknowledge success, invalid immutable
    linkage receives a terminal disposition without fabricating delivery."""
    jobs = refresh()
    return [r for r in jobs.values()
            if r.get("status") in ("done", "failed") and not r.get("followed_up")
            and r.get("followup_state") != "terminal_unfollowable"]


@store_transaction(lambda: _STORE)
def mark_unfollowable(job_id: str, *, expected_record) -> bool:
    """Suppress only the exact completed snapshot inspected by the monitor.

    This conveys no read/signal/continuation authority and cannot renew a PID.
    It deliberately needs no invalid/missing authority sidecar to suppress it.
    """
    jobs = _load()
    record = jobs.get(job_id)
    if (record is None or record != expected_record or record.get("id") != job_id
            or record.get("status") not in {"done", "failed"}):
        return False
    record["followup_state"] = "terminal_unfollowable"
    _save(jobs)
    return True


@store_transaction(lambda: _STORE)
def mark_followed_up(job_id: str, *, expected) -> None:
    jobs = _load()
    if job_id in jobs:
        from src.agent_runtime.process_resources import validate_job
        if expected.job_id != job_id:
            raise ValueError("Acknowledgement job resource changed")
        validate_job(expected, mutation=True)
        jobs[job_id]["followed_up"] = True
        _save(jobs)


def peek(job_id: str) -> Optional[Dict[str, Any]]:
    """Resolve one record without reaping or changing any job."""
    return _load().get(job_id)


def get(job_id: str, *, expected) -> Optional[Dict[str, Any]]:
    from src.agent_runtime.process_resources import validate_job
    if expected.job_id != job_id:
        raise ValueError("Output job selector changed")
    validate_job(expected)
    refresh(job_id)
    validate_job(expected)
    rec = _load().get(job_id)
    if rec:
        from src.agent_runtime.process_resources import job_from_record
        if job_from_record(rec) != expected:
            raise ValueError("Output job resource changed")
        rec = dict(rec)
        rec["output"] = _read_output(rec)
    return rec


@store_transaction(lambda: _STORE)
def kill(job_id: str, *, expected) -> Optional[Dict[str, Any]]:
    """Terminate a running job's process tree and mark it killed. Returns the
    updated record, or None if the id is unknown. Idempotent: a job that already
    finished is returned unchanged. Sets followed_up so the monitor does not also
    fire an auto-continue for a job the agent deliberately stopped."""
    jobs = _load()
    rec = jobs.get(job_id)
    if rec is None:
        return None
    from src.agent_runtime.process_resources import validate_job
    if expected.job_id != job_id:
        raise ValueError("Job selector changed")
    validate_job(expected, mutation=True)
    if rec.get("status") == "running":
        outcome = _kill_record(rec)
        rec["teardown"] = outcome.to_dict()
        if outcome.dead:
            rec["status"] = "failed"
            rec["exit_code"] = -1
            rec["ended_at"] = time.time()
            rec["killed"] = True
            rec["followed_up"] = True
        else:
            rec["kill_failed"] = True
        _save(jobs)
    return rec


@store_transaction(lambda: _STORE)
def disown_unverified() -> Dict[str, Any]:
    """Stop tracking running jobs whose process can no longer be proven ours.

    Called once at startup by :mod:`src.process_reaper`, never from the poll
    loop — every record it sees was written by an earlier run, which is what
    makes "unidentifiable" a statement about a previous run's child rather than
    about a job this run just launched.

    Signals nothing. A detached job is meant to survive a restart, so a job that
    verifies as ours is left alone and its result is still collected. What is
    corrected is the record that would otherwise be signalled later on a pid the
    kernel has reassigned: the max-runtime branch of :func:`refresh` sends
    SIGTERM then SIGKILL to ``rec["pid"]`` an hour in, and on a reused pid that
    lands on a bystander.

    Fail closed: a job that cannot be verified is retired too, not kept.
    Retiring loses a result, which is visible; keeping it leaves a pid this
    server will eventually signal without knowing what it is pointing at, which
    is not.
    """
    jobs = _load()
    report = {"seen": 0, "retired": 0, "kept": 0}
    changed = False
    now = time.time()
    for rec in jobs.values():
        if rec.get("status") != "running":
            continue
        report["seen"] += 1
        verdict = process_ownership.verify(rec.get("pid"), rec.get("start_token"))
        if verdict in (process_ownership.OWNED, process_ownership.GONE):
            # OWNED: still ours, still running, still watched. GONE: refresh()
            # already turns an absent process into a "died" record, and it may
            # yet find an exit-code file the job wrote before it went.
            report["kept"] += 1
            continue
        rec["status"] = "failed"
        rec["exit_code"] = -1
        rec["ended_at"] = now
        rec["ownership_lost"] = verdict
        # followed_up stays False: the agent asked for this job and is owed an
        # answer, even when the answer is that we lost track of it.
        report["retired"] += 1
        changed = True
    if changed:
        _save(jobs)
    return report


def result_text(rec: Dict[str, Any]) -> str:
    """Human/agent-readable summary of a finished job, for the follow-up."""
    out = _read_output(rec)
    if rec.get("ownership_lost"):
        head = (
            "Background job was abandoned across a server restart: its process "
            f"could not be identified as ours ({rec.get('ownership_lost')}), so it was "
            "neither waited on nor signalled. Any output below is what it had "
            "written by then; if the work matters, re-run it."
        )
    elif rec.get("killed"):
        head = "Background job was killed."
    elif rec.get("timed_out"):
        head = f"Background job timed out after {rec.get('max_runtime_s')}s."
    elif rec.get("died"):
        head = "Background job process died unexpectedly (no exit code)."
    else:
        head = f"Background job finished with exit code {rec.get('exit_code')}."
    return f"{head}\nCommand: {rec.get('command')}\n\nOutput:\n{out or '(no output)'}"
