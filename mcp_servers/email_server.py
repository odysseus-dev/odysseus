"""
email_server.py

MCP server exposing email tools: list unread/unresponded emails,
read email content, and draft replies as email documents.
Connects to local Dovecot IMAP and reads from the AI summary cache.
"""

import asyncio
import imaplib
import smtplib
import email
import email.header
import email.utils
from email.message import EmailMessage
import re
import html
import json
import sqlite3
import sys
import os
import os.path
from pathlib import Path
from datetime import datetime, timedelta

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

server = Server("email")
EMAIL_SOCKET_TIMEOUT = float(os.environ.get("EMAIL_SOCKET_TIMEOUT", "20"))
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _b(value) -> bytes:
    return str(value).encode()


def _uid_fetch_rows(data) -> list:
    return [d for d in (data or []) if isinstance(d, bytes) and b"UID " in d]

# ── Config ──
# Multi-account aware. Accounts live in data/app.db :: email_accounts.
# Callers can pass `account=` (match by name, user, or id) to pick a specific
# inbox; None resolves to the default row. Falls back to env vars / settings.json
# flat keys when no DB row matches (legacy single-account behaviour).

_ACCOUNT_CACHE: dict = {}  # key = normalized account selector -> config dict


def _clean_header_value(value) -> str:
    """EmailMessage rejects CR/LF in assigned header values; unfold safely."""
    if value is None:
        return ""
    return re.sub(r"[\r\n]+[ \t]*", " ", str(value)).strip()


def _db_path() -> Path:
    return DATA_DIR / "app.db"


def _list_accounts_raw() -> list:
    """Return list of dicts from the email_accounts table. Empty list if table
    missing or empty. Never raises."""
    path = _db_path()
    if not path.exists():
        return []
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        columns = {r[1] for r in conn.execute("PRAGMA table_info(email_accounts)").fetchall()}
        smtp_security_select = "smtp_security" if "smtp_security" in columns else "'' AS smtp_security"
        rows = conn.execute(f"""
            SELECT id, name, is_default, enabled,
                   imap_host, imap_port, imap_user, imap_password, imap_starttls,
                   smtp_host, smtp_port, {smtp_security_select}, smtp_user, smtp_password, from_address
            FROM email_accounts WHERE enabled = 1
            ORDER BY is_default DESC, created_at ASC
        """).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    except Exception:
        return []


def _resolve_account(selector: str | None) -> dict | None:
    """Given a selector (None = default, or a name/user/id string), return the
    matching row or None. Matching is case-insensitive substring on name +
    imap_user + from_address, plus exact id match."""
    rows = _list_accounts_raw()
    if not rows:
        return None
    if not selector:
        for r in rows:
            if r.get("is_default"):
                return r
        return rows[0]
    sel = selector.strip().lower()
    # Exact id match first
    for r in rows:
        if r["id"] == selector:
            return r
    for r in rows:
        fields = [r.get("name") or "", r.get("imap_user") or "", r.get("from_address") or ""]
        if any(sel in (f or "").lower() for f in fields):
            return r
    try:
        from difflib import get_close_matches
        candidates = []
        by_candidate = {}
        for r in rows:
            for field in (r.get("name"), r.get("imap_user"), r.get("from_address")):
                if field:
                    val = str(field).lower()
                    candidates.append(val)
                    by_candidate[val] = r
        close = get_close_matches(sel, candidates, n=1, cutoff=0.72)
        if close:
            return by_candidate.get(close[0])
    except Exception:
        pass
    return None


def _load_config(account: str | None = None) -> dict:
    """Return the full config dict for the requested account (or default).

    Resolution order per-field:
      1. email_accounts row (selected by `account` or default)
      2. env vars + settings.json flat keys (legacy)
      3. hardcoded fallbacks (localhost:31143 etc.)
    """
    cache_key = (account or "").strip().lower() or "__default__"
    if cache_key in _ACCOUNT_CACHE:
        return _ACCOUNT_CACHE[cache_key]

    cfg = {
        "imap_host": os.environ.get("IMAP_HOST", "localhost"),
        "imap_port": int(os.environ.get("IMAP_PORT", "31143")),
        "imap_user": os.environ.get("IMAP_USER", ""),
        "imap_password": os.environ.get("IMAP_PASSWORD", ""),
        "imap_ssl": os.environ.get("IMAP_SSL", "false").lower() == "true",
        "imap_starttls": os.environ.get("IMAP_STARTTLS", "true").lower() == "true",
        "smtp_host": os.environ.get("SMTP_HOST", ""),
        "smtp_port": int(os.environ.get("SMTP_PORT", "465")),
        "smtp_security": os.environ.get("SMTP_SECURITY", ""),
        "smtp_user": os.environ.get("SMTP_USER", ""),
        "smtp_password": os.environ.get("SMTP_PASSWORD", ""),
        "smtp_starttls": os.environ.get("SMTP_STARTTLS", "false").lower() == "true",
        "smtp_ssl": os.environ.get("SMTP_SSL", "true").lower() == "true",
        "from_address": os.environ.get("EMAIL_FROM", ""),
        "archive_folder": os.environ.get("ARCHIVE_FOLDER", "Archive"),
        "trash_folder": os.environ.get("TRASH_FOLDER", "Trash"),
        "cache_db": os.environ.get(
            "EMAIL_CACHE_DB",
            str(DATA_DIR / "email_cache.db"),
        ),
        "account_id": None,
        "account_name": None,
    }

    rows = _list_accounts_raw()
    row = _resolve_account(account)
    if account and rows and not row:
        available = ", ".join(
            f"{r.get('name') or r.get('imap_user')} <{r.get('imap_user') or r.get('from_address') or '?'}>"
            for r in rows
        )
        raise ValueError(f"Email account not found for selector {account!r}. Available accounts: {available}")
    if row:
        cfg["account_id"] = row["id"]
        cfg["account_name"] = row["name"]
        cfg["imap_host"] = row["imap_host"] or cfg["imap_host"]
        cfg["imap_port"] = int(row["imap_port"] or cfg["imap_port"])
        cfg["imap_user"] = row["imap_user"] or cfg["imap_user"]
        # Passwords in email_accounts are stored encrypted via
        # src.secret_storage.encrypt — decrypt before handing to IMAP
        # (same path email_helpers.py:369 uses). Falling back to the raw
        # ciphertext is what produced AUTHENTICATIONFAILED previously.
        try:
            from src.secret_storage import decrypt as _decrypt
        except Exception:
            _decrypt = lambda v: v  # noqa: E731
        cfg["imap_password"] = _decrypt(row["imap_password"]) if row["imap_password"] else cfg["imap_password"]
        cfg["imap_starttls"] = bool(row["imap_starttls"])
        # The email_accounts table stores STARTTLS but not an explicit IMAP SSL
        # flag. Port 993 is implicit TLS for IMAP providers like Gmail.
        cfg["imap_ssl"] = int(cfg["imap_port"]) == 993 and not cfg["imap_starttls"]
        cfg["smtp_host"] = row["smtp_host"] or cfg["smtp_host"]
        cfg["smtp_port"] = int(row["smtp_port"] or cfg["smtp_port"])
        cfg["smtp_security"] = row["smtp_security"] or cfg["smtp_security"] or ("starttls" if int(cfg["smtp_port"]) == 587 else "ssl")
        cfg["smtp_user"] = row["smtp_user"] or cfg["smtp_user"]
        cfg["smtp_password"] = _decrypt(row["smtp_password"]) if row["smtp_password"] else cfg["smtp_password"]
        cfg["from_address"] = row["from_address"] or row["imap_user"] or cfg["from_address"]
    else:
        # Legacy fallback: settings.json flat keys
        try:
            settings_path = Path(__file__).resolve().parent.parent / "data" / "settings.json"
            if settings_path.exists():
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
                for key in (
                    "imap_host", "imap_port", "imap_user", "imap_password",
                    "smtp_host", "smtp_port", "smtp_user", "smtp_password",
                    "from_address", "archive_folder", "trash_folder",
                ):
                    if settings.get(key) not in (None, ""):
                        cfg[key] = int(settings[key]) if key.endswith("_port") else settings[key]
        except Exception:
            pass

    if not cfg["from_address"]:
        cfg["from_address"] = cfg["imap_user"]

    _ACCOUNT_CACHE[cache_key] = cfg
    return cfg


