// mcp_servers/email_server.rs  <- mcp_servers/email_server.py
//! MCP server exposing email tools: list unread/unresponded emails, read email
//! content, send/reply, archive/delete/mark, bulk operations, and search.
//! Connects to the configured IMAP/SMTP accounts and reads from the AI summary
//! cache. The Rust mirror of the standalone Python `email_server.py`.
//!
//! ## What ports for real
//!
//! email_server owns its OWN account layer (a selector model: match by name,
//! email, or id; difflib fuzzy fallback) which is DISTINCT from
//! [`crate::routes::email_helpers`]'s owner-scoped `_get_email_config`. We port
//! that selector layer (`_list_accounts_raw` / `_resolve_account` / `_load_config`
//! plus the `_ACCOUNT_CACHE`) here, building an [`EmailConfig`] from the resolved
//! row so the rest of the transport can reuse the helpers crate.
//!
//! REUSE from [`crate::routes::email_helpers`]: [`EmailConfig`] (shape match),
//! `secret_storage::decrypt`, `parse_message` / `decode_header` / `extract_text` /
//! `list_attachments_from_msg` / `extract_attachment_to_disk`, `send_smtp_message`,
//! and `imap_connect_with_cfg` (the factored-out TCP+TLS+LOGIN dance). IMAP UID
//! operations (`uid_search` / `uid_fetch` / `uid_store` / `uid_mv` / `uid_copy` /
//! `append` / `examine` / `select` / `expunge` / `logout`) are driven directly on
//! the `imap` crate's `Session`, exactly as `email_routes` / `email_pollers` do.
//!
//! ## Drift / honest reproduction
//!
//! * Python's `difflib.get_close_matches(..., cutoff=0.72)` has no stdlib Rust
//!   analogue, so [`get_close_matches`] ports a small `SequenceMatcher.ratio`
//!   (the matching-blocks ratio difflib uses). This preserves the cutoff-0.72
//!   fuzzy account match for the common cases.
//! * The Python builds outbound mail with `email.message.EmailMessage` +
//!   `set_content(body)` (a single base64 `text/plain` part). We build the same
//!   minimal RFC822 bytes by hand and hand them to lettre's `send_raw` (the
//!   `smtp.send_message` analogue), then append the same bytes to the Sent folder.
//!
//! All blocking IMAP/SMTP work runs inside `tokio::task::spawn_blocking` because
//! the `imap`/`lettre` stack is synchronous (exactly like Python's stdlib
//! `imaplib`/`smtplib`), while the MCP harness drives an async handler.


use crate::mcp_servers::harness::{text_content, CallToolHandler, ToolDef};
use crate::routes::email_helpers::{
    decode_header, extract_attachment_to_disk, extract_text, imap_connect_with_cfg,
    list_attachments_from_msg, parse_message, send_smtp_message, smtp_security_mode, EmailConfig,
    ImapSession,
};
use indexmap::IndexMap;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Mutex;

// ===========================================================================
// Constants / paths
// ===========================================================================

/// `EMAIL_SOCKET_TIMEOUT = float(os.environ.get("EMAIL_SOCKET_TIMEOUT", "20"))`.
fn email_socket_timeout() -> f64 {
    crate::pyos::getenv("EMAIL_SOCKET_TIMEOUT", "20")
        .trim()
        .parse::<f64>()
        .unwrap_or(20.0)
}

/// `DATA_DIR = Path(__file__).resolve().parent.parent / "data"`. The Rust side
/// already exposes that exact path as [`crate::core::constants::DATA_DIR`].
fn data_dir() -> PathBuf {
    PathBuf::from(&*crate::core::constants::DATA_DIR)
}

/// `_db_path() -> DATA_DIR / "app.db"`.
fn db_path() -> PathBuf {
    data_dir().join("app.db")
}

// ===========================================================================
// Config — the multi-account selector layer (`_list_accounts_raw` /
// `_resolve_account` / `_load_config` + `_ACCOUNT_CACHE`)
// ===========================================================================

/// The resolved per-account config dict `_load_config` returns. The Python dict
/// carries more keys than [`EmailConfig`] (archive/trash folders, smtp_ssl, the
/// cache_db path), so we keep this richer struct and project it into an
/// [`EmailConfig`] for the transport helpers.
#[derive(Debug, Clone)]
struct LoadedConfig {
    imap_host: String,
    imap_port: i64,
    imap_user: String,
    imap_password: String,
    imap_ssl: bool,
    imap_starttls: bool,
    smtp_host: String,
    smtp_port: i64,
    /// `cfg["smtp_security"]` — the stored transport-security selector
    /// (`ssl | starttls | none`, or empty/unknown). The standalone Python
    /// `_smtp_connect` resolves the effective mode from this string + `smtp_port`;
    /// the Rust send path forwards it into [`EmailConfig::smtp_security`] (resolved
    /// through [`crate::routes::email_helpers::smtp_security_mode`]) so
    /// [`send_smtp_message`] dispatches SSL vs STARTTLS vs plaintext faithfully.
    smtp_security: String,
    smtp_user: String,
    smtp_password: String,
    // The Python `_load_config` dict ALSO carries `smtp_starttls`/`smtp_ssl` for the
    // standalone `_smtp_connect`'s legacy TLS-mode branch. The Rust send path now
    // resolves the mode through `smtp_security` (above) + `smtp_port` exactly like
    // the Python's `_smtp_connect`, so these two flags are still resolved for
    // fidelity with the dict shape but are not read by the transport branch.
    #[allow(dead_code)]
    smtp_starttls: bool,
    #[allow(dead_code)]
    smtp_ssl: bool,
    from_address: String,
    archive_folder: String,
    trash_folder: String,
    cache_db: String,
    account_id: Option<String>,
    account_name: Option<String>,
}

impl LoadedConfig {
    /// Project the loaded config into the [`EmailConfig`] the transport helpers
    /// (`imap_connect_with_cfg` / `send_smtp_message`) consume. The Python infers
    /// `imap_ssl = (port == 993 and not starttls)`; [`imap_connect_with_cfg`]
    /// makes the same choice from `imap_port`/`imap_starttls`, so the projection is
    /// faithful as long as we forward the resolved `imap_starttls`.
    fn to_email_config(&self) -> EmailConfig {
        EmailConfig {
            account_id: self.account_id.clone(),
            account_name: self.account_name.clone().unwrap_or_default(),
            smtp_host: self.smtp_host.clone(),
            smtp_port: self.smtp_port,
            // `_smtp_connect` resolves the effective mode from the stored
            // `smtp_security` string + `smtp_port`; reproduce that resolution so
            // `send_smtp_message` (which re-resolves idempotently) dispatches the
            // SSL/STARTTLS/none branch faithfully.
            smtp_security: smtp_security_mode(&self.smtp_security, self.smtp_port),
            smtp_user: self.smtp_user.clone(),
            smtp_password: self.smtp_password.clone(),
            imap_host: self.imap_host.clone(),
            imap_port: self.imap_port,
            imap_user: self.imap_user.clone(),
            imap_password: self.imap_password.clone(),
            imap_starttls: self.imap_starttls,
            from_address: self.from_address.clone(),
        }
    }
}

/// A raw `email_accounts` row — the dict `_list_accounts_raw` yields.
#[derive(Debug, Clone)]
struct AccountRow {
    id: String,
    name: Option<String>,
    is_default: bool,
    imap_host: Option<String>,
    imap_port: Option<i64>,
    imap_user: Option<String>,
    imap_password: Option<String>,
    imap_starttls: bool,
    smtp_host: Option<String>,
    smtp_port: Option<i64>,
    /// `email_accounts.smtp_security` (`ssl | starttls | none`). Selected only when
    /// the column exists in the table (older DBs may lack it, mirroring the Python's
    /// `PRAGMA table_info` guard); otherwise resolves to an empty string.
    smtp_security: Option<String>,
    smtp_user: Option<String>,
    smtp_password: Option<String>,
    from_address: Option<String>,
}

/// `_ACCOUNT_CACHE: dict` — key = normalized selector -> config. Module-global to
/// mirror the Python module-level cache (the standalone server is a single
/// process, so a process-wide cache is the faithful shape).
static ACCOUNT_CACHE: Mutex<Option<HashMap<String, LoadedConfig>>> = Mutex::new(None);

/// `_list_accounts_raw()` — list of dicts from the `email_accounts` table. Empty
/// list if the DB / table is missing or empty. NEVER raises.
fn list_accounts_raw() -> Vec<AccountRow> {
    let path = db_path();
    if !path.exists() {
        return Vec::new();
    }
    let inner = || -> rusqlite::Result<Vec<AccountRow>> {
        let conn = rusqlite::Connection::open(&path)?;
        // `smtp_security_select = "smtp_security" if "smtp_security" in columns else
        //  "'' AS smtp_security"` — older DBs may lack the column, so probe
        // `PRAGMA table_info` before referencing it (matching the Python guard).
        let has_smtp_security = {
            let mut pragma = conn.prepare("PRAGMA table_info(email_accounts)")?;
            let cols = pragma.query_map([], |r| r.get::<_, String>(1))?;
            let mut found = false;
            for c in cols {
                if c? == "smtp_security" {
                    found = true;
                    break;
                }
            }
            found
        };
        let smtp_security_select = if has_smtp_security {
            "smtp_security"
        } else {
            "'' AS smtp_security"
        };
        let sql = format!(
            "SELECT id, name, is_default, enabled, \
                    imap_host, imap_port, imap_user, imap_password, imap_starttls, \
                    smtp_host, smtp_port, {smtp_security_select}, smtp_user, smtp_password, from_address \
             FROM email_accounts WHERE enabled = 1 \
             ORDER BY is_default DESC, created_at ASC"
        );
        let mut stmt = conn.prepare(&sql)?;
        let rows = stmt.query_map([], |r| {
            Ok(AccountRow {
                id: r.get::<_, String>(0)?,
                name: r.get::<_, Option<String>>(1)?,
                is_default: r.get::<_, Option<i64>>(2)?.unwrap_or(0) != 0,
                // column 3 = enabled (filtered in WHERE, not stored on the row)
                imap_host: r.get::<_, Option<String>>(4)?,
                imap_port: r.get::<_, Option<i64>>(5)?,
                imap_user: r.get::<_, Option<String>>(6)?,
                imap_password: r.get::<_, Option<String>>(7)?,
                imap_starttls: r.get::<_, Option<i64>>(8)?.unwrap_or(0) != 0,
                smtp_host: r.get::<_, Option<String>>(9)?,
                smtp_port: r.get::<_, Option<i64>>(10)?,
                smtp_security: r.get::<_, Option<String>>(11)?,
                smtp_user: r.get::<_, Option<String>>(12)?,
                smtp_password: r.get::<_, Option<String>>(13)?,
                from_address: r.get::<_, Option<String>>(14)?,
            })
        })?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    };
    // `except sqlite3.OperationalError: return []` / `except Exception: return []`.
    inner().unwrap_or_default()
}

/// `_resolve_account(selector)` — return the matching row or `None`. None ->
/// default / first; exact id match; case-insensitive substring on
/// name/imap_user/from_address; difflib fuzzy fallback (cutoff 0.72).
fn resolve_account(selector: Option<&str>) -> Option<AccountRow> {
    let rows = list_accounts_raw();
    if rows.is_empty() {
        return None;
    }
    let sel_raw = selector.unwrap_or("");
    if sel_raw.is_empty() {
        // `if not selector:` -> default, else first.
        for r in &rows {
            if r.is_default {
                return Some(r.clone());
            }
        }
        return rows.into_iter().next();
    }
    let sel = sel_raw.trim().to_lowercase();
    // Exact id match first (`r["id"] == selector` — the RAW selector, not lowered).
    for r in &rows {
        if r.id == sel_raw {
            return Some(r.clone());
        }
    }
    // Case-insensitive substring on name + imap_user + from_address.
    for r in &rows {
        let fields = [
            r.name.clone().unwrap_or_default(),
            r.imap_user.clone().unwrap_or_default(),
            r.from_address.clone().unwrap_or_default(),
        ];
        if fields.iter().any(|f| f.to_lowercase().contains(&sel)) {
            return Some(r.clone());
        }
    }
    // difflib.get_close_matches(sel, candidates, n=1, cutoff=0.72) fuzzy fallback.
    let mut candidates: Vec<String> = Vec::new();
    let mut by_candidate: HashMap<String, AccountRow> = HashMap::new();
    for r in &rows {
        for f in [&r.name, &r.imap_user, &r.from_address].into_iter().flatten() {
            if !f.is_empty() {
                let val = f.to_lowercase();
                candidates.push(val.clone());
                by_candidate.entry(val).or_insert_with(|| r.clone());
            }
        }
    }
    if let Some(close) = get_close_matches(&sel, &candidates, 1, 0.72).into_iter().next() {
        return by_candidate.get(&close).cloned();
    }
    None
}

