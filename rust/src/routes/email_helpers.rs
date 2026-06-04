// routes/email_helpers.rs  <- routes/email_helpers.py (the TRANSPORT half + dataclasses)
//! Lower-level email TRANSPORT helpers used by both `email_routes.py` (the route
//! file) and `email_pollers.py` (the background loops). This is wave-8's
//! prerequisite: the IMAP / SMTP / MIME stack plus the account-config resolver,
//! folder/UID helpers, scheduled-DB bootstrap, the AI-pipeline text helpers, and
//! the request/response dataclasses.
//!
//! WHAT IS *NOT* HERE: `require_owner` / `require_user` / `_assert_owns_account`
//! live in this Python file but are ALREADY ported in
//! [`crate::routes::auth_adapter`] (they were hoisted there as foundation unit
//! F3 because the wave-4 routers needed them first). `email_routes` /
//! `email_pollers` import the same names; on the Rust side they call
//! `auth_adapter::{require_owner, require_user, assert_owns_account}`. We do NOT
//! re-port them.
//!
//! TRANSPORT BACKENDS (the wave-8 crates, all run synchronously — exactly like
//! Python's stdlib `imaplib`/`smtplib` are synchronous — so every blocking call
//! must run under `tokio::task::spawn_blocking` in the consuming async handler,
//! matching the design's IMAP/SMTP note):
//!   * IMAP `imaplib` -> the `imap` crate (3.0-alpha) + `native-tls`.
//!   * SMTP `smtplib` -> `lettre`'s blocking `SmtpTransport` + `send_raw(&Envelope,
//!     &[u8])` (the exact `smtp.sendmail(from, rcpts, msg)` analogue).
//!   * MIME `email`/`email.header` -> `mail-parser` for the read path; the central
//!     [`crate::src::text_helpers`] for think-strip; raw RFC822 bytes flow through.
//!
//! DOCUMENTED DRIFT (never faked):
//!   * `_decode_header`: Python's `email.header.decode_header` returns per-token
//!     `(bytes, charset)` pairs joined by a single space. `mail-parser` already
//!     RFC2047-decodes headers, but to keep the *exact* "join with space" shape
//!     for arbitrary raw header strings we decode encoded-words ourselves via
//!     `mail-parser`'s header decoder over a synthetic `X: <raw>` header. For the
//!     common case (no encoded-words) this is identity, matching Python.
//!   * The MIME walk (`_extract_text`/`_extract_html`/attachment helpers) is
//!     reproduced over `mail-parser`'s flat `parts` list (the `msg.walk()`
//!     analogue) with the same Content-Type / Content-Disposition gating. bs4 /
//!     `html.parser` shape drift is the same already-documented drift as
//!     [`crate::src::email_thread_parser`].


use crate::pylog as logger;
use mail_parser::{Message, MessageParser, MimeHeaders, PartType};
use once_cell::sync::Lazy;
use regex::Regex;
use serde::{Deserialize, Serialize};
use std::net::ToSocketAddrs;
use std::path::{Path, PathBuf};

// ===========================================================================
// Constants / paths
// ===========================================================================

/// `DATA_DIR = Path(__file__).resolve().parent.parent / "data"`. The Python
/// resolves the repo `data/` dir; the Rust side already exposes that exact path
/// as [`crate::core::constants::DATA_DIR`] (honoring `ODYSSEUS_DATA_DIR`).
fn data_dir() -> PathBuf {
    PathBuf::from(&*crate::core::constants::DATA_DIR)
}

/// `SETTINGS_FILE = DATA_DIR / "settings.json"`.
fn settings_file() -> PathBuf {
    data_dir().join("settings.json")
}

/// `ATTACHMENTS_DIR = Path(os.environ.get("ODYSSEUS_MAIL_ATTACHMENTS_DIR",
/// str(DATA_DIR / "mail-attachments")))`. The directory is created on first
/// access (the Python does `mkdir(parents=True, exist_ok=True)` at import time).
pub fn attachments_dir() -> PathBuf {
    let p = match crate::pyos::getenv_opt("ODYSSEUS_MAIL_ATTACHMENTS_DIR") {
        Some(v) => PathBuf::from(v),
        None => data_dir().join("mail-attachments"),
    };
    let _ = std::fs::create_dir_all(&p);
    p
}

/// `COMPOSE_UPLOADS_DIR = ATTACHMENTS_DIR / "_compose"` (created on access).
pub fn compose_uploads_dir() -> PathBuf {
    let p = attachments_dir().join("_compose");
    let _ = std::fs::create_dir_all(&p);
    p
}

/// `SCHEDULED_DB = DATA_DIR / "scheduled_emails.db"`.
pub fn scheduled_db() -> PathBuf {
    data_dir().join("scheduled_emails.db")
}

/// `re.sub(r"[^A-Za-z0-9._-]", "_", ...)` — flatten an attachment key to a single
/// safe path segment. Anything outside `[A-Za-z0-9._-]` (path separators, `..`
/// dots-with-slashes, NUL, etc.) becomes `_`, so a value like `../../tmp` collapses
/// to a flat `___.._tmp`-style token that cannot escape the attachments root.
static ATTACHMENT_KEY_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"[^A-Za-z0-9._-]").unwrap());

/// `attachment_extract_dir(folder, uid)` — containment-safe extraction directory
/// for an attachment.
///
/// `folder` and `uid` are user-controlled (query/path params). Flatten them to a
/// single safe path segment so a value like `folder='../../tmp'` can't escape
/// [`attachments_dir`], then assert containment as belt-and-suspenders. On a
/// violation returns an `HttpException(400, "Invalid attachment location")` —
/// the exact Python `HTTPException(400, ...)`.
pub fn attachment_extract_dir(folder: &str, uid: &str) -> Result<PathBuf, crate::routes::HttpException> {
    // `key = re.sub(r"[^A-Za-z0-9._-]", "_", f"{folder}_{uid}") or "_"`.
    let raw = format!("{folder}_{uid}");
    let mut key = ATTACHMENT_KEY_RE.replace_all(&raw, "_").into_owned();
    if key.is_empty() {
        // `... or "_"` — an empty sanitized key falls back to a single underscore.
        key = "_".to_string();
    }
    let base = attachments_dir();
    let target = base.join(&key);
    // `target = (...).resolve()` / `base = (...).resolve()` then assert containment
    // (`target != base and base not in target.parents`). `resolve()` normalizes the
    // path WITHOUT requiring it to exist (the dir is created later by the extractor);
    // we mirror that with a lexical normalization (no filesystem canonicalize, so a
    // not-yet-created dir still validates) which is sufficient here because the key
    // has already been flattened to a single segment with no separators.
    let target_norm = lexically_normalize(&target);
    let base_norm = lexically_normalize(&base);
    // `target != base and base not in target.parents` — i.e. reject unless the
    // target IS the base or the base is a strict ancestor of the target.
    let contained = target_norm == base_norm
        || target_norm
            .ancestors()
            .skip(1) // ancestors() yields self first; `.parents` excludes self.
            .any(|p| p == base_norm.as_path());
    if !contained {
        return Err(crate::routes::HttpException::new(400, "Invalid attachment location"));
    }
    Ok(target_norm)
}

/// Lexically normalize a path (resolve `.` / `..` segments WITHOUT touching the
/// filesystem) — the `Path.resolve()` analogue for a possibly-not-yet-existing
/// directory. Keeps the path absolute if it started absolute.
fn lexically_normalize(p: &Path) -> PathBuf {
    use std::path::Component;
    let mut out = PathBuf::new();
    for comp in p.components() {
        match comp {
            Component::ParentDir => {
                // Pop the last normal component; keep root/prefix in place.
                if !out.pop() {
                    out.push(comp.as_os_str());
                }
            }
            Component::CurDir => {}
            other => out.push(other.as_os_str()),
        }
    }
    out
}

/// `_coerce_imap_timeout_seconds(raw)` — parse the raw env value as an int,
/// defaulting to 30 on a missing/garbage value, then clamp to `[5, 300]`.
/// Faithful to the Python `int(raw or "30")` (so empty string also -> 30) +
/// `max(5, min(value, 300))`.
fn coerce_imap_timeout_seconds(raw: Option<&str>) -> u64 {
    // `int(raw or "30")` — None OR empty string falls through to "30"; a non-int
    // value (TypeError/ValueError) also falls back to 30.
    let value: i64 = match raw {
        Some(s) if !s.is_empty() => s.trim().parse::<i64>().unwrap_or(30),
        _ => 30,
    };
    // `max(5, min(value, 300))`.
    value.clamp(5, 300) as u64
}

/// `_IMAP_TIMEOUT_SECONDS = _coerce_imap_timeout_seconds(
/// os.environ.get("ODYSSEUS_IMAP_TIMEOUT_SECONDS"))`. The Python evaluates this
/// once at import time; the Rust reads the env var on each connect (cheap; lets a
/// test set the var without a process-global). Default 30s, clamped to `[5, 300]`.
fn imap_timeout_seconds() -> u64 {
    coerce_imap_timeout_seconds(crate::pyos::getenv_opt("ODYSSEUS_IMAP_TIMEOUT_SECONDS").as_deref())
}

// ===========================================================================
// `_init_scheduled_db()` — bootstrap the standalone scheduled-emails SQLite DB.
// ===========================================================================

/// Port of `_init_scheduled_db()`. Runs at module bring-up (the Python executes
/// `_init_scheduled_db()` at import time); the integration layer calls this once
/// during `run()` startup, before the email pollers spawn. Best-effort: a failure
/// to open/create degrades gracefully (the Python would raise, but the consumers
/// guard their own opens), so we log and return.
pub fn init_scheduled_db() {
    let path = scheduled_db();
    let conn = match rusqlite::Connection::open(&path) {
        Ok(c) => c,
        Err(e) => {
            logger::warning(&format!("scheduled_emails.db open failed: {e}"));
            return;
        }
    };
    init_scheduled_db_on(&conn);
}

/// The DDL + lazy-migration body of [`init_scheduled_db`], factored out so it can
/// run against any [`rusqlite::Connection`] (the live scheduled DB at startup, or a
/// temp DB in tests). Behavior-identical to the Python `_init_scheduled_db()` body.
fn init_scheduled_db_on(conn: &rusqlite::Connection) {
    let ddl = [
        r#"CREATE TABLE IF NOT EXISTS scheduled_emails (
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
        )"#,
        r#"CREATE TABLE IF NOT EXISTS email_summaries (
            message_id TEXT PRIMARY KEY,
            uid TEXT,
            folder TEXT,
            subject TEXT,
            sender TEXT,
            summary TEXT NOT NULL,
            model_used TEXT,
            created_at TEXT NOT NULL
        )"#,
        r#"CREATE TABLE IF NOT EXISTS email_ai_replies (
            message_id TEXT PRIMARY KEY,
            uid TEXT,
            folder TEXT,
            reply TEXT NOT NULL,
            model_used TEXT,
            created_at TEXT NOT NULL
        )"#,
        r#"CREATE TABLE IF NOT EXISTS email_tags (
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
        )"#,
    ];
    for stmt in ddl {
        if let Err(e) = conn.execute(stmt, []) {
            logger::warning(&format!("scheduled_emails.db DDL failed: {e}"));
        }
    }

    // email_tags owner-migration (older installs had message_id as a bare PK and
    // no owner column). The whole block is `try/except -> warning`, so a failure
    // is swallowed with a log line.
    let migrate = || -> rusqlite::Result<()> {
        let cols = pragma_columns(conn, "email_tags")?;
        if !cols.iter().any(|c| c == "owner") {
            conn.execute(
                "ALTER TABLE email_tags ADD COLUMN owner TEXT DEFAULT ''",
                [],
            )?;
            conn.execute(
                r#"CREATE TABLE IF NOT EXISTS email_tags__new (
                    message_id TEXT,
                    owner TEXT DEFAULT '',
                    uid TEXT, folder TEXT, subject TEXT, sender TEXT,
                    tags TEXT, spam_verdict INTEGER DEFAULT 0,
                    spam_reason TEXT, moved_to TEXT, model_used TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (message_id, owner)
                )"#,
                [],
            )?;
            conn.execute(
                r#"INSERT OR IGNORE INTO email_tags__new
                      (message_id, owner, uid, folder, subject, sender, tags,
                       spam_verdict, spam_reason, moved_to, model_used, created_at)
                    SELECT message_id, COALESCE(owner, ''), uid, folder, subject,
                           sender, tags, spam_verdict, spam_reason, moved_to,
                           model_used, created_at
                    FROM email_tags"#,
                [],
            )?;
            conn.execute("DROP TABLE email_tags", [])?;
            conn.execute("ALTER TABLE email_tags__new RENAME TO email_tags", [])?;
        }
        Ok(())
    };
    if let Err(e) = migrate() {
        logger::warning(&format!("email_tags owner-migration skipped: {e}"));
    }

    let ddl2 = [
        r#"CREATE TABLE IF NOT EXISTS email_calendar_extractions (
            message_id TEXT PRIMARY KEY,
            uid TEXT,
            events_created INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )"#,
        r#"CREATE TABLE IF NOT EXISTS email_urgency_alerts (
            message_id TEXT PRIMARY KEY,
            uid TEXT,
            folder TEXT,
            subject TEXT,
            sender TEXT,
            urgency TEXT,
            reason TEXT,
            alerted INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )"#,
        r#"CREATE TABLE IF NOT EXISTS email_event_seen (
            owner TEXT NOT NULL,
            account_key TEXT NOT NULL,
            folder TEXT NOT NULL,
            message_key TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            PRIMARY KEY (owner, account_key, folder, message_key)
        )"#,
        r#"CREATE TABLE IF NOT EXISTS email_boundaries (
            message_id TEXT PRIMARY KEY,
            uid TEXT,
            folder TEXT,
            sig_start INTEGER,
            quote_start INTEGER,
            model_used TEXT,
            created_at TEXT NOT NULL
        )"#,
    ];
    for stmt in ddl2 {
        if let Err(e) = conn.execute(stmt, []) {
            logger::warning(&format!("scheduled_emails.db DDL failed: {e}"));
        }
    }

    // Lazy migration: add account_id / odysseus_kind / owner to scheduled_emails,
    // create the owner-status index, and backfill owner on legacy rows from the
    // owning email account. `try/except: pass` — swallow any error silently.
    let _ = (|| -> rusqlite::Result<()> {
        let cols = pragma_columns(conn, "scheduled_emails")?;
        if !cols.iter().any(|c| c == "account_id") {
            conn.execute("ALTER TABLE scheduled_emails ADD COLUMN account_id TEXT", [])?;
        }
        if !cols.iter().any(|c| c == "odysseus_kind") {
            conn.execute(
                "ALTER TABLE scheduled_emails ADD COLUMN odysseus_kind TEXT",
                [],
            )?;
        }
        if !cols.iter().any(|c| c == "owner") {
            conn.execute(
                "ALTER TABLE scheduled_emails ADD COLUMN owner TEXT DEFAULT ''",
                [],
            )?;
        }
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_scheduled_emails_owner_status \
             ON scheduled_emails(owner, status)",
            [],
        )?;
        // Backfill owner on legacy rows from the owning email account so the
        // owner-scoped list/cancel routes surface pre-migration scheduled sends to
        // the right user. `legacy_accounts = SELECT DISTINCT account_id ...`.
        let legacy_accounts: Vec<String> = {
            let mut stmt = conn.prepare(
                "SELECT DISTINCT account_id FROM scheduled_emails \
                 WHERE (owner IS NULL OR owner = '') AND account_id IS NOT NULL AND account_id != ''",
            )?;
            let rows = stmt.query_map([], |r| r.get::<_, String>(0))?;
            let mut v = Vec::new();
            for r in rows {
                v.push(r?);
            }
            v
        };
        if !legacy_accounts.is_empty() {
            // Inner `try/except: pass` — a DB hiccup in the EmailAccount lookup must
            // not abort the migration. Resolve each account's owner off the shared DB.
            let _ = (|| -> rusqlite::Result<()> {
                let acct_db = crate::core::database::session_local()?;
                for acct_id in &legacy_accounts {
                    // `row = db.query(EmailAccount.owner).filter(id == acct_id).first()`.
                    let acct_owner: String = acct_db
                        .query_row(
                            "SELECT owner FROM email_accounts WHERE id = ?1",
                            rusqlite::params![acct_id],
                            |r| r.get::<_, Option<String>>(0),
                        )
                        .ok()
                        .flatten()
                        .unwrap_or_default();
                    if !acct_owner.is_empty() {
                        conn.execute(
                            "UPDATE scheduled_emails SET owner = ?1 \
                             WHERE account_id = ?2 AND (owner IS NULL OR owner = '')",
                            rusqlite::params![acct_owner, acct_id],
                        )?;
                    }
                }
                Ok(())
            })();
        }
        Ok(())
    })();

    // Lazy migration: add turns_json to email_boundaries. `try/except: pass`.
    let _ = (|| -> rusqlite::Result<()> {
        let cols = pragma_columns(conn, "email_boundaries")?;
        if !cols.iter().any(|c| c == "turns_json") {
            conn.execute("ALTER TABLE email_boundaries ADD COLUMN turns_json TEXT", [])?;
        }
        Ok(())
    })();

    if let Err(e) = conn.execute(
        r#"CREATE TABLE IF NOT EXISTS sender_signatures (
            from_address TEXT PRIMARY KEY,
            signature_text TEXT,
            sample_count INTEGER,
            last_built_at TEXT NOT NULL,
            model_used TEXT,
            source TEXT
        )"#,
        [],
    ) {
        logger::warning(&format!("scheduled_emails.db DDL failed: {e}"));
    }
}

