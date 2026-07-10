"""
email_routes.py

FastAPI route handlers for the email feature. All non-route logic
(IMAP connection helpers, message parsing, account config, the
auto-summarize + scheduled-email pollers, Pydantic models) lives in:

    routes/email_helpers.py   — synchronous helpers + models + constants
    routes/email_pollers.py   — background loops, started by `_start_poller`

Importing from the helpers module brings in everything those route
handlers need. The split is mechanical — no behavior change.
"""

import asyncio
import os
import sqlite3 as _sql3
import time
import email as email_mod
import email.header
import email.utils
import smtplib
import json
import re
import html
import io
import zipfile
from html.parser import HTMLParser as _HTMLParser
import logging
import uuid
from datetime import datetime
from pathlib import Path

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from fastapi import APIRouter, Query, UploadFile, File, BackgroundTasks, HTTPException, Depends, Request
from fastapi.responses import FileResponse, StreamingResponse
from src.constants import DATA_DIR

from src.llm_core import llm_call_async
from src.upload_limits import read_upload_limited, EMAIL_COMPOSE_UPLOAD_MAX_BYTES

from routes.email_helpers import (
    _strip_think, _extract_reply, _apply_email_style_mechanics, require_owner, require_user, _assert_owns_account,
    _q, _attach_compose_uploads, _cleanup_compose_uploads,
    _load_settings, _save_settings, _get_email_config,
    _send_smtp_message, _smtp_security_mode,
    _IMAP_TIMEOUT_SECONDS, _open_imap_connection, _normalize_imap_starttls,
    make_oauth_state, verify_oauth_state,
    EmailNotConfiguredError,
    _imap_connect, _imap, _decode_header, _detect_sent_folder, _detect_drafts_folder,
    _extract_attachment_text, _list_attachments_from_msg, _has_visible_attachments, _is_likely_signature_image_attachment,
    _extract_attachment_to_disk, _extract_html, _extract_text,
    _fetch_sender_thread_context, _pre_retrieve_context,
    _EMAIL_REPLY_SYS_PROMPT_BASE, _POOL_HOOKS,
    _friendly_email_auth_error,
    SendEmailRequest, ExtractStyleRequest,
    ATTACHMENTS_DIR, COMPOSE_UPLOADS_DIR, SCHEDULED_DB,
    attachment_extract_dir, _email_cache_owner_clause, email_translation_body_hash,
)
from routes.email_pollers import _start_poller

logger = logging.getLogger(__name__)

ODYSSEUS_MAIL_ORIGIN = "odysseus-ui"
EMAIL_READ_ATTACHMENT_VERSION = 2



def _safe_attachment_zip_name(name: str, fallback: str) -> str:
    """Return a zip entry filename without path traversal or empty names."""
    base = Path(str(name or "")).name.strip() or fallback
    base = re.sub(r"[\x00-\x1f\x7f]+", "_", base)
    base = base.replace("/", "_").replace("\\", "_").strip(". ") or fallback
    return base[:180] or fallback


def _coerce_port(value, default):
    """Coerce a user-supplied port to int.

    Returns ``(port, error)``. A missing or blank value yields ``default``; a
    non-numeric value yields ``(None, message)`` so callers can return a clean
    error instead of letting ``int()`` raise and surface as an HTTP 500.
    """
    if value in (None, ""):
        return default, None
    try:
        return int(value), None
    except (TypeError, ValueError):
        return None, f"Invalid port {value!r}; must be a whole number"


def _email_tag_owner_aliases(account_id: str | None, owner: str = "") -> list[str]:
    aliases = [owner or ""]
    try:
        from core.database import SessionLocal as _SL, EmailAccount as _EA
        db = _SL()
        try:
            resolved_account_id = account_id
            if not resolved_account_id:
                try:
                    cfg = _get_email_config(None, owner=owner)
                    resolved_account_id = cfg.get("account_id") or None
                    aliases.extend([
                        cfg.get("imap_user") or "",
                        cfg.get("smtp_user") or "",
                        cfg.get("from_address") or "",
                    ])
                except Exception as _e:
                    logger.warning("Failed to resolve email account alias", exc_info=_e)
                    resolved_account_id = None
            row = db.get(_EA, resolved_account_id) if resolved_account_id else None
            if row:
                aliases.extend([row.owner or "", row.imap_user or "", row.from_address or ""])
        finally:
            db.close()
    except Exception as _e:
        logger.warning("Failed to load email aliases", exc_info=_e)
    out = []
    for a in aliases:
        a = (a or "").strip()
        if a not in out:
            out.append(a)
    return out or [""]


def _email_tag_owner_clause(account_id: str | None, owner: str = "") -> tuple[str, list[str]]:
    aliases = _email_tag_owner_aliases(account_id, owner)
    placeholders = ",".join("?" * len(aliases))
    # In configured multi-user mode, do not treat legacy owner='' rows as
    # visible to everyone. Single-user/unconfigured mode keeps legacy rows.
    if owner:
        return f"owner IN ({placeholders})", aliases
    return f"(owner IN ({placeholders}) OR owner IS NULL)", aliases


