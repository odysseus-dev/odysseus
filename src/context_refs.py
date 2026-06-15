"""Resolve library context refs into untrusted preface messages.

Documents, research reports, and chat/archive sessions can be attached to the
active chat as sticky reference context.  This module fetches the source text
with owner-scoped reads, truncates oversized refs, and formats them as
untrusted user-role messages for injection into `build_chat_context`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from src.constants import DEEP_RESEARCH_DIR
from src.model_context import estimate_text_tokens
from src.prompt_security import untrusted_context_message
from core.database import SessionLocal, Session as DbSession, Document, ChatMessage as DBChatMessage

logger = logging.getLogger(__name__)

ALLOWED_TYPES = {"document", "research", "session"}
MAX_REFS_PER_MESSAGE = 5
MAX_REF_CHARS = 80_000
TRUNCATED_SUFFIX = "\n[truncated]"


def _validate_ref(ref: Any) -> Dict[str, str]:
    """Normalize and lightly validate a context ref from the wire."""
    if not isinstance(ref, dict):
        raise HTTPException(400, "context_refs entries must be objects")
    rtype = ref.get("type")
    rid = ref.get("id")
    title = ref.get("title") or "Untitled"
    if rtype not in ALLOWED_TYPES:
        raise HTTPException(400, f"Invalid context ref type: {rtype}")
    if not rid or not isinstance(rid, str):
        raise HTTPException(400, "context ref id is required")
    return {"type": rtype, "id": rid, "title": str(title)}


def _is_owner(row_owner: Optional[str], caller: Optional[str]) -> bool:
    """True if `caller` may access a resource owned by `row_owner`.

    A None/null owner means legacy/unowned data; in multi-user mode only the
    authenticated caller matching an explicit owner passes.  In single-user
    mode (caller is None) we allow legacy rows to keep dev/QA instances working.
    """
    if row_owner is None:
        return True
    if caller is None:
        return True
    return row_owner == caller


def _verify_document_owner(doc: Document, owner: Optional[str]) -> None:
    """404-not-403 owner gate for a Document row.

    Mirrors routes/document_helpers.py so this module stays self-contained.
    """
    doc_owner = getattr(doc, "owner", None)
    if doc_owner is not None:
        if not _is_owner(doc_owner, owner):
            raise HTTPException(404, "Document not found")
        return
    # Legacy fallback: derive ownership from the linked session.
    session_id = getattr(doc, "session_id", None)
    if not session_id:
        raise HTTPException(404, "Document not found")
    db = SessionLocal()
    try:
        row = db.query(DbSession).filter(DbSession.id == session_id).first()
    finally:
        db.close()
    if row is None or not _is_owner(getattr(row, "owner", None), owner):
        raise HTTPException(404, "Document not found")


def _verify_session_owner(session_id: str, owner: Optional[str]) -> None:
    """404-not-403 owner gate for a chat session."""
    db = SessionLocal()
    try:
        row = db.query(DbSession).filter(DbSession.id == session_id).first()
    finally:
        db.close()
    if row is None:
        raise HTTPException(404, f"Session {session_id} not found")
    if not _is_owner(getattr(row, "owner", None), owner):
        raise HTTPException(404, f"Session {session_id} not found")


def _verify_research_owner(session_id: str, owner: Optional[str]) -> None:
    """404-not-403 owner gate for an on-disk research report."""
    path = Path(DEEP_RESEARCH_DIR) / f"{session_id}.json"
    if not path.exists():
        raise HTTPException(404, "Research not found")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(404, "Research not found")
    research_owner = data.get("owner")
    if not _is_owner(research_owner, owner):
        raise HTTPException(404, "Research not found")


def _truncate_content(text: str, max_chars: int = MAX_REF_CHARS) -> str:
    """Truncate a ref body and append a visible suffix."""
    if text is None:
        return ""
    text = str(text)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + TRUNCATED_SUFFIX


def _flatten_message_content(content: Any) -> str:
    """Flatten a message content value to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            item.get("text", "") for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    return ""


def _format_session_transcript(session_id: str) -> str:
    """Render a session transcript as User/Assistant text, skipping tool roles."""
    db = SessionLocal()
    try:
        rows = (
            db.query(DBChatMessage)
            .filter(DBChatMessage.session_id == session_id)
            .order_by(DBChatMessage.timestamp.asc())
            .all()
        )
    finally:
        db.close()
    lines = []
    for row in rows:
        role = row.role
        if role not in ("user", "assistant"):
            continue
        body = _flatten_message_content(row.content).strip()
        if not body:
            continue
        # Strip reasoning tags from context refs (same as _copyChatById).
        body = _strip_thinking(body)
        label = "User" if role == "user" else "Assistant"
        lines.append(f"{label}: {body}")
    return "\n\n".join(lines)


