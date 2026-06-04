// routes/email_routes.rs  <- routes/email_routes.py (wave-8 EMAIL, the route file)
//! FastAPI route handlers for the email feature -> axum. All non-route logic
//! (IMAP/SMTP transport, MIME parse, account config, the request models) lives in
//! [`crate::routes::email_helpers`] (the TRANSPORT half, already ported) and the
//! background pollers in `email_pollers`; this file is the `setup_email_routes()`
//! router factory plus the 42 `@router.<method>("/api/email/...")` handlers.
//!
//! ## What is real here (the wave-8 mail stack, ported for REAL)
//!   * IMAP list / read / search / folders / flags / move / delete / attachments —
//!     the `imap` crate (3.0-alpha) via [`email_helpers::imap_connect`] /
//!     [`email_helpers::with_imap`], every blocking call under
//!     `tokio::task::spawn_blocking`.
//!   * SMTP send / draft / scheduled — `lettre` via
//!     [`email_helpers::send_smtp_message`], building the raw RFC822 ourselves
//!     (the `MIMEMultipart` analogue) so the wire bytes match Python's
//!     `outer.as_string()`/`as_bytes()`.
//!   * MIME parse — `mail-parser` via [`email_helpers::parse_message`] and the
//!     extraction helpers.
//!   * The AI pipeline (summarize / extract-style / ai-reply) — `llm_call_async`
//!     + the shared [`crate::src::endpoint_resolver::resolve_endpoint_triple`]
//!     (owner-threaded, so per-user model prefs apply),
//!     `_strip_think` / `_extract_reply` / style mechanics from `email_helpers`,
//!     and the on-the-fly thread parse via [`crate::src::email_thread_parser`].
//!
//! ## In-process cache / pool drift (documented, faithful)
//! The Python wraps the handlers in three closure-captured caches (`_LIST_CACHE`,
//! `_READ_CACHE`, `_IMAP_POOL`) plus a prefetch task, all created inside
//! `setup_email_routes()`. They are a PERFORMANCE layer, not observable behavior:
//! every cache lookup is a pure function of the same IMAP state, and the Python
//! itself bypasses them on `cache_bust`. The list/read caches are reproduced here
//! as process-global TTL maps (the `_LIST_CACHE`/`_READ_CACHE` analogue) so a
//! flag mutation still invalidates a stale list within the request. The
//! background warm-PREFETCH task IS reproduced (`schedule_recent_email_warm` +
//! `WARMING_READS`): after a list load it `tokio::spawn`s a detached read of the
//! top recent visible UID (with `mark_seen=false`) so the next click lands in the
//! read cache, exactly like the Python `asyncio.create_task(_warm())`. Only the
//! IMAP connection POOL is NOT reproduced — every handler (including the warm
//! read) opens a fresh connection via `with_imap` (the Python's own pre-pool
//! fallback path, and exactly what `_imap` does when `_POOL_HOOKS` are unset).
//! This is the documented drift: same responses, no shared live socket.
//!
//! ## Honest defers (only the genuinely un-portable bits, as Python behaves)
//!   * `attachment-as-doc` `.docx` branch -> Python `except ImportError: return
//!     {"error": "python-docx not installed"}`; there is no docx reader crate
//!     wired, so we reproduce that exact import-failure surface.
//!   * `ai-reply` model-resolve (`list_model_ids(url, headers)`) and the
//!     `resolve_*_fallback_candidates` chains are TODO(db); the Python wraps each
//!     in `try/except` (`except: model unchanged` / `except: []`), so we reproduce
//!     those `except` arms — the primary endpoint still runs via `llm_call_async`.


use std::collections::{HashMap, HashSet};
use std::net::SocketAddr;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use axum::body::Body;
use axum::extract::{ConnectInfo, Multipart, Path, Query, State};
use axum::http::header;
use axum::response::{IntoResponse, Response};
use axum::routing::{delete, get, post, put};
use axum::{Extension, Json, Router};
use indexmap::IndexMap;
use once_cell::sync::Lazy;
use regex::Regex;
use rusqlite::OptionalExtension;
use serde_json::{json, Map, Value};

use crate::pylog as logger;
use crate::routes::auth_adapter;
use crate::routes::email_helpers::{
    apply_email_style_mechanics, attach_compose_uploads, cleanup_compose_uploads,
    compose_uploads_dir, decode_header, detect_drafts_folder, detect_sent_folder,
    extract_attachment_text, extract_attachment_to_disk, extract_first_plaintext, extract_html,
    extract_reply, extract_text, get_email_config, imap_connect, list_attachments_from_msg,
    parse_message, scheduled_db,
    send_smtp_message, strip_think, with_imap, EmailConfig, ExtractStyleRequest, ImapSession,
    ParsedMessage, SendEmailRequest, EMAIL_REPLY_SYS_PROMPT_BASE,
};
use crate::routes::{AppState, CurrentUser, HttpException};

/// `ODYSSEUS_MAIL_ORIGIN = "odysseus-ui"`.
const ODYSSEUS_MAIL_ORIGIN: &str = "odysseus-ui";

// ===========================================================================
// Module-private auth bridge — the `Depends(require_owner / require_user)`
// closures resolve through `auth_adapter`. The handlers pull the stamped user +
// client host out of the axum extractors and call these.
// ===========================================================================

/// Resolve the optional stamped `CurrentUser` to `Option<String>`.
fn user_str(user: Option<Extension<CurrentUser>>) -> Option<String> {
    user.map(|Extension(CurrentUser(u))| u)
}

/// `client_host` from `ConnectInfo<SocketAddr>` (the `request.client.host` analogue).
fn host_str(ci: Option<ConnectInfo<SocketAddr>>) -> Option<String> {
    ci.map(|ConnectInfo(a)| a.ip().to_string())
}

// ===========================================================================
// In-process list / read caches (the closure-captured `_LIST_CACHE` /
// `_READ_CACHE`). Process-global TTL maps. The IMAP pool + prefetch are not
// reproduced (see module docs).
// ===========================================================================

/// `_LIST_TTL = 8.0`.
const LIST_TTL: Duration = Duration::from_secs(8);
/// `_READ_TTL = 30 * 60.0` (30 minutes).
const READ_TTL: Duration = Duration::from_secs(30 * 60);

type ListKey = (String, String, String, i64, i64, String, i64, String);
type ReadKey = (String, String, String, String);

static LIST_CACHE: Lazy<Mutex<HashMap<ListKey, (Instant, Value)>>> =
    Lazy::new(|| Mutex::new(HashMap::new()));
static READ_CACHE: Lazy<Mutex<HashMap<ReadKey, (Instant, Value)>>> =
    Lazy::new(|| Mutex::new(HashMap::new()));

// ── Warm-prefetch state (the closure-captured `_WARMING_READS` set + the
// `_WARM_*` tuning constants). After a list load, kick off a background read of
// the top recent visible UID so a subsequent click lands in the read cache.
/// `_WARM_READ_LIMIT = 1` — at most one warm read scheduled per list load.
const WARM_READ_LIMIT: usize = 1;
/// `_WARM_MAX_BYTES = 128 * 1024` — skip bodies larger than this.
const WARM_MAX_BYTES: u32 = 128 * 1024;
/// `_WARM_RECENT_SECONDS = 7 * 24 * 60 * 60` — skip messages older than a week.
const WARM_RECENT_SECONDS: f64 = 7.0 * 24.0 * 60.0 * 60.0;
/// `_WARMING_READS = set()` — the in-flight warm read-cache keys, so two list
/// loads don't both kick off the same warm read.
static WARMING_READS: Lazy<Mutex<HashSet<ReadKey>>> = Lazy::new(|| Mutex::new(HashSet::new()));

// mirrors the Python cache-key build: `_list_cache_key(account_id, folder, filter_,
// limit, offset, from_addr) + (has_attachments, owner)` (email_routes.py:491,935) —
// the 8 components are the faithful key tuple, not bundled into a struct.
#[allow(clippy::too_many_arguments)]
fn list_cache_key(
    account_id: &str,
    folder: &str,
    filter_: &str,
    limit: i64,
    offset: i64,
    from_addr: &str,
    has_attachments: bool,
    owner: &str,
) -> ListKey {
    (
        account_id.to_string(),
        folder.to_string(),
        filter_.to_string(),
        limit,
        offset,
        from_addr.to_string(),
        has_attachments as i64,
        owner.to_string(),
    )
}

fn read_cache_key(account_id: &str, folder: &str, uid: &str, owner: &str) -> ReadKey {
    (
        account_id.to_string(),
        folder.to_string(),
        uid.to_string(),
        owner.to_string(),
    )
}

fn list_cache_get(key: &ListKey) -> Option<Value> {
    let mut g = LIST_CACHE.lock().ok()?;
    if let Some((exp, v)) = g.get(key) {
        if *exp >= Instant::now() {
            return Some(v.clone());
        }
        g.remove(key);
    }
    None
}

fn list_cache_put(key: ListKey, value: Value) {
    if let Ok(mut g) = LIST_CACHE.lock() {
        g.insert(key, (Instant::now() + LIST_TTL, value));
        if g.len() > 64 {
            // Cap size — drop the oldest-expiring half (the Python keeps the last 32).
            let mut keys: Vec<ListKey> = g.keys().cloned().collect();
            let drop_n = keys.len().saturating_sub(32);
            keys.truncate(drop_n);
            for k in keys {
                g.remove(&k);
            }
        }
    }
}

/// `_invalidate_list_cache(account_id, folder)` — drop entries the mutation
/// stale-ed. `None`/`None` clears everything.
fn invalidate_list_cache(account_id: Option<&str>, folder: Option<&str>) {
    if let Ok(mut g) = LIST_CACHE.lock() {
        if account_id.is_none() && folder.is_none() {
            g.clear();
            return;
        }
        let want_acct = account_id.unwrap_or("");
        g.retain(|k, _| {
            let acct_ok = account_id.is_none() || k.0 == want_acct;
            let folder_ok = folder.is_none_or(|f| k.1 == f);
            !(acct_ok && folder_ok)
        });
    }
}

fn read_cache_get(key: &ReadKey) -> Option<Value> {
    let mut g = READ_CACHE.lock().ok()?;
    if let Some((exp, v)) = g.get(key) {
        if *exp >= Instant::now() {
            return Some(v.clone());
        }
        g.remove(key);
    }
    None
}

fn read_cache_put(key: ReadKey, value: Value) {
    if let Ok(mut g) = READ_CACHE.lock() {
        g.insert(key, (Instant::now() + READ_TTL, value));
        if g.len() > 256 {
            let mut keys: Vec<ReadKey> = g.keys().cloned().collect();
            let drop_n = keys.len().saturating_sub(128);
            keys.truncate(drop_n);
            for k in keys {
                g.remove(&k);
            }
        }
    }
}

// ===========================================================================
// Free helpers (module-level functions in the Python — outside the closure).
// ===========================================================================

/// `email.utils.parseaddr(s)` — split `"Name <addr>"` into `(name, addr)`. A bare
/// address yields `("", addr)`; a bare display name yields `(name, "")` only when
/// it contains no `@`. Faithful enough for the From/To rendering the routes do.
fn parseaddr(s: &str) -> (String, String) {
    let s = s.trim();
    if let (Some(lt), Some(gt)) = (s.rfind('<'), s.rfind('>')) {
        if lt < gt {
            let name = s[..lt].trim().trim_matches('"').trim().to_string();
            let addr = s[lt + 1..gt].trim().to_string();
            return (name, addr);
        }
    }
    if s.contains('@') {
        (String::new(), s.to_string())
    } else {
        (s.to_string(), String::new())
    }
}

/// `email.utils.getaddresses(fieldvalues)` — split one or more address-list header
/// strings (each possibly holding several `Name <addr>` entries) into `(name, addr)`
/// pairs. The key fidelity requirement over a naive `split(',')` is that a comma
/// INSIDE a quoted display name (the canonical Outlook `"Smith, John" <j@corp.com>`)
/// must NOT split the entry. We honor double-quoted regions and angle-bracketed
/// addresses, then run each split entry through [`parseaddr`].
fn getaddresses<'a>(fieldvalues: impl IntoIterator<Item = &'a str>) -> Vec<(String, String)> {
    let mut out: Vec<(String, String)> = Vec::new();
    for field in fieldvalues {
        // Split on commas that are NOT inside a double-quoted string and NOT inside
        // an angle-bracketed address (`<...>` shouldn't contain a comma, but guard
        // it anyway to match Python's address-grammar parse).
        let mut entries: Vec<String> = Vec::new();
        let mut cur = String::new();
        let mut in_quote = false;
        let mut in_angle = false;
        for ch in field.chars() {
            match ch {
                '"' => {
                    in_quote = !in_quote;
                    cur.push(ch);
                }
                '<' if !in_quote => {
                    in_angle = true;
                    cur.push(ch);
                }
                '>' if !in_quote => {
                    in_angle = false;
                    cur.push(ch);
                }
                ',' if !in_quote && !in_angle => {
                    entries.push(std::mem::take(&mut cur));
                }
                _ => cur.push(ch),
            }
        }
        entries.push(cur);
        for entry in entries {
            let entry = entry.trim();
            if entry.is_empty() {
                continue;
            }
            out.push(parseaddr(entry));
        }
    }
    out
}

/// `_envelope_recipients(*fields)` — extract the bare SMTP envelope addresses from
/// one or more To/Cc/Bcc header strings via the [`getaddresses`] grammar parse (so
/// display names containing a comma don't corrupt the recipient list). Empty fields
/// are skipped (`[f for f in fields if f]`), and only non-empty trimmed addresses
/// are returned.
fn envelope_recipients(fields: &[&str]) -> Vec<String> {
    let non_empty: Vec<&str> = fields.iter().copied().filter(|f| !f.is_empty()).collect();
    let mut out: Vec<String> = Vec::new();
    for (_name, addr) in getaddresses(non_empty) {
        let addr = addr.trim();
        if !addr.is_empty() {
            out.push(addr.to_string());
        }
    }
    out
}

/// `email.utils.make_msgid(domain="odysseus.local")`.
fn make_msgid(domain: &str) -> String {
    let id = uuid::Uuid::new_v4().simple().to_string();
    let pid = std::process::id();
    format!("<{id}.{pid}@{domain}>")
}

/// `datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")` — the RFC822 Date.
fn rfc822_date_now() -> String {
    chrono::Utc::now()
        .format("%a, %d %b %Y %H:%M:%S +0000")
        .to_string()
}

/// Render a naive datetime as Python `datetime.isoformat()` would: `T`-separated,
/// and only including fractional seconds when the microsecond field is non-zero
/// (`.6f`). Used by `/schedule` to normalize `send_at` to a naive-UTC ISO string
/// the poller can lexically compare against `datetime.utcnow().isoformat()`.
fn naive_isoformat(dt: chrono::NaiveDateTime) -> String {
    use chrono::Timelike;
    if dt.nanosecond() == 0 {
        dt.format("%Y-%m-%dT%H:%M:%S").to_string()
    } else {
        // `%.6f` -> ".ffffff" (6 digits, leading dot) — matches isoformat's microseconds.
        dt.format("%Y-%m-%dT%H:%M:%S%.6f").to_string()
    }
}

/// `_smtp_ready(cfg)` — true iff smtp_host/user/password are all set.
fn smtp_ready(cfg: &EmailConfig) -> bool {
    !cfg.smtp_host.is_empty() && !cfg.smtp_user.is_empty() && !cfg.smtp_password.is_empty()
}

static ODYSSEUS_KIND_SANITIZE_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"[^A-Za-z0-9_.-]").unwrap());
static ODYSSEUS_REF_SANITIZE_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"[^A-Za-z0-9_.:-]").unwrap());
/// `summarize_email`'s reasoning_content bullet detector — Python's
/// `re.match(r"^[-•*]\s+|^\d+[.)]\s+", stripped)` (anchored at the line start).
static SUMMARY_BULLET_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^[-•*]\s+|^\d+[.)]\s+").unwrap());

/// The Odysseus marker headers `_apply_odysseus_headers` adds to an outgoing
/// message. Returned as ordered `(name, value)` pairs the SMTP builder appends.
fn odysseus_headers(kind: Option<&str>, ref_id: Option<&str>) -> Vec<(String, String)> {
    let mut out = vec![("X-Odysseus-Origin".to_string(), ODYSSEUS_MAIL_ORIGIN.to_string())];
    if let Some(kind) = kind.filter(|k| !k.is_empty()) {
        let v: String = ODYSSEUS_KIND_SANITIZE_RE
            .replace_all(kind, "-")
            .chars()
            .take(64)
            .collect();
        out.push(("X-Odysseus-Kind".to_string(), v));
    }
    if let Some(ref_id) = ref_id.filter(|r| !r.is_empty()) {
        let v: String = ODYSSEUS_REF_SANITIZE_RE
            .replace_all(ref_id, "-")
            .chars()
            .take(128)
            .collect();
        out.push(("X-Odysseus-Ref".to_string(), v));
    }
    out
}

/// `_resolve_send_config(account_id, owner)` — resolve an account for outbound
/// SMTP. Explicit account -> only that one (clear error if no SMTP). No account +
/// receive-only default -> first SMTP-capable account owned by the same user.
pub(crate) fn resolve_send_config(
    account_id: Option<&str>,
    owner: &str,
) -> Result<EmailConfig, String> {
    let cfg = get_email_config(account_id, owner);
    if smtp_ready(&cfg) {
        return Ok(cfg);
    }
    if let Some(aid) = account_id.filter(|a| !a.is_empty()) {
        let label = if cfg.account_name.is_empty() {
            aid.to_string()
        } else {
            cfg.account_name.clone()
        };
        return Err(format!("Email account {label} has no SMTP configured"));
    }
    // Iterate this user's enabled accounts (is_default desc, created_at asc).
    let _ = (|| -> Option<()> {
        let conn = crate::core::database::session_local().ok()?;
        let (sql, params): (String, Vec<String>) = if owner.is_empty() {
            (
                "SELECT id FROM email_accounts WHERE enabled = 1 \
                 ORDER BY is_default DESC, created_at ASC"
                    .to_string(),
                vec![],
            )
        } else {
            (
                "SELECT id FROM email_accounts WHERE enabled = 1 \
                 AND (owner = ?1 OR ((owner IS NULL OR owner = '') \
                      AND (imap_user = ?1 OR from_address = ?1))) \
                 ORDER BY is_default DESC, created_at ASC"
                    .to_string(),
                vec![owner.to_string()],
            )
        };
        let mut stmt = conn.prepare(&sql).ok()?;
        let ids: Vec<String> = stmt
            .query_map(rusqlite::params_from_iter(params.iter()), |r| {
                r.get::<_, String>(0)
            })
            .ok()?
            .filter_map(Result::ok)
            .collect();
        for id in ids {
            let trial = get_email_config(Some(&id), owner);
            if smtp_ready(&trial) {
                return Some(()); // marker — captured below via shadow
            }
        }
        None
    })();
    // Re-walk to actually RETURN the first SMTP-capable trial (the closure above
    // can't return the cfg out cleanly; do the loop here for the value).
    if let Ok(conn) = crate::core::database::session_local() {
        let (sql, params): (String, Vec<String>) = if owner.is_empty() {
            (
                "SELECT id FROM email_accounts WHERE enabled = 1 \
                 ORDER BY is_default DESC, created_at ASC"
                    .to_string(),
                vec![],
            )
        } else {
            (
                "SELECT id FROM email_accounts WHERE enabled = 1 \
                 AND (owner = ?1 OR ((owner IS NULL OR owner = '') \
                      AND (imap_user = ?1 OR from_address = ?1))) \
                 ORDER BY is_default DESC, created_at ASC"
                    .to_string(),
                vec![owner.to_string()],
            )
        };
        if let Ok(mut stmt) = conn.prepare(&sql) {
            if let Ok(rows) = stmt.query_map(rusqlite::params_from_iter(params.iter()), |r| {
                r.get::<_, String>(0)
            }) {
                for id in rows.filter_map(Result::ok) {
                    let trial = get_email_config(Some(&id), owner);
                    if smtp_ready(&trial) {
                        return Ok(trial);
                    }
                }
            }
        }
    }
    Err("No SMTP-capable email account configured".to_string())
}

/// `_email_tag_owner_aliases(account_id, owner)` — the owner-scope alias list used
/// by the tag lookups (review C2/H8): owner + the resolved account's imap_user /
/// from_address / smtp_user, deduped, never empty (`[""]` floor).
fn email_tag_owner_aliases(account_id: Option<&str>, owner: &str) -> Vec<String> {
    let mut aliases: Vec<String> = vec![owner.to_string()];
    // Best-effort, whole block is try/except -> pass.
    let _ = (|| -> Option<()> {
        let conn = crate::core::database::session_local().ok()?;
        let mut resolved = account_id.map(str::to_string).filter(|s| !s.is_empty());
        if resolved.is_none() {
            let cfg = get_email_config(None, owner);
            resolved = cfg.account_id.clone();
            aliases.push(cfg.imap_user.clone());
            aliases.push(cfg.smtp_user.clone());
            aliases.push(cfg.from_address.clone());
        }
        if let Some(aid) = resolved {
            if let Some((o, iu, fa)) = conn
                .query_row(
                    "SELECT owner, imap_user, from_address FROM email_accounts WHERE id = ?1",
                    rusqlite::params![aid],
                    |r| {
                        Ok((
                            r.get::<_, Option<String>>(0)?,
                            r.get::<_, Option<String>>(1)?,
                            r.get::<_, Option<String>>(2)?,
                        ))
                    },
                )
                .optional()
                .ok()
                .flatten()
            {
                aliases.push(o.unwrap_or_default());
                aliases.push(iu.unwrap_or_default());
                aliases.push(fa.unwrap_or_default());
            }
        }
        Some(())
    })();
    let mut out: Vec<String> = Vec::new();
    for a in aliases {
        let a = a.trim().to_string();
        if !out.contains(&a) {
            out.push(a);
        }
    }
    if out.is_empty() {
        out.push(String::new());
    }
    out
}

/// `_email_tag_owner_clause(account_id, owner)` — the `(clause, params)` pair the
/// email-tag queries use to owner-scope their `WHERE`. Faithful to the Python:
/// the alias list (from [`email_tag_owner_aliases`]) becomes a placeholder set,
/// and in CONFIGURED multi-user mode (`owner` non-empty) legacy `owner=''`/NULL
/// rows are NOT treated as visible to everyone — only `owner IN (...)` matches.
/// In single-user / unconfigured mode (`owner` empty) the legacy `owner IS NULL`
/// rows stay visible, matching `(owner IN (...) OR owner IS NULL)`.
fn email_tag_owner_clause(account_id: Option<&str>, owner: &str) -> (String, Vec<String>) {
    let aliases = email_tag_owner_aliases(account_id, owner);
    let placeholders = vec!["?"; aliases.len()].join(",");
    if !owner.is_empty() {
        (format!("owner IN ({placeholders})"), aliases)
    } else {
        (format!("(owner IN ({placeholders}) OR owner IS NULL)"), aliases)
    }
}

/// `_md_to_email_html(text)` — render compose markdown to a SAFE HTML fragment.
/// Escape FIRST, then layer bold/italic/strike/code/links/headings/lists.
fn md_to_email_html(text: &str) -> String {
    fn inline(s: &str) -> String {
        let s = html_escape::encode_text(s).into_owned();
        let s = BOLD_RE.replace_all(&s, "<strong>$1</strong>").into_owned();
        let s = ITALIC_RE.replace_all(&s, "<em>$1</em>").into_owned();
        let s = STRIKE_RE.replace_all(&s, "<del>$1</del>").into_owned();
        let s = CODE_RE.replace_all(&s, "<code>$1</code>").into_owned();
        LINK_RE.replace_all(&s, r#"<a href="$2">$1</a>"#).into_owned()
    }
    let mut parts: Vec<String> = Vec::new();
    let mut in_ul = false;
    let mut in_ol = false;
    for ln in text.split('\n') {
        if let Some(c) = MD_H_RE.captures(ln) {
            if in_ul {
                parts.push("</ul>".to_string());
                in_ul = false;
            }
            if in_ol {
                parts.push("</ol>".to_string());
                in_ol = false;
            }
            let lvl = c.get(1).unwrap().as_str().len();
            parts.push(format!("<h{lvl}>{}</h{lvl}>", inline(c.get(2).unwrap().as_str())));
        } else if let Some(c) = MD_UL_RE.captures(ln) {
            if in_ol {
                parts.push("</ol>".to_string());
                in_ol = false;
            }
            if !in_ul {
                parts.push("<ul>".to_string());
                in_ul = true;
            }
            parts.push(format!("<li>{}</li>", inline(c.get(1).unwrap().as_str())));
        } else if let Some(c) = MD_OL_RE.captures(ln) {
            if in_ul {
                parts.push("</ul>".to_string());
                in_ul = false;
            }
            if !in_ol {
                parts.push("<ol>".to_string());
                in_ol = true;
            }
            parts.push(format!("<li>{}</li>", inline(c.get(1).unwrap().as_str())));
        } else {
            if in_ul {
                parts.push("</ul>".to_string());
                in_ul = false;
            }
            if in_ol {
                parts.push("</ol>".to_string());
                in_ol = false;
            }
            parts.push(format!("{}<br>", inline(ln)));
        }
    }
    if in_ul {
        parts.push("</ul>".to_string());
    }
    if in_ol {
        parts.push("</ol>".to_string());
    }
    format!("<html><body>{}</body></html>", parts.join("\n"))
}

static BOLD_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\*\*([^*]+)\*\*").unwrap());
static ITALIC_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\*([^*]+)\*").unwrap());
static STRIKE_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"~~([^~]+)~~").unwrap());
static CODE_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"`([^`]+)`").unwrap());
static LINK_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"\[([^\]]+)\]\((https?://[^)\s]+)\)").unwrap());
static MD_H_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^(#{1,3})\s+(.*)$").unwrap());
static MD_UL_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^\s*[-*]\s+(.*)$").unwrap());
static MD_OL_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^\s*\d+\.\s+(.*)$").unwrap());

/// `_sanitize_email_html(raw)` — allowlist-sanitize client-supplied compose HTML,
/// returning `<html><body>…</body></html>` or `None`. Uses `scraper` (the same
/// bs4-replacement [`crate::src::email_thread_parser`] uses) to walk the DOM and
/// re-emit only the allowed tags, dropping all attributes except a safe href.
fn sanitize_email_html(raw: &str) -> Option<String> {
    use scraper::Html;
    if raw.is_empty() {
        return None;
    }
    let frag = Html::parse_fragment(raw);
    let mut out = String::new();
    // Walk the parsed tree from the root, emitting only allowed tags.
    for child in frag.tree.root().children() {
        emit_node(child, &mut out);
    }
    let inner = out.trim().to_string();
    if inner.is_empty() {
        return None;
    }
    Some(format!("<html><body>{inner}</body></html>"))
}

/// Tags the WYSIWYG email composer may legitimately produce.
const EMAIL_ALLOWED_TAGS: &[&str] = &[
    "b", "strong", "i", "em", "u", "s", "strike", "del", "a", "br", "p", "div", "ul", "ol", "li",
    "blockquote", "span", "h1", "h2", "h3", "code", "pre",
];

static HREF_OK_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?i)^(https?:|mailto:)").unwrap());

