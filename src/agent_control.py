"""Control surface for running agents: steer, stop, and launch workers.

Backs the Agents dashboard (routes/agents_routes.py). Everything here is
owner-scoped by the routes; this module only knows run ids and session ids.

* **Steer** — queue a message for a running turn. The agent loop drains the
  queue at the start of each round and appends it as a user message, so the
  correction lands mid-task instead of after the turn ends.
* **Stop** — end one unit of work (a sub-agent, Claude Code task, background
  job, or a chat turn) without stopping anything else.
* **Launch** — start a named worker profile in a fresh chat as a detached
  background run, so the user can spin agents up from the dashboard rather
  than only through another agent's tool call.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from src import agent_activity as activity

logger = logging.getLogger(__name__)

# ── steering ──────────────────────────────────────────────────────────────

_STEER: Dict[str, List[dict]] = {}
_STEER_MAX = 10


def steer(session_id: str, text: str, *, owner: Optional[str] = None) -> dict:
    text = " ".join(str(text or "").split())[:4000]
    if not text:
        raise ValueError("steer text is empty")
    rec = {"text": text, "ts": time.time(), "owner": owner}
    queue = _STEER.setdefault(str(session_id), [])
    if len(queue) >= _STEER_MAX:
        raise ValueError("too many queued steer messages")
    queue.append(rec)
    activity.publish(session_id, "note", f"Steer queued: {text[:160]}", source="odysseus",
                     run_id=activity.active_turn(session_id), owner=owner, detail=text, level="warning")
    return rec


def drain_steer(session_id: Optional[str]) -> List[str]:
    """Messages queued since the last round, oldest first (and cleared)."""
    if not session_id:
        return []
    queue = _STEER.pop(str(session_id), None)
    return [rec["text"] for rec in queue] if queue else []


def pending_steer(session_id: str) -> List[dict]:
    return list(_STEER.get(str(session_id), ()))


# ── stop ──────────────────────────────────────────────────────────────────

async def stop_run(run_id: str) -> dict:
    """Stop one run. Returns ``{"stopped": bool, "how": ..., ...}``; raises
    ``LookupError`` when unknown and ``ValueError`` when not stoppable."""
    rec = activity.get_run(run_id)
    if rec is None:
        raise LookupError("Run not found")
    if rec.get("status") != "running":
        return {"stopped": False, "status": rec.get("status"), "reason": "not running"}
    summary = rec.get("summary") or {}
    source = rec.get("source")
    from src.headless_agent import request_stop

    if request_stop(run_id):
        return {"stopped": True, "how": "headless"}
    if source == "claude_code":
        from src.agent_tools.claude_code_tools import get_task_runner

        record = await get_task_runner().cancel(summary.get("task_id") or run_id)
        if record is None:
            raise ValueError("This Claude Code run is part of a chat turn; stop the chat to stop it")
        return {"stopped": True, "how": "claude_code", "status": record.get("status")}
    if source == "bg_job" and summary.get("job_id"):
        from src import bg_jobs

        job = bg_jobs.kill(str(summary["job_id"]))
        if job is None:
            raise LookupError("Background job not found")
        return {"stopped": True, "how": "bg_job", "status": job.get("status")}
    if source == "odysseus" and rec.get("session_id"):
        from src import agent_runs

        return {"stopped": agent_runs.stop(rec["session_id"]), "how": "chat"}
    raise ValueError(f"{source or 'This'} runs can't be stopped individually")


# ── launch a worker ───────────────────────────────────────────────────────

_WORKERS: Dict[str, asyncio.Task] = {}


async def launch_worker(*, owner: Optional[str], task: str, profile_name: Optional[str] = None,
                        parent_session: Optional[str] = None, model: Optional[str] = None) -> dict:
    """Start a worker in a fresh chat and return at once with its ids.

    The worker runs detached (like a chat turn survives a closed tab); its
    progress is on its own chat's activity feed and on the parent's when one
    is given, so the dashboard and the parent chat both see it.
    """
    from src import agent_profiles, agent_runs
    from src.agent_tools.session_tools import _new_child_session
    from src.ai_interaction import get_session_manager
    from src.headless_agent import run_headless

    task = str(task or "").strip()
    if not task:
        raise ValueError("task is empty")
    manager = get_session_manager()
    if manager is None:
        raise RuntimeError("session manager unavailable")
    profile = None
    if profile_name:
        profile = agent_profiles.get_profile(profile_name)
        if profile is None:
            raise ValueError(f"no agent profile named {profile_name!r}")
    if model:
        profile = dict(profile or {"name": "worker", "instructions": "", "disabled_tools": [],
                                   "max_rounds": agent_profiles.DEFAULT_ROUNDS})
        profile["model"] = model
    sess, err = _new_child_session(manager, parent_session, owner, task, profile)
    if err:
        raise ValueError(err)
    try:
        manager.save_sessions()
    except Exception:
        pass
    context: List[Dict[str, Any]] = []
    if profile and profile.get("instructions"):
        context.append({"role": "system", "content": profile["instructions"]})
    context.append({"role": "user", "content": task})
    label = f"{profile['name']} · " if profile and profile.get("name") not in (None, "worker") else ""
    run_id = activity.run_started(
        sess.id, "session", f"Worker · {label}{task[:80]}", owner=owner,
        data={"target_session": sess.id, "target_session_name": sess.name, "model": sess.model,
              "mode": "agent", "launched_from": "dashboard",
              **({"profile": profile["name"]} if profile and profile.get("name") else {})},
        detail=task[:1500],
    )
    if parent_session:
        activity.publish(parent_session, "message", f"→ worker {sess.name}: {task[:160]}", source="session",
                         run_id=run_id, owner=owner, detail=task[:2000])

    async def _run():
        from core.models import ChatMessage

        outcome: Dict[str, Any] = {}
        status, text, events = "completed", "", []
        try:
            with agent_runs.track_external(sess.id, source="worker", owner=owner):
                text, events = await run_headless(
                    sess, context,
                    max_rounds=profile["max_rounds"] if profile else agent_profiles.DEFAULT_ROUNDS,
                    disabled_tools=set(profile.get("disabled_tools") or []) if profile else frozenset(),
                    activity_session_id=sess.id, run_id=run_id, source="session", owner=owner, outcome=outcome,
                )
            if outcome.get("stopped"):
                status = "cancelled"
        except asyncio.CancelledError:
            status = "cancelled"
        except Exception as exc:
            status, text = "failed", f"Worker failed: {exc}"
            logger.warning("worker %s failed: %s", sess.id, exc, exc_info=True)
        try:
            sess.add_message(ChatMessage("user", task, {"source": "dashboard", "direction": "inbound"}))
            meta: Dict[str, Any] = {"source": "worker", "model": sess.model}
            if events:
                meta["tool_events"] = events
            sess.add_message(ChatMessage("assistant", text or "(no reply)", meta))
            manager.save_sessions()
        except Exception:
            logger.debug("worker persist failed", exc_info=True)
        activity.run_finished(sess.id, "session", run_id,
                              f"Worker · {label}{sess.name} {status}", status=status, owner=owner,
                              data={"target_session": sess.id, "steps": len(events), "result_excerpt": text[:400]})
        if parent_session:
            activity.publish(parent_session, "message", f"← worker {sess.name}: {text[:160]}", source="session",
                             run_id=run_id, owner=owner, detail=text[:2000],
                             level="error" if status == "failed" else "info")
            try:
                await _hand_off(manager, parent_session, sess, task, text, status, owner)
            except Exception:
                logger.warning("worker hand-off to %s failed", parent_session, exc_info=True)
        _WORKERS.pop(run_id, None)

    _WORKERS[run_id] = asyncio.create_task(_run())
    return {"session_id": sess.id, "session_name": sess.name, "run_id": run_id, "model": sess.model}


_HANDOFF_MAX_ROUNDS = 12


async def _hand_off(manager, parent_id: str, worker, task: str, text: str, status: str,
                    owner: Optional[str]) -> None:
    """Deliver a finished worker's result to the chat it reports to.

    The result is saved into the parent chat as a message from the worker,
    so the parent's next turn has it. If the parent is idle, its agent
    continues right away (like a background job's follow-up) so a plan that
    delegated a piece of work picks the piece up without the user relaying it.
    """
    from core.models import ChatMessage
    from src import agent_runs
    from src.headless_agent import run_headless

    parent = manager.get_session(parent_id)
    if parent is None:
        return
    headline = "finished" if status == "completed" else status
    inject = (f"[Worker {worker.name} {headline}]\nTask: {task[:1500]}\n\nResult:\n{text[:12000]}\n\n"
              "Continue the task using this result. Don't repeat work the worker already did. "
              "If the task is now complete, give the user the final result.")
    parent.add_message(ChatMessage("user", inject, {"source": "worker", "from_session": worker.id,
                                                    "from_session_name": worker.name, "direction": "inbound"}))
    manager.save_sessions()
    if agent_runs.is_busy(parent_id):
        # Mid-turn: the message waits in history for the next turn.
        activity.publish(parent_id, "note", f"Worker result saved for the next turn: {worker.name}",
                         source="session", owner=owner)
        return
    run_id = activity.run_started(parent_id, "session", f"Continuing after worker {worker.name}", owner=owner,
                                  data={"target_session": worker.id, "target_session_name": worker.name,
                                        "mode": "agent"})
    reply, events = "", []
    try:
        with agent_runs.track_external(parent_id, source="worker", owner=owner):
            reply, events = await run_headless(parent, parent.get_context_messages(), max_rounds=_HANDOFF_MAX_ROUNDS,
                                               disabled_tools=None, activity_session_id=parent_id, run_id=run_id,
                                               source="session", owner=owner)
        status = "completed"
    except Exception as exc:
        reply, status = f"Could not continue after the worker: {exc}", "failed"
    meta: Dict[str, Any] = {"model": parent.model, "source": "worker_followup", "worker_session": worker.id}
    if events:
        meta["tool_events"] = events
    parent.add_message(ChatMessage("assistant", reply or "(no reply)", meta))
    manager.save_sessions()
    activity.run_finished(parent_id, "session", run_id, f"Continued after worker {worker.name}", status=status,
                          owner=owner, data={"target_session": worker.id, "steps": len(events),
                                             "result_excerpt": reply[:400]})
