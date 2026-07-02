"""Server-side tool safety policy."""

from __future__ import annotations

import logging
from typing import Optional, Set

logger = logging.getLogger(__name__)


# Every tool exposed by the built-in email MCP server
# (mcp_servers/email_server.py). Single source of truth: the fence tags
# (TOOL_TAGS), bare-name dispatch (tool_execution), native-call mapping
# (tool_schemas), and the non-admin blocklist below all derive from this set,
# so a tool added to the email server can't become reachable under its bare
# name without also being blocked for non-admins.
BUILTIN_EMAIL_TOOLS = frozenset({
    "list_email_accounts",
    "list_emails",
    "read_email",
    "search_emails",
    "send_email",
    "reply_to_email",
    "draft_email",
    "draft_email_reply",
    "ai_draft_email_reply",
    "archive_email",
    "delete_email",
    "mark_email_read",
    "bulk_email",
    "download_attachment",
})


# Tools regular/public users must not execute directly. These either expose
# server/runtime access, sensitive user data, external messaging, persistent
# state changes, or generic loopback/integration surfaces. All email tools are
# included (SECURITY.md: email/MCP capabilities are privileged admin
# functionality).
NON_ADMIN_BLOCKED_TOOLS = BUILTIN_EMAIL_TOOLS | {
    "bash",
    "python",
    "manage_bg_jobs",
    "read_file",
    "write_file",
    "edit_file",
    "grep",
    "glob",
    "ls",
    "get_workspace",
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


# Plan mode: the agent may investigate but must not mutate anything. Only these
# read-only/inspection tools stay enabled; everything else (writes, sends,
# manage_*, model serving, MCP, etc.) is blocked. Allowlist rather than blocklist
# so any newly added tool defaults to BLOCKED in plan mode — fail safe.
#
# bash/python are deliberately NOT here: the shell can mutate (write files, hit
# the network) and can't be constrained to read-only at the tool layer, so plan
# mode blocks it outright rather than relying on a prompt to keep it well-behaved.
# Code/file discovery is covered by the dedicated read-only tools below
# (read_file, grep, glob, ls) instead of freestyle shell.
PLAN_MODE_READONLY_TOOLS = {
    "read_file",
    "grep",
    "glob",
    "ls",
    "get_workspace",
    "web_search",
    "web_fetch",
    "search_chats",
    "list_models",
    "list_sessions",
    # Read-only email tools. list_email_accounts must be here because the
    # bare/qualified alias gate in execute_tool_block works both ways: it has
    # a native function schema, so plan mode's schema-derived bare denylist
    # contains it — and without this allowlist entry that bare entry would
    # also block the qualified mcp__email__list_email_accounts call that the
    # MCP read-only filter deliberately allows.
    "list_email_accounts",
    "list_emails",
    "read_email",
    # Explicitly read-only rather than allowed-by-omission: this PR makes
    # every BUILTIN_EMAIL_TOOLS name fence-taggable, so each one must be
    # classified — see the plan-mode partition test in
    # tests/test_email_registry_sync.py.
    "search_emails",
    "list_served_models",
    "list_downloads",
    "list_cached_models",
    "search_hf_models",
    "list_serve_presets",
    "list_cookbook_servers",
    "resolve_contact",
    "chat_with_model",
    "ask_teacher",
}


# The agent's tool gate is a DENYLIST: execute_tool_block blocks any tool whose
# name is in `disabled_tools`. Plan mode's policy is the opposite — an allowlist
# (PLAN_MODE_READONLY_TOOLS). To apply an allowlist through a denylist, plan mode
# returns the inverse: every known tool name minus the allowlist.
#
# Known tool names come from FUNCTION_TOOL_SCHEMAS, but that source is imperfect:
# some tools are only XML-invocable (e.g. manage_notes, generate_image) and never
# appear there, and the import can fail outright. Either gap would drop a mutating
# tool from the subtraction and silently leave it enabled. This set is the static
# backstop for both: union it in so known mutators are always subtracted, and so a
# failed import still blocks them (fail closed, never open). Only mutators belong
# here — read-only tools are covered by the allowlist. Keep in sync when adding
# new mutating tools.
_PLAN_MODE_KNOWN_MUTATORS = {
    "write_file", "create_document", "edit_document", "update_document",
    "suggest_document", "manage_documents", "create_session", "manage_session",
    "send_to_session", "pipeline", "manage_memory", "manage_skills",
    "manage_tasks", "manage_notes", "manage_endpoints", "manage_mcp",
    "manage_webhooks", "manage_tokens", "manage_settings", "manage_contact",
    "manage_calendar", "api_call", "app_api", "ui_control",
    "send_email", "reply_to_email", "bulk_email", "delete_email",
    "archive_email", "mark_email_read",
    # The draft tools create documents and download_attachment writes to
    # disk — mutating. They have no native schemas (yet), so without these
    # static entries plan-mode safety for their bare fence tags would depend
    # entirely on the MCP read-only inventory being present and current.
    "draft_email", "draft_email_reply", "ai_draft_email_reply",
    "download_attachment",
    "download_model", "serve_model",
    "stop_served_model", "cancel_download", "adopt_served_model", "serve_preset",
    "generate_image", "edit_image", "trigger_research", "manage_research",
    # Shell is never read-only-safe; block it explicitly so it stays out of plan
    # mode even if the schema list fails to load.
    "bash", "python",
    # Controls shell processes (kill); plan mode can't run bash anyway.
    "manage_bg_jobs",
}


def plan_mode_disabled_tools() -> Set[str]:
    """Tool names to add to the denylist in plan mode.

    Plan mode allows only PLAN_MODE_READONLY_TOOLS. The gate is a denylist, so
    return the inverse: every known tool name minus the allowlist. Known names
    come from the function-tool schemas, backstopped by _PLAN_MODE_KNOWN_MUTATORS
    (see above) so XML-only tools and a failed schema import can't leave a mutator
    enabled. MCP tools are handled separately — the loop drops the MCP manager
    entirely in plan mode."""
    try:
        # agent_tools / tool_parsing / tool_schemas form a mutually-circular
        # cluster that only resolves cleanly when entered via agent_tools.
        # Import it first so the lazy schema import works even from a cold
        # import (e.g. tests) — not just after the app has wired everything up.
        import src.agent_tools  # noqa: F401
        from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

        all_names = {
            (t.get("function") or {}).get("name")
            for t in FUNCTION_TOOL_SCHEMAS
        }
        all_names.discard(None)
    except Exception as exc:
        logger.warning("Unable to load tool schemas for plan-mode gating: %s", exc)
        all_names = set()
    # Subtract the allowlist from all known tool names (schema-derived plus the
    # static mutator backstop). Fail closed: if the schema import failed above,
    # the backstop alone still blocks known mutators.
    return (all_names | _PLAN_MODE_KNOWN_MUTATORS) - PLAN_MODE_READONLY_TOOLS


def email_tool_policy_names(tool_name: str) -> frozenset:
    """All policy-equivalent spellings of a tool name.

    A bare built-in email tool name and its MCP-qualified mcp__email__<name>
    form dispatch to the same email server tool, but policy sources spell
    them either way — plan mode and the MCP settings toggle write qualified
    names into denylists, chat-level toggles write bare ones. Every gate must
    match against the full alias set, or a call in one spelling slips past a
    denylist entry written in the other. Non-email names alias only to
    themselves.
    """
    if not isinstance(tool_name, str):
        return frozenset((tool_name,))
    if tool_name in BUILTIN_EMAIL_TOOLS:
        return frozenset((tool_name, f"mcp__email__{tool_name}"))
    if tool_name.startswith("mcp__email__"):
        bare = tool_name[len("mcp__email__"):]
        if bare in BUILTIN_EMAIL_TOOLS:
            return frozenset((tool_name, bare))
    return frozenset((tool_name,))


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
    """Return True for admins, or in intentional single-user mode.

    Single-user mode means the operator explicitly disabled auth
    (``AUTH_ENABLED=false``) — the local/self-host default where the owner has
    full access to their own box.

    The pre-setup window (auth ENABLED but no admin created yet) is treated as
    NON-admin: returning True there would hand server-execution tools
    (``bash``/``python``) to any caller before setup completes. The auth
    middleware already 401s ``/api/`` requests pre-setup, so this is
    defense-in-depth for callers that bypass it (e.g. trusted loopback).
    """
    try:
        from src.auth_helpers import _auth_disabled

        if _auth_disabled():
            return True

        from core.auth import AuthManager

        auth = AuthManager()
        if not auth.is_configured:
            return False
        return bool(owner and auth.is_admin(owner))
    except Exception as exc:
        logger.warning("Unable to evaluate owner admin status: %s", exc)
        return False


def blocked_tools_for_owner(owner: Optional[str]) -> Set[str]:
    """Tools to hide/disable for this owner under public-user policy."""
    if owner_is_admin_or_single_user(owner):
        return set()
    return set(NON_ADMIN_BLOCKED_TOOLS)
