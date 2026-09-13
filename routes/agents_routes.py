"""Agents dashboard API — one owner-scoped view of every agent the user runs.

Unlike ``/api/workbench`` (admin-only, repository-centric), everything here is
scoped to the caller's own chats, so any signed-in user can watch, steer,
stop and launch their agents.

* ``GET  /overview``            — every chat with agent activity: status,
  latest step, live children, pending approvals, plus totals.
* ``GET  /stream``              — SSE of the caller's activity events.
* ``GET  /runs/{id}/events``    — one run's events.
* ``POST /runs/{id}/stop``      — stop one run.
* ``POST /sessions/{id}/steer`` — queue a mid-task instruction for a running turn.
* ``GET  /approvals``           — pending tool approvals across the caller's chats.
* ``POST /launch``              — start a worker profile in a fresh chat.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional, Set

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from src import agent_activity as activity
from src import agent_control, agent_runs, tool_approvals
from src.auth_helpers import effective_user

logger = logging.getLogger(__name__)

_RECENT_S = 24 * 3600
_LIVE_SOURCES = {"session", "claude_code", "bg_job", "pipeline", "worktree"}


def setup_agents_routes(session_manager) -> APIRouter:
    router = APIRouter(prefix="/api/agents", tags=["agents"])

    def _owned(request: Request) -> tuple[Optional[str], Dict[str, Any]]:
        user = effective_user(request)
        try:
            return user, session_manager.get_sessions_for_user(user)
        except Exception:
            return user, {}

    def _require_owned(request: Request, session_id: str) -> Optional[str]:
        user, owned = _owned(request)
        if session_id not in owned:
            raise HTTPException(404, "Session not found")
        return user

    @router.get("/overview")
    async def overview(request: Request):
        user, owned = _owned(request)
        now = time.time()
        chat_runs = {r["session_id"]: r for r in agent_runs.list_runs(set(owned))}
        by_session: Dict[str, list] = {}
        for rec in activity.list_runs(limit=400):
            sid = rec.get("session_id")
            if sid not in owned or rec.get("source") == "odysseus":
                continue
            fin = rec.get("finished_at")
            if rec.get("status") != "running" and (not fin or now - fin > _RECENT_S):
                continue
            by_session.setdefault(sid, []).append(rec)
        pending: Dict[str, int] = {}
        for rec in tool_approvals.pending_for(set(owned)):
            pending[rec["session_id"]] = pending.get(rec["session_id"], 0) + 1
        from core.database import get_session_settings

        rows = []
        for sid in set(chat_runs) | set(by_session) | set(pending):
            sess = owned.get(sid)
            if sess is None:
                continue
            run = chat_runs.get(sid) or {}
            children = by_session.get(sid, [])
            live_children = [c for c in children if c.get("status") == "running"]
            if pending.get(sid):
                status = "waiting_approval"
            elif run.get("status") == "running" or live_children:
                status = "running"
            elif run.get("status") in ("done", "error", "stopped"):
                status = {"done": "finished", "error": "failed", "stopped": "stopped"}[run["status"]]
            else:
                status = "idle"
            events = activity.history(sid, limit=12)
            latest = next((e for e in reversed(events)
                           if e.get("kind") in ("tool_start", "tool_result", "message", "status", "run_started")), None)
            settings = get_session_settings(sid)
            rows.append({
                "session_id": sid,
                "name": getattr(sess, "name", "") or sid,
                "model": getattr(sess, "model", ""),
                "status": status,
                "started_at": run.get("started_at"),
                "finished_at": run.get("finished_at"),
                "source": run.get("source"),
                "latest": (latest or {}).get("title"),
                "latest_ts": (latest or {}).get("ts"),
                "children": [{"run_id": c["run_id"], "title": c.get("title"), "source": c.get("source"),
                              "status": c.get("status"), "started_at": c.get("started_at"),
                              "finished_at": c.get("finished_at"), "summary": c.get("summary") or {}}
                             for c in sorted(children, key=lambda c: c.get("started_at") or 0, reverse=True)[:12]],
                "children_running": len(live_children),
                "pending_approvals": pending.get(sid, 0),
                "steer_queued": len(agent_control.pending_steer(sid)),
                "profile": settings.get("agent_profile"),
                "parent_session": settings.get("parent_session"),
                "approval_mode": settings.get("approval_mode"),
            })
        order = {"waiting_approval": 0, "running": 1, "failed": 2, "finished": 3, "stopped": 4, "idle": 5}
        rows.sort(key=lambda r: (order.get(r["status"], 9), -(r.get("latest_ts") or r.get("started_at") or 0)))
        totals = {
            "running": sum(1 for r in rows if r["status"] == "running"),
            "waiting_approval": sum(1 for r in rows if r["status"] == "waiting_approval"),
            "workers_running": sum(r["children_running"] for r in rows),
            "finished_24h": sum(1 for r in rows if r["status"] in ("finished",)),
            "failed_24h": sum(1 for r in rows if r["status"] == "failed"),
        }
        from src import agent_profiles

        return {"now": now, "rows": rows, "totals": totals, "profiles": agent_profiles.load_profiles(),
                "chats": [{"id": s.id, "name": getattr(s, "name", "") or s.id} for s in owned.values()
                          if not getattr(s, "archived", False)][:200]}

    @router.get("/stream")
    async def stream(request: Request, since: int = Query(default=0)):
        user, owned = _owned(request)
        owned_ids: Set[str] = set(owned)
        state = {"checked": time.time()}

        async def gen():
            async for frame in activity.subscribe(activity.GLOBAL_FEED, since_seq=max(0, since)):
                if not frame.startswith("data: "):
                    yield frame
                    continue
                try:
                    ev = json.loads(frame[6:])
                except ValueError:
                    continue
                sid = ev.get("session_id") if isinstance(ev, dict) else None
                if sid and sid not in owned_ids and time.time() - state["checked"] > 5:
                    # A worker launched during the stream is a new chat of ours.
                    state["checked"] = time.time()
                    try:
                        owned_ids.update(session_manager.get_sessions_for_user(user))
                    except Exception:
                        pass
                if not sid or sid in owned_ids or ev.get("type") == "ready":
                    yield frame

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @router.get("/runs/{run_id}/events")
    async def run_events(request: Request, run_id: str, limit: int = 300):
        rec = activity.get_run(run_id)
        if rec is None:
            raise HTTPException(404, "Run not found")
        _require_owned(request, rec.get("session_id") or "")
        rec["events"] = activity.run_events(run_id, limit=limit)
        return rec

    @router.post("/runs/{run_id}/stop")
    async def stop(request: Request, run_id: str):
        rec = activity.get_run(run_id)
        if rec is None:
            raise HTTPException(404, "Run not found")
        _require_owned(request, rec.get("session_id") or "")
        try:
            return await agent_control.stop_run(run_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @router.post("/sessions/{session_id}/steer")
    async def steer(request: Request, session_id: str):
        user = _require_owned(request, session_id)
        body = await request.json()
        if not agent_runs.is_busy(session_id):
            raise HTTPException(409, "That chat is not running; send it a normal message instead")
        try:
            rec = agent_control.steer(session_id, str((body or {}).get("text") or ""), owner=user)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"queued": True, "text": rec["text"], "pending": len(agent_control.pending_steer(session_id))}

    @router.get("/approvals")
    async def approvals(request: Request):
        user, owned = _owned(request)
        rows = []
        for rec in tool_approvals.pending_for(set(owned)):
            sess = owned.get(rec["session_id"])
            rows.append({**rec, "session_name": getattr(sess, "name", "") if sess else ""})
        return {"approvals": rows}

    @router.post("/launch")
    async def launch(request: Request):
        user, owned = _owned(request)
        body = await request.json()
        parent = str((body or {}).get("parent_session") or "").strip() or None
        if parent and parent not in owned:
            raise HTTPException(404, "Parent chat not found")
        try:
            return await agent_control.launch_worker(
                owner=user, task=str((body or {}).get("task") or ""),
                profile_name=str((body or {}).get("profile") or "").strip() or None,
                parent_session=parent, model=str((body or {}).get("model") or "").strip() or None,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    return router
