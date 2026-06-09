"""
email_helpers.py

Lower-level helpers used by both `email_routes.py` (the FastAPI route file)
and `email_pollers.py` (the background loops):

    - auth dependencies (require_owner / require_user / _assert_owns_account)
    - account config + settings persistence (`_get_email_config`, `_list_email_accounts`)
    - IMAP connection helpers (`_imap_connect`, `_imap`, folder detection)
    - message parsing (`_decode_header`, `_extract_html/text`, attachment helpers)
    - sender context retrieval for the AI-summary / AI-reply pipelines
    - Pydantic models, shared constants, scheduled-DB bootstrap
"""

import os
import imaplib
import smtplib
import email as email_mod
import email.header
import email.utils
import json
import re
import html
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import mimetypes
from pathlib import Path

from fastapi import Query, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, List

from src.auth_helpers import _auth_disabled, get_current_user
from src.secret_storage import decrypt as _decrypt

logger = logging.getLogger(__name__)


def _smtp_security_mode(cfg: dict) -> str:
    raw = str(cfg.get("smtp_security") or "").strip().lower()
    if raw in {"ssl", "starttls", "none"}:
        return raw
    port = int(cfg.get("smtp_port") or 465)
    if port == 587:
        return "starttls"
    return "ssl"


def _send_smtp_message(cfg: dict, from_addr: str, recipients: list[str], message: str | bytes, timeout: int = 30) -> None:
    """Send through SMTP using the configured transport security mode."""
    host = cfg["smtp_host"]
    port = int(cfg.get("smtp_port") or 465)
    user = cfg.get("smtp_user") or ""
    password = cfg.get("smtp_password") or ""
    security = _smtp_security_mode(cfg)

    if security == "ssl":
        with smtplib.SMTP_SSL(host, port, timeout=timeout) as smtp:
            if user and password:
                smtp.login(user, password)
            smtp.sendmail(from_addr, recipients, message)
        return

    with smtplib.SMTP(host, port, timeout=timeout) as smtp:
        if security == "starttls":
            smtp.starttls()
        if user and password:
            smtp.login(user, password)
        smtp.sendmail(from_addr, recipients, message)


def _friendly_email_auth_error(protocol: str, host: str, error: object) -> str:
    """Return a clearer setup error for known provider auth policies."""
    raw = str(error or "")
    lower = raw.lower()
    host_lower = (host or "").lower()
    microsoft_host = any(
        marker in host_lower
        for marker in (
            "outlook.office365.com",
            "smtp.office365.com",
            "office365.com",
            "outlook.com",
            "hotmail.com",
            "live.com",
        )
    )
    microsoft_basic_auth_failure = (
        "5.7.139" in lower
        or "basic authentication is disabled" in lower
        or ("authenticate failed" in lower and microsoft_host)
        or ("authentication unsuccessful" in lower and microsoft_host)
    )
    if microsoft_basic_auth_failure:
        return (
            "Microsoft no longer accepts normal mailbox passwords for "
            "Outlook/Office 365 IMAP/SMTP in most accounts. Odysseus "
            "does not support Microsoft OAuth/Graph mail yet, so Outlook "
            "accounts cannot be added with this password form."
        )
    return raw[:200]


def _strip_think(text: str) -> str:
    """Email-flavored think strip — thin wrapper over the central helper.

    Email AI features get the prose-strip extension because their outputs
    are short LLM-only generations (replies, summaries, calendar extraction,
    urgency, classification, writing-style) where untagged reasoning leaks
    are common. The central helper only runs the prose-strip when an actual
    `<think>` tag was present in the input, so legit user content is safe.
    """
    if not text:
        return ""
    from src.text_helpers import strip_think as _central, _THINK_CLOSED_RE, _THINK_OPEN_RE, _THINK_TAG_RE
    had_think = bool(_THINK_CLOSED_RE.search(text) or _THINK_OPEN_RE.search(text) or _THINK_TAG_RE.search(text))
    return _central(text, prose=had_think, prompt_echo=True)


import re as _re_reply
# Accept REPLY / SUMMARY / OUTPUT as the opening fence so the same extractor
# serves replies and summaries (any fenced final-output block).
_REPLY_OPEN_RE = _re_reply.compile(r"<<<\s*(?:REPLY|SUMMARY|OUTPUT)\s*>>+", _re_reply.I)
_REPLY_CLOSE_RE = _re_reply.compile(r"<<<\s*END\s*>>+", _re_reply.I)


def _extract_reply(text: str) -> str:
    """Pull the final email reply out of a model response.

    Positive extraction beats blocklist stripping: the model is asked to fence
    its reply in <<<REPLY>>> ... <<<END>>> markers, so we keep ONLY that region
    and ignore whatever reasoning came before/after it. Deterministic, and it
    can never clip a legit reply that merely opens reflectively.

    Fallbacks when the markers are absent (older/weaker models): we just run the
    usual think-strip on the whole text — strictly no worse than before. A
    second think-strip pass always runs on the extracted body too, in case the
    model also reasoned *inside* the markers.
    """
    if not text:
        return ""
    t = text
    m = _REPLY_OPEN_RE.search(t)
    if m:
        rest = t[m.end():]
        c = _REPLY_CLOSE_RE.search(rest)
        t = rest[:c.start()] if c else rest
    # Drop any stray/duplicate marker tokens, then strip think markup.
    t = _REPLY_OPEN_RE.sub("", t)
    t = _REPLY_CLOSE_RE.sub("", t)
    return _strip_think(t).strip()


def _apply_email_style_mechanics(text: str) -> str:
    """Enforce deterministic writing-style mechanics that models often miss."""
    if not text:
        return ""
    return (
        text.replace("—", "--")
        .replace("–", "--")
        .replace("’", "'")
        .replace("‘", "'")
    )


def _require_auth(request: Request) -> str:
    """Defense-in-depth: reject unauthenticated callers even if upstream
    middleware was bypassed (e.g. localhost-bypass, SSRF from a sibling
    service). Mirrors core.middleware.require_admin's resolution path.

    v2 review HIGH-13: previously fell open whenever auth_manager wasn't
    `is_configured`, exposing IMAP creds and SMTP send to any network
    caller on a half-configured deploy. Now: anonymous callers in
    unconfigured mode are only honoured if they're coming from
    localhost; everyone else gets 401.
    """
    u = get_current_user(request)
    if u:
        return u
    if _auth_disabled():
        return ""
    auth_mgr = getattr(request.app.state, "auth_manager", None)
    if auth_mgr is not None and getattr(auth_mgr, "is_configured", False):
        raise HTTPException(401, "Not authenticated")
    # Unconfigured / first-run mode: only allow loopback callers. Public
    # network traffic must authenticate even before auth is set up.
    client = getattr(request, "client", None)
    host = (client.host if client else "") or ""
    if host in ("127.0.0.1", "::1", "localhost"):
        return ""
    raise HTTPException(401, "Not authenticated")


