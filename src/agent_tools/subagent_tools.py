"""Agent tool: delegate work to parallel subagents.

Each subagent is a fresh `stream_agent_loop` run (own context, own tool subset,
never the parent's chat history). Depth is capped in `subagent_runtime`, so a
subagent cannot delegate further.
"""
from __future__ import annotations

import json
from typing import Any

from src import subagent_runtime


class DelegateSubagentTool:
    async def execute(self, content: Any, ctx: dict) -> dict:
        if not subagent_runtime.can_delegate():
            return {
                "error": "delegation_unavailable",
                "message": (
                    "Subagents are only available from the main agent loop, one level deep. "
                    "Do the work directly instead."
                ),
            }

        args = _parse_args(content)
        tasks = args.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            return {
                "error": "bad_arguments",
                "message": "tasks must be a non-empty array of {label, instruction}.",
            }

        timeout = _timeout(args.get("timeout_seconds"))
        try:
            results = await subagent_runtime.run_tasks(
                tasks,
                parallel=bool(args.get("parallel", True)),
                timeout=timeout,
            )
        except RuntimeError as exc:
            return {"error": "delegation_unavailable", "message": str(exc)}

        return {
            "count": len(results),
            "ok": sum(1 for r in results if r.get("ok")),
            "subagents": results,
        }


def _parse_args(content: Any) -> dict:
    if isinstance(content, dict):
        return content
    if isinstance(content, str) and content.strip():
        try:
            parsed = json.loads(content)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _timeout(raw: Any) -> float:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return float(subagent_runtime.DEFAULT_TIMEOUT_SEC)
    return float(max(30, min(value, 3600)))