/// Recursively emit a sanitized node tree (the `_EmailHtmlSanitizer` analogue).
fn emit_node(nref: ego_tree::NodeRef<scraper::Node>, out: &mut String) {
    use scraper::Node;
    match nref.value() {
        Node::Text(t) => out.push_str(&html_escape::encode_text(&**t)),
        Node::Element(el) => {
            let tag = el.name();
            if tag == "script" || tag == "style" {
                return; // discard subtree entirely
            }
            if tag == "br" {
                out.push_str("<br>");
                return; // br has no meaningful children
            }
            let allowed = EMAIL_ALLOWED_TAGS.contains(&tag);
            if allowed {
                if tag == "a" {
                    let href = el
                        .attr("href")
                        .map(str::trim)
                        .filter(|h| HREF_OK_RE.is_match(h))
                        .unwrap_or("");
                    if !href.is_empty() {
                        out.push_str(&format!(
                            r#"<a href="{}" target="_blank" rel="noopener noreferrer">"#,
                            html_escape::encode_double_quoted_attribute(href)
                        ));
                    } else {
                        out.push_str("<a>");
                    }
                } else {
                    out.push_str(&format!("<{tag}>"));
                }
            }
            for child in nref.children() {
                emit_node(child, out);
            }
            if allowed {
                out.push_str(&format!("</{tag}>"));
            }
        }
        _ => {
            for child in nref.children() {
                emit_node(child, out);
            }
        }
    }
}

// ===========================================================================
// `setup_email_routes()` — the router factory (app.py:590).
// ===========================================================================

/// `setup_email_routes()` — assemble the `/api/email/...` router. The Python
/// calls `_start_poller()` first; the Rust pollers are spawned by the integration
/// `run()` (the `email_pollers` start fn), so this factory only builds the router.
/// The `APIRouter(prefix="/api/email")` prefix is baked into each absolute path.
pub fn setup_email_routes() -> Router<AppState> {
    Router::new()
        .route("/api/email/list", get(list_emails))
        .route("/api/email/:uid/unflag-spam", post(unflag_spam))
        .route("/api/email/contacts", get(list_contacts))
        .route("/api/email/search", get(search_emails))
        .route("/api/email/read/:uid", get(read_email_by_uid))
        .route("/api/email/attachments/:uid", get(list_attachments))
        .route(
            "/api/email/attachment/:uid/:index",
            get(download_attachment),
        )
        .route(
            "/api/email/attachment-as-doc/:uid/:index",
            post(attachment_as_doc),
        )
        .route(
            "/api/email/attachment-path/:uid/:index",
            post(get_attachment_path),
        )
        .route("/api/email/mark-unread/:uid", post(mark_unread))
        .route("/api/email/mark-read/:uid", post(mark_read))
        .route("/api/email/archive/:uid", post(archive_email))
        .route("/api/email/delete/:uid", delete(delete_email))
        .route(
            "/api/email/delete-permanent/:uid",
            delete(delete_email_permanent),
        )
        .route(
            "/api/email/odysseus/reminders",
            delete(delete_odysseus_reminder_emails),
        )
        .route("/api/email/move/:uid", post(move_email))
        .route("/api/email/folders", get(list_folders))
        .route("/api/email/mark-answered/:uid", post(mark_answered))
        .route("/api/email/clear-answered/:uid", post(clear_answered))
        .route("/api/email/compose-upload", post(compose_upload))
        .route(
            "/api/email/compose-upload/:token",
            delete(delete_compose_upload),
        )
        .route("/api/email/schedule", post(schedule_email))
        .route("/api/email/scheduled", get(list_scheduled))
        .route("/api/email/scheduled/:sid", delete(cancel_scheduled))
        .route("/api/email/resolve-contact", get(resolve_contact))
        .route("/api/email/send", post(send_email))
        .route("/api/email/draft", post(save_draft))
        .route("/api/email/extract-style", post(extract_writing_style))
        .route("/api/email/summarize", post(summarize_email))
        .route("/api/email/ai-reply", post(ai_reply))
        .route(
            "/api/email/style",
            get(get_writing_style).put(update_writing_style),
        )
        .route(
            "/api/email/config",
            get(get_email_config_route).put(update_email_config),
        )
        .route("/api/email/urgency-state", get(get_email_urgency_state))
        .route(
            "/api/email/accounts",
            get(list_email_accounts_route).post(create_email_account),
        )
        .route(
            "/api/email/accounts/test",
            post(test_account_config),
        )
        .route(
            "/api/email/accounts/:account_id",
            put(update_email_account).delete(delete_email_account),
        )
        .route(
            "/api/email/accounts/:account_id/set-default",
            post(set_default_account),
        )
}

// ===========================================================================
// Shared IMAP helpers used across handlers (the closure-level functions that
// operate on a live `ImapSession`). Run inside `with_imap` / spawn_blocking.
// ===========================================================================

/// `_uid_exists(conn, uid)` — does a UID FETCH succeed (UID present)?
fn uid_exists(conn: &mut ImapSession, uid: &str) -> bool {
    match conn.uid_fetch(uid, "(UID)") {
        Ok(fetches) => fetches.iter().any(|f| f.uid.is_some()),
        Err(_) => false,
    }
}

/// `_store_email_flag(conn, uid, flag, add)` — STORE +/-FLAGS, preferring a
/// UID STORE when the UID exists, else a sequence STORE.
fn store_email_flag(conn: &mut ImapSession, uid: &str, flag: &str, add: bool) -> bool {
    let op = if add { "+FLAGS" } else { "-FLAGS" };
    let query = format!("{op} ({flag})");
    if uid_exists(conn, uid) {
        conn.uid_store(uid, &query).is_ok()
    } else {
        conn.store(uid, &query).is_ok()
    }
}

/// `_folder_name_from_list_line` analogue — the `imap::Name` already decodes the
/// folder name, so this is just `name.to_string()`.
fn list_imap_folders(conn: &mut ImapSession) -> Vec<(String, Vec<String>)> {
    match conn.list(None, Some("*")) {
        Ok(names) => names
            .iter()
            .map(|n| {
                let attrs = n
                    .attributes()
                    .iter()
                    .map(|a| format!("{a:?}"))
                    .collect::<Vec<_>>();
                (n.name().to_string(), attrs)
            })
            .collect(),
        Err(_) => Vec::new(),
    }
}

/// `_folder_role_from_name(name)`.
fn folder_role_from_name(name: &str) -> &'static str {
    let lower = name.to_lowercase();
    if lower.contains("trash") || lower.contains("bin") || lower.contains("deleted") {
        "trash"
    } else if lower.contains("spam") || lower.contains("junk") {
        "junk"
    } else if lower.contains("archive") || lower.contains("all mail") {
        "archive"
    } else {
        ""
    }
}

/// `_resolve_mail_folder(conn, preferred, role)` — resolve provider-specific
/// folder names (Gmail's `[Gmail]/Bin`/`Spam`/`All Mail`) by LIST attribute then
/// by candidate-name match. Falls back to `preferred`.
fn resolve_mail_folder(conn: &mut ImapSession, preferred: &str, role: &str) -> String {
    let folders = list_imap_folders(conn);
    let names: Vec<&String> = folders.iter().map(|(n, _)| n).collect();
    if !preferred.is_empty() && names.iter().any(|n| n.as_str() == preferred) {
        return preferred.to_string();
    }
    let role_flags: &[&str] = match role {
        "trash" => &["Trash"],
        "archive" => &["Archive", "All"],
        "junk" => &["Junk"],
        _ => &[],
    };
    for (name, attrs) in &folders {
        if attrs
            .iter()
            .any(|a| role_flags.iter().any(|rf| a.trim_start_matches('\\').eq_ignore_ascii_case(rf)))
        {
            return name.clone();
        }
    }
    let candidates: &[&str] = match role {
        "trash" => &[
            "Trash",
            "[Gmail]/Trash",
            "[Google Mail]/Trash",
            "Bin",
            "[Gmail]/Bin",
            "Deleted Messages",
            "Deleted Items",
        ],
        "archive" => &[
            "Archive",
            "Archives",
            "[Gmail]/All Mail",
            "[Google Mail]/All Mail",
            "All Mail",
        ],
        "junk" => &["Junk", "Spam", "[Gmail]/Spam", "[Google Mail]/Spam"],
        _ => &[],
    };
    let lower_map: HashMap<String, String> = names
        .iter()
        .map(|n| (n.to_lowercase(), n.to_string()))
        .collect();
    for candidate in candidates {
        if let Some(found) = lower_map.get(&candidate.to_lowercase()) {
            return found.clone();
        }
    }
    preferred.to_string()
}

/// `_move_email_message(conn, uid, dest, role)` — COPY to dest, flag source
/// `\Deleted`, expunge. The `imap` crate's `mv`/`uid_mv` (the IMAP MOVE command)
/// may be unsupported; mirror the Python's COPY+STORE+EXPUNGE path which works
/// everywhere. Returns false on any failure.
fn move_email_message(conn: &mut ImapSession, uid: &str, dest: &str, role: &str) -> bool {
    let resolved_role = if role.is_empty() {
        folder_role_from_name(dest)
    } else {
        role
    };
    let dest = resolve_mail_folder(conn, dest, resolved_role);
    // `mv`/`uid_mv` AUTO-QUOTE the mailbox (`validate_str` -> `quote!`), so pass the
    // bare name there. But `copy`/`uid_copy` do NOT auto-quote — they emit the mailbox
    // raw — so we must quote the destination ourselves to match Python's `_q(dest)`
    // (otherwise a multiword dest like `[Gmail]/Sent Mail` produces a broken command).
    let dest_q = crate::routes::email_helpers::q(&dest);
    if uid_exists(conn, uid) {
        // Try UID MOVE first (`conn.uid("MOVE", ...)`).
        if conn.uid_mv(uid, &dest).is_ok() {
            return true;
        }
        if conn.uid_copy(uid, &dest_q).is_err() {
            return false;
        }
        if conn.uid_store(uid, "+FLAGS (\\Deleted)").is_err() {
            return false;
        }
    } else {
        if conn.copy(uid, &dest_q).is_err() {
            return false;
        }
        if conn.store(uid, "+FLAGS (\\Deleted)").is_err() {
            return false;
        }
    }
    conn.expunge().is_ok()
}

/// Reconstruct Python's space-joined FLAGS string from the typed `Fetch.flags()`.
fn flags_string(fetch: &imap::types::Fetch) -> String {
    fetch
        .flags()
        .iter()
        .map(|f| f.to_string())
        .collect::<Vec<_>>()
        .join(" ")
}

/// Parse an RFC2822 Date header to `(iso_string, epoch_seconds)`, normalising
/// tz-naive parses to UTC (the Python's `parsedate_to_datetime` + tz fix).
fn parse_email_date(date_str: &str) -> (String, f64) {
    if date_str.is_empty() {
        return (String::new(), 0.0);
    }
    if let Ok(dt) = chrono::DateTime::parse_from_rfc2822(date_str) {
        return (dt.to_rfc3339(), dt.timestamp() as f64 + dt.timestamp_subsec_micros() as f64 / 1e6);
    }
    // Try without the trailing "(Comment)" some servers append.
    if let Some(idx) = date_str.find('(') {
        let trimmed = date_str[..idx].trim();
        if let Ok(dt) = chrono::DateTime::parse_from_rfc2822(trimmed) {
            return (dt.to_rfc3339(), dt.timestamp() as f64);
        }
    }
    (String::new(), 0.0)
}

/// `"multipart/mixed" in ct.lower() or "multipart/related" in ct.lower()`.
fn ct_has_attachments(ct: &str) -> bool {
    let l = ct.to_lowercase();
    l.contains("multipart/mixed") || l.contains("multipart/related")
}

/// Open a scheduled-DB connection (best-effort), the `sqlite3.connect(SCHEDULED_DB)`
/// analogue used by the tag/summary lookups.
fn scheduled_conn() -> Option<rusqlite::Connection> {
    rusqlite::Connection::open(scheduled_db()).ok()
}

// ===========================================================================
// GET /api/email/list
// ===========================================================================

/// Per-email row the list endpoint builds (mirrors the Python dict; serialized
/// with `serde_json` `preserve_order` so JSON key order matches).
struct ListRow {
    seq: String,
    message_id: String,
    subject: String,
    from_name: String,
    from_address: String,
    to: String,
    cc: String,
    date: String,
    date_display: String,
    date_epoch: f64,
    size: u32,
    is_read: bool,
    is_answered: bool,
    is_flagged: bool,
    flags: String,
    has_attachments: bool,
    tags: Vec<Value>,
    is_spam_verdict: bool,
    cached_summary: Option<String>,
}

/// IMAP SEARCH-quote: `'"' + value.replace("\\","\\\\").replace('"','\\"') + '"'`.
fn imap_search_quote(value: &str) -> String {
    format!("\"{}\"", value.replace('\\', "\\\\").replace('"', "\\\""))
}

/// `_list_emails_sync(...)` — the synchronous IMAP work. Returns the response dict.
#[allow(clippy::too_many_arguments)]
fn list_emails_sync(
    folder: &str,
    limit: i64,
    offset: i64,
    filter_: &str,
    account_id: Option<&str>,
    from_addr: Option<&str>,
    has_attachments_only: bool,
    owner: &str,
) -> Value {
    let result: Result<Value, String> = (|| {
        let mut conn = imap_connect(account_id, owner)?;
        // readonly select -> EXAMINE.
        if conn.examine(folder).is_err() {
            let _ = conn.logout();
            return Ok(json!({
                "emails": [], "total": 0, "folder": folder,
                "error": format!("Folder not found: {folder}"),
            }));
        }

        let from_clause = match from_addr {
            Some(fa) if !fa.is_empty() => {
                let safe = fa.replace('\\', "\\\\").replace('"', "\\\"");
                format!(" FROM \"{safe}\"")
            }
            _ => String::new(),
        };

        // Build the SEARCH and resolve the matching sequence numbers.
        let mut seqs: Vec<u32>;
        match filter_ {
            "unread" => seqs = search_seqs(&mut conn, &format!("(UNSEEN{from_clause})")),
            "favorites" => seqs = search_seqs(&mut conn, &format!("(FLAGGED{from_clause})")),
            "unanswered" => {
                seqs = search_seqs(&mut conn, &format!("(UNSEEN UNANSWERED{from_clause})"))
            }
            "undone" => seqs = search_seqs(&mut conn, &format!("(UNANSWERED{from_clause})")),
            "reminders" => {
                seqs = search_seqs(
                    &mut conn,
                    &format!(
                        "(OR HEADER X-Odysseus-Kind \"reminder\" SUBJECT \"Reminder (Odysseus):\"{from_clause})"
                    ),
                )
            }
            "pending_30d" => {
                let since = (chrono::Utc::now() - chrono::Duration::days(30))
                    .format("%d-%b-%Y")
                    .to_string();
                seqs = search_seqs(&mut conn, &format!("(UNANSWERED SINCE \"{since}\"{from_clause})"))
            }
            "stale_30d" => {
                let before = (chrono::Utc::now() - chrono::Duration::days(30))
                    .format("%d-%b-%Y")
                    .to_string();
                seqs = search_seqs(&mut conn, &format!("(UNANSWERED BEFORE \"{before}\"{from_clause})"))
            }
            f if f.starts_with("tag:") => {
                seqs = match resolve_tag_filter(&mut conn, f, folder, account_id, owner, &from_clause) {
                    Some(s) => s,
                    None => {
                        let _ = conn.logout();
                        return Ok(json!({"emails": [], "total": 0, "folder": folder}));
                    }
                };
            }
            _ if !from_clause.is_empty() => {
                seqs = search_seqs(&mut conn, &format!("({})", from_clause.trim()))
            }
            _ => seqs = search_seqs(&mut conn, "ALL"),
        }

        if seqs.is_empty() {
            let _ = conn.logout();
            return Ok(json!({"emails": [], "total": 0, "folder": folder}));
        }

        // Newest first (descending sequence number), then paginate.
        seqs.sort_unstable();
        seqs.reverse();
        let total_all = seqs.len() as i64;
        let mut total = total_all;
        let window: Vec<u32> = if has_attachments_only {
            let scan_window = std::cmp::max(400, (offset + limit * 8) as usize);
            seqs.iter().take(scan_window).copied().collect()
        } else {
            seqs.iter()
                .skip(offset.max(0) as usize)
                .take(limit.max(0) as usize)
                .copied()
                .collect()
        };

        // Preload tag rows by uid (the seq str) for the rendered window.
        let tag_by_uid = preload_tags_by_uid(folder, account_id, owner, &window);

        // Batch fetch the window in one round-trip.
        let mut rows: Vec<ListRow> = Vec::new();
        if !window.is_empty() {
            let fetch_set = window
                .iter()
                .map(|s| s.to_string())
                .collect::<Vec<_>>()
                .join(",");
            let fetches = conn
                .fetch(&fetch_set, "(FLAGS RFC822.HEADER RFC822.SIZE)")
                .ok();
            if let Some(fetches) = fetches {
                // Tag preload by Message-ID across the fetched headers.
                let mut header_ids: Vec<String> = Vec::new();
                let mut parsed: Vec<(String, String, u32, Option<ParsedMessage>)> = Vec::new();
                for f in fetches.iter() {
                    let seq = f.message.to_string();
                    let flags = flags_string(f);
                    let size = f.size.unwrap_or(0);
                    let msg = f.header().and_then(parse_message);
                    if let Some(m) = &msg {
                        if let Some(mid) = m.header("message-id") {
                            let mid = mid.trim().to_string();
                            if !mid.is_empty() {
                                header_ids.push(mid);
                            }
                        }
                    }
                    parsed.push((seq, flags, size, msg));
                }
                let tag_by_message_id =
                    preload_tags_by_message_id(folder, account_id, owner, &header_ids);

                for (seq, flags, size, msg) in parsed {
                    let msg = match msg {
                        Some(m) => m,
                        None => continue,
                    };
                    let subject = decode_header(&msg.header("subject").unwrap_or_else(|| "(no subject)".into()));
                    let sender_raw = decode_header(&msg.header("from").unwrap_or_else(|| "unknown".into()));
                    let date_str = msg.header("date").unwrap_or_default();
                    let message_id = msg.header("message-id").unwrap_or_default().trim().to_string();
                    let (sender_name, sender_addr) = parseaddr(&sender_raw);
                    let to_str = decode_header(&msg.header("to").unwrap_or_default());
                    let cc_str = decode_header(&msg.header("cc").unwrap_or_default());
                    let (iso_date, epoch) = parse_email_date(&date_str);
                    let is_read = flags.contains("\\Seen");
                    let is_answered = flags.contains("\\Answered");
                    let is_flagged = flags.contains("\\Flagged");
                    let ct = msg.header("content-type").unwrap_or_default();
                    let has_attachments = ct_has_attachments(&ct);
                    let entry = tag_by_message_id
                        .get(message_id.trim())
                        .or_else(|| tag_by_uid.get(&seq));
                    let (tags, spam) = match entry {
                        Some((t, s)) => (t.clone(), *s),
                        None => (Vec::new(), false),
                    };
                    rows.push(ListRow {
                        seq,
                        message_id,
                        subject,
                        from_name: if sender_name.is_empty() {
                            sender_addr.clone()
                        } else {
                            sender_name
                        },
                        from_address: sender_addr,
                        to: to_str,
                        cc: cc_str,
                        date: iso_date,
                        date_display: date_str,
                        date_epoch: epoch,
                        size,
                        is_read,
                        is_answered,
                        is_flagged,
                        flags,
                        has_attachments,
                        tags,
                        is_spam_verdict: spam,
                        cached_summary: None,
                    });
                }
            }
            // Sort by epoch desc (the cross-tz chronological sort).
            rows.sort_by(|a, b| {
                b.date_epoch
                    .partial_cmp(&a.date_epoch)
                    .unwrap_or(std::cmp::Ordering::Equal)
            });
        }

        if has_attachments_only {
            rows.retain(|r| r.has_attachments);
            total = rows.len() as i64;
            let start = offset.max(0) as usize;
            let end = (start + limit.max(0) as usize).min(rows.len());
            rows = if start < rows.len() {
                rows.drain(start..end).collect()
            } else {
                Vec::new()
            };
        }

        // Bulk-attach cached summaries by Message-ID.
        attach_summaries(&mut rows);

        let _ = conn.logout();
        let emails: Vec<Value> = rows.iter().map(list_row_to_value).collect();
        Ok(json!({
            "emails": emails, "total": total, "folder": folder, "offset": offset,
        }))
    })();

    match result {
        Ok(v) => v,
        Err(e) => {
            logger::error(&format!("Failed to list emails: {e}"));
            let detail = e.trim();
            let msg = if detail.is_empty() {
                "Mail operation failed".to_string()
            } else {
                format!("Mail operation failed: {}", detail.chars().take(180).collect::<String>())
            };
            json!({"emails": [], "total": 0, "error": msg})
        }
    }
}

/// `conn.search(...)` -> sorted-by-nothing `Vec<u32>` of sequence numbers.
fn search_seqs(conn: &mut ImapSession, criteria: &str) -> Vec<u32> {
    match conn.search(criteria) {
        Ok(set) => set.into_iter().collect(),
        Err(_) => Vec::new(),
    }
}

/// Serialize a `ListRow` to the exact ordered JSON the Python emits.
fn list_row_to_value(r: &ListRow) -> Value {
    let mut m = Map::new();
    m.insert("uid".into(), json!(r.seq));
    m.insert("message_id".into(), json!(r.message_id));
    m.insert("subject".into(), json!(r.subject));
    m.insert("from_name".into(), json!(r.from_name));
    m.insert("from_address".into(), json!(r.from_address));
    m.insert("to".into(), json!(r.to));
    m.insert("cc".into(), json!(r.cc));
    m.insert("date".into(), json!(r.date));
    m.insert("date_display".into(), json!(r.date_display));
    m.insert("date_epoch".into(), json!(r.date_epoch));
    m.insert("size".into(), json!(r.size));
    m.insert("is_read".into(), json!(r.is_read));
    m.insert("is_answered".into(), json!(r.is_answered));
    m.insert("is_flagged".into(), json!(r.is_flagged));
    m.insert("flags".into(), json!(r.flags));
    m.insert("has_attachments".into(), json!(r.has_attachments));
    m.insert("tags".into(), json!(r.tags));
    m.insert("is_spam_verdict".into(), json!(r.is_spam_verdict));
    if let Some(s) = &r.cached_summary {
        m.insert("cached_summary".into(), json!(s));
    }
    Value::Object(m)
}

/// Resolve a `tag:<name>` filter to a sorted seq window (the SQLite tag lookup +
/// per-Message-ID IMAP SEARCH). `None` means "no matches at all" (the Python's
/// early `return {emails:[], total:0}` paths).
fn resolve_tag_filter(
    conn: &mut ImapSession,
    filter_: &str,
    folder: &str,
    account_id: Option<&str>,
    owner: &str,
    from_clause: &str,
) -> Option<Vec<u32>> {
    let tag_name = filter_["tag:".len()..].trim().to_lowercase();
    let mut tag_message_ids: Vec<String> = Vec::new();
    let mut tag_seq_fallback: Vec<String> = Vec::new();

    if let Some(ct) = scheduled_conn() {
        let (owner_clause, aliases) = email_tag_owner_clause(account_id, owner);
        if tag_name == "spam" {
            let sql = format!(
                "SELECT message_id, uid FROM email_tags \
                 WHERE folder=? AND spam_verdict=1 \
                 AND {owner_clause}"
            );
            if let Ok(mut stmt) = ct.prepare(&sql) {
                let mut params: Vec<&dyn rusqlite::ToSql> = vec![&folder];
                for a in &aliases {
                    params.push(a);
                }
                if let Ok(rows) = stmt.query_map(params.as_slice(), |r| {
                    Ok((
                        r.get::<_, Option<String>>(0)?,
                        r.get::<_, Option<String>>(1)?,
                    ))
                }) {
                    for row in rows.flatten() {
                        match row {
                            (Some(mid), _) if !mid.trim().is_empty() => {
                                tag_message_ids.push(mid.trim().to_string())
                            }
                            (_, Some(uid)) if !uid.trim().is_empty() => {
                                tag_seq_fallback.push(uid.trim().to_string())
                            }
                            _ => {}
                        }
                    }
                }
            }
        } else {
            let sql = format!(
                "SELECT message_id, uid, tags FROM email_tags \
                 WHERE folder=? AND tags IS NOT NULL AND tags != '' \
                 AND {owner_clause}"
            );
            if let Ok(mut stmt) = ct.prepare(&sql) {
                let mut params: Vec<&dyn rusqlite::ToSql> = vec![&folder];
                for a in &aliases {
                    params.push(a);
                }
                if let Ok(rows) = stmt.query_map(params.as_slice(), |r| {
                    Ok((
                        r.get::<_, Option<String>>(0)?,
                        r.get::<_, Option<String>>(1)?,
                        r.get::<_, Option<String>>(2)?,
                    ))
                }) {
                    for (mid, uid, tags_raw) in rows.flatten() {
                        let tg: Vec<Value> = tags_raw
                            .as_deref()
                            .and_then(|t| serde_json::from_str::<Vec<Value>>(t).ok())
                            .unwrap_or_default();
                        let mut wanted: HashSet<String> = HashSet::new();
                        wanted.insert(tag_name.clone());
                        if tag_name == "marketing" {
                            wanted.insert("promo".to_string());
                        }
                        let row_tags: HashSet<String> = tg
                            .iter()
                            .filter_map(|t| t.as_str())
                            .map(|t| t.trim().to_lowercase().replace('_', "-"))
                            .collect();
                        if wanted.intersection(&row_tags).next().is_some() {
                            match (mid, uid) {
                                (Some(m), _) if !m.trim().is_empty() => {
                                    tag_message_ids.push(m.trim().to_string())
                                }
                                (_, Some(u)) if !u.trim().is_empty() => {
                                    tag_seq_fallback.push(u.trim().to_string())
                                }
                                _ => {}
                            }
                        }
                    }
                }
            }
        }
    }

    if tag_message_ids.is_empty() && tag_seq_fallback.is_empty() {
        return None;
    }

    // Resolve Message-IDs to seqs via HEADER Message-ID search; add seq fallbacks.
    let mut seqs: HashSet<u32> = HashSet::new();
    let mut seen_mid: HashSet<String> = HashSet::new();
    for mid in tag_message_ids {
        if mid.is_empty() || !seen_mid.insert(mid.clone()) {
            continue;
        }
        let criteria = format!(
            "(HEADER Message-ID {}{from_clause})",
            imap_search_quote(&mid)
        );
        for s in search_seqs(conn, &criteria) {
            seqs.insert(s);
        }
    }
    for s in tag_seq_fallback {
        if let Ok(n) = s.parse::<u32>() {
            seqs.insert(n);
        }
    }
    if seqs.is_empty() {
        return None;
    }
    Some(seqs.into_iter().collect())
}