/// `_load_config(account)` — full config dict for the requested account (or
/// default). Caches by normalized selector. Raises a "not found" ValueError when
/// a selector was given, rows exist, and none matched (surfaced here as `Err`).
fn load_config(account: Option<&str>) -> Result<LoadedConfig, String> {
    let cache_key = {
        let k = account.unwrap_or("").trim().to_lowercase();
        if k.is_empty() {
            "__default__".to_string()
        } else {
            k
        }
    };
    if let Some(cached) = ACCOUNT_CACHE
        .lock()
        .ok()
        .and_then(|g| g.as_ref().and_then(|m| m.get(&cache_key).cloned()))
    {
        return Ok(cached);
    }

    // The env / settings.json default scaffold (resolution layer 2/3).
    let env = |k: &str, d: &str| crate::pyos::getenv(k, d);
    let env_bool = |k: &str, d: &str| env(k, d).to_lowercase() == "true";
    let env_int = |k: &str, d: i64| env(k, &d.to_string()).trim().parse::<i64>().unwrap_or(d);

    let mut cfg = LoadedConfig {
        imap_host: env("IMAP_HOST", "localhost"),
        imap_port: env_int("IMAP_PORT", 31143),
        imap_user: env("IMAP_USER", ""),
        imap_password: env("IMAP_PASSWORD", ""),
        imap_ssl: env_bool("IMAP_SSL", "false"),
        imap_starttls: env_bool("IMAP_STARTTLS", "true"),
        smtp_host: env("SMTP_HOST", ""),
        smtp_port: env_int("SMTP_PORT", 465),
        smtp_security: env("SMTP_SECURITY", ""),
        smtp_user: env("SMTP_USER", ""),
        smtp_password: env("SMTP_PASSWORD", ""),
        smtp_starttls: env_bool("SMTP_STARTTLS", "false"),
        smtp_ssl: env_bool("SMTP_SSL", "true"),
        from_address: env("EMAIL_FROM", ""),
        archive_folder: env("ARCHIVE_FOLDER", "Archive"),
        trash_folder: env("TRASH_FOLDER", "Trash"),
        cache_db: crate::pyos::getenv_opt("EMAIL_CACHE_DB")
            .unwrap_or_else(|| data_dir().join("email_cache.db").to_string_lossy().to_string()),
        account_id: None,
        account_name: None,
    };

    let rows = list_accounts_raw();
    let row = resolve_account(account);
    // `if account and rows and not row: raise ValueError(...)`.
    if let Some(sel) = account {
        if !sel.is_empty() && !rows.is_empty() && row.is_none() {
            let available = rows
                .iter()
                .map(|r| {
                    let name = r
                        .name
                        .clone()
                        .filter(|s| !s.is_empty())
                        .or_else(|| r.imap_user.clone())
                        .unwrap_or_default();
                    let addr = r
                        .imap_user
                        .clone()
                        .filter(|s| !s.is_empty())
                        .or_else(|| r.from_address.clone())
                        .filter(|s| !s.is_empty())
                        .unwrap_or_else(|| "?".to_string());
                    format!("{name} <{addr}>")
                })
                .collect::<Vec<_>>()
                .join(", ");
            return Err(format!(
                "Email account not found for selector {sel:?}. Available accounts: {available}"
            ));
        }
    }

    if let Some(row) = row {
        cfg.account_id = Some(row.id.clone());
        cfg.account_name = row.name.clone();
        cfg.imap_host = non_empty(row.imap_host.as_deref()).unwrap_or(cfg.imap_host);
        cfg.imap_port = row.imap_port.filter(|p| *p != 0).unwrap_or(cfg.imap_port);
        cfg.imap_user = non_empty(row.imap_user.as_deref()).unwrap_or(cfg.imap_user);
        // Passwords are stored encrypted via src.secret_storage.encrypt — decrypt.
        cfg.imap_password = match row.imap_password.as_deref() {
            Some(p) if !p.is_empty() => crate::src::secret_storage::decrypt(p),
            _ => cfg.imap_password,
        };
        cfg.imap_starttls = row.imap_starttls;
        // Port 993 is implicit TLS for IMAP providers like Gmail; the table only
        // stores STARTTLS, so infer SSL from the port.
        cfg.imap_ssl = cfg.imap_port == 993 && !cfg.imap_starttls;
        cfg.smtp_host = non_empty(row.smtp_host.as_deref()).unwrap_or(cfg.smtp_host);
        cfg.smtp_port = row.smtp_port.filter(|p| *p != 0).unwrap_or(cfg.smtp_port);
        // `cfg["smtp_security"] = row["smtp_security"] or cfg["smtp_security"]
        //  or ("starttls" if int(cfg["smtp_port"]) == 587 else "ssl")` — uses the
        // ALREADY-resolved `cfg.smtp_port` above, falling back to a port heuristic
        // when neither the row nor the env scaffold supplies a value.
        cfg.smtp_security = non_empty(row.smtp_security.as_deref())
            .or_else(|| non_empty(Some(cfg.smtp_security.as_str())))
            .unwrap_or_else(|| {
                if cfg.smtp_port == 587 {
                    "starttls".to_string()
                } else {
                    "ssl".to_string()
                }
            });
        cfg.smtp_user = non_empty(row.smtp_user.as_deref()).unwrap_or(cfg.smtp_user);
        cfg.smtp_password = match row.smtp_password.as_deref() {
            Some(p) if !p.is_empty() => crate::src::secret_storage::decrypt(p),
            _ => cfg.smtp_password,
        };
        cfg.from_address = non_empty(row.from_address.as_deref())
            .or_else(|| non_empty(row.imap_user.as_deref()))
            .unwrap_or(cfg.from_address);
    } else {
        // Legacy fallback: settings.json flat keys.
        let settings_path = data_dir().join("settings.json");
        if settings_path.exists() {
            if let Ok(text) = std::fs::read_to_string(&settings_path) {
                if let Ok(Value::Object(settings)) = serde_json::from_str::<Value>(&text) {
                    apply_settings_overrides(&mut cfg, &settings);
                }
            }
        }
    }

    // `if not cfg["from_address"]: cfg["from_address"] = cfg["imap_user"]`.
    if cfg.from_address.is_empty() {
        cfg.from_address = cfg.imap_user.clone();
    }

    // Cache it.
    if let Ok(mut guard) = ACCOUNT_CACHE.lock() {
        guard
            .get_or_insert_with(HashMap::new)
            .insert(cache_key, cfg.clone());
    }
    Ok(cfg)
}

/// `value or None` for an `Option<&str>` — Python's `row["x"] or default` only
/// replaces empty/None, so an empty string is treated as "not set".
fn non_empty(v: Option<&str>) -> Option<String> {
    v.filter(|s| !s.is_empty()).map(str::to_string)
}

/// The legacy settings.json override loop:
/// `for key in (...): if settings.get(key) not in (None, ""): cfg[key] = ...`.
fn apply_settings_overrides(cfg: &mut LoadedConfig, settings: &serde_json::Map<String, Value>) {
    let str_val = |v: &Value| -> Option<String> {
        match v {
            Value::String(s) if !s.is_empty() => Some(s.clone()),
            Value::Number(n) => Some(n.to_string()),
            _ => None,
        }
    };
    let int_val = |v: &Value| -> Option<i64> {
        match v {
            Value::Number(n) => n.as_i64(),
            Value::String(s) => s.trim().parse::<i64>().ok(),
            _ => None,
        }
    };
    if let Some(v) = settings.get("imap_host").and_then(&str_val) {
        cfg.imap_host = v;
    }
    if let Some(v) = settings.get("imap_port").and_then(&int_val) {
        cfg.imap_port = v;
    }
    if let Some(v) = settings.get("imap_user").and_then(&str_val) {
        cfg.imap_user = v;
    }
    if let Some(v) = settings.get("imap_password").and_then(&str_val) {
        cfg.imap_password = v;
    }
    if let Some(v) = settings.get("smtp_host").and_then(&str_val) {
        cfg.smtp_host = v;
    }
    if let Some(v) = settings.get("smtp_port").and_then(&int_val) {
        cfg.smtp_port = v;
    }
    if let Some(v) = settings.get("smtp_user").and_then(&str_val) {
        cfg.smtp_user = v;
    }
    if let Some(v) = settings.get("smtp_password").and_then(&str_val) {
        cfg.smtp_password = v;
    }
    if let Some(v) = settings.get("from_address").and_then(&str_val) {
        cfg.from_address = v;
    }
    if let Some(v) = settings.get("archive_folder").and_then(&str_val) {
        cfg.archive_folder = v;
    }
    if let Some(v) = settings.get("trash_folder").and_then(&str_val) {
        cfg.trash_folder = v;
    }
}

// ===========================================================================
// difflib.get_close_matches — the cutoff-0.72 fuzzy account match
// ===========================================================================

/// `difflib.get_close_matches(word, possibilities, n, cutoff)` — return up to `n`
/// of the candidates whose `SequenceMatcher.ratio(word, candidate) >= cutoff`,
/// best-first. Ports the stdlib's ratio gating for parity with `_resolve_account`'s
/// fuzzy fallback (no Rust stdlib analogue exists).
fn get_close_matches(word: &str, possibilities: &[String], n: usize, cutoff: f64) -> Vec<String> {
    let mut scored: Vec<(f64, &String)> = Vec::new();
    for x in possibilities {
        let r = sequence_matcher_ratio(word, x);
        if r >= cutoff {
            scored.push((r, x));
        }
    }
    // `heapq.nlargest(n, ...)` — highest ratio first; a stable sort by descending
    // ratio reproduces difflib's score-ordered result.
    scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
    scored.into_iter().take(n).map(|(_, s)| s.clone()).collect()
}

/// `difflib.SequenceMatcher(None, a, b).ratio()` — `2.0 * M / T` where `T` is the
/// total number of elements in both sequences and `M` is the number of matches
/// (the sum of the matching-block sizes), found by the recursive
/// longest-matching-block algorithm difflib uses.
fn sequence_matcher_ratio(a: &str, b: &str) -> f64 {
    let a: Vec<char> = a.chars().collect();
    let b: Vec<char> = b.chars().collect();
    let total = a.len() + b.len();
    if total == 0 {
        return 1.0;
    }
    let matches = matching_blocks_total(&a, &b, 0, a.len(), 0, b.len());
    2.0 * matches as f64 / total as f64
}

/// Sum of matching-block sizes between `a[alo..ahi]` and `b[blo..bhi]`, found by
/// recursively locating the longest matching block then recursing left/right —
/// the algorithm behind `SequenceMatcher.get_matching_blocks`.
fn matching_blocks_total(
    a: &[char],
    b: &[char],
    alo: usize,
    ahi: usize,
    blo: usize,
    bhi: usize,
) -> usize {
    let (i, j, k) = longest_match(a, b, alo, ahi, blo, bhi);
    if k == 0 {
        return 0;
    }
    let mut total = k;
    if alo < i && blo < j {
        total += matching_blocks_total(a, b, alo, i, blo, j);
    }
    if i + k < ahi && j + k < bhi {
        total += matching_blocks_total(a, b, i + k, ahi, j + k, bhi);
    }
    total
}

/// `find_longest_match(alo, ahi, blo, bhi)` — the longest matching block,
/// returning `(i, j, k)` (a[i..i+k] == b[j..j+k]). Mirrors difflib's b2j-index
/// dynamic-programming scan (without the junk/popular-element heuristics, which
/// do not apply to the short account-name strings this matches).
fn longest_match(
    a: &[char],
    b: &[char],
    alo: usize,
    ahi: usize,
    blo: usize,
    bhi: usize,
) -> (usize, usize, usize) {
    // b2j: char -> list of indices in b[blo..bhi].
    let mut b2j: HashMap<char, Vec<usize>> = HashMap::new();
    for (idx, &ch) in b.iter().enumerate().take(bhi).skip(blo) {
        b2j.entry(ch).or_default().push(idx);
    }
    let (mut besti, mut bestj, mut bestsize) = (alo, blo, 0usize);
    // j2len[j] = length of the longest match ending at a[i-1], b[j-1].
    let mut j2len: HashMap<usize, usize> = HashMap::new();
    // mirrors difflib's `for i in range(alo, ahi)` — `i` is also used arithmetically
    // (besti = i + 1 - k), so the explicit index loop is the faithful shape.
    #[allow(clippy::needless_range_loop)]
    for i in alo..ahi {
        let mut newj2len: HashMap<usize, usize> = HashMap::new();
        if let Some(js) = b2j.get(&a[i]) {
            for &j in js {
                if j < blo {
                    continue;
                }
                if j >= bhi {
                    break;
                }
                let k = if j == 0 {
                    1
                } else {
                    j2len.get(&(j - 1)).copied().unwrap_or(0) + 1
                };
                newj2len.insert(j, k);
                if k > bestsize {
                    besti = i + 1 - k;
                    bestj = j + 1 - k;
                    bestsize = k;
                }
            }
        }
        j2len = newj2len;
    }
    (besti, bestj, bestsize)
}

// ===========================================================================
// IMAP helpers (folder detection / resolution / cache summaries)
// ===========================================================================

/// `_imap_connect(account)` — resolve the selector to a config then open + LOGIN.
/// Delegates the TCP+TLS+LOGIN to the shared [`imap_connect_with_cfg`]. MUST run
/// inside `spawn_blocking`.
fn imap_connect(account: Option<&str>) -> Result<ImapSession, String> {
    let cfg = load_config(account)?;
    imap_connect_with_cfg(&cfg.to_email_config())
}

