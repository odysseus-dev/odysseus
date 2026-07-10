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
import uuid
from contextvars import ContextVar

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

server = Server("email")
EMAIL_SOCKET_TIMEOUT = float(os.environ.get("EMAIL_SOCKET_TIMEOUT", "20"))
from src.constants import DATA_DIR as _DATA_DIR, APP_DB, EMAIL_CACHE_DB, SETTINGS_FILE as _SETTINGS_FILE, MAIL_ATTACHMENTS_DIR
DATA_DIR = Path(_DATA_DIR)


def _b(value) -> bytes:
    return str(value).encode()


def _q(name: str) -> str:
    """Quote an IMAP mailbox name for commands that take mailbox args."""
    return '"' + (name or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def _uid_fetch_rows(data) -> list:
    return [d for d in (data or []) if isinstance(d, bytes) and b"UID " in d]

# ── Config ──
# Multi-account aware. Accounts live in data/app.db :: email_accounts.
# Callers can pass `account=` (match by name, user, or id) to pick a specific
# inbox; None resolves to the default row. Falls back to env vars / settings.json
# flat keys when no DB row matches (legacy single-account behaviour).

_ACCOUNT_CACHE: dict = {}  # key = normalized account selector -> config dict
_MCP_OWNER_ARG = "_odysseus_owner"
_CURRENT_OWNER: ContextVar[str | None] = ContextVar("email_mcp_owner", default=None)
_OWNER_ENV_KEYS = ("ODYSSEUS_MCP_EMAIL_OWNER", "ODYSSEUS_EMAIL_OWNER")
_OWNER_SCOPE_ERROR = (
    "Error: email MCP requires an authenticated owner or ODYSSEUS_MCP_EMAIL_OWNER "
    "when owner-scoped email accounts are configured."
)


def _clean_header_value(value) -> str:
    """EmailMessage rejects CR/LF in assigned header values; unfold safely."""
    if value is None:
        return ""
    return re.sub(r"[\r\n]+[ \t]*", " ", str(value)).strip()


def _db_path() -> Path:
    return Path(APP_DB)


def _configured_owner() -> str | None:
    for key in _OWNER_ENV_KEYS:
        owner = os.environ.get(key, "").strip()
        if owner:
            return owner
    return None


def _current_owner() -> str:
    owner = _CURRENT_OWNER.get()
    return str(owner or _configured_owner() or "").strip()


def _account_owner(row: dict) -> str:
    return str(row.get("owner") or "").strip()


def _has_owner_scoped_accounts(rows: list[dict]) -> bool:
    return any(_account_owner(r) for r in rows)


def _account_visible_to_owner(row: dict, owner: str) -> bool:
    row_owner = _account_owner(row)
    if row_owner == owner:
        return True
    if row_owner:
        return False
    # Legacy ownerless accounts are only visible to a scoped caller when the
    # mailbox itself matches the owner, mirroring the HTTP email route fallback.
    owner_l = owner.lower()
    return owner_l in {
        str(row.get("imap_user") or "").strip().lower(),
        str(row.get("from_address") or "").strip().lower(),
    }


def _filter_accounts_for_owner(rows: list[dict]) -> list[dict]:
    owner = _current_owner()
    if owner:
        return [r for r in rows if _account_visible_to_owner(r, owner)]

    if _has_owner_scoped_accounts(rows):
        return []
    return rows


def _mcp_owner_required(rows: list[dict] | None = None) -> bool:
    if _current_owner():
        return False
    rows = rows if rows is not None else _read_accounts_from_db()
    return _has_owner_scoped_accounts(rows)


def _load_email_writing_style() -> str:
    """Return the existing Settings > Email > Writing Style value."""
    try:
        settings_path = DATA_DIR / "settings.json"
        if not settings_path.exists():
            return ""
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        return str(settings.get("email_writing_style") or "").strip()
    except Exception:
        return ""


def _writing_style_guidance() -> str:
    style = _load_email_writing_style()
    if not style:
        return (
            "No saved writing style is configured in Settings > Email > Writing Style. "
            "Use a concise, natural tone and do not invent facts."
        )
    return (
        "Use this saved writing style from Settings > Email > Writing Style when "
        "drafting the body. It overrides generic tone guidance:\n"
        f"{style}"
    )


def _default_document_owner() -> str | None:
    """Best-effort owner for MCP-created documents.

    MCP stdio tools do not receive the browser request's authenticated user,
    but the document library is owner-filtered. Stamp drafts to the configured
    single/default admin so assistant-created email drafts are visible.
    """
    owner = os.environ.get("ODYSSEUS_DOCUMENT_OWNER", "").strip()
    if owner:
        return owner
    try:
        auth_path = DATA_DIR / "auth.json"
        if not auth_path.exists():
            return None
        users = (json.loads(auth_path.read_text(encoding="utf-8")).get("users") or {})
        if not isinstance(users, dict) or not users:
            return None
        admins = [name for name, data in users.items() if isinstance(data, dict) and data.get("is_admin")]
        if len(admins) == 1:
            return admins[0]
        if len(users) == 1:
            return next(iter(users))
        return admins[0] if admins else next(iter(users))
    except Exception:
        return None


def _read_accounts_from_db() -> list:
    """Return all enabled email account rows. Empty list if missing. Never raises."""
    path = _db_path()
    if not path.exists():
        return []
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        columns = {r[1] for r in conn.execute("PRAGMA table_info(email_accounts)").fetchall()}
        owner_select = "owner" if "owner" in columns else "NULL AS owner"
        smtp_security_select = "smtp_security" if "smtp_security" in columns else "'' AS smtp_security"
        rows = conn.execute(f"""
            SELECT id, {owner_select}, name, is_default, enabled,
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


def _list_accounts_raw() -> list:
    """Return owner-visible email account rows for the active MCP call."""
    return _filter_accounts_for_owner(_read_accounts_from_db())


def _resolve_account_from_rows(rows: list[dict], selector: str | None) -> dict | None:
    """Given a selector (None = default, or a name/user/id string), return the
    matching row or None. Matching is case-insensitive substring on name +
    imap_user + from_address, plus exact id match."""
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


def _resolve_account(selector: str | None) -> dict | None:
    return _resolve_account_from_rows(_list_accounts_raw(), selector)


def _load_config(account: str | None = None) -> dict:
    """Return the full config dict for the requested account (or default).

    Resolution order per-field:
      1. email_accounts row (selected by `account` or default)
      2. env vars + settings.json flat keys (legacy)
      3. hardcoded fallbacks (localhost:31143 etc.)
    """
    cache_key = (_current_owner(), (account or "").strip().lower() or "__default__")
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
            EMAIL_CACHE_DB,
        ),
        "account_id": None,
        "account_name": None,
    }

    raw_rows = _read_accounts_from_db()
    if _mcp_owner_required(raw_rows):
        raise ValueError(_OWNER_SCOPE_ERROR)
    rows = _filter_accounts_for_owner(raw_rows)
    row = _resolve_account_from_rows(rows, account)
    if _current_owner() and raw_rows and not rows:
        raise ValueError("No email account is configured for the authenticated owner")
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
            settings_path = Path(_SETTINGS_FILE)
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
            try:
                conn.starttls()
            except Exception:
                # Don't leak the open plain socket on a rejected STARTTLS. (#3174)
                try:
                    conn.shutdown()
                except Exception:
                    pass
                raise
    if getattr(conn, "sock", None):
        conn.sock.settimeout(EMAIL_SOCKET_TIMEOUT)
    try:
        conn.login(cfg["imap_user"], cfg["imap_password"])
    except Exception:
        # A failed login otherwise orphans the connected socket; close it
        # before propagating (shutdown() is the pre-auth low-level close). (#3174)
        try:
            conn.shutdown()
        except Exception:
            pass
        raise
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


def _fixture_email_file() -> Path:
    return DATA_DIR / "fixture_email_messages.json"


def _fixture_email_enabled() -> bool:
    return _fixture_email_file().exists()


def _parse_fixture_date(raw_date: str) -> tuple[str, float]:
    if not raw_date:
        return "", 0.0
    parsed = None
    try:
        parsed = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
    except Exception:
        try:
            parsed = email.utils.parsedate_to_datetime(str(raw_date))
        except Exception:
            parsed = None
    if parsed:
        return parsed.isoformat(), parsed.timestamp()
    return str(raw_date), 0.0


def _fixture_email_record(row: dict, uid_num: int, owner: str) -> dict:
    sender = str(row.get("from") or "Fixture Sender <fixture@example.invalid>")
    sender_name, sender_addr = email.utils.parseaddr(sender)
    date_str, date_epoch = _parse_fixture_date(str(row.get("date") or ""))
    subject = str(row.get("subject") or "(no subject)")
    body = str(row.get("body") or "")
    owner_key = re.sub(r"[^A-Za-z0-9_.-]", "-", owner or "default")
    uid = str(uid_num)
    return {
        "uid": uid,
        "message_id": f"<fixture-email-{uid}-{owner_key}@fixtures.odysseus.local>",
        "subject": subject,
        "from": sender_name or sender_addr or sender,
        "from_address": sender_addr,
        "date": date_str,
        "date_epoch": date_epoch,
        "summary": body[:240],
        "body": body,
        "account": "Fixture Inbox",
        "account_email": owner or str(row.get("owner") or ""),
        "account_id": "fixture-email",
        "attachments": [],
    }


def _fixture_email_rows(owner: str | None = None) -> list[dict]:
    path = _fixture_email_file()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = raw.get("messages") if isinstance(raw, dict) else raw
    out = []
    owner = str(owner or "").strip()
    for i, row in enumerate(rows if isinstance(rows, list) else [], start=1):
        if not isinstance(row, dict):
            continue
        row_owner = str(row.get("owner") or "").strip()
        if owner and row_owner and row_owner != owner:
            continue
        out.append(_fixture_email_record(row, i, owner or row_owner))
    out.sort(key=lambda item: item.get("date_epoch") or 0, reverse=True)
    return out


def _fixture_account_rows() -> list[dict]:
    if not _fixture_email_enabled():
        return []
    owner = _current_owner()
    owners = []
    for row in _fixture_email_rows(owner or None):
        email_addr = row.get("account_email") or owner or "fixture@fixtures.odysseus.local"
        if email_addr not in owners:
            owners.append(email_addr)
    if not owners:
        owners = [owner or "fixture@fixtures.odysseus.local"]
    return [
        {
            "id": "fixture-email",
            "owner": owner or owners[0],
            "name": "Fixture Inbox",
            "is_default": True,
            "imap_user": owners[0],
            "from_address": owners[0],
        }
    ]


def _fixture_email_matches(item: dict, query: str) -> bool:
    if not query:
        return True
    terms = [term for term in re.split(r"\W+", str(query).lower()) if term]
    haystack = "\n".join(
        str(item.get(key) or "")
        for key in ("subject", "from", "from_address", "body", "summary")
    ).lower()
    return all(term in haystack for term in terms)


def _fixture_list_emails(folder="INBOX", max_results=20, unresponded_only=False,
                         unread_only=False, account=None) -> list[dict] | None:
    if not _fixture_email_enabled():
        return None
    if account and str(account).strip().lower() not in {
        "fixture-email",
        "fixture inbox",
        "fixture",
        str(_current_owner()).lower(),
    }:
        return []
    if (folder or "INBOX").upper() not in {"INBOX", "ALL", "ALL MAIL"}:
        return []
    return _fixture_email_rows(_current_owner())[: int(max_results or 20)]


def _fixture_search_emails(query, folders=None, max_results=20, account=None) -> list[dict] | None:
    if not _fixture_email_enabled():
        return None
    rows = _fixture_list_emails("INBOX", max_results=1000, account=account) or []
    out = [dict(row, _folder="INBOX") for row in rows if _fixture_email_matches(row, str(query or ""))]
    return out[: int(max_results or 20)]


def _fixture_read_email(uid=None, message_id=None, folder="INBOX", account=None) -> dict | None:
    if not _fixture_email_enabled():
        return None
    if (folder or "INBOX").upper() not in {"INBOX", "ALL", "ALL MAIL"}:
        return {"error": f"Email UID {uid or message_id} not found"}
    for item in _fixture_email_rows(_current_owner()):
        if uid and str(item.get("uid")) == str(uid):
            return item
        if message_id and str(item.get("message_id")) == str(message_id):
            return item
    return {"error": f"Email not found with UID/Message-ID: {uid or message_id}"}


# ── Tool implementations ──


def _list_emails(folder="INBOX", max_results=20, unresponded_only=False,
                 unread_only=False, account=None):
    """List emails newest-first. By default returns the latest messages,
    including read mail, so it matches normal inbox UI expectations.
    Pass unread_only=True and/or unresponded_only=True for attention scans.
    account selects mailbox (None = default).
    """
    fixture = _fixture_list_emails(folder, max_results, unresponded_only, unread_only, account)
    if fixture is not None:
        return fixture
    conn = None
    try:
        conn = _imap_connect(account)
        select_status, _ = conn.select(_q(folder), readonly=True)
        if select_status != "OK":
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

        return results
    finally:
        if conn:
            try: conn.logout()
            except Exception: pass


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
    fixture = _fixture_list_emails(folder, max_results, unresponded_only, unread_only, None)
    if fixture is not None:
        return fixture, []
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
    fixture = _fixture_search_emails(query, folders=folders, max_results=max_results, account=account)
    if fixture is not None:
        return fixture
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
                status, _ = conn.select(_q(folder), readonly=True)
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
    fixture = _fixture_read_email(uid=uid, message_id=message_id, folder=folder, account=account)
    if fixture is not None:
        return fixture
    cfg = _load_config(account)
    conn = None
    try:
        conn = _imap_connect(account)
        conn.select(_q(folder), readonly=True)

        if message_id and not uid:
            status, data = conn.uid("SEARCH", None, f'(HEADER Message-ID "{message_id}")')
            if status != "OK" or not data[0]:
                return {"error": f"Email not found with Message-ID: {message_id}"}
            uid = data[0].split()[-1]

        if not uid:
            return {"error": "No UID or Message-ID provided"}

        status, msg_data = conn.uid("FETCH", _b(uid), "(BODY.PEEK[])")
        if status != "OK":
            return {"error": f"Failed to fetch email UID {uid}"}
        if not msg_data or not msg_data[0] or not isinstance(msg_data[0], tuple) or len(msg_data[0]) < 2:
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
    finally:
        if conn:
            try: conn.logout()
            except Exception: pass


def _read_email_across_accounts(uid=None, message_id=None, folder="INBOX"):
    fixture = _fixture_read_email(uid=uid, message_id=message_id, folder=folder, account=None)
    if fixture is not None:
        return fixture
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
        try:
            conn.starttls()
        except Exception:
            # Don't leak the open plain socket on a rejected STARTTLS. SMTP has
            # no shutdown(); close() is the low-level socket close (no QUIT). (#3174)
            try:
                conn.close()
            except Exception:
                pass
            raise
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
        try:
            conn.login(cfg["smtp_user"], cfg["smtp_password"])
        except Exception:
            # A failed login otherwise orphans the connected socket; close it
            # before propagating (SMTP has no shutdown(); close() = socket close). (#3174)
            try:
                conn.close()
            except Exception:
                pass
            raise
    return conn


def _read_agent_email_confirm_setting() -> bool:
    """True if the user wants agent send_email/reply_to_email calls to be
    queued for manual approval instead of SMTPed immediately. Defaults to
    True so a fresh install is safe — agents have been observed inventing
    signatures and sending to real recipients without the user's review."""
    try:
        from src.settings import get_setting
        return bool(get_setting("agent_email_confirm", True))
    except Exception:
        return True


def _stash_agent_draft(*, to, subject, body, in_reply_to=None, references=None,
                      cc=None, bcc=None, account=None) -> dict:
    """Insert the composed email into scheduled_emails with status
    'agent_draft' and a far-future send_at so the scheduled-send poller
    never picks it up. Returns the pending payload the model surfaces to
    the user (and that the chat UI can render as an approval card)."""
    try:
        from src.constants import SCHEDULED_EMAILS_DB
    except Exception:
        return {"success": False, "error": "Pending-email storage unavailable"}
    pending_id = uuid.uuid4().hex[:16]
    far_future = "9999-12-31T00:00:00"
    now = datetime.utcnow().isoformat()
    try:
        conn = sqlite3.connect(SCHEDULED_EMAILS_DB)
        # Touch the schema in case the email-routes init hasn't run yet
        # (MCP server can boot independently).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_emails (
                id TEXT PRIMARY KEY,
                to_addr TEXT NOT NULL,
                cc TEXT,
                bcc TEXT,
                subject TEXT,
                body TEXT NOT NULL,
                in_reply_to TEXT,
                references_hdr TEXT,
                attachments TEXT,
                send_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT,
                owner TEXT DEFAULT '',
                account_id TEXT,
                odysseus_kind TEXT
            )
        """)
        conn.execute("""
            INSERT INTO scheduled_emails
            (id, to_addr, cc, bcc, subject, body, in_reply_to, references_hdr,
             attachments, send_at, created_at, status, account_id, odysseus_kind, owner)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'agent_draft', ?, ?, ?)
        """, (
            pending_id,
            to if isinstance(to, str) else ", ".join(to),
            cc if isinstance(cc, str) else (", ".join(cc) if cc else None),
            bcc if isinstance(bcc, str) else (", ".join(bcc) if bcc else None),
            subject or "",
            body or "",
            in_reply_to or None,
            references if isinstance(references, str) else (" ".join(references) if references else None),
            "[]",
            far_future,
            now,
            account or None,
            "agent_draft",
            _current_owner(),
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        return {"success": False, "error": f"Failed to stash draft: {e}"}
    return {
        "success": True,
        "pending": True,
        "pending_id": pending_id,
        "to": to if isinstance(to, str) else ", ".join(to),
        "subject": subject or "",
        "body": body or "",
        "message": (
            "✋ Draft staged for your approval — nothing has been sent yet.\n"
            "Review the To/Subject/Body above. Reply 'send' to deliver, or "
            "'cancel' to discard."
        ),
    }


def _send_email(to, subject, body, in_reply_to=None, references=None, cc=None, bcc=None, account=None):
    """Send an email via SMTP. Returns dict with status.

    When the `agent_email_confirm` setting is on (the default), the email
    is NOT SMTPed — instead it lands in scheduled_emails as an
    `agent_draft` row and the user reviews + approves it from the chat
    UI. This closes the auto-send hole that let earlier models invent
    signatures and ship them to real recipients without confirmation."""
    if _read_agent_email_confirm_setting():
        # Even confirmation-first sends must resolve the selected account now.
        # Otherwise a caller could stage a pending draft against another
        # owner's account selector before browser approval handles it.
        cfg = _load_config(account)
        return _stash_agent_draft(
            to=to, subject=subject, body=body,
            in_reply_to=in_reply_to, references=references,
            cc=cc, bcc=bcc, account=cfg.get("account_id") or account,
        )
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
            append_st, append_data = imap.append(_q(sent_folder), "\\Seen", None, msg.as_bytes())
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


def _build_email_document_content(
    to,
    subject,
    body,
    *,
    cc=None,
    bcc=None,
    in_reply_to=None,
    references=None,
    source_uid=None,
    source_folder=None,
):
    header_lines = [f"To: {to or ''}"]
    if cc:
        header_lines.append(f"Cc: {cc}")
    if bcc:
        header_lines.append(f"Bcc: {bcc}")
    header_lines.append(f"Subject: {subject or ''}")
    if in_reply_to:
        header_lines.append(f"In-Reply-To: {in_reply_to}")
    if references:
        header_lines.append(f"References: {references}")
    if source_uid:
        header_lines.append(f"X-Source-UID: {source_uid}")
    if source_folder:
        header_lines.append(f"X-Source-Folder: {source_folder}")
    return "\n".join(header_lines) + "\n---\n" + (body or "")


def _merge_email_reply_body(existing_content: str, reply_body: str) -> str:
    """Preserve email headers and quoted chain while replacing the editable reply body."""
    if "\n---\n" not in (existing_content or ""):
        return reply_body or ""
    head, body = existing_content.split("\n---\n", 1)
    quote_markers = (
        "---------- Previous message ----------",
        "-----Original Message-----",
        "----- Original Message -----",
    )
    quote_index = -1
    for marker in quote_markers:
        idx = body.find(marker)
        if idx != -1 and (quote_index == -1 or idx < quote_index):
            quote_index = idx
    quote = body[quote_index:].strip() if quote_index != -1 else ""
    merged_body = (reply_body or "").strip()
    if quote:
        merged_body = f"{merged_body}\n\n{quote}" if merged_body else quote
    return f"{head}\n---\n{merged_body}"


def _create_email_draft_document(
    *,
    to,
    subject,
    body,
    title=None,
    cc=None,
    bcc=None,
    in_reply_to=None,
    references=None,
    source_uid=None,
    source_folder=None,
    account=None,
    source_message_id=None,
):
    """Create an Odysseus email compose document for user review. Does not send."""
    from core.database import SessionLocal, Document, DocumentVersion
    try:
        from src.event_bus import fire_event
    except Exception:
        fire_event = None

    cfg = _load_config(account) if account else _load_config(None)
    content = _build_email_document_content(
        to,
        subject,
        body,
        cc=cc,
        bcc=bcc,
        in_reply_to=in_reply_to,
        references=references,
        source_uid=source_uid,
        source_folder=source_folder,
    )
    doc_id = str(uuid.uuid4())
    ver_id = str(uuid.uuid4())
    doc_title = (title or subject or "Email draft").strip() or "Email draft"
    doc_owner = _current_owner() or _default_document_owner()

    db = SessionLocal()
    try:
        if source_uid and source_folder:
            existing = (
                db.query(Document)
                .filter(Document.is_active == True)
                .filter(Document.language == "email")
                .filter(Document.owner == doc_owner)
                .filter(Document.source_email_uid == str(source_uid))
                .filter(Document.source_email_folder == source_folder)
                .order_by(Document.updated_at.desc())
                .first()
            )
            if existing and "\n---\n" in (existing.current_content or ""):
                existing.current_content = _merge_email_reply_body(existing.current_content, body or "")
                existing.version_count = (existing.version_count or 0) + 1
                ver = DocumentVersion(
                    id=ver_id,
                    document_id=existing.id,
                    version_number=existing.version_count,
                    content=existing.current_content,
                    summary="Updated by email MCP draft tool",
                    source="ai",
                )
                db.add(ver)
                db.commit()
                if fire_event:
                    try:
                        fire_event("document_updated", doc_owner)
                    except Exception:
                        pass
                return {
                    "draft": True,
                    "updated": True,
                    "doc_id": existing.id,
                    "title": existing.title,
                    "language": existing.language,
                    "account": cfg.get("account_name"),
                    "account_id": cfg.get("account_id"),
                    "to": to,
                    "subject": subject,
                }

        doc = Document(
            id=doc_id,
            session_id=None,
            title=doc_title,
            language="email",
            current_content=content,
            version_count=1,
            is_active=True,
            owner=doc_owner,
            source_email_uid=source_uid,
            source_email_folder=source_folder,
            source_email_account_id=cfg.get("account_id"),
            source_email_message_id=source_message_id,
        )
        ver = DocumentVersion(
            id=ver_id,
            document_id=doc_id,
            version_number=1,
            content=content,
            summary="Created by email MCP draft tool",
            source="ai",
        )
        db.add(doc)
        db.add(ver)
        db.commit()
        if fire_event:
            try:
                fire_event("document_created", doc_owner)
            except Exception:
                pass
        return {
            "draft": True,
            "doc_id": doc_id,
            "title": doc_title,
            "language": "email",
            "account": cfg.get("account_name"),
            "account_id": cfg.get("account_id"),
            "to": to,
            "subject": subject,
        }
    finally:
        db.close()


def _draft_reply_to_email(uid, body, folder="INBOX", reply_all=False, account=None, title=None):
    """Create a threaded Odysseus reply draft document. Does not send."""
    conn = _imap_connect(account)
    conn.select(_q(folder), readonly=True)
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
        cfg = _load_config(account)
        own_addrs = {
            (cfg.get("imap_user") or "").strip().lower(),
            (cfg.get("from_address") or "").strip().lower(),
        }
        for header_name in ("To", "Cc"):
            for _, addr in email.utils.getaddresses([orig.get(header_name, "")]):
                addr_l = (addr or "").strip().lower()
                if addr and addr != sender_addr and addr_l not in own_addrs:
                    cc_addrs.append(addr)
        if cc_addrs:
            cc = ", ".join(dict.fromkeys(cc_addrs))

    return _create_email_draft_document(
        to=to_addrs,
        subject=reply_subject,
        body=body,
        title=title or reply_subject,
        cc=cc,
        in_reply_to=orig_message_id,
        references=new_references,
        source_uid=uid,
        source_folder=folder,
        account=account,
        source_message_id=orig_message_id,
    )


async def _ai_draft_reply_to_email(uid, folder="INBOX", reply_all=False, account=None, title=None):
    """Generate a reply with Odysseus' AI-reply prompt/style, then create a compose doc."""
    read_result = _read_email(uid=uid, folder=folder, account=account)
    if "error" in read_result:
        return read_result

    to_addr = read_result.get("from_address") or email.utils.parseaddr(read_result.get("from") or "")[1]
    subject = read_result.get("subject") or ""
    reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    original_body = read_result.get("body") or ""
    message_id = read_result.get("message_id") or ""

    if not original_body.strip():
        return {"error": "No email body available for AI reply"}

    try:
        from routes.email_helpers import (
            _EMAIL_REPLY_SYS_PROMPT_BASE,
            _apply_email_style_mechanics,
            _extract_reply,
            _load_settings,
        )
        from src.endpoint_resolver import (
            resolve_endpoint,
            resolve_utility_fallback_candidates,
            resolve_chat_fallback_candidates,
        )
        from src.llm_core import llm_call_async_with_fallback
    except Exception as exc:
        return {"error": f"AI reply helpers unavailable: {exc}"}

    settings = _load_settings()
    style = settings.get("email_writing_style", "")
    system_prompt = _EMAIL_REPLY_SYS_PROMPT_BASE
    if style:
        system_prompt += f"\n\nWRITING STYLE TO MATCH:\n{style}"

    user_msg = (
        f"Recipient: {to_addr}\nSubject: {reply_subject}\n\n"
        f"Original email and any current draft:\n{original_body[:6000]}\n\n"
        "Draft a reply. Return only the reply body text."
    )

    candidates = []
    seen = set()

    def _add(url, model, headers):
        key = (url or "", model or "")
        if not url or not model or key in seen:
            return
        seen.add(key)
        candidates.append((url, model, headers))

    try:
        _add(*resolve_endpoint("utility", owner=None))
    except Exception:
        pass
    try:
        _add(*resolve_endpoint("default", owner=None))
    except Exception:
        pass
    try:
        utility_fallbacks = resolve_utility_fallback_candidates(owner=None) or []
    except TypeError:
        utility_fallbacks = resolve_utility_fallback_candidates() or []
    for cand in utility_fallbacks:
        _add(*cand)
    try:
        chat_fallbacks = resolve_chat_fallback_candidates(owner=None) or []
    except TypeError:
        chat_fallbacks = resolve_chat_fallback_candidates() or []
    for cand in chat_fallbacks:
        _add(*cand)

    if not candidates:
        return {"error": "No LLM endpoint configured for AI reply"}

    try:
        raw_reply = await llm_call_async_with_fallback(
            candidates,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.7,
            max_tokens=1024,
            timeout=60,
        )
    except Exception as exc:
        return {"error": f"AI reply generation failed: {exc}"}

    reply = _apply_email_style_mechanics(_extract_reply(raw_reply or ""))
    if not reply:
        return {"error": "AI reply generation returned an empty response"}

    return _draft_reply_to_email(
        uid=uid,
        body=reply,
        folder=folder,
        reply_all=reply_all,
        account=account,
        title=title or reply_subject,
    )