# ── IMAP helpers ──


def _imap_connect(account: str | None = None):
    """Connect to IMAP server, returns logged-in connection. account selects
    the mailbox (None = default)."""
    cfg = _load_config(account)
    if cfg["imap_ssl"]:
        conn = imaplib.IMAP4_SSL(
            cfg["imap_host"],
            cfg["imap_port"],
            timeout=EMAIL_SOCKET_TIMEOUT,
        )
    else:
        conn = imaplib.IMAP4(
            cfg["imap_host"],
            cfg["imap_port"],
            timeout=EMAIL_SOCKET_TIMEOUT,
        )
        if cfg["imap_starttls"]:
            conn.starttls()
    if getattr(conn, "sock", None):
        conn.sock.settimeout(EMAIL_SOCKET_TIMEOUT)
    conn.login(cfg["imap_user"], cfg["imap_password"])
    return conn


def _detect_sent_folder(conn):
    """Find the account's Sent folder name; fall back to 'Sent'."""
    candidates = ("Sent", "[Gmail]/Sent Mail", "Sent Mail", "Sent Items", "INBOX.Sent")
    try:
        status, folders = conn.list()
        if status != "OK" or not folders:
            return "Sent"
        names = []
        for f in folders:
            decoded = f.decode() if isinstance(f, bytes) else str(f)
            m = re.search(r'"([^"]*)"\s*$|(\S+)\s*$', decoded)
            if m:
                names.append(m.group(1) or m.group(2))
        for f in folders:
            decoded = f.decode() if isinstance(f, bytes) else str(f)
            if r"\Sent" in decoded:
                m = re.search(r'"([^"]*)"\s*$|(\S+)\s*$', decoded)
                if m:
                    return m.group(1) or m.group(2)
        for c in candidates:
            if c in names:
                return c
    except Exception:
        pass
    return "Sent"


def _folder_name_from_list_line(line) -> str | None:
    decoded = line.decode() if isinstance(line, bytes) else str(line)
    m = re.search(r'"([^"]*)"\s*$|(\S+)\s*$', decoded)
    if not m:
        return None
    return m.group(1) or m.group(2)


def _list_folder_lines(conn) -> list:
    try:
        status, folders = conn.list()
        if status != "OK" or not folders:
            return []
        return folders
    except Exception:
        return []


def _resolve_folder(conn, preferred: str, role: str) -> str:
    """Resolve provider-specific folder names like Gmail's [Gmail]/Trash."""
    folders = _list_folder_lines(conn)
    names = [name for name in (_folder_name_from_list_line(f) for f in folders) if name]
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
        "trash": ("Trash", "[Gmail]/Trash", "[Google Mail]/Trash", "Bin", "Deleted Messages", "Deleted Items"),
        "archive": ("Archive", "Archives", "[Gmail]/All Mail", "[Google Mail]/All Mail"),
        "junk": ("Junk", "Spam", "[Gmail]/Spam", "[Google Mail]/Spam"),
    }.get(role, ())
    lower_map = {n.lower(): n for n in names}
    for candidate in candidates:
        if candidate.lower() in lower_map:
            return lower_map[candidate.lower()]
    return preferred


def _folder_role_from_name(name: str) -> str:
    lower = (name or "").lower()
    if "trash" in lower or "bin" in lower or "deleted" in lower:
        return "trash"
    if "junk" in lower or "spam" in lower:
        return "junk"
    if "archive" in lower or "all mail" in lower:
        return "archive"
    return ""


def _decode_header(raw):
    """Decode MIME encoded header."""
    if not raw:
        return ""
    try:
        # make_header concatenates per RFC 2047: no spurious space between an
        # encoded-word and adjacent plain text (plain runs keep their own
        # whitespace), and whitespace between two adjacent encoded-words is
        # dropped. The old " ".join produced "Re:  Jose" style double spaces
        # on every non-ASCII subject or sender.
        return str(email.header.make_header(email.header.decode_header(raw)))
    except Exception:
        # Malformed header or unknown charset: lossy per-part decode
        decoded = []
        for data, charset in email.header.decode_header(raw):
            if isinstance(data, bytes):
                try:
                    decoded.append(data.decode(charset or "utf-8", errors="replace"))
                except LookupError:
                    decoded.append(data.decode("utf-8", errors="replace"))
            else:
                decoded.append(data)
        return "".join(decoded)


def _extract_text(msg):
    """Extract plain text body from email message."""
    if msg.is_multipart():
        text_parts = []
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if ct == "text/plain" and "attachment" not in cd:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    text_parts.append(payload.decode(charset, errors="replace"))
            elif ct == "text/html" and not text_parts and "attachment" not in cd:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    raw_html = payload.decode(charset, errors="replace")
                    text = re.sub(r"<br\s*/?>", "\n", raw_html, flags=re.I)
                    text = re.sub(r"<[^>]+>", "", text)
                    text = html.unescape(text)
                    text_parts.append(text.strip())
        return "\n".join(text_parts)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    return ""


def _get_cached_summaries():
    """Read pre-computed summaries from SQLite cache."""
    cfg = _load_config()
    db_path = cfg["cache_db"]
    if not os.path.exists(db_path):
        return {}
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT subject, sender, summary, suggested_reply FROM email_ai"
        ).fetchall()
        conn.close()
        result = {}
        for subj, sender, summary, reply in rows:
            result[subj] = {"sender": sender, "summary": summary, "reply": reply}
        return result
    except Exception:
        return {}