/// The `imap::Name` rows from a `LIST`, with their decoded attributes — the shared
/// scrape for the folder-detection / resolution functions (the crate already
/// decodes folder names, so we skip the Python's raw-line regex).
fn list_folder_lines(conn: &mut ImapSession) -> Vec<(String, Vec<String>)> {
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

/// `_detect_sent_folder(conn)` — server's Sent folder name; `"Sent"` default.
fn detect_sent_folder(conn: &mut ImapSession) -> String {
    let candidates = ["Sent", "[Gmail]/Sent Mail", "Sent Mail", "Sent Items", "INBOX.Sent"];
    let folders = list_folder_lines(conn);
    if folders.is_empty() {
        return "Sent".to_string();
    }
    let names: Vec<&String> = folders.iter().map(|(n, _)| n).collect();
    // Prefer the `\Sent` attribute.
    for (name, attrs) in &folders {
        if attrs
            .iter()
            .any(|a| a.trim_start_matches('\\').eq_ignore_ascii_case("Sent"))
        {
            return name.clone();
        }
    }
    for c in candidates {
        if names.iter().any(|n| n.as_str() == c) {
            return c.to_string();
        }
    }
    "Sent".to_string()
}

/// `_folder_role_from_name(name)`.
fn folder_role_from_name(name: &str) -> &'static str {
    let lower = name.to_lowercase();
    if lower.contains("trash") || lower.contains("bin") || lower.contains("deleted") {
        "trash"
    } else if lower.contains("junk") || lower.contains("spam") {
        "junk"
    } else if lower.contains("archive") || lower.contains("all mail") {
        "archive"
    } else {
        ""
    }
}

/// `_resolve_folder(conn, preferred, role)` — resolve provider-specific folder
/// names (Gmail's `[Gmail]/Trash` etc.) by attribute then candidate name. Falls
/// back to `preferred`.
fn resolve_folder(conn: &mut ImapSession, preferred: &str, role: &str) -> String {
    let folders = list_folder_lines(conn);
    let names: Vec<String> = folders.iter().map(|(n, _)| n.clone()).collect();
    if !preferred.is_empty() && names.iter().any(|n| n == preferred) {
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
        "trash" => &["Trash", "[Gmail]/Trash", "[Google Mail]/Trash", "Bin", "Deleted Messages", "Deleted Items"],
        "archive" => &["Archive", "Archives", "[Gmail]/All Mail", "[Google Mail]/All Mail"],
        "junk" => &["Junk", "Spam", "[Gmail]/Spam", "[Google Mail]/Spam"],
        _ => &[],
    };
    let lower_map: HashMap<String, String> =
        names.iter().map(|n| (n.to_lowercase(), n.clone())).collect();
    for candidate in candidates {
        if let Some(found) = lower_map.get(&candidate.to_lowercase()) {
            return found.clone();
        }
    }
    preferred.to_string()
}

/// A cached AI summary row — `{subject: {sender, summary, reply}}`. Only the
/// `summary` is read by the list/search tools.
struct CachedSummary {
    summary: String,
}

/// `_get_cached_summaries()` — pre-computed summaries from the SQLite cache. The
/// cache DB is the DEFAULT account's `cache_db`. Empty map if the DB / table is
/// missing or unreadable.
fn get_cached_summaries() -> HashMap<String, CachedSummary> {
    let db_path = match load_config(None) {
        Ok(cfg) => cfg.cache_db,
        Err(_) => return HashMap::new(),
    };
    if !std::path::Path::new(&db_path).exists() {
        return HashMap::new();
    }
    let inner = || -> rusqlite::Result<HashMap<String, CachedSummary>> {
        let conn = rusqlite::Connection::open(&db_path)?;
        let mut stmt =
            conn.prepare("SELECT subject, sender, summary, suggested_reply FROM email_ai")?;
        let rows = stmt.query_map([], |r| {
            Ok((
                r.get::<_, Option<String>>(0)?.unwrap_or_default(),
                r.get::<_, Option<String>>(2)?.unwrap_or_default(),
            ))
        })?;
        let mut out = HashMap::new();
        for row in rows {
            let (subject, summary) = row?;
            out.insert(subject, CachedSummary { summary });
        }
        Ok(out)
    };
    inner().unwrap_or_default()
}

// ===========================================================================
// Tool implementations — the synchronous IMAP/SMTP work (`_list_emails` etc.)
// ===========================================================================

/// A listed email — the dict `_list_emails` / `_search_emails` build. Extra
/// account / folder fields are carried as options.
#[derive(Debug, Clone, Default)]
struct EmailEntry {
    uid: String,
    subject: String,
    from: String,
    from_address: String,
    to: Option<String>,
    date: String,
    date_epoch: f64,
    summary: String,
    folder: Option<String>,
    account: Option<String>,
    account_email: Option<String>,
}

/// `email.utils.parseaddr(s)` -> `(name, addr)` (the email_routes shape).
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

/// IMAP SEARCH-quote: `value.replace("\\","\\\\").replace('"','\\"')`.
fn imap_search_escape(value: &str) -> String {
    value.replace('\\', "\\\\").replace('"', "\\\"")
}

/// `data[0].split()` -> reversed UID list, capped at `max_results`. The IMAP
/// SEARCH returns ascending UIDs; reverse for newest-first.
fn reversed_capped(uids: Vec<u32>, max_results: usize) -> Vec<u32> {
    let mut v = uids;
    v.sort_unstable();
    v.reverse();
    v.into_iter().take(max_results).collect()
}

/// The IMAP SEARCH criteria `_list_emails` selects, in the Python's exact
/// condition ordering:
///   * `unread_only && unresponded_only` -> `(UNSEEN UNANSWERED)`
///   * `unread_only`                     -> `(UNSEEN)`
///   * `unresponded_only`                -> `(UNANSWERED)` (emails without replies)
///   * else                              -> `ALL` (entire folder, read included)
fn list_search_criteria(unread_only: bool, unresponded_only: bool) -> &'static str {
    if unread_only && unresponded_only {
        "(UNSEEN UNANSWERED)"
    } else if unread_only {
        "(UNSEEN)"
    } else if unresponded_only {
        // Was missing — unresponded_only=True (without unread_only) fell through
        // to "ALL" and returned answered mail too, despite the documented
        // "emails without replies" behaviour.
        "(UNANSWERED)"
    } else {
        // Include read too — IMAP search "ALL" returns the entire folder.
        "ALL"
    }
}

/// `_list_emails(folder, max_results, unresponded_only, unread_only, account)`.
fn list_emails(
    folder: &str,
    max_results: usize,
    unresponded_only: bool,
    unread_only: bool,
    account: Option<&str>,
) -> Result<Vec<EmailEntry>, String> {
    let mut conn = imap_connect(account)?;
    // `conn.select(folder, readonly=True)` -> EXAMINE.
    if conn.examine(folder).is_err() {
        let _ = conn.logout();
        return Err(format!("IMAP folder not found: {folder}"));
    }
    let criteria = list_search_criteria(unread_only, unresponded_only);
    let uids = match conn.uid_search(criteria) {
        Ok(set) if !set.is_empty() => set.into_iter().collect::<Vec<u32>>(),
        _ => {
            let _ = conn.logout();
            return Ok(Vec::new());
        }
    };
    let uid_list = reversed_capped(uids, max_results);
    let cache = get_cached_summaries();
    let mut results = Vec::new();
    for uid in uid_list {
        let uid_s = uid.to_string();
        let raw = match conn.uid_fetch(&uid_s, "(RFC822.HEADER)") {
            Ok(fetches) => fetches.iter().find_map(|f| f.header().map(|b| b.to_vec())),
            Err(_) => continue,
        };
        let raw = match raw {
            Some(b) => b,
            None => continue,
        };
        let msg = match parse_message(&raw) {
            Some(m) => m,
            None => continue,
        };
        let subject = decode_header(&msg.header("subject").unwrap_or_else(|| "(no subject)".into()));
        let sender = decode_header(&msg.header("from").unwrap_or_else(|| "unknown".into()));
        let date_str = msg.header("date").unwrap_or_default();
        let (sender_name, sender_addr) = parseaddr(&sender);
        let sender_display = if sender_name.is_empty() {
            sender_addr.clone()
        } else {
            sender_name
        };
        let summary = cache.get(&subject).map(|c| c.summary.clone()).unwrap_or_default();
        results.push(EmailEntry {
            uid: uid_s,
            subject,
            from: sender_display,
            from_address: sender_addr,
            date: date_str,
            summary,
            ..Default::default()
        });
    }
    let _ = conn.logout();
    Ok(results)
}

/// `_result_sort_time(result)` — parse the `date` to an epoch for sorting; the
/// Python returns `datetime.min` on failure (sorts oldest). We store the epoch
/// (`f64::MIN` ~ unparseable, the floor) and sort descending.
fn result_sort_epoch(date: &str) -> f64 {
    if date.is_empty() {
        return f64::MIN;
    }
    if let Ok(dt) = chrono::DateTime::parse_from_rfc2822(date) {
        return dt.timestamp() as f64;
    }
    if let Some(idx) = date.find('(') {
        if let Ok(dt) = chrono::DateTime::parse_from_rfc2822(date[..idx].trim()) {
            return dt.timestamp() as f64;
        }
    }
    f64::MIN
}

/// `_list_emails_across_accounts(...)` — merge listings from every account,
/// tagging each row with its source account, then sort newest-first and cap.
/// Returns `(combined, errors)`.
fn list_emails_across_accounts(
    folder: &str,
    max_results: usize,
    unresponded_only: bool,
    unread_only: bool,
) -> (Vec<EmailEntry>, Vec<String>) {
    let rows = list_accounts_raw();
    let mut combined: Vec<EmailEntry> = Vec::new();
    let mut errors: Vec<String> = Vec::new();
    for row in &rows {
        let selector = account_selector(row);
        let account_name = row
            .name
            .clone()
            .filter(|s| !s.is_empty())
            .or_else(|| row.imap_user.clone())
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| row.id.clone());
        let account_email = row
            .imap_user
            .clone()
            .filter(|s| !s.is_empty())
            .or_else(|| row.from_address.clone())
            .unwrap_or_default();
        match list_emails(folder, max_results, unresponded_only, unread_only, Some(&selector)) {
            Ok(account_results) => {
                for mut item in account_results {
                    item.account = Some(account_name.clone());
                    item.account_email = Some(account_email.clone());
                    item.date_epoch = result_sort_epoch(&item.date);
                    combined.push(item);
                }
            }
            Err(exc) => errors.push(format!("{account_name} ({account_email}): {exc}")),
        }
    }
    combined.sort_by(|a, b| {
        b.date_epoch.partial_cmp(&a.date_epoch).unwrap_or(std::cmp::Ordering::Equal)
    });
    combined.truncate(max_results);
    (combined, errors)
}

/// `account_selector = row.id or row.name or row.imap_user`.
fn account_selector(row: &AccountRow) -> String {
    if !row.id.is_empty() {
        return row.id.clone();
    }
    row.name
        .clone()
        .filter(|s| !s.is_empty())
        .or_else(|| row.imap_user.clone())
        .unwrap_or_default()
}

/// `_search_emails(query, folders, max_results, account)`.
///
/// Python calls `_imap_connect(account)` (and `_get_cached_summaries`) OUTSIDE the
/// inner per-folder `try`, so a connection failure or an unknown-account
/// `ValueError` from `_load_config` PROPAGATES out of `_search_emails` to the
/// dispatcher's `try/except`, which renders `"Search failed: {e}"`. Only the
/// per-folder / per-uid work is swallowed (it `continue`s, returning partial
/// results). We surface the connect/load error as `Err` and keep the inner loop
/// errors swallowed.
fn search_emails(
    query: &str,
    folders: Option<Vec<String>>,
    max_results: usize,
    account: Option<&str>,
) -> Result<Vec<EmailEntry>, String> {
    if query.trim().is_empty() {
        return Ok(Vec::new());
    }
    let q = imap_search_escape(query);
    let search_cmd = format!("(OR OR FROM \"{q}\" SUBJECT \"{q}\" TEXT \"{q}\")");
    let folders = folders.unwrap_or_else(|| {
        vec!["INBOX".to_string(), "Sent".to_string(), "Archive".to_string()]
    });
    let cache = get_cached_summaries();
    let mut out: Vec<EmailEntry> = Vec::new();
    let mut conn = imap_connect(account)?;
    for folder in &folders {
        if conn.examine(folder).is_err() {
            continue;
        }
        let uids = match conn.uid_search(&search_cmd) {
            Ok(set) if !set.is_empty() => set.into_iter().collect::<Vec<u32>>(),
            _ => continue,
        };
        let uid_list = reversed_capped(uids, max_results);
        for uid in uid_list {
            let uid_s = uid.to_string();
            let raw = match conn.uid_fetch(&uid_s, "(RFC822.HEADER)") {
                Ok(fetches) => fetches.iter().find_map(|f| f.header().map(|b| b.to_vec())),
                Err(_) => continue,
            };
            let raw = match raw {
                Some(b) => b,
                None => continue,
            };
            let msg = match parse_message(&raw) {
                Some(m) => m,
                None => continue,
            };
            let subject = decode_header(&msg.header("subject").unwrap_or_else(|| "(no subject)".into()));
            let sender = decode_header(&msg.header("from").unwrap_or_else(|| "unknown".into()));
            let date_str = msg.header("date").unwrap_or_default();
            let to_str = decode_header(&msg.header("to").unwrap_or_default());
            let (sender_name, sender_addr) = parseaddr(&sender);
            let sender_display = if sender_name.is_empty() {
                sender_addr.clone()
            } else {
                sender_name
            };
            let summary = cache.get(&subject).map(|c| c.summary.clone()).unwrap_or_default();
            out.push(EmailEntry {
                uid: uid_s,
                subject,
                from: sender_display,
                from_address: sender_addr,
                to: Some(to_str),
                date: date_str,
                summary,
                folder: Some(folder.clone()),
                ..Default::default()
            });
        }
    }
    let _ = conn.logout();
    // `out[: max_results * len(folders)]`.
    out.truncate(max_results * folders.len());
    Ok(out)
}

