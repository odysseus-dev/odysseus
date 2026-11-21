"""Per-note sharing: who may do what to a note.

A note has one owner (`Note.owner`) and any number of collaborators
(`NoteShare` rows). `note_access()` is the one place that turns that into a
set of capabilities, and both the REST routes and the `manage_notes` tool ask
it before touching a note:

- the owner gets `read`, `write`, `manage` and `share`;
- an `edit` collaborator gets `read` and `write` (content, title and tasks);
- a `view` collaborator gets `read`;
- anyone else gets nothing.

Changing who a note is shared with is a human action. Only the REST routes
(`POST /api/notes/{id}/share`, `DELETE /api/notes/{id}/share/{user}`), which
the Notes window calls, can do it. Models cannot: the `manage_notes` tool and
the Codex todo bridge refuse `share`, `unshare` and `leave` with
`SHARING_UI_ONLY_ERROR`. Sharing discloses a private note to another account,
so it should be a deliberate click by the owner, and notes, pages, emails and
documents are untrusted input (THREAT_MODEL.md): a prompt-injected one must
never be able to get a note shared. The tool still reports sharing on `list`
and `get`, so the agent can say who has a note.

One note has one owner. When an admin deletes an account, the notes it owns
go with it (`purge_notes_owned_by`), and collaborators simply lose them.

"No user" is the single-user / auth-disabled mode. `require_user()` only
returns an empty identity when there is no account boundary to enforce, so it
keeps full access there, as every notes route already did. `""` and `None`
both mean that mode; usernames compare lowercased on both sides.
"""

import uuid
from typing import Dict, Iterable, List, Optional

from sqlalchemy import or_

from core.database import Note, NoteShare

ACCESS_ALL = frozenset({"read", "write", "manage", "share"})
ACCESS_EDIT = frozenset({"read", "write"})
ACCESS_VIEW = frozenset({"read"})
PERMISSIONS = ("view", "edit")

# Actions models must not perform; see the module docstring.
AGENT_REFUSED_ACTIONS = frozenset({"share", "unshare", "leave"})
SHARING_UI_ONLY_ERROR = (
    "Sharing is changed from the Notes window: the note's owner shares it, "
    "changes access or removes people there, and a collaborator leaves there. "
    "Nothing was changed."
)
# The answer to a share naming no account. It says nothing else about the
# name, but a non-admin can still learn whether an account exists by trying
# names. Accepted for now.
NO_SUCH_ACCOUNT = "No account with that name"

# Fields only the owner may set. An edit collaborator changes content, title
# and tasks; pinning, archiving, ordering, colour, tag, reminder and the
# agent-session link stay with the owner.
OWNER_ONLY_FIELDS = (
    "pinned",
    "archived",
    "sort_order",
    "agent_session_id",
    "color",
    "label",
    "due_date",
    "repeat",
    "note_type",
    "gallery_id",
)

# Owner-side bookkeeping a collaborator's copy of the note never carries.
_OWNER_PRIVATE_FIELDS = ("session_id", "agent_session_id", "agent_edit", "ai_classification", "ai_content_hash", "version_count")


def normalize_user(user: Optional[str]) -> str:
    """One spelling for "no user" (``""``) and lowercased usernames."""
    return str(user or "").strip().lower()


def normalize_permission(value: Optional[str]) -> Optional[str]:
    perm = str(value or "").strip().lower()
    return perm if perm in PERMISSIONS else None


def is_owner(user: Optional[str], note: Note) -> bool:
    u = normalize_user(user)
    return not u or normalize_user(note.owner) == u


def note_access(user: Optional[str], note: Note, db) -> frozenset:
    """Capabilities `user` has on `note`: a subset of read/write/manage/share."""
    u = normalize_user(user)
    if not u or normalize_user(note.owner) == u:
        return ACCESS_ALL
    share = (
        db.query(NoteShare)
        .filter(NoteShare.note_id == note.id, NoteShare.shared_with == u)
        .first()
    )
    if share is None:
        return frozenset()
    return ACCESS_EDIT if share.permission == "edit" else ACCESS_VIEW


def readable_notes_query(db, user: Optional[str]):
    """Notes the user owns plus notes shared with them."""
    u = normalize_user(user)
    q = db.query(Note)
    if not u:
        return q
    shared_ids = db.query(NoteShare.note_id).filter(NoteShare.shared_with == u)
    return q.filter(or_(Note.owner == u, Note.id.in_(shared_ids)))


