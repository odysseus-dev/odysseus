"""Guarded writes to one note row.

A route that reads a note, changes it and commits races every other writer
to the same row: two requests that read the same version both commit, and
the first one's change is lost. Two tokens guard against that:

- `updated_at`: `commit_if_unchanged` turns the commit into
  `UPDATE notes SET … WHERE id = ? AND updated_at = ?`, so a write only lands
  on the version it was computed from. A client sends it as `base_updated_at`.
- the body revision: `note_body_revision` hashes the body fields (content,
  legacy items and image, type), so a client editing the body is not refused
  for a pin or a colour change made meanwhile. A client sends it as
  `body_revision`; `guard_note_body` checks the row still holds the body the
  write was computed from, right before the compare-and-swap commit.

The caller decides what to do when a write does not land: answer 409, or
read the row again and re-apply.
"""

import hashlib
import json
from typing import Any, Callable, Optional, Sequence

from sqlalchemy import DateTime, String, bindparam, inspect, text, update
from sqlalchemy.exc import OperationalError

from core.database import Note, utcnow_naive
from src.constants import NOTE_CONTENT_MAX_CHARS, NOTE_FIELD_MAX_CHARS, NOTE_LABEL_MAX_CHARS, NOTE_TITLE_MAX_CHARS

# How many times a write that is a pure function of the current row (a task
# toggle, an append, a write with no base) is re-applied after losing a race.
NOTE_WRITE_ATTEMPTS = 5

# Every string column a write can set, with the name the refusal uses for it.
# The short ones share NOTE_FIELD_MAX_CHARS.
_SIZED_FIELDS = (
    ("title", "title"),
    ("content", "text"),
    ("label", "tags"),
    ("color", "color"),
    ("due_date", "reminder"),
    ("repeat", "repeat rule"),
    ("note_type", "type"),
    ("source", "source"),
    ("session_id", "session id"),
    ("gallery_id", "gallery id"),
    ("agent_session_id", "agent session id"),
)


def _field_limit(field: str) -> int:
    # Read at call time, so a test can lower a limit on this module.
    if field == "title":
        return NOTE_TITLE_MAX_CHARS
    if field == "content":
        return NOTE_CONTENT_MAX_CHARS
    if field == "label":
        return NOTE_LABEL_MAX_CHARS
    return NOTE_FIELD_MAX_CHARS


def _length(value: Any) -> int:
    if value is None:
        return 0
    return len(value if isinstance(value, str) else str(value))


def size_refusal(fields: dict, before: Optional[dict] = None) -> Optional[dict]:
    """Why a note with these column values can't be stored, or None.

    `fields` holds the note's string columns as the write would leave them;
    the ones it doesn't name are not checked. A field is refused when it is
    past its limit (`NOTE_TITLE_MAX_CHARS`, `NOTE_CONTENT_MAX_CHARS`,
    `NOTE_LABEL_MAX_CHARS`, `NOTE_FIELD_MAX_CHARS`) and longer than it was in
    `before`, so a note stored before the limits existed can still be edited
    down.
    """
    before = before or {}
    for field, name in _SIZED_FIELDS:
        if field not in fields:
            continue
        length = _length(fields[field])
        limit = _field_limit(field)
        if length > limit and length > _length(before.get(field)):
            return {
                "message": f"The note's {name} is too long to save: {length:,} characters, "
                           f"the limit is {limit:,}.",
                "field": field,
                "length": length,
                "limit": limit,
            }
    return None


def sized_fields(note) -> dict:
    """A note row's string columns, as size_refusal checks them."""
    return {field: getattr(note, field, None) for field, _name in _SIZED_FIELDS}


# The columns a body write reads and rewrites. Metadata (title, colour, pin,
# order, reminder) is not part of the body revision.
_BODY_FIELDS = (Note.owner, Note.content, Note.items, Note.image_url, Note.note_type)
NOTE_BODY_CONFLICT = "Note changed. Reload it and retry."


def body_snapshot(note: Note) -> tuple:
    """The note's body fields as loaded, for `guard_note_body(expected=...)`."""
    return tuple(getattr(note, field.key) for field in _BODY_FIELDS)


