"""A note's earlier versions, so its owner can go back to any of them.

Each version is the note's undoable columns (`UNDO_FIELDS`: title, content,
items, colour, tag, pin, reminder, type) exactly as stored just before a
change, in `note_versions`, written in the same transaction as the change:

- every agent write through manage_notes (update, toggle_item), from a chat
  started from the note or from any other chat;
- a save of the user's (the note window, a ticked task, an added task) when
  the note's latest version is older than `NOTE_VERSION_USER_GAP_SECONDS`,
  was not a save of the user's, or was another person's save, so typing
  keeps one version per pause, and what an agent, a restore or another
  person left is always kept before it is replaced;
- a restore (and the agent Undo), so going back can itself be undone.

A write that leaves those columns as they were keeps nothing. A note keeps
at most `NOTE_VERSIONS_MAX` versions and `NOTE_VERSIONS_MAX_CHARS` characters
of them, the oldest go first, except the newest and, for each person who
changed the note, their latest save and what it left (for the owner, before
they first change it, the note as it was created), so a collaborator saving
over and over cannot push the owner's text out. Its versions go with it when
it is deleted. Only the owner lists, reads and restores
them; a collaborator's edits are versions in the owner's history.
"""

from __future__ import annotations

import difflib
import json
from datetime import timedelta
from typing import Dict, Iterable, List, Optional

from sqlalchemy import func

from core.database import NoteVersion, Session, utcnow_naive
from core.note_agent_scope import UNDO_FIELDS, content_digest, current_note_edit_scope, snapshot
from core.notes_markdown import effective_content, parse_task_lines
from src.constants import (
    NOTE_VERSION_PREVIEW_CHARS,
    NOTE_VERSION_SUMMARY_ITEM_CHARS,
    NOTE_VERSION_USER_GAP_SECONDS,
    NOTE_VERSIONS_MAX,
    NOTE_VERSIONS_MAX_CHARS,
)

SOURCE_AGENT = "agent"
SOURCE_USER = "user"
SOURCE_RESTORE = "restore"


def latest_version(db, note_id: str) -> Optional[NoteVersion]:
    with db.no_autoflush:
        return (db.query(NoteVersion).filter(NoteVersion.note_id == note_id)
                .order_by(NoteVersion.id.desc()).first())


def record_version(db, note, before: dict, source: str, *, author: Optional[str] = None,
                   session_id: Optional[str] = None) -> Optional[NoteVersion]:
    """Keep `before` (a `snapshot()` taken before the change) as a version of `note`.

    Call after the change is applied to `note` and before the commit, so the
    version lands with the change or not at all. Returns the new row, or None
    when nothing is kept (no change to the versioned columns, or a save by
    the same person inside the gap).
    """
    if content_digest(before) == content_digest(snapshot(note)):
        return None
    if source == SOURCE_USER:
        last = latest_version(db, note.id)
        if (last is not None and last.source == SOURCE_USER
                and (last.author or None) == (author or None)
                and utcnow_naive() - last.created_at < timedelta(seconds=NOTE_VERSION_USER_GAP_SECONDS)):
            return None
    fields = {field: before.get(field) for field in UNDO_FIELDS}
    row = NoteVersion(
        note_id=note.id,
        owner=note.owner,
        created_at=utcnow_naive(),
        source=source,
        session_id=session_id or None,
        author=author or None,
        fields=json.dumps(fields, ensure_ascii=False, default=str),
        digest=content_digest(fields),
    )
    db.add(row)
    _prune(db, note, row)
    return row


def _prune(db, note, pending: NoteVersion) -> None:
    """Drop the oldest stored versions until, with `pending`, the note is within the cap and the budget.

    Never dropped: `pending` (the newest), and for each author their newest
    version (the save) and the one after it (the note as that save left it).
    The note as created counts as the owner's until the owner has a save of
    their own. Those can take the note past the cap or the budget, by at
    most two versions per author.
    """
    with db.no_autoflush:
        stored = (db.query(NoteVersion.id, NoteVersion.author, func.length(NoteVersion.fields))
                  .filter(NoteVersion.note_id == note.id).order_by(NoteVersion.id.asc()).all())
    rows = [(vid, author or None, size or 0) for vid, author, size in stored]
    rows.append((None, pending.author or None, len(pending.fields or "")))
    newest = {}
    for index, (_vid, author, _size) in enumerate(rows):
        newest[author] = index
    kept = {len(rows) - 1}
    for index in newest.values():
        kept.update((index, index + 1))
    if (note.owner or None) not in newest:
        kept.add(0)
    count, total = len(rows), sum(size for _vid, _author, size in rows)
    drop = []
    for index, (vid, _author, size) in enumerate(rows):
        if count <= NOTE_VERSIONS_MAX and total <= NOTE_VERSIONS_MAX_CHARS:
            break
        if index in kept:
            continue
        drop.append(vid)
        count -= 1
        total -= size
    if drop:
        db.query(NoteVersion).filter(NoteVersion.id.in_(drop)).delete(synchronize_session=False)


