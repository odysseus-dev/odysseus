// src/builtin_actions.rs  <- src/builtin_actions.py
//! Registry of built-in automation actions that can be executed by the task
//! scheduler without needing an LLM call.
//!
//! ## Port classification — PORT_VIA_DB (web)
//!
//! This module owns the shared **`Outcome`** control-flow enum (the Rust model
//! of Python's `TaskNoop` / `TaskDeferred` `BaseException` sentinels — see
//! below) which `task_scheduler.rs` matches *before* generic errors. It is
//! re-exported so the scheduler can `use crate::src::builtin_actions::Outcome`.
//!
//! Action signature parity: each action is an `async fn(owner: &str, kwargs:
//! &Value) -> Result<(String, bool), Outcome>` — the Python `(result_str,
//! success_bool)` tuple is the `Ok` arm, and a raised `TaskNoop` / `TaskDeferred`
//! becomes `Err(Outcome::Noop(..))` / `Err(Outcome::Deferred{..})`. Honest-stub
//! actions return `Ok(("<action>: not available in the Rust port yet", false))` —
//! NEVER `(msg, true)` — so the scheduler records a failed run, exactly the
//! observable shape Python's `except Exception` arms produce.
//!
//! ### PORT_NOW (faithful, pure or std-only)
//!
//! * `Outcome` (TaskNoop / TaskDeferred), `_result_has_work` (non-overlapping
//!   `str.count` parity), `_classify_event_heuristic` + the `_TYPE_COLORS` /
//!   `_HEURISTIC_TYPES` / `_HEURISTIC_HIGH` / `_HEURISTIC_CRITICAL` tables (Lazy
//!   IndexMap; first-match insertion order = health, travel, meal, social,
//!   admin, work — matching the Python dict literal order).
//! * `_run_subprocess` / `action_ssh_command` / `action_run_script` /
//!   `action_run_local` (`subprocess.run` -> `std::process::Command` in a
//!   `tokio::task::spawn_blocking`).
//! * `action_tidy_research` (fs glob + `serde_json` validate of
//!   `data/deep_research/*.json`).
//!
//! ### PORT_VIA_DB (raw rusqlite SQL + owner WHERE clauses)
//!
//! * `action_consolidate_memory` — `memory::MemoryManager` load_all/save + a
//!   PER-OWNER-GROUP LLM tidy pass (`endpoint_resolver::resolve_endpoint_triple`,
//!   owner-threaded per group) with the exact duplicate-cleanup fallback; the
//!   AI prompt caps each memory at 2000 chars with truncation protection and the
//!   removed/cleaned/scanned totals are aggregated across groups.
//! * `action_tidy_calendar` — `calendar_events` dup detection + delete, watermark
//!   state file.
//! * `action_classify_events` — heuristic pass + batch LLM classification over
//!   `calendar_events`, pulling `memories` for context.
//! * `action_daily_brief` — `calendar_events` + `notes` + REAL IMAP unread-count /
//!   recent-subjects slice (direct IMAP via `routes::email_helpers`, the same
//!   stack `action_check_email_urgency` uses): EXAMINE INBOX, search UNSEEN, count,
//!   and From/Subject for the most-recent 5. Best-effort (a connect/fetch failure
//!   leaves 0 unread + no subjects, matching the Python `try/except`).
//! * `action_ping_notes` — `notes` due-window scan + per-owner JSON ping state.
//!   The actual reminder dispatch (`routes.note_routes.dispatch_reminder`) is a
//!   DEFERRED honest no-op, so a "due" note is detected and the state file is
//!   written, but no reminder leaves the process (documented).
//!
//! ### Email automation cluster (PORT_VIA_DB+web — IMAP/SMTP transport reused)
//!
//! The six email-automation actions are now WIRED to the ported transport
//! (`routes.email_pollers` / `routes.email_helpers` IMAP + `SCHEDULED_DB` +
//! `mail-parser` MIME) instead of deferring:
//!
//! * `action_summarize_emails` / `action_draft_email_replies` /
//!   `action_extract_email_events` -> `email_pollers::run_auto_summarize_once`
//!   with the Python flag/`days_back` defaults; the `_result_has_work` gating +
//!   `TaskNoop` messages are preserved verbatim.
//! * `action_learn_sender_signatures` ->
//!   `eh::imap_connect` newest-N scans under `spawn_blocking`, per-item LLM calls
//!   (`endpoint_resolver` + `llm_call_async`), and `INSERT OR REPLACE` into the
//!   `sender_signatures` table.
//! * `action_check_email_urgency` -> per-account `(UNSEEN SINCE ...)` scan, LLM
//!   triage, per-account cache + per-owner state files under `data/`, the
//!   `email_tags` upsert, and a `note_routes::dispatch_reminder` on newly-urgent.
//!
//! The urgency action triages each email through `llm_call_async_with_fallback`
//! over the utility-primary + utility-fallback candidate chain (so a stale primary
//! key falls through), matching `builtin_actions.py:1622,1789`.
//!   * `dispatch_reminder` is called with `scheduler=None` (the urgency action is
//!     a free `async fn`, with no `Arc<TaskScheduler>` handle), so the BROWSER
//!     channel cannot enqueue an in-app notification and `browser_sent` stays
//!     false. EMAIL / NTFY channels (the ones the reminder typically uses) work.
//!
//! ### Skills self-improvement actions (now LIVE — Gap #8)
//!
//! `action_test_skills` / `action_audit_skills` are no longer deferred. They
//! drive the ported test/audit orchestration in
//! `crate::routes::skills_pipeline` (the full `routes/skills_routes.py`
//! pipeline: `run_skill_test_once`, the LLM judge, `resolve_audit_models`,
//! `run_audit_all_job`, the audit-job store). Each builds its own disk-backed
//! `SkillsManager` over `DATA_DIR` (no `AppState` in a free action fn — matches
//! `app_initializer.rs:324`) and reuses `endpoint_resolver::resolve_endpoint`
//! + the pipeline's `resolve_audit_models`. The `seconds_since_model_activity`
//! quiet-window `TaskDeferred` IS ported (via `llm_core::note_model_activity` /
//! `seconds_since_model_activity`), and the `action_test_skills` served-model
//! swap IS ported via `llm_core::list_served_model_ids_with_headers`
//! (`GET /models` with the resolved auth headers; basename match or fail loud).
//!
//! ### Endpoint resolution (centralized)
//!
//! `src.endpoint_resolver.resolve_endpoint` IS now ported, in the shared
//! `crate::src::endpoint_resolver` module (with `get_user_setting` + the
//! owner-scoped `ModelEndpoint` query). The formerly-private `resolve_endpoint`
//! compose here was deleted; both call sites call
//! `endpoint_resolver::resolve_endpoint_triple(prefix, owner)`, preserving the
//! `{prefix}_endpoint_id` / `{prefix}_model` cascade (utility->default and
//! non-utility->utility->default) and the `(chat_url, model, headers)`/`None`
//! collapse. `action_consolidate_memory` threads the resolved endpoint PER OWNER
//! GROUP (`resolve_endpoint("utility", owner=group_owner or None)`, py:114-116 —
//! the no-owner housekeeping run resolves each group's owner independently);
//! `action_test_skills` threads `Some(owner)` (Python `resolve_endpoint("default",
//! owner=owner)`); `action_classify_events` passes `None` (Python passes no
//! owner). When no endpoint id is configured it returns
//! `None` (the Python `fallback_*` default), so LLM-backed actions degrade
//! honestly to their non-LLM fallback or a "No LLM endpoint available" result,
//! never fabricating output.

use crate::pyos as os;
use indexmap::IndexMap;
use once_cell::sync::Lazy;
use serde_json::{json, Value};

// ---------------------------------------------------------------------------
// Control-flow sentinels: Python's `TaskNoop` / `TaskDeferred` BaseExceptions.
// ---------------------------------------------------------------------------

/// The Rust model of Python's `TaskNoop` / `TaskDeferred` control-flow
/// `BaseException`s, plus the generic-error arm.
///
/// Python raises `TaskNoop` (nothing to do — drop the queued run silently,
/// advance `last_run`/`next_run`) or `TaskDeferred` (run later without recording
/// a skipped run) from inside an action. Because both subclass `BaseException`
/// (not `Exception`), the per-action `except Exception` wrappers don't catch
/// them — they propagate to the scheduler, which matches them explicitly.
///
/// `task_scheduler.rs` matches `Err(Outcome::Noop(..))` / `Err(Outcome::Deferred
/// {..})` *before* treating any other failure as a generic error.
#[derive(Debug, Clone)]
pub enum Outcome {
    /// `raise TaskNoop(reason)` — the action determined there's nothing to do.
    Noop(String),
    /// `raise TaskDeferred(reason, delay_seconds=20*60)` — run later.
    Deferred { reason: String, delay_seconds: i64 },
}

impl Outcome {
    /// `TaskDeferred.__init__(reason, delay_seconds=20*60)` default delay (1200s).
    pub const DEFAULT_DEFERRED_DELAY: i64 = 20 * 60;

    /// `raise TaskNoop(reason)`.
    pub fn noop(reason: impl Into<String>) -> Self {
        Outcome::Noop(reason.into())
    }

    /// `raise TaskDeferred(reason)` with the default 1200s delay.
    pub fn deferred(reason: impl Into<String>) -> Self {
        Outcome::Deferred { reason: reason.into(), delay_seconds: Self::DEFAULT_DEFERRED_DELAY }
    }

    /// `raise TaskDeferred(reason, delay_seconds=...)`.
    pub fn deferred_with(reason: impl Into<String>, delay_seconds: i64) -> Self {
        Outcome::Deferred { reason: reason.into(), delay_seconds }
    }

    /// The exception message (`str(exc)`) — `reason` for both variants.
    pub fn reason(&self) -> &str {
        match self {
            Outcome::Noop(r) => r,
            Outcome::Deferred { reason, .. } => reason,
        }
    }
}

impl std::fmt::Display for Outcome {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.reason())
    }
}

/// The `(result_str, success_bool)` shape every action returns, or an `Outcome`
/// sentinel that the scheduler matches before generic errors.
pub type ActionResult = Result<(String, bool), Outcome>;

// Every action that previously deferred (the six email automations,
// `test_skills` / `audit_skills`) is now wired to its ported backend, so the
// former `not_available` honest-stub helper has been removed — it would only
// ever be dead code.

// ---------------------------------------------------------------------------
// Endpoint resolution — now centralized in `crate::src::endpoint_resolver`.
// ---------------------------------------------------------------------------
//
// The local `resolve_endpoint` compose was deleted; both call sites now call the
// shared `endpoint_resolver::resolve_endpoint_triple(prefix, owner)`, which is
// behavior-identical to the old copy (same `utility -> default` /
// `non-utility -> utility -> default` cascade, same `(chat_url, model, headers)`
// or `None` collapse) EXCEPT: (a) the whitelisted setting reads now go through
// `get_user_setting(key, owner, ..)` so per-user model prefs take effect when an
// owner is threaded, and (b) the saved-endpoint lookup applies `is_enabled = 1`
// (matching Python `endpoint_resolver.py:190`; the old copy used the unfiltered
// `model_endpoints::endpoint_auth`, which could resolve a DISABLED endpoint).
//
// Owner threading mirrors Python exactly: `action_consolidate_memory` passes
// `owner=owner` (builtin_actions.py:100/102) so consolidate threads `Some(owner)`;
// `action_classify_events` passes NO owner (builtin_actions.py:541/543) so classify
// passes `None` even though its inner fn receives an `owner` param.

// ---------------------------------------------------------------------------
// Ported actions (session / email / calendar / notes / skills automation).
// ---------------------------------------------------------------------------

/// `action_tidy_sessions` — delete empty/throwaway sessions for the owner.
///
/// Pure heuristic — the LLM folder-sort phase is skipped (`skip_llm=True`). The
/// Python wraps `run_auto_sort` in `asyncio.wait_for(..., timeout=60)` and maps a
/// timeout to `("Chat session tidy timed out", False)` and any other exception to
/// `(str(e), False)`. `run_auto_sort` itself never raises `TaskNoop`.
pub async fn action_tidy_sessions(owner: &str, _kwargs: &Value) -> ActionResult {
    use crate::src::session_actions::run_auto_sort;
    use std::time::Duration;
    match tokio::time::timeout(Duration::from_secs(60), run_auto_sort(owner, true)).await {
        Ok(result) => Ok((result, true)),
        Err(_) => {
            // except asyncio.TimeoutError.
            crate::pylog::error("tidy_sessions action timed out");
            Ok(("Chat session tidy timed out".to_string(), false))
        }
    }
}

/// `action_tidy_documents` — run tidy on documents for the owner.
///
/// `run_document_tidy` returns the summary string (wrapped as `(result, True)`)
/// or raises `TaskNoop` (which is a `BaseException`, so the Python `except
/// Exception` does NOT catch it — it propagates to the scheduler). The Rust
/// `Err(Outcome::Noop(..))` propagates the same way; only a generic error path
/// would be caught (`run_document_tidy` returns no generic error variant, so the
/// `except Exception` arm is effectively unreachable here — documented).
pub async fn action_tidy_documents(owner: &str, _kwargs: &Value) -> ActionResult {
    crate::src::document_actions::run_document_tidy(owner).await
}

/// `action_summarize_emails` — one pass of email summary background processing.
///
/// Wires to `email_pollers::run_auto_summarize_once(do_summary=True,
/// do_reply=False)` (the Python `_run_auto_summarize_once` defaults `do_tag=False,
/// do_spam=False, do_calendar=False, days_back=1` — so this ONLY summarizes; it does
/// NOT tag emails or move spam to Junk). `_result_has_work` gates the `TaskNoop`; a
/// generic error logs + returns `(str(e), false)`.
pub async fn action_summarize_emails(_owner: &str, _kwargs: &Value) -> ActionResult {
    use crate::routes::email_pollers::run_auto_summarize_once;
    // _run_auto_summarize_once(do_summary=True, do_reply=False) + Python defaults
    // do_tag=False, do_spam=False, do_calendar=False, days_back=1.
    let result = run_auto_summarize_once(true, false, false, false, false, 1).await;
    if !_result_has_work(Some(&result)) {
        let detail = if result.is_empty() { "no new emails" } else { &result };
        return Err(Outcome::noop(format!("summarize: {detail}")));
    }
    Ok((result, true))
}

/// `action_draft_email_replies` — one pass of AI reply drafting.
///
/// Wires to `run_auto_summarize_once(do_summary=False, do_reply=True,
/// days_back=7)` (Python passes `days_back=7` explicitly — builtin_actions.py:512;
/// the un-passed flags keep the defaults do_tag=False, do_spam=False,
/// do_calendar=False — drafts replies only, no tagging/spam-move).
pub async fn action_draft_email_replies(_owner: &str, _kwargs: &Value) -> ActionResult {
    use crate::routes::email_pollers::run_auto_summarize_once;
    let result = run_auto_summarize_once(false, true, false, false, false, 7).await;
    if !_result_has_work(Some(&result)) {
        let detail = if result.is_empty() { "no new emails" } else { &result };
        return Err(Outcome::noop(format!("draft replies: {detail}")));
    }
    Ok((result, true))
}

/// `action_extract_email_events` — scan recent emails for booking/meeting
/// confirmations and auto-add them to the calendar.
///
/// Wires to `run_auto_summarize_once(do_summary=False, do_reply=False,
/// do_calendar=True, days_back=3)` (Python defaults do_tag=False, do_spam=False for
/// the un-passed flags) under a hard 5-minute wall-clock budget.
pub async fn action_extract_email_events(_owner: &str, _kwargs: &Value) -> ActionResult {
    use crate::routes::email_pollers::run_auto_summarize_once;
    use std::time::Duration;
    // `_aio.wait_for(_run_auto_summarize_once(do_summary=False, do_reply=False,
    // do_calendar=True, days_back=3), timeout=300)`. The un-passed flags keep the
    // Python defaults do_tag=False, do_spam=False.
    match tokio::time::timeout(
        Duration::from_secs(300),
        run_auto_summarize_once(false, false, false, false, true, 3),
    )
    .await
    {
        Err(_) => Ok((
            "Email→calendar pass exceeded 5 min budget — try fewer emails or a faster model"
                .to_string(),
            false,
        )),
        Ok(result) => {
            if !_result_has_work(Some(&result)) {
                let detail = if result.is_empty() { "no new emails" } else { &result };
                return Err(Outcome::noop(format!("email→calendar: {detail}")));
            }
            Ok((format!("{result} (3d window)"), true))
        }
    }
}

/// Generic-vs-`TaskNoop` split for the IMAP+LLM email actions: `Noop` propagates
/// past the Python `except Exception`; `Generic` is the `except Exception ->
/// (str(e), False)` arm.
enum EmailErr {
    Noop(String),
    Generic(String),
}

/// `action_learn_sender_signatures` — per-sender common-signature extraction,
/// cached in `SCHEDULED_DB.sender_signatures` and re-built after 30 days.
///
/// Wires the ported IMAP newest-300 header-only scan + per-sender body fetch +
/// `llm_call_async`. Caps at 20 senders per pass; eligible = >=3 msgs AND cache
/// older than 30 days.
pub async fn action_learn_sender_signatures(_owner: &str, _kwargs: &Value) -> ActionResult {
    match learn_sender_signatures_inner().await {
        Ok(t) => Ok(t),
        Err(EmailErr::Noop(r)) => Err(Outcome::noop(r)),
        Err(EmailErr::Generic(msg)) => {
            crate::pylog::error(&format!("learn_sender_signatures failed: {msg}"));
            Ok((msg, false))
        }
    }
}

/// One scanned header row (the sig learner's `_pull_headers` result).
struct SigMail {
    seq: String,
    from_address: String,
}

static SIG_FENCE_OPEN_RE: Lazy<regex::Regex> =
    Lazy::new(|| regex::Regex::new(r"^```[\w]*\n?").unwrap());
static SIG_FENCE_CLOSE_RE: Lazy<regex::Regex> =
    Lazy::new(|| regex::Regex::new(r"\n?```\s*$").unwrap());

/// Sender LOCAL-PARTS (matched exactly or by prefix) whose mail never carries a
/// personal signature worth learning (`_SIG_SKIP_PREFIXES`, builtin_actions.py:963).
/// These compare against the local-part (BEFORE "@"), so role names must NOT carry
/// a trailing "@": the old "support@"/"info@"/"admin@" forms could never match a
/// local-part of "support" etc. and were silently dead.
const SIG_SKIP_PREFIXES: &[&str] = &[
    "noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon",
    "notifications", "notification", "bounce", "newsletter", "support",
    "info", "admin",
];

