# core/models.py
"""
Pure data models — no database logic, no side effects.

These are simple datacontainers. All persistence is handled by SessionManager.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Dict, List, Any, Optional, TYPE_CHECKING

from src.tool_approval_scopes import (
    CHAT_SESSION_APPROVAL_CONTEXT_MARKER,
    CHAT_SESSION_APPROVAL_DECISION,
    CHAT_SESSION_APPROVAL_SIGNATURE_FIELD,
    verify_chat_session_grant,
)

if TYPE_CHECKING:
    from .session_manager import SessionManager

# Module-level session manager singleton (single source of truth)
_SESSION_MANAGER_INSTANCE: Optional["SessionManager"] = None


def set_session_manager_instance(manager: "SessionManager"):
    """Set the global SessionManager singleton."""
    global _SESSION_MANAGER_INSTANCE
    _SESSION_MANAGER_INSTANCE = manager


def get_session_manager_instance() -> Optional["SessionManager"]:
    """Get the global SessionManager singleton."""
    return _SESSION_MANAGER_INSTANCE


# Keep legacy name for backward compatibility
set_session_manager = set_session_manager_instance
get_session_manager = get_session_manager_instance


def _history_grants_chat_session_approval(
    history: List["ChatMessage"],
    session_id: str,
) -> bool:
    """Return whether this exact chat has a resolved session-scope grant."""

    expected_session = str(session_id or "")
    if not expected_session:
        return False
    for message in reversed(history or []):
        metadata = getattr(message, "metadata", None)
        if not isinstance(metadata, dict):
            continue
        tool_events = metadata.get("tool_events")
        if not isinstance(tool_events, list):
            continue
        for event in reversed(tool_events):
            ask_user = event.get("ask_user") if isinstance(event, dict) else None
            if not isinstance(ask_user, dict):
                continue
            if (
                ask_user.get("kind") == "tool_approval"
                and ask_user.get("resolved") == CHAT_SESSION_APPROVAL_DECISION
                and str(ask_user.get("session_id") or "") == expected_session
                # Shape proves nothing here: routes that accept a
                # caller-supplied metadata blob write into this same history.
                and verify_chat_session_grant(
                    ask_user.get(CHAT_SESSION_APPROVAL_SIGNATURE_FIELD),
                    expected_session,
                    ask_user.get("approval_id"),
                    CHAT_SESSION_APPROVAL_DECISION,
                )
            ):
                return True
    return False


@dataclass
class ChatMessage:
    """A single chat message."""
    role: str
    content: str
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for API responses."""
        result = {"role": self.role, "content": self.content}
        if self.metadata:
            result["metadata"] = self.metadata
        return result

    def get(self, key: str, default=None):
        """Dict-like access for compatibility."""
        return getattr(self, key, default)


# Chat-route replay limits for historical tool output (issue #6816). Persisted
# output is already capped at MAX_OUTPUT_CHARS, but replaying many old turns at
# that size would crowd out the current request, so older turns get a much
# smaller budget than the two most recent tool-bearing turns.
_RECENT_TOOL_OUTPUT_CHARS = 1500
_OLDER_TOOL_OUTPUT_CHARS = 300
_HISTORY_TOOL_CALL_PREFIX = "odysseus-hist-"
_TOOL_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _history_tool_name(value: Any) -> str:
    """Return a provider-safe function name for a persisted tool event."""
    raw = str(value or "").strip()
    if not raw:
        return "tool"
    return _TOOL_NAME_RE.sub("_", raw)[:64] or "tool"


def _history_tool_call_id(
    *,
    session_id: str,
    message_index: int,
    round_num: Any,
    event_index: int,
    tool_name: str,
    command: str,
    existing: Any = None,
) -> str:
    """Return a stable correlation ID for a replayed historical tool call.

    Native calls already carry an ID; preserve it. Events written by the main
    agent loop do not, so derive one from the session, turn, round, event
    position, tool name and command. That keeps IDs deterministic across
    requests without colliding with live ``odysseus-auto-`` IDs.
    """
    explicit = str(existing or "").strip()
    if explicit:
        return explicit
    seed = "\0".join((
        str(session_id or ""),
        str(message_index),
        str(round_num),
        str(event_index),
        str(tool_name or ""),
        str(command or ""),
    ))
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    return f"{_HISTORY_TOOL_CALL_PREFIX}{digest}"


def _history_tool_arguments(event: Dict[str, Any]) -> str:
    """Return a valid JSON-object string for a persisted tool call.

    The agent loop persists the original arguments as ``command`` for most
    tools, but document tools persist only a truncated display preview. Older
    or forked histories may use ``args``. Anything that is not already a JSON
    object is wrapped so ``function.arguments`` always parses.
    """
    raw = event.get("command")
    if raw is None or raw == "":
        raw = event.get("args")
    if isinstance(raw, dict):
        return json.dumps(raw, ensure_ascii=False, default=str)
    text = str(raw or "").strip()
    if not text:
        return "{}"
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, dict):
        return text
    return json.dumps({"command": text}, ensure_ascii=False)


