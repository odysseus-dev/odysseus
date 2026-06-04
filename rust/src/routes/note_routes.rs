// routes/note_routes.rs  <- routes/note_routes.py
//! Google Keep-style notes / checklists API (routes WAVE 3).
//!
//! Faithful translation of `routes/note_routes.py`'s `setup_note_routes` factory:
//! CRUD over the `notes` table (raw `rusqlite`, mirroring the SQLAlchemy `Note`
//! ORM + `TimestampMixin`), plus pin/archive/checklist-item toggles, reorder, and
//! the module-level [`dispatch_reminder`] helper (browser / email / ntfy +
//! optional LLM synthesis) that backs the `POST /api/notes/fire-reminder` route.
//!
//! ## Shape (the integration substrate)
//! * `setup_note_routes() -> Router<AppState>` mirrors the Python factory. The
//!   Python `APIRouter(prefix="/api/notes", tags=["notes"])` means every handler
//!   carries the absolute `/api/notes...` path here. The Python factory's
//!   `task_scheduler=None` arg + `global _scheduler_ref` is replaced by reading
//!   `State<AppState>.task_scheduler` in the handler and threading it into
//!   [`dispatch_reminder`] (the explicit-state analogue of the module global).
//! * Auth is `get_current_user` for all CRUD routes — the load-bearing `None` case
//!   (auth disabled / single-user) is preserved by reading `Option<Extension<CurrentUser>>`.
//!   Ownership is enforced inline exactly as the Python: `user is None` -> owns everything,
//!   otherwise the row's `owner` must equal the user (a not-found / not-owned row -> 404).
//!   `fire_reminder` is the sole exception: it calls `auth_adapter::require_user` (which
//!   delegates to the ported `src::auth_helpers::require_user`), mirroring the Python's
//!   `require_user(request)` gate. In unconfigured / loopback mode `require_user` returns
//!   `""` rather than raising 401, so auth-disabled callers pass through correctly.
//! * `raise HTTPException(s, d)` -> `return Err(HttpException::new(s, d))`; the
//!   `web`-gated `IntoResponse for HttpException` renders FastAPI's `{"detail": d}`.
//! * `body: NoteCreate` / `body: NoteUpdate` (pydantic) -> local
//!   `#[derive(Deserialize)]` structs read from the JSON body. `fire_reminder` and
//!   `reorder_notes` read the **raw** untyped body (`await request.json()`), so they
//!   take `axum::body::Bytes` and parse by hand to preserve the same validation order.
//!
//! ## The SMTP send path (email cluster, wave-8 transport — now wired)
//! `dispatch_reminder`'s `channel == "email"` branch is fully ported: it resolves
//! the account config via [`crate::routes::email_helpers::get_email_config`] (plus
//! the bespoke owner-scoped SMTP-fallback walk over `email_accounts` ordered by
//! `is_default DESC, created_at ASC`), builds the `MIMEMultipart("alternative")`
//! single-`text/plain` reminder message exactly as `note_routes.py:328-350`, and
//! sends it through [`crate::routes::email_helpers::send_smtp_message`] (blocking
//! lettre, run under `spawn_blocking`). The "Missing ..." soft-fail (cfg lacks
//! smtp_host/user/password/from/recipient) sets `email_error` without sending —
//! byte-for-byte what Python returns when SMTP is unavailable. The browser / ntfy /
//! LLM-synthesis / dedupe-cache paths are unchanged.
//!
//! ## No path collision
//! Every route is under `/api/notes*`, a prefix the inline `web/mod.rs` subset
//! never touches, so the aggregator merges this router without an axum
//! duplicate-`method`+`path` panic.


use std::net::SocketAddr;
use std::sync::Arc;

use axum::extract::{ConnectInfo, Path, Query, State};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Extension, Json, Router};
use rusqlite::OptionalExtension;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::routes::{auth_adapter, AppState, CurrentUser, HttpException};
use crate::src::task_scheduler::TaskScheduler;

// ---------------------------------------------------------------------------
// Request models
// ---------------------------------------------------------------------------

/// `class NoteCreate(BaseModel)` — the JSON body for `POST /api/notes`.
///
/// `title` defaults to `""`, `note_type` to `"note"`, `source` to `"user"`,
/// `pinned` to `False`, `repeat` to `"none"`; everything else is `Optional`. serde
/// `#[serde(default)]` reproduces "absent -> default / None".
#[derive(Debug, Deserialize)]
struct NoteCreate {
    #[serde(default)]
    title: String,
    #[serde(default)]
    content: Option<String>,
    #[serde(default)]
    items: Option<Value>,
    #[serde(default = "default_note_type")]
    note_type: String,
    #[serde(default)]
    color: Option<String>,
    #[serde(default)]
    label: Option<String>,
    #[serde(default)]
    pinned: bool,
    #[serde(default)]
    due_date: Option<String>,
    #[serde(default = "default_source")]
    source: String,
    #[serde(default)]
    session_id: Option<String>,
    #[serde(default)]
    image_url: Option<String>,
    #[serde(default = "default_repeat")]
    repeat: Option<String>,
    #[serde(default)]
    sort_order: Option<i64>,
}

fn default_note_type() -> String {
    "note".to_string()
}
fn default_source() -> String {
    "user".to_string()
}
fn default_repeat() -> Option<String> {
    Some("none".to_string())
}

/// `class NoteUpdate(BaseModel)` — the JSON body for `PUT /api/notes/{id}`. Every
/// field is `Optional[...] = None`; only the present (`is not None`) ones are applied.
#[derive(Debug, Deserialize)]
struct NoteUpdate {
    #[serde(default)]
    title: Option<String>,
    #[serde(default)]
    content: Option<String>,
    #[serde(default)]
    items: Option<Value>,
    #[serde(default)]
    note_type: Option<String>,
    #[serde(default)]
    color: Option<String>,
    #[serde(default)]
    label: Option<String>,
    #[serde(default)]
    pinned: Option<bool>,
    #[serde(default)]
    archived: Option<bool>,
    #[serde(default)]
    due_date: Option<String>,
    #[serde(default)]
    image_url: Option<String>,
    #[serde(default)]
    repeat: Option<String>,
    #[serde(default)]
    sort_order: Option<i64>,
    #[serde(default)]
    agent_session_id: Option<String>,
}

// ---------------------------------------------------------------------------
// Row model
// ---------------------------------------------------------------------------

/// A `notes` row — every column the SQLAlchemy `Note` ORM (+ `TimestampMixin`)
/// loads, in the table column order. `_note_to_dict` reads all of these.
struct NoteRow {
    id: String,
    owner: Option<String>,
    title: Option<String>,
    content: Option<String>,
    items: Option<String>,
    note_type: Option<String>,
    color: Option<String>,
    label: Option<String>,
    pinned: bool,
    archived: bool,
    due_date: Option<String>,
    source: Option<String>,
    session_id: Option<String>,
    sort_order: Option<i64>,
    image_url: Option<String>,
    repeat: Option<String>,
    ai_classification: Option<String>,
    ai_content_hash: Option<String>,
    agent_session_id: Option<String>,
    created_at: Option<String>,
    updated_at: Option<String>,
}

/// The full SELECT column list (table column order). Used by every handler so a
/// loaded row maps cleanly into [`read_note_row`].
const NOTE_COLS: &str = "id, owner, title, content, items, note_type, color, label, \
     pinned, archived, due_date, source, session_id, sort_order, image_url, repeat, \
     ai_classification, ai_content_hash, agent_session_id, created_at, updated_at";

/// Read a `notes` row off a SELECT of [`NOTE_COLS`].
fn read_note_row(r: &rusqlite::Row<'_>) -> rusqlite::Result<NoteRow> {
    Ok(NoteRow {
        id: r.get(0)?,
        owner: r.get(1)?,
        title: r.get(2)?,
        content: r.get(3)?,
        items: r.get(4)?,
        note_type: r.get(5)?,
        color: r.get(6)?,
        label: r.get(7)?,
        // SQLite stores BOOLEAN as 0/1; a NULL coerces to false (Python default).
        pinned: r.get::<_, Option<bool>>(8)?.unwrap_or(false),
        archived: r.get::<_, Option<bool>>(9)?.unwrap_or(false),
        due_date: r.get(10)?,
        source: r.get(11)?,
        session_id: r.get(12)?,
        sort_order: r.get(13)?,
        image_url: r.get(14)?,
        repeat: r.get(15)?,
        ai_classification: r.get(16)?,
        ai_content_hash: r.get(17)?,
        agent_session_id: r.get(18)?,
        created_at: r.get(19)?,
        updated_at: r.get(20)?,
    })
}

/// `def _note_to_dict(note: Note) -> Dict[str, Any]`.
///
/// ```python
/// items = None
/// if note.items:
///     try: items = json.loads(note.items)
///     except (json.JSONDecodeError, TypeError): items = None
/// ai_cls = None
/// raw_ai = getattr(note, "ai_classification", None)
/// if raw_ai:
///     try: ai_cls = json.loads(raw_ai)
///     except (json.JSONDecodeError, TypeError): ai_cls = None
/// return { "id": ..., "owner": ..., ..., "created_at": ..., "updated_at": ... }
/// ```
///
/// `note.items` / `raw_ai` use Python truthiness — an empty string is falsy, so it
/// stays `None`; an unparseable string falls back to `None`. `sort_order or 0` and
/// `repeat or "none"` apply Python truthiness fallbacks. Timestamps are
/// `dt.isoformat()` (plain — no `Z` suffix), or `None`. Key order matches the Python
/// dict literal exactly (`serde_json` is built with `preserve_order`).
fn _note_to_dict(note: &NoteRow) -> Value {
    // `items = json.loads(note.items)` guarded by `if note.items:` + bare-ish except.
    let items: Value = match note.items.as_deref() {
        Some(s) if !s.is_empty() => serde_json::from_str(s).unwrap_or(Value::Null),
        _ => Value::Null,
    };
    // `ai_cls = json.loads(raw_ai)` guarded by `if raw_ai:` + bare-ish except.
    let ai_cls: Value = match note.ai_classification.as_deref() {
        Some(s) if !s.is_empty() => serde_json::from_str(s).unwrap_or(Value::Null),
        _ => Value::Null,
    };
    json!({
        "id": note.id,
        "owner": note.owner,
        "title": note.title,
        "content": note.content,
        "items": items,
        "note_type": note.note_type,
        "color": note.color,
        "label": note.label,
        "pinned": note.pinned,
        "archived": note.archived,
        "due_date": note.due_date,
        "source": note.source,
        "session_id": note.session_id,
        // `note.sort_order or 0` — Python truthiness: None/0 -> 0.
        "sort_order": note.sort_order.filter(|n| *n != 0).unwrap_or(0),
        "image_url": note.image_url,
        // `note.repeat or "none"` — Python truthiness: None/"" -> "none".
        "repeat": repeat_or_none(note.repeat.as_deref()),
        "ai_classification": ai_cls,
        "ai_content_hash": note.ai_content_hash,
        "agent_session_id": note.agent_session_id,
        // `note.created_at.isoformat() if note.created_at else None`.
        "created_at": iso_or_null(note.created_at.as_deref()),
        "updated_at": iso_or_null(note.updated_at.as_deref()),
    })
}