/// A fully-read email — the dict `_read_email` builds.
struct ReadResult {
    uid: String,
    account: String,
    account_email: String,
    account_id: Option<String>,
    message_id: String,
    subject: String,
    from: String,
    from_address: String,
    date: String,
    body: String,
    attachments: Vec<crate::routes::email_helpers::AttachmentMeta>,
}

/// `_read_email(uid, message_id, folder, account)` — full content by UID or
/// Message-ID. `Err` carries the `{"error": ...}` text (the dict-error analogue).
fn read_email(
    uid: Option<&str>,
    message_id: Option<&str>,
    folder: &str,
    account: Option<&str>,
) -> Result<ReadResult, String> {
    let cfg = load_config(account)?;
    let mut conn = imap_connect(account)?;
    let _ = conn.examine(folder);

    let mut uid_owned: Option<String> = uid.map(str::to_string).filter(|s| !s.is_empty());
    if uid_owned.is_none() {
        if let Some(mid) = message_id.filter(|m| !m.is_empty()) {
            match conn.uid_search(format!("(HEADER Message-ID \"{mid}\")")) {
                Ok(set) if !set.is_empty() => {
                    let mut v: Vec<u32> = set.into_iter().collect();
                    v.sort_unstable();
                    uid_owned = v.last().map(|u| u.to_string());
                }
                _ => {
                    let _ = conn.logout();
                    return Err(format!("Email not found with Message-ID: {mid}"));
                }
            }
        }
    }
    let uid_s = match uid_owned {
        Some(u) => u,
        None => {
            let _ = conn.logout();
            return Err("No UID or Message-ID provided".to_string());
        }
    };

    let raw = match conn.uid_fetch(&uid_s, "(RFC822)") {
        Ok(fetches) => fetches.iter().find_map(|f| f.body().map(|b| b.to_vec())),
        Err(_) => {
            let _ = conn.logout();
            return Err(format!("Failed to fetch email UID {uid_s}"));
        }
    };
    let raw = match raw {
        Some(b) if !b.is_empty() => b,
        _ => {
            let _ = conn.logout();
            return Err(format!("Email not found with UID {uid_s}"));
        }
    };
    let msg = match parse_message(&raw) {
        Some(m) => m,
        None => {
            let _ = conn.logout();
            return Err(format!("Email not found with UID {uid_s}"));
        }
    };
    let subject = decode_header(&msg.header("subject").unwrap_or_else(|| "(no subject)".into()));
    let sender = decode_header(&msg.header("from").unwrap_or_else(|| "unknown".into()));
    let date_str = msg.header("date").unwrap_or_default();
    let message_id_header = msg.header("message-id").unwrap_or_default();
    let body = extract_text(&msg);
    let attachments = list_attachments_from_msg(&msg);
    let (sender_name, sender_addr) = parseaddr(&sender);
    let _ = conn.logout();

    // `body[:8000]` — char slice.
    let body_trunc: String = body.chars().take(8000).collect();
    let account_name = cfg
        .account_name
        .clone()
        .filter(|s| !s.is_empty())
        .or_else(|| Some(cfg.imap_user.clone()).filter(|s| !s.is_empty()))
        .unwrap_or_else(|| "default".to_string());
    let account_email = if !cfg.imap_user.is_empty() {
        cfg.imap_user.clone()
    } else {
        cfg.from_address.clone()
    };
    Ok(ReadResult {
        uid: uid_s,
        account: account_name,
        account_email,
        account_id: cfg.account_id.clone(),
        message_id: message_id_header,
        subject,
        from: if sender_name.is_empty() {
            sender_addr.clone()
        } else {
            sender_name
        },
        from_address: sender_addr,
        date: date_str,
        body: body_trunc,
        attachments,
    })
}

/// `_read_email_across_accounts(uid, message_id, folder)` — try each account;
/// resolve to a single match, a multi-account error, or a not-found error.
fn read_email_across_accounts(
    uid: Option<&str>,
    message_id: Option<&str>,
    folder: &str,
) -> Result<ReadResult, String> {
    let rows = list_accounts_raw();
    let mut matches: Vec<ReadResult> = Vec::new();
    let mut errors: Vec<String> = Vec::new();
    for row in &rows {
        let selector = account_selector(row);
        let account_name = row
            .name
            .clone()
            .filter(|s| !s.is_empty())
            .or_else(|| row.imap_user.clone())
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| row.id.clone());
        let account_email = row
            .imap_user
            .clone()
            .filter(|s| !s.is_empty())
            .or_else(|| row.from_address.clone())
            .unwrap_or_default();
        match read_email(uid, message_id, folder, Some(&selector)) {
            Ok(r) => matches.push(r),
            Err(e) => errors.push(format!("{account_name} <{account_email}>: {e}")),
        }
    }
    if matches.len() == 1 {
        return Ok(matches.into_iter().next().unwrap());
    }
    if matches.len() > 1 {
        let accounts = matches
            .iter()
            .map(|m| format!("{} <{}>", m.account, m.account_email))
            .collect::<Vec<_>>()
            .join(", ");
        let sel = uid.filter(|u| !u.is_empty()).or(message_id).unwrap_or("");
        return Err(format!(
            "UID {sel} exists in multiple accounts: {accounts}. Call read_email again with the account name/email."
        ));
    }
    Err(format!(
        "Email not found in any configured account. Checked: {}",
        errors.join("; ")
    ))
}

/// `_smtp_ready(cfg)`.
fn smtp_ready(cfg: &LoadedConfig) -> bool {
    !cfg.smtp_host.is_empty() && !cfg.smtp_user.is_empty() && !cfg.smtp_password.is_empty()
}

/// `_resolve_send_config(account)` — pick an SMTP-capable account: the requested
/// one if ready, else (when no selector) the first SMTP-ready account. Returns
/// `(selector, cfg)`. `Err` carries the ValueError text.
fn resolve_send_config(account: Option<&str>) -> Result<(Option<String>, LoadedConfig), String> {
    let cfg = load_config(account)?;
    if smtp_ready(&cfg) {
        return Ok((account.map(str::to_string), cfg));
    }
    if let Some(sel) = account {
        let name = cfg
            .account_name
            .clone()
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| sel.to_string());
        return Err(format!("Email account {name} has no SMTP configured"));
    }
    for row in list_accounts_raw() {
        let selector = account_selector(&row);
        if let Ok(trial) = load_config(Some(&selector)) {
            if smtp_ready(&trial) {
                return Ok((Some(selector), trial));
            }
        }
    }
    Err("No SMTP-capable email account configured".to_string())
}

/// `_clean_header_value(value)` — unfold CR/LF runs to a single space and trim.
fn clean_header_value(value: &str) -> String {
    static RE: once_cell::sync::Lazy<regex::Regex> =
        once_cell::sync::Lazy::new(|| regex::Regex::new(r"[\r\n]+[ \t]*").unwrap());
    RE.replace_all(value, " ").trim().to_string()
}

/// `email.utils.formatdate(localtime=True)` — an RFC822 Date header. We emit the
/// UTC form (matching the routes layer's `rfc822_date_now`); the only observable
/// use is the Date header on outgoing mail, which servers accept in UTC offset.
fn formatdate_now() -> String {
    chrono::Utc::now()
        .format("%a, %d %b %Y %H:%M:%S +0000")
        .to_string()
}

/// `email.utils.make_msgid()` — a unique Message-ID.
fn make_msgid() -> String {
    let id = uuid::Uuid::new_v4().simple();
    let pid = std::process::id();
    format!("<{id}.{pid}@odysseus.local>")
}

/// Encode a header value as a single base64 encoded-word when it is non-ASCII,
/// else pass it through (the `email.header` behavior `EmailMessage` applies on
/// serialize). The account headers the MCP tools emit are short, so a single
/// encoded-word (no folding) matches the common case.
fn encode_header(value: &str) -> String {
    if value.is_ascii() {
        value.to_string()
    } else {
        format!("=?utf-8?b?{}?=", base64_encode(value.as_bytes()))
    }
}

/// base64 of arbitrary bytes (the `email.base64mime` analogue).
fn base64_encode(data: &[u8]) -> String {
    use base64::Engine;
    base64::engine::general_purpose::STANDARD.encode(data)
}

/// The bytes of a built outbound message (handed to SMTP `send_raw` and appended
/// to Sent). The Message-ID lives in the headers inside `bytes` (the Python's
/// `_send_email` returns it in its dict, but the `send_email` tool's rendered text
/// does not surface it, so we don't carry it separately).
struct OutgoingMessage {
    bytes: Vec<u8>,
}

/// Build the RFC822 bytes for an outbound message — the `EmailMessage()` +
/// `set_content(body)` analogue. A single `text/plain; charset=utf-8` part with
/// the standard headers; the body is base64-encoded (a valid `set_content` CTE).
fn build_outgoing(
    from_address: &str,
    to: &str,
    subject: &str,
    body: &str,
    cc: Option<&str>,
    in_reply_to: Option<&str>,
    references: Option<&str>,
) -> OutgoingMessage {
    let message_id = make_msgid();
    let mut headers: Vec<(String, String)> = Vec::new();
    // (headers built below; message_id is embedded as the Message-ID header)
    headers.push(("From".into(), encode_header(&clean_header_value(from_address))));
    headers.push(("To".into(), encode_header(&clean_header_value(to))));
    headers.push(("Subject".into(), encode_header(&clean_header_value(subject))));
    if let Some(cc) = cc.filter(|c| !c.is_empty()) {
        headers.push(("Cc".into(), encode_header(&clean_header_value(cc))));
    }
    if let Some(irt) = in_reply_to.filter(|c| !c.is_empty()) {
        headers.push(("In-Reply-To".into(), clean_header_value(irt)));
    }
    if let Some(refs) = references.filter(|c| !c.is_empty()) {
        headers.push(("References".into(), clean_header_value(refs)));
    }
    headers.push(("Date".into(), formatdate_now()));
    headers.push(("Message-ID".into(), message_id.clone()));

    let mut top = String::new();
    for (k, v) in &headers {
        top.push_str(&format!("{k}: {v}\r\n"));
    }
    top.push_str("MIME-Version: 1.0\r\n");
    top.push_str("Content-Type: text/plain; charset=\"utf-8\"\r\n");
    top.push_str("Content-Transfer-Encoding: base64\r\n\r\n");
    let b64 = base64_encode(body.as_bytes());
    let wrapped = b64
        .as_bytes()
        .chunks(76)
        .map(|c| String::from_utf8_lossy(c).to_string())
        .collect::<Vec<_>>()
        .join("\r\n");
    top.push_str(&wrapped);
    top.push_str("\r\n");
    OutgoingMessage {
        bytes: top.into_bytes(),
    }
}

/// The dict `_send_email` returns.
struct SendResult {
    to: Vec<String>,
    subject: String,
    account: Option<String>,
}

/// `_send_email(to, subject, body, in_reply_to, references, cc, bcc, account)`.
#[allow(clippy::too_many_arguments)]
fn send_email(
    to: &str,
    subject: &str,
    body: &str,
    in_reply_to: Option<&str>,
    references: Option<&str>,
    cc: Option<&str>,
    bcc: Option<&str>,
    account: Option<&str>,
) -> Result<SendResult, String> {
    let (send_account, cfg) = resolve_send_config(account)?;
    let out = build_outgoing(&cfg.from_address, to, subject, body, cc, in_reply_to, references);

    // Build the recipient list: to + cc + bcc, comma-split + trimmed.
    let mut recipients: Vec<String> = split_addrs(to);
    if let Some(cc) = cc.filter(|c| !c.is_empty()) {
        recipients.extend(split_addrs(cc));
    }
    if let Some(bcc) = bcc.filter(|c| !c.is_empty()) {
        recipients.extend(split_addrs(bcc));
    }

    // `conn = _smtp_connect(...); conn.send_message(...)`. lettre's send_raw is the
    // `smtp.send_message(msg, from_addr, to_addrs)` analogue.
    let email_cfg = cfg.to_email_config();
    let timeout = email_socket_timeout().max(1.0) as u64;
    send_smtp_message(&email_cfg, &cfg.from_address, &recipients, &out.bytes, timeout)?;

    // Append to the account's Sent folder (best-effort; delivery already succeeded).
    if let Ok(mut imap) = imap_connect(send_account.as_deref()) {
        let sent_folder = detect_sent_folder(&mut imap);
        let _ = imap
            .append(&sent_folder, &out.bytes)
            .flag(imap::types::Flag::Seen)
            .finish();
        let _ = imap.logout();
    }

    Ok(SendResult {
        to: recipients,
        subject: subject.to_string(),
        account: cfg.account_name.clone(),
    })
}

/// `to.split(",")` -> trimmed, non-empty addresses.
fn split_addrs(s: &str) -> Vec<String> {
    s.split(',')
        .map(|a| a.trim().to_string())
        .filter(|a| !a.is_empty())
        .collect()
}

