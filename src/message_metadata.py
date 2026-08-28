"""Trust-boundary helpers for client-supplied chat metadata."""

from typing import Any, Optional

from src.tool_approval_scopes import CHAT_SESSION_APPROVAL_CONTEXT_MARKER


_SERVER_OWNED_MESSAGE_METADATA = frozenset({
    "tool_events",
    CHAT_SESSION_APPROVAL_CONTEXT_MARKER,
})

_APPROVAL_PROVENANCE_FIELDS = frozenset({
    "approval_id",
    "approved_by_interactive_session",
    "resolved",
    "session_id",
})


def _scrub_approval_metadata(value: Any, *, projection: bool, in_approval: bool = False):
    """Copy metadata while removing fields that can imply approval authority.

    Client ingress drops every server-owned tool-event container.  Context
    projection keeps harmless server-generated tool-event display data, but
    strips the approval selectors and resolution/provenance fields from every
    nested shape.  This makes old rows useful for display without allowing a
    legacy dict, nested dict, or list-shaped payload to become authority.
    """
    if isinstance(value, dict):
        kind = value.get("kind")
        approval_scope = in_approval or kind == "tool_approval"
        cleaned = {}
        for key, item in value.items():
            if key in _SERVER_OWNED_MESSAGE_METADATA and not (
                projection and key == "tool_events"
            ):
                continue
            if key == "tool_approval":
                continue
            # These fields have no safe client/display meaning in a message
            # projection. Strip them even when a legacy writer placed them at
            # the metadata root instead of under a recognizable approval node.
            if key in _APPROVAL_PROVENANCE_FIELDS:
                continue
            if key == "ask_user":
                scrubbed = _scrub_approval_metadata(
                    item, projection=projection, in_approval=True
                )
                if scrubbed:
                    cleaned[key] = scrubbed
                continue
            if key == "tool_events":
                # Some legacy writers placed approval fields directly on an
                # event rather than under ask_user. Treat the complete event
                # container as non-authoritative approval-shaped metadata.
                cleaned[key] = _scrub_approval_metadata(
                    item, projection=projection, in_approval=True
                )
                continue
            cleaned[key] = _scrub_approval_metadata(
                item, projection=projection, in_approval=approval_scope
            )
        return cleaned
    if isinstance(value, list):
        return [
            _scrub_approval_metadata(item, projection=projection, in_approval=in_approval)
            for item in value
        ]
    return value


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
    sanitized = _scrub_approval_metadata(metadata, projection=False)
    return sanitized or None


def sanitize_projected_message_metadata(metadata: Any) -> Optional[dict]:
    """Return a model-context copy with approval provenance stripped.

    The projection path may retain non-authoritative tool-event details for
    continuity, but it never projects the raw chat-session marker or the
    legacy fields that used to be interpreted as a durable approval grant.
    A separate server-owned grant store is the only source for that marker.
    """
    if not isinstance(metadata, dict):
        return None
    cleaned = _scrub_approval_metadata(metadata, projection=True)
    return cleaned or None
