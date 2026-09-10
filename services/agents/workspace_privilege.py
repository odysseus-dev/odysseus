"""Human-workspace write privilege for interactive Chat vs Agent."""

from __future__ import annotations

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


def is_human_workspace_mutating_action(tool_name: str | None) -> bool:
    """Whether an OpenHands/MCP action mutates Odysseus production records."""

    raw = str(tool_name or "").strip()
    if not raw:
        return False
    if raw in HUMAN_WORKSPACE_MUTATING_OPS:
        return True
    dotted = raw.replace("__", ".").replace("_", ".")
    return any(dotted.endswith(op) or op in dotted for op in HUMAN_WORKSPACE_MUTATING_OPS)