/// `str(result['to'])` — `result['to']` is a Python list of recipient strings,
/// and the dispatcher interpolates it into an f-string, so the repr is Python's
/// list-of-str form: `['a@x.com', 'b@y.com']` (SINGLE quotes), and `[]` for an
/// empty list. Note this assumes the addresses contain no `'`/backslash (real
/// email addresses don't), matching the common output; Rust's `{:?}` would emit
/// DOUBLE quotes, which is the drift this corrects.
fn py_list_repr(items: &[String]) -> String {
    if items.is_empty() {
        "[]".to_string()
    } else {
        format!("['{}']", items.join("', '"))
    }
}

/// The dict `_reply_to_email` returns (a `SendResult` plus the resolved subject /
/// recipient for the dispatcher's report line).
struct ReplyResult {
    subject: String,
    to: Vec<String>,
}

/// `_reply_to_email(uid, body, folder, reply_all, account)`.
fn reply_to_email(
    uid: &str,
    body: &str,
    folder: &str,
    reply_all: bool,
    account: Option<&str>,
) -> Result<ReplyResult, String> {
    let mut conn = imap_connect(account)?;
    let _ = conn.examine(folder);
    let raw = match conn.uid_fetch(uid, "(RFC822)") {
        Ok(fetches) => fetches.iter().find_map(|f| f.body().map(|b| b.to_vec())),
        Err(_) => {
            let _ = conn.logout();
            return Err(format!("Failed to fetch email UID {uid}"));
        }
    };
    let _ = conn.logout();
    let raw = match raw {
        Some(b) if !b.is_empty() => b,
        _ => return Err(format!("Failed to fetch email UID {uid}")),
    };
    let orig = match parse_message(&raw) {
        Some(m) => m,
        None => return Err(format!("Failed to fetch email UID {uid}")),
    };

    let orig_subject = decode_header(&orig.header("subject").unwrap_or_default());
    let reply_subject = if orig_subject.to_lowercase().starts_with("re:") {
        orig_subject.clone()
    } else {
        format!("Re: {orig_subject}")
    };
    let orig_message_id = orig.header("message-id").unwrap_or_default();
    let orig_references = orig.header("references").unwrap_or_default();
    let new_references = if !orig_references.is_empty() {
        format!("{orig_references} {orig_message_id}").trim().to_string()
    } else {
        orig_message_id.clone()
    };

    let sender = decode_header(&orig.header("from").unwrap_or_default());
    let (_, sender_addr) = parseaddr(&sender);
    let to_addrs = sender_addr.clone();

    // reply_all: CC the original To + Cc recipients (minus the sender).
    let cc = if reply_all {
        let mut cc_addrs: Vec<String> = Vec::new();
        for header_name in ["to", "cc"] {
            let raw_hdr = orig.header(header_name).unwrap_or_default();
            for (_, addr) in getaddresses(&raw_hdr) {
                if !addr.is_empty() && addr != sender_addr {
                    cc_addrs.push(addr);
                }
            }
        }
        if cc_addrs.is_empty() {
            None
        } else {
            Some(cc_addrs.join(", "))
        }
    } else {
        None
    };

    let sent = send_email(
        &to_addrs,
        &reply_subject,
        body,
        Some(&orig_message_id),
        Some(&new_references),
        cc.as_deref(),
        None,
        account,
    )?;
    Ok(ReplyResult {
        subject: sent.subject,
        to: sent.to,
    })
}

/// `email.utils.getaddresses([raw])` -> `[(name, addr), ...]`. Splits a header on
/// commas (respecting angle brackets) and parses each piece via [`parseaddr`].
fn getaddresses(raw: &str) -> Vec<(String, String)> {
    if raw.trim().is_empty() {
        return Vec::new();
    }
    let mut parts: Vec<String> = Vec::new();
    let mut depth = 0i32;
    let mut cur = String::new();
    for ch in raw.chars() {
        match ch {
            '<' => {
                depth += 1;
                cur.push(ch);
            }
            '>' => {
                depth -= 1;
                cur.push(ch);
            }
            ',' if depth <= 0 => {
                parts.push(std::mem::take(&mut cur));
            }
            _ => cur.push(ch),
        }
    }
    if !cur.trim().is_empty() {
        parts.push(cur);
    }
    parts
        .into_iter()
        .map(|p| parseaddr(&p))
        .filter(|(n, a)| !(n.is_empty() && a.is_empty()))
        .collect()
}

/// `_set_flag(uid, folder, flag, add, account)` — STORE +/-FLAGS via UID; expunge
/// after a `\Deleted` add. Returns success.
fn set_flag(uid: &str, folder: &str, flag: &str, add: bool, account: Option<&str>) -> bool {
    let mut conn = match imap_connect(account) {
        Ok(c) => c,
        Err(_) => return false,
    };
    if conn.select(folder).is_err() {
        let _ = conn.logout();
        return false;
    }
    let op = if add { "+FLAGS" } else { "-FLAGS" };
    let query = format!("{op} ({flag})");
    let ok = match conn.uid_store(uid, &query) {
        Ok(fetches) => fetches.iter().count() > 0,
        Err(_) => {
            let _ = conn.logout();
            return false;
        }
    };
    if add && flag == "\\Deleted" {
        let _ = conn.expunge();
    }
    let _ = conn.logout();
    ok
}

/// `_bulk_set_flag(uids, folder, flag, add, account)` — one STORE over the
/// comma-joined message-set; returns the number of UIDs that actually exist.
fn bulk_set_flag(uids: &[String], folder: &str, flag: &str, add: bool, account: Option<&str>) -> usize {
    if uids.is_empty() {
        return 0;
    }
    let mut conn = match imap_connect(account) {
        Ok(c) => c,
        Err(_) => return 0,
    };
    if conn.select(folder).is_err() {
        let _ = conn.logout();
        return 0;
    }
    let op = if add { "+FLAGS" } else { "-FLAGS" };
    let msg_set = uids.join(",");
    // First a FETCH (UID) to count which exist.
    let touched = match conn.uid_fetch(&msg_set, "(UID)") {
        Ok(fetches) => fetches.iter().filter(|f| f.uid.is_some()).count(),
        Err(_) => {
            let _ = conn.logout();
            return 0;
        }
    };
    if touched == 0 {
        let _ = conn.logout();
        return 0;
    }
    let query = format!("{op} ({flag})");
    if conn.uid_store(&msg_set, &query).is_err() {
        let _ = conn.logout();
        return 0;
    }
    if add && flag == "\\Deleted" {
        let _ = conn.expunge();
    }
    let _ = conn.logout();
    touched
}

/// `_bulk_move(uids, source_folder, dest_folder, account, role)` — MOVE many
/// messages in one connection; UID COPY+STORE+EXPUNGE fallback. Returns the count
/// of UIDs that existed.
fn bulk_move(
    uids: &[String],
    source_folder: &str,
    dest_folder: &str,
    account: Option<&str>,
    role: &str,
) -> usize {
    if uids.is_empty() {
        return 0;
    }
    let mut conn = match imap_connect(account) {
        Ok(c) => c,
        Err(_) => return 0,
    };
    if conn.select(source_folder).is_err() {
        let _ = conn.logout();
        return 0;
    }
    let resolved_role = if role.is_empty() {
        folder_role_from_name(dest_folder)
    } else {
        role
    };
    let dest = resolve_folder(&mut conn, dest_folder, resolved_role);
    let msg_set = uids.join(",");
    let existing = match conn.uid_fetch(&msg_set, "(UID)") {
        Ok(fetches) => fetches.iter().filter(|f| f.uid.is_some()).count(),
        Err(_) => {
            let _ = conn.logout();
            return 0;
        }
    };
    if existing == 0 {
        let _ = conn.logout();
        return 0;
    }
    let moved = existing;
    // `mv`/`uid_mv` AUTO-QUOTE; `copy`/`uid_copy` do NOT, so quote the dest there.
    let dest_q = crate::routes::email_helpers::q(&dest);
    if conn.uid_mv(&msg_set, &dest).is_err() {
        // Fallback: UID COPY + flag-delete + expunge.
        if conn.uid_copy(&msg_set, &dest_q).is_err() {
            let _ = conn.logout();
            return 0;
        }
        if conn.uid_store(&msg_set, "+FLAGS (\\Deleted)").is_err() {
            let _ = conn.logout();
            return 0;
        }
        let _ = conn.expunge();
    }
    let _ = conn.logout();
    moved
}

/// `_search_uids(folder, criteria, account)` — UID list matching a search.
fn search_uids(folder: &str, criteria: &str, account: Option<&str>) -> Vec<String> {
    let mut conn = match imap_connect(account) {
        Ok(c) => c,
        Err(_) => return Vec::new(),
    };
    if conn.examine(folder).is_err() {
        let _ = conn.logout();
        return Vec::new();
    }
    let out = match conn.uid_search(criteria) {
        Ok(set) if !set.is_empty() => {
            let mut v: Vec<u32> = set.into_iter().collect();
            v.sort_unstable();
            v.into_iter().map(|u| u.to_string()).collect()
        }
        _ => Vec::new(),
    };
    let _ = conn.logout();
    out
}

/// `_move_message(uid, source_folder, dest_folder, account, role)`.
fn move_message(
    uid: &str,
    source_folder: &str,
    dest_folder: &str,
    account: Option<&str>,
    role: &str,
) -> bool {
    let mut conn = match imap_connect(account) {
        Ok(c) => c,
        Err(_) => return false,
    };
    if conn.select(source_folder).is_err() {
        let _ = conn.logout();
        return false;
    }
    let resolved_role = if role.is_empty() {
        folder_role_from_name(dest_folder)
    } else {
        role
    };
    let dest = resolve_folder(&mut conn, dest_folder, resolved_role);
    let existing = match conn.uid_fetch(uid, "(UID)") {
        Ok(fetches) => fetches.iter().any(|f| f.uid.is_some()),
        Err(_) => {
            let _ = conn.logout();
            return false;
        }
    };
    if !existing {
        let _ = conn.logout();
        return false;
    }
    let dest_q = crate::routes::email_helpers::q(&dest);
    if conn.uid_mv(uid, &dest).is_ok() {
        let _ = conn.logout();
        return true;
    }
    // Fallback: UID COPY + delete.
    if conn.uid_copy(uid, &dest_q).is_err() {
        let _ = conn.logout();
        return false;
    }
    if conn.uid_store(uid, "+FLAGS (\\Deleted)").is_err() {
        let _ = conn.logout();
        return false;
    }
    let ok = conn.expunge().is_ok();
    let _ = conn.logout();
    ok
}

/// `_delete_email(uid, folder, permanent, account)`.
fn delete_email(uid: &str, folder: &str, permanent: bool, account: Option<&str>) -> bool {
    let cfg = match load_config(account) {
        Ok(c) => c,
        Err(_) => return false,
    };
    if permanent {
        return set_flag(uid, folder, "\\Deleted", true, account);
    }
    move_message(uid, folder, &cfg.trash_folder, account, "trash")
}

/// `_archive_email(uid, folder, account)`.
fn archive_email(uid: &str, folder: &str, account: Option<&str>) -> bool {
    let cfg = match load_config(account) {
        Ok(c) => c,
        Err(_) => return false,
    };
    move_message(uid, folder, &cfg.archive_folder, account, "archive")
}

/// The dict `_download_attachment` returns.
struct DownloadResult {
    path: String,
    filename: String,
    size: u64,
}

/// `_download_attachment(uid, index, folder, account)` — extract an attachment to
/// disk. `Err` carries the `{"error": ...}` text.
fn download_attachment(
    uid: &str,
    index: usize,
    folder: &str,
    account: Option<&str>,
) -> Result<DownloadResult, String> {
    let mut conn = imap_connect(account)?;
    let _ = conn.examine(folder);
    let raw = match conn.uid_fetch(uid, "(RFC822)") {
        Ok(fetches) => fetches.iter().find_map(|f| f.body().map(|b| b.to_vec())),
        Err(_) => {
            let _ = conn.logout();
            return Err(format!("Failed to fetch email UID {uid}"));
        }
    };
    let _ = conn.logout();
    let raw = match raw {
        Some(b) if !b.is_empty() => b,
        _ => return Err(format!("Failed to fetch email UID {uid}")),
    };
    let msg = match parse_message(&raw) {
        Some(m) => m,
        None => return Err(format!("Failed to fetch email UID {uid}")),
    };

    // `target_dir = DATA_DIR / "mail-attachments" / f"{folder}_{uid}"`.
    let target_dir = data_dir().join("mail-attachments").join(format!("{folder}_{uid}"));
    let filepath = match extract_attachment_to_disk(&msg, index, &target_dir) {
        Some(p) => p,
        None => return Err(format!("Attachment index {index} not found")),
    };
    let size = std::fs::metadata(&filepath).map(|m| m.len()).unwrap_or(0);
    let filename = filepath
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_default();
    Ok(DownloadResult {
        path: filepath.to_string_lossy().to_string(),
        filename,
        size,
    })
}

// ===========================================================================
// Tool definitions (`list_tools`) — the inputSchemas with the shared ACCOUNT_PROP
// ===========================================================================

/// `ACCOUNT_PROP` — the shared optional `account` property merged into each tool's
/// schema.
fn account_prop_value() -> Value {
    json!({
        "type": "string",
        "description":
            "Which email account to use (name, email, or id). \
             Omit to use the default account. Use list_email_accounts to discover available accounts.",
    })
}