def shared_with_me_query(db, user: Optional[str]):
    u = normalize_user(user)
    shared_ids = db.query(NoteShare.note_id).filter(NoteShare.shared_with == u)
    return db.query(Note).filter(Note.id.in_(shared_ids), Note.owner != u)


def shares_by_note(db, note_ids: Iterable[str]) -> Dict[str, List[NoteShare]]:
    ids = [i for i in note_ids if i]
    out: Dict[str, List[NoteShare]] = {}
    if not ids:
        return out
    rows = (
        db.query(NoteShare)
        .filter(NoteShare.note_id.in_(ids))
        .order_by(NoteShare.shared_with.asc())
        .all()
    )
    for row in rows:
        out.setdefault(row.note_id, []).append(row)
    return out


def roster(shares: Iterable[NoteShare]) -> List[Dict[str, str]]:
    return [{"username": s.shared_with, "permission": s.permission} for s in shares]


def sharing_fields(note: Note, user: Optional[str], shares: Optional[List[NoteShare]]) -> Dict:
    """The sharing part of a note payload, as seen by `user`.

    The roster (`shared_with`) goes to the owner only: a collaborator learns
    who shared the note and their own permission, never who else has it.
    """
    shares = shares or []
    u = normalize_user(user)
    owner = is_owner(u, note)
    mine = None if owner else next((s.permission for s in shares if s.shared_with == u), None)
    return {
        "is_owner": owner,
        "shared_by": None if owner else note.owner,
        "my_permission": "owner" if owner else mine,
        "can_edit": owner or mine == "edit",
        "is_shared": bool(shares),
        "shared_with": roster(shares) if owner else [],
    }


def strip_owner_private(payload: Dict) -> Dict:
    """Drop owner-side session and classification links from a collaborator's copy."""
    for key in _OWNER_PRIVATE_FIELDS:
        payload[key] = None
    return payload


def owner_only_fields_in(keys: Iterable[str]) -> List[str]:
    """The owner-only fields a write names, from its keys (a dict or a set).

    Presence decides, not the value: an explicit null is a write too, one
    that clears the field or, for pin and archive, leaves the note in
    neither the active list nor the archive.
    """
    keys = set(keys)
    return [f for f in OWNER_ONLY_FIELDS if f in keys]


def upsert_shares(
    db,
    note: Note,
    users: Iterable[str],
    permission: str,
    valid_users: Optional[Iterable[str]],
) -> List[str]:
    """Grant `permission` to each known user; returns the usernames written.

    Unknown accounts, blanks and the owner are skipped. An existing grant is
    updated in place, so re-sharing flips view/edit instead of duplicating.
    """
    valid = {normalize_user(u) for u in valid_users} if valid_users is not None else None
    owner = normalize_user(note.owner)
    written: List[str] = []
    for raw in users or []:
        name = normalize_user(raw)
        if not name or name == owner or name in written:
            continue
        if valid is not None and name not in valid:
            continue
        row = (
            db.query(NoteShare)
            .filter(NoteShare.note_id == note.id, NoteShare.shared_with == name)
            .first()
        )
        if row is None:
            db.add(NoteShare(id=str(uuid.uuid4()), note_id=note.id, shared_with=name, permission=permission))
        else:
            row.permission = permission
        written.append(name)
    return written


def remove_share(db, note_id: str, username: Optional[str]) -> int:
    name = normalize_user(username)
    if not name:
        return 0
    return (
        db.query(NoteShare)
        .filter(NoteShare.note_id == note_id, NoteShare.shared_with == name)
        .delete(synchronize_session=False)
    )


def delete_note_shares(db, note_id: str) -> int:
    return db.query(NoteShare).filter(NoteShare.note_id == note_id).delete(synchronize_session=False)


def delete_shares_for_user(db, username: Optional[str]) -> int:
    """Drop every grant naming `username` (account deletion)."""
    name = normalize_user(username)
    if not name:
        return 0
    return db.query(NoteShare).filter(NoteShare.shared_with == name).delete(synchronize_session=False)


