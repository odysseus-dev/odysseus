"""Notes-domain tool implementations.

Extracted from tool_implementations.py as part of slice 1 (#4082/#4071).
Holds the manage_notes tool (notes + checklists CRUD).
``src.tool_implementations`` re-exports these for backward compatibility.
"""
import json
import logging
import re
from typing import Dict, Optional

from src.tools._common import _parse_tool_args
from src.tool_utils import get_upload_handler
from src.upload_handler import reserve_upload_references

logger = logging.getLogger(__name__)


async def _push_todo(owner: Optional[str], note_id: str, action: str) -> None:
    """Write an agent's note change back to Microsoft To Do.

    The HTTP routes push through BackgroundTasks; the agent has no response
    to hang one off, so it awaits the same write-back directly. Skipping this
    would make a task the agent added or ticked off exist only in Odysseus —
    the exact split-brain the sync is meant to prevent.
    """
    try:
        from src.msgraph_todo import push_task_create, push_task_delete, push_task_update

        pusher = {"create": push_task_create, "update": push_task_update,
                  "delete": push_task_delete}.get(action)
        if pusher:
            await pusher(owner or "", note_id)
    except Exception as e:
        logger.warning("Microsoft To Do %s push failed for note=%s: %s", action, note_id, e)


def _mark_todo_dirty(note) -> bool:
    """Flag an unpushed local edit, in the same transaction as the change.

    Returns whether this note syncs at all.
    """
    try:
        from src.msgraph_todo import note_should_sync
    except Exception:
        return False
    if not note_should_sync(note):
        return False
    note.todo_sync_pending = "update" if note.remote_id else "create"
    return True