/// `note.repeat or "none"` — Python truthiness: None/"" -> "none".
fn repeat_or_none(repeat: Option<&str>) -> String {
    match repeat {
        Some(r) if !r.is_empty() => r.to_string(),
        _ => "none".to_string(),
    }
}

/// `dt.isoformat() if dt else None` — plain isoformat (T separator, no `Z`), or null.
fn iso_or_null(stored: Option<&str>) -> Value {
    match stored.filter(|s| !s.is_empty()) {
        Some(s) => json!(crate::pydatetime::to_isoformat(s)),
        None => Value::Null,
    }
}

// ---------------------------------------------------------------------------
// Reminder dispatch — module-level so background tasks (built-in actions) can
// call it directly without an HTTP roundtrip + auth cookie. The route version
// (`fire_reminder`) is a thin wrapper that pulls `owner` from the request.
// ---------------------------------------------------------------------------

/// `async def dispatch_reminder(title, note_body, note_id, owner="", queue_browser=True) -> dict`.
///
/// Fire a reminder via the configured channel (browser/email/ntfy). Returns
/// `{synthesis, email_sent, email_error, ntfy_sent, ntfy_error, browser_sent}`.
///
/// The Python module sets `_scheduler_ref` in `setup_note_routes()` (a module
/// global) so this helper can push a parallel in-app notification. In the Rust
/// port the scheduler is threaded explicitly as `scheduler` (`Option<&Arc<TaskScheduler>>`)
/// — the same fact (`_scheduler_ref is not None`), made an argument because Rust
/// has no mutable module global for an `Arc` handle. The route handler passes
/// `Some(&state.task_scheduler)`; a background caller passes whatever it holds.
///
/// The `channel == "email"` SMTP branch is wired to the wave-8 transport
/// (`get_email_config` / `send_smtp_message`): resolve the account config (with the
/// owner-scoped SMTP-fallback walk), build the reminder MIME message, and send it
/// under `spawn_blocking`. A config still lacking SMTP credentials falls through to
/// Python's own "Missing ..." soft-fail (`email_sent=False`). See the module docstring.
pub async fn dispatch_reminder(
    title: &str,
    note_body: &str,
    note_id: &str,
    owner: &str,
    queue_browser: bool,
    scheduler: Option<&Arc<TaskScheduler>>,
) -> Value {
    // `settings = load_settings()`
    let settings = crate::src::settings::load_settings();
    // `channel = settings.get("reminder_channel", "browser")`
    let channel = settings
        .get("reminder_channel")
        .and_then(Value::as_str)
        .unwrap_or("browser")
        .to_string();
    // `llm_on = bool(settings.get("reminder_llm_synthesis", False))`
    let llm_on = settings
        .get("reminder_llm_synthesis")
        .map(json_is_truthy)
        .unwrap_or(false);
    // `title = (title or "").strip(); note_body = (note_body or "").strip()`
    let title = title.trim().to_string();
    let note_body = note_body.trim().to_string();
    // `cache_key = str(note_id) if note_id else ""`
    let cache_key = if note_id.is_empty() {
        String::new()
    } else {
        note_id.to_string()
    };

    let mut cache: serde_json::Map<String, Value> = serde_json::Map::new();
    let mut cache_path: Option<std::path::PathBuf> = None;

    if !cache_key.is_empty() {
        // `try: ... except Exception as _e: logger.debug(...)` — read the per-owner
        // dedupe cache; if the most recent ping is within the reping window for the
        // active channel, skip.
        let path = ping_cache_path(owner);
        cache_path = Some(path.clone());
        if path.exists() {
            if let Ok(raw) = std::fs::read_to_string(&path) {
                if let Ok(Value::Object(m)) = serde_json::from_str::<Value>(&raw) {
                    cache = m;
                }
            }
        }
        // `last = cache.get(cache_key)`
        if let Some(last) = cache.get(&cache_key) {
            // Legacy cache values were plain timestamps; new ones are
            // `{"at": ..., "channel": ...}`.
            let (last_channel, last_at): (Option<String>, Option<String>) = match last {
                Value::Object(o) => (
                    o.get("channel").and_then(Value::as_str).map(str::to_string),
                    o.get("at").and_then(Value::as_str).map(str::to_string),
                ),
                Value::String(s) => (None, Some(s.clone())),
                _ => (None, None),
            };
            if let Some(last_at) = last_at {
                if let Some(last_dt) = parse_iso_utc(&last_at) {
                    // `should_skip = last_dt >= now - timedelta(minutes=25)`
                    let now = chrono::Utc::now();
                    let mut should_skip = last_dt >= now - chrono::Duration::minutes(25);
                    // Email/ntfy only dedupe against a same-channel send (legacy
                    // browser-written timestamps must not suppress an email retry).
                    if should_skip && (channel == "email" || channel == "ntfy") {
                        should_skip = last_channel.as_deref() == Some(channel.as_str());
                    }
                    if should_skip {
                        return json!({
                            "synthesis": Value::Null,
                            "email_sent": false,
                            "ntfy_sent": false,
                            "browser_sent": true,
                            "skipped": true,
                        });
                    }
                }
            }
        }
    }

    // ── LLM synthesis ──
    let mut synthesis: Option<String> = None;
    const SYNTH_FAILED_TAG: &str = "[utility model unavailable — no summary generated]";
    if llm_on {
        // `url, model, headers = resolve_endpoint("utility") or resolve_endpoint("default")`
        // `dispatch_reminder` is owner-less in Python (note_routes.py:182/184), so pass
        // `owner=None` -> the centralized resolver collapses `get_user_setting` to the
        // global `get_setting`, preserving today's exact behavior.
        let mut endpoint =
            crate::src::endpoint_resolver::resolve_endpoint_triple("utility", None);
        if endpoint.as_ref().map(|(u, _, _)| u.is_empty()).unwrap_or(true) {
            endpoint = crate::src::endpoint_resolver::resolve_endpoint_triple("default", None);
        }
        match endpoint {
            // `if url and model:`
            Some((url, model, headers)) if !url.is_empty() && !model.is_empty() => {
                let user_content = format!("Title: {title}\n\n{note_body}")
                    .trim()
                    .to_string();
                let messages = vec![
                    json!({
                        "role": "system",
                        "content": "You are a reminder assistant. Write a single short, warm, motivating sentence (max 25 words) reminding the user about the note below. Do not add greetings, preamble, or hashtags. Output only the sentence."
                    }),
                    json!({ "role": "user", "content": user_content }),
                ];
                let raw = crate::src::llm_core::llm_call_async(
                    &url, &model, messages, 0.7, 200, headers, 30,
                )
                .await;
                match raw {
                    Ok(raw) => {
                        // prose=True + prompt_echo=True (safe: one-sentence LLM output).
                        let mut s = crate::src::text_helpers::strip_think(&raw, true, true);
                        // strip-think's paragraph heuristic misses reasoning + answer on
                        // consecutive lines inside one paragraph; walk lines, drop
                        // reasoning / prompt-echo lines, keep the LAST surviving line.
                        if !s.is_empty() {
                            let lines: Vec<&str> =
                                s.lines().filter(|ln| !ln.trim().is_empty()).collect();
                            let cleaned: Vec<&str> = lines
                                .into_iter()
                                .filter(|ln| !is_reasoning_line(ln) && !is_echo_line(ln))
                                .collect();
                            if let Some(answer) = cleaned.last() {
                                s = answer.trim().to_string();
                            }
                        }
                        synthesis = Some(s);
                    }
                    Err(e) => {
                        crate::pylog::warning(&format!("Reminder LLM synthesis failed: {e}"));
                        synthesis = Some(SYNTH_FAILED_TAG.to_string());
                    }
                }
            }
            // `else: synthesis = _SYNTH_FAILED_TAG`
            _ => {
                synthesis = Some(SYNTH_FAILED_TAG.to_string());
            }
        }
        // Error-looking synthesis -> replace with the failed tag.
        if let Some(ref syn) = synthesis {
            let s = syn.trim();
            let low = s.to_lowercase();
            let looks_error = s.is_empty()
                || low.starts_with("error:")
                || low.starts_with("[error")
                || low.contains("operation failed")
                || (low.contains("upstream") && low.contains("failed"));
            if looks_error && syn != SYNTH_FAILED_TAG {
                crate::pylog::warning(&format!(
                    "Reminder synthesis looked like an error, replacing: {:?}",
                    truncate_chars(s, 120)
                ));
                synthesis = Some(SYNTH_FAILED_TAG.to_string());
            }
        }
    }

    // ── Email channel — ported (wave-8 transport: `eh::get_email_config` +
    // `eh::send_smtp_message`). ──
    let mut email_sent = false;
    let mut email_error = String::new();
    if channel == "email" {
        // Three outcomes, mirroring the Python control flow:
        //   * `Ok(true)`        -> the send succeeded (`email_sent = True`).
        //   * `Ok(false)`-with-`missing` -> the "Missing ..." path: `email_error`
        //     set + the "not sent" warning logged, but NO exception (so the
        //     `"send failed"` log must NOT fire). Returned as `Err(NotSent::Missing)`.
        //   * `Err(NotSent::Failed)` -> a real exception (Python's `except` clause):
        //     `email_error = str(e)` + the `"send failed"` warning.
        enum NotSent {
            Missing(String),
            Failed(String),
        }
        // `try: ... except Exception as e: email_error = str(e) or e.__class__.__name__`.
        // Mirror Python's single try/except over the whole block: any failure in the
        // config resolution / send sets `email_error`; `email_sent` stays false.
        let send_result: Result<bool, NotSent> = async {
            use crate::routes::email_helpers as eh;

            // `_acc_id = (settings.get("reminder_email_account_id") or "").strip() or None`
            let acc_id = settings
                .get("reminder_email_account_id")
                .and_then(Value::as_str)
                .unwrap_or("")
                .trim()
                .to_string();
            let acc_id: Option<&str> = if acc_id.is_empty() { None } else { Some(&acc_id) };

            // `cfg = _get_email_config(account_id=_acc_id, owner=owner or "")`
            let mut cfg = eh::get_email_config(acc_id, owner);

            // SMTP-fallback walk: if cfg lacks smtp_host/user/password, query enabled
            // `email_accounts` (owner OR (unowned AND imap_user/from_address == owner),
            // ORDER BY is_default DESC, created_at ASC) and pick the first
            // `get_email_config(row.id, owner)` that has smtp_host+user+password
            // (note_routes.py:280-299). The DB block is `try/except -> debug log`.
            if !(!cfg.smtp_host.is_empty()
                && !cfg.smtp_user.is_empty()
                && !cfg.smtp_password.is_empty())
            {
                // `db = SessionLocal(); q = db.query(EmailAccount).filter(enabled == True)`
                let trial_ids: Vec<String> = (|| -> Option<Vec<String>> {
                    let conn = crate::core::database::session_local().ok()?;
                    let (sql, has_owner) = if owner.is_empty() {
                        (
                            "SELECT id FROM email_accounts WHERE enabled = 1 \
                             ORDER BY is_default DESC, created_at ASC"
                                .to_string(),
                            false,
                        )
                    } else {
                        (
                            "SELECT id FROM email_accounts WHERE enabled = 1 \
                             AND (owner = ?1 OR ((owner IS NULL OR owner = '') \
                             AND (imap_user = ?1 OR from_address = ?1))) \
                             ORDER BY is_default DESC, created_at ASC"
                                .to_string(),
                            true,
                        )
                    };
                    let mut stmt = conn.prepare(&sql).ok()?;
                    let map = |r: &rusqlite::Row<'_>| r.get::<_, String>(0);
                    let rows = if has_owner {
                        stmt.query_map(rusqlite::params![owner], map).ok()?
                    } else {
                        stmt.query_map([], map).ok()?
                    };
                    Some(rows.filter_map(Result::ok).collect())
                })()
                .unwrap_or_else(|| {
                    crate::pylog::debug("Reminder SMTP fallback lookup failed");
                    Vec::new()
                });
                // `for row in ...: trial = _get_email_config(row.id, owner); if ok: cfg = trial; break`
                for id in &trial_ids {
                    let trial = eh::get_email_config(Some(id), owner);
                    if !trial.smtp_host.is_empty()
                        && !trial.smtp_user.is_empty()
                        && !trial.smtp_password.is_empty()
                    {
                        cfg = trial;
                        break;
                    }
                }
            }

            // `from_addr = (cfg.get("from_address") or cfg.get("smtp_user") or "").strip()`
            let from_addr = if !cfg.from_address.is_empty() {
                cfg.from_address.clone()
            } else {
                cfg.smtp_user.clone()
            };
            let from_addr = from_addr.trim().to_string();
            // `recipient = (settings.get("reminder_email_to") or "").strip() or from_addr`
            let recipient = {
                let r = settings
                    .get("reminder_email_to")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .trim()
                    .to_string();
                if r.is_empty() {
                    from_addr.clone()
                } else {
                    r
                }
            };

            // Loud diagnostic so we can see WHY a reminder didn't send.
            crate::pylog::info(&format!(
                "dispatch_reminder[email] note_id={note_id} owner={owner:?} \
                 smtp_host={:?} smtp_user={:?} from={from_addr:?} recipient={recipient:?} \
                 account_name={:?}",
                cfg.smtp_host, cfg.smtp_user, cfg.account_name
            ));

            // `missing = [...]` from the REAL cfg.
            let mut missing: Vec<&str> = Vec::new();
            if cfg.smtp_host.is_empty() {
                missing.push("SMTP host");
            }
            if cfg.smtp_user.is_empty() {
                missing.push("SMTP user");
            }
            if cfg.smtp_password.is_empty() {
                missing.push("SMTP password");
            }
            if from_addr.is_empty() {
                missing.push("from address");
            }
            if recipient.is_empty() {
                missing.push("recipient");
            }
            if !missing.is_empty() {
                // `email_error = "Missing " + ", ".join(missing)` + warning; email_sent stays false.
                let err = format!("Missing {}", missing.join(", "));
                crate::pylog::warning(&format!(
                    "Reminder email not sent for note_id={note_id} account={:?}: {err}",
                    cfg.account_name
                ));
                return Err(NotSent::Missing(err));
            }

            // Build the MIMEMultipart("alternative")-with-single-text/plain message
            // (note_routes.py:328-350). NO HTML, NO attachments — so we do NOT reuse
            // `build_mime` (which always emits plain+HTML). Construct a minimal
            // multipart/alternative envelope carrying one text/plain part.
            // `_t = (title or 'Note'); strip a leading "Reminder:"`.
            let mut t = if title.is_empty() {
                "Note".to_string()
            } else {
                title.clone()
            };
            if t.to_lowercase().starts_with("reminder:") {
                t = t["Reminder:".len()..].trim().to_string();
            }
            let subject = format!("Reminder (Odysseus): {t}");
            // `Date = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")`.
            let date = crate::pydatetime::utcnow_naive()
                .format("%a, %d %b %Y %H:%M:%S +0000")
                .to_string();
            // Body: "\n\n".join([synthesis?, _t?, note_body?]) else title.
            let mut body_chunks: Vec<&str> = Vec::new();
            if let Some(ref syn) = synthesis {
                if !syn.is_empty() {
                    body_chunks.push(syn);
                }
            }
            if !t.is_empty() {
                body_chunks.push(&t);
            }
            if !note_body.is_empty() {
                body_chunks.push(&note_body);
            }
            let plain = if body_chunks.is_empty() {
                title.clone()
            } else {
                body_chunks.join("\n\n")
            };

            // Minimal multipart/alternative with a single text/plain part. The
            // boundary token only needs to be unique (matching Python's stdlib MIME).
            let boundary = format!(
                "=_ody_reminder_{:032x}_=",
                rand::random::<u128>()
            );
            let mut msg = String::new();
            msg.push_str(&format!("From: {from_addr}\r\n"));
            msg.push_str(&format!("To: {recipient}\r\n"));
            msg.push_str(&format!("Subject: {subject}\r\n"));
            msg.push_str(&format!("Date: {date}\r\n"));
            msg.push_str("X-Odysseus-Origin: odysseus-ui\r\n");
            msg.push_str("X-Odysseus-Kind: reminder\r\n");
            msg.push_str(&format!("X-Odysseus-Ref: {note_id}\r\n"));
            msg.push_str("MIME-Version: 1.0\r\n");
            msg.push_str(&format!(
                "Content-Type: multipart/alternative; boundary=\"{boundary}\"\r\n\r\n"
            ));
            msg.push_str(&format!("--{boundary}\r\n"));
            msg.push_str(
                "Content-Type: text/plain; charset=\"utf-8\"\r\n\
                 Content-Transfer-Encoding: 8bit\r\n\r\n",
            );
            msg.push_str(&plain);
            msg.push_str(&format!("\r\n\r\n--{boundary}--\r\n"));

            // `await asyncio.to_thread(_send_smtp_message(cfg, from, [recipient], msg))`.
            // BLOCKING (lettre) -> spawn_blocking. The Python `_send_smtp_message`
            // uses the default timeout; mirror the other reminder-style callers (30s).
            let cfg_owned = cfg;
            let from_owned = from_addr.clone();
            let recip_owned = vec![recipient.clone()];
            let bytes = msg.into_bytes();
            tokio::task::spawn_blocking(move || {
                eh::send_smtp_message(&cfg_owned, &from_owned, &recip_owned, &bytes, 30)
            })
            .await
            .map_err(|e| NotSent::Failed(format!("{e}")))?
            .map_err(NotSent::Failed)?;
            Ok(true)
        }
        .await;

        match send_result {
            // `email_sent = True`
            Ok(_) => email_sent = true,
            // The "Missing ..." path: error already logged above (NO "send failed").
            Err(NotSent::Missing(err)) => {
                email_error = err;
            }
            // Python's `except`: `email_error = str(e)` + the "send failed" warning.
            Err(NotSent::Failed(err)) => {
                let err = if err.is_empty() {
                    "RuntimeError".to_string()
                } else {
                    err
                };
                crate::pylog::warning(&format!("Reminder email send failed: {err}"));
                email_error = err;
            }
        }
    }

    // ── ntfy channel ──
    let mut ntfy_sent = false;
    let mut ntfy_error = String::new();
    if channel == "ntfy" {
        // `intg = next((i for i in load_integrations() if i.preset=="ntfy" and i.enabled and i.base_url), None)`
        let integrations = crate::src::integrations::load_integrations();
        let intg = integrations.iter().find(|i| {
            i.get("preset").and_then(Value::as_str) == Some("ntfy")
                && i.get("enabled").map(json_is_truthy).unwrap_or(true)
                && i.get("base_url")
                    .and_then(Value::as_str)
                    .map(|b| !b.is_empty())
                    .unwrap_or(false)
        });
        match intg {
            Some(intg) => {
                // `base = intg["base_url"].rstrip("/")`
                let base = intg
                    .get("base_url")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .trim_end_matches('/')
                    .to_string();
                // `topic = settings.get("reminder_ntfy_topic") or "reminders"`
                let topic = settings
                    .get("reminder_ntfy_topic")
                    .and_then(Value::as_str)
                    .filter(|s| !s.is_empty())
                    .unwrap_or("reminders")
                    .to_string();
                // `ntfy_body = synthesis or note_body or title`
                let ntfy_body = first_truthy_str(&[
                    synthesis.as_deref(),
                    Some(&note_body),
                    Some(&title),
                ]);
                // `hdrs = {"Title": title or "Reminder", "Priority": "high", "Tags": "bell"}`
                let title_hdr = if title.is_empty() {
                    "Reminder".to_string()
                } else {
                    title.clone()
                };
                let api_key = intg
                    .get("api_key")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string();
                match ntfy_post(&base, &topic, &ntfy_body, &title_hdr, &api_key).await {
                    Ok(status) => {
                        // `ntfy_sent = resp.is_success`
                        ntfy_sent = (200..300).contains(&status);
                        if !ntfy_sent {
                            ntfy_error = format!("ntfy returned HTTP {status}");
                        }
                    }
                    Err(e) => {
                        ntfy_error = py_exc_str(&e.to_string(), "RequestError");
                        crate::pylog::warning(&format!("Reminder ntfy send failed: {e}"));
                    }
                }
            }
            // `else: ntfy_error = "No enabled ntfy integration"`
            None => {
                ntfy_error = "No enabled ntfy integration".to_string();
            }
        }
    }

    // ── In-app browser notification ALWAYS fires (regardless of channel). ──
    let mut browser_sent = false;
    // `local_browser_sent = (not queue_browser and channel == "browser")`
    let local_browser_sent = !queue_browser && channel == "browser";
    // `if queue_browser and _scheduler_ref is not None:`
    if queue_browser {
        if let Some(sched) = scheduler {
            // `_scheduler_ref.add_notification(task_name=title or "Reminder", status="success",
            //   task_id=f"reminder-{note_id}", owner=owner or None,
            //   body=(synthesis or note_body or title or "").strip()[:500] or "Reminder")`
            let task_name = if title.is_empty() {
                "Reminder".to_string()
            } else {
                title.clone()
            };
            let task_id = format!("reminder-{note_id}");
            let owner_opt = if owner.is_empty() { None } else { Some(owner) };
            // body[:500] or "Reminder" — char-based slice, then truthiness fallback.
            let body_raw = first_truthy_str(&[
                synthesis.as_deref(),
                Some(&note_body),
                Some(&title),
            ]);
            let body_clipped: String = truncate_chars(body_raw.trim(), 500);
            let body = if body_clipped.is_empty() {
                "Reminder".to_string()
            } else {
                body_clipped
            };
            sched.add_notification(
                &task_name,
                "success",
                Some(&task_id),
                owner_opt,
                Some(&body),
            );
            browser_sent = true;
        }
    }

    // ── Dedupe cache write — same file `action_ping_notes` reads. ──
    // `if (email_sent or ntfy_sent or browser_sent or local_browser_sent) and note_id:`
    if (email_sent || ntfy_sent || browser_sent || local_browser_sent) && !note_id.is_empty() {
        // `_STATE = cache_path or data/note_pings_{slug}.json`
        let state_path = cache_path.unwrap_or_else(|| ping_cache_path(owner));
        // `_STATE.parent.mkdir(parents=True, exist_ok=True)`
        if let Some(parent) = state_path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        // `_cache = cache or (json.loads(...) if exists else {})` — `cache` is the dict
        // read at the top; truthy (non-empty) wins, else re-read, else {}.
        let mut out_cache = if !cache.is_empty() {
            cache
        } else if state_path.exists() {
            std::fs::read_to_string(&state_path)
                .ok()
                .and_then(|raw| serde_json::from_str::<Value>(&raw).ok())
                .and_then(|v| match v {
                    Value::Object(m) => Some(m),
                    _ => None,
                })
                .unwrap_or_default()
        } else {
            serde_json::Map::new()
        };
        // `sent_channel = "email" if email_sent else "ntfy" if ntfy_sent else "browser"`
        let sent_channel = if email_sent {
            "email"
        } else if ntfy_sent {
            "ntfy"
        } else {
            "browser"
        };
        // `_cache[cache_key or str(note_id)] = {"at": now, "channel": sent_channel}`
        let key = if cache_key.is_empty() {
            note_id.to_string()
        } else {
            cache_key.clone()
        };
        out_cache.insert(
            key,
            json!({
                "at": format!("{}Z", crate::pydatetime::to_isoformat(&crate::pydatetime::utcnow_naive_iso())),
                "channel": sent_channel,
            }),
        );
        // `_STATE.write_text(json.dumps(_cache))` — compact (no indent), best-effort.
        if let Ok(serialized) = serde_json::to_string(&Value::Object(out_cache)) {
            let _ = std::fs::write(&state_path, serialized);
        }
    }

    json!({
        "synthesis": synthesis,
        "email_sent": email_sent,
        "email_error": email_error,
        "ntfy_sent": ntfy_sent,
        "ntfy_error": ntfy_error,
        "browser_sent": browser_sent || local_browser_sent,
    })
}

