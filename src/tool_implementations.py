"""
tool_implementations.py — aggregator / backward-compat shim.

The tool implementations were decomposed into cohesive submodules:

    src/agent_tools/document_tools.py   create/update/edit/suggest/manage document
                                        handlers + the active-document/model state
                                        (dispatched via src.agent_tools.TOOL_HANDLERS;
                                        import document helpers from there, not here)
    src/tools/_shared.py                lazy seam back to this aggregator
    src/tools/management_tools.py       manage_skills/tasks/endpoints/mcp/webhooks/
                                        tokens/settings/notes, api_call
    src/tools/model_tools.py            do_app_api + cookbook/model serve/download/adopt
    src/tools/calendar_tools.py         manage_calendar/research/contacts
    src/tools/vault_tools.py            vault search/get/unlock

This module now keeps only the truly-shared helpers (_parse_tool_args,
do_search_chats) plus re-exports: get_mcp_manager/_truncate from their single
source of truth in src.tool_utils, and every public name from the submodules
below, so historical importers ("from src.tool_implementations import
do_manage_calendar", and the monkeypatch path
src.tool_implementations.get_mcp_manager) keep resolving unchanged.
"""

import json
import logging
from typing import Any, Dict, List, Optional

# Single source of truth for these lives in src.tool_utils. Re-exported here
# because tests monkeypatch the src.tool_implementations.get_mcp_manager path
# and the split tool modules resolve it through the _ti seam at call time.
from src.tool_utils import _truncate, get_mcp_manager  # noqa: F401

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _parse_tool_args(content):
    """Parse a tool-call argument blob.

    Accepts either a JSON string or an already-decoded dict. Unwraps the
    common `{"body": {...}}` envelope that smaller models emit when they
    read tool descriptions like "Body is JSON: {...}" literally — they
    pass `body` as a field name rather than treating it as a noun.

    Returns a dict on success, raises ValueError on bad JSON.
    """
    if isinstance(content, str):
        try:
            args = json.loads(content) if content.strip() else {}
        except (json.JSONDecodeError, TypeError) as e:
            raise ValueError(str(e))
    elif isinstance(content, dict):
        args = content
    else:
        args = {}
    # Unwrap {"body": {...}} envelope — but only if `body` is the sole key
    # and points at a dict. We don't want to clobber a legitimate `body`
    # field on tools where it's a real arg (e.g. send_email body text).
    if (
        isinstance(args, dict)
        and len(args) == 1
        and "body" in args
        and isinstance(args["body"], dict)
        and "action" in args["body"]  # extra safety: only unwrap if the inner dict looks like a tool call
    ):
        args = args["body"]
    return args

# ---------------------------------------------------------------------------
# Search chats
# ---------------------------------------------------------------------------

async def do_search_chats(query: str, limit: int = 20, owner: str | None = None) -> Dict:
    """Search past session transcripts for the calling user's sessions only.

    Without an owner filter this used to leak EVERY user's chat history
    into the agent's `search_chats` results (v2 review HIGH-11). The
    caller in `tool_execution.execute_tool_block` now plumbs the owner
    through; legacy callers without owner pass through as before but
    will only see legacy/null-owner rows.
    """
    try:
        from src.session_search import search_session_messages

        results = search_session_messages(query, limit=limit, owner=owner)
        if not results:
            return {"results": f"No chats found matching \"{query}\"."}

        # Group by session to avoid duplicate links
        seen_sessions = {}
        for result in results:
            if result.session_id not in seen_sessions:
                seen_sessions[result.session_id] = result

        lines = [f"Found {len(seen_sessions)} session(s) matching \"{query}\":\n"]
        for sid, result in seen_sessions.items():
            lines.append(f"- **{result.session_name}** (#{sid})")
            lines.append(f"  Link: [Open chat](#{sid})")
            lines.append(f"  Match ({result.role}): {result.content_snippet}")
            if result.context_before:
                before = result.context_before[-1]
                lines.append(f"  Before ({before['role']}): {before['content'][:180]}")
            if result.context_after:
                after = result.context_after[0]
                lines.append(f"  After ({after['role']}): {after['content'][:180]}")
            lines.append("")

        return {"results": "\n".join(lines)}
    except Exception as e:
        logger.error(f"search_chats failed: {e}")
        return {"error": str(e), "exit_code": 1}


# ---------------------------------------------------------------------------
# Document tools — these live in src/agent_tools/document_tools.py together
# with the active-document/model state, and are dispatched through the
# handler registry in src/agent_tools/__init__.py (TOOL_HANDLERS). They are
# deliberately NOT re-exported here: importing src.agent_tools at module
# level would re-create the circular import this shim exists to avoid.
# Import them from src.agent_tools.document_tools directly.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Management tools — moved to src/tools/management_tools.py (P8-T3). Re-exported
# here so existing importers keep resolving against this aggregator. The module
# routes get_mcp_manager / _parse_tool_args back through this module so
# monkeypatch on src.tool_implementations.* still binds. (manage_documents
# lives with the other document handlers in src/agent_tools/document_tools.py.)
# ---------------------------------------------------------------------------
from src.tools.management_tools import (  # noqa: E402, F401
    do_manage_skills,
    _skill_dump,
    do_manage_tasks,
    do_manage_endpoints,
    do_manage_mcp,
    do_manage_webhooks,
    do_manage_tokens,
    do_manage_settings,
    do_api_call,
    do_manage_notes,
)


# ---------------------------------------------------------------------------
# Calendar tool — moved to src/tools/calendar_tools.py (P8-T5). Re-exported here.
# ---------------------------------------------------------------------------
from src.tools.calendar_tools import do_manage_calendar  # noqa: E402, F401


# ---------------------------------------------------------------------------
# Model / cookbook tools — moved to src/tools/model_tools.py (P8-T4). Re-exported
# here so existing importers keep resolving against this aggregator.
# ---------------------------------------------------------------------------
from src.tools.model_tools import (  # noqa: E402, F401
    _INTERNAL_BASE,
    _internal_headers,
    _cookbook_servers,
    _resolve_cookbook_host,
    _cookbook_env_for_host,
    _infer_serve_port,
    _infer_serve_host,
    _ensure_served_endpoint,
    _cookbook_register_task,
    do_app_api,
    _cookbook_apply_retry_suggestion,
    _scan_running_model_processes,
    do_download_model,
    do_serve_model,
    do_list_served_models,
    _cookbook_kill_session,
    do_stop_served_model,
    do_tail_serve_output,
    do_list_downloads,
    do_cancel_download,
    do_search_hf_models,
    do_adopt_served_model,
    do_list_cookbook_servers,
    do_list_serve_presets,
    do_serve_preset,
    do_list_cached_models,
    do_edit_image,
)


# ---------------------------------------------------------------------------
# Research / contacts tools — moved to src/tools/calendar_tools.py (P8-T5).
# Re-exported here so existing importers keep resolving.
# ---------------------------------------------------------------------------
from src.tools.calendar_tools import (  # noqa: E402, F401
    do_manage_research,
    do_trigger_research,
    do_resolve_contact,
    do_manage_contact,
)
# ---------------------------------------------------------------------------
# Vault tools — moved to src/tools/vault_tools.py (P8-T6). Re-exported here.
# ---------------------------------------------------------------------------
from src.tools.vault_tools import (  # noqa: E402, F401
    _load_vault_config,
    _get_vault_session,
    _run_bw,
    do_vault_search,
    do_vault_get,
    do_vault_unlock,
)