def _email_tag_account_clause(account_id: str | None) -> tuple[str, list[str]]:
    account = (account_id or "").strip()
    if account:
        return "(account_id=? OR account_id='' OR account_id IS NULL)", [account]
    # No explicit account means the caller is using the default/all-account
    # view. Keep the owner clause as the boundary, but do not hide tags that
    # were written under a concrete account id for the same message.
    return "1=1", []


_VISIBLE_EMAIL_TAGS = {"urgent", "reply-soon", "action-needed", "calendar", "bills", "receipt", "travel"}
_DONE_RESPONSE_TAGS = {"urgent", "reply-soon", "action-needed"}


def _sanitize_visible_email_tags(tags, *, is_answered: bool = False) -> list[str]:
    out = []
    for tag in tags if isinstance(tags, list) else []:
        tag = str(tag or "").strip().lower().replace("_", "-")
        if tag == "promo":
            tag = "marketing"
        if tag not in _VISIBLE_EMAIL_TAGS:
            continue
        if is_answered and tag in _DONE_RESPONSE_TAGS:
            continue
        if tag not in out:
            out.append(tag)
    return out


def _hide_unlinked_calendar_tags(emails: list[dict]) -> None:
    for e in emails or []:
        if not isinstance(e.get("tags"), list):
            continue
        if "calendar" in e.get("tags", []) and not e.get("calendar_event_uids"):
            e["tags"] = [t for t in e.get("tags", []) if t != "calendar"]


def _clear_done_response_tags(owner: str, account_id: str | None, folder: str, uid: str) -> None:
    try:
        conn = _sql3.connect(SCHEDULED_DB)
        owner_clause, owner_params = _email_tag_owner_clause(account_id, owner)
        account_clause, account_params = _email_tag_account_clause(account_id)
        rows = conn.execute(
            f"SELECT rowid, tags FROM email_tags WHERE folder=? AND uid=? AND {owner_clause} AND {account_clause}",
            [folder, str(uid), *owner_params, *account_params],
        ).fetchall()
        for rowid, tags_raw in rows:
            try:
                tags = json.loads(tags_raw or "[]")
            except Exception:
                tags = []
            if not isinstance(tags, list):
                tags = []
            kept = [
                t for t in tags
                if str(t).strip().lower().replace("_", "-") not in _DONE_RESPONSE_TAGS
            ]
            if kept != tags:
                conn.execute("UPDATE email_tags SET tags=? WHERE rowid=?", (json.dumps(kept), rowid))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug(f"clear done response tags skipped: {e}")


def _record_email_received_events(owner: str, account_id: str | None, folder: str, emails: list[dict]):
    """Baseline inbox messages, then fire `email_received` for new arrivals."""
    if not owner or (folder or "INBOX").upper() != "INBOX" or not emails:
        return
    try:
        from src.event_bus import fire_event
        account_key = (account_id or "default").strip() or "default"
        now = datetime.utcnow().isoformat() + "Z"
        keys = []
        for e in emails:
            key = (e.get("message_id") or e.get("uid") or "").strip()
            if key and key not in keys:
                keys.append(key)
        if not keys:
            return

        conn = _sql3.connect(SCHEDULED_DB)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS email_event_seen ("
                "owner TEXT NOT NULL, account_key TEXT NOT NULL, folder TEXT NOT NULL, "
                "message_key TEXT NOT NULL, first_seen_at TEXT NOT NULL, "
                "PRIMARY KEY (owner, account_key, folder, message_key))"
            )
            count = conn.execute(
                "SELECT COUNT(*) FROM email_event_seen WHERE owner=? AND account_key=? AND folder=?",
                (owner, account_key, folder),
            ).fetchone()[0]
            existing = set()
            if count:
                placeholders = ",".join("?" * len(keys))
                rows = conn.execute(
                    f"SELECT message_key FROM email_event_seen "
                    f"WHERE owner=? AND account_key=? AND folder=? AND message_key IN ({placeholders})",
                    (owner, account_key, folder, *keys),
                ).fetchall()
                existing = {r[0] for r in rows}
            new_keys = [k for k in keys if k not in existing]
            conn.executemany(
                "INSERT OR IGNORE INTO email_event_seen "
                "(owner, account_key, folder, message_key, first_seen_at) VALUES (?, ?, ?, ?, ?)",
                [(owner, account_key, folder, k, now) for k in keys],
            )
            conn.commit()
        finally:
            conn.close()

        if count and new_keys:
            for _ in new_keys[:50]:
                fire_event("email_received", owner)
            logger.info("Fired email_received for %d new message(s)", min(len(new_keys), 50))
    except Exception:
        logger.debug("email_received event detection skipped", exc_info=True)


