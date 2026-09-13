"""Inline approval for risky tool calls.

A chat's approval mode decides which tool calls stop and ask first:

* ``auto``      — nothing asks (the historical behaviour).
* ``ask_risky`` — destructive or outward-facing actions ask: destructive shell
  commands, ``git push``/publishing, sending or deleting email, deleting files.
* ``ask_all``   — every tool that can change something asks (plan mode's
  mutator list plus the shell).

Asking does not hold the turn open. Like ``ask_user``, the gated call ends the
turn with an approval card (the pending call is recorded here); the user's
decision is recorded as a grant and sent back as the next message, and the
agent re-issues the call, which the grant then lets through. A held-open
generator would keep the run "running" and vanish on a server restart.

Only interactive chat turns pass an approval mode; background tasks and
sub-agents never prompt (nobody would answer).
"""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from typing import Dict, Optional

MODES = ("auto", "ask_risky", "ask_all")
DEFAULT_MODE = "auto"
_PENDING_TTL_S = 24 * 3600
_ONCE_TTL_S = 30 * 60

_SHELL_TOOLS = {"bash", "python"}

# Shell commands that destroy data, rewrite history, publish, or escalate.
_RISKY_SHELL = [
    (re.compile(r"\brm\s+(?:-[a-zA-Z]*[rf][a-zA-Z]*\s+)+"), "deletes files recursively or forcibly"),
    (re.compile(r"\bgit\s+push\b"), "pushes to a remote"),
    (re.compile(r"\bgit\s+(?:reset\s+--hard|clean\s+-[a-zA-Z]*f|checkout\s+--\s|branch\s+-D|stash\s+(?:drop|clear))"),
     "discards git changes"),
    (re.compile(r"\bgit\s+rebase\b|\bgit\s+commit\s+[^;&|]*--amend\b"), "rewrites git history"),
    (re.compile(r"\b(?:npm|pnpm|yarn)\s+publish\b|\btwine\s+upload\b|\bgh\s+(?:pr\s+merge|release\s+create|repo\s+delete)\b"),
     "publishes or merges"),
    (re.compile(r"\bsudo\b|\bchmod\s+-R\b|\bchown\s+-R\b"), "changes system permissions"),
    (re.compile(r"\b(?:mkfs|fdisk|parted)\b|\bdd\s+[^;&|]*\bof=|>\s*/dev/sd"), "writes to a disk device"),
    (re.compile(r"\b(?:shutdown|reboot|halt|poweroff)\b"), "shuts the machine down"),
    (re.compile(r"\bdocker\s+(?:rm|rmi|system\s+prune|volume\s+(?:rm|prune))\b|\bkubectl\s+delete\b"), "removes containers or resources"),
    (re.compile(r"\b(?:curl|wget)\b[^|;&]*\|\s*(?:ba|z)?sh\b"), "pipes a download into a shell"),
    (re.compile(r"\bDROP\s+(?:TABLE|DATABASE)\b|\bTRUNCATE\s+TABLE\b", re.I), "drops database data"),
    (re.compile(r"\bkill\s+-9\b|\bpkill\b|\bkillall\b"), "kills processes"),
    (re.compile(r"shutil\.rmtree|os\.remove\(|os\.unlink\("), "deletes files"),
]

# Non-shell tools that act on the outside world or destroy data.
_RISKY_TOOLS = {
    "send_email": "sends an email",
    "reply_to_email": "sends an email reply",
    "bulk_email": "acts on many emails at once",
    "delete_email": "deletes email",
    "unsubscribe_email": "unsubscribes from a mailing list",
    "mcp__email__send_email": "sends an email",
    "mcp__email__reply_to_email": "sends an email reply",
    "mcp__email__bulk_email": "acts on many emails at once",
    "mcp__email__delete_email": "deletes email",
    "mcp__github_write__create_pull_request": "opens a pull request",
    "manage_webhooks": "changes webhooks",
    "manage_tokens": "changes API tokens",
    "manage_settings": "changes app settings",
    "manage_endpoints": "changes model endpoints",
}

_DELETE_ACTION_RE = re.compile(r'"action"\s*:\s*"(?:delete|remove|purge|clear)', re.I)


