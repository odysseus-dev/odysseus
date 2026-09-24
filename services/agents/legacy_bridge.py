"""Production entry that dispatches governed OpenHands executions.

Legacy ``stream_agent_loop`` remains for tests until Task 18 deletion.

Agents: ``invoke_agent Native`` wraps create-or-resume. ``openhands.idle``
covers the poll until a terminal execution status. Empty assistant text on
``finished`` adds event ``odysseus.assistant.empty``. Do not put message
bodies on either span.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, AsyncIterator

from .dispatcher import AgentDispatcher, DispatchRequest
from .workspace_privilege import (
    human_workspace_writable,
    is_human_workspace_mutating_action,
    resolve_action_tool_name,
    workspace_grants_for_turn,
)

_CHAT_MUTATING_REJECT_REASON = "Chat mode cannot approve human-workspace writes."

_IDLE = {"finished", "paused", "completed", "failed", "cancelled", "error", "errored"}
_POLL_SLEEP_S = 0.4
_POLL_TIMEOUT_S = 120.0
_REBOUND_NOTE = "Started a new OpenHands conversation because the agent type changed."


def _event_execution_status(event: dict[str, Any]) -> str:
    """Terminal or in-progress execution_status carried by a state event. Else empty."""
    kind = str(event.get("kind") or "")
    if kind not in {"ConversationStateUpdate", "ConversationStateUpdateEvent"}:
        return ""
    if event.get("key") == "execution_status":
        return str(event.get("value") or "").lower()
    return str(event.get("status") or "").lower()


def _event_idle(event: dict[str, Any]) -> bool:
    return _event_execution_status(event) in _IDLE


def _client_conversation_idle(client: Any, conversation_id: str, execution_id: str) -> bool:
    getter = getattr(client, "conversation_status", None)
    if callable(getter):
        return str(getter(conversation_id) or "").lower() in _IDLE
    fetch = getattr(client, "get_execution", None)
    if callable(fetch):
        info = fetch(execution_id) or {}
        status = str(
            info.get("status")
            or info.get("conversation_status")
            or info.get("execution_status")
            or ""
        ).lower()
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
    llm_message = event.get("llm_message") or {}
    parts = (
        event.get("content")
        or event.get("text")
        or llm_message.get("content")
        or ""
    )
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
    if "user_requested_agent" in kwargs:
        writable = human_workspace_writable(
            user_requested_agent=bool(kwargs.get("user_requested_agent")),
        )
    else:
        writable = True
    grants = workspace_grants_for_turn(
        user_requested_agent=bool(kwargs.get("user_requested_agent")),
        requested=tuple(kwargs.get("workspace_grants") or ()),
    )
    # invoke_agent stays around the sync dispatch only. The poll loop yields,
    # so it uses a detached span below and does not attach OTel context across yields.
    from services.observability.otel import (
        apply_span_attributes,
        get_tracer,
        record_span_error,
    )

    tracer = get_tracer("odysseus")
    with tracer.start_as_current_span("invoke_agent Native") as span:
        apply_span_attributes(
            span,
            {
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.name": "Native",
            },
        )
        try:
            ref = dispatcher.dispatch(
                DispatchRequest(
                    request_id=request_id,
                    archetype=str(kwargs.get("archetype") or "chat"),
                    payload={"text": _user_text(messages)},
                    conversation_id=conversation_id,
                    agent_profile_id=profile,
                    workspace_grants=grants,
                )
            )
        except Exception as exc:
            record_span_error(span, exc)
            raise
        apply_span_attributes(
            span,
            {"gen_ai.conversation.id": ref.conversation_id},
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
    rejected_mutating: set[str] = set()
    idle = False
    # Detached span: start_as_current_span across yields trips the ContextVar detach bug.
    idle_span = tracer.start_span("openhands.idle")
    execution_status = ""
    saw_assistant_text = False
    deadline = time.monotonic() + float(kwargs.get("poll_timeout_s", _POLL_TIMEOUT_S))
    try:
        while True:
            try:
                events = dispatcher.client.conversation_events(ref.conversation_id)
            except Exception as exc:
                record_span_error(idle_span, exc)
                raise
            for event in events:
                eid = str(event.get("id") or "")
                if eid and eid in seen:
                    continue
                if eid:
                    seen.add(eid)
                text = _assistant_text(event)
                if text:
                    saw_assistant_text = True
                    yield "data: " + json.dumps({"delta": text}) + "\n\n"
                status_on_event = _event_execution_status(event)
                if status_on_event:
                    execution_status = status_on_event
                if event.get("kind") == "ActionEvent" and eid:
                    tool_name = resolve_action_tool_name(event)
                    mutating = is_human_workspace_mutating_action(tool_name)
                    if not writable and mutating:
                        if eid not in rejected_mutating:
                            rejected_mutating.add(eid)
                            respond = getattr(
                                dispatcher.client, "respond_to_confirmation", None
                            )
                            if callable(respond):
                                respond(
                                    ref.conversation_id,
                                    accept=False,
                                    reason=_CHAT_MUTATING_REJECT_REASON,
                                )
                    elif eid not in emitted_pending:
                        emitted_pending.add(eid)
                        yield "data: " + json.dumps({
                            "type": "pending_confirmation",
                            "event_id": eid,
                        }) + "\n\n"
                if _event_idle(event):
                    idle = True
            conv_idle = _client_conversation_idle(
                dispatcher.client, ref.conversation_id, ref.automation_execution_id
            )
            if idle or conv_idle:
                if conv_idle:
                    info = {}
                    fetch = getattr(dispatcher.client, "get_execution", None)
                    if callable(fetch):
                        info = fetch(ref.automation_execution_id) or {}
                    status = str(
                        info.get("execution_status")
                        or info.get("status")
                        or ""
                    ).lower()
                    if status:
                        execution_status = status
                    if status in {"error", "errored", "failed"}:
                        yield "data: " + json.dumps({
                            "error": "Agent run failed before completion.",
                            "status": 500,
                        }) + "\n\n"
                break
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(_POLL_SLEEP_S)
    finally:
        # finished + no assistant text is a filterable event, not a failed span.
        if execution_status:
            apply_span_attributes(
                idle_span, {"odysseus.execution_status": execution_status}
            )
        else:
            apply_span_attributes(idle_span, {})
        if execution_status == "finished" and not saw_assistant_text:
            idle_span.add_event("odysseus.assistant.empty")
        idle_span.end()
    yield "data: [DONE]\n\n"
