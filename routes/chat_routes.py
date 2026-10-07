"""Chat routes — /api/chat, /api/chat_stream, /api/inject_context, /api/search."""

import asyncio
import json
import os
import re
import time
import logging
import re as _re
from urllib.parse import urlparse
from datetime import datetime
from typing import Dict, Any, AsyncGenerator, List, Optional

from fastapi import APIRouter, Request, HTTPException, Form, Query, Depends
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from core.models import ChatMessage
from src.request_models import ChatRequest
from src.llm_core import (
    _normalize_http_status,
    llm_call_async,
    llm_call_async_with_route_fallback,
    stream_llm,
    stream_llm_with_fallback,
)
from src.agent_loop import (
    stream_agent_loop,
    _configured_model_tool_surface,
    _local_media_needs_browser_render,
    _looks_like_workspace_coding_request,
)
from src.agent_loop import _normalize_ody_qwen_text_artifacts
from src import agent_runs
from src.model_context import estimate_tokens
from src.context_compactor import (
    apply_compaction_state,
    maybe_compact,
    trim_for_context,
)
from src.chat_helpers import coerce_message_and_session
from src.endpoint_resolver import normalize_base as _normalize_base, build_chat_url
from src.foreground_model_routing import (
    build_foreground_model_candidates,
    build_foreground_route_descriptors,
    resolve_foreground_model_policy,
)
from src.session_search import search_session_messages
from src.prompt_security import untrusted_context_message
from core.exceptions import SessionNotFoundError
from src.auth_helpers import (
    effective_user,
    get_current_user,
    is_delegated_credential,
    require_api_token_scope,
    require_chat_api_token_scope,
)
from routes.session_routes import _verify_session_owner
from routes.document_helpers import _owner_session_filter
from core.database import SessionLocal, get_session_mode, set_session_mode
from core.database import Session as DBSession, ChatMessage as DBChatMessage
from core.database import Document as DBDocument, ModelEndpoint
from core.log_safety import redact_url
from routes.research_routes import _resolve_research_endpoint
from routes.model_routes import _visible_models
from routes.chat_helpers import (
    resolve_session_auth,
    build_chat_context,
    save_assistant_response,
    run_post_response_tasks,
    accumulate_token_usage,
    clean_thinking_for_save,
    clean_repeated_assistant_content,
    _allowed_models_for_request,
    _enforce_chat_privileges,
)
from src.action_intents import ToolIntent, classify_tool_intent as _classify_tool_intent
from src.image_model_ids import looks_like_image_generation_model
from src.tool_policy import (
    WEB_ACCESS_TOOL_NAMES,
    WEB_TOOL_NAMES,
    build_effective_tool_policy,
    is_web_search_explicitly_denied,
    web_intent_may_enable_for_turn,
    web_search_enabled_for_turn,
)
from src.tool_approvals import tool_approval_store
from core.guard_deco import content_type, suspicious_frequency
from src.tool_approval_scopes import stamp_chat_session_grant
from src.tool_security import delegated_credential_blocked_tools
from src.workspace_paths import backend_workspace_path
from src.client_tool_contract import TUI_CLIENT_TOOL_NAMES
from src.model_profiles import (
    ODYSSEUS_COMPACT_TOOL_SCHEMA_PROFILE,
    tool_schema_profile,
)
from src.tool_execution import AgentExecutionBridge, bind_execution_bridge
from src.agent_runtime.authority import is_internal_tool_request, request_authority_for_http
from src.agent_runtime.runtime_selection import uses_compact_preview_runtime
from src.turn_contract import (
    FAMILY_TOOLS, bind_turn_contract, preserve_bound_editor_selected_tools,
    requested_capabilities, resolve_turn_contract,
    requests_independent_web_source, requires_external_web_verification,
    selected_tools_for_request,
)

logger = logging.getLogger(__name__)


def _append_internal_chat_context(ctx, message):
    tagged = untrusted_context_message("internal tool request", message)
    ctx.messages.append(tagged)
    routed = getattr(ctx, "route_messages", None)
    if routed is not None and routed is not ctx.messages:
        routed.append(tagged)


# Track active streams for partial-save safety net
_active_streams: Dict[str, dict] = {}

# Ordinary TUI lookups stay bounded because they should finish in one short
# interaction. Workspace coding follows the agent's own done/blocked/progress
# contract instead of a second, smaller coding-specific ceiling.
_TUI_AGENT_ROUND_CAP = 20
_INVISIBLE_RESPONSE_CHARS = "\u2063\u200b\u200c\u200d\ufeff"
_CLEAN_V3_ENDPOINT_ALIASES = frozenset({"cleanv3", "preheret"})


def _clean_v3_route_for_model(
    model: str | None,
    configured_mode: str | None = None,
) -> bool:
    """Select compact runtime by explicit setting, then model-name default."""
    mode = str(configured_mode or "").strip().lower()
    if mode:
        return mode in {"compact", "odysseus_compact"}
    return tool_schema_profile(model) == ODYSSEUS_COMPACT_TOOL_SCHEMA_PROFILE


def _turn_contract_enabled(*, exact_tool_approval, runtime_surface,
                           native_workspace_contract, clean_v3_route,
                           full_schema_route=False):
    """Use immutable capability contracts only for compact/native routes.

    Regular/full-schema models are intentionally allowed to choose from the
    complete enabled tool inventory.  Applying the compact turn classifier to
    those models made an omitted family indistinguishable from an explicit
    denial, so a misspelled web follow-up could silently lose browsing.
    """
    return bool(
        exact_tool_approval is None
        and runtime_surface != "odysseus-tui"
        and not full_schema_route
        and (not native_workspace_contract or clean_v3_route)
    )


def _request_privileges(request, user) -> Dict[str, Any]:
    """Per-user privileges from the app's auth manager; empty when unmanaged."""
    try:
        app = getattr(request, "app", None)
    except (AttributeError, KeyError):
        app = None
    state = getattr(app, "state", None) if app is not None else None
    auth_manager = getattr(state, "auth_manager", None) if state is not None else None
    if not user or not auth_manager:
        return {}
    return auth_manager.get_privileges(user) or {}


def _native_runtime_requires_local_browser(client_runtime_context):
    """Use the private browser to verify declared local HTML artifacts."""
    context = client_runtime_context if isinstance(client_runtime_context, dict) else {}
    if not (
        context.get("surface") == "odysseus-native"
        and context.get("terminal_agent") is True
        and context.get("unattended_mode") is True
    ):
        return False
    requirements = context.get("completion_requirements") or {}
    return any(
        str(path or "").casefold().endswith((".html", ".htm"))
        for path in requirements.get("required_artifacts") or ()
    )


class _AgentRenderState:
    """Track replacement snapshots versus resumed synthesis at the SSE boundary."""

    def __init__(self):
        self.owner = "streamed"
        self.content = ""
        self.replaced_turn = False

    def consume(self, event):
        event = dict(event)
        if event.get("type") == "final_response":
            from routes.chat_helpers import clean_thinking_for_save as _clean_thinking
            content = str(event.get("content") or event.get("delta") or "")
            visible, _ = _clean_thinking(content)
            self.content = visible or content
            if self.content != content:
                event["content"] = self.content
                event.pop("delta", None)
            self.owner = "streamed" if event.get("render_owner") == "streamed" else "structured"
            self.replaced_turn = True
            event["replacement_scope"] = "turn"
        elif event.get("delta") and not event.get("thinking"):
            if self.owner == "structured":
                # final_response can be intermediate. A later model synthesis
                # replaces it instead of being dropped or concatenated with it.
                self.content = ""
                self.owner = "streamed"
                event["replacement_scope"] = "turn"
            self.content += event["delta"]
        if "delta" in event or event.get("type") == "final_response":
            event["render_owner"] = self.owner
        return event

    def metadata(self, metadata=None):
        result = dict(metadata or {})
        result["render_owner"] = self.owner
        if self.replaced_turn:
            result["replacement_scope"] = "turn"
        return result

    def message_saved(self, message_id):
        return self.metadata({"type": "message_saved", "id": message_id})


def _visible_response_text_for_save(text: object) -> str:
    value = clean_repeated_assistant_content(text)
    value = value.strip()
    value = re.sub(r"\bDone\.\s*Done\.\s*$", "Done.", value)
    value = re.sub(
        r"^((?:Updated|Deleted|Created|Saved|Marked|Archived|Blocked|Unblocked|Opened|Closed)\b.+?\.)\s*Done\.\s*$",
        r"\1",
        value,
        flags=re.DOTALL,
    )
    return value
def _is_personal_data_search_without_web_target(text: str) -> bool:
    """Prevent generic ``search`` wording from disabling personal tools."""
    text = str(text or "")
    if not re.search(
        r"\b(?:memory|memories|remembered|recall|brain|prior\s+chats?|"
        r"previous\s+chats?|past\s+conversations?|previous\s+conversations?|"
        r"chat\s+history|sessions?|notes?|todos?|tasks?|skills?|documents?|docs?|"
        r"calendar|events?|meetings?|appointments?|schedule|emails?|inbox|contacts?)\b",
        text,
        re.IGNORECASE,
    ):
        return False
    return not re.search(
        r"\b(?:web|internet|online|google|news|weather|website|url|"
        r"browse|browser)\b",
        text,
        re.IGNORECASE,
    )


def _explicitly_denies_web_lookup(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:no\s+web|do\s+not\s+search|don'?t\s+search|without\s+looking\s+it\s+up|"
            r"without\s+searching|answer\s+from\s+memory\s+only|from\s+memory|"
            r"no\s+tools?|do\s+not\s+use\s+(?:any\s+)?tools?|don'?t\s+use\s+(?:any\s+)?tools?)\b",
            str(text or "").lower(),
        )
    )


def _explicitly_denies_tool_use(text: str) -> bool:
    return bool(re.search(
        r"\b(?:no\s+tools?|do\s+not\s+use\s+(?:any\s+)?tools?|"
        r"don'?t\s+use\s+(?:any\s+)?tools?)\b",
        str(text or ""), re.I,
    ))


_EXPLICIT_URL_TARGET = re.compile(
    r"\bhttps?://\S+|(?<![/\\@])\b(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/\S*)?",
    re.IGNORECASE,
)


def _contains_explicit_url_target(text: str) -> bool:
    """Recognize public URLs/domains without treating local paths as domains."""
    return bool(_EXPLICIT_URL_TARGET.search(str(text or "")))


def _authorizes_exact_url_fetch(text: str) -> bool:
    """Treat a pasted public URL as authority to read that URL, not search.

    The Web toggle controls open-ended discovery.  A concrete URL is already
    the user's chosen network target, so reading it does not need the broader
    search grant. Interactive navigation remains owned by ``private_browser``;
    YouTube links remain owned by ``youtube_tool``.
    """
    value = str(text or "")
    if _explicitly_denies_web_lookup(value) or _is_explicit_browser_automation_request(value):
        return False
    urls = re.findall(r"\bhttps?://[^\s<>\"']+", value, re.IGNORECASE)
    return any(
        not re.match(r"https?://(?:www\.)?(?:youtube\.com|youtu\.be)(?:/|$)", url, re.IGNORECASE)
        for url in urls
    )


def _is_explicit_browser_automation_request(text: str) -> bool:
    """Distinguish interactive navigation from ordinary URL/PDF retrieval."""
    return bool(re.search(
        r"\b(brow(?:ser|esr|sr)|browse|visit|go\s+to|navigate\s+to|"
        r"open\s+(?:the\s+)?(?:site|page|url|link)|click|fill(?:\s+out)?|"
        r"submit|send\s+(?:the\s+)?form|contact\s+form|form\s+submission)\b",
        str(text or ""),
        re.IGNORECASE,
    ))


def _is_external_discovery_request(text: str) -> bool:
    """Recognize requests to locate an authoritative public web source."""
    return bool(re.search(
        r"\b(?:find|locate|get)\s+(?:me\s+)?(?:the\s+|an?\s+)?"
        r"(?:official\s+)?(?:announcement|press\s+release|article|source|web\s*page|website|site)\b",
        str(text or ""),
        re.IGNORECASE,
    ))


_URL_SCHEME_RE = re.compile(r"https?://", re.I)
_PDF_URL_TAIL_RE = re.compile(r"\.pdf\b|/pdf/", re.I)


def _mentions_pdf_url(value: str) -> bool:
    """Same as ``re.search(r"https?://[^\\s]+(?:\\.pdf\\b|/pdf/)", value, re.I)``.

    Checked per whitespace-free token from its first scheme only (a later
    scheme's tail is a suffix of the first's), so a token packed with
    `http://` repeats is scanned once instead of once per repeat (ReDoS).
    """
    for token in value.split():
        scheme = _URL_SCHEME_RE.search(token)
        if scheme and _PDF_URL_TAIL_RE.search(token, scheme.end() + 1):
            return True
    return False


def _prefers_structured_document_tools(text: str) -> bool:
    """Identify external paper/PDF extraction where shell is a bad source route."""
    value = str(text or "")
    if re.search(r"(?:^|\s)(?:file://)?/workspace/[^\s`\"']+\.pdf\b", value, re.I):
        return False
    return bool(
        _mentions_pdf_url(value)
        or re.search(
            r"\b(?:paper|report|study)\b[\s\S]{0,1200}?"
            r"\b(?:tables?|figures?|benchmarks?|scores?|metrics?)\b",
            value,
            re.I,
        )
    )


def _is_contextual_web_link_followup(history: List[ChatMessage], text: str) -> bool:
    """Enable web for terse link follow-ups only when prior chat gives a web topic."""
    latest = str(text or "").strip().lower()
    if not re.fullmatch(
        r"(?:send|sned|share|give|show)?\s*(?:me\s+)?(?:the\s+)?"
        r"(?:links?|urls?|sources?)\s*(?:please|pls)?[.!?]?",
        latest,
    ):
        return False
    chunks: list[str] = []
    for msg in reversed(history or []):
        if getattr(msg, "role", "") not in {"user", "assistant"}:
            continue
        content = str(getattr(msg, "content", "") or "").strip()
        if content:
            chunks.append(content)
        if len(chunks) >= 4:
            break
    recent = "\n".join(chunks).lower()
    return bool(
        re.search(r"\b(?:websites?|sites?|links?|urls?|sources?|resources?)\b", recent)
        and re.search(
            r"\b(?:public domain|wikimedia|met(?:ropolitan)? museum|rijksmuseum|"
            r"smithsonian|library of congress|internet archive|art institute)\b",
            recent,
        )
    )


def _parse_client_tools(raw: Any) -> List[Dict[str, str]]:
    if isinstance(raw, str) and raw.strip():
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name in TUI_CLIENT_TOOL_NAMES and name not in {
            entry["name"] for entry in result
        }:
            result.append({"name": name})
    return result


def _parse_legacy_client_runtime_context(raw: Any) -> Dict[str, Any]:
    """Parse the retired TUI runtime contract for private-branch tests."""
    def clean_skill_name(value: Any) -> str:
        text = str(value or "").strip().strip("`")
        return text if _re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,80}", text) else ""

    def clean_contract_atom(value: Any) -> str:
        text = _re.sub(r"\s+", "_", str(value or "").strip())
        return text if _re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,80}", text) else ""

    def clean_short_text(value: Any, limit: int) -> str:
        text = _re.sub(r"\s+", " ", str(value or "")).strip()
        return text[:limit] if text else ""

    def clean_multiline_text(value: Any, limit: int) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        text = "\n".join(line.rstrip() for line in text.splitlines()).strip()
        text = _re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
        return text[:limit] if text else ""

    def clean_local_agents_md(value: Any) -> list[Dict[str, str]]:
        """Keep bounded workspace instruction bodies from the host TUI."""
        if not isinstance(value, list):
            return []
        cleaned: list[Dict[str, str]] = []
        total_body_chars = 0
        for item in value[:8]:
            if not isinstance(item, dict):
                continue
            path = clean_short_text(item.get("path"), 400)
            label = clean_short_text(item.get("label"), 200)
            remaining = 14000 - total_body_chars
            if remaining <= 0:
                break
            body = clean_multiline_text(item.get("body"), min(3500, remaining))
            if not path or not body:
                continue
            entry = {"path": path, "body": body}
            if label:
                entry["label"] = label
            cleaned.append(entry)
            total_body_chars += len(body)
        return cleaned

    def clean_bool_map(value: Any) -> Dict[str, bool]:
        if not isinstance(value, dict):
            return {}
        cleaned: Dict[str, bool] = {}
        for key, enabled in value.items():
            name = str(key or "").strip()
            if not _re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,80}", name):
                name = ""
            if name and isinstance(enabled, bool):
                cleaned[name] = enabled
            if len(cleaned) >= 24:
                break
        return cleaned

    def clean_host_shell_request(value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        cleaned: Dict[str, Any] = {}
        method = clean_contract_atom(value.get("method"))
        if method == "POST":
            cleaned["method"] = method
        path = str(value.get("path") or "").strip()
        if path == "/run":
            cleaned["path"] = path
        body = value.get("body")
        if isinstance(body, dict):
            body_fields = []
            for key in body.keys():
                text = str(key or "").strip()
                if text in {"command", "job_id", "timeout", "detach"} and text not in body_fields:
                    body_fields.append(text)
            if body_fields:
                cleaned["body_fields"] = body_fields
        try:
            timeout = int(float(value.get("max_timeout_s")))
        except (TypeError, ValueError):
            timeout = 0
        if 1 <= timeout <= 900:
            cleaned["max_timeout_s"] = timeout
        poll = clean_short_text(value.get("poll"), 160)
        if poll:
            cleaned["poll"] = poll
        return cleaned

    def clean_runtime_contract(value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        cleaned: Dict[str, Any] = {}
        for key in (
            "backend_shell_scope",
            "host_shell",
            "local_network_tasks",
            "local_workspace_tasks",
        ):
            atom = clean_contract_atom(value.get(key))
            if atom:
                cleaned[key] = atom
        host_commands = clean_bool_map(value.get("host_commands"))
        if host_commands:
            cleaned["host_commands"] = host_commands
        host_capabilities = clean_bool_map(value.get("host_capabilities"))
        if host_capabilities:
            cleaned["host_capabilities"] = host_capabilities
        host_shell_request = clean_host_shell_request(value.get("host_shell_request"))
        if host_shell_request:
            cleaned["host_shell_request"] = host_shell_request
        guidance = clean_short_text(value.get("guidance"), 280)
        if guidance:
            cleaned["guidance"] = guidance
        return cleaned

    if isinstance(raw, dict):
        data = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except Exception:
            return {}
        data = parsed if isinstance(parsed, dict) else {}
    else:
        return {}
    surface = str(data.get("surface") or "")
    if surface not in {"odysseus-tui", "odysseus-native"}:
        return {}
    result: Dict[str, Any] = {"surface": surface}
    if surface == "odysseus-native":
        # Native terminal callers may declare workspace artifacts for the
        # evidence ledger, but may not inject verifier shell commands or host
        # bridges.  Paths remain confined by the normal workspace resolver.
        result["terminal_agent"] = data.get("terminal_agent") is True
        interaction_mode = clean_contract_atom(data.get("interaction_mode")).lower()
        if interaction_mode == "cook":
            result["interaction_mode"] = "cook"
            result["unattended_mode"] = True
        try:
            max_agent_rounds = int(data.get("max_agent_rounds"))
        except (TypeError, ValueError):
            max_agent_rounds = 0
        if 1 <= max_agent_rounds <= 200:
            result["max_agent_rounds"] = max_agent_rounds
        result["artifact_recovery_enabled"] = (
            data.get("artifact_recovery_enabled") is not False
        )
        input_paths = []
        for value in data.get("input_files") or []:
            path = str(value or "").strip()
            parts = _re.split(r"[/\\]+", path)
            if (
                path.startswith("/workspace/")
                and ".." not in parts
                and "\n" not in path
                and len(path) <= 400
                and path not in input_paths
            ):
                input_paths.append(path)
            if len(input_paths) >= 32:
                break
        if input_paths:
            result["input_files"] = input_paths
        raw_requirements = data.get("completion_requirements")
        paths = []
        workspace_root = ""
        if isinstance(raw_requirements, dict):
            for value in raw_requirements.get("required_artifacts") or []:
                path = str(value or "").strip()
                parts = _re.split(r"[/\\]+", path)
                if (
                    path.startswith("/workspace/")
                    and ".." not in parts
                    and "\n" not in path
                    and len(path) <= 400
                    and path not in paths
                ):
                    paths.append(path)
                if len(paths) >= 32:
                    break
            candidate_root = str(raw_requirements.get("workspace_root") or "").strip()
            root_parts = _re.split(r"[/\\]+", candidate_root)
            if (
                candidate_root.startswith("/")
                and ".." not in root_parts
                and "\n" not in candidate_root
                and "\r" not in candidate_root
                and len(candidate_root) <= 500
            ):
                workspace_root = candidate_root.rstrip("/") or "/"
        result["completion_requirements"] = {
            "required_artifacts": paths,
            "verifier_required": False,
            "executable_verifier_available": False,
            "verifier_commands": [],
        }
        if workspace_root:
            result["completion_requirements"]["workspace_root"] = workspace_root
        return result
    client_tools = _parse_client_tools(data.get("client_tools"))
    if client_tools:
        result["client_tools"] = client_tools
    for key in (
        "interaction_mode",
        "terminal_agent",
        "session_cwd",
        "backend_host_limited",
        "backend_container_network",
        "agent_runtime_directives",
    ):
        if key in data:
            if key == "session_cwd":
                raw_cwd = str(data.get(key) or "")
                if "\n" in raw_cwd or "\r" in raw_cwd:
                    continue
                cwd = clean_short_text(raw_cwd, 400)
                if cwd:
                    result[key] = cwd
            else:
                result[key] = data[key]
    contract = clean_runtime_contract(data.get("runtime_execution_contract"))
    if contract:
        result["runtime_execution_contract"] = contract
    local_agents_md = clean_local_agents_md(data.get("local_agents_md"))
    if local_agents_md:
        result["local_agents_md"] = local_agents_md
    # Keep a compact project index from the TUI. The inventory is host-local
    # metadata, not instructions; relative paths are enough for the model to
    # resolve a named project from session_cwd without copying 120 records
    # into the prompt.
    projects = []
    for item in (data.get("local_workspace_projects") or [])[:24]:
        if not isinstance(item, dict):
            continue
        name = clean_short_text(item.get("name"), 120)
        relative_path = clean_short_text(item.get("relative_path"), 240)
        relation = clean_contract_atom(item.get("relation"))
        markers = [
            clean_short_text(marker, 80)
            for marker in (item.get("markers") or [])[:4]
            if clean_short_text(marker, 80)
        ]
        if not name or not relative_path:
            continue
        entry = {"name": name, "relative_path": relative_path}
        if relation:
            entry["relation"] = relation
        if markers:
            entry["markers"] = markers
        projects.append(entry)
    if projects:
        result["local_workspace_projects"] = projects
    active_skills = []
    for item in data.get("active_skills") or []:
        name = clean_skill_name(item)
        if name and name not in active_skills:
            active_skills.append(name)
        if len(active_skills) >= 8:
            break
    if active_skills:
        result["active_skills"] = active_skills
        details = []
        total_body_chars = 0
        for item in data.get("active_skill_details") or []:
            if not isinstance(item, dict):
                continue
            name = clean_skill_name(item.get("name"))
            if not name or name not in active_skills:
                continue
            detail = {"name": name}
            description = clean_short_text(item.get("description"), 240)
            source = clean_short_text(item.get("source"), 260)
            if description:
                detail["description"] = description
            if source:
                detail["source"] = source
            for key, limit in (
                ("category", 80),
                ("status", 80),
                ("when_to_use", 500),
            ):
                value = clean_short_text(item.get(key), limit)
                if value:
                    detail[key] = value
            remaining = 12000 - total_body_chars
            body = clean_multiline_text(item.get("body"), min(6500, max(0, remaining))) if remaining > 0 else ""
            if body:
                detail["body"] = body
                total_body_chars += len(body)
            if len(detail) > 1:
                details.append(detail)
        if details:
            result["active_skill_details"] = details[:8]
    return result


def _native_context_has_workspace_inputs(context: Dict[str, Any] | None) -> bool:
    """Treat sanitized native input declarations as workspace intent."""
    data = context if isinstance(context, dict) else {}
    return bool(
        data.get("surface") == "odysseus-native"
        and data.get("terminal_agent") is True
        and data.get("input_files")
    )


def _parse_client_runtime_context(raw: Any) -> Dict[str, Any]:
    """Parse and validate the TUI runtime contract used for host-local tools."""
    context = _parse_legacy_client_runtime_context(raw)
    if not context:
        return {}

    data = raw
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
        except Exception:
            return {}
    if not isinstance(data, dict):
        return {}

    client_tools = []
    allowed_client_tools = TUI_CLIENT_TOOL_NAMES
    for item in data.get("client_tools") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name in allowed_client_tools and name not in client_tools:
            client_tools.append(name)
    if client_tools:
        context["client_tools"] = [{"name": name} for name in client_tools]

    if data.get("unattended_mode") is True:
        context["unattended_mode"] = True

    # Native task runtimes may request a bounded generation budget. Keep this
    # separate from interactive preset handling and only retain a finite,
    # validated value for the already-recognized unattended native surface.
    if (
        context.get("surface") == "odysseus-native"
        and context.get("terminal_agent") is True
        and context.get("unattended_mode") is True
    ):
        try:
            max_output_tokens = int(data.get("max_output_tokens"))
        except (TypeError, ValueError):
            max_output_tokens = 0
        if max_output_tokens > 0:
            context["max_output_tokens"] = max(256, min(max_output_tokens, 32768))

        external_bridge = data.get("external_execution_bridge")
        if isinstance(external_bridge, dict):
            url = str(external_bridge.get("url") or "").strip()
            token = str(external_bridge.get("token") or "").strip()
            parsed = urlparse(url)
            tools = []
            for value in external_bridge.get("supported_tools") or []:
                name = str(value or "").strip()
                if (
                    re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]{0,127}", name)
                    and name not in tools
                ):
                    tools.append(name)
                if len(tools) >= 64:
                    break
            if (
                parsed.scheme == "http"
                and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
                and parsed.port is not None
                and parsed.path not in {"", "/"}
                and not parsed.username
                and not parsed.password
                and 16 <= len(token) <= 512
                and tools
            ):
                context["external_execution_bridge"] = {
                    "url": url,
                    "token": token,
                    "supported_tools": tools,
                }

    bridge = data.get("host_shell_bridge")
    # The host bridge is a TUI capability. Native task runtimes execute inside
    # their isolated workspace and must not carry a caller-supplied host bridge
    # into the backend agent context.
    if context.get("surface") == "odysseus-tui" and isinstance(bridge, dict):
        url = str(bridge.get("url") or "").strip()
        token = str(bridge.get("token") or "").strip()
        from src.agent_tools.subprocess_tools import is_host_shell_bridge_url_allowed
        if token and is_host_shell_bridge_url_allowed(url):
            context["host_shell_bridge"] = {"url": url, "token": token}
    return context


def _external_execution_bridge(
    client_runtime_context: Optional[Dict[str, Any]],
) -> Optional[AgentExecutionBridge]:
    """Build the validated request-local execution transport, if declared."""

    context = client_runtime_context if isinstance(client_runtime_context, dict) else {}
    config = context.get("external_execution_bridge")
    if not isinstance(config, dict):
        return None
    url = str(config.get("url") or "")
    token = str(config.get("token") or "")
    supported = frozenset(str(name) for name in config.get("supported_tools") or [])
    if not url or not token or not supported:
        return None

    async def route_tool(tool, content, session_id, runtime_context):
        import httpx

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(35.0, connect=3.0, pool=3.0)
        ) as client:
            response = await client.post(
                url,
                headers={"x-odysseus-execution-token": token},
                json={
                    "tool": tool,
                    "arguments": content,
                    "session_id": session_id,
                },
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("result"), dict):
            raise ValueError("external execution bridge returned an invalid payload")
        return str(payload.get("description") or tool), payload["result"]

    from src.agent_runtime.remote_resources import configuration_incarnation
    return AgentExecutionBridge(
        route_tool=route_tool,
        supported_tools=supported,
        name="request_local_http",
        endpoint_id=url,
        configuration_id=configuration_incarnation((url, token, tuple(sorted(supported)))),
    )


