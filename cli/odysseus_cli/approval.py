"""Approval gate for system-mutating tools.

The Odysseus agent loop executes tools internally via
``src.tool_execution.execute_tool_block`` with no built-in approval hook. To add
a Claude-Code-style "allow this command?" prompt without touching the agent loop,
we wrap that function at runtime. Read-only tools (read_file, web_search, list_*)
pass straight through; mutating tools (bash, python, write_file, edit_document)
are gated by the configured approval policy.
"""

from __future__ import annotations

import asyncio
from typing import Set

from . import renderer as r
from .config import APPROVAL_ASK, APPROVAL_AUTO, APPROVAL_DENY, MUTATING_TOOLS


class ApprovalState:
    """Tracks per-session 'always allow' grants for specific tools."""

    def __init__(self, policy: str):
        self.policy = policy
        self._always: Set[str] = set()

    def always_allowed(self, tool: str) -> bool:
        return tool in self._always

    def grant_always(self, tool: str) -> None:
        self._always.add(tool)


def _denied_result(tool: str):
    """A result tuple shaped like execute_tool_block's own output."""
    desc = f"{tool}: denied by user"
    result = {
        "error": "User denied this tool call. Do not retry it; either choose a "
                 "different approach or ask the user what to do instead.",
        "exit_code": 1,
    }
    return desc, result


async def _ask(tool: str, command: str) -> str:
    """Prompt the user. Returns 'yes', 'always', or 'no'."""
    r.write()
    r.write(r.c(f"  ⚠ allow {tool}?", r.BOLD + r.YELLOW))
    preview = (command or "").strip()
    for line in (preview.splitlines() or [preview])[:8]:
        r.write(r.c("    " + line[:200], r.DIM))
    prompt = r.c("    [y]es / [n]o / [a]lways  ❯ ", r.CYAN)

    # input() blocks; run it off the event loop so we don't stall other tasks.
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
    """Monkeypatch execute_tool_block to enforce the approval policy.

    Patches both the source module and the agent_tools re-export so whichever
    symbol the agent loop imported is covered.
    """
    import src.tool_execution as te

    original = te.execute_tool_block

    async def gated(block, *args, **kwargs):
        tool = getattr(block, "tool_type", None)
        command = getattr(block, "content", "") or ""

        needs_gate = tool in MUTATING_TOOLS and state.policy != APPROVAL_AUTO
        if needs_gate and not state.always_allowed(tool):
            if state.policy == APPROVAL_DENY:
                return _denied_result(tool)
            decision = await _ask(tool, command)
            if decision == "no":
                return _denied_result(tool)
            if decision == "always":
                state.grant_always(tool)

        return await original(block, *args, **kwargs)

    te.execute_tool_block = gated
    try:
        import src.agent_tools as at
        at.execute_tool_block = gated
    except Exception:
        pass
    # agent_loop imports execute_tool_block by name at module load.
    try:
        import src.agent_loop as al
        if hasattr(al, "execute_tool_block"):
            al.execute_tool_block = gated
    except Exception:
        pass