/// Build a tool inputSchema object preserving property order via [`IndexMap`].
fn schema(properties: Vec<(&str, Value)>, required: Vec<&str>) -> Value {
    let mut props: IndexMap<String, Value> = IndexMap::new();
    for (k, v) in properties {
        props.insert(k.to_string(), v);
    }
    let props_value = Value::Object(props.into_iter().collect());
    json!({
        "type": "object",
        "properties": props_value,
        "required": required,
    })
}

/// Build a schema with the shared `account` property appended last (matching the
/// Python `{**props, **ACCOUNT_PROP}` merge order).
fn schema_with_account(mut properties: Vec<(&str, Value)>, required: Vec<&str>) -> Value {
    properties.push(("account", account_prop_value()));
    schema(properties, required)
}

/// `@server.list_tools()` — the eleven tool definitions.
pub fn tools() -> Vec<ToolDef> {
    vec![
        ToolDef::new(
            "list_email_accounts",
            "List the email accounts configured in Odysseus. Returns each account's \
             name, email address, and whether it's the default. Use this first when \
             the user asks about a specific inbox by name (e.g. 'check work').",
            schema(vec![], vec![]),
        ),
        ToolDef::new(
            "list_emails",
            "List unread or unresponded emails from the inbox. \
             Returns subject, sender, date, and cached AI summary for each. \
             Use this to check what emails need attention. \
             Pass `account` to scan a non-default mailbox.",
            schema_with_account(
                vec![
                    ("folder", json!({"type": "string", "description": "IMAP folder to check (default: INBOX)", "default": "INBOX"})),
                    ("max_results", json!({"type": "integer", "description": "Maximum number of emails to return (default: 20)", "default": 20})),
                    ("unresponded_only", json!({"type": "boolean", "description": "Only show emails without replies (default: false)", "default": false})),
                    ("unread_only", json!({"type": "boolean", "description": "Only show unread emails. Default false so latest/all inbox requests match normal mail clients.", "default": false})),
                ],
                vec![],
            ),
        ),
        ToolDef::new(
            "download_attachment",
            "Download an email attachment to the local disk so you can read it. \
             Returns the local file path which you can then read with read_file. \
             Use this when you need to review a document, spreadsheet, or other \
             file attached to an email.",
            schema_with_account(
                vec![
                    ("uid", json!({"type": "string", "description": "Email UID from list_emails"})),
                    ("index", json!({"type": "integer", "description": "Attachment index (from read_email's attachments list)"})),
                    ("folder", json!({"type": "string", "description": "IMAP folder (default: INBOX)", "default": "INBOX"})),
                ],
                vec!["uid", "index"],
            ),
        ),
        ToolDef::new(
            "send_email",
            "Send a new email via SMTP. Provide recipient(s), subject, and body. \
             For replying to an existing thread, use reply_to_email instead. \
             Pass `account` to send from a non-default mailbox.",
            schema_with_account(
                vec![
                    ("to", json!({"type": "string", "description": "Recipient email address(es), comma-separated"})),
                    ("subject", json!({"type": "string", "description": "Email subject line"})),
                    ("body", json!({"type": "string", "description": "Plain text body"})),
                    ("cc", json!({"type": "string", "description": "CC address(es), comma-separated (optional)"})),
                    ("bcc", json!({"type": "string", "description": "BCC address(es), comma-separated (optional)"})),
                ],
                vec!["to", "subject", "body"],
            ),
        ),
        ToolDef::new(
            "reply_to_email",
            "Reply to an existing email by UID. Automatically threads the reply with \
             In-Reply-To and References headers, prefixes 'Re:' on the subject, and \
             uses the original sender as the recipient. Set reply_all=true to also CC \
             the original To/Cc recipients. For follow-up 'reply ...' requests, use \
             the exact UID from the latest list_emails/read_email result; never invent UID 1.",
            schema_with_account(
                vec![
                    ("uid", json!({"type": "string", "description": "Exact Email UID from list_emails/read_email; never invent UID 1"})),
                    ("body", json!({"type": "string", "description": "Reply body text"})),
                    ("folder", json!({"type": "string", "description": "IMAP folder (default: INBOX)", "default": "INBOX"})),
                    ("reply_all", json!({"type": "boolean", "description": "Reply to all recipients (default: false)", "default": false})),
                ],
                vec!["uid", "body"],
            ),
        ),
        ToolDef::new(
            "archive_email",
            "Move an email out of the inbox into the Archive folder. Use after handling an email you want to keep but no longer need in the inbox.",
            schema_with_account(
                vec![
                    ("uid", json!({"type": "string", "description": "Email UID from list_emails"})),
                    ("folder", json!({"type": "string", "description": "Source folder (default: INBOX)", "default": "INBOX"})),
                ],
                vec!["uid"],
            ),
        ),
        ToolDef::new(
            "delete_email",
            "Delete an email. By default moves it to the Trash folder; pass permanent=true to expunge immediately.",
            schema_with_account(
                vec![
                    ("uid", json!({"type": "string", "description": "Email UID from list_emails"})),
                    ("folder", json!({"type": "string", "description": "Source folder (default: INBOX)", "default": "INBOX"})),
                    ("permanent", json!({"type": "boolean", "description": "Hard-delete instead of move to Trash", "default": false})),
                ],
                vec!["uid"],
            ),
        ),
        ToolDef::new(
            "mark_email_read",
            "Mark an email as read (\\Seen flag) or unread (read=false).",
            schema_with_account(
                vec![
                    ("uid", json!({"type": "string", "description": "Email UID"})),
                    ("folder", json!({"type": "string", "description": "IMAP folder", "default": "INBOX"})),
                    ("read", json!({"type": "boolean", "description": "True to mark read, false to mark unread", "default": true})),
                ],
                vec!["uid"],
            ),
        ),
        ToolDef::new(
            "bulk_email",
            "Perform one action on MANY emails at once — the efficient way to \
             'mark all as read', 'archive these', 'delete all spam', etc. Select \
             messages either by an explicit `uids` list OR by `all_unread: true` \
             (operates on every unread message in the folder). Far better than \
             calling mark_email_read / archive_email once per message.",
            schema_with_account(
                vec![
                    ("action", json!({"type": "string", "enum": ["mark_read", "mark_unread", "archive", "delete", "junk"], "description": "What to do to every selected message."})),
                    ("uids", json!({"type": "array", "items": {"type": "string"}, "description": "Explicit list of UIDs. Omit if using all_unread."})),
                    ("all_unread", json!({"type": "boolean", "description": "Operate on ALL unread messages in the folder (ignores uids).", "default": false})),
                    ("folder", json!({"type": "string", "description": "IMAP folder", "default": "INBOX"})),
                    ("permanent", json!({"type": "boolean", "description": "For delete: expunge instead of moving to Trash.", "default": false})),
                ],
                vec!["action"],
            ),
        ),
        ToolDef::new(
            "search_emails",
            "Search emails by free-text query (sender, subject, or body). \
             Walks INBOX + Sent + Archive by default so older threads are findable, \
             not just recent unread. Use this whenever the user names a person or \
             topic that isn't in the most recent inbox slice — e.g. 'Sara Sotheby's', \
             'invoice from EY', 'last email about the property'. Returns matching \
             emails with their UIDs so you can read_email or reply_to_email.",
            schema_with_account(
                vec![
                    ("query", json!({"type": "string", "description": "Free-text query. Matches FROM, SUBJECT, and body TEXT."})),
                    ("folders", json!({"type": "array", "items": {"type": "string"}, "description": "Folders to search (default: INBOX, Sent, Archive)"})),
                    ("max_results", json!({"type": "integer", "description": "Max results per folder (default: 20)", "default": 20})),
                ],
                vec!["query"],
            ),
        ),
        ToolDef::new(
            "read_email",
            "Read the full content of a specific email. \
             Provide either the UID (from list_emails) or a Message-ID. \
             Returns the subject, sender, date, and full body text.",
            schema_with_account(
                vec![
                    ("uid", json!({"type": "string", "description": "Email UID from list_emails results"})),
                    ("message_id", json!({"type": "string", "description": "RFC Message-ID header value"})),
                    ("folder", json!({"type": "string", "description": "IMAP folder (default: INBOX)", "default": "INBOX"})),
                ],
                vec![],
            ),
        ),
    ]
}

// ===========================================================================
// call_tool dispatcher — the `@server.call_tool()` body, run on a blocking thread
// ===========================================================================

/// Argument accessors mirroring `arguments.get(key, default)`.
fn arg_str<'a>(args: &'a Value, key: &str) -> Option<&'a str> {
    args.get(key).and_then(|v| v.as_str())
}
/// `acct = arguments.get("account")`, read with Python truthiness. The Python
/// dispatcher routes on `not acct`, so an explicitly-passed empty string is
/// FALSY and behaves exactly like a missing/`None` account (cross-account merge
/// when >=2 accounts exist; `_load_config("")`/`_resolve_account("")` both resolve
/// to the default row, identical to `None`). Normalize `""` -> `None` here so the
/// routing decisions and every downstream call match the Python truthiness.
fn arg_account<'a>(args: &'a Value, key: &str) -> Option<&'a str> {
    arg_str(args, key).filter(|s| !s.is_empty())
}
fn arg_str_or<'a>(args: &'a Value, key: &str, default: &'a str) -> &'a str {
    arg_str(args, key).unwrap_or(default)
}
fn arg_bool(args: &Value, key: &str, default: bool) -> bool {
    args.get(key).and_then(|v| v.as_bool()).unwrap_or(default)
}
fn arg_usize(args: &Value, key: &str, default: usize) -> usize {
    args.get(key)
        .and_then(|v| v.as_i64())
        .map(|n| n.max(0) as usize)
        .unwrap_or(default)
}

