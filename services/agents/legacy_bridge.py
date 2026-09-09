"""Production entry that dispatches governed OpenHands executions.

Legacy ``stream_agent_loop`` remains for tests until Task 18 deletion.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from .dispatcher import AgentDispatcher, DispatchRequest


def _user_text(messages: list[dict[str, Any]] | None) -> str:
    for message in reversed(messages or []):
        if message.get("role") == "user":
            content = message.get("content") or ""
            return content if isinstance(content, str) else json.dumps(content)
    return ""


async def stream_governed_agent(
    endpoint_url: str | None = None,
    model: str | None = None,
    messages: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> AsyncIterator[str]:
    dispatcher = kwargs.pop("dispatcher", None) or AgentDispatcher()
    request_id = str(kwargs.get("request_id") or kwargs.get("session_id") or "governed")
    conversation_id = kwargs.get("conversation_id") or kwargs.get("session_id")
    archetype = str(kwargs.get("archetype") or "chat")
    ref = dispatcher.dispatch(
        DispatchRequest(
            request_id=request_id,
            archetype=archetype,
            payload={"text": _user_text(messages)},
            conversation_id=str(conversation_id) if conversation_id else None,
        )
    )
    yield "data: " + json.dumps(
        {
            "type": "execution",
            "execution_id": ref.automation_execution_id,
            "conversation_id": ref.conversation_id,
        }
    ) + "\n\n"
    yield "data: [DONE]\n\n"
