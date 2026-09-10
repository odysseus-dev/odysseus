"""Human-workspace write privilege for interactive Chat vs Agent."""

from __future__ import annotations

from typing import Any

HUMAN_WORKSPACE_MUTATING_OPS = frozenset(
    {
        "notes.write",
        "documents.index",
        "mail.send",
        "calendar.write",
        "memory.write",
        "tasks.write",
    }
)


def human_workspace_writable(*, user_requested_agent: bool) -> bool:
    """Return True only when the user explicitly chose Agent mode."""

    return bool(user_requested_agent)


def workspace_grants_for_turn(
    *,
    user_requested_agent: bool,
    requested: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Chat turns never receive owner-workspace grants, including auto-escalation."""

    if not human_workspace_writable(user_requested_agent=user_requested_agent):
        return ()
    return tuple(requested)


def resolve_action_tool_name(event: dict[str, Any]) -> str | None:
    """Resolve MCP tool identity from Agent Server ActionEvent shapes."""

    tool_name = event.get("tool_name") or event.get("tool")
    if tool_name is not None:
        raw = str(tool_name).strip()
        return raw or None
    action = event.get("action")
    if isinstance(action, str):
        raw = action.strip()
        return raw or None
    if isinstance(action, dict):
        for key in ("tool_name", "name", "tool", "kind"):
            val = action.get(key)
            if val is not None:
                raw = str(val).strip()
                if raw:
                    return raw
    return None


def is_human_workspace_mutating_action(tool_name: str | None) -> bool:
    """Whether an OpenHands/MCP action mutates Odysseus production records."""

    raw = str(tool_name or "").strip()
    if not raw:
        return False
    if raw in HUMAN_WORKSPACE_MUTATING_OPS:
        return True
    dotted = raw.replace("__", ".").replace("_", ".")
    return any(dotted.endswith(op) or op in dotted for op in HUMAN_WORKSPACE_MUTATING_OPS)
