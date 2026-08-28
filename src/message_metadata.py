"""Trust-boundary helpers for client-supplied chat metadata."""

from typing import Any

from src.tool_approval_scopes import CHAT_SESSION_APPROVAL_CONTEXT_MARKER


_SERVER_OWNED_MESSAGE_METADATA = frozenset({
    "tool_events",
    CHAT_SESSION_APPROVAL_CONTEXT_MARKER,
})


def sanitize_client_message_metadata(metadata: Any) -> Any:
    """Drop fields that can only be produced by server-side tool execution."""
    if not isinstance(metadata, dict):
        return metadata
    return {
        key: value
        for key, value in metadata.items()
        if key not in _SERVER_OWNED_MESSAGE_METADATA
    }
