"""Agent tool to inspect and control detached background `bash` jobs.

`bash` blocks prefixed with a `#!bg` marker run detached via `src.bg_jobs`; the
agent is auto-re-invoked with the output when they finish. This tool covers the
gaps in that flow: list the jobs in the current chat, read a still-running job's
output on demand, and kill a runaway job instead of waiting out its max-runtime.

Registry tool (`TOOL_HANDLERS["manage_bg_jobs"]`). Jobs are scoped to the chat
that launched them, so every action requires the caller's `session_id` and a job
from another session is treated as not found.
"""

import json
import time
from typing import Any, Dict, List

_LIST_ACTIONS = {"list", "ls", "jobs"}
_OUTPUT_ACTIONS = {"output", "get", "read", "tail", "status", "show"}
_KILL_ACTIONS = {"kill", "stop", "cancel", "terminate"}


def _age(rec: Dict[str, Any]) -> str:
    start = rec.get("started_at")
    if not start:
        return "?"
    secs = int(time.time() - start)
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    return f"{secs // 3600}h{(secs % 3600) // 60}m"


def _status_label(rec: Dict[str, Any]) -> str:
    status = rec.get("status", "?")
    if rec.get("killed"):
        return "killed"
    if rec.get("timed_out"):
        return "timed out"
    if rec.get("died"):
        return "died"
    if status in ("done", "failed"):
        return f"{status} (exit {rec.get('exit_code')})"
    return status


def _row(rec: Dict[str, Any]) -> str:
    cmd = (rec.get("command") or "").strip().splitlines()[0][:80]
    return f"[{rec.get('id')}] {_status_label(rec)} | {_age(rec)} | {cmd}"


class ManageBgJobsTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        from src import bg_jobs

        session_id = ctx.get("session_id")
        raw = (content or "").strip()
        try:
            args = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            args = {}
        if not isinstance(args, dict):
            args = {}
        action = str(args.get("action", "list")).strip().lower()
        job_id = str(args.get("job_id") or args.get("id") or "").strip()

        if not session_id:
            return {"error": "manage_bg_jobs: no active chat session; background jobs are scoped to a chat.", "exit_code": 1}

        if action in _LIST_ACTIONS:
            jobs: List[Dict[str, Any]] = bg_jobs.list_for_session(session_id)
            if not jobs:
                return {"output": "No background jobs in this chat.", "exit_code": 0}
            jobs.sort(key=lambda r: r.get("started_at") or 0, reverse=True)
            lines = "\n".join(_row(r) for r in jobs)
            return {"output": f"{len(jobs)} background job(s):\n{lines}", "exit_code": 0}

        if action in _OUTPUT_ACTIONS or action in _KILL_ACTIONS:
            if not job_id:
                return {"error": f"manage_bg_jobs: action '{action}' requires a job_id (see action='list').", "exit_code": 1}
            rec = bg_jobs.get(job_id)
            # Scope: only the chat that launched a job may see or control it.
            if rec is None or rec.get("session_id") != session_id:
                return {"error": f"manage_bg_jobs: no background job '{job_id}' in this chat.", "exit_code": 1}

            if action in _KILL_ACTIONS:
                if rec.get("status") != "running":
                    return {"output": f"Job `{job_id}` already {_status_label(rec)}; nothing to kill.", "exit_code": 0}
                killed = bg_jobs.kill(job_id)
                return {"output": f"Killed background job `{job_id}` ({(killed or {}).get('command', '').splitlines()[0][:80]}).", "exit_code": 0}

            out = rec.get("output") or "(no output yet)"
            return {
                "output": f"Job `{job_id}` [{_status_label(rec)}, {_age(rec)}]\nCommand: {rec.get('command')}\n\nOutput:\n{out}",
                "exit_code": 0,
            }

        return {"error": f"manage_bg_jobs: unknown action '{action}'. Use list, output, or kill.", "exit_code": 1}