/// Preload `email_tags` rows keyed by uid (seq str) for the window. Map value is
/// `(tags, spam)`. `promo` is normalised to `marketing` (the Python rewrite).
fn preload_tags_by_uid(
    folder: &str,
    account_id: Option<&str>,
    owner: &str,
    window: &[u32],
) -> HashMap<String, (Vec<Value>, bool)> {
    let mut out = HashMap::new();
    if window.is_empty() {
        return out;
    }
    let conn = match scheduled_conn() {
        Some(c) => c,
        None => return out,
    };
    let uid_strs: Vec<String> = window.iter().map(|s| s.to_string()).collect();
    let (owner_clause, aliases) = email_tag_owner_clause(account_id, owner);
    let uid_ph = vec!["?"; uid_strs.len()].join(",");
    let sql = format!(
        "SELECT uid, tags, spam_verdict FROM email_tags \
         WHERE folder=? AND {owner_clause} AND uid IN ({uid_ph})"
    );
    if let Ok(mut stmt) = conn.prepare(&sql) {
        let mut params: Vec<&dyn rusqlite::ToSql> = vec![&folder];
        for a in &aliases {
            params.push(a);
        }
        for u in &uid_strs {
            params.push(u);
        }
        if let Ok(rows) = stmt.query_map(params.as_slice(), |r| {
            Ok((
                r.get::<_, Option<String>>(0)?,
                r.get::<_, Option<String>>(1)?,
                r.get::<_, i64>(2)?,
            ))
        }) {
            for (uid, tags_raw, spam) in rows.flatten() {
                if let Some(uid) = uid {
                    out.insert(uid, (normalize_tags(tags_raw.as_deref()), spam != 0));
                }
            }
        }
    }
    out
}

/// Preload `email_tags` rows keyed by Message-ID. Map value is `(tags, spam)`.
fn preload_tags_by_message_id(
    folder: &str,
    account_id: Option<&str>,
    owner: &str,
    message_ids: &[String],
) -> HashMap<String, (Vec<Value>, bool)> {
    let mut out = HashMap::new();
    if message_ids.is_empty() {
        return out;
    }
    let conn = match scheduled_conn() {
        Some(c) => c,
        None => return out,
    };
    let (owner_clause, aliases) = email_tag_owner_clause(account_id, owner);
    let mid_ph = vec!["?"; message_ids.len()].join(",");
    let sql = format!(
        "SELECT message_id, tags, spam_verdict FROM email_tags \
         WHERE folder=? AND {owner_clause} \
         AND message_id IN ({mid_ph})"
    );
    if let Ok(mut stmt) = conn.prepare(&sql) {
        let mut params: Vec<&dyn rusqlite::ToSql> = vec![&folder];
        for a in &aliases {
            params.push(a);
        }
        for m in message_ids {
            params.push(m);
        }
        if let Ok(rows) = stmt.query_map(params.as_slice(), |r| {
            Ok((
                r.get::<_, Option<String>>(0)?,
                r.get::<_, Option<String>>(1)?,
                r.get::<_, i64>(2)?,
            ))
        }) {
            for (mid, tags_raw, spam) in rows.flatten() {
                let mid = mid.unwrap_or_default().trim().to_string();
                out.insert(mid, (normalize_tags(tags_raw.as_deref()), spam != 0));
            }
        }
    }
    out
}

/// `json.loads(tags or "[]")` with `promo` -> `marketing` rewrite.
fn normalize_tags(tags_raw: Option<&str>) -> Vec<Value> {
    let tg: Vec<Value> = tags_raw
        .and_then(|t| serde_json::from_str::<Vec<Value>>(t).ok())
        .unwrap_or_default();
    tg.into_iter()
        .map(|t| match t.as_str() {
            Some(s) if s.trim().to_lowercase().replace('_', "-") == "promo" => json!("marketing"),
            _ => t,
        })
        .collect()
}

/// Bulk-attach `email_summaries.summary` to each row by Message-ID.
fn attach_summaries(rows: &mut [ListRow]) {
    let ids: Vec<String> = rows
        .iter()
        .filter(|r| !r.message_id.is_empty())
        .map(|r| r.message_id.clone())
        .collect();
    if ids.is_empty() {
        return;
    }
    let conn = match scheduled_conn() {
        Some(c) => c,
        None => return,
    };
    let ph = vec!["?"; ids.len()].join(",");
    let sql = format!("SELECT message_id, summary FROM email_summaries WHERE message_id IN ({ph})");
    let mut by_id: HashMap<String, String> = HashMap::new();
    if let Ok(mut stmt) = conn.prepare(&sql) {
        let params: Vec<&dyn rusqlite::ToSql> = ids.iter().map(|s| s as &dyn rusqlite::ToSql).collect();
        if let Ok(qr) = stmt.query_map(params.as_slice(), |r| {
            Ok((r.get::<_, String>(0)?, r.get::<_, Option<String>>(1)?))
        }) {
            for (mid, s) in qr.flatten() {
                if let Some(s) = s {
                    by_id.insert(mid, s);
                }
            }
        }
    }
    for r in rows.iter_mut() {
        if let Some(s) = by_id.get(&r.message_id) {
            r.cached_summary = Some(s.clone());
        }
    }
}

/// `_record_email_received_events(owner, account_id, folder, emails)` — baseline
/// inbox messages, fire `email_received` for new arrivals.
fn record_email_received_events(owner: &str, account_id: Option<&str>, folder: &str, emails: &[Value]) {
    if owner.is_empty() || folder.to_uppercase() != "INBOX" || emails.is_empty() {
        return;
    }
    let _ = (|| -> Option<()> {
        let account_key = account_id.unwrap_or("default").trim().to_string();
        let account_key = if account_key.is_empty() {
            "default".to_string()
        } else {
            account_key
        };
        let now = format!("{}Z", crate::pydatetime::utcnow_naive_iso());
        let mut keys: Vec<String> = Vec::new();
        for e in emails {
            let key = e
                .get("message_id")
                .and_then(|v| v.as_str())
                .filter(|s| !s.trim().is_empty())
                .map(|s| s.trim().to_string())
                .or_else(|| {
                    e.get("uid")
                        .and_then(|v| v.as_str())
                        .map(|s| s.trim().to_string())
                })
                .unwrap_or_default();
            if !key.is_empty() && !keys.contains(&key) {
                keys.push(key);
            }
        }
        if keys.is_empty() {
            return None;
        }
        let conn = scheduled_conn()?;
        let _ = conn.execute(
            "CREATE TABLE IF NOT EXISTS email_event_seen (\
                owner TEXT NOT NULL, account_key TEXT NOT NULL, folder TEXT NOT NULL, \
                message_key TEXT NOT NULL, first_seen_at TEXT NOT NULL, \
                PRIMARY KEY (owner, account_key, folder, message_key))",
            [],
        );
        let count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM email_event_seen WHERE owner=? AND account_key=? AND folder=?",
                rusqlite::params![owner, account_key, folder],
                |r| r.get(0),
            )
            .unwrap_or(0);
        let mut existing: HashSet<String> = HashSet::new();
        if count > 0 {
            let ph = vec!["?"; keys.len()].join(",");
            let sql = format!(
                "SELECT message_key FROM email_event_seen \
                 WHERE owner=? AND account_key=? AND folder=? AND message_key IN ({ph})"
            );
            if let Ok(mut stmt) = conn.prepare(&sql) {
                let mut params: Vec<&dyn rusqlite::ToSql> = vec![&owner, &account_key, &folder];
                for k in &keys {
                    params.push(k);
                }
                if let Ok(rows) = stmt.query_map(params.as_slice(), |r| r.get::<_, String>(0)) {
                    for k in rows.flatten() {
                        existing.insert(k);
                    }
                }
            }
        }
        let new_keys: Vec<&String> = keys.iter().filter(|k| !existing.contains(*k)).collect();
        for k in &keys {
            let _ = conn.execute(
                "INSERT OR IGNORE INTO email_event_seen \
                 (owner, account_key, folder, message_key, first_seen_at) VALUES (?, ?, ?, ?, ?)",
                rusqlite::params![owner, account_key, folder, k, now],
            );
        }
        if count > 0 && !new_keys.is_empty() {
            for _ in new_keys.iter().take(50) {
                crate::src::event_bus::fire_event("email_received", Some(owner));
            }
            logger::info(&format!(
                "Fired email_received for {} new message(s)",
                std::cmp::min(new_keys.len(), 50)
            ));
        }
        Some(())
    })();
}

/// `q.get(key)` as `&str` (empty when absent).
fn qget<'a>(q: &'a HashMap<String, String>, key: &str) -> &'a str {
    q.get(key).map(String::as_str).unwrap_or("")
}

/// `int(q.get(key, default))` with the FastAPI/pydantic int coercion (trim + parse).
fn qint(q: &HashMap<String, String>, key: &str, default: i64) -> i64 {
    q.get(key)
        .and_then(|v| v.trim().parse::<i64>().ok())
        .unwrap_or(default)
}

/// Optional account_id query param (`None` when absent or empty).
fn q_account_id(q: &HashMap<String, String>) -> Option<String> {
    q.get("account_id").map(|s| s.to_string()).filter(|s| !s.is_empty())
}

/// `GET /api/email/list` — list emails (8s cache + offloaded IMAP).
pub(crate) async fn list_emails(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let user = user_str(user);
    let host = host_str(ci);
    let account_id = q_account_id(&q);
    let owner = auth_adapter::require_owner(user.as_deref(), &s, account_id.as_deref(), host.as_deref())?;

    let folder = {
        let f = qget(&q, "folder");
        if f.is_empty() { "INBOX".to_string() } else { f.to_string() }
    };
    let limit = qint(&q, "limit", 50);
    let offset = qint(&q, "offset", 0);
    let filter_ = {
        let f = qget(&q, "filter");
        if f.is_empty() { "all".to_string() } else { f.to_string() }
    };
    let from_addr = q.get("from").cloned().filter(|s| !s.is_empty());
    let has_attachments = qint(&q, "has_attachments", 0) != 0;
    let cache_bust = q.get("_").cloned().filter(|s| !s.is_empty());

    let ck = list_cache_key(
        account_id.as_deref().unwrap_or(""),
        &folder,
        &filter_,
        limit,
        offset,
        from_addr.as_deref().unwrap_or(""),
        has_attachments,
        &owner,
    );
    if cache_bust.is_none() {
        if let Some(cached) = list_cache_get(&ck) {
            // Warm the top recent visible body so the next click is instant.
            let warm_emails = cached
                .get("emails")
                .and_then(|v| v.as_array())
                .cloned()
                .unwrap_or_default();
            schedule_recent_email_warm(&warm_emails, &folder, account_id.as_deref(), &owner);
            return Ok(Json(cached).into_response());
        }
    }

    let (f2, fil2, fa2, acct2, own2) = (
        folder.clone(),
        filter_.clone(),
        from_addr.clone(),
        account_id.clone(),
        owner.clone(),
    );
    let result = tokio::task::spawn_blocking(move || {
        list_emails_sync(
            &f2,
            limit,
            offset,
            &fil2,
            acct2.as_deref(),
            fa2.as_deref(),
            has_attachments,
            &own2,
        )
    })
    .await
    .unwrap_or_else(|_| json!({"emails": [], "total": 0, "error": "Mail operation failed"}));

    if result.get("error").is_none() {
        if offset == 0
            && from_addr.is_none()
            && !has_attachments
            && matches!(filter_.as_str(), "all" | "unread" | "unanswered" | "undone")
        {
            let emails = result
                .get("emails")
                .and_then(|v| v.as_array())
                .cloned()
                .unwrap_or_default();
            record_email_received_events(&owner, account_id.as_deref(), &folder, &emails);
            schedule_recent_email_warm(&emails, &folder, account_id.as_deref(), &owner);
        }
        list_cache_put(ck, result.clone());
    }
    Ok(Json(result).into_response())
}

/// `POST /api/email/{uid}/unflag-spam` — user override: mark not-spam.
async fn unflag_spam(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Path(uid): Path<String>,
) -> Result<Response, HttpException> {
    let user = user_str(user);
    let host = host_str(ci);
    let owner = auth_adapter::require_owner(user.as_deref(), &s, None, host.as_deref())?;
    let ok = (|| -> Option<()> {
        let (owner_clause, aliases) = email_tag_owner_clause(None, &owner);
        let c = scheduled_conn()?;
        let sql = format!(
            "UPDATE email_tags SET spam_verdict=0, spam_reason='' WHERE uid=? AND {owner_clause}"
        );
        let mut params: Vec<&dyn rusqlite::ToSql> = vec![&uid];
        for a in &aliases {
            params.push(a);
        }
        c.execute(&sql, params.as_slice()).ok()?;
        Some(())
    })();
    match ok {
        Some(()) => Ok(Json(json!({"ok": true})).into_response()),
        None => {
            logger::error("unflag-spam failed");
            Ok(Json(json!({"ok": false, "error": "Mail operation failed"})).into_response())
        }
    }
}

/// `GET /api/email/contacts` — distinct name/address pairs from email_tags.
async fn list_contacts(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let user = user_str(user);
    let host = host_str(ci);
    let owner = auth_adapter::require_owner(user.as_deref(), &s, None, host.as_deref())?;
    let ql = qget(&q, "q").trim().to_lowercase();
    let limit = qint(&q, "limit", 20);

    let res = (|| -> Option<Vec<Value>> {
        let conn = scheduled_conn()?;
        let (owner_clause, aliases) = email_tag_owner_clause(None, &owner);
        let sql = format!(
            "SELECT sender FROM email_tags WHERE sender IS NOT NULL AND sender != '' AND {owner_clause}"
        );
        let mut stmt = conn.prepare(&sql).ok()?;
        let params: Vec<&dyn rusqlite::ToSql> =
            aliases.iter().map(|a| a as &dyn rusqlite::ToSql).collect();
        let senders: Vec<String> = stmt
            .query_map(params.as_slice(), |r| r.get::<_, String>(0))
            .ok()?
            .filter_map(Result::ok)
            .collect();
        let mut seen: IndexMap<String, (String, String)> = IndexMap::new();
        for sdr in senders {
            let (name, addr) = parseaddr(&sdr);
            if addr.is_empty() {
                continue;
            }
            let addr_l = addr.to_lowercase();
            if !ql.is_empty() && !name.to_lowercase().contains(&ql) && !addr_l.contains(&ql) {
                continue;
            }
            if seen.contains_key(&addr_l) {
                continue;
            }
            let display = if name.is_empty() { addr.clone() } else { name.clone() };
            seen.insert(addr_l, (display.trim().to_string(), addr));
        }
        let mut items: Vec<(String, String)> = seen.into_values().collect();
        items.sort_by(|a, b| {
            let a_pref = if !ql.is_empty() && a.0.to_lowercase().starts_with(&ql) { 0 } else { 1 };
            let b_pref = if !ql.is_empty() && b.0.to_lowercase().starts_with(&ql) { 0 } else { 1 };
            let a_key = if a.0.is_empty() { a.1.to_lowercase() } else { a.0.to_lowercase() };
            let b_key = if b.0.is_empty() { b.1.to_lowercase() } else { b.0.to_lowercase() };
            a_pref.cmp(&b_pref).then(a_key.cmp(&b_key))
        });
        let cap = std::cmp::max(1, limit) as usize;
        Some(
            items
                .into_iter()
                .take(cap)
                .map(|(name, address)| json!({"name": name, "address": address}))
                .collect(),
        )
    })();
    match res {
        Some(items) => Ok(Json(json!({"contacts": items})).into_response()),
        None => {
            logger::error("contacts list failed");
            Ok(Json(json!({"contacts": [], "error": "Mail operation failed"})).into_response())
        }
    }
}

/// `GET /api/email/search` — server-side IMAP SEARCH (subject/from/body).
async fn search_emails(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let user = user_str(user);
    let host = host_str(ci);
    let account_id = q_account_id(&q);
    let owner = auth_adapter::require_owner(user.as_deref(), &s, account_id.as_deref(), host.as_deref())?;
    let query = qget(&q, "q").to_string();
    let folder = {
        let f = qget(&q, "folder");
        if f.is_empty() { "INBOX".to_string() } else { f.to_string() }
    };
    let limit = qint(&q, "limit", 50);

    if query.is_empty() || query.chars().count() < 2 {
        return Ok(Json(json!({"emails": [], "total": 0, "query": query})).into_response());
    }
    if query.contains('\r') || query.contains('\n') {
        return Err(HttpException::new(400, "Invalid query"));
    }

    let result = tokio::task::spawn_blocking(move || {
        let r: Result<Value, String> = with_imap(account_id.as_deref(), &owner, |conn| {
            let _ = conn.examine(&folder);
            let q_escaped = query.replace('\\', "\\\\").replace('"', "\\\"");
            // `(OR OR FROM "q" SUBJECT "q" TEXT "q")` — three-way OR matching the
            // From header, Subject, or full body text.
            let search_cmd = format!(
                "(OR OR FROM \"{q_escaped}\" SUBJECT \"{q_escaped}\" TEXT \"{q_escaped}\")"
            );
            let mut seqs = search_seqs(conn, &search_cmd);
            if seqs.is_empty() {
                return json!({"emails": [], "total": 0, "query": query});
            }
            seqs.sort_unstable();
            seqs.reverse();
            let total = seqs.len() as i64;
            let window: Vec<u32> = seqs.into_iter().take(limit.max(0) as usize).collect();
            let mut emails: Vec<Value> = Vec::new();
            for seq in window {
                let fetches = match conn.fetch(seq.to_string(), "(FLAGS RFC822.HEADER)") {
                    Ok(f) => f,
                    Err(_) => continue,
                };
                for f in fetches.iter() {
                    let flags = flags_string(f);
                    let msg = match f.header().and_then(parse_message) {
                        Some(m) => m,
                        None => continue,
                    };
                    let subject = decode_header(&msg.header("subject").unwrap_or_else(|| "(no subject)".into()));
                    let sender_raw = decode_header(&msg.header("from").unwrap_or_else(|| "unknown".into()));
                    let date_str = msg.header("date").unwrap_or_default();
                    let message_id = msg.header("message-id").unwrap_or_default().trim().to_string();
                    let (sender_name, sender_addr) = parseaddr(&sender_raw);
                    let to_str = decode_header(&msg.header("to").unwrap_or_default());
                    let cc_str = decode_header(&msg.header("cc").unwrap_or_default());
                    let (iso_date, epoch) = parse_email_date(&date_str);
                    let ct = msg.header("content-type").unwrap_or_default();
                    emails.push(json!({
                        "uid": seq.to_string(),
                        "message_id": message_id,
                        "subject": subject,
                        "from_name": if sender_name.is_empty() { sender_addr.clone() } else { sender_name },
                        "from_address": sender_addr,
                        "to": to_str,
                        "cc": cc_str,
                        "date": iso_date,
                        "date_display": date_str,
                        "date_epoch": epoch,
                        "is_read": flags.contains("\\Seen"),
                        "is_answered": flags.contains("\\Answered"),
                        "is_flagged": flags.contains("\\Flagged"),
                        "flags": flags,
                        "has_attachments": ct_has_attachments(&ct),
                    }));
                }
            }
            json!({"emails": emails, "total": total, "query": query})
        });
        r
    })
    .await
    .unwrap_or_else(|_| Ok(json!({"emails": [], "total": 0, "error": "Mail operation failed"})));

    match result {
        Ok(v) => Ok(Json(v).into_response()),
        Err(e) => {
            logger::error(&format!("Search failed: {e}"));
            Ok(Json(json!({"emails": [], "total": 0, "error": "Mail operation failed"})).into_response())
        }
    }
}

/// `_read_email_sync(uid, folder, account_id, owner, mark_seen=True)` — read a single
/// message body (readonly), then (when `mark_seen`) flip `\Seen` in a separate
/// session, then enrich with the cached summary / ai-reply / boundaries /
/// thread-turns / sender-signature. The warm-prefetch path passes `mark_seen=false`
/// so background reads don't silently mark messages read.
fn read_email_sync(uid: &str, folder: &str, account_id: Option<&str>, owner: &str, mark_seen: bool) -> Value {
    let result: Result<Value, String> = (|| {
        // Phase 1: read the raw body in readonly.
        let mut conn = imap_connect(account_id, owner)?;
        let _ = conn.examine(folder);
        // The `uid` here is the IMAP SEQUENCE number the /list endpoint returned
        // (the frontend round-trips it back), so this is a SEQUENCE fetch —
        // imaplib's `conn.fetch(uid.encode(), ...)` (email_routes.py:1116), NOT
        // a UID fetch.
        let fetches = conn
            .fetch(uid, "(BODY.PEEK[])")
            .map_err(|e| e.to_string())?;
        let raw: Vec<u8> = match fetches.iter().next().and_then(|f| f.body()) {
            Some(b) => b.to_vec(),
            None => {
                let _ = conn.logout();
                return Ok(json!({"error": format!("Email UID {uid} not found")}));
            }
        };
        let _ = conn.logout();

        let msg = match parse_message(&raw) {
            Some(m) => m,
            None => return Ok(json!({"error": "Mail operation failed"})),
        };
        let subject = decode_header(&msg.header("subject").unwrap_or_else(|| "(no subject)".into()));
        let sender_raw = decode_header(&msg.header("from").unwrap_or_else(|| "unknown".into()));
        let to = decode_header(&msg.header("to").unwrap_or_default());
        let cc = decode_header(&msg.header("cc").unwrap_or_default());
        let date_str = msg.header("date").unwrap_or_default();
        let message_id = msg.header("message-id").unwrap_or_default().trim().to_string();
        let in_reply_to = msg.header("in-reply-to").unwrap_or_default().trim().to_string();
        let references = msg.header("references").unwrap_or_default().trim().to_string();
        let body = extract_text(&msg);
        let body_html = extract_html(&msg);
        let (sender_name, sender_addr) = parseaddr(&sender_raw);
        let (iso_date, _epoch) = parse_email_date(&date_str);
        let attachments = list_attachments_from_msg(&msg);

        // Phase 2: set \Seen in a separate readwrite session (best-effort), only
        // when the caller asked to mark it seen — the warm prefetch passes
        // `mark_seen=false` so background reads never silently mark a message read.
        // SEQUENCE store — imaplib's `conn2.store(uid.encode(), "+FLAGS",
        // "\\Seen")` (email_routes.py:1144), matching the SEQUENCE fetch above.
        if mark_seen {
            let _ = with_imap(account_id, owner, |c2| {
                let _ = c2.select(folder);
                let _ = c2.store(uid, "+FLAGS (\\Seen)");
            });
        }

        // Phase 3: enrich from the scheduled DB.
        let mut cached_summary: Option<String> = None;
        let mut cached_ai_reply: Option<String> = None;
        let mut cached_boundaries: Option<Value> = None;
        let mut cached_turns: Option<Vec<Value>> = None;
        let mut cached_sender_sig: Option<String> = None;
        if let Some(c) = scheduled_conn() {
            cached_summary = c
                .query_row(
                    "SELECT summary FROM email_summaries WHERE message_id = ?",
                    rusqlite::params![message_id],
                    |r| r.get::<_, Option<String>>(0),
                )
                .optional()
                .ok()
                .flatten()
                .flatten();
            cached_ai_reply = c
                .query_row(
                    "SELECT reply FROM email_ai_replies WHERE message_id = ?",
                    rusqlite::params![message_id],
                    |r| r.get::<_, Option<String>>(0),
                )
                .optional()
                .ok()
                .flatten()
                .flatten();
            let boundary_row: Option<(Option<i64>, Option<i64>, Option<String>)> = c
                .query_row(
                    "SELECT sig_start, quote_start, turns_json FROM email_boundaries WHERE message_id = ?",
                    rusqlite::params![message_id],
                    |r| {
                        Ok((
                            r.get::<_, Option<i64>>(0)?,
                            r.get::<_, Option<i64>>(1)?,
                            r.get::<_, Option<String>>(2)?,
                        ))
                    },
                )
                .optional()
                .ok()
                .flatten();
            // Per-sender cached signature.
            if !sender_addr.is_empty() {
                cached_sender_sig = c
                    .query_row(
                        "SELECT signature_text FROM sender_signatures WHERE from_address = ?",
                        rusqlite::params![sender_addr.to_lowercase().trim()],
                        |r| r.get::<_, Option<String>>(0),
                    )
                    .optional()
                    .ok()
                    .flatten()
                    .flatten()
                    .filter(|s| !s.is_empty());
            }
            if let Some((sig_start, quote_start, turns_json)) = boundary_row {
                cached_boundaries = Some(json!({"sig_start": sig_start, "quote_start": quote_start}));
                if let Some(tj) = turns_json {
                    // Versioned envelope: {"v": N, "turns": [...]}.
                    if let Ok(parsed) = serde_json::from_str::<Value>(&tj) {
                        if parsed.get("v").and_then(|v| v.as_i64())
                            == Some(crate::src::email_thread_parser::THREAD_PARSER_VERSION)
                        {
                            if let Some(turns) = parsed.get("turns").and_then(|t| t.as_array()) {
                                cached_turns = Some(turns.clone());
                            }
                        }
                    }
                }
            }
        }

        // If no cached turns, parse on-the-fly.
        if cached_turns.is_none() {
            cached_turns = crate::src::email_thread_parser::parse_thread(
                body_html.as_deref(),
                Some(&body),
            );
        }

        Ok(json!({
            "uid": uid,
            "message_id": message_id,
            "subject": subject,
            "from_name": if sender_name.is_empty() { sender_addr.clone() } else { sender_name },
            "from_address": sender_addr,
            "to": to,
            "cc": cc,
            "date": iso_date,
            "in_reply_to": in_reply_to,
            "references": references,
            "body": body,
            "body_html": body_html,
            "attachments": attachments,
            "cached_summary": cached_summary,
            "cached_ai_reply": cached_ai_reply,
            "boundaries": cached_boundaries,
            "thread_turns": cached_turns,
            "sender_signature": cached_sender_sig,
        }))
    })();
    match result {
        Ok(v) => v,
        Err(e) => {
            logger::error(&format!("Failed to read email {uid}: {e}"));
            json!({"error": "Mail operation failed"})
        }
    }
}

/// `_mark_email_seen_sync(uid, folder, account_id, owner)` — flip `\Seen` in a
/// readwrite session and invalidate the list cache so the unread count refreshes.
/// Used after serving a cached read body (the cached body skipped the flag flip).
/// Runs under `spawn_blocking`.
fn mark_email_seen_sync(uid: &str, folder: &str, account_id: Option<&str>, owner: &str) {
    let r = with_imap(account_id, owner, |conn| {
        let _ = conn.select(folder);
        let _ = conn.store(uid, "+FLAGS (\\Seen)");
    });
    if r.is_ok() {
        invalidate_list_cache(account_id, Some(folder));
    } else {
        logger::debug(&format!("mark-seen after cached read failed uid={uid}"));
    }
}