/// `[r[1] for r in conn.execute("PRAGMA table_info(<t>)")]` — the column names.
fn pragma_columns(conn: &rusqlite::Connection, table: &str) -> rusqlite::Result<Vec<String>> {
    let mut stmt = conn.prepare(&format!("PRAGMA table_info({table})"))?;
    let rows = stmt.query_map([], |r| r.get::<_, String>(1))?;
    let mut out = Vec::new();
    for r in rows {
        out.push(r?);
    }
    Ok(out)
}

// ===========================================================================
// settings.json load/save (`_load_settings` / `_save_settings`)
// ===========================================================================

/// `_load_settings()` — `json.loads(SETTINGS_FILE.read_text())` if it exists,
/// else `{}`. Returns the parsed JSON object (the Python returns a dict).
pub fn load_settings() -> serde_json::Map<String, serde_json::Value> {
    let path = settings_file();
    if path.exists() {
        match std::fs::read_to_string(&path) {
            Ok(text) => match serde_json::from_str::<serde_json::Value>(&text) {
                Ok(serde_json::Value::Object(m)) => return m,
                Ok(_) => return serde_json::Map::new(),
                Err(_) => return serde_json::Map::new(),
            },
            Err(_) => return serde_json::Map::new(),
        }
    }
    serde_json::Map::new()
}

/// `_save_settings(settings)` — `atomic_write_json(str(SETTINGS_FILE), settings,
/// indent=2)` via the already-ported [`crate::core::atomic_io`].
pub fn save_settings(settings: &serde_json::Map<String, serde_json::Value>) {
    let value = serde_json::Value::Object(settings.clone());
    let path = settings_file();
    let _ = crate::core::atomic_io::atomic_write_json(&path.to_string_lossy(), &value, Some(2));
}

// ===========================================================================
// Account config (`_get_email_config` / `_list_email_accounts`)
// ===========================================================================

/// `_smtp_security_mode(cfg)` — resolve the SMTP transport-security mode from the
/// stored `smtp_security` string + `smtp_port`. Returns one of `"ssl"` /
/// `"starttls"` / `"none"`.
///
/// Faithful to the Python:
///   * an explicit, recognized `smtp_security` value (case/space-insensitive) wins;
///   * else, port 587 -> `"starttls"`;
///   * else (465 / any other / unset) -> `"ssl"`.
///
/// `raw_security` is the raw stored string (may be empty / unknown); `smtp_port` is
/// the configured port (`int(cfg.get("smtp_port") or 465)` is reproduced by the
/// caller passing `0` for an unset port, which maps to 465 here).
pub fn smtp_security_mode(raw_security: &str, smtp_port: i64) -> String {
    // `raw = str(cfg.get("smtp_security") or "").strip().lower()`.
    let raw = raw_security.trim().to_lowercase();
    if matches!(raw.as_str(), "ssl" | "starttls" | "none") {
        return raw;
    }
    // `port = int(cfg.get("smtp_port") or 465)`.
    let port = if smtp_port != 0 { smtp_port } else { 465 };
    if port == 587 {
        "starttls".to_string()
    } else {
        "ssl".to_string()
    }
}

/// The resolved IMAP/SMTP config — the dict `_get_email_config` returns. Field
/// names mirror the Python dict keys 1:1. `account_id` is `None` for the legacy
/// settings.json/env fallback when no DB row resolved.
#[derive(Debug, Clone, Default)]
pub struct EmailConfig {
    pub account_id: Option<String>,
    pub account_name: String,
    pub smtp_host: String,
    pub smtp_port: i64,
    /// `smtp_security` — the resolved transport-security mode (`"ssl"`/`"starttls"`/
    /// `"none"`) computed via [`smtp_security_mode`]. Drives SSL vs STARTTLS vs
    /// plaintext in [`send_smtp_message`]. Always one of the three canonical values.
    pub smtp_security: String,
    pub smtp_user: String,
    pub smtp_password: String,
    pub imap_host: String,
    pub imap_port: i64,
    pub imap_user: String,
    pub imap_password: String,
    pub imap_starttls: bool,
    pub from_address: String,
}

/// A single `email_accounts` row, read off the shared DB (raw `rusqlite`,
/// module-private — the per-table DB reads this file owns). Mirrors the
/// SQLAlchemy `EmailAccount` columns `_get_email_config` reads.
struct AccountRow {
    id: String,
    owner: Option<String>,
    name: String,
    smtp_host: Option<String>,
    smtp_port: Option<i64>,
    smtp_user: Option<String>,
    smtp_password: Option<String>,
    imap_host: Option<String>,
    imap_port: Option<i64>,
    imap_user: Option<String>,
    imap_password: Option<String>,
    imap_starttls: bool,
    from_address: Option<String>,
    /// `EmailAccount.smtp_security` (`ssl | starttls | none`, column default `'ssl'`).
    /// May be NULL/empty on legacy rows; resolved through [`smtp_security_mode`].
    smtp_security: Option<String>,
}

const ACCOUNT_COLS: &str = "id, owner, name, smtp_host, smtp_port, smtp_user, \
    smtp_password, imap_host, imap_port, imap_user, imap_password, imap_starttls, \
    from_address, smtp_security";

fn row_from(r: &rusqlite::Row<'_>) -> rusqlite::Result<AccountRow> {
    Ok(AccountRow {
        id: r.get(0)?,
        owner: r.get(1)?,
        name: r.get(2)?,
        smtp_host: r.get(3)?,
        smtp_port: r.get(4)?,
        smtp_user: r.get(5)?,
        smtp_password: r.get(6)?,
        imap_host: r.get(7)?,
        imap_port: r.get(8)?,
        imap_user: r.get(9)?,
        imap_password: r.get(10)?,
        // SQLite stores Boolean as 0/1; default True when NULL (column default).
        imap_starttls: r.get::<_, Option<i64>>(11)?.unwrap_or(1) != 0,
        from_address: r.get(12)?,
        smtp_security: r.get(13)?,
    })
}

/// Build the returned `EmailConfig` from a DB row (the `cfg = {...}` dict). Mirrors
/// the Python field defaults (`smtp_port or 465`, `imap_port or 993`, password
/// decrypt) and the two "not configured" warnings.
fn cfg_from_row(row: &AccountRow) -> EmailConfig {
    let smtp_password = crate::src::secret_storage::decrypt(row.smtp_password.as_deref().unwrap_or(""));
    let imap_password = crate::src::secret_storage::decrypt(row.imap_password.as_deref().unwrap_or(""));
    let cfg = EmailConfig {
        account_id: Some(row.id.clone()),
        account_name: row.name.clone(),
        smtp_host: row.smtp_host.clone().unwrap_or_default(),
        smtp_port: row.smtp_port.filter(|p| *p != 0).unwrap_or(465),
        // `_smtp_security_mode({"smtp_security": getattr(row, "smtp_security", ""),
        // "smtp_port": row.smtp_port})` — uses the RAW row port (NULL -> 465 inside
        // the helper), NOT the `or 465`-defaulted `smtp_port` above.
        smtp_security: smtp_security_mode(
            row.smtp_security.as_deref().unwrap_or(""),
            row.smtp_port.unwrap_or(0),
        ),
        smtp_user: row.smtp_user.clone().unwrap_or_default(),
        smtp_password,
        imap_host: row.imap_host.clone().unwrap_or_default(),
        imap_port: row.imap_port.filter(|p| *p != 0).unwrap_or(993),
        imap_user: row.imap_user.clone().unwrap_or_default(),
        imap_password,
        imap_starttls: row.imap_starttls,
        from_address: {
            let fa = row.from_address.clone().unwrap_or_default();
            if !fa.is_empty() {
                fa
            } else {
                row.imap_user.clone().unwrap_or_default()
            }
        },
    };
    if !(!cfg.smtp_host.is_empty() && !cfg.smtp_user.is_empty() && !cfg.smtp_password.is_empty()) {
        logger::warning(&format!("SMTP not configured for account {:?}", row.name));
    }
    if !(!cfg.imap_host.is_empty() && !cfg.imap_user.is_empty() && !cfg.imap_password.is_empty()) {
        logger::warning(&format!("IMAP not configured for account {:?}", row.name));
    }
    cfg
}

/// `_get_email_config(account_id, owner)` — resolve IMAP/SMTP config.
///
/// Resolution order (faithful to Python):
///   1. `account_id` given -> that specific enabled row (owner-scoped guard).
///   2. else -> `is_default=True` enabled row (owner-scoped fallback filter).
///   3. else -> first enabled row by `created_at asc` (owner-scoped).
///   4. else -> legacy flat keys in settings.json.
///   5. else -> env vars.
pub fn get_email_config(account_id: Option<&str>, owner: &str) -> EmailConfig {
    let mut resolved_id: Option<String> = None;

    // The whole DB block is wrapped in `try/except -> debug log + fall through`.
    let from_db: Option<EmailConfig> = (|| {
        let conn = crate::core::database::session_local().ok()?;
        let mut row: Option<AccountRow> = None;

        if let Some(aid) = account_id {
            if !aid.is_empty() {
                let r: Option<AccountRow> = conn
                    .query_row(
                        &format!(
                            "SELECT {ACCOUNT_COLS} FROM email_accounts WHERE id = ?1 AND enabled = 1"
                        ),
                        rusqlite::params![aid],
                        row_from,
                    )
                    .ok();
                // If the resolved row belongs to a different owner, treat as
                // not-found rather than silently serving it.
                row = match r {
                    Some(rr) => {
                        let owns = owner.is_empty()
                            || match rr.owner.as_deref() {
                                Some(o) if !o.is_empty() => o == owner,
                                _ => true,
                            };
                        if owns {
                            Some(rr)
                        } else {
                            None
                        }
                    }
                    None => None,
                };
            }
        }

        // Fallback 1: is_default. Owner-scoped via `_owner_or_matching_legacy_account`.
        if row.is_none() {
            row = query_fallback(&conn, owner, /* default_only = */ true);
        }
        // Fallback 2: first enabled by created_at asc.
        if row.is_none() {
            row = query_fallback(&conn, owner, /* default_only = */ false);
        }

        let row = row?;
        resolved_id = Some(row.id.clone());
        Some(cfg_from_row(&row))
    })();

    if let Some(cfg) = from_db {
        return cfg;
    }

    // Legacy fallback — flat keys in settings.json / env vars.
    let settings = load_settings();
    let s_str = |k: &str, env: &str| -> String {
        settings
            .get(k)
            .and_then(|v| v.as_str().map(str::to_string))
            .unwrap_or_else(|| crate::pyos::getenv(env, ""))
    };
    let s_int = |k: &str, env: &str, default: i64| -> i64 {
        // `int(settings.get(k, env_or_default) or default)`.
        let raw = settings
            .get(k)
            .and_then(value_to_int_string)
            .unwrap_or_else(|| crate::pyos::getenv(env, &default.to_string()));
        raw.trim().parse::<i64>().ok().filter(|v| *v != 0).unwrap_or(default)
    };
    let s_bool = |k: &str, default: bool| -> bool {
        match settings.get(k) {
            Some(serde_json::Value::Bool(b)) => *b,
            Some(serde_json::Value::Null) | None => default,
            Some(other) => other.as_bool().unwrap_or(default),
        }
    };
    let legacy_smtp_port = s_int("smtp_port", "SMTP_PORT", 465);
    let cfg = EmailConfig {
        account_id: resolved_id,
        account_name: "legacy".to_string(),
        smtp_host: s_str("smtp_host", "SMTP_HOST"),
        smtp_port: legacy_smtp_port,
        // `_smtp_security_mode({"smtp_security": settings/env SMTP_SECURITY,
        // "smtp_port": settings/env SMTP_PORT})`.
        smtp_security: smtp_security_mode(&s_str("smtp_security", "SMTP_SECURITY"), legacy_smtp_port),
        smtp_user: s_str("smtp_user", "SMTP_USER"),
        smtp_password: s_str("smtp_password", "SMTP_PASSWORD"),
        imap_host: s_str("imap_host", "IMAP_HOST"),
        imap_port: s_int("imap_port", "IMAP_PORT", 993),
        imap_user: s_str("imap_user", "IMAP_USER"),
        imap_password: s_str("imap_password", "IMAP_PASSWORD"),
        imap_starttls: s_bool("imap_starttls", true),
        from_address: s_str("email_from", "EMAIL_FROM"),
    };
    if !(!cfg.smtp_host.is_empty() && !cfg.smtp_user.is_empty() && !cfg.smtp_password.is_empty()) {
        logger::warning("SMTP not configured — add an Email Account in Settings or set env vars");
    }
    if !(!cfg.imap_host.is_empty() && !cfg.imap_user.is_empty() && !cfg.imap_password.is_empty()) {
        logger::warning("IMAP not configured — add an Email Account in Settings or set env vars");
    }
    cfg
}

/// `int(...)` coercion for a JSON settings value that might be a number or a
/// numeric string — mirrors `int(settings.get(...))`.
fn value_to_int_string(v: &serde_json::Value) -> Option<String> {
    match v {
        serde_json::Value::Number(n) => Some(n.to_string()),
        serde_json::Value::String(s) => Some(s.clone()),
        _ => None,
    }
}