def agent_session_id() -> Optional[str]:
    """The chat an agent write comes from: the note-started chat, else the turn's own session."""
    scope = current_note_edit_scope()
    if scope is not None:
        return scope.session_id
    from src.agent_runtime.authority import active_request_authority
    authority = active_request_authority()
    return (getattr(authority, "session_id", "") or None) if authority is not None else None


def list_versions(db, note_id: str) -> list:
    return (db.query(NoteVersion).filter(NoteVersion.note_id == note_id)
            .order_by(NoteVersion.id.desc()).all())


def get_version(db, note_id: str, version_id: int) -> Optional[NoteVersion]:
    return (db.query(NoteVersion)
            .filter(NoteVersion.note_id == note_id, NoteVersion.id == version_id).first())


def version_fields(row: NoteVersion) -> dict:
    try:
        data = json.loads(row.fields or "{}")
    except (TypeError, ValueError):
        data = {}
    return {field: data.get(field) for field in UNDO_FIELDS} if isinstance(data, dict) else {}


def version_view(row: NoteVersion, note, *, full: bool = False) -> dict:
    """A version as the history shows it; `full` adds the text and every field."""
    fields = version_fields(row)
    text = effective_content(fields.get("content"), fields.get("items"))
    preview = " ".join(text.split())
    if len(preview) > NOTE_VERSION_PREVIEW_CHARS:
        preview = preview[:NOTE_VERSION_PREVIEW_CHARS - 1].rstrip() + "…"
    view = {
        "id": row.id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "source": row.source,
        "session_id": row.session_id,
        "author": row.author,
        "title": fields.get("title") or "",
        "preview": preview,
        "is_current": row.digest == content_digest(snapshot(note)),
    }
    if full:
        view.update(_shown_fields(fields, text))
    return view


def _shown_fields(fields: dict, text: Optional[str] = None) -> dict:
    """The text and the fields a version shows in full."""
    return {
        "title": fields.get("title") or "",
        "content": effective_content(fields.get("content"), fields.get("items")) if text is None else text,
        "note_type": fields.get("note_type"),
        "color": fields.get("color"),
        "label": fields.get("label"),
        "pinned": fields.get("pinned"),
        "due_date": fields.get("due_date"),
    }


def version_after(db, note, row: NoteVersion) -> dict:
    """The note as the change after `row` left it: the next newer version, else the note as it is now.

    The same pair `history_views` describes, so the summary and the diff of
    a row always speak of the same change.
    """
    newer = (db.query(NoteVersion)
             .filter(NoteVersion.note_id == note.id, NoteVersion.id > row.id)
             .order_by(NoteVersion.id.asc()).first())
    fields = version_fields(newer) if newer is not None else {field: getattr(note, field, None) for field in UNDO_FIELDS}
    return _shown_fields(fields)


def _quoted(text: str) -> str:
    text = " ".join((text or "").split())
    if len(text) > NOTE_VERSION_SUMMARY_ITEM_CHARS:
        text = text[:NOTE_VERSION_SUMMARY_ITEM_CHARS - 1].rstrip() + "…"
    return f"\u201c{text}\u201d"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _task(line: str) -> Optional[dict]:
    found = parse_task_lines(line)
    return found[0] if len(found) == 1 else None