async fn learn_sender_signatures_inner() -> Result<(String, bool), EmailErr> {
    use crate::routes::email_helpers as eh;
    use crate::src::endpoint_resolver::resolve_endpoint_triple;
    use crate::src::llm_core::llm_call_async;
    use crate::src::text_helpers::strip_think;

    // 1. Pull newest-300 INBOX From headers (header-only fetch).
    let mails: Vec<SigMail> = tokio::task::spawn_blocking(|| {
        let mut out: Vec<SigMail> = Vec::new();
        let mut conn = match eh::imap_connect(None, "") {
            Ok(c) => c,
            Err(_) => return out,
        };
        let _scan = (|| -> Result<(), String> {
            conn.examine("INBOX").map_err(|e| e.to_string())?;
            let found = conn.search("ALL").map_err(|e| e.to_string())?;
            let mut seqs: Vec<u32> = found.into_iter().collect();
            seqs.sort_unstable();
            let start = seqs.len().saturating_sub(300);
            let newest: Vec<u32> = seqs[start..].iter().rev().copied().collect();
            for seq in newest {
                let seq_str = seq.to_string();
                let raw = match conn.fetch(&seq_str, "(BODY.PEEK[HEADER.FIELDS (FROM)])") {
                    Ok(fetches) => fetches.iter().find_map(|f| f.header().map(|b| b.to_vec())),
                    Err(_) => continue,
                };
                let raw = match raw {
                    Some(b) => b,
                    None => continue,
                };
                let msg = match eh::parse_message(&raw) {
                    Some(m) => m,
                    None => continue,
                };
                let from_raw = msg.header("From").unwrap_or_default();
                let (_name, addr) = parseaddr(&from_raw);
                let from_addr = addr.to_lowercase().trim().to_string();
                if from_addr.is_empty() || !from_addr.contains('@') {
                    continue;
                }
                out.push(SigMail { seq: seq_str, from_address: from_addr });
            }
            Ok(())
        })();
        let _ = conn.logout();
        let _ = _scan;
        out
    })
    .await
    .map_err(|e| EmailErr::Generic(e.to_string()))?;

    if mails.is_empty() {
        return Ok(("No emails to scan".to_string(), true));
    }

    // 2. Group by sender; drop addresses that don't carry useful sigs
    // (see `SIG_SKIP_PREFIXES` for why the role names carry no trailing "@").
    // Preserve first-seen order of senders (Python dict insertion order).
    let mut by_sender: IndexMap<String, Vec<SigMail>> = IndexMap::new();
    for m in mails {
        let addr = m.from_address.clone();
        let local = addr.split_once('@').map(|(l, _)| l).unwrap_or(&addr).to_string();
        // `if any(local == p or local.startswith(p) for p in _SIG_SKIP_PREFIXES): continue`.
        if SIG_SKIP_PREFIXES.iter().any(|p| local == *p || local.starts_with(p)) {
            continue;
        }
        // Skip plus-aliases / list-style addresses.
        if local.contains('+') || addr.contains("-noreply") || addr.contains("-bounces") {
            continue;
        }
        by_sender.entry(addr).or_default().push(m);
    }

    // 3. Eligibility: >=3 emails AND (no cache OR cache > 30 days old).
    eh::init_scheduled_db();
    let db_path = eh::scheduled_db();
    let cached: std::collections::HashMap<String, String> = {
        match rusqlite::Connection::open(&db_path) {
            Ok(c) => {
                let mut map = std::collections::HashMap::new();
                if let Ok(mut stmt) =
                    c.prepare("SELECT from_address, last_built_at FROM sender_signatures")
                {
                    if let Ok(rows) = stmt.query_map([], |r| {
                        Ok((r.get::<_, String>(0)?, r.get::<_, Option<String>>(1)?.unwrap_or_default()))
                    }) {
                        for (a, ts) in rows.flatten() {
                            map.insert(a, ts);
                        }
                    }
                }
                map
            }
            Err(_) => std::collections::HashMap::new(),
        }
    };

    // `cutoff_iso = (utcnow() - timedelta(days=30)).isoformat()`.
    let cutoff_iso = naive_isoformat(crate::pydatetime::utcnow_naive() - chrono::Duration::days(30));
    let mut eligible: Vec<(String, Vec<SigMail>)> = Vec::new();
    for (addr, msgs) in by_sender {
        if msgs.len() < 3 {
            continue;
        }
        // `if cached.get(addr, "") > cutoff_iso: continue` (string comparison).
        if cached.get(&addr).map(|c| c.as_str()).unwrap_or("") > cutoff_iso.as_str() {
            continue;
        }
        let take: Vec<SigMail> = msgs.into_iter().take(5).collect();
        eligible.push((addr, take));
    }

    if eligible.is_empty() {
        return Ok(("All sender sigs already cached (or no eligible senders)".to_string(), true));
    }

    let endpoint = resolve_endpoint_triple("utility", None)
        .or_else(|| resolve_endpoint_triple("default", None));
    let (url, model, headers) = match endpoint {
        Some(t) => t,
        None => return Ok(("No LLM endpoint available".to_string(), false)),
    };

    let eligible_len = eligible.len();
    let mut analyzed = 0i64;
    let mut no_sig = 0i64;
    // `for addr, msgs in eligible[:20]:`
    for (addr, msgs) in eligible.into_iter().take(20) {
        let seqs: Vec<String> = msgs.iter().map(|m| m.seq.clone()).collect();
        // `_fetch_bodies(msgs)` — BODY.PEEK[TEXT] for each, 4000-char cap.
        let addr_for_log = addr.clone();
        let bodies: Vec<String> = match tokio::task::spawn_blocking(move || {
            let mut bodies: Vec<String> = Vec::new();
            let mut conn = match eh::imap_connect(None, "") {
                Ok(c) => c,
                Err(e) => return Err(e),
            };
            let _ = conn.examine("INBOX");
            for seq in &seqs {
                match conn.fetch(seq, "(BODY.PEEK[TEXT])") {
                    Ok(fetches) => {
                        if let Some(bytes) = fetches.iter().find_map(|f| f.text().map(|b| b.to_vec())) {
                            let text = String::from_utf8_lossy(&bytes);
                            bodies.push(text.chars().take(4000).collect::<String>());
                        }
                    }
                    Err(_) => continue,
                }
            }
            let _ = conn.logout();
            Ok(bodies)
        })
        .await
        {
            Ok(Ok(b)) => b,
            Ok(Err(e)) => {
                crate::pylog::warning(&format!("sig learner: fetch bodies failed for {addr_for_log}: {e}"));
                continue;
            }
            Err(e) => {
                crate::pylog::warning(&format!("sig learner: fetch bodies failed for {addr_for_log}: {e}"));
                continue;
            }
        };
        if bodies.len() < 2 {
            continue;
        }

        // `"\n\n---NEXT EMAIL---\n\n".join(bodies[:5])`.
        let joined = bodies
            .iter()
            .take(5)
            .cloned()
            .collect::<Vec<_>>()
            .join("\n\n---NEXT EMAIL---\n\n");
        let prompt = format!(
            "You are extracting the literal common SIGNATURE block that \
appears at the END of multiple emails from the same sender.\n\n\
Return ONLY the exact signature text, verbatim, with original \
line breaks preserved. If there is no clear common signature \
block across these emails, respond with the single token: \
NONE\n\n\
INCLUDE: title, company, address, phone, email/url lines, \
legal disclaimer block.\n\
EXCLUDE: greetings ('Hi', 'Dear'), closing phrases on their \
own ('Best regards'), the sender's name on its own line, the \
body content, quoted/forwarded threads (lines starting with \
'>' or 'On ... wrote:' or 'From: ... Sent:').\n\n\
EMAILS FROM {addr}:\n{joined}"
        );

        let sig = match llm_call_async(
            &url,
            &model,
            vec![json!({"role": "user", "content": prompt})],
            0.0,
            600,
            headers.clone(),
            60,
        )
        .await
        {
            Ok(raw) => {
                let s = strip_think(&raw, false, false);
                let s = s.trim().to_string();
                // Strip surrounding code fences.
                let s = SIG_FENCE_OPEN_RE.replace(&s, "").into_owned();
                let s = SIG_FENCE_CLOSE_RE.replace(&s, "").into_owned();
                s.trim().to_string()
            }
            Err(e) => {
                crate::pylog::warning(&format!("sig LLM call failed for {addr}: {e}"));
                continue;
            }
        };

        // NONE sentinel / out-of-bounds -> cache a NULL row.
        let sig_norm = sig.to_uppercase().trim().trim_matches('.').to_string();
        let cached_sig: Option<String> = if sig.is_empty()
            || sig_norm == "NONE"
            || sig.chars().count() < 15
            || sig.chars().count() > 3000
        {
            no_sig += 1;
            None
        } else {
            Some(sig.clone())
        };

        match rusqlite::Connection::open(&db_path) {
            Ok(c) => {
                if let Err(e) = c.execute(
                    "INSERT OR REPLACE INTO sender_signatures \
                     (from_address, signature_text, sample_count, last_built_at, model_used, source) \
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                    rusqlite::params![
                        addr,
                        cached_sig,
                        bodies.len() as i64,
                        utcnow_isoformat(),
                        model,
                        "llm",
                    ],
                ) {
                    crate::pylog::warning(&format!("sig cache write failed for {addr}: {e}"));
                } else {
                    analyzed += 1;
                }
            }
            Err(e) => {
                crate::pylog::warning(&format!("sig cache write failed for {addr}: {e}"));
            }
        }
    }

    Ok((
        format!(
            "Learned sigs: {} found, {} no-sig, of {} eligible",
            analyzed - no_sig,
            no_sig,
            eligible_len
        ),
        true,
    ))
}

/// `action_test_skills` — run the per-skill Test on every skill (agent runs the
/// procedure in a sandbox, the LLM judges the transcript, the verdict is recorded
/// on the skill). ADVISORY ONLY — only writes `set_audit` (never rewrites
/// SKILL.md, never demotes status, never overrides confidence). Mirrors
/// `src/builtin_actions.py::action_test_skills` (1240-1349).
///
/// The hard work lives in `crate::routes::skills_pipeline` (the shared
/// test/audit orchestration ported from `routes/skills_routes.py`); this action
/// is the synchronous driver: build a `SkillsManager` (no `AppState` here, so we
/// construct one over `DATA_DIR` exactly like `app_initializer.rs:324`), resolve
/// the Default→Utility endpoint (owner-threaded, matching Python
/// `resolve_endpoint("default", owner=owner)`), then run `run_skill_test_once`
/// per skill and tally the verdicts. The `owner` is threaded through
/// `read_skill_md` / `run_skill_test_once` / `set_audit` so the action stays
/// scoped to a single user's skills (Python passes `owner=owner` throughout).
///
/// The Python `list_model_ids(url, headers)` "model not served by endpoint →
/// loud-fail / basename-swap" guard (py:1268-1286) IS ported via
/// `llm_core::list_served_model_ids_with_headers` (a `GET /models` with the
/// resolved auth headers). If the configured model isn't served, a basename match
/// swaps to the served id; with no match it fails loudly (listing up to 8
/// available ids) instead of producing garbage verdicts. A network failure leaves
/// `avail` empty → no swap, matching the Python `except` branch.
pub async fn action_test_skills(owner: &str, _kwargs: &Value) -> ActionResult {
    use crate::routes::skills_pipeline::{run_skill_test_once, skill_test_task};
    use crate::services::memory::skills::SkillsManager;
    use crate::src::endpoint_resolver::resolve_endpoint_triple;

    // #3 SCOPE GUARD: refuse a None/empty owner — `load(None)` would return every
    // user's skills and we'd cross-test (and write verdicts to) other users.
    if owner.is_empty() {
        return Ok((
            "test_skills requires an owner on the task — refusing to run without scope."
                .to_string(),
            false,
        ));
    }

    // No `AppState` in a free action fn — build the disk-backed SkillsManager
    // directly over DATA_DIR (matches `app_initializer.rs:324`).
    let sm = SkillsManager::new(&crate::src::constants::DATA_DIR);
    let skills = sm.load(Some(owner));
    let names: Vec<String> = skills
        .iter()
        .filter_map(|s| s.get("name").and_then(|v| v.as_str()).map(str::to_string))
        .collect();
    if names.is_empty() {
        return Err(Outcome::noop("no skills to test"));
    }

    // Prefer the configured DEFAULT (→ Utility) model — owner-threaded, matching
    // Python `resolve_endpoint("default", owner=owner)` (builtin_actions.py:1322).
    let (url, mut model, headers) = match resolve_endpoint_triple("default", Some(owner)) {
        Some(t) => t,
        None => {
            return Ok((
                "No Default/Utility model configured — set one in Settings.".to_string(),
                false,
            ));
        }
    };

    // #2 NO SILENT MODEL SWAP (py:1268-1286): if the configured model isn't served
    // by the endpoint, basename-match to a served id; fail loudly if none matches
    // (rather than running every skill against a wrong/embedding-only model). A
    // network failure leaves `avail` empty → no swap (the Python `except` branch).
    let avail = crate::src::llm_core::list_served_model_ids_with_headers(&url, &headers).await;
    if !avail.is_empty() && !avail.iter().any(|a| a == &model) {
        let base = crate::src::llm_core::basename_rstrip_slash(&model);
        match avail
            .iter()
            .find(|a| crate::src::llm_core::basename_rstrip_slash(a) == base)
        {
            Some(m) => model = m.clone(),
            None => {
                let head: Vec<&str> = avail.iter().take(8).map(|s| s.as_str()).collect();
                let ellipsis = if avail.len() > 8 { "\u{2026}" } else { "" };
                return Ok((
                    format!(
                        "Default model '{model}' not served by endpoint {url}. \
Available: {}{ellipsis}. Set a valid Default model in Settings.",
                        head.join(", ")
                    ),
                    false,
                ));
            }
        }
    }

    crate::pylog::info(&format!(
        "test_skills: starting on {} skills, model={model}, owner={owner:?}",
        names.len()
    ));

    // `Counter` -> a small ordered tally. We only ever read it back in the fixed
    // key order below, so a plain map keyed by &str is enough.
    let mut tally: std::collections::HashMap<&str, i64> = std::collections::HashMap::new();
    let mut bump = |k: &'static str| {
        *tally.entry(k).or_insert(0) += 1;
    };
    let mut per_skill_log: Vec<String> = Vec::new();

    for skill in &skills {
        let name = match skill.get("name").and_then(|v| v.as_str()) {
            Some(n) if !n.is_empty() => n.to_string(),
            _ => continue,
        };
        let md = sm.read_skill_md(&name, Some(owner)).unwrap_or_default();
        if md.is_empty() {
            bump("skipped");
            per_skill_log.push(format!("{name}: skipped (no SKILL.md)"));
            continue;
        }
        let task = skill_test_task(skill);
        // The Rust stream yields no error item, so `run_skill_test_once` cannot
        // itself surface a per-skill error the way the Python `try/except`
        // around the coroutine does; the defensive `error` tally arm is kept for
        // shape parity but is unreachable in practice (documented).
        let (transcript, verdict) =
            run_skill_test_once(&md, &task, &url, &model, headers.clone(), Some(owner.to_string()))
                .await;
        let v = verdict
            .get("verdict")
            .and_then(|x| x.as_str())
            .filter(|s| !s.is_empty())
            .unwrap_or("unknown")
            .to_string();
        bump(match v.as_str() {
            "pass" => "pass",
            "needs_work" => "needs_work",
            "fail" => "fail",
            "inconclusive" => "inconclusive",
            "skipped" => "skipped",
            "error" => "error",
            _ => "unknown",
        });
        let summary = verdict
            .get("summary")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .to_string();
        let tlen = transcript.chars().count();
        let mut detail = String::new();
        if matches!(v.as_str(), "unknown" | "inconclusive" | "fail" | "needs_work") {
            let mut bits: Vec<String> = Vec::new();
            if !summary.is_empty() {
                bits.push(summary.chars().take(160).collect());
            }
            if tlen < 200 {
                bits.push(format!("transcript {tlen}b"));
            }
            if !bits.is_empty() {
                detail = format!(" — {}", bits.join("; "));
            }
        }
        per_skill_log.push(format!("{name}: {v}{detail}"));

        // #4 + #8 + #12: ONLY persist a real verdict (pass / needs_work / fail /
        // inconclusive). Skip 'unknown' (the judge's "couldn't parse" sentinel)
        // and skip the confidence rewrite entirely (advisory-only). Rust
        // `set_audit` takes `teacher_model: &str` — pass ""; owner is threaded
        // (Python `sm.set_audit(name, v, by_teacher=False, worker_model=model,
        // owner=owner)`, builtin_actions.py:1383).
        if matches!(v.as_str(), "pass" | "needs_work" | "fail" | "inconclusive") {
            sm.set_audit(&name, &v, false, &model, "", Some(owner));
        }
        if v == "unknown" {
            crate::pylog::warning(&format!(
                "test_skills: {name} → unknown — {}; transcript_len={tlen}",
                summary.chars().take(200).collect::<String>()
            ));
        }
    }

    let mut parts: Vec<String> = Vec::new();
    for k in [
        "pass",
        "needs_work",
        "fail",
        "inconclusive",
        "unknown",
        "skipped",
        "error",
    ] {
        if let Some(&n) = tally.get(k) {
            if n != 0 {
                parts.push(format!("{n} {k}"));
            }
        }
    }
    let joined = parts.join(" · ");
    let header = format!(
        "Tested {} skill(s): {}",
        names.len(),
        if joined.is_empty() { "0".to_string() } else { joined }
    );
    let body = per_skill_log.join("\n");
    Ok((format!("{header}\nmodel={model}\n\n{body}"), true))
}

/// `action_audit_skills` — run the real skills audit pipeline for skills that
/// have not been audited. Unlike `test_skills`, this uses the same audit ladder
/// as the UI "Audit all" flow (metadata narrowing, self-edit/retry, optional
/// teacher rewrite, necessity tagging, publish/draft finalization). Mirrors
/// `src/builtin_actions.py::action_audit_skills` (1352-1418).
///
/// The `seconds_since_model_activity(url, model)` quiet-window `TaskDeferred`
/// (py:1384-1393) IS ported: `llm_core::note_model_activity` records the last-use
/// timestamp on every live LLM request (`stream_llm` / `llm_call_async`), and if
/// the audit model was used < 20 min ago this returns `Outcome::Deferred` (20 min
/// delay) instead of competing with active chat.
pub async fn action_audit_skills(owner: &str, _kwargs: &Value) -> ActionResult {
    use crate::routes::skills_pipeline::{
        audit_key, audit_running, resolve_audit_models, run_audit_all_job, seed_audit_job,
    };
    use crate::services::memory::skills::SkillsManager;

    if owner.is_empty() {
        return Ok((
            "audit_skills requires an owner — refusing to run without scope.".to_string(),
            false,
        ));
    }

    let key = audit_key(Some(owner));
    if let Some(existing) = audit_running(&key) {
        let _ = existing;
        return Err(Outcome::noop("skill audit already running"));
    }

    let sm = SkillsManager::new(&crate::src::constants::DATA_DIR);
    let skills = sm.load(Some(owner));
    let names: Vec<String> = skills
        .iter()
        .filter(|s| {
            s.get("name").and_then(|v| v.as_str()).is_some_and(|n| !n.is_empty())
                && s.get("audit_verdict")
                    .map(|v| !audit_verdict_truthy(v))
                    .unwrap_or(true)
        })
        .filter_map(|s| s.get("name").and_then(|v| v.as_str()).map(str::to_string))
        .collect();
    if names.is_empty() {
        return Err(Outcome::noop("no unaudited skills"));
    }

    let (url, model, headers, teacher) = match resolve_audit_models().await {
        Ok(t) => t,
        Err(e) => {
            crate::pylog::error(&format!("audit_skills action failed: {e}"));
            return Ok((e, false));
        }
    };

    // Quiet-window defer (py:1384-1393): if the audit model was used < 20 min
    // ago, defer 20 min so the background audit doesn't compete with active chat.
    // `seconds_since_model_activity` reads the per-process last-use timestamp set
    // by `note_model_activity` on every live LLM request.
    if let Some(recent) = crate::src::llm_core::seconds_since_model_activity(&url, &model) {
        if recent < (20.0 * 60.0) {
            return Err(Outcome::deferred(format!(
                "audit model {model} was used {}s ago; waiting for quiet window",
                recent as i64
            )));
        }
    }

    // `_skill_audit_jobs[key] = {... scope:"scheduled-unchecked" ...}`. `seed_audit_job`
    // takes the teacher MODEL name (`teacher[1]` in Python) — `Option<&str>`.
    let teacher_model: Option<&str> = teacher.as_ref().map(|(_, m, _)| m.as_str());
    seed_audit_job(&key, "scheduled-unchecked", &model, teacher_model, &names);

    // `await _run_audit_all_job(key, sm, names, url, model, headers, teacher, owner)`.
    // url/model by ref; headers + teacher by value; owner `Option<String>`.
    let total = names.len();
    run_audit_all_job(
        &key,
        &sm,
        names,
        &url,
        &model,
        headers,
        teacher,
        Some(owner.to_string()),
    )
    .await;

    // Tally `job.results[].result` from the finished job snapshot.
    let job = crate::routes::skills_pipeline::get_audit_job(&key).unwrap_or_else(|| json!({}));
    let mut counts: std::collections::BTreeMap<String, i64> = std::collections::BTreeMap::new();
    if let Some(results) = job.get("results").and_then(|v| v.as_array()) {
        for r in results {
            let k = r
                .get("result")
                .and_then(|x| x.as_str())
                .filter(|s| !s.is_empty())
                .unwrap_or("unknown")
                .to_string();
            *counts.entry(k).or_insert(0) += 1;
        }
    }
    let summary = if counts.is_empty() {
        "0 results".to_string()
    } else {
        counts
            .iter()
            .map(|(k, v)| format!("{v} {k}"))
            .collect::<Vec<_>>()
            .join(" · ")
    };
    let done = job.get("done").and_then(|v| v.as_i64()).unwrap_or(0);
    Ok((
        format!("Audited {done}/{total} unaudited skill(s): {summary}"),
        true,
    ))
}

/// `bool(s.get("audit_verdict"))` — Python truthiness of the stored audit
/// verdict: only a non-empty string (or any non-falsy JSON) counts as "audited".
fn audit_verdict_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::String(s) => !s.is_empty(),
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `action_check_email_urgency` — scan unread emails across accounts, LLM-triage
/// new ones, cache per-UID verdicts, tag the inbox (`email_tags`), and fire a
/// reminder when a previously-unseen UID scores reply-soon/urgent (>=2).
///
/// Wires the ported per-account `(UNSEEN SINCE ...)` IMAP scan + `llm_call_async`
/// (PRIMARY ONLY — see FLAG) + per-owner state / per-account cache files under
/// `data/` + the `email_tags` upsert + `note_routes::dispatch_reminder`.
///
/// Each email is triaged through `llm_call_async_with_fallback` over the
/// utility-primary + utility-fallback candidate chain (a stale primary key falls
/// through to the configured fallbacks), matching the Python.
///   * `dispatch_reminder` is called with `scheduler=None` — this free `async fn`
///     has no `Arc<TaskScheduler>` handle, so the BROWSER channel cannot enqueue
///     the in-app notification and `browser_sent` stays false. EMAIL / NTFY
///     channels (the ones the urgency reminder typically uses, gated on
///     `settings.reminder_channel`) work fully through the wired dispatch.
pub async fn action_check_email_urgency(owner: &str, _kwargs: &Value) -> ActionResult {
    match check_email_urgency_inner(owner).await {
        Ok(t) => Ok(t),
        Err(EmailErr::Noop(r)) => Err(Outcome::noop(r)),
        Err(EmailErr::Generic(msg)) => {
            // Python `logger.exception(...)` -> error log + (str(e), False).
            crate::pylog::error(&format!("check_email_urgency action failed: {msg}"));
            Ok((msg, false))
        }
    }
}

/// A single enabled `email_accounts` row the urgency scan iterates.
struct UrgencyAccount {
    id: String,
}

/// A per-UID scan result before LLM triage (the Python `_scan_one` item dict).
struct UrgencyItem {
    key: String,
    uid: String,
    cached: Option<Value>,
    subject: String,
    from: String,
    headers: String,
    body: String,
    message_id: String,
}

