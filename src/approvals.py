"""In-process approval channel for the agent loop (manual / accept-edits modes).

When a tool call needs the user's OK, the loop registers a pending approval and
awaits its future; the client POSTs a decision that resolves it. A per-session
"don't ask again" memory lets an approved tool auto-run for the rest of the
session.

Single-process model (one uvicorn worker): the future lives in this module's
dict, so the awaiting loop and the resolving request share it. Multi-worker
deployments would need a shared store (out of scope here).
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, Optional, Set

APPROVAL_TIMEOUT = 300  # seconds (5 min) -> auto-deny

_pending: Dict[str, Dict[str, Any]] = {}   # id -> {"fut", "owner"}
_remembered: Dict[str, Set[str]] = {}


def new_id() -> str:
    return uuid.uuid4().hex


def register(approval_id: str, owner: Optional[str] = None) -> asyncio.Future:
    """Create + store a future for a pending approval (call from the loop).
    ``owner`` binds the approval to a user so only they can resolve it."""
    fut = asyncio.get_running_loop().create_future()
    _pending[approval_id] = {"fut": fut, "owner": owner}
    return fut


def resolve(approval_id: str, approved: bool, remember: bool = False,
            requester: Optional[str] = None) -> bool:
    """Resolve a pending approval from a client decision. Returns True only if a
    pending approval with that id exists AND ``requester`` matches its owner
    (when both are set) — so one user can't resolve another user's approval."""
    entry = _pending.get(approval_id)
    if entry is None or entry["fut"].done():
        return False
    owner = entry.get("owner")
    if owner is not None and requester is not None and owner != requester:
        return False
    entry["fut"].set_result({"approved": bool(approved), "remember": bool(remember)})
    return True


def discard(approval_id: str) -> None:
    _pending.pop(approval_id, None)


async def await_decision(approval_id: str, timeout: float = APPROVAL_TIMEOUT) -> Dict[str, Any]:
    """Await the client's decision for a registered approval; auto-deny on
    timeout. Always discards the pending entry when done."""
    entry = _pending.get(approval_id)
    if entry is None:
        return {"approved": False, "timeout": False}
    try:
        return await asyncio.wait_for(entry["fut"], timeout=timeout)
    except asyncio.TimeoutError:
        return {"approved": False, "timeout": True}
    finally:
        discard(approval_id)


# --- per-session "don't ask again" memory -----------------------------------
def remember(session_id: Optional[str], tool: str) -> None:
    if session_id:
        _remembered.setdefault(session_id, set()).add(tool)


def is_remembered(session_id: Optional[str], tool: str) -> bool:
    return bool(session_id) and tool in _remembered.get(session_id, ())


def clear_session(session_id: str) -> None:
    """Forget a session's remembered approvals (e.g. when it's deleted)."""
    _remembered.pop(session_id, None)
