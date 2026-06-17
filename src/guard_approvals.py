"""
Guard approval store — manages pending MEDIUM-risk command approvals.
Tokens are single-use and expire after APPROVAL_TTL_SECONDS.
"""
import secrets
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

APPROVAL_TTL_SECONDS = 120

_pending: dict[str, dict] = {}


def create_approval(command: str, owner: str, ws_id: str) -> str:
    """Create a pending approval request. Returns a one-time token."""
    _gc()
    token = secrets.token_urlsafe(24)
    _pending[token] = {
        "command": command,
        "owner": owner,
        "ws_id": ws_id,
        "decision": None,
        "created_at": time.time(),
    }
    logger.info("Approval required: owner=%s ws=%s cmd=%r token=%s", owner, ws_id, command[:80], token)
    return token


def decide(token: str, owner: str, decision: str) -> Optional[dict]:
    """
    Record approve/deny decision.
    Returns the approval record if successful, None if token not found/expired.
    Only the original requester can decide.
    """
    _gc()
    record = _pending.get(token)
    if not record:
        return None
    if record["owner"] != owner:
        return None
    if record["decision"] is not None:
        return None
    record["decision"] = decision
    return record


def consume(token: str, owner: str) -> Optional[dict]:
    """
    Consume (read + delete) an approved token.
    Returns the record if approved and owned, None otherwise.
    """
    _gc()
    record = _pending.get(token)
    if not record:
        return None
    if record["owner"] != owner:
        return None
    if record["decision"] != "approve":
        return None
    del _pending[token]
    return record


def get_pending_for_owner(owner: str) -> list:
    """List all pending (undecided) approval requests for an owner."""
    _gc()
    return [
        {"token": t, "command": r["command"], "ws_id": r["ws_id"]}
        for t, r in _pending.items()
        if r["owner"] == owner and r["decision"] is None
    ]


def _gc():
    now = time.time()
    expired = [t for t, r in _pending.items() if now - r["created_at"] > APPROVAL_TTL_SECONDS]
    for t in expired:
        del _pending[t]