async fn check_email_urgency_inner(owner: &str) -> Result<(String, bool), EmailErr> {
    use crate::routes::email_helpers as eh;
    use crate::src::endpoint_resolver::{resolve_endpoint_triple, resolve_utility_fallback_candidates};
    use crate::src::llm_core::llm_call_async_with_fallback;
    use std::collections::HashSet;
    use std::path::PathBuf;

    let settings = crate::src::settings::load_settings();

    // Per-owner state file + per-account cache dir (literal data/ paths).
    let owner_or_default = if owner.is_empty() { "default" } else { owner };
    let owner_slug: String = owner_or_default
        .chars()
        .map(|c| if c.is_alphanumeric() || "-_.@".contains(c) { c } else { '_' })
        .collect();
    let state_path = PathBuf::from(format!("data/email_urgency_state_{owner_slug}.json"));
    let cache_dir = PathBuf::from("data/email_urgency_cache");
    let _ = std::fs::create_dir_all(&cache_dir);
    if let Some(parent) = state_path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }

    // `AGE_CUTOFF = utcnow() - 7d`.
    let age_cutoff = crate::pydatetime::utcnow_naive() - chrono::Duration::days(7);
    const TRIAGE_VERSION: i64 = 3;
    let category_tags: HashSet<&str> = [
        "newsletter", "marketing", "notification", "finance", "bills", "receipt",
        "travel", "security", "shopping", "social", "work", "personal", "calendar",
    ]
    .into_iter()
    .collect();
    let mut managed_tags: HashSet<&str> = category_tags.clone();
    managed_tags.insert("urgent");
    managed_tags.insert("reply-soon");
    managed_tags.insert("promo");

    // 1. Resolve LLM candidates. PRIMARY only is issued (with_fallback unported).
    let endpoint = resolve_endpoint_triple("utility", None)
        .or_else(|| resolve_endpoint_triple("default", None));
    let (url, model, headers) = match endpoint {
        Some(t) => t,
        None => return Ok(("No LLM endpoint available".to_string(), false)),
    };
    // `candidates = [(url, model, headers)] + resolve_utility_fallback_candidates()`
    // (builtin_actions.py:1622); the per-email classify runs through
    // `llm_call_async_with_fallback` so a stale primary key falls through.
    let candidates: Vec<(String, String, IndexMap<String, String>)> = {
        let mut c = vec![(url.clone(), model.clone(), headers.clone())];
        c.extend(resolve_utility_fallback_candidates(None));
        c
    };

    // 2. Enumerate enabled accounts (owner OR unowned+same-mailbox).
    let accounts: Vec<UrgencyAccount> = {
        let conn = crate::core::database::session_local().map_err(|e| EmailErr::Generic(e.to_string()))?;
        let (sql, params): (String, Vec<String>) = if owner.is_empty() {
            (
                "SELECT id FROM email_accounts WHERE enabled = 1".to_string(),
                vec![],
            )
        } else {
            (
                "SELECT id FROM email_accounts WHERE enabled = 1 \
                 AND (owner = ?1 OR ((owner IS NULL OR owner = '') AND (imap_user = ?1 OR from_address = ?1)))"
                    .to_string(),
                vec![owner.to_string()],
            )
        };
        let mut stmt = conn.prepare(&sql).map_err(|e| EmailErr::Generic(e.to_string()))?;
        let map_row = |r: &rusqlite::Row| -> rusqlite::Result<UrgencyAccount> {
            Ok(UrgencyAccount { id: r.get(0)? })
        };
        let rows = if owner.is_empty() {
            stmt.query_map([], map_row)
        } else {
            stmt.query_map([params[0].as_str()], map_row)
        }
        .map_err(|e| EmailErr::Generic(e.to_string()))?;
        rows.filter_map(|r| r.ok()).collect()
    };
    if accounts.is_empty() {
        return Err(EmailErr::Noop("no email accounts configured".to_string()));
    }

    let urgency_prompt = settings
        .get("urgent_email_prompt")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    // per_uid_scores: key "<acc_id>:<uid>" -> verdict Value.
    let mut per_uid_scores: IndexMap<String, Value> = IndexMap::new();
    let mut all_unread_keys: HashSet<String> = HashSet::new();
    let mut llm_attempts = 0i64;
    let mut saved_classifications = 0i64;
    let mut failed_classifications: Vec<Value> = Vec::new();
    let mut scanned = 0i64;

    // 3. Per-account scan.
    let since_str = age_cutoff.format("%d-%b-%Y").to_string();
    for acc in &accounts {
        let cache_file = cache_dir.join(format!("{}.json", acc.id));
        // cache = {"uids": {...}}.
        let mut cache: serde_json::Map<String, Value> = std::fs::read_to_string(&cache_file)
            .ok()
            .and_then(|t| serde_json::from_str::<Value>(&t).ok())
            .and_then(|v| v.as_object().cloned())
            .unwrap_or_else(|| {
                let mut m = serde_json::Map::new();
                m.insert("uids".to_string(), json!({}));
                m
            });
        let cache_uids_snapshot: serde_json::Map<String, Value> = cache
            .get("uids")
            .and_then(|v| v.as_object().cloned())
            .unwrap_or_default();

        // `_scan_one` — sync IMAP work under spawn_blocking.
        let acc_id = acc.id.clone();
        let since_for_scan = since_str.clone();
        let scan_result: Vec<UrgencyItem> = match tokio::task::spawn_blocking(move || {
            urgency_scan_one(&acc_id, &since_for_scan, TRIAGE_VERSION, &cache_uids_snapshot)
        })
        .await
        {
            Ok(items) => items,
            Err(e) => {
                crate::pylog::warning(&format!("urgency: IMAP scan failed for account {}: {e}", acc.id));
                continue;
            }
        };

        for item in scan_result {
            scanned += 1;
            let key = item.key.clone();
            all_unread_keys.insert(key.clone());
            if let Some(cached) = &item.cached {
                per_uid_scores.insert(key, cached.clone());
                continue;
            }
            // Skip uids we couldn't fetch (no subject/from).
            if item.subject.is_empty() && item.from.is_empty() {
                continue;
            }
            llm_attempts += 1;
            let prompt = format!(
                "You are triaging ONE unread email. Return ONLY JSON: \
{{\"score\":0|1|2|3,\"tags\":[\"...\"],\"spam\":false,\
\"reason\":\"one short phrase\"}}.\n\
0 = trivial / promotional · 1 = informational, no reply needed · \
2 = should reply within a day · 3 = urgent, reply now (deadline, blocker).\n\n\
Allowed tags: newsletter, marketing, notification, finance, bills, receipt, \
travel, security, shopping, social, work, personal, calendar.\n\
Use marketing for ads, promos, sales, offers, and cold sales. Use newsletter \
for newsletters, digests, and recurring content. spam=true for scams, phishing, \
junk, cold sales, generic ads, or no-personal-action bulk mail.\n\
Important: 'I'm outside', 'I am outside', 'waiting outside', 'at the door', \
'locked out', or 'can't get in' means score 3 unless clearly historical.\n\n\
User's rules:\n{urgency_prompt}\n\n\
Email:\nFrom: {}\nSubject: {}\n\
Snippet:\n{}\n",
                item.from, item.subject, item.body
            );

            let raw = match llm_call_async_with_fallback(
                candidates.clone(),
                vec![json!({"role": "user", "content": prompt})],
                0.1,
                220,
                30,
            )
            .await
            {
                Ok(r) => r,
                Err(e) => {
                    failed_classifications.push(json!({
                        "subject": subject_or_default(&item.subject),
                        "from": item.from.clone(),
                        "reason": classify_fail_reason(&e.to_string()),
                    }));
                    crate::pylog::debug(&format!("urgency: LLM classify failed for {key}: {e}"));
                    continue;
                }
            };

            // Tolerant JSON parse: strip code fences.
            let mut txt = raw.trim().to_string();
            if txt.starts_with("```") {
                txt = txt.trim_matches('`').to_string();
                if let Some(nl) = txt.find('\n') {
                    txt = txt[nl + 1..].to_string();
                }
            }
            let s = txt.find('{');
            let e_idx = txt.rfind('}');
            let obj_text = match (s, e_idx) {
                (Some(s), Some(e)) if e > s => txt[s..=e].to_string(),
                _ => {
                    failed_classifications.push(json!({
                        "subject": subject_or_default(&item.subject),
                        "from": item.from.clone(),
                        "reason": "model returned no JSON",
                    }));
                    continue;
                }
            };
            let obj: Value = match serde_json::from_str(&obj_text) {
                Ok(v) => v,
                Err(e) => {
                    failed_classifications.push(json!({
                        "subject": subject_or_default(&item.subject),
                        "from": item.from.clone(),
                        "reason": classify_fail_reason(&e.to_string()),
                    }));
                    continue;
                }
            };

            let mut score = json_to_int(obj.get("score"), 0);
            let mut reason: String = obj
                .get("reason")
                .map(value_to_str)
                .unwrap_or_default()
                .chars()
                .take(200)
                .collect();
            // tags normalization.
            let raw_tags: Vec<String> = match obj.get("tags") {
                Some(Value::Array(arr)) => arr
                    .iter()
                    .filter_map(|t| t.as_str().map(|s| s.to_string()))
                    .collect(),
                Some(Value::String(s)) => vec![s.clone()],
                _ => Vec::new(),
            };
            let mut tags: Vec<String> = Vec::new();
            for t in &raw_tags {
                let mut tag = t.trim().to_lowercase().replace('_', "-");
                if tag == "promo" {
                    tag = "marketing".to_string();
                }
                if category_tags.contains(tag.as_str()) && !tags.contains(&tag) {
                    tags.push(tag);
                }
            }
            // spam coercion.
            let spam = match obj.get("spam") {
                Some(Value::Bool(b)) => *b,
                Some(Value::Number(n)) => n.as_f64().map(|f| f != 0.0).unwrap_or(false),
                Some(v) => {
                    let s = value_to_str(v).trim().to_lowercase();
                    matches!(s.as_str(), "1" | "true" | "yes" | "y")
                }
                None => false,
            };

            // "outside / at the door" regex bump to 3.
            let blob = format!("{}\n{}\n{}", item.headers, item.subject, item.body).to_lowercase();
            if URGENT_OUTSIDE_RE_1.is_match(&blob) || URGENT_OUTSIDE_RE_2.is_match(&blob) {
                if score < 3 {
                    reason = "person is waiting outside".to_string();
                }
                score = score.max(3);
            }
            let bulkish = BULKISH_RE.is_match(&blob);
            let marketingish = MARKETINGISH_RE.is_match(&blob);
            if !tags.iter().any(|t| t == "newsletter") && bulkish {
                tags.push("newsletter".to_string());
            }
            if !tags.iter().any(|t| t == "marketing") && marketingish {
                tags.push("marketing".to_string());
            }
            if (bulkish || marketingish) && score < 2 {
                score = 0;
                if reason.is_empty() || reason.to_lowercase().contains("urgent") {
                    reason = "Bulk marketing/newsletter; no personal reply needed".to_string();
                }
            }
            // Strip "Name <addr>" -> bare display name.
            let from_short = if item.from.contains('<') {
                let head = item.from.split('<').next().unwrap_or("").trim().trim_matches('"').to_string();
                if head.is_empty() { item.from.clone() } else { head }
            } else {
                item.from.clone()
            };

            let verdict = json!({
                "score": score.clamp(0, 3),
                "tags": tags.iter().take(4).cloned().collect::<Vec<_>>(),
                "spam": spam,
                "reason": reason,
                "subject": item.subject.chars().take(200).collect::<String>(),
                "from": from_short.chars().take(120).collect::<String>(),
                "triage_version": TRIAGE_VERSION,
                "message_id": item.message_id.trim(),
                "ts": now_epoch(),
            });
            // cache.setdefault("uids", {})[uid] = verdict.
            if let Some(uids) = cache.get_mut("uids").and_then(|v| v.as_object_mut()) {
                uids.insert(item.uid.clone(), verdict.clone());
            } else {
                let mut m = serde_json::Map::new();
                m.insert(item.uid.clone(), verdict.clone());
                cache.insert("uids".to_string(), Value::Object(m));
            }
            per_uid_scores.insert(key, verdict);
            saved_classifications += 1;
        }

        // Prune cache entries no longer unread. `seen_uids` = all items this scan.
        // We rebuilt items inside the closure, so recompute from the cache snapshot:
        // re-scan returned items list is consumed; track seen uids via the closure
        // result is gone, so re-derive from the keys we processed for this account.
        let seen_uids: HashSet<String> = all_unread_keys
            .iter()
            .filter_map(|k| {
                k.strip_prefix(&format!("{}:", acc.id)).map(|u| u.to_string())
            })
            .collect();
        if let Some(uids) = cache.get_mut("uids").and_then(|v| v.as_object_mut()) {
            let stale: Vec<String> = uids
                .keys()
                .filter(|u| !seen_uids.contains(*u))
                .cloned()
                .collect();
            for u in stale {
                uids.remove(&u);
            }
        }

        if let Ok(serialized) = serde_json::to_string(&Value::Object(cache)) {
            if std::fs::write(&cache_file, serialized).is_err() {
                crate::pylog::warning(&format!("urgency: cache write failed for {}", acc.id));
            }
        }
    }

    // 3.5 Mirror triage verdicts into email_tags.
    {
        eh::init_scheduled_db();
        let db_path = eh::scheduled_db();
        let owner_key = owner.to_string();
        let tag_write = (|| -> rusqlite::Result<()> {
            let conn = rusqlite::Connection::open(&db_path)?;
            for (key, v) in &per_uid_scores {
                let msg_id = v.get("message_id").map(value_to_str).unwrap_or_default().trim().to_string();
                let score = json_to_int(v.get("score"), 0);
                if msg_id.is_empty() {
                    continue;
                }
                let mut new_tags: Vec<String> = Vec::new();
                if score >= 3 {
                    new_tags.push("urgent".to_string());
                } else if score >= 2 {
                    new_tags.push("reply-soon".to_string());
                }
                if let Some(Value::Array(arr)) = v.get("tags") {
                    for t in arr {
                        if let Some(s) = t.as_str() {
                            let mut tag = s.trim().to_lowercase().replace('_', "-");
                            if tag == "promo" {
                                tag = "marketing".to_string();
                            }
                            if category_tags.contains(tag.as_str()) && !new_tags.contains(&tag) {
                                new_tags.push(tag);
                            }
                        }
                    }
                }
                let spam = if v.get("spam").and_then(Value::as_bool).unwrap_or(false) { 1 } else { 0 };
                let uid_only = key.split_once(':').map(|(_, u)| u).unwrap_or(key).to_string();
                let subject = v.get("subject").map(value_to_str).unwrap_or_default();
                let sender = v.get("from").map(value_to_str).unwrap_or_default();
                let reason = v.get("reason").map(value_to_str).unwrap_or_default();

                // `row = SELECT tags FROM email_tags WHERE message_id=? AND owner=?`.
                // `query_row` errs (QueryReturnedNoRows) when absent; map to None.
                let existing_row: Option<Option<String>> = conn
                    .query_row(
                        "SELECT tags FROM email_tags WHERE message_id=?1 AND owner=?2",
                        rusqlite::params![msg_id, owner_key],
                        |r| r.get::<_, Option<String>>(0),
                    )
                    .ok();
                if let Some(existing_tags) = existing_row {
                    // Row exists: drop managed tags, append new, UPDATE.
                    let existing_raw = existing_tags.unwrap_or_else(|| "[]".to_string());
                    let mut existing: Vec<String> = serde_json::from_str::<Value>(&existing_raw)
                        .ok()
                        .and_then(|v| v.as_array().cloned())
                        .map(|arr| {
                            arr.iter()
                                .filter_map(|t| t.as_str().map(|s| s.trim().to_lowercase().replace('_', "-")))
                                .filter(|t| !managed_tags.contains(t.as_str()))
                                .collect()
                        })
                        .unwrap_or_default();
                    for t in &new_tags {
                        if !existing.contains(t) {
                            existing.push(t.clone());
                        }
                    }
                    conn.execute(
                        "UPDATE email_tags SET tags=?1, spam_verdict=?2, spam_reason=?3, uid=?4, folder=?5, subject=?6, sender=?7 \
                         WHERE message_id=?8 AND owner=?9",
                        rusqlite::params![
                            serde_json::to_string(&existing).unwrap_or_else(|_| "[]".to_string()),
                            spam, reason, uid_only, "INBOX", subject, sender, msg_id, owner_key,
                        ],
                    )?;
                } else {
                    // No row: INSERT (only when there is something to record).
                    if new_tags.is_empty() && spam == 0 {
                        continue;
                    }
                    conn.execute(
                        "INSERT INTO email_tags \
                         (message_id, owner, uid, folder, subject, sender, tags, spam_verdict, spam_reason, created_at) \
                         VALUES (?1, ?2, ?3, 'INBOX', ?4, ?5, ?6, ?7, ?8, ?9)",
                        rusqlite::params![
                            msg_id, owner_key, uid_only, subject, sender,
                            serde_json::to_string(&new_tags).unwrap_or_else(|_| "[]".to_string()),
                            spam, reason, utcnow_isoformat(),
                        ],
                    )?;
                }
            }
            Ok(())
        })();
        if let Err(e) = tag_write {
            crate::pylog::warning(&format!("urgency: bulk tag write failed: {e}"));
        }
    }

    // 4. Aggregate. urgent = score >= 2.
    let urgent_keys: Vec<String> = per_uid_scores
        .iter()
        .filter(|(_, v)| json_to_int(v.get("score"), 0) >= 2)
        .map(|(k, _)| k.clone())
        .collect();
    let max_score = per_uid_scores
        .values()
        .map(|v| json_to_int(v.get("score"), 0))
        .max()
        .unwrap_or(0);
    let total_urgent = urgent_keys.len() as i64;

    // Load prior state.
    let prior: Value = std::fs::read_to_string(&state_path)
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or_else(|| json!({}));
    let mut notified_uids: HashSet<String> = prior
        .get("notified_uids")
        .and_then(|v| v.as_array())
        .map(|arr| arr.iter().filter_map(|x| x.as_str().map(|s| s.to_string())).collect())
        .unwrap_or_default();

    // 5. Fire reminder ONLY for previously-unnotified urgent UIDs.
    let new_urgent: Vec<String> = urgent_keys
        .iter()
        .filter(|k| !notified_uids.contains(*k))
        .cloned()
        .collect();
    let mut newly_notified: HashSet<String> = HashSet::new();
    let mut notify_failed: HashSet<String> = HashSet::new();
    if !new_urgent.is_empty() {
        let title = if total_urgent == 1 {
            "Urgent email".to_string()
        } else {
            format!("{total_urgent} urgent emails")
        };
        // sorted_urgent: highest-scored first, cap 10.
        let mut sorted_urgent: Vec<(String, Value)> = urgent_keys
            .iter()
            .map(|k| (k.clone(), per_uid_scores.get(k).cloned().unwrap_or(Value::Null)))
            .collect();
        sorted_urgent.sort_by(|a, b| {
            json_to_int(b.1.get("score"), 0).cmp(&json_to_int(a.1.get("score"), 0))
        });
        sorted_urgent.truncate(10);
        let pub_url = settings
            .get("app_public_url")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim()
            .trim_end_matches('/')
            .to_string();
        let plural = if total_urgent == 1 { "" } else { "s" };
        let mut lines: Vec<String> =
            vec![format!("{total_urgent} email{plural} need an urgent reply:"), String::new()];
        for (i, (k, v)) in sorted_urgent.iter().enumerate() {
            let subj: String = {
                let s = v.get("subject").map(value_to_str).unwrap_or_default();
                let s = if s.is_empty() { "(no subject)".to_string() } else { s };
                s.chars().take(160).collect()
            };
            let frm = v.get("from").map(value_to_str).unwrap_or_default();
            let why = v.get("reason").map(value_to_str).unwrap_or_default();
            let uid_for_link = k.split_once(':').map(|(_, u)| u).unwrap_or(k);
            let hash_link = format!("#email={}:{}", url_quote("INBOX"), uid_for_link);
            let open_link = if pub_url.is_empty() {
                hash_link.clone()
            } else {
                format!("{pub_url}/{hash_link}")
            };
            let mut line = format!("{}. {}", i + 1, subj);
            if !frm.is_empty() {
                line.push_str(&format!("  —  {frm}"));
            }
            if !why.is_empty() {
                line.push_str(&format!("  ·  {why}"));
            }
            lines.push(line);
            lines.push(format!("   Open email: {open_link}"));
        }
        if total_urgent > sorted_urgent.len() as i64 {
            lines.push(String::new());
            lines.push(format!("…and {} more.", total_urgent - sorted_urgent.len() as i64));
        }
        let body = lines.join("\n");

        // dispatch_reminder DIRECTLY. FLAG: scheduler=None -> browser_sent false.
        let dispatch_result = crate::routes::note_routes::dispatch_reminder(
            &title,
            &body,
            "urgent-email",
            owner,
            true,
            None,
        )
        .await;
        let channel = settings
            .get("reminder_channel")
            .and_then(Value::as_str)
            .unwrap_or("browser")
            .trim()
            .to_lowercase();
        let delivered = match channel.as_str() {
            "email" => dispatch_result.get("email_sent").and_then(Value::as_bool).unwrap_or(false),
            "ntfy" => dispatch_result.get("ntfy_sent").and_then(Value::as_bool).unwrap_or(false),
            _ => dispatch_result.get("browser_sent").and_then(Value::as_bool).unwrap_or(false),
        };
        if delivered {
            for k in &new_urgent {
                newly_notified.insert(k.clone());
            }
        } else {
            for k in &new_urgent {
                notify_failed.insert(k.clone());
            }
            crate::pylog::warning(&format!(
                "urgency: reminder dispatch returned no successful delivery path: {dispatch_result}"
            ));
        }
        for k in &newly_notified {
            notified_uids.insert(k.clone());
        }
    }

    // Prune notified_uids no longer unread.
    notified_uids.retain(|u| all_unread_keys.contains(u));

    let mut sorted_notified: Vec<String> = notified_uids.iter().cloned().collect();
    sorted_notified.sort();
    let state = json!({
        "ts": now_epoch(),
        "owner": owner,
        "total_unread": all_unread_keys.len(),
        "total_urgent": total_urgent,
        "max_score": max_score,
        "per_uid": Value::Object(per_uid_scores.iter().map(|(k, v)| (k.clone(), v.clone())).collect()),
        "notified_uids": sorted_notified,
    });
    if let Ok(serialized) = serde_json::to_string(&state) {
        if std::fs::write(&state_path, serialized).is_err() {
            crate::pylog::warning("urgency: state write failed");
        }
    }

    // 6. Activity-log summary.
    let mut tier_counts: [i64; 4] = [0, 0, 0, 0];
    for v in per_uid_scores.values() {
        let s = json_to_int(v.get("score"), 0).clamp(0, 3) as usize;
        tier_counts[s] += 1;
    }
    if scanned == 0 {
        return Err(EmailErr::Noop("no unread emails in last 7 days".to_string()));
    }
    let mut head = format!(
        "scanned {scanned} · urgent {} · reply-soon {} · info {} · trivial {} · {saved_classifications} saved classifications",
        tier_counts[3], tier_counts[2], tier_counts[1], tier_counts[0]
    );
    if llm_attempts != saved_classifications {
        head.push_str(&format!(" · {} failed", llm_attempts - saved_classifications));
    }
    if !newly_notified.is_empty() {
        head.push_str(&format!(" · notified {}", newly_notified.len()));
    }
    if !notify_failed.is_empty() {
        head.push_str(&format!(" · notify failed {}", notify_failed.len()));
    }

    let fmt_one = |v: &Value, key: &str| -> String {
        let subj: String = {
            let s = v.get("subject").map(value_to_str).unwrap_or_default();
            let s = if s.is_empty() { "(no subject)".to_string() } else { s };
            s.chars().take(80).collect()
        };
        let frm = v.get("from").map(value_to_str).unwrap_or_default();
        let why = v.get("reason").map(value_to_str).unwrap_or_default();
        let tag = if newly_notified.contains(key) {
            " · *notified now*"
        } else if notify_failed.contains(key) {
            " · *notify failed*"
        } else {
            ""
        };
        let mut line = format!("- **{subj}**");
        if !frm.is_empty() {
            line.push_str(&format!(" — _{frm}_"));
        }
        if !why.is_empty() {
            line.push_str(&format!(" — {why}"));
        }
        line.push_str(tag);
        line
    };

    // by_tier in insertion order.
    let mut by_tier: std::collections::HashMap<i64, Vec<(String, Value)>> = std::collections::HashMap::new();
    for (k, v) in &per_uid_scores {
        let s = json_to_int(v.get("score"), 0);
        by_tier.entry(s).or_default().push((k.clone(), v.clone()));
    }
    let mut lines = vec![head];
    let tier_labels = [(3, "Urgent"), (2, "Reply soon"), (1, "Informational"), (0, "Trivial")];
    for (tier, label) in tier_labels {
        let items_t = match by_tier.get(&tier) {
            Some(v) if !v.is_empty() => v,
            _ => continue,
        };
        lines.push(String::new());
        lines.push(format!("**{label} ({}):**", items_t.len()));
        for (k, v) in items_t.iter().take(8) {
            lines.push(fmt_one(v, k));
        }
        if items_t.len() > 8 {
            lines.push(format!("…and {} more", items_t.len() - 8));
        }
    }
    if !failed_classifications.is_empty() {
        lines.push(String::new());
        lines.push(format!("**Unclassified ({}):**", failed_classifications.len()));
        for v in failed_classifications.iter().take(8) {
            let subj: String = {
                let s = v.get("subject").map(value_to_str).unwrap_or_default();
                let s = if s.is_empty() { "(no subject)".to_string() } else { s };
                s.chars().take(80).collect()
            };
            let frm = v.get("from").map(value_to_str).unwrap_or_default();
            let why = v.get("reason").map(value_to_str).unwrap_or_default();
            let mut line = format!("- **{subj}**");
            if !frm.is_empty() {
                line.push_str(&format!(" — _{frm}_"));
            }
            if !why.is_empty() {
                line.push_str(&format!(" — {why}"));
            }
            lines.push(line);
        }
        if failed_classifications.len() > 8 {
            lines.push(format!("…and {} more", failed_classifications.len() - 8));
        }
    }
    Ok((lines.join("\n"), true))
}

