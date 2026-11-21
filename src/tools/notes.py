"""Notes-domain tool implementations.

Extracted from tool_implementations.py as part of slice 1 (#4082/#4071).
Holds the manage_notes tool (notes + checklists CRUD).
``src.tool_implementations`` re-exports these for backward compatibility.
"""
import json
import logging
import re
from collections import OrderedDict
from typing import Dict, Optional

from src.tools._common import _parse_tool_args
from src.tool_utils import get_upload_handler
from src.upload_handler import reserve_upload_references
from core import notes_sharing as sharing
from core.note_agent_scope import OWN_NOTE_RESULT_KEY, current_note_edit_scope, record_agent_edit, snapshot as undo_snapshot
from core.note_versions import SOURCE_AGENT, agent_session_id, delete_note_versions, record_version
from core.note_writes import (
    NOTE_BODY_CONFLICT,
    NoteChanged,
    body_snapshot,
    commit_if_unchanged,
    guard_note_body,
    note_body_revision,
    size_refusal,
    sized_fields,
    stored_stamp,
)
from core.notes_markdown import (
    apply_items_to_content,
    derive_note_type,
    effective_content,
    fold_image_url,
    fold_legacy_columns,
    keep_task_lines,
    merge_items_into_content,
    normalize_note_color,
    parse_task_lines,
    strip_task_lines,
    toggle_task,
)

logger = logging.getLogger(__name__)

# The version of each note the tool last showed or wrote, per caller.
# `checklist_items` replaces the whole task list; when the note moved on since
# the caller last saw it (the user ticked or added a task during a run), the
# list is stale, and writing it would drop those changes.
_SEEN_VERSIONS: "OrderedDict[tuple, str]" = OrderedDict()
_SEEN_VERSIONS_MAX = 512


def _version(note) -> Optional[str]:
    return note.updated_at.isoformat() if getattr(note, "updated_at", None) else None


def _remember_version(owner: Optional[str], note) -> None:
    key = (owner or "", note.id)
    _SEEN_VERSIONS[key] = _version(note)
    _SEEN_VERSIONS.move_to_end(key)
    while len(_SEEN_VERSIONS) > _SEEN_VERSIONS_MAX:
        _SEEN_VERSIONS.popitem(last=False)


def _stale_since_seen(owner: Optional[str], note) -> bool:
    seen = _SEEN_VERSIONS.get((owner or "", note.id))
    return seen is not None and seen != _version(note)


def _seen_current(owner: Optional[str], note) -> bool:
    """Whether the tool last showed or wrote this caller exactly the stored version."""
    seen = _SEEN_VERSIONS.get((owner or "", note.id))
    return seen is not None and seen == _version(note)


def _same_as_stored(note, field: str, value) -> bool:
    """Whether `value` is exactly what the note stores for `field` (same type too,
    so 1 is not True and "" is not null)."""
    stored = getattr(note, field, None)
    if value is None or stored is None:
        return value is None and stored is None
    return type(value) is type(stored) and value == stored


def _search_tokens(value: str) -> list[str]:
    """Normalize lightweight singular/plural variants without fuzzy matching."""
    tokens = []
    for token in re.findall(r"[a-z0-9]+", str(value or "").lower()):
        if token in {"the", "a", "an", "note", "notes", "checklist", "list", "todo", "todos"}:
            continue
        if len(token) > 4 and token.endswith("ies"):
            token = token[:-3] + "y"
        elif len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        tokens.append(token)
    return tokens