def _folder_name_from_list_line(line) -> str | None:
    decoded = line.decode() if isinstance(line, bytes) else str(line)
    match = re.search(r'"([^"]*)"\s*$|(\S+)\s*$', decoded)
    if not match:
        return None
    return match.group(1) or match.group(2)


def _list_imap_folders(conn) -> tuple[list, list[str]]:
    try:
        status, folders = conn.list()
        if status != "OK" or not folders:
            return [], []
        names = [name for name in (_folder_name_from_list_line(f) for f in folders) if name]
        return folders, names
    except Exception:
        return [], []


def _resolve_mail_folder(conn, preferred: str, role: str = "") -> str:
    """Resolve provider-specific names such as Gmail's [Gmail]/Bin/Spam."""
    folders, names = _list_imap_folders(conn)
    if preferred and preferred in names:
        return preferred
    role_flags = {
        "trash": ("\\Trash",),
        "archive": ("\\Archive", "\\All"),
        "junk": ("\\Junk",),
    }.get(role, ())
    for f in folders:
        decoded = f.decode() if isinstance(f, bytes) else str(f)
        if any(flag in decoded for flag in role_flags):
            name = _folder_name_from_list_line(f)
            if name:
                return name
    candidates = {
        "trash": ("Trash", "[Gmail]/Trash", "[Google Mail]/Trash", "Bin", "[Gmail]/Bin", "Deleted Messages", "Deleted Items"),
        "archive": ("Archive", "Archives", "[Gmail]/All Mail", "[Google Mail]/All Mail", "All Mail"),
        "junk": ("Junk", "Spam", "[Gmail]/Spam", "[Google Mail]/Spam"),
    }.get(role, ())
    lower_map = {n.lower(): n for n in names}
    for candidate in candidates:
        found = lower_map.get(candidate.lower())
        if found:
            return found
    return preferred


def _folder_role_from_name(name: str) -> str:
    lower = (name or "").lower()
    if "trash" in lower or "bin" in lower or "deleted" in lower:
        return "trash"
    if "spam" in lower or "junk" in lower:
        return "junk"
    if "archive" in lower or "all mail" in lower:
        return "archive"
    return ""


def _uid_bytes(uid: str | bytes) -> bytes:
    return uid if isinstance(uid, bytes) else str(uid).encode()


def _uid_exists(conn, uid: str) -> bool:
    try:
        status, data = conn.uid("FETCH", _uid_bytes(uid), "(UID)")
        if status != "OK":
            return False
        for part in data or []:
            meta = part[0] if isinstance(part, tuple) else part
            meta_b = meta if isinstance(meta, bytes) else str(meta).encode()
            if re.search(rb"\bUID\s+\d+\b", meta_b):
                return True
        return False
    except Exception:
        return False


def _imap_uid_search(conn, criteria: str):
    return conn.uid("SEARCH", None, criteria)


def _imap_uid_fetch(conn, uid_set: str | bytes, query: str):
    return conn.uid("FETCH", _uid_bytes(uid_set), query)