/// `_schedule_recent_email_warm(emails, folder, account_id, owner)` — after a list
/// load, kick off a background read of the top recent visible UID so a subsequent
/// click lands in the read cache. Faithful to the Python selection rules: skip the
/// synthetic `__scheduled__` folder, skip UID-less rows, skip messages older than a
/// week (`date_epoch`), skip bodies over 128KB (`size`), skip rows already cached or
/// already warming, and warm at most `WARM_READ_LIMIT` per call. The warm read uses
/// `mark_seen=false` so it never marks a message read.
fn schedule_recent_email_warm(emails: &[Value], folder: &str, account_id: Option<&str>, owner: &str) {
    if emails.is_empty() || folder == "__scheduled__" {
        return;
    }
    let now = chrono::Utc::now().timestamp() as f64;
    // Selected (uid, read_cache_key) pairs, reserved in WARMING_READS.
    let mut selected: Vec<(String, ReadKey)> = Vec::new();
    {
        let mut warming = match WARMING_READS.lock() {
            Ok(g) => g,
            Err(p) => p.into_inner(),
        };
        for em in emails {
            let uid = em
                .get("uid")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .trim()
                .to_string();
            if uid.is_empty() {
                continue;
            }
            let epoch = em.get("date_epoch").and_then(|v| v.as_f64()).unwrap_or(0.0);
            if epoch != 0.0 && now - epoch > WARM_RECENT_SECONDS {
                continue;
            }
            let size = em.get("size").and_then(|v| v.as_u64()).unwrap_or(0) as u32;
            if size > WARM_MAX_BYTES {
                continue;
            }
            let ck = read_cache_key(account_id.unwrap_or(""), folder, &uid, owner);
            if read_cache_get(&ck).is_some() || warming.contains(&ck) {
                continue;
            }
            warming.insert(ck.clone());
            selected.push((uid, ck));
            if selected.len() >= WARM_READ_LIMIT {
                break;
            }
        }
    }
    if selected.is_empty() {
        return;
    }
    // Background warm task — read each selected UID (mark_seen=false), populate the
    // read cache, then clear the warming marker. Detached, so it runs across the
    // request boundary exactly like the Python's `asyncio.create_task(_warm())`.
    let folder = folder.to_string();
    let account_id = account_id.map(str::to_string);
    let owner = owner.to_string();
    tokio::spawn(async move {
        for (uid, ck) in selected {
            if read_cache_get(&ck).is_some() {
                if let Ok(mut g) = WARMING_READS.lock() {
                    g.remove(&ck);
                }
                continue;
            }
            let (uid2, f2, acct2, own2) = (uid.clone(), folder.clone(), account_id.clone(), owner.clone());
            let result = tokio::task::spawn_blocking(move || {
                read_email_sync(&uid2, &f2, acct2.as_deref(), &own2, false)
            })
            .await
            .unwrap_or_else(|_| json!({"error": "warm join failed"}));
            if result.get("error").is_none() {
                read_cache_put(ck.clone(), result);
            }
            if let Ok(mut g) = WARMING_READS.lock() {
                g.remove(&ck);
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
    });
}

/// `GET /api/email/read/{uid}` — read email body (30m cache, threaded IMAP).
pub(crate) async fn read_email_by_uid(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Path(uid): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let user = user_str(user);
    let host = host_str(ci);
    let account_id = q_account_id(&q);
    let owner = auth_adapter::require_owner(user.as_deref(), &s, account_id.as_deref(), host.as_deref())?;
    let folder = {
        let f = qget(&q, "folder");
        if f.is_empty() { "INBOX".to_string() } else { f.to_string() }
    };
    // `mark_seen: bool = Query(True)` — default true; falsy query values disable it
    // (matching FastAPI's bool coercion: false/0/off/no).
    let mark_seen = match q.get("mark_seen") {
        Some(v) => !matches!(v.trim().to_lowercase().as_str(), "false" | "0" | "off" | "no" | ""),
        None => true,
    };
    let ck = read_cache_key(account_id.as_deref().unwrap_or(""), &folder, &uid, &owner);
    if let Some(cached) = read_cache_get(&ck) {
        // Cached body skipped the flag flip — mark it seen in the background so the
        // unread count still updates (only when the caller asked to mark it seen).
        if mark_seen {
            let (uid2, f2, acct2, own2) = (uid.clone(), folder.clone(), account_id.clone(), owner.clone());
            tokio::spawn(async move {
                let _ = tokio::task::spawn_blocking(move || {
                    mark_email_seen_sync(&uid2, &f2, acct2.as_deref(), &own2)
                })
                .await;
            });
        }
        return Ok(Json(cached).into_response());
    }
    let (uid2, f2, acct2, own2) = (uid.clone(), folder.clone(), account_id.clone(), owner.clone());
    let result = tokio::task::spawn_blocking(move || {
        read_email_sync(&uid2, &f2, acct2.as_deref(), &own2, mark_seen)
    })
    .await
    .unwrap_or_else(|_| json!({"error": "Mail operation failed"}));
    if result.get("error").is_none() {
        read_cache_put(ck, result.clone());
    }
    Ok(Json(result).into_response())
}

/// Fetch a single message's full RFC822 by UID (readonly), returning the parsed
/// message. The shared prefix of the attachment endpoints.
fn fetch_message_by_uid(uid: &str, folder: &str, account_id: Option<&str>, owner: &str) -> Result<Option<ParsedMessage>, String> {
    let raw: Option<Vec<u8>> = with_imap(account_id, owner, |conn| {
        let _ = conn.examine(folder);
        // SEQUENCE fetch — the `uid` is the /list sequence number the frontend
        // round-trips back; imaplib's `conn.fetch(uid.encode(), "(RFC822)")`
        // (email_routes.py:1270/1287/1323/1531) is sequence-based.
        conn.fetch(uid, "(RFC822)")
            .ok()
            .and_then(|f| f.iter().next().and_then(|m| m.body()).map(|b| b.to_vec()))
    })?;
    Ok(raw.and_then(|b| parse_message(&b)))
}

/// `GET /api/email/attachments/{uid}` — list attachments.
async fn list_attachments(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Path(uid): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let user = user_str(user);
    let host = host_str(ci);
    let account_id = q_account_id(&q);
    let owner = auth_adapter::require_owner(user.as_deref(), &s, account_id.as_deref(), host.as_deref())?;
    let folder = folder_or_inbox(&q);
    let (uid2, f2, acct2, own2) = (uid.clone(), folder, account_id, owner);
    let res = tokio::task::spawn_blocking(move || {
        fetch_message_by_uid(&uid2, &f2, acct2.as_deref(), &own2).map(|m| (uid2, m))
    })
    .await
    .unwrap_or_else(|_| Err("join".into()));
    match res {
        Ok((uid, Some(msg))) => {
            let attachments = list_attachments_from_msg(&msg);
            Ok(Json(json!({"attachments": attachments, "uid": uid})).into_response())
        }
        Ok((_, None)) => Ok(Json(json!({"attachments": [], "error": "Email not found"})).into_response()),
        Err(e) => {
            logger::error(&format!("Failed to list attachments for {uid}: {e}"));
            Ok(Json(json!({"attachments": [], "error": "Mail operation failed"})).into_response())
        }
    }
}

/// `folder` query param defaulting to "INBOX".
fn folder_or_inbox(q: &HashMap<String, String>) -> String {
    let f = qget(q, "folder");
    if f.is_empty() { "INBOX".to_string() } else { f.to_string() }
}

/// `GET /api/email/attachment/{uid}/{index}` — download a specific attachment.
async fn download_attachment(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Path((uid, index)): Path<(String, usize)>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let user = user_str(user);
    let host = host_str(ci);
    let account_id = q_account_id(&q);
    let owner = auth_adapter::require_owner(user.as_deref(), &s, account_id.as_deref(), host.as_deref())?;
    let folder = folder_or_inbox(&q);
    // Containment-safe extraction dir: flatten/normalize `folder`/`uid` and reject
    // traversal with a 400, mirroring Python `attachment_extract_dir`. Computed on
    // the async side (pure path work, no IO) so the 400 propagates like Python's
    // HTTPException, then moved into the blocking task.
    let target_dir = crate::routes::email_helpers::attachment_extract_dir(&folder, &uid)?;
    let (uid2, f2, acct2, own2) = (uid.clone(), folder.clone(), account_id, owner);
    let res = tokio::task::spawn_blocking(move || -> Result<Option<std::path::PathBuf>, String> {
        let msg = match fetch_message_by_uid(&uid2, &f2, acct2.as_deref(), &own2)? {
            Some(m) => m,
            None => return Ok(None),
        };
        Ok(extract_attachment_to_disk(&msg, index, &target_dir))
    })
    .await
    .unwrap_or_else(|_| Err("join".into()));
    match res {
        Ok(Some(filepath)) => {
            let bytes = match std::fs::read(&filepath) {
                Ok(b) => b,
                Err(e) => {
                    logger::error(&format!("Failed to download attachment {uid}/{index}: {e}"));
                    return Ok(Json(json!({"error": "Mail operation failed"})).into_response());
                }
            };
            let name = filepath
                .file_name()
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_else(|| "attachment".to_string());
            let resp = Response::builder()
                .header(header::CONTENT_TYPE, "application/octet-stream")
                .header(
                    header::CONTENT_DISPOSITION,
                    format!("attachment; filename=\"{name}\""),
                )
                .body(Body::from(bytes))
                .unwrap();
            Ok(resp)
        }
        Ok(None) => Ok(Json(json!({"error": "Email not found"})).into_response()),
        Err(e) => {
            logger::error(&format!("Failed to download attachment {uid}/{index}: {e}"));
            Ok(Json(json!({"error": "Mail operation failed"})).into_response())
        }
    }
}

/// `POST /api/email/attachment-path/{uid}/{index}` — extract to disk, return path.
async fn get_attachment_path(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Path((uid, index)): Path<(String, usize)>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let user = user_str(user);
    let host = host_str(ci);
    let account_id = q_account_id(&q);
    let owner = auth_adapter::require_owner(user.as_deref(), &s, account_id.as_deref(), host.as_deref())?;
    let folder = folder_or_inbox(&q);
    // Containment-safe extraction dir (rejects folder/uid traversal with a 400),
    // computed before the blocking task like Python `attachment_extract_dir`.
    let target_dir = crate::routes::email_helpers::attachment_extract_dir(&folder, &uid)?;
    let (uid2, f2, acct2, own2) = (uid.clone(), folder.clone(), account_id, owner);
    let res = tokio::task::spawn_blocking(move || -> Result<Option<std::path::PathBuf>, String> {
        let msg = match fetch_message_by_uid(&uid2, &f2, acct2.as_deref(), &own2)? {
            Some(m) => m,
            None => return Ok(None),
        };
        Ok(extract_attachment_to_disk(&msg, index, &target_dir))
    })
    .await
    .unwrap_or_else(|_| Err("join".into()));
    match res {
        Ok(Some(filepath)) => {
            let name = filepath
                .file_name()
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_default();
            let size = std::fs::metadata(&filepath).map(|m| m.len()).unwrap_or(0);
            Ok(Json(json!({
                "path": filepath.to_string_lossy(),
                "filename": name,
                "size": size,
            })).into_response())
        }
        Ok(None) => Ok(Json(json!({"error": "Email not found"})).into_response()),
        Err(e) => {
            logger::error(&format!("Failed to get attachment path {uid}/{index}: {e}"));
            Ok(Json(json!({"error": "Mail operation failed"})).into_response())
        }
    }
}

/// `POST /api/email/attachment-as-doc/{uid}/{index}` — extract an attachment and
/// open it in the document editor (.pdf / .docx / .txt / .md).
async fn attachment_as_doc(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Path((uid, index)): Path<(String, usize)>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let user = user_str(user);
    let host = host_str(ci);
    let account_id = q_account_id(&q);
    let owner = auth_adapter::require_owner(user.as_deref(), &s, account_id.as_deref(), host.as_deref())?;
    let folder = folder_or_inbox(&q);
    let doc_user = user.clone();

    let acct_for_tag = account_id.clone().unwrap_or_default();
    // Containment-safe extraction dir (rejects folder/uid traversal with a 400),
    // computed before the blocking task like Python `attachment_extract_dir`.
    let target_dir = crate::routes::email_helpers::attachment_extract_dir(&folder, &uid)?;
    let (uid2, f2, acct2, own2) = (uid.clone(), folder.clone(), account_id, owner);
    let out = tokio::task::spawn_blocking(move || -> Value {
        // Fetch + extract the attachment to disk.
        let msg = match fetch_message_by_uid(&uid2, &f2, acct2.as_deref(), &own2) {
            Ok(Some(m)) => m,
            Ok(None) => return json!({"error": "Email not found"}),
            Err(e) => {
                logger::error(&format!("attachment-as-doc {uid2}/{index} failed: {e}"));
                return json!({"error": "Mail operation failed"});
            }
        };
        let filepath = match extract_attachment_to_disk(&msg, index, &target_dir) {
            Some(p) => p,
            None => return json!({"error": format!("Attachment index {index} not found")}),
        };
        let base = filepath.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
        if base.starts_with('.') {
            return json!({"error": "Invalid filename", "filename": base});
        }
        let ext = std::path::Path::new(&base)
            .extension()
            .map(|e| format!(".{}", e.to_string_lossy().to_lowercase()))
            .unwrap_or_default();
        // title = os.path.splitext(filepath.name)[0]
        let title = base.rsplit_once('.').map(|(stem, _)| stem.to_string()).unwrap_or_else(|| base.clone());
        let src_message_id = msg.header("message-id").unwrap_or_default().trim().to_string();

        // Resolve the caller's most-recent session.
        let doc_session_id = resolve_doc_session(doc_user.as_deref());

        let tag_source = |doc_id: &str| {
            if doc_id.is_empty() {
                return;
            }
            let _ = (|| -> Option<()> {
                let conn = crate::core::database::session_local().ok()?;
                conn.execute(
                    "UPDATE documents SET source_email_uid=?1, source_email_folder=?2, \
                     source_email_account_id=?3, source_email_message_id=?4 WHERE id=?5",
                    rusqlite::params![uid2, f2, acct_for_tag, src_message_id, doc_id],
                )
                .ok()?;
                Some(())
            })();
        };

        let session_id = doc_session_id.as_deref().unwrap_or("");
        let path_str = filepath.to_string_lossy().to_string();

        if ext == ".pdf" {
            // Copy into UPLOAD_DIR/yyyy/mm/dd/<uuid>.pdf, then create the doc.
            let upload_id = format!("{}.pdf", uuid::Uuid::new_v4().simple());
            let today = chrono::Utc::now().format("%Y/%m/%d").to_string();
            let dated_dir = std::path::Path::new(&*crate::src::constants::UPLOAD_DIR).join(&today);
            let _ = std::fs::create_dir_all(&dated_dir);
            let dest_path = dated_dir.join(&upload_id);
            if std::fs::copy(&filepath, &dest_path).is_err() {
                return json!({"error": "Mail operation failed"});
            }
            let dest_str = dest_path.to_string_lossy().to_string();
            let is_form = std::panic::catch_unwind(|| crate::src::pdf_forms::has_form_fields(&dest_str))
                .unwrap_or(false);
            let doc_id = if is_form {
                let fields = crate::src::pdf_forms::extract_fields(&dest_str);
                crate::src::pdf_form_doc::save_field_sidecar(&dest_str, &fields);
                crate::src::pdf_form_doc::create_form_markdown_document(
                    session_id, &fields, &upload_id, &title, None,
                )
            } else {
                crate::src::pdf_form_doc::create_plain_pdf_document(session_id, &upload_id, &title, None)
            };
            match doc_id {
                Some(id) => {
                    tag_source(&id);
                    json!({"doc_id": id, "filename": base})
                }
                None => json!({"error": "Failed to create document"}),
            }
        } else if ext == ".docx" {
            // HONEST DEFER: no docx reader crate wired -> Python's
            // `except ImportError: return {"error": "python-docx not installed"}`.
            json!({"error": "python-docx not installed", "filename": base})
        } else if matches!(ext.as_str(), ".txt" | ".md" | ".markdown") {
            let content = match std::fs::read(&path_str) {
                Ok(b) => String::from_utf8_lossy(&b).to_string(),
                Err(e) => return json!({"error": format!("Failed to read text file: {e}"), "filename": base}),
            };
            match create_markdown_doc(session_id, &title, &content, "Imported from email attachment") {
                Some(id) => {
                    tag_source(&id);
                    json!({"doc_id": id, "filename": base})
                }
                None => json!({"error": "Mail operation failed"}),
            }
        } else {
            json!({"error": format!("Unsupported attachment type: {ext}"), "filename": base})
        }
    })
    .await
    .unwrap_or_else(|_| json!({"error": "Mail operation failed"}));
    Ok(Json(out).into_response())
}

/// Resolve the most-recent session id for `user` (`updated_at desc`), owner-scoped
/// when a user is given (the Python `_resolve_doc_session`).
fn resolve_doc_session(user: Option<&str>) -> Option<String> {
    let conn = crate::core::database::session_local().ok()?;
    // Bind the owner to a value that outlives the borrowed-params vec below.
    // Bind the owner ref to a function-scoped variable so the borrowed-params vec
    // below can hold `&owner` (a `&&str`, which coerces to `&dyn ToSql`) without the
    // E0597 arm-local-lifetime problem.
    let owner: Option<&str> = user.filter(|u| !u.is_empty());
    let (sql, params): (&str, Vec<&dyn rusqlite::ToSql>) = match &owner {
        Some(u) => (
            "SELECT id FROM sessions WHERE owner = ?1 ORDER BY updated_at DESC LIMIT 1",
            vec![u],
        ),
        None => ("SELECT id FROM sessions ORDER BY updated_at DESC LIMIT 1", vec![]),
    };
    conn.query_row(sql, params.as_slice(), |r| r.get::<_, String>(0))
        .optional()
        .ok()
        .flatten()
}

/// Direct `documents` + `document_versions` insert for the docx/txt branches
/// (the `_Doc`/`_DV` ORM inserts). Sets the new doc active, deactivating others.
fn create_markdown_doc(session_id: &str, title: &str, content: &str, summary: &str) -> Option<String> {
    let conn = crate::core::database::session_local().ok()?;
    let doc_id = uuid::Uuid::new_v4().to_string();
    let ver_id = uuid::Uuid::new_v4().to_string();
    // owner inherited from the session.
    let owner: Option<String> = conn
        .query_row(
            "SELECT owner FROM sessions WHERE id = ?1",
            rusqlite::params![session_id],
            |r| r.get::<_, Option<String>>(0),
        )
        .optional()
        .ok()
        .flatten()
        .flatten();
    let now = crate::pydatetime::utcnow_naive_iso();
    // _db.query(_Doc).filter(_Doc.is_active == True).update({"is_active": False})
    conn.execute("UPDATE documents SET is_active = 0 WHERE is_active = 1", []).ok()?;
    conn.execute(
        "INSERT INTO documents \
           (id, session_id, title, language, current_content, version_count, \
            is_active, archived, owner, created_at, updated_at) \
         VALUES (?1, ?2, ?3, 'markdown', ?4, 1, 1, 0, ?5, ?6, ?6)",
        rusqlite::params![doc_id, session_id, title, content, owner, now],
    )
    .ok()?;
    conn.execute(
        "INSERT INTO document_versions \
           (id, document_id, version_number, content, summary, source, created_at) \
         VALUES (?1, ?2, 1, ?3, ?4, 'upload', ?5)",
        rusqlite::params![ver_id, doc_id, content, summary, now],
    )
    .ok()?;
    crate::src::tool_implementations::documents::set_active_document(Some(&doc_id));
    Some(doc_id)
}

/// Shared body of the simple flag/move mutation handlers: resolve owner, run the
/// closure under spawn_blocking inside a `select`-ed (readwrite) session,
/// invalidate the cache, and return `{"success": ...}`.
// mirrors the Python per-handler signature (owner/host/account/folder/invalidate/
// op-label + the mutating closure); kept as flat params rather than a config struct.
#[allow(clippy::too_many_arguments)]
async fn flag_mutation<F>(
    s: &AppState,
    user: Option<String>,
    host: Option<String>,
    account_id: Option<String>,
    folder: String,
    invalidate_folder: Option<String>,
    op_label: &'static str,
    f: F,
) -> Result<Response, HttpException>
where
    F: FnOnce(&mut ImapSession) -> Result<bool, String> + Send + 'static,
{
    let owner = auth_adapter::require_owner(user.as_deref(), s, account_id.as_deref(), host.as_deref())?;
    let (acct2, own2, f2) = (account_id.clone(), owner, folder.clone());
    let res: Result<Result<bool, String>, String> = tokio::task::spawn_blocking(move || {
        with_imap(acct2.as_deref(), &own2, |conn| {
            let _ = conn.select(&f2);
            f(conn)
        })
    })
    .await
    .map_err(|e| e.to_string())
    .and_then(|r| r);

    match res {
        Ok(Ok(true)) => {
            invalidate_list_cache(account_id.as_deref(), invalidate_folder.as_deref());
            Ok(Json(json!({"success": true})).into_response())
        }
        Ok(Ok(false)) => Ok(Json(json!({"success": false, "error": "Email not found"})).into_response()),
        _ => {
            logger::error(&format!("Failed to {op_label}"));
            Ok(Json(json!({"success": false, "error": "Mail operation failed"})).into_response())
        }
    }
}

/// `POST /api/email/mark-unread/{uid}`.
async fn mark_unread(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Path(uid): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let folder = folder_or_inbox(&q);
    let account_id = q_account_id(&q);
    let inval = Some(folder.clone());
    flag_mutation(&s, user_str(user), host_str(ci), account_id, folder, inval, "mark unread",
        move |conn| Ok(store_email_flag(conn, &uid, "\\Seen", false))).await
}

/// `POST /api/email/mark-read/{uid}`.
async fn mark_read(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Path(uid): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let folder = folder_or_inbox(&q);
    let account_id = q_account_id(&q);
    let inval = Some(folder.clone());
    flag_mutation(&s, user_str(user), host_str(ci), account_id, folder, inval, "mark read",
        move |conn| Ok(store_email_flag(conn, &uid, "\\Seen", true))).await
}

/// `POST /api/email/archive/{uid}` — move to Archive.
async fn archive_email(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Path(uid): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let folder = folder_or_inbox(&q);
    let account_id = q_account_id(&q);
    flag_mutation(&s, user_str(user), host_str(ci), account_id, folder, None, "archive email",
        move |conn| Ok(move_email_message(conn, &uid, "Archive", "archive"))).await
}

/// `DELETE /api/email/delete/{uid}` — move to Trash.
async fn delete_email(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Path(uid): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let folder = folder_or_inbox(&q);
    let account_id = q_account_id(&q);
    flag_mutation(&s, user_str(user), host_str(ci), account_id, folder, None, "delete email",
        move |conn| Ok(move_email_message(conn, &uid, "Trash", "trash"))).await
}

/// `DELETE /api/email/delete-permanent/{uid}` — flag `\Deleted` + expunge.
async fn delete_email_permanent(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Path(uid): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let folder = folder_or_inbox(&q);
    let account_id = q_account_id(&q);
    let inval = Some(folder.clone());
    flag_mutation(&s, user_str(user), host_str(ci), account_id, folder, inval, "permanently delete email",
        move |conn| {
            if !store_email_flag(conn, &uid, "\\Deleted", true) {
                return Ok(false);
            }
            let _ = conn.expunge();
            Ok(true)
        }).await
}

/// `POST /api/email/move/{uid}` — move to a named folder (`dest` query, required).
async fn move_email(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Path(uid): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    // `dest: str = Query(...)` — required; missing -> 422.
    let dest = match q.get("dest") {
        Some(d) => d.clone(),
        None => {
            return Err(HttpException::with_detail(
                422,
                json!([{ "type": "missing", "loc": ["query", "dest"], "msg": "Field required" }]),
            ))
        }
    };
    let folder = folder_or_inbox(&q);
    let account_id = q_account_id(&q);
    let owner = auth_adapter::require_owner(user_str(user).as_deref(), &s, account_id.as_deref(), host_str(ci).as_deref())?;
    let (acct2, own2, f2, dest2) = (account_id.clone(), owner, folder.clone(), dest.clone());
    let res: Result<Result<bool, String>, String> = tokio::task::spawn_blocking(move || {
        with_imap(acct2.as_deref(), &own2, |conn| {
            let _ = conn.select(&f2);
            Ok(move_email_message(conn, &uid, &dest2, ""))
        })
    })
    .await
    .map_err(|e| e.to_string())
    .and_then(|r| r);
    match res {
        Ok(Ok(true)) => {
            invalidate_list_cache(account_id.as_deref(), None);
            Ok(Json(json!({"success": true})).into_response())
        }
        Ok(Ok(false)) => Ok(Json(json!({"success": false, "error": format!("Failed to move to {dest}")})).into_response()),
        _ => {
            logger::error(&format!("Failed to move email to {dest}"));
            Ok(Json(json!({"success": false, "error": "Mail operation failed"})).into_response())
        }
    }
}

/// `POST /api/email/mark-answered/{uid}` — set `\Answered`.
async fn mark_answered(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Path(uid): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let folder = folder_or_inbox(&q);
    let account_id = q_account_id(&q);
    // No cache invalidation in the Python for answered/clear-answered.
    let owner = auth_adapter::require_owner(user_str(user).as_deref(), &s, account_id.as_deref(), host_str(ci).as_deref())?;
    answered_mutation(account_id, owner, folder, uid, true, "mark answered").await
}

/// `POST /api/email/clear-answered/{uid}` — clear `\Answered`.
async fn clear_answered(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Path(uid): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let folder = folder_or_inbox(&q);
    let account_id = q_account_id(&q);
    let owner = auth_adapter::require_owner(user_str(user).as_deref(), &s, account_id.as_deref(), host_str(ci).as_deref())?;
    answered_mutation(account_id, owner, folder, uid, false, "clear answered").await
}

async fn answered_mutation(
    account_id: Option<String>,
    owner: String,
    folder: String,
    uid: String,
    add: bool,
    op_label: &'static str,
) -> Result<Response, HttpException> {
    let res: Result<Result<bool, String>, String> = tokio::task::spawn_blocking(move || {
        with_imap(account_id.as_deref(), &owner, |conn| {
            let _ = conn.select(&folder);
            Ok(store_email_flag(conn, &uid, "\\Answered", add))
        })
    })
    .await
    .map_err(|e| e.to_string())
    .and_then(|r| r);
    match res {
        Ok(Ok(true)) => Ok(Json(json!({"success": true})).into_response()),
        Ok(Ok(false)) => Ok(Json(json!({"success": false, "error": "Email not found"})).into_response()),
        _ => {
            logger::error(&format!("Failed to {op_label}"));
            Ok(Json(json!({"success": false, "error": "Mail operation failed"})).into_response())
        }
    }
}

/// `GET /api/email/folders` — list IMAP folders.
async fn list_folders(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let account_id = q_account_id(&q);
    let owner = auth_adapter::require_owner(user_str(user).as_deref(), &s, account_id.as_deref(), host_str(ci).as_deref())?;
    let res: Result<Vec<String>, String> = tokio::task::spawn_blocking(move || {
        with_imap(account_id.as_deref(), &owner, |conn| {
            list_imap_folders(conn).into_iter().map(|(n, _)| n).collect::<Vec<_>>()
        })
    })
    .await
    .unwrap_or_else(|_| Ok(Vec::new()));
    match res {
        Ok(folders) => Ok(Json(json!({"folders": folders})).into_response()),
        Err(e) => {
            logger::error(&format!("list_folders failed: {e}"));
            Ok(Json(json!({"folders": [], "error": "Mail operation failed"})).into_response())
        }
    }
}

/// `DELETE /api/email/odysseus/reminders` — delete Odysseus-stamped reminder mail.
async fn delete_odysseus_reminder_emails(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let account_id = q_account_id(&q);
    let owner = auth_adapter::require_owner(user_str(user).as_deref(), &s, account_id.as_deref(), host_str(ci).as_deref())?;
    // `if account_id: _assert_owns_account(...)` — require_owner already did this,
    // but the Python repeats it; harmless (the owner check already passed).
    if let Some(aid) = account_id.as_deref() {
        auth_adapter::assert_owns_account(aid, &owner)?;
    }
    let permanent = q
        .get("permanent")
        .map(|v| matches!(v.trim().to_lowercase().as_str(), "1" | "true" | "yes" | "on"))
        .unwrap_or(false);

    let (acct2, own2) = (account_id.clone(), owner);
    let res: Result<(i64, Vec<String>), String> = tokio::task::spawn_blocking(move || {
        let cfg = get_email_config(acct2.as_deref(), &own2);
        let mut own_addrs: Vec<String> = Vec::new();
        for a in [cfg.from_address.trim(), cfg.smtp_user.trim(), cfg.imap_user.trim()] {
            if !a.is_empty() && !own_addrs.iter().any(|x| x == a) {
                own_addrs.push(a.to_string());
            }
        }
        let sq = |v: &str| format!("\"{}\"", v.replace('\\', "\\\\").replace('"', "\\\""));

        with_imap(acct2.as_deref(), &own2, |conn| {
            let mut deleted: i64 = 0;
            let mut folders_checked: Vec<String> = Vec::new();
            let sent_folder = detect_sent_folder(conn);
            let candidates = ["INBOX".to_string(), sent_folder, "All Mail".to_string(), "[Gmail]/All Mail".to_string()];
            let mut seen: HashSet<String> = HashSet::new();
            for folder_name in candidates {
                if folder_name.is_empty() || !seen.insert(folder_name.clone()) {
                    continue;
                }
                if conn.select(&folder_name).is_err() {
                    continue;
                }
                folders_checked.push(folder_name.clone());
                let mut uids: HashSet<u32> = HashSet::new();
                for u in uid_search_set(conn, &format!("(HEADER X-Odysseus-Kind {})", sq("reminder"))) {
                    uids.insert(u);
                }
                for u in uid_search_set(conn, &format!("(SUBJECT {})", sq("Reminder (Odysseus):"))) {
                    uids.insert(u);
                }
                for addr in &own_addrs {
                    let aq = sq(addr);
                    for u in uid_search_set(conn, &format!("(FROM {aq} SUBJECT {})", sq("Reminder (Odysseus):"))) {
                        uids.insert(u);
                    }
                    for u in uid_search_set(conn, &format!("(FROM {aq} SUBJECT {})", sq("Reminder:"))) {
                        uids.insert(u);
                    }
                }
                if uids.is_empty() {
                    continue;
                }
                let mut sorted: Vec<u32> = uids.into_iter().collect();
                sorted.sort_unstable();
                for uid in sorted {
                    let uid_s = uid.to_string();
                    // mirrors Python email_routes.py:1675-1682: the copy-OK and copy-fail
                    // branches deliberately run the SAME `+FLAGS (\Deleted)` store (the
                    // COPY is best-effort), so the identical blocks are faithful.
                    #[allow(clippy::if_same_then_else)]
                    if permanent {
                        let _ = conn.uid_store(&uid_s, "+FLAGS (\\Deleted)");
                    } else if conn.uid_copy(&uid_s, crate::routes::email_helpers::q("Trash")).is_ok() {
                        let _ = conn.uid_store(&uid_s, "+FLAGS (\\Deleted)");
                    } else {
                        let _ = conn.uid_store(&uid_s, "+FLAGS (\\Deleted)");
                    }
                    deleted += 1;
                }
                let _ = conn.expunge();
            }
            (deleted, folders_checked)
        })
    })
    .await
    .map_err(|e| e.to_string())
    .and_then(|r| r);

    match res {
        Ok((deleted, folders_checked)) => {
            invalidate_list_cache(account_id.as_deref(), None);
            Ok(Json(json!({"success": true, "deleted": deleted, "folders_checked": folders_checked})).into_response())
        }
        Err(e) => {
            logger::error(&format!("delete_odysseus_reminder_emails failed: {e}"));
            Ok(Json(json!({"success": false, "error": "Mail operation failed"})).into_response())
        }
    }
}

/// `conn.uid("SEARCH", None, criteria)` -> the set of matching UIDs.
fn uid_search_set(conn: &mut ImapSession, criteria: &str) -> Vec<u32> {
    match conn.uid_search(criteria) {
        Ok(set) => set.into_iter().collect(),
        Err(_) => Vec::new(),
    }
}

/// `EMAIL_COMPOSE_UPLOAD_MAX_BYTES = 25 * 1024 * 1024` — 25 MB cap matching
/// typical SMTP limits w/ base64 overhead (mirrors commit 193dc2f).
const EMAIL_COMPOSE_UPLOAD_MAX_BYTES: usize = 25 * 1024 * 1024;

/// `POST /api/email/compose-upload` — stage a file for a compose attachment.
async fn compose_upload(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    mut mp: Multipart,
) -> Result<Response, HttpException> {
    let _owner = auth_adapter::require_owner(user_str(user).as_deref(), &s, None, host_str(ci).as_deref())?;
    // Pull the `file` field; use the bounded read helper so oversized bodies
    // are rejected without materializing the entire payload in memory.
    let mut filename: Option<String> = None;
    let mut content: Option<Vec<u8>> = None;
    while let Ok(Some(field)) = mp.next_field().await {
        if field.name() == Some("file") {
            filename = field.file_name().map(str::to_string);
            // `read_upload_limited` mirrors `await read_upload_limited(file,
            // EMAIL_COMPOSE_UPLOAD_MAX_BYTES, "Attachment")` from Python — it
            // streams chunks and returns 413 as soon as the cap is exceeded.
            let bytes = crate::src::upload_limits::read_upload_limited(
                field,
                EMAIL_COMPOSE_UPLOAD_MAX_BYTES,
                "Attachment",
            )
            .await?;
            content = Some(bytes);
            break;
        }
    }
    let res = (|| -> Result<Value, HttpException> {
        let raw_name = filename.unwrap_or_else(|| "file".to_string());
        let safe_name = FILENAME_SANITIZE_RE.replace_all(&raw_name, "_").trim().to_string();
        let token = format!("{}_{}", uuid::Uuid::new_v4().simple(), safe_name);
        let filepath = compose_uploads_dir().join(&token);
        let content = content.unwrap_or_default();
        std::fs::write(&filepath, &content).map_err(|_| HttpException::new(500, "write failed"))?;
        Ok(json!({"success": true, "token": token, "filename": safe_name, "size": content.len()}))
    })();
    match res {
        Ok(v) => Ok(Json(v).into_response()),
        Err(e) if e.status_code == 413 => Err(e),
        Err(_) => {
            logger::error("Failed to upload attachment");
            Ok(Json(json!({"success": false, "error": "Mail operation failed"})).into_response())
        }
    }
}

static FILENAME_SANITIZE_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"[^\w\s\-.]").unwrap());