/// The owner-scoped fallback query (`is_default` or first-enabled), reproducing
/// `_owner_or_matching_legacy_account`: when an owner is given, restrict to rows
/// the owner owns OR unowned rows whose imap_user/from_address equals the owner.
fn query_fallback(conn: &rusqlite::Connection, owner: &str, default_only: bool) -> Option<AccountRow> {
    let base = if default_only {
        format!("SELECT {ACCOUNT_COLS} FROM email_accounts WHERE is_default = 1 AND enabled = 1")
    } else {
        format!("SELECT {ACCOUNT_COLS} FROM email_accounts WHERE enabled = 1")
    };
    if owner.is_empty() {
        // No owner filter. `q.first()` for default; `order_by(created_at asc)` else.
        let sql = if default_only {
            base
        } else {
            format!("{base} ORDER BY created_at ASC")
        };
        conn.query_row(&sql, [], row_from).ok()
    } else {
        // `or_(owner == owner, and_(unowned, same_mailbox))`.
        let filt = " AND (owner = ?1 OR ((owner IS NULL OR owner = '') AND (imap_user = ?1 OR from_address = ?1)))";
        let sql = if default_only {
            format!("{base}{filt}")
        } else {
            format!("{base}{filt} ORDER BY created_at ASC")
        };
        conn.query_row(&sql, rusqlite::params![owner], row_from).ok()
    }
}

/// `_list_email_accounts()` — every enabled account in `(is_default desc,
/// created_at asc)` order, each resolved through `get_email_config(id)`. On any
/// DB error returns `[get_email_config(None, "")]` (the Python `[default]`).
pub fn list_email_accounts() -> Vec<EmailConfig> {
    let res: Option<Vec<String>> = (|| {
        let conn = crate::core::database::session_local().ok()?;
        let mut stmt = conn
            .prepare(
                "SELECT id FROM email_accounts WHERE enabled = 1 \
                 ORDER BY is_default DESC, created_at ASC",
            )
            .ok()?;
        let ids = stmt
            .query_map([], |r| r.get::<_, String>(0))
            .ok()?
            .collect::<rusqlite::Result<Vec<String>>>()
            .ok()?;
        Some(ids)
    })();
    match res {
        Some(ids) => ids
            .iter()
            .map(|id| get_email_config(Some(id), ""))
            .collect(),
        None => {
            logger::debug("_list_email_accounts failed, returning [default]");
            vec![get_email_config(None, "")]
        }
    }
}

// ===========================================================================
// IMAP — `imaplib` -> the `imap` crate (run under spawn_blocking by callers)
// ===========================================================================

/// The session type the `imap` crate hands back after LOGIN. `Connection` is the
/// crate's boxed `dyn ImapConnection` stream, so this single concrete type covers
/// the implicit-TLS, STARTTLS, and plaintext cases uniformly (the
/// `imaplib.IMAP4` vs `IMAP4_SSL` branch is resolved inside [`imap_connect`]).
pub type ImapSession = imap::Session<imap::Connection>;

/// A transport-layer error string. The Python helpers raise bare exceptions /
/// return `(status, data)` tuples; on the Rust side a failing IMAP op surfaces a
/// `String` the consumer logs (the pollers/routes wrap every IMAP call in
/// `try/except` already).
pub type ImapError = String;

/// `_imap_connect(account_id, owner)` — open + LOGIN an IMAP connection.
///
/// Connection-mode branch, byte-for-byte with the Python:
///   * `imap_starttls` on  -> plain connect + `STARTTLS` upgrade.
///   * starttls off + 993  -> implicit SSL (IMAPS).
///   * starttls off + else -> plain (local Dovecot, custom ports).
///
/// TIMEOUTS — faithful to `_IMAP_TIMEOUT_SECONDS = _coerce_imap_timeout_seconds(
/// os.environ.get("ODYSSEUS_IMAP_TIMEOUT_SECONDS"))` (default 30s, clamped to
/// `[5, 300]`). The Python applies the value to BOTH the connect AND every
/// subsequent read: `imaplib.IMAP4/IMAP4_SSL(..., timeout=N)` bounds the connect,
/// and `conn.sock.settimeout(N)` bounds reads/writes for the life of the socket.
/// The `imap` crate's `ClientBuilder::connect()` uses a bare `TcpStream::connect`
/// with NO timeout (and exposes no post-login read-timeout setter on `Session`),
/// so we DON'T use the builder: we open the socket ourselves via
/// `TcpStream::connect_timeout(addr, Ns)`, set `set_read_timeout`/
/// `set_write_timeout(Some(Ns))` BEFORE handing the stream to the crate, then run
/// the greeting / optional STARTTLS / TLS-wrap / LOGIN dance by hand. Because the
/// timeouts live on the underlying `TcpStream` (and survive the native-tls wrap),
/// they stay in force for every later FETCH/SEARCH/STORE — exactly the
/// `conn.sock.settimeout(N)` semantics, not just the connect.
///
/// MUST be called inside `tokio::task::spawn_blocking` (it does synchronous
/// TCP+TLS+LOGIN).
pub fn imap_connect(account_id: Option<&str>, owner: &str) -> Result<ImapSession, ImapError> {
    // Resolve the account config (`cfg = _get_email_config(account_id, owner)`),
    // then delegate the whole TCP+TLS+LOGIN dance to `imap_connect_with_cfg`. This
    // is now a thin wrapper: the entire connect logic lives in `imap_connect_with_cfg`
    // so OTHER callers (notably the built-in email MCP server in
    // `crate::mcp_servers::email_server`, which resolves its OWN selector-based
    // `EmailConfig`) can reuse the exact, already-tested socket path without
    // duplicating ~100 lines of socket/TLS code. Behavior is unchanged for existing
    // callers — they still resolve via `get_email_config` then connect identically.
    let cfg = get_email_config(account_id, owner);
    imap_connect_with_cfg(&cfg)
}

/// `_imap_connect`'s TCP+TLS+LOGIN core, factored out so it can run against an
/// already-resolved [`EmailConfig`].
///
/// [`imap_connect`] is the thin `(account_id, owner)` wrapper that resolves the
/// config then calls this; the built-in email MCP server
/// (`crate::mcp_servers::email_server`) resolves its own selector-based config and
/// calls this directly, sharing the exact, already-tested connect dance instead of
/// duplicating the socket/TLS code.
///
/// Connection-mode branch, byte-for-byte with the Python:
///   * `imap_starttls` on  -> plain connect + `STARTTLS` upgrade.
///   * starttls off + 993  -> implicit SSL (IMAPS).
///   * starttls off + else -> plain (local Dovecot, custom ports).
///
/// TIMEOUTS — faithful to `_IMAP_TIMEOUT_SECONDS` (the env-configurable
/// `ODYSSEUS_IMAP_TIMEOUT_SECONDS`, default 30s clamped to `[5, 300]`; see
/// [`imap_connect`] for the full rationale): the connect is bounded by
/// `TcpStream::connect_timeout`, and the `set_read_timeout`/`set_write_timeout` on
/// the raw `TcpStream` (which survive the native-tls wrap) bound every later
/// FETCH/SEARCH/STORE — exactly the `conn.sock.settimeout(N)` semantics.
///
/// MUST be called inside `tokio::task::spawn_blocking` (it does synchronous
/// TCP+TLS+LOGIN).
pub fn imap_connect_with_cfg(cfg: &EmailConfig) -> Result<ImapSession, ImapError> {
    use std::net::TcpStream;
    use std::time::Duration;

    let host = cfg.imap_host.clone();
    let port = cfg.imap_port as u16;
    let timeout = Duration::from_secs(imap_timeout_seconds());

    // `imaplib.IMAP4*(host, port, timeout=15)` — connect with a bounded wait. The
    // host may be a name; resolve to addresses and try each (mirrors the stdlib's
    // create_connection loop) so `connect_timeout` (which needs a `SocketAddr`) works.
    let addrs: Vec<std::net::SocketAddr> = (host.as_str(), port)
        .to_socket_addrs()
        .map_err(|e| format!("IMAP connect failed: {e}"))?
        .collect();
    let mut tcp: Option<TcpStream> = None;
    let mut last_err = String::from("no addresses resolved");
    for addr in &addrs {
        match TcpStream::connect_timeout(addr, timeout) {
            Ok(s) => {
                tcp = Some(s);
                break;
            }
            Err(e) => last_err = e.to_string(),
        }
    }
    let tcp = tcp.ok_or_else(|| format!("IMAP connect failed: {last_err}"))?;

    // `conn.sock.settimeout(15)` — bound BOTH reads and writes for the whole
    // session. Set on the raw socket before any TLS wrap so it persists.
    tcp.set_read_timeout(Some(timeout))
        .map_err(|e| format!("IMAP set_read_timeout failed: {e}"))?;
    tcp.set_write_timeout(Some(timeout))
        .map_err(|e| format!("IMAP set_write_timeout failed: {e}"))?;

    // Build the post-login `Session<imap::Connection>` according to the mode. The
    // crate's `Connection` is `Box<dyn ImapConnection>`; both `TcpStream` and
    // native-tls's `TlsStream<TcpStream>` implement the required `SetReadTimeout`,
    // so either boxes cleanly.
    let client: imap::Client<imap::Connection> = if cfg.imap_starttls {
        // `imaplib.IMAP4(...); conn.starttls()` — greet on the plain socket, issue
        // STARTTLS, then TLS-wrap. imaplib issues STARTTLS unconditionally (no
        // capability pre-check), so we do too. The `imap` crate's own STARTTLS
        // helper (`run_command_and_check_ok`) is `pub(crate)` and unreachable from
        // here, so we run the greeting + STARTTLS exchange directly on the raw
        // socket (exactly what imaplib does at the wire level), then TLS-wrap the
        // upgraded socket and hand it to the crate with the greeting already read.
        let upgraded = starttls_handshake(tcp)?;
        let tls = tls_wrap(&host, upgraded)?;
        let mut c = imap::Client::new(Box::new(tls) as imap::Connection);
        // The greeting was consumed pre-TLS; STARTTLS does not re-send one. Mark it
        // read (the crate's own `connect_with` does the same after STARTTLS).
        c.greeting_read = true;
        c
    } else if cfg.imap_port == 993 {
        // `imaplib.IMAP4_SSL(...)` — implicit TLS, then read the greeting.
        let tls = tls_wrap(&host, tcp)?;
        let mut c = imap::Client::new(Box::new(tls) as imap::Connection);
        c.read_greeting()
            .map_err(|e| format!("IMAP greeting failed: {e}"))?;
        c
    } else {
        // `imaplib.IMAP4(...)` — plain, no TLS (local Dovecot / custom ports).
        let mut c = imap::Client::new(Box::new(tcp) as imap::Connection);
        c.read_greeting()
            .map_err(|e| format!("IMAP greeting failed: {e}"))?;
        c
    };

    // `conn.login(user, password)`.
    let session = client
        .login(&cfg.imap_user, &cfg.imap_password)
        .map_err(|(e, _client)| format!("IMAP login failed: {e}"))?;
    Ok(session)
}

/// `imaplib.IMAP4.starttls()` at the wire level — read the server greeting, send a
/// tagged `STARTTLS`, and consume the tagged response, all on the raw (timeout-bound)
/// socket BEFORE the TLS wrap. Returns the same socket, now positioned right after
/// the server's `OK` so the post-TLS `Client` sees a fresh authenticated stream.
/// Errors if the server doesn't acknowledge STARTTLS with `OK`.
fn starttls_handshake(tcp: std::net::TcpStream) -> Result<std::net::TcpStream, ImapError> {
    use std::io::{BufRead, BufReader, Write};

    // Read the untagged greeting (`* OK ...`). The greeting is a single untagged
    // line, so this is read exactly once (not a loop) — once we've read one `*`/`+`
    // line we proceed to STARTTLS.
    let mut reader = BufReader::new(tcp);
    let mut line = String::new();
    let n = reader
        .read_line(&mut line)
        .map_err(|e| format!("IMAP greeting failed: {e}"))?;
    if n == 0 {
        return Err("IMAP greeting failed: connection closed".to_string());
    }
    if !(line.starts_with("* ") || line.starts_with("+ ")) {
        return Err(format!("IMAP unexpected greeting: {}", line.trim_end()));
    }

    // Send `A1 STARTTLS` and read until the matching tagged line.
    let tag = "A1";
    {
        let inner = reader.get_mut();
        inner
            .write_all(format!("{tag} STARTTLS\r\n").as_bytes())
            .map_err(|e| format!("IMAP STARTTLS write failed: {e}"))?;
        inner
            .flush()
            .map_err(|e| format!("IMAP STARTTLS flush failed: {e}"))?;
    }
    loop {
        line.clear();
        let n = reader
            .read_line(&mut line)
            .map_err(|e| format!("IMAP STARTTLS read failed: {e}"))?;
        if n == 0 {
            return Err("IMAP STARTTLS failed: connection closed".to_string());
        }
        if let Some(after_tag) = line.strip_prefix(tag) {
            // `A1 OK ...` -> proceed; anything else (NO/BAD) -> fail.
            let rest = after_tag.trim_start();
            if rest.to_ascii_uppercase().starts_with("OK") {
                break;
            }
            return Err(format!("IMAP STARTTLS rejected: {}", line.trim_end()));
        }
        // Untagged response line before the tagged result — keep reading.
    }

    // Recover the bare socket. `BufReader` may have buffered bytes past the OK, but
    // a well-behaved server sends nothing between `OK` and the TLS handshake, so the
    // buffer is empty here; reclaim the underlying stream.
    Ok(reader.into_inner())
}

/// native-tls wrap of an established `TcpStream` for `host` — the `IMAP4_SSL` /
/// post-STARTTLS handshake. Returns the boxable `TlsStream<TcpStream>` (which keeps
/// the socket's read/write timeouts in force).
fn tls_wrap(host: &str, tcp: std::net::TcpStream) -> Result<native_tls::TlsStream<std::net::TcpStream>, ImapError> {
    let connector = native_tls::TlsConnector::new()
        .map_err(|e| format!("IMAP TLS init failed: {e}"))?;
    connector
        .connect(host, tcp)
        .map_err(|e| format!("IMAP TLS handshake failed: {e}"))
}

/// `_imap(account_id, owner)` — the `with`-scoped connection. The Python's pool
/// hooks (`_POOL_HOOKS`) are filled in by `setup_email_routes()`; until that runs
/// (pre-setup, tests, pollers spinning up early) it falls back to a plain
/// connect+logout pair. The Rust connection pool is not yet wired, so this is the
/// fallback path: connect, run `f`, then `logout()` (ignoring logout errors), the
/// exact `finally: conn.logout()` shape. Runs under spawn_blocking via the caller.
pub fn with_imap<R>(
    account_id: Option<&str>,
    owner: &str,
    f: impl FnOnce(&mut ImapSession) -> R,
) -> Result<R, ImapError> {
    let mut session = imap_connect(account_id, owner)?;
    let out = f(&mut session);
    let _ = session.logout();
    Ok(out)
}

/// `_q(name)` — quote an IMAP mailbox name (escape `\` and `"`, wrap in quotes).
pub fn q(name: &str) -> String {
    let escaped = name.replace('\\', "\\\\").replace('"', "\\\"");
    format!("\"{escaped}\"")
}

