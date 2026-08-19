"""Sandboxed background job execution for the agent's `bash` tool.

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

This module only owns launch + state. Model commands execute through the
server-selected process boundary: the default workspace Sandbox or explicitly
confirmed Full Access, both retaining the private network policy. A tiny isolated
Python wrapper outside that boundary only records output and the exit code. The
monitor / agent re-invocation lives in the caller (so this stays import-light and
unit-testable).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.atomic_io import atomic_write_json
from core.platform_compat import (
    IS_WINDOWS,
    detached_popen_kwargs,
    kill_process_tree,
    pid_alive,
)

from src.constants import BG_JOBS_DIR, BG_JOBS_FILE
from src.execution_sandbox import (
    SandboxNetworkProfile,
    environment_for_sandbox_launcher,
    full_access_command,
    sandbox_command,
)
from src.process_execution import (
    FULL_ACCESS_WARNING,
    ProcessExecutionMode,
    configured_process_execution_mode,
    process_capability,
)

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

_DETACHED_SANDBOX_WRAPPER = """
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

plan_path = Path(sys.argv[1])
expected_digest = sys.argv[2]
log_path = Path(sys.argv[3])
exit_path = Path(sys.argv[4])
code = 1

def write_private_text(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(text)

try:
    plan_bytes = plan_path.read_bytes()
    actual_digest = hashlib.sha256(plan_bytes).hexdigest()
    if actual_digest != expected_digest:
        raise RuntimeError("detached process plan digest mismatch")
    plan = json.loads(plan_bytes)
    if plan.get("version") != 1 or not isinstance(plan.get("argv"), list):
        raise RuntimeError("invalid detached process plan")
    argv = plan["argv"]
    if not argv or not all(isinstance(part, str) for part in argv):
        raise RuntimeError("invalid detached process argv")
    child_env = {}
    log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(log_fd, "wb") as output:
        completed = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            env=child_env,
            check=False,
        )
        code = int(completed.returncode)
except Exception as exc:
    try:
        write_private_text(log_path, f"process launch failed: {exc}\\n")
    except Exception:
        pass
try:
    write_private_text(exit_path, str(code))
except Exception:
    pass
