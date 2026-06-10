"""Unified workspace search across chats, docs, tasks, memory, and integrations."""

from __future__ import annotations

import anyio
import asyncio
import inspect
import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request

from core.middleware import require_admin
from src.auth_helpers import get_current_user

logger = logging.getLogger(__name__)

Result = Dict[str, Any]
Searcher = Callable[[str, int, Optional[str], Request], Any]

TYPE_ORDER = [
    "chat",
    "email",
    "document",
    "note",
    "task",
    "event",
    "memory",
    "contact",
    "research",
]

TYPE_ALIASES = {
    "all": "",
    "chats": "chat",
    "chat": "chat",
    "emails": "email",
    "email": "email",
    "docs": "document",
    "doc": "document",
    "documents": "document",
    "document": "document",
    "notes": "note",
    "note": "note",
    "tasks": "task",
    "task": "task",
    "calendar": "event",
    "cal": "event",
    "events": "event",
    "event": "event",
    "memories": "memory",
    "memory": "memory",
    "contacts": "contact",
    "contact": "contact",
    "research": "research",
}


def setup_unified_search_routes(
    memory_manager=None,
    *,
    memory_vector=None,
    searchers: Optional[Dict[str, Searcher]] = None,
) -> APIRouter:
    router = APIRouter(tags=["unified-search"])

    default_searchers: Dict[str, Searcher] = {
        "chat": _search_chats,
        "email": _search_emails,
        "document": _search_documents,
        "note": _search_notes,
        "task": _search_tasks,
        "event": _search_events,
        "memory": _memory_searcher(memory_manager, memory_vector),
        "contact": _search_contacts,
        "research": _search_research,
    }
    active_searchers = searchers or default_searchers

    @router.get("/api/search/all")
    async def search_all(
        request: Request,
        q: str = Query("", min_length=0),
        limit: int = Query(30, ge=1, le=100),
        types: str = Query(""),
        _admin: None = Depends(require_admin),
    ) -> Dict[str, Any]:
        query = (q or "").strip()
        if not query:
            return {"query": q, "total": 0, "results": [], "grouped": {}}

        selected = _parse_types(types)
        if not selected:
            selected = set(TYPE_ORDER)

        owner = get_current_user(request)
        per_type_limit = min(max(limit, 10), 50)
        tasks = [
            _run_surface(name, active_searchers[name], query, per_type_limit, owner, request)
            for name in TYPE_ORDER
            if name in selected and name in active_searchers
        ]
        surface_results = await asyncio.gather(*tasks)

        merged: List[Result] = []
        for chunk in surface_results:
            merged.extend(chunk)

        ranked = _rank_results(query, merged)[:limit]
        grouped = {name: [] for name in TYPE_ORDER if name in selected}
        for item in ranked:
            grouped.setdefault(item["type"], []).append(item)
        grouped = {name: items for name, items in grouped.items() if items}

        return {
            "query": query,
            "total": len(ranked),
            "results": ranked,
            "grouped": grouped,
            "types": [name for name in TYPE_ORDER if name in selected],
        }

    return router


async def _run_surface(
    name: str,
    searcher: Searcher,
    query: str,
    limit: int,
    owner: Optional[str],
    request: Request,
) -> List[Result]:
    try:
        return await asyncio.wait_for(
            _run_surface_inner(name, searcher, query, limit, owner, request),
            timeout=4.0,
        )
    except asyncio.TimeoutError:
        logger.warning("Unified search surface %s timed out", name)
        return []


async def _run_surface_inner(
    name: str,
    searcher: Searcher,
    query: str,
    limit: int,
    owner: Optional[str],
    request: Request,
) -> List[Result]:
    try:
        if inspect.iscoroutinefunction(searcher):
            value = await searcher(query, limit, owner, request)
        else:
            # Run blocking searchers on anyio's portal-managed worker pool rather
            # than asyncio.to_thread. anyio's workers are daemon threads tied to
            # the running event loop, so they are torn down with the loop and
            # never block interpreter/TestClient shutdown the way the default
            # asyncio executor can.
            value = await anyio.to_thread.run_sync(searcher, query, limit, owner, request)
            if inspect.isawaitable(value):
                value = await value
        return [r for r in (value or []) if r]
    except Exception as exc:
        logger.warning("Unified search surface %s failed: %s", name, exc)
        return []