def _strip_thinking(text: str) -> str:
    """Remove <think>...</think> blocks and unclosed trailing thinking tags."""
    import re
    text = re.sub(r"<think>[\s\S]*?</think>", "", text)
    text = re.sub(r"<think>[\s\S]*$", "", text)
    return text


def resolve_ref(ref: Dict[str, str], owner: Optional[str]) -> Dict[str, str]:
    """Fetch and format a single context ref.

    Returns {"label": "library ...: title", "content": "..."}.
    Raises HTTPException(404) on missing or cross-owner resources.
    """
    rtype = ref["type"]
    rid = ref["id"]
    title = ref.get("title") or "Untitled"

    if rtype == "document":
        db = SessionLocal()
        try:
            doc = db.query(Document).filter(Document.id == rid).first()
            if not doc:
                raise HTTPException(404, "Document not found")
            _verify_document_owner(doc, owner)
            label = f"library document: {doc.title or title}"
            content = doc.current_content or ""
        finally:
            db.close()

    elif rtype == "research":
        _verify_research_owner(rid, owner)
        path = Path(DEEP_RESEARCH_DIR) / f"{rid}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Could not read research JSON for context ref %s: %s", rid, e)
            raise HTTPException(404, "Research not found")
        query = data.get("query") or title
        label = f"library research: {query}"
        parts = []
        result = data.get("result") or ""
        if result:
            parts.append(result)
        sources = data.get("sources") or []
        if sources:
            parts.append("\nSources:\n" + "\n".join(str(s) for s in sources))
        content = "\n\n".join(parts)

    elif rtype == "session":
        _verify_session_owner(rid, owner)
        db = SessionLocal()
        try:
            row = db.query(DbSession).filter(DbSession.id == rid).first()
            name = row.name if row else title
        finally:
            db.close()
        label = f"library chat transcript: {name}"
        content = _format_session_transcript(rid)

    else:
        raise HTTPException(400, f"Invalid context ref type: {rtype}")

    return {"label": label, "content": _truncate_content(content)}


def estimate_ref_tokens(ref: Dict[str, str], owner: Optional[str]) -> int:
    """Return a token estimate for a ref, resolving it when necessary.

    Preflight needs an accurate count, so we resolve the ref fully rather than
    guessing from the title.  The resolve itself is owner-gated and cached by
    the caller when checking a list of refs.
    """
    try:
        resolved = resolve_ref(ref, owner)
    except HTTPException:
        raise
    text = resolved.get("content", "")
    return estimate_text_tokens(text)


def build_context_messages(refs: List[Dict[str, Any]], owner: Optional[str]) -> List[Dict[str, Any]]:
    """Resolve a list of refs into untrusted context messages.

    Invalid refs are skipped and logged so one bad ref does not break the send.
    """
    messages = []
    seen = set()
    for raw in refs:
        try:
            ref = _validate_ref(raw)
        except HTTPException:
            logger.warning("Skipping invalid context ref: %s", raw)
            continue
        key = (ref["type"], ref["id"])
        if key in seen:
            continue
        seen.add(key)
        try:
            resolved = resolve_ref(ref, owner)
        except HTTPException as e:
            logger.warning(
                "Skipping context ref %s/%s: %s", ref["type"], ref["id"], e.detail
            )
            continue
        messages.append(
            untrusted_context_message(resolved["label"], resolved["content"])
        )
    return messages


def validate_refs(raw_refs: Any) -> List[Dict[str, str]]:
    """Validate the wire shape of a context_refs list.

    Raises HTTPException(400) on structural problems.  Owner checks are
    deferred to resolve time.
    """
    if raw_refs is None:
        return []
    if isinstance(raw_refs, str):
        try:
            raw_refs = json.loads(raw_refs)
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"Invalid context_refs JSON: {e}")
    if not isinstance(raw_refs, list):
        raise HTTPException(400, "context_refs must be a list")
    if len(raw_refs) > MAX_REFS_PER_MESSAGE:
        raise HTTPException(400, f"At most {MAX_REFS_PER_MESSAGE} context refs allowed")
    return [_validate_ref(r) for r in raw_refs]