async def _stream_agent_with_execution_bridge(bridge, *args, **kwargs):
    with bind_turn_contract(kwargs.get("turn_contract")):
        if bridge is None:
            async for chunk in stream_agent_loop(*args, **kwargs):
                yield chunk
            return
        with bind_execution_bridge(bridge):
            async for chunk in stream_agent_loop(*args, **kwargs):
                yield chunk


def _should_detach_chat_stream(
    *,
    compare_mode: bool,
    client_runtime_context: Optional[Dict[str, Any]],
) -> bool:
    """Return whether a stream should survive its client disconnecting.

    Interactive sessions are resumable, so their runs remain detached. A
    compare stream or an explicitly unattended native stream has no user who
    can resume it; tying those runs to the response prevents abandoned work
    from continuing to consume model and tool resources.
    """

    if compare_mode:
        return False
    context = (
        client_runtime_context
        if isinstance(client_runtime_context, dict)
        else {}
    )
    return not (
        context.get("surface") == "odysseus-native"
        and context.get("unattended_mode") is True
    )


def _post_response_extraction_allowed(
    *,
    tools_blocked: bool,
    tool_approval_continuation: bool,
    client_runtime_context: Optional[Dict[str, Any]],
) -> bool:
    """Return whether a completed stream may launch background LLM work.

    Memory and skill extraction are useful for interactive conversations, but
    an explicitly unattended runtime has no user session to enrich. Running
    those jobs also competes with the caller's next autonomous task on local
    endpoints, so the unattended contract disables them at the route boundary.
    """

    context = (
        client_runtime_context
        if isinstance(client_runtime_context, dict)
        else {}
    )
    return bool(
        not tools_blocked
        and not tool_approval_continuation
        and context.get("unattended_mode") is not True
    )


def _client_runtime_context_system_message(
    context: Dict[str, Any],
    disabled_tools: set[str] | None = None,
    *,
    include_directives: bool = True,
) -> Dict[str, Any] | None:
    if not isinstance(context, dict) or context.get("surface") != "odysseus-tui":
        return None
    disabled = set(disabled_tools or set())
    host_shell_enabled = "host_shell" not in disabled
    directives = context.get("agent_runtime_directives")
    if not isinstance(directives, list):
        directives = []
    clean_directives = [
        str(item).strip()[:240]
        for item in directives
        if isinstance(item, str) and item.strip()
    ][:4]
    if not host_shell_enabled:
        clean_directives = [
            item
            for item in clean_directives
            if "host_shell" not in item and "host bridge" not in item.lower()
        ]
    contract = context.get("runtime_execution_contract")
    active_skills = context.get("active_skills")
    if not isinstance(active_skills, list):
        active_skills = []
    active_skills = [str(name).strip() for name in active_skills if str(name).strip()][:8]
    local_agents_md = context.get("local_agents_md")
    if not isinstance(local_agents_md, list):
        local_agents_md = []
    project_inventory = context.get("local_workspace_projects")
    if not isinstance(project_inventory, list):
        project_inventory = []
    bridge_context = context.get("host_shell_bridge")
    if isinstance(bridge_context, dict) and str(bridge_context.get("url") or "").strip():
        # Bridge tools execute on the TUI host, so preserve its host path.
        session_cwd = str(context.get("session_cwd") or "").strip()[:400]
    else:
        session_cwd = _client_runtime_context_cwd(context)
    mode = ""
    workspace_mode = ""
    turn_controls = context.get("turn_controls")
    if not isinstance(turn_controls, dict):
        turn_controls = {}
    if isinstance(contract, dict):
        mode = str(contract.get("local_network_tasks") or "").strip()
        workspace_mode = str(contract.get("local_workspace_tasks") or "").strip()
    if mode == "use_host_shell_bridge" and not host_shell_enabled:
        mode = "host_shell_disabled_by_turn_controls"
    if workspace_mode == "use_host_shell_bridge" and not host_shell_enabled:
        workspace_mode = "host_shell_disabled_by_turn_controls"
    has_contract_facts = isinstance(contract, dict) and any(
        contract.get(key)
        for key in (
            "backend_shell_scope",
            "host_shell",
            "local_workspace_tasks",
            "host_commands",
            "host_capabilities",
        )
    )
    if (
        not clean_directives
        and not mode
        and not workspace_mode
        and not active_skills
        and not local_agents_md
        and not project_inventory
        and not session_cwd
        and not has_contract_facts
    ):
        return None
    lines = ["## Odysseus TUI runtime contract"]
    if session_cwd:
        lines.append(f"- session_cwd: {session_cwd}")
    if turn_controls:
        enabled = [
            name for name in ("web", "bash", "research", "research_tool", "rag")
            if turn_controls.get(name) is True
        ]
        disabled = [
            name for name in ("web", "bash", "research", "research_tool", "rag")
            if turn_controls.get(name) is False
        ]
        if enabled:
            lines.append(f"- turn_controls_enabled: {', '.join(enabled)}")
        if disabled:
            lines.append(f"- turn_controls_disabled: {', '.join(disabled)}")
    if isinstance(contract, dict):
        shell_scope = str(contract.get("backend_shell_scope") or "").strip()
        if shell_scope:
            lines.append(f"- backend_shell_scope: {shell_scope}")
        host_shell = str(contract.get("host_shell") or "").strip()
        if host_shell:
            if host_shell == "available" and not host_shell_enabled:
                host_shell = "disabled_by_turn_controls"
            lines.append(f"- host_shell: {host_shell}")
    if mode:
        lines.append(f"- local_network_tasks: {mode}")
    if workspace_mode:
        lines.append(f"- local_workspace_tasks: {workspace_mode}")
    if context.get("host_shell_bridge") and host_shell_enabled:
        lines.append(
            "- computer tools execute on the USER's machine at session_cwd; "
            "paths and commands are host-local. Bridge credentials are not shown."
        )
    if isinstance(contract, dict) and host_shell_enabled:
        host_commands = contract.get("host_commands")
        if isinstance(host_commands, dict):
            enabled_commands = [
                str(name)
                for name, enabled in host_commands.items()
                if enabled is True and str(name).strip()
            ][:16]
            if enabled_commands:
                lines.append(f"- host_commands: {', '.join(enabled_commands)}")
        host_capabilities = contract.get("host_capabilities")
        if isinstance(host_capabilities, dict):
            enabled_capabilities = [
                str(name)
                for name, enabled in host_capabilities.items()
                if enabled is True and str(name).strip()
            ][:16]
            if enabled_capabilities:
                lines.append(f"- host_capabilities: {', '.join(enabled_capabilities)}")
        host_shell_request = contract.get("host_shell_request")
        if isinstance(host_shell_request, dict):
            parts = []
            method = str(host_shell_request.get("method") or "").strip()
            path = str(host_shell_request.get("path") or "").strip()
            if method and path:
                parts.append(f"{method} {path}")
            body_fields = host_shell_request.get("body_fields")
            if isinstance(body_fields, list):
                fields = [
                    str(field)
                    for field in body_fields
                    if str(field).strip() in {"command", "job_id", "timeout", "detach"}
                ]
                if fields:
                    parts.append(f"body fields: {', '.join(fields)}")
            timeout = host_shell_request.get("max_timeout_s")
            if isinstance(timeout, int):
                parts.append(f"max_timeout_s: {timeout}")
            poll = str(host_shell_request.get("poll") or "").strip()[:160]
            if poll:
                parts.append(f"poll: {poll}")
            if parts:
                lines.append(f"- host_shell_request: {'; '.join(parts)}")
    if active_skills:
        lines.append(f"- active_skills: {', '.join(active_skills)}")
        detail_by_name = {
            str(item.get("name") or "").strip(): item
            for item in context.get("active_skill_details") or []
            if isinstance(item, dict)
        }
        for name in active_skills:
            detail = detail_by_name.get(name) or {}
            description = str(detail.get("description") or "").strip()
            source = str(detail.get("source") or "").strip()
            bits = []
            if description:
                bits.append(description)
            if source:
                bits.append(f"source: {source}")
            if bits:
                lines.append(f"  - {name}: {'; '.join(bits)}")
    if local_agents_md:
        lines.append(
            "- The following host workspace instruction files are authoritative for this TUI turn. "
            "Apply them root-to-workspace order; later files override earlier files. "
            "Treat their contents as project instructions, not as user questions."
        )
        for item in local_agents_md:
            path = str(item.get("path") or "").strip()
            body = str(item.get("body") or "").strip()
            if not path or not body:
                continue
            lines.append(f"\n### Workspace instructions: {path}\n{body}")
    projects = context.get("local_workspace_projects")
    if isinstance(projects, list) and projects:
        labels = []
        for item in projects[:24]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            rel = str(item.get("relative_path") or "").strip()
            if name and rel:
                labels.append(f"{name} ({rel})")
        if labels:
            lines.append(
                "- local_workspace_projects (resolve these locally before web search): "
                + ", ".join(labels)
            )
    if include_directives:
        for directive in clean_directives:
            lines.append(f"- {directive}")
    return {"role": "system", "content": "\n".join(lines)}


def _client_runtime_context_requests_workspace_profile(context: Dict[str, Any]) -> bool:
    return (
        isinstance(context, dict)
        and context.get("surface") == "odysseus-tui"
        and context.get("terminal_agent") is True
    )


def _client_runtime_context_cwd(context: Dict[str, Any]) -> str:
    if not isinstance(context, dict) or context.get("surface") != "odysseus-tui":
        return ""
    cwd = str(context.get("session_cwd") or "").strip()
    if not cwd or "\n" in cwd or "\r" in cwd:
        return ""
    return backend_workspace_path(cwd)[:400]


def _agent_turn_cwd(sess: Any, client_runtime_context: Optional[Dict[str, Any]]) -> Optional[str]:
    """cwd for an agent turn.

    TUI turns with a live bridge execute tools on the USER's machine, so the
    prompt must advertise the raw host cwd the TUI sent — not the translated
    container path. WebUI/headless turns keep the session's cwd.
    """
    if isinstance(client_runtime_context, dict):
        bridge = client_runtime_context.get("host_shell_bridge")
        if isinstance(bridge, dict) and str(bridge.get("url") or "").strip():
            raw = str(client_runtime_context.get("session_cwd") or "").strip()
            if raw and "\n" not in raw and "\r" not in raw:
                return raw[:400]
    return (
        getattr(sess, "cwd", None)
        or _client_runtime_context_cwd(client_runtime_context)
        or None
    )


def _effective_agent_rounds(
    raw_value: Any,
    client_runtime_context: Optional[dict],
    default: int,
    *,
    message: str = "",
    workspace_agent_intent: bool = False,
) -> Optional[int]:
    """Resolve the per-turn agent cap.

    ``None`` is the adaptive coding mode: the agent loop stops on completion,
    a real blocker, cancellation, or one of its progress/resource guards. A
    finite limit remains available for ordinary turns and explicit callers.
    """
    try:
        rounds = int(raw_value or default)
    except (TypeError, ValueError):
        rounds = default
    rounds = max(1, min(rounds, 200))
    if (
        isinstance(client_runtime_context, dict)
        and str(client_runtime_context.get("surface") or "") == "odysseus-native"
        and client_runtime_context.get("terminal_agent") is True
        and client_runtime_context.get("unattended_mode") is True
    ):
        # Streaming clients commonly implement their timeout as an inactivity
        # deadline, which is refreshed by every SSE token.  Honor an explicit
        # native task budget so a model that keeps emitting low-signal planning
        # prose cannot run forever.  Native callers without a declared budget
        # retain the configured finite cap instead of silently becoming
        # unbounded.
        try:
            native_rounds = int(client_runtime_context.get("max_agent_rounds"))
        except (TypeError, ValueError):
            native_rounds = rounds
        return max(1, min(native_rounds, 200))
    if workspace_agent_intent:
        # WebUI and TUI workspace coding share the same progress-driven
        # stopping contract. The interface must not decide how long an
        # inspect -> edit -> verify sequence is allowed to run.
        return None
    if (
        isinstance(client_runtime_context, dict)
        and str(client_runtime_context.get("surface") or "") == "odysseus-tui"
    ):
        # Workspace coding uses the same progress-driven stopping contract as
        # Codex-style coding agents. Do not cut an inspect -> edit -> verify
        # sequence off because it crossed an arbitrary round count.
        coding_turn = bool(
            _looks_like_workspace_coding_request(str(message or ""))
            and re.search(
                r"\b(?:edit|change|fix|repair|write|patch|modify|implement|add|remove|delete|rename|"
                r"refactor|replace|update|create|apply|commit)\b",
                str(message or ""),
                re.IGNORECASE,
            )
        )
        if coding_turn:
            return None
        rounds = min(rounds, _TUI_AGENT_ROUND_CAP)
    return rounds


def _effective_native_output_tokens(
    default: int,
    client_runtime_context: Optional[dict],
) -> int:
    """Honor a bounded per-request generation budget for native runtimes.

    The normal UI preset remains authoritative for WebUI/TUI traffic. An
    unattended native caller owns its task timeout and needs a request-scoped
    cap so a tool followup cannot monopolize the endpoint with the preset's
    full context window.
    """
    if not (
        isinstance(client_runtime_context, dict)
        and str(client_runtime_context.get("surface") or "") == "odysseus-native"
        and client_runtime_context.get("terminal_agent") is True
        and client_runtime_context.get("unattended_mode") is True
    ):
        return default
    try:
        requested = int(client_runtime_context.get("max_output_tokens"))
    except (TypeError, ValueError):
        return default
    bounded = max(256, min(requested, 32768))
    if bounded != default:
        logger.info(
            "[native-output-budget] preset=%s requested=%s enforced=%s",
            default,
            requested,
            bounded,
        )
    return bounded


def _annotate_chat_cost(metrics: Optional[dict], sess) -> None:
    """Attach USD cost fields to a direct-chat metrics payload, in place.

    Provider-reported cost (OpenRouter usage.cost → llm_core's cost_usd)
    wins; otherwise estimate from the session's model/endpoint. Unknown
    models / local endpoints leave the payload untouched — never guess.
    """
    if not isinstance(metrics, dict):
        return
    if metrics.get("cost_usd"):
        metrics.setdefault("cost_source", "reported")
        return
    try:
        from src.model_pricing import estimate_cost_usd

        est = estimate_cost_usd(
            metrics.get("model") or getattr(sess, "model", None),
            metrics.get("input_tokens"),
            metrics.get("output_tokens"),
            getattr(sess, "endpoint_url", None),
        )
    except Exception:
        est = None
    if est is not None:
        metrics["cost_usd"] = round(est, 6)
        metrics["cost_source"] = "estimated"


def _stream_failure_status(chunk: str) -> Optional[int]:
    """Extract a provider status without retaining provider-supplied detail."""

    try:
        for line in str(chunk or "").splitlines():
            if not line.startswith("data: "):
                continue
            status = json.loads(line[6:]).get("status")
            return _normalize_http_status(status)
    except json.JSONDecodeError:
        return None
    return None


def _reject_delegated_tool_approval(request: Request) -> None:
    """Refuse an approval answered by a bearer API token.

    A tool approval records that a HUMAN authorized one dangerous action. A
    token is a delegated credential handed to an integration, so when it
    answers the prompt it triggered, nobody is asked and the gate collapses
    into an extra round trip. Owner and session already match here: the token
    is answering on behalf of the account that minted it.
    """
    if is_delegated_credential(request):
        raise HTTPException(
            403,
            "Tool approvals require an interactive session. "
            "API tokens cannot authorize a gated action.",
        )


def _mark_tool_approval_resolved(sess, approval_id: Any, decision: Any) -> bool:
    """Persist a consumed approval decision on its existing tool event."""

    approval_key = str(approval_id or "")
    normalized_decision = str(decision or "").strip().lower()
    if not approval_key or normalized_decision not in {"approve", "approve_task", "deny"}:
        return False

    message_id = None
    resolved_metadata = None
    for item in reversed(getattr(sess, "history", []) or []):
        metadata = getattr(item, "metadata", None)
        if not isinstance(metadata, dict):
            continue
        tool_events = metadata.get("tool_events")
        if not isinstance(tool_events, list):
            continue
        for event in reversed(tool_events):
            ask_user = event.get("ask_user") if isinstance(event, dict) else None
            if not isinstance(ask_user, dict):
                continue
            if str(ask_user.get("approval_id") or "") != approval_key:
                continue
            ask_user["resolved"] = normalized_decision
            stamp_chat_session_grant(
                ask_user,
                getattr(sess, "id", ""),
                normalized_decision,
            )
            message_id = metadata.get("_db_id")
            resolved_metadata = {
                key: value for key, value in metadata.items() if key != "_db_id"
            }
            break
        if resolved_metadata is not None:
            break

    if resolved_metadata is None or not message_id:
        return False

    db = SessionLocal()
    try:
        db_message = db.query(DBChatMessage).filter(
            DBChatMessage.id == message_id,
            DBChatMessage.session_id == str(getattr(sess, "id", "")),
        ).first()
        if db_message is None:
            return False
        db_message.meta_data = json.dumps(resolved_metadata)
        db.commit()
        return True
    except Exception:
        db.rollback()
        logger.exception("Failed to persist tool approval resolution")
        return False
    finally:
        db.close()


async def _tool_approval_resolution_stream(decision: str) -> AsyncGenerator[str, None]:
    yield f"data: {json.dumps({'type': 'tool_approval_resolved', 'decision': decision})}\n\n"
    yield "data: [DONE]\n\n"


def _chat_candidate_request_factory(
    messages,
    fallback_context_length: int = 0,
    *,
    session=None,
    owner: Optional[str] = None,
):
    """Shape one route-neutral Chat prompt for each candidate window."""

    state = {
        "requests": {},
        "context_lengths": {},
        "trim_stats": {},
        "compactions": {},
        "was_compacted": {},
    }

    async def factory(index, candidate_url, candidate_model, candidate_headers):
        compaction_state = {}
        candidate_messages, context_length, was_compacted = await maybe_compact(
            session,
            candidate_url,
            candidate_model,
            list(messages),
            candidate_headers,
            owner=owner,
            persist=False,
            compaction_state=compaction_state,
        )
        if not context_length:
            context_length = fallback_context_length
        request_messages = trim_for_context(candidate_messages, context_length)
        state["requests"][index] = request_messages
        state["context_lengths"][index] = context_length
        state["compactions"][index] = compaction_state
        state["was_compacted"][index] = was_compacted
        state["trim_stats"][index] = {
            "messages_before": len(messages),
            "messages_after": len(request_messages),
            "tokens_before": estimate_tokens(messages),
            "tokens_after": estimate_tokens(request_messages),
        }
        return {"messages": request_messages}

    return factory, state


def _candidate_index(candidates, actual_candidate) -> int:
    for index, candidate in enumerate(candidates):
        if candidate == actual_candidate:
            return index
    return 0


def _stream_set(session_id: str, **fields) -> None:
    """Update fields on the active-stream entry for `session_id`, or
    no-op if the entry has already been popped. Using .get() avoids a
    KeyError race between `if x in d` and `d[x]["k"] = v` if a sibling
    finally pops the key in between (which becomes possible the moment
    a coroutine cancellation reaches an inner cleanup before the
    outermost cleanup runs)."""
    rec = _active_streams.get(session_id)
    if rec is None:
        return
    rec.update(fields)


def _message_plain_text(content: Any) -> str:
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(block, str):
                parts.append(block)
        return " ".join(parts)
    return str(content or "")


def _last_user_plain_text(messages: List[Dict[str, Any]]) -> str:
    for msg in reversed(messages or []):
        if msg.get("role") == "user":
            return _message_plain_text(msg.get("content"))
    return ""


def _ensure_current_request_is_latest_user(messages: List[Dict[str, Any]], current_message: str) -> List[Dict[str, Any]]:
    """Defensively keep detached streams grounded on the request that created them."""
    current = str(current_message or "").strip()
    if not current:
        return messages
    latest = _last_user_plain_text(messages).strip()
    if latest == current or current in latest or latest in current:
        return messages
    logger.warning(
        "[chat_stream] latest user context mismatch; appending current request for model call. latest=%r current=%r",
        latest[:120],
        current[:120],
    )
    repaired = list(messages or [])
    repaired.append({"role": "user", "content": current})
    return repaired


