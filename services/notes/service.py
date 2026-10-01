"""Shared notes operations used by GUI routes and Odysseus MCP."""

from __future__ import annotations

import json
from typing import Any

from services.agents.domains import NotesService


def note_to_dict(note: Any) -> dict[str, Any]:
    """Serialize a note row for API and MCP responses."""
    items = None
    if getattr(note, "items", None):
        try:
            items = json.loads(note.items)
        except (json.JSONDecodeError, TypeError):
            items = None
    ai_cls = None
    raw_ai = getattr(note, "ai_classification", None)
    if raw_ai:
        try:
            ai_cls = json.loads(raw_ai)
        except (json.JSONDecodeError, TypeError):
            ai_cls = None
    created_at = getattr(note, "created_at", None)
    updated_at = getattr(note, "updated_at", None)
    return {
        "id": note.id,
        "owner": note.owner,
        "title": note.title,
        "content": note.content,
        "items": items,
        "note_type": note.note_type,
        "color": note.color,
        "label": note.label,
        "pinned": note.pinned,
        "archived": note.archived,
        "due_date": note.due_date,
        "source": note.source,
        "session_id": note.session_id,
        "sort_order": note.sort_order or 0,
        "image_url": note.image_url,
        "repeat": note.repeat or "none",
        "ai_classification": ai_cls,
        "ai_content_hash": getattr(note, "ai_content_hash", None),
        "agent_session_id": getattr(note, "agent_session_id", None),
        "created_at": created_at.isoformat() if created_at else None,
        "updated_at": updated_at.isoformat() if updated_at else None,
    }
