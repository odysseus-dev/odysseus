"""
swarm_worker.py

Thin wrapper around ``stream_agent_loop()`` that runs a single swarm worker.

Each worker:
  - Gets a role-specific system prompt injected
  - Has its tool allowlist scoped per the SwarmRole definition
  - Receives shared context from prior workers (via SwarmMemory)
  - Streams SSE events tagged with its role for frontend rendering
  - Reports metrics (tokens, duration) on completion
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Set

from src.swarm.swarm_types import SwarmRole, SwarmTask, TaskStatus
from src.swarm.swarm_memory import SwarmMemory

logger = logging.getLogger(__name__)


def _sse(data: dict) -> str:
    """Format a dict as an SSE ``data:`` line."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def run_worker(
    role: SwarmRole,
    task: SwarmTask,
    *,
    endpoint_url: str,
    model: str,
    session_id: str,
    owner: Optional[str] = None,
    shared_memory: Optional[SwarmMemory] = None,
    user_query: str = "",
    headers: Optional[Dict[str, str]] = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    context_length: int = 0,
    max_rounds: int = 10,
    disabled_tools: Optional[Set[str]] = None,
) -> AsyncGenerator[str, None]:
    """Run a single swarm worker and yield SSE events.

    Yields:
        SSE events of the form ``data: {...}\\n\\n``.  Event types:

        - ``worker_start``  — emitted once at the beginning
        - ``delta``         — streaming text chunks from the worker's LLM
        - ``tool_start``    — forwarded from the inner agent loop
        - ``tool_output``   — forwarded from the inner agent loop
        - ``worker_done``   — emitted once at the end (includes metrics)
        - ``worker_failed`` — emitted if the worker errors out

    The caller (``SwarmManager``) collects the full text output and writes
    it into the ``SwarmTask.result`` / ``SwarmMemory``.
    """
    from src.agent_loop import stream_agent_loop

    # Resolve model/endpoint — role overrides take precedence
    effective_model = role.model or model
    effective_endpoint = role.endpoint_url or endpoint_url

    # Build the worker's message history
    messages = _build_worker_messages(role, task, user_query, shared_memory)

    # Compute the effective disabled-tools set.  If the role declares an
    # explicit allowlist (not ``["all"]``), disable every other built-in tool
    # before the agent loop builds its prompt, schemas, and execution policy.
    worker_disabled = set(disabled_tools or [])
    worker_disabled.update(role.tools_denied)
    if "all" not in role.tools_allowed:
        from src.agent_tools import TOOL_TAGS
        from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

        available_tools = set(TOOL_TAGS)
        available_tools.update(
            schema.get("function", {}).get("name")
            for schema in FUNCTION_TOOL_SCHEMAS
            if schema.get("function", {}).get("name")
        )
        worker_disabled.update(available_tools - set(role.tools_allowed))

    # Emit worker_start
    yield _sse({
        "type": "worker_start",
        "worker": role.name,
        "worker_slug": role.slug,
        "task": task.prompt,
        "task_id": task.id,
    })

    task.status = TaskStatus.RUNNING
    task.started_at = time.time()
    accumulated_text = ""
    token_count = 0

    try:
        async for event_str in stream_agent_loop(
            endpoint_url=effective_endpoint,
            model=effective_model,
            messages=messages,
            headers=headers,
            temperature=temperature,
            max_tokens=max_tokens,
            context_length=context_length,
            session_id=session_id,
            owner=owner,
            disabled_tools=worker_disabled,
            max_rounds=max_rounds,
            workload="swarm_worker",
        ):
            # Parse and re-tag the inner SSE events
            if not event_str.startswith("data: "):
                continue

            payload = event_str[6:].strip()
            if payload == "[DONE]":
                continue

            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue

            # Forward text deltas — tag with worker identity
            if "delta" in event:
                accumulated_text += event["delta"]
                yield _sse({
                    "type": "worker_delta",
                    "worker": role.name,
                    "worker_slug": role.slug,
                    "delta": event["delta"],
                })

            # Forward tool events
            elif event.get("type") in ("tool_start", "tool_output"):
                event["worker"] = role.name
                event["worker_slug"] = role.slug
                yield _sse(event)

            # Capture metrics from the inner loop
            elif event.get("type") == "metrics":
                inner_metrics = event.get("data", {})
                token_count = inner_metrics.get("total_tokens", 0)

        # Success
        task.status = TaskStatus.DONE
        task.result = accumulated_text.strip() or "(No output)"
        task.completed_at = time.time()
        task.metrics = {
            "tokens": token_count,
            "duration_ms": task.duration_ms,
            "model": effective_model,
        }

        # Contribute to shared memory
        if shared_memory and task.result:
            shared_memory.contribute(
                role_slug=role.slug,
                role_name=role.name,
                content=task.result,
                task_id=task.id,
            )

        yield _sse({
            "type": "worker_done",
            "worker": role.name,
            "worker_slug": role.slug,
            "task_id": task.id,
            "tokens": token_count,
            "duration_ms": task.duration_ms,
        })

    except Exception as exc:
        task.status = TaskStatus.FAILED
        task.result = f"Error: {exc}"
        task.completed_at = time.time()
        task.metrics = {"error": str(exc)}
        logger.exception("Swarm worker %s failed", role.slug)

        yield _sse({
            "type": "worker_failed",
            "worker": role.name,
            "worker_slug": role.slug,
            "task_id": task.id,
            "error": str(exc),
        })


def _build_worker_messages(
    role: SwarmRole,
    task: SwarmTask,
    user_query: str,
    shared_memory: Optional[SwarmMemory],
) -> List[Dict[str, str]]:
    """Construct the message history for a worker's agent loop call.

    Structure:
      1. System prompt (role persona + swarm context instructions)
      2. Context from prior workers (if shared memory has contributions)
      3. User message (the specific sub-task assigned by the master)
    """
    # Build system prompt
    system_parts = [role.system_prompt]

    system_parts.append(
        "\n\n## Your Assignment\n"
        "You are working as part of a swarm — a team of AI specialists collaborating "
        "on the user's request. Focus ONLY on your assigned sub-task below. "
        "Be thorough, specific, and actionable. Do not repeat work others have done."
    )

    # Inject shared context from prior workers
    if shared_memory and shared_memory.contribution_count > 0:
        context = shared_memory.get_context(exclude_role=role.slug, max_chars=8000)
        if context:
            system_parts.append(
                f"\n\n## Context from Team Members\n"
                f"The following is work already completed by other team members. "
                f"Build on this, don't repeat it.\n\n{context}"
            )

    messages: List[Dict[str, str]] = [
        {"role": "system", "content": "\n".join(system_parts)},
    ]

    # Add the original user query as context
    if user_query:
        messages.append({
            "role": "user",
            "content": f"Original request: {user_query}\n\nYour specific task: {task.prompt}",
        })
    else:
        messages.append({
            "role": "user",
            "content": task.prompt,
        })

    return messages
