# core/models.py
"""
Pure data models — no database logic, no side effects.

These are simple datacontainers. All persistence is handled by SessionManager.
"""

from dataclasses import dataclass
from typing import Dict, List, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .session_manager import SessionManager

# Module-level session manager reference (set at app startup)
_session_manager: Optional["SessionManager"] = None


def set_session_manager(manager: "SessionManager"):
    """Set the global session manager reference."""
    global _session_manager
    _session_manager = manager


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
    """A chat session — pure data container."""
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
        if self.history is None:
            self.history = []
        if self.headers is None:
            self.headers = {}

    def add_message(self, message: ChatMessage):
        """
        Add a message to this session.

        Delegates to SessionManager for persistence if available,
        otherwise just appends to history.
        """
        self.history.append(message)
        self.message_count = len(self.history)

        # Delegate to session manager for persistence
        if _session_manager:
            _session_manager._persist_message(self.id, message)

    def get_context_messages(self) -> List[Dict[str, Any]]:
        """Get messages in format for LLM API.

        Slash-command / setup replies are persisted to history so they render
        in the transcript, but they are UI chatter (e.g. ``/setup ...`` and its
        status lines) the user never meant as conversation. They carry
        ``metadata.source == "slash"``; exclude them here so they never reach
        the model. Display/history-load paths use the raw ``history`` and are
        unaffected.
        """
        return [
            msg.to_dict()
            for msg in self.history
            if (msg.metadata or {}).get("source") != "slash"
        ]

    def get(self, key: str, default=None):
        """Dict-like access for compatibility."""
        return getattr(self, key, default)