// ---------------------------------------------------------------------------
// Router factory
// ---------------------------------------------------------------------------

/// `def setup_note_routes(task_scheduler=None)` — assemble the notes router.
///
/// The Python `APIRouter(prefix="/api/notes")` is flattened to absolute paths here.
/// The `global _scheduler_ref = task_scheduler` line is replaced by reading
/// `State<AppState>.task_scheduler` inside `fire_reminder`. Routes are registered in
/// the Python source order: list/create (`""`), get/update/delete (`/{note_id}`),
/// pin, archive, item-toggle, fire-reminder, reorder. axum 0.7 path captures use the
/// colon form (`/:note_id`), never braces.
pub fn setup_note_routes() -> Router<AppState> {
    Router::new()
        // --- LIST / CREATE ---
        .route("/api/notes", get(list_notes).post(create_note))
        // --- FIRE REMINDER (static path; must precede the `/:note_id` capture) ---
        .route("/api/notes/fire-reminder", post(fire_reminder))
        // --- REORDER (static path; must precede the `/:note_id` capture) ---
        .route("/api/notes/reorder", post(reorder_notes))
        // --- GET ONE / UPDATE / DELETE ---
        .route(
            "/api/notes/:note_id",
            get(get_note).put(update_note).delete(delete_note),
        )
        // --- TOGGLE PIN ---
        .route("/api/notes/:note_id/pin", post(toggle_pin))
        // --- TOGGLE ARCHIVE ---
        .route("/api/notes/:note_id/archive", post(toggle_archive))
        // --- TOGGLE CHECKLIST ITEM ---
        .route("/api/notes/:note_id/items/:index/toggle", post(toggle_item))
}

