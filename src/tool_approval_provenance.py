"""Durable server-owned provenance for chat-session tool approvals.

Approval cards and their resolution fields live in the chat transcript for
display compatibility.  They are intentionally not authority.  This module
owns the separate database row that can be created only after an interactive
server-side ``ExactToolApproval`` was consumed for the matching session and
owner.  Legacy transcript rows are never migrated into this table.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from fastapi import HTTPException

from src.owner_identity import auth_disabled, is_request_sentinel_owner

logger = logging.getLogger(__name__)

_PROVENANCE_VERSION = 1


def _owner_key(owner: Any) -> str:
    value = str(owner or "").strip().casefold()
    if not value or is_request_sentinel_owner(value):
        return ""
    return value


def _approval_binding_is_valid(
    approval: Any,
    *,
    approval_id: str,
    session_id: str,
    owner_key: str,
) -> bool:
    """Require the exact consumed chat-scope grant before inserting a row."""
    if approval is None or not getattr(approval, "grants_chat_session", False):
        return False
    pending = getattr(approval, "pending", None)
    if pending is None:
        return False
    return (
        str(getattr(pending, "approval_id", "") or "") == approval_id
        and str(getattr(pending, "session_id", "") or "") == session_id
        and _owner_key(getattr(pending, "owner", None)) == owner_key
    )


def create_chat_session_approval_grant(
    request,
    *,
    approval: Any,
    approval_id: Any,
    session_id: Any,
    owner: Any,
) -> bool:
    """Persist one interactive chat-session approval grant.

    The caller must supply the exact in-memory approval object returned by the
    one-use store.  Bearer principals are rejected even if they present a
    client-shaped approval payload.  A database failure fails closed by
    returning ``False``: it never manufactures an in-memory durable grant.
    """
    from src.auth_helpers import effective_user, require_interactive_request

    require_interactive_request(request)
    from src.tool_approvals import ExactToolApproval

    # This proof is set only by ToolApprovalStore.consume().  In particular,
    # a client-shaped dict or a hand-constructed ExactToolApproval is not an
    # interactive approval event and cannot mint durable authority.
    if not isinstance(approval, ExactToolApproval) or not getattr(
        approval, "_consumed_from_store", False
    ):
        return False
    approval_key = str(approval_id or "")
    session_key = str(session_id or "")
    requested_owner_key = _owner_key(owner)
    if not approval_key or not session_key or (
        not requested_owner_key and not auth_disabled()
    ):
        return False
    if not _approval_binding_is_valid(
        approval,
        approval_id=approval_key,
        session_id=session_key,
        owner_key=requested_owner_key,
    ):
        return False

    request_owner_key = _owner_key(effective_user(request))
    if not auth_disabled() and request_owner_key != requested_owner_key:
        raise HTTPException(403, "Approval owner does not match the interactive principal")

    # Import lazily so the pure request/auth helpers do not create a database
    # import cycle during application startup.
    from core.database import (
        ChatSessionApprovalGrant,
        Session as DbSession,
        SessionLocal,
    )

    db = SessionLocal()
    try:
        session_row = db.query(DbSession).filter(DbSession.id == session_key).first()
        if session_row is None:
            return False
        stored_owner_key = _owner_key(getattr(session_row, "owner", None))
        if stored_owner_key != requested_owner_key:
            # AUTH_ENABLED=false is a deliberate single-user compatibility
            # mode. It may reopen an owner-stamped legacy session, but the
            # grant remains bound to that stored owner for projection.
            if not auth_disabled() or requested_owner_key:
                return False
            grant_owner_key = stored_owner_key
        else:
            grant_owner_key = requested_owner_key

        existing = db.query(ChatSessionApprovalGrant).filter(
            ChatSessionApprovalGrant.session_id == session_key,
            ChatSessionApprovalGrant.owner == grant_owner_key,
            ChatSessionApprovalGrant.approval_id == approval_key,
            ChatSessionApprovalGrant.provenance_version == _PROVENANCE_VERSION,
        ).first()
        if existing is not None:
            return True

        db.add(ChatSessionApprovalGrant(
            id=uuid.uuid4().hex,
            session_id=session_key,
            owner=grant_owner_key,
            approval_id=approval_key,
            provenance_version=_PROVENANCE_VERSION,
        ))
        db.commit()
        return True
    except Exception:
        db.rollback()
        logger.warning("Could not persist chat-session approval provenance", exc_info=True)
        return False
    finally:
        db.close()


def has_chat_session_approval_grant(
    session_id: Any,
    owner: Optional[Any],
) -> bool:
    """Return whether the exact owner/session has a server-owned grant."""
    session_key = str(session_id or "")
    owner_key = _owner_key(owner)
    if (
        not session_key
        or (isinstance(owner, str) and is_request_sentinel_owner(owner))
        or (not owner_key and not auth_disabled())
    ):
        return False

    from core.database import ChatSessionApprovalGrant, SessionLocal

    db = SessionLocal()
    try:
        return db.query(ChatSessionApprovalGrant).filter(
            ChatSessionApprovalGrant.session_id == session_key,
            ChatSessionApprovalGrant.owner == owner_key,
            ChatSessionApprovalGrant.provenance_version == _PROVENANCE_VERSION,
        ).first() is not None
    except Exception:
        # Existing installations are upgraded lazily by Base.metadata.create_all
        # at startup. Until that has happened, ignoring the absent table is the
        # safe migration behavior: legacy history can never grant authority.
        logger.debug("Chat-session approval provenance lookup unavailable", exc_info=True)
        return False
    finally:
        db.close()