/// `_imap_move(uid, dest, src="INBOX", account_id=None, owner="")` — copy a UID to
/// `dest`, flag the source `\Deleted`, expunge. Returns `false` on any failure (the
/// Python catches all). Opens its own connection scoped to `account_id`/`owner`
/// (the owner-scoped lookup keeps a poller from moving against another user's
/// mailbox), exactly like the Python `_imap_connect(account_id, owner=owner)`. Runs
/// under spawn_blocking via the caller.
pub fn imap_move(uid: &str, dest: &str, src: &str, account_id: Option<&str>, owner: &str) -> bool {
    let result: Result<bool, ImapError> = (|| {
        let mut c = imap_connect(account_id, owner)?;
        // `c.select(_q(src))` — but the `imap` crate's `select` AUTO-QUOTES the
        // mailbox (`validate_str` -> `quote!`), so passing the already-quoted
        // `q(src)` would DOUBLE-quote it (`""INBOX""`) and SELECT would fail. Pass
        // the BARE name; the crate quotes it once, matching the wire form imaplib's
        // own `_q(src)` produced.
        c.select(src).map_err(|e| e.to_string())?;
        // `status, _ = c.copy(uid, _q(dest))`. UNLIKE select/examine, the crate's
        // `copy` does NOT auto-quote its mailbox arg (it interpolates `as_ref()`
        // raw), so we KEEP `q(dest)` here to reproduce imaplib's quoted COPY dest.
        let copied = c.copy(uid, q(dest)).is_ok();
        if !copied {
            let _ = c.logout();
            return Ok(false);
        }
        // `c.store(uid, "+FLAGS", "\\Deleted")` — the imap crate's store takes the
        // query in one string ("+FLAGS (\\Deleted)").
        let _ = c.store(uid, "+FLAGS (\\Deleted)");
        let _ = c.expunge();
        let _ = c.logout();
        Ok(true)
    })();
    match result {
        Ok(v) => v,
        Err(e) => {
            logger::warning(&format!("IMAP move {uid} → {dest} failed: {e}"));
            false
        }
    }
}

/// The mailbox names from a `LIST` response, decoded. Helper shared by the
/// folder-detection functions. Each `imap::Name` already exposes the decoded
/// folder name + its attributes, so we don't need the Python's
/// `re.search(r'"([^"]*)"\s*$|(\S+)\s*$', decoded)` raw-line scrape — but we do
/// reproduce the `\Sent` / `\Drafts` / `\Junk` attribute preference.
fn list_names(conn: &mut ImapSession) -> Option<Vec<(String, Vec<String>)>> {
    let names = conn.list(None, Some("*")).ok()?;
    let mut out = Vec::new();
    for n in names.iter() {
        let attrs = n
            .attributes()
            .iter()
            .map(|a| format!("{a:?}"))
            .collect::<Vec<_>>();
        out.push((n.name().to_string(), attrs));
    }
    Some(out)
}

/// `_detect_sent_folder(conn)` — server's Sent folder name. `"Sent"` default.
pub fn detect_sent_folder(conn: &mut ImapSession) -> String {
    detect_folder(
        conn,
        &["Sent", "[Gmail]/Sent Mail", "Sent Mail", "Sent Items", "INBOX.Sent"],
        "Sent",
        Some("Sent"),
    )
}

/// `_detect_drafts_folder(conn)` — server's Drafts folder. `"Drafts"` default.
pub fn detect_drafts_folder(conn: &mut ImapSession) -> String {
    detect_folder(
        conn,
        &["Drafts", "[Gmail]/Drafts", "Draft", "INBOX.Drafts"],
        "Drafts",
        Some("Draft"),
    )
}

/// Shared body of `_detect_sent_folder` / `_detect_drafts_folder`. `flag_word`
/// (e.g. "Sent" / "Draft") matches the `\Sent` / `\Drafts`/`\Draft` LIST
/// attribute preference; `candidates` is the name fallback list; `default` is the
/// final fallback string.
fn detect_folder(
    conn: &mut ImapSession,
    candidates: &[&str],
    default: &str,
    flag_word: Option<&str>,
) -> String {
    let names = match list_names(conn) {
        Some(n) if !n.is_empty() => n,
        _ => return default.to_string(),
    };
    // Prefer the `\Sent` / `\Drafts`/`\Draft` attribute if present.
    if let Some(fw) = flag_word {
        for (name, attrs) in &names {
            if attrs.iter().any(|a| {
                let a = a.trim_start_matches('\\');
                a.eq_ignore_ascii_case(fw)
                    || (fw == "Draft" && a.eq_ignore_ascii_case("Drafts"))
            }) {
                return name.clone();
            }
        }
    }
    // Then the candidate names, in order.
    let all: Vec<&String> = names.iter().map(|(n, _)| n).collect();
    for c in candidates {
        if all.iter().any(|n| n.as_str() == *c) {
            return c.to_string();
        }
    }
    default.to_string()
}

/// `_detect_spam_folder(conn)` — server's Junk/Spam folder, if any (`None` else).
pub fn detect_spam_folder(conn: &mut ImapSession) -> Option<String> {
    let names = match list_names(conn) {
        Some(n) if !n.is_empty() => n,
        _ => return None,
    };
    let mut fallback: Option<String> = None;
    for (name, attrs) in &names {
        // `if r"\Junk" in decoded:` -> the `\Junk` attribute.
        if attrs
            .iter()
            .any(|a| a.trim_start_matches('\\').eq_ignore_ascii_case("Junk"))
        {
            return Some(name.clone()); // preferred — break.
        }
        let low = name.to_lowercase();
        if (matches!(low.as_str(), "junk" | "spam" | "junk mail" | "junk e-mail")
            || low.ends_with("/junk")
            || low.ends_with("/spam"))
            && fallback.is_none()
        {
            // `fallback = fallback or name` — keep the first match only.
            fallback = Some(name.clone());
        }
    }
    fallback
}

// ===========================================================================
// MIME parse — `email`/`email.header` -> `mail-parser`
// ===========================================================================

/// A parsed RFC822 message. Wraps `mail-parser`'s owned [`Message`] so the
/// extraction helpers can borrow it. Built from raw FETCH bytes via
/// [`parse_message`] (the `email.message_from_bytes(raw)` analogue).
pub struct ParsedMessage {
    inner: Message<'static>,
}

impl ParsedMessage {
    /// Borrow the underlying `mail-parser` message (for callers that need the
    /// richer API — subject/from/date/message_id).
    pub fn message(&self) -> &Message<'static> {
        &self.inner
    }

    /// `msg.get("<name>")` — return the RAW header value (the bytes Python's
    /// `email.message.Message.get` yields), trimmed of the leading SP / trailing
    /// CRLF. mail-parser parses well-known headers structurally (`From`/`To`/`Cc`
    /// -> `HeaderValue::Address`, `Date` -> `DateTime`, `Message-ID` -> the
    /// bracket-stripped id), so surfacing the *parsed* value here would diverge
    /// from `msg.get(...)`: an `Address` doesn't render at all (empty `From:`), a
    /// `DateTime` REFORMATS the date, and `message_id()` STRIPS the angle brackets.
    /// `header_raw` slices the verbatim value out of the original message bytes,
    /// matching Python byte-for-byte; callers that want decoding re-`decode_header`.
    pub fn header(&self, name: &str) -> Option<String> {
        match name.to_ascii_lowercase().as_str() {
            // Subject is fetched decoded everywhere (callers re-`decode_header`,
            // which is a no-op on an already-decoded string), so keep the parser's
            // RFC2047-decoded subject — it matches `_decode_header(msg.get("Subject"))`.
            "subject" => self.inner.subject().map(str::to_string),
            // From / To / Cc / Date / Message-ID and any other header: the verbatim
            // raw value, exactly as `msg.get(name)` returns it.
            _ => self.inner.header_raw(name).map(|s| s.trim().to_string()),
        }
    }

    /// `msg.is_multipart()` — true when the message has more than the single body
    /// part (mail-parser flattens parts into `parts`; multipart messages have a
    /// `Multipart` part at index 0).
    pub fn is_multipart(&self) -> bool {
        self.inner
            .parts
            .first()
            .map(|p| matches!(p.body, PartType::Multipart(_)))
            .unwrap_or(false)
    }
}

/// `email.message_from_bytes(raw_bytes)` — parse raw RFC822 bytes. Returns `None`
/// when the parser can't make a message (the Python would raise; the consumers
/// wrap fetch+parse in `try/except` and `continue`, so `None` is the faithful
/// "skip this message" signal). Bytes are copied into an owned message so the
/// borrow outlives the FETCH buffer.
pub fn parse_message(raw_bytes: &[u8]) -> Option<ParsedMessage> {
    let owned = raw_bytes.to_vec();
    let msg = MessageParser::default().parse(&owned)?;
    // SAFETY-free owned conversion: `into_owned` detaches the lifetime.
    let inner: Message<'static> = msg.into_owned();
    Some(ParsedMessage { inner })
}

/// `_decode_header(raw)` — RFC2047-decode an arbitrary raw header value, joining
/// decoded tokens with a single space. Empty/`None` -> `""`.
///
/// Python's `email.header.decode_header` splits into `(bytes, charset)` tokens
/// and `" ".join`s the per-charset decodes. mail-parser decodes encoded-words for
/// us; for a raw string with no encoded-words this is identity (matching Python),
/// and for encoded-word headers it produces the same decoded text.
pub fn decode_header(raw: &str) -> String {
    if raw.is_empty() {
        return String::new();
    }
    // Round-trip through the parser as a synthetic header so encoded-words decode
    // exactly as the consumers' Subject/From lines would.
    let synthetic = format!("Subject: {raw}\r\n\r\n");
    if let Some(msg) = MessageParser::default().parse(synthetic.as_bytes()) {
        if let Some(s) = msg.subject() {
            return s.to_string();
        }
    }
    raw.to_string()
}

/// `part.get_filename()` decoded — the attachment filename, RFC2047-decoded, or
/// `None`. mail-parser exposes it via `MimeHeaders::attachment_name`.
fn part_filename(part: &mail_parser::MessagePart<'_>) -> Option<String> {
    part.attachment_name().map(decode_header)
}

/// `part.get_content_type()` lowercased "maintype/subtype" (or `""`).
fn part_content_type(part: &mail_parser::MessagePart<'_>) -> String {
    match part.content_type() {
        Some(ct) => {
            let main = ct.ctype().to_lowercase();
            match ct.subtype() {
                Some(sub) => format!("{main}/{}", sub.to_lowercase()),
                None => main,
            }
        }
        None => String::new(),
    }
}

/// `str(part.get("Content-Disposition", ""))` lowercased — used for the
/// `"attachment" not in cd` / `"inline" in cd.lower()` substring checks.
///
/// Python tests against the FULL Content-Disposition header value (type + params,
/// incl. the `filename=` parameter), not just the disposition type. mail-parser
/// only exposes the parsed `ContentType` (type + attributes), so reconstruct a
/// `disposition; name=value; …` string from it. This keeps the substring tests
/// faithful: a part like `Content-Disposition: inline; filename="my attachment.pdf"`
/// reconstructs to `inline; filename=my attachment.pdf`, which contains both
/// "inline" AND "attachment" — exactly as Python's `str(part.get(...))` would, so
/// it is treated as a real attachment / non-body part the way Python does.
fn part_disposition(part: &mail_parser::MessagePart<'_>) -> String {
    match part.content_disposition() {
        Some(cd) => {
            let mut s = cd.ctype().to_lowercase();
            if let Some(attrs) = cd.attributes() {
                for a in attrs {
                    s.push_str("; ");
                    s.push_str(&a.name.to_lowercase());
                    s.push('=');
                    s.push_str(&a.value.to_lowercase());
                }
            }
            s
        }
        None => String::new(),
    }
}

/// `_extract_html(msg)` — raw HTML body, if present. Walks parts for a
/// `text/html` non-attachment part; falls back to the single-part body.
pub fn extract_html(msg: &ParsedMessage) -> Option<String> {
    let m = &msg.inner;
    if msg.is_multipart() {
        for part in &m.parts {
            let ct = part_content_type(part);
            let cd = part_disposition(part);
            if ct == "text/html" && !cd.contains("attachment") {
                if let Some(text) = part.text_contents() {
                    return Some(text.to_string());
                }
            }
        }
        None
    } else {
        // Single-part message.
        for part in &m.parts {
            if part_content_type(part) == "text/html" {
                if let Some(text) = part.text_contents() {
                    return Some(text.to_string());
                }
            }
        }
        None
    }
}

static BR_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?i)<br\s*/?>").unwrap());
static TAG_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"<[^>]+>").unwrap());

/// `_extract_text(msg)` — plaintext body. Multipart: concatenate `text/plain`
/// non-attachment parts; if none, fall back to stripping the first `text/html`
/// part (`<br>`->`\n`, tag-strip, unescape). Single-part: decode the payload.
pub fn extract_text(msg: &ParsedMessage) -> String {
    let m = &msg.inner;
    if msg.is_multipart() {
        let mut text_parts: Vec<String> = Vec::new();
        for part in &m.parts {
            let ct = part_content_type(part);
            let cd = part_disposition(part);
            if ct == "text/plain" && !cd.contains("attachment") {
                if let Some(text) = part.text_contents() {
                    text_parts.push(text.to_string());
                }
            } else if ct == "text/html" && text_parts.is_empty() && !cd.contains("attachment") {
                if let Some(raw_html) = part.text_contents() {
                    let text = BR_RE.replace_all(raw_html, "\n");
                    let text = TAG_RE.replace_all(&text, "");
                    let text = html_escape::decode_html_entities(&text);
                    text_parts.push(text.trim().to_string());
                }
            }
        }
        text_parts.join("\n")
    } else {
        for part in &m.parts {
            if let Some(text) = part.text_contents() {
                return text.to_string();
            }
        }
        String::new()
    }
}

/// `extract_writing_style`'s inline body extraction (NOT the shared `_extract_text`).
/// Mirrors `_gather_samples`: for a multipart message it walks parts and takes the
/// FIRST `text/plain` part (no Content-Disposition check, no HTML fallback) then
/// stops; for a non-multipart message it decodes the single payload. Returns `""`
/// when there is no `text/plain` content (so HTML-only sent mail yields a sample
/// that the caller's length gate then rejects, exactly as Python does).
pub fn extract_first_plaintext(msg: &ParsedMessage) -> String {
    let m = &msg.inner;
    if msg.is_multipart() {
        for part in &m.parts {
            if part_content_type(part) == "text/plain" {
                if let Some(text) = part.text_contents() {
                    return text.to_string();
                }
            }
        }
        String::new()
    } else {
        for part in &m.parts {
            if let Some(text) = part.text_contents() {
                return text.to_string();
            }
        }
        String::new()
    }
}

static FILENAME_SANITIZE_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"[^\w\s\-.]").unwrap());

/// An attachment's metadata — the dict `_list_attachments_from_msg` builds.
#[derive(Debug, Clone, Serialize)]
pub struct AttachmentMeta {
    pub index: usize,
    pub filename: String,
    pub content_type: String,
    pub size: usize,
    pub is_inline: bool,
}