def _parse_types(types: str) -> set[str]:
    selected: set[str] = set()
    for raw in (types or "").split(","):
        key = raw.strip().lower()
        if not key:
            continue
        mapped = TYPE_ALIASES.get(key)
        if mapped:
            selected.add(mapped)
    return selected


def _database():
    from core.database import (
        CalendarCal,
        CalendarEvent,
        ChatMessage,
        Document,
        Note,
        ScheduledTask,
        Session as DbSession,
        SessionLocal,
    )

    return {
        "CalendarCal": CalendarCal,
        "CalendarEvent": CalendarEvent,
        "ChatMessage": ChatMessage,
        "Document": Document,
        "Note": Note,
        "ScheduledTask": ScheduledTask,
        "DbSession": DbSession,
        "SessionLocal": SessionLocal,
    }


def _or_(*clauses):
    from sqlalchemy import or_

    return or_(*clauses)


def _search_chats(query: str, limit: int, owner: Optional[str], request: Request) -> List[Result]:
    dbm = _database()
    SessionLocal = dbm["SessionLocal"]
    ChatMessage = dbm["ChatMessage"]
    DbSession = dbm["DbSession"]
    db = SessionLocal()
    try:
        q = (
            db.query(ChatMessage, DbSession.name)
            .join(DbSession, ChatMessage.session_id == DbSession.id)
            .filter(
                DbSession.archived == False,  # noqa: E712
                ChatMessage.content.ilike(f"%{query}%"),
                ChatMessage.role.in_(["user", "assistant"]),
            )
        )
        if owner:
            q = q.filter(DbSession.owner == owner)
        rows = q.order_by(ChatMessage.timestamp.desc()).limit(limit).all()
        out = []
        for msg, session_name in rows:
            role = "You" if msg.role == "user" else "AI"
            out.append(_result(
                "chat",
                msg.id,
                session_name or "Chat",
                _snippet(msg.content, query),
                {"session_id": msg.session_id, "message_id": msg.id, "role": msg.role},
                timestamp=msg.timestamp,
                subtitle=role,
            ))
        return out
    finally:
        db.close()


def _search_documents(query: str, limit: int, owner: Optional[str], request: Request) -> List[Result]:
    dbm = _database()
    SessionLocal = dbm["SessionLocal"]
    Document = dbm["Document"]
    DbSession = dbm["DbSession"]
    db = SessionLocal()
    try:
        q = (
            db.query(Document, DbSession.name)
            .outerjoin(DbSession, Document.session_id == DbSession.id)
            .filter(
                Document.is_active == True,  # noqa: E712
                Document.archived == False,  # noqa: E712
                _or_(Document.title.ilike(f"%{query}%"), Document.current_content.ilike(f"%{query}%")),
            )
        )
        if owner is not None:
            q = q.filter(Document.owner == owner)
        rows = q.order_by(Document.updated_at.desc()).limit(limit).all()
        return [
            _result(
                "document",
                doc.id,
                doc.title or "Untitled",
                _snippet(doc.current_content or doc.title or "", query),
                {"document_id": doc.id, "session_id": doc.session_id},
                timestamp=doc.updated_at or doc.created_at,
                subtitle=session_name or (doc.language or "Document"),
            )
            for doc, session_name in rows
        ]
    finally:
        db.close()


def _search_notes(query: str, limit: int, owner: Optional[str], request: Request) -> List[Result]:
    dbm = _database()
    SessionLocal = dbm["SessionLocal"]
    Note = dbm["Note"]
    db = SessionLocal()
    try:
        term = f"%{query}%"
        q = db.query(Note).filter(
            Note.archived == False,  # noqa: E712
            _or_(Note.title.ilike(term), Note.content.ilike(term), Note.items.ilike(term), Note.label.ilike(term)),
        )
        if owner is not None:
            q = q.filter(Note.owner == owner)
        notes = q.order_by(Note.updated_at.desc()).limit(limit).all()
        out = []
        for note in notes:
            text = note.content or _items_text(note.items) or note.title or ""
            out.append(_result(
                "note",
                note.id,
                note.title or "Note",
                _snippet(text, query),
                {"note_id": note.id},
                timestamp=note.updated_at or note.created_at,
                subtitle=note.label or note.note_type or "Note",
            ))
        return out
    finally:
        db.close()