""".strip()


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


def _make_jobs_dir_private() -> None:
    _JOBS_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        _JOBS_DIR.chmod(0o700)
    except (AttributeError, NotImplementedError):
        pass


def _write_private_file(path: Path, content: str) -> None:
    fd = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def _remove_job_record(job_id: str) -> None:
    try:
        jobs = _load()
        if job_id not in jobs:
            return
        jobs.pop(job_id, None)
        try:
            _save(jobs)
        except BaseException:
            atomic_write_json(str(_STORE), jobs, indent=2)
    except BaseException:
        pass


def _remove_job_artifacts(
    job_id: str,
    created_paths: list[Path],
    preexisting_paths: set[Path] = frozenset(),
) -> None:
    paths = set(created_paths)
    try:
        paths.update(_JOBS_DIR.glob(f"{job_id}.*"))
    except OSError:
        pass
    paths.difference_update(preexisting_paths)
    for path in paths:
        try:
            path.unlink()
        except (FileNotFoundError, IsADirectoryError, OSError):
            pass


def _kill_untracked_process(proc: subprocess.Popen) -> None:
    try:
        _kill(proc.pid)
    except BaseException:
        pass
    try:
        proc.wait(timeout=2)
    except BaseException:
        pass


def launch(
    command: str,
    session_id: str,
    cwd: Optional[str] = None,
    max_runtime_s: int = DEFAULT_MAX_RUNTIME_S,
    network_profile: SandboxNetworkProfile = SandboxNetworkProfile.NETWORKLESS,
) -> Dict[str, Any]:
    """Launch `command` detached. Returns the job record (status='running').

    Output + the final exit code are written to files so status survives a
    server restart. The process is put in its own session (setsid) so it
    outlives the request/stream that started it.
    """
    if IS_WINDOWS:
        raise RuntimeError(
            "Sandboxed agent execution requires Linux with bubblewrap."
        )
    _make_jobs_dir_private()
    job_id = uuid.uuid4().hex[:12]
    log_path = _JOBS_DIR / f"{job_id}.log"
    exit_path = _JOBS_DIR / f"{job_id}.exit"
    cmd_path = _JOBS_DIR / f"{job_id}.cmd.sh"
    plan_path = _JOBS_DIR / f"{job_id}.plan.json"
    try:
        preexisting_paths = set(_JOBS_DIR.glob(f"{job_id}.*"))
    except OSError:
        preexisting_paths = set()
    created_paths: list[Path] = [cmd_path, plan_path, log_path, exit_path]
    proc: subprocess.Popen | None = None
    record_saved = False

    try:
        _write_private_file(cmd_path, command + "\n")
        execution_mode = configured_process_execution_mode()
        capability = process_capability().for_mode(execution_mode)
        if not capability.supports(network_profile):
            raise RuntimeError(
                f"{execution_mode.value} process boundary unavailable: "
                + capability.reason_for(network_profile)
            )
        if execution_mode is ProcessExecutionMode.SANDBOX:
            process_argv = sandbox_command(
                ["/bin/bash", "--noprofile", "--norc", "/run/odysseus/command.sh"],
                workspace=cwd or "",
                readonly_files={str(cmd_path): "/run/odysseus/command.sh"},
                network_profile=network_profile,
            )
        else:
            process_argv = full_access_command(
                ["/bin/bash", "--noprofile", "--norc", str(cmd_path)],
                working_directory=cwd or "",
                network_profile=network_profile,
            )
        wrapper_environment = environment_for_sandbox_launcher()

        plan_bytes = json.dumps(
            {
                "version": 1,
                "argv": process_argv,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        plan_digest = hashlib.sha256(plan_bytes).hexdigest()
        _write_private_file(plan_path, plan_bytes.decode("utf-8"))

        argv = [
            sys.executable,
            "-I",
            "-c",
            _DETACHED_SANDBOX_WRAPPER,
            str(plan_path),
            plan_digest,
            str(log_path),
            str(exit_path),
        ]
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            cwd=None,
            env=wrapper_environment,
            **detached_popen_kwargs(),  # detach from the request lifecycle (setsid)
        )

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
            "network_profile": network_profile.value,
            "execution_mode": execution_mode.value,
            "network_enforcement": (
                "brokered_http_https"
                if network_profile is SandboxNetworkProfile.BROKERED_ONLY
                else "networkless"
            ),
            "warning": (
                FULL_ACCESS_WARNING
                if execution_mode is ProcessExecutionMode.FULL_ACCESS
                else ""
            ),
            "followed_up": False,       # has the agent been re-invoked with the result?
            "log_path": str(log_path),
            "exit_path": str(exit_path),
        }
        jobs = _load()
        jobs[job_id] = rec
        _save(jobs)
        record_saved = True
        return rec
    except BaseException:
        if proc is not None and not record_saved:
            _kill_untracked_process(proc)
        if not record_saved:
            _remove_job_record(job_id)
        _remove_job_artifacts(job_id, created_paths, preexisting_paths)
        raise


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
             if rec.get("followed_up") and rec.get("ended_at")
             and (now - rec["ended_at"]) > _RETENTION_S]
    for jid in stale:
        jobs.pop(jid, None)
        for p in _JOBS_DIR.glob(f"{jid}.*"):   # .sh .cmd.sh .log .exit
            try:
                p.unlink()
            except Exception:
                pass
    return bool(stale)


def refresh() -> Dict[str, Dict[str, Any]]:
    """Reconcile every running job against disk. Marks done/failed (incl.
    timeout). Idempotent — safe to call from a poll loop. Returns the store."""
    jobs = _load()
    changed = False
    now = time.time()
    for rec in jobs.values():
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
            changed = True
        elif (now - rec.get("started_at", now)) > rec.get("max_runtime_s", DEFAULT_MAX_RUNTIME_S):
            # Runaway / stuck — reap it but STILL surface a follow-up.
            _kill(rec.get("pid"))
            rec["status"] = "failed"
            rec["exit_code"] = -1
            rec["ended_at"] = now
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
    if _prune(jobs, now):
        changed = True
    if changed:
        _save(jobs)
    return jobs


def _kill(pid: Optional[int]) -> None:
    # Cross-platform process-tree teardown (POSIX killpg / Windows taskkill /T).
    kill_process_tree(pid)


def pending_followups() -> List[Dict[str, Any]]:
    """Finished jobs the agent hasn't been re-invoked for yet. The monitor
    drains these; mark_followed_up() flips the flag only on success."""
    jobs = refresh()
    return [r for r in jobs.values()
            if r.get("status") in ("done", "failed") and not r.get("followed_up")]


def mark_followed_up(job_id: str) -> None:
    jobs = _load()
    if job_id in jobs:
        jobs[job_id]["followed_up"] = True
        _save(jobs)


def get(job_id: str) -> Optional[Dict[str, Any]]:
    refresh()  # reconcile against disk so status/exit_code are current
    rec = _load().get(job_id)
    if rec:
        rec = dict(rec)
        rec["output"] = _read_output(rec)
    return rec


def list_for_session(session_id: str) -> List[Dict[str, Any]]:
    return [r for r in refresh().values() if r.get("session_id") == session_id]


def kill(job_id: str) -> Optional[Dict[str, Any]]:
    """Terminate a running job's process tree and mark it killed. Returns the
    updated record, or None if the id is unknown. Idempotent: a job that already
    finished is returned unchanged. Sets followed_up so the monitor does not also
    fire an auto-continue for a job the agent deliberately stopped."""
    jobs = _load()
    rec = jobs.get(job_id)
    if rec is None:
        return None
    if rec.get("status") == "running":
        _kill(rec.get("pid"))
        rec["status"] = "failed"
        rec["exit_code"] = -1
        rec["ended_at"] = time.time()
        rec["killed"] = True
        rec["followed_up"] = True
        _save(jobs)
    return rec


def result_text(rec: Dict[str, Any]) -> str:
    """Human/agent-readable summary of a finished job, for the follow-up."""
    out = _read_output(rec)
    if rec.get("killed"):
        head = "Background job was killed."
    elif rec.get("timed_out"):
        head = f"Background job timed out after {rec.get('max_runtime_s')}s."
    elif rec.get("died"):
        head = "Background job process died unexpectedly (no exit code)."
    else:
        head = f"Background job finished with exit code {rec.get('exit_code')}."
    authority = f"Execution mode: {rec.get('execution_mode', 'sandbox')}"
    if rec.get("warning"):
        authority += f"\nWARNING: {rec['warning']}"
    return (
        f"{head}\n{authority}\nCommand: {rec.get('command')}"
        f"\n\nOutput:\n{out or '(no output)'}"
    )
