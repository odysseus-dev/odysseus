"""Deterministic capability metadata for agent tools.

Model output requests an action; it never supplies the authority for that
action.  This module classifies the effects of each built-in tool and applies
run-local integrity gates before dispatch.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from src.tool_approval_scopes import CHAT_SESSION_APPROVAL_CONTEXT_MARKER
from src.tool_security import BUILTIN_EMAIL_TOOLS


class ToolEffect(str, Enum):
    READ_PUBLIC = "read_public"
    READ_WORKSPACE = "read_workspace"
    READ_PRIVATE = "read_private"
    WRITE_WORKSPACE = "write_workspace"
    WRITE_PRIVATE = "write_private"
    EXECUTE_CODE = "execute_code"
    BROKERED_NETWORK_READ = "brokered_network_read"
    NETWORK_EGRESS = "network_egress"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"
    UI_SIDE_EFFECT = "ui_side_effect"
    ADMIN_CHANGE = "admin_change"
    DESTRUCTIVE = "destructive"
    USER_INTERACTION = "user_interaction"


class ResultIntegrity(str, Enum):
    SYSTEM = "system"
    WORKSPACE_UNTRUSTED = "workspace_untrusted"
    EXTERNAL_UNTRUSTED = "external_untrusted"


@dataclass(frozen=True)
class ToolCapabilities:
    effects: frozenset[ToolEffect]
    result_integrity: ResultIntegrity = ResultIntegrity.SYSTEM
    known: bool = True


def _capabilities(
    *effects: ToolEffect,
    result_integrity: ResultIntegrity = ResultIntegrity.SYSTEM,
) -> ToolCapabilities:
    return ToolCapabilities(frozenset(effects), result_integrity)


_REGISTRY: dict[str, ToolCapabilities] = {}


def _register(
    names: Iterable[str],
    *effects: ToolEffect,
    result_integrity: ResultIntegrity = ResultIntegrity.SYSTEM,
) -> None:
    capabilities = _capabilities(*effects, result_integrity=result_integrity)
    for name in names:
        if name in _REGISTRY:
            raise RuntimeError(f"Duplicate tool capability classification: {name}")
        _REGISTRY[name] = capabilities


_register(
    {"ask_user", "update_plan"},
    ToolEffect.USER_INTERACTION,
)
_register(
    {
        "list_cached_models",
        "list_cookbook_servers",
        "list_downloads",
        "list_models",
        "list_serve_presets",
        "list_served_models",
    },
    ToolEffect.READ_PRIVATE,
    # These readers return provider-controlled model identifiers or durable
    # user/admin-authored Cookbook and process state.  Local brokering does not
    # make the returned text server-authored.
    result_integrity=ResultIntegrity.EXTERNAL_UNTRUSTED,
)
_register(
    {"search_hf_models"},
    ToolEffect.BROKERED_NETWORK_READ,
    result_integrity=ResultIntegrity.EXTERNAL_UNTRUSTED,
)
_register(
    {"get_workspace", "glob", "grep", "ls", "read_file"},
    ToolEffect.READ_WORKSPACE,
    result_integrity=ResultIntegrity.WORKSPACE_UNTRUSTED,
)
_register(
    {"web_search"},
    ToolEffect.BROKERED_NETWORK_READ,
    result_integrity=ResultIntegrity.EXTERNAL_UNTRUSTED,
)
_register(
    {"web_fetch"},
    ToolEffect.BROKERED_NETWORK_READ,
    ToolEffect.NETWORK_EGRESS,
    result_integrity=ResultIntegrity.EXTERNAL_UNTRUSTED,
)
_register(
    {
        "list_email_accounts",
        "list_emails",
        "read_email",
        "resolve_contact",
        "scan_email_unsubscribes",
        "search_chats",
        "search_emails",
        "list_sessions",
        "tail_serve_output",
        "vault_get",
        "vault_search",
    },
    ToolEffect.READ_PRIVATE,
    result_integrity=ResultIntegrity.EXTERNAL_UNTRUSTED,
)
_register(
    {"bash", "manage_bg_jobs", "python"},
    ToolEffect.EXECUTE_CODE,
    result_integrity=ResultIntegrity.WORKSPACE_UNTRUSTED,
)
_register(
    {"apply_patch", "edit_file", "write_file"},
    ToolEffect.WRITE_WORKSPACE,
    # Successful writes include unified diffs that can echo arbitrary existing
    # workspace content back into the next model round.
    result_integrity=ResultIntegrity.WORKSPACE_UNTRUSTED,
)
_register(
    {
        "create_document",
        "manage_calendar",
        "manage_contact",
        "manage_documents",
        "manage_memory",
        "manage_notes",
        "manage_research",
        "manage_session",
        "manage_skills",
        "manage_tasks",
        "suggest_document",
        "todowrite",
    },
    ToolEffect.WRITE_PRIVATE,
)
_register(
    {
        "ai_draft_email_reply",
        "create_session",
        "draft_email",
        "draft_email_reply",
    },
    ToolEffect.WRITE_PRIVATE,
    # These tools resolve user-configured endpoints/accounts or read stored
    # email content before returning model-visible status text.
    result_integrity=ResultIntegrity.EXTERNAL_UNTRUSTED,
)
_register(
    {"edit_document", "update_document"},
    ToolEffect.WRITE_PRIVATE,
    # These tools can echo stored document content that was not present in
    # their arguments.  edit_document returns the complete edited document;
    # update_document also preserves stored email headers/thread history.
    result_integrity=ResultIntegrity.EXTERNAL_UNTRUSTED,
)
_register(
    {"pipeline"},
    ToolEffect.NETWORK_EGRESS,
    result_integrity=ResultIntegrity.EXTERNAL_UNTRUSTED,
)
_register(
    {"send_to_session"},
    ToolEffect.NETWORK_EGRESS,
    ToolEffect.WRITE_PRIVATE,
    result_integrity=ResultIntegrity.EXTERNAL_UNTRUSTED,
)
_register(
    {"chat_with_model", "ask_teacher"},
    ToolEffect.NETWORK_EGRESS,
    result_integrity=ResultIntegrity.EXTERNAL_UNTRUSTED,
)
_register(
    {"download_attachment"},
    ToolEffect.READ_PRIVATE,
    ToolEffect.WRITE_WORKSPACE,
    result_integrity=ResultIntegrity.EXTERNAL_UNTRUSTED,
)
_register(
    {"edit_image", "generate_image", "trigger_research"},
    ToolEffect.NETWORK_EGRESS,
    ToolEffect.WRITE_PRIVATE,
    result_integrity=ResultIntegrity.EXTERNAL_UNTRUSTED,
)
_register(
    {
        "archive_email",
        "bulk_email",
        "mark_email_read",
        "reply_to_email",
        "send_email",
        "unsubscribe_email",
    },
    ToolEffect.EXTERNAL_SIDE_EFFECT,
    # Email action results can include stored headers/account labels or remote
    # SMTP/IMAP responses, even when the action itself succeeded.
    result_integrity=ResultIntegrity.EXTERNAL_UNTRUSTED,
)
_register(
    {"delete_email"},
    ToolEffect.EXTERNAL_SIDE_EFFECT,
    ToolEffect.DESTRUCTIVE,
    result_integrity=ResultIntegrity.EXTERNAL_UNTRUSTED,
)
_register(
    {"ui_control"},
    ToolEffect.UI_SIDE_EFFECT,
    # Model switches and custom-theme validation read mutable user settings.
    result_integrity=ResultIntegrity.EXTERNAL_UNTRUSTED,
)
_register(
    {
        "adopt_served_model",
        "cancel_download",
        "download_model",
        "serve_model",
        "serve_preset",
        "stop_served_model",
        "vault_unlock",
    },
    ToolEffect.ADMIN_CHANGE,
    # Cookbook/process operations can return stored presets, provider data,
    # remote shell output, and command errors.
    result_integrity=ResultIntegrity.EXTERNAL_UNTRUSTED,
)
_register(
    {
        "api_call",
        "app_api",
        "manage_endpoints",
        "manage_mcp",
        "manage_settings",
        "manage_tokens",
        "manage_webhooks",
    },
    ToolEffect.ADMIN_CHANGE,
    # api_call/app_api return remote or stored application data, and the
    # admin managers can echo user-controlled configuration.  Conservatively
    # retain the action effect while treating every successful result as data.
    result_integrity=ResultIntegrity.EXTERNAL_UNTRUSTED,
)


TOOL_CAPABILITIES: Mapping[str, ToolCapabilities] = MappingProxyType(dict(_REGISTRY))
KNOWN_CAPABILITY_TOOLS = frozenset(TOOL_CAPABILITIES)

_UNKNOWN_CAPABILITIES = _capabilities(
    ToolEffect.READ_PRIVATE,
    ToolEffect.WRITE_WORKSPACE,
    ToolEffect.WRITE_PRIVATE,
    ToolEffect.EXECUTE_CODE,
    ToolEffect.NETWORK_EGRESS,
    ToolEffect.EXTERNAL_SIDE_EFFECT,
    ToolEffect.ADMIN_CHANGE,
    ToolEffect.DESTRUCTIVE,
    result_integrity=ResultIntegrity.EXTERNAL_UNTRUSTED,
)
_UNKNOWN_CAPABILITIES = ToolCapabilities(
    _UNKNOWN_CAPABILITIES.effects,
    _UNKNOWN_CAPABILITIES.result_integrity,
    known=False,
)
_BROWSER_MCP_READ_CAPABILITIES = _capabilities(
    ToolEffect.BROKERED_NETWORK_READ,
    result_integrity=ResultIntegrity.EXTERNAL_UNTRUSTED,
)
_BROWSER_MCP_READ_TOOLS = frozenset(
    {
        "mcp__builtin_browser__browser_console_messages",
        "mcp__builtin_browser__browser_network_requests",
        "mcp__builtin_browser__browser_snapshot",
        "mcp__builtin_browser__browser_take_screenshot",
    }
)


def capabilities_for_tool(tool_name: Any) -> ToolCapabilities:
    """Return deterministic capabilities; malformed and unknown tools fail high."""
    if not isinstance(tool_name, str) or not tool_name:
        return _UNKNOWN_CAPABILITIES
    capabilities = TOOL_CAPABILITIES.get(tool_name)
    if capabilities is not None:
        return capabilities
    if tool_name.startswith("mcp__email__"):
        bare_name = tool_name[len("mcp__email__"):]
        capabilities = TOOL_CAPABILITIES.get(bare_name)
        if bare_name in BUILTIN_EMAIL_TOOLS and capabilities is not None:
            return capabilities
    if tool_name in _BROWSER_MCP_READ_TOOLS:
        return _BROWSER_MCP_READ_CAPABILITIES
    return _UNKNOWN_CAPABILITIES


_PRIVATE_ACTION_READS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "manage_calendar": frozenset({"list_calendars", "list_events"}),
        "manage_contact": frozenset({"list"}),
        "manage_documents": frozenset({"list", "read", "view", "open", "get"}),
        "manage_memory": frozenset({"list", "search"}),
        "manage_notes": frozenset({"list", "search", "find", "view"}),
        "manage_research": frozenset({"list", "read", "open", "view", "get"}),
        "manage_session": frozenset({"list", "switch", "open", "select", "view"}),
        "manage_skills": frozenset({"list", "index", "view", "view_ref", "search"}),
        "manage_tasks": frozenset({"list"}),
    }
)

_PRIVATE_ACTION_WRITES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "manage_calendar": frozenset(
            {"create_event", "update_event", "delete_event"}
        ),
        "manage_contact": frozenset({"add", "update", "edit", "delete"}),
        "manage_documents": frozenset({"delete", "tidy"}),
        "manage_memory": frozenset({"add", "edit", "delete"}),
        "manage_notes": frozenset({"add", "update", "delete", "toggle_item"}),
        "manage_research": frozenset({"delete"}),
        "manage_session": frozenset(
            {
                "rename",
                "archive",
                "unarchive",
                "delete",
                "important",
                "unimportant",
                "truncate",
                "fork",
            }
        ),
        "manage_skills": frozenset({"add", "edit", "patch", "publish", "delete"}),
        "manage_tasks": frozenset({"create", "edit", "delete", "pause", "resume", "run"}),
    }
)

_ACTION_DESTRUCTIVE: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "manage_calendar": frozenset({"delete_event"}),
        "manage_contact": frozenset({"delete"}),
        "manage_documents": frozenset({"delete", "tidy"}),
        "manage_endpoints": frozenset({"delete"}),
        "manage_bg_jobs": frozenset({"kill", "stop", "cancel", "terminate"}),
        "manage_memory": frozenset({"delete"}),
        "manage_mcp": frozenset({"delete"}),
        "manage_notes": frozenset({"delete"}),
        "manage_research": frozenset({"delete"}),
        "manage_session": frozenset({"delete", "truncate"}),
        "manage_settings": frozenset({"delete", "reset"}),
        "manage_skills": frozenset({"delete"}),
        "manage_tasks": frozenset({"delete"}),
        "manage_tokens": frozenset({"delete"}),
        "manage_webhooks": frozenset({"delete"}),
    }
)

_ACTION_DEFAULTS: Mapping[str, str] = MappingProxyType(
    {
        "manage_calendar": "list_events",
        "manage_documents": "list",
        "manage_research": "list",
        "manage_tasks": "list",
    }
)

_ACTION_ALIASES: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        "manage_calendar": MappingProxyType(
            {
                "create": "create_event",
                "update": "update_event",
                "delete": "delete_event",
                "list": "list_events",
            }
        ),
        "manage_notes": MappingProxyType(
            {
                "create": "add",
                "new": "add",
                "save": "add",
                "remind": "add",
                "reminder": "add",
                "remove": "delete",
                "remove_item": "toggle_item",
            }
        ),
    }
)

_LINE_ACTION_TOOLS = frozenset({"manage_memory", "manage_session"})


def _action_from_content(tool_name: str, content: Any) -> str | None:
    """Extract the action discriminator using the same accepted input shapes."""
    if isinstance(content, Mapping):
        payload: Any = dict(content)
    elif isinstance(content, str):
        raw = content.strip()
        if tool_name in _LINE_ACTION_TOOLS and raw and not raw.startswith("{"):
            return raw.splitlines()[0].strip().replace("-", "_").casefold() or None
        try:
            payload = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            return None
    else:
        payload = {}

    if not isinstance(payload, dict):
        return None
    if (
        len(payload) == 1
        and isinstance(payload.get("body"), dict)
        and "action" in payload["body"]
    ):
        payload = payload["body"]

    action = payload.get("action")
    if (
        not action
        and tool_name == "manage_calendar"
        and isinstance(payload.get("events"), list)
    ):
        action = "create_event"
    if not action and tool_name == "manage_tasks" and any(
        payload.get(key) is not None
        for key in ("task", "description", "schedule", "time", "day_of_week")
    ):
        action = "create"
    if not isinstance(action, str) or not action.strip():
        action = _ACTION_DEFAULTS.get(tool_name)
    if not action:
        return None
    normalized = action.strip().replace("-", "_").casefold()
    return _ACTION_ALIASES.get(tool_name, {}).get(normalized, normalized)


def capabilities_for_action(tool_name: Any, content: Any) -> ToolCapabilities:
    """Classify a sealed multiplexed action; ambiguous actions fail high."""
    base = capabilities_for_tool(tool_name)
    if not isinstance(tool_name, str):
        return base

    action = _action_from_content(tool_name, content)
    destructive = action in _ACTION_DESTRUCTIVE.get(tool_name, ())
    if tool_name not in _PRIVATE_ACTION_READS:
        if not destructive:
            return base
        return ToolCapabilities(
            frozenset(set(base.effects) | {ToolEffect.DESTRUCTIVE}),
            base.result_integrity,
            known=base.known,
        )
    if action in _PRIVATE_ACTION_READS[tool_name]:
        return _capabilities(
            ToolEffect.READ_PRIVATE,
            result_integrity=ResultIntegrity.EXTERNAL_UNTRUSTED,
        )
    if action in _PRIVATE_ACTION_WRITES[tool_name]:
        effects = set(base.effects)
        if destructive:
            effects.add(ToolEffect.DESTRUCTIVE)
        return ToolCapabilities(
            frozenset(effects),
            ResultIntegrity.EXTERNAL_UNTRUSTED,
            known=base.known,
        )

    return _capabilities(
        ToolEffect.READ_PRIVATE,
        ToolEffect.WRITE_PRIVATE,
        result_integrity=ResultIntegrity.EXTERNAL_UNTRUSTED,
    )


def tool_result_is_successful(result: Any) -> bool:
    """Return whether a result actually introduced successful tool output."""
    return bool(
        isinstance(result, dict)
        and not result.get("blocked")
        and not result.get("approval_required")
        and not result.get("error")
        and result.get("exit_code") in (None, 0)
        and result.get("success") is not False
    )


def tool_result_should_arm_gate(
    tool_name: Any,
    result: Any,
    content: Any = None,
) -> bool:
    """Return whether a result introduced non-system content to the model.

    A blocked/approval placeholder and a genuinely content-free failure do not
    change authority. Once a non-system tool returns text or structured data
    that will be folded into model context, however, failure status cannot make
    that payload trusted: MCP ``isError`` text, provider exception messages,
    and HTTP error bodies are all attacker-controlled input surfaces.
    """
    if not isinstance(result, dict):
        return False
    if result.get("blocked") or result.get("approval_required"):
        return False
    # A producer that knows a particular response body came from a remote or
    # stored source overrides a coarse static SYSTEM default.
    if result.get("untrusted_content") is True:
        return True
    capabilities = capabilities_for_action(tool_name, content)
    if capabilities.result_integrity is ResultIntegrity.SYSTEM:
        return False
    if tool_result_is_successful(result):
        return True
    # ``format_tool_result`` serializes every additional structured field, so
    # a fixed allowlist here would inevitably miss model-visible payloads such
    # as ``details``, ``events``, or provider-specific response keys. Exclude
    # only status/policy controls that carry no producer content; any other
    # non-empty field crosses the same integrity boundary even on failure.
    non_content_keys = frozenset(
        {
            "approval_required",
            "blocked",
            "exit_code",
            "policy",
            "success",
            "untrusted_content",
        }
    )
    return any(
        key not in non_content_keys and value not in (None, "", [], {}, ())
        for key, value in result.items()
    )


POST_EXTERNAL_BLOCKED_EFFECTS = frozenset(
    {
        ToolEffect.READ_PRIVATE,
        ToolEffect.WRITE_WORKSPACE,
        ToolEffect.WRITE_PRIVATE,
        ToolEffect.EXECUTE_CODE,
        ToolEffect.NETWORK_EGRESS,
        ToolEffect.EXTERNAL_SIDE_EFFECT,
        ToolEffect.UI_SIDE_EFFECT,
        ToolEffect.ADMIN_CHANGE,
        ToolEffect.DESTRUCTIVE,
    }
)


@dataclass(frozen=True)
class ToolGateDecision:
    allowed: bool
    reason: str | None = None


_EXTERNAL_MESSAGE_SOURCES = frozenset(
    {
        "injected research context",
        "prefetched search context",
        "research context",
        "web search results",
        "youtube transcript",
    }
)
_EXTERNAL_MESSAGE_SOURCE_PREFIXES = ("web page:",)


def messages_contain_external_untrusted_context(messages: Iterable[dict]) -> bool:
    """Detect explicitly labelled external context already present in a run."""
    for message in messages or ():
        if not isinstance(message, dict):
            continue
        metadata = message.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("trusted") is not False:
            continue
        gate_marker = metadata.get("tool_gate_untrusted")
        if gate_marker is True:
            return True
        if gate_marker is False:
            # Explicit current-format opt-outs are authoritative.  The source
            # label heuristics below exist only for older saved wrappers that
            # predate the marker.
            continue
        if metadata.get("provenance_origin") == "external":
            return True
        source = metadata.get("source")
        if not isinstance(source, str):
            continue
        normalized_source = source.strip().casefold()
        if normalized_source in _EXTERNAL_MESSAGE_SOURCES:
            return True
        if normalized_source.startswith(_EXTERNAL_MESSAGE_SOURCE_PREFIXES):
            return True
    return False


@dataclass
class ToolRunSecurityContext:
    """Server-owned integrity state for one agent run."""

    external_untrusted_context_seen: bool = False
    external_sources: list[str] = field(default_factory=list)
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    # Task-scope approval sets this for the resumed in-memory run. Chat-scope
    # approval is projected from the server-owned session history marker below.
    # The bypass affects only this automatic gate; current tool policy, ownership,
    # workspace confinement, and execution/sandbox restrictions still apply.
    approval_gate_bypassed: bool = False

    def observe_messages(self, messages: Iterable[dict]) -> None:
        """Apply server-owned chat scope and promote untrusted prompt context."""
        message_list = list(messages or ())
        if any(
            isinstance(message, dict)
            and isinstance(message.get("metadata"), dict)
            and message["metadata"].get(
                CHAT_SESSION_APPROVAL_CONTEXT_MARKER
            ) is True
            for message in message_list
        ):
            self.approval_gate_bypassed = True
        if messages_contain_external_untrusted_context(message_list):
            self.external_untrusted_context_seen = True

    def decision_for(self, tool_name: Any, content: Any = None) -> ToolGateDecision:
        if self.approval_gate_bypassed:
            return ToolGateDecision(True)
        if not self.external_untrusted_context_seen:
            return ToolGateDecision(True)
        capabilities = capabilities_for_action(tool_name, content)
        blocked_effects = capabilities.effects & POST_EXTERNAL_BLOCKED_EFFECTS
        if capabilities.known and not blocked_effects:
            return ToolGateDecision(True)
        effects = ", ".join(sorted(effect.value for effect in blocked_effects))
        if not capabilities.known:
            effects = "unknown/high-impact"
        return ToolGateDecision(
            False,
            (
                "External untrusted context has already influenced this run. "
                f"Tool '{tool_name}' requires a separate user-authorized action "
                f"because it can cause {effects}."
            ),
        )

    def observe_tool_result(
        self,
        tool_name: Any,
        result: Any,
        content: Any = None,
    ) -> None:
        if not tool_result_should_arm_gate(tool_name, result, content):
            return
        self.external_untrusted_context_seen = True
        if isinstance(tool_name, str) and tool_name not in self.external_sources:
            self.external_sources.append(tool_name)


def blocked_tool_result(tool_name: Any, reason: str) -> tuple[str, dict]:
    return (
        f"{tool_name}: BLOCKED",
        {
            "error": reason,
            "exit_code": 1,
            "blocked": True,
            "policy": "external_untrusted_context",
        },
    )