def _search_tasks(query: str, limit: int, owner: Optional[str], request: Request) -> List[Result]:
    dbm = _database()
    SessionLocal = dbm["SessionLocal"]
    ScheduledTask = dbm["ScheduledTask"]
    db = SessionLocal()
    try:
        term = f"%{query}%"
        q = db.query(ScheduledTask).filter(
            _or_(
                ScheduledTask.name.ilike(term),
                ScheduledTask.prompt.ilike(term),
                ScheduledTask.action.ilike(term),
                ScheduledTask.trigger_event.ilike(term),
            )
        )
        if owner is not None:
            q = q.filter(ScheduledTask.owner == owner)
        tasks = q.order_by(ScheduledTask.updated_at.desc()).limit(limit).all()
        out = []
        for task in tasks:
            out.append(_result(
                "task",
                task.id,
                task.name or task.action or "Task",
                _snippet(task.prompt or task.action or task.trigger_event or "", query),
                {"task_id": task.id},
                timestamp=task.updated_at or task.created_at or task.next_run,
                subtitle=task.status or task.task_type or "Task",
            ))
        return out
    finally:
        db.close()


def _search_events(query: str, limit: int, owner: Optional[str], request: Request) -> List[Result]:
    dbm = _database()
    SessionLocal = dbm["SessionLocal"]
    CalendarCal = dbm["CalendarCal"]
    CalendarEvent = dbm["CalendarEvent"]
    db = SessionLocal()
    try:
        term = f"%{query}%"
        q = (
            db.query(CalendarEvent, CalendarCal.name)
            .join(CalendarCal, CalendarEvent.calendar_id == CalendarCal.id)
            .filter(
                CalendarEvent.status != "cancelled",
                _or_(
                    CalendarEvent.summary.ilike(term),
                    CalendarEvent.description.ilike(term),
                    CalendarEvent.location.ilike(term),
                    CalendarCal.name.ilike(term),
                ),
            )
        )
        if owner is not None:
            q = q.filter(CalendarCal.owner == owner)
        rows = q.order_by(CalendarEvent.dtstart.desc()).limit(limit).all()
        out = []
        for event, calendar_name in rows:
            out.append(_result(
                "event",
                event.uid,
                event.summary or "Calendar event",
                _snippet(" ".join([event.description or "", event.location or ""]).strip(), query),
                {
                    "event_uid": event.uid,
                    "calendar_id": event.calendar_id,
                    "date": _date_key(event.dtstart),
                },
                timestamp=event.dtstart,
                subtitle=calendar_name or event.location or "Calendar",
            ))
        return out
    finally:
        db.close()


def _memory_searcher(memory_manager, memory_vector=None) -> Searcher:
    def search(query: str, limit: int, owner: Optional[str], request: Request) -> List[Result]:
        if not memory_manager:
            return []
        memories = memory_manager.load(owner=owner)
        by_id = {str(m.get("id")): m for m in memories if m.get("id")}

        ranked: Dict[str, float] = {}
        if memory_vector is not None and getattr(memory_vector, "healthy", False):
            for item in memory_vector.search(query, k=limit):
                mid = str(item.get("memory_id") or "")
                if mid in by_id:
                    ranked[mid] = max(ranked.get(mid, 0.0), float(item.get("score") or 0.0))

        for mem in memory_manager.get_relevant_memories(query, memories, threshold=0.05, max_items=limit):
            mid = str(mem.get("id") or "")
            ranked[mid] = max(ranked.get(mid, 0.0), _lexical_score(query, mem.get("text", "")))

        rows = sorted(
            (by_id[mid] for mid in ranked if mid in by_id),
            key=lambda m: (ranked.get(str(m.get("id")), 0.0), m.get("timestamp") or 0),
            reverse=True,
        )[:limit]

        return [
            _result(
                "memory",
                mem.get("id"),
                mem.get("category") or "Memory",
                _snippet(mem.get("text", ""), query),
                {"memory_id": mem.get("id"), "session_id": mem.get("session_id")},
                score=ranked.get(str(mem.get("id")), 0.0),
                timestamp=_timestamp_from_epoch(mem.get("timestamp")),
                subtitle=mem.get("source") or "Memory",
            )
            for mem in rows
        ]

    return search


