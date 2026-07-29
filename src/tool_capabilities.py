"""Server-owned capability classification for agent tools.

This is deliberately independent from prompts and UI mode switches.  A tool
implementation declares its effects here; a policy decides whether those
effects may run for a particular agent run.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolCapability:
    """Effects relevant to the authority boundary."""

    effects: frozenset[str]


SAFE = ToolCapability(frozenset({"observe"}))

# These tools only return server-owned, non-sensitive state or communicate a
# question/plan to the user.  Everything not explicitly listed is fail-closed
# once untrusted context has entered a run.
_SAFE_TOOL_NAMES = frozenset({
    "ask_user", "update_plan", "list_models", "list_sessions",
    "list_downloads", "list_cached_models", "list_served_models",
    "list_serve_presets", "list_cookbook_servers",
})

_CAPABILITIES: dict[str, ToolCapability] = {
    name: SAFE for name in _SAFE_TOOL_NAMES
}


def _declare(names: set[str], *effects: str) -> None:
    capability = ToolCapability(frozenset(effects))
    for name in names:
        _CAPABILITIES[name] = capability


_declare({"bash", "python", "manage_bg_jobs"}, "process", "filesystem_write")
_declare({"read_file", "grep", "glob", "ls", "get_workspace"}, "filesystem_read")
_declare({"write_file", "edit_file", "apply_patch", "todowrite"}, "filesystem_write")
_declare({"web_search", "web_fetch", "api_call", "app_api", "builtin_browser"}, "network")
_declare({"send_email", "reply_to_email", "bulk_email", "archive_email", "delete_email",
          "mark_email_read", "unsubscribe_email", "draft_email", "draft_email_reply",
          "ai_draft_email_reply", "send_to_session"}, "messaging")
_declare({"read_email", "list_emails", "search_emails", "list_email_accounts",
          "download_attachment", "search_chats", "vault_search", "vault_get",
          "vault_unlock", "resolve_contact"}, "private_data")
_declare({"manage_memory", "manage_skills", "manage_tasks", "manage_notes",
          "manage_calendar", "manage_documents", "create_document", "update_document",
          "edit_document", "suggest_document", "manage_contact", "manage_session",
          "create_session", "manage_endpoints", "manage_mcp", "manage_webhooks",
          "manage_tokens", "manage_settings", "download_model", "serve_model",
          "serve_preset", "stop_served_model", "cancel_download", "adopt_served_model",
          "generate_image", "edit_image", "trigger_research", "manage_research",
          "pipeline", "ui_control"}, "state_change")
_declare({"ask_teacher", "chat_with_model"}, "external_model")


def capability_for(tool_name: object) -> ToolCapability | None:
    """Return the declared capability, or ``None`` for an unknown tool.

    MCP names are intentionally not inferred from their spelling.  An MCP
    server is external authority, so it must register a capability before it
    can run after untrusted context has been observed.
    """
    if not isinstance(tool_name, str) or not tool_name:
        return None
    return _CAPABILITIES.get(tool_name)


def blocked_after_external_context(tool_name: object) -> bool:
    """Whether a tool is disallowed by the conservative external-context gate."""
    capability = capability_for(tool_name)
    return capability is None or bool(capability.effects - {"observe"})