def _reply_to_email(uid, body, folder="INBOX", reply_all=False, account=None):
    """Reply to an existing email by UID. Threads via In-Reply-To/References."""
    conn = None
    try:
        conn = _imap_connect(account)
        conn.select(_q(folder), readonly=True)
        status, msg_data = conn.uid("FETCH", _b(uid), "(BODY.PEEK[])")
    finally:
        if conn:
            try: conn.logout()
            except Exception: pass
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
    conn.select(_q(folder))
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
        conn.select(_q(folder))
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
        conn.select(_q(source_folder))
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
        dest_arg = _q(dest_folder)
        status, _ = conn.uid("MOVE", _b(msg_set), dest_arg)
        if status != "OK":
            # Fallback: UID copy + flag-delete + expunge
            status, _ = conn.uid("COPY", _b(msg_set), dest_arg)
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
        conn.select(_q(folder), readonly=True)
        status, data = conn.uid("SEARCH", None, criteria)
        if status != "OK" or not data or not data[0]:
            return []
        return data[0].split()
    finally:
        conn.logout()


def _move_message(uid, source_folder, dest_folder, account=None, role: str = ""):
    """Move a message between folders. Tries IMAP MOVE, falls back to copy+delete."""
    conn = _imap_connect(account)
    conn.select(_q(source_folder))
    try:
        dest_folder = _resolve_folder(conn, dest_folder, role or _folder_role_from_name(dest_folder))
        try:
            status, data = conn.uid("FETCH", _b(uid), "(UID)")
        except Exception:
            return False
        existing = _uid_fetch_rows(data)
        if status != "OK" or not existing:
            return False
        dest_arg = _q(dest_folder)
        status, _ = conn.uid("MOVE", _b(uid), dest_arg)
        if status == "OK":
            return True
        # Fallback: UID copy + delete
        status, _ = conn.uid("COPY", _b(uid), dest_arg)
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
    conn = None
    try:
        conn = _imap_connect(account)
        conn.select(_q(folder), readonly=True)
        status, msg_data = conn.uid("FETCH", _b(uid), "(BODY.PEEK[])")
    finally:
        if conn:
            try: conn.logout()
            except Exception: pass
    if status != "OK":
        return {"error": f"Failed to fetch email UID {uid}"}
    raw = msg_data[0][1]
    msg = email.message_from_bytes(raw)

    target_dir = Path(MAIL_ATTACHMENTS_DIR) / f"{folder}_{uid}"
    filepath = _extract_attachment_to_disk(msg, index, target_dir)
    if not filepath:
        return {"error": f"Attachment index {index} not found"}
    size = os.path.getsize(filepath)
    return {"path": filepath, "filename": os.path.basename(filepath), "size": size}


# MCP handler registration lives in this package's __init__.py. Keep this
# module limited to the shared implementation and state that handlers call.
