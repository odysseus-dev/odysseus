"""Server-side tool safety policy."""

from __future__ import annotations

import logging
from typing import Optional, Set

logger = logging.getLogger(__name__)


# Tools regular/public users must not execute directly. These either expose
# server/runtime access, sensitive user data, external messaging, persistent
# state changes, or generic loopback/integration surfaces.
NON_ADMIN_BLOCKED_TOOLS = {
    "bash",
    "python",
    "read_file",
    "write_file",
    "edit_file",
    "grep",
    "glob",
    "ls",
    "search_chats",
    "manage_memory",
    "manage_skills",
    "manage_tasks",
    "manage_endpoints",
    "manage_mcp",
    "manage_webhooks",
    "manage_tokens",
    "manage_documents",
    "manage_settings",
    "api_call",
    "app_api",
    "send_email",
    "reply_to_email",
    "list_emails",
    "read_email",
    "resolve_contact",
    "manage_contact",
    "manage_calendar",
    "vault_search",
    "vault_get",
    "vault_unlock",
    "download_model",
    "serve_model",
    "serve_preset",
    "stop_served_model",
    "cancel_download",
    "adopt_served_model",
}


# Tools whose OUTPUT is externally controllable (web pages, emails, files an
# attacker may influence). Their results are wrapped as untrusted data before
# being returned to the model. Plan 0059 audit C1.
UNTRUSTED_RESULT_TOOLS = {
    "web_fetch", "web_search", "fetch_url",
    "read_email", "list_emails", "search_email",
    "read_file", "grep", "glob", "ls",
}

# Tools that exfiltrate, execute code, or make persistent/irreversible changes.
# Plan 0059 audit C2.
HIGH_RISK_TOOLS = {
    "bash", "python",
    "write_file", "edit_file",
    "send_email", "reply_to_email", "bulk_email",
    "manage_settings", "manage_tokens", "manage_webhooks",
    "manage_mcp", "manage_skills",
    "api_call", "app_api",
}


def is_untrusted_result_tool(tool_name: Optional[str]) -> bool:
    return isinstance(tool_name, str) and tool_name in UNTRUSTED_RESULT_TOOLS


def is_high_risk_tool(tool_name: Optional[str]) -> bool:
    """Whether a tool needs confirmation under the high-risk gate. Fails CLOSED:
    a non-string name is treated as high-risk."""
    if tool_name is None or tool_name == "":
        return False
    if not isinstance(tool_name, str):
        return True
    return tool_name in HIGH_RISK_TOOLS


def highrisk_confirm_enabled() -> bool:
    """True when this deployment requires human confirmation for high-risk tools
    (AGENT_HIGHRISK_REQUIRE_CONFIRM). Off by default to preserve upstream behavior."""
    import os
    return os.environ.get("AGENT_HIGHRISK_REQUIRE_CONFIRM", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def is_public_blocked_tool(tool_name: Optional[str]) -> bool:
    """Return True when a non-admin/public user must not execute this tool.

    This is a security gate, so it fails CLOSED: a malformed non-string tool
    name can't be matched against the blocklist or the ``mcp__`` namespace, so
    it is treated as blocked rather than silently allowed through. ``None`` /
    empty string means there is no tool to gate.
    """
    if tool_name is None or tool_name == "":
        return False
    if not isinstance(tool_name, str):
        return True
    return tool_name in NON_ADMIN_BLOCKED_TOOLS or tool_name.startswith("mcp__")


def owner_is_admin_or_single_user(owner: Optional[str]) -> bool:
    """Return True for admins, or when auth is not configured yet."""
    try:
        from core.auth import AuthManager

        auth = AuthManager()
        if not auth.is_configured:
            return True
        return bool(owner and auth.is_admin(owner))
    except Exception as exc:
        logger.warning("Unable to evaluate owner admin status: %s", exc)
        return False


def blocked_tools_for_owner(owner: Optional[str]) -> Set[str]:
    """Tools to hide/disable for this owner under public-user policy."""
    if owner_is_admin_or_single_user(owner):
        return set()
    return set(NON_ADMIN_BLOCKED_TOOLS)
