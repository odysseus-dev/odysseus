"""Shared wire values and scope markers for tool approval continuations."""

from __future__ import annotations

import hmac
import logging
from enum import Enum
from hashlib import sha256

logger = logging.getLogger(__name__)


# Keep the existing wire values so the current route and no-build frontend do
# not need a second protocol migration. ``approve`` no longer means one action;
# it now selects chat-session scope.
TASK_APPROVAL_DECISION = "approve_task"
CHAT_SESSION_APPROVAL_DECISION = "approve"
DENY_APPROVAL_DECISION = "deny"

# Session.get_context_messages() adds this server-owned marker only when the
# session history contains a matching, resolved chat-session approval.
CHAT_SESSION_APPROVAL_CONTEXT_MARKER = "_tool_approval_chat_session_granted"

# The server's proof that IT resolved this approval. More than one route
# writes caller-supplied metadata into session history, so a client can write
# the shape of a resolved card directly; only the server can produce this.
CHAT_SESSION_APPROVAL_SIGNATURE_FIELD = "_server_grant"


def _grant_key() -> bytes | None:
    """Key material for grant signatures, or None when it is unavailable.

    Reuses the persistent application key so a grant survives a restart the
    way the transcript holding it does.
    """
    try:
        from src.secret_storage import _load_or_create_key

        return _load_or_create_key()
    except Exception as exc:
        logger.warning("Tool approval grant key unavailable: %s", exc)
        return None


def sign_chat_session_grant(
    session_id: object,
    approval_id: object,
    decision: object,
) -> str | None:
    """Return the server's signature for one resolved chat-session grant."""

    key = _grant_key()
    if key is None:
        return None
    payload = "\x00".join(
        (
            str(session_id or ""),
            str(approval_id or ""),
            str(decision or "").strip().lower(),
        )
    )
    return hmac.new(key, payload.encode("utf-8"), sha256).hexdigest()


# Message-metadata keys the server writes and a caller never should. Both are
# read back as authority: ``tool_events`` carries the approval cards, and the
# context marker is projected onto a turn once a grant is found.
_SERVER_OWNED_METADATA_KEYS = (
    "tool_events",
    CHAT_SESSION_APPROVAL_CONTEXT_MARKER,
)


def sanitize_client_message_metadata(metadata):
    """Drop server-owned keys from a caller-supplied message metadata blob.

    Routes that persist a message on the caller's behalf accept this blob
    verbatim, which lets a caller write the shape of a resolved approval into
    its own transcript. The grant check verifies a signature, so this is not
    the control that closes that path; it keeps the state out of the
    transcript in the first place. Anything else in the blob is left alone.
    """
    if not isinstance(metadata, dict):
        return metadata
    if not any(key in metadata for key in _SERVER_OWNED_METADATA_KEYS):
        return metadata
    return {
        key: value
        for key, value in metadata.items()
        if key not in _SERVER_OWNED_METADATA_KEYS
    }


def stamp_chat_session_grant(
    ask_user: dict,
    session_id: object,
    decision: object,
) -> None:
    """Record the server's grant on a card it has just resolved.

    Call this only from the server-side resolve path. A decision that does not
    grant chat-session scope leaves no signature behind, so downgrading a
    ``deny`` to an ``approve`` in the transcript does not carry a usable one.
    """
    if not isinstance(ask_user, dict):
        return
    if str(decision or "").strip().lower() != CHAT_SESSION_APPROVAL_DECISION:
        ask_user.pop(CHAT_SESSION_APPROVAL_SIGNATURE_FIELD, None)
        return
    signature = sign_chat_session_grant(
        session_id,
        ask_user.get("approval_id"),
        CHAT_SESSION_APPROVAL_DECISION,
    )
    if signature:
        ask_user[CHAT_SESSION_APPROVAL_SIGNATURE_FIELD] = signature


def verify_chat_session_grant(
    signature: object,
    session_id: object,
    approval_id: object,
    decision: object,
) -> bool:
    """Whether *signature* is this server's grant for that exact approval.

    Fails CLOSED: an absent, malformed, or unverifiable signature is not a
    grant. Binding the session and approval ids into the payload means a
    signature lifted from one chat cannot be replayed into another.
    """
    # compare_digest accepts only ASCII strings. Treat arbitrary persisted
    # metadata as untrusted and require the exact representation we sign.
    if (
        not isinstance(signature, str)
        or len(signature) != sha256().digest_size * 2
        or any(character not in "0123456789abcdef" for character in signature)
    ):
        return False
    expected = sign_chat_session_grant(session_id, approval_id, decision)
    if expected is None:
        return False
    return hmac.compare_digest(signature, expected)


class ToolApprovalScope(str, Enum):
    # Surfaces without a resumable chat (the skill tester, unattended audits)
    # keep the original one-use meaning: the sealed action runs and the gate
    # re-arms immediately for anything after it.
    SINGLE_ACTION = "single_action"
    TASK = "task"
    CHAT_SESSION = "chat_session"


def scope_for_decision(decision: object) -> ToolApprovalScope | None:
    normalized = str(decision or "").strip().lower()
    if normalized == TASK_APPROVAL_DECISION:
        return ToolApprovalScope.TASK
    if normalized == CHAT_SESSION_APPROVAL_DECISION:
        return ToolApprovalScope.CHAT_SESSION
    return None