# ── Tool implementations ──


def _list_emails(folder="INBOX", max_results=20, unresponded_only=False,
                 unread_only=False, account=None):
    """List emails newest-first. By default returns the latest messages,
    including read mail, so it matches normal inbox UI expectations.
    Pass unread_only=True and/or unresponded_only=True for attention scans.
    account selects mailbox (None = default).
    """
    conn = _imap_connect(account)
    select_status, _ = conn.select(folder, readonly=True)
    if select_status != "OK":
        conn.logout()
        raise ValueError(f"IMAP folder not found: {folder}")

    if unread_only and unresponded_only:
        status, data = conn.uid("SEARCH", None, "(UNSEEN UNANSWERED)")
    elif unread_only:
        status, data = conn.uid("SEARCH", None, "(UNSEEN)")
    elif unresponded_only:
        # Was missing — unresponded_only=True (without unread_only) fell through
        # to "ALL" and returned answered mail too, despite the documented
        # "emails without replies" behaviour.
        status, data = conn.uid("SEARCH", None, "(UNANSWERED)")
    else:
        # Include read too — IMAP search "ALL" returns the entire folder
        status, data = conn.uid("SEARCH", None, "ALL")

    if status != "OK" or not data[0]:
        conn.logout()
        return []

    uid_list = list(reversed(data[0].split()))[:max_results]
    cache = _get_cached_summaries()
    results = []

    for uid in uid_list:
        try:
            status, msg_data = conn.uid("FETCH", uid, "(RFC822.HEADER)")
            if status != "OK":
                continue
            raw_header = msg_data[0][1]
            msg = email.message_from_bytes(raw_header)

            subject = _decode_header(msg.get("Subject", "(no subject)"))
            sender = _decode_header(msg.get("From", "unknown"))
            date_str = msg.get("Date", "")
            message_id = msg.get("Message-ID", "")

            # Parse sender name
            sender_name, sender_addr = email.utils.parseaddr(sender)
            sender_display = sender_name or sender_addr

            # Check cache for summary
            cached = cache.get(subject, {})
            summary = cached.get("summary", "")

            results.append({
                "uid": uid.decode(),
                "message_id": message_id,
                "subject": subject,
                "from": sender_display,
                "from_address": sender_addr,
                "date": date_str,
                "summary": summary,
            })
        except Exception:
            continue

    conn.logout()
    return results


def _result_sort_time(result: dict) -> datetime:
    try:
        parsed = email.utils.parsedate_to_datetime(result.get("date") or "")
        if parsed:
            if parsed.tzinfo:
                parsed = parsed.astimezone().replace(tzinfo=None)
            return parsed
    except Exception:
        pass
    return datetime.min


def _list_emails_across_accounts(folder="INBOX", max_results=20,
                                 unresponded_only=False, unread_only=False):
    rows = _list_accounts_raw()
    combined = []
    errors = []
    for row in rows:
        account_selector = row.get("id") or row.get("name") or row.get("imap_user")
        account_name = row.get("name") or row.get("imap_user") or row.get("id") or "unknown"
        account_email = row.get("imap_user") or row.get("from_address") or ""
        try:
            account_results = _list_emails(
                folder=folder,
                max_results=max_results,
                unresponded_only=unresponded_only,
                unread_only=unread_only,
                account=account_selector,
            )
            for item in account_results:
                item["_account"] = account_name
                item["_account_email"] = account_email
                item["_account_id"] = row.get("id")
            combined.extend(account_results)
        except Exception as exc:
            errors.append(f"{account_name} ({account_email}): {exc}")
    combined.sort(key=_result_sort_time, reverse=True)
    return combined[:max_results], errors


def _search_emails(query, folders=None, max_results=20, account=None):
    """IMAP-search emails by free-text query. Matches FROM, SUBJECT, and
    body TEXT. Walks multiple folders so older threads outside INBOX
    (Sent/Archive) are still findable. Returns the same shape as
    _list_emails plus an `_folder` tag."""
    if not query or not str(query).strip():
        return []
    q = str(query).replace("\\", "\\\\").replace('"', '\\"')
    # Mail clients commonly use OR FROM/SUBJECT/TEXT to match either field.
    # IMAP SEARCH OR is binary, so we nest it.
    search_cmd = f'(OR OR FROM "{q}" SUBJECT "{q}" TEXT "{q}")'
    if folders is None:
        folders = ["INBOX", "Sent", "Archive"]
    cache = _get_cached_summaries()
    out = []
    conn = _imap_connect(account)
    touched = []
    try:
        for folder in folders:
            try:
                status, _ = conn.select(folder, readonly=True)
                if status != "OK":
                    continue
                status, data = conn.uid("SEARCH", None, search_cmd)
                if status != "OK" or not data or not data[0]:
                    continue
                uid_list = list(reversed(data[0].split()))[:max_results]
                for uid in uid_list:
                    try:
                        status, msg_data = conn.uid("FETCH", uid, "(RFC822.HEADER)")
                        if status != "OK":
                            continue
                        raw_header = msg_data[0][1]
                        msg = email.message_from_bytes(raw_header)
                        subject = _decode_header(msg.get("Subject", "(no subject)"))
                        sender = _decode_header(msg.get("From", "unknown"))
                        date_str = msg.get("Date", "")
                        message_id = msg.get("Message-ID", "")
                        to_str = _decode_header(msg.get("To", ""))
                        cc_str = _decode_header(msg.get("Cc", ""))
                        sender_name, sender_addr = email.utils.parseaddr(sender)
                        sender_display = sender_name or sender_addr
                        cached = cache.get(subject, {})
                        out.append({
                            "uid": uid.decode(),
                            "message_id": message_id,
                            "subject": subject,
                            "from": sender_display,
                            "from_address": sender_addr,
                            "to": to_str,
                            "cc": cc_str,
                            "date": date_str,
                            "_folder": folder,
                            "summary": cached.get("summary", ""),
                        })
                    except Exception:
                        continue
            except Exception:
                continue
    finally:
        try: conn.logout()
        except Exception: pass
    # Cap total across folders.
    return out[: max_results * len(folders)]


def _list_attachments_from_msg(msg):
    """Return attachment metadata."""
    if not msg.is_multipart():
        return []
    attachments = []
    idx = 0
    for part in msg.walk():
        if part.is_multipart():
            continue
        cd = str(part.get("Content-Disposition", ""))
        ct = part.get_content_type()
        if ct in ("text/plain", "text/html") and "attachment" not in cd:
            continue
        filename = part.get_filename()
        if filename:
            filename = _decode_header(filename)
        else:
            filename = f"attachment_{idx}"
        payload = part.get_payload(decode=True)
        size = len(payload) if payload else 0
        attachments.append({
            "index": idx,
            "filename": filename,
            "content_type": ct,
            "size": size,
        })
        idx += 1
    return attachments


