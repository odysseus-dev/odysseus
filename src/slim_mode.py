"""
slim_mode.py

Context-aware tool filtering for agent mode.

The full tool set (60 schemas, ~20K tokens) overwhelms smaller local models
whose context windows are 4K-32K tokens.  This module detects the effective
context window and applies tiered reductions:

  - Full    (>32K):  all tools, full descriptions          (backwards-compatible)
  - Large   (16-32K): top ~20 tools, full descriptions
  - Medium  (8-16K):  top ~12 tools, short descriptions
  - Small   (4-8K):   top ~8 tools, minimal descriptions
  - Tiny    (<4K):    top ~5 tools, minimal descriptions

"Top" is determined by a static priority ranking that mirrors real-world
usage frequency.  RAG/tool-index retrieval still runs first; slim mode
further caps the result set and (for smaller tiers) swaps in shorter
tool descriptions so the model has room to think.
"""

import logging
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ── Context tiers ────────────────────────────────────────────────────────
# (name, max_context, max_tools, description_style)
# description_style: "full" | "short" | "minimal"
TIERS = [
    ("full",   999_999_999, 60, "full"),
    ("large",      32_768,  20, "full"),
    ("medium",     16_384,  12, "short"),
    ("small",       8_192,   8, "minimal"),
    ("tiny",        4_096,   5, "minimal"),
]

# ── Tool priority ranking ────────────────────────────────────────────────
# Ordered by real-world usage frequency.  Tools at the top survive slim
# filtering; tools at the bottom are dropped first.
TOOL_PRIORITY = [
    # Tier 0 — core (always survive even tiny contexts)
    "bash",
    "read_file",
    "write_file",
    "create_document",
    "edit_document",
    # Tier 1 — high-frequency
    "web_search",
    "update_document",
    "manage_memory",
    "python",
    "web_fetch",
    # Tier 2 — common
    "ui_control",
    "manage_notes",
    "search_chats",
    "list_sessions",
    "manage_session",
    "send_email",
    "list_emails",
    "read_email",
    "manage_calendar",
    "suggest_document",
    # Tier 3 — moderate
    "manage_tasks",
    "trigger_research",
    "list_email_accounts",
    "reply_to_email",
    "bulk_email",
    "chat_with_model",
    "ask_teacher",
    "list_models",
    "generate_image",
    "manage_documents",
    "resolve_contact",
    "manage_contact",
    "manage_settings",
    "app_api",
    # Tier 4 — infrequent / admin
    "delete_email",
    "archive_email",
    "mark_email_read",
    "create_session",
    "send_to_session",
    "pipeline",
    "manage_endpoints",
    "manage_mcp",
    "manage_webhooks",
    "manage_tokens",
    "manage_skills",
    "download_model",
    "serve_model",
    "list_served_models",
    "stop_served_model",
    "list_downloads",
    "cancel_download",
    "search_hf_models",
    "list_cached_models",
    "list_serve_presets",
    "serve_preset",
    "adopt_served_model",
    "list_cookbook_servers",
    "edit_image",
    "manage_gallery",
    "generate_speech",
    "transcribe_audio",
    "manage_research",
]

