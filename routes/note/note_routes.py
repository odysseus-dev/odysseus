# routes/note_routes.py
"""Google Keep-style notes / checklists API."""

import hashlib
import json
import uuid
import logging
import functools
import threading
from collections import OrderedDict
from typing import Dict, Any, Optional, Tuple

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from core.database import SessionLocal, Note
from core.note_writes import (
    NOTE_BODY_CONFLICT,
    NOTE_WRITE_ATTEMPTS,
    NoteChanged,
    body_snapshot,
    commit_if_unchanged,
    guard_note_body,
    note_body_revision,
    size_refusal,
    sized_fields,
    stored_stamp,
    write_with_retry,
)
from core import notes_sharing as sharing
from core.note_agent_scope import UNDO_FIELDS, agent_edit_view, parse_undo, snapshot, undo_available
from core.note_versions import (
    SOURCE_RESTORE,
    SOURCE_USER,
    apply_version,
    delete_note_versions,
    get_version,
    history_views,
    record_version,
    version_after,
    version_counts,
    version_view,
)
from core.notes_markdown import (
    normalize_note_color,
    append_task_line,
    apply_items_to_content,
    derive_note_type,
    effective_content,
    find_task_index,
    fold_image_url,
    fold_legacy_columns,
    has_legacy_image,
    keep_task_lines,
    parse_task_lines,
    reminder_body,
    remove_image_line,
    toggle_task,
)
from core.middleware import INTERNAL_TOOL_USER
from src.auth_helpers import require_privilege, require_user
from src.constants import (
    DATA_DIR,
    NOTE_MEDIA_ATTACHMENT_TYPES,
    NOTE_MEDIA_INLINE_TYPES,
    NOTE_VIEW_CACHE_MAX_CHARS,
    NOTE_VIEW_CACHE_SIZE,
)
from src.upload_handler import (
    extract_internal_upload_ids,
    reserve_upload_ids,
)

logger = logging.getLogger(__name__)

# What a 409 says when the note changed since the caller last read it (or
# a body write named no version to compare with): core.note_writes.NOTE_BODY_CONFLICT.


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class NoteCreate(BaseModel):
    title: str = ""
    content: Optional[str] = None
    items: Optional[list] = None
    note_type: str = "note"
    color: Optional[str] = None
    label: Optional[str] = None
    pinned: bool = False
    due_date: Optional[str] = None
    source: str = "user"
    session_id: Optional[str] = None
    image_url: Optional[str] = None
    gallery_id: Optional[str] = None
    repeat: Optional[str] = "none"
    sort_order: Optional[int] = None


class NoteUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    items: Optional[list] = None
    note_type: Optional[str] = None
    color: Optional[str] = None
    label: Optional[str] = None
    pinned: Optional[bool] = None
    archived: Optional[bool] = None
    due_date: Optional[str] = None
    image_url: Optional[str] = None
    gallery_id: Optional[str] = None
    repeat: Optional[str] = None
    sort_order: Optional[int] = None
    agent_session_id: Optional[str] = None
    # Concurrency tokens; a body write must carry one (see update_note).
    # `body_revision` is the note's body revision as the client last read
    # it; `base_updated_at` the `updated_at` it last saw.
    body_revision: Optional[str] = None
    base_updated_at: Optional[str] = None
    # The content is the whole document: taken as written, tasks included.
    replace_content: bool = False


class VersionRestore(BaseModel):
    # The note version the client showed; another one gets a 409.
    base_updated_at: Optional[str] = None
    body_revision: Optional[str] = None


class TaskToggle(BaseModel):
    # One of the two is required (see toggle_item).
    body_revision: Optional[str] = None
    base_updated_at: Optional[str] = None
    # The task's text as the client saw it, so a stale index can't flip
    # another task.
    text: Optional[str] = None
    # The state the person asked for. A flip from a view that hasn't seen a
    # tick made elsewhere would undo it; a target state can't.
    done: Optional[bool] = None


class TaskAppend(BaseModel):
    text: str
    indent: Optional[int] = 0


class NoteShareRequest(BaseModel):
    users: list = []
    permission: Optional[str] = "view"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _derived_items(content: Optional[str]) -> Optional[list]:
    """The legacy `items` shape, read from the task lines in `content`."""
    tasks = parse_task_lines(content)
    if not tasks:
        return None
    return [
        {"index": t["index"], "text": t["text"], "done": t["done"], "indent": t["indent"]}
        for t in tasks
    ]


# Every list read derives the same content, items and type for every note.
# The derivation is a pure function of the stored columns, so it is kept for
# rows whose columns have not changed since the last read. The cache is
# bounded by rows and by the characters it holds (keys and results), so a few
# very long notes can't pin hundreds of megabytes.
class _SizedLru:
    def __init__(self, fn, max_entries: int, max_chars: int):
        self._fn = fn
        self._max_entries = max_entries
        self._max_chars = max_chars
        self._entries: "OrderedDict[tuple, tuple]" = OrderedDict()
        self._chars = 0
        self._lock = threading.Lock()
        functools.update_wrapper(self, fn)

    @staticmethod
    def _weight(key: tuple, value: tuple) -> int:
        content, items, _note_type, image_url = value
        weight = sum(len(part or "") for part in key)
        if content is not key[0]:
            weight += len(content or "")
        if image_url is not key[2]:
            weight += len(image_url or "")
        return weight + sum(len(item[1] or "") for item in items or ())

    def __call__(self, *key):
        with self._lock:
            hit = self._entries.get(key)
            if hit is not None:
                self._entries.move_to_end(key)
                return hit[0]
        value = self._fn(*key)
        weight = self._weight(key, value)
        if weight > self._max_chars:
            return value
        with self._lock:
            if key not in self._entries:
                self._entries[key] = (value, weight)
                self._chars += weight
            while self._entries and (len(self._entries) > self._max_entries or self._chars > self._max_chars):
                _old, (_value, dropped) = self._entries.popitem(last=False)
                self._chars -= dropped
        return value

    def cache_clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._chars = 0

    def cache_info(self) -> Dict[str, int]:
        with self._lock:
            return {"entries": len(self._entries), "chars": self._chars,
                    "max_entries": self._max_entries, "max_chars": self._max_chars}


def _derive_view(
    content: Optional[str],
    legacy_items: Optional[str],
    image_url: Optional[str],
    note_type: Optional[str],
) -> Tuple[Optional[str], Optional[tuple], str, Optional[str]]:
    """(content, items, note_type, image_url) as a note payload shows them."""
    folded = effective_content(content, legacy_items, image_url)
    if not folded and content is None:
        folded = None
    # `image_url` is the legacy attachment record, shown as stored; the
    # image itself is a line in `content` (core.notes_markdown.fold_image_url).
    shown_image = image_url
    items = _derived_items(folded)
    frozen = tuple((i["index"], i["text"], i["done"], i["indent"]) for i in items) if items else None
    return folded, frozen, derive_note_type(note_type, folded), shown_image


_derived_view = _SizedLru(_derive_view, NOTE_VIEW_CACHE_SIZE, NOTE_VIEW_CACHE_MAX_CHARS)


def _refuse_oversize(fields: dict, before: Optional[dict] = None) -> None:
    """413 when the write would leave the note past a size limit (core.note_writes.size_refusal)."""
    refusal = size_refusal(fields, before)
    if refusal:
        raise HTTPException(413, detail=refusal)


def _write_image_url(note: Note, url: Optional[str]) -> None:
    """Change the legacy attachment, leaving unrelated Markdown alone."""
    previous = note.image_url
    note.image_url = url if (url or "").strip() else None
    if note.image_url:
        note.content = fold_image_url(note.content, note.image_url, replace=previous)
    elif previous:
        note.content = remove_image_line(note.content, previous)


def _note_access(user: Optional[str], note: Note, db) -> frozenset:
    """Capabilities `user` has on `note` (read/write/manage/share)."""
    return sharing.note_access(user, note, db)