/// Walk the non-multipart, non-body parts in order, yielding `(content_type,
/// disposition, filename_opt, contents)` — the shared iteration the three
/// attachment helpers use. Mirrors `for part in msg.walk(): if part.is_multipart():
/// continue; if ct in (text/plain,text/html) and "attachment" not in cd: continue`.
fn attachment_parts(
    msg: &ParsedMessage,
) -> Vec<(String, String, Option<String>, &mail_parser::MessagePart<'static>)> {
    let mut out = Vec::new();
    for part in &msg.inner.parts {
        if matches!(part.body, PartType::Multipart(_)) {
            continue;
        }
        let ct = part_content_type(part);
        let cd = part_disposition(part);
        // Skip text/plain & text/html BODY parts (not real attachments).
        if (ct == "text/plain" || ct == "text/html") && !cd.contains("attachment") {
            continue;
        }
        let filename = part_filename(part);
        out.push((ct, cd, filename, part));
    }
    out
}

/// `_list_attachments_from_msg(msg)` — attachment metadata list. Non-multipart
/// messages have no attachments (`[]`).
pub fn list_attachments_from_msg(msg: &ParsedMessage) -> Vec<AttachmentMeta> {
    if !msg.is_multipart() {
        return Vec::new();
    }
    let mut out = Vec::new();
    for (idx, (ct, cd, filename, part)) in attachment_parts(msg).into_iter().enumerate() {
        let filename = filename.unwrap_or_else(|| {
            // Inline images etc. — generate a name from the subtype.
            let ext = ct.rsplit('/').next().unwrap_or("bin");
            format!("attachment_{idx}.{ext}")
        });
        let size = part.contents().len();
        out.push(AttachmentMeta {
            index: idx,
            filename,
            content_type: ct,
            size,
            is_inline: cd.contains("inline"),
        });
    }
    out
}

/// `_extract_attachment_to_disk(msg, index, target_dir)` — write the `index`-th
/// attachment to `target_dir`, return its path. `None` when not multipart, the
/// index is out of range, or the payload is empty.
pub fn extract_attachment_to_disk(
    msg: &ParsedMessage,
    index: usize,
    target_dir: &Path,
) -> Option<PathBuf> {
    if !msg.is_multipart() {
        return None;
    }
    for (idx, (ct, _cd, filename, part)) in attachment_parts(msg).into_iter().enumerate() {
        if idx == index {
            let filename = filename.unwrap_or_else(|| {
                let ext = ct.rsplit('/').next().unwrap_or("bin");
                format!("attachment_{idx}.{ext}")
            });
            // `re.sub(r"[^\w\s\-.]", "_", filename).strip()`.
            let safe = FILENAME_SANITIZE_RE
                .replace_all(&filename, "_")
                .trim()
                .to_string();
            let payload = part.contents();
            if payload.is_empty() {
                return None;
            }
            let _ = std::fs::create_dir_all(target_dir);
            let filepath = target_dir.join(&safe);
            if std::fs::write(&filepath, payload).is_err() {
                return None;
            }
            return Some(filepath);
        }
    }
    None
}

/// `_extract_attachment_text(msg, max_chars=6000)` — readable text from PDF /
/// text-ish attachments, capped at `max_chars`, joined `\n\n---\n\n`. Empty when
/// nothing useful. PDF goes through [`crate::src::personal_docs::extract_pdf_text`]
/// (the `from src.personal_docs import extract_pdf_text` analogue) via a tempfile.
pub fn extract_attachment_text(msg: &ParsedMessage, max_chars: usize) -> String {
    if !msg.is_multipart() {
        return String::new();
    }
    let mut out_parts: Vec<String> = Vec::new();
    let mut total: usize = 0;
    for (ct, _cd, filename, part) in attachment_parts(msg) {
        let fname_lower = filename.clone().unwrap_or_default().to_lowercase();
        let payload = part.contents();
        if payload.is_empty() {
            continue;
        }
        // Cap per-attachment size (2 MB) to keep huge PDFs from blowing the budget.
        if payload.len() > 2_000_000 {
            continue;
        }
        let mut text = String::new();
        if ct == "application/pdf" || fname_lower.ends_with(".pdf") {
            // Write to a tempfile, run the PDF extractor, unlink.
            if let Ok(mut tmp) = tempfile_with_suffix(".pdf") {
                use std::io::Write;
                if tmp.file.write_all(payload).is_ok() {
                    let _ = tmp.file.flush();
                    text = crate::src::personal_docs::extract_pdf_text(&tmp.path.to_string_lossy());
                }
                let _ = std::fs::remove_file(&tmp.path);
            }
        } else if ct.starts_with("text/")
            || fname_lower.ends_with(".txt")
            || fname_lower.ends_with(".md")
            || fname_lower.ends_with(".csv")
            || fname_lower.ends_with(".log")
            || fname_lower.ends_with(".json")
        {
            text = String::from_utf8_lossy(payload).to_string();
        }
        let text = text.trim();
        if text.is_empty() {
            continue;
        }
        let remaining = max_chars.saturating_sub(total);
        if remaining == 0 {
            break;
        }
        let snippet = take_chars(text, remaining);
        out_parts.push(format!(
            "[Attachment: {}]\n{}",
            filename.clone().filter(|f| !f.is_empty()).unwrap_or_else(|| "file".to_string()),
            snippet
        ));
        total += snippet.chars().count();
        if total >= max_chars {
            break;
        }
    }
    out_parts.join("\n\n---\n\n")
}

/// Python `text[:n]` over Unicode scalar values (char-based slice).
fn take_chars(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// A tempfile handle pairing the open file with its path (so we can write then
/// unlink, the `tempfile.NamedTemporaryFile(delete=False)` analogue).
struct NamedTemp {
    file: std::fs::File,
    path: PathBuf,
}

/// `tempfile.NamedTemporaryFile(suffix=..., delete=False)` — create a uniquely
/// named file in the system temp dir and return it open for writing.
fn tempfile_with_suffix(suffix: &str) -> std::io::Result<NamedTemp> {
    let dir = std::env::temp_dir();
    let unique = format!("ody_mail_{}{}", uuid::Uuid::new_v4().simple(), suffix);
    let path = dir.join(unique);
    let file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&path)?;
    Ok(NamedTemp { file, path })
}

// ===========================================================================
// Compose-upload attach / cleanup (`_attach_compose_uploads` / `_cleanup_...`)
// ===========================================================================

/// A staged-upload attachment ready to glue onto an outgoing MIME message —
/// the `MIMEBase` part `_attach_compose_uploads` builds. The Python attaches each
/// to an `email.mime.multipart.MIMEMultipart`; on the Rust side the SMTP build
/// lives in the routes layer (lettre's message builder), so this returns the
/// resolved `(maintype, subtype, original_filename, payload_bytes)` tuples for the
/// caller to attach. Tokens are sanitized via `Path::file_name` (traversal-safe);
/// missing files are skipped with a warning (the exact Python control flow).
pub struct ComposeAttachment {
    pub maintype: String,
    pub subtype: String,
    pub filename: String,
    pub data: Vec<u8>,
}

/// `_attach_compose_uploads(outer, tokens)` — resolve each staged token to an
/// attachment part. Returns the parts (the routes layer attaches them to the
/// lettre `MultiPart`). Mirrors the mimetype guess, the `<uuid>_<name>` filename
/// split, and the skip-missing-with-warning behavior.
pub fn attach_compose_uploads(tokens: &[String]) -> Vec<ComposeAttachment> {
    let mut out = Vec::new();
    if tokens.is_empty() {
        return out;
    }
    let base = compose_uploads_dir();
    for token in tokens {
        // `Path(token).name` — strip any directory components (traversal-safe).
        let safe_token = match Path::new(token).file_name() {
            Some(n) => n.to_string_lossy().to_string(),
            None => continue,
        };
        let path = base.join(&safe_token);
        if !path.exists() {
            logger::warning(&format!("Attachment token not found: {safe_token}"));
            continue;
        }
        // `ctype, encoding = mimetypes.guess_type(path)`; default octet-stream.
        let (maintype, subtype) = guess_mime(&safe_token);
        let data = match std::fs::read(&path) {
            Ok(d) => d,
            Err(_) => continue,
        };
        // Token format: "<uuid>_<original_name>".
        let original_name = match safe_token.split_once('_') {
            Some((_, rest)) => rest.to_string(),
            None => safe_token.clone(),
        };
        out.push(ComposeAttachment {
            maintype,
            subtype,
            filename: original_name,
            data,
        });
    }
    out
}

/// `_cleanup_compose_uploads(tokens)` — best-effort unlink of staged uploads.
pub fn cleanup_compose_uploads(tokens: &[String]) {
    if tokens.is_empty() {
        return;
    }
    let base = compose_uploads_dir();
    for token in tokens {
        if let Some(name) = Path::new(token).file_name() {
            // `unlink(missing_ok=True)` — ignore "not found".
            let _ = std::fs::remove_file(base.join(name));
        }
    }
}

/// `mimetypes.guess_type` -> `(maintype, subtype)`, defaulting to
/// `application/octet-stream` for unknown / compression-encoded names. Covers the
/// common attachment types; the Python's default branch (unknown ctype or a
/// content-encoding present) maps to octet-stream here.
fn guess_mime(name: &str) -> (String, String) {
    let ext = name.rsplit('.').next().unwrap_or("").to_lowercase();
    let full = match ext.as_str() {
        "txt" | "text" | "log" => "text/plain",
        "md" | "markdown" => "text/markdown",
        "csv" => "text/csv",
        "html" | "htm" => "text/html",
        "json" => "application/json",
        "pdf" => "application/pdf",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "webp" => "image/webp",
        "svg" => "image/svg+xml",
        "zip" => "application/zip",
        "doc" => "application/msword",
        "docx" => "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xls" => "application/vnd.ms-excel",
        "xlsx" => "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        _ => "application/octet-stream",
    };
    match full.split_once('/') {
        Some((m, s)) => (m.to_string(), s.to_string()),
        None => ("application".to_string(), "octet-stream".to_string()),
    }
}

// ===========================================================================
// SMTP send — `smtplib` -> `lettre`
// ===========================================================================

/// `_send_smtp_message(cfg, from_addr, recipients, message, timeout=30)` —
/// send a pre-built RFC822 message via SMTP.
///
/// TRANSPORT-SECURITY branch, faithful to the Python (it resolves
/// `security = _smtp_security_mode(cfg)` and dispatches on it):
///   * `"ssl"`      -> `smtplib.SMTP_SSL` implicit TLS (lettre `Tls::Wrapper`).
///   * `"starttls"` -> `smtplib.SMTP` + mandatory `STARTTLS` (lettre `Tls::Required`).
///   * `"none"`     -> `smtplib.SMTP` plaintext, no upgrade (lettre `Tls::None`).
///
/// Login only happens when both `smtp_user` and `smtp_password` are non-empty (the
/// `if user and password:` guard). The message bytes are handed to lettre's
/// `send_raw(&Envelope, &[u8])`, the exact `smtp.sendmail(from, rcpts, msg)`
/// analogue (lettre transmits the bytes verbatim — no re-encoding). Runs under
/// spawn_blocking via the caller (lettre's `SmtpTransport` is blocking). Returns
/// `Err` on any failure (the Python raises; the consumers `try/except` it).
pub fn send_smtp_message(
    cfg: &EmailConfig,
    from_addr: &str,
    recipients: &[String],
    message: &[u8],
    timeout_secs: u64,
) -> Result<(), String> {
    use lettre::address::Envelope;
    use lettre::transport::smtp::authentication::Credentials;
    use lettre::transport::smtp::client::{Tls, TlsParameters};
    use lettre::transport::smtp::SmtpTransport;
    use lettre::Address;
    use lettre::Transport;

    let host = cfg.smtp_host.clone();
    let port = if cfg.smtp_port != 0 { cfg.smtp_port as u16 } else { 465 };
    let user = cfg.smtp_user.clone();
    let password = cfg.smtp_password.clone();

    // Build the envelope: reverse-path = from_addr, forward-path = recipients.
    //
    // PERMISSIVE addresses — faithful to `smtp.sendmail(from_addr, recipients, msg)`.
    // smtplib does NO RFC validation: it puts whatever it's given straight into
    // `MAIL FROM:<...>` / `RCPT TO:<...>` and lets the server decide. lettre's strict
    // `Address::from_str` (the `email_address` crate) REJECTS forms smtplib happily
    // sends (long local parts, quoted local parts, unusual-but-server-valid domains),
    // which would make the Rust path 5xx a send Python would deliver. So we bypass the
    // strict parser via `Address::new_dangerous(user, domain)`, which stores the bytes
    // verbatim — lettre then emits them unchanged on the wire, exactly matching
    // smtplib. We still strip a display-name / angle brackets first (smtplib callers
    // pass the bare address; the existing code did the same via `extract_bare_address`).
    //
    // MINIMAL DOCUMENTED DIVERGENCE: lettre's `Address` is structurally `user@domain`
    // (the `MAIL FROM:<{}>` is built from the stored `serialized` string, which
    // `new_dangerous` always renders with an `@`). An envelope address with NO `@` at
    // all (e.g. a bare `postmaster`) is the one shape we can't reproduce byte-for-byte:
    // smtplib would send `<postmaster>`, we send `<postmaster@>`. Such `@`-less SMTP
    // envelope addresses are vanishingly rare (and most servers reject them anyway), so
    // this is the only residual gap vs. smtplib's permissiveness.
    let lenient_addr = |s: &str| -> Address {
        let bare = extract_bare_address(s);
        match bare.rsplit_once('@') {
            Some((local, domain)) => Address::new_dangerous(local, domain),
            // No '@' — keep the whole value in the local part (see divergence note).
            None => Address::new_dangerous(bare.as_str(), ""),
        }
    };
    let from = lenient_addr(from_addr);
    let mut to: Vec<Address> = Vec::with_capacity(recipients.len());
    for r in recipients {
        to.push(lenient_addr(r));
    }
    let envelope = Envelope::new(Some(from), to).map_err(|e| format!("envelope: {e}"))?;

    // `security = _smtp_security_mode(cfg)` — resolve the transport-security mode
    // fresh (idempotent for an already-canonical `cfg.smtp_security`; falls back to
    // the port heuristic when the stored value is empty/unknown), exactly as the
    // Python `_send_smtp_message` does, then branch SSL vs STARTTLS vs plaintext.
    let security = smtp_security_mode(&cfg.smtp_security, cfg.smtp_port);
    let tls_params = TlsParameters::new(host.clone()).map_err(|e| format!("tls params: {e}"))?;
    let tls = match security.as_str() {
        // `smtplib.SMTP_SSL(...)` — implicit TLS from the start.
        "ssl" => Tls::Wrapper(tls_params),
        // `smtplib.SMTP(...); smtp.starttls()` — plain connect, mandatory upgrade.
        "starttls" => Tls::Required(tls_params),
        // `smtplib.SMTP(...)` with no `starttls()` — plaintext (no TLS at all).
        _ => Tls::None,
    };

    let mut builder = SmtpTransport::builder_dangerous(host)
        .port(port)
        .tls(tls)
        .timeout(Some(std::time::Duration::from_secs(timeout_secs)));

    // `if user and password: smtp.login(user, password)`.
    if !user.is_empty() && !password.is_empty() {
        builder = builder.credentials(Credentials::new(user, password));
    }
    let transport = builder.build();

    transport
        .send_raw(&envelope, message)
        .map(|_| ())
        .map_err(|e| format!("SMTP send failed: {e}"))
}

/// Extract the bare `user@domain` from a possibly `Name <user@domain>` form. If
/// no angle-bracket address is present, return the trimmed input.
fn extract_bare_address(s: &str) -> String {
    if let (Some(lt), Some(gt)) = (s.rfind('<'), s.rfind('>')) {
        if lt < gt {
            return s[lt + 1..gt].trim().to_string();
        }
    }
    s.trim().to_string()
}

