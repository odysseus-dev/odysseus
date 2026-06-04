// src/task_scheduler.rs  <- src/task_scheduler.py
//! Background scheduler for `ScheduledTask` execution.
//!
//! PORT classification (per the batch contract):
//!
//!   * PORT_NOW (pure):
//!       - `compute_next_run` (PARITY-CRITICAL date math — chrono + chrono-tz +
//!         the `cron` crate). See the long deviation note on the cron branch.
//!       - `_format_email_output`, `_is_email_output_target`.
//!       - `HOUSEKEEPING_DEFAULTS` / `RETIRED_HOUSEKEEPING_ACTIONS` /
//!         `_SILENT_ACTIONS` / `_MODEL_BACKED_ACTIONS` / `CHECKIN_MCP_PATTERNS`.
//!       - The `Outcome` control-flow enum (Python's `TaskNoop`/`TaskDeferred`
//!         `BaseException` sentinels), defined here and re-used by
//!         `builtin_actions.rs` (shared-decision: "reused from task_scheduler
//!         Outcome").
//!
//!   * PORT_VIA_DB: every SQLAlchemy `.query/.filter/.delete/func.count` becomes
//!     hand-written rusqlite SQL + row mappers for `scheduled_tasks`, `task_runs`,
//!     `crew_members`, `notes`, `calendar_events`, `sessions`, `chat_messages`,
//!     `model_endpoints`. The full executor machinery (`_loop` /
//!     `_check_due_tasks` / `_execute_task[_locked]` / notifications /
//!     `_run_semaphore` Semaphore(1) / `_executing_lock` / `_cached` singleflight
//!     / `_note_pings_loop` / `ensure_defaults` / `ensure_assistant_defaults` /
//!     `_deliver_task_result` session path) is ported faithfully.
//!
//!   * DEFER (honest, never fake success — Python already `try/except`-guards
//!     most of these, so the no-op preserves observable behavior):
//!       - `resolve_endpoint` IS ported (`endpoint_resolver::resolve_endpoint`,
//!         imported below and called for the `"research"` utility lookup); the
//!         saved-endpoint path is composed from `model_endpoints::endpoint_auth`
//!         + `endpoint_resolver::{normalize_base,build_headers}`. The fallback
//!         machinery (`resolve_utility_fallback_candidates` /
//!         `llm_call_async_with_fallback`) is still DEFER'd: it degrades to the
//!         single primary candidate / a single primary call.
//!       - `tool_index` RAG select -> `None` (= use all tools).
//!       - `event_bus` / `prefs` -> no-op / `{}`.
//!       - `_execute_action` dispatches the ported `builtin_actions` registry
//!         (whose own entries DEFER per that module).
//!
//! TaskNoop/TaskDeferred -> the [`Outcome`] enum, matched BEFORE the generic
//! error path in `_execute_task_locked`.
//!
//! Whole module is the async/HTTP tranche (tokio task spawning + the agent-loop
//! SSE + reqwest LLM + the rusqlite DB layer).

use crate::core::database::session_local;
use crate::error::{PyError, PyResult};
use crate::pydatetime;
use crate::pylog as logger;
use crate::src::endpoint_resolver::{build_headers, normalize_base, resolve_endpoint};
use chrono::{Datelike, Duration, NaiveDateTime, TimeZone, Timelike, Utc};
use indexmap::IndexMap;
use once_cell::sync::Lazy;
use rusqlite::{Connection, OptionalExtension};
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

// ===========================================================================
// Datetime helpers (the DB stores SQLAlchemy `"%Y-%m-%d %H:%M:%S[.%f]"` strings)
// ===========================================================================

/// Parse a stored SQLite datetime string into a naive `NaiveDateTime`.
/// (`None` for NULL / unparseable.)
fn parse_dt(s: Option<&str>) -> Option<NaiveDateTime> {
    let s = s?;
    NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S%.f")
        .or_else(|_| NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S"))
        .ok()
}

/// Format a naive datetime back to the SQLAlchemy SQLite datetime string
/// (`datetime.utcnow()` stores microsecond precision).
fn fmt_dt(dt: NaiveDateTime) -> String {
    pydatetime::naive_to_iso(dt)
}

/// `datetime.utcnow()` as a naive datetime.
fn utcnow() -> NaiveDateTime {
    pydatetime::utcnow_naive()
}

// ===========================================================================
// compute_next_run — PARITY-CRITICAL pure date math
// ===========================================================================

/// Translate a 5-field croniter expression into the 6-field, 1-7-DOW form the
/// `cron` crate expects.
///
/// DOCUMENTED DEVIATION (verified against the source crate): the `cron` crate
/// parses the day-of-week field as **1=Sunday .. 7=Saturday** and requires a
/// leading seconds field, whereas `croniter` uses the standard cron **0=Sunday
/// .. 6=Saturday (7 also = Sunday)** with no seconds field. We:
///   1. prepend a `0` seconds field (run at second 0, matching croniter's
///      minute granularity), and
///   2. remap the DOW field numbers `n -> (n % 7) + 1` so `0/7->Sun=1`,
///      `1->Mon=2`, … `6->Sat=7`.
/// Named DOW tokens (`mon`, `sun`, …) and `*` pass through unchanged (the crate
/// understands the names; `*` is field-agnostic). All `HOUSEKEEPING_DEFAULTS`
/// crons use `*` for DOW, so the remap only affects user-authored DOW crons.
fn croniter_to_cron_crate(expr: &str) -> Option<String> {
    let fields: Vec<&str> = expr.split_whitespace().collect();
    // croniter accepts 5 (min hour dom mon dow) or 6 (with seconds) fields; the
    // cron crate wants 6 (sec min hour dom mon dow). Normalize to 6.
    let (sec, rest): (&str, &[&str]) = if fields.len() == 6 {
        (fields[0], &fields[1..])
    } else if fields.len() == 5 {
        ("0", &fields[..])
    } else {
        return None;
    };
    if rest.len() != 5 {
        return None;
    }
    let min = rest[0];
    let hour = rest[1];
    let dom = rest[2];
    let mon = rest[3];
    let dow = remap_dow_field(rest[4]);
    Some(format!("{sec} {min} {hour} {dom} {mon} {dow}"))
}

/// Remap each numeric token in a croniter DOW field (0-7, Sun=0/7) to the cron
/// crate's 1-7 (Sun=1). Handles lists (`1,3`), ranges (`1-5`), steps (`*/2`,
/// `1-5/2`) and bare numbers. Non-numeric tokens (names, `*`) pass through.
fn remap_dow_field(field: &str) -> String {
    if field == "*" {
        return "*".to_string();
    }
    field
        .split(',')
        .map(remap_dow_token)
        .collect::<Vec<_>>()
        .join(",")
}

fn remap_dow_token(token: &str) -> String {
    // Step suffix: "<base>/<step>"
    if let Some((base, step)) = token.split_once('/') {
        return format!("{}/{}", remap_dow_token(base), step);
    }
    // Range: "<lo>-<hi>"
    if let Some((lo, hi)) = token.split_once('-') {
        return format!("{}-{}", remap_dow_num(lo), remap_dow_num(hi));
    }
    remap_dow_num(token)
}

fn remap_dow_num(s: &str) -> String {
    match s.trim().parse::<u32>() {
        Ok(n) => ((n % 7) + 1).to_string(),
        Err(_) => s.to_string(),
    }
}

/// Compute the next run datetime (stored as naive UTC) based on schedule type.
///
/// If `tz_name` is provided (IANA zone), `scheduled_time` / `scheduled_day` are
/// interpreted as local wall-clock time in that zone and the result converted to
/// naive UTC for DB storage. If `tz_name` is `None`, the legacy behavior
/// (`scheduled_time` interpreted as naive-UTC wall clock) is preserved.
#[allow(clippy::too_many_arguments)]
pub fn compute_next_run(
    schedule: Option<&str>,
    scheduled_time: Option<&str>,
    scheduled_day: Option<i64>,
    scheduled_date: Option<NaiveDateTime>,
    after: Option<NaiveDateTime>,
    cron_expression: Option<&str>,
    tz_name: Option<&str>,
) -> Option<NaiveDateTime> {
    use chrono_tz::Tz;

    // `tz = ZoneInfo(tz_name)` guarded by the `if tz_name and ZoneInfo` check;
    // an invalid zone falls back to `tz = None` (legacy naive-UTC behavior).
    let tz: Option<Tz> = match tz_name {
        Some(name) if !name.is_empty() => name.parse::<Tz>().ok(),
        _ => None,
    };

    // "now" used for comparisons. With tz, work entirely in local tz and convert
    // to UTC at the end. Otherwise use naive UTC (legacy).
    //
    // We keep `now_naive` as the local wall-clock naive datetime for `.replace()`
    // / comparison arithmetic, plus the tz so we can convert candidates to UTC.
    let now_utc = after.unwrap_or_else(utcnow);
    let now_naive: NaiveDateTime = match tz {
        Some(z) => {
            // now_utc interpreted as UTC, then .astimezone(tz); take the naive
            // local wall clock for arithmetic.
            let aware = Utc.from_utc_datetime(&now_utc);
            aware.with_timezone(&z).naive_local()
        }
        None => now_utc,
    };

    // Convert a local naive wall-clock candidate to naive UTC for storage.
    let to_utc_naive = |cand: NaiveDateTime| -> NaiveDateTime {
        match tz {
            Some(z) => {
                // Interpret `cand` as wall-clock in `z`, convert to UTC, drop tz.
                match z.from_local_datetime(&cand).earliest() {
                    Some(aware) => aware.with_timezone(&Utc).naive_utc(),
                    None => cand,
                }
            }
            None => cand,
        }
    };

    // ── cron ────────────────────────────────────────────────────────────────
    if schedule == Some("cron") {
        if let Some(expr) = cron_expression.filter(|e| !e.is_empty()) {
            use cron::Schedule;
            use std::str::FromStr;
            let translated = match croniter_to_cron_crate(expr) {
                Some(t) => t,
                None => {
                    logger::warning(&format!(
                        "Invalid cron expression '{expr}': unsupported field count"
                    ));
                    return None;
                }
            };
            let sched = match Schedule::from_str(&translated) {
                Ok(s) => s,
                Err(e) => {
                    logger::warning(&format!("Invalid cron expression '{expr}': {e}"));
                    return None;
                }
            };
            // croniter(expr, now).get_next(datetime) -> first time strictly after
            // `now`. We compute in the relevant zone's wall clock.
            let nxt_naive: Option<NaiveDateTime> = match tz {
                Some(z) => {
                    let now_aware = z.from_local_datetime(&now_naive).earliest()?;
                    sched.after(&now_aware).next().map(|dt| dt.naive_local())
                }
                None => {
                    let now_aware = Utc.from_utc_datetime(&now_naive);
                    sched.after(&now_aware).next().map(|dt| dt.naive_utc())
                }
            };
            return nxt_naive.map(to_utc_naive);
        }
        // `schedule == "cron"` with no expression falls through (Python returns
        // None after the trailing branches when scheduled_time is also None).
    }

    // ── once ──────────────────────────────────────────────────────────────
    if schedule == Some("once") {
        // Python compares scheduled_date > now.replace(tzinfo=None) (i.e. the
        // local naive wall clock when tz set, else naive UTC). We compare
        // against `now_naive` which is exactly that.
        if let Some(sd) = scheduled_date {
            if sd > now_naive {
                return Some(sd);
            }
        }
        return None;
    }

    // `if not scheduled_time: return None`
    let stime = scheduled_time.filter(|s| !s.is_empty())?;

    // Parse HH:MM
    let mut parts = stime.split(':');
    let hour: u32 = parts.next().and_then(|p| p.parse().ok())?;
    let minute: u32 = parts.next().and_then(|p| p.parse().ok())?;

    // ── daily ───────────────────────────────────────────────────────────────
    if schedule == Some("daily") {
        let mut candidate = replace_hms(now_naive, hour, minute);
        if candidate <= now_naive {
            candidate += Duration::days(1);
        }
        return Some(to_utc_naive(candidate));
    }

    // ── weekly ──────────────────────────────────────────────────────────────
    if schedule == Some("weekly") {
        let day = scheduled_day.unwrap_or(0); // 0=Monday
        let mut candidate = replace_hms(now_naive, hour, minute);
        // candidate.weekday() in Python: Monday=0 .. Sunday=6
        let cand_weekday = candidate.weekday().num_days_from_monday() as i64;
        let mut days_ahead = day - cand_weekday;
        if days_ahead < 0 || (days_ahead == 0 && candidate <= now_naive) {
            days_ahead += 7;
        }
        candidate += Duration::days(days_ahead);
        return Some(to_utc_naive(candidate));
    }

    // ── monthly ─────────────────────────────────────────────────────────────
    if schedule == Some("monthly") {
        let day = scheduled_day.unwrap_or(1);
        // candidate = now.replace(day=day, hour, minute, second=0, microsecond=0)
        // — ValueError (invalid day for month) falls back to `candidate = now`.
        let mut candidate = match replace_day_hms(now_naive, day as u32, hour, minute) {
            Some(c) => c,
            None => now_naive,
        };
        if candidate <= now_naive {
            // next_month = first day of the following month
            let next_month = first_of_next_month(now_naive);
            candidate = match replace_day_hms(next_month, day as u32, hour, minute) {
                Some(c) => c,
                None => {
                    // last day of next_month: first-of-(month-after-next) - 1 day
                    let after_next = first_of_next_month(next_month);
                    let last = after_next - Duration::days(1);
                    replace_hms(last, hour, minute)
                }
            };
        }
        return Some(to_utc_naive(candidate));
    }

    None
}

/// `dt.replace(hour, minute, second=0, microsecond=0)`.
fn replace_hms(dt: NaiveDateTime, hour: u32, minute: u32) -> NaiveDateTime {
    dt.date()
        .and_hms_opt(hour, minute, 0)
        .unwrap_or_else(|| dt.date().and_hms_opt(0, 0, 0).unwrap())
}

/// `dt.replace(day, hour, minute, second=0, microsecond=0)` — `None` on an
/// invalid day-of-month (the Python `ValueError`).
fn replace_day_hms(dt: NaiveDateTime, day: u32, hour: u32, minute: u32) -> Option<NaiveDateTime> {
    let d = chrono::NaiveDate::from_ymd_opt(dt.year(), dt.month(), day)?;
    d.and_hms_opt(hour, minute, 0)
}

/// First day of the month after `dt` (at the same hour/min — actually day=1,
/// keeping the time of `dt`, matching Python's `now.replace(month=…, day=1)`).
fn first_of_next_month(dt: NaiveDateTime) -> NaiveDateTime {
    let (y, m) = if dt.month() == 12 {
        (dt.year() + 1, 1)
    } else {
        (dt.year(), dt.month() + 1)
    };
    let d = chrono::NaiveDate::from_ymd_opt(y, m, 1).unwrap();
    d.and_hms_opt(dt.hour(), dt.minute(), dt.second())
        .unwrap_or_else(|| d.and_hms_opt(0, 0, 0).unwrap())
}

// ===========================================================================
// _resolve_task_timezone (PORT_VIA_DB)
// ===========================================================================

/// Look up the IANA timezone name for a task via its linked CrewMember, if any.
fn resolve_task_timezone(db: &Connection, task: &ScheduledTaskRow) -> Option<String> {
    let cmid = task.crew_member_id.as_deref()?;
    let tz: Option<String> = db
        .query_row(
            "SELECT timezone FROM crew_members WHERE id = ?1",
            [cmid],
            |r| r.get::<_, Option<String>>(0),
        )
        .optional()
        .ok()
        .flatten()
        .flatten();
    tz.filter(|t| !t.is_empty())
}

// ===========================================================================
// Module-level constants
// ===========================================================================

/// One housekeeping default's seed/normalize spec.
#[derive(Clone)]
pub struct HousekeepingDef {
    pub name: &'static str,
    pub trigger_type: &'static str,
    pub trigger_event: Option<&'static str>,
    pub trigger_count: Option<i64>,
    pub schedule: Option<&'static str>,
    pub scheduled_time: Option<&'static str>,
    pub cron_expression: Option<&'static str>,
    pub ship_paused: bool,
    pub old_cron_expressions: &'static [&'static str],
    pub legacy_names: &'static [&'static str],
}

impl HousekeepingDef {
    const fn new() -> Self {
        HousekeepingDef {
            name: "",
            trigger_type: "schedule",
            trigger_event: None,
            trigger_count: None,
            schedule: None,
            scheduled_time: None,
            cron_expression: None,
            ship_paused: false,
            old_cron_expressions: &[],
            legacy_names: &[],
        }
    }
}

/// Built-in "housekeeping" tasks seeded for every owner, keyed by action.
/// Insertion order preserved (IndexMap) to match the Python dict iteration.
pub static HOUSEKEEPING_DEFAULTS: Lazy<IndexMap<&'static str, HousekeepingDef>> = Lazy::new(|| {
    let mut m: IndexMap<&'static str, HousekeepingDef> = IndexMap::new();
    m.insert(
        "tidy_sessions",
        HousekeepingDef {
            name: "Chat Sessions Tidy",
            trigger_type: "event",
            trigger_event: Some("session_created"),
            trigger_count: Some(5),
            legacy_names: &["Tidy Chat Sessions"],
            ..HousekeepingDef::new()
        },
    );
    m.insert(
        "tidy_documents",
        HousekeepingDef {
            name: "Documents Tidy",
            trigger_type: "event",
            trigger_event: Some("document_created"),
            trigger_count: Some(5),
            legacy_names: &["Tidy Documents"],
            ..HousekeepingDef::new()
        },
    );
    m.insert(
        "consolidate_memory",
        HousekeepingDef {
            name: "Memory Tidy",
            trigger_type: "event",
            trigger_event: Some("memory_added"),
            trigger_count: Some(5),
            legacy_names: &["Tidy Memory"],
            ..HousekeepingDef::new()
        },
    );
    m.insert(
        "tidy_research",
        HousekeepingDef {
            name: "Research Tidy",
            trigger_type: "event",
            trigger_event: Some("research_completed"),
            trigger_count: Some(5),
            legacy_names: &["Tidy Research"],
            ..HousekeepingDef::new()
        },
    );
    m.insert(
        "summarize_emails",
        HousekeepingDef {
            name: "Email (Summary)",
            schedule: Some("cron"),
            cron_expression: Some("0 */2 * * *"),
            ship_paused: true,
            legacy_names: &["Tidy Email (Summary)"],
            ..HousekeepingDef::new()
        },
    );
    m.insert(
        "draft_email_replies",
        HousekeepingDef {
            name: "Email AI Auto Reply",
            schedule: Some("cron"),
            cron_expression: Some("0 */2 * * *"),
            ship_paused: true,
            legacy_names: &["Tidy Email (Replies)", "AI Auto Reply"],
            ..HousekeepingDef::new()
        },
    );
    m.insert(
        "extract_email_events",
        HousekeepingDef {
            name: "Email Calendar Events",
            schedule: Some("cron"),
            cron_expression: Some("0 */1 * * *"),
            ship_paused: true,
            legacy_names: &["Email → Calendar Events"],
            ..HousekeepingDef::new()
        },
    );
    m.insert(
        "classify_events",
        HousekeepingDef {
            name: "Calendar Classify Events",
            schedule: Some("cron"),
            cron_expression: Some("0 6,18 * * *"),
            ship_paused: true,
            legacy_names: &["Classify Calendar Events"],
            ..HousekeepingDef::new()
        },
    );
    m.insert(
        "check_email_urgency",
        HousekeepingDef {
            name: "Email Tags",
            schedule: Some("cron"),
            cron_expression: Some("0 * * * *"),
            ship_paused: true,
            old_cron_expressions: &["*/15 * * * *"],
            legacy_names: &["Email Triage", "Urgent Email"],
            ..HousekeepingDef::new()
        },
    );
    m.insert(
        "audit_skills",
        HousekeepingDef {
            name: "Skills Audit",
            trigger_type: "event",
            trigger_event: Some("skill_added"),
            trigger_count: Some(5),
            legacy_names: &["Audit Skills"],
            ..HousekeepingDef::new()
        },
    );
    m
});