/// `DELETE /api/email/compose-upload/{token}` — delete a staged upload.
async fn delete_compose_upload(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Path(token): Path<String>,
) -> Result<Response, HttpException> {
    let _owner = auth_adapter::require_owner(user_str(user).as_deref(), &s, None, host_str(ci).as_deref())?;
    // Prevent path traversal: Path(token).name.
    if let Some(name) = std::path::Path::new(&token).file_name() {
        let filepath = compose_uploads_dir().join(name);
        if filepath.exists() {
            let _ = std::fs::remove_file(&filepath);
        }
    }
    Ok(Json(json!({"success": true})).into_response())
}

// ===========================================================================
// Outgoing MIME builder (the `MIMEMultipart` / `MIMEText` analogue). Produces
// the raw RFC822 bytes `send_smtp_message` / IMAP `append` consume, matching
// Python's `outer.as_string()` / `as_bytes()` header set + multipart layout.
// ===========================================================================

use base64::Engine as _;

/// A header pair appended to the outgoing message, in order.
type Header = (String, String);

// ---------------------------------------------------------------------------
// RFC 2047 encoded-word header encoding (the `email.header.Header` analogue).
//
// Python's `MIMEText`/`MIMEMultipart` RFC2047-encode any header value that is
// not pure-ASCII when the message is serialized (`as_string()`/`as_bytes()`).
// ASCII values pass through verbatim. Non-ASCII values become one or more
// `=?utf-8?b?...?=` / `=?utf-8?q?...?=` encoded-words, folded across lines.
//
// We mirror `email.header.Header(value, charset="utf-8",
// header_name=name).encode()` for the utf-8 charset (the only charset the
// MIME builders use):
//   * pure-ASCII -> emitted RAW (no folding — compat32 leaves a short ASCII
//     header untouched, and the values the routes emit are well under the
//     line limit);
//   * non-ASCII -> SHORTEST encoder (whichever of base64/Q yields the shorter
//     encoded text for the FULL value) picked once, then the value is split
//     per-character into encoded-words so each line's encoded text stays
//     within the budget, exactly as `Charset.header_encode_lines` does. The
//     first line budget accounts for the `"<name>: "` prefix; continuation
//     lines are prefixed with `"\r\n "` (the folding whitespace).
// ---------------------------------------------------------------------------

/// `RFC2047_CHROME_LEN` (`=?`, `?b?`/`?q?`, `?=`) + `len("utf-8")` — the fixed
/// per-encoded-word overhead `header_encode_lines` subtracts from each budget.
const RFC2047_EXTRA: usize = "utf-8".len() + 7;
/// `MAXLINELEN = 78` (RFC 2822 recommendation).
const RFC2047_MAXLINELEN: usize = 78;

/// `email.quoprimime.header_length(bytes)` — the Q-encoded length of `bytes`:
/// safe header octets (`-!*+/`, ASCII letters/digits, and space) cost 1, every
/// other octet costs 3 (`=XX`).
fn q_header_length(bytes: &[u8]) -> usize {
    bytes.iter().map(|&b| if q_safe(b) { 1 } else { 3 }).sum()
}

/// `email.base64mime.header_length(bytes)` — `ceil(len/3) * 4`.
fn b64_header_length(bytes: &[u8]) -> usize {
    let (groups, leftover) = (bytes.len() / 3, bytes.len() % 3);
    groups * 4 + if leftover != 0 { 4 } else { 0 }
}

/// `_QUOPRI_HEADER_MAP` safe-byte predicate: a byte that Q-encoding leaves as a
/// single character. The space (0x20) maps to `_`, which is also a single char,
/// so it is treated as "safe" for length purposes here (the actual `_`
/// substitution happens in [`q_encode_word`]).
fn q_safe(b: u8) -> bool {
    b == b'-'
        || b == b'!'
        || b == b'*'
        || b == b'+'
        || b == b'/'
        || b == b' '
        || b.is_ascii_alphanumeric()
}

/// `email.quoprimime.header_encode` body — translate each octet via the header
/// map (space -> `_`, safe -> verbatim, else `=XX` uppercase), no chrome.
fn q_encode_word(bytes: &[u8]) -> String {
    let mut out = String::new();
    for &b in bytes {
        if b == b' ' {
            out.push('_');
        } else if q_safe(b) {
            out.push(b as char);
        } else {
            out.push_str(&format!("={b:02X}"));
        }
    }
    out
}

/// `_get_encoder` SHORTEST decision: base64 iff its encoded length is strictly
/// shorter than Q's (ties go to Q, matching CPython's `if len64 < lenqp`).
fn rfc2047_use_base64(bytes: &[u8]) -> bool {
    b64_header_length(bytes) < q_header_length(bytes)
}

/// `email.charset.Charset.header_encode_lines` for utf-8 — split `value` into
/// encoded-words so each line's encoded text fits its budget, returning the
/// chrome-wrapped `=?utf-8?X?...?=` words in order. `budgets` yields the
/// successive line maxima (RFC2047 chrome NOT counted); the last budget repeats.
fn rfc2047_encode_words(value: &str, budgets: &[usize], use_base64: bool) -> Vec<String> {
    let mut lines: Vec<String> = Vec::new();
    let mut current: String = String::new();
    let mut budget_idx = 0usize;
    // maxlen for the current line = budget - extra (never below 1).
    let next_budget = |idx: &mut usize| -> usize {
        let b = budgets[(*idx).min(budgets.len() - 1)];
        *idx += 1;
        b.saturating_sub(RFC2047_EXTRA).max(1)
    };
    let mut maxlen = next_budget(&mut budget_idx);
    let encoded_len = |s: &str| -> usize {
        let bytes = s.as_bytes();
        if use_base64 {
            b64_header_length(bytes)
        } else {
            q_header_length(bytes)
        }
    };
    let emit = |s: &str| -> String {
        if use_base64 {
            format!(
                "=?utf-8?b?{}?=",
                base64::engine::general_purpose::STANDARD.encode(s.as_bytes())
            )
        } else {
            format!("=?utf-8?q?{}?=", q_encode_word(s.as_bytes()))
        }
    };
    for ch in value.chars() {
        current.push(ch);
        if encoded_len(&current) > maxlen {
            // This char doesn't fit — pop it and flush what we have.
            current.pop();
            if lines.is_empty() && current.is_empty() {
                // Nothing fit on the first line; CPython appends a None
                // placeholder and keeps going. We just start fresh on the
                // next budget (the char will be retried on a wider line).
                lines.push(String::new());
            } else {
                lines.push(emit(&current));
            }
            current = ch.to_string();
            maxlen = next_budget(&mut budget_idx);
        }
    }
    lines.push(emit(&current));
    lines.retain(|l| !l.is_empty());
    lines
}

/// `email.header.Header(value, charset="utf-8", header_name=name).encode()` for
/// the values the MIME builders emit. Returns the header VALUE only (without the
/// `"<name>: "` prefix), RFC2047-folded when non-ASCII, RAW when pure-ASCII.
fn rfc2047_encode_header(name: &str, value: &str) -> String {
    if value.is_ascii() {
        // compat32 leaves a (short) ASCII header value untouched.
        return value.to_string();
    }
    let use_base64 = rfc2047_use_base64(value.as_bytes());
    // First line is shortened by `len(name) + 2` ("<name>: "); continuation
    // lines are shortened by the one-space folding whitespace.
    let first = RFC2047_MAXLINELEN.saturating_sub(name.len() + 2);
    let rest = RFC2047_MAXLINELEN.saturating_sub(1);
    let words = rfc2047_encode_words(value, &[first, rest], use_base64);
    // Fold: each encoded-word on its own line, continuation prefixed "\r\n "
    // (the builders use CRLF line endings; the IMAP/SMTP wire wants CRLF).
    words.join("\r\n ")
}

/// Emit `"<name>: <rfc2047(value)>\r\n"` for each header in order.
fn render_headers(headers: &[Header]) -> String {
    let mut out = String::new();
    for (k, v) in headers {
        out.push_str(&format!("{k}: {}\r\n", rfc2047_encode_header(k, v)));
    }
    out
}

/// Encode a text body as a `Content-Type: <ct>; charset="utf-8"` base64 MIME
/// part (the `MIMEText(body, subtype, "utf-8")` analogue; utf-8 MIMEText uses
/// base64 transfer-encoding).
fn mime_text_part(subtype: &str, body: &str) -> String {
    let b64 = base64::engine::general_purpose::STANDARD.encode(body.as_bytes());
    let wrapped = b64
        .as_bytes()
        .chunks(76)
        .map(|c| String::from_utf8_lossy(c).to_string())
        .collect::<Vec<_>>()
        .join("\r\n");
    format!(
        "Content-Type: text/{subtype}; charset=\"utf-8\"\r\n\
         MIME-Version: 1.0\r\n\
         Content-Transfer-Encoding: base64\r\n\r\n{wrapped}"
    )
}

/// Encode an attachment as a base64 MIME part with a Content-Disposition.
fn mime_attachment_part(maintype: &str, subtype: &str, filename: &str, data: &[u8]) -> String {
    let b64 = base64::engine::general_purpose::STANDARD.encode(data);
    let wrapped = b64
        .as_bytes()
        .chunks(76)
        .map(|c| String::from_utf8_lossy(c).to_string())
        .collect::<Vec<_>>()
        .join("\r\n");
    format!(
        "Content-Type: {maintype}/{subtype}\r\n\
         MIME-Version: 1.0\r\n\
         Content-Transfer-Encoding: base64\r\n\
         Content-Disposition: attachment; filename=\"{filename}\"\r\n\r\n{wrapped}"
    )
}

/// Build a `multipart/alternative` (plain+html) body block; returns
/// `(boundary, block_without_leading_headers)` so it can be the message body or a
/// nested part. The returned string is the FULL part incl. its own Content-Type.
fn build_alternative(plain: &str, html: &str) -> String {
    let boundary = format!("==ody-alt-{}==", uuid::Uuid::new_v4().simple());
    let mut out = format!("Content-Type: multipart/alternative; boundary=\"{boundary}\"\r\nMIME-Version: 1.0\r\n\r\n");
    out.push_str(&format!("--{boundary}\r\n"));
    out.push_str(&mime_text_part("plain", plain));
    out.push_str(&format!("\r\n--{boundary}\r\n"));
    out.push_str(&mime_text_part("html", html));
    out.push_str(&format!("\r\n--{boundary}--\r\n"));
    out
}

/// Assemble the full outgoing message bytes. `headers` are the top-level headers
/// (From/To/Subject/Date/Message-ID/...). When `attachments` is empty the
/// alternative block is the top-level body; otherwise a `multipart/mixed` wraps
/// the alternative + each attachment.
fn build_message(
    headers: &[Header],
    plain: &str,
    html: &str,
    attachments: &[crate::routes::email_helpers::ComposeAttachment],
) -> Vec<u8> {
    let mut top = String::new();
    if attachments.is_empty() {
        // Top-level = multipart/alternative; merge its Content-Type into headers.
        let boundary = format!("==ody-alt-{}==", uuid::Uuid::new_v4().simple());
        top.push_str(&render_headers(headers));
        top.push_str(&format!("Content-Type: multipart/alternative; boundary=\"{boundary}\"\r\n"));
        top.push_str("MIME-Version: 1.0\r\n\r\n");
        top.push_str(&format!("--{boundary}\r\n"));
        top.push_str(&mime_text_part("plain", plain));
        top.push_str(&format!("\r\n--{boundary}\r\n"));
        top.push_str(&mime_text_part("html", html));
        top.push_str(&format!("\r\n--{boundary}--\r\n"));
    } else {
        let boundary = format!("==ody-mix-{}==", uuid::Uuid::new_v4().simple());
        top.push_str(&render_headers(headers));
        top.push_str(&format!("Content-Type: multipart/mixed; boundary=\"{boundary}\"\r\n"));
        top.push_str("MIME-Version: 1.0\r\n\r\n");
        top.push_str(&format!("--{boundary}\r\n"));
        top.push_str(&build_alternative(plain, html));
        for att in attachments {
            top.push_str(&format!("\r\n--{boundary}\r\n"));
            top.push_str(&mime_attachment_part(&att.maintype, &att.subtype, &att.filename, &att.data));
        }
        top.push_str(&format!("\r\n--{boundary}--\r\n"));
    }
    top.into_bytes()
}

/// Build a single `text/plain` (or alternative when html given) draft message
/// (the `/draft` shape: `MIMEText` or a small `multipart/alternative`).
fn build_draft_message(headers: &[Header], plain: &str, html: Option<&str>) -> Vec<u8> {
    let mut top = String::new();
    match html {
        Some(html) => {
            let boundary = format!("==ody-alt-{}==", uuid::Uuid::new_v4().simple());
            top.push_str(&render_headers(headers));
            top.push_str(&format!("Content-Type: multipart/alternative; boundary=\"{boundary}\"\r\n"));
            top.push_str("MIME-Version: 1.0\r\n\r\n");
            top.push_str(&format!("--{boundary}\r\n"));
            top.push_str(&mime_text_part("plain", plain));
            top.push_str(&format!("\r\n--{boundary}\r\n"));
            top.push_str(&mime_text_part("html", html));
            top.push_str(&format!("\r\n--{boundary}--\r\n"));
        }
        None => {
            let b64 = base64::engine::general_purpose::STANDARD.encode(plain.as_bytes());
            let wrapped = b64
                .as_bytes()
                .chunks(76)
                .map(|c| String::from_utf8_lossy(c).to_string())
                .collect::<Vec<_>>()
                .join("\r\n");
            top.push_str(&render_headers(headers));
            top.push_str("Content-Type: text/plain; charset=\"utf-8\"\r\n");
            top.push_str("MIME-Version: 1.0\r\n");
            top.push_str("Content-Transfer-Encoding: base64\r\n\r\n");
            top.push_str(&wrapped);
            top.push_str("\r\n");
        }
    }
    top.into_bytes()
}

/// The serialized data the `/send` delivery closure needs (so it can run detached).
struct DeliverCtx {
    cfg: EmailConfig,
    from_addr: String,
    recipients: Vec<String>,
    message_bytes: Vec<u8>,
    to_label: String,
    subject: String,
    attachments: Vec<String>,
    message_id: String,
    account_id: Option<String>,
    in_reply_to: String,
    owner: String,
}

/// `_deliver()` — SMTP send, append to Sent, auto-mark source `\Answered`. Runs in
/// `spawn_blocking`. Returns the delivery result dict.
fn deliver(ctx: DeliverCtx) -> Value {
    if let Err(e) = send_smtp_message(
        &ctx.cfg,
        &ctx.from_addr,
        &ctx.recipients,
        &ctx.message_bytes,
        60,
    ) {
        logger::error(&format!("Failed to send email to {}: {e}", ctx.to_label));
        let msg = if e.trim().is_empty() { "Failed to send email".to_string() } else { e };
        return json!({"success": false, "error": msg});
    }
    logger::info(&format!("Email sent to {}: {}", ctx.to_label, ctx.subject));
    let mut delivery = json!({
        "success": true,
        "account_id": ctx.cfg.account_id.clone().or_else(|| ctx.account_id.clone()),
        "sent_folder": Value::Null,
        "sent_uid": Value::Null,
        "message_id": ctx.message_id.clone(),
    });

    // Append to Sent + auto-mark source answered (best-effort).
    let _ = with_imap(ctx.account_id.as_deref(), &ctx.owner, |imap| {
        let sent_folder = detect_sent_folder(imap);
        let mut sent_uid: Option<String> = None;
        // Read the APPENDUID UID from the append response FIRST (Python's
        // `re.search(rb"APPENDUID\s+\d+\s+(\d+)", append_data[0])`). Only present on a
        // UIDPLUS server; absent otherwise, falling through to the Message-ID search.
        if let Ok(appended) = imap.append(&sent_folder, &ctx.message_bytes).flag(imap::types::Flag::Seen).finish() {
            if let Some(uids) = appended.uids {
                if let Some(imap_proto::UidSetMember::Uid(u)) = uids.last() {
                    sent_uid = Some(u.to_string());
                }
            }
        }
        // Resolve the sent UID by Message-ID search in Sent (fallback only).
        if sent_uid.is_none() && imap.examine(&sent_folder).is_ok() {
            let mid = ctx
                .message_id
                .trim()
                .trim_start_matches('<')
                .trim_end_matches('>')
                .replace('"', "\\\"");
            if let Ok(set) = imap.uid_search(format!("HEADER Message-ID \"{mid}\"")) {
                let mut v: Vec<u32> = set.into_iter().collect();
                v.sort_unstable();
                if let Some(last) = v.last() {
                    sent_uid = Some(last.to_string());
                }
            }
        }
        // Auto-mark the source as Answered.
        if !ctx.in_reply_to.is_empty() {
            let mid = ctx
                .in_reply_to
                .trim()
                .trim_start_matches('<')
                .trim_end_matches('>')
                .replace('"', "\\\"");
            let candidates = [
                "INBOX".to_string(),
                sent_folder.clone(),
                "Sent".to_string(),
                "[Gmail]/Sent Mail".to_string(),
                "Archive".to_string(),
                "All Mail".to_string(),
                "[Gmail]/All Mail".to_string(),
            ];
            let mut seen: HashSet<String> = HashSet::new();
            for folder_name in candidates {
                if !seen.insert(folder_name.clone()) {
                    continue;
                }
                if imap.select(&folder_name).is_err() {
                    continue;
                }
                if let Ok(set) = imap.search(format!("HEADER Message-ID \"{mid}\"")) {
                    if !set.is_empty() {
                        for u in set {
                            let _ = imap.store(u.to_string(), "+FLAGS (\\Answered)");
                        }
                        logger::info(&format!("Marked source as \\Answered in {folder_name}"));
                        break;
                    }
                }
            }
        }
        delivery = json!({
            "success": true,
            "account_id": ctx.cfg.account_id.clone().or_else(|| ctx.account_id.clone()),
            "sent_folder": sent_folder,
            "sent_uid": sent_uid,
            "message_id": ctx.message_id.clone(),
        });
    });

    cleanup_compose_uploads(&ctx.attachments);
    delivery
}

/// `POST /api/email/send` — queue (or wait for) SMTP delivery.
pub(crate) async fn send_email(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let owner = auth_adapter::require_owner(user_str(user).as_deref(), &s, None, host_str(ci).as_deref())?;
    let req: SendEmailRequest = serde_json::from_slice(&body)
        .map_err(|e| HttpException::with_detail(422, json!([{ "type": "value_error", "msg": e.to_string() }])))?;

    // Body-based account_id — re-check ownership.
    if let Some(aid) = req.account_id.as_deref().filter(|a| !a.is_empty()) {
        auth_adapter::assert_owns_account(aid, &owner)?;
    }
    let cfg = match resolve_send_config(req.account_id.as_deref(), &owner) {
        Ok(c) => c,
        Err(e) => {
            let msg = if e.is_empty() { "No SMTP-capable email account configured".to_string() } else { e };
            return Ok(Json(json!({"success": false, "error": msg})).into_response());
        }
    };

    let attachments = req.attachments.clone().unwrap_or_default();
    logger::info(&format!(
        "Sending email to {}: subject={:?}, attachments={:?}",
        req.to, req.subject, attachments
    ));

    // Build the message bytes.
    let plain = req.body.clone();
    let html = req
        .body_html
        .as_deref()
        .filter(|h| !h.is_empty())
        .and_then(sanitize_email_html)
        .unwrap_or_else(|| md_to_email_html(&req.body));
    let message_id = make_msgid("odysseus.local");

    let mut headers: Vec<Header> = Vec::new();
    headers.push(("From".into(), cfg.from_address.clone()));
    headers.push(("To".into(), req.to.clone()));
    if let Some(cc) = req.cc.as_deref().filter(|c| !c.is_empty()) {
        headers.push(("Cc".into(), cc.to_string()));
    }
    headers.push(("Subject".into(), req.subject.clone()));
    headers.push(("Date".into(), rfc822_date_now()));
    headers.push(("Message-ID".into(), message_id.clone()));
    if let Some(irt) = req.in_reply_to.as_deref().filter(|c| !c.is_empty()) {
        headers.push(("In-Reply-To".into(), irt.to_string()));
    }
    if let Some(refs) = req.references.as_deref().filter(|c| !c.is_empty()) {
        headers.push(("References".into(), refs.to_string()));
    }
    if let Some(kind) = req.odysseus_kind.as_deref().filter(|c| !c.is_empty()) {
        for (k, v) in odysseus_headers(Some(kind), None) {
            headers.push((k, v));
        }
    }
    let compose_atts = attach_compose_uploads(&attachments);
    let message_bytes = build_message(&headers, &plain, &html, &compose_atts);

    // Build the recipient list via the address-grammar parse (so display names
    // with commas — `"Smith, John" <j@corp.com>` — don't get split into broken
    // envelope addresses). `_envelope_recipients(req.to, req.cc, req.bcc)`.
    let recipients = envelope_recipients(&[
        req.to.as_str(),
        req.cc.as_deref().unwrap_or(""),
        req.bcc.as_deref().unwrap_or(""),
    ]);

    let ctx = DeliverCtx {
        cfg: cfg.clone(),
        from_addr: cfg.from_address.clone(),
        recipients,
        message_bytes,
        to_label: req.to.clone(),
        subject: req.subject.clone(),
        attachments,
        message_id,
        account_id: cfg.account_id.clone().or_else(|| req.account_id.clone()),
        in_reply_to: req.in_reply_to.clone().unwrap_or_default().trim().to_string(),
        owner,
    };

    if req.wait_for_delivery {
        let to = req.to.clone();
        let result = tokio::task::spawn_blocking(move || deliver(ctx))
            .await
            .unwrap_or_else(|_| json!({"success": false, "error": "Failed to send email"}));
        if result.get("success").and_then(|v| v.as_bool()) == Some(true) {
            let m = result.as_object().cloned().unwrap_or_default();
            // {"success": True, "queued": False, "message": ..., **result}
            let mut out = Map::new();
            out.insert("success".into(), json!(true));
            out.insert("queued".into(), json!(false));
            out.insert("message".into(), json!(format!("Email sent to {to}")));
            for (k, v) in m.into_iter() {
                out.insert(k, v);
            }
            return Ok(Json(Value::Object(out)).into_response());
        }
        return Ok(Json(result).into_response());
    }

    // Queued: fire-and-forget (the BackgroundTasks.add_task analogue).
    let queued_account = cfg.account_id.clone().or_else(|| req.account_id.clone());
    let to = req.to.clone();
    tokio::spawn(async move {
        let _ = tokio::task::spawn_blocking(move || deliver(ctx)).await;
    });
    Ok(Json(json!({
        "success": true,
        "queued": true,
        "account_id": queued_account,
        "message": format!("Email queued for {to}"),
    })).into_response())
}