async def do_manage_notes(content: str, owner: Optional[str] = None) -> Dict:
    """Handle manage_notes tool calls: CRUD on notes and checklists."""
    import uuid as _uuid
    from core.database import SessionLocal, Note

    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    # Action aliases — match what models actually emit. `create` is the most
    # common alternative to `add`. Hyphenated forms also accepted.
    raw_action = (args.get("action") or "").replace("-", "_").strip().lower()
    action = raw_action
    _NOTE_ACTION_ALIASES = {
        "create": "add",
        "new": "add",
        "save": "add",
        "remind": "add",
        "remove": "delete",
        "get": "view",
        "read": "view",
        "show": "view",
        "open": "view",
    }
    action = _NOTE_ACTION_ALIASES.get(action, action)
    if action in sharing.AGENT_REFUSED_ACTIONS:
        # Sharing is a human action in the Notes window; see core.notes_sharing.
        return {"error": sharing.SHARING_UI_ONLY_ERROR, "exit_code": 1}
    if action in ("add", "update") and args.get("color") is not None:
        try:
            args["color"] = normalize_note_color(args["color"])
        except ValueError as exc:
            return {"error": f"{exc}. Nothing was saved.", "exit_code": 1}
    if action == "add" and any(args.get(key) for key in ("id", "note_id", "noteId")):
        return {
            "error": 'Nothing saved. add creates a new note and cannot take an existing note ID. '
                     'To fill or change that note, retry with action="update", id set to the existing '
                     'note ID, and checklist_items plus note_type="checklist" for a to-do list. '
                     'Do not create another note.',
            "exit_code": 1,
        }
    if action == "remove_item":
        return {
            "error": "To remove a checklist item, use update with id and the complete remaining checklist_items, preserving their done states. No item was changed.",
            "exit_code": 1,
        }
    list_search_query = str(
        args.get("search")
        or args.get("query")
        or args.get("text")
        or args.get("title")
        or args.get("content")
        or ""
    ).strip()
    if action == "list" and list_search_query:
        action = "search"
        args.setdefault("query", list_search_query)
    # A chat started from a note changes that note only; see core.note_agent_scope.
    scope = current_note_edit_scope()
    if scope is not None and action not in ("list", "search", "find", "view"):
        refused = scope.refuse_action(action, args)
        if refused:
            return refused
        if not any(args.get(key) for key in ("id", "note_id", "noteId")):
            # "The note" in this chat is its own note.
            args["id"] = scope.note_id
    from src.agent_runtime.owned_resources import active_owned_operation
    bound = active_owned_operation()
    if bound is not None:
        bound.validate()
    db = SessionLocal()

    def _body_conflict():
        return {"error": NOTE_BODY_CONFLICT, "conflict": True, "exit_code": 1}

    def _norm_note_title(value: str) -> str:
        text = (value or "").strip().lower()
        text = re.sub(r"^\s*reminder\s*:\s*", "", text)
        return re.sub(r"\s+", " ", text)

    def _is_calendar_reminder_note(note) -> bool:
        return getattr(note, "source", None) == "calendar" and getattr(note, "label", None) == "calendar"

    def _note_by_prefix(note_id: str):
        # Resolve only among notes the caller can read (owned or shared with
        # them), so a prefix can neither reach nor reveal anyone else's note.
        # An exact id wins, then the caller's own notes, so a shared note can
        # never shadow one of theirs.
        if not note_id or re.search(r"[\x00-\x1f\x7f-\x9f]", note_id):
            # No id holds a control character, and LIKE stops at a NUL.
            return None
        q = sharing.readable_notes_query(db, owner) if owner else db.query(Note)
        exact = q.filter(Note.id == note_id).first()
        if exact is not None or bound is not None:
            return exact
        q = q.filter(Note.id.startswith(note_id, autoescape=True))
        if owner:
            from sqlalchemy import case
            q = q.order_by(case((Note.owner == owner, 0), else_=1), Note.updated_at.desc())
        return q.first()

    def _not_found(target) -> Dict:
        # One message for "no such id" and "not yours", so the tool is not an
        # existence oracle.
        return {"error": f"Note '{target}' not found", "exit_code": 1}

    def _own(result: Dict, note) -> Dict:
        # Tells the approval gate this answer is about the chat's own note,
        # judged on the note this call loaded (see OWN_NOTE_RESULT_KEY). A call
        # on it that failed once the note was loaded is about it too.
        if scope is not None and scope.owns(getattr(note, "id", "")):
            result[OWN_NOTE_RESULT_KEY] = note.id
        return result

    def _denied(note, need: str) -> Optional[Dict]:
        caps = sharing.note_access(owner, note, db)
        if need in caps:
            return None
        if need == "manage":
            return {"error": f"Only the note's owner ({note.owner}) can do that.", "exit_code": 1}
        return {"error": "This note is shared with you as view only; it cannot be changed.", "exit_code": 1}

    def _share_label(note, grants) -> str:
        if not sharing.is_owner(owner, note):
            mine = next((g.permission for g in grants if g.shared_with == sharing.normalize_user(owner)), "view")
            return f" (shared by {note.owner}, you can {'edit' if mine == 'edit' else 'view'})"
        if grants:
            return " (shared with " + ", ".join(f"{g.shared_with}: {g.permission}" for g in grants) + ")"
        return ""

    def _note_id_arg() -> str:
        return str(args.get("id") or args.get("note_id") or args.get("noteId") or "").strip()

    def _norm_note_text(value) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip())

    def _norm_note_items(value) -> list[dict]:
        if value in (None, ""):
            return []
        raw = value
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return [{"text": _norm_note_text(raw), "done": False}]
        if not isinstance(raw, list):
            return [{"text": _norm_note_text(raw), "done": False}]
        items = []
        for item in raw:
            if isinstance(item, dict):
                text = _norm_note_text(item.get("text") or item.get("label") or item.get("title") or "")
                done = bool(item.get("done") or item.get("checked") or item.get("complete"))
            else:
                text = _norm_note_text(item)
                done = False
            if text:
                items.append({"text": text, "done": done})
        return items

    def _existing_exact_note(
        *,
        title: str,
        content_value,
        items_value,
        note_type: str,
        label,
        due_date,
        color,
        pinned,
    ):
        q = db.query(Note).filter(Note.archived == False)  # noqa: E712
        if owner is not None:
            q = q.filter(Note.owner == owner)
        target_title = _norm_note_title(title)
        # Items are task lines in content, so one comparison covers both.
        target_content = _norm_note_text(
            merge_items_into_content(content_value, _norm_note_items(items_value))
        )
        target_label = label or None
        target_due = due_date or None
        target_color = color or None
        target_pinned = bool(pinned)
        for existing in q.limit(50).all():
            if _norm_note_title(existing.title or "") != target_title:
                continue
            if (existing.note_type or "note") != (note_type or "note"):
                continue
            if (existing.label or None) != target_label:
                continue
            if (existing.due_date or None) != target_due:
                continue
            if (existing.color or None) != target_color:
                continue
            if bool(existing.pinned) != target_pinned:
                continue
            if _norm_note_text(effective_content(existing.content, existing.items, getattr(existing, "image_url", None))) != target_content:
                continue
            return existing
        return None

    def _format_note_list(notes, *, full_content: bool = False) -> str:
        lines = []
        # Single-user mode has no accounts, so nothing is shared.
        grants_by_note = sharing.shares_by_note(db, [n.id for n in notes]) if owner else {}
        for n in notes:
            content = effective_content(n.content, n.items, getattr(n, "image_url", None))
            pin = " [PINNED]" if n.pinned else ""
            typ = " [checklist]" if derive_note_type(n.note_type, content) == "checklist" else ""
            lbl = f" #{n.label}" if n.label else ""
            title = n.title or "(untitled)"
            shared = _share_label(n, grants_by_note.get(n.id, []))
            lines.append(f"- [{n.id}] **{title}**{pin}{typ}{lbl}{shared}")
            # Search/list is a locator operation. Keep the body behind view so
            # a model cannot satisfy an explicit read request without the
            # required second call, and so large checklists do not flood the
            # next model context.
            if not full_content:
                continue
            lines.append(f"  Body revision: {note_body_revision(n)}")
            # The prose first, then every task line with the index that
            # toggle_item takes.
            prose = strip_task_lines(content)
            if prose:
                lines.append(f"  {prose}")
            for task in parse_task_lines(content):
                mark = "x" if task["done"] else " "
                lines.append(f"  [{mark}] {task['index']}: {task['text']}")
        return "\n".join(lines)

    try:
        if action in ("list", "search", "find"):
            show_archived = args.get("archived", False)
            # Shared notes join the active list; the archive is the owner's.
            if show_archived or not owner:
                q = db.query(Note)
                if owner is not None:
                    q = q.filter(Note.owner == owner)
            else:
                q = sharing.readable_notes_query(db, owner)
            label_filter = str(args.get("label") or "").strip()
            if label_filter and label_filter.lower() != "default":
                q = q.filter(Note.label == label_filter)
            q = q.filter(Note.archived == show_archived)
            notes = q.order_by(Note.pinned.desc(), Note.updated_at.desc()).all()
            if bool(args.get("pinned")):
                notes = [n for n in notes if bool(getattr(n, "pinned", False))]
            if bool(args.get("reminders") or args.get("due_only") or args.get("has_due_date")):
                notes = [n for n in notes if bool(getattr(n, "due_date", None))]
            include_calendar_reminders = bool(
                args.get("include_calendar_reminders")
                or str(args.get("source") or "").strip().lower() == "calendar"
                or str(args.get("label") or "").strip().lower() == "calendar-reminders"
            )
            if not include_calendar_reminders:
                notes = [n for n in notes if not _is_calendar_reminder_note(n)]
            if action in ("search", "find"):
                query = str(
                    args.get("query")
                    or args.get("text")
                    or args.get("title")
                    or args.get("content")
                    or ""
                ).strip().lower()
                if query:
                    query_terms = _search_tokens(query)
                    filtered = []
                    for n in notes:
                        haystack = " ".join(
                            str(part or "")
                            for part in (n.title, n.content, n.label, n.items)
                        ).lower()
                        haystack_terms = set(_search_tokens(haystack))
                        if query in haystack or (
                            query_terms and all(term in haystack_terms for term in query_terms)
                        ):
                            filtered.append(n)
                    notes = filtered
            if not notes:
                return {"response": "No notes found.", "exit_code": 0}
            return {"results": _format_note_list(notes), "exit_code": 0}

        elif action == "view":
            note_id = _note_id_arg()
            note = _note_by_prefix(note_id)
            if not note:
                return _not_found(note_id)
            _remember_version(owner, note)
            if scope is not None:
                scope.saw(note)
            return _own({"results": _format_note_list([note], full_content=True),
                         "body_revision": note_body_revision(note), "exit_code": 0}, note)

        elif action == "add":
            # Accept the various field names models emit: `text` is the most
            # common stand-in for "title or body content" when the model
            # treats the note as a single string. If text was supplied and
            # neither title nor content, use it as the title.
            title = (args.get("title") or "").strip()
            content_raw = args.get("content")
            text_raw = args.get("text") or args.get("body")
            if not title and not content_raw and text_raw:
                title = text_raw.strip()
            elif not content_raw and text_raw:
                content_raw = text_raw
            # Accept both `items` (legacy/internal field) and `checklist_items`
            # (the schema-exposed name used by native function calls). Models
            # following the schema emit `checklist_items`; older code paths
            # and direct API callers still use `items`.
            items_raw = args.get("checklist_items")
            if items_raw is None:
                items_raw = args.get("items")
            note_type = args.get("note_type", "checklist" if items_raw else "note")
            if not title and note_type in {"checklist", "todo", "goal"}:
                from src.user_time import now_user_local
                title = f"To-do - {now_user_local().date().isoformat()}"
            if note_type in {"checklist", "todo", "goal"} and not isinstance(items_raw, list):
                return {
                    "error": 'Nothing saved. Checklist creation requires checklist_items as an array of '
                             '{"text":"task including any stated time","done":false}. '
                             'Put each task in its own item, not in title. Use a short title only; '
                             'do not include explanations or timezone calculations. Retry with the structured items. '
                             'Use [] only when the user explicitly requested an empty checklist.',
                    "exit_code": 1,
                }
            if items_raw is not None and (
                not isinstance(items_raw, list)
                or any(not isinstance(item, dict)
                       or not isinstance(item.get("text"), str)
                       or not item["text"].strip()
                       or not isinstance(item.get("done", False), bool)
                       for item in items_raw)
            ):
                return {"error": 'Nothing saved. checklist_items must be an array of objects with '
                                 'nonempty text and an optional boolean done. Retry with corrected items.',
                        "exit_code": 1}
            # Accept natural-language due_date ("tomorrow at 1pm") in
            # addition to ISO. Use the user-tz-aware parser so the LLM's
            # naive times ("today at 9pm") are anchored to the USER's clock,
            # not the server's. Returns ISO with explicit offset so frontend
            # `new Date()` resolves the right absolute moment regardless of
            # where the user is.
            due_raw = args.get("due_date")
            if not due_raw:
                combined_text = " ".join(
                    str(v or "")
                    for v in (title, content_raw, text_raw)
                ).strip()
                lower_combined = combined_text.lower()
                looks_like_reminder = (
                    raw_action in {"remind", "reminder"}
                    or re.search(r"\bremind(?:er)?\b", lower_combined)
                )
                if looks_like_reminder:
                    temporal = re.search(
                        r"\b(?:today|tonight|tomorrow|tmrw|yesterday)\b(?:\s+(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm)?)?"
                        r"|\b\d{1,2}(?::\d{2})?(?:\s*(?:am|pm))?\s+(?:today|tonight|tomorrow|tmrw|yesterday)\b"
                        r"|\bin\s+\d+\s*(?:hour|hr|minute|min|day)s?\b",
                        lower_combined,
                    )
                    if temporal:
                        due_raw = temporal.group(0)
            due_iso = None
            if due_raw:
                try:
                    from routes.calendar_routes import parse_due_for_user as _pdt_user
                    due_iso = _pdt_user(due_raw)
                except Exception:
                    due_iso = due_raw  # fall through; trust the model
            if due_iso and title:
                # Calendar event reminders are represented as Notes. If the
                # model creates a calendar event with reminder_minutes and then
                # also creates a separate note reminder for the same title/time,
                # keep the existing note so the user gets only one dispatch.
                existing_q = db.query(Note).filter(
                    Note.archived == False,  # noqa: E712
                    Note.due_date == due_iso,
                )
                if owner is not None:
                    existing_q = existing_q.filter(Note.owner == owner)
                target_title = _norm_note_title(title)
                for existing in existing_q.limit(25).all():
                    if _norm_note_title(existing.title or "") == target_title:
                        return {
                            "response": f"Reminder already exists: \"{existing.title or title}\" (id: {existing.id[:8]})",
                            "note_id": existing.id,
                            "duplicate": True,
                            "exit_code": 0,
                        }
            duplicate = _existing_exact_note(
                title=title,
                content_value=content_raw,
                items_value=items_raw,
                note_type=note_type,
                label=args.get("label"),
                due_date=due_iso,
                color=args.get("color"),
                pinned=args.get("pinned", False),
            )
            if duplicate:
                return {
                    "response": f"Note already exists: \"{duplicate.title or title or '(untitled)'}\" (id: {duplicate.id[:8]})",
                    "note_id": duplicate.id,
                    "note_title": duplicate.title or title or "",
                    "open_url": f"/#open=notes&note={duplicate.id}",
                    "duplicate": True,
                    "exit_code": 0,
                }
            stored_content = (
                merge_items_into_content(content_raw, items_raw)
                if items_raw else content_raw
            )
            too_big = size_refusal({
                "title": title, "content": stored_content, "label": args.get("label"),
                "color": args.get("color"), "due_date": due_iso, "note_type": note_type,
                "session_id": args.get("session_id"),
            })
            if too_big:
                return {"error": f"{too_big['message']} Nothing was saved.", "exit_code": 1}
            # Reserve from the content as stored: it is what the note-scoped
            # media route serves from.
            missing_id = reserve_upload_references(
                get_upload_handler(),
                owner,
                stored_content,
                args.get("color"),
            )
            if missing_id:
                return {
                    "error": f"Referenced upload is no longer available: {missing_id}",
                    "exit_code": 1,
                }
            note = Note(
                id=str(_uuid.uuid4()),
                owner=owner,
                title=title,
                content=stored_content,
                items=None,
                note_type=note_type,
                color=args.get("color"),
                label=args.get("label"),
                pinned=args.get("pinned", False),
                due_date=due_iso,
                source="agent",
                session_id=args.get("session_id"),
            )
            db.add(note)
            db.commit()
            db.refresh(note)
            _remember_version(owner, note)
            # Return note_id so the chat-side renderer can build a real
            # "View note" button that opens the notes modal at this id.
            # Previously the create response only included a prose
            # confirmation; the model would type "View note" as a markdown
            # link with no target, leaving the user with a click that
            # did nothing and uncertainty about whether the note was made.
            return {
                "response": f"{'Reminder' if due_iso else 'Note'} created: \"{title or '(untitled)'}\" (id: {note.id[:8]})",
                "note_id": note.id,
                "note_title": title or "",
                "open_url": f"/#open=notes&note={note.id}",
                "exit_code": 0,
            }

        elif action == "update":
            note_id = _note_id_arg()
            note = _note_by_prefix(note_id)
            if not note and bound is None:
                title_query = str(
                    args.get("title")
                    or args.get("query")
                    or args.get("text")
                    or ""
                ).strip()
                if title_query:
                    q = db.query(Note)
                    if owner:
                        q = q.filter(Note.owner == owner)
                    candidates = [
                        n for n in q.filter(Note.archived == False).all()
                        if _norm_note_title(n.title) == _norm_note_title(title_query)
                    ]
                    if len(candidates) == 1:
                        note = candidates[0]
                    elif len(candidates) > 1:
                        return {
                            "error": f"Multiple notes titled '{title_query}' found; pass an id.",
                            "exit_code": 1,
                        }
            if not note:
                target = note_id or args.get("title") or args.get("query") or args.get("text") or ""
                return _not_found(target)
            denied = _denied(note, "write")
            if denied:
                return denied
            if scope is not None:
                refused = scope.refuse_write(note)
                if refused:
                    return _own(refused, note)
            if not sharing.is_owner(owner, note):
                # A collaborator's agent often sends back the whole note it
                # viewed. An owner-only key carrying the stored value changes
                # nothing, so it is dropped rather than refused; any other
                # value (null included, when something is stored) is refused.
                blocked = []
                for field in sharing.owner_only_fields_in(args):
                    if _same_as_stored(note, field, args[field]):
                        args.pop(field)
                    else:
                        blocked.append(field)
                if blocked:
                    return _own({"error": f"Only the note's owner ({note.owner}) can change: {', '.join(blocked)}. "
                                          "Nothing was saved.", "exit_code": 1}, note)
            # Applied as sent below, so anything but a boolean (null included)
            # would store a note that is neither active nor archived.
            not_bool = [f for f in ("pinned", "archived") if f in args and not isinstance(args[f], bool)]
            if not_bool:
                return _own({"error": f"{', '.join(not_bool)} must be true or false. Nothing was saved.", "exit_code": 1}, note)
            # Uploads already in the note were reserved by whoever inserted
            # them (on a shared note, possibly someone else); only new ones
            # are reserved for this writer, read from the note as it will be
            # stored (what the note-scoped media route serves from).
            from src.upload_handler import extract_internal_upload_ids, reserve_upload_ids

            def _stored_upload_refs():
                refs = extract_internal_upload_ids(effective_content(note.content, note.items, getattr(note, "image_url", None)) or "")
                refs |= extract_internal_upload_ids(getattr(note, "image_url", None) or "")
                refs |= extract_internal_upload_ids(getattr(note, "color", None) or "")
                return refs

            present = _stored_upload_refs()
            new_items = args.get("checklist_items")
            if new_items is None:
                new_items = args.get("items")
            if new_items is not None and _stale_since_seen(owner, note):
                return _own({"error": "This note changed since you last viewed it (someone ticked, added or edited "
                                      "something). View it again and send the updated checklist_items. Nothing was saved.",
                             "exit_code": 1}, note)
            # The version this write is computed from, read before the row is
            # reloaded: a write landing in between fails the commit below.
            stamp = stored_stamp(db, note.id)
            db.refresh(note)
            if scope is not None:
                # Read again after the refresh: a save that landed since the
                # check above is the user's, and is not overwritten.
                refused = scope.refuse_write(note)
                if refused:
                    return _own(refused, note)
            # `body_revision` (from view) says which body this write was
            # computed from; another one is a conflict, nothing is saved.
            revision = args.get("body_revision")
            if revision is not None and revision != note_body_revision(note):
                return _own(_body_conflict(), note)
            guarded = (args.get("content") is not None or new_items is not None
                       or args.get("note_type") is not None or revision is not None)
            expected_body = body_snapshot(note)
            before = undo_snapshot(note)
            sizes_before = sized_fields(note)
            present = _stored_upload_refs()
            if args.get("content") is not None or new_items is not None:
                fold_legacy_columns(note)
            previous_content = note.content
            for field in ("title", "content", "note_type", "color", "label"):
                if field in args and args[field] is not None:
                    setattr(note, field, args[field])
            # `view` shows the prose and the tasks apart, so text sent on its
            # own keeps the task lines; clearing them takes checklist_items: [].
            if args.get("content") is not None and new_items is None:
                note.content = keep_task_lines(previous_content, note.content)
            if args.get("content") is not None and note.image_url:
                note.content = fold_image_url(note.content, note.image_url)
            # Parse due_date the same way the `add` action does. The schema
            # advertises natural language ("tomorrow at 9am"), and naive ISO
            # strings need the user's tz offset attached so the frontend's
            # `new Date()` resolves the right absolute moment. Storing the raw
            # value here left updated reminders as unparseable literals that
            # never fired.
            if args.get("due_date") is not None:
                due_raw = args["due_date"]
                try:
                    from routes.calendar_routes import parse_due_for_user as _pdt_user
                    note.due_date = _pdt_user(due_raw)
                except Exception:
                    note.due_date = due_raw  # fall through; trust the model
            # checklist_items is the complete remaining list: it rewrites the
            # task lines in content, appending new ones and dropping the rest.
            if new_items is not None:
                note.content = apply_items_to_content(
                    note.content, new_items if isinstance(new_items, list) else _norm_note_items(new_items)
                )
                note.items = None
            if "pinned" in args:
                note.pinned = args["pinned"]
            if "archived" in args:
                note.archived = args["archived"]
            too_big = size_refusal(sized_fields(note), sizes_before)
            if too_big:
                db.rollback()
                return _own({"error": f"{too_big['message']} Nothing was saved.", "exit_code": 1}, note)
            missing_id = reserve_upload_ids(get_upload_handler(), owner, _stored_upload_refs() - present)
            if missing_id:
                db.rollback()
                return _own({
                    "error": f"Referenced upload is no longer available: {missing_id}",
                    "exit_code": 1,
                }, note)
            if scope is not None:
                record_agent_edit(note, before, scope.session_id)
            # Every agent write keeps the note as it was, from any chat; see core.note_versions.
            record_version(db, note, before, SOURCE_AGENT, author=owner, session_id=agent_session_id())
            try:
                # Right before the compare-and-swap: the guard takes the write lock.
                if guarded and not guard_note_body(db, note, expected_body):
                    db.rollback()
                    raise NoteChanged(note.id)
                commit_if_unchanged(db, note, stamp)
            except NoteChanged:
                return _own({"error": "The note changed while this update was being saved. Nothing was saved; "
                                      "view it again and retry.", "conflict": True, "exit_code": 1}, note)
            db.refresh(note)
            _remember_version(owner, note)
            result = {"response": f"Note updated: \"{note.title or '(untitled)'}\"",
                      "note_id": note.id, "note_title": note.title or "",
                      "open_url": f"/#open=notes&note={note.id}", "exit_code": 0}
            if scope is not None:
                scope.saw(note)
                result["agent_edit"] = True
            return _own(result, note)

        elif action == "delete":
            note_id = _note_id_arg()
            note = _note_by_prefix(note_id)
            if not note and bound is None:
                title_query = str(
                    args.get("title")
                    or args.get("query")
                    or args.get("text")
                    or ""
                ).strip()
                if title_query:
                    q = db.query(Note)
                    if owner:
                        q = q.filter(Note.owner == owner)
                    candidates = [
                        n for n in q.filter(Note.archived == False).all()
                        if _norm_note_title(n.title) == _norm_note_title(title_query)
                    ]
                    if len(candidates) == 1:
                        note = candidates[0]
                    elif len(candidates) > 1:
                        return {
                            "error": f"Multiple notes titled '{title_query}' found; pass an id.",
                            "exit_code": 1,
                        }
            if not note:
                target = note_id or args.get("title") or args.get("query") or args.get("text") or ""
                return _not_found(target)
            denied = _denied(note, "manage")
            if denied:
                return denied
            title = note.title
            from src.tool_routing_experiment import note_fixture_scope
            fixture_scope = note_fixture_scope.get()
            if fixture_scope is not None and note.id not in fixture_scope:
                return {"error": "Target is outside the disposable test fixtures; no change made.", "exit_code": 1}
            sharing.delete_note_shares(db, note.id)
            delete_note_versions(db, [note.id])
            db.delete(note)
            db.commit()
            return {"response": f"Deleted note: \"{title or '(untitled)'}\"", "exit_code": 0}

        elif action == "toggle_item":
            note_id = _note_id_arg()
            index = args.get("index")
            if not isinstance(index, int) or isinstance(index, bool):
                return {"error": "toggle_item requires an explicit integer index (0-based). Use view to inspect item indices if unknown; no change made.", "exit_code": 1}
            note = _note_by_prefix(note_id)
            if not note:
                return _not_found(note_id)
            denied = _denied(note, "write")
            if denied:
                return denied
            if "done" in args and not isinstance(args["done"], bool):
                return _own({"error": "done must be a boolean (true or false)", "exit_code": 1}, note)
            stamp = stored_stamp(db, note.id)
            db.refresh(note)
            if scope is not None:
                refused = scope.refuse_write(note)
                if refused:
                    return _own(refused, note)
            # A positional flip needs the body it was computed from: on
            # another one the index may name another task. The proof is the
            # `body_revision` `view` returned, or that this caller was last
            # shown (or wrote) exactly this version: a note chat's own read,
            # checked by refuse_write above, or the tool's seen version.
            revision = args.get("body_revision")
            if revision is not None:
                if revision != note_body_revision(note):
                    return _own(_body_conflict(), note)
            elif scope is None and not _seen_current(owner, note):
                return _own(_body_conflict(), note)
            expected_body = body_snapshot(note)
            before = undo_snapshot(note)
            fold_legacy_columns(note)
            content = note.content or ""
            tasks = parse_task_lines(content)
            if not tasks:
                return _own({"error": "Note has no checklist items", "exit_code": 1}, note)
            if index < 0 or index >= len(tasks):
                return _own({"error": f"Item index {index} out of range (0-{len(tasks)-1})", "exit_code": 1}, note)
            item = tasks[index]
            if "done" not in args or args["done"] != item["done"]:
                content, item = toggle_task(content, index)
                note.content = content
                if scope is not None:
                    record_agent_edit(note, before, scope.session_id)
                record_version(db, note, before, SOURCE_AGENT, author=owner, session_id=agent_session_id())
                try:
                    if not guard_note_body(db, note, expected_body):
                        db.rollback()
                        raise NoteChanged(note.id)
                    commit_if_unchanged(db, note, stamp)
                except NoteChanged:
                    return _own({"error": "The note changed while this item was being saved. Nothing was saved; "
                                          "view it again and retry.", "conflict": True, "exit_code": 1}, note)
                db.refresh(note)
                _remember_version(owner, note)
                if scope is not None:
                    scope.saw(note)
                    mark = "done" if item["done"] else "undone"
                    return _own({"response": f"Item '{item['text']}' marked {mark}", "note_id": note.id,
                                 "agent_edit": True, "exit_code": 0}, note)
            mark = "done" if item["done"] else "undone"
            return _own({"response": f"Item '{item['text']}' marked {mark}", "exit_code": 0}, note)

        else:
            return {"error": f"Unknown action: {action}. Use list/search/get/add/update/delete/toggle_item", "exit_code": 1}
    except Exception as e:
        logger.error(f"manage_notes error: {e}")
        return {"error": str(e), "exit_code": 1}
    finally:
        db.close()