/// `RETIRED_HOUSEKEEPING_ACTIONS = frozenset({...})`.
pub static RETIRED_HOUSEKEEPING_ACTIONS: Lazy<HashSet<&'static str>> =
    Lazy::new(|| ["tidy_calendar", "tidy_email_inbox", "mark_email_boundaries"].into_iter().collect());

/// Built-in housekeeping actions whose output is pure infra — kept out of the
/// assistant chat session.
static SILENT_ACTIONS: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    [
        "check_email_urgency",
        "learn_sender_signatures",
        "summarize_emails",
        "draft_email_replies",
        "extract_email_events",
        "classify_events",
        "tidy_sessions",
        "tidy_documents",
        "consolidate_memory",
        "tidy_research",
        "test_skills",
        "audit_skills",
    ]
    .into_iter()
    .collect()
});

/// Actions that should wait in the model queue (Semaphore(1)).
static MODEL_BACKED_ACTIONS: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    [
        "summarize_emails",
        "draft_email_replies",
        "extract_email_events",
        "classify_events",
        "learn_sender_signatures",
        "check_email_urgency",
        "test_skills",
        "audit_skills",
        "consolidate_memory",
    ]
    .into_iter()
    .collect()
});

/// One MCP check-in source pattern.
///
/// The fields are read by the check-in MCP auto-discovery loop in
/// `execute_checkin` (`detect`/`section`/`tool`/`args`/`label_from_identity`).
/// `formatter` is declared to mirror the Python pattern table but, like the
/// Python `_execute_checkin`, is not consumed by the loop (the raw output is
/// stored), so it is read via a discard there.
pub struct CheckinPattern {
    pub detect: &'static str,
    pub section: &'static str,
    pub tool: &'static str,
    pub args: fn() -> Value,
    pub label_from_identity: bool,
    pub formatter: Option<&'static str>,
}

/// `CHECKIN_MCP_PATTERNS` — pattern-based MCP check-in source discovery.
pub static CHECKIN_MCP_PATTERNS: Lazy<Vec<CheckinPattern>> = Lazy::new(|| {
    vec![
        CheckinPattern {
            detect: "list_emails",
            section: "Email",
            tool: "list_emails",
            args: || json!({"mailbox": "INBOX", "limit": 10, "unread_only": true}),
            label_from_identity: true,
            formatter: Some("_format_email_output"),
        },
        CheckinPattern {
            detect: "search_emails",
            section: "Email",
            tool: "search_emails",
            args: || json!({"query": "is:unread", "limit": 10}),
            label_from_identity: true,
            formatter: Some("_format_email_output"),
        },
        CheckinPattern {
            detect: "get_feed",
            section: "RSS",
            tool: "get_feed",
            args: || json!({}),
            label_from_identity: false,
            formatter: None,
        },
        CheckinPattern {
            detect: "list_feeds",
            section: "RSS",
            tool: "list_feeds",
            args: || json!({}),
            label_from_identity: false,
            formatter: None,
        },
        CheckinPattern {
            detect: "list_messages",
            section: "Messages",
            tool: "list_messages",
            args: || json!({"limit": 10}),
            label_from_identity: true,
            formatter: None,
        },
    ]
});

// ===========================================================================
// _format_email_output / _is_email_output_target (pure)
// ===========================================================================

static EMAIL_LINE_RE: Lazy<regex::Regex> = Lazy::new(|| {
    // [?\d+]? \s* (?:↩️|📎|🔵|⭐\s*)? (.+?) (?:\s*From:\s*(.+?))? (?:\s*\|\s*(\S+))?$
    regex::Regex::new(
        r"^\[?\d+\]?\s*(?:\x{21A9}\x{FE0F}\s*|\x{1F4CE}\s*|\x{1F535}\s*|\x{2B50}\s*)?(.+?)(?:\s*From:\s*(.+?))?(?:\s*\|\s*(\S+))?$",
    )
    .unwrap()
});

static EMAIL_CLEAN_RE: Lazy<regex::Regex> = Lazy::new(|| {
    // ^\[?\d+\]?\s*(?:↩️\s*|📎\s*)?
    regex::Regex::new(r"^\[?\d+\]?\s*(?:\x{21A9}\x{FE0F}\s*|\x{1F4CE}\s*)?").unwrap()
});

static EMAIL_TARGET_RE: Lazy<regex::Regex> =
    Lazy::new(|| regex::Regex::new(r"^[^@\s]+@[^@\s]+\.[^@\s]+$").unwrap());

/// Clean up raw MCP email list output into readable format.
pub fn format_email_output(raw: &str) -> String {
    let mut lines: Vec<String> = Vec::new();
    for line in raw.split('\n') {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        // Skip header lines like "📬 [INBOX] 856 emails..."
        if line.starts_with('\u{1F4EC}')
            || line.starts_with("No emails")
            || line.starts_with("---")
            || line.starts_with("Page ")
        {
            continue;
        }
        // Skip "more pages available" etc
        if line.to_lowercase().contains("page") && line.contains('/') {
            continue;
        }
        if let Some(m) = EMAIL_LINE_RE.captures(line) {
            let subject = m
                .get(1)
                .map(|g| g.as_str().trim().trim_end_matches('|').trim().to_string())
                .unwrap_or_default();
            let sender = m
                .get(2)
                .map(|g| g.as_str().trim().trim_end_matches('|').trim().to_string())
                .unwrap_or_default();
            if !sender.is_empty() {
                lines.push(format!("- {sender} — {subject}"));
            } else {
                lines.push(format!("- {subject}"));
            }
        } else if line.starts_with('[') || line.starts_with('-') {
            // Generic cleanup: line.lstrip('- ') then strip the [n] prefix.
            let lstripped = line.trim_start_matches(['-', ' ']);
            let cleaned = EMAIL_CLEAN_RE.replace(lstripped, "");
            let cleaned = cleaned.trim();
            if !cleaned.is_empty() {
                lines.push(format!("- {cleaned}"));
            }
        }
    }
    if lines.is_empty() {
        return "No unread emails".to_string();
    }
    lines.into_iter().take(10).collect::<Vec<_>>().join("\n")
}

/// `_is_email_output_target(output)`.
pub fn is_email_output_target(output: &str) -> bool {
    let target = output.trim();
    if target == "email" || target == "email:self" {
        return true;
    }
    if target.starts_with("email:") {
        return true;
    }
    EMAIL_TARGET_RE.is_match(target)
}