/// `archived` / `label` query params for `GET /api/notes`.
#[derive(Debug, Deserialize)]
struct ListQuery {
    #[serde(default)]
    archived: Option<bool>,
    #[serde(default)]
    label: Option<String>,
}

/// `GET /api/notes` — list notes.
///
/// ```python
/// q = db.query(Note)
/// if user is not None: q = q.filter(Note.owner == user)
/// if archived is not None: q = q.filter(Note.archived == archived)
/// else: q = q.filter(Note.archived == False)
/// if label: q = q.filter(Note.label == label)
/// if archived is True: notes = q.order_by(Note.updated_at.desc()).all()
/// else: notes = q.order_by(Note.pinned.desc(), Note.sort_order.asc(), Note.updated_at.desc()).all()
/// return {"notes": [_note_to_dict(n) for n in notes]}
/// ```
async fn list_notes(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Query(q): Query<ListQuery>,
) -> Result<Response, HttpException> {
    let user = current_user(&user);
    let conn = session_local()?;

    // Build the WHERE clause + bound params in the Python filter order.
    let mut wheres: Vec<String> = Vec::new();
    let mut params: Vec<Box<dyn rusqlite::ToSql>> = Vec::new();
    if let Some(u) = user.as_deref() {
        wheres.push(format!("owner = ?{}", params.len() + 1));
        params.push(Box::new(u.to_string()));
    }
    match q.archived {
        Some(a) => {
            wheres.push(format!("archived = ?{}", params.len() + 1));
            params.push(Box::new(a));
        }
        None => {
            wheres.push("archived = 0".to_string());
        }
    }
    // `if label:` — Python truthiness (an empty string is falsy and skips the filter).
    if let Some(label) = q.label.as_deref().filter(|s| !s.is_empty()) {
        wheres.push(format!("label = ?{}", params.len() + 1));
        params.push(Box::new(label.to_string()));
    }

    let order = if q.archived == Some(true) {
        "ORDER BY updated_at DESC"
    } else {
        "ORDER BY pinned DESC, sort_order ASC, updated_at DESC"
    };
    let where_sql = if wheres.is_empty() {
        String::new()
    } else {
        format!("WHERE {}", wheres.join(" AND "))
    };
    let sql = format!("SELECT {NOTE_COLS} FROM notes {where_sql} {order}");

    let mut stmt = conn.prepare(&sql).map_err(db_500)?;
    let param_refs: Vec<&dyn rusqlite::ToSql> = params.iter().map(|p| p.as_ref()).collect();
    let rows: Vec<Value> = stmt
        .query_map(param_refs.as_slice(), |r| Ok(_note_to_dict(&read_note_row(r)?)))
        .map_err(db_500)?
        .collect::<rusqlite::Result<Vec<_>>>()
        .map_err(db_500)?;

    Ok(Json(json!({ "notes": rows })).into_response())
}

/// `POST /api/notes` — create a note.
///
/// ```python
/// note = Note(id=str(uuid.uuid4()), owner=user, title=body.title, content=body.content,
///     items=json.dumps(body.items) if body.items is not None else None,
///     note_type=body.note_type, color=body.color, label=body.label, pinned=body.pinned,
///     due_date=body.due_date, source=body.source, session_id=body.session_id,
///     image_url=body.image_url, repeat=body.repeat or "none",
///     sort_order=body.sort_order if body.sort_order is not None else 0)
/// db.add(note); db.commit(); db.refresh(note); return _note_to_dict(note)
/// ```
async fn create_note(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Json(body): Json<NoteCreate>,
) -> Result<Response, HttpException> {
    let user = current_user(&user);
    let conn = session_local()?;

    let id = uuid::Uuid::new_v4().to_string();
    // `items=json.dumps(body.items) if body.items is not None else None`.
    let items: Option<String> = body
        .items
        .as_ref()
        .map(|v| serde_json::to_string(v).unwrap_or_else(|_| "null".to_string()));
    // `repeat=body.repeat or "none"` — Python truthiness.
    let repeat = repeat_or_none(body.repeat.as_deref());
    // `sort_order=body.sort_order if body.sort_order is not None else 0`.
    let sort_order = body.sort_order.unwrap_or(0);
    // TimestampMixin: created_at/updated_at default to utcnow at flush.
    let now = crate::pydatetime::utcnow_naive_iso();

    conn.execute(
        "INSERT INTO notes \
           (id, owner, title, content, items, note_type, color, label, pinned, archived, \
            due_date, source, session_id, sort_order, image_url, repeat, created_at, updated_at) \
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, 0, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?16)",
        rusqlite::params![
            id,
            user,
            body.title,
            body.content,
            items,
            body.note_type,
            body.color,
            body.label,
            body.pinned,
            body.due_date,
            body.source,
            body.session_id,
            sort_order,
            body.image_url,
            repeat,
            now,
        ],
    )
    .map_err(db_500)?;

    // `db.refresh(note); return _note_to_dict(note)` — the refreshed row equals the
    // values we just inserted (pinned default False, archived default False).
    let note = NoteRow {
        id,
        owner: user,
        title: Some(body.title),
        content: body.content,
        items,
        note_type: Some(body.note_type),
        color: body.color,
        label: body.label,
        pinned: body.pinned,
        archived: false,
        due_date: body.due_date,
        source: Some(body.source),
        session_id: body.session_id,
        sort_order: Some(sort_order),
        image_url: body.image_url,
        repeat: Some(repeat),
        ai_classification: None,
        ai_content_hash: None,
        agent_session_id: None,
        created_at: Some(now.clone()),
        updated_at: Some(now),
    };
    Ok(Json(_note_to_dict(&note)).into_response())
}