/// The synchronous per-account IMAP scan (`_scan_one`). Selects INBOX readonly,
/// searches `(UNSEEN SINCE <since>)`, and for each new (uncached) sequence number
/// fetches headers + the first ~800 chars of plaintext body, dropping
/// Odysseus-generated reminders. Runs under `spawn_blocking`.
fn urgency_scan_one(
    account_id: &str,
    since_str: &str,
    triage_version: i64,
    cache_uids: &serde_json::Map<String, Value>,
) -> Vec<UrgencyItem> {
    use crate::routes::email_helpers as eh;
    let mut results: Vec<UrgencyItem> = Vec::new();
    let mut conn = match eh::imap_connect(Some(account_id), "") {
        Ok(c) => c,
        Err(_) => return results,
    };
    let _ = (|| -> Result<(), String> {
        conn.examine("INBOX").map_err(|e| e.to_string())?;
        // `(UNSEEN SINCE DD-Mon-YYYY)`.
        let found = conn
            .search(format!("UNSEEN SINCE {since_str}"))
            .map_err(|e| e.to_string())?;
        let mut seqs: Vec<u32> = found.into_iter().collect();
        seqs.sort_unstable();
        for seq in seqs {
            let uid = seq.to_string();
            let key = format!("{account_id}:{uid}");
            let cached = cache_uids.get(&uid);
            let cached_ok = cached
                .and_then(|v| v.as_object())
                .and_then(|o| o.get("triage_version"))
                .and_then(|v| v.as_i64())
                .map(|tv| tv == triage_version)
                .unwrap_or(false);
            if cached_ok {
                results.push(UrgencyItem {
                    key,
                    uid,
                    cached: cached.cloned(),
                    subject: String::new(),
                    from: String::new(),
                    headers: String::new(),
                    body: String::new(),
                    message_id: String::new(),
                });
                continue;
            }
            // Fetch headers + first ~800 chars of body.
            let fetched = match conn.fetch(&uid, "(RFC822.HEADER BODY.PEEK[TEXT]<0.800>)") {
                Ok(f) => f,
                Err(e) => {
                    crate::pylog::debug(&format!("urgency: header fetch for uid {uid} failed: {e}"));
                    continue;
                }
            };
            // Concatenate header + text bytes (Python concatenates all parts).
            let mut raw: Vec<u8> = Vec::new();
            for f in fetched.iter() {
                if let Some(h) = f.header() {
                    raw.extend_from_slice(h);
                    raw.extend_from_slice(b"\n\n");
                }
                if let Some(t) = f.text() {
                    raw.extend_from_slice(t);
                    raw.extend_from_slice(b"\n\n");
                }
            }
            if raw.is_empty() {
                continue;
            }
            let msg = match eh::parse_message(&raw) {
                Some(m) => m,
                None => continue,
            };
            // Drop Odysseus-generated reminders.
            let ody_origin = msg.header("X-Odysseus-Origin").unwrap_or_default().trim().to_lowercase();
            let ody_kind = msg.header("X-Odysseus-Kind").unwrap_or_default().trim().to_lowercase();
            let raw_subj = msg.header("Subject").unwrap_or_default().to_lowercase();
            if ody_origin == "odysseus-ui"
                || ody_kind == "reminder"
                || raw_subj.starts_with("reminder (odysseus):")
                || raw_subj.starts_with("reminder:")
                || raw_subj.starts_with("[task]")
            {
                continue;
            }
            let subject = eh::decode_header(&msg.header("Subject").unwrap_or_default());
            let from_raw = eh::decode_header(&msg.header("From").unwrap_or_default());
            // header_blob: "Name: value" for the bulk-detection headers present.
            let header_names = [
                "From", "Subject", "List-Unsubscribe", "List-ID", "Precedence",
                "X-Mailchimp-Campaign-Id", "X-Campaign", "X-MC-User",
            ];
            let header_blob = header_names
                .iter()
                .filter_map(|name| {
                    msg.header(name).filter(|v| !v.is_empty()).map(|v| format!("{name}: {v}"))
                })
                .collect::<Vec<_>>()
                .join("\n");
            // body_snippet: first text/plain part (or single part), first 1600 chars.
            let body_snippet = urgency_body_snippet(&msg);
            results.push(UrgencyItem {
                key,
                uid,
                cached: None,
                subject,
                from: from_raw,
                headers: header_blob,
                body: body_snippet.trim().to_string(),
                message_id: msg.header("Message-ID").unwrap_or_default().trim().to_string(),
            });
        }
        Ok(())
    })();
    let _ = conn.logout();
    results
}

/// Extract the body snippet (first 1600 chars), mirroring the Python:
/// multipart -> the FIRST `text/plain` part's decoded text; single-part -> the
/// decoded payload. Any decode failure -> "".
fn urgency_body_snippet(msg: &crate::routes::email_helpers::ParsedMessage) -> String {
    use mail_parser::MimeHeaders;
    let m = msg.message();
    if msg.is_multipart() {
        for part in &m.parts {
            let is_plain = part
                .content_type()
                .map(|ct| {
                    ct.ctype().eq_ignore_ascii_case("text")
                        && ct.subtype().map(|s| s.eq_ignore_ascii_case("plain")).unwrap_or(false)
                })
                .unwrap_or(false);
            if is_plain {
                if let Some(txt) = part.text_contents() {
                    return txt.chars().take(1600).collect();
                }
                return String::new();
            }
        }
        String::new()
    } else {
        // `msg.get_payload(decode=True)` — the single part's text/raw contents.
        for part in &m.parts {
            if let Some(txt) = part.text_contents() {
                return txt.chars().take(1600).collect();
            }
        }
        String::new()
    }
}

/// `urllib.parse.quote(s, safe='')` — percent-encode everything except the
/// RFC3986 unreserved set (`A-Za-z0-9-._~`). Used for the email deep-link mailbox.
fn url_quote(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        if b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.' | b'~') {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

/// `datetime.utcnow().isoformat()` — `T`-separated, microseconds only when nonzero.
fn utcnow_isoformat() -> String {
    naive_isoformat(crate::pydatetime::utcnow_naive())
}

/// `dt.isoformat()` for a naive datetime — `T` separator, microseconds dropped
/// only when zero (Python `datetime.isoformat()` semantics).
fn naive_isoformat(dt: chrono::NaiveDateTime) -> String {
    use chrono::Timelike;
    if dt.nanosecond() == 0 {
        dt.format("%Y-%m-%dT%H:%M:%S").to_string()
    } else {
        dt.format("%Y-%m-%dT%H:%M:%S%.6f").to_string()
    }
}

/// `item.get("subject") or "(no subject)"` for the failed-classification entry.
fn subject_or_default(subject: &str) -> String {
    if subject.is_empty() {
        "(no subject)".to_string()
    } else {
        subject.to_string()
    }
}

/// `str(e)[:120] or "classification failed"` — the failed-classification reason.
fn classify_fail_reason(err: &str) -> String {
    let m: String = err.chars().take(120).collect();
    if m.is_empty() {
        "classification failed".to_string()
    } else {
        m
    }
}

/// `time.time()` — Unix epoch seconds as a float.
fn now_epoch() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// `int(x.get(key, default))` over a JSON value — number, numeric string, or
/// the default for anything else (matching the Python tolerant `int(...)`).
fn json_to_int(v: Option<&Value>, default: i64) -> i64 {
    match v {
        Some(Value::Number(n)) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)).unwrap_or(default),
        Some(Value::String(s)) => s.trim().parse::<i64>().ok().or_else(|| {
            s.trim().parse::<f64>().ok().map(|f| f as i64)
        }).unwrap_or(default),
        Some(Value::Bool(b)) => i64::from(*b),
        _ => default,
    }
}

/// `str(v)` for the reason/subject/from fields — a JSON string yields its inner
/// text; any other value is rendered as its JSON form (the Python `str(...)`).
fn value_to_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Null => String::new(),
        other => other.to_string(),
    }
}

/// `parseaddr(s)` -> `(display_name, email_address)`. Mirrors
/// `email.utils.parseaddr`: extracts the `<addr>` half (or the bare token) and
/// the display name. A local helper (the route-layer copies are module-private).
fn parseaddr(s: &str) -> (String, String) {
    let s = s.trim();
    if let Some(lt) = s.find('<') {
        if let Some(gt) = s[lt..].find('>') {
            let addr = s[lt + 1..lt + gt].trim().to_string();
            let name = s[..lt].trim().trim_matches('"').to_string();
            return (name, addr);
        }
    }
    // No angle brackets: the whole token is the address (Python returns ("", s)
    // when it looks like a bare address).
    (String::new(), s.to_string())
}

/// `re.search(r"\b(i'?m|i am|im|we'?re|we are)\s+outside\b", blob)`.
static URGENT_OUTSIDE_RE_1: Lazy<regex::Regex> =
    Lazy::new(|| regex::Regex::new(r"\b(i'?m|i am|im|we'?re|we are)\s+outside\b").unwrap());
/// `re.search(r"\b(waiting outside|at the door|locked out|can'?t get in|cannot get in)\b", blob)`.
static URGENT_OUTSIDE_RE_2: Lazy<regex::Regex> = Lazy::new(|| {
    regex::Regex::new(r"\b(waiting outside|at the door|locked out|can'?t get in|cannot get in)\b").unwrap()
});
/// The bulk-mail regex.
static BULKISH_RE: Lazy<regex::Regex> = Lazy::new(|| {
    regex::Regex::new(
        r"\b(list-unsubscribe|list-id|mailchimp|mailchimpapp|view this email in your browser|unsubscribe|newsletter|digest|precedence:\s*bulk)\b",
    )
    .unwrap()
});
/// The marketing regex.
static MARKETINGISH_RE: Lazy<regex::Regex> = Lazy::new(|| {
    regex::Regex::new(
        r"\b(advertisement|sponsored|promo|promotion|sale|discount|offer|limited time|deal|tickets?|tour|merch|stream|purchase|sold out|low tickets|coupon|shop now|buy now)\b",
    )
    .unwrap()
});

// ---------------------------------------------------------------------------
// Shared subprocess runner + the three command actions (PORT_NOW).
// ---------------------------------------------------------------------------

/// Shared subprocess runner. `subprocess.run(argv, shell=..., capture_output=
/// True, text=True, timeout=...)` -> a blocking `std::process::Command` on a
/// `spawn_blocking` thread so the async runtime stays responsive.
///
/// `shell=True` in Python with a *string* command runs it via `/bin/sh -c`; the
/// Rust port mirrors that for the script actions (which pass a single string),
/// while the list-argv calls (`bash -c`, `ssh ...`) run the program directly.
async fn run_subprocess(
    argv: Vec<String>,
    shell: bool,
    timeout: u64,
    label: &str,
) -> (String, bool) {
    let label = label.to_string();
    let res = tokio::task::spawn_blocking(move || run_subprocess_blocking(argv, shell, timeout, &label))
        .await;
    match res {
        Ok(t) => t,
        // spawn_blocking JoinError (panic) — surface as a failure tuple.
        Err(e) => (e.to_string(), false),
    }
}

/// The blocking body of `run_subprocess`, isolated so it can be unit-tested
/// directly without an async runtime.
fn run_subprocess_blocking(argv: Vec<String>, shell: bool, timeout: u64, label: &str) -> (String, bool) {
    use std::process::Command;
    use std::time::{Duration, Instant};

    if argv.is_empty() {
        return ("(no output)".to_string(), true);
    }

    // Build the command. `shell=True` with a single string -> `/bin/sh -c "<str>"`.
    let mut cmd = if shell {
        let joined = argv.join(" ");
        let mut c = Command::new("/bin/sh");
        c.arg("-c").arg(joined);
        c
    } else {
        let mut c = Command::new(&argv[0]);
        for a in &argv[1..] {
            c.arg(a);
        }
        c
    };
    cmd.stdout(std::process::Stdio::piped());
    cmd.stderr(std::process::Stdio::piped());

    // `subprocess.run(timeout=...)` raises TimeoutExpired -> "<label> timed
    // out (<timeout>s)". Spawn the child and poll for completion with a wall
    // clock budget so the blocking thread can enforce the timeout.
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => return (e.to_string(), false),
    };
    let deadline = Instant::now() + Duration::from_secs(timeout);
    loop {
        match child.try_wait() {
            Ok(Some(_status)) => break,
            Ok(None) => {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    return (format!("{label} timed out ({timeout}s)"), false);
                }
                std::thread::sleep(Duration::from_millis(50));
            }
            Err(e) => return (e.to_string(), false),
        }
    }
    let output = match child.wait_with_output() {
        Ok(o) => o,
        Err(e) => return (e.to_string(), false),
    };

    // output = (result.stdout or "").strip()
    let mut text = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(&output.stderr);
    let success = output.status.success();
    // if result.returncode != 0 and result.stderr: output += "\nSTDERR: " + ...
    if !success && !stderr.trim().is_empty() {
        text.push_str("\nSTDERR: ");
        text.push_str(stderr.trim());
    }
    // return output or "(no output)", returncode == 0
    let out = if text.is_empty() { "(no output)".to_string() } else { text };
    (out, success)
}

/// `action_ssh_command` — run a shell command locally or on a remote host via
/// SSH. `kwargs`: `command` (str), `host` (str, default "localhost").
pub async fn action_ssh_command(_owner: &str, kwargs: &Value) -> ActionResult {
    let command = kwarg_str(kwargs, "command", "");
    let host = kwarg_str(kwargs, "host", "localhost");
    // if not command: return "No command specified", False
    if command.is_empty() {
        return Ok(("No command specified".to_string(), false));
    }
    if host == "localhost" || host == "127.0.0.1" || host == "local" {
        let r = run_subprocess(
            vec!["bash".to_string(), "-c".to_string(), command],
            false,
            120,
            "Command",
        )
        .await;
        return Ok(r);
    }
    let r = run_subprocess(
        vec![
            "ssh".to_string(),
            "-o".to_string(),
            "ConnectTimeout=10".to_string(),
            host,
            command,
        ],
        false,
        120,
        "Command",
    )
    .await;
    Ok(r)
}

/// `action_run_script` — run a script locally, or via SSH when a host is
/// configured (`kwargs`: `script`, `host`; `host` defaults to
/// `ODYSSEUS_SCRIPT_HOST` or "localhost").
pub async fn action_run_script(_owner: &str, kwargs: &Value) -> ActionResult {
    let script = kwarg_str(kwargs, "script", "");
    // if not script: return "No script specified", False
    if script.is_empty() {
        return Ok(("No script specified".to_string(), false));
    }
    let host_kw = kwarg_str(kwargs, "host", "");
    // target_host = (host or os.getenv("ODYSSEUS_SCRIPT_HOST", "localhost")).strip()
    let target_host = if host_kw.is_empty() {
        os::getenv("ODYSSEUS_SCRIPT_HOST", "localhost")
    } else {
        host_kw
    };
    let target_host = target_host.trim().to_string();
    if target_host.is_empty()
        || target_host == "localhost"
        || target_host == "127.0.0.1"
        || target_host == "local"
    {
        let r = run_subprocess(vec![script], true, 300, "Script").await;
        return Ok(r);
    }
    let r = run_subprocess(vec!["ssh".to_string(), target_host, script], false, 300, "Script").await;
    Ok(r)
}

/// `action_run_local` — run a script locally (no SSH). `kwargs`: `script`.
pub async fn action_run_local(_owner: &str, kwargs: &Value) -> ActionResult {
    let script = kwarg_str(kwargs, "script", "");
    if script.is_empty() {
        return Ok(("No script specified".to_string(), false));
    }
    let r = run_subprocess(vec![script], true, 300, "Script").await;
    Ok(r)
}

// ---------------------------------------------------------------------------
// action_tidy_research (PORT_NOW — fs glob + JSON validate).
// ---------------------------------------------------------------------------

