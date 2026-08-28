# core/models.py
"""
Pure data models — no database logic, no side effects.

These are simple datacontainers. All persistence is handled by SessionManager.
"""

from dataclasses import dataclass
from typing import Dict, List, Any, Optional, TYPE_CHECKING

from src.tool_approval_scopes import (
    CHAT_SESSION_APPROVAL_CONTEXT_MARKER,
    CHAT_SESSION_APPROVAL_DECISION,
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

    def get(self, key: str, default=None):
        """Dict-like access for compatibility."""
        return getattr(self, key, default)

    def __getitem__(self, key: str):
        """Allow session['field'] syntax."""
        return getattr(self, key)
