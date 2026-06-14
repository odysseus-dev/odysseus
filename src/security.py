import contextvars
from functools import wraps
from typing import AbstractSet, Optional

# Fail-open by default for backward compatibility: legacy callers outside
# agent_loop historically invoked tools with full write access. The chat turn
# lifecycle is therefore responsible for explicitly opting exploratory / plan
# turns into read-only mode before any tool executes.
_turn_readonly: contextvars.ContextVar = contextvars.ContextVar(
    "agent_turn_readonly", default=False
)
_turn_readonly_reason: contextvars.ContextVar = contextvars.ContextVar(
    "agent_turn_readonly_reason", default=None
)
_READONLY_HTTP_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def get_turn_readonly() -> bool:
    """Whether the current tool execution is bound as read-only."""
    return bool(_turn_readonly.get())


def get_turn_readonly_reason() -> Optional[str]:
    """Why the current tool execution is read-only, when set."""
    return _turn_readonly_reason.get()


class ReadonlyTurnViolation(RuntimeError):
    def __init__(self, tool_name: str, reason: Optional[str] = None):
        self.tool_name = tool_name
        self.reason = reason
        detail = f" ({reason})" if reason else ""
        super().__init__(
            f"Tool '{tool_name}' is blocked in this read-only turn{detail}. "
            "Ask for an explicit write/execute action first."
        )


def assert_not_readonly(tool_name: str, reason: Optional[str] = None) -> None:
    if get_turn_readonly():
        raise ReadonlyTurnViolation(tool_name, reason or get_turn_readonly_reason())


def assert_mutating_action_allowed(
    tool_name: str,
    action: Optional[str],
    mutating_actions: AbstractSet[str],
) -> None:
    action_name = str(action or "").strip().lower()
    if action_name in mutating_actions:
        assert_not_readonly(tool_name, reason=f"action={action_name}")


def assert_mutating_http_method_allowed(tool_name: str, method: Optional[str]) -> None:
    method_name = str(method or "GET").strip().upper()
    if method_name not in _READONLY_HTTP_METHODS:
        assert_not_readonly(tool_name, reason=f"method={method_name}")


def requires_write_access(tool_name: str):
    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            assert_not_readonly(tool_name)
            return await fn(*args, **kwargs)
        return wrapper
    return decorator