/// `GET /api/notes/{note_id}` — fetch one note.
///
/// ```python
/// note = db.query(Note).filter(Note.id == note_id).first()
/// if not note: raise HTTPException(404, "Note not found")
/// if user is not None and note.owner != user: raise HTTPException(404, "Note not found")
/// return _note_to_dict(note)
/// ```
async fn get_note(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(note_id): Path<String>,
) -> Result<Response, HttpException> {
    let user = current_user(&user);
    let conn = session_local()?;
    let note = load_note(&conn, &note_id)?;
    let note = require_owned(note, user.as_deref())?;
    Ok(Json(_note_to_dict(&note)).into_response())
}

/// `PUT /api/notes/{note_id}` — partial update.
///
/// ```python
/// note = ...first(); if not note or (user is not None and note.owner != user): 404
/// if body.title is not None: note.title = body.title
/// ... (each present field applied; items also flag_modified) ...
/// db.commit(); db.refresh(note); return _note_to_dict(note)
/// ```
async fn update_note(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(note_id): Path<String>,
    Json(body): Json<NoteUpdate>,
) -> Result<Response, HttpException> {
    let user = current_user(&user);
    let conn = session_local()?;
    let note = load_note(&conn, &note_id)?;
    let mut note = require_owned(note, user.as_deref())?;

    // Apply each present field in the Python source order, mutating the in-memory row.
    //
    // `dirty` tracks whether `db.commit()` would see a NET column change. SQLAlchemy's
    // `onupdate=utcnow` (TimestampMixin) fires at flush ONLY for an instance with a
    // genuinely changed attribute — assigning a column its current value is a no-op the
    // unit-of-work coalesces away, so `updated_at` is NOT bumped. We mirror that: for
    // each scalar field, mark dirty ONLY when the present new value DIFFERS from the
    // value already loaded on the row. (The prior port marked dirty on mere PRESENCE,
    // which over-bumped `updated_at` for a body that re-set fields to their current
    // values.) An empty body — OR a body that only re-sets fields to their current
    // values — is therefore a pure no-op: no UPDATE, `updated_at` unchanged.
    //
    // `items` is the lone exception: Python calls `flag_modified(note, "items")`, which
    // FORCES the attribute dirty even when the serialized JSON is byte-identical, so a
    // present `items` always counts as a change (unconditional dirty).
    let mut dirty = false;
    if let Some(title) = body.title {
        let new = Some(title);
        if note.title != new {
            dirty = true;
        }
        note.title = new;
    }
    if let Some(content) = body.content {
        let new = Some(content);
        if note.content != new {
            dirty = true;
        }
        note.content = new;
    }
    if let Some(items) = body.items {
        // `note.items = json.dumps(body.items); flag_modified(note, "items")` — the
        // explicit `flag_modified` forces dirty unconditionally (even for identical
        // JSON), so a present `items` is always a change.
        note.items = Some(serde_json::to_string(&items).unwrap_or_else(|_| "null".to_string()));
        dirty = true;
    }
    if let Some(note_type) = body.note_type {
        let new = Some(note_type);
        if note.note_type != new {
            dirty = true;
        }
        note.note_type = new;
    }
    if let Some(color) = body.color {
        let new = Some(color);
        if note.color != new {
            dirty = true;
        }
        note.color = new;
    }
    if let Some(label) = body.label {
        let new = Some(label);
        if note.label != new {
            dirty = true;
        }
        note.label = new;
    }
    if let Some(pinned) = body.pinned {
        if note.pinned != pinned {
            dirty = true;
        }
        note.pinned = pinned;
    }
    if let Some(archived) = body.archived {
        if note.archived != archived {
            dirty = true;
        }
        note.archived = archived;
    }
    if let Some(due_date) = body.due_date {
        let new = Some(due_date);
        if note.due_date != new {
            dirty = true;
        }
        note.due_date = new;
    }
    if let Some(image_url) = body.image_url {
        let new = Some(image_url);
        if note.image_url != new {
            dirty = true;
        }
        note.image_url = new;
    }
    if let Some(repeat) = body.repeat {
        let new = Some(repeat);
        if note.repeat != new {
            dirty = true;
        }
        note.repeat = new;
    }
    if let Some(sort_order) = body.sort_order {
        let new = Some(sort_order);
        if note.sort_order != new {
            dirty = true;
        }
        note.sort_order = new;
    }
    if let Some(agent_session_id) = body.agent_session_id {
        let new = Some(agent_session_id);
        if note.agent_session_id != new {
            dirty = true;
        }
        note.agent_session_id = new;
    }

    // Only when at least one field was an actual NET change does Python's `db.commit()`
    // see a dirty Note and fire the `onupdate=utcnow` trigger. An empty body — or a body
    // that only re-sets fields to their current values — is a pure no-op: no UPDATE,
    // `updated_at` unchanged, and `db.refresh(note)` reloads the same row we already
    // hold — so we just return `_note_to_dict(note)` as-is below.
    if dirty {
        // `db.commit()` bumps updated_at (onupdate=utcnow); `db.refresh(note)` reloads it.
        let now = crate::pydatetime::utcnow_naive_iso();
        note.updated_at = Some(now.clone());

        conn.execute(
            "UPDATE notes SET \
               title = ?1, content = ?2, items = ?3, note_type = ?4, color = ?5, label = ?6, \
               pinned = ?7, archived = ?8, due_date = ?9, image_url = ?10, repeat = ?11, \
               sort_order = ?12, agent_session_id = ?13, updated_at = ?14 \
             WHERE id = ?15",
            rusqlite::params![
                note.title,
                note.content,
                note.items,
                note.note_type,
                note.color,
                note.label,
                note.pinned,
                note.archived,
                note.due_date,
                note.image_url,
                note.repeat,
                note.sort_order,
                note.agent_session_id,
                now,
                note_id,
            ],
        )
        .map_err(db_500)?;
    }

    Ok(Json(_note_to_dict(&note)).into_response())
}

/// `DELETE /api/notes/{note_id}` — delete a note.
///
/// ```python
/// note = ...first(); if not note or (user is not None and note.owner != user): 404
/// db.delete(note); db.commit(); return {"ok": True}
/// ```
async fn delete_note(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(note_id): Path<String>,
) -> Result<Response, HttpException> {
    let user = current_user(&user);
    let conn = session_local()?;
    let note = load_note(&conn, &note_id)?;
    let note = require_owned(note, user.as_deref())?;
    conn.execute("DELETE FROM notes WHERE id = ?1", rusqlite::params![note.id])
        .map_err(db_500)?;
    Ok(Json(json!({ "ok": true })).into_response())
}

/// `POST /api/notes/{note_id}/pin` — toggle pin.
///
/// ```python
/// note = ...first(); if not note or (user is not None and note.owner != user): 404
/// note.pinned = not note.pinned; db.commit(); return {"ok": True, "pinned": note.pinned}
/// ```
async fn toggle_pin(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(note_id): Path<String>,
) -> Result<Response, HttpException> {
    let user = current_user(&user);
    let conn = session_local()?;
    let note = load_note(&conn, &note_id)?;
    let note = require_owned(note, user.as_deref())?;
    let pinned = !note.pinned;
    conn.execute(
        "UPDATE notes SET pinned = ?1 WHERE id = ?2",
        rusqlite::params![pinned, note.id],
    )
    .map_err(db_500)?;
    Ok(Json(json!({ "ok": true, "pinned": pinned })).into_response())
}

/// `POST /api/notes/{note_id}/archive` — toggle archive.
///
/// ```python
/// note = ...first(); if not note or (user is not None and note.owner != user): 404
/// note.archived = not note.archived; db.commit(); return {"ok": True, "archived": note.archived}
/// ```
async fn toggle_archive(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(note_id): Path<String>,
) -> Result<Response, HttpException> {
    let user = current_user(&user);
    let conn = session_local()?;
    let note = load_note(&conn, &note_id)?;
    let note = require_owned(note, user.as_deref())?;
    let archived = !note.archived;
    conn.execute(
        "UPDATE notes SET archived = ?1 WHERE id = ?2",
        rusqlite::params![archived, note.id],
    )
    .map_err(db_500)?;
    Ok(Json(json!({ "ok": true, "archived": archived })).into_response())
}

