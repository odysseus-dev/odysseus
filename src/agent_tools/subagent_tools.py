"""Subagent tools — spawn, wait, list actors.

Tools that allow the agent to spawn child agent loops,
wait for their completion, and list active actors.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

# Max rounds for subagent execution
_SUBAGENT_MAX_ROUNDS = 20

# All available tools (from TOOL_TAGS)
_ALL_TOOLS = {
    "bash", "python", "read_file", "write_file", "edit_file",
    "web_search", "web_fetch", "grep", "glob", "ls",
    "create_document", "update_document", "edit_document", "suggest_document",
    "manage_documents", "get_workspace", "ask_user", "update_plan",
    "chat_with_model", "ask_teacher", "list_models", "manage_bg_jobs",
    "create_session", "list_sessions", "send_to_session", "manage_session",
    "manage_memory", "manage_tasks", "manage_skills",
    "manage_endpoints", "manage_mcp", "manage_webhooks", "manage_tokens",
    "manage_settings", "manage_notes", "manage_calendar",
    "ui_control", "generate_image", "api_call",
    "spawn_subagent", "wait_actor", "list_actors",
}


def _compute_disabled_tools(tool_allowlist: set = None) -> set:
    """Compute which tools should be disabled based on the allowlist.
    
    If tool_allowlist is provided, all tools NOT in the allowlist are disabled.
    If tool_allowlist is None, no tools are disabled.
    """
    if not tool_allowlist:
        return set()
    return _ALL_TOOLS - tool_allowlist


async def _run_subagent_loop(
    actor_id: str,
    task: str,
    session_id: str,
    endpoint_url: str = "",
    model: str = "",
    owner: str = "",
    tool_allowlist: set = None,
):
    """Run agent loop headless for a subagent. Stores result in Actor."""
    from src.agent.actor import ActorRegistry, ActorStatus, ActorOutcome
    from src.agent_loop import stream_agent_loop

    registry = ActorRegistry.get_instance()
    actor = registry.get(actor_id)
    if not actor:
        return

    try:
        # Build messages for the subagent
        messages = [{"role": "user", "content": task}]

        full = ""
        tool_events = []
        round_num = 1

        async for chunk in stream_agent_loop(
            endpoint_url, model, messages,
            context_length=0,
            session_id=session_id,
            max_rounds=_SUBAGENT_MAX_ROUNDS,
            owner=owner,
            disabled_tools=_compute_disabled_tools(tool_allowlist),
            relevant_tools=tool_allowlist,
        ):
            if not chunk.startswith("data: "):
                continue
            body = chunk[6:].strip()
            if not body or body == "[DONE]":
                continue
            try:
                d = json.loads(body)
            except (ValueError, TypeError):
                continue
            if not isinstance(d, dict):
                continue
            if "delta" in d:
                delta = d.get("delta")
                if isinstance(delta, str):
                    if d.get("thinking"):
                        continue
                    full += delta
            elif d.get("type") == "agent_step":
                round_num = d.get("round", round_num)
            elif d.get("type") == "tool_output":
                tool_events.append({
                    "round": round_num,
                    "tool": d.get("tool"),
                    "command": d.get("command", ""),
                    "output": (d.get("output") or "")[:2000],
                    "exit_code": d.get("exit_code"),
                })

        # Store result
        actor.result = full
        actor.tool_events = tool_events
        registry.update_status(actor_id, ActorStatus.IDLE, outcome=ActorOutcome.SUCCESS)
        logger.info(f"Subagent {actor_id} completed: {len(full)} chars, {len(tool_events)} tools")

        # Write task progress to file
        try:
            import os
            _data_dir = os.environ.get("APP_DATA_DIR", "/app/data")
            _progress_base = os.path.join(_data_dir, "memory", session_id)
            from src.agent.memory_persist import TaskProgressStore
            _progress_store = TaskProgressStore(_progress_base)
            _progress_content = f"Status: completed\nTask: {task[:200]}\nResult: {full[:1000]}\nTools used: {len(tool_events)}"
            _progress_store.write_progress(actor_id, _progress_content)
        except Exception as _e:
            logger.debug(f"Task progress write skipped: {_e}")

    except Exception as e:
        actor.error = str(e)
        registry.update_status(actor_id, ActorStatus.IDLE, outcome=ActorOutcome.FAILURE, error=str(e))
        logger.warning(f"Subagent {actor_id} failed: {e}")


class SpawnSubagentTool:
    """Spawn a subagent to perform a task."""

    def _get_schema(self) -> Dict:
        return {
            "name": "spawn_subagent",
            "description": "Spawn a child agent to perform a task. Returns actor_id for tracking.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "The task for the subagent to perform",
                    },
                    "agent_type": {
                        "type": "string",
                        "description": "Agent type: explore, general, build, plan",
                        "default": "general",
                    },
                    "background": {
                        "type": "boolean",
                        "description": "Run in background (fire-and-forget)",
                        "default": False,
                    },
                    "tool_allowlist": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of allowed tools",
                    },
                },
                "required": ["task"],
            },
        }

    async def execute(self, content: str, ctx: Dict) -> Dict:
        try:
            args = json.loads(content) if isinstance(content, str) else content
        except json.JSONDecodeError:
            args = {"task": content}

        task = args.get("task", "")
        agent_type = args.get("agent_type", "general")
        background = args.get("background", False)
        tool_allowlist = args.get("tool_allowlist")

        from src.agent.actor import Actor, ActorMode, ActorRegistry, ActorStatus
        registry = ActorRegistry.get_instance()
        actor_id = registry.allocate_id(agent_type)

        actor = Actor(
            id=actor_id,
            session_id=ctx.get("session_id", ""),
            mode=ActorMode.SUBAGENT,
            parent_id=ctx.get("actor_id", "main"),
            background=background,
            tool_allowlist=set(tool_allowlist) if tool_allowlist else None,
        )
        registry.register(actor)
        registry.update_status(actor_id, ActorStatus.RUNNING)

        # Get endpoint/model from context or settings
        endpoint_url = ctx.get("endpoint_url", "")
        model = ctx.get("model", "")
        owner = ctx.get("owner", "")

        if not endpoint_url or not model:
            try:
                from src.settings import get_setting
                from src.endpoint_resolver import resolve_endpoint
                ep = resolve_endpoint(model or "default")
                if ep:
                    endpoint_url = ep.get("url", endpoint_url)
                    model = ep.get("model", model)
            except Exception:
                pass

        # Launch the subagent loop as a background task
        task_coro = _run_subagent_loop(
            actor_id=actor_id,
            task=task,
            session_id=ctx.get("session_id", ""),
            endpoint_url=endpoint_url,
            model=model,
            owner=owner,
            tool_allowlist=set(tool_allowlist) if tool_allowlist else None,
        )

        if background:
            # Fire-and-forget
            asyncio.create_task(task_coro)
            return {
                "actor_id": actor_id,
                "status": "spawned",
                "background": True,
                "message": f"Subagent {actor_id} spawned in background for: {task[:80]}...",
            }
        else:
            # Run and wait for completion
            actor._task = asyncio.create_task(task_coro)
            try:
                await asyncio.wait_for(actor._task, timeout=300)
            except asyncio.TimeoutError:
                logger.warning(f"Subagent {actor_id} timed out after 300s")

            return {
                "actor_id": actor_id,
                "status": actor.status.value,
                "outcome": actor.outcome.value if actor.outcome else None,
                "result": (actor.result or "")[:5000],
                "tool_count": len(actor.tool_events),
                "error": actor.error,
            }


class WaitActorTool:
    """Wait for an actor to complete."""

    def _get_schema(self) -> Dict:
        return {
            "name": "wait_actor",
            "description": "Wait for an actor to complete and return its result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "actor_id": {
                        "type": "string",
                        "description": "The actor ID to wait for",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Timeout in seconds (default 600)",
                        "default": 600,
                    },
                },
                "required": ["actor_id"],
            },
        }

    async def execute(self, content: str, ctx: Dict) -> Dict:
        try:
            args = json.loads(content) if isinstance(content, str) else content
        except json.JSONDecodeError:
            return {"error": "Invalid JSON"}

        actor_id = args.get("actor_id", "")
        timeout = args.get("timeout", 600)

        from src.agent.actor import ActorRegistry
        registry = ActorRegistry.get_instance()
        actor = registry.get(actor_id)
        if not actor:
            return {"actor_id": actor_id, "status": "not_found", "outcome": None, "error": f"Actor {actor_id} not found"}

        # If still running, wait for completion
        if actor.is_active and actor._task:
            try:
                await asyncio.wait_for(actor._task, timeout=timeout)
            except asyncio.TimeoutError:
                return {
                    "actor_id": actor_id,
                    "status": "running",
                    "outcome": None,
                    "message": f"Still running after {timeout}s timeout",
                }

        return {
            "actor_id": actor_id,
            "status": actor.status.value,
            "outcome": actor.outcome.value if actor.outcome else None,
            "result": (actor.result or "")[:5000],
            "tool_count": len(actor.tool_events),
            "error": actor.error,
        }


class ListActorsTool:
    """List active actors."""

    def _get_schema(self) -> Dict:
        return {
            "name": "list_actors",
            "description": "List all active actors and their status.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        }

    async def execute(self, content: str, ctx: Dict) -> Dict:
        from src.agent.actor import ActorRegistry
        registry = ActorRegistry.get_instance()
        active = registry.list_active()
        return {
            "actors": [
                {
                    "id": a.id,
                    "mode": a.mode.value,
                    "status": a.status.value,
                    "outcome": a.outcome.value if a.outcome else None,
                    "result_preview": (a.result or "")[:200] if a.result else None,
                }
                for a in active
            ]
        }