def normalize_mode(value: Optional[str]) -> str:
    value = str(value or "").strip().lower()
    return value if value in MODES else DEFAULT_MODE


def approval_reason(tool: str, content: str, mode: str) -> Optional[str]:
    """Why this call needs approval under ``mode``, or None to let it run."""
    mode = normalize_mode(mode)
    if mode == "auto":
        return None
    tool = str(tool or "")
    text = str(content or "")
    if tool in _SHELL_TOOLS:
        for pattern, why in _RISKY_SHELL:
            if pattern.search(text):
                return why
        return "runs a shell command" if mode == "ask_all" else None
    if tool in _RISKY_TOOLS:
        return _RISKY_TOOLS[tool]
    if tool.startswith("manage_") and _DELETE_ACTION_RE.search(text):
        return "deletes data"
    if mode == "ask_all":
        from src.tool_security import _PLAN_MODE_KNOWN_MUTATORS

        if tool in _PLAN_MODE_KNOWN_MUTATORS or tool.startswith("mcp__github_write__"):
            return "changes files or data"
    return None


def _key(tool: str, content: str) -> str:
    normalized = " ".join(str(content or "").split())
    return hashlib.sha256(f"{tool}\x00{normalized}".encode("utf-8", "replace")).hexdigest()[:32]


_PENDING: Dict[str, dict] = {}
_GRANTS: Dict[str, dict] = {}   # session_id -> {"once": {key: expires_at}, "tools": set()}


def _prune(now: float) -> None:
    for pid in [pid for pid, rec in _PENDING.items() if now - rec["created_at"] > _PENDING_TTL_S]:
        _PENDING.pop(pid, None)
    for grants in _GRANTS.values():
        for key in [k for k, exp in grants["once"].items() if exp < now]:
            grants["once"].pop(key, None)


def request(session_id: str, tool: str, content: str, reason: str) -> dict:
    """Record a call waiting for approval; returns the pending record."""
    now = time.time()
    _prune(now)
    pid = uuid.uuid4().hex[:12]
    rec = {"id": pid, "session_id": session_id, "tool": tool, "command": str(content or "")[:4000],
           "reason": reason, "key": _key(tool, content), "created_at": now, "decision": None}
    _PENDING[pid] = rec
    return rec


def get_pending(pid: str) -> Optional[dict]:
    return _PENDING.get(pid)


def pending_for(session_ids) -> list:
    """Undecided requests for these chats, oldest first."""
    _prune(time.time())
    ids = set(session_ids or ())
    rows = [dict(rec) for rec in _PENDING.values() if rec["session_id"] in ids and rec["decision"] is None]
    rows.sort(key=lambda r: r["created_at"])
    return rows


def decide(session_id: str, pid: str, decision: str) -> Optional[dict]:
    """Apply the user's decision: ``once``, ``always`` (this tool in this chat)
    or ``deny``. Returns the updated record, or None when unknown."""
    rec = _PENDING.get(pid)
    if rec is None or rec["session_id"] != session_id:
        return None
    if decision not in ("once", "always", "deny"):
        raise ValueError("decision must be once, always or deny")
    rec["decision"] = decision
    grants = _GRANTS.setdefault(session_id, {"once": {}, "tools": set()})
    if decision == "once":
        grants["once"][rec["key"]] = time.time() + _ONCE_TTL_S
    elif decision == "always":
        grants["tools"].add(rec["tool"])
    return rec


def consume_grant(session_id: str, tool: str, content: str) -> bool:
    """True when the user already approved this call (a one-time grant is used
    up) or approved the tool for the whole chat."""
    grants = _GRANTS.get(session_id or "")
    if not grants:
        return False
    if tool in grants["tools"]:
        return True
    key = _key(tool, content)
    expires = grants["once"].get(key)
    if expires and expires >= time.time():
        grants["once"].pop(key, None)
        return True
    return False


def chat_grants(session_id: str) -> list:
    grants = _GRANTS.get(session_id or "")
    return sorted(grants["tools"]) if grants else []


def revoke_chat_grants(session_id: str) -> None:
    _GRANTS.pop(session_id or "", None)


def _reset_for_tests() -> None:
    _PENDING.clear()
    _GRANTS.clear()