def _truncate_history_tool_output(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    marker = f"\n... [truncated {len(text) - limit} chars] ...\n"
    head = int(limit * 0.7)
    tail = max(0, limit - head)
    return text[:head] + marker + text[-tail:]


def _history_tool_output(event: Dict[str, Any], limit: int) -> str:
    """Render a persisted tool result, including failures and truncation."""
    raw = event.get("output")
    if raw is None:
        raw = event.get("result")
    if isinstance(raw, str):
        text = raw
    elif raw is None:
        text = ""
    else:
        try:
            text = json.dumps(raw, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(raw)
    text = _truncate_history_tool_output(text, limit)
    exit_code = event.get("exit_code")
    failed = bool(event.get("error")) or (
        exit_code is not None and str(exit_code).strip() not in ("", "0")
    )
    if failed:
        code = "" if exit_code is None else f": exit_code={exit_code}"
        marker = f"[tool failed{code}]"
        text = f"{marker}\n{text}".strip() if text else marker
    return text or "(no output)"


def _expand_persisted_tool_turn(
    message: Dict[str, Any],
    *,
    session_id: str,
    message_index: int,
    recent_tool_turn: bool,
) -> List[Dict[str, Any]]:
    """Expand one assistant turn's ``tool_events`` into OpenAI tool messages.

    A round with several tool calls becomes one assistant message carrying all
    of them followed by one ``role:"tool"`` message per call, matching the live
    ``_append_tool_results`` shape. When ``round_texts`` lines up with the
    persisted round numbers, each round's prose is replayed on its own
    assistant message and only the terminal prose is left on the final
    assistant message. If it does not line up (rounds can be popped by the
    agent loop's recovery paths), intermediate messages omit content and the
    original assistant content is kept verbatim, so prose is never duplicated
    or lost.
    """
    metadata = message.get("metadata") or {}
    events = metadata.get("tool_events") or []
    if not isinstance(events, list):
        return [message]

    groups: Dict[Any, List[tuple]] = {}
    order: List[Any] = []
    for event_index, event in enumerate(events):
        if not isinstance(event, dict) or event.get("context_only"):
            continue
        round_num = event.get("round")
        key = round_num if isinstance(round_num, int) else None
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((event_index, event))
    if not groups:
        return [message]

    ordered_keys = sorted(key for key in order if key is not None)
    if None in groups:
        ordered_keys.append(None)

    round_texts = metadata.get("round_texts")
    texts = (
        [str(text or "") for text in round_texts]
        if isinstance(round_texts, list)
        else []
    )
    round_keys = [key for key in ordered_keys if isinstance(key, int)]
    faithful = bool(texts) and bool(round_keys) and all(
        1 <= key <= len(texts) for key in round_keys
    )

    limit = _RECENT_TOOL_OUTPUT_CHARS if recent_tool_turn else _OLDER_TOOL_OUTPUT_CHARS
    expanded: List[Dict[str, Any]] = []
    consumed_round = 0
    for key in ordered_keys:
        calls = []
        results = []
        for event_index, event in groups[key]:
            tool_name = _history_tool_name(event.get("tool"))
            command = event.get("command")
            if command is None or command == "":
                command = event.get("args")
            call_id = _history_tool_call_id(
                session_id=session_id,
                message_index=message_index,
                round_num=key,
                event_index=event_index,
                tool_name=tool_name,
                command=str(command or ""),
                existing=event.get("call_id") or event.get("tool_call_id"),
            )
            calls.append({
                "id": call_id,
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": _history_tool_arguments(event),
                },
            })
            results.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": _history_tool_output(event, limit),
            })

        content = None
        if faithful and isinstance(key, int) and 1 <= key <= len(texts):
            content = texts[key - 1].strip() or None
            consumed_round = max(consumed_round, key)
        expanded.append({
            "role": "assistant",
            "content": content,
            "tool_calls": calls,
        })
        expanded.extend(results)

    final = dict(message)
    final_metadata = dict(metadata)
    final_metadata.pop("tool_events", None)
    final_metadata.pop("round_texts", None)
    if final_metadata:
        final["metadata"] = final_metadata
    else:
        final.pop("metadata", None)

    raw_content = message.get("content")
    if isinstance(raw_content, str):
        final_content = raw_content
        if faithful and consumed_round:
            prefix = "\n\n".join(texts[:consumed_round])
            if prefix and final_content.startswith(prefix):
                final_content = final_content[len(prefix):]
        final["content"] = final_content.strip()
    else:
        final["content"] = raw_content
    if final.get("content"):
        expanded.append(final)
    return expanded


