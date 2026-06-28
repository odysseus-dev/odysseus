"""Notes-domain tool implementations.

Extracted from tool_implementations.py as part of slice 1 (#4082/#4071).
Holds the manage_notes tool (notes + checklists CRUD).
``src.tool_implementations`` re-exports these for backward compatibility.
"""
import logging
import re
from typing import Dict, Optional

from src.tools._common import _parse_tool_args

logger = logging.getLogger(__name__)


async def do_manage_notes(content: str, owner: Optional[str] = None) -> Dict:
    """Handle manage_notes tool calls: CRUD on notes and checklists."""
    import uuid as _uuid
    from core.database import SessionLocal, Note
    from core.notes_markdown import merge_items_into_content, parse_task_lines, toggle_task

    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    # Action aliases — match what models actually emit. `create` is the most
    # common alternative to `add`. Hyphenated forms also accepted.
    action = (args.get("action") or "").replace("-", "_").strip().lower()
    _NOTE_ACTION_ALIASES = {
        "create": "add",
        "new": "add",
        "save": "add",
        "remind": "add",
        "remove": "delete",
        "remove_item": "toggle_item",
        "read": "get",
        "view": "get",
        "show": "get",
        "open": "get",
    }
    action = _NOTE_ACTION_ALIASES.get(action, action)
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

    def _note_by_prefix(note_id: str):
        if not note_id:
            return None
        q = db.query(Note).filter(Note.id.startswith(note_id))
        if owner:
            q = q.filter(Note.owner == owner)
        return q.first()

    try:
        if action == "list":
            q = db.query(Note)
            if owner is not None:
                q = q.filter(Note.owner == owner)
            if args.get("label"):
                q = q.filter(Note.label == args["label"])
            show_archived = args.get("archived", False)
            q = q.filter(Note.archived == show_archived)
            notes = q.order_by(Note.pinned.desc(), Note.updated_at.desc()).all()
            if not notes:
                return {"response": "No notes found.", "exit_code": 0}
            lines = []
            for n in notes:
                pin = " [PINNED]" if n.pinned else ""
                tasks = parse_task_lines(n.content)
                lbl = f" #{n.label}" if n.label else ""
                title = n.title or "(untitled)"
                lines.append(f"- [{n.id[:8]}] **{title}**{pin}{lbl}")
                if tasks:
                    # Show the checklist with its toggle indices.
                    for t in tasks:
                        mark = "x" if t["done"] else " "
                        lines.append(f"  [{mark}] {t['index']}: {t['text']}")
                elif n.content:
                    snippet = n.content[:80].replace("\n", " ")
                    lines.append(f"  {snippet}")
            return {"results": "\n".join(lines)}

        elif action == "get":
            # Read one note in full (content isn't truncated like `list`).
            note_id = args.get("id", "")
            note = _note_by_prefix(note_id)
            if not note:
                return {"error": f"Note '{note_id}' not found", "exit_code": 1}
            if not _note_visible_to_owner(note, owner):
                return {"error": "Note not found", "exit_code": 1}
            tasks = parse_task_lines(note.content)
            meta = []
            if note.label:
                meta.append(f"#{note.label}")
            if note.due_date:
                meta.append(f"due {note.due_date}")
            if note.repeat and note.repeat != "none":
                meta.append(f"repeats {note.repeat}")
            if note.pinned:
                meta.append("pinned")
            if note.archived:
                meta.append("archived")
            lines = [f"**{note.title or '(untitled)'}**  [{note.id[:8]}]"]
            if meta:
                lines.append("_" + " · ".join(meta) + "_")
            lines.append("")
            lines.append(note.content or "(empty)")
            if tasks:
                lines.append("")
                lines.append("Checklist items (toggle_item by index):")
                for t in tasks:
                    lines.append(f"  [{'x' if t['done'] else ' '}] {t['index']}: {t['text']}")
            return {"results": "\n".join(lines)}

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
            # Unified model: a checklist is just `- [ ]` / `- [x]` task lines in
            # the markdown `content`. The primary path is for the model to write
            # those lines directly. For backward compatibility we still accept a
            # structured `checklist_items` (or legacy `items`) array and fold it
            # into `content` as task lines.
            items_raw = args.get("checklist_items")
            if items_raw is None:
                items_raw = args.get("items")
            content_raw = merge_items_into_content(content_raw, items_raw)
            # Accept natural-language due_date ("tomorrow at 1pm") in
            # addition to ISO. Use the user-tz-aware parser so the LLM's
            # naive times ("today at 9pm") are anchored to the USER's clock,
            # not the server's. Returns ISO with explicit offset so frontend
            # `new Date()` resolves the right absolute moment regardless of
            # where the user is.
            due_raw = args.get("due_date")
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
            note = Note(
                id=str(_uuid.uuid4()),
                owner=owner,
                title=title,
                content=content_raw,
                items=None,
                note_type="note",
                color=args.get("color"),
                label=args.get("label"),
                pinned=args.get("pinned", False),
                due_date=due_iso,
                source="agent",
                session_id=args.get("session_id"),
            )
            db.add(note)
            db.commit()
            # Return note_id so the chat-side renderer can build a real
            # "View note" button that opens the notes modal at this id.
            # Previously the create response only included a prose
            # confirmation; the model would type "View note" as a markdown
            # link with no target, leaving the user with a click that
            # did nothing and uncertainty about whether the note was made.
            return {
                "response": f"Note created: \"{title or '(untitled)'}\" (id: {note.id[:8]})",
                "note_id": note.id,
                "note_title": title or "",
                "open_url": f"/#open=notes&note={note.id}",
                "exit_code": 0,
            }

        elif action == "update":
            note_id = args.get("id", "")
            note = _note_by_prefix(note_id)
            if not note:
                return {"error": f"Note '{note_id}' not found", "exit_code": 1}
            if not _note_visible_to_owner(note, owner):
                return {"error": "Note not found", "exit_code": 1}
            for field in ("title", "content", "color", "label"):
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
            # Back-compat: fold a structured items array into the markdown
            # content (only when the content doesn't already carry task lines).
            new_items = args.get("checklist_items")
            if new_items is None:
                new_items = args.get("items")
            if new_items is not None:
                note.content = merge_items_into_content(note.content, new_items)
            if "pinned" in args:
                note.pinned = args["pinned"]
            if "archived" in args:
                note.archived = args["archived"]
            db.commit()
            return {"response": f"Note updated: \"{note.title or '(untitled)'}\"", "exit_code": 0}

        elif action == "delete":
            note_id = args.get("id", "")
            note = _note_by_prefix(note_id)
            if not note:
                return {"error": f"Note '{note_id}' not found", "exit_code": 1}
            if not _note_visible_to_owner(note, owner):
                return {"error": "Note not found", "exit_code": 1}
            title = note.title
            db.delete(note)
            db.commit()
            return {"response": f"Deleted note: \"{title or '(untitled)'}\"", "exit_code": 0}

        elif action == "toggle_item":
            note_id = args.get("id", "")
            index = args.get("index", 0)
            note = _note_by_prefix(note_id)
            if not note:
                return {"error": f"Note '{note_id}' not found", "exit_code": 1}
            if not _note_visible_to_owner(note, owner):
                return {"error": "Note not found", "exit_code": 1}
            # Toggle the index-th task line inside the markdown content.
            try:
                new_content, item = toggle_task(note.content, int(index))
            except (IndexError, ValueError, TypeError):
                n_tasks = len(parse_task_lines(note.content))
                return {"error": f"Item index {index} out of range (0-{max(n_tasks-1, 0)})", "exit_code": 1}
            note.content = new_content
            db.commit()
            mark = "done" if item["done"] else "undone"
            return {"response": f"Item '{item['text']}' marked {mark}", "exit_code": 0}

        else:
            return {"error": f"Unknown action: {action}. Use list/get/add/update/delete/toggle_item", "exit_code": 1}
    except Exception as e:
        logger.error(f"manage_notes error: {e}")
        return {"error": str(e), "exit_code": 1}
    finally:
        db.close()