/// `POST /api/email/draft` — save a draft to IMAP Drafts.
pub(crate) async fn save_draft(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let owner = auth_adapter::require_owner(user_str(user).as_deref(), &s, None, host_str(ci).as_deref())?;
    let req: SendEmailRequest = serde_json::from_slice(&body)
        .map_err(|e| HttpException::with_detail(422, json!([{ "type": "value_error", "msg": e.to_string() }])))?;
    if let Some(aid) = req.account_id.as_deref().filter(|a| !a.is_empty()) {
        auth_adapter::assert_owns_account(aid, &owner)?;
    }
    let cfg = get_email_config(req.account_id.as_deref(), &owner);

    let draft_html = req
        .body_html
        .as_deref()
        .filter(|h| !h.is_empty())
        .and_then(sanitize_email_html);
    let mut headers: Vec<Header> = Vec::new();
    headers.push(("From".into(), cfg.from_address.clone()));
    headers.push(("To".into(), req.to.clone()));
    if let Some(cc) = req.cc.as_deref().filter(|c| !c.is_empty()) {
        headers.push(("Cc".into(), cc.to_string()));
    }
    if let Some(bcc) = req.bcc.as_deref().filter(|c| !c.is_empty()) {
        headers.push(("Bcc".into(), bcc.to_string()));
    }
    headers.push(("Subject".into(), req.subject.clone()));
    headers.push(("Date".into(), rfc822_date_now()));
    if let Some(irt) = req.in_reply_to.as_deref().filter(|c| !c.is_empty()) {
        headers.push(("In-Reply-To".into(), irt.to_string()));
    }
    if let Some(refs) = req.references.as_deref().filter(|c| !c.is_empty()) {
        headers.push(("References".into(), refs.to_string()));
    }
    let message_bytes = build_draft_message(&headers, &req.body, draft_html.as_deref());
    let account_id = req.account_id.clone();
    let subject = req.subject.clone();

    let err: Option<String> = tokio::task::spawn_blocking(move || {
        with_imap(account_id.as_deref(), &owner, |imap| {
            let drafts_folder = detect_drafts_folder(imap);
            imap.append(&drafts_folder, &message_bytes)
                .flag(imap::types::Flag::Draft)
                .finish()
                .err()
                .map(|e| e.to_string())
        })
        .unwrap_or_else(Some)
    })
    .await
    .unwrap_or_else(|e| Some(e.to_string()));

    match err {
        Some(e) => {
            logger::error(&format!("Failed to save draft: {e}"));
            Ok(Json(json!({"success": false, "error": e})).into_response())
        }
        None => {
            logger::info(&format!("Draft saved: {subject}"));
            Ok(Json(json!({"success": true, "message": "Draft saved"})).into_response())
        }
    }
}

/// `POST /api/email/schedule` — schedule an email for future delivery.
async fn schedule_email(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let owner = auth_adapter::require_owner(user_str(user).as_deref(), &s, None, host_str(ci).as_deref())?;
    let req: Value = serde_json::from_slice(&body).unwrap_or(json!({}));
    let gets = |k: &str| req.get(k).and_then(|v| v.as_str()).unwrap_or("").to_string();

    let res = (|| -> Result<Value, HttpException> {
        let mut send_at = gets("send_at");
        if send_at.is_empty() {
            return Ok(json!({"success": false, "error": "send_at required (ISO8601 UTC)"}));
        }
        let acct = req.get("account_id").and_then(|v| v.as_str()).filter(|s| !s.is_empty());
        if let Some(aid) = acct {
            auth_adapter::assert_owns_account(aid, &owner)?;
        }
        // Validate parseable + reject past times. The Python parses
        // `fromisoformat(send_at.replace("Z","+00:00"))`: if the value carries a
        // tzinfo (a Z suffix or numeric offset) it compares against an aware
        // now(utc); a naive value compares against utcnow(). Both reduce to a UTC
        // instant, so we resolve a UTC instant either way for the past-time guard
        // AND track the naive-UTC datetime for the normalized storage form.
        let normalized_input = send_at.replace('Z', "+00:00");
        let (instant, naive_utc) = match chrono::DateTime::parse_from_rfc3339(&normalized_input) {
            // Had a tzinfo (offset/Z) -> astimezone(utc).replace(tzinfo=None).
            Ok(aware) => {
                let utc = aware.with_timezone(&chrono::Utc);
                (utc, utc.naive_utc())
            }
            Err(_) => {
                // Naive ISO8601 — stays naive; treated AS UTC for the instant.
                match chrono::NaiveDateTime::parse_from_str(&send_at, "%Y-%m-%dT%H:%M:%S")
                    .or_else(|_| {
                        chrono::NaiveDateTime::parse_from_str(&send_at, "%Y-%m-%dT%H:%M:%S%.f")
                    }) {
                    Ok(naive) => (
                        chrono::DateTime::<chrono::Utc>::from_naive_utc_and_offset(naive, chrono::Utc),
                        naive,
                    ),
                    Err(_) => {
                        return Ok(json!({"success": false, "error": "send_at must be ISO8601"}))
                    }
                }
            }
        };
        if instant < chrono::Utc::now() {
            return Ok(json!({"success": false, "error": "send_at must be in the future"}));
        }
        // Normalize to naive UTC before storing: the poller selects due rows with a
        // lexicographic string compare against `datetime.utcnow().isoformat()`, so a
        // raw "+02:00"/"Z"-suffixed client string would fire hours late/early.
        send_at = naive_isoformat(naive_utc);
        let sid: String = uuid::Uuid::new_v4().simple().to_string()[..16].to_string();
        let conn = scheduled_conn().ok_or_else(|| HttpException::new(500, "db"))?;
        let to = gets("to");
        let cc = req.get("cc").and_then(|v| v.as_str()).filter(|s| !s.is_empty()).map(str::to_string);
        let bcc = req.get("bcc").and_then(|v| v.as_str()).filter(|s| !s.is_empty()).map(str::to_string);
        let subject = gets("subject");
        let body_text = gets("body");
        let in_reply_to = req.get("in_reply_to").and_then(|v| v.as_str()).filter(|s| !s.is_empty()).map(str::to_string);
        let references = req.get("references").and_then(|v| v.as_str()).filter(|s| !s.is_empty()).map(str::to_string);
        let atts = serde_json::to_string(req.get("attachments").cloned().as_ref().unwrap_or(&json!([]))).unwrap_or("[]".into());
        let account_id = req.get("account_id").and_then(|v| v.as_str()).filter(|s| !s.is_empty()).map(str::to_string);
        let kind = {
            let k = gets("odysseus_kind");
            if k.is_empty() { "scheduled".to_string() } else { k }
        };
        let created_at = crate::pydatetime::utcnow_naive_iso();
        conn.execute(
            "INSERT INTO scheduled_emails \
               (id, to_addr, cc, bcc, subject, body, in_reply_to, references_hdr, attachments, send_at, created_at, status, account_id, odysseus_kind, owner) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, 'pending', ?12, ?13, ?14)",
            rusqlite::params![sid, to, cc, bcc, subject, body_text, in_reply_to, references, atts, send_at, created_at, account_id, kind, owner],
        )
        .map_err(|_| HttpException::new(500, "db"))?;
        logger::info(&format!("Scheduled email {sid} for {send_at}"));
        Ok(json!({"success": true, "id": sid, "send_at": send_at}))
    })();
    match res {
        Ok(v) => Ok(Json(v).into_response()),
        Err(e) if matches!(e.status_code, 403 | 404 | 503) => Err(e),
        Err(_) => {
            logger::error("Failed to schedule email");
            Ok(Json(json!({"success": false, "error": "Mail operation failed"})).into_response())
        }
    }
}

/// `GET /api/email/scheduled` — list pending/failed scheduled emails.
async fn list_scheduled(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
) -> Result<Response, HttpException> {
    let owner = auth_adapter::require_owner(user_str(user).as_deref(), &s, None, host_str(ci).as_deref())?;
    let res = (|| -> Option<Vec<Value>> {
        let conn = scheduled_conn()?;
        let mut stmt = conn
            .prepare(
                "SELECT id, to_addr, cc, subject, send_at, created_at, status, error \
                 FROM scheduled_emails WHERE status IN ('pending', 'failed') AND owner = ? \
                 ORDER BY send_at ASC",
            )
            .ok()?;
        let rows = stmt
            .query_map(rusqlite::params![owner], |r| {
                Ok(json!({
                    "id": r.get::<_, Option<String>>(0)?,
                    "to": r.get::<_, Option<String>>(1)?,
                    "cc": r.get::<_, Option<String>>(2)?,
                    "subject": r.get::<_, Option<String>>(3)?,
                    "send_at": r.get::<_, Option<String>>(4)?,
                    "created_at": r.get::<_, Option<String>>(5)?,
                    "status": r.get::<_, Option<String>>(6)?,
                    "error": r.get::<_, Option<String>>(7)?,
                }))
            })
            .ok()?;
        Some(rows.filter_map(Result::ok).collect())
    })();
    match res {
        Some(scheduled) => Ok(Json(json!({"scheduled": scheduled})).into_response()),
        None => {
            logger::error("list_scheduled failed");
            Ok(Json(json!({"scheduled": [], "error": "Mail operation failed"})).into_response())
        }
    }
}

/// `DELETE /api/email/scheduled/{sid}` — cancel a pending scheduled email.
async fn cancel_scheduled(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Path(sid): Path<String>,
) -> Result<Response, HttpException> {
    let owner = auth_adapter::require_owner(user_str(user).as_deref(), &s, None, host_str(ci).as_deref())?;
    let ok = (|| -> Option<()> {
        let conn = scheduled_conn()?;
        conn.execute(
            "DELETE FROM scheduled_emails WHERE id = ? AND status = 'pending' AND owner = ?",
            rusqlite::params![sid, owner],
        )
        .ok()?;
        Some(())
    })();
    match ok {
        Some(()) => Ok(Json(json!({"success": true})).into_response()),
        None => {
            logger::error(&format!("cancel_scheduled {sid:?} failed"));
            Ok(Json(json!({"success": false, "error": "Mail operation failed"})).into_response())
        }
    }
}

/// `GET /api/email/resolve-contact` — search Sent/INBOX/Drafts for a contact name.
async fn resolve_contact(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let _owner = auth_adapter::require_owner(user_str(user).as_deref(), &s, None, host_str(ci).as_deref())?;
    // `name: str = Query(...)` — required.
    let name = match q.get("name") {
        Some(n) => n.clone(),
        None => {
            return Err(HttpException::with_detail(
                422,
                json!([{ "type": "missing", "loc": ["query", "name"], "msg": "Field required" }]),
            ))
        }
    };
    let name2 = name.clone();
    let res: Result<Vec<Value>, String> = tokio::task::spawn_blocking(move || {
        // Python uses `with _imap()` (no account/owner — default account).
        with_imap(None, "", |conn| {
            let mut matches: IndexMap<String, String> = IndexMap::new();
            for folder in ["Sent", "INBOX", "Drafts"] {
                if conn.examine(folder).is_err() {
                    continue;
                }
                let mut seqs = search_seqs(conn, "ALL");
                if seqs.is_empty() {
                    continue;
                }
                seqs.sort_unstable();
                // last 200, reversed.
                let tail: Vec<u32> = seqs.iter().rev().take(200).copied().collect();
                for seq in tail {
                    let fetches = match conn.fetch(seq.to_string(), "(BODY.PEEK[HEADER.FIELDS (FROM TO CC)])") {
                        Ok(f) => f,
                        Err(_) => continue,
                    };
                    for f in fetches.iter() {
                        let raw = f.header().or_else(|| f.body());
                        let msg = match raw.and_then(parse_message) {
                            Some(m) => m,
                            None => continue,
                        };
                        for field in ["from", "to", "cc"] {
                            let val = decode_header(&msg.header(field).unwrap_or_default());
                            if val.is_empty() {
                                continue;
                            }
                            for part in val.split(',') {
                                let part = part.trim();
                                if part.to_lowercase().contains(&name2.to_lowercase()) {
                                    let (disp, addr) = parseaddr(part);
                                    let addr = addr.trim().to_lowercase();
                                    if !addr.is_empty() && addr.contains('@') {
                                        let display = if disp.is_empty() { addr.clone() } else { disp };
                                        matches.entry(addr).or_insert(display);
                                    }
                                }
                            }
                        }
                    }
                }
                if matches.len() >= 10 {
                    break;
                }
            }
            matches
                .into_iter()
                .take(10)
                .map(|(addr, name)| json!({"email": addr, "name": name}))
                .collect::<Vec<_>>()
        })
    })
    .await
    .unwrap_or_else(|e| Err(e.to_string()));
    match res {
        Ok(contacts) => Ok(Json(json!({"contacts": contacts, "query": name})).into_response()),
        Err(e) => {
            logger::error(&format!("resolve_contact {name:?} failed: {e}"));
            Ok(Json(json!({"contacts": [], "error": "Mail operation failed"})).into_response())
        }
    }
}

// ===========================================================================
// LLM endpoint resolution now lives in the shared
// `crate::src::endpoint_resolver` module: `resolve_endpoint_triple(prefix, owner)`
// returns the collapse-to-None-if-model-empty shape this file relied on. The
// three call sites below thread the authed `owner` (Python email_routes.py
// 2273/2275, 2357/2359, 2497/2499 all pass `owner=owner`), so per-user model
// prefs now take effect via `get_user_setting`.
// ===========================================================================

/// `POST /api/email/extract-style` — extract writing style from sent emails.
async fn extract_writing_style(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let owner = auth_adapter::require_owner(user_str(user).as_deref(), &s, None, host_str(ci).as_deref())?;
    let req: ExtractStyleRequest = serde_json::from_slice(&body).unwrap_or(ExtractStyleRequest {
        sample_count: Some(20),
    });
    let sample_count = req.sample_count.unwrap_or(20).max(0) as usize;

    let own2 = owner.clone();
    let gathered: (Vec<String>, Option<String>) = tokio::task::spawn_blocking(move || {
        with_imap(None, &own2, |imap| -> (Vec<String>, Option<String>) {
            let sent = detect_sent_folder(imap);
            if imap.examine(&sent).is_err() {
                return (Vec::new(), Some("No sent emails found".to_string()));
            }
            let mut seqs = search_seqs(imap, "ALL");
            if seqs.is_empty() {
                return (Vec::new(), Some("No sent emails found".to_string()));
            }
            seqs.sort_unstable();
            let tail: Vec<u32> = seqs.iter().rev().take(sample_count).copied().collect();
            let mut out: Vec<String> = Vec::new();
            for seq in tail {
                let fetches = match imap.fetch(seq.to_string(), "(RFC822)") {
                    Ok(f) => f,
                    Err(_) => continue,
                };
                for f in fetches.iter() {
                    if let Some(msg) = f.body().and_then(parse_message) {
                        // `_gather_samples` extracts the FIRST text/plain part only (no
                        // HTML fallback), not the shared `_extract_text`. The length gate
                        // is `len(body) > 20` on a Python str = CHARACTER count, and the
                        // truncation is `body[:1000]` = chars.
                        let body = extract_first_plaintext(&msg);
                        if !body.trim().is_empty() && body.chars().count() > 20 {
                            out.push(body.chars().take(1000).collect());
                        }
                    }
                }
            }
            (out, None)
        })
        .unwrap_or_else(|e| (Vec::new(), Some(e)))
    })
    .await
    .unwrap_or((Vec::new(), Some("Mail operation failed".to_string())));

    let (samples, err) = gathered;
    if let Some(e) = err.filter(|_| samples.is_empty()) {
        return Ok(Json(json!({"success": false, "error": e})).into_response());
    }
    if samples.len() < 3 {
        return Ok(Json(json!({
            "success": false,
            "error": format!("Only found {} usable sent emails, need at least 3", samples.len()),
        })).into_response());
    }

    let endpoint = crate::src::endpoint_resolver::resolve_endpoint_triple("utility", Some(&owner))
        .or_else(|| crate::src::endpoint_resolver::resolve_endpoint_triple("default", Some(&owner)));
    let (url, model, headers) = match endpoint {
        Some(e) => e,
        None => {
            return Ok(Json(json!({
                "success": false,
                "error": "No LLM endpoint configured — set a Utility or Default Chat model in Settings → AI Defaults.",
            })).into_response())
        }
    };

    let sample_text = samples.iter().take(15).cloned().collect::<Vec<_>>().join("\n\n---EMAIL---\n\n");
    let messages = vec![
        json!({"role": "system", "content":
            "You are analyzing a user's email writing style. Based on the sample emails below, \
             describe their writing style in 3-5 concise sentences. Cover: tone (formal/informal), \
             typical greeting and sign-off patterns, sentence structure (short/long), \
             any distinctive phrases or habits, and overall communication approach. \
             Write this as instructions for an AI to mimic this style. \
             Start with 'Write emails in this style:'"}),
        json!({"role": "user", "content": format!("Here are {} recently sent emails:\n\n{sample_text}", samples.len())}),
    ];
    let style = match crate::src::llm_core::llm_call_async(&url, &model, messages, 1.0, 2048, headers, 300).await {
        Ok(s) => strip_think(&s),
        Err(e) => {
            logger::error(&format!("Failed to extract writing style: {e}"));
            return Ok(Json(json!({"success": false, "error": "Mail operation failed"})).into_response());
        }
    };
    if style.is_empty() {
        return Ok(Json(json!({"success": false, "error": "LLM failed to generate style description"})).into_response());
    }
    // Save to settings.
    save_email_setting("email_writing_style", json!(style));
    logger::info("Writing style extracted and saved");
    Ok(Json(json!({"success": true, "style": style})).into_response())
}

/// Update a single key in `data/settings.json` (the `_load_settings`/`_save_settings`
/// round-trip). Reuses the email_helpers store.
fn save_email_setting(key: &str, value: Value) {
    let mut settings = crate::routes::email_helpers::load_settings();
    settings.insert(key.to_string(), value);
    crate::routes::email_helpers::save_settings(&settings);
}

// ===========================================================================
// AI summarize / ai-reply, writing-style, config, urgency-state, and the
// email-accounts CRUD — the tail of `setup_email_routes()` (Python lines
// 2315-3037). These run over the same `resolve_endpoint_triple` + `llm_call_async`
// path as `extract_writing_style`.
// ===========================================================================

/// `POST /api/email/summarize` — generate a quick AI summary of an email body.
async fn summarize_email(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let owner = auth_adapter::require_owner(user_str(user).as_deref(), &s, None, host_str(ci).as_deref())?;
    let data: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);
    let dget = |k: &str| data.get(k).and_then(|v| v.as_str()).unwrap_or("").to_string();

    let result: Result<Value, String> = async {
        let body_s = dget("body");
        let subject = dget("subject");
        let sender = dget("from");
        let uid = dget("uid");
        let folder = {
            let f = dget("folder");
            if f.is_empty() { "INBOX".to_string() } else { f }
        };
        let account_id = data.get("account_id").and_then(|v| v.as_str()).map(str::to_string);
        if let Some(aid) = account_id.as_deref().filter(|a| !a.is_empty()) {
            auth_adapter::assert_owns_account(aid, &owner)
                .map_err(|_| "Mail operation failed".to_string())?;
        }
        if body_s.is_empty() {
            return Ok(json!({"success": false, "error": "No body provided"}));
        }

        // On-demand attachment-text fetch when we know the UID (so the summary can
        // reference invoice totals / contract clauses, not just the body).
        let mut att_text = String::new();
        if !uid.is_empty() {
            let aid = account_id.clone();
            let own = owner.clone();
            let uid2 = uid.clone();
            let folder2 = folder.clone();
            let fetched: Result<String, String> = tokio::task::spawn_blocking(move || {
                with_imap(aid.as_deref(), &own, |conn| {
                    // The `imap` crate auto-quotes the mailbox in examine()
                    // (validate_str -> quote!), so pass the BARE folder — a q()
                    // wrapper would DOUBLE-quote and EXAMINE would fail.
                    if conn.examine(&folder2).is_err() {
                        return String::new();
                    }
                    // SEQUENCE fetch — `uid` is the /list sequence number the
                    // frontend round-trips back; imaplib's
                    // `conn.fetch(str(uid).encode(), "(BODY.PEEK[])")`
                    // (email_routes.py:2343) is sequence-based.
                    let fetches = match conn.fetch(&uid2, "(BODY.PEEK[])") {
                        Ok(f) => f,
                        Err(_) => return String::new(),
                    };
                    for f in fetches.iter() {
                        if let Some(msg) = f.body().and_then(parse_message) {
                            return extract_attachment_text(&msg, 6000);
                        }
                    }
                    String::new()
                })
                .unwrap_or_default()
            })
            .await
            .map_err(|e| e.to_string());
            // Python swallows any error here (debug log only).
            if let Ok(t) = fetched {
                att_text = t;
            } else {
                logger::debug(&format!("on-demand summarize attachment fetch failed for uid={uid}"));
            }
        }

        let body_for_llm = if att_text.is_empty() {
            body_s.clone()
        } else {
            format!("{body_s}\n\n--- ATTACHMENTS ---\n\n{att_text}")
        };

        let endpoint = crate::src::endpoint_resolver::resolve_endpoint_triple("utility", Some(&owner))
            .or_else(|| crate::src::endpoint_resolver::resolve_endpoint_triple("default", Some(&owner)));
        let (url, model, headers) = match endpoint {
            Some(e) => e,
            None => return Ok(json!({"success": false, "error": "No LLM endpoint configured"})),
        };

        let body_trunc: String = body_for_llm.chars().take(12000).collect();
        // Raw HTTP POST (Python's `_req.post`), NOT `llm_call_async` — we need the
        // `message.reasoning_content` field and the literal HTTP status, neither of
        // which `llm_call_async` surfaces (it drops reasoning_content and raises on
        // non-ok). This mirrors `summarize_email`'s payload + `LLM HTTP {status}` +
        // reasoning_content bullet/last-paragraph salvage exactly.
        let tok_key = if crate::src::llm_core::_uses_max_completion_tokens(&model) {
            "max_completion_tokens"
        } else {
            "max_tokens"
        };
        let payload = json!({
            "model": model,
            "messages": [
                {"role": "system", "content": "You are an email summarizer. Format: 1-3 short bullet points (use '- '). Cover: main point, action items, deadlines. If the email has attachments (marked '--- ATTACHMENTS ---'), USE THEIR CONTENTS — pull invoice totals, deadlines, key clauses, concrete numbers/dates from PDFs/docs into the bullets. Be terse.\n\nOUTPUT FORMAT: Put ONLY the bullet points between these exact markers, each on its own line:\n<<<SUMMARY>>>\n- ...\n<<<END>>>\nAny reasoning must come BEFORE <<<SUMMARY>>> (ideally inside <think>...</think>). Only the text between the markers is kept."},
                {"role": "user", "content": format!("From: {sender}\nSubject: {subject}\n\n{body_trunc}\n\n---\n\nSummarize the email. Output the bullets between <<<SUMMARY>>> and <<<END>>>.")},
            ],
            tok_key: 8192,
            "temperature": 0.3,
            "stream": false,
        });
        // `req_headers = {"Content-Type": "application/json"}; req_headers.update(headers)`.
        let mut hmap = reqwest::header::HeaderMap::new();
        hmap.insert(
            reqwest::header::CONTENT_TYPE,
            reqwest::header::HeaderValue::from_static("application/json"),
        );
        for (k, v) in &headers {
            if let (Ok(name), Ok(val)) = (
                reqwest::header::HeaderName::from_bytes(k.as_bytes()),
                reqwest::header::HeaderValue::from_str(v),
            ) {
                hmap.insert(name, val);
            }
        }
        let client = match reqwest::Client::builder()
            .timeout(Duration::from_secs(180))
            .build()
        {
            Ok(c) => c,
            Err(e) => {
                logger::error(&format!("Failed to summarize: {e}"));
                return Ok(json!({"success": false, "error": "Mail operation failed"}));
            }
        };
        let resp = match client.post(&url).headers(hmap).json(&payload).send().await {
            Ok(r) => r,
            Err(e) => {
                logger::error(&format!("Failed to summarize: {e}"));
                return Ok(json!({"success": false, "error": "Mail operation failed"}));
            }
        };
        // `if not resp.ok: return {"error": f"LLM HTTP {resp.status_code}"}` —
        // requests' `.ok` is true for any 2xx/3xx (< 400).
        if resp.status().as_u16() >= 400 {
            return Ok(json!({"success": false, "error": format!("LLM HTTP {}", resp.status().as_u16())}));
        }
        let rdata: Value = match resp.json().await {
            Ok(v) => v,
            Err(e) => {
                logger::error(&format!("Failed to summarize: {e}"));
                return Ok(json!({"success": false, "error": "Mail operation failed"}));
            }
        };
        // `msg = (rdata.get("choices") or [{}])[0].get("message", {})`.
        let msg_obj = rdata
            .get("choices")
            .and_then(|c| c.as_array())
            .and_then(|c| c.first())
            .and_then(|c| c.get("message"))
            .cloned()
            .unwrap_or_else(|| json!({}));
        let raw_content = msg_obj.get("content").and_then(|c| c.as_str()).unwrap_or("").trim();
        let mut content = extract_reply(raw_content);
        if content.is_empty() {
            // Model put everything in reasoning_content — extract bullet points, else
            // the last paragraph, else rc[:500].
            let rc = msg_obj
                .get("reasoning_content")
                .and_then(|c| c.as_str())
                .unwrap_or("")
                .trim()
                .to_string();
            let bullet_lines: Vec<String> = rc
                .split('\n')
                .map(|l| l.trim())
                .filter(|s| SUMMARY_BULLET_RE.is_match(s))
                .map(|s| s.to_string())
                .collect();
            if !bullet_lines.is_empty() {
                content = bullet_lines.join("\n");
            } else {
                let paragraphs: Vec<&str> = rc
                    .split("\n\n")
                    .map(|p| p.trim())
                    .filter(|p| !p.is_empty())
                    .collect();
                content = match paragraphs.last() {
                    Some(p) => p.to_string(),
                    None => rc.chars().take(500).collect(),
                };
            }
        }
        if content.is_empty() {
            return Ok(json!({"success": false, "error": "Empty response from model"}));
        }

        // Cache the summary if we have a message_id.
        let mid = dget("message_id");
        if !mid.is_empty() {
            if let Ok(conn) = rusqlite::Connection::open(scheduled_db()) {
                let r = conn.execute(
                    "INSERT OR REPLACE INTO email_summaries \
                     (message_id, uid, folder, subject, sender, summary, model_used, created_at) \
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
                    rusqlite::params![
                        mid,
                        dget("uid"),
                        dget("folder"),
                        subject,
                        sender,
                        content,
                        model,
                        crate::pydatetime::utcnow_naive_iso()
                    ],
                );
                if let Err(e) = r {
                    logger::warning(&format!("Failed to cache summary: {e}"));
                }
            }
        }

        Ok(json!({"success": true, "summary": content, "model_used": model}))
    }
    .await;

    match result {
        Ok(v) => Ok(Json(v).into_response()),
        Err(e) => {
            logger::error(&format!("Failed to summarize: {e}"));
            Ok(Json(json!({"success": false, "error": "Mail operation failed"})).into_response())
        }
    }
}