@dataclass
class Session:
    """A chat session — pure data container.

    ``.history`` is the authoritative mutable message list. Callers may
    read, append, pop, or reassign it directly — these changes take
    effect immediately. ``_history`` remains a compatibility alias that
    always resolves to the authoritative ``history`` list.

    Each session gets its own unique history list at construction time
    (the dataclass default is never shared between instances).
    """

    id: str
    name: str
    endpoint_url: str
    model: str
    rag: bool = False
    archived: bool = False
    headers: Optional[Dict[str, str]] = None
    history: List[ChatMessage] = None
    owner: Optional[str] = None
    is_important: bool = False
    message_count: int = 0
    memory_extraction_enabled: bool = True
    memory_injection_enabled: bool = True
    skill_injection_enabled: bool = True
    thinking_mode: str = "off"
    temperature_override: Optional[float] = None
    max_tokens_override: Optional[int] = None
    cwd: Optional[str] = None
    # Registered ModelEndpoint id this session is bound to (None = legacy /
    # URL-matched). Lets two endpoints that share a provider URL but not
    # credentials stay distinguishable.
    endpoint_id: Optional[str] = None

    def __post_init__(self):
        if self.headers is None:
            self.headers = {}
        # Ensure each session gets its OWN list (not the shared dataclass default)
        if self.history is None:
            self.history = []

    @property
    def _history(self) -> List[ChatMessage]:
        """Compatibility alias for callers that still reference ``_history``."""
        return self.history

    @_history.setter
    def _history(self, messages: List[ChatMessage]):
        self.history = messages

    def add_message(self, message: ChatMessage):
        """
        Add a message to this session.

        Appends to the authoritative history list and increments
        message_count. Delegates to SessionManager for persistence
        if available.
        """
        self.history.append(message)
        self.message_count = len(self.history)

        # Delegate to session manager for persistence
        if _SESSION_MANAGER_INSTANCE:
            _SESSION_MANAGER_INSTANCE._persist_message(self.id, message)

    def get_context_messages(self) -> List[Dict[str, Any]]:
        """Get messages in format for LLM API.

        Slash-command / setup replies are persisted to history so they render
        in the transcript, but they are UI chatter (e.g. ``/setup ...`` and its
        status lines) the user never meant as conversation. They carry
        ``metadata.source == "slash"``; exclude them here so they never reach
        the model. Display/history-load paths use the raw ``history`` and are
        unaffected.
        """
        messages = [
            msg.to_dict()
            for msg in self.history
            if (msg.metadata or {}).get("source") != "slash"
        ]
        from src.background_tool_jobs import background_result_context
        messages = [part for message in messages for part in (
            *background_result_context(message.get('metadata')), message,
        )]
        # Resume an interrupted thinking-only response from its actual model
        # reasoning channel. Restrict this to the latest assistant message so
        # old traces do not accumulate in context or cause reasoning loops.
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if message.get("role") != "assistant":
                continue
            metadata = message.get("metadata") or {}
            thinking = str(metadata.get("thinking") or "").strip()
            if metadata.get("stopped") and thinking:
                resumed = dict(message)
                resumed["reasoning_content"] = thinking
                messages[index] = resumed
            break
        if not _history_grants_chat_session_approval(self.history, self.id):
            return messages

        # Keep the grant close to the latest user request so route-neutral
        # compaction/trimming preserves it. Copy the metadata instead of
        # mutating the durable transcript object.
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].get("role") != "user":
                continue
            message = dict(messages[index])
            metadata = dict(message.get("metadata") or {})
            metadata[CHAT_SESSION_APPROVAL_CONTEXT_MARKER] = True
            message["metadata"] = metadata
            messages[index] = message
            break
        return messages

    def get_llm_messages(self, *, include_tool_calls: bool = True) -> List[Dict[str, Any]]:
        """Get chat-route history with persisted tool calls replayed.

        ``get_context_messages()`` is the route-neutral view used by
        compaction, the background monitor, memories and skills; it must stay
        plain text. The chat route, however, needs the assistant ``tool_calls``
        and matching ``role:"tool"`` results from earlier turns, otherwise the
        model sees a history where it answered every question from memory and
        stops calling tools (issue #6816).

        This builds on ``get_context_messages()`` so slash filtering,
        background results, thinking resume and the chat-session approval
        marker are preserved, then expands assistant turns that carry
        ``metadata["tool_events"]``. Callers that cannot accept replayed tool
        calls (e.g. Gemini, whose historical ``thought_signature`` is not
        persisted) pass ``include_tool_calls=False`` for the plain view.
        """
        messages = self.get_context_messages()
        if not include_tool_calls:
            return messages

        expandable = set()
        for index, message in enumerate(messages):
            if message.get("role") != "assistant" or message.get("tool_calls"):
                continue
            events = (message.get("metadata") or {}).get("tool_events")
            if isinstance(events, list) and events:
                expandable.add(index)
        if not expandable:
            return messages

        recent = set(sorted(expandable)[-2:])
        expanded: List[Dict[str, Any]] = []
        for index, message in enumerate(messages):
            if index in expandable:
                expanded.extend(_expand_persisted_tool_turn(
                    message,
                    session_id=self.id,
                    message_index=index,
                    recent_tool_turn=index in recent,
                ))
            else:
                expanded.append(message)
        return expanded

    def get(self, key: str, default=None):
        """Dict-like access for compatibility."""
        return getattr(self, key, default)

    def __getitem__(self, key: str):
        """Allow session['field'] syntax."""
        return getattr(self, key)