/// `action_tidy_research` — remove only broken research files (empty or
/// unparseable JSON) under `data/deep_research/`.
///
/// Faithful to the Python: research history is *not* backed by chat-session
/// rows, so a file is deleted only when it fails to load/parse — never just
/// because no session matches.
///
/// DOCUMENTED PATH NOTE: Python hard-codes the source-relative `Path("data/
/// deep_research")`. The Rust port also uses the literal `data/deep_research`
/// (relative to the process CWD) to match that behaviour, NOT the
/// ODYSSEUS_DATA_DIR-honoring `crate::core::constants::DATA_DIR`.
pub async fn action_tidy_research(_owner: &str, _kwargs: &Value) -> ActionResult {
    use std::path::Path;
    let research_dir = Path::new("data/deep_research");
    // if not research_dir.exists(): raise TaskNoop("no research directory")
    if !research_dir.exists() {
        return Err(Outcome::noop("no research directory"));
    }
    // files = list(research_dir.glob("*.json"))
    let mut files: Vec<std::path::PathBuf> = Vec::new();
    if let Ok(rd) = std::fs::read_dir(research_dir) {
        for entry in rd.flatten() {
            let p = entry.path();
            if p.extension().and_then(|e| e.to_str()) == Some("json") {
                files.push(p);
            }
        }
    }
    let mut removed: Vec<String> = Vec::new();
    for p in &files {
        // valid JSON (non-empty) -> keep; else unlink + record stem[:8]
        let broken = match std::fs::read_to_string(p) {
            Ok(raw) => {
                let txt = raw.trim();
                txt.is_empty() || serde_json::from_str::<Value>(txt).is_err()
            }
            Err(_) => true,
        };
        if broken {
            let _ = std::fs::remove_file(p);
            let stem = p
                .file_stem()
                .and_then(|s| s.to_str())
                .unwrap_or("")
                .chars()
                .take(8)
                .collect::<String>();
            removed.push(stem);
        }
    }
    if removed.is_empty() {
        return Err(Outcome::noop(format!(
            "scanned {} research file(s), none broken",
            files.len()
        )));
    }
    Ok((
        format!("Removed {} broken research file(s) of {}", removed.len(), files.len()),
        true,
    ))
}

// ---------------------------------------------------------------------------
// _result_has_work (PORT_NOW — non-overlapping str.count parity).
// ---------------------------------------------------------------------------

/// Heuristic: did the email pass actually process anything?
///
/// `_run_auto_summarize_once` returns strings like 'Processed 0 emails',
/// 'No new emails to summarize', 'Tagged 0 / Moved 0', etc. when nothing was
/// done. `low.count(" 0")` is Python's *non-overlapping* substring count.
pub fn _result_has_work(result: Option<&str>) -> bool {
    let result = match result {
        Some(r) if !r.is_empty() => r,
        _ => return false,
    };
    let low = result.to_lowercase();
    if low.contains("processed 0") || low.contains("no new") || low.contains("nothing to") {
        return false;
    }
    // "Tagged 0 / Moved 0" or similar zero-count summaries
    if count_non_overlapping(&low, " 0") >= 2
        && (low.contains("tagged") || low.contains("moved") || low.contains("drafted"))
    {
        return false;
    }
    true
}

/// Python `str.count(sub)` — non-overlapping occurrences of `needle` in `hay`.
fn count_non_overlapping(hay: &str, needle: &str) -> usize {
    if needle.is_empty() {
        // CPython: an empty substring counts len+1, but we never call it so.
        return hay.chars().count() + 1;
    }
    let mut count = 0usize;
    let mut start = 0usize;
    while let Some(pos) = hay[start..].find(needle) {
        count += 1;
        start += pos + needle.len();
    }
    count
}

// ---------------------------------------------------------------------------
// Event-classification heuristic tables (PORT_NOW — Lazy IndexMap, dict order).
// ---------------------------------------------------------------------------

/// `_TYPE_COLORS` — event type -> hex color. IndexMap preserves the Python dict
/// literal order (work, personal, health, travel, meal, social, admin, other).
pub static TYPE_COLORS: Lazy<IndexMap<&'static str, &'static str>> = Lazy::new(|| {
    let mut m = IndexMap::new();
    m.insert("work", "#5b8abf");
    m.insert("personal", "#a07ae0");
    m.insert("health", "#e06c75");
    m.insert("travel", "#e5a33a");
    m.insert("meal", "#d8b974");
    m.insert("social", "#82c882");
    m.insert("admin", "#888888");
    m.insert("other", "#6b9cb5");
    m
});

/// `_HEURISTIC_TYPES` — type -> keyword list. IndexMap preserves the Python dict
/// literal order (health, travel, meal, social, admin, work) so the
/// first-match-wins loop in `_classify_event_heuristic` is faithful.
static HEURISTIC_TYPES: Lazy<IndexMap<&'static str, Vec<&'static str>>> = Lazy::new(|| {
    let mut m = IndexMap::new();
    m.insert(
        "health",
        vec![
            "doctor", "dentist", "clinic", "hospital", "appointment", "checkup", "therapy",
            "physio", "chiropract", "vaccine", "blood test", "xray", "scan", "surgery",
        ],
    );
    m.insert(
        "travel",
        vec![
            "flight", "airport", "train", "shinkansen", "boarding", "uber", "taxi", "trip",
            "hotel", "airbnb", "depart", "arrival", "check-in", "checkout",
        ],
    );
    m.insert(
        "meal",
        vec![
            "lunch", "dinner", "breakfast", "brunch", "coffee", "drinks", "restaurant",
            "reservation", "bar", "cafe",
        ],
    );
    m.insert(
        "social",
        vec![
            "birthday", "party", "hangout", "wedding", "date with", "drinks with", "anniversary",
            "baby shower", "graduation", "picnic", "bbq",
        ],
    );
    m.insert(
        "admin",
        vec![
            "bill", "renewal", "tax", "deadline", "filing", "submit", "due date", "registration",
            "license", "passport", "visa", "form",
        ],
    );
    m.insert(
        "work",
        vec![
            "meeting", "standup", "sync", "1:1", "1on1", "review", "interview", "demo",
            "presentation", "kickoff", "retro", "all-hands", "town hall", "call with", "client",
            "deck",
        ],
    );
    m
});

/// `_HEURISTIC_HIGH`.
static HEURISTIC_HIGH: &[&str] = &[
    "flight", "interview", "wedding", "surgery", "exam", "deadline", "court", "presentation",
    "demo", "kickoff", "launch",
];
/// `_HEURISTIC_CRITICAL`.
static HEURISTIC_CRITICAL: &[&str] =
    &["surgery", "court", "wedding day", "funeral", "delivery date"];

/// `_classify_event_heuristic(summary)` — returns `(event_type, importance)` or
/// `(None, None)` if unclear. `etype` is the first matching `_HEURISTIC_TYPES`
/// key; `importance` is critical/high or `None`.
pub fn _classify_event_heuristic(summary: &str) -> (Option<&'static str>, Option<&'static str>) {
    let s = summary.to_lowercase();
    let mut etype: Option<&'static str> = None;
    for (t, kws) in HEURISTIC_TYPES.iter() {
        if kws.iter().any(|k| s.contains(k)) {
            etype = Some(*t);
            break;
        }
    }
    if HEURISTIC_CRITICAL.iter().any(|k| s.contains(k)) {
        return (etype, Some("critical"));
    }
    if HEURISTIC_HIGH.iter().any(|k| s.contains(k)) {
        return (etype, Some("high"));
    }
    (etype, None)
}

// ---------------------------------------------------------------------------
// action_consolidate_memory (PORT_VIA_DB+web — memory.rs + LLM tidy).
// ---------------------------------------------------------------------------

/// `re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE)` — strip
/// a leading fenced ```json marker and a trailing ``` (multiline).
static CODE_FENCE_RE: Lazy<regex::Regex> =
    Lazy::new(|| regex::Regex::new(r"(?m)^```(?:json)?\s*|\s*```$").unwrap());

/// `action_consolidate_memory` — consolidate/deduplicate memories for the owner.
///
/// PORT_VIA_DB+web: loads via `memory::MemoryManager` (the JSON-file store),
/// runs an optional LLM tidy pass (`endpoint_resolver::resolve_endpoint_triple`,
/// owner-threaded), and falls back to the exact lower-cased-whitespace dedupe.
///
/// Memories are processed PER OWNER GROUP (builtin_actions.py:88-98): when an
/// owner is set, the single group is the memories whose `owner` matches exactly;
/// when no owner is given (built-in housekeeping), the memories are grouped by
/// their own `owner` field and each group is tidied independently, with the
/// per-group endpoint resolved for that group's owner. The AI-tidy prompt caps
/// each memory at 2000 chars WITH truncation protection — a memory the model
/// only saw truncated is never deleted or rewritten. The total removed/cleaned/
/// scanned counts (and the dedupe `removed_examples`) are AGGREGATED across the
/// groups before the single save + summary line.
pub async fn action_consolidate_memory(owner: &str, _kwargs: &Value) -> ActionResult {
    match consolidate_memory_inner(owner).await {
        Ok(t) => Ok(t),
        // The Python `raise TaskNoop` propagates past the `except Exception`.
        Err(ConsolidateErr::Noop(reason)) => Err(Outcome::noop(reason)),
        // `except Exception as e: return str(e), False`.
        Err(ConsolidateErr::Generic(msg)) => {
            crate::pylog::error(&format!("consolidate_memory action failed: {msg}"));
            Ok((msg, false))
        }
    }
}

enum ConsolidateErr {
    Noop(String),
    Generic(String),
}

/// The per-memory `owner` key a group is built on: `(mem.get("owner") or "").strip()`
/// (builtin_actions.py:84-85). NOTE: this is exact — unlike the previous Rust port,
/// an owner-set run does NOT fold in empty-owner memories (Python groups on `==`).
fn memory_owner(mem: &Value) -> String {
    mem.get("owner").and_then(|v| v.as_str()).unwrap_or("").trim().to_string()
}

/// Per-item char cap for the AI-tidy prompt (`text_limit`, builtin_actions.py:82).
const MEMORY_TEXT_LIMIT: usize = 2000;

/// Partition `all_memories` into the per-owner groups
/// (`owner -> [indices]`), dropping empties (builtin_actions.py:88-98):
///   * `owner_clean` non-empty -> a single group of the EXACT-owner matches;
///   * `owner_clean` empty -> every memory grouped by its own `memory_owner()`.
/// IndexMap preserves the Python dict insertion order (first-seen owner first).
fn build_memory_groups(all_memories: &[Value], owner_clean: &str) -> IndexMap<String, Vec<usize>> {
    let mut groups: IndexMap<String, Vec<usize>> = IndexMap::new();
    if !owner_clean.is_empty() {
        let idxs: Vec<usize> = all_memories
            .iter()
            .enumerate()
            .filter(|(_, m)| memory_owner(m) == owner_clean)
            .map(|(i, _)| i)
            .collect();
        if !idxs.is_empty() {
            groups.insert(owner_clean.to_string(), idxs);
        }
    } else {
        for (i, m) in all_memories.iter().enumerate() {
            groups.entry(memory_owner(m)).or_default().push(i);
        }
        groups.retain(|_, v| !v.is_empty());
    }
    groups
}

