"""Email send / IMAP / config primitives — real implementations (ARCH-P7-01).

These low-level, FastAPI-free primitives were relocated here from
routes/email_helpers.py and routes/email_routes.py (P8-T14) so the dependency
is inverted: the route layer now imports FROM this service instead of the
service delegating back into routes via importlib. Keeping them in src/services
lets any src/ module (pollers, tools, schedulers) use them without dragging in
the route layer's FastAPI imports.

Do NOT add route-layer imports (FastAPI, APIRouter, Request) here.

The only remaining delegation is _run_auto_summarize_once, which wraps the
auto-summarize loop in routes/email_pollers.py (that body is route/poller-coupled
and is out of scope for this dependency inversion).
"""

import imaplib
import importlib
import os
import smtplib
import email as email_mod  # noqa: F401  (kept for parity with the original module surface)
import email.header
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


# Single source of truth for persisted paths is src.constants (which honors
# the ODYSSEUS_DATA_DIR deploy-time override).
from src.constants import DATA_DIR as _DATA_DIR, SETTINGS_FILE as _SETTINGS_FILE, SCHEDULED_EMAILS_DB

DATA_DIR = Path(_DATA_DIR)
SETTINGS_FILE = Path(_SETTINGS_FILE)
SCHEDULED_DB = Path(SCHEDULED_EMAILS_DB)


# ── SMTP ──

def _smtp_security_mode(cfg: dict) -> str:
    raw = str(cfg.get("smtp_security") or "").strip().lower()
    if raw in {"ssl", "starttls", "none"}:
        return raw
    port = int(cfg.get("smtp_port") or 465)
    if port == 587:
        return "starttls"
    return "ssl"


def _send_smtp_message(cfg: dict, from_addr: str, recipients: list, message, timeout: int = 30) -> None:
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


def _smtp_ready(cfg: dict) -> bool:
    return bool(cfg.get("smtp_host") and cfg.get("smtp_user") and cfg.get("smtp_password"))


# ── Settings persistence ──

def _load_settings():
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    return {}


def _save_settings(settings):
    from core.atomic_io import atomic_write_json
    atomic_write_json(str(SETTINGS_FILE), settings, indent=2)


# ── Account config resolution ──

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
    from src.secret_storage import decrypt as _decrypt

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


# ── Scheduled-send DB bootstrap ──

def _ensure_owner_scoped_email_cache_table(conn, table: str, create_sql: str, columns: list):
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
        logging.getLogger(__name__).warning(f"{table} owner-migration skipped: {_mig_e}")


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
        logging.getLogger(__name__).warning(f"email_tags owner-migration skipped: {_mig_e}")
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


def get_scheduled_db_path():
    return SCHEDULED_DB


async def _run_auto_summarize_once(do_summary=True, do_reply=True, **kwargs):
    # Auto-summarize loop is poller/route-coupled; this is the one remaining
    # delegation (into routes.email_pollers, NOT routes.email_helpers/email_routes).
    mod = importlib.import_module('routes.email_pollers')
    return await mod._run_auto_summarize_once(do_summary=do_summary, do_reply=do_reply, **kwargs)