/// The synchronous body of `call_tool(name, arguments)`. Returns the TextContent
/// `text` strings (the harness wraps them into content blocks). Runs inside
/// `spawn_blocking`. Python's outer `try/except Exception as e -> "Error: {e}"` is
/// reproduced by the inner `Result` collation + the catch-all at the boundary.
fn call_tool_sync(name: &str, arguments: Value) -> Vec<String> {
    let run = || -> Result<Vec<String>, String> {
        match name {
            "list_email_accounts" => {
                let rows = list_accounts_raw();
                if rows.is_empty() {
                    return Ok(vec![
                        "No email accounts configured. Legacy single-account mode active.".to_string(),
                    ]);
                }
                let mut lines = vec![format!("Found {} email account(s):\n", rows.len())];
                for r in &rows {
                    let star = if r.is_default { " (default)" } else { "" };
                    let email = r
                        .imap_user
                        .clone()
                        .filter(|s| !s.is_empty())
                        .or_else(|| r.from_address.clone())
                        .filter(|s| !s.is_empty())
                        .unwrap_or_else(|| "(unknown)".to_string());
                    let name_disp = r.name.clone().unwrap_or_default();
                    lines.push(format!(
                        "- **{name_disp}**{star}\n  email: {email}\n  id: {}",
                        r.id
                    ));
                }
                Ok(vec![lines.join("\n")])
            }
            "list_emails" => Ok(vec![handle_list_emails(&arguments)?]),
            "download_attachment" => {
                let acct = arg_account(&arguments, "account");
                let uid = arg_str(&arguments, "uid");
                let index = arguments.get("index").and_then(|v| v.as_i64());
                let folder = arg_str_or(&arguments, "folder", "INBOX");
                if uid.is_none() || index.is_none() {
                    return Ok(vec!["Error: uid and index are required".to_string()]);
                }
                match download_attachment(uid.unwrap(), index.unwrap().max(0) as usize, folder, acct) {
                    Ok(r) => Ok(vec![format!(
                        "Attachment downloaded to: `{}`\nFilename: {}\nSize: {} bytes\n\nYou can now read this file using the read_file tool.",
                        r.path, r.filename, r.size
                    )]),
                    Err(e) => Ok(vec![format!("Error: {e}")]),
                }
            }
            "search_emails" => {
                let acct = arg_account(&arguments, "account");
                let q = arg_str_or(&arguments, "query", "");
                let folders = arguments
                    .get("folders")
                    .and_then(|v| v.as_array())
                    .map(|a| a.iter().filter_map(|x| x.as_str().map(str::to_string)).collect::<Vec<_>>());
                // `folders = arguments.get("folders") or None` — an empty list -> None.
                let folders = folders.filter(|f| !f.is_empty());
                let max_results = arg_usize(&arguments, "max_results", 20);
                // Python wraps the `_search_emails` call in its own try/except, so a
                // propagated connect/unknown-account error renders "Search failed: {e}".
                let hits = match search_emails(q, folders, max_results, acct) {
                    Ok(hits) => hits,
                    Err(e) => return Ok(vec![format!("Search failed: {e}")]),
                };
                if hits.is_empty() {
                    return Ok(vec![format!("No emails matched \"{q}\".")]);
                }
                let mut lines = vec![format!("Found {} email(s) matching \"{q}\":\n", hits.len())];
                for (i, em) in hits.iter().enumerate() {
                    lines.push(format!(
                        "{}. **{}**\n   From: {} ({})\n   Date: {}\n   Folder: {}\n   UID: {}",
                        i + 1,
                        em.subject,
                        em.from,
                        em.from_address,
                        em.date,
                        em.folder.clone().unwrap_or_else(|| "INBOX".to_string()),
                        em.uid
                    ));
                    if let Some(to) = &em.to {
                        if !to.is_empty() {
                            lines.push(format!("   To: {to}"));
                        }
                    }
                    if !em.summary.is_empty() {
                        lines.push(format!("   Summary: {}", em.summary));
                    }
                }
                Ok(vec![lines.join("\n")])
            }
            "read_email" => Ok(vec![handle_read_email(&arguments)?]),
            "send_email" => {
                let acct = arg_account(&arguments, "account");
                let to = arg_str(&arguments, "to");
                let subject = arg_str(&arguments, "subject");
                let body = arguments.get("body").and_then(|v| v.as_str());
                if to.map(str::is_empty).unwrap_or(true)
                    || subject.map(str::is_empty).unwrap_or(true)
                    || body.is_none()
                {
                    return Ok(vec!["Error: to, subject, and body are required".to_string()]);
                }
                let result = send_email(
                    to.unwrap(),
                    subject.unwrap(),
                    body.unwrap(),
                    None,
                    None,
                    arg_str(&arguments, "cc"),
                    arg_str(&arguments, "bcc"),
                    acct,
                )?;
                let acct_note = match &result.account {
                    Some(a) if !a.is_empty() => format!(" (from {a})"),
                    _ => String::new(),
                };
                Ok(vec![format!(
                    "Sent email to {} with subject '{}'{acct_note}.",
                    py_list_repr(&result.to),
                    result.subject
                )])
            }
            "reply_to_email" => {
                let acct = arg_account(&arguments, "account");
                let uid = arg_str(&arguments, "uid");
                let body = arguments.get("body").and_then(|v| v.as_str());
                if uid.map(str::is_empty).unwrap_or(true) || body.is_none() {
                    return Ok(vec!["Error: uid and body are required".to_string()]);
                }
                let folder = arg_str_or(&arguments, "folder", "INBOX");
                let reply_all = arg_bool(&arguments, "reply_all", false);
                match reply_to_email(uid.unwrap(), body.unwrap(), folder, reply_all, acct) {
                    Ok(result) => {
                        // Mark original as answered (best-effort).
                        let _ = set_flag(uid.unwrap(), folder, "\\Answered", true, acct);
                        Ok(vec![format!(
                            "Replied to UID {}: '{}' → {}",
                            uid.unwrap(),
                            result.subject,
                            py_list_repr(&result.to)
                        )])
                    }
                    Err(e) => Ok(vec![format!("Error: {e}")]),
                }
            }
            "archive_email" => {
                let acct = arg_account(&arguments, "account");
                let uid = arg_str(&arguments, "uid");
                if uid.map(str::is_empty).unwrap_or(true) {
                    return Ok(vec!["Error: uid is required".to_string()]);
                }
                let folder = arg_str_or(&arguments, "folder", "INBOX");
                let ok = archive_email(uid.unwrap(), folder, acct);
                Ok(vec![format!(
                    "{} UID {}",
                    if ok { "Archived" } else { "Failed to archive" },
                    uid.unwrap()
                )])
            }
            "delete_email" => {
                let acct = arg_account(&arguments, "account");
                let uid = arg_str(&arguments, "uid");
                if uid.map(str::is_empty).unwrap_or(true) {
                    return Ok(vec!["Error: uid is required".to_string()]);
                }
                let folder = arg_str_or(&arguments, "folder", "INBOX");
                let permanent = arg_bool(&arguments, "permanent", false);
                let ok = delete_email(uid.unwrap(), folder, permanent, acct);
                Ok(vec![format!(
                    "{} UID {}",
                    if ok { "Deleted" } else { "Failed to delete" },
                    uid.unwrap()
                )])
            }
            "mark_email_read" => {
                let acct = arg_account(&arguments, "account");
                let uid = arg_str(&arguments, "uid");
                if uid.map(str::is_empty).unwrap_or(true) {
                    return Ok(vec!["Error: uid is required".to_string()]);
                }
                let folder = arg_str_or(&arguments, "folder", "INBOX");
                let read = arg_bool(&arguments, "read", true);
                let ok = set_flag(uid.unwrap(), folder, "\\Seen", read, acct);
                let state = if read { "read" } else { "unread" };
                Ok(vec![format!(
                    "{} UID {} as {state}",
                    if ok { "Marked" } else { "Failed to mark" },
                    uid.unwrap()
                )])
            }
            "bulk_email" => Ok(vec![handle_bulk_email(&arguments)?]),
            _ => Ok(vec![format!("Unknown tool: {name}")]),
        }
    };
    match run() {
        Ok(texts) => texts,
        Err(e) => vec![format!("Error: {e}")],
    }
}

/// The `list_emails` branch — broken out for the multi-account header-note logic.
fn handle_list_emails(arguments: &Value) -> Result<String, String> {
    let acct = arg_account(arguments, "account");
    let folder = arg_str_or(arguments, "folder", "INBOX");
    // `arguments.get("max_results", arguments.get("limit", 20))`.
    let max_results = arguments
        .get("max_results")
        .and_then(|v| v.as_i64())
        .or_else(|| arguments.get("limit").and_then(|v| v.as_i64()))
        .map(|n| n.max(0) as usize)
        .unwrap_or(20);
    let unresponded_only = arg_bool(arguments, "unresponded_only", false);
    let unread_only = arg_bool(arguments, "unread_only", false);

    let all_accounts = list_accounts_raw();
    let mut header_lines: Vec<String> = Vec::new();
    let mut errors: Vec<String> = Vec::new();
    let mut results: Vec<EmailEntry>;

    if all_accounts.len() >= 2 && acct.is_none() {
        let (r, errs) = list_emails_across_accounts(folder, max_results, unresponded_only, unread_only);
        results = r;
        errors = errs;
        let account_names: Vec<String> = all_accounts
            .iter()
            .map(|a| {
                let name = a
                    .name
                    .clone()
                    .filter(|s| !s.is_empty())
                    .or_else(|| a.imap_user.clone())
                    .unwrap_or_default();
                let email = a
                    .imap_user
                    .clone()
                    .filter(|s| !s.is_empty())
                    .or_else(|| a.from_address.clone())
                    .filter(|s| !s.is_empty())
                    .unwrap_or_else(|| "?".to_string());
                format!("{name} <{email}>")
            })
            .collect();
        header_lines.push(format!(
            "[EMAIL ACCOUNT CONTEXT: No `account` was provided, so this result is merged across configured accounts: {}. Each row includes its source account.]\n",
            account_names.join(", ")
        ));
    } else {
        results = list_emails(folder, max_results, unresponded_only, unread_only, acct)?;
        let active_cfg = load_config(acct)?;
        let has_name = active_cfg.account_name.as_deref().map(|s| !s.is_empty()).unwrap_or(false)
            || !active_cfg.imap_user.is_empty();
        if has_name {
            let label = active_cfg
                .account_name
                .clone()
                .filter(|s| !s.is_empty())
                .or_else(|| Some(active_cfg.imap_user.clone()).filter(|s| !s.is_empty()))
                .unwrap_or_else(|| "default".to_string());
            for item in &mut results {
                item.account = Some(label.clone());
                item.account_email = Some(active_cfg.imap_user.clone());
            }
        }
    }

    if all_accounts.len() >= 2 && acct.is_some() {
        let active_cfg = load_config(acct)?;
        let active_name = active_cfg
            .account_name
            .clone()
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| "default".to_string());
        let active_email = active_cfg.imap_user.clone();
        let other: Vec<String> = all_accounts
            .iter()
            .filter(|a| Some(&a.id) != active_cfg.account_id.as_ref())
            .map(|a| {
                let name = a.name.clone().unwrap_or_default();
                let email = a
                    .imap_user
                    .clone()
                    .filter(|s| !s.is_empty())
                    .or_else(|| a.from_address.clone())
                    .filter(|s| !s.is_empty())
                    .unwrap_or_else(|| "?".to_string());
                format!("{name} <{email}>")
            })
            .collect();
        header_lines.push(format!(
            "[EMAIL ACCOUNT CONTEXT: This result is ONLY from account `{active_name}` ({active_email}). Other configured accounts: {}. If the user asks for Gmail/another inbox, call list_emails again with `account` set to that account name or email.]\n",
            other.join(", ")
        ));
    }
    if !errors.is_empty() {
        header_lines.push(format!("[EMAIL ACCOUNT ERRORS: {}]\n", errors.join("; ")));
    }

    if results.is_empty() {
        let mut msg = "No unread/unresponded emails found.".to_string();
        if !header_lines.is_empty() {
            msg = format!("{}{}", header_lines.join("\n"), msg);
        }
        return Ok(msg);
    }

    // `lines = header_lines + [f"Found {n} email(s):\n"]`.
    let mut lines: Vec<String> = header_lines;
    lines.push(format!("Found {} email(s):\n", results.len()));
    for (i, em) in results.iter().enumerate() {
        let mut line = format!(
            "{}. **{}**\n   From: {} ({})\n   Date: {}\n   UID: {}",
            i + 1,
            em.subject,
            em.from,
            em.from_address,
            em.date,
            em.uid
        );
        if let Some(acct_name) = &em.account {
            if !acct_name.is_empty() {
                let mut label = acct_name.clone();
                if let Some(ae) = &em.account_email {
                    if !ae.is_empty() {
                        label = format!("{label} <{ae}>");
                    }
                }
                line.push_str(&format!("\n   Account: {label}"));
            }
        }
        if !em.summary.is_empty() {
            line.push_str(&format!("\n   Summary: {}", em.summary));
        }
        lines.push(line);
    }
    // `"\n\n".join(lines)`.
    Ok(lines.join("\n\n"))
}

/// The `read_email` branch — the across-accounts dispatch + the rendered block.
fn handle_read_email(arguments: &Value) -> Result<String, String> {
    let acct = arg_account(arguments, "account");
    let uid = arg_str(arguments, "uid");
    let message_id = arg_str(arguments, "message_id");
    let folder = arg_str_or(arguments, "folder", "INBOX");
    let all_accounts = list_accounts_raw();

    let result = if all_accounts.len() >= 2 && acct.is_none() {
        read_email_across_accounts(uid, message_id, folder)
    } else {
        read_email(uid, message_id, folder, acct)
    };
    let result = match result {
        Ok(r) => r,
        Err(e) => return Ok(format!("Error: {e}")),
    };

    let mut text = format!(
        "**Subject:** {}\n**From:** {} ({})\n**Date:** {}\n**UID:** {}\n**Account:** {} ({})\n**Message-ID:** {}\n",
        result.subject,
        result.from,
        result.from_address,
        result.date,
        result.uid,
        result.account,
        result.account_email,
        result.message_id,
    );
    if !result.attachments.is_empty() {
        text.push_str(&format!("\n**Attachments ({}):**\n", result.attachments.len()));
        for a in &result.attachments {
            let size_kb = a.size / 1024;
            text.push_str(&format!(
                "  - [{}] {} ({}, {}KB)\n",
                a.index, a.filename, a.content_type, size_kb
            ));
        }
        text.push_str("\n_Use `download_attachment` with the UID and index to download._\n");
    }
    text.push_str(&format!("\n---\n\n{}", result.body));
    // account_id is carried on the result but the rendered block does not surface
    // it (matches Python — internal-only); reference it so the field is exercised.
    let _ = &result.account_id;
    Ok(text)
}

/// The `bulk_email` branch.
fn handle_bulk_email(arguments: &Value) -> Result<String, String> {
    let acct = arg_account(arguments, "account");
    let action = arg_str_or(arguments, "action", "");
    let folder = arg_str_or(arguments, "folder", "INBOX");
    let all_unread = arg_bool(arguments, "all_unread", false);
    let mut uids: Vec<String> = arguments
        .get("uids")
        .and_then(|v| v.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|x| {
                    x.as_str()
                        .map(str::to_string)
                        .or_else(|| x.as_i64().map(|n| n.to_string()))
                })
                .collect()
        })
        .unwrap_or_default();
    if all_unread {
        uids = search_uids(folder, "UNSEEN", acct);
    }
    if uids.is_empty() {
        return Ok("No messages selected (pass uids or all_unread=true).".to_string());
    }
    let requested_n = uids.len();

    // The dispatch is wrapped in `try/except -> "Bulk {action} failed ...: {e}"`.
    // None of our ops raise (they return counts / bools), so the inner closure
    // returns the changed count + verb directly.
    let dispatch = || -> Option<(usize, &'static str)> {
        match action {
            "mark_read" => Some((bulk_set_flag(&uids, folder, "\\Seen", true, acct), "marked read")),
            "mark_unread" => Some((bulk_set_flag(&uids, folder, "\\Seen", false, acct), "marked unread")),
            "archive" => {
                let cfg = load_config(acct).ok()?;
                Some((bulk_move(&uids, folder, &cfg.archive_folder, acct, "archive"), "archived"))
            }
            "junk" => {
                // `cfg.get("junk_folder") or "Junk"` — the cfg never carries a
                // junk_folder key, so it is always "Junk" (faithful).
                Some((bulk_move(&uids, folder, "Junk", acct, "junk"), "moved to Junk"))
            }
            "delete" => {
                let permanent = arg_bool(arguments, "permanent", false);
                if permanent {
                    Some((bulk_set_flag(&uids, folder, "\\Deleted", true, acct), "permanently deleted"))
                } else {
                    let cfg = load_config(acct).ok()?;
                    Some((bulk_move(&uids, folder, &cfg.trash_folder, acct, "trash"), "moved to Trash"))
                }
            }
            _ => None,
        }
    };
    let (changed_n, verb) = match dispatch() {
        Some(v) => v,
        None => {
            return Ok(format!(
                "Unknown bulk action: {action:?}. Use mark_read/mark_unread/archive/delete/junk."
            ))
        }
    };
    if changed_n == 0 {
        return Ok(format!(
            "No matching UIDs found in {folder}; 0 of {requested_n} email(s) {verb}."
        ));
    }
    let suffix = if changed_n == requested_n {
        String::new()
    } else {
        format!(" ({changed_n} of {requested_n} requested UIDs matched)")
    };
    Ok(format!("Done — {changed_n} email(s) {verb}{suffix}."))
}