def _extract_attachment_to_disk(msg, index, target_dir):
    """Extract a specific attachment to disk."""
    if not msg.is_multipart():
        return None
    idx = 0
    for part in msg.walk():
        if part.is_multipart():
            continue
        cd = str(part.get("Content-Disposition", ""))
        ct = part.get_content_type()
        if ct in ("text/plain", "text/html") and "attachment" not in cd:
            continue
        if idx == index:
            filename = part.get_filename()
            if filename:
                filename = _decode_header(filename)
            else:
                filename = f"attachment_{idx}"
            safe_name = re.sub(r"[^\w\s\-.]", "_", filename).strip()
            payload = part.get_payload(decode=True)
            if not payload:
                return None
            os.makedirs(target_dir, exist_ok=True)
            filepath = os.path.join(target_dir, safe_name)
            with open(filepath, "wb") as f:
                f.write(payload)
            return filepath
        idx += 1
    return None


def _read_email(uid=None, message_id=None, folder="INBOX", account=None):
    """Read full email content by UID or message-ID. account = mailbox selector."""
    cfg = _load_config(account)
    conn = _imap_connect(account)
    conn.select(folder, readonly=True)

    if message_id and not uid:
        status, data = conn.uid("SEARCH", None, f'(HEADER Message-ID "{message_id}")')
        if status != "OK" or not data[0]:
            conn.logout()
            return {"error": f"Email not found with Message-ID: {message_id}"}
        uid = data[0].split()[-1]

    if not uid:
        conn.logout()
        return {"error": "No UID or Message-ID provided"}

    status, msg_data = conn.uid("FETCH", _b(uid), "(BODY.PEEK[])")
    if status != "OK":
        conn.logout()
        return {"error": f"Failed to fetch email UID {uid}"}
    if not msg_data or not msg_data[0] or not isinstance(msg_data[0], tuple) or len(msg_data[0]) < 2:
        conn.logout()
        return {"error": f"Email not found with UID {uid}"}

    raw = msg_data[0][1]
    msg = email.message_from_bytes(raw)

    subject = _decode_header(msg.get("Subject", "(no subject)"))
    sender = _decode_header(msg.get("From", "unknown"))
    date_str = msg.get("Date", "")
    message_id_header = msg.get("Message-ID", "")
    body = _extract_text(msg)
    attachments = _list_attachments_from_msg(msg)

    sender_name, sender_addr = email.utils.parseaddr(sender)

    conn.logout()
    return {
        "uid": uid.decode() if isinstance(uid, bytes) else str(uid),
        "account": cfg.get("account_name") or cfg.get("imap_user") or "default",
        "account_email": cfg.get("imap_user") or cfg.get("from_address") or "",
        "account_id": cfg.get("account_id"),
        "message_id": message_id_header,
        "subject": subject,
        "from": sender_name or sender_addr,
        "from_address": sender_addr,
        "date": date_str,
        "body": body[:8000],
        "attachments": attachments,
    }


def _read_email_across_accounts(uid=None, message_id=None, folder="INBOX"):
    rows = _list_accounts_raw()
    matches = []
    errors = []
    for row in rows:
        account_selector = row.get("id") or row.get("name") or row.get("imap_user")
        account_name = row.get("name") or row.get("imap_user") or row.get("id") or "unknown"
        account_email = row.get("imap_user") or row.get("from_address") or ""
        result = _read_email(
            uid=uid,
            message_id=message_id,
            folder=folder,
            account=account_selector,
        )
        if "error" in result:
            errors.append(f"{account_name} <{account_email}>: {result['error']}")
            continue
        matches.append(result)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        accounts = ", ".join(
            f"{m.get('account')} <{m.get('account_email')}>" for m in matches
        )
        return {
            "error": (
                f"UID {uid or message_id} exists in multiple accounts: {accounts}. "
                "Call read_email again with the account name/email."
            )
        }
    return {"error": f"Email not found in any configured account. Checked: {'; '.join(errors)}"}


def _smtp_ready(cfg: dict) -> bool:
    return bool(cfg.get("smtp_host") and cfg.get("smtp_user") and cfg.get("smtp_password"))


def _resolve_send_config(account=None):
    cfg = _load_config(account)
    if _smtp_ready(cfg):
        return account, cfg
    if account:
        raise ValueError(f"Email account {cfg.get('account_name') or account} has no SMTP configured")
    for row in _list_accounts_raw():
        selector = row.get("id") or row.get("name") or row.get("imap_user")
        trial = _load_config(selector)
        if _smtp_ready(trial):
            return selector, trial
    raise ValueError("No SMTP-capable email account configured")


def _smtp_connect(account=None, cfg=None):
    """Connect to SMTP server, returns logged-in connection."""
    cfg = cfg or _load_config(account)
    if not _smtp_ready(cfg):
        raise ValueError(f"Email account {cfg.get('account_name') or account or 'default'} has no SMTP configured")
    port = int(cfg.get("smtp_port") or 465)
    security = str(cfg.get("smtp_security") or "").strip().lower()
    if security not in {"ssl", "starttls", "none"}:
        security = "starttls" if port == 587 else "ssl"
    if security == "starttls":
        conn = smtplib.SMTP(
            cfg["smtp_host"],
            port,
            timeout=EMAIL_SOCKET_TIMEOUT,
        )
        conn.starttls()
    elif security == "ssl":
        conn = smtplib.SMTP_SSL(
            cfg["smtp_host"],
            port,
            timeout=EMAIL_SOCKET_TIMEOUT,
        )
    else:
        conn = smtplib.SMTP(
            cfg["smtp_host"],
            port,
            timeout=EMAIL_SOCKET_TIMEOUT,
        )
    if cfg["smtp_user"] and cfg["smtp_password"]:
        conn.login(cfg["smtp_user"], cfg["smtp_password"])
    return conn