/// `POST /api/notes/{note_id}/items/{index}/toggle` — toggle a checklist item.
///
/// ```python
/// note = ...first(); if not note or (user is not None and note.owner != user): 404
/// if not note.items: raise HTTPException(400, "Note has no checklist items")
/// items = json.loads(note.items)
/// if index < 0 or index >= len(items): raise HTTPException(400, f"Item index {index} out of range")
/// items[index]["done"] = not items[index].get("done", False)
/// note.items = json.dumps(items); flag_modified(note, "items"); db.commit()
/// return {"ok": True, "items": items}
/// ```
async fn toggle_item(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path((note_id, index)): Path<(String, i64)>,
) -> Result<Response, HttpException> {
    let user = current_user(&user);
    let conn = session_local()?;
    let note = load_note(&conn, &note_id)?;
    let note = require_owned(note, user.as_deref())?;

    // `if not note.items:` — Python truthiness: NULL/"" -> 400.
    let items_raw = match note.items.as_deref().filter(|s| !s.is_empty()) {
        Some(s) => s,
        None => return Err(HttpException::new(400, "Note has no checklist items")),
    };
    // `items = json.loads(note.items)` — an unparseable payload would raise inside the
    // handler (FastAPI surfaces a 500); reproduce that with a 500 here.
    let mut items: Vec<Value> = match serde_json::from_str::<Value>(items_raw) {
        Ok(Value::Array(a)) => a,
        // A non-array `json.loads` result makes `len(items)` / indexing raise; treat as 500.
        Ok(_) => return Err(HttpException::new(500, "Internal Server Error")),
        Err(_) => return Err(HttpException::new(500, "Internal Server Error")),
    };

    // `if index < 0 or index >= len(items): raise HTTPException(400, ...)`.
    if index < 0 || index as usize >= items.len() {
        return Err(HttpException::new(
            400,
            format!("Item index {index} out of range"),
        ));
    }

    // `items[index]["done"] = not items[index].get("done", False)`.
    let idx = index as usize;
    let cur_done = items[idx]
        .get("done")
        .map(json_is_truthy)
        .unwrap_or(false);
    if let Value::Object(ref mut obj) = items[idx] {
        obj.insert("done".to_string(), Value::Bool(!cur_done));
    } else {
        // A non-object item makes `items[index]["done"] = ...` raise (TypeError) in
        // Python -> the handler's outer 500.
        return Err(HttpException::new(500, "Internal Server Error"));
    }

    // `note.items = json.dumps(items); db.commit()`.
    let dumped = serde_json::to_string(&items).unwrap_or_else(|_| "[]".to_string());
    let now = crate::pydatetime::utcnow_naive_iso();
    conn.execute(
        "UPDATE notes SET items = ?1, updated_at = ?2 WHERE id = ?3",
        rusqlite::params![dumped, now, note.id],
    )
    .map_err(db_500)?;

    Ok(Json(json!({ "ok": true, "items": items })).into_response())
}

/// `POST /api/notes/fire-reminder` — dispatch a reminder per user settings.
///
/// ```python
/// # Gate against anonymous callers — LLM synthesis can burn tokens.
/// from src.auth_helpers import require_user as _ru
/// _ru(request)
/// body = await request.json()
/// note_id = body.get("note_id")
/// title = (body.get("title") or "").strip()
/// note_body = (body.get("body") or "").strip()
/// if not note_id: raise HTTPException(400, "note_id required")
/// return await dispatch_reminder(title=title, note_body=note_body, note_id=note_id,
///     owner=_owner(request) or "", queue_browser=False)
/// ```
///
/// Auth gate uses `auth_adapter::require_user` (matching the rest of the codebase), NOT
/// the manual `None`-check the original port used. This means auth-disabled / loopback
/// callers pass through (owner = `""`) rather than getting a spurious 401. The body is
/// the raw `await request.json()` (untyped), so it is read from `Bytes`.
async fn fire_reminder(
    State(state): State<AppState>,
    ci: Option<ConnectInfo<SocketAddr>>,
    user: Option<Extension<CurrentUser>>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    // `from src.auth_helpers import require_user as _ru; _ru(request)` — raises 401 when
    // auth IS configured but no user is authenticated. In unconfigured / loopback mode
    // `require_user` returns `""` instead of raising, so the gate passes. Mirror this via
    // `auth_adapter::require_user`, which delegates to the ported `helpers::require_user`.
    let user_str = user.as_ref().map(|Extension(CurrentUser(u))| u.as_str());
    let client_host = ci.as_ref().map(|c| c.0.ip().to_string());
    // `owner = _owner(request) or ""` — the owner for dispatch is the resolved user;
    // `auth_adapter::require_user` already returns `""` for unconfigured-loopback.
    let owner = auth_adapter::require_user(user_str, &state, client_host.as_deref())?;

    // `body = await request.json()` — FastAPI raises on invalid JSON.
    let body: Value = serde_json::from_slice(&raw_body)
        .map_err(|_| HttpException::new(400, "Invalid JSON"))?;
    let body = body.as_object().cloned().unwrap_or_default();

    // `note_id = body.get("note_id")`
    let note_id = body
        .get("note_id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    // `title = (body.get("title") or "").strip()`
    let title = body
        .get("title")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    // `note_body = (body.get("body") or "").strip()`
    let note_body = body
        .get("body")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    // `if not note_id: raise HTTPException(400, "note_id required")`.
    if note_id.is_empty() {
        return Err(HttpException::new(400, "note_id required"));
    }

    // `return await dispatch_reminder(..., owner=_gcu(request) or "", queue_browser=False)`.
    let result = dispatch_reminder(
        &title,
        &note_body,
        &note_id,
        &owner,
        false,
        Some(&state.task_scheduler),
    )
    .await;
    Ok(Json(result).into_response())
}

/// `POST /api/notes/reorder` — update `sort_order` for a list of note IDs.
///
/// ```python
/// user = _owner(request); body = await request.json(); ids = body.get("ids", [])
/// if not isinstance(ids, list): raise HTTPException(400, "ids must be a list")
/// try: _allow_null = not AuthManager().is_configured  except Exception: _allow_null = False
/// for i, nid in enumerate(ids):
///     q = db.query(Note).filter(Note.id == nid)
///     if user is not None:
///         if _allow_null: q = q.filter((Note.owner == user) | (Note.owner == None))
///         else:           q = q.filter(Note.owner == user)
///     note = q.first()
///     if note: note.sort_order = i
/// db.commit(); return {"ok": True, "count": len(ids)}
/// ```
async fn reorder_notes(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let user = current_user(&user);

    // `body = await request.json(); ids = body.get("ids", [])`.
    let body: Value = serde_json::from_slice(&raw_body)
        .map_err(|_| HttpException::new(400, "Invalid JSON"))?;
    let ids_val = body
        .as_object()
        .and_then(|o| o.get("ids").cloned())
        .unwrap_or_else(|| json!([]));
    // `if not isinstance(ids, list): raise HTTPException(400, "ids must be a list")`.
    let ids = match ids_val {
        Value::Array(a) => a,
        _ => return Err(HttpException::new(400, "ids must be a list")),
    };

    // `_allow_null = not AuthManager().is_configured` (Python builds a fresh
    // AuthManager; it reads the same auth.json as `state.auth`). The bare `except`
    // -> `_allow_null = False`; `is_configured()` here is infallible, so always Some.
    let allow_null = !state.auth.is_configured();

    let conn = session_local()?;
    // `for i, nid in enumerate(ids): ... note.sort_order = i`.
    for (i, nid) in ids.iter().enumerate() {
        // `q = db.query(Note).filter(Note.id == nid)` — `nid` is whatever the JSON list
        // held; SQLAlchemy compares it against the String PK. Only string ids can match
        // a row, so a non-string id simply finds nothing (mirrored by binding the JSON
        // string form / skipping non-strings as no-match).
        let nid = match nid.as_str() {
            Some(s) => s,
            None => continue,
        };
        let updated = if user.is_some() {
            if allow_null {
                // `(Note.owner == user) | (Note.owner == None)`.
                conn.execute(
                    "UPDATE notes SET sort_order = ?1 WHERE id = ?2 AND (owner = ?3 OR owner IS NULL)",
                    rusqlite::params![i as i64, nid, user.as_deref()],
                )
            } else {
                conn.execute(
                    "UPDATE notes SET sort_order = ?1 WHERE id = ?2 AND owner = ?3",
                    rusqlite::params![i as i64, nid, user.as_deref()],
                )
            }
        } else {
            // `if user is None:` — no owner filter (auth disabled / single-user).
            conn.execute(
                "UPDATE notes SET sort_order = ?1 WHERE id = ?2",
                rusqlite::params![i as i64, nid],
            )
        };
        updated.map_err(db_500)?;
    }

    // `return {"ok": True, "count": len(ids)}` — `len(ids)` counts ALL provided ids
    // (including non-strings / non-matches), not just the ones that updated.
    Ok(Json(json!({ "ok": true, "count": ids.len() })).into_response())
}

// ---------------------------------------------------------------------------
// Shared handler helpers
// ---------------------------------------------------------------------------

/// `user = _owner(request)` -> `get_current_user(request)` — the resolved username
/// or `None` (the load-bearing auth-disabled / single-user case).
fn current_user(user: &Option<Extension<CurrentUser>>) -> Option<String> {
    user.as_ref().map(|Extension(CurrentUser(u))| u.clone())
}

/// `note = db.query(Note).filter(Note.id == note_id).first()` then
/// `if not note: raise HTTPException(404, "Note not found")`. Returns the loaded row.
fn load_note(conn: &rusqlite::Connection, note_id: &str) -> Result<NoteRow, HttpException> {
    let note = conn
        .query_row(
            &format!("SELECT {NOTE_COLS} FROM notes WHERE id = ?1"),
            rusqlite::params![note_id],
            read_note_row,
        )
        .optional()
        .map_err(db_500)?;
    note.ok_or_else(|| HttpException::new(404, "Note not found"))
}

/// `if user is not None and note.owner != user: raise HTTPException(404, "Note not found")`.
///
/// STRICT ownership (the security-comment behavior): when a user resolves, the row's
/// owner must equal the user exactly — a `null`/empty owner does NOT match a non-empty
/// user, so it 404s. `user is None` (auth disabled) owns everything.
fn require_owned(note: NoteRow, user: Option<&str>) -> Result<NoteRow, HttpException> {
    match user {
        Some(u) if note.owner.as_deref() != Some(u) => Err(HttpException::new(404, "Note not found")),
        _ => Ok(note),
    }
}

/// Open a DB connection (`SessionLocal()`), mapping a failure to a 500.
fn session_local() -> Result<rusqlite::Connection, HttpException> {
    crate::core::database::session_local().map_err(db_500)
}

/// Map a `rusqlite::Error` to a 500 `HttpException` (an unhandled handler exception).
fn db_500(e: rusqlite::Error) -> HttpException {
    crate::pylog::error(&format!("note_routes DB error: {e}"));
    HttpException::new(500, "Internal Server Error")
}

// ---------------------------------------------------------------------------
// dispatch_reminder helpers
// ---------------------------------------------------------------------------

/// `data/note_pings_{slug}.json` — the CWD-relative per-owner dedupe cache, mirroring
/// Python's `Path(f"data/note_pings_{slug}.json")` (NOT the configured DATA_DIR; the
/// literal `data/` path, like `vault_routes`).
///
/// `_slug = "".join(c if (c.isalnum() or c in "-_.@") else "_" for c in (owner or "default"))`
/// — Python's `str.isalnum()` is Unicode-aware, matching Rust's `char::is_alphanumeric`.
fn ping_cache_path(owner: &str) -> std::path::PathBuf {
    let base = if owner.is_empty() { "default" } else { owner };
    let slug: String = base
        .chars()
        .map(|c| {
            if c.is_alphanumeric() || matches!(c, '-' | '_' | '.' | '@') {
                c
            } else {
                '_'
            }
        })
        .collect();
    std::path::PathBuf::from(format!("data/note_pings_{slug}.json"))
}

/// POST a reminder to an ntfy topic (`httpx.AsyncClient(timeout=10).post(...)`),
/// returning the HTTP status. Bearer-authorizes when an `api_key` is present.
async fn ntfy_post(
    base: &str,
    topic: &str,
    body: &str,
    title: &str,
    api_key: &str,
) -> Result<u16, reqwest::Error> {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(10))
        .build()?;
    let mut req = client
        .post(format!("{base}/{topic}"))
        .header("Title", title)
        .header("Priority", "high")
        .header("Tags", "bell")
        .body(body.to_string());
    if !api_key.is_empty() {
        req = req.header("Authorization", format!("Bearer {api_key}"));
    }
    let resp = req.send().await?;
    Ok(resp.status().as_u16())
}