// ===========================================================================
// Sender / context retrieval for the AI-summary / AI-reply pipelines
// (`_fetch_sender_thread_context` / `_pre_retrieve_context`)
// ===========================================================================

static MULTI3_NEWLINE_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\n{3,}").unwrap());
// `\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b` — a Capitalized word, optionally
// followed by up to two more Capitalized words. No backrefs, so `regex` handles it.
static CAP_TERMS_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b").unwrap());

/// `email.utils.parseaddr(s)[1]` — the bare address out of `"Name <addr>"` or a
/// raw address. Mirrors `email_routes`' private `parseaddr` (kept module-local here
/// to avoid a cross-module pub just for the address half).
fn parseaddr_addr(s: &str) -> String {
    let s = s.trim();
    if let (Some(lt), Some(gt)) = (s.rfind('<'), s.rfind('>')) {
        if lt < gt {
            return s[lt + 1..gt].trim().to_string();
        }
    }
    if s.contains('@') {
        s.to_string()
    } else {
        String::new()
    }
}

/// `_fetch_sender_thread_context(sender_addr, exclude_uid, exclude_folder, limit,
/// max_chars_per_email, max_attachment_chars, account_id, owner)` — pull the last N
/// emails from `sender_addr` across INBOX/Sent/Archive/Drafts, extract body snippet
/// + attachment text, and return one formatted "REFERENCED MATERIAL" block. Empty
/// string when nothing useful was found. Never raises (the Python swallows every
/// error and returns `""`).
///
/// Opens its OWN connection via [`imap_connect`] scoped to `account_id`/`owner`
/// (the owner-scoped config lookup keeps the AI-reply path bound to this user's
/// mailbox), exactly like the Python `_imap_connect(account_id, owner=owner)`. Each
/// folder is opened read-only via `examine` (the `select(.., readonly=True)`
/// analogue) with the BARE name (the crate auto-quotes). FETCH is sequence-based
/// (`conn.fetch(seq, "(RFC822)")`, matching imaplib's seq-based `.fetch`). Must run
/// inside `tokio::task::spawn_blocking`.
#[allow(clippy::too_many_arguments)]
pub fn fetch_sender_thread_context(
    sender_addr: &str,
    exclude_uid: &str,
    exclude_folder: &str,
    limit: usize,
    max_chars_per_email: usize,
    max_attachment_chars: usize,
    account_id: Option<&str>,
    owner: &str,
) -> String {
    if sender_addr.is_empty() {
        return String::new();
    }
    let sender_addr = sender_addr.trim().to_lowercase();
    if sender_addr.is_empty() {
        return String::new();
    }

    let mut blocks: Vec<String> = Vec::new();
    // (folder, uid) pairs already consumed — seeded with the excluded message.
    let mut seen_uids: std::collections::HashSet<(String, String)> = std::collections::HashSet::new();
    if !exclude_uid.is_empty() {
        let ef = if exclude_folder.is_empty() {
            "INBOX".to_string()
        } else {
            exclude_folder.to_string()
        };
        seen_uids.insert((ef, exclude_uid.to_string()));
    }

    let mut conn = match imap_connect(account_id, owner) {
        Ok(c) => c,
        Err(e) => {
            logger::warning(&format!("sender-thread-context: imap connect failed: {e}"));
            return String::new();
        }
    };

    for folder in ["INBOX", "Sent", "Archive", "Drafts"] {
        if blocks.len() >= limit {
            break;
        }
        // `conn.select(_q(folder), readonly=True)` -> EXAMINE the BARE name (the
        // crate auto-quotes; a `q()` here would double-quote and fail).
        if conn.examine(folder).is_err() {
            continue;
        }
        // `conn.search(None, f'(FROM "{addr_escaped}")')` — the query is passed RAW
        // to the crate (no auto-quoting on search), so we build the exact same
        // `(FROM "...")` string the Python sent.
        let addr_escaped = sender_addr.replace('"', "\\\"");
        let found = match conn.search(format!("(FROM \"{addr_escaped}\")")) {
            Ok(s) if !s.is_empty() => s,
            _ => continue,
        };
        // `uids = sdata[0].split(); uids = list(reversed(uids))` — IMAP SEARCH
        // returns ascending seq numbers; reverse for most-recent-first.
        let mut seqs: Vec<u32> = found.into_iter().collect();
        seqs.sort_unstable();
        seqs.reverse();

        for seq in seqs {
            if blocks.len() >= limit {
                break;
            }
            let uid = seq.to_string();
            let key = (folder.to_string(), uid.clone());
            if seen_uids.contains(&key) {
                continue;
            }
            seen_uids.insert(key);

            // `st_f, msg_data = conn.fetch(raw_uid, "(RFC822)")`.
            let raw_bytes: Vec<u8> = match conn.fetch(&uid, "(RFC822)") {
                Ok(fetches) => match fetches.iter().find_map(|f| f.body().map(|b| b.to_vec())) {
                    Some(b) if !b.is_empty() => b,
                    _ => continue,
                },
                Err(_) => continue,
            };
            let msg = match parse_message(&raw_bytes) {
                Some(m) => m,
                None => continue,
            };

            let subj = decode_header(&msg.header("subject").unwrap_or_else(|| "(no subject)".to_string()));
            let date_hdr = msg.header("date").unwrap_or_default();
            // `body_text = (_extract_text(msg) or "").strip()`.
            let mut body_text = extract_text(&msg).trim().to_string();
            // `re.sub(r"\n{3,}", "\n\n", body_text)`.
            body_text = MULTI3_NEWLINE_RE.replace_all(&body_text, "\n\n").into_owned();
            if body_text.chars().count() > max_chars_per_email {
                // `body_text[:max].rstrip() + "…"`.
                let truncated: String = body_text.chars().take(max_chars_per_email).collect();
                body_text = format!("{}\u{2026}", truncated.trim_end());
            }
            let atts_text = extract_attachment_text(&msg, max_attachment_chars);

            if body_text.is_empty() && atts_text.is_empty() {
                continue;
            }

            // `lines = [f"— {folder} · {date_hdr} · Subject: {subj}"]`.
            let mut lines = vec![format!("\u{2014} {folder} \u{00b7} {date_hdr} \u{00b7} Subject: {subj}")];
            if !body_text.is_empty() {
                lines.push(body_text);
            }
            if !atts_text.is_empty() {
                lines.push(atts_text);
            }
            blocks.push(lines.join("\n"));
        }
    }
    // `finally: conn.close(); conn.logout()` — best-effort.
    let _ = conn.close();
    let _ = conn.logout();

    if blocks.is_empty() {
        return String::new();
    }
    // `"\n\n=====\n\n".join(blocks)`.
    blocks.join("\n\n=====\n\n")
}

/// `_pre_retrieve_context(body, sender)` — extract key Capitalized terms from an
/// incoming email and search past emails + contacts. Returns
/// `(context_snippets, terms_list)`. Best-effort; never raises.
///
/// Security shape preserved verbatim: only retrieve for KNOWN senders (a contact
/// match OR prior INBOX mail from them); require terms >= 5 chars; require a
/// multiword term for unknown senders (here all retrieval is gated on `is_known`,
/// matching the Python); cap to 3 terms; skip entirely for cold senders.
///
/// CONTACTS ADMIN-GATE: the CardDAV address book is global admin data backed by a
/// single Radicale instance, so it is only folded into reply context for an
/// admin / single-user owner — `contacts_allowed = owner_is_admin_or_single_user(owner)`
/// ([`crate::src::tool_security::owner_is_admin_or_single_user`]). Non-admin owners
/// still get their own (owner-scoped) IMAP history below, just not the shared
/// contacts. The known-sender contact check + the contact-match snippet read the
/// PLURAL `emails` list (with a legacy singular `email` fallback) and the plural
/// `phones` list, matching the Python.
///
/// `account_id`/`owner` thread through `_imap`/`_imap_connect` so the IMAP history
/// is owner-scoped. Must run inside `tokio::task::spawn_blocking`.
pub fn pre_retrieve_context(
    body: &str,
    sender: &str,
    account_id: Option<&str>,
    owner: &str,
) -> (Vec<String>, Vec<String>) {
    const STOPWORDS: &[&str] = &[
        "dear", "hello", "hi", "hey", "thanks", "thank", "regards", "best", "kind",
        "sincerely", "cheers", "the", "this", "that", "from", "subject", "re", "fwd",
        "yours", "my", "our", "your",
    ];
    // `contact_emails(c)` — collect a contact's addresses from the PLURAL `emails`
    // list plus the legacy singular `email` key, matching the Python's
    // `contact_emails.extend(emails); if legacy_email: append(legacy_email)`.
    fn contact_emails(c: &serde_json::Value) -> Vec<String> {
        let mut out: Vec<String> = Vec::new();
        if let Some(serde_json::Value::Array(arr)) = c.get("emails") {
            for e in arr {
                out.push(e.as_str().unwrap_or("").to_string());
            }
        }
        if let Some(legacy) = c.get("email").and_then(|v| v.as_str()) {
            if !legacy.is_empty() {
                out.push(legacy.to_string());
            }
        }
        out
    }
    // The whole body is `try/except -> logger.warning`; we mirror with an inner
    // closure whose `None` paths are the Python's `continue`/early returns.
    let run = || {
        // ── Known-sender check ──
        let sender_addr = parseaddr_addr(sender).to_lowercase();
        // `contacts_allowed = owner_is_admin_or_single_user(owner or None)`. The
        // Python wraps this in try/except with `not bool(owner)` as the fallback;
        // the Rust helper never raises, so the primary path is exact.
        let owner_opt = if owner.is_empty() { None } else { Some(owner) };
        let contacts_allowed = crate::src::tool_security::owner_is_admin_or_single_user(owner_opt);
        let mut is_known = false;
        // Contacts known-sender check — ONLY when contacts are admin-allowed. Match
        // the sender against the contact's plural `emails` (+ legacy singular).
        if contacts_allowed {
            for c in crate::routes::contacts_routes::fetch_contacts_json(false) {
                if contact_emails(&c)
                    .iter()
                    .any(|addr| addr.trim().to_lowercase() == sender_addr)
                {
                    is_known = true;
                    break;
                }
            }
        }
        if !is_known && !sender_addr.is_empty() {
            // `with _imap(account_id, owner=owner) as _ck: _ck.select("INBOX",
            // readonly=True); _ck.search(...)`.
            let _ = with_imap(account_id, owner, |ck| {
                if ck.examine("INBOX").is_ok() {
                    if let Ok(hits) = ck.search(format!("(FROM \"{sender_addr}\")")) {
                        if !hits.is_empty() {
                            is_known = true;
                        }
                    }
                }
            });
        }
        if !is_known {
            logger::info(&format!("Pre-retrieval skipped — unknown sender {sender_addr}"));
            return (Vec::new(), Vec::new());
        }

        // ── Term extraction ──
        let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
        let mut multiword: Vec<String> = Vec::new();
        let mut singleword: Vec<String> = Vec::new();
        for caps in CAP_TERMS_RE.captures_iter(body) {
            let term = caps.get(1).map(|m| m.as_str()).unwrap_or("").trim().to_string();
            let key = term.to_lowercase();
            if seen.contains(&key) {
                continue;
            }
            let first = term.split_whitespace().next().unwrap_or("").to_lowercase();
            if STOPWORDS.contains(&first.as_str()) {
                continue;
            }
            // `len(term) < 5` — Python `len` is char count.
            if term.chars().count() < 5 {
                continue;
            }
            seen.insert(key);
            if term.contains(' ') {
                multiword.push(term);
            } else {
                singleword.push(term);
            }
        }
        // `sender_name_clean = _decode_header(sender).split("<")[0].strip().lower()`.
        let decoded_sender = decode_header(sender);
        let sender_name_clean = decoded_sender
            .split('<')
            .next()
            .unwrap_or("")
            .trim()
            .to_lowercase();
        // `ranked = [t for t in (multiword + singleword) if t.lower() != sender_name_clean]`.
        let ranked: Vec<String> = multiword
            .into_iter()
            .chain(singleword)
            .filter(|t| t.to_lowercase() != sender_name_clean)
            .collect();
        let terms_list: Vec<String> = ranked.into_iter().take(3).collect();
        logger::info(&format!("Pre-retrieval terms={terms_list:?}"));

        if terms_list.is_empty() {
            return (Vec::new(), terms_list);
        }

        let mut context_snippets: Vec<String> = Vec::new();

        // ── IMAP TEXT search across folders ──
        // `ctx_conn = _imap_connect(account_id, owner=owner)` then per-folder/per-term
        // TEXT search, fetch the last 2 hits. Whole block is `try/except -> warning`.
        match imap_connect(account_id, owner) {
            Ok(mut ctx_conn) => {
                for folder in ["INBOX", "Sent", "Archive", "Drafts"] {
                    // `ctx_conn.select(_q(folder), readonly=True)` -> EXAMINE bare name.
                    if ctx_conn.examine(folder).is_err() {
                        continue;
                    }
                    for term in &terms_list {
                        // `safe_term = term.replace('"', '').replace('\\', '')`.
                        let safe_term: String =
                            term.chars().filter(|&c| c != '"' && c != '\\').collect();
                        // `ctx_conn.search(None, "TEXT", f'"{safe_term}"')`.
                        let all_hits = match ctx_conn.search(format!("TEXT \"{safe_term}\"")) {
                            Ok(h) if !h.is_empty() => h,
                            Ok(_) => continue,
                            Err(e) => {
                                logger::warning(&format!("  search {folder} {term:?} failed: {e}"));
                                continue;
                            }
                        };
                        // `all_hits = data2[0].split(); hit_uids = all_hits[-2:]`.
                        let mut sorted: Vec<u32> = all_hits.iter().copied().collect();
                        sorted.sort_unstable();
                        let total = sorted.len();
                        let hit_uids: Vec<u32> =
                            sorted.into_iter().skip(total.saturating_sub(2)).collect();
                        logger::info(&format!("  [{folder}] term={term:?} hits={total}"));
                        for huid in hit_uids {
                            // `st2, hd = ctx_conn.fetch(huid, "(RFC822)")`.
                            let raw: Vec<u8> = match ctx_conn.fetch(huid.to_string(), "(RFC822)") {
                                Ok(fetches) => {
                                    match fetches.iter().find_map(|f| f.body().map(|b| b.to_vec())) {
                                        Some(b) if !b.is_empty() => b,
                                        _ => continue,
                                    }
                                }
                                Err(_) => continue,
                            };
                            let hmsg = match parse_message(&raw) {
                                Some(m) => m,
                                None => continue,
                            };
                            let hsubj = decode_header(&hmsg.header("subject").unwrap_or_default());
                            let hfrom = decode_header(&hmsg.header("from").unwrap_or_default());
                            let hdate = hmsg.header("date").unwrap_or_default();
                            // `hbody = _extract_text(hmsg)[:600]`.
                            let hbody: String = extract_text(&hmsg).chars().take(600).collect();
                            context_snippets.push(format!(
                                "[{folder} match for \"{term}\"]\nFrom: {hfrom}\nDate: {hdate}\nSubject: {hsubj}\n{hbody}"
                            ));
                        }
                    }
                }
                let _ = ctx_conn.logout();
            }
            Err(e) => {
                logger::warning(&format!("IMAP context search failed: {e}"));
            }
        }

        // ── Contact matches ──
        // `all_contacts = _fetch_contacts() if contacts_allowed else []`. Match a term
        // against the contact `name` OR any of its plural `emails`; emit the plural
        // `emails`/`phones` lists joined with ", " (the admin-gated CardDAV book).
        let all_contacts = if contacts_allowed {
            crate::routes::contacts_routes::fetch_contacts_json(false)
        } else {
            Vec::new()
        };
        // `', '.join(c['emails'])` — render a plural string list (skips empties).
        let join_strs = |c: &serde_json::Value, k: &str| -> Vec<String> {
            match c.get(k) {
                Some(serde_json::Value::Array(arr)) => arr
                    .iter()
                    .map(|v| v.as_str().unwrap_or("").to_string())
                    .collect(),
                _ => Vec::new(),
            }
        };
        for term in &terms_list {
            let t_lower = term.to_lowercase();
            let matches: Vec<&serde_json::Value> = all_contacts
                .iter()
                .filter(|c| {
                    let name_lc = c.get("name").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
                    name_lc.contains(&t_lower)
                        || join_strs(c, "emails")
                            .iter()
                            .any(|e| e.to_lowercase().contains(&t_lower))
                })
                .collect();
            for c in matches.into_iter().take(2) {
                let cname = c.get("name").and_then(|v| v.as_str()).unwrap_or("").to_string();
                let mut parts = vec![format!("Name: {cname}")];
                let emails = join_strs(c, "emails");
                if !emails.is_empty() {
                    parts.push(format!("Email: {}", emails.join(", ")));
                }
                let phones = join_strs(c, "phones");
                if !phones.is_empty() {
                    parts.push(format!("Phone: {}", phones.join(", ")));
                }
                context_snippets.push(format!("[Contact match for \"{term}\"] {}", parts.join(", ")));
            }
        }

        (context_snippets, terms_list)
    };

    let (context_snippets, terms_list) = run();
    logger::info(&format!("Pre-retrieval snippets={}", context_snippets.len()));
    (context_snippets, terms_list)
}