def _imap_search_quote(value: str) -> str:
    return '"' + str(value or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def _message_id_chain(*values: str) -> list[str]:
    seen = set()
    out = []
    for value in values:
        for mid in re.findall(r"<[^>]+>", value or ""):
            if mid not in seen:
                seen.add(mid)
                out.append(mid)
    return out


def _uid_from_fetch_meta(meta_b: bytes) -> str:
    m = re.search(rb"\bUID\s+(\d+)\b", meta_b)
    return m.group(1).decode() if m else ""


_FETCH_SEQ_RE = re.compile(rb"^(\d+)\s+\(")


def _group_uid_fetch_records(msg_data) -> list:
    """Group an imaplib UID FETCH response into per-message (meta, payload).

    imaplib yields an interleaved list: ``(meta, literal)`` tuples for
    attributes that carry a literal (``RFC822.HEADER {n}`` etc.) plus bare
    ``bytes`` elements for everything the server sends outside a literal.
    Where each attribute lands is server-specific: Dovecot sends FLAGS
    *before* the header literal (so it ends up inside the tuple meta), while
    Gmail sends FLAGS *after* it, arriving as a bare ``b' FLAGS (\\Seen))'``
    element. Dropping bare elements therefore silently loses FLAGS on Gmail
    and every message renders as unread/unflagged.

    A tuple whose meta starts with a sequence number opens a new record;
    every other part — continuation tuple or bare bytes — is folded into the
    current record's meta so attribute regexes see the full meta text.
    Plain ``b')'`` terminators get folded in too, which is harmless.
    """
    grouped: list = []  # list of (meta_bytes, payload_bytes_or_None)
    for part in (msg_data or []):
        if isinstance(part, tuple):
            meta_b = part[0] if isinstance(part[0], (bytes, bytearray)) else str(part[0]).encode()
            if _FETCH_SEQ_RE.match(meta_b):
                grouped.append((meta_b, part[1]))
            elif grouped:
                cur_meta, cur_payload = grouped[-1]
                grouped[-1] = (cur_meta + b" " + meta_b, cur_payload or part[1])
        elif isinstance(part, (bytes, bytearray)) and grouped:
            cur_meta, cur_payload = grouped[-1]
            grouped[-1] = (cur_meta + b" " + bytes(part), cur_payload)
    return grouped


def _account_cache_key(account_id: str | None, owner: str = "") -> str:
    return (account_id or "default").strip() or f"default:{owner or ''}"


def _parse_email_list_record(meta_b: bytes, raw_header: bytes | None) -> dict | None:
    try:
        meta = meta_b.decode(errors="replace")
        uid_num = _uid_from_fetch_meta(meta_b)
        if not uid_num or not raw_header:
            return None
        flag_m = re.search(r'FLAGS \(([^)]*)\)', meta)
        flags = flag_m.group(1) if flag_m else ""
        size_m = re.search(r'RFC822\.SIZE (\d+)', meta)
        size = int(size_m.group(1)) if size_m else 0
        msg = email_mod.message_from_bytes(raw_header)
        subject = _decode_header(msg.get("Subject", "(no subject)"))
        sender = _decode_header(msg.get("From", "unknown"))
        date_str = msg.get("Date", "")
        message_id = (msg.get("Message-ID", "") or "").strip()
        sender_name, sender_addr = email.utils.parseaddr(sender)
        to_str = _decode_header(msg.get("To", ""))
        cc_str = _decode_header(msg.get("Cc", ""))
        parsed_date = email.utils.parsedate_to_datetime(date_str) if date_str else None
        if parsed_date and parsed_date.tzinfo is None:
            from datetime import timezone as _tz
            parsed_date = parsed_date.replace(tzinfo=_tz.utc)
        iso_date = parsed_date.isoformat() if parsed_date else ""
        date_epoch = parsed_date.timestamp() if parsed_date else 0.0
        ct = msg.get("Content-Type", "")
        has_attachments = "multipart/mixed" in ct.lower() or "multipart/related" in ct.lower()
        return {
            "uid": uid_num,
            "message_id": message_id,
            "subject": subject,
            "from_name": sender_name or sender_addr,
            "from_address": sender_addr,
            "to": to_str,
            "cc": cc_str,
            "date": iso_date,
            "date_display": date_str,
            "date_epoch": date_epoch,
            "size": size,
            "is_read": "\\Seen" in flags,
            "is_answered": "\\Answered" in flags,
            "is_flagged": "\\Flagged" in flags,
            "flags": flags,
            "has_attachments": has_attachments,
        }
    except Exception as e:
        logger.warning(f"Error parsing email index entry: {e}")
        return None


def _email_index_rows(owner: str, account_id: str | None, folder: str, uids: list[str]) -> dict[str, dict]:
    if not uids:
        return {}
    try:
        conn = _sql3.connect(SCHEDULED_DB)
        try:
            placeholders = ",".join("?" * len(uids))
            rows = conn.execute(
                f"""
                SELECT uid, message_id, subject, from_name, from_address, to_text, cc_text,
                       date_iso, date_display, date_epoch, size, flags, has_attachments
                FROM email_message_index
                WHERE owner=? AND account_key=? AND folder=? AND uid IN ({placeholders})
                """,
                [owner or "", _account_cache_key(account_id, owner), folder, *uids],
            ).fetchall()
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"email index read skipped: {e}")
        return {}
    out: dict[str, dict] = {}
    for row in rows:
        uid, message_id, subject, from_name, from_address, to_text, cc_text, date_iso, date_display, date_epoch, size, flags, has_attachments = row
        flags = flags or ""
        out[str(uid)] = {
            "uid": str(uid),
            "message_id": (message_id or "").strip(),
            "subject": subject or "(no subject)",
            "from_name": from_name or from_address or "",
            "from_address": from_address or "",
            "to": to_text or "",
            "cc": cc_text or "",
            "date": date_iso or "",
            "date_display": date_display or "",
            "date_epoch": float(date_epoch or 0),
            "size": int(size or 0),
            "is_read": "\\Seen" in flags,
            "is_answered": "\\Answered" in flags,
            "is_flagged": "\\Flagged" in flags,
            "flags": flags,
            "has_attachments": bool(has_attachments),
        }
    return out