def require_owner(request: Request, account_id: str | None = Query(None)) -> str:
    """FastAPI dependency: authenticate the caller and, if `account_id` is in
    the query string, assert ownership. Returns the resolved owner ("" in
    unconfigured single-user mode). Routes whose `account_id` lives in the
    request body or path must still call `_assert_owns_account(body_id, owner)`
    explicitly. Use `require_user` (no Query read) for path-param routes."""
    owner = _require_auth(request)
    if account_id:
        _assert_owns_account(account_id, owner)
    return owner


def require_user(request: Request) -> str:
    """Auth-only dependency for routes where `account_id` is a path param
    or absent. Avoids `require_owner`'s Query collision with path params."""
    return _require_auth(request)


def _assert_owns_account(account_id: str, owner: str) -> None:
    """Reject requests that name an `account_id` belonging to another user.
    Previously the account lookup in `_get_email_config` filtered only on
    `id == account_id`, letting a multi-user deploy enumerate / operate
    against any other user's IMAP/SMTP mailbox. Call this *before* opening
    the IMAP connection or reading creds. `owner == ""` is the unconfigured /
    single-user case — accept any account."""
    if not account_id or not owner:
        return
    try:
        from core.database import SessionLocal as _SL, EmailAccount as _EA
        db = _SL()
        try:
            row = db.query(_EA).filter(_EA.id == account_id).first()
            if row is None:
                raise HTTPException(404, "Account not found")
            if row.owner and row.owner != owner:
                # Treat as 404 (not 403) so we don't leak existence.
                raise HTTPException(404, "Account not found")
        finally:
            db.close()
    except HTTPException:
        raise
    except Exception as e:
        # Fail closed — a DB hiccup must not let cross-tenant access slip
        # through. 503 tells the caller to retry; logs preserve detail.
        logger.error(f"Account-owner check failed: {e}")
        raise HTTPException(503, "Account check failed")

def _q(name: str) -> str:
    """Quote an IMAP mailbox name. Defensive: escapes `\\` and `"` and wraps
    in double quotes so user-supplied folder names with spaces or quotes can't
    confuse `SELECT` / `COPY`. imaplib already rejects CRLF, but quoting also
    handles `[Gmail]/Sent Mail`-style names that need wrapping anyway."""
    return '"' + (name or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def _attach_compose_uploads(outer: MIMEMultipart, tokens) -> None:
    """Read each staged upload token, build a MIMEBase part, and attach to
    `outer`. Tokens are sanitized via Path(token).name to prevent traversal.
    Missing files are skipped silently. Used by /send, scheduled delivery,
    and the agent send pipeline."""
    if not tokens:
        return
    for token in tokens:
        safe_token = Path(token).name
        path = COMPOSE_UPLOADS_DIR / safe_token
        if not path.exists():
            logger.warning(f"Attachment token not found: {safe_token}")
            continue
        ctype, encoding = mimetypes.guess_type(str(path))
        if ctype is None or encoding is not None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        with open(path, "rb") as f:
            part = MIMEBase(maintype, subtype)
            part.set_payload(f.read())
        encoders.encode_base64(part)
        # Token format: "<uuid>_<original_name>"
        original_name = safe_token.split("_", 1)[1] if "_" in safe_token else safe_token
        part.add_header("Content-Disposition", "attachment", filename=original_name)
        outer.attach(part)


def _cleanup_compose_uploads(tokens) -> None:
    """Best-effort unlink of staged uploads after delivery (or failure)."""
    if not tokens:
        return
    for token in tokens:
        try:
            (COMPOSE_UPLOADS_DIR / Path(token).name).unlink(missing_ok=True)
        except Exception:
            pass


from src.constants import DATA_DIR as _DATA_DIR, MAIL_ATTACHMENTS_DIR, SETTINGS_FILE as _SETTINGS_FILE, SCHEDULED_EMAILS_DB
DATA_DIR = Path(_DATA_DIR)
SETTINGS_FILE = Path(_SETTINGS_FILE)
# Override at deploy time via ODYSSEUS_MAIL_ATTACHMENTS_DIR. Defaults to a
# subdir of the install's data/ tree so the app works out-of-the-box without
# a hardcoded /home/<user>/ path.
ATTACHMENTS_DIR = Path(MAIL_ATTACHMENTS_DIR)
ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
COMPOSE_UPLOADS_DIR = ATTACHMENTS_DIR / "_compose"
COMPOSE_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
SCHEDULED_DB = Path(SCHEDULED_EMAILS_DB)


OWNER_SCOPED_EMAIL_CACHE_TABLES = {
    "email_summaries",
    "email_ai_replies",
    "email_calendar_extractions",
    "email_urgency_alerts",
}


def _email_cache_owner_clause(owner: str = "") -> tuple[str, tuple[str, ...]]:
    owner = (owner or "").strip()
    if owner:
        return "owner = ?", (owner,)
    return "(owner = '' OR owner IS NULL)", ()


def _ensure_owner_scoped_email_cache_table(conn, table: str, create_sql: str, columns: list[str]):
    """Rebuild legacy Message-ID-only cache tables with owner in the PK."""
    conn.execute(create_sql)
    try:
        info = conn.execute(f"PRAGMA table_info({table})").fetchall()
        cols = [r[1] for r in info]
        pk_cols = [r[1] for r in sorted((r for r in info if r[5]), key=lambda r: r[5])]
        if "owner" in cols and pk_cols == ["message_id", "owner"]:
            return

        conn.execute(f"ALTER TABLE {table} RENAME TO {table}__old")
        conn.execute(create_sql)
        old_cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table}__old)").fetchall()]
        copy_cols = [c for c in columns if c != "owner" and c in old_cols]
        source_owner = "COALESCE(owner, '')" if "owner" in old_cols else "''"
        target_cols = ["owner", *copy_cols]
        select_exprs = [source_owner, *copy_cols]
        conn.execute(
            f"INSERT OR IGNORE INTO {table} ({', '.join(target_cols)}) "
            f"SELECT {', '.join(select_exprs)} FROM {table}__old"
        )
        conn.execute(f"DROP TABLE {table}__old")
    except Exception as _mig_e:
        import logging as _lg
        _lg.getLogger(__name__).warning(f"{table} owner-migration skipped: {_mig_e}")


def attachment_extract_dir(folder: str, uid: str) -> Path:
    """Containment-safe extraction directory for an attachment.

    `folder` and `uid` are user-controlled (query/path params). Flatten them to
    a single safe path segment so a value like folder='../../tmp' can't escape
    ATTACHMENTS_DIR, then assert containment as belt-and-suspenders."""
    key = re.sub(r"[^A-Za-z0-9._-]", "_", f"{folder}_{uid}") or "_"
    target = (ATTACHMENTS_DIR / key).resolve()
    base = ATTACHMENTS_DIR.resolve()
    if target != base and base not in target.parents:
        raise HTTPException(400, "Invalid attachment location")
    return target