# ── Short tool descriptions (for medium/small/tiny tiers) ──────────────
# Significantly shorter than the full TOOL_SECTIONS text to save tokens.
SHORT_DESCRIPTIONS: Dict[str, str] = {
    "bash": "Run shell commands. Output returned.",
    "python": "Execute Python code.",
    "web_search": "Quick web lookup. Args: query.",
    "web_fetch": "Fetch URL text content.",
    "read_file": "Read a file from disk.",
    "write_file": "Write content to a file.",
    "create_document": "Create a new document in editor. Args: title, language, content.",
    "edit_document": "Find-and-replace edits on active doc. Args: edits array with find/replace.",
    "update_document": "Replace entire active document. Only for full rewrites.",
    "suggest_document": "Suggest changes without editing. Args: suggestions array.",
    "manage_memory": "Manage persistent memories. Actions: list/add/edit/delete/search.",
    "manage_notes": "Notes, todos, reminders. Accepts natural-language due_date.",
    "manage_calendar": "Calendar events. Actions: list_events/create_event/update_event/delete_event.",
    "manage_tasks": "Scheduled background tasks. Actions: list/create/edit/delete/pause/resume/run.",
    "manage_session": "Rename/archive/delete/fork chats. Actions: list/rename/archive/delete/fork.",
    "list_sessions": "List chats (most-recent first). Optional filter keyword.",
    "search_chats": "Search past chat history by keyword.",
    "send_email": "Send email. Args: to, subject, body, account.",
    "list_emails": "List emails newest-first. Args: folder, max_results, account.",
    "read_email": "Read email by UID. Args: uid, folder, account.",
    "reply_to_email": "Send reply by UID. Args: uid, body, account.",
    "bulk_email": "Bulk email actions. Args: action, uids, account.",
    "delete_email": "Delete email by UID.",
    "archive_email": "Archive email by UID.",
    "mark_email_read": "Mark email read/unread by UID.",
    "list_email_accounts": "List configured email accounts.",
    "resolve_contact": "Look up contact email by name.",
    "manage_contact": "CRUD CardDAV contacts. Actions: list/add/update/delete.",
    "chat_with_model": "Ask another AI model. Args: model, message.",
    "ask_teacher": "Escalate to capable model. Args: problem.",
    "list_models": "List available AI models.",
    "ui_control": "Toggle tools, open panels, set themes, switch models.",
    "manage_documents": "List/delete/tidy documents. Actions: list/delete/tidy.",
    "manage_settings": "View/change app settings, toggle tools on/off.",
    "manage_skills": "Skill registry. Actions: list/view/add/edit/publish/delete.",
    "trigger_research": "Start deep research job. Args: topic.",
    "manage_endpoints": "Manage model API endpoints.",
    "manage_mcp": "Manage MCP tool servers.",
    "manage_webhooks": "Manage webhook endpoints.",
    "manage_tokens": "Manage API access tokens.",
    "generate_image": "Generate AI image. Args: prompt, model, size.",
    "app_api": "Generic loopback to any Odysseus /api/* endpoint.",
    "pipeline": "Multi-step AI pipeline. Args: steps array.",
    "create_session": "Create a new chat. Args: name, model.",
    "send_to_session": "Send message to another chat.",
    "download_model": "Download HuggingFace model. Args: repo_id.",
    "serve_model": "Start model server (vLLM/SGLang/llama.cpp/Ollama).",
    "list_served_models": "List running model servers.",
    "stop_served_model": "Stop a running model server.",
    "list_downloads": "List in-progress model downloads.",
    "cancel_download": "Cancel in-progress download.",
    "search_hf_models": "Search HuggingFace for models.",
    "list_cached_models": "List models on disk.",
    "list_serve_presets": "List saved serve presets.",
    "serve_preset": "Launch a saved serve preset.",
    "adopt_served_model": "Register existing tmux server into Cookbook.",
    "list_cookbook_servers": "List Cookbook configured servers.",
    "edit_image": "Edit gallery image: upscale/rembg/inpaint/harmonize.",
    "manage_gallery": "List/search/delete images in gallery.",
    "generate_speech": "Text-to-speech. Args: text.",
    "transcribe_audio": "Speech-to-text. Args: audio_path.",
    "manage_research": "List/read/delete saved research results.",
}

# ── Minimal tool descriptions (for small/tiny tiers) ──────────────────
# Even shorter — just the tool name purpose in under 10 words.
MINIMAL_DESCRIPTIONS: Dict[str, str] = {
    "bash": "Run shell command.",
    "python": "Execute Python code.",
    "web_search": "Web search query.",
    "web_fetch": "Fetch URL content.",
    "read_file": "Read file from disk.",
    "write_file": "Write file to disk.",
    "create_document": "Create editor document.",
    "edit_document": "Find-replace in active doc.",
    "update_document": "Replace entire active doc.",
    "suggest_document": "Suggest doc changes.",
    "manage_memory": "Persistent memory CRUD.",
    "manage_notes": "Notes and reminders.",
    "manage_calendar": "Calendar events.",
    "manage_tasks": "Scheduled tasks.",
    "manage_session": "Manage chats.",
    "list_sessions": "List chats.",
    "search_chats": "Search chat history.",
    "send_email": "Send email.",
    "list_emails": "List emails.",
    "read_email": "Read email by UID.",
    "reply_to_email": "Reply to email.",
    "bulk_email": "Bulk email action.",
    "delete_email": "Delete email.",
    "archive_email": "Archive email.",
    "mark_email_read": "Mark email read.",
    "list_email_accounts": "List email accounts.",
    "resolve_contact": "Look up contact email.",
    "manage_contact": "Manage contacts.",
    "chat_with_model": "Ask another model.",
    "ask_teacher": "Ask capable model.",
    "list_models": "List AI models.",
    "ui_control": "UI control.",
    "manage_documents": "Manage documents.",
    "manage_settings": "Change settings.",
    "manage_skills": "Manage skills.",
    "trigger_research": "Start deep research.",
    "manage_endpoints": "Manage endpoints.",
    "manage_mcp": "Manage MCP servers.",
    "manage_webhooks": "Manage webhooks.",
    "manage_tokens": "Manage API tokens.",
    "generate_image": "Generate image.",
    "app_api": "Generic API loopback.",
    "pipeline": "Multi-step pipeline.",
    "create_session": "Create chat.",
    "send_to_session": "Send to chat.",
    "download_model": "Download HF model.",
    "serve_model": "Start model server.",
    "list_served_models": "List running servers.",
    "stop_served_model": "Stop model server.",
    "list_downloads": "List downloads.",
    "cancel_download": "Cancel download.",
    "search_hf_models": "Search HF models.",
    "list_cached_models": "List cached models.",
    "list_serve_presets": "List serve presets.",
    "serve_preset": "Launch serve preset.",
    "adopt_served_model": "Adopt tmux server.",
    "list_cookbook_servers": "List servers.",
    "edit_image": "Edit gallery image.",
    "manage_gallery": "Manage image gallery.",
    "generate_speech": "Text-to-speech.",
    "transcribe_audio": "Speech-to-text.",
    "manage_research": "Manage saved research.",
}