def _note_to_dict(note: Note, user: Optional[str] = None, shares=None, version_count: int = 0) -> Dict[str, Any]:
    # `content` is the source of truth. `items`, `note_type` and a drawing's
    # `image_url` are derived from it so existing clients keep their shape.
    # A row the startup migration never folded (a non-SQLite database) shows
    # its legacy `items` and `image_url` the way the migration would store them.
    content, frozen_items, note_type, image_url = _derived_view(
        note.content, note.items, note.image_url, note.note_type,
    )
    items = (
        [{"index": i, "text": t, "done": d, "indent": n} for i, t, d, n in frozen_items]
        if frozen_items else None
    )
    ai_cls = None
    raw_ai = getattr(note, "ai_classification", None)
    if raw_ai:
        try:
            ai_cls = json.loads(raw_ai)
        except (json.JSONDecodeError, TypeError):
            ai_cls = None
    data = {
        "id": note.id,
        "owner": note.owner,
        "title": note.title,
        "content": content,
        "items": items,
        "note_type": note_type,
        "color": note.color,
        "label": note.label,
        "pinned": note.pinned,
        "archived": note.archived,
        "due_date": note.due_date,
        "source": note.source,
        "session_id": note.session_id,
        "sort_order": note.sort_order or 0,
        "image_url": note.image_url,
        "body_revision": note_body_revision(note),
        "gallery_id": getattr(note, "gallery_id", None),
        "repeat": note.repeat or "none",
        "ai_classification": ai_cls,
        "ai_content_hash": getattr(note, "ai_content_hash", None),
        "agent_session_id": getattr(note, "agent_session_id", None),
        "agent_edit": agent_edit_view(note),
        # How many earlier versions the history keeps (owner only; None for a collaborator).
        "version_count": version_count,
        "created_at": note.created_at.isoformat() if note.created_at else None,
        "updated_at": note.updated_at.isoformat() if note.updated_at else None,
    }
    data.update(sharing.sharing_fields(note, user, shares))
    if not data["is_owner"]:
        sharing.strip_owner_private(data)
    return data


def _note_dict_for(db, note: Note, user: Optional[str]) -> Dict[str, Any]:
    """One note's payload with its grants loaded."""
    return _note_to_dict(note, user, sharing.shares_by_note(db, [note.id]).get(note.id),
                         version_counts(db, [note.id]).get(note.id, 0))


def _note_conflict(db, note: Note, user: Optional[str], message: str = NOTE_BODY_CONFLICT) -> HTTPException:
    """The 409 a write answers when the note moved on since the caller read it, carrying the current note."""
    return HTTPException(409, detail={"message": message, "note": _note_dict_for(db, note, user)})


def _conditional_json(request: Request, payload: Dict[str, Any]) -> Response:
    """A JSON response with a strong ETag over its exact body.

    The Notes panel re-reads the list every few seconds; when nothing it
    would see has changed, it gets a 304 with no body instead of the list.
    The tag is computed from the bytes sent, so any change a reader would
    see (an edit, a deleted note, a share added or removed) changes it.
    """
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    etag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
    headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    sent = request.headers.get("if-none-match", "")
    if etag in [tag.strip().removeprefix("W/") for tag in sent.split(",")]:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


def _fold_legacy_columns(note: Note) -> None:
    """Move a row the startup migration skipped into content before a write.

    Shared with the agent tool (`core.notes_markdown.fold_legacy_columns`):
    unreadable `items` are left in place for the migration to retry.
    """
    fold_legacy_columns(note)


def _reminder_text_from_note(note: Note) -> tuple[str, str]:
    """Return the reminder title/body from a stored note row."""
    title = (note.title or "Note reminder").strip() or "Note reminder"
    return title, reminder_body(effective_content(note.content, note.items, getattr(note, "image_url", None)))



# ---------------------------------------------------------------------------
# Reminder dispatch — module-level so background tasks (built-in actions)
# can call it directly without an HTTP roundtrip + auth cookie. The route
# version below is a thin wrapper that pulls `owner` from the request.
# ---------------------------------------------------------------------------

# Scheduler reference — set by setup_note_routes() so dispatch_reminder can
# push a parallel in-app notification (frontend polls the scheduler's queue
# and fires real browser Notification(...) popups). Optional; works without it.
_scheduler_ref = None