def _search_contacts(query: str, limit: int, owner: Optional[str], request: Request) -> List[Result]:
    from routes.contacts_routes import _fetch_contacts

    q_lower = query.lower()
    contacts = _fetch_contacts()
    out = []
    for contact in contacts:
        emails = contact.get("emails") or []
        phones = contact.get("phones") or []
        haystack = " ".join([contact.get("name", ""), *emails, *phones]).lower()
        if q_lower not in haystack:
            continue
        contact_id = contact.get("uid") or (emails[0] if emails else contact.get("name"))
        out.append(_result(
            "contact",
            contact_id,
            contact.get("name") or (emails[0] if emails else "Contact"),
            ", ".join([*emails, *phones]),
            {"contact_id": contact_id, "emails": emails},
            subtitle="Contact",
        ))
        if len(out) >= limit:
            break
    return out


def _search_emails(query: str, limit: int, owner: Optional[str], request: Request) -> List[Result]:
    from routes.email_helpers import SCHEDULED_DB, _init_scheduled_db

    _init_scheduled_db()
    if not Path(SCHEDULED_DB).exists():
        return []

    aliases = _email_owner_aliases(owner)
    like = f"%{query.lower()}%"
    conn = sqlite3.connect(SCHEDULED_DB)
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" for _ in aliases) or "?"
        rows = conn.execute(
            f"""
            SELECT t.message_id, t.uid, t.folder, t.subject, t.sender, t.tags,
                   t.created_at, s.summary
            FROM email_tags t
            LEFT JOIN email_summaries s ON s.message_id = t.message_id
            WHERE t.owner IN ({placeholders})
              AND (
                lower(coalesce(t.subject, '')) LIKE ?
                OR lower(coalesce(t.sender, '')) LIKE ?
                OR lower(coalesce(t.tags, '')) LIKE ?
                OR lower(coalesce(s.summary, '')) LIKE ?
              )
            ORDER BY t.created_at DESC
            LIMIT ?
            """,
            [*aliases, like, like, like, like, limit],
        ).fetchall()

        out = []
        for row in rows:
            title = row["subject"] or "(no subject)"
            sender = row["sender"] or "Email"
            snippet_text = row["summary"] or row["tags"] or sender
            out.append(_result(
                "email",
                row["message_id"] or row["uid"],
                title,
                _snippet(snippet_text, query),
                {"uid": row["uid"], "folder": row["folder"] or "INBOX", "message_id": row["message_id"]},
                timestamp=_parse_datetime(row["created_at"]),
                subtitle=sender,
            ))
        return out
    finally:
        conn.close()


def _email_owner_aliases(owner: Optional[str]) -> List[str]:
    aliases = {owner or ""}
    if not owner:
        return [""]

    try:
        from core.database import EmailAccount, SessionLocal

        db = SessionLocal()
        try:
            rows = db.query(EmailAccount).filter(EmailAccount.owner == owner).all()
            for row in rows:
                aliases.update([row.owner or "", row.imap_user or "", row.smtp_user or "", row.from_address or ""])
        finally:
            db.close()
    except Exception:
        pass

    if owner:
        return [a for a in aliases if a]
    return [""]