def _send_email(to, subject, body, in_reply_to=None, references=None, cc=None, bcc=None, account=None):
    """Send an email via SMTP. Returns dict with status."""
    send_account, cfg = _resolve_send_config(account)
    msg = EmailMessage()
    msg["From"] = _clean_header_value(cfg["from_address"])
    msg["To"] = _clean_header_value(to if isinstance(to, str) else ", ".join(to))
    msg["Subject"] = _clean_header_value(subject)
    if cc:
        msg["Cc"] = _clean_header_value(cc if isinstance(cc, str) else ", ".join(cc))
    if in_reply_to:
        msg["In-Reply-To"] = _clean_header_value(in_reply_to)
    if references:
        msg["References"] = _clean_header_value(references if isinstance(references, str) else " ".join(references))
    if "Date" not in msg:
        msg["Date"] = email.utils.formatdate(localtime=True)
    if "Message-ID" not in msg:
        msg["Message-ID"] = email.utils.make_msgid()
    msg.set_content(body)

    recipients = []
    if isinstance(to, str):
        recipients.extend([a.strip() for a in to.split(",") if a.strip()])
    else:
        recipients.extend(to)
    if cc:
        recipients.extend([a.strip() for a in cc.split(",")] if isinstance(cc, str) else cc)
    if bcc:
        recipients.extend([a.strip() for a in bcc.split(",")] if isinstance(bcc, str) else bcc)

    conn = _smtp_connect(send_account, cfg=cfg)
    try:
        conn.send_message(msg, from_addr=cfg["from_address"], to_addrs=recipients)
    finally:
        conn.quit()

    sent_folder = None
    sent_uid = None
    try:
        imap = _imap_connect(send_account)
        try:
            sent_folder = _detect_sent_folder(imap)
            append_st, append_data = imap.append(sent_folder, "\\Seen", None, msg.as_bytes())
            if append_st == "OK" and append_data:
                m = re.search(rb"APPENDUID\s+\d+\s+(\d+)", append_data[0] or b"")
                if m:
                    sent_uid = m.group(1).decode("ascii", errors="ignore")
        finally:
            imap.logout()
    except Exception:
        # Delivery already succeeded; Sent-copy failure should not turn a sent
        # message into a hard failure for the user.
        pass

    return {
        "sent": True,
        "to": recipients,
        "subject": subject,
        "account": cfg.get("account_name"),
        "account_id": cfg.get("account_id"),
        "sent_folder": sent_folder,
        "sent_uid": sent_uid,
        "message_id": msg.get("Message-ID", ""),
    }


def _reply_to_email(uid, body, folder="INBOX", reply_all=False, account=None):
    """Reply to an existing email by UID. Threads via In-Reply-To/References."""
    conn = _imap_connect(account)
    conn.select(folder, readonly=True)
    status, msg_data = conn.uid("FETCH", _b(uid), "(BODY.PEEK[])")
    conn.logout()
    if status != "OK" or not msg_data or not msg_data[0]:
        return {"error": f"Failed to fetch email UID {uid}"}
    raw = msg_data[0][1]
    orig = email.message_from_bytes(raw)

    orig_subject = _decode_header(orig.get("Subject", ""))
    reply_subject = orig_subject if orig_subject.lower().startswith("re:") else f"Re: {orig_subject}"
    orig_message_id = orig.get("Message-ID", "")
    orig_references = orig.get("References", "")
    new_references = (orig_references + " " + orig_message_id).strip() if orig_references else orig_message_id

    sender = _decode_header(orig.get("From", ""))
    _, sender_addr = email.utils.parseaddr(sender)
    to_addrs = sender_addr

    cc = None
    if reply_all:
        cc_addrs = []
        for header_name in ("To", "Cc"):
            for _, addr in email.utils.getaddresses([orig.get(header_name, "")]):
                if addr and addr != sender_addr:
                    cc_addrs.append(addr)
        if cc_addrs:
            cc = ", ".join(cc_addrs)

    return _send_email(
        to=to_addrs,
        subject=reply_subject,
        body=body,
        in_reply_to=orig_message_id,
        references=new_references,
        cc=cc,
        account=account,
    )


def _set_flag(uid, folder, flag, add=True, account=None):
    """Add or remove an IMAP flag (e.g. \\Seen, \\Answered, \\Deleted)."""
    conn = _imap_connect(account)
    conn.select(folder)
    op = "+FLAGS" if add else "-FLAGS"
    try:
        status, data = conn.uid("STORE", _b(uid), op, flag)
        if add and flag == "\\Deleted":
            conn.expunge()
        return status == "OK" and bool(data and data[0])
    except Exception:
        return False
    finally:
        conn.logout()


def _bulk_set_flag(uids, folder, flag, add=True, account=None):
    """Add/remove an IMAP flag on MANY messages in one connection.
    `uids` is a list; we issue a single STORE over the comma-joined set
    (IMAP supports message-set syntax). Returns count attempted."""
    if not uids:
        return 0
    conn = _imap_connect(account)
    touched = []
    try:
        conn.select(folder)
        op = "+FLAGS" if add else "-FLAGS"
        msg_set = ",".join(str(u) for u in uids)
        try:
            status, data = conn.uid("FETCH", _b(msg_set), "(UID)")
        except Exception:
            return 0
        touched = _uid_fetch_rows(data)
        if status != "OK" or not touched:
            return 0
        status, data = conn.uid("STORE", _b(msg_set), op, flag)
        if add and flag == "\\Deleted":
            conn.expunge()
        if status != "OK":
            return 0
    finally:
        conn.logout()
    return len(touched)


def _bulk_move(uids, source_folder, dest_folder, account=None, role: str = ""):
    """Move MANY messages between folders in one connection."""
    if not uids:
        return 0
    conn = _imap_connect(account)
    moved = 0
    try:
        conn.select(source_folder)
        dest_folder = _resolve_folder(conn, dest_folder, role or _folder_role_from_name(dest_folder))
        msg_set = ",".join(str(u) for u in uids)
        try:
            status, data = conn.uid("FETCH", _b(msg_set), "(UID)")
        except Exception:
            return 0
        existing = _uid_fetch_rows(data)
        if not existing:
            return 0
        moved = len(existing)
        status, _ = conn.uid("MOVE", _b(msg_set), dest_folder)
        if status != "OK":
            # Fallback: UID copy + flag-delete + expunge
            status, _ = conn.uid("COPY", _b(msg_set), dest_folder)
            if status != "OK":
                return 0
            status, _ = conn.uid("STORE", _b(msg_set), "+FLAGS", "\\Deleted")
            if status != "OK":
                return 0
            conn.expunge()
    finally:
        conn.logout()
    return moved


def _search_uids(folder="INBOX", criteria="UNSEEN", account=None):
    """Return a list of UIDs matching an IMAP search (e.g. UNSEEN,
    ALL, ANSWERED). Used to resolve selectors like all_unread → uids."""
    conn = _imap_connect(account)
    try:
        conn.select(folder, readonly=True)
        status, data = conn.uid("SEARCH", None, criteria)
        if status != "OK" or not data or not data[0]:
            return []
        return data[0].split()
    finally:
        conn.logout()


def _move_message(uid, source_folder, dest_folder, account=None, role: str = ""):
    """Move a message between folders. Tries IMAP MOVE, falls back to copy+delete."""
    conn = _imap_connect(account)
    conn.select(source_folder)
    try:
        dest_folder = _resolve_folder(conn, dest_folder, role or _folder_role_from_name(dest_folder))
        try:
            status, data = conn.uid("FETCH", _b(uid), "(UID)")
        except Exception:
            return False
        existing = _uid_fetch_rows(data)
        if status != "OK" or not existing:
            return False
        status, _ = conn.uid("MOVE", _b(uid), dest_folder)
        if status == "OK":
            return True
        # Fallback: UID copy + delete
        status, _ = conn.uid("COPY", _b(uid), dest_folder)
        if status != "OK":
            return False
        status, _ = conn.uid("STORE", _b(uid), "+FLAGS", "\\Deleted")
        if status != "OK":
            return False
        conn.expunge()
        ok = True
    finally:
        conn.logout()
    return ok