/// `POST /api/email/ai-reply` — generate an AI-drafted reply in the user's style.
async fn ai_reply(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let owner = auth_adapter::require_owner(user_str(user).as_deref(), &s, None, host_str(ci).as_deref())?;
    let data: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);
    let dget = |k: &str| data.get(k).and_then(|v| v.as_str()).unwrap_or("").to_string();

    let result: Result<Value, String> = async {
        let to = dget("to");
        let subject = dget("subject");
        let original_body = dget("original_body");
        let requested_model = dget("model").trim().to_string();
        let session_id = dget("session_id").trim().to_string();
        let message_id = dget("message_id").trim().to_string();
        let source_uid = dget("uid").trim().to_string();
        let source_folder = {
            let f = dget("folder");
            let f = if f.is_empty() { "INBOX".to_string() } else { f };
            f.trim().to_string()
        };
        // `fast_reply = bool(data.get("fast", False))` — manual AI Reply should feel
        // immediate, so fast mode skips the heavy context mining and uses tighter
        // token/timeout budgets.
        let fast_reply = data.get("fast").map(json_truthy).unwrap_or(false);

        if original_body.is_empty() {
            return Ok(json!({"success": false, "error": "No email body provided"}));
        }

        // Reply cache: a previously generated reply for this Message-ID is served
        // instantly (after the same style-mechanics + reply-extraction normalization
        // the live path applies). `model_used` falls back to "cached".
        if !message_id.is_empty() {
            let cached: Option<(Option<String>, Option<String>)> =
                scheduled_conn().and_then(|c| {
                    c.query_row(
                        "SELECT reply, model_used FROM email_ai_replies WHERE message_id = ?",
                        rusqlite::params![message_id],
                        |r| Ok((r.get::<_, Option<String>>(0)?, r.get::<_, Option<String>>(1)?)),
                    )
                    .optional()
                    .ok()
                    .flatten()
                });
            if let Some((Some(raw_reply), model_used)) = cached {
                let cached_reply = apply_email_style_mechanics(&extract_reply(&raw_reply));
                if !cached_reply.is_empty() {
                    let model_used = model_used.filter(|m| !m.is_empty()).unwrap_or_else(|| "cached".to_string());
                    return Ok(json!({
                        "success": true,
                        "reply": cached_reply,
                        "model_used": model_used,
                        "cached": true,
                    }));
                }
            }
        }

        let settings = crate::routes::email_helpers::load_settings();
        let style = settings.get("email_writing_style").and_then(|v| v.as_str()).unwrap_or("").to_string();

        // Try the session's stored endpoint first if session_id provided.
        let mut url: Option<String> = None;
        let mut model = requested_model.clone();
        let mut headers: IndexMap<String, String> = IndexMap::new();
        if !session_id.is_empty() {
            if let Ok(conn) = crate::core::database::session_local() {
                let row: Option<(Option<String>, Option<String>, Option<String>)> = conn
                    .query_row(
                        "SELECT endpoint_url, headers, model FROM sessions WHERE id = ?1",
                        rusqlite::params![session_id],
                        |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
                    )
                    .optional()
                    .ok()
                    .flatten();
                if let Some((ep, hdrs, mdl)) = row {
                    if let Some(ep) = ep.filter(|e| !e.is_empty()) {
                        url = Some(ep);
                        // Unwrap possibly double-encoded headers (a JSON string
                        // inside the JSON column) until we have an object.
                        let mut hv: Value = match hdrs {
                            Some(s) => serde_json::from_str(&s).unwrap_or(Value::Null),
                            None => Value::Null,
                        };
                        for _ in 0..3 {
                            if let Value::String(s) = &hv {
                                hv = serde_json::from_str(s).unwrap_or(Value::Null);
                            } else {
                                break;
                            }
                        }
                        if let Value::Object(map) = hv {
                            for (k, v) in map {
                                if let Some(vs) = v.as_str() {
                                    headers.insert(k, vs.to_string());
                                }
                            }
                        }
                        if requested_model.is_empty() {
                            model = mdl.unwrap_or_default();
                        }
                    }
                }
            }
        }

        if url.is_none() {
            // Prefer the caller's Utility model, then their Default chat model.
            let (ep, fallback_model, ep_headers) = match crate::src::endpoint_resolver::resolve_endpoint_triple("utility", Some(&owner))
                .or_else(|| crate::src::endpoint_resolver::resolve_endpoint_triple("default", Some(&owner)))
            {
                Some(e) => e,
                None => (String::new(), String::new(), IndexMap::new()),
            };
            if !ep.is_empty() {
                url = Some(ep);
                headers = ep_headers;
            }
            if model.is_empty() {
                model = fallback_model;
            }
        }

        let url = match url.filter(|u| !u.is_empty()) {
            Some(u) if !model.is_empty() => u,
            _ => return Ok(json!({"success": false, "error": "No LLM endpoint configured"})),
        };

        // Resolve the model against what the endpoint actually serves (try/except
        // -> model unchanged). `list_model_ids` is a stub over the cached list here.
        let avail = crate::src::llm_core::list_model_ids(&model);
        if !avail.is_empty() && !avail.contains(&model) {
            let base = model.trim_end_matches('/').rsplit('/').next().unwrap_or("").to_string();
            let matched = avail
                .iter()
                .find(|a| a.trim_end_matches('/').rsplit('/').next().unwrap_or("") == base)
                .cloned();
            model = matched.unwrap_or_else(|| avail[0].clone());
        }

        logger::info(&format!("AI reply using model={model} url={url}"));

        // Pre-retrieval: mine names/topics from the original email, search past
        // mail + contacts (`context_snippets, _ = _pre_retrieve_context(original_body, to)`),
        // then pull the last few emails from the original sender + their attachments
        // (`referenced = _fetch_sender_thread_context(parseaddr(to)[1], ...)`). Both do
        // blocking IMAP/DB work, so run them inside spawn_blocking. The sender-thread
        // helper mirrors Python's `try/except -> ""` (it never raises internally).
        // Python defaults: max_chars_per_email=1500, max_attachment_chars=4000.
        // Skip the (blocking IMAP/DB) context mining entirely in fast mode —
        // `context_snippets, _terms = ([], [])` and `referenced = ""` stay empty.
        let (context_snippets, referenced) = if fast_reply {
            (Vec::new(), String::new())
        } else {
            let original_body = original_body.clone();
            let to = to.clone();
            let source_uid = source_uid.clone();
            let source_folder = source_folder.clone();
            let owner = owner.clone();
            tokio::task::spawn_blocking(move || {
                // Owner-scoped so pre-retrieval never crosses tenants; `account_id`
                // defaults to None (Python `_pre_retrieve_context(.., owner=owner)`).
                let (snippets, _terms) =
                    crate::routes::email_helpers::pre_retrieve_context(&original_body, &to, None, &owner);
                let from_addr_for_ctx = parseaddr(&to).1;
                let referenced = if from_addr_for_ctx.is_empty() {
                    String::new()
                } else {
                    crate::routes::email_helpers::fetch_sender_thread_context(
                        &from_addr_for_ctx,
                        &source_uid,
                        &source_folder,
                        3,
                        1500,
                        4000,
                        None,
                        &owner,
                    )
                };
                (snippets, referenced)
            })
            .await
            .unwrap_or_else(|_| (Vec::new(), String::new()))
        };

        let mut system_prompt = EMAIL_REPLY_SYS_PROMPT_BASE.to_string();
        if !style.is_empty() {
            system_prompt.push_str(&format!("\n\nWRITING STYLE TO MATCH:\n{style}"));
        }
        if !context_snippets.is_empty() {
            let joined = context_snippets.iter().take(5).cloned().collect::<Vec<_>>().join("\n\n---\n\n");
            system_prompt.push_str(&format!("\n\nRELEVANT CONTEXT FROM PAST EMAILS AND CONTACTS:\n{joined}"));
        }
        if !referenced.is_empty() {
            let ref_trunc: String = referenced.chars().take(18000).collect();
            system_prompt.push_str(&format!(
                "\n\nREFERENCED MATERIAL — the last few emails from this sender, plus any text extracted from their attachments. Use this to answer numbered questions or refer to documents they previously sent. Do NOT cite this material verbatim unless the sender directly asked about something in it.\n\n{ref_trunc}"
            ));
        }

        let body_trunc: String = original_body.chars().take(6000).collect();
        let user_msg = format!(
            "Recipient: {to}\nSubject: {subject}\n\nOriginal email and any current draft:\n{body_trunc}\n\nDraft a reply. Return only the reply body text."
        );

        // Build a dedup'd candidate chain so a stale session-stored API key (the
        // most common cause of "authentication failed" here) doesn't kill AI Reply
        // outright — fall through to the user's Utility / Default endpoints AND
        // their configured fallback chains (email_routes.py:2563-2603). Dedupe by
        // url+model. The session endpoint is tried FIRST, so the common path is
        // unchanged from the previous primary-only behavior.
        let mut candidates: Vec<(String, String, indexmap::IndexMap<String, String>)> = Vec::new();
        {
            let mut seen: std::collections::HashSet<(String, String)> = std::collections::HashSet::new();
            let mut add = |u: String, m: String, h: indexmap::IndexMap<String, String>| {
                if u.is_empty() || m.is_empty() {
                    return;
                }
                if seen.insert((u.clone(), m.clone())) {
                    candidates.push((u, m, h));
                }
            };
            // Session endpoint first (may be the broken one).
            add(url.clone(), model.clone(), headers.clone());
            // Primary utility, then default (fresh creds).
            let (uu, um, uh) = crate::src::endpoint_resolver::resolve_endpoint("utility", Some(&owner));
            add(uu.unwrap_or_default(), um.unwrap_or_default(), uh.unwrap_or_default());
            let (du, dm, dh) = crate::src::endpoint_resolver::resolve_endpoint("default", Some(&owner));
            add(du.unwrap_or_default(), dm.unwrap_or_default(), dh.unwrap_or_default());
            // Configured fallback chains last.
            for (u, m, h) in crate::src::endpoint_resolver::resolve_utility_fallback_candidates(Some(&owner)) {
                add(u, m, h);
            }
            for (u, m, h) in crate::src::endpoint_resolver::resolve_chat_fallback_candidates() {
                add(u, m, h);
            }
        }
        // `max_tokens=1024 if fast_reply else 6144`, `timeout=60 if fast_reply else 180`.
        let max_tokens: i64 = if fast_reply { 1024 } else { 6144 };
        let timeout_secs: u64 = if fast_reply { 60 } else { 180 };
        let reply = match crate::src::llm_core::llm_call_async_with_fallback(
            candidates,
            vec![
                json!({"role": "system", "content": system_prompt}),
                json!({"role": "user", "content": user_msg}),
            ],
            0.7,
            max_tokens,
            timeout_secs,
        )
        .await
        {
            Ok(r) => r,
            Err(e) => {
                let host = url.split('/').nth(2).unwrap_or(&url);
                return Ok(json!({
                    "success": false,
                    "error": format!("All endpoints failed ({model}@{host}): {e}. Check your API keys in Settings → Services."),
                }));
            }
        };

        let reply = apply_email_style_mechanics(&extract_reply(&reply));
        if reply.is_empty() {
            return Ok(json!({"success": false, "error": "LLM returned empty response"}));
        }

        // Cache so the next click is instant.
        if !message_id.is_empty() {
            if let Ok(conn) = rusqlite::Connection::open(scheduled_db()) {
                let r = conn.execute(
                    "INSERT OR REPLACE INTO email_ai_replies \
                     (message_id, uid, folder, reply, model_used, created_at) \
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                    rusqlite::params![
                        message_id,
                        source_uid,
                        source_folder,
                        reply,
                        model,
                        crate::pydatetime::utcnow_naive_iso()
                    ],
                );
                if let Err(e) = r {
                    logger::warning(&format!("Failed to cache ai_reply: {e}"));
                }
            }
        }

        Ok(json!({"success": true, "reply": reply, "model_used": model}))
    }
    .await;

    match result {
        Ok(v) => Ok(Json(v).into_response()),
        Err(e) => {
            logger::error(&format!("Failed to generate AI reply: {e}"));
            Ok(Json(json!({"success": false, "error": "Mail operation failed"})).into_response())
        }
    }
}

/// `GET /api/email/style` — get the current writing-style prompt.
async fn get_writing_style(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
) -> Result<Response, HttpException> {
    auth_adapter::require_user(user_str(user).as_deref(), &s, host_str(ci).as_deref())?;
    let settings = crate::routes::email_helpers::load_settings();
    let style = settings.get("email_writing_style").cloned().unwrap_or_else(|| json!(""));
    Ok(Json(json!({"style": style})).into_response())
}

/// `PUT /api/email/style` — manually update the writing-style prompt.
async fn update_writing_style(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    auth_adapter::require_user(user_str(user).as_deref(), &s, host_str(ci).as_deref())?;
    let data: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);
    let style = data.get("style").cloned().unwrap_or_else(|| json!(""));
    let mut settings = crate::routes::email_helpers::load_settings();
    settings.insert("email_writing_style".to_string(), style);
    crate::routes::email_helpers::save_settings(&settings);
    Ok(Json(json!({"success": true})).into_response())
}

/// Serialize an [`EmailConfig`] to the JSON dict shape the Python `_get_email_config`
/// returns (with passwords still in cleartext — the route masks them).
fn email_config_to_json(cfg: &EmailConfig) -> Map<String, Value> {
    let mut m = Map::new();
    m.insert("account_id".into(), match &cfg.account_id {
        Some(id) => json!(id),
        None => Value::Null,
    });
    m.insert("account_name".into(), json!(cfg.account_name));
    m.insert("smtp_host".into(), json!(cfg.smtp_host));
    m.insert("smtp_port".into(), json!(cfg.smtp_port));
    // `smtp_security` sits between smtp_port and smtp_user in the Python cfg dict.
    m.insert("smtp_security".into(), json!(cfg.smtp_security));
    m.insert("smtp_user".into(), json!(cfg.smtp_user));
    m.insert("smtp_password".into(), json!(cfg.smtp_password));
    m.insert("imap_host".into(), json!(cfg.imap_host));
    m.insert("imap_port".into(), json!(cfg.imap_port));
    m.insert("imap_user".into(), json!(cfg.imap_user));
    m.insert("imap_password".into(), json!(cfg.imap_password));
    m.insert("imap_starttls".into(), json!(cfg.imap_starttls));
    m.insert("from_address".into(), json!(cfg.from_address));
    m
}

/// `GET /api/email/config` — get email configuration (passwords masked).
async fn get_email_config_route(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
) -> Result<Response, HttpException> {
    let owner = auth_adapter::require_user(user_str(user).as_deref(), &s, host_str(ci).as_deref())?;
    let cfg = get_email_config(None, &owner);
    let mut m = email_config_to_json(&cfg);
    // Mask passwords: "***" when present, else "".
    let mask = |present: bool| if present { json!("***") } else { json!("") };
    m.insert("smtp_password".into(), mask(!cfg.smtp_password.is_empty()));
    m.insert("imap_password".into(), mask(!cfg.imap_password.is_empty()));
    // Include preferences from settings.json.
    let settings = crate::routes::email_helpers::load_settings();
    let flag = |k: &str| json!(settings.get(k).map(|v| v.as_bool().unwrap_or(false)).unwrap_or(false));
    m.insert("email_auto_summarize".into(), flag("email_auto_summarize"));
    m.insert("email_auto_reply".into(), flag("email_auto_reply"));
    m.insert("email_auto_tag".into(), flag("email_auto_tag"));
    m.insert("email_auto_spam".into(), flag("email_auto_spam"));
    m.insert("email_auto_calendar".into(), flag("email_auto_calendar"));
    Ok(Json(Value::Object(m)).into_response())
}

/// `PUT /api/email/config` — update email configuration. Automation flags live in
/// settings.json; credentials are written to the default `email_accounts` row.
/// Passwords overwrite only when a non-empty value is provided.
async fn update_email_config(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    auth_adapter::require_owner(user_str(user).as_deref(), &s, None, host_str(ci).as_deref())?;
    let data: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);

    // Automation flags stay in settings.json (global, not per-account).
    let mut settings = crate::routes::email_helpers::load_settings();
    for key in ["email_auto_summarize", "email_auto_reply", "email_auto_tag", "email_auto_spam", "email_auto_calendar"] {
        if let Some(v) = data.get(key) {
            settings.insert(key.to_string(), v.clone());
        }
    }
    crate::routes::email_helpers::save_settings(&settings);

    // Credentials go into the default account row.
    if let Ok(conn) = crate::core::database::session_local() {
        // Find (or create) the default row.
        let existing: Option<String> = conn
            .query_row(
                "SELECT id FROM email_accounts WHERE is_default = 1 LIMIT 1",
                [],
                |r| r.get::<_, String>(0),
            )
            .optional()
            .ok()
            .flatten();
        let now = crate::pydatetime::utcnow_naive_iso();
        let row_id = match existing {
            Some(id) => id,
            None => {
                let id = uuid::Uuid::new_v4().simple().to_string();
                let _ = conn.execute(
                    "INSERT INTO email_accounts (id, name, is_default, enabled, created_at, updated_at) \
                     VALUES (?1, 'Default', 1, 1, ?2, ?2)",
                    rusqlite::params![id, now],
                );
                id
            }
        };

        // String / port fields. `email_from` -> `from_address`. Ports skipped when
        // None/"" and coerced to int otherwise.
        let str_fields: &[(&str, &str)] = &[
            ("smtp_host", "smtp_host"),
            ("smtp_user", "smtp_user"),
            // `smtp_security` is in the Python field_map and stored as the raw
            // posted value (NOT through `_smtp_security_mode` on this PUT path).
            ("smtp_security", "smtp_security"),
            ("imap_host", "imap_host"),
            ("imap_user", "imap_user"),
            ("email_from", "from_address"),
        ];
        for (in_key, col) in str_fields {
            if let Some(v) = data.get(*in_key) {
                let val = v.as_str().unwrap_or("").to_string();
                let _ = conn.execute(
                    &format!("UPDATE email_accounts SET {col} = ?1 WHERE id = ?2"),
                    rusqlite::params![val, row_id],
                );
            }
        }
        for (in_key, col) in [("smtp_port", "smtp_port"), ("imap_port", "imap_port")] {
            if let Some(v) = data.get(in_key) {
                if let Some(port) = coerce_port(v) {
                    let _ = conn.execute(
                        &format!("UPDATE email_accounts SET {col} = ?1 WHERE id = ?2"),
                        rusqlite::params![port, row_id],
                    );
                }
            }
        }
        if let Some(v) = data.get("imap_starttls") {
            let b = json_truthy(v);
            let _ = conn.execute(
                "UPDATE email_accounts SET imap_starttls = ?1 WHERE id = ?2",
                rusqlite::params![b as i64, row_id],
            );
        }
        // Passwords: only update when a non-empty value is given (encrypted).
        if let Some(p) = data.get("imap_password").and_then(|v| v.as_str()).filter(|p| !p.is_empty()) {
            let enc = crate::src::secret_storage::encrypt(p);
            let _ = conn.execute(
                "UPDATE email_accounts SET imap_password = ?1 WHERE id = ?2",
                rusqlite::params![enc, row_id],
            );
        }
        if let Some(p) = data.get("smtp_password").and_then(|v| v.as_str()).filter(|p| !p.is_empty()) {
            let enc = crate::src::secret_storage::encrypt(p);
            let _ = conn.execute(
                "UPDATE email_accounts SET smtp_password = ?1 WHERE id = ?2",
                rusqlite::params![enc, row_id],
            );
        }
    }
    Ok(Json(json!({"success": true})).into_response())
}

/// `int(val)` parity for ports: skip None/"", coerce strings/numbers to int.
fn coerce_port(v: &Value) -> Option<i64> {
    match v {
        Value::Null => None,
        Value::String(s) if s.is_empty() => None,
        Value::String(s) => s.trim().parse::<i64>().ok(),
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)),
        _ => None,
    }
}

/// `bool(data[key])` parity — Python truthiness over a JSON value.
fn json_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(false),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `GET /api/email/urgency-state` — read-only urgency state file (per-owner slug).
async fn get_email_urgency_state(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
) -> Result<Response, HttpException> {
    let owner = auth_adapter::require_user(user_str(user).as_deref(), &s, host_str(ci).as_deref())?;
    let base = if owner.is_empty() { "default".to_string() } else { owner.clone() };
    let slug: String = base
        .chars()
        .map(|c| if c.is_alphanumeric() || "-_.@".contains(c) { c } else { '_' })
        .collect();
    let path = std::path::Path::new("data").join(format!("email_urgency_state_{slug}.json"));
    let empty = json!({"total_unread": 0, "total_urgent": 0, "max_score": 0, "per_uid": {}});
    if !path.exists() {
        return Ok(Json(empty).into_response());
    }
    let data: Value = match std::fs::read_to_string(&path).ok().and_then(|t| serde_json::from_str(&t).ok()) {
        Some(v) => v,
        None => return Ok(Json(empty).into_response()),
    };
    let mut data = data;
    // Drop `notified_uids` (internal scheduler debounce, not UI-relevant).
    if let Value::Object(m) = &mut data {
        m.remove("notified_uids");
    }
    Ok(Json(data).into_response())
}

// ═══════════════ Email Accounts CRUD ═══════════════

/// `GET /api/email/accounts` — list all email accounts (credentials masked,
/// scoped to this user's accounts + matching legacy unowned rows).
async fn list_email_accounts_route(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
) -> Result<Response, HttpException> {
    let owner = auth_adapter::require_user(user_str(user).as_deref(), &s, host_str(ci).as_deref())?;
    let mut out: Vec<Value> = Vec::new();
    if let Ok(conn) = crate::core::database::session_local() {
        // SECURITY: scope to this user's accounts + legacy unowned rows that match
        // the logged-in mailbox.
        let (sql, want_owner) = if owner.is_empty() {
            (
                "SELECT id, name, is_default, enabled, imap_host, imap_port, imap_user, imap_starttls, \
                 smtp_host, smtp_port, smtp_user, from_address, imap_password, smtp_password, smtp_security \
                 FROM email_accounts ORDER BY is_default DESC, created_at ASC"
                    .to_string(),
                false,
            )
        } else {
            (
                "SELECT id, name, is_default, enabled, imap_host, imap_port, imap_user, imap_starttls, \
                 smtp_host, smtp_port, smtp_user, from_address, imap_password, smtp_password, smtp_security \
                 FROM email_accounts \
                 WHERE owner = ?1 \
                    OR ((owner IS NULL OR owner = '') AND (imap_user = ?1 OR from_address = ?1)) \
                 ORDER BY is_default DESC, created_at ASC"
                    .to_string(),
                true,
            )
        };
        let collect = |r: &rusqlite::Row<'_>| -> rusqlite::Result<Value> {
            let imap_pw: Option<String> = r.get(12)?;
            let smtp_pw: Option<String> = r.get(13)?;
            let smtp_port: Option<i64> = r.get(9)?;
            // `_smtp_security_mode({"smtp_security": ..., "smtp_port": r.smtp_port})`
            // — uses the RAW stored smtp_port (NULL -> 465 inside the helper).
            let smtp_security = crate::routes::email_helpers::smtp_security_mode(
                r.get::<_, Option<String>>(14)?.unwrap_or_default().as_str(),
                smtp_port.unwrap_or(0),
            );
            Ok(json!({
                "id": r.get::<_, String>(0)?,
                "name": r.get::<_, Option<String>>(1)?.unwrap_or_default(),
                "is_default": r.get::<_, Option<i64>>(2)?.unwrap_or(0) != 0,
                "enabled": r.get::<_, Option<i64>>(3)?.unwrap_or(0) != 0,
                "imap_host": r.get::<_, Option<String>>(4)?.unwrap_or_default(),
                "imap_port": r.get::<_, Option<i64>>(5)?.filter(|p| *p != 0).unwrap_or(993),
                "imap_user": r.get::<_, Option<String>>(6)?.unwrap_or_default(),
                "imap_starttls": r.get::<_, Option<i64>>(7)?.unwrap_or(0) != 0,
                "smtp_host": r.get::<_, Option<String>>(8)?.unwrap_or_default(),
                "smtp_port": smtp_port.filter(|p| *p != 0).unwrap_or(465),
                "smtp_security": smtp_security,
                "smtp_user": r.get::<_, Option<String>>(10)?.unwrap_or_default(),
                "from_address": r.get::<_, Option<String>>(11)?.unwrap_or_default(),
                "has_imap_password": imap_pw.map(|p| !p.is_empty()).unwrap_or(false),
                "has_smtp_password": smtp_pw.map(|p| !p.is_empty()).unwrap_or(false),
            }))
        };
        if let Ok(mut stmt) = conn.prepare(&sql) {
            let rows = if want_owner {
                stmt.query_map(rusqlite::params![owner], collect).map(|it| it.flatten().collect::<Vec<_>>())
            } else {
                stmt.query_map([], collect).map(|it| it.flatten().collect::<Vec<_>>())
            };
            if let Ok(rows) = rows {
                out = rows;
            }
        }
    }
    Ok(Json(json!({"accounts": out})).into_response())
}

