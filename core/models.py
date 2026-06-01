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
    """A chat session — pure data container.

    IMPORTANT: History immutability contract
    ----------------------------------------
    ``.history`` exposes a COPY of the internal message list. Callers that
    mutate ``.history`` (e.g. via ``.append()``) are modifying a throwaway
    list that will NOT affect the session or persist. Always use
    ``.add_message()`` to append messages.

    ``.message_count`` always reflects the true internal count,
    NOT ``len(.history)`` (which may be stale if you held a reference).
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
        # Internal authoritative list
        if self.history is None:
            self._history: List[ChatMessage] = []
        else:
            self._history = list(self.history)
        # Public copy (callers get a snapshot, not the internal list)
        self.history = list(self._history)

    def add_message(self, message: ChatMessage):
        """
        Add a message to this session.

        Appends to the internal _history list, then exposes a fresh copy
        via .history. The caller's pre-existing references to .history
        are unaffected (they hold the previous snapshot).

        Delegates to SessionManager for persistence if available.
        """
        self._history.append(message)
        self.history = list(self._history)
        self.message_count = len(self._history)

        # Delegate to session manager for persistence
        if _session_manager:
            _session_manager._persist_message(self.id, message)

    def get_context_messages(self) -> List[Dict[str, Any]]:
        """Get messages in format for LLM API (derived from internal list)."""
        return [msg.to_dict() for msg in self._history]

    def get(self, key: str, default=None):
        """Dict-like access for compatibility."""
        return getattr(self, key, default)