/// `str(e) or e.__class__.__name__` — Python's error-string fallback (an exception
/// with an empty `str()` falls back to its class name). For the ntfy path the only
/// raised type is an httpx request error, so the class-name fallback is `RequestError`.
fn py_exc_str(msg: &str, class_name: &str) -> String {
    if msg.is_empty() {
        class_name.to_string()
    } else {
        msg.to_string()
    }
}

/// `a or b or c` over optional strings — Python truthiness: the first non-empty wins,
/// else `""`.
fn first_truthy_str(candidates: &[Option<&str>]) -> String {
    for c in candidates {
        if let Some(s) = c.filter(|s| !s.is_empty()) {
            return s.to_string();
        }
    }
    String::new()
}

/// First `n` characters (`s[:n]` — Python slices by code point, not byte).
fn truncate_chars(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// Python `bool(value)` over a JSON value: `None`/`False`/`0`/`0.0`/`""`/`[]`/`{}`
/// are falsy; everything else is truthy.
fn json_is_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// Parse a cache timestamp the way Python's `datetime.fromisoformat(str(last))` +
/// `if tzinfo is None: replace(tzinfo=utc)` does: an offset-bearing ISO string is
/// parsed with its offset; a naive value is assumed UTC. The stored values are
/// `datetime.now(utc).isoformat()` (`...+00:00`) or `...Z` (we write the `Z` form);
/// legacy plain naive timestamps are assumed UTC.
fn parse_iso_utc(raw: &str) -> Option<chrono::DateTime<chrono::Utc>> {
    // RFC3339 covers `...+00:00` and `...Z`.
    if let Ok(dt) = chrono::DateTime::parse_from_rfc3339(raw) {
        return Some(dt.with_timezone(&chrono::Utc));
    }
    // Naive (no tzinfo) -> assume UTC. Try with and without fractional seconds.
    let naive = chrono::NaiveDateTime::parse_from_str(raw, "%Y-%m-%dT%H:%M:%S%.f")
        .or_else(|_| chrono::NaiveDateTime::parse_from_str(raw, "%Y-%m-%dT%H:%M:%S"))
        .or_else(|_| chrono::NaiveDateTime::parse_from_str(raw, "%Y-%m-%d %H:%M:%S%.f"))
        .or_else(|_| chrono::NaiveDateTime::parse_from_str(raw, "%Y-%m-%d %H:%M:%S"))
        .ok()?;
    Some(chrono::DateTime::<chrono::Utc>::from_naive_utc_and_offset(
        naive,
        chrono::Utc,
    ))
}

/// One of the `_reasoning` self-talk patterns the LLM-synthesis line-walk drops.
///
/// Ports the Python `_reasoning` regex (case-insensitive, anchored at line start).
/// Targets ACTUAL self-talk (the model narrating what it will do), not any
/// first-person sentence: "I (need|should|...) to <task-verb> <object>...",
/// the model narrating about the user, "Let me <verb> (about/for/the...)",
/// "Looking at the/this/that", "Based on the/this/what", prompt-echo of length /
/// style instructions.
fn is_reasoning_line(line: &str) -> bool {
    let l = line.trim_start().to_lowercase();

    // "I (need|should|have|'ll|will|am going|am) to <task-verb> <object-ish>"
    let starts = [
        "i need to ",
        "i should to ",
        "i have to ",
        "i'll to ",
        "i will to ",
        "i am going to ",
        "i am to ",
    ];
    // The Python alternation is `i (need|should|have|'ll|will|am going|am)\s+to\s+`;
    // note `should`/`have`/`'ll`/`will` are normally followed directly by `to`, so the
    // observed leading forms are e.g. "i need to ", "i should ... to " — the regex
    // requires `<modal> to <task-verb>`. Build the matcher from the modal + " to ".
    const MODALS: &[&str] = &["i need", "i should", "i have", "i'll", "i will", "i am going", "i am"];
    const TASK_VERBS: &[&str] = &[
        "write ", "draft ", "compose ", "craft ", "generate ", "produce ", "create ",
        "summarize ", "answer ", "provide ", "note ", "address ", "remind ", "output ",
    ];
    const TASK_OBJECTS: &[&str] = &[
        "a ", "an ", "the ", "something", "this", "that", "here", "them", "him", "her",
        "you", "user", "reply", "response", "sentence", "message", "line", "warm",
    ];
    for modal in MODALS {
        let prefix = format!("{modal} to ");
        if let Some(rest) = l.strip_prefix(&prefix) {
            for verb in TASK_VERBS {
                if let Some(after_verb) = rest.strip_prefix(verb) {
                    if TASK_OBJECTS.iter().any(|obj| after_verb.starts_with(obj)) {
                        return true;
                    }
                }
            }
        }
    }
    let _ = starts; // documentation of the modal forms; matching done above.

    // "the user (wants|is asking|asks|needs|wrote|said|requested) [me] (to|for|that|about|something)"
    if let Some(rest) = l.strip_prefix("the user ") {
        const NARRATE: &[&str] = &[
            "wants ", "is asking ", "asks ", "needs ", "wrote ", "said ", "requested ",
        ];
        for verb in NARRATE {
            if let Some(after) = rest.strip_prefix(verb) {
                let after = after.strip_prefix("me ").unwrap_or(after);
                const TAILS: &[&str] = &["to", "for", "that", "about", "something"];
                if TAILS.iter().any(|t| after.starts_with(t)) {
                    return true;
                }
            }
        }
    }

    // "let me (think|write|draft|consider|note|see|check) (about|for|the|this|that|if|whether)"
    if let Some(rest) = l.strip_prefix("let me ") {
        const VERBS: &[&str] = &[
            "think ", "write ", "draft ", "consider ", "note ", "see ", "check ",
        ];
        for verb in VERBS {
            if let Some(after) = rest.strip_prefix(verb) {
                const TAILS: &[&str] =
                    &["about", "for", "the", "this", "that", "if", "whether"];
                if TAILS.iter().any(|t| after.starts_with(t)) {
                    return true;
                }
            }
        }
    }

    // "looking at (the|this|that)"
    for tail in ["the", "this", "that"] {
        if l.starts_with(&format!("looking at {tail}")) {
            return true;
        }
    }
    // "based on (the|this|what|context|that)"
    for tail in ["the", "this", "what", "context", "that"] {
        if l.starts_with(&format!("based on {tail}")) {
            return true;
        }
    }
    // "keep it under <N> words" — prompt-echo of the length instruction.
    if let Some(rest) = l.strip_prefix("keep it under ") {
        let digits: String = rest.chars().take_while(|c| c.is_ascii_digit()).collect();
        if !digits.is_empty() && rest[digits.len()..].trim_start().starts_with("words") {
            return true;
        }
    }
    // "no greetings|no preamble|no hashtags|just output the"
    for p in ["no greetings", "no preamble", "no hashtags", "just output the"] {
        if l.starts_with(p) {
            return true;
        }
    }

    false
}

/// The `_echo` pattern: a leading `Pending:` / `<N> pending` tail echoed from the
/// prompt (`^\s*(?:pending\s*[:.]|(?:\d+|one|two|three|four|five)\s+pending\b)`),
/// case-insensitive, anchored at line start.
fn is_echo_line(line: &str) -> bool {
    let l = line.trim_start().to_lowercase();
    // `pending\s*[:.]`
    if let Some(rest) = l.strip_prefix("pending") {
        let rest = rest.trim_start();
        if rest.starts_with(':') || rest.starts_with('.') {
            return true;
        }
    }
    // `(\d+|one|two|three|four|five)\s+pending\b`
    let leading_digits: String = l.chars().take_while(|c| c.is_ascii_digit()).collect();
    let after_count = if !leading_digits.is_empty() {
        Some(&l[leading_digits.len()..])
    } else {
        ["one", "two", "three", "four", "five"]
            .iter()
            .find_map(|w| l.strip_prefix(w))
    };
    if let Some(after) = after_count {
        // require at least one whitespace then "pending" at a word boundary.
        let trimmed = after.trim_start();
        if after.len() != trimmed.len() {
            if let Some(tail) = trimmed.strip_prefix("pending") {
                if tail.is_empty() || !tail.chars().next().unwrap().is_alphanumeric() {
                    return true;
                }
            }
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn row(owner: Option<&str>) -> NoteRow {
        NoteRow {
            id: "n1".to_string(),
            owner: owner.map(str::to_string),
            title: Some("T".to_string()),
            content: None,
            items: None,
            note_type: Some("note".to_string()),
            color: None,
            label: None,
            pinned: false,
            archived: false,
            due_date: None,
            source: Some("user".to_string()),
            session_id: None,
            sort_order: Some(0),
            image_url: None,
            repeat: Some("none".to_string()),
            ai_classification: None,
            ai_content_hash: None,
            agent_session_id: None,
            created_at: None,
            updated_at: None,
        }
    }

    #[test]
    fn require_owned_strict_ownership() {
        // `user is None` owns everything.
        assert!(require_owned(row(Some("alice")), None).is_ok());
        assert!(require_owned(row(None), None).is_ok());
        // resolved user must match exactly.
        assert!(require_owned(row(Some("alice")), Some("alice")).is_ok());
        assert!(require_owned(row(Some("bob")), Some("alice")).is_err());
        // null/empty owner does NOT match a non-empty user (strict).
        assert!(require_owned(row(None), Some("alice")).is_err());
        assert!(require_owned(row(Some("")), Some("alice")).is_err());
    }

    #[test]
    fn note_to_dict_key_order_and_shape() {
        let mut n = row(Some("alice"));
        n.items = Some(r#"[{"text":"a","done":false}]"#.to_string());
        n.ai_classification = Some(r#"{"kind":"todo"}"#.to_string());
        n.created_at = Some("2026-06-01 12:30:00".to_string());
        n.updated_at = Some("2026-06-01 12:45:00.500000".to_string());
        let d = _note_to_dict(&n);
        // Key order matches the Python dict literal exactly.
        let keys: Vec<&str> = d.as_object().unwrap().keys().map(|k| k.as_str()).collect();
        assert_eq!(
            keys,
            vec![
                "id", "owner", "title", "content", "items", "note_type", "color",
                "label", "pinned", "archived", "due_date", "source", "session_id",
                "sort_order", "image_url", "repeat", "ai_classification",
                "ai_content_hash", "agent_session_id", "created_at", "updated_at"
            ]
        );
        assert_eq!(d["items"], json!([{"text": "a", "done": false}]));
        assert_eq!(d["ai_classification"], json!({"kind": "todo"}));
        assert_eq!(d["created_at"], json!("2026-06-01T12:30:00"));
        assert_eq!(d["updated_at"], json!("2026-06-01T12:45:00.500000"));
        assert_eq!(d["repeat"], json!("none"));
        assert_eq!(d["sort_order"], json!(0));
    }

    #[test]
    fn note_to_dict_falsy_and_unparseable_fallbacks() {
        let mut n = row(None);
        // empty items string is falsy -> null; unparseable ai_classification -> null.
        n.items = Some("".to_string());
        n.ai_classification = Some("not json".to_string());
        n.repeat = Some("".to_string());
        n.sort_order = None;
        let d = _note_to_dict(&n);
        assert_eq!(d["items"], Value::Null);
        assert_eq!(d["ai_classification"], Value::Null);
        assert_eq!(d["repeat"], json!("none"));
        assert_eq!(d["sort_order"], json!(0));
        assert_eq!(d["created_at"], Value::Null);
    }

    #[test]
    fn repeat_or_none_truthiness() {
        assert_eq!(repeat_or_none(Some("daily")), "daily");
        assert_eq!(repeat_or_none(Some("")), "none");
        assert_eq!(repeat_or_none(None), "none");
    }

    #[test]
    fn note_create_defaults() {
        let body: NoteCreate = serde_json::from_value(json!({})).unwrap();
        assert_eq!(body.title, "");
        assert_eq!(body.note_type, "note");
        assert_eq!(body.source, "user");
        assert!(!body.pinned);
        assert_eq!(body.repeat.as_deref(), Some("none"));
        assert!(body.items.is_none());
        assert!(body.sort_order.is_none());

        let full: NoteCreate = serde_json::from_value(json!({
            "title": "Buy milk",
            "items": [{"text": "milk", "done": false}],
            "note_type": "checklist",
            "pinned": true,
            "sort_order": 3,
            "repeat": "weekly"
        }))
        .unwrap();
        assert_eq!(full.title, "Buy milk");
        assert_eq!(full.note_type, "checklist");
        assert!(full.pinned);
        assert_eq!(full.sort_order, Some(3));
        assert_eq!(full.repeat.as_deref(), Some("weekly"));
    }

    #[test]
    fn note_update_all_optional() {
        let empty: NoteUpdate = serde_json::from_value(json!({})).unwrap();
        assert!(empty.title.is_none());
        assert!(empty.archived.is_none());
        assert!(empty.agent_session_id.is_none());

        let upd: NoteUpdate = serde_json::from_value(json!({
            "title": "x", "archived": true, "sort_order": 2
        }))
        .unwrap();
        assert_eq!(upd.title.as_deref(), Some("x"));
        assert_eq!(upd.archived, Some(true));
        assert_eq!(upd.sort_order, Some(2));
    }

    #[test]
    fn ping_cache_path_slugifies_owner() {
        assert_eq!(
            ping_cache_path("alice@example.com"),
            std::path::PathBuf::from("data/note_pings_alice@example.com.json")
        );
        // empty owner -> "default".
        assert_eq!(
            ping_cache_path(""),
            std::path::PathBuf::from("data/note_pings_default.json")
        );
        // non-[alnum-_.@] chars -> "_".
        assert_eq!(
            ping_cache_path("a b/c"),
            std::path::PathBuf::from("data/note_pings_a_b_c.json")
        );
    }

    #[test]
    fn first_truthy_str_picks_first_nonempty() {
        assert_eq!(first_truthy_str(&[Some(""), Some("b"), Some("c")]), "b");
        assert_eq!(first_truthy_str(&[None, Some("x")]), "x");
        assert_eq!(first_truthy_str(&[Some(""), None]), "");
    }

    #[test]
    fn truncate_chars_respects_code_points() {
        let emoji: String = "🙂".repeat(600);
        assert_eq!(truncate_chars(&emoji, 500).chars().count(), 500);
        assert_eq!(truncate_chars("short", 500), "short");
    }

    #[test]
    fn parse_iso_utc_offset_and_naive() {
        // offset-bearing (the +00:00 form Python writes).
        let a = parse_iso_utc("2026-06-01T12:30:00.500000+00:00").unwrap();
        // Z form (what this port writes).
        let b = parse_iso_utc("2026-06-01T12:30:00.500000Z").unwrap();
        assert_eq!(a, b);
        // naive -> assumed UTC.
        let c = parse_iso_utc("2026-06-01T12:30:00").unwrap();
        assert_eq!(c.timezone(), chrono::Utc);
        assert!(parse_iso_utc("garbage").is_none());
    }

    #[test]
    fn json_truthiness_matches_python_bool() {
        assert!(!json_is_truthy(&Value::Null));
        assert!(!json_is_truthy(&json!(false)));
        assert!(json_is_truthy(&json!(true)));
        assert!(!json_is_truthy(&json!(0)));
        assert!(json_is_truthy(&json!(1)));
        assert!(!json_is_truthy(&json!("")));
        assert!(json_is_truthy(&json!("x")));
        assert!(!json_is_truthy(&json!([])));
        assert!(!json_is_truthy(&json!({})));
    }

    #[test]
    fn reasoning_line_drops_self_talk_keeps_warm_sentence() {
        // self-talk dropped.
        assert!(is_reasoning_line("I need to write a warm sentence."));
        assert!(is_reasoning_line("The user wants me to remind them."));
        assert!(is_reasoning_line("Let me think about the note."));
        assert!(is_reasoning_line("Looking at the note, ..."));
        assert!(is_reasoning_line("Based on the context, ..."));
        assert!(is_reasoning_line("Keep it under 25 words."));
        assert!(is_reasoning_line("No greetings or preamble."));
        // legit warm first-person sentences are KEPT (the tightened rule).
        assert!(!is_reasoning_line("I'll see you tomorrow!"));
        assert!(!is_reasoning_line("Don't forget to call mom."));
        assert!(!is_reasoning_line("You have one task waiting for you."));
    }

    #[test]
    fn echo_line_drops_pending_tail() {
        assert!(is_echo_line("Pending: 3 notes"));
        assert!(is_echo_line("Pending. done"));
        assert!(is_echo_line("3 pending"));
        assert!(is_echo_line("two pending tasks"));
        assert!(!is_echo_line("You have a pending note")); // not anchored at start.
        assert!(!is_echo_line("Remember to water the plants."));
    }

    #[test]
    fn py_exc_str_falls_back_to_class_name() {
        assert_eq!(py_exc_str("", "RequestError"), "RequestError");
        assert_eq!(py_exc_str("boom", "RequestError"), "boom");
    }

    // ── fire_reminder auth-gate parity ──────────────────────────────────────
    //
    // The parity change replaced the manual `None`-check gate with
    // `auth_adapter::require_user`, matching the Python's
    // `from src.auth_helpers import require_user as _ru; _ru(request)`.
    //
    // The critical behavioral difference: the old gate 401'd on `user == None`
    // unconditionally, which broke auth-disabled and loopback callers.
    // The new gate — `helpers::require_user` — only raises 401 when auth IS
    // configured but the caller has no session; in unconfigured / loopback mode
    // it returns `""` (first-run / single-user pass-through). These tests verify
    // that `require_user` is called with the right semantics and that the loopback
    // pass-through survives.

    #[test]
    fn fire_reminder_gate_loopback_unconfigured_passes() {
        // In unconfigured auth + loopback host the new gate (`require_user`)
        // returns "" instead of 401-ing. Build the helpers::Request directly
        // (the adapter's internal shape) to verify this — same pattern used in
        // auth_adapter's own unit tests.
        use crate::src::auth_helpers as helpers;
        use std::sync::Arc;
        use crate::core::auth::AuthManager;

        let auth = Arc::new(AuthManager::new());
        let req = helpers::Request {
            current_user: None,
            auth_manager: Some(&*auth),
            client_host: Some("127.0.0.1".to_string()),
        };
        // Only meaningful when the test data dir is genuinely unconfigured.
        if !auth.is_configured() {
            // Old gate: would have returned Err(401) because user == None.
            // New gate: returns Ok("") — auth-disabled / first-run loopback pass-through.
            let result = helpers::require_user(&req);
            assert!(result.is_ok(), "loopback + unconfigured should pass, got {:?}", result);
            assert_eq!(result.unwrap(), "", "loopback unconfigured owner should be empty string");
        }
    }

    #[test]
    fn fire_reminder_gate_nonloopback_unauthenticated_rejects() {
        // Auth configured or non-loopback with no user -> 401 (same as before,
        // just now via `require_user` rather than the manual check).
        use crate::src::auth_helpers as helpers;
        use std::sync::Arc;
        use crate::core::auth::AuthManager;

        let auth = Arc::new(AuthManager::new());
        let req = helpers::Request {
            current_user: None,
            auth_manager: Some(&*auth),
            client_host: Some("10.0.0.5".to_string()), // non-loopback
        };
        if !auth.is_configured() {
            // Unconfigured + non-loopback: should reject (401).
            let result = helpers::require_user(&req);
            assert!(result.is_err(), "non-loopback unauthenticated should be rejected");
        }
    }
}