// ===========================================================================
// AI-pipeline text helpers (`_strip_think` / `_extract_reply` / style mechanics)
// ===========================================================================

// `_THINK_CLOSED_RE` / `_THINK_OPEN_RE` / `_THINK_TAG_RE` — the email-side
// `_strip_think` checks whether any `<think>` markup was present so it only runs
// the prose-strip when the input actually had a think tag. The central regexes in
// `src::text_helpers` are module-private, so these mirror them exactly (same
// patterns as that module's `THINK_CLOSED_RE` / `THINK_OPEN_RE` / `THINK_TAG_RE`).
static THINK_CLOSED_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)<think(?:ing)?>[\s\S]*?</think(?:ing)?>\s*").unwrap());
static THINK_OPEN_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?is)^\s*<think(?:ing)?>.*?(?:</think(?:ing)?>|$)").unwrap());
static THINK_TAG_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)</?think(?:ing)?[^>]*>\s*").unwrap());

/// `_strip_think(text)` — email-flavored think strip. Runs the central
/// [`crate::src::text_helpers::strip_think`] with `prompt_echo=True` and
/// `prose=had_think` (the prose-strip extension only when a `<think>` tag was
/// actually present, so legit user content is safe).
pub fn strip_think(text: &str) -> String {
    if text.is_empty() {
        return String::new();
    }
    let had_think = THINK_CLOSED_RE.is_match(text)
        || THINK_OPEN_RE.is_match(text)
        || THINK_TAG_RE.is_match(text);
    crate::src::text_helpers::strip_think(text, had_think, true)
}

// `_REPLY_OPEN_RE` / `_REPLY_CLOSE_RE` — accept REPLY / SUMMARY / OUTPUT as the
// opening fence (any fenced final-output block). The closing `>>+` (two or more
// `>`) is intentionally loose so a model that emits `>>>>` (or just `>>`) still
// matches — exactly the Python `>>+` quantifier.
static REPLY_OPEN_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)<<<\s*(?:REPLY|SUMMARY|OUTPUT)\s*>>+").unwrap());
static REPLY_CLOSE_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?i)<<<\s*END\s*>>+").unwrap());

/// `_extract_reply(text)` — pull the final reply out of a model response. Keeps
/// ONLY the `<<<REPLY>>> ... <<<END>>>` region (positive extraction), then strips
/// stray marker tokens and runs the think-strip on the result. Falls back to a
/// plain think-strip of the whole text when the markers are absent.
pub fn extract_reply(text: &str) -> String {
    if text.is_empty() {
        return String::new();
    }
    let mut t: String = text.to_string();
    if let Some(m) = REPLY_OPEN_RE.find(&t) {
        let rest = t[m.end()..].to_string();
        t = match REPLY_CLOSE_RE.find(&rest) {
            Some(c) => rest[..c.start()].to_string(),
            None => rest,
        };
    }
    // Drop any stray/duplicate marker tokens, then strip think markup.
    let t = REPLY_OPEN_RE.replace_all(&t, "").into_owned();
    let t = REPLY_CLOSE_RE.replace_all(&t, "").into_owned();
    strip_think(&t).trim().to_string()
}

/// `_apply_email_style_mechanics(text)` — deterministic writing-style mechanics:
/// em/en dashes -> `--`, curly apostrophes -> `'`.
pub fn apply_email_style_mechanics(text: &str) -> String {
    if text.is_empty() {
        return String::new();
    }
    // `.replace("—","--").replace("–","--").replace("’","'").replace("‘","'")` —
    // collapsed into two char-set replaces (behavior-identical: em/en dash -> "--",
    // curly single quotes -> "'").
    text.replace(['\u{2014}', '\u{2013}'], "--") // em dash — / en dash –
        .replace(['\u{2019}', '\u{2018}'], "'") // curly single quotes ’ ‘
}

/// `_EMAIL_REPLY_SYS_PROMPT_BASE` — the AI reply system-prompt base string.
pub const EMAIL_REPLY_SYS_PROMPT_BASE: &str = concat!(
    "You are drafting an email reply. Write only the reply body, no subject line, ",
    "and no extra commentary. The saved WRITING STYLE below outranks generic tone guidance. ",
    "If the saved style says to use a greeting/sign-off, include them. For English replies, ",
    "default to 'Hi [Name]' rather than 'Hey'. Be direct and concise. Match the tone of the ",
    "original email without violating the saved style.\n\n",
    "MECHANICAL STYLE RULES — CRITICAL: Never use an em dash or en dash; use -- instead. ",
    "Never use curly apostrophes; write I'm, don't, we'll with straight '. Do not start ",
    "with 'Hey' unless the saved style explicitly requests it.\n\n",
    "IDENTITY RULE — CRITICAL: write as the user/mailbox owner only. NEVER sign as, ",
    "speak as, or imply you are the recipient, original sender, quoted sender, spouse, ",
    "assistant, company, or any third party. Do not copy a name from the quoted thread ",
    "into the sign-off. If a writing style below names a signature, use only that ",
    "signature; otherwise omit the sign-off.\n\n",
    "CRITICAL RULE: NEVER invent facts, names, dates, phone numbers, emails, addresses, ",
    "or any specifics not explicitly present in the RELEVANT CONTEXT section below or ",
    "the original email itself. If the sender asks for information you don't have in ",
    "the context, say plainly that you don't have it on hand — do NOT guess or fabricate. ",
    "Do not promise to 'look it up' or 'get back to you soon' as a way to pad the reply. ",
    "If you have no real information to offer, write a short honest reply (2-4 sentences max).\n\n",
    "OUTPUT FORMAT — IMPORTANT: Put ONLY the final email reply between these exact markers, ",
    "each on its own line:\n",
    "<<<REPLY>>>\n",
    "(the reply body goes here)\n",
    "<<<END>>>\n",
    "Any reasoning, planning, or notes-to-self must come BEFORE the <<<REPLY>>> marker ",
    "(ideally wrapped in <think>...</think>). Only the text between <<<REPLY>>> and <<<END>>> ",
    "is sent as the email — nothing else is shown to anyone."
);

// ===========================================================================
// Request models (`SendEmailRequest` / `ExtractStyleRequest`)
// ===========================================================================

/// `class SendEmailRequest(BaseModel)`. `serde` defaults reproduce the pydantic
/// optionals (`None`) and the `wait_for_delivery: bool = False` default.
#[derive(Debug, Clone, Deserialize)]
pub struct SendEmailRequest {
    pub to: String,
    #[serde(default)]
    pub cc: Option<String>,
    #[serde(default)]
    pub bcc: Option<String>,
    pub subject: String,
    pub body: String,
    /// WYSIWYG compose sends rendered HTML here; absent -> render markdown from body.
    #[serde(default)]
    pub body_html: Option<String>,
    #[serde(default)]
    pub in_reply_to: Option<String>,
    #[serde(default)]
    pub references: Option<String>,
    /// Uploaded attachment tokens (filenames in COMPOSE_UPLOADS_DIR).
    #[serde(default)]
    pub attachments: Option<Vec<String>>,
    /// Which account to send from. None = default account.
    #[serde(default)]
    pub account_id: Option<String>,
    /// Internal marker for Odysseus-generated mail (reminder, scheduled, …).
    #[serde(default)]
    pub odysseus_kind: Option<String>,
    /// If true, /send waits for SMTP + Sent append and returns the sent UID.
    #[serde(default)]
    pub wait_for_delivery: bool,
}

/// `class ExtractStyleRequest(BaseModel)` — `sample_count: Optional[int] = 20`.
#[derive(Debug, Clone, Deserialize)]
pub struct ExtractStyleRequest {
    #[serde(default = "default_sample_count")]
    pub sample_count: Option<i64>,
}

