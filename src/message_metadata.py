"""Trust-boundary helpers for client-supplied chat metadata."""

from typing import Any, Optional

from src.tool_approval_scopes import CHAT_SESSION_APPROVAL_CONTEXT_MARKER


_SERVER_OWNED_MESSAGE_METADATA = frozenset({
    "tool_events",
    CHAT_SESSION_APPROVAL_CONTEXT_MARKER,
})


def sanitize_client_message_metadata(metadata: Any) -> Optional[dict]:
    """Normalize client metadata and drop server-owned fields.

    Client metadata is only a JSON object. In particular, do not let a
    list-of-pairs value reach ``dict.update``: that mapping-compatible shape
    can smuggle protected approval fields through an otherwise safe merge.
    Malformed metadata is normalized away; server-generated metadata remains
    untouched because this helper is called only at client ingress points.
    """
    if metadata is None:
        return None
    if not isinstance(metadata, dict):
        return None
    sanitized = {
        key: value
        for key, value in metadata.items()
        if key not in _SERVER_OWNED_MESSAGE_METADATA
    }
    return sanitized or None
