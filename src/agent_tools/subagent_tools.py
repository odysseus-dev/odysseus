"""Subagent tools — spawn, wait, list actors."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class SpawnSubagentTool:
    def _get_schema(self) -> Dict:
        return {
            "name": "spawn_subagent",
            "description": "Spawn a child agent to perform a task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "The task for the subagent"},
                    "agent_type": {"type": "string", "description": "Agent type: explore, general, build, plan", "default": "general"},
                    "background": {"type": "boolean", "description": "Run in background", "default": False},
                    "tool_allowlist": {"type": "array", "items": {"type": "string"}, "description": "Optional allowed tools"},
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
        from src.agent.actor import Actor, ActorMode, ActorRegistry
        registry = ActorRegistry()
        actor_id = registry.allocate_id(agent_type)
        actor = Actor(id=actor_id, session_id=ctx.get("session_id", ""), mode=ActorMode.SUBAGENT, parent_id=ctx.get("actor_id", "main"), background=background, tool_allowlist=set(tool_allowlist) if tool_allowlist else None)
        registry.register(actor)
        from src.agent.actor import ActorStatus
        registry.update_status(actor_id, ActorStatus.RUNNING)
        return {"actor_id": actor_id, "status": "spawned", "background": background, "message": f"Subagent {actor_id} spawned for task: {task[:50]}..."}


class WaitActorTool:
    def _get_schema(self) -> Dict:
        return {
            "name": "wait_actor",
            "description": "Wait for an actor to complete.",
            "parameters": {
                "type": "object",
                "properties": {
                    "actor_id": {"type": "string", "description": "The actor ID to wait for"},
                    "timeout": {"type": "number", "description": "Timeout in seconds", "default": 600},
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
        from src.agent.actor import ActorRegistry
        registry = ActorRegistry()
        actor = registry.get(actor_id)
        if not actor:
            return {"actor_id": actor_id, "status": "not_found", "outcome": None, "error": f"Actor {actor_id} not found"}
        return {"actor_id": actor_id, "status": actor.status.value, "outcome": actor.outcome.value if actor.outcome else None}


class ListActorsTool:
    def _get_schema(self) -> Dict:
        return {
            "name": "list_actors",
            "description": "List all active actors.",
            "parameters": {"type": "object", "properties": {}},
        }

    async def execute(self, content: str, ctx: Dict) -> Dict:
        from src.agent.actor import ActorRegistry
        registry = ActorRegistry()
        active = registry.list_active()
        return {"actors": [{"id": a.id, "mode": a.mode.value, "status": a.status.value, "outcome": a.outcome.value if a.outcome else None} for a in active]}