_WEB_FOLLOWUP_RE = re.compile(
    r"^\s*(?:now\s+)?(?:(?:can|could|would|will)\s+you\s+)?"
    r"(?:check|try\s+again|look(?:\s+now|\s+it\s+up)?|search(?:\s+now|\s+online|\s+it)?|"
    r"grab\s+(?:the\s+)?(?:top|first|second|third|next)\s+(?:story|result|link|article)\s+and\s+(?:open|read|summarize)\s+it|"
    r"(?:pull|get|read|check)\s+.{1,160}\b(?:off|from)\s+(?:that|this|the)\s+(?:link|page|result)|"
    r"tell\s+me\s+more(?:\s+about\s+.{1,120})?|more\s+about\s+.{1,120}|"
    r"what\s+else(?:\s+did\s+(?:it|this|that)\s+say)?(?:\s+about\s+.{1,120})?|"
    r"what\s+(?:did|does)\s+(?:it|this|that)\s+say(?:\s+about\s+.{1,120})?|"
    r"do\s+it|again|approved|approve(?:d)?|yes|ok(?:ay)?|proceed|go\s+ahead|"
    r"send(?:\s+it)?|submit(?:\s+it)?|email(?:\s+them|\s+it)?)\??\s*$",
    re.I,
)
_RECENT_WEB_CONTEXT_RE = re.compile(
    r"\b(?:weather|forecast|rain|raining|hourly|news|headlines|rate|exchange|currency|"
    r"price|current|latest|search|look\s+up|online|fetch|https?://)\b",
    re.I,
)
_RECENT_BROWSER_CONTEXT_RE = re.compile(
    r"\b(?:browser|browse|open\s+(?:the\s+)?(?:site|page|url|link)|click|"
    r"fill(?:\s+out)?|submit|send\s+(?:the\s+)?form|contact\s+form|web\s*form|"
    r"form\s+submission|playwright|automation)\b",
    re.I,
)
_BROWSER_STATE_FOLLOWUP_RE = re.compile(
    r"\b(?:what|which|show|read|check|inspect|open|click|tell)\b.{0,100}"
    r"\b(?:this|that|the|current|same)\s+(?:page|site|tab|link|button|form)\b"
    r"|\b(?:this|that|the|current|same)\s+(?:page|site|tab)\b.{0,100}"
    r"\b(?:show|read|check|inspect|open|click|visible|heading|title|link|button|form)\b"
    r"|\b(?:try|do|run)\s+(?:it\s+)?again\b.{0,100}"
    r"\b(?:this|that|the|current|same)\s+(?:page|site|tab)\b",
    re.I,
)
_BROWSER_MCP_TOOLS = {
    "mcp__builtin_browser__browser_navigate",
    "mcp__builtin_browser__browser_snapshot",
    "mcp__builtin_browser__browser_click",
    "mcp__builtin_browser__browser_type",
    "mcp__builtin_browser__browser_fill_form",
    "mcp__builtin_browser__browser_select_option",
    "mcp__builtin_browser__browser_press_key",
    "mcp__builtin_browser__browser_wait_for",
    "mcp__builtin_browser__browser_take_screenshot",
    "mcp__builtin_browser__browser_drag",
    "mcp__builtin_browser__browser_navigate_back",
    "mcp__builtin_browser__browser_close",
}


def _recent_session_text(sess, limit: int = 8, max_chars: int = 2000) -> str:
    history = getattr(sess, "history", None) or getattr(sess, "_history", None) or []
    chunks: List[str] = []
    for msg in history[-limit:]:
        content = getattr(msg, "content", None)
        if content is None and isinstance(msg, dict):
            content = msg.get("content")
        text = _message_plain_text(content).strip()
        if text:
            chunks.append(text)
    return " ".join(chunks)[-max_chars:]


def _is_contextual_web_followup(message: str, sess) -> bool:
    """Treat short retry/check replies as web lookups when recent context was web."""
    if not message or not _WEB_FOLLOWUP_RE.search(message):
        return False
    return bool(_RECENT_WEB_CONTEXT_RE.search(_recent_session_text(sess)))


def _has_recent_web_tool_event(sess, limit: int = 4) -> bool:
    """Require recorded web execution before inheriting web on a follow-up."""
    return _most_recent_successful_web_tool(sess, limit=limit) is not None


def _successful_session_tool_names(sess) -> frozenset[str]:
    """Return exact tools that completed successfully earlier in this chat.

    Routing can add tools, but must not retract a capability already exercised
    by the conversation. Authorization remains enforced later by the effective
    policy and executable-inventory intersection.
    """
    history = getattr(sess, "history", None) or getattr(sess, "_history", None) or []
    names: set[str] = set()
    for msg in history:
        metadata = getattr(msg, "metadata", None)
        if metadata is None and isinstance(msg, dict):
            metadata = msg.get("metadata")
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (TypeError, json.JSONDecodeError):
                metadata = {}
        if not isinstance(metadata, dict):
            continue
        for event in metadata.get("tool_events") or []:
            if not isinstance(event, dict):
                continue
            name = str(event.get("tool") or "").strip()
            status = str(event.get("status") or "done").casefold()
            if (
                name
                and event.get("error") is not True
                and event.get("exit_code") in (None, 0)
                and status not in {"failed", "error", "denied", "cancelled", "canceled"}
            ):
                names.add(name)
    return frozenset(names)


def _most_recent_successful_web_tool(sess, limit: int = 4) -> Optional[str]:
    """Return the latest successfully executed public-web tool, if any."""
    history = getattr(sess, "history", None) or getattr(sess, "_history", None) or []
    for msg in reversed(history[-limit:]):
        metadata = getattr(msg, "metadata", None)
        if metadata is None and isinstance(msg, dict):
            metadata = msg.get("metadata")
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (TypeError, json.JSONDecodeError):
                metadata = {}
        for event in reversed((metadata or {}).get("tool_events") or []):
            tool = str(event.get("tool") or "").rsplit("__", 1)[-1]
            if (
                tool in WEB_TOOL_NAMES
                and event.get("error") is not True
                and event.get("exit_code") in (None, 0)
            ):
                return tool
    return None


def _has_recent_private_browser_success(sess, limit: int = 6) -> bool:
    """Keep an explicitly opened browser available briefly using typed evidence."""
    def has_success(metadata: object) -> bool:
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (TypeError, json.JSONDecodeError):
                metadata = {}
        for event in (metadata or {}).get("tool_events") or []:
            if not isinstance(event, dict):
                continue
            tool = str(event.get("tool") or "").removeprefix("mcp__email__")
            if tool == "private_browser" and not event.get("error") and event.get("exit_code") in (None, 0):
                return True
        return False

    history = getattr(sess, "history", None) or getattr(sess, "_history", None) or []
    for msg in reversed(history[-limit:]):
        metadata = getattr(msg, "metadata", None)
        if metadata is None and isinstance(msg, dict):
            metadata = msg.get("metadata")
        if has_success(metadata):
            return True

    # The database is the cross-request source of truth. A session object can
    # be stale after a persistence reload seam, while the previous completed
    # tool turn is already durable and visible through /api/history.
    session_id = str(getattr(sess, "id", "") or "")
    if not session_id:
        return False
    db = SessionLocal()
    try:
        rows = (
            db.query(DBChatMessage)
            .filter(DBChatMessage.session_id == session_id)
            .order_by(DBChatMessage.timestamp.desc())
            .limit(limit)
            .all()
        )
        return any(has_success(row.meta_data) for row in rows)
    finally:
        db.close()


def _is_contextual_browser_followup(message: str, sess) -> bool:
    """Treat short retry replies as browser tasks when recent context was forms/browser automation."""
    if not message or not (
        _WEB_FOLLOWUP_RE.search(message)
        or _BROWSER_STATE_FOLLOWUP_RE.search(message)
    ):
        return False
    return bool(_RECENT_BROWSER_CONTEXT_RE.search(_recent_session_text(sess, limit=12, max_chars=4000)))


def _resolve_request_workspace(request, raw_value) -> tuple:
    """Resolve the posted workspace for this request: (workspace, rejected).

    Privilege is checked BEFORE the path ever touches the filesystem. Only
    admin/single-user callers can use the workspace-backed file/shell tools,
    so only they get vet_workspace() and the workspace_rejected signal. For
    any other caller the submitted value is dropped uniformly, with no vetting
    and no event: otherwise the presence/absence of workspace_rejected would
    let a non-admin chat caller probe which host paths exist.

    vet_workspace rejects non-directories, sensitive roots (.ssh, .gnupg,
    ...), and filesystem roots; on rejection there is no confinement and the
    default tool-path allowlist applies. The rejected value is surfaced so the
    stream can tell an admin client (which believes a workspace is active)
    that it was dropped.
    """
    requested = (raw_value or "").strip()
    if not requested:
        return "", ""
    from src.tool_security import owner_is_admin_or_single_user
    # Bearer clients are stamped as the sandboxed ``api`` pseudo-user by
    # middleware. Use the token's effective owner for the privilege check so
    # an owner's WebUI/API coding session can bind its workspace just like a
    # cookie-authenticated browser session.
    # A few internal callers/tests pass a minimal request object without the
    # Starlette ``state`` namespace. Real HTTP requests always have it, but
    # retaining the fallback keeps those callers on the cookie-user path.
    try:
        request_owner = effective_user(request)
    except AttributeError:
        request_owner = get_current_user(request)
    if not owner_is_admin_or_single_user(request_owner):
        return "", ""
    from src.workspace_paths import backend_workspace_path
    from src.tool_execution import vet_workspace
    backend_requested = backend_workspace_path(requested) or requested
    workspace = vet_workspace(backend_requested) or ""
    return workspace, (requested if not workspace else "")


def _resolve_persisted_session_workspace(request, sess, *, current_workspace: str = "", current_rejected: str = "") -> tuple[str, str]:
    """Use a session's saved cwd only when this request did not set one."""
    if current_workspace or current_rejected:
        return current_workspace, current_rejected
    persisted = str(getattr(sess, "cwd", "") or "").strip()
    if not persisted:
        return "", ""
    return _resolve_request_workspace(request, persisted)


_ABS_PATH_RE = re.compile(r"(?<!\S)(~?/[^\"'\s`<>]+)")
_LOCAL_FILE_TASK_RE = re.compile(
    r"\b(?:file|folder|directory|path|workspace|repo|project|movie|video|"
    r"subtitle|subtitles|srt|vtt|ass|download|save|rename|move|copy|extract|"
    r"convert|ffmpeg|run|execute|open|read|inspect|fix|debug|test|build)\b",
    re.IGNORECASE,
)


def _resolve_workspace_from_message_path(request, message: str) -> tuple[str, str]:
    """Auto-bind a workspace only when the user names an explicit safe path.

    This is intentionally deterministic rather than LLM/RAG-driven: RAG can
    choose the tool family, but filesystem binding must not let a prompt infer
    or probe arbitrary host paths. For a file path, bind its parent directory.
    For a directory path, bind that directory.
    """
    text = str(message or "")
    if not text or not _LOCAL_FILE_TASK_RE.search(text):
        return "", ""

    from src.tool_security import owner_is_admin_or_single_user
    if not owner_is_admin_or_single_user(get_current_user(request)):
        return "", ""

    from src.tool_execution import vet_workspace

    for match in _ABS_PATH_RE.finditer(text):
        raw = match.group(1).rstrip(".,;:)]}")
        expanded = os.path.realpath(os.path.expanduser(raw))
        candidates = [expanded]
        if os.path.isfile(expanded):
            candidates.insert(0, os.path.dirname(expanded))
        for candidate in candidates:
            workspace = vet_workspace(candidate) or ""
            if workspace:
                return workspace, ""
    return "", ""


def _session_url_matches_endpoint(session_url: str, endpoint_base: str) -> bool:
    if not session_url or not endpoint_base:
        return False
    sess = session_url.rstrip("/")
    base = _normalize_base(endpoint_base).rstrip("/")
    variants = {
        base,
        base + "/chat/completions",
        build_chat_url(base).rstrip("/"),
    }
    return sess in variants or sess.startswith(base + "/")


def _clear_orphaned_session_endpoint(sess, owner: str | None = None) -> bool:
    """Clear a session model if its endpoint was deleted from ModelEndpoint."""
    if not getattr(sess, "endpoint_url", ""):
        return False
    db = SessionLocal()
    try:
        q = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
        if owner:
            from src.auth_helpers import owner_filter
            q = owner_filter(q, ModelEndpoint, owner)
        endpoints = q.all()
        bound_id = getattr(sess, "endpoint_id", None)
        for ep in endpoints:
            if bound_id and ep.id != bound_id:
                continue
            if _session_url_matches_endpoint(sess.endpoint_url or "", ep.base_url or ""):
                return False
        if bound_id:
            # Keep the identity so re-enabling/reconnecting A can recover A.
            # Returning True stops chat; B must never replace a missing A.
            sess.headers = {}
            stored = db.query(DBSession).filter(DBSession.id == sess.id, DBSession.owner == owner).first()
            if stored is not None:
                stored.headers = {}
                db.commit()
            return True
        db_session = db.query(DBSession).filter(DBSession.id == sess.id).first()
        if db_session:
            db_session.endpoint_url = ""
            db_session.model = ""
            db_session.updated_at = datetime.utcnow()
            db.commit()
        sess.endpoint_url = ""
        sess.model = ""
        sess.headers = {}
        return True
    except Exception as e:
        logger.warning("Failed to clear orphaned session endpoint", exc_info=e)
        db.rollback()
        return False
    finally:
        db.close()


def _endpoint_cache_contains_model(endpoint, model: str) -> bool:
    """Return True when a populated endpoint model cache includes ``model``.

    Empty/malformed caches are treated as unknown rather than a negative match
    so older image endpoints without cached models still work.
    """
    raw = getattr(endpoint, "cached_models", None)
    if not raw:
        return True
    try:
        models = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as e:
        logger.warning("Failed to parse cached models list, treating as containing model", exc_info=e)
        return True
    if not isinstance(models, list) or not models:
        return True
    wanted = (model or "").strip()
    return wanted in {str(item).strip() for item in models}


def _is_image_generation_session(sess, owner: str | None = None) -> bool:
    """Whether this chat session should bypass text chat and generate images.

    Model-name prefixes are explicit image models. Endpoint type is only used
    when the current session endpoint actually matches that image endpoint, and
    when a populated endpoint model cache includes the selected model. This
    prevents an image endpoint on the same host from misrouting ordinary text
    models into the image-generation path.
    """
    model = (getattr(sess, "model", "") or "").strip()
    if looks_like_image_generation_model(model):
        return True

    endpoint_url = (getattr(sess, "endpoint_url", "") or "").strip()
    if not endpoint_url:
        return False

    db = SessionLocal()
    try:
        q = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
        if owner:
            from src.auth_helpers import owner_filter
            q = owner_filter(q, ModelEndpoint, owner)
        endpoints = q.all()
        for endpoint in endpoints:
            if (getattr(endpoint, "model_type", None) or "llm") != "image":
                continue
            if not _session_url_matches_endpoint(endpoint_url, getattr(endpoint, "base_url", "") or ""):
                continue
            if _endpoint_cache_contains_model(endpoint, model):
                return True
    except Exception:
        return False
    finally:
        db.close()
    return False


def _first_image_attachment(chat_handler, att_ids: List[str], owner: str | None = None) -> Optional[Dict[str, Any]]:
    """Return the first attached image file that this owner can read."""
    upload_handler = getattr(chat_handler, "upload_handler", None)
    if not upload_handler:
        return None
    for att_id in att_ids or []:
        try:
            info = upload_handler.resolve_upload(att_id, owner=owner)
        except Exception as e:
            logger.warning("Failed to resolve image edit upload %s", att_id, exc_info=e)
            continue
        if not info:
            continue
        name = info.get("name") or info.get("original_name") or info.get("id") or ""
        mime = info.get("mime", "")
        try:
            if upload_handler.is_image_file(name, mime):
                return info
        except Exception:
            continue
    return None


def _ts_or_zero(value) -> float:
    try:
        return float(value.timestamp()) if value else 0.0
    except Exception:
        return 0.0


def _recover_empty_session_model(sess, session_id: str, owner: str | None = None) -> bool:
    """Re-populate sess.model from the matching endpoint's cached models.

    Covers the window between endpoint setup and the first chat send: the
    picker showed a model in the dropdown but the session record never got
    written (Issue #587 — UI uses the cached endpoint list, not s.model).
    For ChatGPT Subscription, also repairs stale OpenAI API model names such as
    ``gpt-5`` that are not accepted by the Codex-backed ChatGPT account route.
    """
    current_model = (getattr(sess, "model", "") or "").strip()
    endpoint_url = (getattr(sess, "endpoint_url", "") or "").strip()
    is_chatgpt_subscription = False
    if current_model:
        try:
            from src.chatgpt_subscription import is_chatgpt_subscription_base
            is_chatgpt_subscription = is_chatgpt_subscription_base(endpoint_url)
            if not is_chatgpt_subscription:
                return False
        except Exception:
            return False
    db = SessionLocal()
    try:
        # Prefer the endpoint whose base URL matches the session — we know the
        # user already pointed this session at that endpoint, so its first
        # cached model is the most defensible default.
        ep = None
        if getattr(sess, "endpoint_url", ""):
            q = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
            if owner:
                from src.auth_helpers import owner_filter
                q = owner_filter(q, ModelEndpoint, owner)
            endpoints = q.all()
            # Honour the session's exact endpoint binding first: two endpoints
            # can share a provider URL (e.g. two ChatGPT Subscription accounts).
            bound_id = getattr(sess, "endpoint_id", None) or None
            if bound_id:
                for cand in endpoints:
                    if cand.id == bound_id and _session_url_matches_endpoint(sess.endpoint_url or "", cand.base_url or ""):
                        ep = cand
                        break
            if ep is None and not bound_id:
                for cand in sorted(endpoints, key=lambda row: (_ts_or_zero(getattr(row, "created_at", None)), str(row.id))):
                    if _session_url_matches_endpoint(sess.endpoint_url or "", cand.base_url or ""):
                        ep = cand
                        break
        if not ep:
            return False
        if not is_chatgpt_subscription:
            try:
                from src.chatgpt_subscription import is_chatgpt_subscription_base
                is_chatgpt_subscription = is_chatgpt_subscription_base(getattr(ep, "base_url", "") or endpoint_url)
            except Exception:
                is_chatgpt_subscription = False
        try:
            cached = json.loads(ep.cached_models) if isinstance(ep.cached_models, str) else (ep.cached_models or [])
        except Exception as e:
            logger.warning("Failed to parse cached_models for endpoint %r", getattr(ep, "id", "?"), exc_info=e)
            cached = []
        if not cached:
            visible = []
        else:
            try:
                visible = _visible_models(cached, getattr(ep, "hidden_models", None))
            except Exception:
                visible = cached
        if current_model and current_model in {str(item).strip() for item in visible}:
            return False
        if is_chatgpt_subscription:
            live_models = []
            if getattr(ep, "provider_auth_id", None):
                try:
                    from src.chatgpt_subscription import fetch_available_models
                    from src.endpoint_resolver import resolve_endpoint_runtime
                    _base, api_key = resolve_endpoint_runtime(ep, owner=owner)
                    if api_key:
                        live_models = fetch_available_models(api_key)
                        if live_models:
                            ep.cached_models = json.dumps(live_models)
                            db.commit()
                except Exception:
                    live_models = []
            # ChatGPT Subscription recovery must use the live Codex catalog.
            # Cached rows are only trusted above to avoid revalidating a model
            # that is already present in the visible picker list.
            cached = live_models
            if not cached:
                return False
            try:
                visible = _visible_models(cached, getattr(ep, "hidden_models", None))
            except Exception:
                visible = cached
            if current_model and current_model in {str(item).strip() for item in visible}:
                return False
        if not visible:
            return False
        model = visible[0]
        if not isinstance(model, str) or not model.strip():
            return False
        model = model.strip()
        # Persist so the next request, websocket reconnect, or page reload
        # picks up the same model (we'd otherwise re-pick on every send
        # and silently switch on the user if the cached order shifts).
        db_session_q = db.query(DBSession).filter(DBSession.id == session_id)
        if owner:
            db_session_q = db_session_q.filter(DBSession.owner == owner)
        db_session = db_session_q.first()
        if db_session:
            db_session.model = model
            db_session.updated_at = datetime.utcnow()
            db.commit()
        sess.model = model
        logger.info(
            "Recovered session model for %s — picked %r from endpoint %s",
            session_id, model, ep.id,
        )
        return True
    except Exception as e:
        db.rollback()
        logger.warning("Failed to recover empty session model for %s: %s", session_id, e)
    return False


def _reconcile_selected_route_from_request(
    request: Request,
    sess,
    session_id: str,
    form_data,
    owner: str | None = None,
) -> bool:
    """Apply the model route the browser selected before streaming.

    The frontend creates a pending chat first and only materializes it on first
    send. Startup/default-model refreshes can race with that UI state, so the
    stream request includes the route that was selected at click/send time.
    Trust only registered endpoint ids, or the session's existing endpoint URL.
    """
    selected_model = str(form_data.get("selected_model") or "").strip()
    selected_endpoint_id = str(form_data.get("selected_endpoint_id") or "").strip()
    selected_endpoint_url = str(form_data.get("selected_endpoint_url") or "").strip()
    if not selected_model:
        return False

    endpoint_url = ""
    headers = None
    resolved_endpoint_id = None
    if selected_endpoint_id or selected_endpoint_url:
        try:
            from src.auth_helpers import owner_filter
            from src.endpoint_resolver import build_headers, normalize_base
            db = SessionLocal()
            try:
                q = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
                if selected_endpoint_id:
                    q = q.filter(ModelEndpoint.id == selected_endpoint_id)
                if owner:
                    q = owner_filter(q, ModelEndpoint, owner)
                if selected_endpoint_url and not selected_endpoint_id:
                    candidates = [row for row in q.all() if _session_url_matches_endpoint(selected_endpoint_url, row.base_url or "")]
                    bound_id = getattr(sess, "endpoint_id", None)
                    if bound_id:
                        candidates = [row for row in candidates if row.id == bound_id]
                    if len(candidates) != 1:
                        return False
                else:
                    candidates = [q.first()]
                ep = None
                for cand in candidates:
                    if not cand:
                        continue
                    if selected_endpoint_id or _session_url_matches_endpoint(selected_endpoint_url, cand.base_url or ""):
                        ep = cand
                        break
                if not ep:
                    return False
                endpoint_url = build_chat_url(normalize_base(ep.base_url or ""))
                headers = build_headers(ep.api_key or "", ep.base_url or "") if ep.api_key else {}
                resolved_endpoint_id = ep.id
            finally:
                db.close()
        except Exception as e:
            logger.warning("Failed to resolve selected endpoint %s/%s for %s: %s", selected_endpoint_id, selected_endpoint_url, session_id, e)
            return False

    if not endpoint_url:
        return False

    route_changed = not (
        selected_model == (getattr(sess, "model", "") or "")
        and endpoint_url == (getattr(sess, "endpoint_url", "") or "")
    )
    headers_changed = dict(getattr(sess, "headers", None) or {}) != dict(headers or {})
    binding_changed = bool(
        resolved_endpoint_id
        and resolved_endpoint_id != (getattr(sess, "endpoint_id", None) or None)
    )
    if not route_changed and not headers_changed and not binding_changed:
        return False

    sess.model = selected_model
    sess.endpoint_url = endpoint_url
    sess.headers = headers or {}
    if resolved_endpoint_id:
        sess.endpoint_id = resolved_endpoint_id
    elif route_changed:
        # The route moved without an explicit endpoint id: drop a stale binding
        # rather than keep pointing at an endpoint the session no longer uses.
        sess.endpoint_id = None
    db = SessionLocal()
    try:
        db_session = db.query(DBSession).filter(DBSession.id == session_id).first()
        if db_session:
            db_session.model = selected_model
            db_session.endpoint_url = endpoint_url
            db_session.endpoint_id = getattr(sess, "endpoint_id", None) or None
            db_session.headers = sess.headers or {}
            db_session.updated_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()
    logger.info(
        "Reconciled selected route for %s: model=%r endpoint=%s route_changed=%s headers_changed=%s",
        session_id,
        selected_model,
        redact_url(endpoint_url),
        route_changed,
        headers_changed,
    )
    return True


def _set_user_time_from_request(request: Request) -> None:
    """Copy browser timezone headers into the per-request context.

    This is intentionally ephemeral: it is used only while building prompts
    and running tools for this request. It is not persisted or logged.
    """
    try:
        tz_offset = request.headers.get("x-tz-offset")
        tz_name = request.headers.get("x-tz-name")
        from src.user_time import clear_user_time_context, set_user_timezone, set_user_tz_name, set_user_tz_offset

        clear_user_time_context()
        # Synthetic SFT fixtures can be forced to UTC for fully deterministic
        # batch generation, but interactive SFT accounts should still use the
        # browser timezone so "4pm" lands at 4pm in the calendar UI.
        force_sft_utc = os.getenv("ODYSSEUS_SFT_FORCE_UTC_TIMEZONE", "0").strip().lower() in {"1", "true", "yes", "on"}
        if force_sft_utc and str(effective_user(request) or "").startswith("sft_"):
            set_user_timezone("UTC", 0)
            return
        if tz_offset is not None:
            set_user_tz_offset(tz_offset)
        if tz_name:
            set_user_tz_name(tz_name)
    except Exception:
        pass


def _resolve_prompt_thinking_mode(explicit_mode, preset_id, preset_manager):
    """Use an explicit request override, then fall back to the active preset."""
    mode = str(explicit_mode or "").strip().lower()
    if mode in {"on", "off"}:
        return mode
    preset = getattr(preset_manager, "presets", {}).get(preset_id) if preset_id else None
    if isinstance(preset, dict) and preset.get("enabled") is not False:
        mode = str(preset.get("thinking_mode") or "").strip().lower()
        if mode in {"on", "off"}:
            return mode
    return None


