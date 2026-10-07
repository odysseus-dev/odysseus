"""Dependency-neutral types shared by tool parsing and the tool facade."""

from collections import namedtuple

from src.tool_security import BUILTIN_EMAIL_TOOLS


ToolBlock = namedtuple("ToolBlock", ["tool_type", "content"])

# Keep this registry free of imports from ``src.agent_tools``.  The parser is
# a public low-level module and must be importable without initializing the
# facade, whose backwards-compatible re-exports include the parser itself.
TOOL_TAGS = {
    "bash", "host_shell", "python", "web_search", "web_fetch", "get_weather", "pdf_extract", "youtube_tool", "private_browser", "inspect_media", "extract_text", "transcribe_media", "read_file", "write_file", "edit_file",
    "apply_patch", "todowrite",
    "grep", "glob", "ls", "get_workspace", "manage_bg_jobs",
    "create_document", "update_document", "edit_document",
    "search_chats",
    "chat_with_model", "create_session", "list_sessions",
    "send_to_session", "pipeline", "manage_session", "manage_memory", "list_models",
    "ui_control", "generate_image", "ask_user", "update_plan",
    "manage_tasks", "api_call", "ask_teacher", "manage_skills",
    "suggest_document",
    "manage_endpoints", "manage_mcp", "manage_webhooks",
    "manage_tokens", "manage_documents", "manage_settings",
    "manage_notes", "manage_calendar", "resolve_contact", "manage_contact",
    "download_model", "serve_model", "list_served_models", "stop_served_model",
    "tail_serve_output", "list_downloads", "cancel_download", "search_hf_models",
    "list_cached_models", "list_serve_presets", "serve_preset", "adopt_served_model",
    "list_cookbook_servers", "edit_image", "trigger_research", "manage_research",
    "app_api",
} | BUILTIN_EMAIL_TOOLS