def get_context_tier(context_length: int) -> tuple:
    """Return (tier_name, max_tools, description_style) for the given context window.

    TIERS is ordered from largest max_ctx to smallest.  Walk from the
    smallest tier upward: the first tier whose max_ctx is >= the context
    length is the match.
    """
    for name, max_ctx, max_tools, desc_style in reversed(TIERS):
        if context_length <= max_ctx:
            return name, max_tools, desc_style
    # context > all tier bounds — use the first (largest) tier
    return TIERS[0][0], TIERS[0][2], TIERS[0][3]


def filter_tools_by_priority(tool_names: Set[str], max_tools: int) -> Set[str]:
    """Keep at most `max_tools` from tool_names, preferring higher-priority tools."""
    if len(tool_names) <= max_tools:
        return tool_names

    # Walk priority list; include tools that are in the requested set
    selected = set()
    for name in TOOL_PRIORITY:
        if name in tool_names:
            selected.add(name)
            if len(selected) >= max_tools:
                break

    # If we still have room, add any remaining tools not in the priority list
    # (e.g. MCP tools, newly added tools)
    if len(selected) < max_tools:
        for name in tool_names:
            if name not in selected:
                selected.add(name)
                if len(selected) >= max_tools:
                    break

    return selected


def get_short_description(tool_name: str, style: str) -> Optional[str]:
    """Get a shorter description for a tool based on the description style.

    Returns None if no short description is available (caller should use
    the original full description).
    """
    if style == "minimal":
        return MINIMAL_DESCRIPTIONS.get(tool_name)
    if style == "short":
        return SHORT_DESCRIPTIONS.get(tool_name)
    return None  # "full" — use original


def apply_slim_mode(
    context_length: int,
    tool_names: Set[str],
    relevant_tools: Optional[Set[str]] = None,
) -> tuple:
    """Apply slim mode filtering based on context window size.

    Args:
        context_length: The model's context window in tokens.
        tool_names: Full set of tool names to consider.
        relevant_tools: Pre-selected tools from RAG (if available).

    Returns:
        (filtered_tool_names, description_style, tier_name)
        - filtered_tool_names: reduced set of tool names
        - description_style: "full" | "short" | "minimal"
        - tier_name: the tier that was applied
    """
    if context_length <= 0:
        # Unknown context — assume large, apply no filtering
        return tool_names, "full", "unknown"

    tier_name, max_tools, desc_style = get_context_tier(context_length)

    if tier_name == "full":
        # No reduction needed for large context models
        return tool_names, "full", tier_name

    # Start with the provided tool set (RAG-selected or full)
    candidates = set(relevant_tools or tool_names)

    # For smaller tiers, apply priority-based filtering
    filtered = filter_tools_by_priority(candidates, max_tools)

    dropped = candidates - filtered
    if dropped:
        logger.info(
            f"[slim-mode] tier={tier_name} ctx={context_length} "
            f"max_tools={max_tools} dropped={len(dropped)} tools: "
            f"{sorted(dropped)[:10]}{'...' if len(dropped) > 10 else ''}"
        )

    return filtered, desc_style, tier_name