// ===========================================================================
// Server entry point (the `@server.call_tool()` handler the harness drives)
// ===========================================================================

/// `@server.call_tool()` — the handler the [`crate::mcp_servers::harness`] loop
/// drives. The IMAP/SMTP stack is blocking, so the synchronous body runs inside
/// `tokio::task::spawn_blocking` to keep the async stdio loop responsive.
pub fn handler() -> CallToolHandler {
    Box::new(|name: String, arguments: Value| {
        Box::pin(async move {
            let texts =
                match tokio::task::spawn_blocking(move || call_tool_sync(&name, arguments)).await {
                    Ok(t) => t,
                    Err(e) => vec![format!("Error: {e}")],
                };
            texts.into_iter().map(text_content).collect::<Vec<Value>>()
        })
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tool_list_has_eleven_tools_with_account_prop() {
        let tools = tools();
        assert_eq!(tools.len(), 11);
        let names: Vec<&str> = tools.iter().map(|t| t.name.as_str()).collect();
        assert_eq!(
            names,
            vec![
                "list_email_accounts",
                "list_emails",
                "download_attachment",
                "send_email",
                "reply_to_email",
                "archive_email",
                "delete_email",
                "mark_email_read",
                "bulk_email",
                "search_emails",
                "read_email",
            ]
        );
        // Every tool EXCEPT list_email_accounts merges the shared `account` prop.
        for t in &tools {
            let props = &t.input_schema["properties"];
            if t.name == "list_email_accounts" {
                assert!(props.as_object().unwrap().is_empty());
            } else {
                assert!(props.get("account").is_some(), "{} missing account prop", t.name);
            }
        }
    }

    #[test]
    fn list_emails_schema_property_order_matches_python() {
        // IndexMap preserves the Python dict insertion order: folder, max_results,
        // unresponded_only, unread_only, then the merged account prop last.
        let tools = tools();
        let le = tools.iter().find(|t| t.name == "list_emails").unwrap();
        let props = le.input_schema["properties"].as_object().unwrap();
        let keys: Vec<&String> = props.keys().collect();
        assert_eq!(
            keys,
            vec!["folder", "max_results", "unresponded_only", "unread_only", "account"]
        );
    }

    #[test]
    fn send_email_schema_required_fields() {
        let tools = tools();
        let se = tools.iter().find(|t| t.name == "send_email").unwrap();
        let req = se.input_schema["required"].as_array().unwrap();
        assert_eq!(req, &[json!("to"), json!("subject"), json!("body")]);
    }

    #[test]
    fn parseaddr_splits_name_and_address() {
        assert_eq!(parseaddr("Alice <a@x.com>"), ("Alice".into(), "a@x.com".into()));
        assert_eq!(parseaddr("b@y.com"), (String::new(), "b@y.com".into()));
        assert_eq!(parseaddr("Just A Name"), ("Just A Name".into(), String::new()));
    }

    #[test]
    fn imap_search_escape_doubles_backslash_and_quote() {
        assert_eq!(imap_search_escape("a\"b"), "a\\\"b");
        assert_eq!(imap_search_escape("a\\b"), "a\\\\b");
        assert_eq!(imap_search_escape("plain"), "plain");
    }

    #[test]
    fn reversed_capped_newest_first_and_capped() {
        // SEARCH returns ascending; reverse for newest-first then cap.
        assert_eq!(reversed_capped(vec![1, 2, 3, 4, 5], 3), vec![5, 4, 3]);
        assert_eq!(reversed_capped(vec![10, 2, 30], 10), vec![30, 10, 2]);
        assert!(reversed_capped(vec![], 5).is_empty());
    }

    #[test]
    fn clean_header_value_unfolds_crlf() {
        assert_eq!(clean_header_value("a\r\n b"), "a b");
        assert_eq!(clean_header_value("  hi  "), "hi");
        assert_eq!(clean_header_value("no folding"), "no folding");
    }

    #[test]
    fn encode_header_passes_ascii_and_encodes_unicode() {
        assert_eq!(encode_header("Hello World"), "Hello World");
        // A non-ASCII subject becomes a base64 encoded-word.
        let enc = encode_header("Café");
        assert!(enc.starts_with("=?utf-8?b?") && enc.ends_with("?="), "{enc}");
    }

    #[test]
    fn split_addrs_trims_and_drops_empties() {
        assert_eq!(split_addrs("a@x.com, b@y.com ,"), vec!["a@x.com", "b@y.com"]);
        assert!(split_addrs("  , ").is_empty());
    }

    #[test]
    fn py_list_repr_uses_single_quotes_like_python() {
        // `str(['a@x.com', 'b@y.com'])` -> single quotes, NOT Rust's `{:?}` doubles.
        assert_eq!(
            py_list_repr(&["a@x.com".to_string(), "b@y.com".to_string()]),
            "['a@x.com', 'b@y.com']"
        );
        assert_eq!(py_list_repr(&["solo@x.com".to_string()]), "['solo@x.com']");
        // `str([])` -> "[]".
        assert_eq!(py_list_repr(&[]), "[]");
    }

    #[test]
    fn getaddresses_respects_angle_brackets() {
        let got = getaddresses("Alice <a@x.com>, Bob <b@y.com>");
        assert_eq!(
            got,
            vec![
                ("Alice".to_string(), "a@x.com".to_string()),
                ("Bob".to_string(), "b@y.com".to_string())
            ]
        );
        assert!(getaddresses("").is_empty());
    }

    #[test]
    fn folder_role_from_name_classifies() {
        assert_eq!(folder_role_from_name("Trash"), "trash");
        assert_eq!(folder_role_from_name("[Gmail]/Bin"), "trash");
        assert_eq!(folder_role_from_name("Spam"), "junk");
        assert_eq!(folder_role_from_name("Archive"), "archive");
        assert_eq!(folder_role_from_name("All Mail"), "archive");
        assert_eq!(folder_role_from_name("INBOX"), "");
    }

    #[test]
    fn sequence_matcher_ratio_matches_difflib() {
        // difflib.SequenceMatcher(None, "work", "work").ratio() == 1.0.
        assert!((sequence_matcher_ratio("work", "work") - 1.0).abs() < 1e-9);
        // ("abcd", "abce").ratio() == 2*3/8 == 0.75.
        assert!((sequence_matcher_ratio("abcd", "abce") - 0.75).abs() < 1e-9);
        // Disjoint -> 0.0.
        assert!(sequence_matcher_ratio("abc", "xyz").abs() < 1e-9);
        // Empty/empty -> 1.0.
        assert!((sequence_matcher_ratio("", "") - 1.0).abs() < 1e-9);
    }

    #[test]
    fn get_close_matches_honors_cutoff_072() {
        let candidates = vec!["work gmail".to_string(), "personal".to_string()];
        // A near-exact match clears 0.72.
        let close = get_close_matches("work gmai", &candidates, 1, 0.72);
        assert_eq!(close, vec!["work gmail".to_string()]);
        // A weak match does not.
        let none = get_close_matches("zzzz", &candidates, 1, 0.72);
        assert!(none.is_empty());
    }

    #[test]
    fn list_search_criteria_orders_like_python() {
        // unread + unresponded -> combined UNSEEN UNANSWERED.
        assert_eq!(list_search_criteria(true, true), "(UNSEEN UNANSWERED)");
        // unread only -> UNSEEN.
        assert_eq!(list_search_criteria(true, false), "(UNSEEN)");
        // unresponded only (without unread) -> UNANSWERED (the previously-missing
        // branch; used to fall through to ALL and return answered mail too).
        assert_eq!(list_search_criteria(false, true), "(UNANSWERED)");
        // neither -> ALL (entire folder, read included).
        assert_eq!(list_search_criteria(false, false), "ALL");
    }

    /// Build a minimal `LoadedConfig` for projection tests, varying only the SMTP
    /// transport-security inputs.
    fn loaded_cfg_with_smtp(smtp_security: &str, smtp_port: i64) -> LoadedConfig {
        LoadedConfig {
            imap_host: String::new(),
            imap_port: 993,
            imap_user: "u".into(),
            imap_password: String::new(),
            imap_ssl: true,
            imap_starttls: false,
            smtp_host: "smtp.example.com".into(),
            smtp_port,
            smtp_security: smtp_security.into(),
            smtp_user: "u".into(),
            smtp_password: "p".into(),
            smtp_starttls: false,
            smtp_ssl: true,
            from_address: "u@example.com".into(),
            archive_folder: "Archive".into(),
            trash_folder: "Trash".into(),
            cache_db: String::new(),
            account_id: None,
            account_name: None,
        }
    }

    #[test]
    fn to_email_config_resolves_smtp_security() {
        // An explicit recognized value passes through unchanged.
        let cfg = loaded_cfg_with_smtp("starttls", 465).to_email_config();
        assert_eq!(cfg.smtp_security, "starttls");
        // An empty selector falls back to the port heuristic: 587 -> starttls.
        let cfg = loaded_cfg_with_smtp("", 587).to_email_config();
        assert_eq!(cfg.smtp_security, "starttls");
        // Empty + non-587 -> ssl.
        let cfg = loaded_cfg_with_smtp("", 465).to_email_config();
        assert_eq!(cfg.smtp_security, "ssl");
        // An unknown stored value also falls back to the port heuristic.
        let cfg = loaded_cfg_with_smtp("bogus", 25).to_email_config();
        assert_eq!(cfg.smtp_security, "ssl");
        // "none" (plaintext) is a recognized value and survives projection.
        let cfg = loaded_cfg_with_smtp("NONE", 25).to_email_config();
        assert_eq!(cfg.smtp_security, "none");
    }

    #[test]
    fn build_outgoing_has_standard_headers_and_b64_body() {
        let out = build_outgoing(
            "me@x.com",
            "you@y.com",
            "Hi",
            "Hello body",
            Some("cc@z.com"),
            Some("<orig@x>"),
            Some("<orig@x>"),
        );
        let s = String::from_utf8(out.bytes).unwrap();
        assert!(s.contains("From: me@x.com\r\n"));
        assert!(s.contains("To: you@y.com\r\n"));
        assert!(s.contains("Subject: Hi\r\n"));
        assert!(s.contains("Cc: cc@z.com\r\n"));
        assert!(s.contains("In-Reply-To: <orig@x>\r\n"));
        assert!(s.contains("References: <orig@x>\r\n"));
        assert!(s.contains("Content-Type: text/plain; charset=\"utf-8\"\r\n"));
        assert!(s.contains("Content-Transfer-Encoding: base64\r\n"));
        // A Message-ID header is present and bracketed.
        assert!(s.contains("Message-ID: <") && s.contains("@odysseus.local>\r\n"));
        // The body is base64 of "Hello body".
        let expect_b64 = base64_encode("Hello body".as_bytes());
        assert!(s.contains(&expect_b64));
    }

    #[test]
    fn unknown_tool_returns_unknown_text() {
        let texts = call_tool_sync("no_such_tool", json!({}));
        assert_eq!(texts, vec!["Unknown tool: no_such_tool".to_string()]);
    }

    #[test]
    fn send_email_requires_to_subject_body() {
        // Missing body -> the validation message (no SMTP attempt).
        let texts = call_tool_sync("send_email", json!({"to": "a@x.com", "subject": "s"}));
        assert_eq!(texts, vec!["Error: to, subject, and body are required".to_string()]);
    }

    #[test]
    fn download_attachment_requires_uid_and_index() {
        let texts = call_tool_sync("download_attachment", json!({"uid": "5"}));
        assert_eq!(texts, vec!["Error: uid and index are required".to_string()]);
    }

    #[test]
    fn bulk_email_no_selection_message() {
        let texts = call_tool_sync("bulk_email", json!({"action": "mark_read"}));
        assert_eq!(
            texts,
            vec!["No messages selected (pass uids or all_unread=true).".to_string()]
        );
    }

    #[test]
    fn bulk_email_unknown_action() {
        let texts = call_tool_sync(
            "bulk_email",
            json!({"action": "frobnicate", "uids": ["1", "2"]}),
        );
        assert_eq!(
            texts,
            vec![
                "Unknown bulk action: \"frobnicate\". Use mark_read/mark_unread/archive/delete/junk."
                    .to_string()
            ]
        );
    }

    #[test]
    fn list_email_accounts_returns_single_text_block() {
        // list_accounts_raw reads DATA_DIR/app.db; whether or not a DB exists in the
        // test process, the tool returns exactly one text block and never panics.
        let texts = call_tool_sync("list_email_accounts", json!({}));
        assert_eq!(texts.len(), 1);
    }
}
