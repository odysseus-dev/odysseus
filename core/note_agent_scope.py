"""What an agent run started from a note may change, and how its edits are undone.

A chat created from a note (the note's "Agent: solve this", a checklist item's
agent, "Ask about this note") carries that note's id on its session row,
`sessions.note_scope_id`. It is set once by the server when the session is
created, after checking that the caller owns the note, and every later turn
in that chat reads it back from there. The client never says which note a
turn may change. A per-item run also keeps the checklist item it is about,
`sessions.note_focus_task`, set at the same time: every turn of the chat
shows it with the note, so a follow-up still knows which todo was clicked.

In such a chat, `manage_notes`:

- reads (list, search, view) any note the caller can read, as before;
- changes the scoped note only (update, toggle_item: its title, content,
  tasks, colour, tag, pin, reminder and type), and only from the version the
  run last read, the same `updated_at` rule the note window follows, so a
  run never overwrites what the user saved in the meantime;
- refuses add, delete, archive, and any change to another note.

When the note has collaborators, its text may have been written by someone
else, so each change goes through the tool approval prompt (see
`ToolRunSecurityContext.decision_for`). A private note gets no prompt until
the run reads untrusted content other than that note (a web page, an email,
another tool's output); from then on each change to it asks too.

Before each agent change, every field the run may change (`UNDO_FIELDS`) is
kept on the note as stored (`notes.agent_undo`), with a digest of what the
agent wrote. The owner can
put the earlier bytes back while the note still holds exactly what the agent
wrote. Consecutive edits from the same chat keep the first snapshot, so one
Undo takes the note back to how it was before that chat touched it.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

# manage_notes actions a scoped run may perform on its note. Everything that
# is not a read and not one of these (add, delete, unknown actions) is refused.
SCOPED_WRITE_ACTIONS = frozenset({"update", "toggle_item"})
# manage_notes actions that read one note by id (`view` and the aliases the
# tool maps to it). list, search and find are not among them.
VIEW_ACTIONS = frozenset({"view", "get", "read", "show", "open"})
# `update` fields a scoped run may not set: archiving hides the note, which
# is not an edit Undo should have to find.
SCOPED_REFUSED_UPDATE_FIELDS = frozenset({"archived"})
# Every column a scoped run can change, kept as stored before each agent write
# and put back by Undo.
UNDO_FIELDS = ("title", "content", "items", "color", "label", "pinned", "due_date", "note_type")

SCOPE_OTHER_NOTE_ERROR = (
    "This chat was started from one note and may change only that note (id {note_id}). "
    "Other notes are read-only here; nothing was changed. Tell the user what to change "
    "instead, or ask them to do it."
)
SCOPE_ACTION_ERROR = (
    "This chat was started from one note: it may read any note, and change only that note "
    "(id {note_id}) with update or toggle_item. "
    "{action} is not available here; nothing was changed."
)
SCOPE_FIELDS_ERROR = (
    "This chat may edit its note but not archive it. It cannot change: {fields}. Nothing was saved."
)
SCOPE_UNREAD_ERROR = (
    "Read the note first (action=view, id {note_id}), then send your change based on what "
    "it holds now. Nothing was saved."
)
SCOPE_STALE_ERROR = (
    "The note changed since you last read it (the user may be editing it). Nothing was saved. "
    "Read it again with action=view, id {note_id}, and redo your change on the current text."
)
# Key manage_notes sets on its result when that result is about the chat's own
# note only: a view that loaded it, or a change (or a refusal of one) made to
# it. The value is the id of the note the tool actually loaded, never anything
# the model sent. Every other result (list, search, find, another note, an
# error before a note was loaded) counts as content from outside the note.
OWN_NOTE_RESULT_KEY = "note_scope_own_note"
# One checklist line; generous, since the text is only ever wrapped context.
NOTE_FOCUS_TASK_MAX_CHARS = 2000

SCOPE_SHARED_APPROVAL_REASON = (
    "This note is shared, so its text may have been written by someone else: "
    "each change the agent makes to it needs your approval."
)
SCOPE_TAINTED_APPROVAL_REASON = (
    "The agent has read content from outside this note (a web page, an email or a tool's "
    "output), which may contain instructions: each change it makes to the note now needs "
    "your approval."
)

# The note version each scoped chat last read or wrote, by session id. A turn
# that writes without having read the current version is refused. Kept across
# turns in this process, so an approval answered later, or a follow-up turn,
# still knows what the chat read; a restart only means the run reads again.
_SEEN_BY_SESSION: "OrderedDict[str, str]" = OrderedDict()
_SEEN_BY_SESSION_MAX = 1024

_current_scope: ContextVar[Optional["NoteEditScope"]] = ContextVar("note_edit_scope", default=None)


def note_version(note) -> Optional[str]:
    stamp = getattr(note, "updated_at", None)
    return stamp.isoformat() if stamp else None


def parse_note_focus_task(raw) -> str:
    """The checklist item a note-started run is about, as one line of text.

    It comes from the note, so it is reference context like the note itself:
    it only ever reaches the model inside the selected-notes wrapper, never
    as the user's own turn. Raises ValueError when it is not a short string.
    """
    if raw is None or raw == "":
        return ""
    if not isinstance(raw, str) or len(raw) > NOTE_FOCUS_TASK_MAX_CHARS:
        raise ValueError("Invalid note_focus_task")
    return " ".join(raw.split())


@dataclass
class NoteEditScope:
    """The note one chat may change. `shared`: the note has at least one collaborator.

    `focus_task`: the checklist item the chat was started from, or "".
    """

    session_id: str
    note_id: str
    shared: bool = False
    focus_task: str = ""

    @property
    def seen_version(self) -> Optional[str]:
        return _SEEN_BY_SESSION.get(self.session_id)

    def saw(self, note) -> None:
        """The run has read (or just written) this version of its note."""
        if getattr(note, "id", None) != self.note_id:
            return
        version = note_version(note)
        if version is None:
            return
        _SEEN_BY_SESSION[self.session_id] = version
        _SEEN_BY_SESSION.move_to_end(self.session_id)
        while len(_SEEN_BY_SESSION) > _SEEN_BY_SESSION_MAX:
            _SEEN_BY_SESSION.popitem(last=False)

    def owns(self, note_id: str) -> bool:
        return bool(note_id) and note_id == self.note_id

    def might_target(self, note_ref: str) -> bool:
        """A note reference (full id or the 8-char prefix the tool accepts) that can mean this note.

        Case-insensitive on purpose: manage_notes resolves a prefix with SQL
        LIKE, which ignores ASCII case in SQLite, so `MINE0001` reaches the
        note `mine0001-...`. Folding here is never narrower than the database.
        """
        ref = str(note_ref or "").strip()
        return not ref or self.note_id.casefold().startswith(ref.casefold())

    # --- refusals, before any note is loaded ---

    def refuse_action(self, action: str, args: dict) -> Optional[dict]:
        """An error for an action this chat may not perform, else None.

        For update, a refused field sent empty (`archived: false`, as models
        often fill every field) is dropped from `args` rather than refused.
        """
        if action in SCOPED_WRITE_ACTIONS:
            if action == "update":
                for key in [k for k, v in args.items() if k in SCOPED_REFUSED_UPDATE_FIELDS and v in (None, "", False)]:
                    args.pop(key)
                extra = sorted(key for key in args if key in SCOPED_REFUSED_UPDATE_FIELDS)
                if extra:
                    return {"error": SCOPE_FIELDS_ERROR.format(fields=", ".join(extra)), "exit_code": 1}
            return None
        return {"error": SCOPE_ACTION_ERROR.format(note_id=self.note_id, action=action or "That action"),
                "exit_code": 1}

    # --- refusals, once the target note is known ---

    def refuse_write(self, note) -> Optional[dict]:
        if not self.owns(getattr(note, "id", "")):
            return {"error": SCOPE_OTHER_NOTE_ERROR.format(note_id=self.note_id), "exit_code": 1}
        seen = self.seen_version
        if seen is None:
            return {"error": SCOPE_UNREAD_ERROR.format(note_id=self.note_id), "exit_code": 1}
        if seen != note_version(note):
            return {"error": SCOPE_STALE_ERROR.format(note_id=self.note_id), "exit_code": 1}
        return None


def current_note_edit_scope() -> Optional[NoteEditScope]:
    return _current_scope.get()


def bind_note_edit_scope(scope: Optional[NoteEditScope]):
    """Make `scope` the current one; returns the token for `unbind_note_edit_scope`."""
    return _current_scope.set(scope)


def unbind_note_edit_scope(token) -> None:
    try:
        _current_scope.reset(token)
    except (ValueError, LookupError):
        # Reset from another context (a generator closed elsewhere): the
        # context it was set in is gone with it.
        pass


def owned_note_query(db, owner: Optional[str]):
    """The caller's own notes. Single-user mode has no username: its notes carry no owner."""
    from core.database import Note
    q = db.query(Note)
    return q.filter(Note.owner == owner) if owner else q.filter(Note.owner.is_(None) | (Note.owner == ""))


