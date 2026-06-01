"""Approval gate + project sandbox for system-mutating tools.

The Odysseus agent loop executes tools internally via
``src.tool_execution.execute_tool_block`` with no built-in approval hook. To add
a Claude-Code-style "allow this command?" prompt without touching the agent loop,
we wrap that function at runtime.

Two layers of control:
  1. Sandbox — ``read_file`` / ``write_file`` paths must stay inside the project
     root; escapes are denied regardless of the approval policy.
  2. Approval — mutating tools (bash, python, write_file, edit_document) are
     gated by the policy. For ``write_file`` the prompt shows a unified diff so
     you see exactly what changes before it's applied.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional, Set

from . import renderer as r
from . import sandbox
from .config import APPROVAL_AUTO, APPROVAL_DENY, MUTATING_TOOLS


class ApprovalState:
    """Tracks policy + per-session 'always allow' grants for specific tools."""

    def __init__(self, policy: str, project_root: Optional[Path] = None):
        self.policy = policy
        self.project_root = Path(project_root or Path.cwd()).resolve()
        self._always: Set[str] = set()
        self._call_counts: dict = {}
        self.todos: list = []  # agent's task checklist (todo_write tool)

    def always_allowed(self, tool: str) -> bool:
        return tool in self._always

    def grant_always(self, tool: str) -> None:
        self._always.add(tool)

    def note_call(self, signature) -> int:
        """Record a tool-call signature; return how many times it's been seen."""
        n = self._call_counts.get(signature, 0) + 1
        self._call_counts[signature] = n
        return n

    def reset_calls(self) -> None:
        """Clear the per-turn duplicate-call tracker."""
        self._call_counts.clear()


def _denied_result(tool: str, reason: str = ""):
    """A result tuple shaped like execute_tool_block's own output."""
    why = reason or "the user declined this tool call"
    desc = f"{tool}: denied ({why})"
    result = {
        "error": f"Tool call denied: {why}. Do not retry it; choose a different "
                 "approach or ask the user what to do instead.",
        "exit_code": 1,
    }
    return desc, result


async def _ask(tool: str, command: str, diff_lines=None) -> str:
    """Prompt the user. Returns 'yes', 'always', or 'no'."""
    r.write()
    r.write(r.c(f"  ⚠ allow {tool}?", r.BOLD + r.YELLOW))
    if diff_lines is not None:
        r.diff(diff_lines)
    else:
        preview = (command or "").strip()
        for line in (preview.splitlines() or [preview])[:8]:
            r.write(r.c("    " + line[:200], r.DIM))
    prompt = r.c("    [y]es / [n]o / [a]lways  ❯ ", r.CYAN)

    loop = asyncio.get_event_loop()
    try:
        answer = (await loop.run_in_executor(None, input, prompt)).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "no"
    if answer in ("y", "yes"):
        return "yes"
    if answer in ("a", "always"):
        return "always"
    return "no"


def install(state: ApprovalState) -> None:
    """Monkeypatch execute_tool_block to enforce sandbox + approval policy."""
    import src.tool_execution as te

    original = te.execute_tool_block

    async def gated(block, *args, **kwargs):
        tool = getattr(block, "tool_type", None)
        content = getattr(block, "content", "") or ""

        # ── Layer 0: loop breaker ──
        # Local coder models often re-issue the *same* call every round instead
        # of using the result they already got. After the first execution of an
        # identical call, short-circuit and push the model to answer.
        count = state.note_call((tool, content.strip()))
        if count > 1:
            desc = f"{tool}: duplicate call suppressed"
            return desc, {
                "error": "You already ran this exact call and its output is "
                         "shown above. Do NOT call it again — use what you have "
                         "and write your final answer now.",
                "exit_code": 1,
            }

        # ── Layer 1: path-containment sandbox for file tools ──
        diff_lines = None
        if tool in sandbox.PATH_TOOLS:
            pstr = sandbox.tool_path(tool, content)
            if pstr:
                resolved = sandbox.resolve_in_root(pstr, state.project_root)
                if resolved is None:
                    return _denied_result(
                        tool, f"path '{pstr}' is outside the project root")
                if tool == "write_file":
                    _, new_content = sandbox.split_write(content)
                    diff_lines = sandbox.unified_diff_for_write(resolved, new_content)

        # ── Layer 2: approval policy for mutating tools ──
        needs_gate = tool in MUTATING_TOOLS and state.policy != APPROVAL_AUTO
        if needs_gate and not state.always_allowed(tool):
            if state.policy == APPROVAL_DENY:
                return _denied_result(tool, "read-only mode")
            decision = await _ask(tool, content, diff_lines=diff_lines)
            if decision == "no":
                return _denied_result(tool)
            if decision == "always":
                state.grant_always(tool)

        return await original(block, *args, **kwargs)

    te.execute_tool_block = gated
    for mod_name in ("src.agent_tools", "src.agent_loop"):
        try:
            mod = __import__(mod_name, fromlist=["execute_tool_block"])
            if hasattr(mod, "execute_tool_block"):
                mod.execute_tool_block = gated
        except Exception:
            pass