def _init_scheduled_db():
    import sqlite3
    conn = sqlite3.connect(SCHEDULED_DB)
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
            owner TEXT DEFAULT ''
        )
    """)
    # Email summary cache. SECURITY: Message-IDs are global, so AI-derived
    # cache rows must be owner-scoped just like email_tags.
    _ensure_owner_scoped_email_cache_table(conn, "email_summaries", """
        CREATE TABLE IF NOT EXISTS email_summaries (
            message_id TEXT,
            owner TEXT DEFAULT '',
            uid TEXT,
            folder TEXT,
            subject TEXT,
            sender TEXT,
            summary TEXT NOT NULL,
            model_used TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (message_id, owner)
        )
    """, ["message_id", "owner", "uid", "folder", "subject", "sender", "summary", "model_used", "created_at"])
    # Email AI reply cache (pre-generated draft replies)
    _ensure_owner_scoped_email_cache_table(conn, "email_ai_replies", """
        CREATE TABLE IF NOT EXISTS email_ai_replies (
            message_id TEXT,
            owner TEXT DEFAULT '',
            uid TEXT,
            folder TEXT,
            reply TEXT NOT NULL,
            model_used TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (message_id, owner)
        )
    """, ["message_id", "owner", "uid", "folder", "reply", "model_used", "created_at"])
    # Email tags / spam classification cache. SECURITY: keyed by
    # (message_id, owner) because Message-IDs are GLOBAL (a newsletter goes
    # to many users with the same Message-ID). Without owner-scoping, a
    # tag-write for user A's row clobbered user B's row and surfaced A's
    # UID in B's `tag:urgent` IMAP filter (review C2).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS email_tags (
            message_id TEXT,
            owner TEXT DEFAULT '',
            uid TEXT,
            folder TEXT,
            subject TEXT,
            sender TEXT,
            tags TEXT,
            spam_verdict INTEGER DEFAULT 0,
            spam_reason TEXT,
            moved_to TEXT,
            model_used TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (message_id, owner)
        )
    """)
    # Backfill migration: older installs created the table with
    # message_id as a bare PK and no owner column. Add the column +
    # promote it into the PK by rebuild-copy-swap (SQLite can't ALTER PK).
    try:
        _cols = [r[1] for r in conn.execute("PRAGMA table_info(email_tags)")]
        if "owner" not in _cols:
            # Add the column first so reads/writes don't break mid-migration.
            conn.execute("ALTER TABLE email_tags ADD COLUMN owner TEXT DEFAULT ''")
            # Rebuild with composite PK. Existing rows get owner='' (legacy
            # single-user); the urgency scanner will overwrite as it
            # re-classifies. No data loss.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS email_tags__new (
                    message_id TEXT,
                    owner TEXT DEFAULT '',
                    uid TEXT, folder TEXT, subject TEXT, sender TEXT,
                    tags TEXT, spam_verdict INTEGER DEFAULT 0,
                    spam_reason TEXT, moved_to TEXT, model_used TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (message_id, owner)
                )
            """)
            conn.execute("""
                INSERT OR IGNORE INTO email_tags__new
                  (message_id, owner, uid, folder, subject, sender, tags,
                   spam_verdict, spam_reason, moved_to, model_used, created_at)
                SELECT message_id, COALESCE(owner, ''), uid, folder, subject,
                       sender, tags, spam_verdict, spam_reason, moved_to,
                       model_used, created_at
                FROM email_tags
            """)
            conn.execute("DROP TABLE email_tags")
            conn.execute("ALTER TABLE email_tags__new RENAME TO email_tags")
    except Exception as _mig_e:
        # Best-effort — log via the module logger if available
        import logging as _lg
        _lg.getLogger(__name__).warning(f"email_tags owner-migration skipped: {_mig_e}")
    _ensure_owner_scoped_email_cache_table(conn, "email_calendar_extractions", """
        CREATE TABLE IF NOT EXISTS email_calendar_extractions (
            message_id TEXT,
            owner TEXT DEFAULT '',
            uid TEXT,
            events_created INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            PRIMARY KEY (message_id, owner)
        )
    """, ["message_id", "owner", "uid", "events_created", "created_at"])
    _ensure_owner_scoped_email_cache_table(conn, "email_urgency_alerts", """
        CREATE TABLE IF NOT EXISTS email_urgency_alerts (
            message_id TEXT,
            owner TEXT DEFAULT '',
            uid TEXT,
            folder TEXT,
            subject TEXT,
            sender TEXT,
            urgency TEXT,
            reason TEXT,
            alerted INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            PRIMARY KEY (message_id, owner)
        )
    """, ["message_id", "owner", "uid", "folder", "subject", "sender", "urgency", "reason", "alerted", "created_at"])
    conn.execute("""
        CREATE TABLE IF NOT EXISTS email_event_seen (
            owner TEXT NOT NULL,
            account_key TEXT NOT NULL,
            folder TEXT NOT NULL,
            message_key TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            PRIMARY KEY (owner, account_key, folder, message_key)
        )
    """)
    # Boundary cache — LLM-detected sig/quote start positions in the body.
    # Stored as char offsets (-1 = no boundary found). Once cached, the
    # client uses these to fold without ever re-calling the LLM.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS email_boundaries (
            message_id TEXT PRIMARY KEY,
            uid TEXT,
            folder TEXT,
            sig_start INTEGER,
            quote_start INTEGER,
            model_used TEXT,
            created_at TEXT NOT NULL
        )
    """)
    # Lazy migration: add account_id column to scheduled_emails if missing
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(scheduled_emails)").fetchall()]
        if "account_id" not in cols:
            conn.execute("ALTER TABLE scheduled_emails ADD COLUMN account_id TEXT")
        if "odysseus_kind" not in cols:
            conn.execute("ALTER TABLE scheduled_emails ADD COLUMN odysseus_kind TEXT")
        if "owner" not in cols:
            conn.execute("ALTER TABLE scheduled_emails ADD COLUMN owner TEXT DEFAULT ''")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_scheduled_emails_owner_status ON scheduled_emails(owner, status)")
        # Backfill owner on legacy rows from the owning email account so the
        # owner-scoped list/cancel routes surface pre-migration scheduled
        # sends to the right user (the poller already resolves these by
        # account at send time; this aligns the UI with that).
        legacy_accounts = conn.execute(
            "SELECT DISTINCT account_id FROM scheduled_emails "
            "WHERE (owner IS NULL OR owner = '') AND account_id IS NOT NULL AND account_id != ''"
        ).fetchall()
        if legacy_accounts:
            try:
                from core.database import SessionLocal as _SL, EmailAccount as _EA
                _db = _SL()
                try:
                    for (acct_id,) in legacy_accounts:
                        row = _db.query(_EA.owner).filter(_EA.id == acct_id).first()
                        acct_owner = (row[0] or "") if row else ""
                        if acct_owner:
                            conn.execute(
                                "UPDATE scheduled_emails SET owner = ? "
                                "WHERE account_id = ? AND (owner IS NULL OR owner = '')",
                                (acct_owner, acct_id),
                            )
                finally:
                    _db.close()
            except Exception:
                pass
    except Exception:
        pass
    # Lazy migration: add turns_json to email_boundaries for server-side
    # thread parsing cache (talon-style precomputed reply chain).
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(email_boundaries)").fetchall()]
        if "turns_json" not in cols:
            conn.execute("ALTER TABLE email_boundaries ADD COLUMN turns_json TEXT")
    except Exception:
        pass
    # Per-sender signature cache. Populated by `learn_sender_signatures`
    # action: the LLM extracts the common trailing block across N emails
    # from each sender; the renderer folds it consistently for every
    # future email from that address.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sender_signatures (
            from_address TEXT PRIMARY KEY,
            signature_text TEXT,
            sample_count INTEGER,
            last_built_at TEXT NOT NULL,
            model_used TEXT,
            source TEXT
        )
    """)
    conn.commit()
    conn.close()


_init_scheduled_db()


def _load_settings():
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    return {}


def _save_settings(settings):
    from core.atomic_io import atomic_write_json
    atomic_write_json(str(SETTINGS_FILE), settings, indent=2)


def _get_email_config(account_id: str | None = None, owner: str = "") -> dict:
    """Return IMAP/SMTP config as a dict.

    Resolution order:
      1. If account_id given → that specific EmailAccount row.
      2. Else → the row with is_default=True (scoped to `owner` when given).
      3. Else → the first enabled row (scoped to `owner` when given).
      4. Else → legacy flat keys in data/settings.json (kept for envs
         where the migration hasn't run yet or accounts table is empty).
      5. Else → env vars (SMTP_HOST / IMAP_HOST / ...).

    Returned dict always has the same shape as before; an `account_id` key is
    added so callers can stamp derivative records (email_ai_replies etc.).

    SECURITY: without `owner`, the fallback queries (is_default, first-enabled)
    don't filter by user — so on a multi-user deploy a brand-new account would
    inherit whoever else's IMAP/SMTP creds happened to be the default. Pass
    `owner` from the route's auth dependency to scope the lookup.
    """
    import os
    from core.database import SessionLocal as _SL, EmailAccount as _EA

    def _owner_or_matching_legacy_account(query):
        if not owner:
            return query
        from sqlalchemy import and_, or_
        unowned = or_(_EA.owner == None, _EA.owner == "")  # noqa: E711
        same_mailbox = or_(_EA.imap_user == owner, _EA.from_address == owner)
        return query.filter(or_(_EA.owner == owner, and_(unowned, same_mailbox)))

    resolved_id = None
    row = None
    try:
        db = _SL()
        try:
            if account_id:
                row = db.query(_EA).filter(_EA.id == account_id, _EA.enabled == True).first()  # noqa: E712
                # If the resolved row belongs to a different owner, treat as
                # not-found rather than silently serving it. This is a defense
                # in depth — `require_owner` already calls `_assert_owns_account`
                # for query-param account_ids, but other callers (cookbook
                # rules, scheduled poller) may not.
                if row is not None and owner and row.owner and row.owner != owner:
                    row = None
            # Fallback path — restrict to this owner's accounts so we don't
            # leak another user's default mailbox to an unconfigured user.
            if row is None:
                q = db.query(_EA).filter(_EA.is_default == True, _EA.enabled == True)  # noqa: E712
                q = _owner_or_matching_legacy_account(q)
                row = q.first()
            if row is None:
                q = db.query(_EA).filter(_EA.enabled == True)  # noqa: E712
                q = _owner_or_matching_legacy_account(q)
                row = q.order_by(_EA.created_at.asc()).first()
            if row is not None:
                resolved_id = row.id
                cfg = {
                    "account_id": row.id,
                    "account_name": row.name,
                    "smtp_host": row.smtp_host or "",
                    "smtp_port": int(row.smtp_port or 465),
                    "smtp_security": _smtp_security_mode({"smtp_security": getattr(row, "smtp_security", ""), "smtp_port": row.smtp_port}),
                    "smtp_user": row.smtp_user or "",
                    "smtp_password": _decrypt(row.smtp_password or ""),
                    "imap_host": row.imap_host or "",
                    "imap_port": int(row.imap_port or 993),
                    "imap_user": row.imap_user or "",
                    "imap_password": _decrypt(row.imap_password or ""),
                    "imap_starttls": bool(row.imap_starttls),
                    "from_address": row.from_address or row.imap_user or "",
                }
                if not (cfg["smtp_host"] and cfg["smtp_user"] and cfg["smtp_password"]):
                    logger.warning(f"SMTP not configured for account {row.name!r}")
                if not (cfg["imap_host"] and cfg["imap_user"] and cfg["imap_password"]):
                    logger.warning(f"IMAP not configured for account {row.name!r}")
                return cfg
        finally:
            db.close()
    except Exception as e:
        logger.debug(f"email_accounts lookup failed, falling back to settings.json: {e}")

    # Legacy fallback — flat keys in settings.json / env vars
    settings = _load_settings()
    cfg = {
        "account_id": resolved_id,
        "account_name": "legacy",
        "smtp_host": settings.get("smtp_host", os.environ.get("SMTP_HOST", "")),
        "smtp_port": int(settings.get("smtp_port", os.environ.get("SMTP_PORT", "465")) or 465),
        "smtp_security": _smtp_security_mode({
            "smtp_security": settings.get("smtp_security", os.environ.get("SMTP_SECURITY", "")),
            "smtp_port": settings.get("smtp_port", os.environ.get("SMTP_PORT", "465")),
        }),
        "smtp_user": settings.get("smtp_user", os.environ.get("SMTP_USER", "")),
        "smtp_password": settings.get("smtp_password", os.environ.get("SMTP_PASSWORD", "")),
        "imap_host": settings.get("imap_host", os.environ.get("IMAP_HOST", "")),
        "imap_port": int(settings.get("imap_port", os.environ.get("IMAP_PORT", "993")) or 993),
        "imap_user": settings.get("imap_user", os.environ.get("IMAP_USER", "")),
        "imap_password": settings.get("imap_password", os.environ.get("IMAP_PASSWORD", "")),
        "imap_starttls": settings.get("imap_starttls", True),
        "from_address": settings.get("email_from", os.environ.get("EMAIL_FROM", "")),
    }
    if not (cfg["smtp_host"] and cfg["smtp_user"] and cfg["smtp_password"]):
        logger.warning("SMTP not configured — add an Email Account in Settings or set env vars")
    if not (cfg["imap_host"] and cfg["imap_user"] and cfg["imap_password"]):
        logger.warning("IMAP not configured — add an Email Account in Settings or set env vars")
    return cfg


def _list_email_accounts() -> list[dict]:
    """Return all enabled accounts in creation order. Used by background loops
    that iterate over every account (auto-summarize, urgency, etc.)."""
    from core.database import SessionLocal as _SL, EmailAccount as _EA
    try:
        db = _SL()
        try:
            rows = (
                db.query(_EA)
                .filter(_EA.enabled == True)  # noqa: E712
                .order_by(_EA.is_default.desc(), _EA.created_at.asc())
                .all()
            )
            return [_get_email_config(r.id) for r in rows]
        finally:
            db.close()
    except Exception as e:
        logger.debug(f"_list_email_accounts failed, returning [default]: {e}")
        return [_get_email_config()]


# ── IMAP helpers ──

def _coerce_imap_timeout_seconds(raw: str | None) -> int:
    try:
        value = int(raw or "30")
    except (TypeError, ValueError):
        value = 30
    return max(5, min(value, 300))


_IMAP_TIMEOUT_SECONDS = _coerce_imap_timeout_seconds(os.environ.get("ODYSSEUS_IMAP_TIMEOUT_SECONDS"))


def _open_imap_connection(host: str, port: int, *, starttls: bool, timeout: int = _IMAP_TIMEOUT_SECONDS):
    """Open an IMAP connection using the configured security mode."""
    port = int(port or 993)
    if starttls:
        conn = imaplib.IMAP4(host, port, timeout=timeout)
        try:
            conn.starttls()
        except Exception:
            # Don't leak the open plain socket if the STARTTLS upgrade is
            # rejected; close it before propagating. (#3174)
            try:
                conn.shutdown()
            except Exception:
                pass
            raise
    elif port == 993:
        conn = imaplib.IMAP4_SSL(host, port, timeout=timeout)
    else:
        conn = imaplib.IMAP4(host, port, timeout=timeout)
    try:
        conn.sock.settimeout(timeout)
    except Exception:
        pass
    # Raise the IMAP line-length limit from the default 1 MB to 50 MB so that
    # large mailboxes (tens of thousands of messages) don't crash with
    # "got more than 1000000 bytes" on UID SEARCH ALL.  (#2883)
    imaplib._MAXLINE = 50_000_000
    return conn

def _imap_connect(account_id: str | None = None, owner: str = "",
                  timeout: int = _IMAP_TIMEOUT_SECONDS):
    # SECURITY: passing `owner` scopes the fallback config lookup so a brand
    # new user doesn't get connected against another user's default mailbox
    # when they have no account configured.
    #
    # `timeout` is overridable so short-lived callers (e.g. the service-health
    # probe) can impose a tighter budget than the default IMAP timeout.
    cfg = _get_email_config(account_id, owner=owner)
    # Connection mode:
    #   STARTTLS on → plain + upgrade
    #   STARTTLS off + port 993 → implicit SSL (IMAPS)
    #   STARTTLS off + any other port → plain (local Dovecot, custom ports)
    # The last branch is critical: previously this fell into IMAP4_SSL
    # for any non-STARTTLS port, which would fail the TLS handshake on
    # plain local servers (Dovecot on 31143, etc.).
    conn = _open_imap_connection(
        cfg["imap_host"],
        cfg["imap_port"],
        starttls=bool(cfg.get("imap_starttls")),
        timeout=timeout,
    )
    try:
        conn.login(cfg["imap_user"], cfg["imap_password"])
    except Exception:
        # A failed AUTHENTICATE (e.g. an Office 365 app password on an
        # MFA-enabled tenant, #3174) otherwise orphans the already-connected
        # socket; close it before propagating so a misconfigured account
        # can't leak one descriptor per retry / background poller pass.
        try:
            conn.shutdown()
        except Exception:
            pass
        raise
    return conn


from contextlib import contextmanager


# Filled in by setup_email_routes() once its closure-scoped pool helpers are
# defined. Keyed so we can swap them out in tests.
_POOL_HOOKS: dict = {"connect": None, "release": None}


@contextmanager
def _imap(account_id: str | None = None, owner: str = ""):
    """IMAP connection scoped to a `with` block.

    Uses the connection pool when available so we don't pay the
    TCP+TLS+LOGIN handshake (~30-100ms with Dovecot) on every request.
    Falls back to a fresh connect+logout pair before `setup_email_routes()`
    has run (e.g. background pollers spinning up early).

    SECURITY: `owner` flows through `_imap_connect` → `_get_email_config`
    so the fallback config lookup (when `account_id` is missing) is scoped
    to this user's accounts.
    """
    pool_connect = _POOL_HOOKS.get("connect")
    pool_release = _POOL_HOOKS.get("release")
    if pool_connect and pool_release:
        # SECURITY: forward owner so the pool slot is per-user and the
        # fresh-connection fallback runs through a scoped config lookup.
        try:
            conn, _reused = pool_connect(account_id, owner=owner)
        except TypeError:
            # Older hook signature without owner — fall back transparently.
            conn, _reused = pool_connect(account_id)
        ok = True
        try:
            yield conn
        except Exception:
            ok = False
            raise
        finally:
            try:
                try:
                    pool_release(account_id, conn, ok=ok, owner=owner)
                except TypeError:
                    pool_release(account_id, conn, ok=ok)
            except Exception:
                pass
        return
    # Fallback: plain connect+logout. Used pre-setup or in tests.
    conn = _imap_connect(account_id, owner=owner)
    try:
        yield conn
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _decode_header(raw):
    if not raw:
        return ""
    try:
        # make_header concatenates per RFC 2047: no spurious space between an
        # encoded-word and adjacent plain text (plain runs keep their own
        # whitespace), and the whitespace between two adjacent encoded-words is
        # dropped. The old " ".join produced "Re:  Jose"-style double spaces on
        # every non-ASCII subject or sender.
        return str(email.header.make_header(email.header.decode_header(raw)))
    except Exception:
        # Malformed header or unknown/invalid MIME charset (e.g. a spam header
        # like =?x-unknown-charset?B?...?=) makes make_header raise LookupError;
        # fall back to a lossy per-part decode. errors="replace" only covers
        # byte-decode errors, not codec lookup, hence the explicit utf-8 retry.
        decoded = []
        for data, charset in email.header.decode_header(raw):
            if isinstance(data, bytes):
                try:
                    decoded.append(data.decode(charset or "utf-8", errors="replace"))
                except (LookupError, ValueError):
                    decoded.append(data.decode("utf-8", errors="replace"))
            else:
                decoded.append(data)
        return "".join(decoded)


def _detect_sent_folder(conn):
    """Find the server's Sent folder name. Returns 'Sent' if nothing matches.

    Different IMAP servers expose the sent folder under different names:
      Dovecot/typical: "Sent"
      Gmail:          "[Gmail]/Sent Mail"
      Outlook/EWS:    "Sent Items"
      Some hosts:     "INBOX.Sent"
    """
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
        # Prefer \Sent flag in LIST response if present.
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


def _detect_drafts_folder(conn):
    """Find the server's Drafts folder name. Gmail usually exposes
    "[Gmail]/Drafts"; other servers often use "Drafts"."""
    candidates = ("Drafts", "[Gmail]/Drafts", "Draft", "INBOX.Drafts")
    try:
        status, folders = conn.list()
        if status != "OK" or not folders:
            return "Drafts"
        names = []
        for f in folders:
            decoded = f.decode() if isinstance(f, bytes) else str(f)
            m = re.search(r'"([^"]*)"\s*$|(\S+)\s*$', decoded)
            if m:
                names.append(m.group(1) or m.group(2))
        for f in folders:
            decoded = f.decode() if isinstance(f, bytes) else str(f)
            if r"\Drafts" in decoded or r"\Draft" in decoded:
                m = re.search(r'"([^"]*)"\s*$|(\S+)\s*$', decoded)
                if m:
                    return m.group(1) or m.group(2)
        for c in candidates:
            if c in names:
                return c
    except Exception:
        pass
    return "Drafts"


def _detect_spam_folder(conn):
    """Find the server's Junk/Spam folder name, if any."""
    try:
        status, folders = conn.list()
        if status != "OK" or not folders:
            return None
        preferred = None
        fallback = None
        for f in folders:
            decoded = f.decode() if isinstance(f, bytes) else str(f)
            m = re.search(r'"([^"]*)"\s*$|(\S+)\s*$', decoded)
            if not m:
                continue
            name = m.group(1) or m.group(2)
            if r"\Junk" in decoded:
                preferred = name
                break
            low = name.lower()
            if low in ("junk", "spam", "junk mail", "junk e-mail") or low.endswith("/junk") or low.endswith("/spam"):
                fallback = fallback or name
        return preferred or fallback
    except Exception:
        return None


def _imap_move(uid, dest, src="INBOX", account_id: str | None = None, owner: str = ""):
    """Move a single IMAP UID from src folder to dest. Returns True on success."""
    c = None
    try:
        c = _imap_connect(account_id, owner=owner)
        c.select(_q(src))
        status, _ = c.copy(uid, _q(dest))
        if status != "OK":
            return False
        c.store(uid, "+FLAGS", "\\Deleted")
        c.expunge()
        return True
    except Exception as e:
        logger.warning(f"IMAP move {uid} → {dest} failed: {e}")
        return False
    finally:
        if c:
            try:
                c.logout()
            except Exception:
                pass


def _extract_attachment_text(msg, max_chars: int = 6000) -> str:
    """Pull readable text out of an email's attachments — PDF (via PyMuPDF),
    plain text, markdown, csv, log. Caps total at `max_chars`. Returns a
    formatted string with `[Attachment: filename]\\n<content>` blocks
    separated by `---`. Empty string if there's nothing useful.

    Used by the summarize/reply pipeline so an email like "see attached
    invoice" produces a summary that actually references the invoice.
    """
    if not msg or not msg.is_multipart():
        return ""
    out_parts: list[str] = []
    total = 0
    import os as _os
    import tempfile as _tempfile
    for part in msg.walk():
        if part.is_multipart():
            continue
        cd = str(part.get("Content-Disposition", ""))
        ct = (part.get_content_type() or "").lower()
        if ct in ("text/plain", "text/html") and "attachment" not in cd.lower():
            continue
        filename = part.get_filename() or ""
        if filename:
            try:
                filename = _decode_header(filename)
            except Exception:
                pass
        fname_lower = (filename or "").lower()
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        # Cap per-attachment size to avoid huge PDFs blowing the budget.
        if len(payload) > 2_000_000:
            continue
        text = ""
        try:
            if ct == "application/pdf" or fname_lower.endswith(".pdf"):
                tmp = _tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
                try:
                    tmp.write(payload)
                    tmp.close()
                    from src.personal_docs import extract_pdf_text
                    text = extract_pdf_text(tmp.name) or ""
                finally:
                    try:
                        _os.unlink(tmp.name)
                    except Exception:
                        pass
            elif ct.startswith("text/") or fname_lower.endswith((".txt", ".md", ".csv", ".log", ".json")):
                text = payload.decode("utf-8", errors="replace")
        except Exception as e:
            logger.debug(f"attachment-text extract failed for {filename}: {e}")
            continue
        text = (text or "").strip()
        if not text:
            continue
        remaining = max_chars - total
        if remaining <= 0:
            break
        snippet = text[:remaining]
        out_parts.append(f"[Attachment: {filename or 'file'}]\n{snippet}")
        total += len(snippet)
        if total >= max_chars:
            break
    return "\n\n---\n\n".join(out_parts)


def _list_attachments_from_msg(msg):
    """Return a list of attachment metadata from an email message."""
    attachments = []
    if not msg.is_multipart():
        return attachments
    idx = 0
    for part in msg.walk():
        if part.is_multipart():
            continue
        cd = str(part.get("Content-Disposition", ""))
        ct = part.get_content_type()
        # Skip text/html body parts (only consider real attachments)
        if ct in ("text/plain", "text/html") and "attachment" not in cd:
            continue
        filename = part.get_filename()
        if filename:
            filename = _decode_header(filename)
        else:
            # Inline images, etc. - generate a name
            ext = ct.split("/")[-1] if "/" in ct else "bin"
            filename = f"attachment_{idx}.{ext}"
        payload = part.get_payload(decode=True)
        size = len(payload) if payload else 0
        attachments.append({
            "index": idx,
            "filename": filename,
            "content_type": ct,
            "size": size,
            "is_inline": "inline" in cd.lower(),
        })
        idx += 1
    return attachments


def _extract_attachment_to_disk(msg, index, target_dir):
    """Extract a specific attachment to disk and return the file path."""
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
                ext = ct.split("/")[-1] if "/" in ct else "bin"
                filename = f"attachment_{idx}.{ext}"
            # Sanitize
            safe_name = re.sub(r"[^\w\s\-.]", "_", filename).strip()
            payload = part.get_payload(decode=True)
            if not payload:
                return None
            target_dir.mkdir(parents=True, exist_ok=True)
            filepath = target_dir / safe_name
            with open(filepath, "wb") as f:
                f.write(payload)
            return filepath
        idx += 1
    return None


def _extract_html(msg):
    """Extract raw HTML body from an email message, if present."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if ct == "text/html" and "attachment" not in cd:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
    elif msg.get_content_type() == "text/html":
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    return None


def _extract_text(msg):
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


def _fetch_sender_thread_context(sender_addr: str,
                                 exclude_uid: str = "",
                                 exclude_folder: str = "INBOX",
                                 limit: int = 3,
                                 max_chars_per_email: int = 1500,
                                 max_attachment_chars: int = 4000,
                                 account_id: str | None = None,
                                 owner: str = "") -> str:
    """Pull the last N emails from `sender_addr` (across common folders),
    extract their body snippets + attachment text, and return one formatted
    block ready to be glued into an LLM system prompt as "REFERENCED MATERIAL".

    Returns empty string if nothing useful was found. Never raises.

    Used by the AI reply path so a follow-up like "regarding question 3 of the
    document you sent" can actually quote that document instead of pretending.
    """
    if not sender_addr:
        return ""
    sender_addr = sender_addr.strip().lower()
    if not sender_addr:
        return ""

    blocks: list[str] = []
    seen_uids: set[tuple[str, str]] = set()  # (folder, uid)
    if exclude_uid:
        seen_uids.add((exclude_folder or "INBOX", str(exclude_uid)))

    conn = None
    try:
        conn = _imap_connect(account_id, owner=owner)
        for folder in ["INBOX", "Sent", "Archive", "Drafts"]:
            if len(blocks) >= limit:
                break
            try:
                st_sel, _ = conn.select(_q(folder), readonly=True)
                if st_sel != "OK":
                    continue
            except Exception:
                continue
            try:
                addr_escaped = sender_addr.replace('"', '\\"')
                status, sdata = conn.search(None, f'(FROM "{addr_escaped}")')
                if status != "OK" or not sdata or not sdata[0]:
                    continue
                uids = sdata[0].split()
                # Most recent first.
                uids = list(reversed(uids))
            except Exception:
                continue

            for raw_uid in uids:
                if len(blocks) >= limit:
                    break
                uid = raw_uid.decode() if isinstance(raw_uid, bytes) else str(raw_uid)
                key = (folder, uid)
                if key in seen_uids:
                    continue
                seen_uids.add(key)

                try:
                    st_f, msg_data = conn.fetch(raw_uid, "(RFC822)")
                    if st_f != "OK" or not msg_data:
                        continue
                    raw_bytes = None
                    for part in msg_data:
                        if isinstance(part, tuple) and len(part) >= 2 and part[1]:
                            raw_bytes = part[1]
                            break
                    if not raw_bytes:
                        continue
                    msg = email_mod.message_from_bytes(raw_bytes)
                except Exception as e:
                    logger.debug(f"sender-thread-context fetch fail uid={uid}: {e}")
                    continue

                try:
                    subj = _decode_header(msg.get("Subject", "(no subject)"))
                    date_hdr = msg.get("Date", "")
                    body_text = (_extract_text(msg) or "").strip()
                    body_text = re.sub(r"\n{3,}", "\n\n", body_text)
                    if len(body_text) > max_chars_per_email:
                        body_text = body_text[:max_chars_per_email].rstrip() + "…"
                    atts_text = _extract_attachment_text(msg, max_chars=max_attachment_chars)
                except Exception as e:
                    logger.debug(f"sender-thread-context parse fail uid={uid}: {e}")
                    continue

                if not body_text and not atts_text:
                    continue

                lines = [f"— {folder} · {date_hdr} · Subject: {subj}"]
                if body_text:
                    lines.append(body_text)
                if atts_text:
                    lines.append(atts_text)
                blocks.append("\n".join(lines))
    except Exception as e:
        logger.warning(f"sender-thread-context: imap failed: {e}")
    finally:
        if conn:
            try: conn.close()
            except Exception: pass
            try: conn.logout()
            except Exception: pass

    if not blocks:
        return ""
    return "\n\n=====\n\n".join(blocks)


def _pre_retrieve_context(
    body: str,
    sender: str,
    account_id: str | None = None,
    owner: str = "",
) -> tuple:
    """Extract key terms from an incoming email and search past emails + contacts.

    Returns (context_snippets, terms_list). Best-effort; never raises.

    Sec note: this is called from the auto-reply path. An attacker who can
    craft an inbound email's content to contain Capitalized words matching
    private context (legal/medical names, project codenames) can coerce the
    LLM reply to quote that context back in the auto-reply. To narrow the
    blast radius:
      - require terms ≥ 5 chars (was 4),
      - require multiword for an unknown sender,
      - cap to 3 terms (was 4),
      - skip entirely for senders with no prior contact / no past mail.
    """
    STOPWORDS = {"dear", "hello", "hi", "hey", "thanks", "thank", "regards",
                 "best", "kind", "sincerely", "cheers", "the", "this", "that",
                 "from", "subject", "re", "fwd", "yours", "my", "our", "your"}
    context_snippets = []
    terms_list = []
    try:
        # ── Known-sender check: only retrieve context for senders we already
        # have a relationship with. New / cold senders get an empty context.
        sender_addr = email.utils.parseaddr(sender or "")[1].lower()
        # The CardDAV address book is global admin data backed by a single
        # Radicale instance, so only fold it into reply context for an admin /
        # single-user owner. Non-admin owners still get their own (owner-scoped)
        # IMAP history below, just not the shared contacts.
        try:
            from src.tool_security import owner_is_admin_or_single_user
            contacts_allowed = owner_is_admin_or_single_user(owner or None)
        except Exception:
            contacts_allowed = not bool(owner)
        is_known = False
        if contacts_allowed:
            try:
                from routes.contacts_routes import _fetch_contacts
                for c in _fetch_contacts() or []:
                    # Contacts are normalized to plural `emails` lists, but
                    # keep the legacy singular key fallback for older data.
                    contact_emails = []
                    raw_emails = c.get("emails")
                    if isinstance(raw_emails, list):
                        contact_emails.extend(str(e or "") for e in raw_emails)
                    legacy_email = c.get("email")
                    if legacy_email:
                        contact_emails.append(str(legacy_email))
                    if any((addr or "").strip().lower() == sender_addr for addr in contact_emails):
                        is_known = True
                        break
            except Exception:
                pass
        if not is_known and sender_addr:
            try:
                with _imap(account_id, owner=owner) as _ck:
                    _ck.select("INBOX", readonly=True)
                    st_known, dk = _ck.search(None, f'(FROM "{sender_addr}")')
                    if st_known == "OK" and dk and dk[0]:
                        is_known = True
            except Exception:
                pass
        if not is_known:
            logger.info(f"Pre-retrieval skipped — unknown sender {sender_addr}")
            return [], []

        seen = set()
        multiword = []
        singleword = []
        for m in re.finditer(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b", body or ""):
            term = m.group(1).strip()
            key = term.lower()
            if key in seen:
                continue
            first = term.split()[0].lower()
            if first in STOPWORDS:
                continue
            if len(term) < 5:
                continue
            seen.add(key)
            (multiword if " " in term else singleword).append(term)
        sender_name_clean = _decode_header(sender or "").split("<")[0].strip().lower()
        # Multiword terms are far less likely to collide with unrelated context
        # than single capitalized words. Prefer them; only fall back to
        # singletons when we don't have enough multiwords.
        ranked = [t for t in (multiword + singleword) if t.lower() != sender_name_clean]
        terms_list = ranked[:3]
        logger.info(f"Pre-retrieval terms={terms_list}")

        if not terms_list:
            return context_snippets, terms_list

        ctx_conn = None
        try:
            ctx_conn = _imap_connect(account_id, owner=owner)
            for folder in ["INBOX", "Sent", "Archive", "Drafts"]:
                try:
                    st_sel, _sd = ctx_conn.select(_q(folder), readonly=True)
                    if st_sel != "OK":
                        continue
                except Exception:
                    continue
                for term in terms_list:
                    try:
                        safe_term = term.replace('"', '').replace('\\', '')
                        st, data2 = ctx_conn.search(None, "TEXT", f'"{safe_term}"')
                        if st != "OK" or not data2 or not data2[0]:
                            continue
                        all_hits = data2[0].split()
                        hit_uids = all_hits[-2:]
                        logger.info(f"  [{folder}] term={term!r} hits={len(all_hits)}")
                        for huid in hit_uids:
                            try:
                                st2, hd = ctx_conn.fetch(huid, "(RFC822)")
                                if st2 != "OK" or not hd or not hd[0]:
                                    continue
                                hmsg = email_mod.message_from_bytes(hd[0][1])
                                hsubj = _decode_header(hmsg.get("Subject", ""))
                                hfrom = _decode_header(hmsg.get("From", ""))
                                hdate = hmsg.get("Date", "")
                                hbody = _extract_text(hmsg)[:600]
                                context_snippets.append(
                                    f"[{folder} match for \"{term}\"]\nFrom: {hfrom}\nDate: {hdate}\nSubject: {hsubj}\n{hbody}"
                                )
                            except Exception:
                                continue
                    except Exception as _e:
                        logger.warning(f"  search {folder} {term!r} failed: {_e}")
                        continue
        except Exception as _e:
            logger.warning(f"IMAP context search failed: {_e}")
        finally:
            if ctx_conn:
                try: ctx_conn.logout()
                except Exception: pass

        try:
            from routes.contacts_routes import _fetch_contacts
            all_contacts = _fetch_contacts() if contacts_allowed else []
            for term in terms_list:
                t_lower = term.lower()
                matches = [c for c in all_contacts
                           if t_lower in (c.get("name") or "").lower()
                           or any(t_lower in (e or "").lower() for e in (c.get("emails") or []))]
                for c in matches[:2]:
                    parts = [f"Name: {c.get('name','')}"]
                    if c.get("emails"):
                        parts.append(f"Email: {', '.join(c['emails'])}")
                    if c.get("phones"):
                        parts.append(f"Phone: {', '.join(c['phones'])}")
                    context_snippets.append(f"[Contact match for \"{term}\"] " + ", ".join(parts))
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"Pre-retrieval failed: {e}")
    logger.info(f"Pre-retrieval snippets={len(context_snippets)}")
    return context_snippets, terms_list


_EMAIL_REPLY_SYS_PROMPT_BASE = (
    "You are drafting an email reply. Write only the reply body, no subject line, "
    "and no extra commentary. The saved WRITING STYLE below outranks generic tone guidance. "
    "If the saved style says to use a greeting/sign-off, include them. For English replies, "
    "default to 'Hi [Name]' rather than 'Hey'. Be direct and concise. Match the tone of the "
    "original email without violating the saved style.\n\n"
    "MECHANICAL STYLE RULES — CRITICAL: Never use an em dash or en dash; use -- instead. "
    "Never use curly apostrophes; write I'm, don't, we'll with straight '. Do not start "
    "with 'Hey' unless the saved style explicitly requests it.\n\n"
    "IDENTITY RULE — CRITICAL: write as the user/mailbox owner only. NEVER sign as, "
    "speak as, or imply you are the recipient, original sender, quoted sender, spouse, "
    "assistant, company, or any third party. Do not copy a name from the quoted thread "
    "into the sign-off. If a writing style below names a signature, use only that "
    "signature; otherwise omit the sign-off.\n\n"
    "CRITICAL RULE: NEVER invent facts, names, dates, phone numbers, emails, addresses, "
    "or any specifics not explicitly present in the RELEVANT CONTEXT section below or "
    "the original email itself. If the sender asks for information you don't have in "
    "the context, say plainly that you don't have it on hand — do NOT guess or fabricate. "
    "Do not promise to 'look it up' or 'get back to you soon' as a way to pad the reply. "
    "If you have no real information to offer, write a short honest reply (2-4 sentences max).\n\n"
    "OUTPUT FORMAT — IMPORTANT: Put ONLY the final email reply between these exact markers, "
    "each on its own line:\n"
    "<<<REPLY>>>\n"
    "(the reply body goes here)\n"
    "<<<END>>>\n"
    "Any reasoning, planning, or notes-to-self must come BEFORE the <<<REPLY>>> marker "
    "(ideally wrapped in <think>...</think>). Only the text between <<<REPLY>>> and <<<END>>> "
    "is sent as the email — nothing else is shown to anyone."
)


# ── Request models ──

class SendEmailRequest(BaseModel):
    to: str
    cc: Optional[str] = None
    bcc: Optional[str] = None
    subject: str
    body: str
    # WYSIWYG compose sends the rendered HTML here; the server sanitizes it and
    # uses it for the text/html part (body stays the plain-text fallback). When
    # absent, the server renders markdown from `body` instead.
    body_html: Optional[str] = None
    in_reply_to: Optional[str] = None
    references: Optional[str] = None
    # List of uploaded attachment tokens (filenames in COMPOSE_UPLOADS_DIR)
    attachments: Optional[List[str]] = None
    # Which account to send from. None = default account.
    account_id: Optional[str] = None
    # Internal marker for Odysseus-generated mail (e.g. reminder, scheduled).
    odysseus_kind: Optional[str] = None
    # If true, /send waits for SMTP + Sent append and returns the sent UID.
    wait_for_delivery: bool = False


class ExtractStyleRequest(BaseModel):
    sample_count: Optional[int] = 20