def _delete_email(uid, folder="INBOX", permanent=False, account=None):
    """Delete an email. By default moves to Trash; permanent=True expunges."""
    cfg = _load_config(account)
    if permanent:
        return _set_flag(uid, folder, "\\Deleted", add=True, account=account)
    return _move_message(uid, folder, cfg["trash_folder"], account=account, role="trash")


def _archive_email(uid, folder="INBOX", account=None):
    """Move an email to the archive folder."""
    cfg = _load_config(account)
    return _move_message(uid, folder, cfg["archive_folder"], account=account, role="archive")


def _download_attachment(uid, index, folder="INBOX", account=None):
    """Extract a specific attachment to disk and return its local path."""
    conn = _imap_connect(account)
    conn.select(folder, readonly=True)
    status, msg_data = conn.uid("FETCH", _b(uid), "(BODY.PEEK[])")
    conn.logout()
    if status != "OK":
        return {"error": f"Failed to fetch email UID {uid}"}
    raw = msg_data[0][1]
    msg = email.message_from_bytes(raw)

    target_dir = DATA_DIR / "mail-attachments" / f"{folder}_{uid}"
    filepath = _extract_attachment_to_disk(msg, index, target_dir)
    if not filepath:
        return {"error": f"Attachment index {index} not found"}
    size = os.path.getsize(filepath)
    return {"path": filepath, "filename": os.path.basename(filepath), "size": size}


# ── MCP Tool Registration ──