def load_note_edit_scope(db, session_id: Optional[str], owner: Optional[str]) -> Optional[NoteEditScope]:
    """The scope stored on this chat, or None for an ordinary chat.

    A chat whose note was deleted (or is no longer the caller's) keeps a scope
    on that id, so it can still change nothing else.
    """
    if not session_id:
        return None
    from core.database import Session as DbSession
    row = (
        db.query(DbSession.note_scope_id, DbSession.note_focus_task)
        .filter(DbSession.id == str(session_id))
        .first()
    )
    note_id = row[0] if row else None
    if not note_id:
        return None
    note = owned_note_query(db, owner).filter_by(id=note_id).first()
    shared = bool(note) and bool(shared_note_ids(db, [note_id]))
    return NoteEditScope(session_id=str(session_id), note_id=note_id, shared=shared, focus_task=row[1] or "")


def shared_note_ids(db, note_ids) -> set:
    """The ids among `note_ids` that have at least one collaborator."""
    ids = [i for i in dict.fromkeys(note_ids or ()) if i]
    if not ids:
        return set()
    from core.database import NoteShare
    return {r[0] for r in db.query(NoteShare.note_id).filter(NoteShare.note_id.in_(ids)).distinct()}


def set_session_note_scope(db, session_id: str, note_id: str, focus_task: str = "") -> None:
    from core.database import Session as DbSession
    row = db.query(DbSession).filter(DbSession.id == session_id).first()
    if row is None:
        raise LookupError(session_id)
    row.note_scope_id = note_id
    row.note_focus_task = focus_task or None
    db.commit()


