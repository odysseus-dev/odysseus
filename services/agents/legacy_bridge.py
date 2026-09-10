"""Production entry that dispatches governed OpenHands executions.

Legacy ``stream_agent_loop`` remains for tests until Task 18 deletion.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, AsyncIterator

from .dispatcher import AgentDispatcher, DispatchRequest

_IDLE = {"finished", "paused", "completed", "failed", "cancelled"}
_POLL_SLEEP_S = 0.4
_POLL_TIMEOUT_S = 120.0
_REBOUND_NOTE = "Started a new OpenHands conversation because the agent type changed."


def _event_idle(event: dict[str, Any]) -> bool:
    status = str(event.get("status") or "").lower()
    return event.get("kind") == "ConversationStateUpdate" and status in _IDLE


def _client_conversation_idle(client: Any, conversation_id: str, execution_id: str) -> bool:
    getter = getattr(client, "conversation_status", None)
    if callable(getter):
        return str(getter(conversation_id) or "").lower() in _IDLE
    fetch = getattr(client, "get_execution", None)
    if callable(fetch):
        info = fetch(execution_id) or {}
        status = str(info.get("status") or info.get("conversation_status") or "").lower()
        return status in _IDLE
    return False


def _user_text(messages: list[dict[str, Any]] | None) -> str:
    for message in reversed(messages or []):
        if message.get("role") == "user":
            content = message.get("content") or ""
            return content if isinstance(content, str) else json.dumps(content)
    return ""


def _assistant_text(event: dict[str, Any]) -> str:
    if event.get("kind") != "MessageEvent" or event.get("source") == "user":
        return ""
    parts = event.get("content") or event.get("text") or ""
    if isinstance(parts, str):
        return parts
    return "".join(
        block.get("text") or ""
        for block in parts
        if isinstance(block, dict)
    )


async def stream_governed_agent(
    endpoint_url: str | None = None,
    model: str | None = None,
    messages: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> AsyncIterator[str]:
    dispatcher = kwargs.pop("dispatcher", None) or AgentDispatcher()
    session_id = kwargs.get("session_id")
    requested = kwargs.get("conversation_id")
    conversation_id = requested if requested and requested != session_id else None
    profile = str(kwargs.get("agent_profile_id") or "odysseus")
    bound_profile = kwargs.get("bound_agent_profile_id")
    rebound = bool(conversation_id and bound_profile and bound_profile != profile)
    if rebound:
        conversation_id = None
    request_id = str(kwargs.get("turn_id") or kwargs.get("request_id") or uuid.uuid4().hex)
    ref = dispatcher.dispatch(
        DispatchRequest(
            request_id=request_id,
            archetype=str(kwargs.get("archetype") or "chat"),
            payload={"text": _user_text(messages)},
            conversation_id=conversation_id,
            agent_profile_id=profile,
        )
    )
    yield "data: " + json.dumps({
        "type": "execution",
        "execution_id": ref.automation_execution_id,
        "conversation_id": ref.conversation_id,
        "rebound": rebound,
    }) + "\n\n"
    if rebound:
        yield "data: " + json.dumps({"delta": _REBOUND_NOTE}) + "\n\n"
    seen: set[str] = set()
    emitted_pending: set[str] = set()
    idle = False
    deadline = time.monotonic() + float(kwargs.get("poll_timeout_s", _POLL_TIMEOUT_S))
    while True:
        events = dispatcher.client.conversation_events(ref.conversation_id)
        for event in events:
            eid = str(event.get("id") or "")
            if eid and eid in seen:
                continue
            if eid:
                seen.add(eid)
            text = _assistant_text(event)
            if text:
                yield "data: " + json.dumps({"delta": text}) + "\n\n"
            if event.get("kind") == "ActionEvent" and eid and eid not in emitted_pending:
                emitted_pending.add(eid)
                yield "data: " + json.dumps({
                    "type": "pending_confirmation",
                    "event_id": eid,
                }) + "\n\n"
            if _event_idle(event):
                idle = True
        if idle or _client_conversation_idle(
            dispatcher.client, ref.conversation_id, ref.automation_execution_id
        ):
            break
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(_POLL_SLEEP_S)
    yield "data: [DONE]\n\n"