def _todo_tombstone(db, note) -> bool:
    """Record what a delete needs before the note row goes away."""
    remote_id = getattr(note, "remote_id", None)
    remote_list_id = getattr(note, "remote_list_id", None)
    if not (remote_id and remote_list_id):
        return False
    from core.database import MsTodoDeletedNote

    db.merge(MsTodoDeletedNote(
        id=note.id,
        owner=note.owner,
        remote_id=remote_id,
        remote_list_id=remote_list_id,
        account_id=getattr(note, "todo_account_id", None),
        title=getattr(note, "title", None),
    ))
    return True


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
    from sqlalchemy.orm.attributes import flag_modified

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
    }
    action = _NOTE_ACTION_ALIASES.get(action, action)
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
    from src.agent_runtime.owned_resources import active_owned_operation
    bound = active_owned_operation()
    if bound is not None:
        bound.validate()
    db = SessionLocal()

    def _norm_note_title(value: str) -> str:
        text = (value or "").strip().lower()
        text = re.sub(r"^\s*reminder\s*:\s*", "", text)
        return re.sub(r"\s+", " ", text)

    def _note_visible_to_owner(note, owner_value: Optional[str]) -> bool:
        # Empty owner_value is single-user / auth-disabled mode. A real
        # authenticated owner must match exactly; null/empty legacy rows are not
        # shared between accounts.
        if not owner_value:
            return True
        return getattr(note, "owner", None) == owner_value

    def _is_calendar_reminder_note(note) -> bool:
        return getattr(note, "source", None) == "calendar" and getattr(note, "label", None) == "calendar"

    def _note_by_prefix(note_id: str):
        if not note_id:
            return None
        q = db.query(Note).filter(Note.id == note_id if bound is not None else Note.id.startswith(note_id))
        if owner:
            q = q.filter(Note.owner == owner)
        return q.first()

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
        target_content = _norm_note_text(content_value)
        target_items = _norm_note_items(items_value)
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
            if _norm_note_text(existing.content) != target_content:
                continue
            if _norm_note_items(existing.items) != target_items:
                continue
            return existing
        return None

    def _format_note_list(notes, *, full_content: bool = False) -> str:
        lines = []
        for n in notes:
            pin = " [PINNED]" if n.pinned else ""
            typ = " [checklist]" if n.note_type == "checklist" else ""
            lbl = f" #{n.label}" if n.label else ""
            title = n.title or "(untitled)"
            lines.append(f"- [{n.id}] **{title}**{pin}{typ}{lbl}")
            # Search/list is a locator operation. Keep the body behind view so
            # a model cannot satisfy an explicit read request without the
            # required second call, and so large checklists do not flood the
            # next model context.
            if full_content and n.note_type == "checklist" and n.items:
                try:
                    items = json.loads(n.items)
                    for i, item in enumerate(items):
                        mark = "x" if item.get("done") else " "
                        lines.append(f"  [{mark}] {i}: {item.get('text', '')}")
                except (json.JSONDecodeError, TypeError):
                    pass
            elif full_content and n.content:
                lines.append(f"  {n.content}")
        return "\n".join(lines)

    try:
        if action in ("list", "search", "find"):
            q = db.query(Note)
            if owner is not None:
                q = q.filter(Note.owner == owner)
            label_filter = str(args.get("label") or "").strip()
            if label_filter and label_filter.lower() != "default":
                q = q.filter(Note.label == label_filter)
            show_archived = args.get("archived", False)
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
                return {"error": f"Note '{note_id}' not found", "exit_code": 1}
            if not _note_visible_to_owner(note, owner):
                return {"error": "Note not found", "exit_code": 1}
            return {"results": _format_note_list([note], full_content=True), "exit_code": 0}

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
            items_json = json.dumps(items_raw) if items_raw is not None else None
            note_type = args.get("note_type", "checklist" if items_raw else "note")
            if not title and note_type in {"checklist", "todo", "goal"}:
                from src.user_time import now_user_local
                title = f"To-do - {now_user_local().date().isoformat()}"
            if note_type in {"checklist", "goal"} and not isinstance(items_raw, list):
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
            missing_id = reserve_upload_references(
                get_upload_handler(),
                owner,
                content_raw,
                args.get("color"),
                items_json,
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
                content=content_raw,
                items=items_json,
                note_type=note_type,
                color=args.get("color"),
                label=args.get("label"),
                pinned=args.get("pinned", False),
                due_date=due_iso,
                source="agent",
                session_id=args.get("session_id"),
            )
            syncs = _mark_todo_dirty(note)
            db.add(note)
            db.commit()
            if syncs:
                await _push_todo(owner, note.id, "create")
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
                return {"error": f"Note '{target}' not found", "exit_code": 1}
            if not _note_visible_to_owner(note, owner):
                return {"error": "Note not found", "exit_code": 1}
            missing_id = reserve_upload_references(
                get_upload_handler(),
                owner,
                args.get("content"),
                args.get("color"),
                args.get("checklist_items"),
                args.get("items"),
            )
            if missing_id:
                return {
                    "error": f"Referenced upload is no longer available: {missing_id}",
                    "exit_code": 1,
                }
            for field in ("title", "content", "note_type", "color", "label"):
                if field in args and args[field] is not None:
                    setattr(note, field, args[field])
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
            new_items = args.get("checklist_items")
            if new_items is None:
                new_items = args.get("items")
            if new_items is not None:
                note.items = json.dumps(new_items)
                flag_modified(note, "items")
            if "pinned" in args:
                note.pinned = args["pinned"]
            if "archived" in args:
                note.archived = args["archived"]
            syncs = _mark_todo_dirty(note)
            note_pk = note.id
            db.commit()
            if syncs:
                await _push_todo(owner, note_pk, "update")
            return {"response": f"Note updated: \"{note.title or '(untitled)'}\"",
                    "note_id": note.id, "note_title": note.title or "",
                    "open_url": f"/#open=notes&note={note.id}", "exit_code": 0}

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
                return {"error": f"Note '{target}' not found", "exit_code": 1}
            if not _note_visible_to_owner(note, owner):
                return {"error": "Note not found", "exit_code": 1}
            title = note.title
            from src.tool_routing_experiment import note_fixture_scope
            fixture_scope = note_fixture_scope.get()
            if fixture_scope is not None and note.id not in fixture_scope:
                return {"error": "Target is outside the disposable test fixtures; no change made.", "exit_code": 1}
            note_pk = note.id
            had_remote = _todo_tombstone(db, note)
            db.delete(note)
            db.commit()
            if had_remote:
                await _push_todo(owner, note_pk, "delete")
            return {"response": f"Deleted note: \"{title or '(untitled)'}\"", "exit_code": 0}

        elif action == "toggle_item":
            note_id = _note_id_arg()
            index = args.get("index")
            if not isinstance(index, int) or isinstance(index, bool):
                return {"error": "toggle_item requires an explicit integer index (0-based). Use view to inspect item indices if unknown; no change made.", "exit_code": 1}
            note = _note_by_prefix(note_id)
            if not note:
                return {"error": f"Note '{note_id}' not found", "exit_code": 1}
            if not _note_visible_to_owner(note, owner):
                return {"error": "Note not found", "exit_code": 1}
            if not note.items:
                return {"error": "Note has no checklist items", "exit_code": 1}
            items = json.loads(note.items)
            if index < 0 or index >= len(items):
                return {"error": f"Item index {index} out of range (0-{len(items)-1})", "exit_code": 1}
            if "done" in args and not isinstance(args["done"], bool):
                return {"error": "done must be a boolean (true or false)", "exit_code": 1}
            items[index]["done"] = args["done"] if "done" in args else not items[index].get("done", False)
            note.items = json.dumps(items)
            flag_modified(note, "items")
            syncs = _mark_todo_dirty(note)
            note_pk = note.id
            db.commit()
            if syncs:
                await _push_todo(owner, note_pk, "update")
            mark = "done" if items[index]["done"] else "undone"
            return {"response": f"Item '{items[index].get('text', '')}' marked {mark}", "exit_code": 0}

        else:
            return {"error": f"Unknown action: {action}. Use list/search/view/add/update/delete/toggle_item", "exit_code": 1}
    except Exception as e:
        logger.error(f"manage_notes error: {e}")
        return {"error": str(e), "exit_code": 1}
    finally:
        db.close()