def _text_changes(before: str, after: str) -> List[str]:
    """What happened to the text, as short phrases: ticks first, then lines added, removed or edited."""
    old = [line for line in (before or "").splitlines() if line.strip()]
    new = [line for line in (after or "").splitlines() if line.strip()]
    ticked, unticked, added, removed = [], [], [], []
    edited = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace" and i2 - i1 == j2 - j1:
            for a, b in zip(old[i1:i2], new[j1:j2]):
                ta, tb = _task(a), _task(b)
                if ta and tb and ta["text"] == tb["text"] and ta["done"] != tb["done"]:
                    (ticked if tb["done"] else unticked).append(tb["text"])
                else:
                    edited += 1
            continue
        if tag == "replace":
            edited += max(i2 - i1, j2 - j1)
            continue
        added.extend(new[j1:j2])
        removed.extend(old[i1:i2])
    parts = []
    for done, items in (("ticked", ticked), ("unticked", unticked)):
        if len(items) == 1:
            parts.append(f"{done} {_quoted(items[0])}")
        elif items:
            parts.append(f"{done} {_plural(len(items), 'task')}")
    if edited or (added and removed):
        parts.append("edited the text")
    elif added:
        task = _task(added[0]) if len(added) == 1 else None
        parts.append(f"added the task {_quoted(task['text'])}" if task else f"added {_plural(len(added), 'line')}")
    elif removed:
        task = _task(removed[0]) if len(removed) == 1 else None
        parts.append(f"removed the task {_quoted(task['text'])}" if task else f"removed {_plural(len(removed), 'line')}")
    return parts


def describe_change(before: dict, after: dict) -> str:
    """One line saying what turned `before` into `after` (two sets of `UNDO_FIELDS`).

    Lowercase, no subject: the history puts who did it in front ("Agent in
    Trip ticked “book the cabin”"). Computed when the list is read, from the
    stored versions, so nothing extra is stored.
    """
    parts = []
    if (before.get("title") or "").strip() != (after.get("title") or "").strip():
        parts.append("renamed the note")
    old_text = effective_content(before.get("content"), before.get("items"))
    new_text = effective_content(after.get("content"), after.get("items"))
    parts += _text_changes(old_text, new_text)
    if old_text == new_text and before.get("note_type") != after.get("note_type"):
        parts.append("changed the type")
    if (before.get("color") or "") != (after.get("color") or ""):
        parts.append("changed the colour")
    if (before.get("label") or "") != (after.get("label") or ""):
        parts.append("changed the tag")
    old_due, new_due = before.get("due_date") or "", after.get("due_date") or ""
    if old_due != new_due:
        parts.append("set a reminder" if not old_due else "removed the reminder" if not new_due else "changed the reminder")
    if bool(before.get("pinned")) != bool(after.get("pinned")):
        parts.append("pinned the note" if after.get("pinned") else "unpinned the note")
    if not parts and old_text != new_text:
        parts.append("edited the text")
    return ", ".join(parts) or "changed the note"


def _chat_titles(db, session_ids: Iterable[str], user: Optional[str]) -> Dict[str, str]:
    """The names of the chats, among `session_ids`, that still exist and are `user`'s."""
    ids = [i for i in dict.fromkeys(session_ids) if i]
    if not ids:
        return {}
    query = db.query(Session.id, Session.name).filter(Session.id.in_(ids))
    if user is not None:
        query = query.filter(Session.owner == user)
    return {sid: name or "" for sid, name in query.all()}


def history_views(db, note, user: Optional[str]) -> list:
    """The note's versions, newest first, each with what the change after it did.

    A version is the note just before a change; that change took it to the
    next newer version, or, for the newest, to the note as it is now. So each
    row can say what happened ("summary") and, for an agent edit, in which
    chat ("chat_title"; None once the chat is deleted, "chat_exists" False).
    """
    rows = list_versions(db, note.id)
    titles = _chat_titles(db, (r.session_id for r in rows), user)
    after = {field: getattr(note, field, None) for field in UNDO_FIELDS}
    views = []
    for row in rows:
        fields = version_fields(row)
        view = version_view(row, note)
        view["summary"] = describe_change(fields, after)
        view["chat_title"] = titles.get(row.session_id) if row.session_id else None
        view["chat_exists"] = bool(row.session_id) and row.session_id in titles
        views.append(view)
        after = fields
    return views


def version_counts(db, note_ids: Iterable[str]) -> Dict[str, int]:
    """How many versions each of `note_ids` keeps (absent: none), in one query."""
    ids = [i for i in dict.fromkeys(note_ids or ()) if i]
    if not ids:
        return {}
    return dict(db.query(NoteVersion.note_id, func.count(NoteVersion.id))
                .filter(NoteVersion.note_id.in_(ids)).group_by(NoteVersion.note_id).all())


def apply_version(note, row: NoteVersion) -> None:
    """Put the version's columns back on `note` (as stored, legacy `items` included)."""
    for field, value in version_fields(row).items():
        setattr(note, field, value)


def delete_note_versions(db, note_ids: Iterable[str]) -> int:
    ids = [i for i in dict.fromkeys(note_ids or ()) if i]
    if not ids:
        return 0
    return db.query(NoteVersion).filter(NoteVersion.note_id.in_(ids)).delete(synchronize_session=False)