def _email_index_search(owner: str, account_id: str | None, folder: str, query: str, limit: int, global_search: bool = True) -> tuple[list[dict], int, str | None]:
    q = (query or "").strip()
    if not q:
        return [], 0, None
    limit = max(1, min(int(limit or 50), 200))
    account_key = _account_cache_key(account_id, owner)
    folder_clause = ""
    params: list = [owner or "", account_key]
    # Searching from INBOX should feel global for Gmail-style accounts,
    # because users expect archived/labelled mail to show up too. The
    # local index only contains folders that have been warmed/listed, so
    # this remains a best-effort fast path; IMAP is still the fallback.
    if not global_search or (folder or "").upper() != "INBOX":
        folder_clause = "AND folder=?"
        params.append(folder)
    terms = _email_search_terms(q)
    if not terms:
        return [], 0, None
    term_clause = " AND ".join([
        """(
                    subject LIKE ? ESCAPE '\\' OR
                    from_name LIKE ? ESCAPE '\\' OR
                    from_address LIKE ? ESCAPE '\\' OR
                    to_text LIKE ? ESCAPE '\\' OR
                    cc_text LIKE ? ESCAPE '\\'
                  )"""
        for _ in terms
    ])
    for term in terms:
        like = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        params.extend([like, like, like, like, like])
    try:
        conn = _sql3.connect(SCHEDULED_DB)
        try:
            total_row = conn.execute(
                f"""
                SELECT COUNT(*), MAX(updated_at)
                FROM email_message_index
                WHERE owner=? AND account_key=? {folder_clause}
                  AND {term_clause}
                """,
                params,
            ).fetchone()
            total = int((total_row or [0])[0] or 0)
            if not total:
                return [], 0, (total_row or [None, None])[1]
            rows = conn.execute(
                f"""
                SELECT uid, message_id, subject, from_name, from_address, to_text, cc_text,
                       date_iso, date_display, date_epoch, size, flags, has_attachments,
                       folder
                FROM email_message_index
                WHERE owner=? AND account_key=? {folder_clause}
                  AND {term_clause}
                ORDER BY date_epoch DESC
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        logger.debug("email index search skipped", exc_info=True)
        return [], 0, None

    emails: list[dict] = []
    for row in rows:
        uid, message_id, subject, from_name, from_address, to_text, cc_text, date_iso, date_display, date_epoch, size, flags, has_attachments, row_folder = row
        flags = flags or ""
        emails.append({
            "uid": str(uid),
            "message_id": (message_id or "").strip(),
            "subject": subject or "(no subject)",
            "from_name": from_name or from_address or "",
            "from_address": from_address or "",
            "to": to_text or "",
            "cc": cc_text or "",
            "date": date_iso or "",
            "date_display": date_display or "",
            "date_epoch": float(date_epoch or 0),
            "size": int(size or 0),
            "is_read": "\\Seen" in flags,
            "is_answered": "\\Answered" in flags,
            "is_flagged": "\\Flagged" in flags,
            "flags": flags,
            "has_attachments": bool(has_attachments),
            "folder": row_folder or folder,
        })
    return emails, total, (total_row or [None, None])[1]


def _email_search_terms(query: str) -> list[str]:
    q = (query or "").strip()
    if not q:
        return []
    # Preserve quoted phrases, then split the rest. This makes:
    #   honda insurance -> honda AND insurance
    #   "Yoko Honda" insurance -> "Yoko Honda" AND insurance
    # The cap avoids creating huge IMAP expressions from pasted paragraphs.
    parts = []
    consumed = []
    for m in re.finditer(r'"([^"]{1,120})"', q):
        phrase = m.group(1).strip()
        if phrase:
            parts.append(phrase)
        consumed.append((m.start(), m.end()))
    remainder = q
    for start, end in reversed(consumed):
        remainder = remainder[:start] + " " + remainder[end:]
    parts.extend(re.findall(r"[^\s,;]+", remainder))
    out = []
    seen = set()
    for p in parts:
        p = p.strip().strip('"').strip()
        if len(p) < 2:
            continue
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
        if len(out) >= 6:
            break
    return out


def _imap_or_many(keys: list[str]) -> str:
    if not keys:
        return "ALL"
    expr = keys[0]
    for key in keys[1:]:
        expr = f"OR ({expr}) ({key})"
    return expr


def _email_imap_search_criteria(query: str) -> str:
    terms = _email_search_terms(query)
    if not terms:
        return "ALL"
    term_exprs = []
    for term in terms:
        q = _imap_search_quote(term)
        # Search both sides of the conversation, plus subject and body. The
        # older route only searched FROM/SUBJECT/TEXT, so recipient searches
        # and many sent-message searches felt broken.
        term_exprs.append(f"({_imap_or_many([f'FROM {q}', f'TO {q}', f'CC {q}', f'SUBJECT {q}', f'TEXT {q}'])})")
    return "(" + " ".join(term_exprs) + ")"


def _email_index_upsert(owner: str, account_id: str | None, folder: str, emails: list[dict]):
    if not emails:
        return
    now = datetime.utcnow().isoformat() + "Z"
    rows = []
    for e in emails:
        uid = str(e.get("uid") or "").strip()
        if not uid:
            continue
        rows.append((
            owner or "",
            _account_cache_key(account_id, owner),
            folder,
            uid,
            (e.get("message_id") or "").strip(),
            e.get("subject") or "",
            e.get("from_name") or "",
            e.get("from_address") or "",
            e.get("to") or "",
            e.get("cc") or "",
            e.get("date") or "",
            e.get("date_display") or "",
            float(e.get("date_epoch") or 0),
            int(e.get("size") or 0),
            e.get("flags") or "",
            1 if e.get("has_attachments") else 0,
            now,
        ))
    if not rows:
        return
    try:
        conn = _sql3.connect(SCHEDULED_DB)
        try:
            conn.executemany(
                """
                INSERT INTO email_message_index
                (owner, account_key, folder, uid, message_id, subject, from_name,
                 from_address, to_text, cc_text, date_iso, date_display, date_epoch,
                 size, flags, has_attachments, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(owner, account_key, folder, uid) DO UPDATE SET
                    message_id=excluded.message_id,
                    subject=excluded.subject,
                    from_name=excluded.from_name,
                    from_address=excluded.from_address,
                    to_text=excluded.to_text,
                    cc_text=excluded.cc_text,
                    date_iso=excluded.date_iso,
                    date_display=excluded.date_display,
                    date_epoch=excluded.date_epoch,
                    size=excluded.size,
                    flags=excluded.flags,
                    has_attachments=excluded.has_attachments,
                    updated_at=excluded.updated_at
                """,
                rows,
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"email index write skipped: {e}")


def _email_index_update_flags(owner: str, account_id: str | None, folder: str, uid: str, flag: str, add: bool):
    try:
        conn = _sql3.connect(SCHEDULED_DB)
        try:
            row = conn.execute(
                "SELECT flags FROM email_message_index WHERE owner=? AND account_key=? AND folder=? AND uid=?",
                (owner or "", _account_cache_key(account_id, owner), folder, str(uid)),
            ).fetchone()
            if not row:
                return
            parts = {p for p in (row[0] or "").split() if p}
            if add:
                parts.add(flag)
            else:
                parts.discard(flag)
            conn.execute(
                "UPDATE email_message_index SET flags=?, updated_at=? WHERE owner=? AND account_key=? AND folder=? AND uid=?",
                (" ".join(sorted(parts)), datetime.utcnow().isoformat() + "Z", owner or "", _account_cache_key(account_id, owner), folder, str(uid)),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.debug("email index flag update skipped", exc_info=True)


def _email_index_delete(owner: str, account_id: str | None, folder: str | None, uid: str):
    try:
        conn = _sql3.connect(SCHEDULED_DB)
        try:
            if folder:
                conn.execute(
                    "DELETE FROM email_message_index WHERE owner=? AND account_key=? AND folder=? AND uid=?",
                    (owner or "", _account_cache_key(account_id, owner), folder, str(uid)),
                )
            else:
                conn.execute(
                    "DELETE FROM email_message_index WHERE owner=? AND account_key=? AND uid=?",
                    (owner or "", _account_cache_key(account_id, owner), str(uid)),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.debug("email index delete skipped", exc_info=True)


def _email_preview_cache_get(owner: str, account_id: str | None, folder: str, uid: str) -> dict | None:
    try:
        conn = _sql3.connect(SCHEDULED_DB)
        try:
            row = conn.execute(
                """
                SELECT payload_json, updated_at
                FROM email_body_preview_cache
                WHERE owner=? AND account_key=? AND folder=? AND uid=?
                """,
                (owner or "", _account_cache_key(account_id, owner), folder, str(uid)),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        payload = json.loads(row[0] or "{}")
        if isinstance(payload, dict):
            payload.setdefault("sync", {})
            payload["sync"].update({"source": "preview_cache", "updated_at": row[1]})
            return payload
    except Exception:
        logger.debug("email preview cache read skipped", exc_info=True)
    return None


def _email_preview_cache_put(owner: str, account_id: str | None, folder: str, uid: str, payload: dict):
    if not payload:
        return
    try:
        now = datetime.utcnow().isoformat() + "Z"
        message_id = (payload.get("message_id") or "").strip()
        stored = dict(payload)
        stored["sync"] = {"source": "preview_cache", "updated_at": now}
        conn = _sql3.connect(SCHEDULED_DB)
        try:
            conn.execute(
                """
                INSERT INTO email_body_preview_cache
                (owner, account_key, folder, uid, message_id, payload_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(owner, account_key, folder, uid) DO UPDATE SET
                    message_id=excluded.message_id,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    owner or "",
                    _account_cache_key(account_id, owner),
                    folder,
                    str(uid),
                    message_id,
                    json.dumps(stored, ensure_ascii=False),
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.debug("email preview cache write skipped", exc_info=True)


def _email_attachment_meta_cache_get(owner: str, account_id: str | None, folder: str, uid: str) -> list[dict] | None:
    try:
        conn = _sql3.connect(SCHEDULED_DB)
        try:
            row = conn.execute(
                """
                SELECT attachments_json
                FROM email_attachment_metadata_cache
                WHERE owner=? AND account_key=? AND folder=? AND uid=?
                """,
                (owner or "", _account_cache_key(account_id, owner), folder, str(uid)),
            ).fetchone()
            if not row:
                row = conn.execute(
                    """
                    SELECT attachments_json
                    FROM email_attachment_metadata_cache
                    WHERE owner=? AND folder=? AND uid=?
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (owner or "", folder, str(uid)),
                ).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        data = json.loads(row[0] or "[]")
        return data if isinstance(data, list) else []
    except Exception:
        logger.debug("email attachment metadata cache read skipped", exc_info=True)
    return None


def _email_attachment_meta_cache_put(owner: str, account_id: str | None, folder: str, uid: str, message_id: str, attachments: list[dict]):
    try:
        conn = _sql3.connect(SCHEDULED_DB)
        try:
            conn.execute(
                """
                INSERT INTO email_attachment_metadata_cache
                (owner, account_key, folder, uid, message_id, attachments_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(owner, account_key, folder, uid) DO UPDATE SET
                    message_id=excluded.message_id,
                    attachments_json=excluded.attachments_json,
                    updated_at=excluded.updated_at
                """,
                (
                    owner or "",
                    _account_cache_key(account_id, owner),
                    folder,
                    str(uid),
                    (message_id or "").strip(),
                    json.dumps(attachments or [], ensure_ascii=False),
                    datetime.utcnow().isoformat() + "Z",
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.debug("email attachment metadata cache write skipped", exc_info=True)


def _smtp_ready(cfg: dict) -> bool:
    if not cfg.get("smtp_host") or not cfg.get("smtp_user"):
        return False
    return bool(cfg.get("smtp_password") or cfg.get("oauth_provider"))


def _resolve_send_config(account_id: str | None = None, owner: str = "") -> dict:
    """Resolve an account for outbound SMTP.

    If the caller explicitly picked an account, use only that account and
    return a clear error when it cannot send. If no account was picked and
    the default is receive-only, fall back to the first SMTP-capable account
    owned by the same user.
    """
    cfg = _get_email_config(account_id, owner=owner)
    if _smtp_ready(cfg):
        return cfg
    if account_id:
        raise ValueError(f"Email account {cfg.get('account_name') or account_id} has no SMTP configured")
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
                trial = _get_email_config(account_id=row.id, owner=owner)
                if _smtp_ready(trial):
                    return trial
        finally:
            db.close()
    except Exception as e:
        logger.debug(f"SMTP-capable account fallback failed: {e}")
    raise ValueError("No SMTP-capable email account configured")


def _store_email_flag(conn, uid: str, flag: str, add: bool = True) -> bool:
    op = "+FLAGS" if add else "-FLAGS"
    if _uid_exists(conn, uid):
        status, _ = conn.uid("STORE", _uid_bytes(uid), op, flag)
    else:
        status, _ = conn.store(_uid_bytes(uid), op, flag)
    return status == "OK"


def _move_email_message(conn, uid: str, dest: str, role: str = "") -> bool:
    dest = _resolve_mail_folder(conn, dest, role or _folder_role_from_name(dest))
    if _uid_exists(conn, uid):
        status, _ = conn.uid("MOVE", _uid_bytes(uid), _q(dest))
        if status == "OK":
            return True
        status, _ = conn.uid("COPY", _uid_bytes(uid), _q(dest))
        if status != "OK":
            return False
        status, _ = conn.uid("STORE", _uid_bytes(uid), "+FLAGS", "\\Deleted")
    else:
        status, _ = conn.copy(_uid_bytes(uid), _q(dest))
        if status != "OK":
            return False
        status, _ = conn.store(_uid_bytes(uid), "+FLAGS", "\\Deleted")
    if status == "OK":
        conn.expunge()
        return True
    return False


def _apply_odysseus_headers(msg, kind: str | None = None, ref_id: str | None = None):
    msg["X-Odysseus-Origin"] = ODYSSEUS_MAIL_ORIGIN
    if kind:
        msg["X-Odysseus-Kind"] = re.sub(r"[^A-Za-z0-9_.-]", "-", kind)[:64]
    if ref_id:
        msg["X-Odysseus-Ref"] = re.sub(r"[^A-Za-z0-9_.:-]", "-", ref_id)[:128]


def _normalize_addr_field(field: str) -> str:
    """Strip the malformed-but-common trailing/leading commas and stray
    whitespace from a To/Cc/Bcc string before it lands in the MIME header
    or the SMTP envelope. Users often paste a single address with a
    trailing comma (e.g. `felix@pewdiepie.com,`) and most MTAs reject the
    resulting `To: felix@pewdiepie.com,` line as a syntax error. Collapse
    any run of separator junk between addresses too."""
    if not field:
        return field
    # Split on commas, drop empty tokens, rejoin with a single ', '.
    parts = [p.strip() for p in field.split(",")]
    parts = [p for p in parts if p]
    return ", ".join(parts)


def _envelope_recipients(*fields: str) -> list:
    """Extract bare SMTP envelope addresses from one or more To/Cc/Bcc header
    strings. A naive `field.split(",")` corrupts display names that contain a
    comma (e.g. `"Smith, John" <john@corp.com>`, the canonical Outlook form):
    it splits into `"Smith` and `John" <john@corp.com>`, breaking delivery.
    email.utils.getaddresses parses the address grammar correctly."""
    out = []
    for _name, addr in email.utils.getaddresses([f for f in fields if f]):
        addr = (addr or "").strip()
        if addr:
            out.append(addr)
    return out


def _md_to_email_html(text: str) -> str:
    """Render the compose markdown body to a SAFE HTML fragment for the email's
    text/html part. Everything is HTML-escaped FIRST (so a pasted <script> /
    <img onerror=...> can never become live HTML in the recipient's client),
    then the toolbar's formatting is layered on with controlled regex: bold,
    italic, strike, inline code, http(s) links, headings, and bullet/numbered
    lists. Plain-text readers still get the raw markdown via the text/plain part.
    """
    def _inline(s: str) -> str:
        s = html.escape(s)                                  # escape BEFORE formatting
        s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", s)
        s = re.sub(r"~~([^~]+)~~", r"<del>\1</del>", s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        # links: text + http(s) url only (escape() already neutralised quotes)
        s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', s)
        return s

    parts: list[str] = []
    in_ul = in_ol = False
    for ln in (text or "").split("\n"):
        m_h = re.match(r"^(#{1,3})\s+(.*)$", ln)
        m_ul = re.match(r"^\s*[-*]\s+(.*)$", ln)
        m_ol = re.match(r"^\s*\d+\.\s+(.*)$", ln)
        if m_h:
            if in_ul: parts.append("</ul>"); in_ul = False
            if in_ol: parts.append("</ol>"); in_ol = False
            lvl = len(m_h.group(1))
            parts.append(f"<h{lvl}>{_inline(m_h.group(2))}</h{lvl}>")
        elif m_ul:
            if in_ol: parts.append("</ol>"); in_ol = False
            if not in_ul: parts.append("<ul>"); in_ul = True
            parts.append(f"<li>{_inline(m_ul.group(1))}</li>")
        elif m_ol:
            if in_ul: parts.append("</ul>"); in_ul = False
            if not in_ol: parts.append("<ol>"); in_ol = True
            parts.append(f"<li>{_inline(m_ol.group(1))}</li>")
        else:
            if in_ul: parts.append("</ul>"); in_ul = False
            if in_ol: parts.append("</ol>"); in_ol = False
            parts.append(_inline(ln) + "<br>")
    if in_ul: parts.append("</ul>")
    if in_ol: parts.append("</ol>")
    return "<html><body>" + "\n".join(parts) + "</body></html>"


# Tags the WYSIWYG email composer may legitimately produce.
_EMAIL_ALLOWED_TAGS = {
    "b", "strong", "i", "em", "u", "s", "strike", "del", "a", "br", "p", "div",
    "ul", "ol", "li", "blockquote", "span", "h1", "h2", "h3", "code", "pre",
}


class _EmailHtmlSanitizer(_HTMLParser):
    """Allowlist sanitizer for WYSIWYG-composed email HTML. Emits only known
    formatting tags (all attributes dropped except a safe href on <a>), escapes
    all text, and discards <script>/<style> content entirely — so client-sent
    HTML can never carry live script/handlers into the recipient's client."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self._skip = 0  # depth inside <script>/<style>

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
            return
        if tag == "br":
            self.out.append("<br>")
            return
        if tag not in _EMAIL_ALLOWED_TAGS:
            return
        if tag == "a":
            href = ""
            for k, v in attrs:
                if k.lower() == "href" and v and re.match(r"^(https?:|mailto:)", v.strip(), re.I):
                    href = v.strip()
            self.out.append(
                f'<a href="{html.escape(href, quote=True)}" target="_blank" rel="noopener noreferrer">'
                if href else "<a>")
        else:
            self.out.append(f"<{tag}>")

    def handle_startendtag(self, tag, attrs):
        if tag == "br":
            self.out.append("<br>")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            if self._skip:
                self._skip -= 1
            return
        if tag == "br" or tag not in _EMAIL_ALLOWED_TAGS:
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if self._skip:
            return
        self.out.append(html.escape(data))


def _sanitize_email_html(raw: str) -> str:
    """Return a safe <html><body>…</body></html> from client-supplied compose
    HTML, or None if it can't be parsed."""
    p = _EmailHtmlSanitizer()
    try:
        p.feed(raw or "")
        p.close()
    except Exception:
        return None
    inner = "".join(p.out).strip()
    if not inner:
        return None
    return f"<html><body>{inner}</body></html>"