/// Python `s[:n]` — keep at most the first `n` Unicode code points. Used for the
/// `stderr[:400]` / `stdout[:200]` log truncations and the `content[:3000]`
/// check-in snapshot cap (Python slices strings by code point, not byte).
fn truncate_chars(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// Inlined `routes.email_routes._resolve_send_config(account_id, owner)`
/// (email_routes.py:229-256). The Python lives MODULE-PRIVATE as
/// `routes::email_routes::resolve_send_config` (a `fn`, not importable from
/// here), so — to keep this email-wiring pass scoped to a single file — its
/// exact logic is mirrored here rather than depending on a cross-file
/// visibility change. Behavior is identical to that ported fn:
///   1. resolve the requested/default account via the ported
///      `email_helpers::get_email_config`; if it is SMTP-ready, return it;
///   2. an explicit `account_id` that is not SMTP-ready -> clear error;
///   3. otherwise walk this owner's enabled accounts
///      (`is_default DESC, created_at ASC`) and return the first SMTP-capable
///      one (the DB block is the Python `try/except -> debug log + continue`);
///   4. none -> `"No SMTP-capable email account configured"`.
fn resolve_send_config(
    account_id: Option<&str>,
    owner: &str,
) -> Result<crate::routes::email_helpers::EmailConfig, String> {
    use crate::routes::email_helpers::{get_email_config, EmailConfig};
    // `_smtp_ready(cfg)` — host + user + password all present.
    fn smtp_ready(cfg: &EmailConfig) -> bool {
        !cfg.smtp_host.is_empty() && !cfg.smtp_user.is_empty() && !cfg.smtp_password.is_empty()
    }

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
    // Enabled-account fallback walk (owner-scoped), `is_default DESC, created_at ASC`.
    if let Ok(conn) = session_local() {
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

// ===========================================================================
// Outcome — Python's TaskNoop / TaskDeferred BaseException sentinels
// ===========================================================================
//
// CROSS-MODULE: the shared-decision note in the batch contract suggested
// task_scheduler would own `Outcome`, but the parallel `builtin_actions.rs`
// landed with its OWN canonical `Outcome` (Noop / Deferred) + the
// `ActionResult = Result<(String,bool), Outcome>` action contract. To avoid a
// duplicate type, the scheduler reuses THAT one. We re-export it here so callers
// reading `task_scheduler::Outcome` get the same type.

pub use crate::src::builtin_actions::Outcome;

/// Internal control-flow result threaded out of the per-task executor — models
/// the Python `try/except TaskDeferred/TaskNoop` flow with the (result, success)
/// happy path. Matched BEFORE the generic error path in `execute_task_inner`.
enum ExecResult {
    /// `(result_text, success)` — LLM/research/action normal return.
    ///
    /// Action tasks reach the `success=false` arm via `_execute_action`'s own
    /// `except Exception` (which returns `(str(e), False)`), so a failed action
    /// flows through the *normal* error path (run_count++, notify with the task
    /// name, raw error string).
    Done(String, bool),
    /// `raise TaskNoop(reason)`.
    Noop(String),
    /// `raise TaskDeferred(reason, delay_seconds)`.
    Deferred { reason: String, delay_seconds: i64 },
    /// An LLM/research executor *raised* (no swallow). Routed through the
    /// Python OUTER `except Exception` block in `_execute_task_locked`
    /// (task_scheduler.py:706-789): does NOT increment run_count, titles the
    /// notification `Task {task_id}`, persists `error = f"{type(e).__name__}:
    /// {e}"[:2000]`, and advances next_run — distinct from the normal path.
    Raised(PyError),
}

// ===========================================================================
// DB row mappers (PORT_VIA_DB)
// ===========================================================================

/// A `scheduled_tasks` row — only the columns the scheduler touches.
#[derive(Clone, Debug)]
pub struct ScheduledTaskRow {
    pub id: String,
    pub owner: Option<String>,
    pub name: String,
    pub prompt: Option<String>,
    pub task_type: Option<String>,
    pub action: Option<String>,
    pub schedule: Option<String>,
    pub scheduled_time: Option<String>,
    pub scheduled_day: Option<i64>,
    pub scheduled_date: Option<NaiveDateTime>,
    pub trigger_type: Option<String>,
    pub trigger_event: Option<String>,
    pub trigger_count: Option<i64>,
    pub next_run: Option<NaiveDateTime>,
    pub status: Option<String>,
    pub output_target: Option<String>,
    pub session_id: Option<String>,
    pub model: Option<String>,
    pub endpoint_url: Option<String>,
    pub run_count: Option<i64>,
    pub cron_expression: Option<String>,
    pub then_task_id: Option<String>,
    pub crew_member_id: Option<String>,
    pub max_steps: Option<i64>,
    pub notifications_enabled: Option<bool>,
    pub created_at: Option<NaiveDateTime>,
}

const TASK_COLS: &str = "id, owner, name, prompt, task_type, action, schedule, scheduled_time, \
    scheduled_day, scheduled_date, trigger_type, trigger_event, trigger_count, next_run, status, \
    output_target, session_id, model, endpoint_url, run_count, cron_expression, then_task_id, \
    crew_member_id, max_steps, notifications_enabled, created_at";

fn map_task(r: &rusqlite::Row<'_>) -> rusqlite::Result<ScheduledTaskRow> {
    Ok(ScheduledTaskRow {
        id: r.get(0)?,
        owner: r.get(1)?,
        name: r.get::<_, Option<String>>(2)?.unwrap_or_default(),
        prompt: r.get(3)?,
        task_type: r.get(4)?,
        action: r.get(5)?,
        schedule: r.get(6)?,
        scheduled_time: r.get(7)?,
        scheduled_day: r.get(8)?,
        scheduled_date: parse_dt(r.get::<_, Option<String>>(9)?.as_deref()),
        trigger_type: r.get(10)?,
        trigger_event: r.get(11)?,
        trigger_count: r.get(12)?,
        next_run: parse_dt(r.get::<_, Option<String>>(13)?.as_deref()),
        status: r.get(14)?,
        output_target: r.get(15)?,
        session_id: r.get(16)?,
        model: r.get(17)?,
        endpoint_url: r.get(18)?,
        run_count: r.get(19)?,
        cron_expression: r.get(20)?,
        then_task_id: r.get(21)?,
        crew_member_id: r.get(22)?,
        max_steps: r.get(23)?,
        notifications_enabled: r.get::<_, Option<bool>>(24)?,
        created_at: parse_dt(r.get::<_, Option<String>>(25)?.as_deref()),
    })
}

fn load_task(db: &Connection, task_id: &str) -> Option<ScheduledTaskRow> {
    let sql = format!("SELECT {TASK_COLS} FROM scheduled_tasks WHERE id = ?1");
    db.query_row(&sql, [task_id], map_task).optional().ok().flatten()
}

/// A `crew_members` row — only the columns the executor uses.
#[derive(Clone, Debug, Default)]
pub struct CrewRow {
    pub id: String,
    pub personality: Option<String>,
    pub model: Option<String>,
    pub endpoint_url: Option<String>,
    pub enabled_tools: Option<String>,
    pub is_default_assistant: bool,
}

fn load_crew(db: &Connection, crew_id: &str) -> Option<CrewRow> {
    db.query_row(
        "SELECT id, personality, model, endpoint_url, enabled_tools, is_default_assistant \
         FROM crew_members WHERE id = ?1",
        [crew_id],
        |r| {
            Ok(CrewRow {
                id: r.get(0)?,
                personality: r.get(1)?,
                model: r.get(2)?,
                endpoint_url: r.get(3)?,
                enabled_tools: r.get(4)?,
                is_default_assistant: r.get::<_, Option<bool>>(5)?.unwrap_or(false),
            })
        },
    )
    .optional()
    .ok()
    .flatten()
}

// ===========================================================================
// TaskScheduler
// ===========================================================================

/// Shared singleton state. The Python `TaskScheduler` keeps these on `self`; the
/// Rust port wraps each mutable field in a `Mutex`/`tokio::sync::Mutex` so the
/// scheduler can be cloned (`Arc`) across spawned tasks and request handlers.
pub struct TaskScheduler {
    running: AtomicBool,
    /// Task IDs currently running OR queued behind the model slot. Guarded by an
    /// async mutex (`_executing_lock`).
    executing: tokio::sync::Mutex<HashSet<String>>,
    pending_notifications: Mutex<Vec<Value>>,
    task_defer_counts: Mutex<HashMap<String, i64>>,
    /// Semaphore(1): exactly one model-backed task runs at a time.
    run_semaphore: tokio::sync::Semaphore,
    concurrency_cap: i64,
    /// Resolved model of the most recent run (cleared each run). Per-task local
    /// in Python (`self._last_run_model`); here a `Mutex` since runs can overlap
    /// only behind the semaphore for model tasks, but action tasks run free —
    /// so we thread the value explicitly through the executor instead and keep
    /// this only for API completeness.
    _last_run_model: Mutex<Option<String>>,
    /// `_task_handles` — maps task_id -> tokio `AbortHandle` for the spawned
    /// execution future. Populated at every `tokio::spawn(execute_task(...))` site
    /// (`check_due_tasks`, `run_task_now`, `run_chained`). Consumed by `stop_task`
    /// to abort the running/queued task, mirroring the Python
    /// `self._task_handles[task_id] = asyncio.current_task()` / `.cancel()` pattern.
    task_handles: tokio::sync::Mutex<HashMap<String, tokio::task::AbortHandle>>,
}

/// `_shared_cache` singleflight TTL cache (module-level in Python). Kept as a
/// process-global behind a `tokio::sync::Mutex`. Consumed by the (DEFERRED)
/// check-in integrations/MCP snapshot fetches — `#[allow(dead_code)]` until they
/// land.
#[allow(dead_code)]
static SHARED_CACHE: Lazy<tokio::sync::Mutex<HashMap<String, (f64, Value)>>> =
    Lazy::new(|| tokio::sync::Mutex::new(HashMap::new()));

/// `_cached(key, ttl, fetch)` — return a fresh cached result for `key`, else call
/// `fetch()` and store.
///
/// DEVIATION: the Python coordinates concurrent callers via an `asyncio.Future`
/// "owner" handoff so a missing key triggers exactly one `fetch()`. Reproducing
/// the in-flight-future sharing in Rust would require a per-key oneshot
/// registry; the observable result (a fresh value cached for `ttl`) is the same,
/// so this keeps the TTL cache but lets two simultaneous misses each run
/// `fetch()` (the last writer wins). Used only for the best-effort check-in
/// snapshots, where a duplicate fetch is harmless.
#[allow(dead_code)]
async fn cached<F, Fut>(key: String, ttl: f64, fetch: F) -> PyResult<Value>
where
    F: FnOnce() -> Fut,
    Fut: std::future::Future<Output = PyResult<Value>>,
{
    let now = crate::pytime::time();
    {
        let cache = SHARED_CACHE.lock().await;
        if let Some((expiry, val)) = cache.get(&key) {
            if *expiry > now {
                return Ok(val.clone());
            }
        }
    }
    let val = fetch().await?;
    {
        let mut cache = SHARED_CACHE.lock().await;
        cache.insert(key, (crate::pytime::time() + ttl, val.clone()));
    }
    Ok(val)
}

impl TaskScheduler {
    /// `__init__(self, session_manager)`.
    ///
    /// DEVIATION: the Rust port has no live in-process `SessionManager` mirror
    /// (`crate::core::session_manager` is DB-backed and stateless per call), so
    /// the executor writes session/message rows directly via SQL — the
    /// `session_manager.sessions[...]` in-memory cache updates are no-ops.
    pub fn new() -> Self {
        TaskScheduler {
            running: AtomicBool::new(false),
            executing: tokio::sync::Mutex::new(HashSet::new()),
            pending_notifications: Mutex::new(Vec::new()),
            task_defer_counts: Mutex::new(HashMap::new()),
            run_semaphore: tokio::sync::Semaphore::new(1),
            concurrency_cap: 1,
            _last_run_model: Mutex::new(None),
            task_handles: tokio::sync::Mutex::new(HashMap::new()),
        }
    }

    /// `add_notification(task_name, status, task_id, owner, body)`.
    pub fn add_notification(
        &self,
        task_name: &str,
        status: &str,
        task_id: Option<&str>,
        owner: Option<&str>,
        body: Option<&str>,
    ) {
        let body_val = body.map(|b| {
            // (body[:500] + "…") if body and len(body) > 500 else body — char-based.
            let chars: Vec<char> = b.chars().collect();
            if chars.len() > 500 {
                let truncated: String = chars[..500].iter().collect();
                format!("{truncated}…")
            } else {
                b.to_string()
            }
        });
        let note = json!({
            "task_name": task_name,
            "status": status,
            "task_id": task_id,
            "owner": owner,
            "body": body_val,
            // datetime.utcnow().isoformat() + "Z" — `.isoformat()` uses a `T`
            // separator and includes microseconds only when non-zero.
            "timestamp": format!("{}Z", pydatetime::to_isoformat(&pydatetime::utcnow_naive_iso())),
        });
        let mut notes = self.pending_notifications.lock().unwrap();
        notes.push(note);
        // Cap at 50.
        if notes.len() > 50 {
            let start = notes.len() - 50;
            *notes = notes.split_off(start);
        }
    }

    /// `pop_notifications(owner=None)`.
    pub fn pop_notifications(&self, owner: Option<&str>) -> Vec<Value> {
        let mut notes = self.pending_notifications.lock().unwrap();
        match owner {
            None => {
                let all = notes.clone();
                notes.clear();
                all
            }
            Some(o) => {
                let mut keep: Vec<Value> = Vec::new();
                let mut take: Vec<Value> = Vec::new();
                for n in notes.iter() {
                    if n.get("owner").and_then(|v| v.as_str()) == Some(o) {
                        take.push(n.clone());
                    } else {
                        keep.push(n.clone());
                    }
                }
                *notes = keep;
                take
            }
        }
    }

    /// `start(self)` — startup cleanup + launch the loop + the note-pings loop.
    pub async fn start(self: &Arc<Self>) {
        // Mark leftover "running"/"queued" task_runs as "aborted".
        if let Ok(db) = session_local() {
            let res = (|| -> rusqlite::Result<usize> {
                let now = pydatetime::utcnow_naive_iso();
                let n = db.execute(
                    "UPDATE task_runs SET status='aborted', \
                     error='Server restarted while task was ' || COALESCE(status,'running'), \
                     finished_at=?1 \
                     WHERE status IN ('running','queued')",
                    rusqlite::params![now],
                )?;
                Ok(n)
            })();
            match res {
                Ok(n) if n > 0 => {
                    logger::info(&format!("Cleared {n} stale task_runs from previous run"))
                }
                Ok(_) => {}
                Err(e) => {
                    logger::warning(&format!("Could not clear stale task_runs on startup: {e}"))
                }
            }
        }

        // Default-assistant dedupe sweep.
        self.dedupe_default_assistants();

        self.running.store(true, Ordering::SeqCst);
        let loop_self = Arc::clone(self);
        tokio::spawn(async move { loop_self.run_loop().await });
        let pings_self = Arc::clone(self);
        tokio::spawn(async move { pings_self.note_pings_loop().await });
        logger::info(&format!(
            "Task scheduler started (concurrency cap: {})",
            self.concurrency_cap
        ));

        // Cluster audit (log-only).
        self.cluster_audit();
    }

    fn dedupe_default_assistants(&self) {
        let db = match session_local() {
            Ok(c) => c,
            Err(e) => {
                logger::warning(&format!(
                    "Could not dedupe default-assistant rows on startup: {e}"
                ));
                return;
            }
        };
        let res = (|| -> rusqlite::Result<()> {
            // groups: owners with >1 default-assistant rows.
            let mut stmt = db.prepare(
                "SELECT owner, COUNT(id) FROM crew_members \
                 WHERE is_default_assistant = 1 GROUP BY owner HAVING COUNT(id) > 1",
            )?;
            let owners: Vec<Option<String>> = stmt
                .query_map([], |r| r.get::<_, Option<String>>(0))?
                .filter_map(|x| x.ok())
                .collect();
            drop(stmt);
            for owner in owners {
                let mut s2 = db.prepare(
                    "SELECT id FROM crew_members \
                     WHERE is_default_assistant = 1 AND owner IS ?1 \
                     ORDER BY created_at ASC",
                )?;
                let ids: Vec<String> = s2
                    .query_map([&owner], |r| r.get::<_, String>(0))?
                    .filter_map(|x| x.ok())
                    .collect();
                drop(s2);
                if ids.len() <= 1 {
                    continue;
                }
                let keep = ids[0].clone();
                let losers = &ids[1..];
                let mut n_tasks = 0usize;
                for lid in losers {
                    n_tasks += db.execute(
                        "DELETE FROM scheduled_tasks WHERE crew_member_id = ?1",
                        [lid],
                    )?;
                    db.execute("DELETE FROM crew_members WHERE id = ?1", [lid])?;
                }
                logger::warning(&format!(
                    "Default-assistant dedupe: owner={owner:?} kept {keep}, dropped {} crew + {} orphan tasks",
                    losers.len(),
                    n_tasks
                ));
            }
            Ok(())
        })();
        if let Err(e) = res {
            logger::warning(&format!(
                "Could not dedupe default-assistant rows on startup: {e}"
            ));
        }
    }

    fn cluster_audit(&self) {
        let db = match session_local() {
            Ok(c) => c,
            Err(e) => {
                logger::debug(&format!("Cluster audit skipped: {e}"));
                return;
            }
        };
        let res = (|| -> rusqlite::Result<()> {
            let mut stmt = db.prepare(
                "SELECT name, id, next_run FROM scheduled_tasks \
                 WHERE status='active' AND trigger_type='schedule' AND next_run IS NOT NULL",
            )?;
            let rows: Vec<(Option<String>, String, Option<String>)> = stmt
                .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?
                .filter_map(|x| x.ok())
                .collect();
            drop(stmt);
            let mut buckets: IndexMap<String, Vec<String>> = IndexMap::new();
            for (name, id, next_run) in rows {
                let nr = match parse_dt(next_run.as_deref()) {
                    Some(dt) => dt,
                    None => continue,
                };
                let key = nr.format("%H:%M").to_string();
                buckets
                    .entry(key)
                    .or_default()
                    .push(name.unwrap_or(id));
            }
            let mut clusters: Vec<(String, usize)> = buckets
                .into_iter()
                .filter(|(_, v)| v.len() > 1)
                .map(|(k, v)| (k, v.len()))
                .collect();
            if !clusters.is_empty() {
                clusters.sort_by(|a, b| a.0.cmp(&b.0));
                let summary = clusters
                    .iter()
                    .map(|(k, n)| format!("{k} ({n})"))
                    .collect::<Vec<_>>()
                    .join(", ");
                logger::info(&format!(
                    "Task scheduling clusters (>1 task/minute): {summary}"
                ));
            }
            Ok(())
        })();
        if let Err(e) = res {
            logger::debug(&format!("Cluster audit skipped: {e}"));
        }
    }

    /// `stop(self)`.
    pub async fn stop(&self) {
        self.running.store(false, Ordering::SeqCst);
        logger::info("Task scheduler stopped");
    }

    /// `_note_pings_loop(self)` — ticks every 60s, pure infra (no LLM).
    async fn note_pings_loop(self: Arc<Self>) {
        tokio::time::sleep(std::time::Duration::from_secs(30)).await;
        while self.running.load(Ordering::SeqCst) {
            let owners = self.known_task_owners();
            let visit: Vec<String> = if owners.is_empty() {
                vec![String::new()]
            } else {
                owners
            };
            for ow in visit {
                // except TaskNoop: pass; except Exception: log. A real error in
                // this port surfaces as Ok((msg, false)) only inside the action;
                // action_ping_notes returns Err(Outcome::Noop) for "nothing due".
                match crate::src::builtin_actions::action_ping_notes(&ow, &json!({})).await {
                    Ok(_) | Err(Outcome::Noop(_)) => {}
                    Err(other) => logger::warning(&format!(
                        "ping_notes background scanner errored for owner={ow:?}: {other}"
                    )),
                }
            }
            tokio::time::sleep(std::time::Duration::from_secs(60)).await;
        }
    }

    /// `_known_task_owners(self)` — distinct non-empty owners across scheduled
    /// tasks + notes with a due date.
    fn known_task_owners(&self) -> Vec<String> {
        let db = match session_local() {
            Ok(c) => c,
            Err(_) => return vec![],
        };
        let res = (|| -> rusqlite::Result<Vec<String>> {
            let mut owners: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();
            let mut s1 = db.prepare("SELECT DISTINCT owner FROM scheduled_tasks")?;
            for r in s1.query_map([], |r| r.get::<_, Option<String>>(0))? {
                if let Ok(Some(o)) = r {
                    if !o.is_empty() {
                        owners.insert(o);
                    }
                }
            }
            drop(s1);
            let mut s2 = db.prepare(
                "SELECT DISTINCT owner FROM notes \
                 WHERE due_date IS NOT NULL AND due_date != '' AND archived = 0",
            )?;
            for r in s2.query_map([], |r| r.get::<_, Option<String>>(0))? {
                if let Ok(Some(o)) = r {
                    if !o.is_empty() {
                        owners.insert(o);
                    }
                }
            }
            drop(s2);
            Ok(owners.into_iter().collect())
        })();
        res.unwrap_or_default()
    }

    /// `_loop(self)`.
    async fn run_loop(self: Arc<Self>) {
        tokio::time::sleep(std::time::Duration::from_secs(10)).await;
        while self.running.load(Ordering::SeqCst) {
            if let Err(e) = self.check_due_tasks().await {
                logger::warning(&format!("Error in task scheduler loop: {e}"));
            }
            // Sleep until the next scheduled run, capped at 60s.
            let mut sleep_for = 60.0_f64;
            if let Ok(db) = session_local() {
                let next: Option<String> = db
                    .query_row(
                        "SELECT next_run FROM scheduled_tasks \
                         WHERE status='active' AND next_run IS NOT NULL \
                         ORDER BY next_run ASC LIMIT 1",
                        [],
                        |r| r.get::<_, Option<String>>(0),
                    )
                    .optional()
                    .ok()
                    .flatten()
                    .flatten();
                if let Some(nr) = parse_dt(next.as_deref()) {
                    let delta = (nr - utcnow()).num_milliseconds() as f64 / 1000.0;
                    sleep_for = 1.0_f64.max(60.0_f64.min(delta));
                }
            }
            tokio::time::sleep(std::time::Duration::from_secs_f64(sleep_for)).await;
        }
    }

    /// `_check_due_tasks(self)`.
    async fn check_due_tasks(self: &Arc<Self>) -> PyResult<()> {
        // Read the due task IDs into an owned Vec (sync block; conn dropped) so
        // the rusqlite `Connection` is not held across the `executing.lock().await`.
        // Python snapshots `_executing` under the lock then filters; the SQL
        // already excludes the snapshot in the source — here we re-check membership
        // under the async lock (the `if task.id in self._executing: continue` guard).
        let due: Vec<String> = {
            let db = session_local().map_err(|e| PyError::other(e.to_string()))?;
            let now = pydatetime::utcnow_naive_iso();
            let mut stmt = db
                .prepare("SELECT id FROM scheduled_tasks WHERE status='active' AND next_run <= ?1")
                .map_err(|e| PyError::other(e.to_string()))?;
            let ids: Vec<String> = stmt
                .query_map([&now], |r| r.get::<_, String>(0))
                .map_err(|e| PyError::other(e.to_string()))?
                .filter_map(|x| x.ok())
                .collect();
            ids
        };
        let to_dispatch: Vec<String> = {
            let mut executing = self.executing.lock().await;
            let mut out = Vec::new();
            for id in due {
                if executing.contains(&id) {
                    continue;
                }
                executing.insert(id.clone());
                out.push(id);
            }
            out
        };
        for task_id in to_dispatch {
            let me = Arc::clone(self);
            // The spawned task takes its own clone; the original `task_id` is the
            // key for the abort-handle registration below.
            let task_id_spawn = task_id.clone();
            let handle = tokio::spawn(async move {
                me.execute_task(&task_id_spawn, false, true).await;
            });
            self.task_handles
                .lock()
                .await
                .insert(task_id, handle.abort_handle());
        }
        Ok(())
    }

    /// `_task_needs_model_slot(self, task_id)`.
    fn task_needs_model_slot(&self, task_id: &str) -> bool {
        let db = match session_local() {
            Ok(c) => c,
            Err(_) => return true,
        };
        let task = match load_task(&db, task_id) {
            Some(t) => t,
            None => return true,
        };
        let task_type = task.task_type.as_deref().unwrap_or("llm");
        if task_type != "action" {
            return true;
        }
        MODEL_BACKED_ACTIONS.contains(task.action.as_deref().unwrap_or(""))
    }

    /// `_execute_task(self, task_id, *, bypass_model_slot, release_executing)`.
    ///
    /// Returns a BOXED future (not `async fn`). Task chaining makes the call
    /// graph recursive (`execute_task` → … → `finish_run` → spawn → `run_chained`
    /// → `execute_task`); a plain `async fn` produces an opaque self-referential
    /// future the `Send` checker can't resolve at the `tokio::spawn` site. Boxing
    /// gives the recursion a concrete `Pin<Box<dyn Future + Send>>` type, which is
    /// the idiomatic Rust fix for recursive async.
    pub fn execute_task(
        self: &Arc<Self>,
        task_id: &str,
        bypass_model_slot: bool,
        release_executing: bool,
    ) -> std::pin::Pin<Box<dyn std::future::Future<Output = ()> + Send>> {
        let me = Arc::clone(self);
        let task_id = task_id.to_string();
        Box::pin(async move {
            // Create the queued run row.
            let run_id = uuid::Uuid::new_v4().to_string();
            if let Ok(db) = session_local() {
                let now = pydatetime::utcnow_naive_iso();
                if let Err(e) = db.execute(
                    "INSERT INTO task_runs (id, task_id, started_at, status) VALUES (?1, ?2, ?3, 'queued')",
                    rusqlite::params![run_id, task_id, now],
                ) {
                    logger::warning(&format!(
                        "Failed to create queued run row for task {task_id}: {e}"
                    ));
                }
            }

            if bypass_model_slot || !me.task_needs_model_slot(&task_id) {
                me.execute_task_locked(&task_id, &run_id, release_executing)
                    .await;
                return;
            }

            // Acquire the model slot (Semaphore(1)).
            let _permit = me.run_semaphore.acquire().await;
            me.execute_task_locked(&task_id, &run_id, release_executing)
                .await;
        })
    }

    /// `_execute_task_locked(self, task_id, run_id, *, release_executing)`.
    async fn execute_task_locked(
        self: &Arc<Self>,
        task_id: &str,
        run_id: &str,
        release_executing: bool,
    ) {
        // Inner body that returns so the `finally` (release_executing) always runs.
        self.execute_task_inner(task_id, run_id).await;
        // `finally: self._task_handles.pop(task_id, None)` — clean up the
        // abort handle so stop_task does not try to abort an already-finished task.
        self.task_handles.lock().await.remove(task_id);
        if release_executing {
            let mut executing = self.executing.lock().await;
            executing.remove(task_id);
        }
    }

    async fn execute_task_inner(self: &Arc<Self>, task_id: &str, run_id: &str) {
        // PORT NOTE: rusqlite `Connection` is NOT `Send`, so we MUST NOT hold a
        // connection across an `.await` (this future is `tokio::spawn`-ed and
        // therefore must be `Send`). Python keeps one `db` session open for the
        // whole `_execute_task_locked` body; the Rust port instead opens a
        // short-lived connection in each synchronous block and drops it before
        // every await. Observable behavior is identical (the per-call connection
        // is the `SessionLocal()`-per-call analogue documented in database.rs).

        // ── Load the task + flip the run queued -> running (sync block) ────────
        let task: ScheduledTaskRow = {
            let db = match session_local() {
                Ok(c) => c,
                Err(e) => {
                    logger::warning(&format!("Task {task_id} execution error: {e}"));
                    return;
                }
            };
            match load_task(&db, task_id) {
                Some(t) if t.status.as_deref() == Some("active") => {
                    // Flip the run from queued -> running.
                    let now = pydatetime::utcnow_naive_iso();
                    let updated = db
                        .execute(
                            "UPDATE task_runs SET status='running', started_at=?1 WHERE id = ?2",
                            rusqlite::params![now, run_id],
                        )
                        .unwrap_or(0);
                    if updated == 0 {
                        let _ = db.execute(
                            "INSERT INTO task_runs (id, task_id, started_at, status) \
                             VALUES (?1, ?2, ?3, 'running')",
                            rusqlite::params![run_id, t.id, now],
                        );
                    }
                    t
                }
                other => {
                    // Paused/deleted while queued — record `skipped` if still queued.
                    let status = other.as_ref().and_then(|t| t.status.clone());
                    let exists = other.is_some();
                    let cur: Option<String> = db
                        .query_row(
                            "SELECT status FROM task_runs WHERE id = ?1",
                            [run_id],
                            |r| r.get(0),
                        )
                        .optional()
                        .ok()
                        .flatten();
                    if cur.as_deref() == Some("queued") {
                        let now = pydatetime::utcnow_naive_iso();
                        let task_status = if exists {
                            status.unwrap_or_else(|| "deleted".to_string())
                        } else {
                            "deleted".to_string()
                        };
                        let _ = db.execute(
                            "UPDATE task_runs SET status='skipped', finished_at=?1, \
                             error=?2 WHERE id = ?3",
                            rusqlite::params![
                                now,
                                format!("Task no longer active (status={task_status})"),
                                run_id
                            ],
                        );
                    }
                    return;
                }
            }
        };

        let task_type = task.task_type.clone().unwrap_or_else(|| "llm".to_string());
        *self._last_run_model.lock().unwrap() = None;

        // ── Run the executor (no DB connection held across the awaits) ─────────
        let mut last_run_model: Option<String> = None;
        let exec_result: ExecResult = match task_type.as_str() {
            "action" => match self.execute_action(&task).await {
                Ok((result, success)) => ExecResult::Done(result, success),
                Err(Outcome::Noop(reason)) => ExecResult::Noop(reason),
                Err(Outcome::Deferred {
                    reason,
                    delay_seconds,
                }) => ExecResult::Deferred {
                    reason,
                    delay_seconds,
                },
            },
            "research" => match self
                .execute_research_task(&task, &mut last_run_model)
                .await
            {
                // run.status = "success"; run.result = result (research never
                // returns a failure tuple — a raised error takes the OUTER
                // `except Exception` finalization path, not the normal one).
                Ok(r) => ExecResult::Done(r, true),
                Err(e) => ExecResult::Raised(e),
            },
            _ => match self.execute_llm_task(&task, &mut last_run_model).await {
                // `_execute_llm_task` RAISES on failure (it never returns a
                // (msg, False) tuple), so a raised error takes the OUTER
                // `except Exception` finalization path.
                Ok(r) => ExecResult::Done(r, true),
                Err(e) => ExecResult::Raised(e),
            },
        };

        match exec_result {
            ExecResult::Deferred {
                reason,
                delay_seconds,
            } => {
                self.handle_defer(&task, run_id, &reason, delay_seconds);
            }
            ExecResult::Noop(reason) => {
                self.handle_noop(&task, run_id, &reason);
            }
            ExecResult::Done(result, success) => {
                self.finish_run(&task, run_id, &result, success, last_run_model)
                    .await;
            }
            ExecResult::Raised(exec_exc) => {
                self.handle_exec_error(task_id, run_id, &exec_exc);
            }
        }
    }

    /// Python OUTER `except Exception as exec_exc` block of
    /// `_execute_task_locked` (task_scheduler.py:706-789) — the finalization
    /// path taken when an LLM/research executor *raises* (as opposed to the
    /// normal success/error path in `finish_run`).
    ///
    /// Materially different from the normal path: it does NOT increment
    /// `run_count`, titles the error notification `Task {task_id}` (not the task
    /// name), persists `error = f"{type(exec_exc).__name__}: {exec_exc}"[:2000]`,
    /// advances `next_run` for schedule-triggered tasks, and emits no assistant
    /// log / chaining.
    ///
    /// All DB work is synchronous (the rusqlite `Connection` never crosses an
    /// await — the Python keeps one session; the port uses short-lived ones).
    fn handle_exec_error(&self, task_id: &str, run_id: &str, exec_exc: &PyError) {
        // Python `logger.exception(...)`; the `pylog` shim has no `exception`,
        // so this file ports those to `warning` (see queued-run-row site).
        logger::warning(&format!("Task {task_id} execution error: {exec_exc}"));

        let db = match session_local() {
            Ok(c) => c,
            Err(_) => return,
        };

        // Re-fetch the task (Python re-queries owner + notify-eligibility here,
        // defensive against the task being deleted mid-run).
        let task_obj = load_task(&db, task_id);
        let owner = task_obj.as_ref().and_then(|t| t.owner.clone());
        let should_notify_error = task_obj
            .as_ref()
            .map(|t| {
                matches!(t.task_type.as_deref().unwrap_or("llm"), "llm" | "research")
                    && t.notifications_enabled.unwrap_or(true)
            })
            .unwrap_or(false);
        if should_notify_error {
            self.add_notification(
                &format!("Task {task_id}"),
                "error",
                Some(task_id),
                owner.as_deref(),
                None,
            );
        }

        // err_text = f"{type(exec_exc).__name__}: {exec_exc}" — char-capped 2000.
        let err_text_full = format!("{}: {exec_exc}", exec_exc.py_type_name());
        let err_chars: Vec<char> = err_text_full.chars().collect();
        let err_text: String = err_chars.iter().take(2000).collect();

        let now = utcnow();
        // Only flip the run if it's still running/success (Python guard).
        let cur_status: Option<String> = db
            .query_row(
                "SELECT status FROM task_runs WHERE id = ?1",
                [run_id],
                |r| r.get(0),
            )
            .optional()
            .ok()
            .flatten();
        if matches!(cur_status.as_deref(), Some("running") | Some("success")) {
            let _ = db.execute(
                "UPDATE task_runs SET status='error', error=?1, finished_at=?2 WHERE id=?3",
                rusqlite::params![err_text, fmt_dt(now), run_id],
            );
        }

        // Advance next_run on failure for schedule-triggered tasks so a broken
        // task doesn't busy-loop the scheduler with a stale past date. NOTE: no
        // run_count increment here (unlike the normal path).
        if let Some(t) = task_obj.as_ref() {
            if t.trigger_type.as_deref().unwrap_or("schedule") == "schedule" {
                let next_run = compute_next_run(
                    t.schedule.as_deref(),
                    t.scheduled_time.as_deref(),
                    t.scheduled_day,
                    t.scheduled_date,
                    Some(now),
                    t.cron_expression.as_deref(),
                    resolve_task_timezone(&db, t).as_deref(),
                );
                let _ = db.execute(
                    "UPDATE scheduled_tasks SET last_run=?1, next_run=?2 WHERE id=?3",
                    rusqlite::params![fmt_dt(now), next_run.map(fmt_dt), task_id],
                );
            }
        }
    }

    /// `except TaskDeferred` path. (Own short-lived connection — no `db` held
    /// across the caller's awaits.)
    fn handle_defer(&self, task: &ScheduledTaskRow, run_id: &str, reason: &str, delay_seconds: i64) {
        let count = {
            let mut counts = self.task_defer_counts.lock().unwrap();
            let c = counts.entry(task.id.clone()).or_insert(0);
            *c += 1;
            *c
        };
        let mut delay = if delay_seconds != 0 {
            delay_seconds
        } else {
            20 * 60
        };
        if count > 2 {
            delay = delay.max(40 * 60);
        }
        let when = utcnow() + Duration::seconds(delay);
        logger::info(&format!(
            "Task '{}' deferred for {delay}s after {count} quiet-window hit(s): {reason}",
            task.name
        ));
        if let Ok(db) = session_local() {
            let _ = db.execute("DELETE FROM task_runs WHERE id = ?1", [run_id]);
            let _ = db.execute(
                "UPDATE scheduled_tasks SET next_run = ?1 WHERE id = ?2",
                rusqlite::params![fmt_dt(when), task.id],
            );
        }
    }

    /// `except TaskNoop` path.
    fn handle_noop(&self, task: &ScheduledTaskRow, run_id: &str, reason: &str) {
        logger::info(&format!("Task '{}' no-op: {reason}", task.name));
        let db = match session_local() {
            Ok(c) => c,
            Err(_) => return,
        };
        let now = utcnow();
        let next_run = if task.trigger_type.as_deref().unwrap_or("schedule") == "schedule" {
            compute_next_run(
                task.schedule.as_deref(),
                task.scheduled_time.as_deref(),
                task.scheduled_day,
                task.scheduled_date,
                Some(now),
                task.cron_expression.as_deref(),
                resolve_task_timezone(&db, task).as_deref(),
            )
        } else {
            None
        };
        let _ = db.execute(
            "UPDATE task_runs SET status='skipped', result=?1, finished_at=?2 WHERE id = ?3",
            rusqlite::params![reason, fmt_dt(now), run_id],
        );
        let _ = db.execute(
            "UPDATE scheduled_tasks SET last_run=?1, next_run=?2 WHERE id = ?3",
            rusqlite::params![fmt_dt(now), next_run.map(fmt_dt), task.id],
        );
    }

    /// Success / failure run finalization + delivery + chaining.
    ///
    /// The DB work is in non-async helpers so the rusqlite `Connection` never
    /// enters this `Send` future (this future is `tokio::spawn`-ed via chaining /
    /// `run_chained`).
    async fn finish_run(
        self: &Arc<Self>,
        task: &ScheduledTaskRow,
        run_id: &str,
        result: &str,
        success: bool,
        last_run_model: Option<String>,
    ) {
        let status = if success { "success" } else { "error" };
        // Record the run row + model (sync helper; conn dropped before the await).
        self.record_run_row(run_id, result, success, last_run_model.as_deref());

        // Delivery (only on success). No DB held across this await.
        if success {
            self.deliver_task_result(task, result, last_run_model.as_deref())
                .await;
        }

        // Finalize the task + decide chaining (sync helper).
        let chain_id = self.finalize_task_row(task, run_id, result, status, success);

        // Task chaining — spawn after the connection is dropped.
        if let Some(cid) = chain_id {
            logger::info(&format!("Chaining: '{}' → task {cid}", task.name));
            let me = Arc::clone(self);
            let chain_cid = cid.clone();
            let handle = tokio::spawn(async move {
                me.run_chained(&cid).await;
            });
            // Best-effort: register the abort handle so stop_task can cancel a
            // chained run before it starts. Lock failure is non-fatal.
            if let Ok(mut handles) = self.task_handles.try_lock() {
                handles.insert(chain_cid, handle.abort_handle());
            }
        }
    }

    /// Write the run row's status/result/error/model (sync; conn dropped).
    fn record_run_row(
        &self,
        run_id: &str,
        result: &str,
        success: bool,
        last_run_model: Option<&str>,
    ) {
        let now = utcnow();
        let status = if success { "success" } else { "error" };
        if let Ok(db) = session_local() {
            let _ = db.execute(
                "UPDATE task_runs SET status=?1, result=?2, error=?3, finished_at=?4 WHERE id=?5",
                rusqlite::params![
                    status,
                    result,
                    if success { None } else { Some(result) },
                    fmt_dt(now),
                    run_id
                ],
            );
            if let Some(m) = last_run_model {
                let _ = db.execute(
                    "UPDATE task_runs SET model=?1 WHERE id=?2",
                    rusqlite::params![m, run_id],
                );
            }
        }
    }

    /// Update the task row (last_run/run_count/next_run/completed), emit the
    /// notification + assistant log, and return the chain target id (cycle-checked)
    /// — all synchronous DB work. Returns `Some(then_task_id)` if a chain should
    /// fire.
    fn finalize_task_row(
        &self,
        task: &ScheduledTaskRow,
        run_id: &str,
        result: &str,
        status: &str,
        success: bool,
    ) -> Option<String> {
        let now = utcnow();
        self.task_defer_counts.lock().unwrap().remove(&task.id);
        let mut chain_id: Option<String> = None;
        if let Ok(db) = session_local() {
            let next_run = if task.trigger_type.as_deref().unwrap_or("schedule") == "schedule" {
                compute_next_run(
                    task.schedule.as_deref(),
                    task.scheduled_time.as_deref(),
                    task.scheduled_day,
                    task.scheduled_date,
                    Some(now),
                    task.cron_expression.as_deref(),
                    resolve_task_timezone(&db, task).as_deref(),
                )
            } else {
                None
            };
            let new_status = if next_run.is_none() && task.schedule.as_deref() == Some("once") {
                Some("completed".to_string())
            } else {
                None
            };
            let rc = task.run_count.unwrap_or(0) + 1;
            if let Some(st) = &new_status {
                let _ = db.execute(
                    "UPDATE scheduled_tasks SET last_run=?1, run_count=?2, next_run=?3, status=?4 WHERE id=?5",
                    rusqlite::params![fmt_dt(now), rc, next_run.map(fmt_dt), st, task.id],
                );
            } else {
                let _ = db.execute(
                    "UPDATE scheduled_tasks SET last_run=?1, run_count=?2, next_run=?3 WHERE id=?4",
                    rusqlite::params![fmt_dt(now), rc, next_run.map(fmt_dt), task.id],
                );
            }
            logger::info(&format!("Task '{}' completed (run {run_id})", task.name));

            if success {
                if let Some(cid) = task.then_task_id.clone() {
                    if !self.has_chain_cycle(&db, &cid, 10) {
                        chain_id = Some(cid);
                    } else {
                        logger::warning(&format!(
                            "Skipping chain from '{}': cycle detected",
                            task.name
                        ));
                    }
                }
            }
        }

        let output = task.output_target.clone().unwrap_or_else(|| "session".to_string());
        let should_notify = matches!(task.task_type.as_deref().unwrap_or("llm"), "llm" | "research")
            && task.notifications_enabled.unwrap_or(true);
        if should_notify {
            self.add_notification(
                &task.name,
                status,
                Some(&task.id),
                task.owner.as_deref(),
                if output == "notification" { Some(result) } else { None },
            );
        }

        // Log to the assistant chat (success only).
        if success {
            self.log_to_assistant(task, result);
        }

        chain_id
    }

    /// `_log_to_assistant(self, db, task, result_text)`.
    fn log_to_assistant(&self, task: &ScheduledTaskRow, result_text: &str) {
        if task.name.to_lowercase().contains("check-in") {
            return;
        }
        if SILENT_ACTIONS.contains(task.action.as_deref().unwrap_or("")) {
            return;
        }
        // result_text[:1000] char-based.
        let chars: Vec<char> = result_text.chars().collect();
        let truncated: String = chars.iter().take(1000).collect();
        crate::src::assistant_log::log_to_assistant(
            task.owner.as_deref().unwrap_or(""),
            &truncated,
            "assistant",
            Some(&task.name),
        );
    }

    /// `_execute_action(self, task)`.
    ///
    /// Dispatches by name into `builtin_actions::BUILTIN_ACTIONS`. Returns the
    /// action `ActionResult` directly: `Err(Outcome::Noop/Deferred)` bubbles up
    /// so the caller drops/defers the run; a real error is mapped to
    /// `Ok((str(e), false))` (the Python `except Exception` arm of `_execute_action`).
    async fn execute_action(
        &self,
        task: &ScheduledTaskRow,
    ) -> crate::src::builtin_actions::ActionResult {
        let action = task.action.as_deref().unwrap_or("");
        let action_fn = match crate::src::builtin_actions::BUILTIN_ACTIONS.get(action) {
            Some(f) => f,
            None => return Ok((format!("Unknown action: {action}"), false)),
        };
        // kwargs = {"owner", "task_name", + "script"/"command" for the script
        // actions}. Python passes `owner` separately + the rest as **kwargs; the
        // Rust action signature is `(owner: &str, kwargs: &Value)`.
        let mut kwargs = serde_json::Map::new();
        kwargs.insert("task_name".to_string(), json!(task.name));
        if let Some(prompt) = task.prompt.as_deref().filter(|p| !p.is_empty()) {
            match action {
                "run_script" | "run_local" => {
                    kwargs.insert("script".to_string(), json!(prompt));
                }
                "ssh_command" => {
                    kwargs.insert("command".to_string(), json!(prompt));
                }
                _ => {}
            }
        }
        action_fn(task.owner.as_deref().unwrap_or(""), &Value::Object(kwargs)).await
    }

    /// `_execute_llm_task(self, task, db)`.
    async fn execute_llm_task(
        self: &Arc<Self>,
        task: &ScheduledTaskRow,
        last_run_model: &mut Option<String>,
    ) -> PyResult<String> {
        // ── Resolution + session-ensure + crew + tz (sync block; no conn held
        //    across an await) ───────────────────────────────────────────────
        struct LlmPrep {
            crew: Option<CrewRow>,
            endpoint_url: String,
            model: String,
            session_id: String,
            tz_name: Option<String>,
        }
        let prep: PyResult<LlmPrep> = (|| {
            let db = session_local().map_err(|e| PyError::other(e.to_string()))?;
            let crew = task.crew_member_id.as_deref().and_then(|id| load_crew(&db, id));
            let mut endpoint_url = task.endpoint_url.clone();
            let mut model = task.model.clone();
            if endpoint_url.is_none() || model.is_none() {
                if let Some(c) = crew.as_ref() {
                    endpoint_url = endpoint_url.or_else(|| c.endpoint_url.clone());
                    model = model.or_else(|| c.model.clone());
                }
            }
            if endpoint_url.is_none() || model.is_none() {
                let (u, m) = self.resolve_defaults(&db, task.owner.as_deref());
                endpoint_url = endpoint_url.or(u);
                model = model.or(m);
            }
            let endpoint_url = endpoint_url.filter(|s| !s.is_empty());
            let model = model.filter(|s| !s.is_empty());
            let (endpoint_url, model) = match (endpoint_url, model) {
                (Some(u), Some(m)) => (u, m),
                _ => return Err(PyError::other("No model/endpoint configured")),
            };
            let session_id = self.ensure_task_session(&db, task, &endpoint_url, &model, "[Task] ");
            let tz_name = resolve_task_timezone(&db, task);
            Ok(LlmPrep {
                crew,
                endpoint_url,
                model,
                session_id,
                tz_name,
            })
        })();
        let prep = prep?;
        let crew = prep.crew;
        let endpoint_url = prep.endpoint_url;
        let model = prep.model;
        let session_id = prep.session_id;
        *last_run_model = Some(model.clone());

        // Check-in branch.
        let is_checkin = crew
            .as_ref()
            .map(|c| c.is_default_assistant)
            .unwrap_or(false)
            && task.name.to_lowercase().contains("check-in");
        if is_checkin {
            return self
                .execute_checkin(task, crew.as_ref(), &session_id, &endpoint_url, &model)
                .await;
        }

        // System prompt.
        let base_prompt = match crew.as_ref().and_then(|c| c.personality.as_deref()) {
            Some(p) if !p.trim().is_empty() => p.trim().to_string(),
            _ => "You are a helpful assistant executing a scheduled task. Use available tools to complete the task thoroughly.".to_string(),
        };
        let time_str = self.now_time_str(prep.tz_name.as_deref());
        let system_prompt = format!("Current time: {time_str}\n\n{base_prompt}");

        // `disabled_tools = BUILTIN_TOOL_DESCRIPTIONS.keys() - set(crew.enabled_tools)`
        // when the crew restricts its tools (task_scheduler.py:1202-1213). enabled_tools
        // is a JSON array string; a parse failure / empty list leaves it None.
        let disabled_tools: Option<HashSet<String>> = crew
            .as_ref()
            .and_then(|c| c.enabled_tools.as_deref())
            .filter(|s| !s.trim().is_empty())
            .and_then(|s| serde_json::from_str::<Vec<String>>(s).ok())
            .filter(|enabled| !enabled.is_empty())
            .map(|enabled| {
                let enabled: HashSet<String> = enabled.into_iter().collect();
                crate::src::tool_index::BUILTIN_TOOL_DESCRIPTIONS
                    .keys()
                    .map(|k| k.to_string())
                    .filter(|k| !enabled.contains(k))
                    .collect()
            });

        // `relevant_tools = get_tool_index().get_tools_for_query(prompt, k=8) |
        // ASSISTANT_ALWAYS_AVAILABLE; if disabled_tools: relevant_tools -= disabled_tools`
        // (task_scheduler.py:1216-1228) — RAG-narrow the 40+ tools so the model does not
        // hit its tool cap. `None` (index unavailable) = use all (the Python `except`).
        let prompt_for_rag = task.prompt.clone().unwrap_or_default();
        let relevant_tools: Option<HashSet<String>> =
            match crate::src::tool_index::tools_for_query(&prompt_for_rag, 8).await {
                Some(rag) => {
                    let mut rel: HashSet<String> = rag;
                    for t in crate::src::tool_index::ASSISTANT_ALWAYS_AVAILABLE.iter() {
                        rel.insert(t.to_string());
                    }
                    if let Some(dis) = &disabled_tools {
                        rel.retain(|t| !dis.contains(t));
                    }
                    Some(rel)
                }
                None => None,
            };

        // Run the agent loop.
        let result = match self
            .run_agent_loop(
                &endpoint_url,
                &model,
                task,
                &session_id,
                Some(&system_prompt),
                disabled_tools,
                relevant_tools,
                None,
            )
            .await
        {
            Ok(r) => r,
            Err(e) => {
                logger::warning(&format!(
                    "Agent loop failed for task '{}', falling back to simple call: {e}",
                    task.name
                ));
                let messages = vec![
                    json!({"role": "system", "content": system_prompt}),
                    json!({"role": "user", "content": task.prompt.clone().unwrap_or_default()}),
                ];
                // Python passes neither temperature nor max_tokens here
                // (task_scheduler.py:1243), so both inherit the LLMConfig
                // defaults DEFAULT_TEMPERATURE=1.0 / DEFAULT_MAX_TOKENS=0
                // (llm_core.py:16-17). max_tokens=0 omits the token cap.
                crate::src::llm_core::llm_call_async(
                    &endpoint_url,
                    &model,
                    messages,
                    1.0,
                    0,
                    IndexMap::new(),
                    120,
                )
                .await
                .unwrap_or_default()
            }
        };

        // strip_think(result, prose=True, prompt_echo=True).strip() or result
        let stripped = crate::src::text_helpers::strip_think(&result, true, true);
        let stripped = stripped.trim();
        Ok(if stripped.is_empty() {
            result
        } else {
            stripped.to_string()
        })
    }

    /// `_execute_checkin(...)` — gather raw data, hand to the agent loop.
    ///
    /// PORT_PARTIAL: the calendar + notes DB pulls and the MCP auto-discovery
    /// loop are ported; the integrations (Miniflux HTTP) discovery still DEFERs
    /// honestly. The MCP snapshot loop walks the live manager's connected
    /// non-builtin servers (`connected_server_snapshots`) and probes each
    /// `CHECKIN_MCP_PATTERNS` entry whose `detect` tool is present, inserting the
    /// (truncated, optionally formatted) output into `raw`. Nothing is
    /// fabricated; an empty/failed section is simply omitted (the Python
    /// `if content` / `exit_code != 0` guards).
    ///
    /// Deviation: the Python wraps each MCP call in a 3-minute `_cached` snapshot
    /// so co-firing tasks share one probe. The Rust `cached` helper is not wired
    /// for MCP keys here, so the call is made directly; the observable result
    /// (the sections added to the data dump) is identical.
    #[allow(clippy::too_many_arguments)]
    async fn execute_checkin(
        self: &Arc<Self>,
        task: &ScheduledTaskRow,
        crew: Option<&CrewRow>,
        session_id: &str,
        endpoint_url: &str,
        model: &str,
    ) -> PyResult<String> {
        // tz + calendar pull (sync block; no conn held across the awaits below).
        let mut raw: IndexMap<String, String> = IndexMap::new();
        let (time_str, _now_local) = {
            let db = session_local().map_err(|e| PyError::other(e.to_string()))?;
            let tz_name = resolve_task_timezone(&db, task);
            let (time_str, now_local) = self.now_for_checkin(tz_name.as_deref());
            self.checkin_calendar(&db, now_local, &mut raw);
            (time_str, now_local)
        };

        // Notes/Tasks via do_manage_notes (returns a result map, never raises).
        let r = crate::src::tool_implementations::do_manage_notes(
            &serde_json::to_string(&json!({"action": "list"})).unwrap_or_default(),
            task.owner.as_deref(),
        )
        .await;
        let val = r
            .get("results")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .or_else(|| r.get("response").and_then(|v| v.as_str()).filter(|s| !s.is_empty()))
            .unwrap_or("No notes")
            .to_string();
        raw.insert("notes_tasks".to_string(), val);

        // Integrations discovery (Miniflux HTTP): DEFER honestly (see method
        // doc). The Python wraps it in `try/except` and silently skips on
        // failure, so an empty result here preserves the "no extra sections"
        // outcome.

        // Auto-discover MCP sources. Walk the live manager's connected,
        // non-builtin servers and, for each `CHECKIN_MCP_PATTERNS` entry whose
        // `detect` tool the server exposes, call the pattern's tool once.
        if let Some(mcp) = crate::src::agent_tools::get_mcp_manager() {
            let mut discovered: HashSet<String> = HashSet::new();
            // `connected_server_snapshots` already skips builtins; it returns one
            // entry per non-builtin server with its (tool_names, status, identity).
            for (server_id, tool_names, status, identity) in mcp.connected_server_snapshots() {
                // `if conn.get("status") != "connected": continue`.
                if status != "connected" {
                    continue;
                }
                let names: HashSet<&str> = tool_names.iter().map(String::as_str).collect();
                for pattern in CHECKIN_MCP_PATTERNS.iter() {
                    if !names.contains(pattern.detect) {
                        continue;
                    }
                    // `key = f"{pattern['section']}_{server_id}"` — one section per
                    // (section, server) pair, regardless of how many patterns match.
                    let key = format!("{}_{}", pattern.section, server_id);
                    if discovered.contains(&key) {
                        continue;
                    }
                    discovered.insert(key);

                    // `label = f"{section} ({identity})" if identity else section`
                    // (task_scheduler.py:1082). The Python gates PURELY on a
                    // non-empty `identity` — the `label_from_identity` pattern
                    // field is declared in the table but never read in this loop,
                    // so it must NOT gate the label here.
                    let label = if !identity.is_empty() {
                        format!("{} ({})", pattern.section, identity)
                    } else {
                        pattern.section.to_string()
                    };
                    let _ = pattern.label_from_identity; // declared-but-unread, as in Python

                    let qualified = format!("mcp__{}__{}", server_id, pattern.tool);
                    // `args = dict(pattern.args); args["account"] = "default"`.
                    let mut args = (pattern.args)();
                    if let Some(obj) = args.as_object_mut() {
                        obj.insert("account".to_string(), json!("default"));
                    }

                    let result = mcp.call_tool(&qualified, args).await;
                    // `if result.get("exit_code", 0) != 0: continue`.
                    if result.get("exit_code").and_then(|v| v.as_i64()).unwrap_or(0) != 0 {
                        continue;
                    }
                    // `content = result.get("stdout") or result.get("output") or ""`.
                    let content = result
                        .get("stdout")
                        .and_then(|v| v.as_str())
                        .filter(|s| !s.is_empty())
                        .or_else(|| {
                            result.get("output").and_then(|v| v.as_str()).filter(|s| !s.is_empty())
                        })
                        .unwrap_or("");
                    if content.trim().is_empty() {
                        continue;
                    }
                    // `raw[label] = content[:3000]` (3000-char cap by code point).
                    //
                    // NOTE: the Python check-in loop stores the RAW MCP output and
                    // does NOT apply `pattern["formatter"]` here — the `formatter`
                    // field is declared on the pattern table but never consumed in
                    // `_execute_checkin` (only `_format_email_output` exists as a
                    // staticmethod; nothing calls it in this path). To preserve the
                    // observable byte-shape of the data dump, we mirror that exactly
                    // and store the raw content; `pattern.formatter` stays unused by
                    // this loop. (See deferral note.)
                    let _ = pattern.formatter; // declared-but-unconsumed, as in Python
                    raw.insert(label, truncate_chars(content, 3000));
                }
            }
        }

        // Build data dump.
        let mut data_dump = format!("Current time: {time_str}\n\n");
        for (key, val) in &raw {
            data_dump.push_str(&format!("--- {key} ---\n{val}\n\n"));
        }

        let prompt = task.prompt.clone().unwrap_or_default();
        let context = format!(
            "{data_dump}---\n\n{prompt}\n\n\
Write the check-in. YOU decide what matters, what to skip, how to format. \
Only show future events. Calendar events are pre-tagged with importance: \
[!!] critical, [!] high, plain = normal, ' ·' = low. \
GROUP your output by importance — lead with critical/high, then normal, \
skip low entirely unless explicitly relevant. Mention event type (work/health/travel/etc) \
where it adds context (e.g. 'leave 1h early for travel'). \
Flag anything coming up that needs prep (birthdays, deadlines, holidays). \
Use tools to take action if needed. Keep it concise — no raw data dumps."
        );

        let system_prompt = crew
            .and_then(|c| c.personality.as_deref())
            .map(|p| p.trim().to_string());

        self.run_agent_loop(
            endpoint_url,
            model,
            task,
            session_id,
            system_prompt.as_deref(),
            None,
            None,
            Some(&context),
        )
        .await
    }

    /// Calendar pull for the check-in (three windows, grouped by importance).
    fn checkin_calendar(
        &self,
        db: &Connection,
        now_local: NaiveDateTime,
        raw: &mut IndexMap<String, String>,
    ) {
        let windows: [(&str, i64, i64); 3] = [
            ("today_tomorrow", 0, 2),
            ("this_week", 2, 7),
            ("next_30_days", 8, 30),
        ];
        for (label, start_off, end_off) in windows {
            let start = now_local + Duration::days(start_off);
            let end = now_local + Duration::days(end_off);
            // mirrors Python calendar_events row tuple (dtstart, summary, event_type, location, importance)
            #[allow(clippy::type_complexity)]
            let res = (|| -> rusqlite::Result<Vec<(String, Option<String>, Option<String>, Option<String>, String)>> {
                let mut stmt = db.prepare(
                    "SELECT dtstart, summary, event_type, location, importance \
                     FROM calendar_events \
                     WHERE dtstart >= ?1 AND dtstart <= ?2 AND status != 'cancelled' \
                     ORDER BY dtstart",
                )?;
                let rows = stmt
                    .query_map(
                        rusqlite::params![fmt_dt(start), fmt_dt(end)],
                        |r| {
                            Ok((
                                r.get::<_, String>(0)?,
                                r.get::<_, Option<String>>(1)?,
                                r.get::<_, Option<String>>(2)?,
                                r.get::<_, Option<String>>(3)?,
                                r.get::<_, Option<String>>(4)?.unwrap_or_else(|| "normal".to_string()),
                            ))
                        },
                    )?
                    .filter_map(|x| x.ok())
                    .collect();
                Ok(rows)
            })();
            let evs = match res {
                Ok(e) if !e.is_empty() => e,
                Ok(_) => continue,
                Err(e) => {
                    raw.insert("calendar".to_string(), format!("Error: {e}"));
                    continue;
                }
            };
            // Group by importance: critical/high/normal/low.
            // mirrors Python by-importance dict of (dtstart, summary, event_type, location) tuples
            #[allow(clippy::type_complexity)]
            let mut by_imp: IndexMap<String, Vec<(String, Option<String>, Option<String>, Option<String>)>> =
                IndexMap::new();
            for tier in ["critical", "high", "normal", "low"] {
                by_imp.insert(tier.to_string(), Vec::new());
            }
            for (dtstart, summary, etype, loc, importance) in evs {
                let imp = importance.to_lowercase();
                by_imp
                    .entry(imp)
                    .or_default()
                    .push((dtstart, summary, etype, loc));
            }
            let mut lines: Vec<String> = Vec::new();
            for tier in ["critical", "high", "normal", "low"] {
                let items = match by_imp.get(tier) {
                    Some(v) if !v.is_empty() => v,
                    _ => continue,
                };
                let marker = match tier {
                    "critical" => "[!!]",
                    "high" => "[!]",
                    "normal" => "  ",
                    _ => " ·",
                };
                for (dtstart, summary, etype, loc) in items {
                    let t = parse_dt(Some(dtstart))
                        .map(|d| d.format("%a %b %d %H:%M").to_string())
                        .unwrap_or_else(|| dtstart.clone());
                    let tag = etype
                        .as_deref()
                        .filter(|s| !s.is_empty())
                        .map(|s| format!(" ({s})"))
                        .unwrap_or_default();
                    let l = loc
                        .as_deref()
                        .filter(|s| !s.is_empty())
                        .map(|s| format!(" @ {s}"))
                        .unwrap_or_default();
                    let sm = summary.clone().unwrap_or_default();
                    lines.push(format!("{marker} {t} — {sm}{tag}{l}"));
                }
            }
            if !lines.is_empty() {
                raw.insert(format!("calendar_{label}"), lines.join("\n"));
            }
        }
    }

    /// `_execute_research_task(self, task, db)`.
    async fn execute_research_task(
        &self,
        task: &ScheduledTaskRow,
        last_run_model: &mut Option<String>,
    ) -> PyResult<String> {
        use crate::src::deep_research::DeepResearcher;
        use crate::src::research_handler::{extract_raw_findings, extract_sources, RESEARCH_DATA_DIR};

        // ── Resolve endpoint/model + headers (sync block; no conn across await) ──
        let (endpoint_url, model, headers) = {
            let db = session_local().map_err(|e| PyError::other(e.to_string()))?;
            // Resolve endpoint/model: task settings > session defaults.
            let mut endpoint_url = task.endpoint_url.clone().filter(|s| !s.is_empty());
            let mut model = task.model.clone().filter(|s| !s.is_empty());
            // LIVE: resolve_endpoint("research", ...) — task_scheduler.py:1518-1530.
            // Python passes owner=None (the 4th positional arg is fallback_headers,
            // so owner stays default None — NOT task.owner). The Rust resolver dropped
            // the fallback_* params, so Python's `ep_x or fallback_x` becomes
            // `u.or(endpoint_url)` / `m.or(model)`: when the resolver returns None
            // (unconfigured), task.endpoint_url/model are preserved exactly (those were
            // Python's fallback args). Errors can't escape (the fn can't panic), so the
            // Python try/except->pass is a no-op here.
            if endpoint_url.is_none() || model.is_none() {
                let (u, m, _h) = resolve_endpoint("research", None);
                endpoint_url = u.or(endpoint_url);
                model = m.or(model);
            }
            // task settings/resolver miss > session defaults (_resolve_defaults is
            // owner-aware — task_scheduler.py:1532-1533 passes task.owner).
            if endpoint_url.is_none() || model.is_none() {
                let (u, m) = self.resolve_defaults(&db, task.owner.as_deref());
                endpoint_url = endpoint_url.or(u);
                model = model.or(m);
            }
            let (endpoint_url, model) = match (endpoint_url, model) {
                (Some(u), Some(m)) => (u, m),
                _ => return Err(PyError::other("No model/endpoint configured for research")),
            };
            let headers = self.resolve_headers_for_endpoint(&db, &endpoint_url);
            (endpoint_url, model, headers)
        };
        *last_run_model = Some(model.clone());

        // research_max_tokens (settings) — default 8192 via settings.rs.
        let max_tokens = crate::src::settings::get_setting("research_max_tokens", json!(8192))
            .as_i64()
            .unwrap_or(8192);

        let mut researcher = DeepResearcher::new(endpoint_url.clone(), model.clone())
            .with_headers(if headers.is_empty() { None } else { Some(headers) });
        researcher.max_rounds = 8;
        researcher.max_time = 600; // 10 min for scheduled research
        researcher.max_report_tokens = max_tokens;

        let started_ts = crate::pytime::time();
        let report = researcher
            .research(task.prompt.as_deref().unwrap_or(""), "", None, None)
            .await;
        let completed_ts = crate::pytime::time();
        let stats = researcher.get_stats();

        // Ensure a session exists for output (sync block).
        let session_id = {
            let db = session_local().map_err(|e| PyError::other(e.to_string()))?;
            self.ensure_task_session(&db, task, &endpoint_url, &model, "[Research] ")
        };

        // Persist scheduled research in the Research-panel on-disk shape.
        let persist = (|| -> std::io::Result<()> {
            crate::pyos::makedirs(&RESEARCH_DATA_DIR, true)?;
            let findings = &researcher.findings;
            // stats IndexMap -> serde_json object preserving order.
            let mut stats_obj = serde_json::Map::new();
            for (k, v) in &stats {
                stats_obj.insert(k.clone(), v.clone());
            }
            let payload = json!({
                "query": task.prompt.clone()
                    .filter(|s| !s.is_empty())
                    .unwrap_or_else(|| if task.name.is_empty() { "Scheduled research".to_string() } else { task.name.clone() }),
                "status": "done",
                "result": report,
                "raw_report": crate::src::research_utils::strip_thinking(Some(&report)).unwrap_or_default(),
                "sources": extract_sources(findings),
                "raw_findings": extract_raw_findings(findings),
                "stats": Value::Object(stats_obj),
                "category": "scheduled",
                "started_at": started_ts,
                "completed_at": completed_ts,
                "owner": task.owner.clone().unwrap_or_default(),
                "task_id": task.id,
                "task_name": task.name,
            });
            let path = crate::pyos::path::join(&RESEARCH_DATA_DIR, &format!("{session_id}.json"));
            std::fs::write(&path, serde_json::to_string(&payload).unwrap_or_default())?;
            // fire_event("research_completed", task.owner or None) — empty owner
            // collapses to None just like Python's `task.owner or None`.
            let owner = task.owner.as_deref().filter(|s| !s.is_empty());
            crate::src::event_bus::fire_event("research_completed", owner);
            Ok(())
        })();
        if let Err(e) = persist {
            logger::warning(&format!(
                "Failed to persist task research report {session_id}: {e}"
            ));
        }

        Ok(report)
    }

    /// `_run_agent_loop(...)` — run the agent loop, collect final text.
    #[allow(clippy::too_many_arguments)]
    async fn run_agent_loop(
        &self,
        endpoint_url: &str,
        model: &str,
        task: &ScheduledTaskRow,
        session_id: &str,
        system_prompt: Option<&str>,
        disabled_tools: Option<HashSet<String>>,
        relevant_tools: Option<HashSet<String>>,
        override_user_message: Option<&str>,
    ) -> PyResult<String> {
        use crate::src::agent_loop::{stream_agent_loop, AgentLoopArgs};
        use futures_util::StreamExt;

        let system_content = system_prompt
            .filter(|s| !s.is_empty())
            .unwrap_or("You are a helpful assistant executing a scheduled task. Use available tools to complete the task thoroughly.")
            .to_string();
        let user_content = override_user_message
            .map(|s| s.to_string())
            .or_else(|| task.prompt.clone())
            .unwrap_or_default();
        let messages = vec![
            json!({"role": "system", "content": system_content}),
            json!({"role": "user", "content": user_content}),
        ];

        // Resolve headers from the endpoint's API key.
        let db2 = session_local().map_err(|e| PyError::other(e.to_string()))?;
        let headers = self.resolve_headers_for_endpoint(&db2, endpoint_url);
        drop(db2);

        // max_steps -> max_rounds (default 20).
        let max_rounds = match task.max_steps {
            Some(n) if n > 0 => n,
            _ => 20,
        };
        // `fallbacks=resolve_utility_fallback_candidates()` (task_scheduler.py:1444):
        // the agent loop falls through to the user's utility fallback chain when the
        // primary endpoint dies.
        #[allow(clippy::type_complexity)]
        let fallbacks: Option<Vec<(String, String, IndexMap<String, String>)>> = {
            let fb = crate::src::endpoint_resolver::resolve_utility_fallback_candidates(None);
            if fb.is_empty() {
                None
            } else {
                Some(fb)
            }
        };

        let args = AgentLoopArgs {
            endpoint_url: endpoint_url.to_string(),
            model: model.to_string(),
            messages,
            headers,
            max_rounds,
            session_id: Some(session_id.to_string()),
            owner: task.owner.clone(),
            disabled_tools,
            relevant_tools,
            fallbacks,
            ..Default::default()
        };

        let mut full_text = String::new();
        let mut tool_results: Vec<String> = Vec::new();
        let stream = stream_agent_loop(args);
        futures_util::pin_mut!(stream);
        while let Some(event_str) = stream.next().await {
            if event_str.starts_with("data: ") && !event_str.starts_with("data: [DONE]") {
                let payload = &event_str[6..];
                if let Ok(data) = serde_json::from_str::<Value>(payload) {
                    if let Some(delta) = data.get("delta").and_then(|v| v.as_str()) {
                        full_text.push_str(delta);
                    } else if data.get("type").and_then(|v| v.as_str()) == Some("tool_output") {
                        let summary = data
                            .get("stdout")
                            .and_then(|v| v.as_str())
                            .or_else(|| data.get("output").and_then(|v| v.as_str()))
                            .or_else(|| data.get("result").and_then(|v| v.as_str()))
                            .unwrap_or("");
                        if !summary.trim().is_empty() {
                            let tool = data.get("tool").and_then(|v| v.as_str()).unwrap_or("?");
                            let chars: Vec<char> = summary.chars().collect();
                            let snippet: String = chars.iter().take(500).collect();
                            tool_results.push(format!("[{tool}] {snippet}"));
                        }
                    }
                }
            }
        }

        // Grace summarization.
        if full_text.trim().is_empty() {
            let mut grace_context = "You ran out of steps. ".to_string();
            if !tool_results.is_empty() {
                let tail: Vec<String> = tool_results
                    .iter()
                    .rev()
                    .take(5)
                    .cloned()
                    .collect::<Vec<_>>()
                    .into_iter()
                    .rev()
                    .collect();
                grace_context.push_str("Here's what your tools returned:\n");
                grace_context.push_str(&tail.join("\n"));
            } else {
                grace_context.push_str("No tool results were captured.");
            }
            grace_context.push_str(
                "\n\nSummarize what you accomplished and what's still pending. Be concise.",
            );
            // `llm_call_async_with_fallback([(endpoint,model,headers)] +
            // resolve_utility_fallback_candidates(), messages, timeout=30)`
            // (task_scheduler.py:1481-1490): grace summarization survives a dead
            // primary by falling through to the utility chain.
            let db3 = session_local().map_err(|e| PyError::other(e.to_string()))?;
            let grace_headers = self.resolve_headers_for_endpoint(&db3, endpoint_url);
            drop(db3);
            let mut grace_candidates =
                vec![(endpoint_url.to_string(), model.to_string(), grace_headers)];
            grace_candidates
                .extend(crate::src::endpoint_resolver::resolve_utility_fallback_candidates(None));
            // Python passes only timeout=30 here, so temperature/max_tokens inherit
            // the LLMConfig defaults DEFAULT_TEMPERATURE=1.0 / DEFAULT_MAX_TOKENS=0.
            match crate::src::llm_core::llm_call_async_with_fallback(
                grace_candidates,
                vec![
                    json!({"role": "system", "content": system_content}),
                    json!({"role": "user", "content": grace_context}),
                ],
                1.0,
                0,
                30,
            )
            .await
            {
                Ok(t) => full_text = t.trim().to_string(),
                Err(e) => {
                    logger::warning(&format!("Grace summarization failed: {e}"));
                    if !tool_results.is_empty() {
                        let tail: Vec<String> = tool_results
                            .iter()
                            .rev()
                            .take(5)
                            .cloned()
                            .collect::<Vec<_>>()
                            .into_iter()
                            .rev()
                            .collect();
                        full_text = tail.join("\n");
                    }
                }
            }
        }

        Ok(if full_text.is_empty() {
            "(no output)".to_string()
        } else {
            full_text
        })
    }

    /// `_deliver_task_result(self, task, result, db, model)`.
    async fn deliver_task_result(&self, task: &ScheduledTaskRow, result: &str, model: Option<&str>) {
        let output = task.output_target.clone().unwrap_or_else(|| "session".to_string());
        if output.starts_with("mcp__") {
            self.deliver_via_mcp(&output, task, result).await;
            return;
        }
        if is_email_output_target(&output) {
            self.deliver_via_email(&output, task, result).await;
            return;
        }
        if output != "session" {
            return;
        }
        // Session delivery is entirely synchronous DB work — done in a non-async
        // helper so the rusqlite `Connection` never enters this future's state.
        self.deliver_to_session(task, result, model);
    }

    /// Synchronous session-delivery branch of `_deliver_task_result` (writes the
    /// user + assistant `chat_messages` rows). Non-async so the `Connection`
    /// stays out of any `Send` future.
    fn deliver_to_session(&self, task: &ScheduledTaskRow, result: &str, model: Option<&str>) {
        let db = match session_local() {
            Ok(c) => c,
            Err(_) => return,
        };
        let crew = task.crew_member_id.as_deref().and_then(|id| load_crew(&db, id));
        let mut endpoint_url = task.endpoint_url.clone();
        let mut model_name = model.map(|m| m.to_string()).or_else(|| task.model.clone());
        if endpoint_url.is_none() || model_name.is_none() {
            if let Some(c) = crew.as_ref() {
                endpoint_url = endpoint_url.or_else(|| c.endpoint_url.clone());
                model_name = model_name.or_else(|| c.model.clone());
            }
        }
        if endpoint_url.is_none() || model_name.is_none() {
            let (u, m) = self.resolve_defaults(&db, task.owner.as_deref());
            endpoint_url = endpoint_url.or(u);
            model_name = model_name.or(m);
        }

        let session_id = self.ensure_task_session(
            &db,
            task,
            endpoint_url.as_deref().unwrap_or(""),
            model_name.as_deref().unwrap_or(""),
            "[Task] ",
        );

        // meta
        let mut meta = serde_json::Map::new();
        if let Some(m) = &model_name {
            meta.insert("model".to_string(), json!(m));
        }
        if crew.as_ref().map(|c| c.is_default_assistant).unwrap_or(false) {
            meta.insert("source".to_string(), json!("cron"));
            meta.insert("task_id".to_string(), json!(task.id));
            meta.insert("task_name".to_string(), json!(task.name));
        }
        let msg_meta = serde_json::to_string(&Value::Object(meta)).unwrap_or_else(|_| "{}".to_string());
        let user_content = task
            .prompt
            .clone()
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| format!("[Task] {}", task.name));
        let now = pydatetime::utcnow_naive_iso();
        let _ = db.execute(
            "INSERT INTO chat_messages (id, session_id, role, content, timestamp, meta_data) \
             VALUES (?1, ?2, 'user', ?3, ?4, ?5)",
            rusqlite::params![
                uuid::Uuid::new_v4().to_string(),
                session_id,
                user_content,
                now,
                msg_meta
            ],
        );
        let _ = db.execute(
            "INSERT INTO chat_messages (id, session_id, role, content, timestamp, meta_data) \
             VALUES (?1, ?2, 'assistant', ?3, ?4, ?5)",
            rusqlite::params![
                uuid::Uuid::new_v4().to_string(),
                session_id,
                result,
                now,
                msg_meta
            ],
        );
        // session_manager in-memory cache update — no-op (see TaskScheduler::new).
    }

    // (see the module-level `resolve_send_config` below for the inlined
    // `_resolve_send_config` mirror this method consumes)

    /// `_deliver_via_email(self, output, task, result)` — send task output
    /// through the app's configured SMTP account (task_scheduler.py:1363-1401).
    ///
    /// Supported `output` (already gated by `is_email_output_target`):
    ///   * `email` / `email:self` -> send to the account's From address
    ///   * `email:name@example.com` / raw `name@example.com` -> send there
    ///
    /// Transport reused verbatim: the ported SMTP sender
    /// `email_helpers::send_smtp_message` (lettre, BLOCKING -> `spawn_blocking`)
    /// plus the ported send-config resolver. The Python `_resolve_send_config`
    /// is ported MODULE-PRIVATE as `routes::email_routes::resolve_send_config`,
    /// so — to keep this wiring pass scoped to this file — its logic is inlined
    /// here via [`resolve_send_config`] (a faithful mirror of that exact ported
    /// fn / Python `_resolve_send_config`).
    ///
    /// Run-status note (faithful to the task brief): Python re-raises on a send
    /// failure, but the caller (`deliver_task_result`) runs AFTER `finish_run`
    /// already recorded the run as success. The Rust method returns `()` and is
    /// invoked the same way, so a failure is logged (`logger::error`, matching
    /// the Python `logger.error(..., exc_info=True)`) and NOT propagated — the
    /// run status is left untouched, exactly as in the Python flow.
    async fn deliver_via_email(&self, output: &str, task: &ScheduledTaskRow, result: &str) {
        // `target = (output or "").strip()`
        let target = output.trim();
        // `explicit = ...`: `email:<addr>` keeps the suffix; a bare `addr@host`
        // is used as-is; anything else leaves `explicit` empty (py:1373-1377).
        let explicit: String = if let Some(rest) = target.strip_prefix("email:") {
            rest.trim().to_string()
        } else if target.contains('@') {
            target.to_string()
        } else {
            String::new()
        };

        // The whole send is the Python `try/except ... raise`. Here the failure
        // never propagates (see the run-status note above): we log it instead.
        let send_result: Result<String, String> = (async {
            // `cfg = _resolve_send_config(owner=task.owner or "")`.
            let owner = task.owner.clone().unwrap_or_default();
            let cfg = resolve_send_config(None, &owner)?;

            // `to_addr = explicit or cfg.from_address or cfg.smtp_user or ""`.
            let mut to_addr = explicit.clone();
            if to_addr.is_empty() {
                to_addr = cfg.from_address.clone();
            }
            if to_addr.is_empty() {
                to_addr = cfg.smtp_user.clone();
            }
            if to_addr.is_empty() {
                return Err("No email recipient resolved for task output".to_string());
            }

            // `from_addr = cfg.from_address or cfg.smtp_user or to_addr`.
            let from_addr = if !cfg.from_address.is_empty() {
                cfg.from_address.clone()
            } else if !cfg.smtp_user.is_empty() {
                cfg.smtp_user.clone()
            } else {
                to_addr.clone()
            };

            // Build the `EmailMessage().set_content(result)` analogue: a SINGLE
            // non-multipart text/plain message (py:1389-1396). No `Date` header
            // is set by the Python (matched here). The body is carried `8bit` /
            // utf-8, the existing port's plain-part convention (build_mime,
            // email_pollers.rs:409-410) — lettre transmits the bytes verbatim.
            let body = result;
            let msg = format!(
                "From: {from_addr}\r\n\
                 To: {to_addr}\r\n\
                 Subject: [Task] {name}\r\n\
                 X-Odysseus-Origin: odysseus-ui\r\n\
                 X-Odysseus-Kind: task\r\n\
                 X-Odysseus-Ref: {ref_id}\r\n\
                 MIME-Version: 1.0\r\n\
                 Content-Type: text/plain; charset=\"utf-8\"\r\n\
                 Content-Transfer-Encoding: 8bit\r\n\r\n\
                 {body}\r\n",
                name = task.name,
                ref_id = task.id,
            );

            // `_send_smtp_message(cfg, from_addr, [to_addr], msg.as_string(), timeout=30)`.
            // BLOCKING (lettre) -> spawn_blocking; own everything moved in.
            let cfg_owned = cfg.clone();
            let from_owned = from_addr.clone();
            let to_owned = to_addr.clone();
            let bytes = msg.into_bytes();
            let send = tokio::task::spawn_blocking(move || {
                crate::routes::email_helpers::send_smtp_message(
                    &cfg_owned,
                    &from_owned,
                    &[to_owned],
                    &bytes,
                    30,
                )
            })
            .await;
            match send {
                Ok(Ok(())) => Ok(to_addr),
                Ok(Err(e)) => Err(e),
                Err(e) => Err(format!("send task panicked: {e}")),
            }
        })
        .await;

        match send_result {
            Ok(to_addr) => {
                // `logger.info("Task %s emailed result to %s (%sb)", task.id, to_addr, len(result))`.
                logger::info(&format!(
                    "Task {} emailed result to {to_addr} ({}b)",
                    task.id,
                    result.len()
                ));
            }
            Err(e) => {
                // `logger.error("Task %s email delivery failed: %s", task.id, e)`.
                logger::error(&format!("Task {} email delivery failed: {e}", task.id));
            }
        }
    }

    /// `_deliver_via_mcp(self, tool_name, task, result)`.
    ///
    /// Routes the task output to an MCP delivery tool (e.g. an email/Slack MCP
    /// server). Resolves the recipient by preferring the configured email From
    /// (the `daily_brief` "email yourself" pattern) then falling back to the task
    /// owner when it looks like an email. Common recipient field names
    /// (to / recipient / email / address) are all populated so we work across MCP
    /// servers without special-casing each tool's schema. When the MCP manager is
    /// absent it logs and returns, exactly like the Python `if not mcp:` branch.
    async fn deliver_via_mcp(&self, tool_name: &str, task: &ScheduledTaskRow, result: &str) {
        // `from src.agent_tools import get_mcp_manager; mcp = get_mcp_manager()`.
        let mcp = match crate::src::agent_tools::get_mcp_manager() {
            Some(m) => m,
            None => {
                logger::warning(&format!(
                    "Task {}: MCP manager not available for delivery",
                    task.id
                ));
                return;
            }
        };

        // Resolve recipient — prefer the configured email From (the established
        // "email yourself" pattern from daily_brief), fall back to task.owner.
        // `get_email_config(None, "")` mirrors Python's no-arg `_get_email_config()`:
        // no account pins, empty owner matches any owner -> the default account.
        let mut recipient: Option<String> = {
            let cfg = crate::routes::email_helpers::get_email_config(None, "");
            let from = cfg.from_address.trim().to_string();
            if from.is_empty() {
                None
            } else {
                Some(from)
            }
        };
        if recipient.is_none() {
            if let Some(owner) = task.owner.as_deref() {
                if owner.contains('@') {
                    recipient = Some(owner.to_string());
                }
            }
        }

        let mut args = json!({
            "subject": format!("[Task] {}", task.name),
            "body": result,
            "headers": {
                "X-Odysseus-Origin": "odysseus-ui",
                "X-Odysseus-Kind": "task",
                "X-Odysseus-Ref": task.id,
            },
        });
        if let Some(r) = recipient.as_deref() {
            // Cover the common field names so we work across MCP servers (Gmail,
            // generic SMTP, Slack DMs, etc.) without having to hard-code each.
            if let Some(obj) = args.as_object_mut() {
                obj.insert("to".to_string(), json!(r));
                obj.insert("recipient".to_string(), json!(r));
                obj.insert("email".to_string(), json!(r));
                obj.insert("address".to_string(), json!(r));
            }
        } else {
            logger::warning(&format!(
                "Task {}: no recipient resolved for MCP delivery via {tool_name} — \
                 set an email From address in Settings or give the task an owner email.",
                task.id
            ));
        }

        // `mcp.call_tool` never raises in the Rust port (the Python `try/except`
        // wraps an awaitable that can throw); a transport error surfaces as a
        // result with a non-zero exit_code, handled by the failure branch below.
        let mcp_result = mcp.call_tool(tool_name, args).await;
        let stderr = mcp_result.get("stderr").and_then(|v| v.as_str()).unwrap_or("");
        let stdout = mcp_result.get("stdout").and_then(|v| v.as_str()).unwrap_or("");
        let body_len = result.len();
        let exit_code = mcp_result.get("exit_code").and_then(|v| v.as_i64()).unwrap_or(0);
        if exit_code != 0 {
            logger::warning(&format!(
                "Task {} MCP delivery FAILED via {tool_name}: exit={exit_code} \
                 stderr={:?} stdout={:?}",
                task.id,
                truncate_chars(stderr, 400),
                truncate_chars(stdout, 400),
            ));
        } else {
            // Include the MCP tool's own stdout (e.g. email_server returns
            // "Sent email to ... with subject ...") + the body size so a silent
            // SMTP failure is easier to spot in the logs.
            logger::info(&format!(
                "Task {} delivered via MCP tool {tool_name} \
                 (to={}, body={body_len}b, reply={:?})",
                task.id,
                recipient.as_deref().unwrap_or("<unset>"),
                truncate_chars(stdout, 200),
            ));
        }
    }

    /// `_run_chained(self, task_id)`.
    async fn run_chained(self: &Arc<Self>, task_id: &str) {
        {
            let mut executing = self.executing.lock().await;
            if executing.contains(task_id) {
                return;
            }
            executing.insert(task_id.to_string());
        }
        self.execute_task(task_id, false, true).await;
    }

    /// `_has_chain_cycle(self, db, start_id, max_depth=10)`.
    fn has_chain_cycle(&self, db: &Connection, start_id: &str, max_depth: usize) -> bool {
        let mut visited: HashSet<String> = HashSet::new();
        let mut current = start_id.to_string();
        for _ in 0..max_depth {
            if visited.contains(&current) {
                return true;
            }
            visited.insert(current.clone());
            // query_row -> Result<Option<String>>, .optional() -> Result<Option<Option<String>>>,
            // .ok() -> Option<Option<Option<String>>>. Flatten twice to the id.
            let next: Option<String> = db
                .query_row(
                    "SELECT then_task_id FROM scheduled_tasks WHERE id = ?1",
                    [&current],
                    |r| r.get::<_, Option<String>>(0),
                )
                .optional()
                .ok()
                .flatten()
                .flatten();
            match next {
                Some(nid) if !nid.is_empty() => current = nid,
                _ => return false, // not found OR no then_task_id
            }
        }
        true // too deep, treat as cycle
    }

    /// `_resolve_defaults(self, db, owner)`.
    fn resolve_defaults(&self, db: &Connection, owner: Option<&str>) -> (Option<String>, Option<String>) {
        let (sql, has_owner) = match owner.filter(|o| !o.is_empty()) {
            Some(_) => (
                "SELECT endpoint_url, model FROM sessions \
                 WHERE endpoint_url IS NOT NULL AND model IS NOT NULL AND owner = ?1 \
                 ORDER BY created_at DESC LIMIT 1",
                true,
            ),
            None => (
                "SELECT endpoint_url, model FROM sessions \
                 WHERE endpoint_url IS NOT NULL AND model IS NOT NULL \
                 ORDER BY created_at DESC LIMIT 1",
                false,
            ),
        };
        let row: Option<(Option<String>, Option<String>)> = if has_owner {
            db.query_row(sql, [owner.unwrap()], |r| Ok((r.get(0)?, r.get(1)?)))
                .optional()
                .ok()
                .flatten()
        } else {
            db.query_row(sql, [], |r| Ok((r.get(0)?, r.get(1)?)))
                .optional()
                .ok()
                .flatten()
        };
        match row {
            Some((u, m)) => (u, m),
            None => (None, None),
        }
    }

    /// Resolve auth headers for an endpoint URL by matching enabled model_endpoints.
    fn resolve_headers_for_endpoint(
        &self,
        db: &Connection,
        endpoint_url: &str,
    ) -> IndexMap<String, String> {
        let res = (|| -> rusqlite::Result<IndexMap<String, String>> {
            let mut stmt = db.prepare(
                "SELECT base_url, api_key FROM model_endpoints WHERE is_enabled = 1",
            )?;
            let rows: Vec<(Option<String>, Option<String>)> = stmt
                .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?
                .filter_map(|x| x.ok())
                .collect();
            for (base_url, api_key) in rows {
                let base = base_url.unwrap_or_default();
                let normalized = normalize_base(Some(&base));
                if endpoint_url.contains(&normalized) || normalized.contains(endpoint_url) {
                    let key = api_key
                        .filter(|k| !k.is_empty())
                        .map(|k| crate::src::secret_storage::decrypt(&k));
                    return Ok(build_headers(key.as_deref(), &normalized));
                }
            }
            Ok(IndexMap::new())
        })();
        res.unwrap_or_default()
    }

    /// Ensure a `sessions` row exists for the task's output; returns the
    /// session_id (creating one + persisting `task.session_id` if absent).
    fn ensure_task_session(
        &self,
        db: &Connection,
        task: &ScheduledTaskRow,
        endpoint_url: &str,
        model: &str,
        name_prefix: &str,
    ) -> String {
        if let Some(sid) = task.session_id.clone().filter(|s| !s.is_empty()) {
            return sid;
        }
        let session_id = uuid::Uuid::new_v4().to_string();
        let now = pydatetime::utcnow_naive_iso();
        let _ = db.execute(
            "INSERT INTO sessions (id, name, endpoint_url, model, owner, created_at, updated_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?6)",
            rusqlite::params![
                session_id,
                format!("{name_prefix}{}", task.name),
                endpoint_url,
                model,
                task.owner,
                now
            ],
        );
        let _ = db.execute(
            "UPDATE scheduled_tasks SET session_id = ?1 WHERE id = ?2",
            rusqlite::params![session_id, task.id],
        );
        session_id
    }

    /// `run_task_now(self, task_id, *, force=False)`.
    pub async fn run_task_now(self: &Arc<Self>, task_id: &str, force: bool) -> bool {
        if force {
            let me = Arc::clone(self);
            let id = task_id.to_string();
            let handle = tokio::spawn(async move {
                me.execute_task(&id, true, false).await;
            });
            self.task_handles
                .lock()
                .await
                .insert(task_id.to_string(), handle.abort_handle());
            return true;
        }
        {
            let mut executing = self.executing.lock().await;
            if executing.contains(task_id) {
                return false;
            }
            executing.insert(task_id.to_string());
        }
        let me = Arc::clone(self);
        let id = task_id.to_string();
        let handle = tokio::spawn(async move {
            me.execute_task(&id, false, true).await;
        });
        self.task_handles
            .lock()
            .await
            .insert(task_id.to_string(), handle.abort_handle());
        true
    }

    /// `_mark_run_aborted(self, task_id, run_id=None, message="Stopped by user")` (sync).
    ///
    /// Marks the most-recent running/queued run for `task_id` (or a specific
    /// `run_id`) as `aborted`. Returns `true` if a row was actually updated, which
    /// tells `stop_task` whether it found live work to cancel.
    fn mark_run_aborted(
        &self,
        task_id: &str,
        run_id: Option<&str>,
        message: &str,
    ) -> bool {
        let db = match session_local() {
            Ok(c) => c,
            Err(e) => {
                logger::debug(&format!(
                    "Task abort marker failed for {task_id}: {e}"
                ));
                return false;
            }
        };
        let res = (|| -> rusqlite::Result<bool> {
            // Find the target run row.
            let (id, status): (Option<String>, Option<String>) = if let Some(rid) = run_id {
                db.query_row(
                    "SELECT id, status FROM task_runs WHERE id = ?1",
                    [rid],
                    |r| Ok((r.get(0)?, r.get(1)?)),
                )
                .optional()?
                .map(|(id, st)| (Some(id), st))
                .unwrap_or((None, None))
            } else {
                // Most-recent queued/running run for this task (Python `.order_by(started_at.desc())`).
                db.query_row(
                    "SELECT id, status FROM task_runs \
                     WHERE task_id = ?1 AND status IN ('queued', 'running') \
                     ORDER BY started_at DESC LIMIT 1",
                    [task_id],
                    |r| Ok((r.get(0)?, r.get(1)?)),
                )
                .optional()?
                .map(|(id, st): (String, Option<String>)| (Some(id), st))
                .unwrap_or((None, None))
            };
            let Some(row_id) = id else { return Ok(false) };
            if !matches!(status.as_deref(), Some("queued") | Some("running")) {
                return Ok(false);
            }
            let now = fmt_dt(utcnow());
            // `run.result = run.result or message` — keep existing result if set.
            db.execute(
                "UPDATE task_runs SET status='aborted', error=?1, \
                 result=COALESCE(NULLIF(result,''),?1), finished_at=?2 \
                 WHERE id=?3",
                rusqlite::params![message, now, row_id],
            )?;
            Ok(true)
        })();
        match res {
            Ok(v) => v,
            Err(e) => {
                logger::debug(&format!("Task abort marker failed for {task_id}: {e}"));
                false
            }
        }
    }

    /// `stop_task(self, task_id)` — request cancellation of a running/queued task
    /// and mark its run aborted.
    ///
    /// Mirrors the Python:
    /// ```python
    /// async def stop_task(self, task_id: str) -> bool:
    ///     handle = self._task_handles.get(task_id)
    ///     stopped = False
    ///     if handle and not handle.done():
    ///         handle.cancel(); stopped = True
    ///     async with self._executing_lock:
    ///         if task_id in self._executing:
    ///             self._executing.discard(task_id); stopped = True
    ///     stopped = self._mark_run_aborted(task_id) or stopped
    ///     return stopped
    /// ```
    ///
    /// Returns `true` if any work was found to stop (task was running/queued).
    pub async fn stop_task(&self, task_id: &str) -> bool {
        let mut stopped = false;

        // Abort the tokio task handle, if present and not yet finished.
        {
            let mut handles = self.task_handles.lock().await;
            if let Some(h) = handles.remove(task_id) {
                // AbortHandle::abort() is a no-op when the task has already
                // completed, matching `if handle and not handle.done()`.
                h.abort();
                stopped = true;
            }
        }

        // Discard from the in-flight executing set.
        {
            let mut executing = self.executing.lock().await;
            if executing.remove(task_id) {
                stopped = true;
            }
        }

        // Mark the DB run row as aborted.
        let aborted = self.mark_run_aborted(task_id, None, "Stopped by user");
        stopped = aborted || stopped;
        stopped
    }

    /// Local time string: `"%A, %B %d %Y, %H:%M %Z"` (tz) or UTC fallback.
    ///
    /// DEVIATION: chrono's `%Z` over a naive datetime renders empty (no tz info),
    /// so for the tz path we append the IANA zone name; for the UTC path the
    /// literal "UTC" matches the Python `strftime(... "%H:%M UTC")`.
    fn now_time_str(&self, tz_name: Option<&str>) -> String {
        use chrono_tz::Tz;
        match tz_name.and_then(|n| n.parse::<Tz>().ok()) {
            Some(z) => {
                let local = Utc.from_utc_datetime(&utcnow()).with_timezone(&z);
                format!("{} {}", local.format("%A, %B %d %Y, %H:%M"), z.name())
            }
            None => format!("{} UTC", utcnow().format("%A, %B %d %Y, %H:%M")),
        }
    }

    /// Check-in time string + the naive local "now" used for calendar windows.
    fn now_for_checkin(&self, tz_name: Option<&str>) -> (String, NaiveDateTime) {
        use chrono_tz::Tz;
        match tz_name.and_then(|n| n.parse::<Tz>().ok()) {
            Some(z) => {
                let local = Utc.from_utc_datetime(&utcnow()).with_timezone(&z);
                (
                    local.format("%A, %B %d %Y, %H:%M").to_string(),
                    local.naive_local(),
                )
            }
            None => {
                let now = utcnow();
                (now.format("%H:%M UTC").to_string(), now)
            }
        }
    }

    /// `ensure_defaults(self, owner)` — create default housekeeping tasks.
    ///
    /// PORT_VIA_DB. `tasks_enabled` / `tasks_opened` are read from the user's prefs
    /// via `prefs_store::load_for_user` (task_scheduler.py:1755-1760); a missing
    /// pref defaults to `false` (the Python `_prefs.get(..., False)` shape).
    pub async fn ensure_defaults(self: &Arc<Self>, owner: &str) {
        let prefs = crate::routes::prefs_store::load_for_user(
            Some(owner).filter(|o| !o.is_empty()),
        );
        let tasks_enabled = prefs
            .get("tasks_enabled")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
        let tasks_opened = prefs
            .get("tasks_opened")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);

        if let Ok(db) = session_local() {
            let res = self.ensure_defaults_db(&db, owner, tasks_enabled, tasks_opened);
            if let Err(e) = res {
                logger::warning(&format!("Failed to create default tasks: {e}"));
            }
        }

        // Always ensure the personal assistant exists.
        if let Err(e) = self.ensure_assistant_defaults(owner).await {
            logger::warning(&format!("Failed to seed assistant for {owner}: {e}"));
        }
    }

    fn ensure_defaults_db(
        &self,
        db: &Connection,
        owner: &str,
        tasks_enabled: bool,
        tasks_opened: bool,
    ) -> rusqlite::Result<()> {
        // name_to_action map (current + legacy names).
        let mut name_to_action: HashMap<String, &str> = HashMap::new();
        for (action, defs) in HOUSEKEEPING_DEFAULTS.iter() {
            name_to_action.insert(defs.name.to_string(), action);
            for legacy in defs.legacy_names {
                name_to_action.insert(legacy.to_string(), action);
            }
        }
        // Normalize legacy-named rows to action type.
        let possible_names: Vec<String> = name_to_action.keys().cloned().collect();
        if !possible_names.is_empty() {
            // Build `name IN (?, ?, …)`.
            let placeholders: Vec<String> = (0..possible_names.len()).map(|_| "?".to_string()).collect();
            let sql = format!(
                "SELECT id, name FROM scheduled_tasks WHERE owner = ? AND name IN ({})",
                placeholders.join(",")
            );
            let mut stmt = db.prepare(&sql)?;
            let mut params: Vec<&dyn rusqlite::ToSql> = vec![&owner];
            for n in &possible_names {
                params.push(n);
            }
            let rows: Vec<(String, String)> = stmt
                .query_map(params.as_slice(), |r| Ok((r.get(0)?, r.get(1)?)))?
                .filter_map(|x| x.ok())
                .collect();
            drop(stmt);
            for (id, name) in rows {
                if let Some(action) = name_to_action.get(&name) {
                    db.execute(
                        "UPDATE scheduled_tasks SET task_type='action', action=?1 WHERE id=?2",
                        rusqlite::params![action, id],
                    )?;
                }
            }
        }

        // Delete retired actions — first purge their task_runs, then the tasks.
        {
            // Collect IDs of retired scheduled_task rows for this owner.
            let mut retired_ids: Vec<String> = Vec::new();
            for retired in RETIRED_HOUSEKEEPING_ACTIONS.iter() {
                let mut stmt = db.prepare(
                    "SELECT id FROM scheduled_tasks \
                     WHERE owner=?1 AND task_type='action' AND action=?2",
                )?;
                let ids: Vec<String> = stmt
                    .query_map(rusqlite::params![owner, retired], |r| r.get::<_, String>(0))?
                    .filter_map(|x| x.ok())
                    .collect();
                retired_ids.extend(ids);
            }
            // Delete task_runs for the soon-to-be-deleted tasks first.
            for rid in &retired_ids {
                db.execute(
                    "DELETE FROM task_runs WHERE task_id = ?1",
                    rusqlite::params![rid],
                )?;
            }
            // Now delete the retired scheduled_tasks rows.
            for retired in RETIRED_HOUSEKEEPING_ACTIONS.iter() {
                db.execute(
                    "DELETE FROM scheduled_tasks WHERE owner=?1 AND task_type='action' AND action=?2",
                    rusqlite::params![owner, retired],
                )?;
            }
        }

        // Sweep orphan task_run rows (parent task deleted previously) so retired
        // actions stop showing in Activity. Only when at least one live task
        // exists — avoids wiping run history on a fresh DB.
        {
            let live_ids: Vec<String> = {
                let mut stmt = db.prepare("SELECT id FROM scheduled_tasks")?;
                // Bind to a `let` so `stmt` outlives the collected Vec (the
                // `?`-desugared temporary would otherwise outlive `stmt`).
                let rows = stmt.query_map([], |r| r.get::<_, String>(0))?;
                let out: Vec<String> = rows.filter_map(|x| x.ok()).collect();
                out
            };
            if !live_ids.is_empty() {
                // Delete task_runs whose task_id is not in the live set.
                // rusqlite doesn't support NOT IN with a runtime Vec directly via
                // a single bind, so we build a parameterized query.
                let placeholders: String = live_ids
                    .iter()
                    .enumerate()
                    .map(|(i, _)| format!("?{}", i + 1))
                    .collect::<Vec<_>>()
                    .join(",");
                let sql = format!(
                    "DELETE FROM task_runs WHERE task_id NOT IN ({placeholders})"
                );
                // rusqlite::params_from_iter for a Vec<String>.
                let _ = db.execute(
                    &sql,
                    rusqlite::params_from_iter(live_ids.iter()),
                );
            }
        }

        // existing_actions.
        let mut existing_actions: HashSet<String> = HashSet::new();
        {
            let mut stmt = db.prepare(
                "SELECT action FROM scheduled_tasks WHERE owner=?1 AND task_type='action'",
            )?;
            for r in stmt.query_map([owner], |r| r.get::<_, Option<String>>(0))? {
                if let Ok(Some(a)) = r {
                    existing_actions.insert(a);
                }
            }
        }

        // Builtin tasks for this owner, grouped by action (dedupe + normalize).
        let mut by_action: HashMap<String, Vec<ScheduledTaskRow>> = HashMap::new();
        {
            let action_keys: Vec<&str> = HOUSEKEEPING_DEFAULTS.keys().copied().collect();
            let placeholders: Vec<String> = (0..action_keys.len()).map(|_| "?".to_string()).collect();
            let sql = format!(
                "SELECT {TASK_COLS} FROM scheduled_tasks \
                 WHERE owner = ? AND task_type='action' AND action IN ({})",
                placeholders.join(",")
            );
            let mut stmt = db.prepare(&sql)?;
            let mut params: Vec<&dyn rusqlite::ToSql> = vec![&owner];
            for a in &action_keys {
                params.push(a);
            }
            let rows: Vec<ScheduledTaskRow> = stmt
                .query_map(params.as_slice(), map_task)?
                .filter_map(|x| x.ok())
                .collect();
            for t in rows {
                if let Some(a) = t.action.clone() {
                    by_action.entry(a).or_default().push(t);
                }
            }
        }

        // Dedupe per action — keep the best-scoring, delete the rest.
        let mut kept_ids: HashSet<String> = HashSet::new();
        let mut kept_rows: Vec<ScheduledTaskRow> = Vec::new();
        for (action, tasks) in &by_action {
            let defs = match HOUSEKEEPING_DEFAULTS.get(action.as_str()) {
                Some(d) => d,
                None => continue,
            };
            let desired_trigger = defs.trigger_type;
            // _score: (matches_default, is_active, created_key) descending.
            let score = |c: &ScheduledTaskRow| -> (i32, i32, (i64, u32, u32, u32, u32)) {
                let matches_default = c.trigger_type.as_deref().unwrap_or("schedule") == desired_trigger
                    && c.trigger_event.as_deref() == defs.trigger_event
                    && c.trigger_count.unwrap_or(1) == defs.trigger_count.unwrap_or(1)
                    && c.schedule.as_deref() == defs.schedule
                    && c.scheduled_time.as_deref() == defs.scheduled_time
                    && c.cron_expression.as_deref() == defs.cron_expression;
                let created = c.created_at.unwrap_or(NaiveDateTime::MIN);
                let created_key = (
                    created.date().num_days_from_ce() as i64,
                    created.hour(),
                    created.minute(),
                    created.second(),
                    created.and_utc().timestamp_subsec_micros(),
                );
                (
                    if matches_default { 1 } else { 0 },
                    if c.status.as_deref() == Some("active") { 1 } else { 0 },
                    created_key,
                )
            };
            let mut sorted = tasks.clone();
            // sorted(..., key=_score, reverse=True)[0] — stable sort then pick max.
            sorted.sort_by_key(|c| std::cmp::Reverse(score(c)));
            let keep = sorted[0].clone();
            kept_ids.insert(keep.id.clone());
            for dupe in tasks {
                if dupe.id == keep.id {
                    continue;
                }
                db.execute("DELETE FROM scheduled_tasks WHERE id=?1", [&dupe.id])?;
            }
            kept_rows.push(keep);
        }

        // Normalize the kept rows.
        for task in &kept_rows {
            let defs = match task.action.as_deref().and_then(|a| HOUSEKEEPING_DEFAULTS.get(a)) {
                Some(d) => d,
                None => continue,
            };
            let action = task.action.clone().unwrap_or_default();
            // legacy name rename.
            if defs.legacy_names.contains(&task.name.as_str()) {
                db.execute(
                    "UPDATE scheduled_tasks SET name=?1 WHERE id=?2",
                    rusqlite::params![defs.name, task.id],
                )?;
            }
            // check_email_urgency old-cron normalization.
            if action == "check_email_urgency"
                && task.schedule.as_deref() == Some("cron")
                && defs
                    .old_cron_expressions
                    .contains(&task.cron_expression.as_deref().unwrap_or(""))
            {
                let next_run = compute_next_run(
                    defs.schedule,
                    defs.scheduled_time,
                    None,
                    None,
                    Some(utcnow()),
                    defs.cron_expression,
                    resolve_task_timezone(db, task).as_deref(),
                );
                db.execute(
                    "UPDATE scheduled_tasks SET cron_expression=?1, next_run=?2 WHERE id=?3",
                    rusqlite::params![defs.cron_expression, next_run.map(fmt_dt), task.id],
                )?;
            }
            // event-trigger normalization.
            let desired_trigger = defs.trigger_type;
            let needs_event_norm = desired_trigger == "event"
                && (task.trigger_type.as_deref().unwrap_or("schedule") != "event"
                    || task.trigger_event.as_deref() != defs.trigger_event
                    || task.trigger_count.unwrap_or(1) != defs.trigger_count.unwrap_or(1)
                    || task.schedule.is_some()
                    || task.scheduled_time.is_some()
                    || task.scheduled_date.is_some()
                    || task.cron_expression.is_some());
            if needs_event_norm {
                db.execute(
                    "UPDATE scheduled_tasks SET trigger_type='event', trigger_event=?1, \
                     trigger_count=?2, trigger_counter=0, schedule=?3, scheduled_time=?4, \
                     scheduled_day=NULL, scheduled_date=NULL, cron_expression=?5 WHERE id=?6",
                    rusqlite::params![
                        defs.trigger_event,
                        defs.trigger_count.unwrap_or(1),
                        defs.schedule,
                        defs.scheduled_time,
                        defs.cron_expression,
                        task.id
                    ],
                )?;
            }
            // ship_paused status normalization (only when prefs untouched).
            if !tasks_enabled && !tasks_opened {
                if defs.ship_paused && task.status.as_deref() == Some("active") {
                    db.execute(
                        "UPDATE scheduled_tasks SET status='paused' WHERE id=?1",
                        [&task.id],
                    )?;
                } else if !defs.ship_paused && task.status.as_deref() == Some("paused") {
                    let next_run = if task.trigger_type.as_deref().unwrap_or("schedule") == "schedule" {
                        compute_next_run(
                            task.schedule.as_deref(),
                            task.scheduled_time.as_deref(),
                            task.scheduled_day,
                            task.scheduled_date,
                            Some(utcnow()),
                            task.cron_expression.as_deref(),
                            resolve_task_timezone(db, task).as_deref(),
                        )
                    } else {
                        None
                    };
                    db.execute(
                        "UPDATE scheduled_tasks SET status='active', next_run=?1 WHERE id=?2",
                        rusqlite::params![next_run.map(fmt_dt), task.id],
                    )?;
                }
            }
            // Built-in jobs: no browser notifications.
            db.execute(
                "UPDATE scheduled_tasks SET notifications_enabled=0 WHERE id=?1",
                [&task.id],
            )?;
        }

        // Seed missing actions.
        for (action, defs) in HOUSEKEEPING_DEFAULTS.iter() {
            if existing_actions.contains(*action) {
                continue;
            }
            let next_run = if defs.trigger_type == "schedule" {
                compute_next_run(
                    defs.schedule,
                    defs.scheduled_time,
                    None,
                    None,
                    Some(utcnow()),
                    defs.cron_expression,
                    None,
                )
            } else {
                None
            };
            let status = if defs.ship_paused { "paused" } else { "active" };
            let id: String = uuid::Uuid::new_v4().simple().to_string()[..8].to_string();
            let now = pydatetime::utcnow_naive_iso();
            db.execute(
                "INSERT INTO scheduled_tasks \
                 (id, owner, name, task_type, action, trigger_type, trigger_event, trigger_count, \
                  trigger_counter, schedule, scheduled_time, cron_expression, next_run, status, \
                  output_target, notifications_enabled, created_at, updated_at) \
                 VALUES (?1, ?2, ?3, 'action', ?4, ?5, ?6, ?7, 0, ?8, ?9, ?10, ?11, ?12, \
                         'session', 0, ?13, ?13)",
                rusqlite::params![
                    id,
                    owner,
                    defs.name,
                    action,
                    defs.trigger_type,
                    defs.trigger_event,
                    defs.trigger_count,
                    defs.schedule,
                    defs.scheduled_time,
                    defs.cron_expression,
                    next_run.map(fmt_dt),
                    status,
                    now
                ],
            )?;
        }
        Ok(())
    }

    /// `ensure_assistant_defaults(self, owner)`.
    pub async fn ensure_assistant_defaults(&self, owner: &str) -> PyResult<()> {
        // Hard-reject synthetic owners.
        if owner.is_empty()
            || matches!(owner, "internal-tool" | "api" | "demo" | "system")
        {
            logger::info(&format!(
                "ensure_assistant_defaults: skip synthetic owner {owner:?}"
            ));
            return Ok(());
        }
        let db = session_local().map_err(|e| PyError::other(e.to_string()))?;

        // Already seeded?
        let existing: Option<String> = db
            .query_row(
                "SELECT id FROM crew_members WHERE owner = ?1 AND is_default_assistant = 1 LIMIT 1",
                [owner],
                |r| r.get(0),
            )
            .optional()
            .ok()
            .flatten();
        if existing.is_some() {
            return Ok(());
        }

        let (endpoint_url, model) = self.resolve_defaults(&db, Some(owner));

        let session_id = uuid::Uuid::new_v4().to_string();
        let now = pydatetime::utcnow_naive_iso();
        let res = (|| -> rusqlite::Result<()> {
            db.execute(
                "INSERT INTO sessions \
                 (id, name, endpoint_url, model, owner, is_important, mode, folder, created_at, updated_at) \
                 VALUES (?1, 'Assistant', ?2, ?3, ?4, 1, 'agent', 'Assistant', ?5, ?5)",
                rusqlite::params![
                    session_id,
                    endpoint_url.clone().unwrap_or_default(),
                    model.clone().unwrap_or_default(),
                    owner,
                    now
                ],
            )?;
            let crew_id = uuid::Uuid::new_v4().to_string();
            let enabled_tools = serde_json::to_string(&json!([
                "manage_calendar", "manage_notes", "manage_tasks", "manage_memory",
                "list_email_accounts", "list_emails", "read_email", "send_email", "reply_to_email", "archive_email",
                "mark_email_read", "delete_email", "resolve_contact",
                "search_chats", "web_search", "read_file",
                "create_document", "update_document", "edit_document",
                "generate_image", "trigger_research",
                "download_model", "serve_model", "list_served_models", "stop_served_model",
                "edit_image"
            ]))
            .unwrap_or_else(|_| "[]".to_string());
            db.execute(
                "INSERT INTO crew_members \
                 (id, owner, name, personality, model, endpoint_url, enabled_tools, session_id, \
                  is_active, sort_order, is_default_assistant, created_at, updated_at) \
                 VALUES (?1, ?2, 'Assistant', ?3, ?4, ?5, ?6, ?7, 1, 0, 1, ?8, ?8)",
                rusqlite::params![
                    crew_id,
                    owner,
                    DEFAULT_ASSISTANT_PERSONALITY,
                    model,
                    endpoint_url,
                    enabled_tools,
                    session_id,
                    now
                ],
            )?;
            // Link session back to crew.
            db.execute(
                "UPDATE sessions SET crew_member_id = ?1 WHERE id = ?2",
                rusqlite::params![crew_id, session_id],
            )?;
            logger::info(&format!(
                "Seeded personal assistant (crew {crew_id}) for owner={owner}"
            ));
            Ok(())
        })();
        if let Err(e) = res {
            logger::warning(&format!("ensure_assistant_defaults({owner}) failed: {e}"));
        }
        Ok(())
    }
}

impl Default for TaskScheduler {
    fn default() -> Self {
        Self::new()
    }
}

/// The personal-assistant default personality (verbatim from the Python).
const DEFAULT_ASSISTANT_PERSONALITY: &str = "You are the user's personal assistant. Concise, warm, a little dry. Never waste time with fluff. Default to English. Only match the other language when replying to a non-English email.\n\nCORE RULE: You MUST use your tools to take action — do not describe what you would do. Never say 'I would check your calendar' — actually call manage_calendar. Never say 'I can look that up' — actually call web_search or search_chats. If you have a tool for it, use it. No hypotheticals, no promises, only actions and results.\n\nDECISION FRAMEWORK — follow these rules, not just tool descriptions:\n\nCONTEXT GATHERING (before any response involving a specific person):\n1. resolve_contact if you only have a name and need their email\n2. search_chats for recent conversations mentioning them or their topic\n3. manage_memory to check stored facts about them\nSkip steps you already have answers for. Don't search for the user themselves.\n\nEMAIL HANDLING:\n- If a document is open in the editor, that IS the email. Use update_document to write the reply.\n- BEFORE drafting any reply: gather context (steps above) about the sender and topic.\n- When an email mentions a date/meeting: check calendar for conflicts, add if clear.\n- When an email asks a question you can't answer from context: say so honestly. Never fabricate.\n- Skip automated/marketing emails in check-ins. Only surface human-sent, actionable ones.\n- Never duplicate information the user already saw in a previous check-in.\n\nESCALATION LADDER (when you need info you don't have):\n1. search_chats (fast, free)\n2. manage_memory (fast, free)\n3. web_search (medium cost)\n4. trigger_research (expensive, async — only for complex multi-source questions)\nStop as soon as you have a sufficient answer.\n\n'SEND TO [NAME]' FLOW:\n1. resolve_contact to find their email\n2. If a document is open, use its content as the body\n3. Draft the email in a document (create_document with language='email')\n4. Tell the user to review — NEVER auto-send\n\nSELF-IMPROVEMENT — use manage_memory constantly:\n- When the user corrects you, IMMEDIATELY store the correction as a memory.\n- After every check-in or task, store new facts you learned (contacts, preferences, patterns).\n- Before responding about a person or topic, search_chats and manage_memory FIRST.\n- Build knowledge over time: who people are, what projects are active, how the user likes things done.\n- If something failed or you got corrected, store WHY so you never repeat it.\n- When you figure out a multi-step workflow that works, save it as a SKILL using manage_skills.\n  A skill is a reusable procedure. Next time, recall the skill instead of figuring it out again.\n- Before starting a complex task, check manage_skills for an existing procedure.\n\nAUTONOMY RULES:\n- Auto-add calendar events from clear meeting invitations (mention what you added)\n- Auto-draft email replies (cached for when user clicks Reply)\n- NEVER send emails without explicit user instruction\n- NEVER delete anything without explicit instruction\n- If uncertain, ask rather than guess";

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::NaiveDate;

    fn dt(y: i32, mo: u32, d: u32, h: u32, mi: u32) -> NaiveDateTime {
        NaiveDate::from_ymd_opt(y, mo, d)
            .unwrap()
            .and_hms_opt(h, mi, 0)
            .unwrap()
    }

    #[test]
    fn daily_next_run_same_day_and_rollover() {
        let after = dt(2024, 6, 1, 8, 0);
        // 09:00 daily, after 08:00 -> same day 09:00.
        let n = compute_next_run(Some("daily"), Some("09:00"), None, None, Some(after), None, None);
        assert_eq!(n, Some(dt(2024, 6, 1, 9, 0)));
        // 07:00 daily, after 08:00 -> next day 07:00.
        let n = compute_next_run(Some("daily"), Some("07:00"), None, None, Some(after), None, None);
        assert_eq!(n, Some(dt(2024, 6, 2, 7, 0)));
        // 08:00 daily, exactly equal -> next day (candidate <= now).
        let n = compute_next_run(Some("daily"), Some("08:00"), None, None, Some(after), None, None);
        assert_eq!(n, Some(dt(2024, 6, 2, 8, 0)));
    }

    #[test]
    fn weekly_next_run_monday_zero() {
        // 2024-06-01 is a Saturday (weekday 5, Mon=0). Target Monday (0).
        let after = dt(2024, 6, 1, 8, 0);
        let n = compute_next_run(Some("weekly"), Some("09:00"), Some(0), None, Some(after), None, None);
        // Next Monday is 2024-06-03.
        assert_eq!(n, Some(dt(2024, 6, 3, 9, 0)));
        // Target Saturday (5) same day after time -> +7 (not same minute).
        let n = compute_next_run(Some("weekly"), Some("07:00"), Some(5), None, Some(after), None, None);
        assert_eq!(n, Some(dt(2024, 6, 8, 7, 0)));
        // Target Saturday (5) later today -> today.
        let n = compute_next_run(Some("weekly"), Some("10:00"), Some(5), None, Some(after), None, None);
        assert_eq!(n, Some(dt(2024, 6, 1, 10, 0)));
    }

    #[test]
    fn monthly_day_overflow_clamps_to_last_day() {
        // After Jan 31: monthly on day 31 -> Feb has no 31, clamps to last day.
        let after = dt(2024, 1, 31, 12, 0);
        let n = compute_next_run(Some("monthly"), Some("09:00"), Some(31), None, Some(after), None, None);
        // Jan 31 09:00 <= now (12:00), so go to next month; Feb 2024 has 29 days.
        assert_eq!(n, Some(dt(2024, 2, 29, 9, 0)));
    }

    #[test]
    fn monthly_same_month_future() {
        let after = dt(2024, 6, 1, 8, 0);
        let n = compute_next_run(Some("monthly"), Some("09:00"), Some(15), None, Some(after), None, None);
        assert_eq!(n, Some(dt(2024, 6, 15, 9, 0)));
    }

    #[test]
    fn cron_every_two_hours() {
        let after = dt(2024, 6, 1, 8, 30);
        // "0 */2 * * *" -> next even hour at minute 0 strictly after 08:30 = 10:00.
        let n = compute_next_run(Some("cron"), None, None, None, Some(after), Some("0 */2 * * *"), None);
        assert_eq!(n, Some(dt(2024, 6, 1, 10, 0)));
    }

    #[test]
    fn cron_hourly() {
        let after = dt(2024, 6, 1, 8, 30);
        let n = compute_next_run(Some("cron"), None, None, None, Some(after), Some("0 * * * *"), None);
        assert_eq!(n, Some(dt(2024, 6, 1, 9, 0)));
    }

    #[test]
    fn cron_dow_remap_monday() {
        // croniter "0 6 * * 1" = Monday 06:00. After Sat 2024-06-01 08:00,
        // next Monday is 2024-06-03.
        let after = dt(2024, 6, 1, 8, 0);
        let n = compute_next_run(Some("cron"), None, None, None, Some(after), Some("0 6 * * 1"), None);
        assert_eq!(n, Some(dt(2024, 6, 3, 6, 0)));
    }

    #[test]
    fn once_future_and_past() {
        let after = dt(2024, 6, 1, 8, 0);
        let future = dt(2024, 6, 2, 8, 0);
        let n = compute_next_run(Some("once"), None, None, Some(future), Some(after), None, None);
        assert_eq!(n, Some(future));
        let past = dt(2024, 5, 1, 8, 0);
        let n = compute_next_run(Some("once"), None, None, Some(past), Some(after), None, None);
        assert_eq!(n, None);
    }

    #[test]
    fn daily_with_timezone_converts_to_utc() {
        // 09:00 local in America/New_York (UTC-4 in June DST) -> 13:00 UTC.
        let after = dt(2024, 6, 1, 8, 0); // 08:00 UTC = 04:00 NY
        let n = compute_next_run(
            Some("daily"),
            Some("09:00"),
            None,
            None,
            Some(after),
            None,
            Some("America/New_York"),
        );
        assert_eq!(n, Some(dt(2024, 6, 1, 13, 0)));
    }

    #[test]
    fn is_email_target_matches() {
        assert!(is_email_output_target("email"));
        assert!(is_email_output_target("email:self"));
        assert!(is_email_output_target("email:foo@bar.com"));
        assert!(is_email_output_target("foo@bar.com"));
        assert!(!is_email_output_target("session"));
        assert!(!is_email_output_target("mcp__gmail__send"));
        assert!(!is_email_output_target("notification"));
    }

    #[test]
    fn format_email_output_basic() {
        let raw = "📬 [INBOX] 5 emails\n[1] Re: Hello From: Alice | 2024-06-01\n[2] Newsletter\n";
        let out = format_email_output(raw);
        assert!(out.contains("- Alice — Re: Hello"));
        assert!(out.contains("- Newsletter"));
        // empty input -> "No unread emails"
        assert_eq!(format_email_output(""), "No unread emails");
    }

    #[test]
    fn dow_field_remap() {
        assert_eq!(remap_dow_field("*"), "*");
        assert_eq!(remap_dow_field("0"), "1"); // Sun
        assert_eq!(remap_dow_field("1"), "2"); // Mon
        assert_eq!(remap_dow_field("6"), "7"); // Sat
        assert_eq!(remap_dow_field("7"), "1"); // Sun (alt)
        assert_eq!(remap_dow_field("1,3"), "2,4");
        assert_eq!(remap_dow_field("1-5"), "2-6");
        assert_eq!(remap_dow_field("1-5/2"), "2-6/2");
    }

    // ── stop_task / mark_run_aborted (DB-backed) ────────────────────────────
    //
    // Uses a unique temp DB per test so these tests are safe to run in parallel
    // with the rest of the suite. The process-global DB_TEST_LOCK serializes
    // against any other test that writes DATABASE_URL.

    use crate::core::database::DB_TEST_LOCK;

    struct TmpDb(std::path::PathBuf);
    impl Drop for TmpDb {
        fn drop(&mut self) {
            let _ = std::fs::remove_file(&self.0);
        }
    }

    fn fresh_temp_db_ts(tag: &str) -> TmpDb {
        let path = std::env::temp_dir().join(format!(
            "odysseus_task_scheduler_{tag}_{}_{}.db",
            std::process::id(),
            uuid::Uuid::new_v4().simple()
        ));
        let _ = std::fs::remove_file(&path);
        std::env::set_var("DATABASE_URL", format!("sqlite:///{}", path.display()));
        crate::core::database::create_all().unwrap();
        TmpDb(path)
    }

    /// `mark_run_aborted` flips a `running` run to `aborted` and returns `true`.
    #[test]
    fn mark_run_aborted_running_row() {
        let _g = DB_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let _db = fresh_temp_db_ts("mark_abort_running");

        let task_id = "task-mra-1";
        let run_id = "run-mra-1";
        let conn = crate::core::database::session_local().unwrap();
        let now = pydatetime::utcnow_naive_iso();
        // Seed the parent scheduled_tasks row first. PRAGMA foreign_keys=ON is
        // now enforced, so the task_runs FK (task_id -> scheduled_tasks.id)
        // requires the parent to exist. `created_at`/`updated_at` are
        // NOT NULL with no default (TimestampMixin), so they must be supplied.
        conn.execute(
            "INSERT INTO scheduled_tasks (id, name, status, trigger_type, created_at, updated_at) \
             VALUES (?1, 'test', 'active', 'schedule', ?2, ?2)",
            rusqlite::params![task_id, now],
        ).unwrap();
        conn.execute(
            "INSERT INTO task_runs (id, task_id, started_at, status) VALUES (?1, ?2, ?3, 'running')",
            rusqlite::params![run_id, task_id, now],
        ).unwrap();
        drop(conn);

        let sched = TaskScheduler::new();
        let aborted = sched.mark_run_aborted(task_id, None, "Stopped by user");
        assert!(aborted, "expected mark_run_aborted to return true for a running row");

        let conn2 = crate::core::database::session_local().unwrap();
        let status: String = conn2
            .query_row(
                "SELECT status FROM task_runs WHERE id = ?1",
                [run_id],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(status, "aborted");
    }

    /// `mark_run_aborted` returns `false` when no running/queued run exists.
    #[test]
    fn mark_run_aborted_no_active_row() {
        let _g = DB_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let _db = fresh_temp_db_ts("mark_abort_noop");

        let sched = TaskScheduler::new();
        let result = sched.mark_run_aborted("task-nonexistent", None, "Stopped by user");
        assert!(!result, "expected false when no running/queued run exists");
    }

    /// `stop_task` removes the task from the executing set and marks the DB run
    /// as aborted, returning `true` if it found live work.
    #[tokio::test]
    // DB_TEST_LOCK must span the awaited stop_task (which does DB work on the
    // unique per-test DATABASE_URL); dropping it before the await would let a
    // sibling DB test swap the env var mid-run.
    #[allow(clippy::await_holding_lock)]
    async fn stop_task_removes_executing_and_aborts_run() {
        let _g = DB_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let _db = fresh_temp_db_ts("stop_task_basic");

        let task_id = "task-stop-1";
        let run_id = "run-stop-1";
        let conn = crate::core::database::session_local().unwrap();
        let now = pydatetime::utcnow_naive_iso();
        // Seed the parent scheduled_tasks row first (FK target for task_runs,
        // enforced under PRAGMA foreign_keys=ON). `created_at`/`updated_at` are
        // NOT NULL with no default, so they must be supplied.
        conn.execute(
            "INSERT INTO scheduled_tasks (id, name, status, trigger_type, created_at, updated_at) \
             VALUES (?1, 'stop-test', 'active', 'schedule', ?2, ?2)",
            rusqlite::params![task_id, now],
        ).unwrap();
        conn.execute(
            "INSERT INTO task_runs (id, task_id, started_at, status) VALUES (?1, ?2, ?3, 'queued')",
            rusqlite::params![run_id, task_id, now],
        ).unwrap();
        drop(conn);

        let sched = std::sync::Arc::new(TaskScheduler::new());
        // Simulate the task being in the executing set (as _check_due_tasks would).
        sched.executing.lock().await.insert(task_id.to_string());

        let stopped = sched.stop_task(task_id).await;
        assert!(stopped, "stop_task should return true when work was found");

        // The task_id must have been removed from executing.
        assert!(
            !sched.executing.lock().await.contains(task_id),
            "executing set should not contain the task after stop_task"
        );

        // The DB run row should now be 'aborted'.
        let conn2 = crate::core::database::session_local().unwrap();
        let status: String = conn2
            .query_row(
                "SELECT status FROM task_runs WHERE id = ?1",
                [run_id],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(status, "aborted");
    }

    /// `stop_task` returns `false` when the task is not executing and has no
    /// active run rows — mirrors the Python `handle=None, not in _executing,
    /// _mark_run_aborted=False -> return False`.
    #[tokio::test]
    #[allow(clippy::await_holding_lock)] // DB_TEST_LOCK must span the awaited stop_task
    async fn stop_task_idle_task_returns_false() {
        let _g = DB_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let _db = fresh_temp_db_ts("stop_task_idle");

        let sched = std::sync::Arc::new(TaskScheduler::new());
        let stopped = sched.stop_task("task-not-running").await;
        assert!(!stopped, "stop_task on an idle task should return false");
    }

    // ── ensure_defaults_db: retired-action task_runs purge + orphan sweep ───

    /// `ensure_defaults_db` deletes `task_runs` rows belonging to a retired action
    /// (e.g. `mark_email_boundaries`) before deleting the task itself, and then
    /// sweeps any orphan `task_runs` whose parent task no longer exists.
    ///
    /// Mirrors the behaviour added in commit 0892466: retired_ids -> delete runs
    /// -> delete tasks -> orphan sweep (only when live tasks exist).
    #[test]
    fn ensure_defaults_db_purges_retired_task_runs_and_orphans() {
        let _g = DB_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let _db = fresh_temp_db_ts("retired_purge");

        let owner = "test-owner-retired";
        let conn = crate::core::database::session_local().unwrap();
        let now = pydatetime::utcnow_naive_iso();

        // Insert a retired-action task (mark_email_boundaries) and a run for it.
        let retired_task_id = "task-retired-meb";
        let retired_run_id = "run-retired-meb";
        conn.execute(
            "INSERT INTO scheduled_tasks \
             (id, owner, name, task_type, action, status, trigger_type, created_at, updated_at) \
             VALUES (?1, ?2, 'Email Mark Boundaries', 'action', 'mark_email_boundaries', \
                     'active', 'schedule', ?3, ?3)",
            rusqlite::params![retired_task_id, owner, now],
        ).unwrap();
        conn.execute(
            "INSERT INTO task_runs (id, task_id, started_at, status) VALUES (?1, ?2, ?3, 'success')",
            rusqlite::params![retired_run_id, retired_task_id, now],
        ).unwrap();

        // Insert an extra orphan task_run whose parent task doesn't exist (simulates
        // a previously-retired task that left a dangling run).
        let ghost_run_id = "run-ghost-123";
        // Temporarily disable FK enforcement so we can insert a fatherless run.
        conn.execute_batch("PRAGMA foreign_keys=OFF").unwrap();
        conn.execute(
            "INSERT INTO task_runs (id, task_id, started_at, status) VALUES (?1, 'task-deleted-ghost', ?2, 'success')",
            rusqlite::params![ghost_run_id, now],
        ).unwrap();
        conn.execute_batch("PRAGMA foreign_keys=ON").unwrap();

        // Insert one live task so the orphan sweep is triggered (it only runs when
        // live_ids is non-empty).
        let live_task_id = "task-live-001";
        conn.execute(
            "INSERT INTO scheduled_tasks \
             (id, owner, name, task_type, action, status, trigger_type, created_at, updated_at) \
             VALUES (?1, ?2, 'Check-in', 'action', 'tidy_sessions', 'active', 'schedule', ?3, ?3)",
            rusqlite::params![live_task_id, owner, now],
        ).unwrap();
        drop(conn);

        // Run ensure_defaults_db.
        let sched = TaskScheduler::new();
        let db = crate::core::database::session_local().unwrap();
        sched
            .ensure_defaults_db(&db, owner, false, false)
            .expect("ensure_defaults_db should not fail");
        drop(db);

        let conn2 = crate::core::database::session_local().unwrap();

        // The retired scheduled_task row must be gone.
        let task_count: i64 = conn2
            .query_row(
                "SELECT COUNT(*) FROM scheduled_tasks WHERE id = ?1",
                [retired_task_id],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(task_count, 0, "retired task row should have been deleted");

        // Its task_run must also be gone (purged before the task delete).
        let run_count: i64 = conn2
            .query_row(
                "SELECT COUNT(*) FROM task_runs WHERE id = ?1",
                [retired_run_id],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(run_count, 0, "task_run for retired task should have been deleted");

        // The orphan run (parent task-deleted-ghost never existed) must be swept.
        let ghost_count: i64 = conn2
            .query_row(
                "SELECT COUNT(*) FROM task_runs WHERE id = ?1",
                [ghost_run_id],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(ghost_count, 0, "orphan task_run should have been swept by ensure_defaults_db");
    }

    /// `RETIRED_HOUSEKEEPING_ACTIONS` now includes `mark_email_boundaries`.
    #[test]
    fn mark_email_boundaries_is_retired() {
        assert!(
            RETIRED_HOUSEKEEPING_ACTIONS.contains("mark_email_boundaries"),
            "mark_email_boundaries must be in RETIRED_HOUSEKEEPING_ACTIONS after commit 0892466"
        );
    }

    /// `mark_email_boundaries` must no longer be in `HOUSEKEEPING_DEFAULTS`.
    #[test]
    fn mark_email_boundaries_not_in_defaults() {
        assert!(
            !HOUSEKEEPING_DEFAULTS.contains_key("mark_email_boundaries"),
            "mark_email_boundaries must be removed from HOUSEKEEPING_DEFAULTS after commit 0892466"
        );
    }

    /// `mark_email_boundaries` must no longer appear in `SILENT_ACTIONS` or
    /// `MODEL_BACKED_ACTIONS` (both are private statics; verify via absence from the
    /// sets that gate task-execution behavior).
    #[test]
    fn mark_email_boundaries_not_in_silent_or_model_backed() {
        assert!(
            !SILENT_ACTIONS.contains("mark_email_boundaries"),
            "mark_email_boundaries must be removed from SILENT_ACTIONS"
        );
        assert!(
            !MODEL_BACKED_ACTIONS.contains("mark_email_boundaries"),
            "mark_email_boundaries must be removed from MODEL_BACKED_ACTIONS"
        );
    }
}