def note_body_revision(note: Note) -> str:
    values = [note.id, *body_snapshot(note)]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def guard_note_body(db, note: Note, expected: Optional[Sequence] = None) -> bool:
    """Acquire the row for writing only if the body still matches the read.

    `expected` is `body_snapshot(note)` taken when the note was read; without
    it the note's current attribute values are used, which is only right
    before the ORM object is modified. Call it right before the commit (the
    compare-and-swap in `commit_if_unchanged`): it takes SQLite's write lock,
    so calling it earlier blocks every other writer for the rest of the
    request. Returns False when the body moved on; the caller rolls back.
    Metadata-only writers update their own columns independently of this guard.
    """
    values = body_snapshot(note) if expected is None else tuple(expected)
    stmt = (
        update(Note)
        .where(Note.id == note.id, *(field == value for field, value in zip(_BODY_FIELDS, values)))
        .values(content=Note.content, updated_at=Note.updated_at)
        .execution_options(synchronize_session=False)
    )
    try:
        # The note's pending changes must not be flushed by this probe: they
        # are written by the compare-and-swap UPDATE, or not at all.
        with db.no_autoflush:
            return db.execute(stmt).rowcount == 1
    except OperationalError as exc:
        # A SQLite read transaction cannot upgrade a stale WAL snapshot.
        if getattr(exc.orig, "sqlite_errorname", None) == "SQLITE_BUSY_SNAPSHOT":
            return False
        raise


class NoteChanged(Exception):
    """Another write landed between this request's read and its commit."""


def stored_stamp(db, note_id: str) -> Any:
    """The row's `updated_at` exactly as the database holds it.

    Not the ORM value: SQLite keeps whatever text the writer stored (raw SQL
    migrations and older code may use another format than SQLAlchemy binds),
    and comparing the parsed value back would never match such a row.
    """
    return db.execute(text("SELECT updated_at FROM notes WHERE id = :id"), {"id": note_id}).scalar()


def commit_if_unchanged(db, note: Note, stamp: Any) -> None:
    """Write `note`'s pending column changes only if its row still carries `stamp`.

    Raises NoteChanged (after a rollback) when the row moved on. Without
    pending changes this is a plain commit.
    """
    state = inspect(note)
    changes = {}
    for attr in state.mapper.column_attrs:
        if state.attrs[attr.key].history.has_changes():
            changes[attr.columns[0].key] = getattr(note, attr.key)
    if not changes:
        db.commit()
        return
    table = Note.__table__
    note_id = note.id
    changes["updated_at"] = utcnow_naive()
    if stamp is None:
        # A row a raw SQL writer left without a timestamp.
        same_version = table.c.updated_at.is_(None)
    else:
        stamp_type = String() if isinstance(stamp, str) else DateTime()
        same_version = table.c.updated_at == bindparam("expected_stamp", stamp, type_=stamp_type)
    stmt = update(table).where(table.c.id == note_id).where(same_version).values(**changes)
    # Drop the ORM's own pending UPDATE, which would write unconditionally.
    db.expire(note)
    if db.execute(stmt).rowcount != 1:
        db.rollback()
        raise NoteChanged(note_id)
    db.commit()


def write_with_retry(db, note_id: str, load: Callable[[], Note], apply: Callable[[Note], Optional[Any]],
                     attempts: int = NOTE_WRITE_ATTEMPTS,
                     guard: Optional[Callable[[Any, Note, tuple], bool]] = None):
    """Load, apply and commit-if-unchanged, re-reading and re-applying on a lost race.

    `apply(note)` changes the loaded note and returns a value for the caller;
    it must be safe to run again on a fresher row. `guard(db, note, expected)`
    (normally `guard_note_body`) runs right before the commit with the body
    as loaded; False counts as a lost race. Raises NoteChanged when every
    attempt lost.
    """
    for _ in range(attempts):
        # The stamp before the row: a write landing in between fails the
        # compare below instead of being silently overwritten.
        stamp = stored_stamp(db, note_id)
        note = load()
        expected = body_snapshot(note)
        result = apply(note)
        if guard is not None and not guard(db, note, expected):
            db.rollback()
            db.expire_all()
            continue
        try:
            commit_if_unchanged(db, note, stamp)
        except NoteChanged:
            db.expire_all()
            continue
        db.refresh(note)
        return note, result
    raise NoteChanged()