/// `" ".join(text.lower().split())` — the whitespace-normalized dedupe key for a
/// memory's text (builtin_actions.py:237). Empty input -> empty key.
fn dedupe_key(text: &str) -> String {
    text.to_lowercase().split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Accumulators threaded across the per-owner groups (the Python `nonlocal`
/// `total_*` / `removed_examples` / `ai_reasons` / `ai_used` closure state).
#[derive(Default)]
struct ConsolidateAccum {
    total_removed: i64,
    total_cleaned: i64,
    total_scanned: i64,
    removed_examples: Vec<String>,
    ai_reasons: Vec<String>,
    ai_used: bool,
}

/// The full consolidation body, lifted out so the `TaskNoop` vs generic-exception
/// split maps cleanly onto `Result`.
///
/// Faithful to the per-owner grouping in builtin_actions.py:88-274: an owner-set
/// run yields a single exact-match group; a no-owner (housekeeping) run groups by
/// each memory's own `owner`. Each group is AI-tidied (or dedupe-cleaned on
/// fall-through) and the counts are aggregated before the single save + summary.
async fn consolidate_memory_inner(owner: &str) -> Result<(String, bool), ConsolidateErr> {
    use crate::src::endpoint_resolver::resolve_endpoint_triple;
    use crate::src::memory::MemoryManager;

    let manager = MemoryManager::new(&crate::core::constants::DATA_DIR)
        .map_err(|e| ConsolidateErr::Generic(e.to_string()))?;
    // Mutable: per-group rebuilds replace it, exactly like Python `all_memories`.
    let mut all_memories = manager.load_all();

    let owner_clean = owner.trim().to_string();

    // Build the owner -> [indices] groups, dropping empties (py:88-98).
    let memory_groups = build_memory_groups(&all_memories, &owner_clean);
    if memory_groups.is_empty() {
        return Err(ConsolidateErr::Noop("no memories to consolidate".to_string()));
    }

    let mut acc = ConsolidateAccum::default();

    // Process the groups in their first-seen owner order. A prior group's AI tidy
    // rebuilds `all_memories` (dropping only THAT group's members), so we re-scan
    // each group's current indices by owner just before processing it — exact
    // because a partition by `memory_owner` keeps groups disjoint.
    let group_keys: Vec<String> = memory_groups.keys().cloned().collect();

    for group_owner in group_keys {
        // Re-collect this group's current indices (the rebuild from an earlier
        // group only removed entries, so a fresh scan by owner is exact).
        let group_idxs: Vec<usize> = all_memories
            .iter()
            .enumerate()
            .filter(|(_, m)| {
                if owner_clean.is_empty() {
                    memory_owner(m) == group_owner
                } else {
                    memory_owner(m) == owner_clean
                }
            })
            .map(|(i, _)| i)
            .collect();
        if group_idxs.is_empty() {
            continue;
        }

        // Resolve the endpoint for THIS group's owner (group_owner or None).
        let group_owner_opt = if group_owner.is_empty() { None } else { Some(group_owner.as_str()) };
        let endpoint = resolve_endpoint_triple("utility", group_owner_opt)
            .or_else(|| resolve_endpoint_triple("default", group_owner_opt));

        // ----- AI tidy (best-effort) -----
        let mut handled = false;
        if let Some((url, model, headers)) = endpoint {
            match ai_tidy_group(
                &mut all_memories,
                &group_idxs,
                &url,
                &model,
                &headers,
                &mut acc,
            )
            .await
            {
                Ok(true) => handled = true,
                Ok(false) => { /* fall through to dedupe */ }
                Err(e) => {
                    crate::pylog::warning(&format!(
                        "AI memory tidy failed; falling back to duplicate cleanup: {e}"
                    ));
                }
            }
        }
        if handled {
            continue;
        }

        // ----- Duplicate cleanup fallback (per group) -----
        let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
        let mut keep: std::collections::HashSet<usize> = std::collections::HashSet::new();
        acc.total_scanned += group_idxs.len() as i64;
        for &i in &group_idxs {
            let text = all_memories[i]
                .get("text")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .trim()
                .to_string();
            // key = " ".join(text.lower().split())  (whitespace-normalized)
            let key = dedupe_key(&text);
            if key.is_empty() {
                if acc.removed_examples.len() < 3 {
                    acc.removed_examples.push("(empty)".to_string());
                }
                continue;
            }
            if seen.contains(&key) {
                if acc.removed_examples.len() < 3 {
                    let preview = if text.chars().count() > 60 {
                        format!("{}...", text.chars().take(60).collect::<String>())
                    } else {
                        text.clone()
                    };
                    acc.removed_examples.push(preview);
                }
                continue;
            }
            seen.insert(key);
            keep.insert(i);
        }

        let group_removed = group_idxs.len() as i64 - keep.len() as i64;
        if group_removed == 0 {
            continue;
        }

        // Rebuild all_memories: drop within-group members not in `keep`.
        let drop_set: std::collections::HashSet<usize> =
            group_idxs.iter().copied().filter(|i| !keep.contains(i)).collect();
        let new_all: Vec<Value> = all_memories
            .iter()
            .enumerate()
            .filter(|(i, _)| !drop_set.contains(i))
            .map(|(_, m)| m.clone())
            .collect();
        all_memories = new_all;
        acc.total_removed += group_removed;
    }

    if acc.total_removed != 0 || acc.total_cleaned != 0 {
        manager.save(&mut all_memories).map_err(|e| ConsolidateErr::Generic(e.to_string()))?;
        if acc.ai_used {
            let reasons: Vec<String> = acc.ai_reasons.iter().take(3).cloned().collect();
            let reason_text = if reasons.is_empty() {
                String::new()
            } else {
                format!(": {}", reasons.join("; "))
            };
            return Ok((
                format!(
                    "AI tidied {} memories: removed {}, cleaned {}{}",
                    acc.total_scanned, acc.total_removed, acc.total_cleaned, reason_text
                ),
                true,
            ));
        }
        let preview = acc.removed_examples.join("; ");
        let extra = if acc.total_removed > acc.removed_examples.len() as i64 {
            format!(" (+{} more)", acc.total_removed - acc.removed_examples.len() as i64)
        } else {
            String::new()
        };
        return Ok((
            format!(
                "Removed {} duplicate(s) of {}: {}{}",
                acc.total_removed, acc.total_scanned, preview, extra
            ),
            true,
        ));
    }

    Err(ConsolidateErr::Noop(format!(
        "scanned {} memories, no duplicates",
        acc.total_scanned
    )))
}

/// The LLM tidy branch of `action_consolidate_memory`, run for ONE owner group
/// (`_try_ai_tidy_group`, builtin_actions.py:109-226). Mutates `all_memories` in
/// place (the within-group rebuild) and accumulates into `acc`. Returns:
///   * `Ok(true)`  — the AI pass ran with a valid decision; caller SKIPS dedupe
///     (matches Python `return True`, even when there were no net changes).
///   * `Ok(false)` — fewer than 2 items, no usable decision, or empty keep set;
///     caller falls back to per-group dedupe (`return False`).
///   * `Err(msg)`  — a generic error; caller logs a warning and falls back.
///
/// Truncation protection (py:124-126,182-188,202-203): each memory is sent at
/// `MEMORY_TEXT_LIMIT` chars with a `truncated` flag; a memory the model only saw
/// truncated is force-kept and never has its text rewritten.
#[allow(clippy::too_many_arguments)]
async fn ai_tidy_group(
    all_memories: &mut Vec<Value>,
    group_idxs: &[usize],
    url: &str,
    model: &str,
    headers: &IndexMap<String, String>,
    acc: &mut ConsolidateAccum,
) -> Result<bool, String> {
    use crate::src::llm_core::llm_call_async;
    use crate::src::text_helpers::strip_think;

    // `if len(group_memories) < 2: return False`.
    if group_idxs.len() < 2 {
        return Ok(false);
    }

    // items = [{id, category, text[:2000], truncated} for m with id + non-empty text]
    let mut items: Vec<Value> = Vec::new();
    let mut truncated_ids: std::collections::HashSet<String> = std::collections::HashSet::new();
    for &i in group_idxs {
        let m = &all_memories[i];
        let id = m.get("id").cloned().unwrap_or(Value::Null);
        let text = m.get("text").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
        if id.is_null() || id.as_str() == Some("") || text.is_empty() {
            continue;
        }
        let truncated = text.chars().count() > MEMORY_TEXT_LIMIT;
        if truncated {
            if let Some(ids) = id.as_str() {
                truncated_ids.insert(ids.to_string());
            }
        }
        let category = m.get("category").and_then(|v| v.as_str()).unwrap_or("fact").to_string();
        items.push(json!({
            "id": id,
            "category": category,
            "text": text.chars().take(MEMORY_TEXT_LIMIT).collect::<String>(),
            "truncated": truncated,
        }));
    }
    // `if len(items) < 2: return False`.
    if items.len() < 2 {
        return Ok(false);
    }

    let prompt = format!(
        "You are tidying a user's saved personal memories. Return ONLY raw JSON, no markdown.\n\
Remove memories that are empty, broken, trivial conversation filler, duplicates, or obsolete \
because a clearer newer memory replaces them. Preserve useful personal facts, preferences, \
contacts, project context, and instructions. If memories conflict, keep the clearest/latest \
one and drop the obsolete one.\n\n\
JSON shape:\n\
{{\"keep\":[{{\"id\":\"existing id\",\"text\":\"cleaned text\",\"category\":\"fact|preference|identity|event|contact|project|instruction\"}}],\"drop\":[{{\"id\":\"existing id\",\"reason\":\"short reason\"}}]}}\n\n\
MEMORIES:\n{}",
        serde_json::to_string(&Value::Array(items)).unwrap_or_default()
    );

    let raw = llm_call_async(
        url,
        model,
        vec![json!({"role": "user", "content": prompt})],
        0.0,
        4096,
        headers.clone(),
        120,
    )
    .await
    .map_err(|e| e.to_string())?;

    // raw = strip_think(raw, prose=False, prompt_echo=False).strip()
    let mut raw = strip_think(&raw, false, false).trim().to_string();
    raw = CODE_FENCE_RE.replace_all(&raw, "").trim().to_string();
    let start = raw.find('{');
    let end = raw.rfind('}');
    let (start, end) = match (start, end) {
        (Some(s), Some(e)) if e > s => (s, e),
        _ => return Ok(false),
    };
    let decision: Value = match serde_json::from_str(&raw[start..=end]) {
        Ok(v) => v,
        Err(e) => return Err(e.to_string()),
    };
    let keep_items = decision.get("keep").and_then(|v| v.as_array());
    let drop_items = decision.get("drop").and_then(|v| v.as_array());
    let (keep_items, drop_items) = match (keep_items, drop_items) {
        (Some(k), Some(d)) => (k, d),
        _ => return Ok(false),
    };

    // by_id = {m.id: m} over this group.
    let mut by_id: IndexMap<String, usize> = IndexMap::new();
    for &i in group_idxs {
        if let Some(id) = all_memories[i].get("id").and_then(|v| v.as_str()) {
            by_id.insert(id.to_string(), i);
        }
    }
    let mut keep_ids: std::collections::HashSet<String> = std::collections::HashSet::new();
    // cleaned_by_id[mid] = (Option<text>, category). `text` is only carried when
    // the ORIGINAL text was within the limit (py:181-184: never rewrite a memory
    // the model only saw truncated).
    let mut cleaned_by_id: IndexMap<String, (Option<String>, String)> = IndexMap::new();
    for item in keep_items {
        if item.as_object().is_none() {
            continue;
        }
        let mid = match item.get("id").and_then(|v| v.as_str()) {
            Some(s) => s.to_string(),
            None => continue,
        };
        let orig_idx = match by_id.get(&mid) {
            Some(&i) => i,
            None => continue,
        };
        let text = item.get("text").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
        if text.is_empty() {
            continue;
        }
        keep_ids.insert(mid.clone());
        // category = item.category or by_id[mid].category or "fact"
        let category = {
            let item_cat = item.get("category").and_then(|v| v.as_str()).unwrap_or("").trim();
            if !item_cat.is_empty() {
                item_cat.to_string()
            } else {
                all_memories[orig_idx]
                    .get("category")
                    .and_then(|v| v.as_str())
                    .filter(|c| !c.is_empty())
                    .unwrap_or("fact")
                    .trim()
                    .to_string()
            }
        };
        // Only carry cleaned text when the original was NOT truncated.
        let original_text = all_memories[orig_idx]
            .get("text")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        let cleaned_text = if original_text.chars().count() <= MEMORY_TEXT_LIMIT {
            Some(text)
        } else {
            None
        };
        cleaned_by_id.insert(mid, (cleaned_text, category));
    }

    // If the model only saw a truncated memory, force-keep the full memory.
    for tid in &truncated_ids {
        if by_id.contains_key(tid) {
            keep_ids.insert(tid.clone());
        }
    }

    // `if not keep_ids: return False`.
    if keep_ids.is_empty() {
        return Ok(false);
    }

    // Rebuild all_memories: members not in this group untouched; within-group
    // kept only if mid in keep_ids (mid None -> dropped), applying cleaned edits.
    let group_set: std::collections::HashSet<usize> = group_idxs.iter().copied().collect();
    let mut changed_text = 0i64;
    let mut kept_all: Vec<Value> = Vec::with_capacity(all_memories.len());
    for (i, mem) in all_memories.iter().enumerate() {
        if !group_set.contains(&i) {
            kept_all.push(mem.clone());
            continue;
        }
        let mid = match mem.get("id").and_then(|v| v.as_str()) {
            Some(s) => s.to_string(),
            None => continue, // mid None -> not in keep_ids -> dropped
        };
        if !keep_ids.contains(&mid) {
            continue;
        }
        let mut mem = mem.clone();
        if let Some((text, category)) = cleaned_by_id.get(&mid) {
            // py:202-203 — drop the text edit entirely for a truncated memory.
            let text: Option<&String> = if truncated_ids.contains(&mid) {
                None
            } else {
                text.as_ref()
            };
            if let Some(text) = text {
                let cur_text = mem.get("text").and_then(|v| v.as_str()).unwrap_or("");
                if !text.is_empty() && text != cur_text {
                    if let Some(obj) = mem.as_object_mut() {
                        obj.insert("text".to_string(), json!(text));
                    }
                    changed_text += 1;
                }
            }
            if !category.is_empty() {
                if let Some(obj) = mem.as_object_mut() {
                    obj.insert("category".to_string(), json!(category));
                }
            }
        }
        kept_all.push(mem);
    }

    // `total_scanned += len(group_memories)` (py:212) — always, in this branch.
    let group_len = group_idxs.len() as i64;
    acc.total_scanned += group_len;
    let removed = group_len - keep_ids.len() as i64;
    if removed != 0 || changed_text != 0 {
        *all_memories = kept_all;
        acc.total_removed += removed;
        acc.total_cleaned += changed_text;
        acc.ai_used = true;
        // ai_reasons.extend([d.reason for d in drop_items if non-empty]).
        for d in drop_items {
            if d.as_object().is_some() {
                if let Some(r) = d.get("reason").and_then(|v| v.as_str()) {
                    let r = r.trim();
                    if !r.is_empty() {
                        acc.ai_reasons.push(r.to_string());
                    }
                }
            }
        }
    }
    // `return True` either way (a clean scan with no changes still skips dedupe).
    Ok(true)
}

// ---------------------------------------------------------------------------
// action_tidy_calendar (PORT_VIA_DB+web — calendar_events dedupe).
// ---------------------------------------------------------------------------

/// `action_tidy_calendar` — find duplicate calendar events (same title + start
/// time) and DELETE the dups, keeping the oldest (first-seen) instance.
///
/// Incremental via `data/tidy_calendar_state.json` watermark. Faithful to the
/// Python: events at/before the watermark seed the seen-set (first occurrence
/// wins; a dup in the known-clean region is also removed), events after the
/// watermark are candidates checked against the full seen-set.
///
/// DOCUMENTED PATH NOTE: the state file is the literal source-relative
/// `data/tidy_calendar_state.json` (matching the Python `Path("data/...")`),
/// not ODYSSEUS_DATA_DIR.
pub async fn action_tidy_calendar(_owner: &str, _kwargs: &Value) -> ActionResult {
    let inner = tokio::task::spawn_blocking(tidy_calendar_blocking).await;
    match inner {
        Ok(Ok(t)) => Ok(t),
        Ok(Err(CalErr::Noop(r))) => Err(Outcome::noop(r)),
        Ok(Err(CalErr::Generic(msg))) => {
            crate::pylog::error(&format!("tidy_calendar action failed: {msg}"));
            Ok((msg, false))
        }
        Err(e) => {
            let msg = e.to_string();
            crate::pylog::error(&format!("tidy_calendar action failed: {msg}"));
            Ok((msg, false))
        }
    }
}

enum CalErr {
    Noop(String),
    Generic(String),
}

/// Parse a stored DATETIME string into a comparable key + display label. The
/// `dtstart` column stores the SQLAlchemy SQLite datetime string; comparison is
/// lexicographic on the raw string (timestamp order), which mirrors the Python
/// `e.dtstart` tuple key equality. The display label uses `%Y-%m-%d %H:%M`.
fn fmt_event_when(dtstart: &str) -> String {
    use chrono::NaiveDateTime;
    let parsed = NaiveDateTime::parse_from_str(dtstart, "%Y-%m-%d %H:%M:%S%.f")
        .or_else(|_| NaiveDateTime::parse_from_str(dtstart, "%Y-%m-%d %H:%M:%S"))
        .or_else(|_| NaiveDateTime::parse_from_str(dtstart, "%Y-%m-%dT%H:%M:%S%.f"))
        .or_else(|_| NaiveDateTime::parse_from_str(dtstart, "%Y-%m-%dT%H:%M:%S"));
    match parsed {
        Ok(dt) => dt.format("%Y-%m-%d %H:%M").to_string(),
        Err(_) => dtstart.to_string(),
    }
}

// type_complexity: faithful 4-tuple mirroring the SELECT row shape
// (uid, summary, dtstart, created_at) the Python query_map yields 1:1.
#[allow(clippy::type_complexity)]
fn tidy_calendar_blocking() -> Result<(String, bool), CalErr> {
    use std::path::Path;
    let state_file = Path::new("data/tidy_calendar_state.json");

    // last_watermark = saved.last_created_at (raw string for lexicographic cmp)
    let last_watermark: Option<String> = (|| {
        if !state_file.exists() {
            return None;
        }
        let raw = std::fs::read_to_string(state_file).ok()?;
        let saved: Value = serde_json::from_str(&raw).ok()?;
        saved
            .get("last_created_at")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .map(|s| s.to_string())
    })();

    let conn = crate::core::database::session_local().map_err(|e| CalErr::Generic(e.to_string()))?;

    // newest = max(created_at)
    let newest: Option<String> = conn
        .query_row("SELECT MAX(created_at) FROM calendar_events", [], |r| {
            r.get::<_, Option<String>>(0)
        })
        .map_err(|e| CalErr::Generic(e.to_string()))?;

    // Short-circuit: nothing new since last run.
    if let (Some(wm), Some(nw)) = (last_watermark.as_ref(), newest.as_ref()) {
        if nw.as_str() <= wm.as_str() {
            return Err(CalErr::Noop(format!(
                "no new events since watermark {}",
                fmt_event_when(wm)
            )));
        }
    }

    // events = order_by(dtstart) — pull uid, summary, dtstart, created_at.
    let mut stmt = conn
        .prepare("SELECT uid, summary, dtstart, created_at FROM calendar_events ORDER BY dtstart")
        .map_err(|e| CalErr::Generic(e.to_string()))?;
    let rows = stmt
        .query_map([], |r| {
            Ok((
                r.get::<_, String>(0)?,                 // uid
                r.get::<_, Option<String>>(1)?,         // summary
                r.get::<_, Option<String>>(2)?,         // dtstart
                r.get::<_, Option<String>>(3)?,         // created_at
            ))
        })
        .map_err(|e| CalErr::Generic(e.to_string()))?;
    let events: Vec<(String, Option<String>, Option<String>, Option<String>)> =
        rows.filter_map(|r| r.ok()).collect();
    drop(stmt);

    // Build seen-set from known-clean region; collect candidates.
    let mut seen: std::collections::HashSet<(String, String)> = std::collections::HashSet::new();
    let mut candidates: Vec<(String, String, String)> = Vec::new(); // (uid, title, dtstart)
    let mut no_title = 0i64;
    for (uid, summary, dtstart, created_at) in &events {
        let title = summary.clone().unwrap_or_default();
        let title = title.trim().to_string();
        if title.is_empty() {
            no_title += 1;
            continue;
        }
        let dt = dtstart.clone().unwrap_or_default();
        let known_clean = match &last_watermark {
            None => true, // last_watermark is None -> known-clean region
            Some(wm) => created_at
                .as_ref()
                .map(|c| c.as_str() <= wm.as_str())
                .unwrap_or(false),
        };
        if known_clean {
            let key = (title.to_lowercase(), dt.clone());
            if !seen.contains(&key) {
                seen.insert(key);
            } else {
                candidates.push((uid.clone(), title, dt));
            }
        } else {
            candidates.push((uid.clone(), title, dt));
        }
    }

    // Delete candidates whose key is already seen; else add to seen.
    let mut removed: Vec<String> = Vec::new();
    let mut to_delete: Vec<String> = Vec::new();
    for (uid, title, dt) in &candidates {
        let key = (title.to_lowercase(), dt.clone());
        if seen.contains(&key) {
            let when = if dt.is_empty() { "?".to_string() } else { fmt_event_when(dt) };
            removed.push(format!("{title} @ {when}"));
            to_delete.push(uid.clone());
        } else {
            seen.insert(key);
        }
    }
    for uid in &to_delete {
        let _ = conn.execute("DELETE FROM calendar_events WHERE uid = ?1", [uid]);
    }

    // Persist the new watermark (best-effort).
    if let Some(nw) = newest.as_ref() {
        let _ = std::fs::create_dir_all("data");
        let state = json!({
            "last_created_at": nw,
            "last_run_at": crate::pydatetime::utcnow_naive_iso(),
            "scanned": events.len(),
            "removed": removed.len(),
        });
        if let Ok(serialized) = serde_json::to_string_pretty(&state) {
            if std::fs::write(state_file, serialized).is_err() {
                crate::pylog::warning("tidy_calendar watermark save failed");
            }
        }
    }

    let new_since = candidates.len();
    let mut parts: Vec<String> = vec![format!(
        "Scanned {} event(s), {} new since last run",
        events.len(),
        new_since
    )];
    if !removed.is_empty() {
        let mut preview = removed.iter().take(5).cloned().collect::<Vec<_>>().join("; ");
        if removed.len() > 5 {
            preview.push_str(&format!(" (+{} more)", removed.len() - 5));
        }
        parts.push(format!("removed {} duplicate(s): {}", removed.len(), preview));
    }
    if no_title > 0 {
        parts.push(format!("{no_title} untitled (kept)"));
    }
    if removed.is_empty() && no_title == 0 {
        parts.push("no duplicates".to_string());
    }
    Ok((parts.join(" \u{b7} "), true))
}

// ---------------------------------------------------------------------------
// action_classify_events (PORT_VIA_DB+web — heuristic + batch LLM).
// ---------------------------------------------------------------------------

/// `action_classify_events` — hybrid classification of upcoming calendar events.
///
/// PORT_VIA_DB+web: fast heuristic for obvious cases, batched LLM fallback (10
/// per call) for ambiguous ones. Pulls owner `memories` for LLM context.
/// Re-classifies anything not already set (event_type AND importance!=normal).
pub async fn action_classify_events(owner: &str, _kwargs: &Value) -> ActionResult {
    match classify_events_inner(owner).await {
        Ok(t) => Ok(t),
        Err(msg) => {
            crate::pylog::error(&format!("classify_events action failed: {msg}"));
            Ok((msg, false))
        }
    }
}

/// A mutable in-memory view of a candidate calendar event row.
struct EvRow {
    uid: String,
    summary: String,
    dtstart: String,
    location: String,
    event_type: Option<String>,
    importance: Option<String>,
    color: Option<String>,
}

// needless_range_loop: mirrors Python builtin_actions.py:570 `for ev in events`
// — the Rust must index `events` by position because it mutates entries in place
// and queues positional indices into `llm_queue` (Python queues the object ref).
#[allow(clippy::needless_range_loop)]
async fn classify_events_inner(owner: &str) -> Result<(String, bool), String> {
    use crate::src::llm_core::llm_call_async;
    use crate::src::text_helpers::strip_think;

    // now / horizon as stored-datetime strings (lexicographic compare).
    let now_dt = crate::pydatetime::utcnow_naive();
    let now_str = crate::pydatetime::naive_to_iso(now_dt);
    let horizon_dt = now_dt + chrono::Duration::days(30);
    let horizon_str = crate::pydatetime::naive_to_iso(horizon_dt);

    // Load candidate events (status != cancelled, in [now, horizon]).
    let mut events: Vec<EvRow> = {
        let conn = crate::core::database::session_local().map_err(|e| e.to_string())?;
        let mut stmt = conn
            .prepare(
                "SELECT uid, summary, dtstart, location, event_type, importance, color \
                 FROM calendar_events \
                 WHERE dtstart >= ?1 AND dtstart <= ?2 AND status != 'cancelled'",
            )
            .map_err(|e| e.to_string())?;
        let rows = stmt
            .query_map(rusqlite::params![now_str, horizon_str], |r| {
                Ok(EvRow {
                    uid: r.get(0)?,
                    summary: r.get::<_, Option<String>>(1)?.unwrap_or_default(),
                    dtstart: r.get::<_, Option<String>>(2)?.unwrap_or_default(),
                    location: r.get::<_, Option<String>>(3)?.unwrap_or_default(),
                    event_type: r.get::<_, Option<String>>(4)?,
                    importance: r.get::<_, Option<String>>(5)?,
                    color: r.get::<_, Option<String>>(6)?,
                })
            })
            .map_err(|e| e.to_string())?;
        rows.filter_map(|r| r.ok()).collect()
    };
    if events.is_empty() {
        return Ok(("No upcoming events to classify".to_string(), true));
    }

    // Python action_classify_events resolves with NO owner (builtin_actions.py:541/543),
    // so pass None even though `classify_events_inner` receives an `owner` param —
    // threading owner here would be new drift away from Python.
    use crate::src::endpoint_resolver::resolve_endpoint_triple;
    let endpoint = resolve_endpoint_triple("utility", None)
        .or_else(|| resolve_endpoint_triple("default", None));
    let llm_available = endpoint.is_some();

    // memory_context — pull up to 60 owner memories, format up to 40 lines.
    let mut memory_context = String::new();
    if !owner.is_empty() {
        if let Ok(conn) = crate::core::database::session_local() {
            if let Ok(mut stmt) =
                conn.prepare("SELECT text FROM memories WHERE owner = ?1 LIMIT 60")
            {
                let rows = stmt.query_map([owner], |r| r.get::<_, Option<String>>(0));
                if let Ok(rows) = rows {
                    let mut lines: Vec<String> = Vec::new();
                    for c in rows.flatten().flatten() {
                        let c = c.trim();
                        if !c.is_empty() {
                            lines.push(format!("- {}", c.chars().take(200).collect::<String>()));
                        }
                    }
                    if !lines.is_empty() {
                        let take: Vec<String> = lines.into_iter().take(40).collect();
                        memory_context = format!(
                            "USER CONTEXT (relationships, work, life):\n{}\n\n",
                            take.join("\n")
                        );
                    }
                }
            }
        }
    }

    let mut classified_h = 0i64;
    let mut classified_llm = 0i64;
    let mut failed = 0i64;
    let mut unchanged = 0i64;
    let mut llm_queue: Vec<usize> = Vec::new(); // indices into `events`

    // Pass 1: heuristic + queue ambiguous.
    for idx in 0..events.len() {
        let already_set = {
            let ev = &events[idx];
            let has_type = ev.event_type.as_deref().map(|t| !t.is_empty()).unwrap_or(false);
            let imp = ev.importance.as_deref().unwrap_or("");
            has_type && !imp.is_empty() && imp != "normal"
        };
        if already_set {
            unchanged += 1;
            continue;
        }
        let (etype, importance) = _classify_event_heuristic(&events[idx].summary);
        if let (Some(et), Some(imp)) = (etype, importance) {
            let ev = &mut events[idx];
            ev.event_type = Some(et.to_string());
            ev.color = TYPE_COLORS.get(et).map(|c| c.to_string());
            ev.importance = Some(imp.to_string());
            classified_h += 1;
            continue;
        }
        if let Some(et) = etype {
            let ev = &mut events[idx];
            ev.event_type = Some(et.to_string());
            ev.color = TYPE_COLORS.get(et).map(|c| c.to_string());
        }
        if llm_available {
            llm_queue.push(idx);
        } else if etype.is_some() {
            classified_h += 1;
        }
    }
    // Persist heuristic results before the LLM pass.
    persist_events(&events);

    // Pass 2: batch LLM classification (10 per call).
    if let Some((url, model, headers)) = endpoint {
        const BATCH: usize = 10;
        let mut i = 0;
        while i < llm_queue.len() {
            let batch: Vec<usize> = llm_queue[i..(i + BATCH).min(llm_queue.len())].to_vec();
            // items = [{i, title[:120], when, loc[:80]} for batch]
            let items: Vec<Value> = batch
                .iter()
                .enumerate()
                .map(|(bi, &ei)| {
                    let ev = &events[ei];
                    json!({
                        "i": bi,
                        "title": ev.summary.chars().take(120).collect::<String>(),
                        "when": crate::pydatetime::to_isoformat(&ev.dtstart),
                        "loc": ev.location.chars().take(80).collect::<String>(),
                    })
                })
                .collect();
            let prompt = format!(
                "{}Classify these calendar events using the USER CONTEXT above (people they know, \
their job, hobbies). Return ONLY a raw JSON array, no prose, no markdown.\n\
Each item: {{\"i\": <index>, \"type\": \"work|personal|health|travel|meal|social|admin|other\", \"importance\": \"low|normal|high|critical\"}}\n\n\
Type guidance:\n\
- personal = family, partner, kids, pets, errands, home stuff\n\
- social = friends, parties, birthdays, hangouts\n\
- work = the user's own job/career commitments only (not their partner's)\n\
- health = doctor, gym, therapy\n\
- travel = flights, trips, hotels\n\
- meal = lunch/dinner/coffee specifically\n\
- admin = bills, taxes, paperwork\n\
- other = anything else\n\n\
Importance guide: critical = surgery/court/wedding day; high = flight/interview/big presentation/exam; normal = regular meetings/appointments; low = recurring routine.\n\n\
EVENTS: {}",
                memory_context,
                serde_json::to_string(&Value::Array(items)).unwrap_or_default()
            );

            let llm_res = llm_call_async(
                &url,
                &model,
                vec![json!({"role": "user", "content": prompt})],
                0.1,
                16384,
                headers.clone(),
                180,
            )
            .await;

            match llm_res {
                Ok(raw) => {
                    let raw = strip_think(&raw, false, false);
                    let raw = CODE_FENCE_RE.replace_all(&raw, "").trim().to_string();
                    // m = re.search(r"\[.*\]", raw, DOTALL)
                    let arr_text = find_json_array(&raw);
                    match arr_text {
                        None => {
                            crate::pylog::warning(&format!(
                                "[classify-llm] no JSON array in response: {:?}",
                                raw.chars().take(300).collect::<String>()
                            ));
                            failed += batch.len() as i64;
                        }
                        Some(at) => match serde_json::from_str::<Value>(&at) {
                            Ok(Value::Array(arr)) => {
                                // by_idx = {x["i"]: x for x in arr if dict}
                                let mut by_idx: IndexMap<i64, Value> = IndexMap::new();
                                for x in &arr {
                                    if let Some(i_val) = x.get("i").and_then(py_to_i64) {
                                        if x.is_object() {
                                            by_idx.insert(i_val, x.clone());
                                        }
                                    }
                                }
                                for (bi, &ei) in batch.iter().enumerate() {
                                    let x = by_idx.get(&(bi as i64));
                                    match x {
                                        None => {
                                            failed += 1;
                                            continue;
                                        }
                                        Some(x) => {
                                            let t = x
                                                .get("type")
                                                .and_then(|v| v.as_str())
                                                .unwrap_or("other")
                                                .to_lowercase();
                                            let imp = x
                                                .get("importance")
                                                .and_then(|v| v.as_str())
                                                .unwrap_or("normal")
                                                .to_lowercase();
                                            let ev = &mut events[ei];
                                            if TYPE_COLORS.contains_key(t.as_str()) {
                                                ev.event_type = Some(t.clone());
                                                ev.color =
                                                    TYPE_COLORS.get(t.as_str()).map(|c| c.to_string());
                                            }
                                            if matches!(
                                                imp.as_str(),
                                                "low" | "normal" | "high" | "critical"
                                            ) {
                                                ev.importance = Some(imp.clone());
                                            }
                                            classified_llm += 1;
                                            crate::pylog::info(&format!(
                                                "[classify-llm] '{}' \u{2192} type={} importance={}",
                                                ev.summary, t, imp
                                            ));
                                        }
                                    }
                                }
                            }
                            _ => {
                                crate::pylog::warning("[classify-llm] batch failed: not an array");
                                failed += batch.len() as i64;
                            }
                        },
                    }
                }
                Err(e) => {
                    crate::pylog::warning(&format!("[classify-llm] batch failed: {e}"));
                    failed += batch.len() as i64;
                }
            }
            // Commit after each batch (persist all events so far).
            persist_events(&events);
            i += BATCH;
        }
    }
    // Final commit.
    persist_events(&events);

    let mut parts: Vec<String> = vec![format!("Scanned {} upcoming event(s)", events.len())];
    if classified_h > 0 {
        parts.push(format!("{classified_h} via heuristic"));
    }
    if classified_llm > 0 {
        parts.push(format!("{classified_llm} via LLM"));
    }
    if unchanged > 0 {
        parts.push(format!("{unchanged} already set (skipped)"));
    }
    if failed > 0 {
        parts.push(format!("{failed} LLM failed"));
    }
    Ok((parts.join(" \u{b7} "), true))
}

/// Write back event_type / importance / color for every row (the per-batch /
/// final `db.commit()`). Best-effort: a single failed row is skipped silently.
fn persist_events(events: &[EvRow]) {
    let conn = match crate::core::database::session_local() {
        Ok(c) => c,
        Err(_) => return,
    };
    for ev in events {
        let _ = conn.execute(
            "UPDATE calendar_events SET event_type = ?2, importance = ?3, color = ?4 WHERE uid = ?1",
            rusqlite::params![ev.uid, ev.event_type, ev.importance, ev.color],
        );
    }
}

/// `re.search(r"\[.*\]", raw, re.DOTALL).group()` — the first `[` to the last
/// `]` (DOTALL so the body spans newlines). Returns the slice, or `None`.
fn find_json_array(raw: &str) -> Option<String> {
    let start = raw.find('[')?;
    let end = raw.rfind(']')?;
    if end > start {
        Some(raw[start..=end].to_string())
    } else {
        None
    }
}

/// `int(x)` over a JSON value for the LLM index field — number or numeric string.
fn py_to_i64(v: &Value) -> Option<i64> {
    match v {
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)),
        Value::String(s) => s.trim().parse::<i64>().ok(),
        _ => None,
    }
}

