"""Per-turn tool permission modes and interactive approval broker."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Dict, Optional

from src.tool_security import PLAN_MODE_READONLY_TOOLS, email_tool_policy_names

PERMISSION_MODES = frozenset({"auto", "ask_actions", "ask_all", "read_only", "sandboxed_workspace"})
_INTERNAL_TOOLS = frozenset({"ask_user", "update_plan"})


def normalize_permission_mode(value: object) -> str:
    mode = str(value or "auto").strip().lower()
    return mode if mode in PERMISSION_MODES else "auto"


def tool_is_read_only(tool_name: str) -> bool:
    aliases = email_tool_policy_names(str(tool_name or ""))
    return any(alias in PLAN_MODE_READONLY_TOOLS for alias in aliases)


def tool_requires_approval(tool_name: str, mode: str) -> bool:
    mode = normalize_permission_mode(mode)
    if mode in {"auto", "read_only"} or tool_name in _INTERNAL_TOOLS:
        return False
    if mode == "sandboxed_workspace":
        return tool_name in {"bash", "python"}
    if mode == "ask_all":
        return True
    return not tool_is_read_only(tool_name)


@dataclass
class PendingApproval:
    owner: Optional[str]
    session_id: Optional[str]
    tool: str
    future: asyncio.Future


_pending: Dict[str, PendingApproval] = {}


def create_approval(owner: Optional[str], session_id: Optional[str], tool: str) -> tuple[str, asyncio.Future]:
    approval_id = uuid.uuid4().hex
    future = asyncio.get_running_loop().create_future()
    _pending[approval_id] = PendingApproval(owner, session_id, tool, future)
    return approval_id, future


def resolve_approval(approval_id: str, owner: Optional[str], approved: bool) -> bool:
    pending = _pending.get(str(approval_id or ""))
    if pending is None or pending.owner != owner:
        return False
    _pending.pop(approval_id, None)
    if not pending.future.done():
        pending.future.set_result(bool(approved))
    return True


def cancel_approval(approval_id: str) -> None:
    pending = _pending.pop(str(approval_id or ""), None)
    if pending and not pending.future.done():
        pending.future.cancel()