def setup_chat_routes(
    session_manager,
    chat_handler,
    chat_processor,
    memory_manager,
    research_handler,
    upload_handler,
    memory_vector=None,
    webhook_manager=None,
    skills_manager=None,
) -> APIRouter:
    router = APIRouter(
        tags=["chat"],
        dependencies=[Depends(require_chat_api_token_scope)],
    )

    # ------------------------------------------------------------------ #
    # POST /api/chat (non-streaming)
    # ------------------------------------------------------------------ #
    @router.post("/api/chat", response_model=Dict[str, Any])
    @suspicious_frequency(0.5, 60, "log")
    @content_type(["application/json"])
    async def chat_endpoint(request: Request, chat_request: ChatRequest) -> Dict[str, Any]:
        require_api_token_scope(request, "chat")
        _set_user_time_from_request(request)

        message = chat_request.message
        session = chat_request.session
        att_ids = chat_request.attachments or []
        use_web = chat_request.use_web
        use_research = chat_request.use_research
        time_filter = chat_request.time_filter
        preset_id = chat_request.preset_id
        thinking_mode = None

        # Verify the caller owns this session before loading it.
        # Without this, any authenticated user can post into another user's chat.
        _verify_session_owner(request, session)

        try:
            sess = session_manager.get_session(session)
        except KeyError:
            raise HTTPException(404, f"Session '{session}' not found")
        session_mode = str(getattr(sess, "thinking_mode", "") or "off").lower()
        if session_mode in {"on", "off"}:
            thinking_mode = session_mode
        from src.model_profiles import supports_user_thinking_toggle
        if not supports_user_thinking_toggle(sess.model):
            thinking_mode = "off"
        reasoning_effort = None
        req_effort = getattr(chat_request, "reasoning_effort", None)
        if req_effort:
            reasoning_effort = str(req_effort).strip().lower()
        elif session_mode.startswith("effort:"):
            reasoning_effort = session_mode[7:].strip()
        from src.chatgpt_subscription import validate_reasoning_effort
        reasoning_effort = validate_reasoning_effort(sess.model, reasoning_effort)
        owner = effective_user(request)
        _reconcile_selected_route_from_request(request, sess, session, {
            "selected_model": sess.model,
            "selected_endpoint_id": chat_request.selected_endpoint_id,
        }, owner=owner)
        if _clear_orphaned_session_endpoint(sess, owner=owner):
            raise HTTPException(400, "Selected model endpoint was removed. Pick another model in Settings.")

        # Empty model + live endpoint = setup race (Issue #587). Repair from
        # the endpoint's cached model list before privilege checks, which
        # otherwise see "" and behave inconsistently with the allowlist.
        _recover_empty_session_model(sess, session, owner=owner)
        if not getattr(sess, "model", "").strip():
            raise HTTPException(
                400,
                "No model selected for this chat. Open the model picker and choose one before sending.",
            )
        if not (getattr(sess, "endpoint_url", "") or "").strip():
            raise HTTPException(400, "Selected model endpoint is not configured")

        resolve_session_auth(sess, session, owner=owner)

        # Same allowed_models + daily-cap gate as chat_stream (mirror so the
        # non-streaming path can't be used to bypass).
        _enforce_chat_privileges(request, sess)

        tool_policy = build_effective_tool_policy(last_user_message=message)
        allow_tool_preprocessing = not tool_policy.block_all_tool_calls

        # Inline memory command
        memory_response = None
        if not tool_policy.blocks("manage_memory"):
            memory_response = await chat_handler.handle_memory_command(sess, message)
        if memory_response:
            return {"response": memory_response}

        foreground_policy = resolve_foreground_model_policy(
            owner=owner,
            allowed_models=_allowed_models_for_request(request),
        )

        # Build shared context (preset, preprocess, preface, compact)
        ctx = await build_chat_context(
            sess, request, chat_handler, chat_processor,
            message=message,
            session_id=session,
            preset_id=preset_id,
            att_ids=att_ids,
            use_web=use_web,
            time_filter=time_filter,
            webhook_manager=webhook_manager,
            allow_tool_preprocessing=allow_tool_preprocessing,
            defer_context_shaping=foreground_policy.enabled,
            persist_user_message=not is_internal_tool_request(request),
        )
        if is_internal_tool_request(request):
            _append_internal_chat_context(ctx, message)

        # Research injection
        research_blocked_by_policy = (
            tool_policy.blocks("trigger_research")
            or tool_policy.blocks("manage_research")
        )
        if use_research and not research_blocked_by_policy:
            try:
                _r_ep, _r_model, _r_headers = _resolve_research_endpoint(sess)
                research_ctx = await research_handler.call_research_service(
                    message, _r_ep, _r_model, llm_headers=_r_headers
                )
                research_message = untrusted_context_message("research context", research_ctx)
                ctx.messages.insert(len(ctx.preface), research_message)
                if foreground_policy.enabled:
                    getattr(ctx, "route_messages", ctx.messages).insert(
                        len(ctx.preface),
                        research_message,
                    )
            except Exception as e:
                logger.error(f"Research failed: {e}")

        foreground_candidates = build_foreground_model_candidates(
            sess.endpoint_url,
            sess.model,
            sess.headers,
            owner=owner,
            policy=foreground_policy,
        )
        route_descriptors = build_foreground_route_descriptors(
            sess.endpoint_url,
            sess.model,
            sess.headers,
            owner=owner,
            policy=foreground_policy,
            selected_endpoint_id=chat_request.selected_endpoint_id or getattr(sess, "endpoint_id", None),
        )
        candidate_request_factory = None
        selected_context_length = getattr(ctx, "context_length", 0)
        candidate_request_state = {
            "context_lengths": {0: selected_context_length},
            "requests": {0: ctx.messages},
            "trim_stats": {},
        }
        request_messages = ctx.messages
        if foreground_policy.enabled:
            request_messages = getattr(ctx, "route_messages", ctx.messages)
            candidate_request_factory, candidate_request_state = _chat_candidate_request_factory(
                request_messages,
                selected_context_length,
                session=sess,
                owner=owner,
            )
        requested_model = sess.model
        reply, actual_candidate, actual_model = await llm_call_async_with_route_fallback(
            foreground_candidates,
            request_messages,
            fallback_statuses=foreground_policy.eligible_statuses,
            candidate_request_factory=candidate_request_factory,
            temperature=(sess.temperature_override if getattr(sess, "temperature_override", None) is not None else 1.0),
            max_tokens=(sess.max_tokens_override if getattr(sess, "max_tokens_override", None) is not None else 0),
            prompt_type=preset_id,
            session_id=session,
            thinking_mode=thinking_mode,
            reasoning_effort=reasoning_effort,
        )
        actual_index = _candidate_index(foreground_candidates, actual_candidate)
        apply_compaction_state(
            sess,
            candidate_request_state.get("compactions", {}).get(actual_index),
        )
        requested_route = route_descriptors[0]
        actual_route = route_descriptors[actual_index]
        actual_trim = candidate_request_state.get("trim_stats", {}).get(actual_index, {})
        _clean_reply, _clean_md = clean_thinking_for_save(
            reply,
            {
                "model": actual_model,
                "requested_model": requested_model,
                "endpoint_id": actual_route.get("endpoint_id"),
                "endpoint_label": actual_route.get("endpoint_label"),
                "requested_endpoint_id": requested_route.get("endpoint_id"),
                "requested_endpoint_label": requested_route.get("endpoint_label"),
                "context_length": candidate_request_state["context_lengths"].get(
                    actual_index,
                    selected_context_length,
                ),
                "context_trimmed": bool(
                    actual_trim
                    and (
                        actual_trim.get("messages_after") < actual_trim.get("messages_before")
                        or actual_trim.get("tokens_after") < actual_trim.get("tokens_before")
                    )
                ),
            },
        )
        sess.add_message(ChatMessage("assistant", _clean_reply, metadata=_clean_md))

        from core.database import update_session_last_accessed
        update_session_last_accessed(session)
        session_manager.save_sessions()

        # Background tasks (memory, webhook, auto-name)
        run_post_response_tasks(
            sess, session_manager, session, message, reply, None,
            ctx.uprefs, memory_manager, memory_vector, webhook_manager,
            character_name=ctx.preset.character_name,
            owner=ctx.user,
            allow_background_extraction=not tool_policy.block_all_tool_calls,
        )

        return {
            "response": reply,
            "requested_model": requested_model,
            "model": actual_model,
            "requested_endpoint_id": requested_route.get("endpoint_id"),
            "requested_endpoint_label": requested_route.get("endpoint_label"),
            "endpoint_id": actual_route.get("endpoint_id"),
            "endpoint_label": actual_route.get("endpoint_label"),
        }

    # ------------------------------------------------------------------ #
    # POST /api/chat_stream
    # ------------------------------------------------------------------ #
    @router.post("/api/chat_stream")
    @suspicious_frequency(0.5, 60, "log")
    async def chat_stream(request: Request) -> StreamingResponse:
        require_api_token_scope(request, "chat")
        body = None
        try:
            if request.headers.get("content-type", "").startswith("application/json"):
                try:
                    body = await request.json()
                except json.JSONDecodeError as e:
                    raise HTTPException(400, f"Invalid JSON: {e}")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, f"Request parsing error: {e}")

        _set_user_time_from_request(request)

        form_data = await request.form()
        message = form_data.get("message")
        session = form_data.get("session")
        attachments = form_data.get("attachments")
        use_web = form_data.get("use_web")
        use_research = form_data.get("use_research")
        time_filter = form_data.get("time_filter")
        preset_id = form_data.get("preset_id")
        selected_endpoint_id = str(
            form_data.get("selected_endpoint_id")
            or (body or {}).get("selected_endpoint_id")
            or ""
        ).strip()
        # Issue #3229: API callers send JSON, not FormData.  Read from the
        # JSON body as fallback so callers who send {"allow_bash": true}
        # actually get bash enabled.
        allow_bash = form_data.get("allow_bash") or (body or {}).get("allow_bash")
        allow_web_search = form_data.get("allow_web_search") or (body or {}).get("allow_web_search")
        use_rag = form_data.get("use_rag")
        search_context = form_data.get("search_context")  # pre-fetched web search results (compare mode)
        compare_mode = str(form_data.get("compare_mode", "")).lower() == "true"
        thinking_mode = str(form_data.get("thinking_mode") or "").strip().lower()
        thinking_mode = thinking_mode if thinking_mode in {"on", "off"} else None
        raw_effort = str(form_data.get("reasoning_effort") or (body or {}).get("reasoning_effort") or "").strip().lower()
        reasoning_effort = raw_effort if raw_effort else None
        temperature_override = None
        raw_temperature = form_data.get("temperature")
        if raw_temperature not in (None, ""):
            try:
                temperature_override = min(2.0, max(0.0, float(raw_temperature)))
            except (TypeError, ValueError):
                raise HTTPException(400, "temperature must be a number between 0 and 2")
        incognito = str(form_data.get("incognito", "")).lower() == "true"
        plan_mode = str(form_data.get("plan_mode") or (body or {}).get("plan_mode") or "").lower() == "true"
        chat_mode = str(form_data.get("mode", "")).lower()  # 'chat' or 'agent'
        client_runtime_context = None
        raw_client_runtime_context = (
            form_data.get("client_runtime_context")
            or (body or {}).get("client_runtime_context")
        )
        if raw_client_runtime_context:
            try:
                parsed_client_runtime_context = (
                    json.loads(raw_client_runtime_context)
                    if isinstance(raw_client_runtime_context, str)
                    else raw_client_runtime_context
                )
                if isinstance(parsed_client_runtime_context, dict):
                    client_runtime_context = _parse_client_runtime_context(parsed_client_runtime_context)
            except Exception:
                client_runtime_context = {}
        tool_approval_id = (
            form_data.get("tool_approval_id")
            or (body or {}).get("tool_approval_id")
        )
        tool_approval_decision = (
            form_data.get("tool_approval_decision")
            or (body or {}).get("tool_approval_decision")
        )
        exact_tool_approval = None
        pending_tool_approval = None
        retired_tool_approval_taint = False
        external_untrusted_context_seen = False
        tool_approval_continuation = False
        # Workspace: confine the agent's file/shell tools to this folder.
        workspace, workspace_rejected = _resolve_request_workspace(
            request, form_data.get("workspace") or form_data.get("cwd")
        )
        # Plan mode is a modifier on agent mode — it only makes sense with tools.
        if plan_mode:
            chat_mode = "agent"
        # An approved plan being EXECUTED: the frontend sends the checklist back
        # on each turn so we can pin it in context. This way a long plan on a
        # weak model survives history truncation — the agent can always re-read
        # the plan. Ignored while still proposing (plan_mode on). Capped so a
        # huge plan can't blow the prompt.
        approved_plan = ""
        if not plan_mode:
            approved_plan = (form_data.get("approved_plan") or "").strip()[:8192]
        # Did the USER explicitly pick agent mode? (vs. us auto-escalating
        # below). Skill extraction should only learn from real agent sessions,
        # not chats we quietly promoted for a notes/calendar intent.
        user_requested_agent = (chat_mode == "agent")
        _search_enabled = web_search_enabled_for_turn(allow_web_search, use_web)
        _explicit_web_intent = False
        _explicit_personal_store_intent = False
        _explicit_web_target = False
        _exact_url_fetch_intent = False
        _explicit_browser_intent = False
        _external_discovery_intent = False
        _explicit_private_browser_intent = False
        _clean_v3_private_browser_warm = False
        _contextual_browser_turn_followup = False
        _local_browser_render_intent = False
        if isinstance(message, str):
            _msg_l = message.lower()
            _explicit_url_target = _contains_explicit_url_target(_msg_l)
            _explicit_personal_store_intent = _is_personal_data_search_without_web_target(_msg_l)
            _explicit_web_target = bool(re.search(
                r"\b(?:web|internet|online|google|news|weather|website|url|browse|browser)\b",
                _msg_l,
            )) or _explicit_url_target
            _explicit_web_intent = (
                _explicit_url_target
                or bool(re.search(
                r"\b(search|look\s+(?:(?:this|that|it|them|these|those)\s*)?up|lookup|find\s*out|google|browse|web|online|latest|current|today|news|weather|forecast|rate|exchange\s+rate)\b",
                _msg_l,
                ))
                or requires_external_web_verification(message)
            ) and (not _explicit_personal_store_intent or _explicit_web_target)
            _explicit_browser_intent = _is_explicit_browser_automation_request(
                _msg_l
            )
            _external_discovery_intent = _is_external_discovery_request(_msg_l)
            if _external_discovery_intent:
                _explicit_web_intent = True
            _exact_url_fetch_intent = _authorizes_exact_url_fetch(_msg_l)
            # Browser automation is distinct from open-ended web search. This
            # is also used by reviewed email flows whose prompt contains an
            # exact unsubscribe URL and explicitly names private_browser.
            _explicit_private_browser_intent = bool(re.search(
                r"\bprivate[_ -]?brow(?:ser|esr|sr)\b",
                _msg_l,
            )) or bool(re.search(
                r"\bagent\s+unsubscribe\b.*\bhttps?://",
                _msg_l,
                re.DOTALL,
            ))
            if _explicit_private_browser_intent:
                _explicit_browser_intent = True
                # An exact browser workflow must not be downgraded to a
                # search-only turn merely because its URL is present.
                _explicit_web_intent = False
            # Rendering a workspace HTML page to an image uses the local
            # browser as an artifact tool, not as open-ended web access. Keep
            # that capability independent from the web-search toggle while
            # retaining the ordinary browser privilege and global policy
            # checks below.
            _local_browser_render_intent = bool(
                workspace and (
                    _local_media_needs_browser_render(message)
                    or _native_runtime_requires_local_browser(client_runtime_context)
                )
            )
        _allow_browser_for_web_turn = bool(
            _explicit_browser_intent
            or _local_browser_render_intent
            or (_explicit_web_intent and not _explicit_personal_store_intent)
            or _search_enabled
        )
        # Intent auto-escalation: if the user is clearly asking the assistant
        # to create a todo, reminder, or calendar event, promote chat → agent
        # for this turn so the LLM has access to manage_notes / manage_calendar.
        # This is a LIGHT promotion — see the disabled_tools block below, which
        # withholds shell/code/file tools so the model doesn't try to `bash`
        # its way through a plain chat request (and fail, especially with the
        # shell disabled).
        auto_escalated = False
        _tool_intent = _classify_tool_intent(message) if isinstance(message, str) else None
        # The opt-in trained-tools route owns its complete conversation loop.
        # Do not make each follow-up earn Agent mode again through the legacy
        # lexical intent classifier: that recreated the same per-turn RAG gate
        # this experiment is intended to remove (for example, add-note matched
        # while delete-notes silently fell back to plain chat).
        _clean_v3_route_requested = bool(
            selected_endpoint_id in _CLEAN_V3_ENDPOINT_ALIASES
            or _clean_v3_route_for_model(form_data.get("selected_model"))
        )
        # Classify workspace intent independently of chat→agent escalation.
        # Native terminal callers normally arrive in Agent mode already; they
        # still need their isolated execution contract, while ordinary native
        # product turns must use the product tool-family contract below.
        _workspace_agent_intent = bool(
            (
                _tool_intent
                and _tool_intent.needs_tools
                and _tool_intent.category in {"shell", "workspace"}
            )
            or _native_context_has_workspace_inputs(client_runtime_context)
        )
        if chat_mode == "chat" and _tool_intent and _tool_intent.needs_tools:
            chat_mode = "agent"
            auto_escalated = True
            if _workspace_agent_intent:
                allow_bash = "true"
            logger.info(
                "chat→agent auto-escalation: category=%s reason=%s",
                _tool_intent.category,
                _tool_intent.reason,
            )
        elif chat_mode == "chat" and _search_enabled:
            chat_mode = "agent"
            auto_escalated = True
            logger.info("chat→agent auto-escalation: search enabled")
        elif chat_mode == "chat" and _explicit_web_intent:
            chat_mode = "agent"
            auto_escalated = True
            logger.info("chat→agent auto-escalation: explicit web intent")
        elif chat_mode == "chat" and _explicit_private_browser_intent:
            chat_mode = "agent"
            auto_escalated = True
            logger.info("chat→agent auto-escalation: explicit private browser workflow")
        active_doc_id = form_data.get("active_doc_id", "").strip()
        active_doc_state = form_data.get("active_doc_state", "").strip().casefold()
        logger.info(
            "[doc-inject] chat_mode=%s, active_doc_id=%r, active_doc_state=%r",
            chat_mode, active_doc_id, active_doc_state,
        )

        # Active email reader — when the user has an email open in the UI, the
        # frontend passes its uid/folder/account so "reply", "summarize this",
        # etc. resolve to the real email instead of the agent inventing a
        # fake markdown draft.
        active_email_uid = form_data.get("active_email_uid", "").strip()
        active_email_folder = form_data.get("active_email_folder", "INBOX").strip() or "INBOX"
        active_email_account = form_data.get("active_email_account", "").strip()
        active_email_ctx: Optional[Dict[str, str]] = None
        # Always reset between requests so a stale active-email pointer from
        # a previous turn (different reader closed, different account, etc.)
        # can't leak in when the user has no email open this turn.
        try:
            from src.tool_implementations import clear_active_email
            clear_active_email()
        except Exception:
            pass
        if active_email_uid:
            active_email_ctx = {
                "uid": active_email_uid,
                "folder": active_email_folder,
                "account": active_email_account,
            }
            # Try to enrich with subject + from so the agent's system prompt
            # block can quote them. Best-effort: a stale cache is fine, a
            # missing email just means we pass uid/folder/account only.
            try:
                from routes.email_routes import _read_cache_get, _read_cache_key
                _ck = _read_cache_key(active_email_account or None, active_email_folder, active_email_uid, owner=get_current_user(request))
                _cached_email = _read_cache_get(_ck)
                if _cached_email and isinstance(_cached_email, dict):
                    active_email_ctx["subject"] = str(_cached_email.get("subject") or "")
                    active_email_ctx["from"] = str(
                        _cached_email.get("from_address")
                        or _cached_email.get("from")
                        or _cached_email.get("from_name")
                        or ""
                    )
                    _body_preview = (_cached_email.get("body") or "")[:2000]
                    if _body_preview:
                        active_email_ctx["body_preview"] = _body_preview
            except Exception as _e:
                logger.debug(f"[email-inject] cache enrich skipped: {_e}")
            # Stash so email tools can resolve "this email" without UID guessing.
            try:
                from src.tool_implementations import set_active_email
                set_active_email(
                    uid=active_email_uid,
                    folder=active_email_folder,
                    account=active_email_account or None,
                    subject=active_email_ctx.get("subject"),
                    sender=active_email_ctx.get("from"),
                )
            except Exception as _e:
                logger.debug(f"[email-inject] set_active_email failed: {_e}")
            logger.info(
                "[email-inject] active_email uid=%s folder=%s account=%s subject=%r",
                active_email_uid, active_email_folder, active_email_account or "(default)",
                active_email_ctx.get("subject", ""),
            )

        try:
            # Attachment-only sends and approval controls may omit message text.
            _has_atts = (
                bool(body and isinstance(body.get("attachments"), list) and body["attachments"])
                or bool(form_data.get("attachments"))
            )
            message, session = coerce_message_and_session(
                body, message, session, session_manager,
                allow_empty=(_has_atts or bool(tool_approval_id)),
            )
            # Verify ownership AFTER coerce (which may resolve a default session)
            # but BEFORE loading. Prevents cross-user session hijack.
            _verify_session_owner(request, session)
            sess = session_manager.get_session(session)
            session_mode = str(getattr(sess, "thinking_mode", "") or "off").lower()
            # An explicit request-scoped mode (headless eval, API client, or
            # UI override) wins over the persisted session default. The old
            # unconditional assignment made `thinking_mode=off` impossible
            # for an existing session and silently changed evaluation/model
            # contracts.
            if thinking_mode is None and session_mode in {"on", "off"}:
                thinking_mode = session_mode
            from src.model_profiles import supports_user_thinking_toggle
            if not supports_user_thinking_toggle(sess.model):
                thinking_mode = "off"
            if reasoning_effort is None and session_mode.startswith("effort:"):
                reasoning_effort = session_mode[7:].strip()
            from src.chatgpt_subscription import validate_reasoning_effort
            reasoning_effort = validate_reasoning_effort(sess.model, reasoning_effort)
            if getattr(sess, "temperature_override", None) is not None:
                temperature_override = float(sess.temperature_override)
            # A resumed session may omit workspace/cwd from the new request.
            # Restore the persisted session workspace only after ownership and
            # session loading, while preserving an explicit request value.
            workspace, workspace_rejected = _resolve_persisted_session_workspace(
                request,
                sess,
                current_workspace=workspace,
                current_rejected=workspace_rejected,
            )
            owner = effective_user(request)
            if tool_approval_id:
                _reject_delegated_tool_approval(request)
                from src.agent_runtime.authority import require_user_approval_request
                require_user_approval_request(request)
                pending_tool_approval = tool_approval_store.peek(tool_approval_id)
                normalized_owner = str(owner or "").strip().casefold()
                if (
                    pending_tool_approval is None
                    or pending_tool_approval.owner != normalized_owner
                    or pending_tool_approval.session_id != str(session)
                ):
                    raise HTTPException(
                        409,
                        "This tool approval is invalid, expired, or belongs to another thread.",
                    )
                pending_taint = bool(
                    pending_tool_approval.external_untrusted_context_seen
                )
                external_untrusted_context_seen = (
                    external_untrusted_context_seen or pending_taint
                )
                decision = str(tool_approval_decision or "").strip().lower()
                if decision not in {"approve", "approve_task", "deny"}:
                    raise HTTPException(400, "Invalid tool approval decision.")
                if plan_mode:
                    raise HTTPException(
                        409,
                        "Tool approvals cannot be consumed while plan mode is active.",
                    )
                exact_tool_approval = tool_approval_store.consume(
                    tool_approval_id,
                    decision=decision,
                    owner=owner,
                    session_id=session,
                )
                tool_approval_continuation = True
                if (
                    decision in {"approve", "approve_task"}
                    and exact_tool_approval is None
                ):
                    raise HTTPException(
                        409,
                        "This tool approval could not be consumed.",
                    )
                if not _mark_tool_approval_resolved(
                    sess,
                    tool_approval_id,
                    decision,
                ):
                    logger.warning(
                        "Tool approval %s was consumed but its persisted card could not be marked resolved",
                        tool_approval_id,
                    )
                if decision == "deny":
                    return StreamingResponse(
                        _tool_approval_resolution_stream(decision),
                        media_type="text/event-stream",
                    )
                # Approval is a control-plane continuation, not a new user turn.
                # Reuse the sealed interrupted request only for internal context,
                # retrieval, and policy reconstruction; never persist or display it.
                message = pending_tool_approval.continuation_query
                # The sealed server record, not mutable composer state,
                # restores the original action workspace.
                workspace = pending_tool_approval.workspace or None
                workspace_rejected = None
                if pending_tool_approval.document_id:
                    active_doc_id = pending_tool_approval.document_id
                # Restore only the coarse request toggle needed by the exact
                # sealed action. Current privilege, global-disable, incognito,
                # compare, and tool-policy gates still run.
                if pending_tool_approval.tool_name == "bash":
                    allow_bash = "true"
                if pending_tool_approval.tool_name in WEB_TOOL_NAMES:
                    allow_web_search = "true"
                    _search_enabled = True
                chat_mode = "agent"
            else:
                # A normal user message supersedes the card that was waiting
                # in this thread. Retire its opaque grant, but preserve the
                # originating provenance for this turn so dismissing a card
                # cannot make the same model-requested action authoritative.
                retired_tool_approval_taint = tool_approval_store.retire_for_session(
                    owner=owner,
                    session_id=session,
                )
                external_untrusted_context_seen = (
                    external_untrusted_context_seen or retired_tool_approval_taint
                )
            _reconcile_selected_route_from_request(request, sess, session, form_data, owner=owner)
            if _clear_orphaned_session_endpoint(sess, owner=owner):
                raise HTTPException(400, "Selected model endpoint was removed. Pick another model in Settings.")
            # Issue #587: picker shows a model from the endpoint cache but
            # s.model never made it onto the DB row (first-send race after
            # endpoint setup, or a previous endpoint delete/recreate). Pull
            # the first cached model off the matching endpoint so the
            # upstream isn't called with model="" (which surfaces as a
            # generic 401/503).
            _recover_empty_session_model(sess, session, owner=owner)
            if not getattr(sess, "model", "").strip():
                raise HTTPException(
                    400,
                    "No model selected for this chat. Open the model picker and choose one before sending.",
                )
            if not (getattr(sess, "endpoint_url", "") or "").strip():
                raise HTTPException(400, "Selected model endpoint is not configured")
            # Route reconciliation above can switch models after the request's
            # generation settings were parsed. Do not carry a stale thinking
            # toggle from the previously selected model into one that does not
            # expose that control (notably OpenRouter Grok 4.5, where enabling
            # reasoning can put the complete answer in reasoning_content).
            from src.model_profiles import supports_user_thinking_toggle
            if not supports_user_thinking_toggle(sess.model):
                thinking_mode = "off"
            # Both picker entries point at the same fine-tuned model. Clean
            # harness ownership follows that model, not the endpoint alias;
            # every other model continues through the legacy RAG path.
            _effective_tool_schema_mode = _configured_model_tool_surface(
                getattr(sess, "endpoint_url", ""),
                getattr(sess, "model", ""),
                owner,
            )
            _clean_v3_route_requested = _clean_v3_route_for_model(
                getattr(sess, "model", ""),
                _effective_tool_schema_mode,
            )
            _clean_v3_private_browser_warm = bool(
                _clean_v3_route_requested and _has_recent_private_browser_success(sess)
            )
            logger.info(
                "clean v3 private-browser capability: route=%s warm=%s",
                _clean_v3_route_requested,
                _clean_v3_private_browser_warm,
            )
            if _clean_v3_private_browser_warm:
                _explicit_browser_intent = True
            if chat_mode == "chat" and _clean_v3_route_requested:
                chat_mode = "agent"
                auto_escalated = True
                logger.info("chat→agent route ownership: clean v3 persisted endpoint")
            if (
                chat_mode == "chat"
                and isinstance(message, str)
                and (not _tool_intent or not _tool_intent.needs_tools)
                and _is_contextual_web_followup(message, sess)
            ):
                _tool_intent = ToolIntent(True, "web", "contextual web lookup follow-up")
                chat_mode = "agent"
                auto_escalated = True
                _workspace_agent_intent = False
                logger.info(
                    "chat→agent auto-escalation: category=%s reason=%s",
                    _tool_intent.category,
                    _tool_intent.reason,
                )
            _contextual_browser_turn_followup = bool(
                isinstance(message, str)
                and _is_contextual_browser_followup(message, sess)
            )
            if _contextual_browser_turn_followup:
                _explicit_browser_intent = True
                if chat_mode == "chat":
                    chat_mode = "agent"
                    auto_escalated = True
                    _workspace_agent_intent = False
                    logger.info("chat→agent auto-escalation: contextual browser/form follow-up")
            if not workspace and isinstance(message, str):
                _auto_workspace, _ = _resolve_workspace_from_message_path(request, message)
                if _auto_workspace:
                    workspace = _auto_workspace
                    chat_mode = "agent"
                    auto_escalated = True
                    _workspace_agent_intent = True
                    allow_bash = "true"
                    logger.info("chat→agent auto-escalation: explicit path workspace=%s", workspace)
        except SessionNotFoundError as e:
            raise HTTPException(404, str(e))
        except (ValueError, ValidationError):
            raise HTTPException(400, "Invalid request parameters")

        # ------------------------------------------------------------------ #
        # Privilege gates that must fire BEFORE any LLM work / token spend.
        #   1. allowed_models — reject if session.model isn't in the user's
        #      configured allowlist (empty list = "no restriction").
        #   2. max_messages_per_day — count user-role ChatMessage rows owned
        #      by this user in the last UTC day; 429 if at/over the cap.
        # Admins always have full privileges via get_privileges (returns
        # ADMIN_PRIVILEGES wholesale) so this is a no-op for them.
        _enforce_chat_privileges(request, sess)

        # Ensure session has auth headers
        resolve_session_auth(sess, session, owner=effective_user(request))

        # Check for research_pending BEFORE mode persist overwrites it
        # An approval response resumes the sealed agent action.  Do not let
        # mutable form fields, or a stale research_pending session marker,
        # consume the one-use grant on the unrelated research path.
        do_research = (
            not tool_approval_continuation
            and str(use_research).lower() == "true"
        )
        if not do_research and not tool_approval_continuation:
            if get_session_mode(session) == 'research_pending':
                do_research = True
                logger.info(f"Session {session} in research_pending — auto-triggering research")

        att_ids = []
        if tool_approval_continuation:
            # Browser composer state is unrelated to the action that was
            # reviewed.  The original turn remains in session history.
            att_ids = []
        elif body and isinstance(body.get("attachments"), list):
            att_ids = [str(x) for x in body["attachments"]]
        elif attachments:
            try:
                att_ids = [str(x) for x in json.loads(attachments)]
            except Exception as e:
                logger.warning("Failed to parse attachments JSON, ignoring attachments", exc_info=e)

        image_generation_session = _is_image_generation_session(sess, owner=effective_user(request))
        no_memory = str(form_data.get("no_memory", "")).lower() == "true"
        if image_generation_session:
            no_memory = True
            use_rag = "false"
            search_context = None
        pre_context_tool_policy = build_effective_tool_policy(
            last_user_message=message,
        )
        allow_tool_preprocessing = not pre_context_tool_policy.block_all_tool_calls
        foreground_policy = resolve_foreground_model_policy(
            owner=owner,
            allowed_models=_allowed_models_for_request(request),
        )

        # Decide once whether this turn runs on the compact (clean v3)
        # runtime. Every input is final here; the native workspace term of
        # the contract policy cannot veto a requested clean route. This one
        # value prepares the turn below and stamps its contract later, and
        # the agent loop dispatches on that stamp.
        _compact_preview_turn = uses_compact_preview_runtime(
            clean_route_requested=_clean_v3_route_requested,
            turn_contract_enabled=_turn_contract_enabled(
                exact_tool_approval=exact_tool_approval,
                runtime_surface=str((client_runtime_context or {}).get("surface") or ""),
                native_workspace_contract=False,
                clean_v3_route=_clean_v3_route_requested,
                full_schema_route=(_effective_tool_schema_mode == "full"),
            ),
            agent_mode=(chat_mode == "agent"),
            agent_permitted=_request_privileges(
                request, effective_user(request),
            ).get("can_use_agent", True),
            image_generation=image_generation_session,
        )
        # A compact turn resolves its typed context window once, here, with
        # the session's provider credentials. History shaping below and the
        # compact runtime both reuse this exact object, so the turn neither
        # probes twice nor mixes the legacy untyped lookup into it.
        _compact_context_resolution = None
        if _compact_preview_turn:
            from src.agent_runtime.context_resolution import resolve_effective_context
            _compact_context_resolution = await resolve_effective_context(
                sess.endpoint_url, sess.model, headers=sess.headers,
                client_runtime_context=client_runtime_context,
            )

        # Build shared context (stream path uses enhanced_message for context preface)
        ctx = await build_chat_context(
            sess, request, chat_handler, chat_processor,
            message=message,
            session_id=session,
            preset_id=preset_id,
            att_ids=att_ids,
            use_web=use_web,
            use_rag=use_rag,
            time_filter=time_filter,
            incognito=incognito,
            no_memory=no_memory,
            search_context=search_context,
            compare_mode=compare_mode,
            webhook_manager=webhook_manager,
            use_enhanced_message=True,
            # Skills index only ships when the model can actually call
            # manage_skills (agent mode). In plain chat or incognito the
            # index would be useless / unwanted noise.
            agent_mode=(chat_mode == "agent"),
            allow_tool_preprocessing=allow_tool_preprocessing,
            defer_context_shaping=foreground_policy.enabled,
            continuation_context_message=(
                pending_tool_approval.continuation_query
                if exact_tool_approval
                and pending_tool_approval
                and pending_tool_approval.continuation_query
                else None
            ),
            persist_user_message=not tool_approval_continuation and not is_internal_tool_request(request),
            context_resolution=_compact_context_resolution,
            interaction_mode=chat_mode,
            auto_escalated=auto_escalated,
        )
        if is_internal_tool_request(request):
            _append_internal_chat_context(ctx, message)

        _research_flags = {"do": do_research}  # Mutable container for generator scope

        # Browser turns explicitly declare whether the editor is visible. The
        # visible active tab is authoritative; a minimized/closed editor must
        # not be resurrected from session or process-global state. Legacy API
        # clients that omit active_doc_state retain the old fallback behavior.
        active_doc = None
        legacy_active_doc_fallback = not active_doc_state
        _doc_db = SessionLocal()
        try:
            if active_doc_id:
                logger.info(f"[doc-inject] active_doc_id from frontend: {active_doc_id}")
                # Scope to the caller's documents. The session and in-memory
                # fallbacks below are already owner/session-bound; this
                # explicit-id path looked up by id alone, so a user could
                # inject another user's document by passing its id.
                _doc_q = _doc_db.query(DBDocument).filter(DBDocument.id == active_doc_id)
                active_doc = _owner_session_filter(_doc_q, ctx.user).first()
                if active_doc:
                    doc_session = active_doc.session_id
                    doc_owner = getattr(active_doc, "owner", None)
                    if doc_owner and ctx.user and doc_owner != ctx.user:
                        logger.warning(
                            "[doc-inject] ignoring active_doc_id %s owned by another user",
                            active_doc_id,
                        )
                        active_doc = None
                    else:
                        # NOTE: previously dropped the doc when doc.session_id
                        # != current chat session — but that broke the common
                        # case of "open an email draft from one chat, ask a
                        # different chat to write into it". The frontend only
                        # sends active_doc_id only for the currently visible
                        # active editor tab,
                        # the UI, and we already owner-checked above, so trust
                        # the explicit signal. We just log the mismatch and
                        # re-bind the doc to the current session for ownership
                        # and document-history continuity.
                        if doc_session and doc_session != session:
                            logger.info(
                                "[doc-inject] cross-session active_doc_id %s (was session %s, now %s) — accepting and rebinding",
                                active_doc_id, doc_session, session,
                            )
                            try:
                                active_doc.session_id = session
                                _doc_db.commit()
                            except Exception as _e:
                                _doc_db.rollback()
                                logger.warning(f"[doc-inject] session rebind failed: {_e}")
                        logger.info(f"[doc-inject] found by ID: title={active_doc.title!r}, lang={active_doc.language!r}, is_active={active_doc.is_active}, content_len={len(active_doc.current_content or '')}")
                else:
                    logger.warning(f"[doc-inject] NOT FOUND by ID {active_doc_id}")
            if not active_doc and legacy_active_doc_fallback:
                _email_doc_q = _doc_db.query(DBDocument).filter(
                    DBDocument.session_id == session,
                    DBDocument.is_active == True,
                    DBDocument.language == "email",
                )
                active_doc = _owner_session_filter(_email_doc_q, ctx.user).order_by(DBDocument.updated_at.desc()).first()
                if active_doc:
                    logger.info(f"[doc-inject] found email draft by session fallback: title={active_doc.title!r}")
            if not active_doc and legacy_active_doc_fallback:
                _session_doc_q = _doc_db.query(DBDocument).filter(
                    DBDocument.session_id == session,
                    DBDocument.is_active == True
                )
                active_doc = _owner_session_filter(_session_doc_q, ctx.user).order_by(DBDocument.updated_at.desc()).first()
                if active_doc:
                    logger.info(f"[doc-inject] found by session fallback: title={active_doc.title!r}")
            # Last resort: the document the agent itself just created/edited
            # (tracked in-memory by the tool layer). This rescues docs that
            # got orphaned from their session (session_id NULL) — otherwise
            # neither lookup above can associate them with this conversation,
            # so the agent never sees what it just wrote. Guarded so we never
            # leak a doc that belongs to a DIFFERENT session.
            if not active_doc and legacy_active_doc_fallback:
                try:
                    from src.agent_tools.document_tools import get_active_document
                    _mem_id = get_active_document()
                    if _mem_id:
                        _mem_q = _doc_db.query(DBDocument).filter(DBDocument.id == _mem_id)
                        cand = _owner_session_filter(_mem_q, ctx.user).first()
                        is_sft_fixture_user = str(ctx.user or "").startswith("sft_")
                        if (
                            cand
                            and cand.session_id == session
                            or (
                                cand
                                and not cand.session_id
                                and not is_sft_fixture_user
                            )
                        ):
                            active_doc = cand
                            logger.info(f"[doc-inject] found by in-memory active id: title={active_doc.title!r} (session_id={cand.session_id!r})")
                except Exception as _e:
                    logger.debug(f"[doc-inject] in-memory fallback failed: {_e}")
            if not active_doc:
                logger.info(f"[doc-inject] no active doc for session {session}")
            if active_doc:
                _doc_db.expunge(active_doc)
        except Exception as e:
            logger.warning(f"Failed to query active document: {e}")
        finally:
            _doc_db.close()

        if (
            active_doc
            and chat_mode == "chat"
            and isinstance(message, str)
            and re.search(
                r"\b(?:make|sound|rewrite|revise|rework|edit|update|change|polish|professional|fun|formal|casual|shorter|longer|friendlier|warmer|clearer)\b",
                message,
                re.IGNORECASE,
            )
        ):
            chat_mode = "agent"
            auto_escalated = True
            logger.info(
                "chat→agent auto-escalation: active document edit request doc_id=%s",
                getattr(active_doc, "id", ""),
            )

        # Build disabled-tools set from frontend toggles + user privileges
        # Product Agent turns resolve a contract once.  A native desktop/web
        # surface is still the product surface: its runtime marker must not
        # bypass the contract and let tool RAG replace (for example) a browser
        # request with shell tools.  Only an actual environment-owned TUI, or
        # a native terminal task that explicitly needs its isolated workspace,
        # retains a separate declared execution contract.
        _runtime_surface = str((client_runtime_context or {}).get("surface") or "")
        _native_workspace_contract = bool(
            _runtime_surface == "odysseus-native"
            and (client_runtime_context or {}).get("terminal_agent") is True
            and (
                _workspace_agent_intent
                or (
                    (client_runtime_context or {}).get("unattended_mode") is True
                    and workspace
                )
            )
        )
        _use_turn_contract = _turn_contract_enabled(
            exact_tool_approval=exact_tool_approval,
            runtime_surface=_runtime_surface,
            native_workspace_contract=_native_workspace_contract,
            clean_v3_route=_clean_v3_route_requested,
            full_schema_route=(_effective_tool_schema_mode == "full"),
        )
        _turn_history = getattr(sess, "history", []) or []
        from src.turn_contract import corrected_browser_target
        _corrected_browser_target = corrected_browser_target(message, _turn_history)
        _turn_capabilities = requested_capabilities(
            message, _turn_history,
            active_document=bool(active_doc), workspace=bool(workspace),
            image_attachment=any(str(a.get('mime') or '').startswith('image/') for a in (ctx.preprocessed.attachment_meta or [])),
        ) if _use_turn_contract else frozenset()
        if 'image_editing' in _turn_capabilities and ctx.preprocessed.attachment_meta:
            image_refs = ['odysseus://attachment/' + str(a['id']) for a in ctx.preprocessed.attachment_meta
                          if a.get('id') and str(a.get('mime') or '').startswith('image/')]
            if image_refs:
                image_edit_context = {'role': 'system', 'content':
                    'For the requested image edit, use edit_image with action=prompt and image_id set to the uploaded image reference: '
                    + ', '.join(image_refs) + '. Pass the requested changes as prompt. The backend sends the actual source pixels; a description or stock-image URL is not an edited image.'}
                ctx.messages.insert(0, image_edit_context)
                if foreground_policy.enabled:
                    getattr(ctx, 'route_messages', ctx.messages).insert(0, dict(image_edit_context))
        if _use_turn_contract and _explicit_browser_intent:
            # Interactive navigation is already an unambiguous request for
            # the browser family.  The lexical family classifier intentionally
            # stays conservative, so phrases such as "go to IKEA's site" can
            # otherwise produce an empty contract despite the browser router
            # having classified them correctly.
            _turn_capabilities = _turn_capabilities | {"search_browser"}
        if _use_turn_contract and _external_discovery_intent:
            _turn_capabilities = _turn_capabilities | {"search_browser"}
        if (
            _use_turn_contract
            and not _turn_capabilities
            and _clean_v3_private_browser_warm
            and _is_contextual_browser_followup(message, sess)
        ):
            # Typed successful browser state plus a referential page request is
            # sufficient to retain the browser family. Do not union this into
            # explicit notes/calendar/email requests merely because a browser
            # happened to run earlier in the session.
            _turn_capabilities = frozenset({'search_browser'})
        _active_turn_capabilities = _turn_capabilities
        if _use_turn_contract and active_doc:
            # A visible, owner-checked editor is a turn capability even when
            # the request classifier focuses on another task or misses a
            # pasted revision request. This only offers permitted schemas;
            # it never requires or performs a document mutation.
            _turn_capabilities = _turn_capabilities | {"documents"}
        # Same decision that prepared the turn; it only stamps the contract
        # inside the agent-contract branch below.
        _clean_v3_preview = _compact_preview_turn
        # requested_capabilities already inherits a typed, recently executed
        # family for referential follow-ups. Do not additionally union stale
        # families into an explicit new request: that inflated regular-model
        # schemas and made family switches less reliable. The exact Odysseus
        # model receives the trained compact form of this same contract below.
        _warm_turn_capabilities = frozenset()
        if _use_turn_contract and _turn_capabilities and "search_browser" not in _turn_capabilities:
            _explicit_web_intent = False
        disabled_tools = set()
        # Minting is admin-only, so every owner-keyed check below answers
        # "admin" for a token. Cap it at the non-admin policy instead.
        # stream_agent_loop repeats this from delegated_credential.
        _delegated_credential = is_delegated_credential(request)
        if _delegated_credential:
            disabled_tools.update(delegated_credential_blocked_tools())
        # Only disable bash when the caller *explicitly* set it to a falsy
        # value. When unset (None), defer to per-user privilege checks below.
        # Web search is per-turn opt-in: either the chat pre-search setting
        # (`use_web=true`) or agent web toggle (`allow_web_search=true`) must
        # explicitly enable it.
        if allow_bash is not None and str(allow_bash).lower() != "true":
            disabled_tools.add("bash")
        _model_lower = str(getattr(sess, "model", "") or "").lower()
        _qwen_tool_router_selected = (
            "qwen38-tool-router" in _model_lower
            or "qwen35-9b-tool-router" in _model_lower
            or "qwen3.5-9b-tool-router" in _model_lower
            or "odysseus-qwen3.5-9b" in _model_lower
        )
        _explicit_past_chat_search_intent = bool(
            isinstance(message, str)
            and re.search(r"\b(?:search|find|look\s*up)\b", message, re.IGNORECASE)
            and re.search(
                r"\b(?:prior|past|previous|old)\s+(?:chats?|sessions?|conversations?)\b",
                message,
                re.IGNORECASE,
            )
        )
        if _explicit_past_chat_search_intent:
            _explicit_web_intent = False
        _explicit_web_intent = _explicit_web_intent or bool(
            _tool_intent
            and _tool_intent.category == "web"
            and not _explicit_personal_store_intent
            and not _explicit_past_chat_search_intent
        )
        _contextual_web_link_followup = _is_contextual_web_link_followup(
            getattr(sess, "history", []) or [],
            message,
        )
        _contextual_web_turn_followup = bool(
            "search_browser" in _turn_capabilities
            and _is_contextual_web_followup(message, sess)
            and _has_recent_web_tool_event(sess)
            and not _explicitly_denies_web_lookup(message)
        )
        _clean_v3_web_intent = bool(
            _clean_v3_route_requested
            and "search_browser" in _turn_capabilities
            and not _explicitly_denies_web_lookup(message)
        )
        if (
            (_explicit_web_intent or _contextual_web_link_followup
             or _contextual_web_turn_followup or _clean_v3_web_intent)
            and web_intent_may_enable_for_turn(
                None if (_contextual_web_turn_followup or _clean_v3_web_intent)
                else allow_web_search,
                message_denies_lookup=_explicitly_denies_web_lookup(message),
            )
        ):
            _search_enabled = True
            allow_web_search = "true"
        if is_web_search_explicitly_denied(allow_web_search) or not _search_enabled:
            disabled_tools.update(WEB_TOOL_NAMES)
            if not _explicit_browser_intent:
                disabled_tools.add("youtube_tool")
            if not (_explicit_browser_intent or _local_browser_render_intent):
                disabled_tools.add("private_browser")
        if _exact_url_fetch_intent:
            # A pasted URL grants only the exact-target reader. Keep broad
            # search and interactive browsing behind their normal toggles.
            disabled_tools.discard("web_fetch")
        if (
            _explicit_web_intent
            and not _use_turn_contract
            and _effective_tool_schema_mode != "full"
        ):
            # A direct lookup/search request should not drift into personal
            # tools or shell fallbacks. A combined web+workspace deliverable
            # is the exception: it still needs native file/Python tools after
            # gathering evidence from the web.
            disabled_tools.update({
                "search_chats", "manage_skills", "manage_memory",
                "create_document", "edit_document", "update_document",
                "send_email", "reply_to_email",
                "manage_notes", "manage_calendar", "manage_tasks",
                "api_call",
            })
            _web_workspace_output = bool(
                workspace
                and isinstance(message, str)
                and re.search(r"(?:^|\s)/workspace/[^\s]+", message)
                and re.search(
                    r"(?:\b(?:create|generate|save|write|render|export|produce|build|make)\b|"
                    r"创建|生成|保存|写入|制作|截取|剪辑|拼接|导出)",
                    message,
                    re.IGNORECASE,
                )
            )
            if not _web_workspace_output:
                disabled_tools.update({
                    "bash", "python", "read_file", "write_file", "edit_file",
                })
            if _search_enabled:
                disabled_tools.difference_update(WEB_TOOL_NAMES)
            else:
                disabled_tools.update(WEB_TOOL_NAMES)
        elif _search_enabled:
            disabled_tools.difference_update(WEB_TOOL_NAMES)

        # Nobody/incognito mode: deny tools that would expose the user's
        # persistent memory, past chats, or other identity-linked data.
        if incognito:
            disabled_tools.update({
                "manage_memory",      # persistent memory store
                "search_chats",       # past chat history
                "manage_skills",      # skill presets tied to user
                "create_session",
                "list_sessions",
                "manage_session",
                "send_to_session",
                "chat_with_model",
            })

        # Active email reader open → strip the tools that let the agent drift
        # away from the visible email or skip review. The only allowed compose
        # path is ui_control open_email_reply, which opens the same draft editor
        # as the Reply button with the generated body pre-filled. This prevents
        # the model from falling back to direct SMTP when it botches a draft
        # call, and prevents fake email-shaped documents.
        if active_email_ctx and active_email_ctx.get("uid"):
            disabled_tools.update({
                "create_document",
                "send_email",
                "reply_to_email",
                "mcp__email__send_email",
                "mcp__email__reply_to_email",
            })

        # Enforce per-user privileges
        # Bearer clients enter the agent loop as the sandboxed ``api`` user,
        # but their token is owned by the real account. Use that owner here so
        # a permitted TUI/WebUI client does not inherit api's default denial.
        _user = effective_user(request)
        _privs = _request_privileges(request, _user)
        if _privs:
            if not _privs.get("can_use_bash", True):
                disabled_tools.update(FAMILY_TOOLS["shell_files"])
            if not _privs.get("can_use_browser", True):
                disabled_tools.update(_BROWSER_MCP_TOOLS)
                disabled_tools.add("private_browser")
            if not _privs.get("can_use_documents", True):
                disabled_tools.update({"manage_documents", "create_document", "edit_document", "update_document", "suggest_document"})
            if not _privs.get("can_generate_images", True):
                disabled_tools.update({"generate_image", "edit_image"})
            if not _privs.get("can_manage_memory", True):
                disabled_tools.update({"manage_memory", "manage_skills"})
            if not _privs.get("can_use_research", True):
                _research_flags["do"] = False
                disabled_tools.update({"trigger_research", "manage_research"})
            if not _privs.get("can_use_agent", True):
                _effective_mode = 'chat'
                chat_mode = 'chat'
        # Global admin disabled tools
        from src.settings import get_setting
        _global_disabled = get_setting("disabled_tools", [])
        if _global_disabled and isinstance(_global_disabled, list):
            disabled_tools.update(_global_disabled)

        # Light auto-escalation: the user is in chat mode and just expressed a
        # notes/calendar/email intent. Grant the relevant managers but withhold
        # the heavy "do things on the computer" tools — otherwise the model
        # tries to shell out for a request that never needed it, then fails
        # (and looks broken when the shell is disabled).
        if auto_escalated and not _workspace_agent_intent and not _use_turn_contract:
            disabled_tools.update({
                "bash", "python", "read_file", "write_file",
            })
            if not _allow_browser_for_web_turn:
                disabled_tools.update(_BROWSER_MCP_TOOLS)

        # Disable document tools in compare sessions — they break the pane UI
        if sess.name and sess.name.startswith("[CMP]"):
            disabled_tools.update({"create_document", "edit_document", "update_document"})

        # Compare mode: disable tools based on compare type
        if compare_mode:
            _compare_strip = {
                "create_document", "edit_document", "update_document",
                "chat_with_model", "create_session", "list_sessions",
                "send_to_session",
                "pipeline", "manage_session", "manage_memory", "list_models",
                "generate_image", "ui_control",
            }
            disabled_tools.update(_compare_strip)
            # In chat mode compare, disable ALL agent tools (no bash, python, file ops)
            if chat_mode == 'chat':
                disabled_tools.update({"bash", "python", "read_file", "write_file", "web_search", "web_fetch", "search_chats", "manage_tasks"})

        # Plan mode: investigate read-only, propose a plan, don't mutate. Block
        # every tool not on the read-only allowlist. (stream_agent_loop enforces
        # this again + drops MCP, so this is belt-and-suspenders.)
        if plan_mode:
            from src.tool_security import plan_mode_disabled_tools
            disabled_tools.update(plan_mode_disabled_tools())

        tool_policy = build_effective_tool_policy(
            disabled_tools=disabled_tools,
            last_user_message=message,
        )
        if str(_user or "").startswith("sft_"):
            logger.info(
                "[sft-policy-audit] owner=%s personal_disabled=%s "
                "compare=%s explicit_web=%s privileges=%s global_disabled=%s",
                _user,
                sorted(set(disabled_tools) & {"manage_notes", "manage_calendar", "manage_tasks"}),
                bool(compare_mode),
                bool(_explicit_web_intent),
                _privs,
                _global_disabled,
            )
        disabled_tools = tool_policy.all_disabled_names()
        # ui_control executes server-side, while these interactive toggles are
        # resolved from this request. Carry the effective, sanitized booleans
        # into the agent runtime so a get_toggles call reports real turn state
        # instead of claiming the backend cannot see the client.
        client_runtime_context = dict(client_runtime_context or {})
        client_runtime_context["web_ui_state"] = {
            "web": "web_search" not in disabled_tools,
            "bash": "bash" not in disabled_tools,
            "rag": str(use_rag if use_rag is not None else "true").lower() != "false",
            "research": str(form_data.get("use_research") or "").lower() == "true",
            "incognito": bool(incognito),
            "document_editor": not {
                "manage_documents", "create_document", "edit_document", "update_document",
            }.issubset(disabled_tools),
        }
        # Capture permission state before schema selection/reconciliation.
        # Only deterministic request intent supplies grants, never inventory.
        _request_authority = request_authority_for_http(
            request, message, owner=_user, session_id=session, workspace=workspace,
            history=_turn_history, policy=tool_policy,
            client_runtime_context=client_runtime_context,
            active_document=bool(active_doc),
            image_attachment=any(str(a.get('mime') or '').startswith('image/')
                                 for a in (ctx.preprocessed.attachment_meta or [])),
            capabilities=({'search_browser'} if (
                _explicit_browser_intent or _external_discovery_intent
            ) else ()),
        )
        if exact_tool_approval is not None:
            from src.agent_runtime.authority import RequestAuthority
            _request_authority = (
                exact_tool_approval.pending.request_authority
                or RequestAuthority.empty(owner=_user, session_id=session, workspace=workspace)
            ).restrict(tool_policy)
        _turn_contract = None
        # Image models execute directly, not through the text-agent inventory.
        # Keep the permission policy above, but do not apply routing omissions
        # as denials to this separate execution path.
        if _use_turn_contract and chat_mode == "agent" and not image_generation_session:
            from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
            from src.tool_utils import get_mcp_manager
            from src.tool_security import blocked_tools_for_owner
            from src.agent_loop import (
                _load_mcp_disabled_map, _workspace_tools_disabled_for_owner,
                _SFT_DISABLED_WORKSPACE_TOOLS,
            )
            # host_shell belongs to an environment-owned execution bridge;
            # the product WebUI has no such executable runtime.
            _contract_schemas = [s for s in FUNCTION_TOOL_SCHEMAS
                                 if s["function"]["name"] != "host_shell"]
            _contract_mgr = get_mcp_manager()
            _owner_blocked = blocked_tools_for_owner(_user)
            if _delegated_credential:
                _owner_blocked.update(delegated_credential_blocked_tools())
            if (
                _workspace_tools_disabled_for_owner(_user)
                and not _native_workspace_contract
            ):
                # The SFT fixture guard protects the WebUI user's backend
                # filesystem. A server-validated odysseus-native request owns
                # a separate confined workspace, matching the exemption in
                # _strip_workspace_tools_for_sft inside the agent runtime.
                disabled_tools.update(_SFT_DISABLED_WORKSPACE_TOOLS)
            if _contract_mgr and not plan_mode and not tool_policy.disable_mcp and not _owner_blocked:
                _contract_schemas.extend(_contract_mgr.get_all_openai_schemas(_load_mcp_disabled_map()))
            if _explicitly_denies_tool_use(message):
                disabled_tools.update(
                    schema["function"]["name"] for schema in _contract_schemas
                )
            _contract_policy = build_effective_tool_policy(
                disabled_tools=disabled_tools | set(_owner_blocked),
                last_user_message=message,
            )
            _warm_tools = _successful_session_tool_names(sess)
            _selected_tools = selected_tools_for_request(message)
            if _selected_tools is None and _contextual_browser_turn_followup:
                # A referential retry targets the browser state established by
                # typed successful execution. Keep the exact browser tool;
                # do not broaden the turn to web search/fetch merely because
                # the wording no longer repeats the original URL.
                _selected_tools = frozenset({"private_browser"})
            _required_tools = set(_selected_tools or ())
            _selected_tools = preserve_bound_editor_selected_tools(
                message,
                _selected_tools,
                active_document=bool(active_doc),
            )
            _explicit_fixture_personal_tools = (
                set(_selected_tools or ())
                & {"manage_notes", "manage_calendar", "manage_tasks"}
            ) - disabled_tools - set(_owner_blocked)
            if (
                str(_user or "").startswith("sft_")
                and _explicit_fixture_personal_tools
            ):
                _fixture_tool_families = {
                    "manage_notes": "notes",
                    "manage_calendar": "calendar",
                    "manage_tasks": "tasks",
                }
                # Explicit permitted personal tools may restore a family,
                # but never override disabled tools or owner restrictions.
                # Do not erase other
                # domains already detected for a causal multi-store request
                # (for example calendar -> email -> calendar).
                _turn_capabilities = frozenset(
                    set(_turn_capabilities)
                    | {
                        _fixture_tool_families[name]
                        for name in _explicit_fixture_personal_tools
                    }
                )
                _active_turn_capabilities = _turn_capabilities
                _contract_policy = build_effective_tool_policy(
                    disabled_tools=disabled_tools | set(_owner_blocked),
                    last_user_message=message,
                )
                logger.info(
                    "[sft-policy-audit] explicit personal contract tools=%s capabilities=%s",
                    sorted(_explicit_fixture_personal_tools),
                    sorted(_turn_capabilities),
                )
            if (
                _selected_tools == {"web_search"}
                and requests_independent_web_source(message)
                and _most_recent_successful_web_tool(sess) in {"web_search", "web_fetch"}
            ):
                # Candidate URLs already exist in typed web evidence. A second
                # source is a different page read, not the cached search again.
                _selected_tools = {"web_fetch"}
            _exact_selected_native_chain = bool(
                _selected_tools
                and {"write_file", "read_file"}.issubset(_selected_tools)
                and set(_selected_tools).intersection({"inspect_media", "extract_text"})
                and set(_selected_tools).issubset(
                    {"inspect_media", "extract_text", "write_file", "read_file"}
                )
            )
            if _selected_tools is None and _contextual_web_turn_followup:
                # A referential follow-up should retain the proven web route,
                # not reopen every search/browser schema. Besides reducing
                # ambiguity, this avoids one unrelated provider-incompatible
                # schema invalidating an otherwise valid follow-up request.
                _recent_web_tool = _most_recent_successful_web_tool(sess)
                if _recent_web_tool:
                    _selected_tools = {_recent_web_tool}
            if (_selected_tools is None and active_email_ctx
                    and active_email_ctx.get("uid") and "email" in _turn_capabilities):
                # The review UI is a declared dependency, not permission to
                # substitute direct sending or document creation.
                _turn_capabilities = _turn_capabilities | {"ui"}
                _required_tools.add("ui_control")
            if _corrected_browser_target:
                _selected_tools = {'private_browser', 'web_fetch', 'web_search'}
                _required_tools = {'private_browser'}
            _turn_contract = resolve_turn_contract(
                capabilities=_turn_capabilities, schemas=_contract_schemas,
                policy=_contract_policy, required_tools=_required_tools,
                required_capabilities=_active_turn_capabilities,
                selected_tools=_selected_tools,
                always_available_tools=(FAMILY_TOOLS["documents"] if active_doc else ()),
                warm_tools=_warm_tools,
                message=message, history=getattr(sess, "history", []) or [],
            )
            _request_authority = _request_authority.restrict(_contract_policy)
            # Resolution already applies user, owner, and global policy. An
            # admitted tool must not later be rejected by the stale
            # pre-contract disabled snapshot during execution.
            disabled_tools.difference_update(_turn_contract.offered)
            _routed_turn_contract = _turn_contract
            if _clean_v3_preview:
                from dataclasses import replace
                from src.clean_agent_preview import (
                    INTERACTIVE_CORE_TOOLS, MODE, NATIVE_WORKSPACE_TOOLS, PREVIEW_TOOLS, canonical,
                    scope_preview_contract,
                )
                from src.turn_contract import resolve_full_inventory_contract
                _warm_canonical = {canonical(name) for name in _warm_tools}
                _clean_runtime_tools = PREVIEW_TOOLS | (
                    NATIVE_WORKSPACE_TOOLS
                    if _native_workspace_contract else frozenset()
                )
                _preview_schemas = [
                    s for s in _contract_schemas
                    if canonical(s['function']['name']) in _clean_runtime_tools
                ]
                if (
                    _native_workspace_contract
                    and _prefers_structured_document_tools(message)
                ):
                    # Keep extraction/discovery and Python/file artifact tools,
                    # but remove shell as a competing source-discovery route.
                    _preview_schemas = [
                        s for s in _preview_schemas
                        if canonical(s['function']['name']) != 'bash'
                        or canonical(s['function']['name']) in _warm_canonical
                    ]
                _turn_contract = scope_preview_contract(
                    replace(resolve_full_inventory_contract(
                        schemas=_preview_schemas,
                        policy=_contract_policy,
                    ), selection_mode=MODE),
                    _routed_turn_contract,
                    _active_turn_capabilities,
                    # A validated native workspace is a persistent capability,
                    # including on referential turns such as "undo that".
                    # scope_preview_contract still intersects the policy-filtered
                    # executable inventory; this cannot restore denied tools.
                    # A fully specified media -> artifact operation already
                    # has an exact routed contract. Adding the whole native
                    # workspace inventory here reintroduced overlapping PDF
                    # readers and caused the model to abandon the selected
                    # OCR operation. A task-only turn likewise has an exact
                    # personal manager; core shell/Web tools are not task
                    # fallbacks. Other turns retain warm and workspace tools.
                    extra_tools=(
                        frozenset()
                        if _exact_selected_native_chain or _active_turn_capabilities == frozenset({"tasks"}) or (
                            _active_turn_capabilities in (
                                frozenset({"transcription"}), frozenset({"ocr"}),
                            )
                            and not _selected_tools
                        )
                        else INTERACTIVE_CORE_TOOLS | _warm_tools | (
                            NATIVE_WORKSPACE_TOOLS | (
                                {"private_browser"} if _local_browser_render_intent else frozenset()
                            )
                            if _native_workspace_contract
                            else frozenset()
                        )
                    ),
                )
                from src.tool_routing_experiment import experiment_mode, select_experiment_inventory
                _experiment_mode = experiment_mode(
                    request.headers.get('x-odysseus-routing-experiment'), _user,
                    model=getattr(sess, 'model', ''),
                )
                if _experiment_mode != 'baseline':
                    _turn_contract = select_experiment_inventory(
                        replace(resolve_full_inventory_contract(
                            schemas=[s for s in _contract_schemas
                                     if canonical(s['function']['name']) in _clean_runtime_tools],
                            policy=_contract_policy,
                        ), selection_mode=MODE),
                        _routed_turn_contract, _turn_history, _experiment_mode,
                        user_text=message,
                        browser_requested=_explicit_browser_intent,
                    )
            # Every recovery path receives the same scope denial. The central
            # dispatcher also checks the immutable contract after rewrites.
            disabled_tools.update(
                s["function"]["name"] for s in _contract_schemas
                if not _turn_contract.permits(s["function"]["name"])
            )
            # Contract resolution is the final policy-and-routing authority.
            # Some legacy/API-model paths arrive with a stale disabled snapshot
            # assembled before routing.  The scope-denial pass above may retain
            # an admitted name through aliases or an earlier inventory view;
            # never let that stale snapshot reject a tool the final immutable
            # contract explicitly offers.  User/global denials cannot be
            # restored here because resolve_turn_contract filtered them out.
            disabled_tools.difference_update(_turn_contract.offered)
            tool_policy = build_effective_tool_policy(
                disabled_tools=disabled_tools, last_user_message=message,
            )
        research_blocked_by_policy = bool(
            tool_policy.blocks("trigger_research")
            or tool_policy.blocks("manage_research")
        )
        effective_do_research = bool(
            do_research and _research_flags["do"] and not research_blocked_by_policy
        )

        if chat_mode == "agent":
            runtime_msg = _client_runtime_context_system_message(
                client_runtime_context,
                disabled_tools=disabled_tools,
                # Agent turns render the full directive set in
                # _tui_runtime_directive; keeping it here too duplicates
                # routing instructions in the model context.
                include_directives=(chat_mode != "agent"),
            )
            if runtime_msg:
                ctx.messages.insert(0, runtime_msg)
                if foreground_policy.enabled:
                    getattr(ctx, "route_messages", ctx.messages).insert(0, dict(runtime_msg))

        # Persist session mode after policy/privilege gates so blocked research
        # turns remain ordinary chat/agent streams and saved messages.
        _effective_mode = 'research' if effective_do_research else (chat_mode or 'chat')
        if _effective_mode in ('agent', 'research', 'chat'):
            set_session_mode(session, _effective_mode)

        async def stream_with_save() -> AsyncGenerator[str, None]:
            # _effective_mode is read-only here; closure captures it from
            # the outer scope. (Was `nonlocal` but never reassigned.)
            research_sources = None
            web_sources = ctx.web_sources

            # Register active stream for partial-save safety net
            _active_streams[session] = {"status": "streaming", "partial": "", "query": message, "is_research": effective_do_research, "mode": _effective_mode}
            if not tool_approval_continuation:
                yield f"data: {json.dumps({'type': 'turn_mode', 'mode': _effective_mode, 'auto_escalated': auto_escalated})}\n\n"

            # The client sent a workspace the server refused to bind (deleted
            # folder, file path, sensitive dir, filesystem root). Tell it up
            # front so the UI can clear the pill instead of displaying a
            # confinement that is not actually in effect.
            if workspace_rejected:
                yield f"data: {json.dumps({'type': 'workspace_rejected', 'data': {'path': workspace_rejected}})}\n\n"

            if ctx.preprocessed.attachment_meta:
                yield f"data: {json.dumps({'type': 'attachments', 'data': ctx.preprocessed.attachment_meta})}\n\n"

            # Announce any docs auto-created during preprocess (e.g. fillable
            # PDF → editable markdown) so the editor pane switches to them
            # before the model starts streaming.
            for _opened in ctx.auto_opened_docs:
                yield (
                    f'data: {json.dumps({"type": "doc_update", **_opened})}\n\n'
                )

            if ctx.rag_sources:
                yield f"data: {json.dumps({'type': 'rag_sources', 'data': ctx.rag_sources})}\n\n"

            if web_sources:
                yield f"data: {json.dumps({'type': 'web_sources', 'data': web_sources})}\n\n"

            # Emit which memories were injected into context (captured before stream)
            if ctx.used_memories:
                yield f"data: {json.dumps({'type': 'memories_used', 'data': ctx.used_memories})}\n\n"

            # Run research as a background task (survives page refresh)
            if effective_do_research:
                _r_ep, _r_model, _r_headers = _resolve_research_endpoint(sess)
                _auth_keys = list(_r_headers.keys()) if _r_headers else []
                logger.info(f"Research endpoint resolved: model={_r_model}, endpoint={redact_url(_r_ep)}, auth_keys={_auth_keys}, sess_headers_keys={list(sess.headers.keys()) if isinstance(sess.headers, dict) else type(sess.headers)}")

                # Clarification round: only for very short/vague queries on first research message.
                # Skip in compare mode — each pane is a fresh session, so every one would
                # ask clarifying questions and the user would have to answer each pane
                # separately, breaking the parallel comparison.
                _prior_json = research_handler._get_session_json(session)
                _history_len = len(sess.history) if hasattr(sess, 'history') else 0
                _is_first_research = not _prior_json and _history_len <= 2 and not compare_mode

                if _is_first_research:
                    logger.info(f"First research message — asking clarifying questions for: {message[:60]}")
                    yield f'data: {json.dumps({"type": "model_info", "model": sess.model, "suffix": "Research"})}\n\n'
                    # Set DB mode to research_pending so the NEXT message auto-triggers research
                    set_session_mode(session, "research_pending")
                    ctx.messages.insert(0, {"role": "system", "content":
                        "The user wants to start deep web research. Before searching, ask 2-3 brief "
                        "clarifying questions to understand exactly what they want to know. For example: "
                        "what aspects matter most, are they comparing to something, what's their context "
                        "(moving, traveling, curiosity). Be conversational. Keep it short."
                    })
                    if foreground_policy.enabled:
                        getattr(ctx, "route_messages", ctx.messages).insert(0, dict(ctx.messages[0]))
                    _skip_research = True
                else:
                    _skip_research = False

                if not _skip_research:
                    # Phase 2: Start actual research
                    def _on_research_done(_sid, _result, _sources, _findings):
                        """Persist research to DB when background task finishes."""
                        if incognito:
                            return
                        try:
                            _s = session_manager.get_session(_sid)
                            if not _s:
                                logger.warning(f"Session {_sid} expired before research completed")
                                return
                            _md = {"research": True, "model": _s.model}
                            if _sources:
                                _md["research_sources"] = _sources
                            if _findings:
                                _md["research_findings"] = _findings
                            _clean_res, _md = clean_thinking_for_save(_result, _md)
                            _s.add_message(ChatMessage("assistant", _clean_res, metadata=_md))
                            session_manager.save_sessions()
                            logger.info(f"Research result persisted to DB for session {_sid}")
                        except Exception as _e:
                            logger.error(f"Failed to persist research to DB: {_e}")

                    # Check for prior research to continue from
                    _prior_report = ""
                    _prior_findings = None
                    _prior_urls = None
                    _prior_json = research_handler._get_session_json(session)
                    if _prior_json:
                        _prior_report = _prior_json.get("raw_report", "")
                        _prior_findings = _prior_json.get("raw_findings")
                        _src_urls = {s.get("url", "") for s in (_prior_json.get("sources") or []) if s.get("url")}
                        _prior_urls = _src_urls if _src_urls else None
                        if _prior_report:
                            logger.info(f"Continuing research for session {session} with {len(_src_urls)} prior URLs")

                    # Synthesize conversation into a focused research query
                    _research_query = await research_handler.synthesize_query(
                        sess, message, _r_ep, _r_model, _r_headers,
                    )
                    logger.info(f"Research query: {_research_query[:120]}")

                    research_handler.start_research(
                        session, _research_query, _r_ep, _r_model,
                        llm_headers=_r_headers,
                        prior_report=_prior_report,
                        prior_findings=_prior_findings,
                        prior_urls=_prior_urls,
                        on_complete=_on_research_done,
                        owner=_user,
                    )

                    _heartbeat_counter = 0
                    _last_progress = {}
                    _sent_avg = False
                    while True:
                        status = research_handler.get_status(session)
                        if not status or status["status"] != "running":
                            break
                        progress = status.get("progress", {})
                        if progress and progress != _last_progress:
                            _last_progress = progress
                            if not _sent_avg:
                                _sent_avg = True
                                progress = dict(progress)
                                progress["started_at"] = status.get("started_at")
                                avg = status.get("avg_duration")
                                if avg:
                                    progress["avg_duration"] = avg
                            yield f"data: {json.dumps({'type': 'research_progress', 'data': progress})}\n\n"
                            _heartbeat_counter = 0
                        else:
                            _heartbeat_counter += 1
                            yield f": heartbeat {_heartbeat_counter}\n\n"
                        await asyncio.sleep(1.0)

                    research_sources = research_handler.get_sources(session)
                    if research_sources:
                        yield f"data: {json.dumps({'type': 'research_sources', 'data': research_sources})}\n\n"

                    research_findings = research_handler.get_raw_findings(session)
                    if research_findings:
                        yield f"data: {json.dumps({'type': 'research_findings', 'data': research_findings})}\n\n"

                    # Signal frontend to fetch and render the research result
                    yield f"data: {json.dumps({'type': 'research_done', 'data': {'session_id': session}})}\n\n"
                    yield "data: [DONE]\n\n"
                    research_handler.clear_result(session)
                    _stream_set(session, status="done")
                    _active_streams.pop(session, None)
                    return

            context_source = (
                getattr(ctx, "route_messages", ctx.messages)
                if foreground_policy.enabled
                else ctx.messages
            )
            messages = (
                list(context_source)
                if tool_approval_continuation
                else _ensure_current_request_is_latest_user(context_source, message)
            )

            # Auto-compact notification
            if ctx.was_compacted:
                yield f"data: {json.dumps({'type': 'compacted', 'context_length': ctx.context_length})}\n\n"
            if ctx.context_trimmed and not ctx.was_compacted:
                yield f"data: {json.dumps({'type': 'context_trimmed', 'data': {'context_length': ctx.context_length, 'messages_before': ctx.context_messages_before_trim, 'messages_after': ctx.context_messages_after_trim, 'tokens_before': ctx.context_tokens_before_trim, 'tokens_after': ctx.context_tokens_after_trim}})}\n\n"

            full_response = ""
            _render_state = _AgentRenderState()
            thinking_response = ""
            last_metrics = None

            # Foreground Chat and Agent requests share one explicit owner-aware
            # policy. Strict mode is the default; legacy values are unrelated.
            _foreground_policy = foreground_policy
            _foreground_candidates = build_foreground_model_candidates(
                sess.endpoint_url,
                sess.model,
                sess.headers,
                owner=_user,
                policy=_foreground_policy,
            )
            _foreground_route_descriptors = build_foreground_route_descriptors(
                sess.endpoint_url,
                sess.model,
                sess.headers,
                owner=_user,
                policy=_foreground_policy,
                selected_endpoint_id=selected_endpoint_id or getattr(sess, "endpoint_id", None),
            )
            _chat_request_factory = None
            _selected_context_length = getattr(ctx, "context_length", 0)
            _chat_request_state = {
                "context_lengths": {0: _selected_context_length},
                "requests": {0: messages},
                "trim_stats": {},
            }
            if _foreground_policy.enabled:
                _chat_request_factory, _chat_request_state = _chat_candidate_request_factory(
                    messages,
                    _selected_context_length,
                    session=sess,
                    owner=_user,
                )

            # Send model name early so the frontend can show it during streaming
            _model_suffix = "Research" if effective_do_research else None
            _selected_route = _foreground_route_descriptors[0]
            _model_info = {
                "type": "model_info",
                "model": sess.model,
                "endpoint_id": _selected_route.get("endpoint_id"),
                "endpoint_label": _selected_route.get("endpoint_label"),
            }
            if _model_suffix:
                _model_info["suffix"] = _model_suffix
            if ctx.preset.character_name:
                _model_info["character_name"] = ctx.preset.character_name
            yield f'data: {json.dumps(_model_info)}\n\n'

            _terminal_saved = False
            if image_generation_session:
                from src.settings import get_setting
                _image_upload = _first_image_attachment(chat_handler, att_ids, owner=_user)
                _image_tool_name = "edit_image" if _image_upload else "generate_image"
                _blocked_image_tool = next((
                    name for name in dict.fromkeys(("generate_image", _image_tool_name))
                    if tool_policy.blocks(name)
                ), None)
                if _blocked_image_tool:
                    _blocked_msg = tool_policy.reason_for(_blocked_image_tool)
                    yield f'data: {json.dumps({"delta": _blocked_msg})}\n\n'
                    yield "data: [DONE]\n\n"
                    _active_streams.pop(session, None)
                    return
                if not get_setting("image_gen_enabled", True):
                    yield f'data: {json.dumps({"delta": "Image generation is disabled by the administrator."})}\n\n'
                    yield "data: [DONE]\n\n"
                    _active_streams.pop(session, None)
                    return
                from src.ai_interaction import do_edit_image, do_generate_image
                _user_msg = message or ""
                yield f'data: {json.dumps({"type": "tool_start", "tool": _image_tool_name, "command": _user_msg[:100]})}\n\n'
                yield ": heartbeat\n\n"
                _progress_queue: asyncio.Queue = asyncio.Queue()

                async def _image_progress_callback(progress: Dict[str, Any]):
                    try:
                        _progress_queue.put_nowait(progress)
                    except Exception:
                        pass

                if _image_upload:
                    _img_task = asyncio.create_task(do_edit_image(
                        _user_msg,
                        _image_upload.get("path", ""),
                        model_spec=sess.model,
                        session_id=session,
                        owner=_user,
                        size="auto",
                        progress_callback=_image_progress_callback,
                    ))
                else:
                    _img_task = asyncio.create_task(do_generate_image(f"{_user_msg}\n{sess.model}\n512x512", session, owner=_user))
                _img_started = time.time()
                _img_tick = 0
                while not _img_task.done():
                    try:
                        _progress = await asyncio.wait_for(_progress_queue.get(), timeout=2.0)
                    except asyncio.TimeoutError:
                        _progress = None
                    _img_tick += 1
                    _elapsed = int(time.time() - _img_started)
                    _label = "Editing image" if _image_upload else "Generating image"
                    yield ": image generation still running\n\n"
                    _progress_data = {"type": "tool_progress", "tool": _image_tool_name, "message": f"{_label}… {_elapsed}s", "elapsed": _elapsed, "tick": _img_tick}
                    if isinstance(_progress, dict) and _progress.get("total"):
                        _step = int(_progress.get("step") or 0)
                        _total = int(_progress.get("total") or 0)
                        _percent = _progress.get("percent")
                        _progress_data.update({
                            "step": _step,
                            "total": _total,
                            "percent": _percent,
                            "message": f"{_label}… {_step}/{_total}",
                        })
                    yield f'data: {json.dumps(_progress_data)}\n\n'
                _img_result = await _img_task
                _img_output = _img_result.get("results", _img_result.get("error", ""))
                _img_tool_data = {"type": "tool_output", "tool": _image_tool_name, "command": _user_msg[:100], "output": _img_output, "exit_code": 0 if "error" not in _img_result else 1}
                for _k in ("image_url", "image_id", "image_prompt", "image_model", "image_size", "image_quality"):
                    if _k in _img_result:
                        _img_tool_data[_k] = _img_result[_k]
                if _image_upload:
                    _img_tool_data["source_image"] = {
                        "id": _image_upload.get("id"),
                        "name": _image_upload.get("name") or _image_upload.get("original_name"),
                    }
                yield f'data: {json.dumps(_img_tool_data)}\n\n'
                if _img_result.get("image_url"):
                    _img_event = {"type": "generated_image", "url": _img_result.get("image_url")}
                    for _k in ("image_url", "image_id", "image_prompt", "image_model", "image_size", "image_quality"):
                        if _img_result.get(_k):
                            _img_event[_k] = _img_result[_k]
                    yield f'data: {json.dumps(_img_event)}\n\n'
                _desc = _img_result.get("results", _img_result.get("error", "Image generation complete"))
                full_response = _desc
                yield f'data: {json.dumps({"delta": _desc})}\n\n'
                # Save to session history
                if not incognito:
                    _ev = {"round": 1, "tool": _image_tool_name, "command": _user_msg[:100], "output": _img_output, "exit_code": 0 if "error" not in _img_result else 1}
                    for _ek in ("image_url", "image_id", "image_prompt", "image_model", "image_size", "image_quality"):
                        if _img_result.get(_ek):
                            _ev[_ek] = _img_result[_ek]
                    if _image_upload:
                        _ev["source_image_id"] = _image_upload.get("id")
                        _ev["source_image_name"] = _image_upload.get("name") or _image_upload.get("original_name")
                    sess.add_message(ChatMessage("assistant", full_response, metadata={"tool_events": [_ev], "model": sess.model}))
                    session_manager.save_sessions()
                yield f'data: {json.dumps({"type": "metrics", "data": {"total_time": 0}})}\n\n'
                yield "data: [DONE]\n\n"
                _active_streams.pop(session, None)
                return
            elif chat_mode == "chat":
                _chat_start = time.time()
                _answered_by = None  # set if the selected model failed and a fallback answered
                _requested_model = sess.model
                _actual_model = None
                _requested_route = _foreground_route_descriptors[0]
                _actual_route = _requested_route
                _actual_candidate_index = 0
                _chat_terminal_saved = False
                def _commit_chat_compaction(candidate_index: int) -> bool:
                    return apply_compaction_state(
                        sess,
                        _chat_request_state.get("compactions", {}).get(candidate_index),
                    )

                # ── Chat mode: call stream_llm directly, NO tools, NO document access ──
                try:
                    async for chunk in stream_llm_with_fallback(
                        _foreground_candidates,
                        messages,
                        temperature=(temperature_override if temperature_override is not None else 1.0),
                        # Respect the preset; 0/unset = let the server decide (no
                        # cap), matching agent mode. The old hard 4096 fallback
                        # truncated reasoning models mid-<think> — they'd burn the
                        # whole budget thinking and never emit the answer (seen in
                        # Compare on heavy generation prompts).
                        max_tokens=(sess.max_tokens_override if getattr(sess, "max_tokens_override", None) is not None else 0),
                        prompt_type=preset_id,
                        tools=None,
                        session_id=session,
                        fallback_statuses=_foreground_policy.eligible_statuses,
                        fallback_on_empty=_foreground_policy.fallback_on_empty,
                        candidate_request_factory=_chat_request_factory,
                        candidate_route_descriptors=_foreground_route_descriptors,
                        thinking_mode=thinking_mode,
                        reasoning_effort=reasoning_effort,
                    ):
                        if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
                            try:
                                data = json.loads(chunk[6:])
                                if "delta" in data:
                                    if _commit_chat_compaction(_actual_candidate_index):
                                        _compacted_length = _chat_request_state["context_lengths"].get(
                                            _actual_candidate_index,
                                            _selected_context_length,
                                        )
                                        yield f'data: {json.dumps({"type": "compacted", "context_length": _compacted_length})}\n\n'
                                    # Reasoning tokens arrive flagged thinking:true.
                                    # Forward them so the client can show a thinking
                                    # indicator, but don't fold them into the saved
                                    # reply (mirrors the rewrite path below).
                                    if data.get("thinking"):
                                        if thinking_mode == "off":
                                            continue
                                        thinking_response += data["delta"]
                                    else:
                                        full_response += data["delta"]
                                        _stream_set(session, partial=full_response)
                                    yield chunk
                                elif data.get("type") == "fallback":
                                    # Selected model failed; a fallback answered.
                                    # Forward the notice and remember the real model.
                                    _answered_by = data.get("answered_by") or _answered_by
                                    _actual_model = _actual_model or _answered_by
                                    _actual_candidate_index = data.get("candidate_index", 0)
                                    if not isinstance(_actual_candidate_index, int):
                                        _actual_candidate_index = 0
                                    if 0 <= _actual_candidate_index < len(_foreground_route_descriptors):
                                        _actual_route = _foreground_route_descriptors[_actual_candidate_index]
                                    if _commit_chat_compaction(_actual_candidate_index):
                                        _compacted_length = _chat_request_state["context_lengths"].get(
                                            _actual_candidate_index,
                                            _selected_context_length,
                                        )
                                        yield f'data: {json.dumps({"type": "compacted", "context_length": _compacted_length})}\n\n'
                                    data["selected_model"] = data.get("selected_model") or _requested_model
                                    yield f'data: {json.dumps(data)}\n\n'
                                elif data.get("type") == "model_actual":
                                    if _commit_chat_compaction(_actual_candidate_index):
                                        _compacted_length = _chat_request_state["context_lengths"].get(
                                            _actual_candidate_index,
                                            _selected_context_length,
                                        )
                                        yield f'data: {json.dumps({"type": "compacted", "context_length": _compacted_length})}\n\n'
                                    _actual_model = data.get("model") or _actual_model
                                    data["requested_model"] = _requested_model
                                    data["requested_endpoint_id"] = _requested_route.get("endpoint_id")
                                    data["requested_endpoint_label"] = _requested_route.get("endpoint_label")
                                    data["endpoint_id"] = _actual_route.get("endpoint_id")
                                    data["endpoint_label"] = _actual_route.get("endpoint_label")
                                    yield f'data: {json.dumps(data)}\n\n'
                                elif data.get("type") == "usage":
                                    if _commit_chat_compaction(_actual_candidate_index):
                                        _compacted_length = _chat_request_state["context_lengths"].get(
                                            _actual_candidate_index,
                                            _selected_context_length,
                                        )
                                        yield f'data: {json.dumps({"type": "compacted", "context_length": _compacted_length})}\n\n'
                                    last_metrics = data.get("data", {})
                                    _reported_model = last_metrics.get("model")
                                    last_metrics["requested_model"] = _requested_model
                                    last_metrics["model"] = _reported_model or _actual_model or _answered_by or _requested_model
                                    last_metrics["requested_endpoint_id"] = _requested_route.get("endpoint_id")
                                    last_metrics["requested_endpoint_label"] = _requested_route.get("endpoint_label")
                                    last_metrics["endpoint_id"] = _actual_route.get("endpoint_id")
                                    last_metrics["endpoint_label"] = _actual_route.get("endpoint_label")
                                    if isinstance(
                                        _actual_route.get("endpoint_cost_tracked"),
                                        bool,
                                    ):
                                        last_metrics["endpoint_cost_tracked"] = _actual_route.get(
                                            "endpoint_cost_tracked"
                                        )
                                    _actual_context_length = _chat_request_state["context_lengths"].get(
                                    _actual_candidate_index,
                                        _selected_context_length,
                                    )
                                    _route_trim = _chat_request_state.get("trim_stats", {}).get(
                                        _actual_candidate_index,
                                        {},
                                    )
                                    if _route_trim and (
                                        _route_trim.get("messages_after") < _route_trim.get("messages_before")
                                        or _route_trim.get("tokens_after") < _route_trim.get("tokens_before")
                                    ):
                                        last_metrics["context_trimmed"] = True
                                        last_metrics["context_messages_before_trim"] = _route_trim.get("messages_before")
                                        last_metrics["context_messages_after_trim"] = _route_trim.get("messages_after")
                                        last_metrics["context_tokens_before_trim"] = _route_trim.get("tokens_before")
                                        last_metrics["context_tokens_after_trim"] = _route_trim.get("tokens_after")
                                    elif ctx.context_trimmed:
                                        last_metrics["context_trimmed"] = True
                                        last_metrics["context_messages_before_trim"] = ctx.context_messages_before_trim
                                        last_metrics["context_messages_after_trim"] = ctx.context_messages_after_trim
                                        last_metrics["context_tokens_before_trim"] = ctx.context_tokens_before_trim
                                        last_metrics["context_tokens_after_trim"] = ctx.context_tokens_after_trim
                                    if _actual_context_length and last_metrics.get("input_tokens"):
                                        pct = min(round((last_metrics["input_tokens"] / _actual_context_length) * 100, 1), 100.0)
                                        last_metrics["context_percent"] = pct
                                        last_metrics["context_length"] = _actual_context_length
                                    # The frontend reads `tokens_per_second`; the raw usage event
                                    # carries the backend's true gen speed as `gen_tps` (llama.cpp
                                    # timings). Map it through so this direct-chat path shows real
                                    # t/s instead of "n/a" → falling back to a bare token count.
                                    if last_metrics.get("gen_tps") and not last_metrics.get("tokens_per_second"):
                                        last_metrics["tokens_per_second"] = last_metrics["gen_tps"]
                                        last_metrics["tps_source"] = "backend"
                                    # Wall-clock response time for the stats popup ("Time").
                                    last_metrics.setdefault("response_time", round(time.time() - _chat_start, 2))
                                    _annotate_chat_cost(last_metrics, sess)
                                    yield f'data: {json.dumps({"type": "metrics", "data": last_metrics})}\n\n'
                            except json.JSONDecodeError:
                                yield chunk
                        elif chunk.startswith("event: error"):
                            logger.warning(f"Stream error for {sess.model} on {sess.endpoint_url}: {chunk!r}")
                            if (
                                not _chat_terminal_saved
                                and (full_response.strip() or thinking_response.strip())
                            ):
                                _failure_status = _stream_failure_status(chunk)
                                _failure_message = (
                                    f"Model request failed (HTTP {_failure_status})"
                                    if _failure_status is not None
                                    else "Model request failed"
                                )
                                _terminal_content = full_response.strip()
                                _failure_note = f"[Response stopped: {_failure_message}]"
                                _terminal_content = (
                                    f"{_terminal_content}\n\n{_failure_note}"
                                    if _terminal_content
                                    else _failure_note
                                )
                                _had_terminal_usage = bool(last_metrics)
                                _terminal_metrics = dict(last_metrics or {})
                                if not _had_terminal_usage:
                                    _actual_request_messages = _chat_request_state["requests"].get(
                                        _actual_candidate_index,
                                        messages,
                                    )
                                    _actual_context_length = _chat_request_state["context_lengths"].get(
                                        _actual_candidate_index,
                                        _selected_context_length,
                                    )
                                    _estimated_input = estimate_tokens(_actual_request_messages)
                                    _estimated_output = max(
                                        len(full_response + thinking_response) // 4,
                                        0,
                                    )
                                    _terminal_metrics.update({
                                        "input_tokens": _estimated_input,
                                        "output_tokens": _estimated_output,
                                        "total_tokens": _estimated_input + _estimated_output,
                                        "usage_source": "estimated",
                                        "response_time": round(time.time() - _chat_start, 2),
                                        "context_length": _actual_context_length,
                                        "context_percent": (
                                            min(
                                                round(
                                                    (_estimated_input / _actual_context_length) * 100,
                                                    1,
                                                ),
                                                100.0,
                                            )
                                            if _actual_context_length
                                            else 0
                                        ),
                                    })
                                _terminal_metrics.update({
                                    "failed": True,
                                    "failure": {
                                        "status": _failure_status,
                                        "message": _failure_message,
                                    },
                                    "model": _actual_model or _answered_by or _requested_model,
                                    "requested_model": _requested_model,
                                    "endpoint_id": _actual_route.get("endpoint_id"),
                                    "endpoint_label": _actual_route.get("endpoint_label"),
                                    "requested_endpoint_id": _requested_route.get("endpoint_id"),
                                    "requested_endpoint_label": _requested_route.get("endpoint_label"),
                                })
                                if isinstance(
                                    _actual_route.get("endpoint_cost_tracked"),
                                    bool,
                                ):
                                    _terminal_metrics["endpoint_cost_tracked"] = _actual_route.get(
                                        "endpoint_cost_tracked"
                                    )
                                if thinking_response.strip():
                                    _terminal_metrics["thinking"] = thinking_response.strip()
                                _commit_chat_compaction(_actual_candidate_index)
                                _saved_id = save_assistant_response(
                                    sess,
                                    session_manager,
                                    session,
                                    _terminal_content,
                                    _terminal_metrics,
                                    character_name=ctx.preset.character_name,
                                    incognito=incognito,
                                )
                                accumulate_token_usage(session, _terminal_metrics)
                                _chat_terminal_saved = True
                                _stream_set(session, status="error")
                                if _saved_id:
                                    yield f'data: {json.dumps({"type": "message_saved", "id": _saved_id})}\n\n'
                                yield f'data: {json.dumps({"type": "chat_terminal", "data": _terminal_metrics})}\n\n'
                            yield chunk
                        elif chunk.startswith("event: "):
                            if chunk.startswith("event: error"):
                                _stream_set(session, status="error")
                            yield chunk
                        elif chunk == "data: [DONE]\n\n":
                            if _chat_terminal_saved:
                                # Some providers append DONE after a terminal
                                # error.  The failed partial is already saved;
                                # never re-save/post-process it as a success or
                                # advertise successful completion to the client.
                                continue
                            # Generate fallback metrics if LLM didn't send usage
                            if not last_metrics and full_response:
                                _elapsed = time.time() - _chat_start
                                _est_out = len(full_response) // 4
                                _tps = round(_est_out / _elapsed, 2) if _elapsed > 0 else 0
                                _actual_context_length = _chat_request_state["context_lengths"].get(
                                    _actual_candidate_index,
                                    _selected_context_length,
                                )
                                _actual_request_messages = _chat_request_state["requests"].get(
                                    _actual_candidate_index,
                                    messages,
                                )
                                _est_in = estimate_tokens(_actual_request_messages)
                                _ctx_pct = min(round((_est_in / _actual_context_length) * 100, 1), 100.0) if _actual_context_length else 0
                                last_metrics = {
                                    "response_time": round(_elapsed, 2),
                                    "input_tokens": _est_in,
                                    "output_tokens": _est_out,
                                    "tokens_per_second": _tps,
                                    "request_context_tokens": _est_in,
                                    "context_percent": _ctx_pct,
                                    "context_length": _actual_context_length,
                                    "model": _actual_model or _answered_by or _requested_model,
                                    "requested_model": _requested_model,
                                    "requested_endpoint_id": _requested_route.get("endpoint_id"),
                                    "requested_endpoint_label": _requested_route.get("endpoint_label"),
                                    "endpoint_id": _actual_route.get("endpoint_id"),
                                    "endpoint_label": _actual_route.get("endpoint_label"),
                                    "usage_source": "estimated",
                                }
                                if isinstance(
                                    _actual_route.get("endpoint_cost_tracked"),
                                    bool,
                                ):
                                    last_metrics["endpoint_cost_tracked"] = _actual_route.get(
                                        "endpoint_cost_tracked"
                                    )
                                _annotate_chat_cost(last_metrics, sess)
                                yield f'data: {json.dumps({"type": "metrics", "data": last_metrics})}\n\n'
                            if full_response:
                                _commit_chat_compaction(_actual_candidate_index)
                                _metrics_to_save = dict(last_metrics or {})
                                _round_texts = _metrics_to_save.get("round_texts") or []
                                _final_round_text = next(
                                    (
                                        _visible_response_text_for_save(_item)
                                        for _item in reversed(_round_texts)
                                        if _visible_response_text_for_save(_item)
                                    ),
                                    "",
                                )
                                _response_to_save = (
                                    _final_round_text
                                    if _metrics_to_save.get("tool_events") and _final_round_text
                                    else _visible_response_text_for_save(full_response)
                                )
                                if thinking_response.strip() and not _metrics_to_save.get("thinking"):
                                    _metrics_to_save["thinking"] = thinking_response.strip()
                                _saved_id = save_assistant_response(
                                    sess, session_manager, session, _response_to_save, _metrics_to_save,
                                    character_name=ctx.preset.character_name,
                                    web_sources=web_sources,
                                    rag_sources=ctx.rag_sources,
                                    research_sources=research_sources,
                                    used_memories=ctx.used_memories,
                                    do_research=effective_do_research,
                                    incognito=incognito,
                                )
                                if _saved_id:
                                    yield f'data: {json.dumps({"type": "message_saved", "id": _saved_id})}\n\n'
                                run_post_response_tasks(
                                    sess, session_manager, session, message, _response_to_save,
                                    _metrics_to_save, ctx.uprefs, memory_manager, memory_vector, webhook_manager,
                                    incognito=incognito, compare_mode=compare_mode,
                                    character_name=ctx.preset.character_name,
                                    owner=_user,
                                    allow_background_extraction=_post_response_extraction_allowed(
                                        tools_blocked=tool_policy.block_all_tool_calls,
                                        tool_approval_continuation=tool_approval_continuation,
                                        client_runtime_context=client_runtime_context,
                                    ),
                                )
                            _stream_set(session, status="done")
                            yield chunk
                except (asyncio.CancelledError, GeneratorExit):
                    if full_response and not incognito:
                        logger.info("Client disconnected mid-stream (chat mode) for session %s, saving partial (%d chars)", session, len(full_response))
                        _stopped_content, _stopped_md = clean_thinking_for_save(
                            full_response,
                            {
                                "stopped": True,
                                "model": _actual_model or _answered_by or _requested_model,
                                "requested_model": _requested_model,
                                "endpoint_id": _actual_route.get("endpoint_id"),
                                "endpoint_label": _actual_route.get("endpoint_label"),
                                "requested_endpoint_id": _requested_route.get("endpoint_id"),
                                "requested_endpoint_label": _requested_route.get("endpoint_label"),
                            },
                        )
                        sess.add_message(ChatMessage("assistant", _stopped_content, metadata=_stopped_md))
                        session_manager.save_sessions()
                    raise
                finally:
                    _active_streams.pop(session, None)
            else:
                # ── Agent mode: full agent loop with tools ──
                _agent_rounds = 0
                _agent_tool_calls = 0
                _answered_by = None  # set if the selected model failed and a fallback answered
                _requested_model = sess.model
                _actual_model = None
                _agent_requested_route = _foreground_route_descriptors[0]
                _agent_actual_endpoint_id = _agent_requested_route.get("endpoint_id")
                _agent_actual_endpoint_label = _agent_requested_route.get("endpoint_label")
                _agent_round_models = {1: _requested_model}
                _agent_round_endpoint_ids = {1: _agent_actual_endpoint_id}
                _agent_round_endpoint_labels = {1: _agent_actual_endpoint_label}
                _terminal_saved = False
                try:
                    from src.settings import get_setting
                    from src.agent_tools import MAX_AGENT_ROUNDS as _DEFAULT_ROUNDS
                    # Per-message tool budget from settings; guard defensively in
                    # case settings.json was hand-edited to a non-numeric value
                    # (the HTTP admin endpoint validates, but direct edits bypass
                    # it). 0 = unlimited, matching auth_routes set_settings().
                    try:
                        _tool_budget = int(get_setting("agent_max_tool_calls", 0))
                    except (TypeError, ValueError):
                        _tool_budget = 0
                    # Per-message round cap from settings; clamp defensively in
                    # case settings.json was hand-edited to a bad value.
                    _max_rounds = _effective_agent_rounds(
                        get_setting("agent_max_rounds", _DEFAULT_ROUNDS),
                        client_runtime_context,
                        _DEFAULT_ROUNDS,
                        message=message,
                        workspace_agent_intent=_workspace_agent_intent,
                    )
                    _max_tokens = _effective_native_output_tokens(
                        (sess.max_tokens_override if getattr(sess, "max_tokens_override", None) is not None else 0),
                        client_runtime_context,
                    )

                    _forced_tools = None
                    if _search_enabled:
                        _forced_tools = set(WEB_TOOL_NAMES)
                        if _explicit_browser_intent:
                            _forced_tools |= set(_BROWSER_MCP_TOOLS) | {"private_browser"}
                    elif _explicit_browser_intent:
                        _forced_tools = set(_BROWSER_MCP_TOOLS) | {"private_browser"}
                    # A globally enabled web toggle must not erase the typed
                    # state tool selected for an unrelated personal action.
                    # Otherwise words such as "today" make a calendar create
                    # look web-adjacent, the correct model call is dropped as
                    # unoffered, and provider fallback searches the internet.
                    if _tool_intent and _tool_intent.needs_tools:
                        _typed_forced_tools = {
                            "calendar": {"manage_calendar"},
                            "notes": {"manage_notes", "manage_tasks"},
                        }.get(_tool_intent.category, set())
                        if _typed_forced_tools:
                            if _forced_tools is None:
                                _forced_tools = set()
                            _forced_tools.update(_typed_forced_tools)
                    if _workspace_agent_intent:
                        if _forced_tools is None:
                            _forced_tools = set()
                        _forced_tools.update({"bash", "ls", "manage_bg_jobs"})
                    if _turn_contract is None:
                        _explicit_selected_tools = selected_tools_for_request(message)
                        if _explicit_selected_tools:
                            # Full-schema/API models normally retain broad
                            # freedom, but a complete request that explicitly
                            # names a bounded native tool chain should not be
                            # drowned out by lexical RAG (for example, the word
                            # "report" selecting research instead of the named
                            # OCR/write/read workflow).
                            _forced_tools = set(_explicit_selected_tools)
                    if _turn_contract is not None:
                        _forced_tools = set(_turn_contract.offered)

                    async for chunk in _stream_agent_with_execution_bridge(
                        _external_execution_bridge(client_runtime_context),
                        sess.endpoint_url,
                        sess.model,
                        messages,
                        headers=sess.headers,
                        temperature=(temperature_override if temperature_override is not None else 1.0),
                        max_tokens=_max_tokens,
                        prompt_type=preset_id,
                        max_tool_calls=_tool_budget,
                        max_rounds=_max_rounds,
                        context_length=_selected_context_length,
                        active_document=active_doc,
                        active_email=active_email_ctx,
                        session_id=session,
                        history_session=sess,
                        disabled_tools=disabled_tools if disabled_tools else None,
                        tool_policy=tool_policy,
                        owner=_user,
                        fallbacks=_foreground_candidates[1:],
                        route_descriptors=_foreground_route_descriptors,
                        fallback_statuses=_foreground_policy.eligible_statuses,
                        fallback_on_empty=_foreground_policy.fallback_on_empty,
                        plan_mode=plan_mode,
                        approved_plan=approved_plan or None,
                        workspace=workspace or None,
                        relevant_tools=(
                            set(pending_tool_approval.selected_tools)
                            if exact_tool_approval
                            and pending_tool_approval
                            and pending_tool_approval.selected_tools
                            else None
                        ),
                        cwd=_agent_turn_cwd(sess, client_runtime_context),
                        forced_tools=_forced_tools,
                        turn_contract=_turn_contract,
                        request_authority=_request_authority,
                        uploaded_files=ctx.uploaded_files,
                        defer_context_shaping=_foreground_policy.enabled,
                        external_untrusted_context_seen=external_untrusted_context_seen,
                        delegated_credential=_delegated_credential,
                        exact_approval=exact_tool_approval,
                        client_runtime_context=client_runtime_context,
                        thinking_mode=thinking_mode,
                        reasoning_effort=reasoning_effort,
                        context_resolution=(
                            _compact_context_resolution if _clean_v3_preview else None
                        ),
                    ):
                        if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
                            try:
                                data = _render_state.consume(json.loads(chunk[6:]))
                                chunk = "data: " + json.dumps(data) + "\n\n"
                                if "delta" in data and data.get("type") != "final_response":
                                    # Reasoning tokens arrive flagged thinking:true.
                                    # Forward them for the live indicator, but keep
                                    # them out of the saved reply (same as chat mode).
                                    if data.get("thinking"):
                                        if thinking_mode == "off":
                                            continue
                                        thinking_response += data["delta"]
                                    else:
                                        full_response = _render_state.content
                                        _stream_set(session, partial=full_response)
                                    yield chunk
                                elif data.get("type") == "final_response":
                                    # Some deterministic post-processing
                                    # replaces a streamed model draft (for
                                    # example, compacting a broad memory list).
                                    # Replace the accumulator instead of
                                    # concatenating the replacement to the
                                    # draft that clients already received.
                                    full_response = _render_state.content
                                    _stream_set(session, partial=full_response)
                                    yield chunk
                                elif data.get("type") == "web_sources":
                                    web_sources = data.get("data", [])
                                    yield chunk
                                elif data.get("type") in (
                                    "tool_start", "tool_output", "agent_step",
                                    "doc_stream_open", "doc_stream_delta",
                                    "doc_update", "doc_suggestions", "editor_progress", "ui_control", "email_open",
                                    "rounds_exhausted", "budget_exceeded",
                                    "loop_breaker_triggered",
                                    "intent_nudge_exhausted",
                                    "ask_user",
                                    "plan_update",
                                    "model_request_snapshot",
                                    "model_tool_proposal",
                                    "tool_routing_audit",
                                    "tool_resolution_audit",
                                    "turn_contract",
                                ):
                                    if data.get("type") == "agent_step":
                                        _event_round = data.get("round", 1)
                                        _agent_rounds = max(_agent_rounds, _event_round)
                                        _agent_round_models.setdefault(
                                            _event_round,
                                            _actual_model or _answered_by or _requested_model,
                                        )
                                        _agent_round_endpoint_ids.setdefault(
                                            _event_round,
                                            _agent_actual_endpoint_id,
                                        )
                                        _agent_round_endpoint_labels.setdefault(
                                            _event_round,
                                            _agent_actual_endpoint_label,
                                        )
                                    elif data.get("type") == "tool_start":
                                        _agent_tool_calls += 1
                                    yield chunk
                                elif data.get("type") == "fallback":
                                    # Selected model failed; a fallback answered.
                                    # Forward the notice and remember the real
                                    # model so metrics reflect it, not the masked
                                    # selected model.
                                    _answered_by = data.get("answered_by") or _answered_by
                                    _actual_model = _answered_by or _actual_model
                                    if "answered_by_endpoint_id" in data:
                                        _agent_actual_endpoint_id = data.get("answered_by_endpoint_id")
                                    if data.get("answered_by_endpoint_label"):
                                        _agent_actual_endpoint_label = data.get("answered_by_endpoint_label")
                                    _event_round = data.get("round") or max(_agent_rounds, 1)
                                    _agent_round_models[_event_round] = _answered_by or _requested_model
                                    _agent_round_endpoint_ids[_event_round] = _agent_actual_endpoint_id
                                    _agent_round_endpoint_labels[_event_round] = _agent_actual_endpoint_label
                                    data["selected_model"] = data.get("selected_model") or _requested_model
                                    yield chunk
                                elif data.get("type") == "model_actual":
                                    _actual_model = data.get("model") or _actual_model
                                    if "endpoint_id" in data:
                                        _agent_actual_endpoint_id = data.get("endpoint_id")
                                    if data.get("endpoint_label"):
                                        _agent_actual_endpoint_label = data.get("endpoint_label")
                                    _event_round = data.get("round") or max(_agent_rounds, 1)
                                    _agent_round_models[_event_round] = _actual_model or _requested_model
                                    _agent_round_endpoint_ids[_event_round] = _agent_actual_endpoint_id
                                    _agent_round_endpoint_labels[_event_round] = _agent_actual_endpoint_label
                                    data["requested_model"] = _requested_model
                                    yield f'data: {json.dumps(data)}\n\n'
                                elif data.get("type") == "agent_terminal":
                                    terminal_metadata = _render_state.metadata(data.get("data"))
                                    if thinking_mode == "off":
                                        terminal_metadata.pop("thinking", None)
                                    last_metrics = terminal_metadata
                                    failure = terminal_metadata.get("failure") or {}
                                    failure_status = _normalize_http_status(
                                        failure.get("status")
                                    )
                                    failure_message = (
                                        f"Model request failed (HTTP {failure_status})"
                                        if failure_status is not None
                                        else "Model request failed"
                                    )
                                    terminal_metadata["failure"] = {
                                        "status": failure_status,
                                        "message": failure_message,
                                    }
                                    terminal_content = full_response.strip()
                                    failure_note = f"[Agent stopped: {failure_message}]"
                                    if terminal_content:
                                        terminal_content = f"{terminal_content}\n\n{failure_note}"
                                    else:
                                        terminal_content = failure_note
                                    if not _terminal_saved:
                                        _saved_id = save_assistant_response(
                                            sess,
                                            session_manager,
                                            session,
                                            terminal_content,
                                            terminal_metadata,
                                            character_name=ctx.preset.character_name,
                                            web_sources=web_sources,
                                            rag_sources=ctx.rag_sources,
                                            used_memories=ctx.used_memories,
                                            incognito=incognito,
                                        )
                                        _terminal_saved = True
                                        accumulate_token_usage(session, terminal_metadata)
                                        _stream_set(session, status="error")
                                        if _saved_id:
                                            yield f'data: {json.dumps(_render_state.message_saved(_saved_id))}\n\n'
                                    yield chunk
                                elif data.get("type") == "metrics":
                                    last_metrics = _render_state.metadata(data.get("data"))
                                    if thinking_mode == "off":
                                        last_metrics.pop("thinking", None)
                                    _reported_model = last_metrics.get("model")
                                    last_metrics["requested_model"] = last_metrics.get("requested_model") or _requested_model
                                    last_metrics["model"] = _reported_model or _actual_model or _answered_by or _requested_model
                                    if ctx.context_trimmed:
                                        last_metrics["context_trimmed"] = True
                                        last_metrics["context_messages_before_trim"] = ctx.context_messages_before_trim
                                        last_metrics["context_messages_after_trim"] = ctx.context_messages_after_trim
                                        last_metrics["context_tokens_before_trim"] = ctx.context_tokens_before_trim
                                        last_metrics["context_tokens_after_trim"] = ctx.context_tokens_after_trim
                                    _metrics_event = {"type": "metrics", "data": last_metrics}
                                    # Inline teacher escalation marks its
                                    # recursively emitted events at the SSE
                                    # envelope. Preserve that non-secret marker
                                    # when normalizing metrics so the browser's
                                    # replay-stable ledger keeps primary and
                                    # teacher segments distinct.
                                    if data.get("teacher") is True:
                                        _metrics_event["teacher"] = True
                                    _metrics_round_texts = last_metrics.get("round_texts") or []
                                    _metrics_fallback_response = next(
                                        (
                                            _visible_response_text_for_save(_item)
                                            for _item in reversed(_metrics_round_texts)
                                            if _visible_response_text_for_save(_item)
                                        ),
                                        "",
                                    )
                                    _saveable_no_tool_response = (
                                        _visible_response_text_for_save(full_response) or _metrics_fallback_response
                                    )
                                    if (
                                        (
                                            last_metrics.get("direct_low_signal")
                                            or not last_metrics.get("tool_events")
                                        )
                                        and _saveable_no_tool_response
                                        and not _terminal_saved
                                    ):
                                        _metrics_to_save = dict(last_metrics)
                                        if thinking_response.strip() and not _metrics_to_save.get("thinking"):
                                            _metrics_to_save["thinking"] = thinking_response.strip()
                                        _saved_id = save_assistant_response(
                                            sess,
                                            session_manager,
                                            session,
                                            _saveable_no_tool_response,
                                            _metrics_to_save,
                                            character_name=ctx.preset.character_name,
                                            web_sources=web_sources,
                                            rag_sources=ctx.rag_sources,
                                            used_memories=ctx.used_memories,
                                            incognito=incognito,
                                        )
                                        _terminal_saved = True
                                        if _saved_id:
                                            yield f'data: {json.dumps(_render_state.message_saved(_saved_id))}\n\n'
                                    yield f'data: {json.dumps(_metrics_event)}\n\n'
                            except json.JSONDecodeError:
                                yield chunk
                        elif chunk.startswith("event: "):
                            yield chunk
                        elif chunk == "data: [DONE]\n\n":
                            _has_tool_events = bool((last_metrics or {}).get("tool_events"))
                            if not _terminal_saved and (full_response or _has_tool_events):
                                _metrics_to_save = _render_state.metadata(last_metrics)
                                _round_texts = _metrics_to_save.get("round_texts") or []
                                _final_round_text = next(
                                    (
                                        _visible_response_text_for_save(_item)
                                        for _item in reversed(_round_texts)
                                        if _visible_response_text_for_save(_item)
                                    ),
                                    "",
                                )
                                _visible_full_response = _visible_response_text_for_save(full_response)
                                _response_to_save = (
                                    _visible_full_response
                                    or _final_round_text
                                    or "Done."
                                )
                                if _response_to_save and _round_texts:
                                    for _idx in range(len(_round_texts) - 1, -1, -1):
                                        if _visible_response_text_for_save(_round_texts[_idx]):
                                            _round_texts[_idx] = _response_to_save
                                            _metrics_to_save["round_texts"] = _round_texts
                                            break
                                if thinking_response.strip() and not _metrics_to_save.get("thinking"):
                                    _metrics_to_save["thinking"] = thinking_response.strip()
                                _saved_id = save_assistant_response(
                                    sess, session_manager, session, _response_to_save, _metrics_to_save,
                                    character_name=ctx.preset.character_name,
                                    web_sources=web_sources,
                                    rag_sources=ctx.rag_sources,
                                    used_memories=ctx.used_memories,
                                    incognito=incognito,
                                )
                                if _saved_id:
                                    yield f'data: {json.dumps(_render_state.message_saved(_saved_id))}\n\n'
                                run_post_response_tasks(
                                    sess, session_manager, session, message, _response_to_save,
                                    _metrics_to_save, ctx.uprefs, memory_manager, memory_vector, webhook_manager,
                                    incognito=incognito, compare_mode=compare_mode,
                                    character_name=ctx.preset.character_name,
                                                            agent_rounds=_agent_rounds,
                                    agent_tool_calls=_agent_tool_calls,
                                    skills_manager=skills_manager,
                                    owner=_user,
                                    extract_skills=(
                                        user_requested_agent
                                        and not tool_approval_continuation
                                    ),
                                    allow_background_extraction=_post_response_extraction_allowed(
                                        tools_blocked=tool_policy.block_all_tool_calls,
                                        tool_approval_continuation=tool_approval_continuation,
                                        client_runtime_context=client_runtime_context,
                                    ),
                                )
                            _stream_set(session, status="done")
                            yield chunk
                except (asyncio.CancelledError, GeneratorExit):
                    # Client disconnected — save partial response. Wrap
                    # the save in its own try so an exception inside
                    # add_message / save_sessions doesn't mask the
                    # original CancelledError (which prevented the
                    # outer finally from running and left _active_streams
                    # with a stale entry).
                    try:
                        if full_response and not incognito:
                            logger.info("Client disconnected mid-stream for session %s, saving partial response (%d chars)", session, len(full_response))
                            _stopped_content2, _stopped_md2 = clean_thinking_for_save(
                                full_response,
                                {
                                    "stopped": True,
                                    "model": _actual_model or _answered_by or _requested_model,
                                    "requested_model": _requested_model,
                                    "endpoint_id": _agent_actual_endpoint_id,
                                    "endpoint_label": _agent_actual_endpoint_label,
                                    "requested_endpoint_id": _agent_requested_route.get("endpoint_id"),
                                    "requested_endpoint_label": _agent_requested_route.get("endpoint_label"),
                                    "round_models": [
                                        _agent_round_models.get(i, _actual_model or _requested_model)
                                        for i in range(1, max(_agent_round_models, default=1) + 1)
                                    ],
                                    "round_endpoint_ids": [
                                        _agent_round_endpoint_ids.get(i)
                                        for i in range(1, max(_agent_round_models, default=1) + 1)
                                    ],
                                    "round_endpoint_labels": [
                                        _agent_round_endpoint_labels.get(i)
                                        for i in range(1, max(_agent_round_models, default=1) + 1)
                                    ],
                                },
                            )
                            sess.add_message(ChatMessage("assistant", _stopped_content2, metadata=_stopped_md2))
                            session_manager.save_sessions()
                    except Exception:
                        logger.exception("Failed to save partial response on disconnect (session %s)", session)
                    raise
                finally:
                    _active_streams.pop(session, None)

        async def _safe_stream() -> AsyncGenerator[str, None]:
            """Wrapper that guarantees _active_streams cleanup even if stream_with_save
            raises before reaching a mode-specific finally block."""
            try:
                async for chunk in stream_with_save():
                    yield chunk
            finally:
                _active_streams.pop(session, None)

        # Compare panes and explicitly unattended native clients are
        # short-lived, single-shot generations with nobody to resume them.
        # Closing their SSE must promptly cancel the upstream LLM call.
        # Detaching would keep burning upstream tokens/compute after the caller
        # exits and would surface a stale /resume target nobody will revisit.
        #
        # So: stream them directly (no agent_runs wrapping). Starlette cancels
        # the underlying async generator (raising CancelledError/GeneratorExit
        # inside it) as soon as it notices the client disconnected — which the
        # mode-specific except blocks above already handle by saving the
        # partial response exactly once. This stops the upstream call promptly
        # without waiting on the next streamed chunk.
        #
        # Resumable interactive chat/agent streams keep the DETACHED behavior
        # below: they survive the client closing the tab or navigating away.
        # The SSE response only subscribes; reconnect via /api/chat/resume.
        if not _should_detach_chat_stream(
            compare_mode=compare_mode,
            client_runtime_context=client_runtime_context,
        ):
            return StreamingResponse(_safe_stream(), media_type="text/event-stream", headers={
                    "Cache-Control": "no-cache, no-transform",
                    "X-Accel-Buffering": "no",
                })

        _detached_run = agent_runs.start(session, _safe_stream())
        return StreamingResponse(
            agent_runs.subscribe(session, _detached_run),
            media_type="text/event-stream",
            headers={
                "X-Odysseus-Run-Id": _detached_run.run_id,
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    # ------------------------------------------------------------------ #
    # GET /api/chat/resume — reconnect to a detached run that's still going
    # (e.g. after reopening a session whose agent kept running in the background)
    # ------------------------------------------------------------------ #
    @router.get("/api/chat/resume/{session_id}")
    async def chat_resume(request: Request, session_id: str) -> StreamingResponse:
        _verify_session_owner(request, session_id)
        _active_run = agent_runs.get_active_run(session_id)
        if _active_run is None:
            raise HTTPException(404, "No active run for this session")
        return StreamingResponse(
            agent_runs.subscribe(session_id, _active_run),
            media_type="text/event-stream",
            headers={
                "X-Odysseus-Run-Id": _active_run.run_id,
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    # ------------------------------------------------------------------ #
    # POST /api/chat/stop — cancel a detached run (Stop button). Closing the SSE
    # no longer stops it (it's detached), so the Stop button must call this.
    # ------------------------------------------------------------------ #
    @router.post("/api/chat/stop/{session_id}")
    async def chat_stop(request: Request, session_id: str) -> Dict[str, Any]:
        _verify_session_owner(request, session_id)
        _expected_run_id = request.headers.get("X-Odysseus-Run-Id")
        stopped = agent_runs.stop(session_id, _expected_run_id)
        return {"stopped": stopped}

    @router.post("/api/chat/finish/{session_id}")
    async def chat_finish(request: Request, session_id: str) -> Dict[str, Any]:
        """Finish an editor run without discarding completed tools or review cards."""
        _verify_session_owner(request, session_id)
        expected_run_id = request.headers.get("X-Odysseus-Run-Id")
        accepted = agent_runs.request_finish(session_id, expected_run_id)
        return {"accepted": accepted}

    # ------------------------------------------------------------------ #
    # GET /api/chat/stream_status — check if a stream is active for a session
    # ------------------------------------------------------------------ #
    @router.get("/api/chat/stream_status/{session_id}")
    async def chat_stream_status(request: Request, session_id: str) -> Dict[str, Any]:
        _verify_session_owner(request, session_id)
        # A detached run can still be going even if _active_streams was popped;
        # report it as active so the client knows to reconnect via /resume.
        # Read once via .get() to avoid a KeyError race between the membership
        # check and the indexed read if a sibling stream's finally pops the
        # entry in between (same pattern _stream_set already uses).
        rec = _active_streams.get(session_id)
        if rec is None:
            if agent_runs.is_active(session_id):
                return {"status": "streaming", "detached": True}
            raise HTTPException(404, "No active stream for this session")
        return rec

    # ------------------------------------------------------------------ #
    # POST /api/inject_context
    # ------------------------------------------------------------------ #
    @router.post("/api/inject_context/{session_id}")
    async def inject_context(request: Request, session_id: str, context: str = Form(...)) -> Dict[str, str]:
        _verify_session_owner(request, session_id)
        try:
            sess = session_manager.get_session(session_id)
            msg = untrusted_context_message("injected research context", f"Research Context: {context}")
            sess.add_message(ChatMessage(msg["role"], msg["content"], metadata=msg.get("metadata")))
            session_manager.save_sessions()
            return {"status": "context_injected"}
        except KeyError:
            raise HTTPException(404, "Session not found")

    # ------------------------------------------------------------------ #
    # GET /api/search — search across chat messages
    # ------------------------------------------------------------------ #
    @router.get("/api/search")
    async def search_messages(
        request: Request,
        q: str = Query("", min_length=0),
        limit: int = Query(20, ge=1, le=100),
    ) -> List[Dict[str, Any]]:
        if not q or not q.strip():
            return []

        _user = effective_user(request)
        return [
            result.to_dict()
            for result in search_session_messages(
                q,
                limit=limit,
                owner=_user,
                restrict_owner=_user is not None,
                include_legacy_owner=False,
            )
        ]

    # ------------------------------------------------------------------ #
    # POST /api/rewrite — lightweight rewrite of last AI message (no tools)
    # ------------------------------------------------------------------ #
    @router.post("/api/rewrite")
    @suspicious_frequency(0.5, 60, "log")
    @content_type(["application/json"])
    async def rewrite_message(request: Request) -> StreamingResponse:
        """Rewrite the last AI message with an instruction (shorter/simpler/etc).

        Unlike the full chat pipeline, this does NOT run the agent loop or tools.
        It just asks the LLM to rewrite the given text.
        """
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Invalid JSON")

        session_id = body.get("session_id")
        original_text = body.get("original_text", "")
        instruction = body.get("instruction", "")

        if not session_id or not original_text or not instruction:
            raise HTTPException(400, "session_id, original_text, and instruction are required")

        _verify_session_owner(request, session_id)

        try:
            sess = session_manager.get_session(session_id)
        except (KeyError, SessionNotFoundError):
            raise HTTPException(404, "Session not found")

        messages = [
            {"role": "system", "content": (
                "You are rewriting a previous response. Follow the instruction exactly. "
                "Output ONLY the rewritten text — no preamble, no explanation, no meta-commentary. "
                "Preserve any formatting (markdown, code blocks, lists) from the original."
            )},
            {"role": "user", "content": (
                f"Here is the original response:\n\n{original_text}\n\n"
                f"Instruction: {instruction}"
            )},
        ]

        async def stream_rewrite() -> AsyncGenerator[str, None]:
            full_response = ""
            try:
                async for chunk in stream_llm(
                    sess.endpoint_url,
                    sess.model,
                    messages,
                    headers=sess.headers,
                    temperature=0.7,
                    # 0 = let the server decide (no cap). A hardcoded 4096 made
                    # local reasoning models (Qwen3 / R1) burn the whole budget
                    # inside <think> and emit no rewrite — the bubble just hung
                    # on "Rewriting...". Same fix as the chat max_tokens cap.
                    max_tokens=0,
                    tools=None,
                ):
                    if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
                        try:
                            data = json.loads(chunk[6:])
                            if "delta" in data:
                                # Forward the chunk (so the client can show a
                                # thinking indicator) but DON'T fold reasoning
                                # tokens into the saved rewrite — only real
                                # content. reasoning_content arrives flagged
                                # with thinking:true.
                                if not data.get("thinking"):
                                    full_response += data["delta"]
                                yield chunk
                        except json.JSONDecodeError:
                            yield chunk
                    elif chunk.startswith("event: "):
                        yield chunk
                    elif chunk == "data: [DONE]\n\n":
                        # Update the last assistant message in session history.
                        # Strip reasoning-model <think> blocks so the persisted
                        # rewrite is just the rewritten text, not its scratchpad.
                        from src.research_utils import strip_thinking
                        full_response = strip_thinking(full_response).strip() or full_response
                        if full_response:
                            for msg in reversed(sess.history):
                                if (isinstance(msg, ChatMessage) and msg.role == 'assistant') or \
                                   (isinstance(msg, dict) and msg.get('role') == 'assistant'):
                                    if isinstance(msg, ChatMessage):
                                        msg.content = full_response
                                    else:
                                        msg['content'] = full_response
                                    break
                            # Update in DB too
                            db = SessionLocal()
                            try:
                                db_msg = (
                                    db.query(DBChatMessage)
                                    .filter(DBChatMessage.session_id == session_id, DBChatMessage.role == 'assistant')
                                    .order_by(DBChatMessage.timestamp.desc())
                                    .first()
                                )
                                if db_msg:
                                    db_msg.content = full_response
                                    db.commit()
                            except Exception as e:
                                logger.warning("Failed to update rewritten message in DB: %s", e)
                                db.rollback()
                            finally:
                                db.close()
                            session_manager.save_sessions()
                        yield chunk
            except Exception as e:
                logger.error("Rewrite stream error: %s", e)
                yield f'event: error\ndata: {json.dumps({"error": str(e), "status": 500})}\n\n'

        return StreamingResponse(stream_rewrite(), media_type="text/event-stream")

    return router