// ---------------------------------------------------------------------------
// action_daily_brief (PORT_VIA_DB+web — calendar + notes; IMAP DEFERRED).
// ---------------------------------------------------------------------------

/// `action_daily_brief` — build a short morning digest: today's calendar
/// events, unread email count + top senders, active todos.
///
/// PORT_PARTIAL: the calendar + notes halves are ported via raw SQL. The IMAP
/// unread-count / recent-subjects slice is DEFERRED (honest: 0 unread, no
/// subjects listed) because `routes.email_helpers` IMAP is unported — the
/// Python guards that block in its own try/except, so a 0 count is the exact
/// shape it falls back to when IMAP is unavailable.
pub async fn action_daily_brief(owner: &str, _kwargs: &Value) -> ActionResult {
    let owner = owner.to_string();
    let inner = tokio::task::spawn_blocking(move || daily_brief_blocking(&owner)).await;
    match inner {
        Ok(Ok(t)) => Ok(t),
        Ok(Err(msg)) => {
            crate::pylog::error(&format!("daily_brief action failed: {msg}"));
            Ok((msg, false))
        }
        Err(e) => {
            let msg = e.to_string();
            crate::pylog::error(&format!("daily_brief action failed: {msg}"));
            Ok((msg, false))
        }
    }
}

fn daily_brief_blocking(owner: &str) -> Result<(String, bool), String> {
    use chrono::{Datelike, Local, NaiveDateTime, Timelike};

    // today = datetime.now().replace(hour=0,...); tomorrow = today + 1d. The
    // Python uses LOCAL now() for the brief's date window.
    let now_local = Local::now().naive_local();
    let today = now_local
        .date()
        .and_hms_opt(0, 0, 0)
        .unwrap_or(now_local);
    let tomorrow = today + chrono::Duration::days(1);
    // Stored datetimes are naive-UTC strings; compare against the local-midnight
    // boundaries rendered to the same string format (documented: the Python
    // compares naive local `today`/`tomorrow` against the stored naive values).
    let today_str = today.format("%Y-%m-%d %H:%M:%S%.6f").to_string();
    let tomorrow_str = tomorrow.format("%Y-%m-%d %H:%M:%S%.6f").to_string();

    // v2 review HIGH-12: gate the OR-null branch on single-user (unconfigured)
    // deploys only. In a multi-user deploy, one user's daily brief must not
    // include another user's notes or events stored with owner=None. Mirrors
    // Python's `_allow_null = not AuthManager().is_configured` (best-effort,
    // defaulting to false like the Python `except: _allow_null = False`;
    // AuthManager::new() is infallible so the false-on-error case is moot here).
    let allow_null = !crate::core::auth::AuthManager::new().is_configured();

    let conn = crate::core::database::session_local().map_err(|e| e.to_string())?;

    // Events: dtstart < tomorrow AND dtend > today AND status != cancelled,
    // joined to calendars; owner filter when set: `c.owner = ?` always, plus
    // `OR c.owner IS NULL` only when `allow_null` (single-user) — matching
    // Python's owner_filter(..., include_shared=_allow_null).
    struct EvBrief {
        summary: String,
        location: String,
        dtstart: String,
        all_day: bool,
    }
    let events: Vec<EvBrief> = {
        let (sql, owner_param): (String, Vec<String>) = if owner.is_empty() {
            (
                "SELECT e.summary, e.location, e.dtstart, e.all_day \
                 FROM calendar_events e JOIN calendars c ON e.calendar_id = c.id \
                 WHERE e.dtstart < ?1 AND e.dtend > ?2 AND e.status != 'cancelled' \
                 ORDER BY e.dtstart"
                    .to_string(),
                vec![],
            )
        } else {
            let owner_clause = if allow_null {
                "AND (c.owner = ?3 OR c.owner IS NULL)"
            } else {
                "AND c.owner = ?3"
            };
            (
                format!(
                    "SELECT e.summary, e.location, e.dtstart, e.all_day \
                     FROM calendar_events e JOIN calendars c ON e.calendar_id = c.id \
                     WHERE e.dtstart < ?1 AND e.dtend > ?2 AND e.status != 'cancelled' \
                     {owner_clause} \
                     ORDER BY e.dtstart"
                ),
                vec![owner.to_string()],
            )
        };
        let mut stmt = conn.prepare(&sql).map_err(|e| e.to_string())?;
        let map_row = |r: &rusqlite::Row| -> rusqlite::Result<EvBrief> {
            Ok(EvBrief {
                summary: r.get::<_, Option<String>>(0)?.unwrap_or_default(),
                location: r.get::<_, Option<String>>(1)?.unwrap_or_default(),
                dtstart: r.get::<_, Option<String>>(2)?.unwrap_or_default(),
                all_day: r.get::<_, Option<bool>>(3)?.unwrap_or(false),
            })
        };
        let rows = if owner.is_empty() {
            stmt.query_map(rusqlite::params![tomorrow_str, today_str], map_row)
        } else {
            stmt.query_map(
                rusqlite::params![tomorrow_str, today_str, owner_param[0]],
                map_row,
            )
        }
        .map_err(|e| e.to_string())?;
        rows.filter_map(|r| r.ok()).collect()
    };

    // Notes: archived == False; owner filter when set.
    struct NoteBrief {
        title: String,
        note_type: String,
        items: Option<String>,
        pinned: bool,
    }
    let notes: Vec<NoteBrief> = {
        let (sql, owner_param): (String, Vec<String>) = if owner.is_empty() {
            (
                "SELECT title, note_type, items, pinned FROM notes WHERE archived = 0".to_string(),
                vec![],
            )
        } else {
            // Owner filter: `owner = ?` always, plus `OR owner IS NULL` only
            // when `allow_null` (single-user) — matching Python's owner_filter
            // include_shared=_allow_null gate.
            let owner_clause = if allow_null {
                "AND (owner = ?1 OR owner IS NULL)"
            } else {
                "AND owner = ?1"
            };
            (
                format!("SELECT title, note_type, items, pinned FROM notes WHERE archived = 0 {owner_clause}"),
                vec![owner.to_string()],
            )
        };
        let mut stmt = conn.prepare(&sql).map_err(|e| e.to_string())?;
        let map_row = |r: &rusqlite::Row| -> rusqlite::Result<NoteBrief> {
            Ok(NoteBrief {
                title: r.get::<_, Option<String>>(0)?.unwrap_or_default(),
                note_type: r.get::<_, Option<String>>(1)?.unwrap_or_default(),
                items: r.get::<_, Option<String>>(2)?,
                pinned: r.get::<_, Option<bool>>(3)?.unwrap_or(false),
            })
        };
        let rows = if owner.is_empty() {
            stmt.query_map([], map_row)
        } else {
            stmt.query_map([owner_param[0].as_str()], map_row)
        }
        .map_err(|e| e.to_string())?;
        rows.filter_map(|r| r.ok()).collect()
    };

    // ----- Email: unread count + top 5 inbox subjects (best-effort) -----
    // Direct IMAP (Python action_daily_brief L1151-1186): connect to the default
    // account, count UNSEEN in INBOX, and grab From/Subject for the most recent 5.
    // We already run inside `spawn_blocking` (action_daily_brief), so the sync
    // IMAP work is fine here. INBOX is opened with EXAMINE (readonly) and headers
    // fetched via `RFC822.HEADER` (implicitly BODY.PEEK — never sets `\Seen`), so
    // the brief never marks unread mail as read.
    let mut unread_count = 0usize;
    let mut recent_subjects: Vec<(String, String)> = Vec::new();
    {
        use crate::routes::email_helpers as eh;
        let email_result = (|| -> Result<(), String> {
            let mut imap = eh::imap_connect(None, "").map_err(|e| e.to_string())?;
            // conn.select("INBOX", readonly=True) -> EXAMINE.
            imap.examine("INBOX").map_err(|e| e.to_string())?;
            // status, data = conn.search(None, "UNSEEN"); unread_count = len(uids)
            let found = imap.search("UNSEEN").map_err(|e| e.to_string())?;
            let mut seqs: Vec<u32> = found.into_iter().collect();
            seqs.sort_unstable();
            unread_count = seqs.len();
            // for uid in uids[-5:][::-1]: most-recent 5, newest first.
            let last5: Vec<u32> = seqs.iter().rev().take(5).copied().collect();
            for seq in last5 {
                let fetched = match imap.fetch(seq.to_string(), "(RFC822.HEADER)") {
                    Ok(f) => f,
                    Err(e) => {
                        crate::pylog::debug(&format!(
                            "daily_brief: header fetch for uid {seq} failed: {e}"
                        ));
                        continue;
                    }
                };
                let mut raw: Vec<u8> = Vec::new();
                for f in fetched.iter() {
                    if let Some(h) = f.header() {
                        raw.extend_from_slice(h);
                    }
                }
                if raw.is_empty() {
                    continue;
                }
                let parsed = match eh::parse_message(&raw) {
                    Some(m) => m,
                    None => continue,
                };
                // subject = _decode_header(parsed.get("Subject") or "") or "(no subject)"
                let subject = {
                    let d = eh::decode_header(&parsed.header("Subject").unwrap_or_default());
                    if d.is_empty() {
                        "(no subject)".to_string()
                    } else {
                        d
                    }
                };
                // from_raw = _decode_header(parsed.get("From") or "") or "?"
                let from_raw = {
                    let d = eh::decode_header(&parsed.header("From").unwrap_or_default());
                    if d.is_empty() {
                        "?".to_string()
                    } else {
                        d
                    }
                };
                // Extract just the display name if "Name <addr>" form.
                let name = if let Some(idx) = from_raw.find('<') {
                    let n = from_raw[..idx].trim().trim_matches('"').to_string();
                    if n.is_empty() {
                        from_raw.clone()
                    } else {
                        n
                    }
                } else {
                    from_raw.clone()
                };
                recent_subjects.push((name, subject));
            }
            let _ = imap.logout();
            Ok(())
        })();
        if let Err(e) = email_result {
            crate::pylog::debug(&format!("daily_brief: email fetch failed: {e}"));
        }
    }

    // Pull active todo items from notes.
    let mut todo_lines: Vec<String> = Vec::new();
    for n in &notes {
        if n.note_type == "checklist" {
            if let Some(items_raw) = &n.items {
                if let Ok(Value::Array(items)) = serde_json::from_str::<Value>(items_raw) {
                    let pending: Vec<String> = items
                        .iter()
                        .filter(|it| !it.get("done").and_then(|v| v.as_bool()).unwrap_or(false))
                        .map(|it| it.get("text").and_then(|v| v.as_str()).unwrap_or("").to_string())
                        .collect();
                    for t in pending.iter().take(3) {
                        if !t.is_empty() {
                            let title = if n.title.is_empty() {
                                "Checklist".to_string()
                            } else {
                                n.title.clone()
                            };
                            todo_lines.push(format!("{title}: {t}"));
                        }
                    }
                }
            }
        } else if n.pinned && !n.title.is_empty() {
            todo_lines.push(n.title.clone());
        }
    }

    // date_label = today.strftime(f"%A, %B {today.day}, %Y")
    let _ = NaiveDateTime::default; // silence unused import if optimized away
    let weekday = today.format("%A").to_string();
    let month = today.format("%B").to_string();
    let date_label = format!("{}, {} {}, {}", weekday, month, today.day(), today.year());

    let mut plain: Vec<String> = vec![format!("Daily brief \u{2014} {date_label}"), String::new()];
    if !events.is_empty() {
        plain.push("Calendar:".to_string());
        for e in &events {
            let t = if e.all_day {
                "all day".to_string()
            } else {
                // e.dtstart.strftime("%H:%M")
                fmt_event_hm(&e.dtstart)
            };
            let loc = if e.location.is_empty() {
                String::new()
            } else {
                format!(" @ {}", e.location)
            };
            plain.push(format!("  {}  {}{}", t, e.summary, loc));
        }
        plain.push(String::new());
    } else {
        plain.push("Calendar: nothing scheduled.".to_string());
        plain.push(String::new());
    }

    plain.push(format!("Email: {unread_count} unread"));
    for (sender, subj) in &recent_subjects {
        plain.push(format!("  \u{b7} {sender} \u{2014} {subj}"));
    }
    plain.push(String::new());

    if !todo_lines.is_empty() {
        plain.push("Todos:".to_string());
        for t in todo_lines.iter().take(10) {
            plain.push(format!("  \u{b7} {t}"));
        }
    } else {
        plain.push("Todos: none active.".to_string());
    }

    let _ = now_local.hour(); // keep Timelike import meaningful
    Ok((plain.join("\n"), true))
}

/// `dt.strftime("%H:%M")` over a stored datetime string.
fn fmt_event_hm(dtstart: &str) -> String {
    use chrono::NaiveDateTime;
    let parsed = NaiveDateTime::parse_from_str(dtstart, "%Y-%m-%d %H:%M:%S%.f")
        .or_else(|_| NaiveDateTime::parse_from_str(dtstart, "%Y-%m-%d %H:%M:%S"))
        .or_else(|_| NaiveDateTime::parse_from_str(dtstart, "%Y-%m-%dT%H:%M:%S%.f"))
        .or_else(|_| NaiveDateTime::parse_from_str(dtstart, "%Y-%m-%dT%H:%M:%S"));
    match parsed {
        Ok(dt) => dt.format("%H:%M").to_string(),
        Err(_) => dtstart.to_string(),
    }
}

// ---------------------------------------------------------------------------
// action_ping_notes (PORT_VIA_DB+web — notes due window; dispatch DEFERRED).
// ---------------------------------------------------------------------------

/// `action_ping_notes` — background note-due scanner. Fires a reminder for any
/// note whose `due_date` falls in the current ±window and hasn't been pinged
/// within the last 25 minutes.
///
/// PORT_PARTIAL: the due-window detection + per-owner JSON ping-state file
/// (`data/note_pings_<slug>.json`, with legacy `data/note_pings.json` seed) is
/// ported faithfully. The actual reminder dispatch
/// (`routes.note_routes.dispatch_reminder`) is DEFERRED — it is a no-op here, so
/// a "due" note is *detected* and the state file is written as if pinged, but no
/// reminder leaves the process (documented honest deviation; `dispatch_reminder`
/// route is unported).
///
/// NOTE: `ping_notes` is NOT in `BUILTIN_ACTIONS` (it runs only inside the
/// scheduler's `_note_pings_loop`); it is exported for that consumer.
pub async fn action_ping_notes(owner: &str, _kwargs: &Value) -> ActionResult {
    let owner = owner.to_string();
    let inner = tokio::task::spawn_blocking(move || ping_notes_blocking(&owner)).await;
    match inner {
        Ok(Ok(t)) => Ok(t),
        Ok(Err(PingErr::Noop(r))) => Err(Outcome::noop(r)),
        Ok(Err(PingErr::Generic(msg))) => {
            crate::pylog::error(&format!("ping_notes action failed: {msg}"));
            Ok((msg, false))
        }
        Err(e) => {
            let msg = e.to_string();
            crate::pylog::error(&format!("ping_notes action failed: {msg}"));
            Ok((msg, false))
        }
    }
}

enum PingErr {
    Noop(String),
    Generic(String),
}