# --- Undo ---

def snapshot(note) -> dict:
    """The note's undoable columns exactly as stored."""
    return {field: getattr(note, field, None) for field in UNDO_FIELDS}


def content_digest(fields: dict) -> str:
    payload = json.dumps([fields.get(field) for field in UNDO_FIELDS], ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_undo(raw: Any) -> Optional[dict]:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("before"), dict) or not data.get("after"):
        return None
    return data


def record_agent_edit(note, before: dict, session_id: str) -> None:
    """Keep `before` on the note for Undo; call after the change is applied, before the commit.

    Consecutive edits from the same chat, with nothing else in between, keep
    the first snapshot.
    """
    previous = parse_undo(getattr(note, "agent_undo", None))
    if (
        previous
        and previous.get("session_id") == session_id
        and previous.get("after") == content_digest(before)
    ):
        before = previous["before"]
    note.agent_undo = json.dumps({
        "before": before,
        "after": content_digest(snapshot(note)),
        "session_id": session_id,
        "at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    }, ensure_ascii=False)


def undo_available(note, undo: Optional[dict] = None) -> bool:
    undo = undo if undo is not None else parse_undo(getattr(note, "agent_undo", None))
    return bool(undo) and undo["after"] == content_digest(snapshot(note))


def agent_edit_view(note) -> Optional[dict]:
    """What the owner's copy of the note says about its last agent edit."""
    undo = parse_undo(getattr(note, "agent_undo", None))
    if not undo:
        return None
    return {"at": undo.get("at"), "session_id": undo.get("session_id"), "undoable": undo_available(note, undo)}
