"""Database guards for read-modify-write operations on a note body."""

import hashlib
import json

from sqlalchemy import update
from sqlalchemy.exc import OperationalError

from core.database import Note


_BODY_FIELDS = (Note.owner, Note.content, Note.items, Note.image_url, Note.note_type)
NOTE_BODY_CONFLICT = "Note changed. Reload it and retry."


def note_body_revision(note: Note) -> str:
    values = [note.id, *(getattr(note, field.key) for field in _BODY_FIELDS)]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def guard_note_body(db, note: Note) -> bool:
    """Acquire the row for writing only if the body still matches the read.

    Call before modifying the ORM object. Keep the transaction open through
    the real write, or roll it back before a retry. Metadata-only writers
    update their own columns independently of this body guard.
    """
    stmt = (
        update(Note)
        .where(Note.id == note.id, *(field == getattr(note, field.key) for field in _BODY_FIELDS))
        .values(content=Note.content, updated_at=Note.updated_at)
        .execution_options(synchronize_session=False)
    )
    try:
        return db.execute(stmt).rowcount == 1
    except OperationalError as exc:
        # A SQLite read transaction cannot upgrade a stale WAL snapshot.
        if getattr(exc.orig, "sqlite_errorname", None) == "SQLITE_BUSY_SNAPSHOT":
            return False
        raise