/// `_parse_due(s)` — accept `'...T...'` (local) or `'...Z'` (UTC), return UTC.
fn parse_due(s: &str) -> Option<chrono::DateTime<chrono::Utc>> {
    use chrono::{DateTime, Local, NaiveDateTime, TimeZone, Utc};
    if s.is_empty() {
        return None;
    }
    if let Some(stripped) = s.strip_suffix('Z') {
        // fromisoformat(s[:-1]).replace(tzinfo=utc)
        let ndt = NaiveDateTime::parse_from_str(stripped, "%Y-%m-%dT%H:%M:%S%.f")
            .or_else(|_| NaiveDateTime::parse_from_str(stripped, "%Y-%m-%dT%H:%M:%S"))
            .or_else(|_| NaiveDateTime::parse_from_str(stripped, "%Y-%m-%dT%H:%M"))
            .ok()?;
        return Some(Utc.from_utc_datetime(&ndt));
    }
    // Try a tz-aware parse first (rfc3339 with offset).
    if let Ok(dt) = DateTime::parse_from_rfc3339(s) {
        return Some(dt.with_timezone(&Utc));
    }
    // Naive -> assume local server time, convert to UTC.
    let ndt = NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S%.f")
        .or_else(|_| NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S"))
        .or_else(|_| NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M"))
        .ok()?;
    let local = Local.from_local_datetime(&ndt).single()?;
    Some(local.with_timezone(&Utc))
}

fn ping_notes_blocking(owner: &str) -> Result<(String, bool), PingErr> {
    use chrono::Utc;
    use std::path::PathBuf;

    const WINDOW_SEC: i64 = 90;
    const REPING_MIN: i64 = 25;

    // owner_slug = "".join(c if alnum or in "-_.@" else "_" for c in owner or "default")
    let owner_or_default = if owner.is_empty() { "default" } else { owner };
    let slug: String = owner_or_default
        .chars()
        .map(|c| if c.is_alphanumeric() || "-_.@".contains(c) { c } else { '_' })
        .collect();
    let state = PathBuf::from(format!("data/note_pings_{slug}.json"));
    let _ = std::fs::create_dir_all("data");
    // One-time migration from legacy global file.
    let legacy = PathBuf::from("data/note_pings.json");
    if legacy.exists() && !state.exists() {
        if let Ok(txt) = std::fs::read_to_string(&legacy) {
            let _ = std::fs::write(&state, txt);
        }
    }

    // cache = {note_id: iso_ts}
    let mut cache: serde_json::Map<String, Value> = (|| {
        if !state.exists() {
            return serde_json::Map::new();
        }
        std::fs::read_to_string(&state)
            .ok()
            .and_then(|t| serde_json::from_str::<Value>(&t).ok())
            .and_then(|v| v.as_object().cloned())
            .unwrap_or_default()
    })();

    let conn = crate::core::database::session_local().map_err(|e| PingErr::Generic(e.to_string()))?;

    struct NoteDue {
        id: String,
        title: String,
        content: Option<String>,
        items: Option<String>,
        owner: Option<String>,
        due_date: String,
    }
    let notes: Vec<NoteDue> = {
        let (sql, with_owner) = if owner.is_empty() {
            (
                "SELECT id, title, content, items, owner, due_date FROM notes \
                 WHERE archived = 0 AND due_date IS NOT NULL AND due_date != ''"
                    .to_string(),
                false,
            )
        } else {
            (
                "SELECT id, title, content, items, owner, due_date FROM notes \
                 WHERE archived = 0 AND due_date IS NOT NULL AND due_date != '' \
                 AND (owner = ?1 OR owner IS NULL)"
                    .to_string(),
                true,
            )
        };
        let mut stmt = conn.prepare(&sql).map_err(|e| PingErr::Generic(e.to_string()))?;
        let map_row = |r: &rusqlite::Row| -> rusqlite::Result<NoteDue> {
            Ok(NoteDue {
                id: r.get(0)?,
                title: r.get::<_, Option<String>>(1)?.unwrap_or_default(),
                content: r.get::<_, Option<String>>(2)?,
                items: r.get::<_, Option<String>>(3)?,
                owner: r.get::<_, Option<String>>(4)?,
                due_date: r.get::<_, Option<String>>(5)?.unwrap_or_default(),
            })
        };
        let rows = if with_owner {
            stmt.query_map([owner], map_row)
        } else {
            stmt.query_map([], map_row)
        }
        .map_err(|e| PingErr::Generic(e.to_string()))?;
        rows.filter_map(|r| r.ok()).collect()
    };
    if notes.is_empty() {
        return Err(PingErr::Noop("no notes with due dates".to_string()));
    }

    let now = Utc::now();
    let reping_cutoff = now - chrono::Duration::minutes(REPING_MIN);
    let mut seen_ids: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut sent: Vec<String> = Vec::new();

    for n in &notes {
        seen_ids.insert(n.id.clone());
        let due = match parse_due(&n.due_date) {
            Some(d) => d,
            None => continue,
        };
        // Inside the ±window?
        if (due - now).num_seconds().abs() > WINDOW_SEC {
            continue;
        }
        // Recently pinged? Skip.
        if let Some(last_val) = cache.get(&n.id) {
            let last_str = if let Some(obj) = last_val.as_object() {
                obj.get("at").and_then(|v| v.as_str()).map(|s| s.to_string())
            } else {
                last_val.as_str().map(|s| s.to_string())
            };
            if let Some(last_str) = last_str {
                if let Some(last_dt) = parse_due_or_iso(&last_str) {
                    if last_dt >= reping_cutoff {
                        continue;
                    }
                }
            }
        }
        // Compose body (used by the deferred dispatch).
        let title = {
            let t = n.title.trim();
            if t.is_empty() { "Reminder".to_string() } else { t.to_string() }
        };
        let mut body_parts: Vec<String> = Vec::new();
        if let Some(c) = &n.content {
            if !c.is_empty() {
                body_parts.push(c.chars().take(400).collect::<String>());
            }
        }
        if let Some(items_raw) = &n.items {
            if let Ok(Value::Array(items)) = serde_json::from_str::<Value>(items_raw) {
                let pending: Vec<String> = items
                    .iter()
                    .filter(|it| {
                        !it.get("done").and_then(|v| v.as_bool()).unwrap_or(false)
                            && !it.get("checked").and_then(|v| v.as_bool()).unwrap_or(false)
                    })
                    .map(|it| it.get("text").and_then(|v| v.as_str()).unwrap_or("").to_string())
                    .collect();
                if !pending.is_empty() {
                    let listed = pending
                        .iter()
                        .take(8)
                        .map(|t| format!("- {t}"))
                        .collect::<Vec<_>>()
                        .join("\n");
                    body_parts.push(format!("Pending:\n{listed}"));
                }
            }
        }
        let body = {
            let joined = body_parts.iter().filter(|p| !p.is_empty()).cloned().collect::<Vec<_>>().join("\n\n");
            if joined.is_empty() { title.clone() } else { joined }
        };
        // `from routes.note_routes import dispatch_reminder; await dispatch_reminder(
        //  title=title, note_body=body, note_id=n.id, owner=n.owner or owner or "")`
        // (builtin_actions.py:1535-1543). dispatch_reminder swallows its own errors
        // and returns a status Value, matching Python try/except -> log-and-continue.
        // scheduler=None skips the browser queue (same as action_check_email_urgency);
        // the email / LLM-synthesis channels still fire.
        let r_owner = n.owner.as_deref().filter(|o| !o.is_empty()).unwrap_or(owner);
        // dispatch_reminder is async; ping_notes runs on a spawn_blocking thread, so
        // drive it on the current runtime handle (block_on is allowed off the async
        // workers). It swallows its own errors and returns a status Value, so we
        // always advance the cache+sent afterward (Python dispatch-then-cache flow).
        let _ = tokio::runtime::Handle::current().block_on(
            crate::routes::note_routes::dispatch_reminder(&title, &body, &n.id, r_owner, true, None),
        );
        cache.insert(n.id.clone(), json!(now.to_rfc3339()));
        sent.push(title);
    }

    // Prune cache entries for notes that no longer exist.
    let stale: Vec<String> = cache
        .keys()
        .filter(|k| !seen_ids.contains(*k))
        .cloned()
        .collect();
    for k in stale {
        cache.remove(&k);
    }

    if let Ok(serialized) = serde_json::to_string(&Value::Object(cache)) {
        if std::fs::write(&state, serialized).is_err() {
            crate::pylog::warning("ping_notes: cache write failed");
        }
    }

    if sent.is_empty() {
        return Err(PingErr::Noop(format!(
            "scanned {} note(s), none due in \u{b1}{}s",
            notes.len(),
            WINDOW_SEC
        )));
    }
    let preview = sent.iter().take(3).cloned().collect::<Vec<_>>().join("; ");
    let extra = if sent.len() > 3 {
        format!(" (+{} more)", sent.len() - 3)
    } else {
        String::new()
    };
    Ok((format!("Pinged {} note(s): {}{}", sent.len(), preview, extra), true))
}

/// Parse a cached ping timestamp: an rfc3339/offset string, a `Z`-suffixed UTC
/// string, or a naive ISO string treated as UTC (the Python `fromisoformat` +
/// `replace(tzinfo=utc)` for the naive case).
fn parse_due_or_iso(s: &str) -> Option<chrono::DateTime<chrono::Utc>> {
    use chrono::{DateTime, NaiveDateTime, TimeZone, Utc};
    if let Ok(dt) = DateTime::parse_from_rfc3339(s) {
        return Some(dt.with_timezone(&Utc));
    }
    let body = s.strip_suffix('Z').unwrap_or(s);
    let ndt = NaiveDateTime::parse_from_str(body, "%Y-%m-%dT%H:%M:%S%.f")
        .or_else(|_| NaiveDateTime::parse_from_str(body, "%Y-%m-%dT%H:%M:%S"))
        .or_else(|_| NaiveDateTime::parse_from_str(body, "%Y-%m-%dT%H:%M"))
        .or_else(|_| NaiveDateTime::parse_from_str(body, "%Y-%m-%d %H:%M:%S%.f"))
        .ok()?;
    Some(Utc.from_utc_datetime(&ndt))
}

// ---------------------------------------------------------------------------
// kwargs helper.
// ---------------------------------------------------------------------------

/// `kwargs.get(key, default)` coerced to a string (the action `**kwargs`
/// arrive as a `serde_json::Value` object). A non-string value falls back to
/// the default, matching the Python signature defaults (all are `: str = ...`).
fn kwarg_str(kwargs: &Value, key: &str, default: &str) -> String {
    kwargs
        .get(key)
        .and_then(|v| v.as_str())
        .unwrap_or(default)
        .to_string()
}

// ---------------------------------------------------------------------------
// Registries: BUILTIN_ACTIONS + BUILTIN_ACTION_INFO (Lazy IndexMap).
// ---------------------------------------------------------------------------

/// The boxed async action signature stored in `BUILTIN_ACTIONS`.
///
/// `async fn(owner, **kwargs) -> (result_str, success_bool)` -> a function
/// pointer returning a pinned boxed future. The scheduler dispatches by name and
/// matches the `Err(Outcome::..)` arm before generic errors.
pub type ActionFuture = std::pin::Pin<Box<dyn std::future::Future<Output = ActionResult> + Send>>;

/// `fn(owner: &str, kwargs: &Value) -> ActionFuture`.
pub type ActionFn = fn(&str, &Value) -> ActionFuture;

/// Wrap an `async fn(&str, &Value) -> ActionResult` into the boxed-future
/// function-pointer the registry stores. The owner/kwargs are cloned into the
/// future so it satisfies `'static + Send`.
macro_rules! action_entry {
    ($f:path) => {{
        fn wrapper(owner: &str, kwargs: &Value) -> ActionFuture {
            let owner = owner.to_string();
            let kwargs = kwargs.clone();
            Box::pin(async move { $f(&owner, &kwargs).await })
        }
        wrapper as ActionFn
    }};
}

/// `BUILTIN_ACTIONS` — action name -> async function. IndexMap preserves the
/// Python dict insertion order. `ping_events` and `ping_notes` are EXCLUDED from
/// this registry (the comments in the Python source: calendar reminders are
/// Notes; note pings run only inside `_note_pings_loop`).
pub static BUILTIN_ACTIONS: Lazy<IndexMap<&'static str, ActionFn>> = Lazy::new(|| {
    let mut m: IndexMap<&'static str, ActionFn> = IndexMap::new();
    m.insert("tidy_sessions", action_entry!(action_tidy_sessions));
    m.insert("tidy_documents", action_entry!(action_tidy_documents));
    m.insert("consolidate_memory", action_entry!(action_consolidate_memory));
    m.insert("tidy_research", action_entry!(action_tidy_research));
    m.insert("summarize_emails", action_entry!(action_summarize_emails));
    m.insert("draft_email_replies", action_entry!(action_draft_email_replies));
    m.insert("extract_email_events", action_entry!(action_extract_email_events));
    m.insert("classify_events", action_entry!(action_classify_events));
    m.insert("daily_brief", action_entry!(action_daily_brief));
    m.insert("learn_sender_signatures", action_entry!(action_learn_sender_signatures));
    m.insert("ssh_command", action_entry!(action_ssh_command));
    m.insert("run_script", action_entry!(action_run_script));
    m.insert("run_local", action_entry!(action_run_local));
    m.insert("test_skills", action_entry!(action_test_skills));
    m.insert("audit_skills", action_entry!(action_audit_skills));
    m.insert("check_email_urgency", action_entry!(action_check_email_urgency));
    m
});

/// `BUILTIN_ACTION_INFO` — descriptions for the UI/API. IndexMap preserves the
/// Python dict insertion order. (Note `run_local`, `daily_brief` etc. follow the
/// Python literal exactly — `run_local` has no entry there;
/// `learn_sender_signatures` / `check_email_urgency` do.)
pub static BUILTIN_ACTION_INFO: Lazy<IndexMap<&'static str, &'static str>> = Lazy::new(|| {
    let mut m = IndexMap::new();
    m.insert("tidy_sessions", "Clean up empty chat sessions and auto-sort into folders");
    m.insert("tidy_documents", "Remove junk/empty documents");
    m.insert("consolidate_memory", "Remove duplicate memories");
    m.insert("tidy_research", "Remove orphaned research files (sessions that were deleted)");
    m.insert("summarize_emails", "Pre-generate AI summaries for new inbox emails");
    m.insert("draft_email_replies", "Pre-draft AI reply suggestions for new inbox emails");
    m.insert(
        "extract_email_events",
        "Scan emails for booking/meeting confirmations and auto-add to calendar",
    );
    m.insert(
        "classify_events",
        "Tag upcoming events with importance (low/normal/high/critical) and type (work/health/travel/etc.); colors them too",
    );
    m.insert(
        "daily_brief",
        "Build a morning digest: today's calendar, unread email count + top senders, active todos",
    );
    m.insert(
        "learn_sender_signatures",
        "LLM learns each sender's signature from 3+ of their recent emails; cached per address so future renders fold sigs reliably without heuristics",
    );
    m.insert("ssh_command", "Run a shell command on a local or remote host");
    m.insert("run_script", "Run a script locally or on ODYSSEUS_SCRIPT_HOST");
    m.insert(
        "test_skills",
        "Run the per-skill Test on every skill: agent run + LLM judge \u{2192} records verdict on the skill (pass/needs_work/fail/inconclusive). Advisory only \u{2014} never rewrites or demotes anything.",
    );
    m.insert(
        "audit_skills",
        "Audit unaudited skills after enough new skills are added: test, narrow metadata, self-edit/retry, optional teacher rewrite, tag duplicates/trivial skills, and publish/draft using the auto-approve threshold.",
    );
    m.insert(
        "check_email_urgency",
        "Scan unread emails hourly, tag urgent/reply-soon/newsletter/marketing/spam, and send a reminder when a new email needs a fast reply.",
    );
    m
});

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn outcome_default_delay_is_1200() {
        assert_eq!(Outcome::DEFAULT_DEFERRED_DELAY, 1200);
        match Outcome::deferred("later") {
            Outcome::Deferred { delay_seconds, reason } => {
                assert_eq!(delay_seconds, 1200);
                assert_eq!(reason, "later");
            }
            _ => panic!("expected Deferred"),
        }
    }

    #[test]
    fn classify_heuristic_first_match_order() {
        // "surgery" is in BOTH health-types and critical -> (health, critical).
        assert_eq!(_classify_event_heuristic("Surgery prep"), (Some("health"), Some("critical")));
        // "flight to tokyo" -> travel + high.
        assert_eq!(_classify_event_heuristic("Flight to Tokyo"), (Some("travel"), Some("high")));
        // "team meeting" -> work, no high/critical.
        assert_eq!(_classify_event_heuristic("Team meeting"), (Some("work"), None));
        // unknown -> (None, None).
        assert_eq!(_classify_event_heuristic("xyzzy"), (None, None));
        // "deadline" matches admin (type) AND high (importance).
        assert_eq!(_classify_event_heuristic("Tax filing deadline"), (Some("admin"), Some("high")));
    }

    #[test]
    fn type_colors_dict_order_preserved() {
        let keys: Vec<&str> = TYPE_COLORS.keys().copied().collect();
        assert_eq!(
            keys,
            vec!["work", "personal", "health", "travel", "meal", "social", "admin", "other"]
        );
        assert_eq!(TYPE_COLORS.get("health"), Some(&"#e06c75"));
    }

    #[test]
    fn count_non_overlapping_parity() {
        // Python "aaaa".count("aa") == 2 (non-overlapping).
        assert_eq!(count_non_overlapping("aaaa", "aa"), 2);
        assert_eq!(count_non_overlapping("tagged 0 / moved 0", " 0"), 2);
        assert_eq!(count_non_overlapping("nope", " 0"), 0);
    }
}

#[cfg(test)]
mod web_tests {
    use super::*;

    #[test]
    fn result_has_work_zero_count_paths() {
        assert!(!_result_has_work(None));
        assert!(!_result_has_work(Some("")));
        assert!(!_result_has_work(Some("Processed 0 emails")));
        assert!(!_result_has_work(Some("No new emails to summarize")));
        assert!(!_result_has_work(Some("Nothing to do")));
        // Tagged 0 / Moved 0 -> two " 0" + "tagged"/"moved" -> false.
        assert!(!_result_has_work(Some("Tagged 0 / Moved 0")));
        // Real work.
        assert!(_result_has_work(Some("Tagged 3 / Moved 2")));
        assert!(_result_has_work(Some("Summarized 5 emails")));
    }

    #[test]
    fn builtin_actions_registry_order_and_exclusions() {
        let keys: Vec<&str> = BUILTIN_ACTIONS.keys().copied().collect();
        // ping_events / ping_notes excluded.
        assert!(!keys.contains(&"ping_events"));
        assert!(!keys.contains(&"ping_notes"));
        // mark_email_boundaries was removed from the registry (commit 0892466 —
        // "not good enough yet"; the action is retired, no longer dispatchable).
        assert!(!keys.contains(&"mark_email_boundaries"));
        // First and last entries match the Python dict literal.
        assert_eq!(keys.first(), Some(&"tidy_sessions"));
        assert_eq!(keys.last(), Some(&"check_email_urgency"));
        assert_eq!(keys.len(), 16);
    }

    #[test]
    fn action_info_has_expected_keys() {
        // run_local is NOT in BUILTIN_ACTION_INFO (matches Python literal).
        assert!(!BUILTIN_ACTION_INFO.contains_key("run_local"));
        // mark_email_boundaries was stripped from the info registry in 0892466.
        assert!(!BUILTIN_ACTION_INFO.contains_key("mark_email_boundaries"));
        assert!(BUILTIN_ACTION_INFO.contains_key("check_email_urgency"));
    }

    #[tokio::test]
    async fn skills_actions_refuse_empty_owner() {
        // `test_skills` / `audit_skills` are no longer deferred — they drive the
        // ported `routes::skills_pipeline` orchestration. The one branch that is
        // fully self-contained (no SkillsManager / endpoint / pipeline state) is
        // the #3 SCOPE GUARD: an empty owner must refuse loudly with a recorded
        // failure (`ok = false`), never `load(None)` across every user's skills.
        // Mirrors src/builtin_actions.py:1255-1256 / 1366-1367.
        let kw = json!({});

        let (msg, ok) = action_test_skills("", &kw).await.unwrap();
        assert!(!ok, "empty-owner test_skills must be a recorded failure");
        assert_eq!(
            msg,
            "test_skills requires an owner on the task — refusing to run without scope."
        );

        let (msg, ok) = action_audit_skills("", &kw).await.unwrap();
        assert!(!ok, "empty-owner audit_skills must be a recorded failure");
        assert_eq!(
            msg,
            "audit_skills requires an owner — refusing to run without scope."
        );
    }

    #[tokio::test]
    async fn ssh_command_empty_command() {
        let (msg, ok) = action_ssh_command("alice", &json!({})).await.unwrap();
        assert_eq!(msg, "No command specified");
        assert!(!ok);
    }

    #[tokio::test]
    async fn run_local_localhost_echo() {
        // run_local runs via /bin/sh -c; echo is portable.
        let (msg, ok) = action_run_local("alice", &json!({"script": "echo hello"})).await.unwrap();
        assert!(ok);
        assert_eq!(msg, "hello");
    }

    // ----- consolidate_memory per-owner grouping + dedupe helpers -----

    fn mem(id: &str, owner: &str, text: &str) -> Value {
        json!({"id": id, "owner": owner, "text": text})
    }

    #[test]
    fn memory_owner_strips_and_defaults_empty() {
        assert_eq!(memory_owner(&json!({"owner": "  alice "})), "alice");
        assert_eq!(memory_owner(&json!({"owner": ""})), "");
        // Missing owner / non-string owner -> "".
        assert_eq!(memory_owner(&json!({})), "");
        assert_eq!(memory_owner(&json!({"owner": 7})), "");
    }

    #[test]
    fn build_memory_groups_no_owner_groups_per_owner() {
        // Empty owner -> group PER-OWNER (not one big list), first-seen order,
        // empty-owner memories keep their own "" group (builtin_actions.py:93-98).
        let all = vec![
            mem("1", "alice", "a"),
            mem("2", "bob", "b"),
            mem("3", "alice", "c"),
            mem("4", "", "d"),
        ];
        let groups = build_memory_groups(&all, "");
        let keys: Vec<&str> = groups.keys().map(|s| s.as_str()).collect();
        assert_eq!(keys, vec!["alice", "bob", ""]);
        assert_eq!(groups["alice"], vec![0, 2]);
        assert_eq!(groups["bob"], vec![1]);
        assert_eq!(groups[""], vec![3]);
    }

    #[test]
    fn build_memory_groups_owner_set_exact_match_only() {
        // Owner set -> a SINGLE group of EXACT-owner matches; empty-owner
        // memories are NOT folded in (Python groups on `== owner_clean`).
        let all = vec![
            mem("1", "alice", "a"),
            mem("2", "", "b"),
            mem("3", "alice", "c"),
            mem("4", "bob", "d"),
        ];
        let groups = build_memory_groups(&all, "alice");
        let keys: Vec<&str> = groups.keys().map(|s| s.as_str()).collect();
        assert_eq!(keys, vec!["alice"]);
        assert_eq!(groups["alice"], vec![0, 2]);
        // No memories for the owner -> empty group dropped -> empty map.
        assert!(build_memory_groups(&all, "carol").is_empty());
    }

    #[test]
    fn dedupe_key_whitespace_normalized_lowercase() {
        assert_eq!(dedupe_key("  Hello   World "), "hello world");
        assert_eq!(dedupe_key("HELLO\tworld\n"), "hello world");
        // Differently-spaced/cased duplicates collapse to the same key.
        assert_eq!(dedupe_key("Foo  Bar"), dedupe_key("foo bar"));
        // Empty / whitespace-only -> empty key (the "(empty)" drop path).
        assert_eq!(dedupe_key("   "), "");
        assert_eq!(dedupe_key(""), "");
    }

    #[test]
    fn memory_text_limit_is_2000() {
        // The AI-tidy per-item char cap was raised from 600 to 2000 (with
        // truncation protection) — lock the constant.
        assert_eq!(MEMORY_TEXT_LIMIT, 2000);
    }

    #[test]
    fn sig_skip_prefixes_match_localpart_without_at() {
        // The role names must compare against the LOCAL-PART (no trailing "@"):
        // "support"/"info"/"admin" now match; the dead "support@" forms are gone.
        let pred = |addr: &str| -> bool {
            let local = addr.split_once('@').map(|(l, _)| l).unwrap_or(addr);
            SIG_SKIP_PREFIXES.iter().any(|p| local == *p || local.starts_with(p))
        };
        assert!(pred("support@example.com"));
        assert!(pred("info@example.com"));
        assert!(pred("admin@example.com"));
        // prefix match still works (support-team@..., noreply-foo@...).
        assert!(pred("support-team@example.com"));
        assert!(pred("noreply@example.com"));
        // A real person is NOT skipped.
        assert!(!pred("jane.doe@example.com"));
        // The dead "@"-suffixed forms were removed; confirm none remain.
        assert!(!SIG_SKIP_PREFIXES.iter().any(|p| p.ends_with('@')));
    }
}
