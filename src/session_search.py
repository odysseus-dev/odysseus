"""Shared session transcript search for UI and agent tools."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import text

from core.database import ChatMessage as DBChatMessage
from core.database import Session as DBSession
from core.database import SessionLocal

logger = logging.getLogger(__name__)

SEARCH_ROLES = ("user", "assistant")


@dataclass(frozen=True)
class SessionSearchResult:
    message_id: str
    session_id: str
    session_name: str
    role: str
    content: str
    content_snippet: str
    timestamp: str | None
    context_before: list[dict[str, Any]]
    context_after: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "session_id": self.session_id,
            "session_name": self.session_name,
            "role": self.role,
            "content_snippet": self.content_snippet,
            "timestamp": self.timestamp,
            "context_before": self.context_before,
            "context_after": self.context_after,
        }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _message_to_context(msg: DBChatMessage) -> dict[str, Any]:
    return {
        "message_id": msg.id,
        "role": msg.role,
        "content": msg.content or "",
        "timestamp": _iso(msg.timestamp),
    }


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _snippet(content: str, query: str, radius: int = 60) -> str:
    content = content or ""
    query = query or ""
    if not query:
        return content[: radius * 2]

    idx = content.lower().find(query.lower())
    if idx == -1:
        return content[: radius * 2]

    start = max(0, idx - radius)
    end = min(len(content), idx + len(query) + radius)
    return ("..." if start > 0 else "") + content[start:end] + ("..." if end < len(content) else "")


def _sanitize_fts_query(query: str) -> str | None:
    """Convert free text into a conservative FTS5 MATCH query.

    User input can contain FTS5 operators or punctuation that raises
    sqlite3.OperationalError. For transcript search we do not need advanced
    syntax in v1, so keep only words and balanced quoted phrases.
    """
    parts: list[str] = []
    for match in re.finditer(r'"([^"]+)"|[\w][\w._-]*', query, flags=re.UNICODE):
        phrase = match.group(1)
        if phrase is not None:
            phrase = phrase.strip()
            if phrase:
                parts.append('"' + phrase.replace('"', '""') + '"')
            continue

        token = match.group(0).strip("._-")
        if not token:
            continue
        if any(ch in token for ch in "._-"):
            parts.append('"' + token.replace('"', '""') + '"')
        else:
            parts.append(token)

    if not parts:
        return None
    return " ".join(parts)


def _is_sqlite_session(db) -> bool:
    try:
        bind = db.get_bind()
        return getattr(getattr(bind, "dialect", None), "name", None) == "sqlite"
    except Exception:
        return False


def _has_fts_table(db) -> bool:
    if not _is_sqlite_session(db):
        return False
    try:
        row = db.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_messages_fts' LIMIT 1")
        ).first()
        return row is not None
    except Exception as e:
        logger.debug("chat_messages_fts availability check failed: %s", e)
        return False


def _owner_filter(query, owner: str | None, include_legacy_owner: bool):
    if owner is None:
        return query.filter(DBSession.owner.is_(None))
    if not include_legacy_owner:
        return query.filter(DBSession.owner == owner)
    return query.filter((DBSession.owner == owner) | (DBSession.owner.is_(None)))


def _context_for_message(db, msg: DBChatMessage, count: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if count <= 0 or not msg.timestamp:
        return [], []

    before_rows = (
        db.query(DBChatMessage)
        .filter(
            DBChatMessage.session_id == msg.session_id,
            DBChatMessage.role.in_(SEARCH_ROLES),
            DBChatMessage.timestamp < msg.timestamp,
        )
        .order_by(DBChatMessage.timestamp.desc())
        .limit(count)
        .all()
    )
    after_rows = (
        db.query(DBChatMessage)
        .filter(
            DBChatMessage.session_id == msg.session_id,
            DBChatMessage.role.in_(SEARCH_ROLES),
            DBChatMessage.timestamp > msg.timestamp,
        )
        .order_by(DBChatMessage.timestamp.asc())
        .limit(count)
        .all()
    )
    before = [_message_to_context(row) for row in reversed(before_rows)]
    after = [_message_to_context(row) for row in after_rows]
    return before, after


def _rows_to_results(db, rows: Iterable[tuple[DBChatMessage, str, str]], query: str, context_messages: int) -> list[SessionSearchResult]:
    results: list[SessionSearchResult] = []
    for msg, session_name, snippet in rows:
        before, after = _context_for_message(db, msg, context_messages)
        content = msg.content or ""
        results.append(
            SessionSearchResult(
                message_id=msg.id,
                session_id=msg.session_id,
                session_name=session_name or "Untitled",
                role=msg.role,
                content=content,
                content_snippet=snippet or _snippet(content, query),
                timestamp=_iso(msg.timestamp),
                context_before=before,
                context_after=after,
            )
        )
    return results


def _search_like(
    db,
    query: str,
    limit: int,
    owner: str | None,
    include_archived: bool,
    context_messages: int,
    restrict_owner: bool,
    include_legacy_owner: bool,
) -> list[SessionSearchResult]:
    safe_q = _escape_like(query)
    q = (
        db.query(DBChatMessage, DBSession.name)
        .join(DBSession, DBChatMessage.session_id == DBSession.id)
        .filter(
            DBChatMessage.content.ilike(f"%{safe_q}%", escape="\\"),
            DBChatMessage.role.in_(SEARCH_ROLES),
        )
    )
    if not include_archived:
        q = q.filter(DBSession.archived == False)
    if restrict_owner:
        q = _owner_filter(q, owner, include_legacy_owner)
    rows = q.order_by(DBChatMessage.timestamp.desc()).limit(limit).all()
    shaped = ((msg, session_name, _snippet(msg.content or "", query)) for msg, session_name in rows)
    return _rows_to_results(db, shaped, query, context_messages)


def _fetch_messages_by_id(db, message_ids):
    """Fetch (message, session_name) for many message ids in a single query.

    The FTS search returns a list of hit ids; fetching each row on its own was an
    N+1 query (one SELECT per hit). Batch them with one IN(...) query and return
    a lookup so the caller can reassemble results in hit (relevance) order.
    """
    if not message_ids:
        return {}
    rows = (
        db.query(DBChatMessage, DBSession.name)
        .join(DBSession, DBChatMessage.session_id == DBSession.id)
        .filter(DBChatMessage.id.in_(message_ids))
        .all()
    )
    return {msg.id: (msg, session_name) for msg, session_name in rows}


def _search_fts(
    db,
    query: str,
    limit: int,
    owner: str | None,
    include_archived: bool,
    context_messages: int,
    restrict_owner: bool,
    include_legacy_owner: bool,
) -> list[SessionSearchResult] | None:
    fts_query = _sanitize_fts_query(query)
    if not fts_query or not _has_fts_table(db):
        return None

    archived_clause = "" if include_archived else "AND s.archived = 0"
    if not restrict_owner:
        owner_clause = ""
    elif owner is None:
        owner_clause = "AND s.owner IS NULL"
    elif not include_legacy_owner:
        owner_clause = "AND s.owner = :owner"
    else:
        owner_clause = "AND (s.owner = :owner OR s.owner IS NULL)"
    params: dict[str, Any] = {"fts_query": fts_query, "limit": limit}
    if restrict_owner and owner is not None:
        params["owner"] = owner

    sql = text(
        f"""
        SELECT
            m.id AS message_id,
            snippet(chat_messages_fts, 0, '', '', '...', 24) AS content_snippet
        FROM chat_messages_fts
        JOIN chat_messages m ON m.id = chat_messages_fts.message_id
        JOIN sessions s ON s.id = m.session_id
        WHERE chat_messages_fts MATCH :fts_query
          {archived_clause}
          {owner_clause}
          AND m.role IN ('user', 'assistant')
        ORDER BY bm25(chat_messages_fts), m.timestamp DESC
        LIMIT :limit
        """
    )

    try:
        hits = db.execute(sql, params).fetchall()
    except Exception as e:
        logger.debug("FTS session search failed; falling back to LIKE: %s", e)
        return None

    if not hits:
        return None

    by_id = _fetch_messages_by_id(db, [hit[0] for hit in hits])
    rows = []
    for hit in hits:
        found = by_id.get(hit[0])
        if found:
            msg, session_name = found
            rows.append((msg, session_name, hit[1] or ""))
    return _rows_to_results(db, rows, query, context_messages)


def search_session_messages(
    query: str,
    limit: int = 20,
    owner: str | None = None,
    include_archived: bool = False,
    context_messages: int = 1,
    restrict_owner: bool = True,
    include_legacy_owner: bool = True,
    db=None,
) -> list[SessionSearchResult]:
    """Search session transcripts using FTS5 when available.

    `owner=None` is deliberately treated as legacy/null-owner scope rather
    than global access.
    """
    query = (query or "").strip()
    if not query:
        return []

    limit = max(1, min(int(limit or 20), 100))
    context_messages = max(0, min(int(context_messages or 0), 3))

    owns_db = db is None
    if owns_db:
        db = SessionLocal()
    try:
        fts_results = _search_fts(
            db,
            query,
            limit,
            owner,
            include_archived,
            context_messages,
            restrict_owner,
            include_legacy_owner,
        )
        if fts_results is not None:
            like_results = _search_like(
                db,
                query,
                limit,
                owner,
                include_archived,
                context_messages,
                restrict_owner,
                include_legacy_owner,
            )
            merged: list[SessionSearchResult] = []
            seen: set[str] = set()
            for result in [*fts_results, *like_results]:
                if result.message_id in seen:
                    continue
                seen.add(result.message_id)
                merged.append(result)
                if len(merged) >= limit:
                    break
            return merged
        return _search_like(
            db,
            query,
            limit,
            owner,
            include_archived,
            context_messages,
            restrict_owner,
            include_legacy_owner,
        )
    finally:
        if owns_db:
            db.close()