def purge_notes_owned_by(username: Optional[str], db=None) -> Dict[str, int]:
    """Delete everything notes-side that belongs to a deleted account.

    For admin account deletion, run just before the account is removed so a
    failure keeps the account (and its name) in place: it removes every note
    `username` owns, with their grants and
    their `note_legacy_fields` backup rows (so the markdown-migration rollback
    SQL cannot bring them back), plus every grant naming `username` on other
    people's notes. Collaborators simply stop seeing the deleted notes.

    - Notes with `owner IS NULL` (legacy single-user rows) and other
      accounts' notes are never touched; an empty name deletes nothing.
    - Uploads the notes referenced are not deleted here. They become
      unreferenced, and the admin upload cleanup (`POST /api/upload/cleanup`)
      removes them once they are past its age cutoff.
    - Idempotent: running it again, e.g. when the account removal after it
      failed and the admin retries, finds nothing left and returns zeros.

    With `db`, it runs inside the caller's transaction and the caller
    commits. Without, it opens a session and commits it, all or nothing.
    Returns the number of rows removed per kind.
    """
    name = normalize_user(username)
    if not name:
        return {"notes": 0, "shares": 0, "legacy_rows": 0, "grants": 0}
    if db is None:
        from core.database import get_db_session
        with get_db_session() as own_db:
            return _purge_notes_owned_by(own_db, name)
    return _purge_notes_owned_by(db, name)


def _purge_notes_owned_by(db, name: str) -> Dict[str, int]:
    from sqlalchemy import bindparam, func, inspect, text

    counts = {"notes": 0, "shares": 0, "legacy_rows": 0, "grants": 0}

    # Same comparison as is_owner(): trimmed and lowercased. NULL never matches.
    owned_ids = [
        row[0]
        for row in db.query(Note.id).filter(func.lower(func.trim(Note.owner)) == name).all()
    ]
    if owned_ids:
        # The FK cascade covers grants too; this does not rely on it.
        counts["shares"] = (
            db.query(NoteShare)
            .filter(NoteShare.note_id.in_(owned_ids))
            .delete(synchronize_session=False)
        )
        from core.note_versions import delete_note_versions
        delete_note_versions(db, owned_ids)
        # Raw table written by the boot migrations, not an ORM model, and
        # absent on a database that never ran them.
        if inspect(db.connection()).has_table("note_legacy_fields"):
            stmt = text("DELETE FROM note_legacy_fields WHERE note_id IN :ids").bindparams(
                bindparam("ids", expanding=True)
            )
            counts["legacy_rows"] = db.execute(stmt, {"ids": owned_ids}).rowcount or 0
        counts["notes"] = (
            db.query(Note)
            .filter(Note.id.in_(owned_ids))
            .delete(synchronize_session=False)
        )
    counts["grants"] = delete_shares_for_user(db, name)
    return counts


def rename_shared_user(db, old: Optional[str], new: Optional[str]) -> int:
    """Re-point grants from `old` to `new` inside the caller's transaction.

    If `new` already holds a grant on the same note (a stale row from an
    earlier account with that name), the rows merge into one and keep the
    stronger permission. A grant on a note `new` now owns is dropped.
    """
    old_name, new_name = normalize_user(old), normalize_user(new)
    if not old_name or not new_name or old_name == new_name:
        return 0
    moved = 0
    for row in db.query(NoteShare).filter(NoteShare.shared_with == old_name).all():
        existing = (
            db.query(NoteShare)
            .filter(NoteShare.note_id == row.note_id, NoteShare.shared_with == new_name)
            .first()
        )
        if existing is not None:
            if row.permission == "edit":
                existing.permission = "edit"
            db.delete(row)
        else:
            row.shared_with = new_name
        moved += 1
    db.flush()
    owned_ids = db.query(Note.id).filter(Note.owner == new_name)
    (
        db.query(NoteShare)
        .filter(NoteShare.shared_with == new_name, NoteShare.note_id.in_(owned_ids))
        .delete(synchronize_session=False)
    )
    return moved


def known_usernames(auth_manager) -> Optional[set]:
    """Lowercased account names, or None when there is no auth store."""
    if auth_manager is None:
        return None
    users = getattr(auth_manager, "users", None)
    if not isinstance(users, dict):
        return None
    return {normalize_user(name) for name in users if normalize_user(name)}