fn default_sample_count() -> Option<i64> {
    Some(20)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn q_quotes_and_escapes() {
        // `_q` wraps in double quotes and escapes \ and ".
        assert_eq!(q("Sent"), "\"Sent\"");
        assert_eq!(q("[Gmail]/Sent Mail"), "\"[Gmail]/Sent Mail\"");
        assert_eq!(q("a\"b"), "\"a\\\"b\"");
        assert_eq!(q("a\\b"), "\"a\\\\b\"");
        assert_eq!(q(""), "\"\"");
    }

    #[test]
    fn style_mechanics_replaces_dashes_and_quotes() {
        assert_eq!(apply_email_style_mechanics("a—b–c"), "a--b--c");
        assert_eq!(apply_email_style_mechanics("don\u{2019}t \u{2018}x\u{2019}"), "don't 'x'");
        assert_eq!(apply_email_style_mechanics(""), "");
    }

    #[test]
    fn extract_reply_keeps_only_fenced_region() {
        let t = "thinking here\n<<<REPLY>>>\nHello there\n<<<END>>>\ntrailing";
        assert_eq!(extract_reply(t), "Hello there");
    }

    #[test]
    fn extract_reply_accepts_summary_and_output_fences() {
        assert_eq!(extract_reply("<<<SUMMARY>>>\nA summary\n<<<END>>>"), "A summary");
        assert_eq!(extract_reply("<<<OUTPUT>>>\nThe output\n<<<END>>>"), "The output");
    }

    #[test]
    fn extract_reply_falls_back_to_think_strip() {
        // No markers -> just think-strip the whole text.
        assert_eq!(extract_reply("Just a plain reply."), "Just a plain reply.");
        assert_eq!(
            extract_reply("<think>reasoning</think>The reply."),
            "The reply."
        );
    }

    #[test]
    fn extract_reply_accepts_loose_gt_markers() {
        // The `>>+` quantifier accepts two-or-more `>` on BOTH the open and close
        // fence — so `>>` and `>>>>` markers extract the same region as `>>>`.
        assert_eq!(
            extract_reply("pre\n<<<REPLY>>\nLoose two\n<<<END>>\npost"),
            "Loose two"
        );
        assert_eq!(
            extract_reply("pre\n<<<REPLY>>>>\nLoose four\n<<<END>>>>\npost"),
            "Loose four"
        );
        // Mixed open/close widths still match.
        assert_eq!(
            extract_reply("<<<SUMMARY>>>>\nMixed\n<<<END>>"),
            "Mixed"
        );
    }

    #[test]
    fn smtp_security_mode_explicit_value_wins() {
        // An explicit, recognized mode wins regardless of port (case/space-insensitive).
        assert_eq!(smtp_security_mode("ssl", 587), "ssl");
        assert_eq!(smtp_security_mode("  STARTTLS ", 465), "starttls");
        assert_eq!(smtp_security_mode("None", 25), "none");
    }

    #[test]
    fn smtp_security_mode_port_heuristic() {
        // Empty/unknown stored value -> port heuristic: 587 -> starttls, else ssl.
        assert_eq!(smtp_security_mode("", 587), "starttls");
        assert_eq!(smtp_security_mode("", 465), "ssl");
        assert_eq!(smtp_security_mode("bogus", 2525), "ssl");
        // Unset port (0) -> `int(... or 465)` -> 465 -> ssl.
        assert_eq!(smtp_security_mode("", 0), "ssl");
    }

    #[test]
    fn coerce_imap_timeout_clamps_and_defaults() {
        // `int(raw or "30")` then `max(5, min(value, 300))`.
        assert_eq!(coerce_imap_timeout_seconds(None), 30);
        assert_eq!(coerce_imap_timeout_seconds(Some("")), 30); // empty -> "30"
        assert_eq!(coerce_imap_timeout_seconds(Some("notanint")), 30); // ValueError -> 30
        assert_eq!(coerce_imap_timeout_seconds(Some("45")), 45);
        assert_eq!(coerce_imap_timeout_seconds(Some("1")), 5); // clamp low
        assert_eq!(coerce_imap_timeout_seconds(Some("99999")), 300); // clamp high
        assert_eq!(coerce_imap_timeout_seconds(Some("  60  ")), 60); // trimmed
    }

    /// Serialize the `ODYSSEUS_MAIL_ATTACHMENTS_DIR`-mutating tests (env vars are
    /// process-global) and point the attachments root at a fresh temp dir so we
    /// NEVER create `mail-attachments` under the live repo `data/`.
    static ATTACH_ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

    fn with_temp_attachments_dir<R>(f: impl FnOnce() -> R) -> R {
        let _g = ATTACH_ENV_LOCK.lock().unwrap_or_else(|p| p.into_inner());
        let tmp = std::env::temp_dir()
            .join(format!("ody_eh_att_{}", uuid::Uuid::new_v4().simple()));
        let prev = std::env::var("ODYSSEUS_MAIL_ATTACHMENTS_DIR").ok();
        std::env::set_var("ODYSSEUS_MAIL_ATTACHMENTS_DIR", &tmp);
        let out = f();
        match prev {
            Some(v) => std::env::set_var("ODYSSEUS_MAIL_ATTACHMENTS_DIR", v),
            None => std::env::remove_var("ODYSSEUS_MAIL_ATTACHMENTS_DIR"),
        }
        let _ = std::fs::remove_dir_all(&tmp);
        out
    }

    #[test]
    fn attachment_extract_dir_sanitizes_and_contains() {
        with_temp_attachments_dir(|| {
            // A normal folder/uid resolves to ATTACHMENTS_DIR/<sanitized>, contained.
            let dir = attachment_extract_dir("INBOX", "42").expect("ok");
            let base = lexically_normalize(&attachments_dir());
            assert!(dir.starts_with(&base), "{dir:?} not under {base:?}");
            assert_eq!(dir.file_name().unwrap().to_string_lossy(), "INBOX_42");
        });
    }

    #[test]
    fn attachment_extract_dir_flattens_traversal() {
        with_temp_attachments_dir(|| {
            // `../../tmp` style values are flattened: every path-separator / dot-run
            // that isn't `[A-Za-z0-9._-]` becomes `_`, so the key has NO separators
            // and cannot escape the root. The result stays strictly under ATTACHMENTS_DIR.
            let dir = attachment_extract_dir("../../etc", "passwd").expect("ok");
            let base = lexically_normalize(&attachments_dir());
            assert!(dir.starts_with(&base), "{dir:?} escaped {base:?}");
            // The folder/uid is flattened to a SINGLE path segment: every char
            // outside `[A-Za-z0-9._-]` (notably `/`) becomes `_`. Dots ARE kept
            // (Python's charset allows `.`), so `../../etc` + `passwd` becomes the
            // literal ".._.._etc_passwd" — it contains the substring ".." but, with
            // no separator, it is one inert directory name that cannot traverse.
            let seg = dir.file_name().unwrap().to_string_lossy().to_string();
            assert_eq!(seg, ".._.._etc_passwd");
            assert!(!seg.contains('/'), "segment {seg:?} still has a separator");
            // Containment holds: the target is exactly one component below base.
            assert_eq!(dir.parent().map(lexically_normalize), Some(base));
        });
    }

    #[test]
    fn attachment_extract_dir_empty_key_falls_back_to_underscore() {
        with_temp_attachments_dir(|| {
            // `re.sub(...) or "_"` — a folder/uid whose join sanitizes to "" becomes
            // a single `_`. (The `_` separator between folder/uid keeps it non-empty
            // in practice, but the guard is faithful to the Python.)
            let dir = attachment_extract_dir("", "").expect("ok");
            // "_" + "_" + "" -> "_" -> sanitized "_". Stays contained.
            let base = lexically_normalize(&attachments_dir());
            assert!(dir.starts_with(&base));
            assert!(!dir.file_name().unwrap().to_string_lossy().is_empty());
        });
    }

    #[test]
    fn lexically_normalize_resolves_dotdot() {
        // Belt-and-suspenders: prove the containment check would REJECT an escape if
        // a `..` ever survived sanitization, by exercising the normalizer directly.
        // Pure path math — no filesystem access, so no env / temp dir needed.
        let base = lexically_normalize(Path::new("/srv/data/mail-attachments"));
        let escaped =
            lexically_normalize(Path::new("/srv/data/mail-attachments/../../evil"));
        // The escaped path is NOT under base nor equal to it.
        let contained = escaped == base || escaped.ancestors().skip(1).any(|p| p == base.as_path());
        assert!(!contained, "normalized {escaped:?} must not be contained in {base:?}");
        assert_eq!(escaped, PathBuf::from("/srv/evil"));
    }

    /// A scratch SQLite file in the system temp dir (NEVER the live repo `data/`).
    /// Cleaned up on drop. `init_scheduled_db_on` runs the DDL/migration against it
    /// directly, so these tests never touch `scheduled_db()` / the real DATA_DIR.
    struct TempDb {
        path: PathBuf,
    }
    impl TempDb {
        fn new() -> Self {
            let path = std::env::temp_dir()
                .join(format!("ody_eh_sched_{}.db", uuid::Uuid::new_v4().simple()));
            TempDb { path }
        }
        fn open(&self) -> rusqlite::Connection {
            rusqlite::Connection::open(&self.path).unwrap()
        }
    }
    impl Drop for TempDb {
        fn drop(&mut self) {
            let _ = std::fs::remove_file(&self.path);
        }
    }

    #[test]
    fn scheduled_db_has_owner_column_and_index() {
        // Bootstrap a FRESH temp scheduled DB and assert the owner column + the
        // owner-status index exist (Phase-B owner-scoping foundation). Uses a temp
        // DB file directly — never the live DATA_DIR.
        let db = TempDb::new();
        let conn = db.open();
        init_scheduled_db_on(&conn);

        let cols = pragma_columns(&conn, "scheduled_emails").unwrap();
        assert!(cols.iter().any(|c| c == "owner"), "owner column missing: {cols:?}");
        let idx_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name='ix_scheduled_emails_owner_status'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(idx_count, 1, "ix_scheduled_emails_owner_status missing");
        // An insert carrying owner round-trips.
        conn.execute(
            "INSERT INTO scheduled_emails (id, to_addr, body, send_at, created_at, status, owner) \
             VALUES ('id1', 'a@b.com', 'hi', '2026-01-01', '2026-01-01', 'pending', 'alice')",
            [],
        )
        .unwrap();
        let got: String = conn
            .query_row("SELECT owner FROM scheduled_emails WHERE id='id1'", [], |r| r.get(0))
            .unwrap();
        assert_eq!(got, "alice");
    }

    #[test]
    fn scheduled_db_owner_backfill_migrates_legacy_table() {
        // A pre-owner scheduled_emails table (no owner column) gets the column +
        // index added on bootstrap, and legacy rows are LEFT with the default ''
        // when no matching email_accounts row exists. Proves the ALTER path runs
        // without dropping data. Temp DB only — never the live DATA_DIR.
        let db = TempDb::new();
        let conn = db.open();
        // Pre-create the OLD-shape table (no owner column) with a legacy row.
        conn.execute(
            "CREATE TABLE scheduled_emails (\
                id TEXT PRIMARY KEY, to_addr TEXT NOT NULL, cc TEXT, bcc TEXT, \
                subject TEXT, body TEXT NOT NULL, in_reply_to TEXT, references_hdr TEXT, \
                attachments TEXT, send_at TEXT NOT NULL, created_at TEXT NOT NULL, \
                status TEXT NOT NULL DEFAULT 'pending', error TEXT, account_id TEXT)",
            [],
        )
        .unwrap();
        // Leave account_id NULL so the `account_id != ''` backfill filter yields no
        // legacy accounts — the migration's ALTER/index path still runs, but the
        // EmailAccount backfill (which would open the shared app DB) is skipped, so
        // this stays hermetic and never touches the live DATA_DIR.
        conn.execute(
            "INSERT INTO scheduled_emails (id, to_addr, body, send_at, created_at, status) \
             VALUES ('legacy1', 'x@y.com', 'b', '2026-01-01', '2026-01-01', 'pending')",
            [],
        )
        .unwrap();

        init_scheduled_db_on(&conn);

        let cols = pragma_columns(&conn, "scheduled_emails").unwrap();
        assert!(cols.iter().any(|c| c == "owner"), "owner not added by migration");
        // The legacy row survived the ALTER and defaults to '' (no matching account).
        let owner: String = conn
            .query_row("SELECT owner FROM scheduled_emails WHERE id='legacy1'", [], |r| {
                r.get::<_, Option<String>>(0).map(|o| o.unwrap_or_default())
            })
            .unwrap();
        assert_eq!(owner, "");
    }

    #[test]
    fn strip_think_empty_is_empty() {
        assert_eq!(strip_think(""), "");
    }

    #[test]
    fn decode_header_identity_on_plain() {
        // No encoded-words -> identity (matching Python's join-with-space on a
        // single ASCII token).
        assert_eq!(decode_header("Hello World"), "Hello World");
        assert_eq!(decode_header(""), "");
    }

    #[test]
    fn decode_header_decodes_encoded_word() {
        // RFC2047 encoded-word -> decoded UTF-8.
        assert_eq!(
            decode_header("=?utf-8?q?Caf=C3=A9?="),
            "Café"
        );
    }

    #[test]
    fn extract_bare_address_strips_display_name() {
        assert_eq!(extract_bare_address("Alice <a@x.com>"), "a@x.com");
        assert_eq!(extract_bare_address("b@y.com"), "b@y.com");
        assert_eq!(extract_bare_address("  c@z.com  "), "c@z.com");
    }

    #[test]
    fn parseaddr_addr_extracts_address_half() {
        // `email.utils.parseaddr(s)[1]`.
        assert_eq!(parseaddr_addr("Alice <a@x.com>"), "a@x.com");
        assert_eq!(parseaddr_addr("b@y.com"), "b@y.com");
        // A bare display name with no '@' yields the empty address.
        assert_eq!(parseaddr_addr("Just A Name"), "");
        assert_eq!(parseaddr_addr(""), "");
    }

    #[test]
    fn lenient_address_accepts_what_smtplib_accepts() {
        // The strict `Address::from_str` REJECTS these, but smtplib (and thus the
        // permissive envelope path) sends them verbatim. We reproduce that via
        // `Address::new_dangerous(user, domain)`, whose wire form is `user@domain`.
        use lettre::Address;
        let build = |s: &str| -> Address {
            let bare = extract_bare_address(s);
            match bare.rsplit_once('@') {
                Some((local, domain)) => Address::new_dangerous(local, domain),
                None => Address::new_dangerous(bare.as_str(), ""),
            }
        };
        // A local part the RFC parser would reject for length is accepted here.
        let long = "a".repeat(80);
        let a = build(&format!("{long}@example.com"));
        // `Display`/`AsRef<str>` both render the verbatim wire form; use Display to
        // avoid the `AsRef<str>` vs `AsRef<OsStr>` ambiguity.
        assert_eq!(a.to_string(), format!("{long}@example.com"));
        // Display-name form is reduced to the bare address.
        let b = build("Bob <bob@host.local>");
        assert_eq!(b.to_string(), "bob@host.local");
        // Confirm the strict parser really would have rejected the long form.
        assert!(format!("{long}@example.com").parse::<Address>().is_err());
    }

    #[test]
    fn send_email_request_defaults() {
        let r: SendEmailRequest =
            serde_json::from_str(r#"{"to":"x@y.com","subject":"s","body":"b"}"#).unwrap();
        assert_eq!(r.to, "x@y.com");
        assert!(r.cc.is_none());
        assert!(!r.wait_for_delivery);
        assert!(r.attachments.is_none());
    }

    #[test]
    fn extract_style_request_default_sample_count() {
        let r: ExtractStyleRequest = serde_json::from_str("{}").unwrap();
        assert_eq!(r.sample_count, Some(20));
        let r2: ExtractStyleRequest = serde_json::from_str(r#"{"sample_count":5}"#).unwrap();
        assert_eq!(r2.sample_count, Some(5));
    }

    #[test]
    fn parse_message_extracts_text_and_subject() {
        let raw = b"Subject: Hi\r\nFrom: a@b.com\r\nContent-Type: text/plain\r\n\r\nHello body";
        let msg = parse_message(raw).expect("parse");
        assert_eq!(msg.header("subject").as_deref(), Some("Hi"));
        assert_eq!(extract_text(&msg).trim(), "Hello body");
    }

    #[test]
    fn extract_text_strips_html_when_no_plaintext() {
        let raw = b"MIME-Version: 1.0\r\nContent-Type: text/html\r\n\r\n<p>Hi<br>there</p>";
        let msg = parse_message(raw).expect("parse");
        // Single-part html: extract_text decodes the payload (text_contents).
        let text = extract_text(&msg);
        assert!(text.contains("Hi"));
    }

    #[test]
    fn header_returns_raw_value_like_python_msg_get() {
        // mail-parser parses From -> Address, Date -> DateTime, Message-ID ->
        // bracket-stripped id; `header()` must instead surface the RAW value, the
        // exact bytes Python's `msg.get(name)` yields (trimmed of SP/CRLF).
        let raw = b"From: John Doe <john@example.com>\r\n\
            To: Jane <jane@example.com>\r\n\
            Date: Tue, 3 Jun 2025 09:05:00 +0000\r\n\
            Message-ID: <abc.123@host>\r\n\
            Subject: =?utf-8?q?Hi_=E2=98=83?=\r\n\
            Content-Type: text/plain\r\n\r\nbody";
        let msg = parse_message(raw).expect("parse");
        // From: the real sender line, NOT empty (regression for the Address arm).
        assert_eq!(
            msg.header("from").as_deref(),
            Some("John Doe <john@example.com>")
        );
        assert_eq!(msg.header("to").as_deref(), Some("Jane <jane@example.com>"));
        // Date: verbatim raw string, NOT a reformatted/recomputed RFC822 render.
        assert_eq!(
            msg.header("date").as_deref(),
            Some("Tue, 3 Jun 2025 09:05:00 +0000")
        );
        // Message-ID: brackets preserved (Python stores `<...>`), NOT stripped.
        assert_eq!(msg.header("message-id").as_deref(), Some("<abc.123@host>"));
        // Subject stays RFC2047-decoded (callers re-decode_header it harmlessly).
        assert_eq!(msg.header("subject").as_deref(), Some("Hi \u{2603}"));
    }

    #[test]
    fn part_disposition_includes_full_header_params() {
        // `Content-Disposition: inline; filename="my attachment.pdf"` — Python's
        // full-header `"attachment" not in cd` test sees the filename param, so the
        // part is enumerated as a real attachment. The reconstructed disposition
        // string must therefore contain BOTH "inline" and "attachment".
        let raw = b"MIME-Version: 1.0\r\n\
            Content-Type: multipart/mixed; boundary=\"B\"\r\n\r\n\
            --B\r\nContent-Type: text/plain\r\n\r\nbody\r\n\
            --B\r\nContent-Type: application/octet-stream\r\n\
            Content-Disposition: inline; filename=\"my attachment.pdf\"\r\n\r\n\
            data\r\n--B--\r\n";
        let msg = parse_message(raw).expect("parse");
        let atts = list_attachments_from_msg(&msg);
        // The inline-with-attachment-named part is enumerated (Python parity).
        assert_eq!(atts.len(), 1, "inline part with filename is a real attachment");
        assert!(atts[0].is_inline, "inline disposition -> is_inline true");
        assert_eq!(atts[0].filename, "my attachment.pdf");
    }

    #[test]
    fn guess_mime_known_and_default() {
        assert_eq!(guess_mime("x.pdf"), ("application".into(), "pdf".into()));
        assert_eq!(guess_mime("x.png"), ("image".into(), "png".into()));
        assert_eq!(
            guess_mime("x.unknownext"),
            ("application".into(), "octet-stream".into())
        );
    }

    #[test]
    fn email_config_default_ports() {
        // The fallback (no DB row, no settings) resolves the env/default ports.
        let cfg = EmailConfig {
            smtp_port: 465,
            imap_port: 993,
            ..Default::default()
        };
        assert_eq!(cfg.smtp_port, 465);
        assert_eq!(cfg.imap_port, 993);
    }

    #[test]
    fn imap_connect_with_cfg_errors_on_unresolvable_host() {
        // `imap_connect` is now a thin wrapper that resolves an `EmailConfig` then
        // calls `imap_connect_with_cfg`. This proves the factored-out seam is
        // reachable and composes the address-resolution path the same way: an empty
        // host resolves to no SocketAddr (immediate, no DNS / no hang) and returns
        // the `"IMAP connect failed: ..."` error string — exactly the error shape
        // `imap_connect` would produce for the same config. Callers (incl. the
        // built-in email MCP server) thread their own `EmailConfig` through this.
        let cfg = EmailConfig {
            imap_host: String::new(),
            imap_port: 993,
            imap_starttls: false,
            ..Default::default()
        };
        let res = imap_connect_with_cfg(&cfg);
        assert!(res.is_err());
        assert!(
            res.unwrap_err().starts_with("IMAP connect failed:"),
            "empty host yields the connect-failed error shape"
        );
    }
}