@server.list_tools()
async def list_tools() -> list[Tool]:
    # The user may have multiple IMAP accounts configured. Every tool accepts an
    # optional `account` param — match by name (e.g. "work"), email address,
    # or account id. Leave it out to use the default account.
    ACCOUNT_PROP = {
        "account": {
            "type": "string",
            "description": "Which email account to use (name, email, or id). "
                           "Omit to use the default account. Use list_email_accounts to discover available accounts.",
        },
    }
    return [
        Tool(
            name="list_email_accounts",
            description=(
                "List the email accounts configured in Odysseus. Returns each account's "
                "name, email address, and whether it's the default. Use this first when "
                "the user asks about a specific inbox by name (e.g. 'check work')."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="list_emails",
            description=(
                "List unread or unresponded emails from the inbox. "
                "Returns subject, sender, date, and cached AI summary for each. "
                "Use this to check what emails need attention. "
                "Pass `account` to scan a non-default mailbox."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "folder": {
                        "type": "string",
                        "description": "IMAP folder to check (default: INBOX)",
                        "default": "INBOX",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of emails to return (default: 20)",
                        "default": 20,
                    },
                    "unresponded_only": {
                        "type": "boolean",
                        "description": "Only show emails without replies (default: false)",
                        "default": False,
                    },
                    "unread_only": {
                        "type": "boolean",
                        "description": "Only show unread emails. Default false so latest/all inbox requests match normal mail clients.",
                        "default": False,
                    },
                    **ACCOUNT_PROP,
                },
                "required": [],
            },
        ),
        Tool(
            name="download_attachment",
            description=(
                "Download an email attachment to the local disk so you can read it. "
                "Returns the local file path which you can then read with read_file. "
                "Use this when you need to review a document, spreadsheet, or other "
                "file attached to an email."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Email UID from list_emails"},
                    "index": {"type": "integer", "description": "Attachment index (from read_email's attachments list)"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)", "default": "INBOX"},
                    **ACCOUNT_PROP,
                },
                "required": ["uid", "index"],
            },
        ),
        Tool(
            name="send_email",
            description=(
                "Send a new email via SMTP. Provide recipient(s), subject, and body. "
                "For replying to an existing thread, use reply_to_email instead. "
                "Pass `account` to send from a non-default mailbox."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address(es), comma-separated"},
                    "subject": {"type": "string", "description": "Email subject line"},
                    "body": {"type": "string", "description": "Plain text body"},
                    "cc": {"type": "string", "description": "CC address(es), comma-separated (optional)"},
                    "bcc": {"type": "string", "description": "BCC address(es), comma-separated (optional)"},
                    **ACCOUNT_PROP,
                },
                "required": ["to", "subject", "body"],
            },
        ),
        Tool(
            name="reply_to_email",
            description=(
                "Reply to an existing email by UID. Automatically threads the reply with "
                "In-Reply-To and References headers, prefixes 'Re:' on the subject, and "
                "uses the original sender as the recipient. Set reply_all=true to also CC "
                "the original To/Cc recipients. For follow-up 'reply ...' requests, use "
                "the exact UID from the latest list_emails/read_email result; never invent UID 1."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Exact Email UID from list_emails/read_email; never invent UID 1"},
                    "body": {"type": "string", "description": "Reply body text"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)", "default": "INBOX"},
                    "reply_all": {"type": "boolean", "description": "Reply to all recipients (default: false)", "default": False},
                    **ACCOUNT_PROP,
                },
                "required": ["uid", "body"],
            },
        ),
        Tool(
            name="archive_email",
            description="Move an email out of the inbox into the Archive folder. Use after handling an email you want to keep but no longer need in the inbox.",
            inputSchema={
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Email UID from list_emails"},
                    "folder": {"type": "string", "description": "Source folder (default: INBOX)", "default": "INBOX"},
                    **ACCOUNT_PROP,
                },
                "required": ["uid"],
            },
        ),
        Tool(
            name="delete_email",
            description="Delete an email. By default moves it to the Trash folder; pass permanent=true to expunge immediately.",
            inputSchema={
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Email UID from list_emails"},
                    "folder": {"type": "string", "description": "Source folder (default: INBOX)", "default": "INBOX"},
                    "permanent": {"type": "boolean", "description": "Hard-delete instead of move to Trash", "default": False},
                    **ACCOUNT_PROP,
                },
                "required": ["uid"],
            },
        ),
        Tool(
            name="mark_email_read",
            description="Mark an email as read (\\Seen flag) or unread (read=false).",
            inputSchema={
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Email UID"},
                    "folder": {"type": "string", "description": "IMAP folder", "default": "INBOX"},
                    "read": {"type": "boolean", "description": "True to mark read, false to mark unread", "default": True},
                    **ACCOUNT_PROP,
                },
                "required": ["uid"],
            },
        ),
        Tool(
            name="bulk_email",
            description=(
                "Perform one action on MANY emails at once — the efficient way to "
                "'mark all as read', 'archive these', 'delete all spam', etc. Select "
                "messages either by an explicit `uids` list OR by `all_unread: true` "
                "(operates on every unread message in the folder). Far better than "
                "calling mark_email_read / archive_email once per message."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["mark_read", "mark_unread", "archive", "delete", "junk"],
                        "description": "What to do to every selected message.",
                    },
                    "uids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Explicit list of UIDs. Omit if using all_unread.",
                    },
                    "all_unread": {
                        "type": "boolean",
                        "description": "Operate on ALL unread messages in the folder (ignores uids).",
                        "default": False,
                    },
                    "folder": {"type": "string", "description": "IMAP folder", "default": "INBOX"},
                    "permanent": {"type": "boolean", "description": "For delete: expunge instead of moving to Trash.", "default": False},
                    **ACCOUNT_PROP,
                },
                "required": ["action"],
            },
        ),
        Tool(
            name="search_emails",
            description=(
                "Search emails by free-text query (sender, subject, or body). "
                "Walks INBOX + Sent + Archive by default so older threads are findable, "
                "not just recent unread. Use this whenever the user names a person or "
                "topic that isn't in the most recent inbox slice — e.g. 'Sara Sotheby's', "
                "'invoice from EY', 'last email about the property'. Returns matching "
                "emails with their UIDs so you can read_email or reply_to_email."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Free-text query. Matches FROM, SUBJECT, and body TEXT.",
                    },
                    "folders": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Folders to search (default: INBOX, Sent, Archive)",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max results per folder (default: 20)",
                        "default": 20,
                    },
                    **ACCOUNT_PROP,
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="read_email",
            description=(
                "Read the full content of a specific email. "
                "Provide either the UID (from list_emails) or a Message-ID. "
                "Returns the subject, sender, date, and full body text."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "uid": {
                        "type": "string",
                        "description": "Email UID from list_emails results",
                    },
                    "message_id": {
                        "type": "string",
                        "description": "RFC Message-ID header value",
                    },
                    "folder": {
                        "type": "string",
                        "description": "IMAP folder (default: INBOX)",
                        "default": "INBOX",
                    },
                    **ACCOUNT_PROP,
                },
                "required": [],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        if name == "list_email_accounts":
            rows = _list_accounts_raw()
            if not rows:
                return [TextContent(type="text", text="No email accounts configured. Legacy single-account mode active.")]
            lines = [f"Found {len(rows)} email account(s):\n"]
            for r in rows:
                star = " (default)" if r.get("is_default") else ""
                lines.append(
                    f"- **{r['name']}**{star}\n"
                    f"  email: {r.get('imap_user') or r.get('from_address') or '(unknown)'}\n"
                    f"  id: {r['id']}"
                )
            return [TextContent(type="text", text="\n".join(lines))]

        acct = arguments.get("account")  # consumed by all email ops

        if name == "list_emails":
            max_results = arguments.get("max_results", arguments.get("limit", 20))
            unresponded_only = arguments.get("unresponded_only", False)
            unread_only = arguments.get("unread_only", False)
            # Build a header note so the LLM always knows which account was hit
            # AND what other accounts exist. Prevents "I can see emails" →
            # user: "I have 2 inboxes" → "which one?" loop.
            all_accounts = _list_accounts_raw()
            header_lines = []
            errors = []
            if len(all_accounts) >= 2 and not acct:
                results, errors = _list_emails_across_accounts(
                    folder=arguments.get("folder", "INBOX"),
                    max_results=max_results,
                    unresponded_only=unresponded_only,
                    unread_only=unread_only,
                )
                account_names = [
                    f"{a.get('name') or a.get('imap_user')} <{a.get('imap_user') or a.get('from_address') or '?'}>"
                    for a in all_accounts
                ]
                header_lines.append(
                    f"[EMAIL ACCOUNT CONTEXT: No `account` was provided, so this result is merged across configured accounts: "
                    f"{', '.join(account_names)}. Each row includes its source account.]\n"
                )
            else:
                results = _list_emails(
                    folder=arguments.get("folder", "INBOX"),
                    max_results=max_results,
                    unresponded_only=unresponded_only,
                    unread_only=unread_only,
                    account=acct,
                )
                active_cfg = _load_config(acct)
                if active_cfg.get("account_name") or active_cfg.get("imap_user"):
                    for item in results:
                        item["_account"] = active_cfg.get("account_name") or active_cfg.get("imap_user") or "default"
                        item["_account_email"] = active_cfg.get("imap_user") or ""

            if len(all_accounts) >= 2 and acct:
                active_cfg = _load_config(acct)
                active_name = active_cfg.get("account_name") or "default"
                active_email = active_cfg.get("imap_user") or ""
                other = [
                    f"{a['name']} <{a.get('imap_user') or a.get('from_address') or '?'}>"
                    for a in all_accounts
                    if a['id'] != active_cfg.get("account_id")
                ]
                header_lines.append(
                    f"[EMAIL ACCOUNT CONTEXT: This result is ONLY from account `{active_name}` ({active_email}). "
                    f"Other configured accounts: {', '.join(other)}. "
                    f"If the user asks for Gmail/another inbox, call list_emails again with `account` set to that account name or email.]\n"
                )
            if errors:
                header_lines.append("[EMAIL ACCOUNT ERRORS: " + "; ".join(errors) + "]\n")

            if not results:
                msg = "No unread/unresponded emails found."
                if header_lines:
                    msg = "\n".join(header_lines) + msg
                return [TextContent(type="text", text=msg)]

            lines = header_lines + [f"Found {len(results)} email(s):\n"]
            for i, em in enumerate(results, 1):
                line = f"{i}. **{em['subject']}**\n   From: {em['from']} ({em['from_address']})\n   Date: {em['date']}\n   UID: {em['uid']}"
                if em.get("_account"):
                    account_label = em.get("_account")
                    if em.get("_account_email"):
                        account_label += f" <{em['_account_email']}>"
                    line += f"\n   Account: {account_label}"
                if em.get("summary"):
                    line += f"\n   Summary: {em['summary']}"
                lines.append(line)
            return [TextContent(type="text", text="\n\n".join(lines))]

        elif name == "download_attachment":
            uid = arguments.get("uid")
            index = arguments.get("index")
            folder = arguments.get("folder", "INBOX")
            if uid is None or index is None:
                return [TextContent(type="text", text="Error: uid and index are required")]
            result = _download_attachment(uid, index, folder, account=acct)
            if "error" in result:
                return [TextContent(type="text", text=f"Error: {result['error']}")]
            text = (
                f"Attachment downloaded to: `{result['path']}`\n"
                f"Filename: {result['filename']}\n"
                f"Size: {result['size']} bytes\n\n"
                f"You can now read this file using the read_file tool."
            )
            return [TextContent(type="text", text=text)]

        elif name == "search_emails":
            q = arguments.get("query", "")
            folders = arguments.get("folders") or None
            max_results = arguments.get("max_results", 20)
            try:
                hits = _search_emails(q, folders=folders, max_results=max_results, account=acct)
            except Exception as e:
                return [TextContent(type="text", text=f"Search failed: {e}")]
            if not hits:
                return [TextContent(type="text", text=f'No emails matched "{q}".')]
            lines = [f'Found {len(hits)} email(s) matching "{q}":\n']
            for i, em in enumerate(hits, 1):
                lines.append(
                    f"{i}. **{em['subject']}**\n"
                    f"   From: {em['from']} ({em['from_address']})\n"
                    f"   Date: {em['date']}\n"
                    f"   Folder: {em.get('_folder', 'INBOX')}\n"
                    f"   UID: {em['uid']}"
                )
                if em.get('to'):
                    lines.append(f"   To: {em['to']}")
                if em.get('summary'):
                    lines.append(f"   Summary: {em['summary']}")
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "read_email":
            all_accounts = _list_accounts_raw()
            if len(all_accounts) >= 2 and not acct:
                result = _read_email_across_accounts(
                    uid=arguments.get("uid"),
                    message_id=arguments.get("message_id"),
                    folder=arguments.get("folder", "INBOX"),
                )
            else:
                result = _read_email(
                    uid=arguments.get("uid"),
                    message_id=arguments.get("message_id"),
                    folder=arguments.get("folder", "INBOX"),
                    account=acct,
                )
            if "error" in result:
                return [TextContent(type="text", text=f"Error: {result['error']}")]

            text = (
                f"**Subject:** {result['subject']}\n"
                f"**From:** {result['from']} ({result['from_address']})\n"
                f"**Date:** {result['date']}\n"
                f"**UID:** {result['uid']}\n"
                f"**Account:** {result.get('account', 'default')} ({result.get('account_email', '')})\n"
                f"**Message-ID:** {result['message_id']}\n"
            )
            if result.get('attachments'):
                text += f"\n**Attachments ({len(result['attachments'])}):**\n"
                for a in result['attachments']:
                    size_kb = a['size'] // 1024
                    text += f"  - [{a['index']}] {a['filename']} ({a['content_type']}, {size_kb}KB)\n"
                text += "\n_Use `download_attachment` with the UID and index to download._\n"
            text += f"\n---\n\n{result['body']}"
            return [TextContent(type="text", text=text)]

        elif name == "send_email":
            to = arguments.get("to")
            subject = arguments.get("subject")
            body = arguments.get("body")
            if not to or not subject or body is None:
                return [TextContent(type="text", text="Error: to, subject, and body are required")]
            result = _send_email(
                to=to,
                subject=subject,
                body=body,
                cc=arguments.get("cc"),
                bcc=arguments.get("bcc"),
                account=acct,
            )
            acct_note = f" (from {result['account']})" if result.get("account") else ""
            return [TextContent(type="text", text=f"Sent email to {result['to']} with subject '{result['subject']}'{acct_note}.")]

        elif name == "reply_to_email":
            uid = arguments.get("uid")
            body = arguments.get("body")
            if not uid or body is None:
                return [TextContent(type="text", text="Error: uid and body are required")]
            result = _reply_to_email(
                uid=uid,
                body=body,
                folder=arguments.get("folder", "INBOX"),
                reply_all=bool(arguments.get("reply_all", False)),
                account=acct,
            )
            if "error" in result:
                return [TextContent(type="text", text=f"Error: {result['error']}")]
            # Mark original as answered
            try:
                _set_flag(uid, arguments.get("folder", "INBOX"), "\\Answered", add=True, account=acct)
            except Exception:
                pass
            return [TextContent(type="text", text=f"Replied to UID {uid}: '{result['subject']}' → {result['to']}")]

        elif name == "archive_email":
            uid = arguments.get("uid")
            if not uid:
                return [TextContent(type="text", text="Error: uid is required")]
            ok = _archive_email(uid, arguments.get("folder", "INBOX"), account=acct)
            return [TextContent(type="text", text=f"{'Archived' if ok else 'Failed to archive'} UID {uid}")]

        elif name == "delete_email":
            uid = arguments.get("uid")
            if not uid:
                return [TextContent(type="text", text="Error: uid is required")]
            ok = _delete_email(
                uid,
                arguments.get("folder", "INBOX"),
                permanent=bool(arguments.get("permanent", False)),
                account=acct,
            )
            return [TextContent(type="text", text=f"{'Deleted' if ok else 'Failed to delete'} UID {uid}")]

        elif name == "mark_email_read":
            uid = arguments.get("uid")
            if not uid:
                return [TextContent(type="text", text="Error: uid is required")]
            read = bool(arguments.get("read", True))
            ok = _set_flag(uid, arguments.get("folder", "INBOX"), "\\Seen", add=read, account=acct)
            state = "read" if read else "unread"
            return [TextContent(type="text", text=f"{'Marked' if ok else 'Failed to mark'} UID {uid} as {state}")]

        elif name == "bulk_email":
            action = arguments.get("action", "")
            folder = arguments.get("folder", "INBOX")
            all_unread = bool(arguments.get("all_unread", False))
            uids = arguments.get("uids") or []
            if all_unread:
                uids = _search_uids(folder, "UNSEEN", account=acct)
            if not uids:
                return [TextContent(type="text", text="No messages selected (pass uids or all_unread=true).")]
            requested_n = len(uids)
            changed_n = 0
            try:
                if action == "mark_read":
                    changed_n = _bulk_set_flag(uids, folder, "\\Seen", add=True, account=acct)
                    verb = "marked read"
                elif action == "mark_unread":
                    changed_n = _bulk_set_flag(uids, folder, "\\Seen", add=False, account=acct)
                    verb = "marked unread"
                elif action == "archive":
                    cfg = _load_config(acct)
                    changed_n = _bulk_move(uids, folder, cfg["archive_folder"], account=acct, role="archive")
                    verb = "archived"
                elif action == "junk":
                    cfg = _load_config(acct)
                    junk_folder = cfg.get("junk_folder") or "Junk"
                    changed_n = _bulk_move(uids, folder, junk_folder, account=acct, role="junk")
                    verb = "moved to Junk"
                elif action == "delete":
                    permanent = bool(arguments.get("permanent", False))
                    if permanent:
                        changed_n = _bulk_set_flag(uids, folder, "\\Deleted", add=True, account=acct)
                        verb = "permanently deleted"
                    else:
                        cfg = _load_config(acct)
                        changed_n = _bulk_move(uids, folder, cfg["trash_folder"], account=acct, role="trash")
                        verb = "moved to Trash"
                else:
                    return [TextContent(type="text", text=f"Unknown bulk action: {action!r}. Use mark_read/mark_unread/archive/delete/junk.")]
            except Exception as e:
                return [TextContent(type="text", text=f"Bulk {action} failed after partial work: {e}")]
            if changed_n <= 0:
                return [TextContent(type="text", text=f"No matching UIDs found in {folder}; 0 of {requested_n} email(s) {verb}.")]
            suffix = "" if changed_n == requested_n else f" ({changed_n} of {requested_n} requested UIDs matched)"
            return [TextContent(type="text", text=f"Done — {changed_n} email(s) {verb}{suffix}.")]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}")]


# ── Main ──

async def run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(run())