def _search_research(query: str, limit: int, owner: Optional[str], request: Request) -> List[Result]:
    base = Path("data/deep_research")
    if not base.exists():
        return []

    q_lower = query.lower()
    out = []
    for path in base.glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        if owner is not None and data.get("owner") != owner:
            continue
        if data.get("archived"):
            continue

        title = data.get("query") or data.get("title") or "Research"
        text = " ".join([
            title,
            data.get("category") or "",
            data.get("summary") or "",
            data.get("final_answer") or "",
        ])
        if q_lower not in text.lower():
            continue

        out.append(_result(
            "research",
            path.stem,
            title,
            _snippet(text, query),
            {"research_id": path.stem, "session_id": path.stem},
            timestamp=_timestamp_from_epoch(data.get("completed_at") or data.get("started_at")),
            subtitle=f"{len(data.get('sources') or [])} sources" if data.get("sources") else "Research",
        ))
        if len(out) >= limit:
            break

    out.sort(key=lambda r: r.get("timestamp") or "", reverse=True)
    return out


def _result(
    type_: str,
    id_: Any,
    title: str,
    snippet: str,
    source_ref: Dict[str, Any],
    *,
    score: float = 0.0,
    timestamp: Any = None,
    subtitle: str = "",
) -> Result:
    return {
        "type": type_,
        "id": "" if id_ is None else str(id_),
        "title": title or type_.title(),
        "snippet": snippet or "",
        "source_ref": source_ref,
        "score": float(score or 0.0),
        "timestamp": _serialize_timestamp(timestamp),
        "subtitle": subtitle or "",
    }


def _rank_results(query: str, results: List[Result]) -> List[Result]:
    ranked = []
    for result in results:
        text = " ".join([result.get("title", ""), result.get("snippet", ""), result.get("subtitle", "")])
        lexical = _lexical_score(query, text)
        recency = _recency_score(result.get("timestamp"))
        type_bias = 0.05 * (len(TYPE_ORDER) - TYPE_ORDER.index(result["type"])) if result.get("type") in TYPE_ORDER else 0
        score = float(result.get("score") or 0.0) + (2.0 * lexical) + recency + type_bias
        result = dict(result)
        result["score"] = round(score, 4)
        ranked.append(result)
    ranked.sort(key=lambda r: (r.get("score", 0.0), r.get("timestamp") or ""), reverse=True)
    return ranked


def _lexical_score(query: str, text: str) -> float:
    terms = [t.lower() for t in re.findall(r"\b\w+\b", query or "")]
    if not terms:
        return 0.0
    text_lower = (text or "").lower()
    hits = sum(1 for term in terms if term in text_lower)
    score = hits / len(terms)
    if query.lower() in text_lower:
        score += 0.5
    return min(score, 1.5)


def _recency_score(value: Any) -> float:
    dt = _parse_datetime(value)
    if not dt:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age_days = max(0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).days)
    if age_days <= 7:
        return 0.3
    if age_days >= 90:
        return 0.0
    return (90 - age_days) / 90 * 0.3


def _snippet(text: str, query: str, width: int = 180) -> str:
    text = str(text or "").replace("\n", " ").strip()
    if not text:
        return ""
    idx = text.lower().find((query or "").lower())
    if idx < 0:
        return text[:width]
    start = max(0, idx - width // 3)
    end = min(len(text), idx + len(query) + width // 3)
    return ("..." if start else "") + text[start:end] + ("..." if end < len(text) else "")


def _items_text(raw: Any) -> str:
    if not raw:
        return ""
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return str(raw)
    if not isinstance(items, list):
        return str(raw)
    pieces = []
    for item in items:
        if isinstance(item, dict):
            pieces.append(str(item.get("text") or item.get("title") or ""))
        else:
            pieces.append(str(item))
    return " ".join(p for p in pieces if p)


def _serialize_timestamp(value: Any) -> Optional[str]:
    dt = _parse_datetime(value)
    if not dt:
        return None
    if dt.tzinfo is None:
        return dt.isoformat() + "Z"
    return dt.isoformat()


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    return datetime.strptime(raw, fmt)
                except ValueError:
                    continue
    return None


def _timestamp_from_epoch(value: Any) -> Optional[datetime]:
    try:
        if value is None or value == "":
            return None
        return datetime.fromtimestamp(float(value), timezone.utc)
    except Exception:
        return _parse_datetime(value)


def _date_key(value: Any) -> Optional[str]:
    dt = _parse_datetime(value)
    return dt.date().isoformat() if dt else None
