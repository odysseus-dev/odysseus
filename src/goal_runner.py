"""Detached backend runner for active agent-goal continuations."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Dict, Optional, Tuple

from src import agent_runs
from src.agent_goals import can_continue_goal, mark_continuation_started

logger = logging.getLogger(__name__)

GOAL_CONTINUATION_PROMPT = (
    "Continue working on the active session goal from the current conversation "
    "state. Do not repeat completed work. If the goal is fully achieved, call "
    "update_goal with status=complete. If the same blocker has repeated for at "
    "least three goal turns and you cannot make meaningful progress without "
    "user input or an external-state change, call update_goal with "
    "status=blocked."
)
GOAL_CONTINUATION_MAX_ROUNDS = 12
_PENDING: set[tuple[str, Optional[str]]] = set()


def _session_manager():
    from src.ai_interaction import get_session_manager

    return get_session_manager()


def _session_visible(sess, owner: Optional[str]) -> bool:
    if owner is None:
        return True
    return getattr(sess, "owner", None) == owner


async def _goal_stream(session_id: str, owner: Optional[str]):
    """Drain one goal-continuation agent turn and persist its assistant reply."""
    sm = _session_manager()
    if sm is None:
        yield f'event: error\ndata: {json.dumps({"error": "Session manager unavailable", "status": 503})}\n\n'
        yield "data: [DONE]\n\n"
        return

    try:
        sess = sm.get_session(session_id)
    except Exception:
        yield f'event: error\ndata: {json.dumps({"error": "Session not found", "status": 404})}\n\n'
        yield "data: [DONE]\n\n"
        return
    if not _session_visible(sess, owner):
        yield f'event: error\ndata: {json.dumps({"error": "Session not found", "status": 404})}\n\n'
        yield "data: [DONE]\n\n"
        return

    if not (getattr(sess, "endpoint_url", "") and getattr(sess, "model", "")):
        yield f'event: error\ndata: {json.dumps({"error": "No model selected for this chat", "status": 400})}\n\n'
        yield "data: [DONE]\n\n"
        return

    from routes.chat_helpers import resolve_session_auth, save_assistant_response

    resolve_session_auth(sess, session_id, owner=owner)
    messages = list(sess.get_context_messages())
    messages.append({"role": "user", "content": GOAL_CONTINUATION_PROMPT})

    from src.agent_loop import stream_agent_loop

    full_response = ""
    last_metrics: Dict = {}
    tool_events = []
    round_num = 1
    async for chunk in stream_agent_loop(
        sess.endpoint_url,
        sess.model,
        messages,
        headers=getattr(sess, "headers", None),
        context_length=getattr(sess, "context_length", 0) or 0,
        session_id=session_id,
        max_rounds=GOAL_CONTINUATION_MAX_ROUNDS,
        owner=owner,
    ):
        if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
            try:
                data = json.loads(chunk[6:])
            except (ValueError, TypeError):
                yield chunk
                continue
            if isinstance(data, dict):
                if "delta" in data and not data.get("thinking") and isinstance(data.get("delta"), str):
                    full_response += data["delta"]
                elif data.get("type") == "metrics":
                    last_metrics = data.get("data") or {}
                elif data.get("type") == "agent_step":
                    round_num = data.get("round", round_num)
                elif data.get("type") == "tool_output":
                    tool_events.append({
                        "round": round_num,
                        "tool": data.get("tool"),
                        "command": data.get("command"),
                        "output": data.get("output"),
                        "exit_code": data.get("exit_code"),
                    })
            yield chunk
            continue

        if chunk == "data: [DONE]\n\n":
            if full_response:
                persisted_tool_events = last_metrics.get("tool_events") or tool_events
                saved_id = save_assistant_response(
                    sess,
                    sm,
                    session_id,
                    full_response,
                    last_metrics,
                    tool_events=persisted_tool_events,
                )
                if saved_id:
                    yield f'data: {json.dumps({"type": "message_saved", "id": saved_id})}\n\n'
            yield chunk
            return

        yield chunk


def start_goal_continuation(session_id: str, owner: Optional[str] = None) -> Tuple[bool, str, Optional[Dict]]:
    """Start one detached continuation run if the session goal is eligible."""
    if agent_runs.is_active(session_id):
        _, _, goal = can_continue_goal(session_id, owner=owner)
        return False, "run_active", goal

    ok, reason, goal = can_continue_goal(session_id, owner=owner)
    if not ok:
        return False, reason, goal

    sm = _session_manager()
    if sm is None:
        return False, "session_manager_unavailable", goal
    try:
        sess = sm.get_session(session_id)
    except Exception:
        return False, "session_not_found", goal
    if not _session_visible(sess, owner):
        return False, "session_not_found", goal
    if not (getattr(sess, "endpoint_url", "") and getattr(sess, "model", "")):
        return False, "model_not_configured", goal

    started_goal = mark_continuation_started(session_id, owner=owner) or goal
    agent_runs.start(session_id, _goal_stream(session_id, owner))
    return True, "started", started_goal


async def _schedule_after_idle(session_id: str, owner: Optional[str]) -> None:
    key = (session_id, owner)
    try:
        for _ in range(20):
            await asyncio.sleep(0.5)
            if agent_runs.is_active(session_id):
                continue
            started, reason, _ = start_goal_continuation(session_id, owner=owner)
            if started or reason != "run_active":
                return
    finally:
        _PENDING.discard(key)


def schedule_goal_continuation(session_id: str, owner: Optional[str] = None) -> bool:
    """Queue a continuation once the current detached run becomes idle."""
    key = (session_id, owner)
    if key in _PENDING:
        return False
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    ok, _, _ = can_continue_goal(session_id, owner=owner)
    if not ok:
        return False
    _PENDING.add(key)
    asyncio.create_task(_schedule_after_idle(session_id, owner))
    return True