/// `POST /api/email/accounts` — create a new email account.
async fn create_email_account(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let owner = auth_adapter::require_owner(user_str(user).as_deref(), &s, None, host_str(ci).as_deref())?;
    let data: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);
    let strf = |k: &str| data.get(k).and_then(|v| v.as_str()).unwrap_or("").trim().to_string();

    let name = strf("name");
    if name.is_empty() {
        return Ok(Json(json!({"ok": false, "error": "name required"})).into_response());
    }
    let conn = match crate::core::database::session_local() {
        Ok(c) => c,
        Err(_) => return Ok(Json(json!({"ok": false, "error": "Mail operation failed"})).into_response()),
    };

    let id = uuid::Uuid::new_v4().simple().to_string();
    let mut is_default = data.get("is_default").map(json_truthy).unwrap_or(false);
    let enabled = data.get("enabled").map(json_truthy).unwrap_or(true);
    let imap_port = coerce_port(data.get("imap_port").unwrap_or(&Value::Null)).unwrap_or(993);
    let smtp_port = coerce_port(data.get("smtp_port").unwrap_or(&Value::Null)).unwrap_or(465);
    let imap_starttls = data.get("imap_starttls").map(json_truthy).unwrap_or(true);
    let imap_password = crate::src::secret_storage::encrypt(&strf("imap_password"));
    let smtp_password = crate::src::secret_storage::encrypt(&strf("smtp_password"));
    // `_smtp_security_mode({"smtp_security": data.get("smtp_security"),
    // "smtp_port": data.get("smtp_port") or 465})` — `smtp_port` is already the
    // `or 465`-defaulted value here, and the helper maps an absent value to 465.
    let smtp_security = crate::routes::email_helpers::smtp_security_mode(
        data.get("smtp_security").and_then(|v| v.as_str()).unwrap_or(""),
        smtp_port,
    );

    // Enforce the one-default invariant, scoped to THIS user's accounts.
    let existing_count: i64 = if owner.is_empty() {
        conn.query_row("SELECT COUNT(*) FROM email_accounts", [], |r| r.get(0)).unwrap_or(0)
    } else {
        conn.query_row(
            "SELECT COUNT(*) FROM email_accounts WHERE owner = ?1",
            rusqlite::params![owner],
            |r| r.get(0),
        )
        .unwrap_or(0)
    };
    if is_default || existing_count == 0 {
        if owner.is_empty() {
            let _ = conn.execute("UPDATE email_accounts SET is_default = 0", []);
        } else {
            let _ = conn.execute(
                "UPDATE email_accounts SET is_default = 0 WHERE owner = ?1",
                rusqlite::params![owner],
            );
        }
        is_default = true;
    }

    let now = crate::pydatetime::utcnow_naive_iso();
    let owner_val: Option<&str> = if owner.is_empty() { None } else { Some(owner.as_str()) };
    let r = conn.execute(
        "INSERT INTO email_accounts \
           (id, owner, name, is_default, enabled, imap_host, imap_port, imap_user, imap_password, \
            imap_starttls, smtp_host, smtp_port, smtp_security, smtp_user, smtp_password, from_address, created_at, updated_at) \
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17, ?17)",
        rusqlite::params![
            id,
            owner_val,
            name,
            is_default as i64,
            enabled as i64,
            strf("imap_host"),
            imap_port,
            strf("imap_user"),
            imap_password,
            imap_starttls as i64,
            strf("smtp_host"),
            smtp_port,
            smtp_security,
            strf("smtp_user"),
            smtp_password,
            strf("from_address"),
            now,
        ],
    );
    match r {
        Ok(_) => Ok(Json(json!({"ok": true, "id": id})).into_response()),
        Err(e) => {
            logger::error(&format!("Failed to create email account: {e}"));
            Ok(Json(json!({"ok": false, "error": "Mail operation failed"})).into_response())
        }
    }
}

/// `PUT /api/email/accounts/:account_id` — update an account (passwords overwrite
/// only when non-empty).
async fn update_email_account(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Path(account_id): Path<String>,
    body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let owner = auth_adapter::require_user(user_str(user).as_deref(), &s, host_str(ci).as_deref())?;
    auth_adapter::assert_owns_account(&account_id, &owner)?;
    let data: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);

    let conn = match crate::core::database::session_local() {
        Ok(c) => c,
        Err(_) => return Ok(Json(json!({"ok": false, "error": "Account not found"})).into_response()),
    };
    let exists: bool = conn
        .query_row(
            "SELECT 1 FROM email_accounts WHERE id = ?1",
            rusqlite::params![account_id],
            |_| Ok(()),
        )
        .optional()
        .ok()
        .flatten()
        .is_some();
    if !exists {
        return Ok(Json(json!({"ok": false, "error": "Account not found"})).into_response());
    }

    // Simple string fields (trimmed).
    for key in ["name", "imap_host", "imap_user", "smtp_host", "smtp_user", "from_address"] {
        if let Some(v) = data.get(key) {
            let val = v.as_str().unwrap_or("").trim().to_string();
            let _ = conn.execute(
                &format!("UPDATE email_accounts SET {key} = ?1 WHERE id = ?2"),
                rusqlite::params![val, account_id],
            );
        }
    }
    for key in ["imap_port", "smtp_port"] {
        if let Some(v) = data.get(key) {
            if let Some(port) = coerce_port(v) {
                let _ = conn.execute(
                    &format!("UPDATE email_accounts SET {key} = ?1 WHERE id = ?2"),
                    rusqlite::params![port, account_id],
                );
            }
        }
    }
    // `if "smtp_security" in data:` -> recompute via `_smtp_security_mode` using the
    // payload's smtp_port `or row.smtp_port` (the stored value, read back since the
    // port update above may have just changed it).
    if data.get("smtp_security").is_some() {
        // `data.get("smtp_port") or row.smtp_port`: prefer a coercible payload port,
        // else the row's current (possibly just-updated) smtp_port.
        let port = data
            .get("smtp_port")
            .and_then(coerce_port)
            .or_else(|| {
                conn.query_row(
                    "SELECT smtp_port FROM email_accounts WHERE id = ?1",
                    rusqlite::params![account_id],
                    |r| r.get::<_, Option<i64>>(0),
                )
                .ok()
                .flatten()
            })
            .unwrap_or(0);
        let mode = crate::routes::email_helpers::smtp_security_mode(
            data.get("smtp_security").and_then(|v| v.as_str()).unwrap_or(""),
            port,
        );
        let _ = conn.execute(
            "UPDATE email_accounts SET smtp_security = ?1 WHERE id = ?2",
            rusqlite::params![mode, account_id],
        );
    }
    for key in ["imap_starttls", "enabled"] {
        if let Some(v) = data.get(key) {
            let b = json_truthy(v);
            let _ = conn.execute(
                &format!("UPDATE email_accounts SET {key} = ?1 WHERE id = ?2"),
                rusqlite::params![b as i64, account_id],
            );
        }
    }
    if let Some(p) = data.get("imap_password").and_then(|v| v.as_str()).filter(|p| !p.is_empty()) {
        let enc = crate::src::secret_storage::encrypt(p);
        let _ = conn.execute(
            "UPDATE email_accounts SET imap_password = ?1 WHERE id = ?2",
            rusqlite::params![enc, account_id],
        );
    }
    if let Some(p) = data.get("smtp_password").and_then(|v| v.as_str()).filter(|p| !p.is_empty()) {
        let enc = crate::src::secret_storage::encrypt(p);
        let _ = conn.execute(
            "UPDATE email_accounts SET smtp_password = ?1 WHERE id = ?2",
            rusqlite::params![enc, account_id],
        );
    }
    Ok(Json(json!({"ok": true, "id": account_id})).into_response())
}

/// `DELETE /api/email/accounts/:account_id` — delete an account; promote the
/// next-oldest enabled owned row if the deleted one was default.
async fn delete_email_account(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Path(account_id): Path<String>,
) -> Result<Response, HttpException> {
    let owner = auth_adapter::require_user(user_str(user).as_deref(), &s, host_str(ci).as_deref())?;
    auth_adapter::assert_owns_account(&account_id, &owner)?;

    let conn = match crate::core::database::session_local() {
        Ok(c) => c,
        Err(_) => return Ok(Json(json!({"ok": false, "error": "Account not found"})).into_response()),
    };
    let was_default: Option<bool> = conn
        .query_row(
            "SELECT is_default FROM email_accounts WHERE id = ?1",
            rusqlite::params![account_id],
            |r| Ok(r.get::<_, Option<i64>>(0)?.unwrap_or(0) != 0),
        )
        .optional()
        .ok()
        .flatten();
    let was_default = match was_default {
        Some(b) => b,
        None => return Ok(Json(json!({"ok": false, "error": "Account not found"})).into_response()),
    };
    let _ = conn.execute("DELETE FROM email_accounts WHERE id = ?1", rusqlite::params![account_id]);

    if was_default {
        // Promote the next-oldest enabled row owned by THIS user.
        let promote: Option<String> = if owner.is_empty() {
            conn.query_row(
                "SELECT id FROM email_accounts WHERE enabled = 1 ORDER BY created_at ASC LIMIT 1",
                [],
                |r| r.get::<_, String>(0),
            )
        } else {
            conn.query_row(
                "SELECT id FROM email_accounts WHERE enabled = 1 AND owner = ?1 ORDER BY created_at ASC LIMIT 1",
                rusqlite::params![owner],
                |r| r.get::<_, String>(0),
            )
        }
        .optional()
        .ok()
        .flatten();
        if let Some(pid) = promote {
            let _ = conn.execute(
                "UPDATE email_accounts SET is_default = 1 WHERE id = ?1",
                rusqlite::params![pid],
            );
        }
    }
    Ok(Json(json!({"ok": true})).into_response())
}

/// `POST /api/email/accounts/test` — actually connect to the provided IMAP (and
/// optionally SMTP) server with the given credentials. Per-protocol status.
async fn test_account_config(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let owner = auth_adapter::require_user(user_str(user).as_deref(), &s, host_str(ci).as_deref())?;
    let mut body: Value = match serde_json::from_slice(&body) {
        Ok(v) => v,
        Err(_) => {
            return Ok(Json(json!({"ok": false, "imap": {"ok": false, "error": "invalid request body"}})).into_response());
        }
    };

    // Saved-account shortcut — hydrate missing credentials from the DB row,
    // keeping any edited form fields from the request. Passwords are decrypted.
    if let Some(acc_id) = body.get("account_id").and_then(|v| v.as_str()).map(str::to_string) {
        auth_adapter::assert_owns_account(&acc_id, &owner)?;
        let conn = match crate::core::database::session_local() {
            Ok(c) => c,
            Err(_) => {
                return Ok(Json(json!({"ok": false, "imap": {"ok": false, "error": "Account not found"}})).into_response());
            }
        };
        // Build the saved-credentials map directly off the row (passwords
        // decrypted), mirroring the Python `saved_body` dict.
        let saved_row: Option<serde_json::Map<String, Value>> = conn
            .query_row(
                "SELECT imap_host, imap_port, imap_user, imap_password, imap_starttls, \
                        smtp_host, smtp_port, smtp_user, smtp_password \
                 FROM email_accounts WHERE id = ?1",
                rusqlite::params![acc_id],
                |r| {
                    let dec = |o: Option<String>| crate::src::secret_storage::decrypt(o.as_deref().unwrap_or(""));
                    let mut m = serde_json::Map::new();
                    m.insert("imap_host".into(), json!(r.get::<_, Option<String>>(0)?.unwrap_or_default()));
                    m.insert("imap_port".into(), json!(r.get::<_, Option<i64>>(1)?.filter(|p| *p != 0).unwrap_or(993)));
                    m.insert("imap_user".into(), json!(r.get::<_, Option<String>>(2)?.unwrap_or_default()));
                    m.insert("imap_password".into(), json!(dec(r.get::<_, Option<String>>(3)?)));
                    m.insert("imap_starttls".into(), json!(r.get::<_, Option<i64>>(4)?.unwrap_or(0) != 0));
                    m.insert("smtp_host".into(), json!(r.get::<_, Option<String>>(5)?.unwrap_or_default()));
                    m.insert("smtp_port".into(), json!(r.get::<_, Option<i64>>(6)?.filter(|p| *p != 0).unwrap_or(465)));
                    m.insert("smtp_user".into(), json!(r.get::<_, Option<String>>(7)?.unwrap_or_default()));
                    m.insert("smtp_password".into(), json!(dec(r.get::<_, Option<String>>(8)?)));
                    Ok(m)
                },
            )
            .optional()
            .ok()
            .flatten();
        let mut saved = match saved_row {
            Some(m) => m,
            None => {
                return Ok(Json(json!({"ok": false, "imap": {"ok": false, "error": "Account not found"}})).into_response());
            }
        };
        // Overlay edited form fields (non-empty values).
        if let Value::Object(req_map) = &body {
            for (k, v) in req_map {
                if k == "account_id" {
                    continue;
                }
                let non_empty = !matches!(v, Value::Null) && !matches!(v, Value::String(s) if s.is_empty());
                if non_empty {
                    saved.insert(k.clone(), v.clone());
                }
            }
        }
        body = Value::Object(saved);
    }

    let bstr = |k: &str| body.get(k).and_then(|v| v.as_str()).unwrap_or("").trim().to_string();

    let imap_host = bstr("imap_host");
    let imap_port = coerce_port(body.get("imap_port").unwrap_or(&Value::Null)).unwrap_or(993);
    let imap_user = bstr("imap_user");
    let imap_pass = body.get("imap_password").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let imap_starttls = body.get("imap_starttls").map(json_truthy).unwrap_or(false);

    // Run the blocking IMAP/SMTP probes off the async runtime.
    let smtp_host_in = bstr("smtp_host");
    let smtp_port_in = coerce_port(body.get("smtp_port").unwrap_or(&Value::Null)).unwrap_or(465);
    let smtp_user_in = {
        let u = bstr("smtp_user");
        if u.is_empty() { imap_user.clone() } else { u }
    };
    let smtp_pass_in = {
        let p = body.get("smtp_password").and_then(|v| v.as_str()).unwrap_or("").to_string();
        if p.is_empty() { imap_pass.clone() } else { p }
    };

    let probe: Value = tokio::task::spawn_blocking(move || {
        // IMAP probe.
        let imap_result: Value = if imap_host.is_empty() || imap_user.is_empty() || imap_pass.is_empty() {
            json!({"ok": false, "error": "Need IMAP host, username, and password"})
        } else {
            match probe_imap(&imap_host, imap_port as u16, &imap_user, &imap_pass, imap_starttls) {
                Ok(()) => json!({"ok": true}),
                Err(e) => json!({"ok": false, "error": truncate_chars(&e, 200)}),
            }
        };

        // SMTP probe (only when a host is given).
        let smtp_result: Value = if smtp_host_in.is_empty() {
            Value::Null
        } else {
            match probe_smtp(&smtp_host_in, smtp_port_in as u16, &smtp_user_in, &smtp_pass_in) {
                Ok(()) => json!({"ok": true}),
                Err(e) => json!({"ok": false, "error": truncate_chars(&e, 200)}),
            }
        };

        let imap_ok = imap_result.get("ok").and_then(|v| v.as_bool()).unwrap_or(false);
        let smtp_ok = match &smtp_result {
            Value::Null => true,
            v => v.get("ok").and_then(|b| b.as_bool()).unwrap_or(false),
        };
        json!({"ok": imap_ok && smtp_ok, "imap": imap_result, "smtp": smtp_result})
    })
    .await
    .unwrap_or_else(|_| json!({"ok": false, "imap": {"ok": false, "error": "Mail operation failed"}, "smtp": Value::Null}));

    Ok(Json(probe).into_response())
}

/// `str(e)[:200]` parity over chars (not bytes).
fn truncate_chars(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// Raw IMAP connect + login probe with the same mode resolution as `imap_connect`
/// (STARTTLS on -> plain+upgrade; off+993 -> implicit TLS; off+other -> plaintext).
/// Logs out regardless (the Python `finally: conn.logout()`). Blocking.
fn probe_imap(host: &str, port: u16, user: &str, password: &str, starttls: bool) -> Result<(), String> {
    let builder = imap::ClientBuilder::new(host.to_string(), port);
    let builder = if starttls {
        builder.mode(imap::ConnectionMode::StartTls)
    } else if port == 993 {
        builder.mode(imap::ConnectionMode::Tls)
    } else {
        builder.mode(imap::ConnectionMode::Plaintext)
    };
    let client = builder.connect().map_err(|e| e.to_string())?;
    match client.login(user, password) {
        Ok(mut session) => {
            let _ = session.logout();
            Ok(())
        }
        Err((e, _client)) => Err(e.to_string()),
    }
}

/// Raw SMTP connect + login probe via lettre: port 587 -> STARTTLS, else implicit
/// TLS (`smtplib.SMTP_SSL`). Uses `test_connection()` (which authenticates).
fn probe_smtp(host: &str, port: u16, user: &str, password: &str) -> Result<(), String> {
    use lettre::transport::smtp::authentication::Credentials;
    use lettre::transport::smtp::client::{Tls, TlsParameters};
    use lettre::transport::smtp::SmtpTransport;

    let tls_params = TlsParameters::new(host.to_string()).map_err(|e| e.to_string())?;
    let tls = if port == 587 {
        Tls::Required(tls_params)
    } else {
        Tls::Wrapper(tls_params)
    };
    let transport = SmtpTransport::builder_dangerous(host)
        .port(port)
        .tls(tls)
        .timeout(Some(std::time::Duration::from_secs(10)))
        .credentials(Credentials::new(user.to_string(), password.to_string()))
        .build();
    match transport.test_connection() {
        Ok(true) => Ok(()),
        Ok(false) => Err("connection test failed".to_string()),
        Err(e) => Err(e.to_string()),
    }
}

/// `POST /api/email/accounts/:account_id/set-default` — set the account as default
/// (clearing other defaults, scoped to this user's accounts).
async fn set_default_account(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Path(account_id): Path<String>,
) -> Result<Response, HttpException> {
    let owner = auth_adapter::require_user(user_str(user).as_deref(), &s, host_str(ci).as_deref())?;
    auth_adapter::assert_owns_account(&account_id, &owner)?;

    let conn = match crate::core::database::session_local() {
        Ok(c) => c,
        Err(_) => return Ok(Json(json!({"ok": false, "error": "Account not found"})).into_response()),
    };
    let exists: bool = conn
        .query_row(
            "SELECT 1 FROM email_accounts WHERE id = ?1",
            rusqlite::params![account_id],
            |_| Ok(()),
        )
        .optional()
        .ok()
        .flatten()
        .is_some();
    if !exists {
        return Ok(Json(json!({"ok": false, "error": "Account not found"})).into_response());
    }
    // SECURITY: scope the "clear other defaults" sweep to this user's accounts.
    if owner.is_empty() {
        let _ = conn.execute("UPDATE email_accounts SET is_default = 0", []);
    } else {
        let _ = conn.execute(
            "UPDATE email_accounts SET is_default = 0 WHERE owner = ?1",
            rusqlite::params![owner],
        );
    }
    let _ = conn.execute(
        "UPDATE email_accounts SET is_default = 1 WHERE id = ?1",
        rusqlite::params![account_id],
    );
    Ok(Json(json!({"ok": true})).into_response())
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── parseaddr / getaddresses / envelope_recipients ──

    #[test]
    fn parseaddr_handles_name_and_bare_address() {
        assert_eq!(
            parseaddr("John Smith <john@corp.com>"),
            ("John Smith".to_string(), "john@corp.com".to_string())
        );
        assert_eq!(parseaddr("bare@x.com"), (String::new(), "bare@x.com".to_string()));
        assert_eq!(parseaddr("No Address"), ("No Address".to_string(), String::new()));
        // Quotes around the display name are stripped.
        assert_eq!(
            parseaddr("\"Smith, John\" <j@corp.com>"),
            ("Smith, John".to_string(), "j@corp.com".to_string())
        );
    }

    #[test]
    fn getaddresses_does_not_split_quoted_display_name_commas() {
        // The canonical Outlook form: a comma inside a quoted display name must NOT
        // split the entry into two broken addresses.
        let got = getaddresses(["\"Smith, John\" <j@corp.com>, jane@x.com"]);
        assert_eq!(
            got,
            vec![
                ("Smith, John".to_string(), "j@corp.com".to_string()),
                (String::new(), "jane@x.com".to_string()),
            ]
        );
    }

    #[test]
    fn envelope_recipients_parses_multiple_fields_and_drops_empties() {
        // to + cc + bcc, with a quoted-comma display name and an empty cc field.
        let r = envelope_recipients(&[
            "\"Doe, Jane\" <jane@x.com>, bob@y.com",
            "",
            "carol@z.com",
        ]);
        assert_eq!(r, vec!["jane@x.com", "bob@y.com", "carol@z.com"]);
        // A naive split(',') would have produced 4 bogus recipients from the first
        // field alone — assert we got exactly the three real envelope addresses.
        assert_eq!(r.len(), 3);
    }

    // ── naive_isoformat (send_at normalization) ──

    #[test]
    fn naive_isoformat_omits_fraction_when_microsecond_zero() {
        let dt = chrono::NaiveDate::from_ymd_opt(2030, 1, 2)
            .unwrap()
            .and_hms_opt(3, 4, 5)
            .unwrap();
        assert_eq!(naive_isoformat(dt), "2030-01-02T03:04:05");
    }

    #[test]
    fn naive_isoformat_includes_microseconds_when_present() {
        let dt = chrono::NaiveDate::from_ymd_opt(2030, 1, 2)
            .unwrap()
            .and_hms_micro_opt(3, 4, 5, 123456)
            .unwrap();
        assert_eq!(naive_isoformat(dt), "2030-01-02T03:04:05.123456");
    }

    // ── email_tag_owner_clause (owner scoping) ──

    #[test]
    fn email_tag_owner_clause_multiuser_excludes_legacy_null_rows() {
        // Configured multi-user mode (owner non-empty): legacy owner=''/NULL rows
        // are NOT visible to everyone, so the clause must be a bare `owner IN (...)`
        // with no `owner IS NULL` escape hatch.
        let (clause, params) = email_tag_owner_clause(None, "alice@example.com");
        assert!(clause.starts_with("owner IN ("), "got: {clause}");
        assert!(!clause.contains("owner IS NULL"), "got: {clause}");
        // One placeholder per alias.
        assert_eq!(clause.matches('?').count(), params.len());
        assert!(params.iter().any(|p| p == "alice@example.com"));
    }

    #[test]
    fn email_tag_owner_clause_single_user_keeps_legacy_null_rows() {
        // Single-user / unconfigured mode (owner empty): legacy NULL-owner rows stay
        // visible, so the clause keeps the `OR owner IS NULL` arm.
        let (clause, params) = email_tag_owner_clause(None, "");
        assert!(clause.starts_with("(owner IN ("), "got: {clause}");
        assert!(clause.contains("OR owner IS NULL"), "got: {clause}");
        assert_eq!(clause.matches('?').count(), params.len());
    }

    // ── scheduled_emails owner scoping (real temp DB) ──

    /// Create a temp scheduled DB with the `scheduled_emails` schema (the subset the
    /// schedule/list/cancel routes touch) so owner-scoping can be exercised without
    /// going near the live `data/` directory.
    fn temp_scheduled_db() -> (rusqlite::Connection, std::path::PathBuf) {
        let path = std::env::temp_dir()
            .join(format!("ody_email_routes_sched_{}.db", uuid::Uuid::new_v4().simple()));
        let conn = rusqlite::Connection::open(&path).expect("open temp db");
        conn.execute(
            "CREATE TABLE scheduled_emails (
                id TEXT PRIMARY KEY, to_addr TEXT NOT NULL, cc TEXT, bcc TEXT,
                subject TEXT, body TEXT NOT NULL, in_reply_to TEXT, references_hdr TEXT,
                attachments TEXT, send_at TEXT NOT NULL, created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', error TEXT,
                account_id TEXT, odysseus_kind TEXT, owner TEXT DEFAULT ''
            )",
            [],
        )
        .expect("create table");
        (conn, path)
    }

    fn insert_scheduled(conn: &rusqlite::Connection, id: &str, owner: &str, send_at: &str) {
        // Exactly the INSERT column list / VALUES shape the schedule_email handler uses.
        conn.execute(
            "INSERT INTO scheduled_emails \
               (id, to_addr, cc, bcc, subject, body, in_reply_to, references_hdr, attachments, send_at, created_at, status, account_id, odysseus_kind, owner) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, 'pending', ?12, ?13, ?14)",
            rusqlite::params![
                id, "to@x.com", Option::<String>::None, Option::<String>::None, "Subj",
                "Body", Option::<String>::None, Option::<String>::None, "[]", send_at,
                "2030-01-01T00:00:00", Option::<String>::None, "scheduled", owner,
            ],
        )
        .expect("insert");
    }

    #[test]
    fn scheduled_list_and_cancel_are_owner_scoped() {
        let (conn, path) = temp_scheduled_db();
        insert_scheduled(&conn, "a1", "alice", "2030-01-01T10:00:00");
        insert_scheduled(&conn, "b1", "bob", "2030-01-01T09:00:00");

        // list query (the exact WHERE the list_scheduled handler uses) for alice
        // returns only alice's row.
        let mut stmt = conn
            .prepare(
                "SELECT id FROM scheduled_emails \
                 WHERE status IN ('pending', 'failed') AND owner = ? ORDER BY send_at ASC",
            )
            .unwrap();
        let ids: Vec<String> = stmt
            .query_map(rusqlite::params!["alice"], |r| r.get::<_, String>(0))
            .unwrap()
            .filter_map(Result::ok)
            .collect();
        drop(stmt);
        assert_eq!(ids, vec!["a1".to_string()]);

        // cancel query: alice cannot cancel bob's row.
        let n = conn
            .execute(
                "DELETE FROM scheduled_emails WHERE id = ? AND status = 'pending' AND owner = ?",
                rusqlite::params!["b1", "alice"],
            )
            .unwrap();
        assert_eq!(n, 0, "alice must not be able to cancel bob's scheduled email");
        // bob's own cancel succeeds.
        let n = conn
            .execute(
                "DELETE FROM scheduled_emails WHERE id = ? AND status = 'pending' AND owner = ?",
                rusqlite::params!["b1", "bob"],
            )
            .unwrap();
        assert_eq!(n, 1);

        drop(conn);
        let _ = std::fs::remove_file(&path);
    }

    // ── email_tags owner scoping (real temp DB) ──

    #[test]
    fn email_tags_unflag_spam_owner_scoped_sql() {
        // Verify the owner-scoped UPDATE only clears rows owned by the caller, using a
        // standalone temp DB so we never touch the live email_tags table.
        let path = std::env::temp_dir()
            .join(format!("ody_email_routes_tags_{}.db", uuid::Uuid::new_v4().simple()));
        let conn = rusqlite::Connection::open(&path).expect("open temp db");
        conn.execute(
            "CREATE TABLE email_tags (
                message_id TEXT, owner TEXT DEFAULT '', uid TEXT, folder TEXT,
                subject TEXT, sender TEXT, tags TEXT, spam_verdict INTEGER DEFAULT 0,
                spam_reason TEXT, moved_to TEXT, model_used TEXT, created_at TEXT NOT NULL,
                PRIMARY KEY (message_id, owner)
            )",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO email_tags (message_id, owner, uid, folder, spam_verdict, spam_reason, created_at) \
             VALUES ('m1', 'alice', '7', 'INBOX', 1, 'looks spammy', 'now')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO email_tags (message_id, owner, uid, folder, spam_verdict, spam_reason, created_at) \
             VALUES ('m2', 'bob', '7', 'INBOX', 1, 'looks spammy', 'now')",
            [],
        )
        .unwrap();

        // Multi-user clause for alice — only her uid=7 row clears.
        let (clause, aliases) = email_tag_owner_clause(None, "alice");
        let sql = format!(
            "UPDATE email_tags SET spam_verdict=0, spam_reason='' WHERE uid=? AND {clause}"
        );
        let mut params: Vec<&dyn rusqlite::ToSql> = vec![&"7"];
        for a in &aliases {
            params.push(a);
        }
        conn.execute(&sql, params.as_slice()).unwrap();

        let alice_verdict: i64 = conn
            .query_row(
                "SELECT spam_verdict FROM email_tags WHERE message_id='m1'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        let bob_verdict: i64 = conn
            .query_row(
                "SELECT spam_verdict FROM email_tags WHERE message_id='m2'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(alice_verdict, 0, "alice's row should be cleared");
        assert_eq!(bob_verdict, 1, "bob's row must be untouched");

        drop(conn);
        let _ = std::fs::remove_file(&path);
    }
}
