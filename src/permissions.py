"""
permissions.py — mode-based tool permission policy (the "permission pipeline").

One pure function, ``evaluate(mode, tool, content)``, decides whether a tool call
may run, must be denied, or needs explicit user approval — from the active
composer mode and a classification of the tool as read-only / edit / mutating.
The agent loop calls this before executing each tool block; per-user privilege +
admin gates stay in ``execute_tool_block`` as a backstop (this layer is only the
mode policy, so the two compose).

Modes (autonomy ladder; the composer's Shift+Tab steps through in this order):

==============  =========================================================
``chat``        no tools (handled upstream; here: deny all, defensively)
``plan``        read-only tools only; never mutates — investigate + propose
``manual``      every edit/mutation pauses for approval
``accept_edits``  file/doc edits auto-approved; other mutations still pause
``agent``       full autonomy (allow all)
==============  =========================================================

Tool classes:

``read``    no side effects (web_search, read_file, list_*, read_email, …)
``edit``    file/document edits — the ``accept_edits`` auto-approve bucket
``mutate``  any other state change (bash, python, send_email, manage_*, …)

CRUD-style tools (``manage_*`` etc.) are reclassified per call: if the call's
action verb is a read (list/get/read/search/…) it counts as ``read``, so plan
mode and read-heavy flows aren't needlessly blocked. Anything unrecognized
defaults to ``mutate`` (the safe bias).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

# Ordered for Shift+Tab cycling in the composer (chat … agent = rising autonomy).
MODES = ("chat", "plan", "manual", "accept_edits", "agent")
DEFAULT_MODE = "agent"

# --- tool classification ----------------------------------------------------
READ_ONLY_TOOLS = {
    "web_search", "web_fetch", "read_file", "search_chats", "list_sessions",
    "list_models", "list_email_accounts", "list_emails", "read_email",
    "list_served_models", "list_downloads", "search_hf_models",
    "list_cached_models", "list_serve_presets", "list_cookbook_servers",
    "resolve_contact", "suggest_document",
}

# The "edit" bucket = scoped document edits that accept_edits auto-approves.
# File-write tools (write_file/edit_file) are deliberately NOT here: they take
# arbitrary paths with no workspace sandbox on this branch, so they stay
# `mutate` (approval required outside agent mode).
EDIT_TOOLS = {
    "create_document", "update_document", "edit_document",
}

# CRUD-style tools whose action verb decides read-vs-mutate per call.
ACTION_TOOLS = {
    "manage_memory", "manage_tasks", "manage_notes", "manage_calendar",
    "manage_documents", "manage_contact", "manage_session", "manage_skills",
    "manage_endpoints", "manage_mcp", "manage_webhooks", "manage_tokens",
    "manage_settings", "manage_research",
}

READ_ACTIONS = {
    "list", "get", "read", "search", "view", "show", "find", "fetch",
    "describe", "status", "count", "export", "preview",
}

READ = "read"
EDIT = "edit"
MUTATE = "mutate"

# policy[mode][class] -> "allow" | "deny" | "approve"
ALLOW, DENY, APPROVE = "allow", "deny", "approve"
_POLICY = {
    "chat":         {READ: DENY,  EDIT: DENY,    MUTATE: DENY},
    "plan":         {READ: ALLOW, EDIT: DENY,    MUTATE: DENY},
    "manual":       {READ: ALLOW, EDIT: APPROVE, MUTATE: APPROVE},
    "accept_edits": {READ: ALLOW, EDIT: ALLOW,   MUTATE: APPROVE},
    "agent":        {READ: ALLOW, EDIT: ALLOW,   MUTATE: ALLOW},
}

_PLAN_DENY_REASON = (
    "Plan mode is read-only — describe this step in your plan instead of running "
    "it. The user can switch to Manual/Accept-Edits/Agent to let you execute."
)


@dataclass(frozen=True)
class Decision:
    action: str            # "allow" | "deny" | "approve"
    tool_class: str        # "read" | "edit" | "mutate"
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.action == ALLOW

    @property
    def needs_approval(self) -> bool:
        return self.action == APPROVE


def _action_of(content: str) -> Optional[str]:
    """Best-effort extract an action verb from a CRUD tool's JSON content."""
    try:
        data = json.loads(content)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    for key in ("action", "operation", "op", "command", "cmd", "method"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
    return None


def classify(tool: str, content: str = "") -> str:
    """Return ``read`` / ``edit`` / ``mutate`` for a tool call."""
    if tool in READ_ONLY_TOOLS:
        return READ
    if tool in EDIT_TOOLS:
        return EDIT
    if tool in ACTION_TOOLS:
        act = _action_of(content)
        if act:
            head = act.split("_", 1)[0]          # "list_models" -> "list"
            if act in READ_ACTIONS or head in READ_ACTIONS:
                return READ
        return MUTATE
    return MUTATE


def evaluate(mode: str, tool: str, content: str = "") -> Decision:
    """Decide allow / deny / approve for a tool call under ``mode``."""
    m = mode if mode in _POLICY else "manual"   # unknown -> safest enforced posture
    cls = classify(tool, content)
    action = _POLICY[m][cls]
    if action == DENY:
        reason = _PLAN_DENY_REASON if m == "plan" else f"'{tool}' is not allowed in {m} mode."
    elif action == APPROVE:
        reason = f"{m} mode: '{tool}' ({cls}) needs your approval before it runs."
    else:
        reason = ""
    return Decision(action=action, tool_class=cls, reason=reason)