async def dispatch_reminder(
    title: str,
    note_body: str,
    note_id: str,
    owner: str = "",
    queue_browser: bool = True,
    settings_override: dict | None = None,
) -> dict:
    """Fire a reminder via the configured channel (browser/email/ntfy/webhook).

    Args:
        title: short headline shown to the user
        note_body: longer body text
        note_id: stable id (used as tag/dedupe in browser notifications)
        owner: the user this reminder belongs to — scopes SMTP config to
               their account so we don't cross-leak credentials

    Returns: {synthesis, email_sent, ntfy_sent}. Browser channel is wired via
    the in-memory notification queue picked up by the frontend poller, so
    nothing is "sent" synchronously for it — the channel just routes there.
    """
    from src.settings import load_settings
    settings = {**load_settings(), **(settings_override or {})}
    channel = settings.get("reminder_channel", "browser")
    llm_on = bool(settings.get("reminder_llm_synthesis", False))
    title = (title or "").strip()
    note_body = (note_body or "").strip()
    cache_key = str(note_id) if note_id else ""
    cache = {}
    cache_path = None
    if cache_key:
        try:
            import json as _json
            from datetime import datetime as _dt, timezone as _tz, timedelta as _td
            from pathlib import Path as _P
            _slug = "".join(c if (c.isalnum() or c in "-_.@") else "_" for c in (owner or "default"))
            cache_path = _P(DATA_DIR) / f"note_pings_{_slug}.json"
            if cache_path.exists():
                cache = _json.loads(cache_path.read_text(encoding="utf-8"))
            last = cache.get(cache_key)
            if last:
                last_channel = None
                if isinstance(last, dict):
                    last_channel = last.get("channel")
                    last = last.get("at")
                last_dt = _dt.fromisoformat(str(last))
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=_tz.utc)
                # Legacy cache values were plain timestamps and could be
                # written by the frontend even when the email/ntfy send failed.
                # Treat those as browser-only dedupe so email reminders can be
                # retried by the backend scanner after a failed frontend path.
                should_skip = last_dt >= _dt.now(_tz.utc) - _td(minutes=25)
                if should_skip and channel in ("email", "ntfy", "webhook"):
                    should_skip = last_channel == channel
                if should_skip:
                    return {
                        "synthesis": None,
                        "email_sent": False,
                        "ntfy_sent": False,
                        "webhook_sent": False,
                        "browser_sent": True,
                        "skipped": True,
                    }
        except Exception as _e:
            logger.debug(f"dispatch_reminder: cache read failed: {_e}")

    synthesis = None
    _SYNTH_FAILED_TAG = "[utility model unavailable — no summary generated]"
    if llm_on:
        try:
            from src.endpoint_resolver import resolve_endpoint
            from src.llm_core import llm_call_async
            from src.reminder_personas import synthesis_system_prompt
            url, model, headers = resolve_endpoint("utility", owner=owner or None)
            if not url:
                url, model, headers = resolve_endpoint("default", owner=owner or None)
            if url and model:
                persona_id = (settings.get("reminder_llm_persona") or "").strip()
                sys_prompt = synthesis_system_prompt(persona_id)
                raw = await llm_call_async(
                    url=url, model=model,
                    messages=[
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": f"Title: {title}\n\n{note_body}".strip()},
                    ],
                    temperature=0.7, max_tokens=200, headers=headers, timeout=30,
                )
                from src.text_helpers import strip_think as _strip_think
                # prose=True strips untagged "The user wants me to…" chain-of-thought.
                # prompt_echo=True strips Qwen-style "Thinking Process:" / leaked
                # prompt prefixes. Both are safe here because this is a
                # one-sentence LLM-only output, not user-pasted content.
                synthesis = _strip_think(raw or "", prose=True, prompt_echo=True)
                # Reminder synthesis is supposed to be ONE sentence. Strip-think's
                # paragraph-based heuristic misses cases where the model puts
                # reasoning + answer on consecutive lines inside one paragraph
                # (e.g. "I should write... [\n] You have one task waiting...").
                # Walk lines, drop reasoning/prompt-echo lines, then keep the
                # last surviving line — that's the actual warm sentence.
                if synthesis:
                    import re as _re
                    # Tightened: target ACTUAL self-talk (model narrating what
                    # it'll do) rather than any first-person sentence. The old
                    # pattern killed legit warm sentences like "I'll see you
                    # tomorrow" or "I should be done by then". New rules:
                    #  • "I (need|should|have|'ll|will) (write|draft|reply|…)"
                    #    only matches when followed by a TASK verb taking an
                    #    OBJECT (so first-person + intransitive verb passes).
                    #  • Self-instructional patterns the model emits verbatim:
                    #    "I should write something that reminds them…",
                    #    "I need to draft…", "Let me think…".
                    #  • Explicit instructions echoed back from the prompt:
                    #    "Keep it under 25 words", "No greetings".
                    _reasoning = _re.compile(
                        r"^\s*(?:"
                        # "I should write/draft/compose…" with a task-object follow
                        r"i (?:need|should|have|'ll|will|am going|am)\s+to\s+"
                        r"(?:write|draft|compose|craft|generate|produce|create|"
                        r"summarize|answer|provide|note|address|remind|output)"
                        r"\s+(?:a |an |the |something|this|that|here|them|him|her|"
                        r"you|user|reply|response|sentence|message|line|warm)|"
                        # The model literally narrating about the user
                        r"the user (?:wants|is asking|asks|needs|wrote|said|requested) (?:me )?(?:to|for|that|about|something)|"
                        # "Let me [think/write/draft/…] (about/for/the …)"
                        r"let me (?:think|write|draft|consider|note|see|check)\b\s+(?:about|for|the|this|that|if|whether)|"
                        # "Looking at the/this/that …"
                        r"looking at (?:the|this|that)\b|"
                        # "Based on the/this/what …"
                        r"based on (?:the|this|what|context|that)\b|"
                        # Prompt-echo of length / style instructions
                        r"keep it under \d+ words\b|"
                        r"(?:no greetings|no preamble|no hashtags|just output the)\b"
                        r").*",
                        _re.IGNORECASE,
                    )
                    # Echo of the prompt's "Pending (N):" / "<N> pending" tail.
                    _echo = _re.compile(
                        r"^\s*(?:pending\s*(?:\(\d+\))?\s*[:.]|(?:\d+|one|two|three|four|five)\s+pending\b)",
                        _re.IGNORECASE,
                    )
                    lines = [ln for ln in synthesis.splitlines() if ln.strip()]
                    cleaned = [ln for ln in lines if not _reasoning.match(ln) and not _echo.match(ln)]
                    if cleaned:
                        # The model's actual answer is normally the LAST surviving
                        # line — reasoning leads, answer trails.
                        synthesis = cleaned[-1].strip()
            else:
                synthesis = _SYNTH_FAILED_TAG
        except Exception as e:
            logger.warning(f"Reminder LLM synthesis failed: {e}")
            synthesis = _SYNTH_FAILED_TAG
        if synthesis:
            _s = synthesis.strip(); _low = _s.lower()
            if (not _s or _low.startswith("error:") or _low.startswith("[error")
                    or "operation failed" in _low
                    or ("upstream" in _low and "failed" in _low)) and synthesis != _SYNTH_FAILED_TAG:
                logger.warning(f"Reminder synthesis looked like an error, replacing: {_s[:120]!r}")
                synthesis = _SYNTH_FAILED_TAG

    email_sent = False
    email_error = ""
    if channel == "email":
        try:
            from routes.email_routes import _get_email_config, _smtp_ready
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            from datetime import datetime as _dt
            # `reminder_email_account_id` lets the user pick WHICH email
            # account to send reminders from (when they have several
            # configured in Integrations). Falls back to the default
            # account when no explicit choice is saved.
            _acc_id = (settings.get("reminder_email_account_id") or "").strip() or None
            cfg = _get_email_config(account_id=_acc_id, owner=owner or "")
            if not _smtp_ready(cfg):
                try:
                    from core.database import SessionLocal as _SL, EmailAccount as _EA
                    from sqlalchemy import and_, or_
                    db = _SL()
                    try:
                        q = db.query(_EA).filter(_EA.enabled == True)  # noqa: E712
                        if owner:
                            unowned = or_(_EA.owner == None, _EA.owner == "")  # noqa: E711
                            same_mailbox = or_(_EA.imap_user == owner, _EA.from_address == owner)
                            q = q.filter(or_(_EA.owner == owner, and_(unowned, same_mailbox)))
                        for row in q.order_by(_EA.is_default.desc(), _EA.created_at.asc()).all():
                            trial = _get_email_config(account_id=row.id, owner=owner or "")
                            if _smtp_ready(trial):
                                cfg = trial
                                break
                    finally:
                        db.close()
                except Exception as _fallback_error:
                    logger.debug(f"Reminder SMTP fallback lookup failed: {_fallback_error}")
            from_addr = (cfg.get("from_address") or cfg.get("smtp_user") or "").strip()
            recipient = (settings.get("reminder_email_to") or "").strip() or from_addr
            # Loud diagnostic so we can see WHY a reminder didn't send (the
            # previous "silently no-op when cfg has no smtp_host" was invisible).
            logger.info(
                "dispatch_reminder[email] note_id=%s owner=%r "
                "has_smtp_host=%s has_smtp_user=%s has_from=%s has_recipient=%s",
                note_id, owner,
                bool(cfg.get("smtp_host")), bool(cfg.get("smtp_user")),
                bool(from_addr), bool(recipient),
            )
            missing = []
            if not cfg.get("smtp_host"):
                missing.append("SMTP host")
            if not cfg.get("smtp_user"):
                missing.append("SMTP user")
            if not (cfg.get("smtp_password") or cfg.get("oauth_provider")):
                missing.append("SMTP credentials")
            if not from_addr:
                missing.append("from address")
            if not recipient:
                missing.append("recipient")
            if missing:
                email_error = "Missing " + ", ".join(missing)
                logger.warning(
                    "Reminder email not sent for note_id=%s account=%r: %s",
                    note_id, cfg.get("account_name"), email_error,
                )
            else:
                msg = MIMEMultipart("alternative")
                msg["From"] = from_addr
                msg["To"] = recipient
                _t = title or 'Note'
                _t = _t[len('Reminder:'):].strip() if _t.lower().startswith('reminder:') else _t
                msg["Subject"] = f"Reminder (Odysseus): {_t}"
                msg["Date"] = _dt.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")
                msg["X-Odysseus-Origin"] = "odysseus-ui"
                msg["X-Odysseus-Kind"] = "reminder"
                msg["X-Odysseus-Ref"] = str(note_id)
                # Body shape: synthesis (warm sentence) → blank line → bold
                # title header → note details. The title was previously only
                # in the subject line, so the email read like a faceless
                # to-do list with no anchor to which note triggered it.
                _body_chunks = []
                if synthesis:
                    _body_chunks.append(synthesis)
                if _t:
                    _body_chunks.append(_t)
                if note_body:
                    _body_chunks.append(note_body)
                plain = "\n\n".join(_body_chunks) if _body_chunks else title
                msg.attach(MIMEText(plain, "plain", "utf-8"))

                def _smtp_send():
                    from routes.email_helpers import _send_smtp_message
                    _send_smtp_message(cfg, from_addr, [recipient], msg.as_string())

                import asyncio as _aio
                await _aio.to_thread(_smtp_send)
                email_sent = True
        except Exception as e:
            email_error = str(e) or e.__class__.__name__
            logger.warning(f"Reminder email send failed: {e}")

    webhook_sent = False
    webhook_error = ""
    if channel == "webhook":
        try:
            import httpx
            import json as _wjson
            from src.integrations import load_integrations
            # Built-in payload defaults for known presets so users don't have
            # to configure a template just to use a standard service.
            _PRESET_TEMPLATE_DEFAULTS = {
                "discord_webhook": '{"embeds": [{"title": "{{title}}", "description": "{{message}}", "color": 5793266}]}',
            }
            intg_id = settings.get("reminder_webhook_integration_id", "").strip()
            template = settings.get("reminder_webhook_payload_template", "").strip()
            if not intg_id:
                webhook_error = "No webhook integration selected"
            else:
                intg = next(
                    (i for i in load_integrations()
                     if i.get("id") == intg_id and i.get("base_url")),
                    None,
                )
                if not intg:
                    webhook_error = f"Integration {intg_id!r} not found or missing base URL"
                else:
                    # Fall back to a built-in default for known presets so
                    # users don't have to configure a template for standard
                    # services like Discord.
                    if not template:
                        template = _PRESET_TEMPLATE_DEFAULTS.get(intg.get("preset", ""), "")
                    if not template:
                        webhook_error = "No payload template configured"
                    else:
                        # Render template: JSON-escape the values so the result
                        # is always valid JSON regardless of special characters.
                        # dumps() returns `"value"` — strip outer quotes.
                        msg = (synthesis or note_body or title or "Reminder")[:4000]
                        _t = _wjson.dumps(title or "Reminder")[1:-1]
                        _m = _wjson.dumps(msg)[1:-1]
                        rendered = template.replace("{{title}}", _t).replace("{{message}}", _m)
                        hdrs = {"Content-Type": "application/json"}
                        api_key = intg.get("api_key", "")
                        auth_type = (intg.get("auth_type") or "none").lower()
                        if api_key:
                            if auth_type == "bearer":
                                hdrs["Authorization"] = f"Bearer {api_key}"
                            elif auth_type == "header":
                                hdrs[intg.get("auth_header") or "Authorization"] = api_key
                        url = intg["base_url"].rstrip("/")
                        # SSRF guard — matches the pattern used by webhook_routes,
                        # CalDAV, search, and embeddings. Blocks link-local / metadata
                        # addresses (169.254.x.x) by default; set
                        # REMINDER_WEBHOOK_BLOCK_PRIVATE_IPS=true to also block
                        # RFC-1918 ranges for locked-down deployments.
                        import os as _os
                        from src.url_safety import check_outbound_url as _chk
                        _block = _os.getenv("REMINDER_WEBHOOK_BLOCK_PRIVATE_IPS", "false").lower() == "true"
                        _ok, _reason = _chk(url, block_private=_block)
                        if not _ok:
                            webhook_error = f"Webhook URL rejected: {_reason}"
                        else:
                            async with httpx.AsyncClient(timeout=10.0) as client:
                                resp = await client.post(url, content=rendered.encode(), headers=hdrs)
                                webhook_sent = resp.is_success
                                if not webhook_sent:
                                    webhook_error = f"Webhook returned HTTP {resp.status_code}"
        except Exception as e:
            webhook_error = str(e) or e.__class__.__name__
            logger.warning(f"Reminder webhook send failed: {e}")

    ntfy_sent = False
    ntfy_error = ""
    if channel == "ntfy":
        try:
            from src.integrations import load_integrations
            import httpx
            intg = next(
                (i for i in load_integrations()
                 if i.get("preset") == "ntfy" and i.get("enabled", True) and i.get("base_url")),
                None,
            )
            if intg:
                base = intg["base_url"].rstrip("/")
                topic = settings.get("reminder_ntfy_topic") or "reminders"
                ntfy_body = synthesis or note_body or title
                # ntfy Title is an ASCII HTTP header; sanitize Unicode and cap its length.
                _clean_title = (title or "Reminder").encode("ascii", "replace").decode("ascii")[:200]
                hdrs = {"Title": _clean_title, "Priority": "high", "Tags": "bell"}
                api_key = intg.get("api_key", "")
                if api_key:
                    hdrs["Authorization"] = f"Bearer {api_key}"
                # SSRF guard — same check (and env knob) as the webhook branch
                # above: link-local / metadata addresses are always rejected;
                # REMINDER_WEBHOOK_BLOCK_PRIVATE_IPS=true also blocks RFC-1918
                # so a ntfy base_url can't be pointed at internal services.
                import os as _os
                from src.url_safety import check_outbound_url as _chk
                _block = _os.getenv("REMINDER_WEBHOOK_BLOCK_PRIVATE_IPS", "false").lower() == "true"
                _ok, _reason = _chk(f"{base}/{topic}", block_private=_block)
                if not _ok:
                    ntfy_error = f"ntfy URL rejected: {_reason}"
                else:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        resp = await client.post(f"{base}/{topic}", content=ntfy_body, headers=hdrs)
                        ntfy_sent = resp.is_success
                        if not ntfy_sent:
                            ntfy_error = f"ntfy returned HTTP {resp.status_code}"
            else:
                ntfy_error = "No enabled ntfy integration"
        except Exception as e:
            ntfy_error = str(e) or e.__class__.__name__
            logger.warning(f"Reminder ntfy send failed: {e}")

    # In-app browser notification ALWAYS fires (regardless of channel). The
    # frontend polls `/api/tasks/notifications` and turns any entry with a
    # `body` into a real `Notification(...)` — same surface as task-success
    # popups. Lets the user see reminders inside the app even when the
    # primary channel is email/ntfy and the tab is open.
    browser_sent = False
    local_browser_sent = (not queue_browser and channel == "browser")
    if queue_browser and _scheduler_ref is not None:
        try:
            _scheduler_ref.add_notification(
                task_name=title or "Reminder",
                status="success",
                task_id=f"reminder-{note_id}",
                owner=owner or None,
                body=(synthesis or note_body or title or "").strip()[:500] or "Reminder",
            )
            browser_sent = True
        except Exception as _e:
            logger.debug(f"dispatch_reminder: in-app notif push failed: {_e}")

    # Dedupe across paths: write to the same cache file `action_ping_notes`
    # reads, so the background scanner's REPING_MIN window suppresses a
    # second send for the same note within 25 min. Without this, a note
    # whose due_date fires while the user has the app open got TWO emails
    # (frontend-fired here + background-fired by ping_notes 0–5 min later).
    if (email_sent or ntfy_sent or webhook_sent or browser_sent or local_browser_sent) and note_id:
        try:
            import json as _json
            from datetime import datetime as _dt, timezone as _tz
            from pathlib import Path as _P
            # Per-owner cache so the scanner's prune step on user A's run
            # doesn't drop user B's just-fired entry (review C4).
            _STATE = cache_path
            if _STATE is None:
                _slug = "".join(c if (c.isalnum() or c in "-_.@") else "_" for c in (owner or "default"))
                _STATE = _P(DATA_DIR) / f"note_pings_{_slug}.json"
            _STATE.parent.mkdir(parents=True, exist_ok=True)
            try:
                _cache = cache or (_json.loads(_STATE.read_text(encoding="utf-8")) if _STATE.exists() else {})
            except Exception:
                _cache = {}
            sent_channel = "email" if email_sent else "ntfy" if ntfy_sent else "webhook" if webhook_sent else "browser"
            _cache[cache_key or str(note_id)] = {
                "at": _dt.now(_tz.utc).isoformat(),
                "channel": sent_channel,
            }
            _STATE.write_text(_json.dumps(_cache), encoding="utf-8")
        except Exception as _e:
            logger.debug(f"dispatch_reminder: cache write failed: {_e}")

    return {
        "channel": channel,
        "synthesis": synthesis,
        "email_sent": email_sent,
        "email_error": email_error,
        "ntfy_sent": ntfy_sent,
        "ntfy_error": ntfy_error,
        "webhook_sent": webhook_sent,
        "webhook_error": webhook_error,
        "browser_sent": browser_sent or local_browser_sent,
    }


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def setup_note_routes(task_scheduler=None, upload_handler=None):
    # Expose the scheduler to module-level `dispatch_reminder` so reminders
    # can also push to the in-app notification queue (the polling system
    # turns each entry into a real browser Notification + the existing
    # tasks-tab badge / dot system).
    global _scheduler_ref
    _scheduler_ref = task_scheduler

    router = APIRouter(prefix="/api/notes", tags=["notes"])

    def _owner(request: Request) -> Optional[str]:
        # require_user, not bare get_current_user: a request that reaches
        # these owner-scoped routes with NO identity (auth-middleware
        # regression, SSRF from a sibling service) must fail closed (401)
        # when auth is configured — not be treated as the single-user mode
        # and handed blanket access to every account's notes. The documented
        # anonymous modes (AUTH_ENABLED=false, LOCALHOST_BYPASS on loopback,
        # unconfigured first-run) still resolve to None, the single-user
        # path. fire_reminder below already gated this way; the CRUD routes
        # did not.
        return require_user(request) or None

    def _checked_color(value):
        try:
            return normalize_note_color(value)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    def _is_admin_or_single_user(request: Request, user: str | None) -> bool:
        if user == INTERNAL_TOOL_USER:
            return True
        if not user:
            # require_user() already admitted this request, which only happens
            # for auth-disabled, loopback-bypass, or unconfigured single-user
            # modes. There is no separate non-admin account boundary there.
            return True
        try:
            from core.auth import AuthManager
            auth_mgr = getattr(request.app.state, "auth_manager", None) or AuthManager()
            if not getattr(auth_mgr, "is_configured", True):
                return True
            return bool(auth_mgr.is_admin(user))
        except Exception:
            return False

    def _note_upload_refs(note: Note) -> set:
        """Uploads the stored note references: what the media route serves."""
        refs = extract_internal_upload_ids(effective_content(note.content, note.items, note.image_url) or "")
        refs |= extract_internal_upload_ids(note.image_url or "")
        refs |= extract_internal_upload_ids(note.color or "")
        return refs

    def _reserve_new_note_uploads(owner: Optional[str], before: set, note: Note) -> None:
        # Reserve from the note as it will be stored, not from the request
        # fields: the stored content is what the media route serves from, so
        # the two cannot disagree about which uploads the note references.
        # Only uploads this write introduces are reserved for the writer. The
        # ones already in the note were reserved when they were inserted, and
        # on a shared note they can belong to the owner or another
        # collaborator, so re-reserving them as the writer would refuse.
        missing_id = reserve_upload_ids(upload_handler, owner, _note_upload_refs(note) - before)
        if missing_id:
            raise HTTPException(409, f"Referenced upload is no longer available: {missing_id}")

    def _reserve_restored_uploads(user: Optional[str], before: set, note: Note) -> None:
        # A version holds only what the note once stored, so each upload a
        # restore brings back was reserved when someone who could edit the
        # note put it there, and on a shared note that can be a collaborator.
        # Reserve it for the account that owns it: reserving it as the
        # restoring owner would refuse a collaborator's image. One deleted
        # since has no owner to look up and is still refused.
        for upload_id in sorted(_note_upload_refs(note) - before):
            info = upload_handler.get_upload_info(upload_id) if upload_handler is not None else None
            owner = info.get("owner") if info else user
            if reserve_upload_ids(upload_handler, owner, {upload_id}):
                raise HTTPException(409, f"Referenced upload is no longer available: {upload_id}")

    def _load_note(db, note_id: str, user: Optional[str], need: str) -> tuple:
        """The note and the caller's capabilities, or the matching error.

        No read access is the same 404 as a missing note, so the route is not
        an existence oracle. A collaborator who can see the note but lacks
        `need` gets 403.
        """
        note = db.query(Note).filter(Note.id == note_id).first()
        if not note:
            raise HTTPException(404, "Note not found")
        caps = _note_access(user, note, db)
        if "read" not in caps:
            raise HTTPException(404, "Note not found")
        if need not in caps:
            raise HTTPException(403, "Only the note's owner can do that" if need in ("manage", "share")
                                else "This note is shared with you as view only")
        return note, caps

    def _sharing_user(request: Request) -> str:
        user = sharing.normalize_user(require_privilege(request, "can_share_notes"))
        if not user:
            raise HTTPException(400, "Sharing needs signed-in accounts")
        return user

    # --- LIST ---
    @router.get("")
    def list_notes(
        request: Request,
        archived: Optional[bool] = None,
        label: Optional[str] = None,
    ):
        user = _owner(request)
        db = SessionLocal()
        try:
            q = db.query(Note)
            if user is not None:
                q = q.filter(Note.owner == user)
            if archived is not None:
                q = q.filter(Note.archived == archived)
            else:
                q = q.filter(Note.archived == False)
            if label:
                q = q.filter(Note.label == label)
            # Archived view: most recently archived first. Active view: pin + manual order.
            if archived is True:
                notes = q.order_by(Note.updated_at.desc()).all()
            else:
                notes = q.order_by(Note.pinned.desc(), Note.sort_order.asc(), Note.updated_at.desc()).all()
            # Notes shared with me follow, in the active view only: archiving
            # is the owner's filing, not something a collaborator browses.
            if user is not None and archived is not True:
                sq = sharing.shared_with_me_query(db, user).filter(Note.archived == False)  # noqa: E712
                if label:
                    sq = sq.filter(Note.label == label)
                notes += sq.order_by(Note.updated_at.desc()).all()
            grants = sharing.shares_by_note(db, [n.id for n in notes])
            counts = version_counts(db, [n.id for n in notes])
            payload = {
                "notes": [_note_to_dict(n, user, grants.get(n.id), counts.get(n.id, 0)) for n in notes],
                "me": user,
            }
        finally:
            db.close()
        return _conditional_json(request, payload)

    # --- CREATE ---
    @router.post("")
    def create_note(request: Request, body: NoteCreate):
        user = _owner(request)
        body.color = _checked_color(body.color)
        # Checklist items and the legacy single image are written into the
        # markdown content. Keep the identity of a legacy attachment.
        content = body.content
        if body.items is not None:
            content = apply_items_to_content(content, body.items)
        if body.image_url:
            content = fold_image_url(content, body.image_url)
        _refuse_oversize({
            "title": body.title, "content": content, "label": body.label, "color": body.color,
            "due_date": body.due_date, "repeat": body.repeat, "note_type": body.note_type,
            "source": body.source, "session_id": body.session_id, "gallery_id": body.gallery_id,
        })
        db = SessionLocal()
        try:
            note = Note(
                id=str(uuid.uuid4()),
                owner=user,
                title=body.title,
                content=content,
                items=None,
                note_type=body.note_type,
                color=body.color,
                label=body.label,
                pinned=body.pinned,
                due_date=body.due_date,
                source=body.source,
                session_id=body.session_id,
                image_url=body.image_url if (body.image_url or "").strip() else None,
                gallery_id=body.gallery_id,
                repeat=body.repeat or "none",
                sort_order=body.sort_order if body.sort_order is not None else 0,
            )
            _reserve_new_note_uploads(user, set(), note)
            db.add(note)
            db.flush()
            # Read before the commit, so the payload (and its body revision)
            # is the note this request wrote, not a later write's.
            result = _note_to_dict(note, user)
            db.commit()
            return result
        finally:
            db.close()

    # --- SHARE TARGETS ---
    # Declared before GET /{note_id} so the literal path is not taken as an id.
    # Usernames only, and only for accounts allowed to share. The list of
    # accounts is an admin's: everyone else shares by typing the exact
    # username (`directory: false` tells the popover to show that field).
    @router.get("/share-targets")
    def share_targets(request: Request):
        user = sharing.normalize_user(require_privilege(request, "can_share_notes"))
        if not user or not _is_admin_or_single_user(request, user):
            return {"users": [], "directory": False}
        names = sharing.known_usernames(getattr(request.app.state, "auth_manager", None)) or set()
        return {"users": [{"username": n} for n in sorted(names) if n != user], "directory": True}

    # --- GET ONE ---
    @router.get("/{note_id}")
    def get_note(request: Request, note_id: str):
        user = _owner(request)
        db = SessionLocal()
        try:
            note, _caps = _load_note(db, note_id, user, "read")
            return _note_dict_for(db, note, user)
        finally:
            db.close()

    # --- UPDATE ---
    @router.put("/{note_id}")
    def update_note(request: Request, note_id: str, body: NoteUpdate):
        """Change a note. A body write carries `body_revision` or `base_updated_at`.

        A write to the body (`content`, `items`, `image_url`, `note_type`)
        without either token is a 409 carrying the current note, so a client
        can't overwrite text it never saw. A stale token of either kind is
        the same 409. `body_revision` tolerates metadata changes made in the
        meantime (pin, colour, order); `base_updated_at` asks for exactly the
        version the client showed.
        """
        user = _owner(request)
        supplied = body.model_fields_set
        if body.replace_content and "content" not in supplied:
            raise HTTPException(422, "replace_content requires content")
        # Only when sent: assigning a field marks it as sent, and the
        # owner-only check below goes by the fields a request names.
        if "color" in supplied:
            body.color = _checked_color(body.color)
        # A write that lost a race is re-applied to the newer row when it has
        # no base: one with `body_revision` re-checks that the body is still
        # the one it saw. One with `base_updated_at` asked for exactly that
        # version, so it gets the 409 the client handles.
        for _attempt in range(NOTE_WRITE_ATTEMPTS):
            try:
                return _update_note_once(user, note_id, body)
            except NoteChanged:
                if body.base_updated_at is not None:
                    break
        db = SessionLocal()
        try:
            note, _caps = _load_note(db, note_id, user, "read")
            raise _note_conflict(db, note, user)
        finally:
            db.close()

    def _stale_body_token(note: Note, base_updated_at: Optional[str], body_revision: Optional[str],
                          required: bool) -> bool:
        """Whether a write's concurrency token is missing (when `required`) or stale."""
        if required and base_updated_at is None and body_revision is None:
            return True
        current = note.updated_at.isoformat() if note.updated_at else None
        if base_updated_at is not None and base_updated_at != current:
            return True
        return body_revision is not None and body_revision != note_body_revision(note)

    def _guard_body(db, note: Note, expected: tuple) -> bool:
        # Looked up at call time, so a test can pause or fail the guard.
        return guard_note_body(db, note, expected)

    def _update_note_once(user: Optional[str], note_id: str, body: NoteUpdate) -> Dict[str, Any]:
        db = SessionLocal()
        try:
            supplied = body.model_fields_set
            # Read the stamp first: a write landing before the row is loaded
            # then fails the compare instead of being overwritten.
            stamp = stored_stamp(db, note_id)
            note, caps = _load_note(db, note_id, user, "write")
            # Pin, archive, order, colour, tag, reminder and the agent-session
            # link are the owner's. The dedicated /pin and /archive routes are
            # owner-only, and this generic route must not be a side door.
            if "manage" not in caps:
                blocked = sharing.owner_only_fields_in(supplied)
                if blocked:
                    raise HTTPException(403, f"Only the note's owner can change: {', '.join(blocked)}")
            body_write = bool(supplied & {"content", "items", "image_url"})
            guarded = body_write or body.note_type is not None or body.body_revision is not None
            if _stale_body_token(note, body.base_updated_at, body.body_revision, body_write or body.note_type is not None):
                raise _note_conflict(db, note, user)
            expected_body = body_snapshot(note)

            refs_before = _note_upload_refs(note)
            before = snapshot(note)
            sizes_before = sized_fields(note)
            if body.title is not None:
                note.title = body.title
            # Omitted fields preserve their values; explicit null clears the
            # nullable fields used by the editor's removal controls.
            if body_write:
                _fold_legacy_columns(note)
            # The note window sends the whole document, flagged by
            # `replace_content` (or, from older windows, by `base_updated_at`):
            # its text is taken as written, tasks and attachment line included.
            replace = body.replace_content or ("content" in supplied and body.base_updated_at is not None)
            if "content" in supplied:
                if not replace and "items" not in supplied:
                    # Text on its own (an agent or API client that edited the
                    # prose) keeps the task lines.
                    note.content = keep_task_lines(note.content, body.content) or body.content
                else:
                    note.content = body.content
                if replace and "image_url" not in supplied:
                    if not has_legacy_image(note.content, note.image_url):
                        note.image_url = None
                    note.items = None
            # Writers that still send `items` (card edits, older API clients)
            # rewrite the task lines in place.
            if "items" in supplied:
                if note.content is not None or body.items:
                    note.content = apply_items_to_content(note.content, body.items or [])
                note.items = None
            # `image_url` names the legacy attachment, kept as a line in
            # content; a re-saved drawing replaces its image instead of stacking.
            if "image_url" in supplied:
                _write_image_url(note, body.image_url)
            elif "content" in supplied and note.image_url:
                note.content = fold_image_url(note.content, note.image_url)
            if body.note_type is not None:
                note.note_type = body.note_type
            if "color" in supplied:
                note.color = body.color
            if "label" in supplied:
                note.label = body.label
            if body.pinned is not None:
                note.pinned = body.pinned
            if body.archived is not None:
                note.archived = body.archived
            if "due_date" in supplied:
                note.due_date = body.due_date
            if "gallery_id" in supplied:
                note.gallery_id = body.gallery_id
            if body.repeat is not None:
                note.repeat = body.repeat
            if body.sort_order is not None:
                note.sort_order = body.sort_order
            if "agent_session_id" in supplied:
                note.agent_session_id = body.agent_session_id

            _refuse_oversize(sized_fields(note), sizes_before)
            _reserve_new_note_uploads(user, refs_before, note)
            record_version(db, note, before, SOURCE_USER, author=user)
            # Right before the compare-and-swap: the guard takes the write lock.
            if guarded and not _guard_body(db, note, expected_body):
                db.rollback()
                raise NoteChanged(note_id)
            commit_if_unchanged(db, note, stamp)
            db.refresh(note)
            return _note_dict_for(db, note, user)
        finally:
            db.close()

    # --- UNDO THE LAST AGENT EDIT ---
    @router.post("/{note_id}/agent-undo")
    def undo_agent_edit(request: Request, note_id: str):
        """Put back the note as it was before its last agent edit (core.note_agent_scope.UNDO_FIELDS).

        Only while the note still holds exactly what the agent wrote: after
        anyone else's change it answers 409 with the note as it is, and
        nothing is undone. Owner only, like the runs themselves.
        """
        user = _owner(request)
        db = SessionLocal()
        try:
            stamp = stored_stamp(db, note_id)
            note, _caps = _load_note(db, note_id, user, "manage")
            undo = parse_undo(note.agent_undo)
            if not undo:
                raise HTTPException(404, "No agent edit to undo")
            if not undo_available(note, undo):
                raise HTTPException(409, detail={"message": "The note changed after the agent's edit",
                                                 "note": _note_dict_for(db, note, user)})
            before = snapshot(note)
            for field, value in undo["before"].items():
                if field in UNDO_FIELDS:
                    setattr(note, field, value)
            note.agent_undo = None
            # What the agent wrote stays in the history, so the Undo can be undone.
            record_version(db, note, before, SOURCE_RESTORE, author=user)
            try:
                commit_if_unchanged(db, note, stamp)
            except NoteChanged:
                db.expire_all()
                note, _caps = _load_note(db, note_id, user, "read")
                raise _note_conflict(db, note, user)
            db.refresh(note)
            return _note_dict_for(db, note, user)
        finally:
            db.close()

    # --- VERSION HISTORY (core.note_versions) ---
    @router.get("/{note_id}/versions")
    def list_note_versions(request: Request, note_id: str):
        """The note's earlier versions, newest first, without their full text. Owner only."""
        user = _owner(request)
        db = SessionLocal()
        try:
            note, _caps = _load_note(db, note_id, user, "manage")
            return {"versions": history_views(db, note, user)}
        finally:
            db.close()

    @router.get("/{note_id}/versions/{version_id}")
    def get_note_version(request: Request, note_id: str, version_id: int):
        user = _owner(request)
        db = SessionLocal()
        try:
            note, _caps = _load_note(db, note_id, user, "manage")
            row = get_version(db, note.id, version_id)
            if row is None:
                raise HTTPException(404, "Version not found")
            # `after`: the note as this change left it, so the history shows the change itself.
            return {**version_view(row, note, full=True), "after": version_after(db, note, row)}
        finally:
            db.close()

    @router.post("/{note_id}/versions/{version_id}/restore")
    def restore_note_version(request: Request, note_id: str, version_id: int,
                             body: Optional[VersionRestore] = Body(default=None)):
        """Put a version back. The state it replaces becomes a version, so this can be undone too.

        With `base_updated_at` (or `body_revision`), only onto that version of the note; a note
        that changed meanwhile (then, or while restoring) is a 409 carrying
        the note as it is, and nothing is restored. An upload the version
        shows that has since been deleted is a 409 as well.
        """
        user = _owner(request)
        db = SessionLocal()
        try:
            stamp = stored_stamp(db, note_id)
            note, _caps = _load_note(db, note_id, user, "manage")
            row = get_version(db, note.id, version_id)
            if row is None:
                raise HTTPException(404, "Version not found")
            if body is not None and _stale_body_token(note, body.base_updated_at, body.body_revision, False):
                raise _note_conflict(db, note, user)
            refs_before = _note_upload_refs(note)
            before = snapshot(note)
            apply_version(note, row)
            _reserve_restored_uploads(user, refs_before, note)
            kept = record_version(db, note, before, SOURCE_RESTORE, author=user)
            try:
                commit_if_unchanged(db, note, stamp)
            except NoteChanged:
                db.expire_all()
                note, _caps = _load_note(db, note_id, user, "read")
                raise _note_conflict(db, note, user)
            db.refresh(note)
            # The note, plus the version this restore kept of what it replaced:
            # restoring that one undoes the restore.
            return {**_note_dict_for(db, note, user), "undo_version_id": kept.id if kept is not None else None}
        finally:
            db.close()

    # --- DELETE ---
    @router.delete("/{note_id}")
    def delete_note(request: Request, note_id: str):
        user = _owner(request)
        db = SessionLocal()
        try:
            # Owner only. A collaborator leaves through DELETE /{id}/share/{me}.
            note, _caps = _load_note(db, note_id, user, "manage")
            sharing.delete_note_shares(db, note.id)
            delete_note_versions(db, [note.id])
            db.delete(note)
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    # --- TOGGLE PIN ---
    @router.post("/{note_id}/pin")
    def toggle_pin(request: Request, note_id: str):
        user = _owner(request)
        db = SessionLocal()
        try:
            note, _caps = _load_note(db, note_id, user, "manage")
            note.pinned = not note.pinned
            db.commit()
            return {"ok": True, "pinned": note.pinned}
        finally:
            db.close()

    # --- TOGGLE ARCHIVE ---
    @router.post("/{note_id}/archive")
    def toggle_archive(request: Request, note_id: str):
        user = _owner(request)
        db = SessionLocal()
        try:
            note, _caps = _load_note(db, note_id, user, "manage")
            note.archived = not note.archived
            db.commit()
            return {"ok": True, "archived": note.archived}
        finally:
            db.close()

    # --- TOGGLE CHECKLIST ITEM ---
    @router.post("/{note_id}/items/{index}/toggle")
    def toggle_item(request: Request, note_id: str, index: int, body: Optional[TaskToggle] = Body(default=None)):
        """Flip one task. The body carries `body_revision` or `base_updated_at`.

        Without either token, or with a stale one, the answer is a 409
        carrying the current note: a positional flip computed from another
        version of the checklist could tick the wrong task. `text` is the
        task's text as the client saw it; a task that is gone (or now
        ambiguous) is the same 409. With `body_revision` the flip is
        re-applied when a metadata-only write lands first. With `done`, the
        task is set to that state rather than flipped, so a tick from a view
        that is behind can't undo the same tick made elsewhere.
        """
        user = _owner(request)
        wanted = body.text if body else None
        db = SessionLocal()
        try:
            def load():
                return _load_note(db, note_id, user, "write")[0]

            def flip(note):
                if _stale_body_token(note, body.base_updated_at if body else None,
                                     body.body_revision if body else None, True):
                    raise _note_conflict(db, note, user)
                before = snapshot(note)
                _fold_legacy_columns(note)
                if not parse_task_lines(note.content):
                    raise HTTPException(400, "Note has no checklist items")
                target = find_task_index(note.content, index, wanted)
                if target is None:
                    raise _note_conflict(db, note, user, "That checklist item changed elsewhere")
                try:
                    note.content, item = toggle_task(note.content, target, body.done if body else None)
                except IndexError:
                    raise HTTPException(400, f"Item index {index} out of range")
                record_version(db, note, before, SOURCE_USER, author=user)
                return item

            try:
                note, item = write_with_retry(db, note_id, load, flip, guard=_guard_body)
            except NoteChanged:
                note = load()
                raise _note_conflict(db, note, user)
            data = _note_dict_for(db, note, user)
            return {"ok": True, "item": item, "items": data["items"], "note": data}
        finally:
            db.close()

    # --- APPEND CHECKLIST ITEM ---
    @router.post("/{note_id}/items")
    def append_item(request: Request, note_id: str, body: TaskAppend):
        """Append one open task line server-side.

        Quick-add used to PUT the whole checklist, which raced any other
        write to the same note. This reads the stored content and adds the
        line in one transaction. An append can't overwrite anything, so it
        takes no token: a lost race re-reads the row and appends again.
        """
        user = _owner(request)
        text = " ".join((body.text or "").split())
        if not text:
            raise HTTPException(400, "text required")
        db = SessionLocal()
        try:
            def load():
                return _load_note(db, note_id, user, "write")[0]

            def add(note):
                refs_before = _note_upload_refs(note)
                before = snapshot(note)
                _fold_legacy_columns(note)
                note.content, item = append_task_line(note.content, text, body.indent or 0)
                _refuse_oversize({"content": note.content}, before)
                _reserve_new_note_uploads(user, refs_before, note)
                record_version(db, note, before, SOURCE_USER, author=user)
                return item

            try:
                note, item = write_with_retry(db, note_id, load, add, guard=_guard_body)
            except NoteChanged:
                note = load()
                raise _note_conflict(db, note, user)
            return {"ok": True, "item": item, "note": _note_dict_for(db, note, user)}
        finally:
            db.close()

    # --- SHARING ---
    @router.get("/{note_id}/shares")
    def list_shares(request: Request, note_id: str):
        user = _owner(request)
        db = SessionLocal()
        try:
            note, _caps = _load_note(db, note_id, user, "share")
            grants = sharing.shares_by_note(db, [note.id]).get(note.id, [])
            return {"shared_with": sharing.roster(grants)}
        finally:
            db.close()

    @router.post("/{note_id}/share")
    def share_note(request: Request, note_id: str, body: NoteShareRequest):
        """Grant `view` or `edit` to accounts on this instance (owner only).

        Upserts: sharing again with another permission flips it. Names are
        matched case-insensitively. A name with no account refuses the whole
        request with 404 and writes nothing; the owner and blanks are skipped
        and reported back.
        """
        user = _sharing_user(request)
        permission = sharing.normalize_permission(body.permission)
        if permission is None:
            raise HTTPException(400, "permission must be 'view' or 'edit'")
        db = SessionLocal()
        try:
            note, _caps = _load_note(db, note_id, user, "share")
            valid = sharing.known_usernames(getattr(request.app.state, "auth_manager", None))
            if valid is None:
                raise HTTPException(503, "Authentication service unavailable")
            requested = [sharing.normalize_user(u) for u in (body.users or []) if isinstance(u, str)]
            # Without the account list, a person types the name, so a typo
            # has to say so rather than look shared. One message for every
            # unknown name.
            if any(name and name not in valid for name in requested):
                raise HTTPException(404, sharing.NO_SUCH_ACCOUNT)
            written = sharing.upsert_shares(db, note, requested, permission, valid)
            db.commit()
            grants = sharing.shares_by_note(db, [note.id]).get(note.id, [])
            return {
                "ok": True,
                "shared": written,
                "skipped": [u for u in requested if u and u not in written],
                "shared_with": sharing.roster(grants),
            }
        finally:
            db.close()

    @router.delete("/{note_id}/share/{username}")
    def unshare_note(request: Request, note_id: str, username: str):
        """The owner removes anyone; a collaborator can only remove themselves."""
        user = _owner(request)
        target = sharing.normalize_user(username)
        db = SessionLocal()
        try:
            note, caps = _load_note(db, note_id, user, "read")
            leaving = bool(user) and target == sharing.normalize_user(user) and "share" not in caps
            if "share" not in caps and not leaving:
                raise HTTPException(403, "Only the note's owner can remove other people")
            sharing.remove_share(db, note.id, target)
            db.commit()
            return {"ok": True, "left": leaving}
        finally:
            db.close()

    # --- NOTE-SCOPED MEDIA ---
    @router.get("/{note_id}/media/{upload_id}")
    def note_media(request: Request, note_id: str, upload_id: str):
        """An image in a note, for anyone who can read the note.

        `/api/upload/{id}` is owner-only, so on a shared note the owner's and
        each collaborator's uploads would break for the others. This serves an
        upload only when the note's content references it and the upload
        belongs to the note's owner or a current collaborator, so read access
        to a note never reaches an upload the note does not show, nor one a
        stranger's reference points at. An image a collaborator added stops
        loading here once they leave the note or are removed from it.
        """
        user = _owner(request)
        if upload_handler is None or not upload_handler.validate_upload_id(upload_id):
            raise HTTPException(404, "File not found")
        db = SessionLocal()
        try:
            note, _caps = _load_note(db, note_id, user, "read")
            referenced = extract_internal_upload_ids(effective_content(note.content, note.items, note.image_url) or "")
            referenced |= extract_internal_upload_ids(note.image_url or "")
            # Whose uploads this note may show: its owner's and its current
            # collaborators'. A reference alone is not enough, because not
            # every writer of note content reserves what it references.
            allowed_owners = {sharing.normalize_user(note.owner)}
            allowed_owners |= {
                sharing.normalize_user(share.shared_with)
                for share in sharing.shares_by_note(db, [note.id]).get(note.id, [])
            }
        finally:
            db.close()
        if upload_id not in referenced:
            raise HTTPException(404, "File not found")
        from routes.upload_routes import UPLOAD_RESPONSE_HEADERS, resolve_upload_file_path
        from fastapi.responses import FileResponse
        info = upload_handler.get_upload_info(upload_id) or {}
        if not info or sharing.normalize_user(info.get("owner")) not in allowed_owners:
            raise HTTPException(404, "File not found")
        path = resolve_upload_file_path(upload_handler, upload_id)
        import mimetypes as _mt
        mime = str(info.get("mime") or _mt.guess_type(path)[0] or "").split(";")[0].strip().lower()
        # Notes show uploads as images only. Any other type is refused rather
        # than served inline from this origin.
        headers = dict(UPLOAD_RESPONSE_HEADERS)
        if mime in NOTE_MEDIA_ATTACHMENT_TYPES:
            headers["Content-Disposition"] = "attachment"
        elif mime not in NOTE_MEDIA_INLINE_TYPES:
            raise HTTPException(404, "File not found")
        return FileResponse(path, media_type=mime, headers=headers)

    # --- FIRE REMINDER ---
    @router.post("/fire-reminder")
    async def fire_reminder(request: Request):
        """Dispatch a reminder according to user settings.

        Called by the frontend when a reminder fires. Optionally generates an
        LLM synthesis line and/or sends an email through configured SMTP.
        Returns {synthesis, email_sent}.
        """
        # Gate against anonymous callers — LLM synthesis can burn tokens.
        user = require_user(request)
        body = await request.json()
        note_id = str(body.get("note_id") or "").strip()
        if not note_id:
            raise HTTPException(400, "note_id required")

        caller = _owner(request)
        is_test = note_id.startswith("test-")
        is_admin = _is_admin_or_single_user(request, user or caller)
        _override: dict = {}
        if is_test:
            if not is_admin:
                raise HTTPException(403, "Admin only")
            title = (body.get("title") or "Test Reminder").strip() or "Test Reminder"
            note_body = (body.get("body") or "").strip()
            # Optional overrides let the admin settings test button pass the
            # current UI values directly so it never races a pending save.
            if body.get("channel"):
                _override["reminder_channel"] = body["channel"]
            if body.get("webhook_integration_id"):
                _override["reminder_webhook_integration_id"] = body["webhook_integration_id"]
            if body.get("webhook_payload_template"):
                _override["reminder_webhook_payload_template"] = body["webhook_payload_template"]
            # Mirror the in-UI AI Synthesis toggle + persona so the test
            # actually exercises the synthesis path before/without a Save.
            if "llm_synthesis" in body:
                _override["reminder_llm_synthesis"] = bool(body["llm_synthesis"])
            if "llm_persona" in body:
                _override["reminder_llm_persona"] = str(body["llm_persona"] or "")
        else:
            db = SessionLocal()
            try:
                note = db.query(Note).filter(Note.id == note_id).first()
                if not note:
                    raise HTTPException(404, "Note not found")
                if caller is not None and note.owner != caller:
                    raise HTTPException(404, "Note not found")
                title, note_body = _reminder_text_from_note(note)
            finally:
                db.close()

        return await dispatch_reminder(
            title=title, note_body=note_body, note_id=note_id,
            owner=caller or "",
            queue_browser=False,
            settings_override=_override or None,
        )

    # --- REORDER NOTES ---
    @router.post("/reorder")
    async def reorder_notes(request: Request):
        """Update sort_order for a list of note IDs in the order provided."""
        user = _owner(request)
        body = await request.json()
        ids = body.get("ids", [])
        if not isinstance(ids, list):
            raise HTTPException(400, "ids must be a list")
        # v2 review HIGH-12: drop the legacy `(owner == user) | (owner ==
        # None)` OR which let an authenticated user silently reorder
        # every legacy-null-owner note belonging to other accounts. In
        # an unconfigured (single-user) auth deploy the OR is still safe
        # because there's no second user to attack; we keep that branch
        # explicit and gated on AuthManager.is_configured.
        try:
            from core.auth import AuthManager
            _allow_null = not AuthManager().is_configured
        except Exception:
            _allow_null = False
        db = SessionLocal()
        try:
            for i, nid in enumerate(ids):
                q = db.query(Note).filter(Note.id == nid)
                if user is not None:
                    if _allow_null:
                        q = q.filter((Note.owner == user) | (Note.owner == None))  # noqa: E711
                    else:
                        q = q.filter(Note.owner == user)
                note = q.first()
                if note:
                    note.sort_order = i
            db.commit()
            return {"ok": True, "count": len(ids)}
        finally:
            db.close()

    return router
