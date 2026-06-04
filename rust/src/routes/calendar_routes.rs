// routes/calendar_routes.rs  <- routes/calendar_routes.py
//! Calendar routes — local SQLite-backed calendar CRUD.
//!
//! Wave-7 port (app.py include #527). The full local-calendar surface:
//! CalDAV config get/save/test/sync, calendar + event CRUD, ICS import/export,
//! and the LLM-backed `quick-parse` natural-language event parser.
//!
//! ## Auth — module-private `_require_user`, NOT `auth_adapter::require_user`
//! `calendar_routes.py` ships its OWN `_require_user`, distinct from
//! `src.auth_helpers.require_user`: it resolves `get_current_user(request)` and,
//! when that is `None`, returns the `FALLBACK_OWNER`
//! (`ODYSSEUS_FALLBACK_OWNER`, default `owner@localhost`) in single-user mode
//! (`ODYSSEUS_SINGLE_USER != "0"`), else raises `HTTPException(401)`. Reproduced
//! faithfully in [`require_user`]. The `FALLBACK_OWNER` constant matches the one
//! already ported in `src/tool_implementations/management_db.rs`.
//!
//! ## Date / tz handling (PORT_NOW, no defer)
//! `_parse_dt` (full natural-language parser), `_parse_dt_pair` (tz-detecting),
//! `parse_due_for_user` (user-tz natural language), and the `set/get_user_tz_offset`
//! `ContextVar` stash are all ported. `datetime.now()` -> `chrono::Local::now()`
//! (naive local); tz-aware ISO -> naive UTC via `DateTime::parse_from_rfc3339`;
//! user-offset tz via `chrono::FixedOffset`. The SQLite DATETIME store format
//! (`%Y-%m-%d %H:%M:%S%.6f`) and `.isoformat()` read-back are reproduced via
//! [`crate::pydatetime`] (microseconds emitted only when non-zero, `T` separator).
//!
//! The "Last resort: dateutil" tail of `_parse_dt`/`parse_due_for_user`
//! (`from dateutil import parser as _du; _du.parse(s)`) is now a REAL dateutil
//! port via the [`dtparse`] crate (the faithful Rust port of python-dateutil's
//! parser: `parse(&str) -> Result<(NaiveDateTime, Option<FixedOffset>), _>`) —
//! no longer the prior 4-format `%Y-%m-%d %H:%M` stub. `_parse_dt`'s DB-naive
//! callers (`parse_dt`/`parse_dt_pair`) take only the `NaiveDateTime`, since
//! Python only strips tz on the strict-ISO fast path (py:275-277); the
//! `.isoformat()` callers (`parse_due_for_user`, py:161/221, via
//! `parse_dt_isoformat`) RETAIN the dateutil offset because `_du.parse` keeps
//! tzinfo on the last-resort branch. `parse_due_for_user` otherwise honors the
//! returned offset when present, else attaches the user's tz (py:212-218).
//! `parse_dt`/`parse_dt_pair` are `pub(crate)` so `management_db.rs` reuses them
//! instead of duplicating the phrase parser.
//!
//! ## ICS import (PORT_NOW)
//! Uses the `icalendar` crate the same way `crate::src::caldav_sync` does
//! (`parser::unfold` + `parser::read_calendar` + `Calendar::try_from` + VEVENT
//! walk). The dedup + per-user fresh-uuid + UTC-normalization logic is mirrored
//! byte-for-byte from the Python.
//!
//! ## `/test` CalDAV probe (PORT_NOW)
//! `httpx.AsyncClient` PROPFIND -> an async `reqwest::Client` with a custom
//! `PROPFIND` method, basic auth, `follow_redirects=True`, 8s timeout. The
//! status-code -> message mapping and the connect/timeout/other error classes
//! mirror the `httpx.ConnectError` / `httpx.TimeoutException` / generic branches.
//!
//! ## `quick-parse` (PORT_NOW)
//! `resolve_endpoint("utility")` -> `resolve_endpoint("default")` via the
//! centralized `crate::src::endpoint_resolver::resolve_endpoint_triple(prefix,
//! owner)` (owner-less here — Python `quick_parse` passes no owner -> `None`),
//! `llm_call_async`, `strip_think`, and the summary-cleanup / tz-stripping regex
//! chain. (The former module-private `resolve_endpoint` copy that read
//! `model_endpoints::endpoint_auth` WITHOUT an `is_enabled` filter is gone; the
//! centralized row read applies `is_enabled = 1`, matching Python.)
//!
//! HONEST DEFERS: none. Every endpoint is ported against landed deps.


use std::sync::Mutex;
use std::time::Duration;

use axum::extract::{Multipart, Path, Query};
use axum::http::{header, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post, put};
use axum::{Extension, Json, Router};
use chrono::{Datelike, Duration as ChronoDuration, FixedOffset, NaiveDate, NaiveDateTime, TimeZone, Timelike, Utc};
use once_cell::sync::Lazy;
use rusqlite::OptionalExtension;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::pydatetime;
use crate::routes::{AppState, CurrentUser, HttpException};

// ===========================================================================
// Single-user fallback identity (module constants)
// ===========================================================================

/// `FALLBACK_OWNER = os.environ.get("ODYSSEUS_FALLBACK_OWNER", "owner@localhost")`.
fn fallback_owner() -> String {
    crate::pyos::getenv("ODYSSEUS_FALLBACK_OWNER", "owner@localhost")
}

/// `_SINGLE_USER_MODE = os.environ.get("ODYSSEUS_SINGLE_USER", "1") != "0"`.
fn single_user_mode() -> bool {
    crate::pyos::getenv("ODYSSEUS_SINGLE_USER", "1") != "0"
}

/// SQLite DATETIME store-string format (`crate::pydatetime` FMT, private there).
const SQLITE_DT_FMT: &str = "%Y-%m-%d %H:%M:%S%.6f";

/// 10 MB hard cap on ICS upload (`_ICS_MAX_BYTES = 10 * 1024 * 1024`).
const ICS_MAX_BYTES: usize = 10 * 1024 * 1024;

// ===========================================================================
// `_require_user(request)` — module-private auth (FALLBACK_OWNER aware)
// ===========================================================================

/// `_require_user(request) -> str`. Resolves the stamped username; in single-user
/// mode an unauthenticated request falls through to `FALLBACK_OWNER`, otherwise
/// raises `HTTPException(401, "Authentication required")`.
fn require_user(user: Option<&CurrentUser>) -> Result<String, HttpException> {
    // u = get_current_user(request)  — the stamped current_user, or None.
    if let Some(u) = user {
        if !u.0.is_empty() {
            return Ok(u.0.clone());
        }
    }
    if single_user_mode() {
        return Ok(fallback_owner());
    }
    Err(HttpException::new(401, "Authentication required"))
}

// ===========================================================================
// Per-request user UTC offset (ContextVar `_USER_TZ_OFFSET_MIN`)
// ===========================================================================

/// `_USER_TZ_OFFSET_MIN: ContextVar(default=None)`.
///
/// The Python uses a `contextvars.ContextVar` so each async task sees its own
/// value. The axum runtime is multi-threaded and the routes don't thread a
/// per-request context object through these module-level setters/getters, so the
/// closest faithful stand-in is a process-global `Mutex<Option<i32>>` (the value
/// is only ever set by `chat_routes`' fire-and-forget header stash, which is
/// itself noted as unported there — see that module). `None` => unknown, fall
/// back to legacy server-local behavior.
static USER_TZ_OFFSET_MIN: Lazy<Mutex<Option<i32>>> = Lazy::new(|| Mutex::new(None));

/// `set_user_tz_offset(offset_min)` — `int(offset_min)` or no-op on TypeError/ValueError.
pub fn set_user_tz_offset(offset_min: &str) {
    // try: v = int(offset_min) except (TypeError, ValueError): return
    if let Ok(v) = offset_min.trim().parse::<i32>() {
        if let Ok(mut g) = USER_TZ_OFFSET_MIN.lock() {
            *g = Some(v);
        }
    }
}

/// `get_user_tz_offset()` — the current user's UTC offset (minutes east of UTC), or `None`.
pub fn get_user_tz_offset() -> Option<i32> {
    USER_TZ_OFFSET_MIN.lock().ok().and_then(|g| *g)
}

// ===========================================================================
// `_parse_dt` — strict ISO + natural-language parser
// ===========================================================================

static AMPM_RE: Lazy<regex::Regex> =
    Lazy::new(|| regex::Regex::new(r"(?i)^\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*$").unwrap());
static REL_DAY_RE: Lazy<regex::Regex> =
    Lazy::new(|| regex::Regex::new(r"(?i)^(today|tomorrow|tmrw|yesterday)(?:\s+at)?\s*(.*)$").unwrap());
static REL_DAY_TONIGHT_RE: Lazy<regex::Regex> = Lazy::new(|| {
    regex::Regex::new(r"(?i)^(today|tonight|tomorrow|tmrw|yesterday)(?:\s+at)?\s*(.*)$").unwrap()
});
static NEXT_WEEKDAY_RE: Lazy<regex::Regex> =
    Lazy::new(|| regex::Regex::new(r"(?i)^next\s+(\w+)(?:\s+at)?\s*(.*)$").unwrap());
static IN_N_RE: Lazy<regex::Regex> =
    Lazy::new(|| regex::Regex::new(r"(?i)^in\s+(\d+)\s*(hour|hr|minute|min|day)s?\s*$").unwrap());
static STRIP_TZ_OFFSET_RE: Lazy<regex::Regex> =
    Lazy::new(|| regex::Regex::new(r"[+-]\d{2}:?\d{2}$").unwrap());

const WEEKDAYS: [&str; 7] = [
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
];

/// `_parse_time(t)` — return `(hour, minute)` from "1pm", "1:30 PM", "13:00", or `None`.
fn parse_time(t: &str) -> Option<(u32, u32)> {
    let caps = AMPM_RE.captures(t)?;
    let mut h: u32 = caps.get(1)?.as_str().parse().ok()?;
    let mn: u32 = caps
        .get(2)
        .map(|m| m.as_str().parse().unwrap_or(0))
        .unwrap_or(0);
    let ampm = caps.get(3).map(|m| m.as_str().to_lowercase()).unwrap_or_default();
    if ampm == "pm" && h < 12 {
        h += 12;
    } else if ampm == "am" && h == 12 {
        h = 0;
    }
    if h < 24 && mn < 60 {
        Some((h, mn))
    } else {
        None
    }
}

/// Try the strict-ISO fast path of `_parse_dt` (and `_parse_dt_pair`). Returns
/// `Some((naive_dt, had_tz))` on success. `had_tz` is true iff the input carried
/// explicit timezone info; the returned datetime is then naive UTC.
fn parse_iso_fast(s: &str) -> Option<(NaiveDateTime, bool)> {
    // len == 10 -> date-only (naive midnight, no tz).
    if s.chars().count() == 10 {
        if let Ok(d) = NaiveDate::parse_from_str(s, "%Y-%m-%d") {
            return Some((d.and_hms_opt(0, 0, 0).unwrap(), false));
        }
        return None;
    }
    // tz-aware: ends with Z (-> +00:00) or has an explicit offset.
    let normalized = if let Some(stripped) = s.strip_suffix('Z') {
        format!("{stripped}+00:00")
    } else {
        s.to_string()
    };
    if let Ok(parsed) = chrono::DateTime::parse_from_rfc3339(&normalized) {
        return Some((parsed.with_timezone(&Utc).naive_utc(), true));
    }
    // naive ISO datetime (no tz).
    for fmt in [
        "%Y-%m-%dT%H:%M:%S%.f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
    ] {
        if let Ok(naive) = NaiveDateTime::parse_from_str(s, fmt) {
            return Some((naive, false));
        }
    }
    None
}

/// `_parse_dt(s)` — strict ISO first, then natural language. Returns the naive
/// (local-ish) datetime, or `Err` ("could not parse datetime").
///
/// `pub(crate)` so `management_db.rs` can reuse it (its `parse_event_dt`/notes
/// due-date paths delegate here instead of carrying a duplicate phrase parser).
pub(crate) fn parse_dt(s: &str) -> Result<NaiveDateTime, String> {
    let s = s.trim();
    if s.is_empty() {
        return Err("empty datetime string".to_string());
    }
    // Fast path: strict ISO (tz stripped to naive UTC for legacy callers).
    if let Some((dt, _had_tz)) = parse_iso_fast(s) {
        return Ok(dt);
    }
    natural_language_dt(s)
}

/// The natural-language tail of `_parse_dt`, anchored on `datetime.now()`
/// (`chrono::Local::now()` naive-local).
fn natural_language_dt(s: &str) -> Result<NaiveDateTime, String> {
    let now = chrono::Local::now().naive_local();
    let today = now
        .date()
        .and_hms_opt(0, 0, 0)
        .unwrap();
    let lower = s.to_lowercase();
    let lower = lower.trim();

    // today/tomorrow/yesterday [at] TIME
    if let Some(m) = REL_DAY_RE.captures(lower) {
        let word = m.get(1).unwrap().as_str();
        let rest = m.get(2).unwrap().as_str().trim();
        let mut base = today;
        if word == "tomorrow" || word == "tmrw" {
            base = today + ChronoDuration::days(1);
        } else if word == "yesterday" {
            base = today - ChronoDuration::days(1);
        }
        if rest.is_empty() {
            return Ok(base);
        }
        if let Some((h, mn)) = parse_time(rest) {
            return Ok(base.date().and_hms_opt(h, mn, 0).unwrap());
        }
    }

    // next <weekday> [at] TIME
    if let Some(m) = NEXT_WEEKDAY_RE.captures(lower) {
        let wd = m.get(1).unwrap().as_str();
        if let Some(target_dow) = WEEKDAYS.iter().position(|w| *w == wd) {
            // days = (target_dow - today.weekday()) % 7 or 7
            let cur = today.weekday().num_days_from_monday() as i64;
            let diff = (target_dow as i64 - cur).rem_euclid(7);
            let days = if diff == 0 { 7 } else { diff };
            let base = today + ChronoDuration::days(days);
            let rest = m.get(2).unwrap().as_str().trim();
            if rest.is_empty() {
                return Ok(base);
            }
            if let Some((h, mn)) = parse_time(rest) {
                return Ok(base.date().and_hms_opt(h, mn, 0).unwrap());
            }
        }
    }

    // in N hours/minutes/days
    if let Some(m) = IN_N_RE.captures(lower) {
        let n: i64 = m.get(1).unwrap().as_str().parse().unwrap_or(0);
        let unit = m.get(2).unwrap().as_str();
        match unit {
            "hour" | "hr" => return Ok(now + ChronoDuration::hours(n)),
            "minute" | "min" => return Ok(now + ChronoDuration::minutes(n)),
            "day" => return Ok(now + ChronoDuration::days(n)),
            _ => {}
        }
    }

    // Bare time -> today at that time.
    if let Some((h, mn)) = parse_time(lower) {
        return Ok(today.date().and_hms_opt(h, mn, 0).unwrap());
    }

    // Last resort: dateutil's fuzzy parser (`_du.parse(s)`).
    //
    // `dtparse` is the faithful Rust port of python-dateutil's parser; its
    // `parse(&str) -> Result<(NaiveDateTime, Option<FixedOffset>), _>` maps 1:1
    // onto dateutil's `datetime` return. `_parse_dt` strips tz to naive for its
    // legacy callers (Python py:275-277), so we take ONLY the `NaiveDateTime`
    // and drop the `FixedOffset`. Use the ORIGINAL `s` (not `lower`) to match
    // `_du.parse(s)`. On `Err` keep the same final error shape Python raises.
    //
    // (Known cosmetic drift: for a bare unrecognized tz abbreviation, e.g.
    // "...21:00 EST", dtparse prints one diagnostic line to STDOUT and returns
    // offset=None — the naive datetime is still correct and matches dateutil's
    // default naive return for un-mapped tz names.)
    if let Ok((dt, _offset)) = dtparse::parse(s) {
        return Ok(dt);
    }
    Err(format!("could not parse datetime: {s:?}"))
}

/// `_parse_dt_pair(s)` — return `(naive_dt, is_utc)`. `is_utc` is true iff the
/// input carried explicit tz info; the returned datetime is then naive UTC.
///
/// `pub(crate)` so `management_db.rs`'s `parse_event_dt` can delegate here
/// (replacing its strict-ISO-only body) and accept natural-language +
/// dateutil-fallback inputs exactly like the Python `_parse_dt_pair`.
pub(crate) fn parse_dt_pair(s: &str) -> Result<(NaiveDateTime, bool), String> {
    let s = s.trim();
    if s.is_empty() {
        return Err("empty datetime string".to_string());
    }
    if let Some(pair) = parse_iso_fast(s) {
        return Ok(pair);
    }
    // ValueError fall-through -> (_parse_dt(s), False)
    natural_language_dt(s).map(|dt| (dt, false))
}

// ===========================================================================
// `parse_due_for_user(s)` — natural language in the user's tz (kept for parity;
// used by chat/agent code, not the routes themselves)
// ===========================================================================

/// `parse_due_for_user(s)` — parse a due-date in the USER's tz; returns an ISO
/// string with explicit offset, or naive ISO when no user offset is set.
pub fn parse_due_for_user(s: &str) -> String {
    let offset = get_user_tz_offset();
    let s = s.trim();
    if s.is_empty() {
        return s.to_string();
    }

    // Tz-aware ISO short-circuit — preserve as-is.
    let s2 = if let Some(stripped) = s.strip_suffix('Z') {
        format!("{stripped}+00:00")
    } else {
        s.to_string()
    };
    let aware = chrono::DateTime::parse_from_rfc3339(&s2).ok();
    if let Some(parsed) = aware {
        return parsed.to_rfc3339_opts(chrono::SecondsFormat::AutoSi, false);
    }

    let offset = match offset {
        // No user tz known — legacy `_parse_dt(s).isoformat()` (py:161), which
        // retains a tz offset on the dateutil last-resort branch.
        None => return parse_dt_isoformat(s).unwrap_or_else(|_| s.to_string()),
        Some(o) => o,
    };
    let user_tz = match FixedOffset::east_opt(offset * 60) {
        Some(tz) => tz,
        None => return parse_dt_isoformat(s).unwrap_or_else(|_| s.to_string()),
    };

    // Naive ISO -> tag with user tz.
    if let Ok(naive) = NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S")
        .or_else(|_| NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M"))
    {
        if let Some(local) = user_tz.from_local_datetime(&naive).single() {
            return local.to_rfc3339_opts(chrono::SecondsFormat::AutoSi, false);
        }
    }

    // Natural language — evaluate against the user's "now".
    let server_now_utc = Utc::now();
    let user_now = server_now_utc.with_timezone(&user_tz).naive_local();
    let lower = s.to_lowercase();
    let lower = lower.trim();
    let today = user_now.date().and_hms_opt(0, 0, 0).unwrap();

    if let Some(m) = REL_DAY_TONIGHT_RE.captures(lower) {
        let word = m.get(1).unwrap().as_str();
        let rest = m.get(2).unwrap().as_str().trim();
        let mut base = today;
        if word == "tomorrow" || word == "tmrw" {
            base = today + ChronoDuration::days(1);
        } else if word == "yesterday" {
            base = today - ChronoDuration::days(1);
        }
        if rest.is_empty() {
            return iso_with_offset(base, &user_tz);
        }
        if let Some((h, mn)) = parse_time(rest) {
            let b = base.date().and_hms_opt(h, mn, 0).unwrap();
            return iso_with_offset(b, &user_tz);
        }
    }

    if let Some(m) = IN_N_RE.captures(lower) {
        let n: i64 = m.get(1).unwrap().as_str().parse().unwrap_or(0);
        let unit = m.get(2).unwrap().as_str();
        let dt = match unit {
            "hour" | "hr" => Some(user_now + ChronoDuration::hours(n)),
            "minute" | "min" => Some(user_now + ChronoDuration::minutes(n)),
            "day" => Some(user_now + ChronoDuration::days(n)),
            _ => None,
        };
        if let Some(dt) = dt {
            return iso_with_offset(dt, &user_tz);
        }
    }

    if let Some((h, mn)) = parse_time(lower) {
        let b = today.date().and_hms_opt(h, mn, 0).unwrap();
        return iso_with_offset(b, &user_tz);
    }

    // Last resort: dateutil. Trust it but apply user tz if it returned naive
    // (`_du.parse(s)`; `if parsed2.tzinfo is None: parsed2 =
    // parsed2.replace(tzinfo=user_tz)`; `return parsed2.isoformat()`; py:212-218).
    // `dtparse` returns `(NaiveDateTime, Option<FixedOffset>)`:
    //   - `Some(off)` => the parsed value carried tz info; render with THAT
    //     offset (matches dateutil's tz-aware `.isoformat()`).
    //   - `None`      => naive; attach the user's tz.
    // On `Err` -> "Final fallback: legacy parser, naive." (py:219-221), i.e.
    // `_parse_dt(s).isoformat()` (which itself now ends in the dtparse fallback).
    match dtparse::parse(s) {
        Ok((dt, Some(off))) => match off.from_local_datetime(&dt).single() {
            Some(local) => local.to_rfc3339_opts(chrono::SecondsFormat::AutoSi, false),
            None => iso_with_offset(dt, &user_tz),
        },
        Ok((dt, None)) => iso_with_offset(dt, &user_tz),
        // Final fallback: legacy `_parse_dt(s).isoformat()` (py:221), retaining
        // a tz offset on its own dateutil last-resort branch.
        Err(_) => parse_dt_isoformat(s).unwrap_or_else(|_| s.to_string()),
    }
}

/// Render a naive datetime in the user's fixed-offset tz as an ISO string with
/// the explicit offset (`datetime.replace(tzinfo=user_tz).isoformat()`).
fn iso_with_offset(naive: NaiveDateTime, tz: &FixedOffset) -> String {
    match tz.from_local_datetime(&naive).single() {
        Some(local) => local.to_rfc3339_opts(chrono::SecondsFormat::AutoSi, false),
        None => naive_isoformat(naive),
    }
}

/// `datetime.isoformat()` for a naive datetime (T separator, microseconds only
/// when non-zero).
fn naive_isoformat(dt: NaiveDateTime) -> String {
    if dt.nanosecond() == 0 {
        dt.format("%Y-%m-%dT%H:%M:%S").to_string()
    } else {
        dt.format("%Y-%m-%dT%H:%M:%S%.6f").to_string()
    }
}

/// `_parse_dt(s).isoformat()` — used by the `offset is None`/`Err` rendering
/// paths of `parse_due_for_user` (Python calendar_routes.py:161 and :221).
///
/// This is NOT `naive_isoformat(parse_dt(s))`: Python's `_parse_dt` only strips
/// tz on the strict-ISO fast path (py:275-277). Its LAST-RESORT `_du.parse(s)`
/// branch keeps whatever tzinfo dateutil produced, so a tz-aware input renders
/// WITH its offset. We mirror that by retaining the dtparse `FixedOffset` when
/// present in the last-resort branch, rather than collapsing it to naive.
///
/// Every earlier branch of `_parse_dt` (strict ISO + the natural-language tail)
/// yields a naive `datetime`, so `parse_dt(s)` -> `naive_isoformat` matches them
/// 1:1; only the dtparse tail can differ, and that is the branch this special-
/// cases. We detect "fell through to dtparse" by checking that none of the
/// naive natural-language branches resolved (`parse_dt` reached its own dtparse
/// tail), then re-parse with dtparse to recover the offset.
fn parse_dt_isoformat(s: &str) -> Result<String, String> {
    let trimmed = s.trim();
    if trimmed.is_empty() {
        return Err("empty datetime string".to_string());
    }
    // Strict-ISO fast path and the resolved natural-language branches are all
    // naive; render those exactly as before via the naive path.
    if parse_iso_fast(trimmed).is_some() || naive_nl_resolves(trimmed) {
        return parse_dt(trimmed).map(naive_isoformat);
    }
    // Fell through to the dateutil last-resort. Retain the `FixedOffset` like
    // `_du.parse(s)` keeps tzinfo; offset=None stays naive (matches dateutil's
    // naive default for un-mapped tz names).
    match dtparse::parse(trimmed) {
        Ok((dt, Some(off))) => match off.from_local_datetime(&dt).single() {
            Some(local) => Ok(local.to_rfc3339_opts(chrono::SecondsFormat::AutoSi, false)),
            None => Ok(naive_isoformat(dt)),
        },
        Ok((dt, None)) => Ok(naive_isoformat(dt)),
        Err(_) => Err(format!("could not parse datetime: {trimmed:?}")),
    }
}

/// True iff one of the naive natural-language branches of `_parse_dt`
/// (`today/tomorrow`, `next <weekday>`, `in N <unit>`, bare time) RESOLVES to a
/// value for `s` — i.e. `_parse_dt` returns before reaching the dtparse tail.
/// Mirrors the early-return conditions in `natural_language_dt` exactly so the
/// offset-retaining path only triggers when Python would also reach `_du.parse`.
fn naive_nl_resolves(s: &str) -> bool {
    let lower = s.to_lowercase();
    let lower = lower.trim();

    if let Some(m) = REL_DAY_RE.captures(lower) {
        let rest = m.get(2).unwrap().as_str().trim();
        if rest.is_empty() || parse_time(rest).is_some() {
            return true;
        }
    }
    if let Some(m) = NEXT_WEEKDAY_RE.captures(lower) {
        let wd = m.get(1).unwrap().as_str();
        if WEEKDAYS.contains(&wd) {
            let rest = m.get(2).unwrap().as_str().trim();
            if rest.is_empty() || parse_time(rest).is_some() {
                return true;
            }
        }
    }
    if let Some(m) = IN_N_RE.captures(lower) {
        let unit = m.get(2).unwrap().as_str();
        if matches!(unit, "hour" | "hr" | "minute" | "min" | "day") {
            return true;
        }
    }
    parse_time(lower).is_some()
}

// ===========================================================================
// Pydantic models
// ===========================================================================

#[derive(Deserialize, Default)]
struct EventCreate {
    summary: String,
    dtstart: String,
    #[serde(default)]
    dtend: Option<String>,
    #[serde(default)]
    all_day: bool,
    #[serde(default)]
    description: String,
    #[serde(default)]
    location: String,
    #[serde(default)]
    calendar_href: Option<String>,
    #[serde(default)]
    rrule: Option<String>,
    #[serde(default)]
    color: Option<String>,
}

#[derive(Deserialize, Default)]
struct EventUpdate {
    #[serde(default)]
    summary: Option<String>,
    #[serde(default)]
    dtstart: Option<String>,
    #[serde(default)]
    dtend: Option<String>,
    #[serde(default)]
    all_day: Option<bool>,
    #[serde(default)]
    description: Option<String>,
    #[serde(default)]
    location: Option<String>,
    #[serde(default)]
    rrule: Option<String>,
    #[serde(default)]
    color: Option<String>,
}

// ===========================================================================
// DB row helpers
// ===========================================================================

/// A loaded `CalendarEvent` row + its calendar's name/color (the join the
/// serializer needs). Mirrors the columns `_event_to_dict` reads.
struct EventRow {
    uid: String,
    summary: Option<String>,
    dtstart: String,
    dtend: String,
    all_day: bool,
    is_utc: bool,
    description: Option<String>,
    location: Option<String>,
    rrule: Option<String>,
    calendar_id: String,
    color: Option<String>,
    event_type: Option<String>,
    importance: Option<String>,
    cal_name: Option<String>,
    cal_color: Option<String>,
}

/// `_event_to_dict(ev)` — convert a row to the API dict (exact key order).
fn event_to_dict(ev: &EventRow) -> Value {
    // Parse the stored DATETIME strings to render start/end appropriately.
    let (start_str, end_str) = if ev.all_day {
        // ev.dtstart.strftime("%Y-%m-%d")
        (date_strftime(&ev.dtstart), date_strftime(&ev.dtend))
    } else {
        // suffix = "Z" if is_utc else ""; isoformat() + suffix
        let suffix = if ev.is_utc { "Z" } else { "" };
        (
            format!("{}{suffix}", pydatetime::to_isoformat(&ev.dtstart)),
            format!("{}{suffix}", pydatetime::to_isoformat(&ev.dtend)),
        )
    };
    // color = ev.color or (ev.calendar.color if ev.calendar else "")
    let color = match ev.color.as_deref().filter(|c| !c.is_empty()) {
        Some(c) => c.to_string(),
        None => ev.cal_color.clone().unwrap_or_default(),
    };
    // calendar = ev.calendar.name if ev.calendar else ""
    let calendar = ev.cal_name.clone().unwrap_or_default();
    // importance = getattr(...) or "normal"
    let importance = match ev.importance.as_deref().filter(|s| !s.is_empty()) {
        Some(s) => s.to_string(),
        None => "normal".to_string(),
    };
    json!({
        "uid": ev.uid,
        "summary": ev.summary.clone().unwrap_or_default(),
        "dtstart": start_str,
        "dtend": end_str,
        "all_day": ev.all_day,
        "is_utc": ev.is_utc,
        "description": ev.description.clone().unwrap_or_default(),
        "location": ev.location.clone().unwrap_or_default(),
        "rrule": ev.rrule.clone().unwrap_or_default(),
        "calendar": calendar,
        "calendar_href": ev.calendar_id,
        "color": color,
        "event_type": ev.event_type,
        "importance": importance,
    })
}

/// `ev.dtstart.strftime("%Y-%m-%d")` for a stored DATETIME string.
fn date_strftime(stored: &str) -> String {
    parse_stored(stored)
        .map(|dt| dt.format("%Y-%m-%d").to_string())
        .unwrap_or_else(|| stored.to_string())
}

/// `ev.dtstart.strftime("%Y%m%d")` (ICS all-day).
fn ics_date(stored: &str) -> String {
    parse_stored(stored)
        .map(|dt| dt.format("%Y%m%d").to_string())
        .unwrap_or_else(|| stored.to_string())
}

/// `ev.dtstart.strftime("%Y%m%dT%H%M%S")` (ICS timed).
fn ics_datetime(stored: &str) -> String {
    parse_stored(stored)
        .map(|dt| dt.format("%Y%m%dT%H%M%S").to_string())
        .unwrap_or_else(|| stored.to_string())
}

/// Parse a stored SQLite DATETIME string back into a `NaiveDateTime`.
fn parse_stored(stored: &str) -> Option<NaiveDateTime> {
    NaiveDateTime::parse_from_str(stored, "%Y-%m-%d %H:%M:%S%.f")
        .or_else(|_| NaiveDateTime::parse_from_str(stored, "%Y-%m-%d %H:%M:%S"))
        .ok()
}

/// SELECT clause loading an event joined to its calendar.
const EVENT_SELECT: &str = "SELECT e.uid, e.summary, e.dtstart, e.dtend, e.all_day, e.is_utc, \
    e.description, e.location, e.rrule, e.calendar_id, e.color, e.event_type, e.importance, \
    c.name, c.color \
    FROM calendar_events e JOIN calendars c ON e.calendar_id = c.id";

fn map_event_row(r: &rusqlite::Row) -> rusqlite::Result<EventRow> {
    Ok(EventRow {
        uid: r.get(0)?,
        summary: r.get(1)?,
        dtstart: r.get(2)?,
        dtend: r.get(3)?,
        all_day: r.get::<_, Option<bool>>(4)?.unwrap_or(false),
        is_utc: r.get::<_, Option<bool>>(5)?.unwrap_or(false),
        description: r.get(6)?,
        location: r.get(7)?,
        rrule: r.get(8)?,
        calendar_id: r.get(9)?,
        color: r.get(10)?,
        event_type: r.get(11)?,
        importance: r.get(12)?,
        cal_name: r.get(13)?,
        cal_color: r.get(14)?,
    })
}

/// `_ensure_default_calendar(db, owner)` — return the owner's calendar id,
/// creating a `Personal` row if none exists.
fn ensure_default_calendar(
    conn: &rusqlite::Connection,
    owner: &str,
) -> rusqlite::Result<String> {
    // owner = owner or FALLBACK_OWNER
    let eff_owner = if owner.is_empty() {
        fallback_owner()
    } else {
        owner.to_string()
    };
    let existing: Option<String> = conn
        .query_row(
            "SELECT id FROM calendars WHERE owner = ?1 LIMIT 1",
            [&eff_owner],
            |r| r.get(0),
        )
        .optional()?;
    if let Some(id) = existing {
        return Ok(id);
    }
    let id = uuid::Uuid::new_v4().to_string();
    let now = pydatetime::utcnow_naive_iso();
    conn.execute(
        "INSERT INTO calendars (id, owner, name, color, source, created_at, updated_at) \
         VALUES (?1, ?2, 'Personal', '#5b8abf', 'local', ?3, ?3)",
        rusqlite::params![id, eff_owner, now],
    )?;
    Ok(id)
}

/// A loaded calendar row (for the `_get_or_404` gates).
struct CalRow {
    // Selected to faithfully mirror the Python row object; no caller reads it
    // (gates use `name`/`owner`/`color`), so it is intentionally unread.
    #[allow(dead_code)]
    id: String,
    name: String,
    owner: Option<String>,
    color: Option<String>,
}

/// `_get_or_404_calendar(db, cal_id, owner)`.
fn get_or_404_calendar(
    conn: &rusqlite::Connection,
    cal_id: &str,
    owner: &str,
) -> Result<CalRow, HttpException> {
    let row: Option<CalRow> = conn
        .query_row(
            "SELECT id, name, owner, color FROM calendars WHERE id = ?1",
            [cal_id],
            |r| {
                Ok(CalRow {
                    id: r.get(0)?,
                    name: r.get(1)?,
                    owner: r.get(2)?,
                    color: r.get(3)?,
                })
            },
        )
        .optional()
        .map_err(|_| HttpException::new(500, "Internal Server Error"))?;
    let cal = match row {
        Some(c) => c,
        None => return Err(HttpException::new(404, "Calendar not found")),
    };
    // if owner and (cal.owner is None or cal.owner != owner): raise 404
    if !owner.is_empty() {
        match &cal.owner {
            None => return Err(HttpException::new(404, "Calendar not found")),
            Some(o) if o != owner => return Err(HttpException::new(404, "Calendar not found")),
            _ => {}
        }
    }
    Ok(cal)
}

/// `_get_or_404_event(db, uid, owner)` — returns the row (with calendar owner gate).
fn get_or_404_event(
    conn: &rusqlite::Connection,
    uid: &str,
    owner: &str,
) -> Result<(), HttpException> {
    // ev = db.query(CalendarEvent).join(CalendarCal).filter(uid == uid).first()
    let cal_owner: Option<(Option<String>,)> = conn
        .query_row(
            "SELECT c.owner FROM calendar_events e JOIN calendars c ON e.calendar_id = c.id \
             WHERE e.uid = ?1",
            [uid],
            |r| Ok((r.get::<_, Option<String>>(0)?,)),
        )
        .optional()
        .map_err(|_| HttpException::new(500, "Internal Server Error"))?;
    // The join means a row without a matching calendar is already absent.
    let cal_owner = match cal_owner {
        Some(c) => c.0,
        None => {
            // The Python join can still return the event if the calendar exists;
            // check whether the event exists at all to mirror the 404 message.
            return Err(HttpException::new(404, "Event not found"));
        }
    };
    // if owner and cal and (cal.owner is None or cal.owner != owner): raise 404
    if !owner.is_empty() {
        match &cal_owner {
            None => return Err(HttpException::new(404, "Event not found")),
            Some(o) if o != owner => return Err(HttpException::new(404, "Event not found")),
            _ => {}
        }
    }
    Ok(())
}

// ===========================================================================
// Router factory
// ===========================================================================

/// `setup_calendar_routes()` — `APIRouter(prefix="/api/calendar", tags=["calendar"])`.
///
/// app.py registers this as include #527. Each handler mounts at the absolute
/// `/api/calendar/...` path the Python decorator names (colon path params).
pub fn setup_calendar_routes() -> Router<AppState> {
    Router::new()
        .route("/api/calendar/config", get(get_config).post(save_config))
        .route("/api/calendar/test", post(test_connection))
        .route("/api/calendar/sync", post(sync_caldav_endpoint))
        .route("/api/calendar/calendars", get(list_calendars).post(create_calendar))
        .route("/api/calendar/events", get(list_events).post(create_event))
        .route(
            "/api/calendar/events/:uid",
            put(update_event).delete(delete_event),
        )
        .route(
            "/api/calendar/calendars/:cal_id",
            put(update_calendar).delete(delete_calendar),
        )
        .route("/api/calendar/import", post(import_ics))
        .route("/api/calendar/export/:cal_id", get(export_ics))
        .route("/api/calendar/quick-parse", post(quick_parse))
}

// ===========================================================================
// DB connection helper
// ===========================================================================

/// `db = SessionLocal()` — a fresh connection; a failure maps to a 500.
fn session() -> Result<rusqlite::Connection, HttpException> {
    crate::core::database::session_local().map_err(|_| HttpException::new(500, "Internal Server Error"))
}

/// Parse a (possibly malformed/empty) JSON body the way `await request.json()`
/// + `except Exception: body = {}` does.
fn json_or_empty(body: &axum::body::Bytes) -> Value {
    serde_json::from_slice::<Value>(body).unwrap_or_else(|_| json!({}))
}

fn body_get_str<'a>(body: &'a Value, key: &str) -> &'a str {
    body.get(key).and_then(Value::as_str).unwrap_or("")
}

// ===========================================================================
// Config endpoints
// ===========================================================================

/// `GET /api/calendar/config` — surface url+username (never the password).
async fn get_config(user: Option<Extension<CurrentUser>>) -> Result<Response, HttpException> {
    let owner = require_user(user.as_deref())?;
    // cfg = (_load_for_user(owner) or {}).get("caldav", {}) or {}
    let prefs = crate::routes::prefs_store::load_for_user(Some(&owner));
    let cfg = prefs.get("caldav").cloned().unwrap_or_else(|| json!({}));
    let url = cfg.get("url").and_then(Value::as_str).unwrap_or("");
    let username = cfg.get("username").and_then(Value::as_str).unwrap_or("");
    let has_password = !body_get_str(&cfg, "password").is_empty();
    Ok(Json(json!({
        "url": url,
        "username": username,
        "password": "",
        "has_password": has_password,
        "local": url.is_empty(),
    }))
    .into_response())
}

/// `POST /api/calendar/config` — save/clear CalDAV creds.
async fn save_config(
    user: Option<Extension<CurrentUser>>,
    body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let owner = require_user(user.as_deref())?;
    let body = json_or_empty(&body);
    // prefs = _load_for_user(owner) or {}
    let mut prefs = crate::routes::prefs_store::load_for_user(Some(&owner));
    // cfg = dict(prefs.get("caldav") or {})
    let mut cfg = prefs
        .get("caldav")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();

    // if not (body.get("url") or "").strip(): clear & return
    if body_get_str(&body, "url").trim().is_empty() {
        if let Some(map) = prefs.as_object_mut() {
            map.remove("caldav");
        }
        let _ = crate::routes::prefs_store::save_for_user(Some(&owner), &prefs);
        return Ok(Json(json!({"ok": true, "cleared": true})).into_response());
    }
    cfg.insert(
        "url".to_string(),
        Value::String(body_get_str(&body, "url").trim().to_string()),
    );
    cfg.insert(
        "username".to_string(),
        Value::String(body_get_str(&body, "username").trim().to_string()),
    );
    // `if body.get("password"): cfg["password"] = encrypt(body["password"])`.
    // Only touch the password when a NEW one is supplied — `cfg` already holds the
    // existing (already-encrypted) value from prefs, so re-encrypting it would
    // double-encrypt (upstream c58cb06). The new password is encrypted AT REST.
    let pw = body.get("password");
    if pw.map(value_truthy).unwrap_or(false) {
        let plain = pw.and_then(|v| v.as_str()).unwrap_or("");
        cfg.insert(
            "password".to_string(),
            Value::String(crate::src::secret_storage::encrypt(plain)),
        );
    }
    if let Some(map) = prefs.as_object_mut() {
        map.insert("caldav".to_string(), Value::Object(cfg));
    }
    let _ = crate::routes::prefs_store::save_for_user(Some(&owner), &prefs);
    Ok(Json(json!({"ok": true})).into_response())
}

/// `value or ""` truthiness for a JSON value (Python truthiness).
fn value_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `POST /api/calendar/test` — PROPFIND probe against the CalDAV server.
async fn test_connection(
    user: Option<Extension<CurrentUser>>,
    body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let owner = require_user(user.as_deref())?;
    let body = json_or_empty(&body);
    let mut url = body_get_str(&body, "url").trim().to_string();
    let mut user_s = body_get_str(&body, "username").trim().to_string();
    let mut pw = body_get_str(&body, "password").to_string();
    // if not (url and user and pw): fall back to saved settings.
    if url.is_empty() || user_s.is_empty() || pw.is_empty() {
        let prefs = crate::routes::prefs_store::load_for_user(Some(&owner));
        let cfg = prefs.get("caldav").cloned().unwrap_or_else(|| json!({}));
        if url.is_empty() {
            url = body_get_str(&cfg, "url").to_string();
        }
        if user_s.is_empty() {
            user_s = body_get_str(&cfg, "username").to_string();
        }
        if pw.is_empty() {
            // The stored CalDAV password is encrypted at rest -> decrypt before use
            // (lenient: a legacy plaintext value is returned unchanged).
            pw = crate::src::secret_storage::decrypt(body_get_str(&cfg, "password"));
        }
    }
    if url.is_empty() || user_s.is_empty() || pw.is_empty() {
        return Ok(Json(json!({"ok": false, "error": "Missing URL, username, or password"})).into_response());
    }
    let propfind_body = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n\
        <d:propfind xmlns:d=\"DAV:\"><d:prop><d:resourcetype/></d:prop></d:propfind>";

    // async with httpx.AsyncClient(timeout=8.0, follow_redirects=True)
    let client = match reqwest::Client::builder()
        .timeout(Duration::from_secs(8))
        // httpx.AsyncClient(follow_redirects=True) defaults to max_redirects=20.
        .redirect(reqwest::redirect::Policy::limited(20))
        .build()
    {
        Ok(c) => c,
        Err(e) => {
            let msg = truncate_chars(&e.to_string(), 200);
            return Ok(Json(json!({"ok": false, "error": msg})).into_response());
        }
    };
    let method = match reqwest::Method::from_bytes(b"PROPFIND") {
        Ok(m) => m,
        Err(e) => {
            let msg = truncate_chars(&e.to_string(), 200);
            return Ok(Json(json!({"ok": false, "error": msg})).into_response());
        }
    };
    let resp = client
        .request(method, &url)
        .basic_auth(&user_s, Some(&pw))
        .header("Depth", "0")
        .header("Content-Type", "application/xml")
        .body(propfind_body)
        .send()
        .await;

    match resp {
        Ok(r) => {
            let code = r.status().as_u16();
            let payload = match code {
                200 | 207 => json!({"ok": true}),
                401 => json!({"ok": false, "error": "Auth failed — check username/password"}),
                403 => json!({"ok": false, "error": "Forbidden — user can't access that URL"}),
                404 => json!({"ok": false, "error": "Not found — check the URL path"}),
                other => json!({"ok": false, "error": format!("HTTP {other}")}),
            };
            Ok(Json(payload).into_response())
        }
        Err(e) => {
            // httpx.TimeoutException -> "Connection timed out";
            // httpx.ConnectError -> "Connection refused: ..."[:200];
            // else -> str(e)[:200].
            let payload = if e.is_timeout() {
                json!({"ok": false, "error": "Connection timed out"})
            } else if e.is_connect() {
                json!({"ok": false, "error": truncate_chars(&format!("Connection refused: {e}"), 200)})
            } else {
                json!({"ok": false, "error": truncate_chars(&e.to_string(), 200)})
            };
            Ok(Json(payload).into_response())
        }
    }
}

/// Python `s[:n]` over code points.
fn truncate_chars(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// `POST /api/calendar/sync` — pull events from the configured CalDAV server.
async fn sync_caldav_endpoint(
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    let owner = require_user(user.as_deref())?;
    // return await sync_caldav(owner)
    let result = crate::src::caldav_sync::sync_caldav(&owner).await;
    Ok(Json(result).into_response())
}

// ===========================================================================
// Calendar list / CRUD
// ===========================================================================

/// `GET /api/calendar/calendars`.
async fn list_calendars(user: Option<Extension<CurrentUser>>) -> Result<Response, HttpException> {
    let owner = require_user(user.as_deref())?;
    let conn = session()?;
    // _ensure_default_calendar(db, owner)
    if ensure_default_calendar(&conn, &owner).is_err() {
        crate::pylog::error("Failed to list calendars");
        return Err(HttpException::new(500, "Failed to list calendars"));
    }
    let mut stmt = conn
        .prepare("SELECT name, id, color FROM calendars WHERE owner = ?1")
        .map_err(|_| HttpException::new(500, "Failed to list calendars"))?;
    let rows = stmt
        .query_map([&owner], |r| {
            Ok(json!({
                "name": r.get::<_, String>(0)?,
                "href": r.get::<_, String>(1)?,
                "color": r.get::<_, Option<String>>(2)?,
            }))
        })
        .map_err(|_| HttpException::new(500, "Failed to list calendars"))?;
    let mut cals: Vec<Value> = Vec::new();
    for row in rows {
        match row {
            Ok(v) => cals.push(v),
            Err(_) => return Err(HttpException::new(500, "Failed to list calendars")),
        }
    }
    Ok(Json(json!({"calendars": cals})).into_response())
}

/// `POST /api/calendar/calendars` — query params `name="Imported"`, `color="#5b8abf"`.
async fn create_calendar(
    user: Option<Extension<CurrentUser>>,
    Query(q): Query<std::collections::HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let owner = require_user(user.as_deref())?;
    let name = q.get("name").cloned().unwrap_or_else(|| "Imported".to_string());
    let color = q.get("color").cloned().unwrap_or_else(|| "#5b8abf".to_string());
    let conn = session()?;
    let id = uuid::Uuid::new_v4().to_string();
    let now = pydatetime::utcnow_naive_iso();
    let res = conn.execute(
        "INSERT INTO calendars (id, owner, name, color, source, created_at, updated_at) \
         VALUES (?1, ?2, ?3, ?4, 'local', ?5, ?5)",
        rusqlite::params![id, owner, name, color, now],
    );
    match res {
        Ok(_) => Ok(Json(json!({"ok": true, "id": id, "name": name, "color": color})).into_response()),
        Err(e) => {
            crate::pylog::error(&format!("Failed to create calendar: {e}"));
            Err(HttpException::new(500, "Failed to create calendar"))
        }
    }
}

/// `PUT /api/calendar/calendars/:cal_id` — query params `name`, `color`.
async fn update_calendar(
    user: Option<Extension<CurrentUser>>,
    Path(cal_id): Path<String>,
    Query(q): Query<std::collections::HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let owner = require_user(user.as_deref())?;
    let conn = session()?;
    let cal = get_or_404_calendar(&conn, &cal_id, &owner)?;
    let now = pydatetime::utcnow_naive_iso();
    // if name is not None: cal.name = name; if color is not None: cal.color = color
    let new_name = q.get("name").cloned().unwrap_or(cal.name);
    let new_color = q.get("color").cloned().or(cal.color);
    let res = conn.execute(
        "UPDATE calendars SET name = ?2, color = ?3, updated_at = ?4 WHERE id = ?1",
        rusqlite::params![cal_id, new_name, new_color, now],
    );
    match res {
        Ok(_) => Ok(Json(json!({"ok": true})).into_response()),
        Err(e) => {
            crate::pylog::error(&format!("Failed to update calendar: {e}"));
            Err(HttpException::new(500, "Failed to update calendar"))
        }
    }
}

/// `DELETE /api/calendar/calendars/:cal_id`.
async fn delete_calendar(
    user: Option<Extension<CurrentUser>>,
    Path(cal_id): Path<String>,
) -> Result<Response, HttpException> {
    let owner = require_user(user.as_deref())?;
    let conn = session()?;
    let _cal = get_or_404_calendar(&conn, &cal_id, &owner)?;
    // db.query(CalendarEvent).filter(calendar_id == cal_id).delete(); db.delete(cal)
    let res = (|| -> rusqlite::Result<()> {
        conn.execute("DELETE FROM calendar_events WHERE calendar_id = ?1", [&cal_id])?;
        conn.execute("DELETE FROM calendars WHERE id = ?1", [&cal_id])?;
        Ok(())
    })();
    match res {
        Ok(_) => Ok(Json(json!({"ok": true})).into_response()),
        // except Exception as e: return {"error": str(e)}
        Err(e) => Ok(Json(json!({"error": e.to_string()})).into_response()),
    }
}

// ===========================================================================
// Event list / CRUD
// ===========================================================================

/// `GET /api/calendar/events?start=&end=&calendar=`.
async fn list_events(
    user: Option<Extension<CurrentUser>>,
    Query(q): Query<std::collections::HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let owner = require_user(user.as_deref())?;
    // start/end are required query params; calendar defaults to "".
    let start = match q.get("start") {
        Some(s) => s.clone(),
        None => return Err(HttpException::new(422, "Field required")),
    };
    let end = match q.get("end") {
        Some(s) => s.clone(),
        None => return Err(HttpException::new(422, "Field required")),
    };
    let calendar = q.get("calendar").cloned().unwrap_or_default();

    let (start_dt, end_dt) = match (parse_dt(&start), parse_dt(&end)) {
        (Ok(s), Ok(e)) => (s, e),
        _ => {
            // Malformed range -> log + return no events.
            crate::pylog::warning(&format!(
                "list_events: unparseable range start={start:?} end={end:?}"
            ));
            return Ok(Json(json!({"events": []})).into_response());
        }
    };
    let start_store = start_dt.format(SQLITE_DT_FMT).to_string();
    let end_store = end_dt.format(SQLITE_DT_FMT).to_string();

    let conn = session()?;
    // Scope events to calendars owned by the caller. Non-recurring events must
    // overlap the query window; recurring events (with an RRULE) whose base
    // dtstart is before the window END are fetched so their actual occurrences
    // can be expanded server-side and appear in every year they repeat, not
    // just the DTSTART year. Mirrors the Python `or_(non_recurring_overlap,
    // recurring_dtstart_before_end)` filter — the prior Rust SQL only kept the
    // overlap branch, silently collapsing recurring series whose base window
    // had already passed.
    let mut sql = format!(
        "{EVENT_SELECT} WHERE e.status != 'cancelled' AND c.owner = ?3 AND ( \
            ((e.rrule = '' OR e.rrule IS NULL) AND e.dtstart < ?1 AND e.dtend > ?2) \
            OR (e.rrule IS NOT NULL AND e.rrule != '' AND e.dtstart < ?1) \
         )"
    );
    let mut params: Vec<Box<dyn rusqlite::ToSql>> = vec![
        Box::new(end_store),
        Box::new(start_store),
        Box::new(owner.clone()),
    ];
    if !calendar.is_empty() {
        sql.push_str(" AND (e.calendar_id = ?4 OR c.name = ?4)");
        params.push(Box::new(calendar.clone()));
    }
    sql.push_str(" ORDER BY e.dtstart");

    let result = (|| -> rusqlite::Result<Vec<EventRow>> {
        let mut stmt = conn.prepare(&sql)?;
        let param_refs: Vec<&dyn rusqlite::ToSql> = params.iter().map(|b| b.as_ref()).collect();
        let rows = stmt.query_map(param_refs.as_slice(), map_event_row)?;
        let mut out = Vec::new();
        for r in rows {
            out.push(r?);
        }
        Ok(out)
    })();

    let rows = match result {
        Ok(rows) => rows,
        Err(e) => {
            crate::pylog::error(&format!("Failed to list events: {e}"));
            return Err(HttpException::new(500, "Failed to list events"));
        }
    };

    // Expand recurring events into individual occurrences, then sort by the
    // occurrence start time for consistent frontend ordering
    // (`expanded.sort(key=lambda d: d["dtstart"])`).
    let mut expanded: Vec<Value> = Vec::new();
    for ev in &rows {
        expanded.extend(expand_rrule(ev, start_dt, end_dt));
    }
    expanded.sort_by(|a, b| {
        let sa = a.get("dtstart").and_then(Value::as_str).unwrap_or("");
        let sb = b.get("dtstart").and_then(Value::as_str).unwrap_or("");
        sa.cmp(sb)
    });
    Ok(Json(json!({"events": expanded})).into_response())
}

// ===========================================================================
// Recurrence expansion (`_expand_rrule`)
// ===========================================================================

/// `UNTIL=YYYYMMDD[THHMMSS]Z` -> drop the trailing `Z` (case-insensitive), so the
/// UNTIL bound matches the naive (UTC) DTSTART the rows are stored with. Mirrors
/// `re.sub(r"(UNTIL=\d{8}(?:T\d{6})?)Z", r"\1", rrule_str, flags=IGNORECASE)`.
static UNTIL_Z_RE: Lazy<regex::Regex> =
    Lazy::new(|| regex::Regex::new(r"(?i)(UNTIL=\d{8}(?:T\d{6})?)Z").unwrap());

/// `_expand_rrule(ev, start, end)` — expand a single (possibly recurring) event
/// into occurrence dicts.
///
/// Non-recurring events (empty rrule) are returned as a single-item list with
/// `is_recurrence=false` and `series_uid=uid`. Recurring events have their RRULE
/// expanded across `[start - duration, end]` (so multi-day / overnight
/// occurrences that start before the window but end inside it are captured) and
/// each surviving occurrence gets a compound `{base_uid}::{date_or_datetime}`
/// uid. Mirrors the Python `_expand_rrule`, including the UTC-normalized UNTIL
/// handling and the all-day vs timed serialization.
fn expand_rrule(ev: &EventRow, start: NaiveDateTime, end: NaiveDateTime) -> Vec<Value> {
    // The stored DATETIME strings parse to naive datetimes; if either won't
    // parse, fall back to the non-recurring single-item behavior with the row's
    // serialized form (matches the Python's "return the base event as-is").
    let dtstart = match parse_stored(&ev.dtstart) {
        Some(d) => d,
        None => {
            let mut d = event_to_dict(ev);
            if let Some(m) = d.as_object_mut() {
                m.insert("is_recurrence".to_string(), Value::Bool(false));
                m.insert("series_uid".to_string(), Value::String(ev.uid.clone()));
            }
            return vec![d];
        }
    };
    let dtend = parse_stored(&ev.dtend).unwrap_or(dtstart);
    // duration = ev.dtend - ev.dtstart
    let duration = dtend - dtstart;

    let rrule_raw = ev.rrule.as_deref().unwrap_or("");
    if rrule_raw.trim().is_empty() {
        // Non-recurring — return the base event as-is. The SQL already filtered
        // non-recurring rows by overlap, so no re-check here.
        let mut d = event_to_dict(ev);
        if let Some(m) = d.as_object_mut() {
            m.insert("is_recurrence".to_string(), Value::Bool(false));
            m.insert("series_uid".to_string(), Value::String(ev.uid.clone()));
        }
        return vec![d];
    }

    // Events are stored with a naive (UTC) dtstart; standard exporters write the
    // bound as an absolute UTC value (UNTIL=...Z). Drop the trailing Z so UNTIL
    // matches the naive DTSTART (otherwise the parse would collapse the series).
    let rrule_str = UNTIL_Z_RE.replace_all(rrule_raw, "$1").to_string();

    let rule = match RecurRule::parse(&rrule_str, dtstart) {
        Some(r) => r,
        None => {
            // Failed to parse — warn and return the base event ONLY if it
            // actually overlaps the window (the recurring SQL branch fetches
            // rows by dtstart < end alone, so the base may not overlap).
            crate::pylog::warning(&format!(
                "Failed to parse rrule={:?} for event {}",
                rrule_raw, ev.uid
            ));
            if dtstart < end && dtend > start {
                let mut d = event_to_dict(ev);
                if let Some(m) = d.as_object_mut() {
                    m.insert("is_recurrence".to_string(), Value::Bool(false));
                    m.insert("series_uid".to_string(), Value::String(ev.uid.clone()));
                }
                return vec![d];
            }
            return Vec::new();
        }
    };

    // Expand from start - duration so multi-day / overnight occurrences that
    // start before the window but end inside it are captured.
    let expand_start = start - duration;
    let occurrences = rule.between(expand_start, end);
    if occurrences.is_empty() {
        return Vec::new();
    }

    let base = event_to_dict(ev);
    let suffix = if ev.is_utc { "Z" } else { "" };
    let mut results: Vec<Value> = Vec::new();
    for occ_start in occurrences {
        let occ_end = occ_start + duration;
        // Overlap filter: occurrence must intersect [start, end).
        if occ_start >= end || occ_end <= start {
            continue;
        }
        // Compound uid: {base_uid}::{date} or ::{datetime}.
        let occ_uid = if ev.all_day {
            format!("{}::{}", ev.uid, occ_start.format("%Y-%m-%d"))
        } else {
            format!("{}::{}", ev.uid, occ_start.format("%Y-%m-%dT%H:%M"))
        };

        let mut d = base.clone();
        if let Some(m) = d.as_object_mut() {
            m.insert("uid".to_string(), Value::String(occ_uid));
            m.insert("series_uid".to_string(), Value::String(ev.uid.clone()));
            m.insert("is_recurrence".to_string(), Value::Bool(true));
            if ev.all_day {
                m.insert(
                    "dtstart".to_string(),
                    Value::String(occ_start.format("%Y-%m-%d").to_string()),
                );
                m.insert(
                    "dtend".to_string(),
                    Value::String(occ_end.format("%Y-%m-%d").to_string()),
                );
            } else {
                m.insert(
                    "dtstart".to_string(),
                    Value::String(format!("{}{suffix}", naive_isoformat(occ_start))),
                );
                m.insert(
                    "dtend".to_string(),
                    Value::String(format!("{}{suffix}", naive_isoformat(occ_end))),
                );
                m.insert("is_utc".to_string(), Value::Bool(ev.is_utc));
            }
        }
        results.push(d);
    }
    results
}

// ---------------------------------------------------------------------------
// `dateutil.rrule.rrulestr(...).between(...)` — pure-Rust RRULE expander
// ---------------------------------------------------------------------------
//
// The Python uses `dateutil.rrule.rrulestr(rrule_str, dtstart=ev.dtstart)` then
// `.between(after, before, inc=True)`. There is no `rrule` crate landed (and
// `Cargo.toml` is off-limits), so this is a faithful hand-port of dateutil's
// recurrence iterator for the FREQ types the Python imports (DAILY / WEEKLY /
// MONTHLY / YEARLY) plus the BY* parts real calendars emit. It walks period by
// period (FREQ * INTERVAL), builds the matching set of datetimes inside each
// period, applies BYSETPOS, and yields occurrences `>= dtstart`. `between`
// returns those with `after <= occ <= before` (inc=True on both ends).
//
// A guard bounds the walk so a COUNT-less / UNTIL-less rule can't loop forever
// when `before` is far away — dateutil itself relies on the caller's `before`
// bound, which `_expand_rrule` always supplies (the window `end`).

#[derive(Clone, Copy, PartialEq)]
enum Freq {
    Daily,
    Weekly,
    Monthly,
    Yearly,
}

/// A parsed RRULE + its anchoring DTSTART, ready to enumerate occurrences.
struct RecurRule {
    dtstart: NaiveDateTime,
    freq: Freq,
    interval: i64,
    count: Option<u64>,
    until: Option<NaiveDateTime>,
    wkst: u32, // weekday index, 0 = Monday (dateutil default WKST=MO)
    bymonth: Vec<u32>,
    bymonthday: Vec<i32>, // negative => from end of month
    byday: Vec<(Option<i32>, u32)>, // (ordinal, weekday 0=Mon)
    byhour: Vec<u32>,
    byminute: Vec<u32>,
    bysecond: Vec<u32>,
    bysetpos: Vec<i32>,
}

impl RecurRule {
    /// `rrulestr(rrule_str, dtstart=...)`. Returns `None` on an unparseable /
    /// unsupported rule (the caller mirrors dateutil's parse-failure handling).
    fn parse(rrule_str: &str, dtstart: NaiveDateTime) -> Option<RecurRule> {
        // Strip an optional leading "RRULE:" prefix; dateutil's rrulestr accepts
        // both the bare value and the full property line.
        let body = rrule_str.trim();
        let body = body.strip_prefix("RRULE:").unwrap_or(body);
        // Multi-line / multi-part content (RDATE/EXDATE etc.) is beyond this
        // hand-port; only a single RRULE line is supported.
        if body.contains('\n') || body.contains('\r') {
            return None;
        }

        let mut freq: Option<Freq> = None;
        let mut interval: i64 = 1;
        let mut count: Option<u64> = None;
        let mut until: Option<NaiveDateTime> = None;
        let mut wkst: u32 = 0;
        let mut bymonth: Vec<u32> = Vec::new();
        let mut bymonthday: Vec<i32> = Vec::new();
        let mut byday: Vec<(Option<i32>, u32)> = Vec::new();
        let mut byhour: Vec<u32> = Vec::new();
        let mut byminute: Vec<u32> = Vec::new();
        let mut bysecond: Vec<u32> = Vec::new();
        let mut bysetpos: Vec<i32> = Vec::new();

        for part in body.split(';') {
            let part = part.trim();
            if part.is_empty() {
                continue;
            }
            let (key, val) = part.split_once('=')?;
            let key = key.trim().to_ascii_uppercase();
            let val = val.trim();
            match key.as_str() {
                "FREQ" => {
                    freq = Some(match val.to_ascii_uppercase().as_str() {
                        "DAILY" => Freq::Daily,
                        "WEEKLY" => Freq::Weekly,
                        "MONTHLY" => Freq::Monthly,
                        "YEARLY" => Freq::Yearly,
                        // HOURLY / MINUTELY / SECONDLY aren't emitted by calendar
                        // clients here; treat as unsupported.
                        _ => return None,
                    });
                }
                "INTERVAL" => {
                    interval = val.parse::<i64>().ok()?;
                    if interval < 1 {
                        return None;
                    }
                }
                "COUNT" => {
                    count = Some(val.parse::<u64>().ok()?);
                }
                "UNTIL" => {
                    until = Some(parse_until(val)?);
                }
                "WKST" => {
                    wkst = weekday_code(val)?;
                }
                "BYMONTH" => {
                    for v in val.split(',') {
                        bymonth.push(v.trim().parse::<u32>().ok()?);
                    }
                }
                "BYMONTHDAY" => {
                    for v in val.split(',') {
                        bymonthday.push(v.trim().parse::<i32>().ok()?);
                    }
                }
                "BYDAY" => {
                    for v in val.split(',') {
                        byday.push(parse_byday(v.trim())?);
                    }
                }
                "BYHOUR" => {
                    for v in val.split(',') {
                        byhour.push(v.trim().parse::<u32>().ok()?);
                    }
                }
                "BYMINUTE" => {
                    for v in val.split(',') {
                        byminute.push(v.trim().parse::<u32>().ok()?);
                    }
                }
                "BYSECOND" => {
                    for v in val.split(',') {
                        bysecond.push(v.trim().parse::<u32>().ok()?);
                    }
                }
                "BYSETPOS" => {
                    for v in val.split(',') {
                        bysetpos.push(v.trim().parse::<i32>().ok()?);
                    }
                }
                // BYYEARDAY / BYWEEKNO / BYEASTER aren't emitted by the calendar
                // clients here; ignore unknown keys rather than failing so a
                // benign extension doesn't collapse the whole series.
                _ => {}
            }
        }

        let freq = freq?;
        Some(RecurRule {
            dtstart,
            freq,
            interval,
            count,
            until,
            wkst,
            bymonth,
            bymonthday,
            byday,
            byhour,
            byminute,
            bysecond,
            bysetpos,
        })
    }

    /// `rule.between(after, before, inc=True)` — occurrences with
    /// `after <= occ <= before`, in chronological order.
    fn between(&self, after: NaiveDateTime, before: NaiveDateTime) -> Vec<NaiveDateTime> {
        let mut out: Vec<NaiveDateTime> = Vec::new();
        let mut emitted: u64 = 0;
        // Bound the walk: dateutil relies on `before` for an unbounded rule. Cap
        // the number of generated periods so a far-future `before` can't spin.
        // 200k periods covers a daily rule across ~500 years.
        let max_periods: u64 = 200_000;
        let mut period_anchor = self.dtstart;

        for _ in 0..max_periods {
            // Build the matching set of datetimes for this period, in order.
            let mut set = self.period_set(period_anchor);
            // BYSETPOS selects from the period's ordered set.
            if !self.bysetpos.is_empty() {
                set = apply_setpos(&set, &self.bysetpos);
            }
            for occ in set {
                // dateutil only yields occurrences >= dtstart.
                if occ < self.dtstart {
                    continue;
                }
                if let Some(u) = self.until {
                    if occ > u {
                        return out;
                    }
                }
                if let Some(c) = self.count {
                    if emitted >= c {
                        return out;
                    }
                }
                emitted += 1;
                // inc=True on both ends.
                if occ >= after && occ <= before {
                    out.push(occ);
                }
            }

            // Advance to the next period.
            let next = match self.advance(period_anchor) {
                Some(n) => n,
                None => break,
            };
            // Termination: once a period's EARLIEST possible occurrence is past
            // `before`, no later period can contribute (periods + their internal
            // sets advance monotonically). For WEEKLY the set may reach back to
            // the week start (up to 6 days before the anchor) and BYMONTHDAY/
            // BYDAY sets span the whole month, so subtract a generous period span
            // before comparing — this never stops too early, only avoids spinning
            // an unbounded rule far past the requested window.
            let grace = match self.freq {
                Freq::Daily => ChronoDuration::days(0),
                Freq::Weekly => ChronoDuration::days(7),
                Freq::Monthly => ChronoDuration::days(31),
                Freq::Yearly => ChronoDuration::days(366),
            };
            if next - grace > before {
                break;
            }
            period_anchor = next;
        }
        out
    }

    /// Advance the period anchor by FREQ * INTERVAL (calendar arithmetic).
    fn advance(&self, anchor: NaiveDateTime) -> Option<NaiveDateTime> {
        match self.freq {
            Freq::Daily => Some(anchor + ChronoDuration::days(self.interval)),
            Freq::Weekly => Some(anchor + ChronoDuration::weeks(self.interval)),
            Freq::Monthly => add_months(anchor, self.interval),
            Freq::Yearly => add_months(anchor, self.interval * 12),
        }
    }

    /// The ordered set of candidate datetimes within the period starting at
    /// `anchor`, after applying the BY* rules (excluding BYSETPOS).
    fn period_set(&self, anchor: NaiveDateTime) -> Vec<NaiveDateTime> {
        // Candidate DATES for this period.
        let dates: Vec<NaiveDate> = match self.freq {
            Freq::Daily => self.daily_dates(anchor),
            Freq::Weekly => self.weekly_dates(anchor),
            Freq::Monthly => self.monthly_dates(anchor),
            Freq::Yearly => self.yearly_dates(anchor),
        };
        // Expand each date across the time-of-day set (BYHOUR/BYMINUTE/BYSECOND
        // default to the DTSTART's components).
        let hours: Vec<u32> = if self.byhour.is_empty() {
            vec![self.dtstart.hour()]
        } else {
            let mut h = self.byhour.clone();
            h.sort_unstable();
            h
        };
        let minutes: Vec<u32> = if self.byminute.is_empty() {
            vec![self.dtstart.minute()]
        } else {
            let mut m = self.byminute.clone();
            m.sort_unstable();
            m
        };
        let seconds: Vec<u32> = if self.bysecond.is_empty() {
            vec![self.dtstart.second()]
        } else {
            let mut s = self.bysecond.clone();
            s.sort_unstable();
            s
        };
        let mut out: Vec<NaiveDateTime> = Vec::new();
        for d in dates {
            // BYMONTH filter (applies to every freq once a date is chosen).
            if !self.bymonth.is_empty() && !self.bymonth.contains(&d.month()) {
                continue;
            }
            for &h in &hours {
                for &mi in &minutes {
                    for &s in &seconds {
                        if let Some(dt) = d.and_hms_opt(h, mi, s) {
                            out.push(dt);
                        }
                    }
                }
            }
        }
        out.sort_unstable();
        out.dedup();
        out
    }

    /// DAILY: the single anchor date, filtered by BYDAY / BYMONTHDAY when given.
    fn daily_dates(&self, anchor: NaiveDateTime) -> Vec<NaiveDate> {
        let d = anchor.date();
        if !self.byday.is_empty() {
            let wd = weekday_index(d);
            if !self.byday.iter().any(|(ord, w)| ord.is_none() && *w == wd) {
                return Vec::new();
            }
        }
        if !self.bymonthday.is_empty() && !month_day_matches(d, &self.bymonthday) {
            return Vec::new();
        }
        vec![d]
    }

    /// WEEKLY: the 7 days of the week containing `anchor` (week starting at WKST),
    /// filtered to BYDAY (default = the DTSTART weekday).
    fn weekly_dates(&self, anchor: NaiveDateTime) -> Vec<NaiveDate> {
        let anchor_date = anchor.date();
        // Start of the week the anchor falls in, honoring WKST.
        let anchor_wd = weekday_index(anchor_date);
        let back = ((anchor_wd + 7 - self.wkst) % 7) as i64;
        let week_start = anchor_date - ChronoDuration::days(back);
        // Weekdays to emit: BYDAY (ordinals ignored for WEEKLY) or DTSTART's wd.
        let wanted: Vec<u32> = if self.byday.is_empty() {
            vec![weekday_index(self.dtstart.date())]
        } else {
            self.byday.iter().map(|(_, w)| *w).collect()
        };
        let mut out = Vec::new();
        for offset in 0..7i64 {
            let d = week_start + ChronoDuration::days(offset);
            let wd = weekday_index(d);
            if wanted.contains(&wd)
                && (self.bymonthday.is_empty() || month_day_matches(d, &self.bymonthday))
            {
                out.push(d);
            }
        }
        out
    }

    /// MONTHLY: days of the anchor month selected by BYMONTHDAY and/or BYDAY,
    /// defaulting to the DTSTART day-of-month.
    fn monthly_dates(&self, anchor: NaiveDateTime) -> Vec<NaiveDate> {
        let year = anchor.year();
        let month = anchor.month();
        let ndays = days_in_month(year, month);
        let mut days: Vec<u32> = Vec::new();

        if self.bymonthday.is_empty() && self.byday.is_empty() {
            // Default: the DTSTART day-of-month (skip months without that day,
            // matching dateutil).
            let dom = self.dtstart.day();
            if dom <= ndays {
                days.push(dom);
            }
        } else {
            if !self.bymonthday.is_empty() {
                for &md in &self.bymonthday {
                    if let Some(day) = resolve_monthday(md, ndays) {
                        days.push(day);
                    }
                }
            }
            if !self.byday.is_empty() {
                for &(ord, wd) in &self.byday {
                    days.extend(byday_in_month(year, month, ord, wd));
                }
            }
        }
        days.sort_unstable();
        days.dedup();
        days.into_iter()
            .filter_map(|d| NaiveDate::from_ymd_opt(year, month, d))
            .collect()
    }

    /// YEARLY: anchored on the DTSTART month/day by default; BYMONTH + BYDAY /
    /// BYMONTHDAY refine the set across the anchor year.
    fn yearly_dates(&self, anchor: NaiveDateTime) -> Vec<NaiveDate> {
        let year = anchor.year();
        // Months in scope: BYMONTH, else the DTSTART month.
        let months: Vec<u32> = if self.bymonth.is_empty() {
            vec![self.dtstart.month()]
        } else {
            let mut m = self.bymonth.clone();
            m.sort_unstable();
            m
        };
        let mut out: Vec<NaiveDate> = Vec::new();
        for &month in &months {
            let ndays = days_in_month(year, month);
            if self.bymonthday.is_empty() && self.byday.is_empty() {
                // Default: DTSTART day-of-month within this month.
                let dom = self.dtstart.day();
                if dom <= ndays {
                    if let Some(d) = NaiveDate::from_ymd_opt(year, month, dom) {
                        out.push(d);
                    }
                }
            } else {
                if !self.bymonthday.is_empty() {
                    for &md in &self.bymonthday {
                        if let Some(day) = resolve_monthday(md, ndays) {
                            if let Some(d) = NaiveDate::from_ymd_opt(year, month, day) {
                                out.push(d);
                            }
                        }
                    }
                }
                if !self.byday.is_empty() {
                    for &(ord, wd) in &self.byday {
                        for day in byday_in_month(year, month, ord, wd) {
                            if let Some(d) = NaiveDate::from_ymd_opt(year, month, day) {
                                out.push(d);
                            }
                        }
                    }
                }
            }
        }
        out.sort_unstable();
        out.dedup();
        out
    }
}

/// Apply BYSETPOS to an ordered set: 1-based from the front, -1-based from the
/// back. Out-of-range positions are dropped (dateutil behavior).
fn apply_setpos(set: &[NaiveDateTime], setpos: &[i32]) -> Vec<NaiveDateTime> {
    let n = set.len() as i32;
    let mut idxs: Vec<usize> = Vec::new();
    for &p in setpos {
        let idx = if p > 0 {
            p - 1
        } else if p < 0 {
            n + p
        } else {
            continue;
        };
        if idx >= 0 && idx < n {
            idxs.push(idx as usize);
        }
    }
    idxs.sort_unstable();
    idxs.dedup();
    idxs.into_iter().map(|i| set[i]).collect()
}

/// `UNTIL` value parser: `YYYYMMDD` or `YYYYMMDDTHHMMSS` (the trailing `Z` has
/// already been stripped by the caller's UTC normalization).
fn parse_until(val: &str) -> Option<NaiveDateTime> {
    let v = val.trim().trim_end_matches(['Z', 'z']);
    if let Ok(dt) = NaiveDateTime::parse_from_str(v, "%Y%m%dT%H%M%S") {
        return Some(dt);
    }
    if let Ok(d) = NaiveDate::parse_from_str(v, "%Y%m%d") {
        // A date-only UNTIL bounds the whole day; dateutil compares the naive
        // datetime, so use end-of-day to include same-day occurrences.
        return d.and_hms_opt(23, 59, 59);
    }
    // Also tolerate ISO with separators (some stores keep them).
    NaiveDateTime::parse_from_str(v, "%Y-%m-%dT%H:%M:%S").ok()
}

/// `BYDAY` token: optional signed ordinal prefix + 2-letter weekday code, e.g.
/// `MO`, `1MO`, `-1SU`, `+2WE`.
fn parse_byday(tok: &str) -> Option<(Option<i32>, u32)> {
    let t = tok.trim();
    if t.len() < 2 {
        return None;
    }
    let code = &t[t.len() - 2..];
    let wd = weekday_code(code)?;
    let prefix = &t[..t.len() - 2];
    if prefix.is_empty() {
        Some((None, wd))
    } else {
        let ord = prefix.parse::<i32>().ok()?;
        if ord == 0 {
            return None;
        }
        Some((Some(ord), wd))
    }
}

/// Two-letter iCalendar weekday code -> index (0 = Monday).
fn weekday_code(code: &str) -> Option<u32> {
    match code.trim().to_ascii_uppercase().as_str() {
        "MO" => Some(0),
        "TU" => Some(1),
        "WE" => Some(2),
        "TH" => Some(3),
        "FR" => Some(4),
        "SA" => Some(5),
        "SU" => Some(6),
        _ => None,
    }
}

/// Weekday index for a date (0 = Monday), matching the iCalendar convention.
fn weekday_index(d: NaiveDate) -> u32 {
    d.weekday().num_days_from_monday()
}

/// Days in a (year, month).
fn days_in_month(year: i32, month: u32) -> u32 {
    let (ny, nm) = if month == 12 { (year + 1, 1) } else { (year, month + 1) };
    let first_next = NaiveDate::from_ymd_opt(ny, nm, 1).unwrap();
    let first_this = NaiveDate::from_ymd_opt(year, month, 1).unwrap();
    (first_next - first_this).num_days() as u32
}

/// Resolve a (possibly negative) BYMONTHDAY against the month length. Returns a
/// 1-based day-of-month, or `None` when out of range.
fn resolve_monthday(md: i32, ndays: u32) -> Option<u32> {
    if md > 0 && (md as u32) <= ndays {
        Some(md as u32)
    } else if md < 0 {
        let day = ndays as i32 + 1 + md; // -1 => last day
        if day >= 1 && (day as u32) <= ndays {
            Some(day as u32)
        } else {
            None
        }
    } else {
        None
    }
}

/// True iff `d`'s day-of-month satisfies one of the BYMONTHDAY entries.
fn month_day_matches(d: NaiveDate, bymonthday: &[i32]) -> bool {
    let ndays = days_in_month(d.year(), d.month());
    bymonthday
        .iter()
        .any(|&md| resolve_monthday(md, ndays) == Some(d.day()))
}

/// The day-of-month(s) in (year, month) matching a BYDAY (ordinal, weekday).
/// `ordinal = None` => every matching weekday in the month; positive => Nth from
/// the start; negative => Nth from the end.
fn byday_in_month(year: i32, month: u32, ordinal: Option<i32>, wd: u32) -> Vec<u32> {
    let ndays = days_in_month(year, month);
    let matches: Vec<u32> = (1..=ndays)
        .filter(|&day| {
            NaiveDate::from_ymd_opt(year, month, day)
                .map(|d| weekday_index(d) == wd)
                .unwrap_or(false)
        })
        .collect();
    match ordinal {
        None => matches,
        Some(o) if o > 0 => matches
            .get((o - 1) as usize)
            .copied()
            .into_iter()
            .collect(),
        Some(o) if o < 0 => {
            let idx = matches.len() as i32 + o;
            if idx >= 0 {
                matches.get(idx as usize).copied().into_iter().collect()
            } else {
                Vec::new()
            }
        }
        _ => Vec::new(),
    }
}

/// Add `months` calendar months to a datetime, clamping the day to the target
/// month's length (matching `dateutil.relativedelta` semantics used by rrule's
/// month stepping). Time-of-day is preserved.
fn add_months(dt: NaiveDateTime, months: i64) -> Option<NaiveDateTime> {
    let total = (dt.year() as i64) * 12 + (dt.month() as i64 - 1) + months;
    let year = (total.div_euclid(12)) as i32;
    let month = (total.rem_euclid(12)) as u32 + 1;
    let ndays = days_in_month(year, month);
    let day = dt.day().min(ndays);
    NaiveDate::from_ymd_opt(year, month, day)?.and_hms_opt(dt.hour(), dt.minute(), dt.second())
}

/// `POST /api/calendar/events` — create an event.
async fn create_event(
    user: Option<Extension<CurrentUser>>,
    Json(data): Json<EventCreate>,
) -> Result<Response, HttpException> {
    let owner = require_user(user.as_deref())?;
    let conn = session()?;

    // Resolve the target calendar.
    let cal_id: String = if let Some(href) = data.calendar_href.as_deref().filter(|h| !h.is_empty()) {
        let cal: Option<(Option<String>,)> = conn
            .query_row("SELECT owner FROM calendars WHERE id = ?1", [href], |r| {
                Ok((r.get::<_, Option<String>>(0)?,))
            })
            .optional()
            .map_err(|_| HttpException::new(500, "Failed to create event"))?;
        match cal {
            Some((cal_owner,)) => {
                // if cal and (cal.owner is None or cal.owner != owner): raise 404
                match cal_owner {
                    None => return Err(HttpException::new(404, "Calendar not found")),
                    Some(o) if o != owner => return Err(HttpException::new(404, "Calendar not found")),
                    _ => href.to_string(),
                }
            }
            // cal is None -> fall through to default calendar
            None => ensure_default_calendar(&conn, &owner)
                .map_err(|_| HttpException::new(500, "Failed to create event"))?,
        }
    } else {
        ensure_default_calendar(&conn, &owner)
            .map_err(|_| HttpException::new(500, "Failed to create event"))?
    };

    let uid = uuid::Uuid::new_v4().to_string();
    // dtstart, _is_utc = _parse_dt_pair(data.dtstart)
    let (dtstart, mut is_utc) = match parse_dt_pair(&data.dtstart) {
        Ok(p) => p,
        Err(e) => {
            crate::pylog::error(&format!("Failed to create event: {e}"));
            return Err(HttpException::new(500, "Failed to create event"));
        }
    };
    let dtend: NaiveDateTime = if let Some(de) = data.dtend.as_deref().filter(|s| !s.is_empty()) {
        match parse_dt_pair(de) {
            Ok((d, end_utc)) => {
                is_utc = is_utc || end_utc;
                d
            }
            Err(e) => {
                crate::pylog::error(&format!("Failed to create event: {e}"));
                return Err(HttpException::new(500, "Failed to create event"));
            }
        }
    } else if data.all_day {
        dtstart + ChronoDuration::days(1)
    } else {
        dtstart + ChronoDuration::hours(1)
    };

    let row_is_utc = is_utc && !data.all_day;
    let rrule = data.rrule.clone().unwrap_or_default();
    // color = data.color or None
    let color = data.color.as_deref().filter(|c| !c.is_empty()).map(str::to_string);
    let now = pydatetime::utcnow_naive_iso();

    let res = conn.execute(
        "INSERT INTO calendar_events \
           (uid, calendar_id, summary, description, location, dtstart, dtend, all_day, is_utc, \
            rrule, color, status, importance, created_at, updated_at) \
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, 'confirmed', 'normal', ?12, ?12)",
        rusqlite::params![
            uid,
            cal_id,
            data.summary,
            data.description,
            data.location,
            dtstart.format(SQLITE_DT_FMT).to_string(),
            dtend.format(SQLITE_DT_FMT).to_string(),
            data.all_day,
            row_is_utc,
            rrule,
            color,
            now,
        ],
    );
    if let Err(e) = res {
        crate::pylog::error(&format!("Failed to create event: {e}"));
        return Err(HttpException::new(500, "Failed to create event"));
    }

    // `if cal.source == "caldav":` — push the new event to the remote so it
    // appears on the user's other devices (the sync is otherwise pull-only,
    // #800). Capture the calendar source while the connection is open, then
    // drop the (non-Send) connection BEFORE the `.await` so the future stays
    // Send. The remote push is best-effort: `writeback_event` never raises and
    // its result is discarded — the local create has already succeeded.
    let cal_source: String = conn
        .query_row("SELECT source FROM calendars WHERE id = ?1", [&cal_id], |r| {
            r.get::<_, Option<String>>(0)
        })
        .optional()
        .ok()
        .flatten()
        .flatten()
        .unwrap_or_default();
    drop(conn);

    if cal_source == "caldav" {
        let ev = json!({
            "uid": &uid,
            "summary": &data.summary,
            "description": &data.description,
            "location": &data.location,
            "dtstart": dtstart.format(SQLITE_DT_FMT).to_string(),
            "dtend": dtend.format(SQLITE_DT_FMT).to_string(),
            "all_day": data.all_day,
            "is_utc": row_is_utc,
            "rrule": &rrule,
        });
        let _ = crate::src::caldav_writeback::writeback_event(
            &owner, &cal_source, &cal_id, &ev, false,
        )
        .await;
    }
    Ok(Json(json!({"ok": true, "uid": uid})).into_response())
}

/// `PUT /api/calendar/events/:uid`.
async fn update_event(
    user: Option<Extension<CurrentUser>>,
    Path(uid): Path<String>,
    Json(data): Json<EventUpdate>,
) -> Result<Response, HttpException> {
    let owner = require_user(user.as_deref())?;
    let conn = session()?;
    get_or_404_event(&conn, &uid, &owner)?;

    // Load the row's CURRENT column values so dirty-tracking can mirror
    // SQLAlchemy: `ev.X = data.X` only marks the instance dirty when the new
    // value actually DIFFERS from the loaded one. The `onupdate=utcnow`
    // (TimestampMixin) hook fires at flush ONLY for a dirty instance, so an
    // all-None / empty body — OR a body that only re-sets fields to their
    // current values — is a pure no-op: no UPDATE, `updated_at` unchanged.
    // (The prior port unconditionally appended `updated_at = now` and always
    // ran the UPDATE, over-bumping `updated_at`.) Mirrors the note_routes
    // `update_note` dirty-tracking fix.
    // Scope the dynamic UPDATE builder so its `Vec<Box<dyn rusqlite::ToSql>>` (a
    // non-Send type) is fully dropped BEFORE the `.await` write-back below.
    // Otherwise that Vec lives across the await, making the handler future
    // non-Send and axum rejects `update_event` as a route handler.
    {
    let cur = match load_event_current(&conn, &uid) {
        Ok(c) => c,
        Err(e) => {
            crate::pylog::error(&format!("Failed to update event: {e}"));
            return Err(HttpException::new(500, "Failed to update event"));
        }
    };

    // Build the SET list incrementally (only the provided fields), mirroring the
    // per-field `if data.X is not None` mutations + the is_utc escalation rules.
    let mut sets: Vec<String> = Vec::new();
    let mut params: Vec<Box<dyn rusqlite::ToSql>> = Vec::new();
    let mut idx = 1usize;
    let mut dirty = false;
    let push = |sets: &mut Vec<String>, params: &mut Vec<Box<dyn rusqlite::ToSql>>, idx: &mut usize, col: &str, val: Box<dyn rusqlite::ToSql>| {
        sets.push(format!("{col} = ?{idx}"));
        params.push(val);
        *idx += 1;
    };

    if let Some(s) = &data.summary {
        // ev.summary current is non-nullable (default ""); compare strings.
        if cur.summary.as_deref().unwrap_or("") != s.as_str() {
            dirty = true;
        }
        push(&mut sets, &mut params, &mut idx, "summary", Box::new(s.clone()));
    }
    if let Some(s) = &data.description {
        if cur.description.as_deref().unwrap_or("") != s.as_str() {
            dirty = true;
        }
        push(&mut sets, &mut params, &mut idx, "description", Box::new(s.clone()));
    }
    if let Some(s) = &data.location {
        if cur.location.as_deref().unwrap_or("") != s.as_str() {
            dirty = true;
        }
        push(&mut sets, &mut params, &mut idx, "location", Box::new(s.clone()));
    }
    let mut escalate_utc = false;
    if let Some(s) = &data.dtstart {
        match parse_dt_pair(s) {
            Ok((dt, s_utc)) => {
                let stored = dt.format(SQLITE_DT_FMT).to_string();
                if dt_changed(&cur.dtstart, &stored) {
                    dirty = true;
                }
                push(&mut sets, &mut params, &mut idx, "dtstart", Box::new(stored));
                if s_utc {
                    escalate_utc = true;
                }
            }
            Err(e) => {
                crate::pylog::error(&format!("Failed to update event: {e}"));
                return Err(HttpException::new(500, "Failed to update event"));
            }
        }
    }
    if let Some(s) = &data.dtend {
        match parse_dt_pair(s) {
            Ok((dt, e_utc)) => {
                let stored = dt.format(SQLITE_DT_FMT).to_string();
                if dt_changed(&cur.dtend, &stored) {
                    dirty = true;
                }
                push(&mut sets, &mut params, &mut idx, "dtend", Box::new(stored));
                if e_utc {
                    escalate_utc = true;
                }
            }
            Err(e) => {
                crate::pylog::error(&format!("Failed to update event: {e}"));
                return Err(HttpException::new(500, "Failed to update event"));
            }
        }
    }
    // is_utc net effect (Python sets the attribute, possibly twice; the LAST
    // write wins, so we compute the final value and write the column at most
    // once — SQLite rejects a duplicate column in one SET list).
    //   * `if data.all_day is not None: ... if data.all_day: ev.is_utc = False`
    //   * else the start/end branches only escalate to True.
    let all_day_set = data.all_day;
    let final_is_utc: Option<bool> = match all_day_set {
        // all-day stays date-only -> is_utc=False overrides any escalation.
        Some(true) => Some(false),
        // all_day explicitly False (or unset): escalate to True iff tz-aware.
        _ => {
            if escalate_utc {
                Some(true)
            } else {
                None
            }
        }
    };
    if let Some(v) = final_is_utc {
        // ev.is_utc = v — dirty only when it changes the current flag.
        if cur.is_utc != v {
            dirty = true;
        }
        push(&mut sets, &mut params, &mut idx, "is_utc", Box::new(v));
    }
    if let Some(all_day) = all_day_set {
        if cur.all_day != all_day {
            dirty = true;
        }
        push(&mut sets, &mut params, &mut idx, "all_day", Box::new(all_day));
    }
    if let Some(r) = &data.rrule {
        if cur.rrule.as_deref().unwrap_or("") != r.as_str() {
            dirty = true;
        }
        push(&mut sets, &mut params, &mut idx, "rrule", Box::new(r.clone()));
    }
    if let Some(c) = &data.color {
        // ev.color = data.color if data.color else None
        let v: Option<String> = if c.is_empty() { None } else { Some(c.clone()) };
        if cur.color != v {
            dirty = true;
        }
        push(&mut sets, &mut params, &mut idx, "color", Box::new(v));
    }

    // Run the UPDATE only when something actually changed: a pure no-op body
    // leaves `updated_at` untouched (SQLAlchemy onupdate fires only for a dirty
    // instance). NOTE: this dirty gate governs ONLY the local UPDATE — the
    // CalDAV write-back below still runs for a caldav calendar even on a no-op,
    // matching the Python, which pushes the current event state to the remote
    // unconditionally after `db.commit()`.
    if dirty {
        // Bump updated_at (SQLAlchemy onupdate hook fires only for a dirty instance).
        push(
            &mut sets,
            &mut params,
            &mut idx,
            "updated_at",
            Box::new(pydatetime::utcnow_naive_iso()),
        );
        let sql = format!(
            "UPDATE calendar_events SET {} WHERE uid = ?{idx}",
            sets.join(", ")
        );
        params.push(Box::new(uid.clone()));
        let param_refs: Vec<&dyn rusqlite::ToSql> = params.iter().map(|b| b.as_ref()).collect();
        if let Err(e) = conn.execute(&sql, param_refs.as_slice()) {
            crate::pylog::error(&format!("Failed to update event: {e}"));
            return Err(HttpException::new(500, "Failed to update event"));
        }
    }
    } // end non-Send UPDATE-builder scope

    // `cal = db.query(CalendarCal).filter(id == ev.calendar_id).first()`
    // `if cal and cal.source == "caldav": writeback_event(...)`. Read the
    // calendar source and the (now-mutated) event state while the connection is
    // open, then DROP the non-Send connection before the `.await`. The remote
    // push is best-effort and its result is discarded — the local update has
    // already succeeded.
    let (cal_source, ev_state) = {
        let final_row = load_event_for_writeback(&conn, &uid);
        let source: String = conn
            .query_row(
                "SELECT c.source FROM calendar_events e JOIN calendars c ON e.calendar_id = c.id \
                 WHERE e.uid = ?1",
                [&uid],
                |r| r.get::<_, Option<String>>(0),
            )
            .optional()
            .ok()
            .flatten()
            .flatten()
            .unwrap_or_default();
        (source, final_row)
    };
    drop(conn);

    if cal_source == "caldav" {
        if let Some(ev) = ev_state {
            let _ = crate::src::caldav_writeback::writeback_event(
                &owner, &cal_source, &ev.0, &ev.1, false,
            )
            .await;
        }
    }
    Ok(Json(json!({"ok": true})).into_response())
}

/// Load the event's final state (calendar id + the `ev` JSON dict CalDAV
/// write-back needs) after an update. Returns `(calendar_id, ev_json)`, or
/// `None` if the row vanished (should not happen — `get_or_404_event` proved it
/// exists). The datetimes are emitted in the SQLite store format, which
/// `LocalEvent::from_json` accepts.
fn load_event_for_writeback(
    conn: &rusqlite::Connection,
    uid: &str,
) -> Option<(String, Value)> {
    conn.query_row(
        "SELECT uid, summary, description, location, dtstart, dtend, all_day, is_utc, rrule, calendar_id \
         FROM calendar_events WHERE uid = ?1",
        [uid],
        |r| {
            let uid: String = r.get(0)?;
            let summary: Option<String> = r.get(1)?;
            let description: Option<String> = r.get(2)?;
            let location: Option<String> = r.get(3)?;
            let dtstart: String = r.get(4)?;
            let dtend: String = r.get(5)?;
            let all_day: bool = r.get::<_, Option<bool>>(6)?.unwrap_or(false);
            let is_utc: bool = r.get::<_, Option<bool>>(7)?.unwrap_or(false);
            let rrule: Option<String> = r.get(8)?;
            let calendar_id: String = r.get(9)?;
            Ok((
                calendar_id,
                json!({
                    "uid": uid,
                    "summary": summary.unwrap_or_default(),
                    "description": description.unwrap_or_default(),
                    "location": location.unwrap_or_default(),
                    "dtstart": dtstart,
                    "dtend": dtend,
                    "all_day": all_day,
                    "is_utc": is_utc,
                    "rrule": rrule.unwrap_or_default(),
                }),
            ))
        },
    )
    .optional()
    .ok()
    .flatten()
}

/// The current column values `update_event` compares its incoming fields against
/// for dirty-tracking. Mirrors the loaded `CalendarEvent` instance attributes.
struct EventCurrent {
    summary: Option<String>,
    description: Option<String>,
    location: Option<String>,
    dtstart: String,
    dtend: String,
    all_day: bool,
    is_utc: bool,
    rrule: Option<String>,
    color: Option<String>,
}

/// Load the row's current values (the event is already proven to exist + be
/// owned by `get_or_404_event`).
fn load_event_current(conn: &rusqlite::Connection, uid: &str) -> rusqlite::Result<EventCurrent> {
    conn.query_row(
        "SELECT summary, description, location, dtstart, dtend, all_day, is_utc, rrule, color \
         FROM calendar_events WHERE uid = ?1",
        [uid],
        |r| {
            Ok(EventCurrent {
                summary: r.get(0)?,
                description: r.get(1)?,
                location: r.get(2)?,
                dtstart: r.get(3)?,
                dtend: r.get(4)?,
                all_day: r.get::<_, Option<bool>>(5)?.unwrap_or(false),
                is_utc: r.get::<_, Option<bool>>(6)?.unwrap_or(false),
                rrule: r.get(7)?,
                color: r.get(8)?,
            })
        },
    )
}

/// Whether a freshly-computed DATETIME store-string differs from the currently
/// stored one. Compares the parsed instants so cosmetic format differences
/// (e.g. trailing `.000000`) don't spuriously mark the row dirty; falls back to
/// a raw string compare when either side won't parse.
fn dt_changed(current: &str, new_stored: &str) -> bool {
    match (parse_stored(current), parse_stored(new_stored)) {
        (Some(a), Some(b)) => a != b,
        _ => current != new_stored,
    }
}

/// `DELETE /api/calendar/events/:uid`.
async fn delete_event(
    user: Option<Extension<CurrentUser>>,
    Path(uid): Path<String>,
) -> Result<Response, HttpException> {
    let owner = require_user(user.as_deref())?;
    let conn = session()?;
    get_or_404_event(&conn, &uid, &owner)?;

    // Capture what the remote push needs BEFORE the row is gone: the event's
    // calendar id, its calendar source (to gate the push on "caldav"), and the
    // event uid. `_cal = db.query(...); _is_caldav = bool(_cal and _cal.source
    // == "caldav"); _cal_id, _ev_uid = ev.calendar_id, ev.uid`.
    let captured: Option<(String, String, String)> = conn
        .query_row(
            "SELECT e.calendar_id, c.source, e.uid \
             FROM calendar_events e JOIN calendars c ON e.calendar_id = c.id \
             WHERE e.uid = ?1",
            [&uid],
            |r| {
                Ok((
                    r.get::<_, String>(0)?,
                    r.get::<_, Option<String>>(1)?.unwrap_or_default(),
                    r.get::<_, String>(2)?,
                ))
            },
        )
        .optional()
        .ok()
        .flatten();

    if let Err(e) = conn.execute("DELETE FROM calendar_events WHERE uid = ?1", [&uid]) {
        crate::pylog::error(&format!("Failed to delete event: {e}"));
        return Err(HttpException::new(500, "Failed to delete event"));
    }
    // Drop the non-Send connection BEFORE awaiting the write-back so the future
    // stays Send. The remote DELETE is best-effort and its result is discarded
    // — the local delete has already succeeded.
    drop(conn);

    if let Some((cal_id, source, ev_uid)) = captured {
        if source == "caldav" {
            let _ = crate::src::caldav_writeback::writeback_event(
                &owner,
                "caldav",
                &cal_id,
                &json!({"uid": ev_uid}),
                true,
            )
            .await;
        }
    }
    Ok(Json(json!({"ok": true})).into_response())
}

// ===========================================================================
// ICS import / export
// ===========================================================================

/// `POST /api/calendar/import` — import events from an `.ics` upload.
async fn import_ics(
    user: Option<Extension<CurrentUser>>,
    mut mp: Multipart,
) -> Result<Response, HttpException> {
    let owner = require_user(user.as_deref())?;

    // Parse the multipart form manually so we can apply the bounded read on the
    // file field via `read_upload_limited` (upstream 193dc2f). Other text fields
    // (`calendar_name`) are read normally — only the file upload is bounded.
    let mut calendar_name = String::new();
    let mut file_name: Option<String> = None;
    let mut content: Vec<u8> = Vec::new();
    while let Ok(Some(field)) = mp.next_field().await {
        let name = field.name().unwrap_or("").to_string();
        if name == "file" {
            file_name = field.file_name().map(str::to_string);
            // content = await read_upload_limited(file, _ICS_MAX_BYTES, "ICS file")
            content = crate::src::upload_limits::read_upload_limited(
                field,
                ICS_MAX_BYTES,
                "ICS file",
            )
            .await?;
        } else if name == "calendar_name" {
            if let Ok(text) = field.text().await {
                calendar_name = text;
            }
        }
    }
    let text = String::from_utf8_lossy(&content).into_owned();
    // cal_data = iCal.from_ical(content) — parse error -> 400.
    let unfolded = icalendar::parser::unfold(&text);
    let parsed = match icalendar::parser::read_calendar(&unfolded) {
        Ok(p) => p,
        Err(e) => return Err(HttpException::new(400, format!("Invalid ICS file: {e}"))),
    };
    let cal_data: icalendar::Calendar = parsed.into();

    // Sanitize display name.
    let raw_name = {
        let cn = calendar_name.trim();
        if !cn.is_empty() {
            cn.to_string()
        } else {
            // (file.filename or "").replace(".ics","").replace("_"," ").strip() or "Imported"
            let fname = file_name.unwrap_or_default();
            let cleaned = fname.replace(".ics", "").replace('_', " ");
            let cleaned = cleaned.trim();
            if cleaned.is_empty() {
                "Imported".to_string()
            } else {
                cleaned.to_string()
            }
        }
    };
    // "".join(c for c in raw_name if c.isprintable())[:120] or "Imported"
    let printable: String = raw_name.chars().filter(|c| is_printable(*c)).collect();
    let cal_display = {
        let truncated: String = printable.chars().take(120).collect();
        if truncated.is_empty() {
            "Imported".to_string()
        } else {
            truncated
        }
    };

    let conn = session()?;

    let result = import_ics_inner(&conn, &owner, &cal_data, &cal_display);
    match result {
        Ok((imported, skipped, target_id)) => Ok(Json(json!({
            "ok": true,
            "imported": imported,
            "skipped": skipped,
            "calendar": cal_display,
            "calendar_id": target_id,
        }))
        .into_response()),
        Err(e) => {
            crate::pylog::error(&format!("Failed to import ICS: {e}"));
            Err(HttpException::new(500, "Failed to import ICS"))
        }
    }
}

/// The DB-touching body of `import_ics`, kept separate so a DB error maps to the
/// 500 the Python `except Exception` arm produces.
fn import_ics_inner(
    conn: &rusqlite::Connection,
    owner: &str,
    cal_data: &icalendar::Calendar,
    cal_display: &str,
) -> rusqlite::Result<(i64, i64, String)> {
    use icalendar::{Component, EventLike};

    // target_cal = db.query(...).filter(name == cal_display, owner == owner).first()
    let target_id: String = {
        let existing: Option<String> = conn
            .query_row(
                "SELECT id FROM calendars WHERE name = ?1 AND owner = ?2",
                rusqlite::params![cal_display, owner],
                |r| r.get(0),
            )
            .optional()?;
        match existing {
            Some(id) => id,
            None => {
                let id = uuid::Uuid::new_v4().to_string();
                let now = pydatetime::utcnow_naive_iso();
                conn.execute(
                    "INSERT INTO calendars (id, owner, name, color, source, created_at, updated_at) \
                     VALUES (?1, ?2, ?3, '#7c4dff', 'import', ?4, ?4)",
                    rusqlite::params![id, owner, cal_display, now],
                )?;
                id
            }
        }
    };

    let mut imported = 0i64;
    let mut skipped = 0i64;
    let now = pydatetime::utcnow_naive_iso();

    for comp in &cal_data.components {
        let event = match comp {
            icalendar::CalendarComponent::Event(ev) => ev,
            _ => continue,
        };
        // Fresh uid per import row.
        let uid_val = uuid::Uuid::new_v4().to_string();
        // dtstart = comp.get("dtstart"); if not dtstart: skipped += 1; continue
        let dtstart_p = match event.get_start() {
            Some(d) => d,
            None => {
                skipped += 1;
                continue;
            }
        };
        let start_when = parse_when(&dtstart_p);
        let summary = event.get_summary().unwrap_or("").to_string();

        // Dedup INSIDE this user's target calendar — and ONLY when the source
        // VEVENT carried a UID (`if source_uid:`). Same source-dtstart + summary
        // in the same target = duplicate. Python's `naive_src = src.replace(
        // tzinfo=None)` strips tz WITHOUT converting to UTC (keeping the wall
        // clock), and an all-day `date` is compared bare. The bind value is the
        // SQLite render of that — which only matches a stored row for
        // naive-source timed events; the tz-aware and all-day cases render a
        // value that never equals the UTC-normalized / midnight-datetime stored
        // row, exactly reproducing the Python dedup's effective behavior.
        let source_uid = event
            .get_uid()
            .map(|s| s.to_string())
            .filter(|s| !s.is_empty());
        if source_uid.is_some() {
            let src_store = match dedup_wall_clock(&dtstart_p) {
                WallClock::Date(d) => d.format("%Y-%m-%d").to_string(),
                WallClock::DateTime(dt) => dt.format(SQLITE_DT_FMT).to_string(),
            };
            let exists: bool = conn
                .query_row(
                    "SELECT 1 FROM calendar_events WHERE calendar_id = ?1 AND dtstart = ?2 AND summary = ?3",
                    rusqlite::params![target_id, src_store, summary],
                    |_| Ok(true),
                )
                .optional()?
                .unwrap_or(false);
            if exists {
                skipped += 1;
                continue;
            }
        }

        let all_day = matches!(start_when, IcalWhen::Date(_));
        let mut row_is_utc = false;
        let (start_dt, end_dt) = if all_day {
            // start = datetime(y,m,d); end = datetime(end) or start + 1 day
            let start = match start_when {
                IcalWhen::Date(d) => d.and_hms_opt(0, 0, 0).unwrap(),
                // Unreachable: `all_day` is true iff start_when is Date.
                other => to_naive(other).0,
            };
            let end = match event.get_end() {
                // Python: end_dt = datetime(dtend.dt.year, dtend.dt.month, dtend.dt.day)
                // — TRUNCATES the DTEND to that date at midnight (drops any
                // time-of-day), whether the DTEND is a date or a datetime.
                Some(dtend_p) => {
                    let d = match parse_when(&dtend_p) {
                        IcalWhen::Date(d) => d,
                        other => to_naive(other).0.date(),
                    };
                    d.and_hms_opt(0, 0, 0).unwrap()
                }
                None => start + ChronoDuration::days(1),
            };
            (start, end)
        } else {
            // timed: tz-aware -> UTC + is_utc, naive -> as-is.
            let start = match start_when {
                IcalWhen::Aware(dt) => {
                    row_is_utc = true;
                    dt
                }
                other => to_naive(other).0,
            };
            let end = match event.get_end() {
                Some(dtend_p) => match parse_when(&dtend_p) {
                    IcalWhen::Aware(dt) => dt,
                    other => to_naive(other).0,
                },
                None => start + ChronoDuration::hours(1),
            };
            (start, end)
        };

        let description = event.get_description().unwrap_or("").to_string();
        let location = event.get_location().unwrap_or("").to_string();
        let rrule = event
            .properties()
            .get("RRULE")
            .map(|p| p.value().to_string())
            .unwrap_or_default();

        conn.execute(
            "INSERT INTO calendar_events \
               (uid, calendar_id, summary, description, location, dtstart, dtend, all_day, is_utc, \
                rrule, status, importance, created_at, updated_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, 'confirmed', 'normal', ?11, ?11)",
            rusqlite::params![
                uid_val,
                target_id,
                summary,
                description,
                location,
                start_dt.format(SQLITE_DT_FMT).to_string(),
                end_dt.format(SQLITE_DT_FMT).to_string(),
                all_day,
                row_is_utc,
                rrule,
                now,
            ],
        )?;
        imported += 1;
    }

    Ok((imported, skipped, target_id))
}

/// `str.isprintable()` — Unicode printable check (no control/format/separator
/// chars except ASCII space).
fn is_printable(c: char) -> bool {
    if c == ' ' {
        return true;
    }
    !c.is_control() && !c.is_whitespace()
}

/// icalendar `DatePerhapsTime` -> our `IcalWhen` (mirrors `caldav_sync::parse_when`).
#[derive(Clone, Copy)]
enum IcalWhen {
    Date(NaiveDate),
    Aware(NaiveDateTime),
    Naive(NaiveDateTime),
}

fn parse_when(dpt: &icalendar::DatePerhapsTime) -> IcalWhen {
    use icalendar::CalendarDateTime as CDT;
    use icalendar::DatePerhapsTime as DPT;
    match dpt {
        DPT::Date(d) => IcalWhen::Date(*d),
        DPT::DateTime(cdt) => match cdt {
            CDT::Utc(dt) => IcalWhen::Aware(dt.naive_utc()),
            CDT::Floating(ndt) => IcalWhen::Naive(*ndt),
            CDT::WithTimezone { date_time, tzid } => match tzid.parse::<chrono_tz::Tz>() {
                Ok(tz) => match tz.from_local_datetime(date_time).single() {
                    Some(local) => IcalWhen::Aware(local.with_timezone(&Utc).naive_utc()),
                    None => IcalWhen::Naive(*date_time),
                },
                Err(_) => IcalWhen::Naive(*date_time),
            },
        },
    }
}

/// The source dtstart with tzinfo stripped (Python `replace(tzinfo=None)`) — wall
/// clock preserved, NO UTC conversion — for the import dedup key.
enum WallClock {
    Date(NaiveDate),
    DateTime(NaiveDateTime),
}

/// `src_dtstart.replace(tzinfo=None) if tzinfo else src_dtstart` — the dedup key.
fn dedup_wall_clock(dpt: &icalendar::DatePerhapsTime) -> WallClock {
    use icalendar::CalendarDateTime as CDT;
    use icalendar::DatePerhapsTime as DPT;
    match dpt {
        DPT::Date(d) => WallClock::Date(*d),
        DPT::DateTime(cdt) => match cdt {
            // tz-aware UTC: replace(tzinfo=None) -> the UTC wall clock (the value
            // chrono already holds for a `Z`-suffixed time).
            CDT::Utc(dt) => WallClock::DateTime(dt.naive_utc()),
            // floating/naive: stays as-is.
            CDT::Floating(ndt) => WallClock::DateTime(*ndt),
            // tz-aware with TZID: replace(tzinfo=None) keeps the LOCAL wall clock.
            CDT::WithTimezone { date_time, .. } => WallClock::DateTime(*date_time),
        },
    }
}

/// Reduce an `IcalWhen` to a naive datetime + all_day flag.
fn to_naive(when: IcalWhen) -> (NaiveDateTime, bool) {
    match when {
        IcalWhen::Aware(dt) => (dt, false),
        IcalWhen::Naive(dt) => (dt, false),
        IcalWhen::Date(d) => (d.and_hms_opt(0, 0, 0).unwrap(), true),
    }
}

/// `GET /api/calendar/export/:cal_id` — export a calendar as `.ics`.
async fn export_ics(
    user: Option<Extension<CurrentUser>>,
    Path(cal_id): Path<String>,
) -> Result<Response, HttpException> {
    let owner = require_user(user.as_deref())?;
    let conn = session()?;
    let cal = get_or_404_calendar(&conn, &cal_id, &owner)?;

    let result = (|| -> rusqlite::Result<String> {
        let mut lines: Vec<String> = vec![
            "BEGIN:VCALENDAR".to_string(),
            "VERSION:2.0".to_string(),
            "PRODID:-//Odysseus//Calendar//EN".to_string(),
            format!("X-WR-CALNAME:{}", cal.name),
        ];
        let mut stmt = conn.prepare(
            "SELECT uid, summary, all_day, dtstart, dtend, description, location, rrule \
             FROM calendar_events WHERE calendar_id = ?1 AND status != 'cancelled'",
        )?;
        let rows = stmt.query_map([&cal_id], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, Option<String>>(1)?,
                r.get::<_, Option<bool>>(2)?.unwrap_or(false),
                r.get::<_, String>(3)?,
                r.get::<_, String>(4)?,
                r.get::<_, Option<String>>(5)?,
                r.get::<_, Option<String>>(6)?,
                r.get::<_, Option<String>>(7)?,
            ))
        })?;
        for row in rows {
            let (uid, summary, all_day, dtstart, dtend, description, location, rrule) = row?;
            lines.push("BEGIN:VEVENT".to_string());
            lines.push(format!("UID:{uid}"));
            lines.push(format!("SUMMARY:{}", summary.unwrap_or_default()));
            if all_day {
                lines.push(format!("DTSTART;VALUE=DATE:{}", ics_date(&dtstart)));
                lines.push(format!("DTEND;VALUE=DATE:{}", ics_date(&dtend)));
            } else {
                lines.push(format!("DTSTART:{}", ics_datetime(&dtstart)));
                lines.push(format!("DTEND:{}", ics_datetime(&dtend)));
            }
            if let Some(desc) = description.filter(|d| !d.is_empty()) {
                lines.push(format!("DESCRIPTION:{}", desc.replace('\n', "\\n")));
            }
            if let Some(loc) = location.filter(|l| !l.is_empty()) {
                lines.push(format!("LOCATION:{loc}"));
            }
            if let Some(rr) = rrule.filter(|r| !r.is_empty()) {
                lines.push(format!("RRULE:{rr}"));
            }
            lines.push("END:VEVENT".to_string());
        }
        lines.push("END:VCALENDAR".to_string());
        Ok(lines.join("\r\n"))
    })();

    let ics_data = match result {
        Ok(d) => d,
        Err(e) => {
            crate::pylog::error(&format!("Failed to export ICS: {e}"));
            return Err(HttpException::new(500, "Failed to export ICS"));
        }
    };

    // safe_name = cal.name.replace(" ","_").replace("/","_")
    let safe_name = cal.name.replace([' ', '/'], "_");
    let resp = Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "text/calendar")
        .header(
            header::CONTENT_DISPOSITION,
            format!("attachment; filename=\"{safe_name}.ics\""),
        )
        .body(axum::body::Body::from(ics_data))
        .unwrap();
    Ok(resp)
}

// ===========================================================================
// quick-parse (LLM)
// ===========================================================================

static QP_IN_MIN_RE: Lazy<regex::Regex> = Lazy::new(|| {
    regex::Regex::new(r"(?i)\bin\s+\d+\s*(min|minute|hour|hr|day)s?\b").unwrap()
});
static QP_PAREN_TIME_RE: Lazy<regex::Regex> =
    Lazy::new(|| regex::Regex::new(r"\(\s*\d{1,2}:\d{2}\s*\)").unwrap());
static QP_AMPM_RE: Lazy<regex::Regex> =
    Lazy::new(|| regex::Regex::new(r"(?i)\b\d{1,2}(:\d{2})?\s*(am|pm)\b").unwrap());
// Python: `re.sub(r'\s+@\s+(?=\d)', ' ', summary)` — a lookahead `(?=\d)` that
// matches "<ws>@<ws>" only when a digit follows, WITHOUT consuming the digit.
// The `regex` crate has no look-around, so we capture the following digit and
// reinsert it via the replacement (" $1"), giving the identical observable
// substitution (`\s+@\s+` -> " ", digit preserved).
static QP_AT_TIME_RE: Lazy<regex::Regex> =
    Lazy::new(|| regex::Regex::new(r"\s+@\s+(\d)").unwrap());
static QP_WS_RE: Lazy<regex::Regex> = Lazy::new(|| regex::Regex::new(r"\s+").unwrap());
static QP_CODE_FENCE_RE: Lazy<regex::Regex> =
    Lazy::new(|| regex::Regex::new(r"(?m)^```(?:json)?\s*|\s*```$").unwrap());
static QP_JSON_RE: Lazy<regex::Regex> =
    Lazy::new(|| regex::Regex::new(r"(?s)\{.*\}").unwrap());

/// `POST /api/calendar/quick-parse`.
async fn quick_parse(
    user: Option<Extension<CurrentUser>>,
    body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    require_user(user.as_deref())?;
    // Python: `body = await request.json()` WITHOUT a try/except (unlike
    // /config /test /sync, which wrap it in `except Exception: body = {}`). A
    // malformed / non-JSON body therefore raises an uncaught `JSONDecodeError`
    // -> FastAPI's default handler -> 500. A body that parses but isn't a JSON
    // object makes the subsequent `body.get("text")` raise `AttributeError`
    // -> also 500. Reproduce both: any parse failure (or non-object) is a 500.
    let body: Value = serde_json::from_slice(&body)
        .map_err(|_| HttpException::new(500, "Internal Server Error"))?;
    if !body.is_object() {
        return Err(HttpException::new(500, "Internal Server Error"));
    }
    let text = body_get_str(&body, "text").trim().to_string();
    if text.is_empty() {
        return Err(HttpException::new(400, "text is required"));
    }
    let tz_hint = body_get_str(&body, "tz").trim().to_string();

    // url, model, headers = resolve_endpoint("utility") or resolve_endpoint("default")
    // Python calendar_routes.py:953/955 (quick_parse) is owner-less -> None.
    let endpoint = crate::src::endpoint_resolver::resolve_endpoint_triple("utility", None)
        .or_else(|| crate::src::endpoint_resolver::resolve_endpoint_triple("default", None));
    let (url, model, headers) = match endpoint {
        Some(e) => e,
        None => {
            return Ok(Json(json!({"ok": false, "error": "No LLM endpoint configured"})).into_response())
        }
    };

    let now = chrono::Local::now().naive_local();
    let now_iso = now.format("%Y-%m-%dT%H:%M:%S").to_string();
    // now.strftime('%A, %Y-%m-%d')
    let now_human = now.format("%A, %Y-%m-%d").to_string();
    let mut system_prompt = format!(
        "You are a calendar event parser. Read the user's one-line description and emit STRICT JSON describing the event. Today is {now_human} ({now_iso}). "
    );
    if !tz_hint.is_empty() {
        system_prompt.push_str(&format!("User timezone: {tz_hint}. "));
    }
    system_prompt.push_str(
        "Resolve relative dates (\"tomorrow\", \"friday\", \"next monday\", \"in 30 minutes\") against today. Default duration is 60 minutes when no end time is given. If the text mentions a date with no time, treat it as an all-day event.\n\n\
Output ONLY this JSON shape, nothing else:\n\
{\n\
  \"summary\": \"<event title, capitalized>\",\n\
  \"dtstart\": \"<YYYY-MM-DDTHH:MM:00>\",\n\
  \"dtend\":   \"<YYYY-MM-DDTHH:MM:00>\",\n\
  \"all_day\": <true|false>,\n\
  \"location\": \"<place or empty>\",\n\
  \"description\": \"\",\n\
  \"confidence\": <0.0-1.0>\n\
}\n\
For all-day events use \"YYYY-MM-DD\" (no time) for both fields.",
    );

    let messages = vec![
        json!({"role": "system", "content": system_prompt}),
        json!({"role": "user", "content": text}),
    ];
    let raw = match crate::src::llm_core::llm_call_async(
        &url,
        &model,
        messages,
        0.0,
        512,
        headers,
        20,
    )
    .await
    {
        Ok(r) => r,
        Err(e) => {
            return Ok(Json(json!({"ok": false, "error": format!("LLM call failed: {e}")})).into_response())
        }
    };

    // cleaned = strip_think(raw or "", prose=False, prompt_echo=True)
    let cleaned = crate::src::text_helpers::strip_think(&raw, false, true);
    let cleaned = QP_CODE_FENCE_RE.replace_all(&cleaned, "").trim().to_string();
    let m = match QP_JSON_RE.find(&cleaned) {
        Some(m) => m.as_str().to_string(),
        None => {
            return Ok(Json(json!({
                "ok": false,
                "error": "Could not extract JSON",
                "raw": truncate_chars(&cleaned, 400),
            }))
            .into_response())
        }
    };
    let parsed: Value = match serde_json::from_str(&m) {
        Ok(v) => v,
        Err(e) => {
            return Ok(Json(json!({
                "ok": false,
                "error": format!("Invalid JSON: {e}"),
                "raw": truncate_chars(&cleaned, 400),
            }))
            .into_response())
        }
    };

    // summary = (parsed.get("summary") or text)[:200]
    let summary_src = match parsed.get("summary").and_then(Value::as_str) {
        Some(s) if !s.is_empty() => s.to_string(),
        _ => text.clone(),
    };
    let mut summary: String = summary_src.chars().take(200).collect();
    summary = QP_IN_MIN_RE.replace_all(&summary, "").to_string();
    summary = QP_PAREN_TIME_RE.replace_all(&summary, "").to_string();
    summary = QP_AMPM_RE.replace_all(&summary, "").to_string();
    summary = QP_AT_TIME_RE.replace_all(&summary, " $1").to_string();
    summary = QP_WS_RE.replace_all(&summary, " ").to_string();
    summary = summary.trim_matches(|c| " -—,@".contains(c)).to_string();

    let all_day = parsed.get("all_day").map(value_truthy).unwrap_or(false);
    let dtstart = strip_tz(body_get_str_opt(&parsed, "dtstart").trim());
    let mut dtend = strip_tz(body_get_str_opt(&parsed, "dtend").trim());

    if dtstart.is_empty() {
        return Ok(Json(json!({
            "ok": false,
            "error": "Model did not produce a start time",
            "raw": truncate_chars(&cleaned, 400),
        }))
        .into_response());
    }
    if dtend.is_empty() {
        if all_day {
            dtend = dtstart.clone();
        } else {
            match NaiveDateTime::parse_from_str(&dtstart, "%Y-%m-%dT%H:%M:%S")
                .or_else(|_| NaiveDateTime::parse_from_str(&dtstart, "%Y-%m-%dT%H:%M"))
            {
                Ok(dt) => {
                    dtend = (dt + ChronoDuration::minutes(60))
                        .format("%Y-%m-%dT%H:%M:00")
                        .to_string();
                }
                Err(_) => dtend = dtstart.clone(),
            }
        }
    }

    let location: String = body_get_str_opt(&parsed, "location")
        .trim()
        .chars()
        .take(200)
        .collect();
    let description: String = body_get_str_opt(&parsed, "description")
        .trim()
        .chars()
        .take(2000)
        .collect();
    // confidence = float(parsed.get("confidence", 0.7) or 0.7)
    let confidence = match parsed.get("confidence") {
        Some(v) if value_truthy(v) => v.as_f64().unwrap_or(0.7),
        _ => 0.7,
    };

    Ok(Json(json!({
        "ok": true,
        "event": {
            "summary": summary,
            "dtstart": dtstart,
            "dtend": dtend,
            "all_day": all_day,
            "location": location,
            "description": description,
        },
        "confidence": confidence,
    }))
    .into_response())
}

fn body_get_str_opt<'a>(v: &'a Value, key: &str) -> &'a str {
    v.get(key).and_then(Value::as_str).unwrap_or("")
}

/// `_strip_tz(s)` — drop a trailing `Z`/`z` or `+HH:MM`/`-HH:MM` tz marker.
fn strip_tz(s: &str) -> String {
    let s = s.trim();
    if s.is_empty() {
        return String::new();
    }
    let mut out = s.to_string();
    if out.ends_with('Z') || out.ends_with('z') {
        out.pop();
    }
    out = STRIP_TZ_OFFSET_RE.replace(&out, "").to_string();
    out
}

// ===========================================================================
// Multipart helper (mirrors document_routes::parse_multipart_with_file)
// ===========================================================================

// (the bounded `read_upload_limited` helper replaced the old unbounded
// multipart file reader here — see crate::src::upload_limits.)

// ===========================================================================
// Tests
// ===========================================================================

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn router_mounts_all_absolute_paths() {
        let base: Router<AppState> = Router::new();
        let _merged: Router<AppState> = base.merge(setup_calendar_routes());
    }

    #[test]
    fn parse_dt_iso_fast_paths() {
        // date-only
        let d = parse_dt("2026-06-01").unwrap();
        assert_eq!(d.format("%Y-%m-%d %H:%M:%S").to_string(), "2026-06-01 00:00:00");
        // tz-aware -> naive UTC
        let (dt, is_utc) = parse_dt_pair("2026-05-13T10:00:00+09:00").unwrap();
        assert!(is_utc);
        assert_eq!(dt.format("%Y-%m-%d %H:%M:%S").to_string(), "2026-05-13 01:00:00");
        // Z suffix
        let (dt2, is_utc2) = parse_dt_pair("2026-05-13T01:00:00Z").unwrap();
        assert!(is_utc2);
        assert_eq!(dt2.format("%H:%M:%S").to_string(), "01:00:00");
        // naive ISO
        let (_dt3, is_utc3) = parse_dt_pair("2026-05-13T21:00:00").unwrap();
        assert!(!is_utc3);
    }

    #[test]
    fn parse_dt_dateutil_fallback_absolute_formats() {
        // Absolute formats that the hand-rolled phrase parser does NOT cover
        // now resolve via the dtparse (dateutil) last-resort fallback.
        let a = parse_dt("May 13 2026 9pm").unwrap();
        assert_eq!(a.format("%Y-%m-%d %H:%M:%S").to_string(), "2026-05-13 21:00:00");
        let b = parse_dt("Jan 5 2027").unwrap();
        assert_eq!(b.format("%Y-%m-%d").to_string(), "2027-01-05");
        // Pair form delegates to the same fallback; no explicit tz -> not UTC.
        let (c, is_utc) = parse_dt_pair("May 13 2026 9pm").unwrap();
        assert!(!is_utc);
        assert_eq!(c.format("%Y-%m-%d %H:%M:%S").to_string(), "2026-05-13 21:00:00");
        // Genuinely unparseable input keeps the Python error shape.
        assert!(parse_dt("not a date at all zzz").is_err());
    }

    #[test]
    fn parse_dt_isoformat_retains_dateutil_offset() {
        // `_parse_dt(s).isoformat()` (py:161/221): the strict-ISO fast path
        // strips tz, but the dateutil LAST-RESORT branch keeps the offset.
        // RFC-2822 form fails `datetime.fromisoformat` -> dtparse keeps +0200.
        assert_eq!(
            parse_dt_isoformat("Mon, 01 Jun 2026 12:00:00 +0200").unwrap(),
            "2026-06-01T12:00:00+02:00"
        );
        // Strict-ISO fast path is naive (tz stripped), matching `_parse_dt`.
        assert_eq!(
            parse_dt_isoformat("2026-05-13T10:00:00+09:00").unwrap(),
            "2026-05-13T01:00:00"
        );
        // Naive natural-language branch stays naive (no offset).
        assert_eq!(
            parse_dt_isoformat("May 13 2026 9pm").unwrap(),
            "2026-05-13T21:00:00"
        );
    }

    #[test]
    fn parse_time_variants() {
        assert_eq!(parse_time("1pm"), Some((13, 0)));
        assert_eq!(parse_time("1:30 PM"), Some((13, 30)));
        assert_eq!(parse_time("13:00"), Some((13, 0)));
        assert_eq!(parse_time("12am"), Some((0, 0)));
        assert_eq!(parse_time("12pm"), Some((12, 0)));
        assert_eq!(parse_time("25"), None);
    }

    #[test]
    fn event_to_dict_timed_utc_gets_z() {
        let ev = EventRow {
            uid: "u1".to_string(),
            summary: Some("Lunch".to_string()),
            dtstart: "2026-05-13 01:00:00.000000".to_string(),
            dtend: "2026-05-13 02:00:00.000000".to_string(),
            all_day: false,
            is_utc: true,
            description: None,
            location: None,
            rrule: None,
            calendar_id: "cal1".to_string(),
            color: None,
            event_type: None,
            importance: None,
            cal_name: Some("Personal".to_string()),
            cal_color: Some("#5b8abf".to_string()),
        };
        let d = event_to_dict(&ev);
        assert_eq!(d["dtstart"], "2026-05-13T01:00:00Z");
        assert_eq!(d["dtend"], "2026-05-13T02:00:00Z");
        assert_eq!(d["importance"], "normal");
        assert_eq!(d["color"], "#5b8abf");
    }

    #[test]
    fn event_to_dict_all_day_is_date_only() {
        let ev = EventRow {
            uid: "u2".to_string(),
            summary: None,
            dtstart: "2026-05-13 00:00:00.000000".to_string(),
            dtend: "2026-05-14 00:00:00.000000".to_string(),
            all_day: true,
            is_utc: false,
            description: Some("d".to_string()),
            location: Some("here".to_string()),
            rrule: Some("FREQ=DAILY".to_string()),
            calendar_id: "cal1".to_string(),
            color: Some("#abc".to_string()),
            event_type: Some("task".to_string()),
            importance: Some("high".to_string()),
            cal_name: Some("Work".to_string()),
            cal_color: Some("#000".to_string()),
        };
        let d = event_to_dict(&ev);
        assert_eq!(d["dtstart"], "2026-05-13");
        assert_eq!(d["dtend"], "2026-05-14");
        assert_eq!(d["summary"], "");
        assert_eq!(d["color"], "#abc");
        assert_eq!(d["importance"], "high");
    }

    #[test]
    fn strip_tz_removes_markers() {
        assert_eq!(strip_tz("2026-05-13T21:00:00Z"), "2026-05-13T21:00:00");
        assert_eq!(strip_tz("2026-05-13T21:00:00+09:00"), "2026-05-13T21:00:00");
        assert_eq!(strip_tz("2026-05-13T21:00:00-0500"), "2026-05-13T21:00:00");
        assert_eq!(strip_tz("2026-05-13T21:00:00"), "2026-05-13T21:00:00");
    }

    // ── Recurrence expansion (RRULE) ────────────────────────────────────────

    fn ndt(s: &str) -> NaiveDateTime {
        NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S").unwrap()
    }

    /// Build an `EventRow` for expansion tests. Datetimes are SQLite store
    /// strings; `rrule`/`all_day`/`is_utc` exercise the expander branches.
    fn row(dtstart: &str, dtend: &str, all_day: bool, is_utc: bool, rrule: &str) -> EventRow {
        EventRow {
            uid: "series-1".to_string(),
            summary: Some("Standup".to_string()),
            dtstart: dtstart.to_string(),
            dtend: dtend.to_string(),
            all_day,
            is_utc,
            description: None,
            location: None,
            rrule: Some(rrule.to_string()),
            calendar_id: "cal1".to_string(),
            color: None,
            event_type: None,
            importance: None,
            cal_name: Some("Personal".to_string()),
            cal_color: Some("#5b8abf".to_string()),
        }
    }

    #[test]
    fn expand_non_recurring_passes_through_with_flags() {
        let ev = row("2026-05-13 09:00:00", "2026-05-13 10:00:00", false, true, "");
        let out = expand_rrule(&ev, ndt("2026-05-01 00:00:00"), ndt("2026-06-01 00:00:00"));
        assert_eq!(out.len(), 1);
        assert_eq!(out[0]["is_recurrence"], false);
        assert_eq!(out[0]["series_uid"], "series-1");
        assert_eq!(out[0]["uid"], "series-1");
        // is_utc => Z suffix on read-back.
        assert_eq!(out[0]["dtstart"], "2026-05-13T09:00:00Z");
    }

    #[test]
    fn expand_weekly_spans_window_beyond_dtstart_year() {
        // A weekly Monday series that STARTED before the window. The pre-fix SQL
        // dropped these because their base dtend was already < window start;
        // expansion now generates the actual occurrences inside the window.
        // DTSTART 2024-01-01 is a Monday.
        let ev = row(
            "2024-01-01 09:00:00",
            "2024-01-01 09:30:00",
            false,
            false,
            "FREQ=WEEKLY;BYDAY=MO",
        );
        // Window: the month of June 2026 — 5 Mondays (1, 8, 15, 22, 29).
        let out = expand_rrule(&ev, ndt("2026-06-01 00:00:00"), ndt("2026-07-01 00:00:00"));
        assert_eq!(out.len(), 5);
        assert!(out.iter().all(|d| d["is_recurrence"] == true));
        assert!(out.iter().all(|d| d["series_uid"] == "series-1"));
        // First occurrence in window is Mon 2026-06-01; compound uid carries the
        // occurrence start (no Z; not is_utc).
        assert_eq!(out[0]["dtstart"], "2026-06-01T09:00:00");
        assert_eq!(out[0]["uid"], "series-1::2026-06-01T09:00");
        assert_eq!(out[4]["dtstart"], "2026-06-29T09:00:00");
    }

    #[test]
    fn expand_daily_honors_utc_normalized_until() {
        // UNTIL carries a trailing Z (absolute UTC) while DTSTART is naive — the
        // expander must strip the Z so the bound matches, instead of collapsing
        // the series. UNTIL=2026-06-05 -> occurrences 06-01..06-05 inclusive.
        let ev = row(
            "2026-06-01 08:00:00",
            "2026-06-01 08:30:00",
            false,
            true,
            "FREQ=DAILY;UNTIL=20260605T080000Z",
        );
        let out = expand_rrule(&ev, ndt("2026-06-01 00:00:00"), ndt("2026-07-01 00:00:00"));
        assert_eq!(out.len(), 5);
        assert_eq!(out[0]["dtstart"], "2026-06-01T08:00:00Z");
        assert_eq!(out[4]["dtstart"], "2026-06-05T08:00:00Z");
    }

    #[test]
    fn expand_daily_count_caps_occurrences() {
        let ev = row(
            "2026-06-01 08:00:00",
            "2026-06-01 08:30:00",
            false,
            false,
            "FREQ=DAILY;COUNT=3",
        );
        let out = expand_rrule(&ev, ndt("2026-06-01 00:00:00"), ndt("2026-07-01 00:00:00"));
        assert_eq!(out.len(), 3);
        assert_eq!(out[2]["dtstart"], "2026-06-03T08:00:00");
    }

    #[test]
    fn expand_all_day_uses_date_only_strings() {
        // All-day daily series — occurrence dtstart/dtend are date-only.
        let ev = row(
            "2026-06-10 00:00:00",
            "2026-06-11 00:00:00",
            true,
            false,
            "FREQ=DAILY;COUNT=2",
        );
        let out = expand_rrule(&ev, ndt("2026-06-01 00:00:00"), ndt("2026-07-01 00:00:00"));
        assert_eq!(out.len(), 2);
        assert_eq!(out[0]["dtstart"], "2026-06-10");
        assert_eq!(out[0]["dtend"], "2026-06-11");
        assert_eq!(out[0]["uid"], "series-1::2026-06-10");
    }

    #[test]
    fn expand_monthly_byday_ordinal() {
        // First Monday of each month, all of 2026 in scope.
        let ev = row(
            "2026-01-05 09:00:00", // first Monday of Jan 2026
            "2026-01-05 10:00:00",
            false,
            false,
            "FREQ=MONTHLY;BYDAY=1MO",
        );
        let out = expand_rrule(&ev, ndt("2026-06-01 00:00:00"), ndt("2026-08-01 00:00:00"));
        // June (1st), July (6th).
        assert_eq!(out.len(), 2);
        assert_eq!(out[0]["dtstart"], "2026-06-01T09:00:00");
        assert_eq!(out[1]["dtstart"], "2026-07-06T09:00:00");
    }

    #[test]
    fn expand_monthly_negative_bymonthday() {
        // Last day of each month (-1).
        let ev = row(
            "2026-01-31 12:00:00",
            "2026-01-31 13:00:00",
            false,
            false,
            "FREQ=MONTHLY;BYMONTHDAY=-1",
        );
        let out = expand_rrule(&ev, ndt("2026-02-01 00:00:00"), ndt("2026-05-01 00:00:00"));
        // Feb 28, Mar 31, Apr 30.
        assert_eq!(out.len(), 3);
        assert_eq!(out[0]["dtstart"], "2026-02-28T12:00:00");
        assert_eq!(out[1]["dtstart"], "2026-03-31T12:00:00");
        assert_eq!(out[2]["dtstart"], "2026-04-30T12:00:00");
    }

    #[test]
    fn expand_yearly_default_anchor() {
        let ev = row(
            "2020-07-04 00:00:00",
            "2020-07-05 00:00:00",
            true,
            false,
            "FREQ=YEARLY",
        );
        let out = expand_rrule(&ev, ndt("2026-01-01 00:00:00"), ndt("2027-01-01 00:00:00"));
        assert_eq!(out.len(), 1);
        assert_eq!(out[0]["dtstart"], "2026-07-04");
    }

    #[test]
    fn expand_interval_skips_periods() {
        // Every other day, COUNT=3 -> 06-01, 06-03, 06-05.
        let ev = row(
            "2026-06-01 08:00:00",
            "2026-06-01 08:30:00",
            false,
            false,
            "FREQ=DAILY;INTERVAL=2;COUNT=3",
        );
        let out = expand_rrule(&ev, ndt("2026-06-01 00:00:00"), ndt("2026-07-01 00:00:00"));
        assert_eq!(out.len(), 3);
        assert_eq!(out[0]["dtstart"], "2026-06-01T08:00:00");
        assert_eq!(out[1]["dtstart"], "2026-06-03T08:00:00");
        assert_eq!(out[2]["dtstart"], "2026-06-05T08:00:00");
    }

    #[test]
    fn expand_overnight_occurrence_before_window_but_overlapping() {
        // A daily 23:00 -> 01:00 (next day) overnight series. An occurrence that
        // STARTS at 23:00 the day before the window but ENDS at 01:00 inside it
        // must be captured (expand_start = start - duration).
        let ev = row(
            "2026-06-01 23:00:00",
            "2026-06-02 01:00:00",
            false,
            false,
            "FREQ=DAILY",
        );
        // Window starts mid-day 2026-06-10; the 2026-06-09 23:00 occurrence ends
        // 2026-06-10 01:00 — before the window start (12:00), so excluded; but
        // verify the boundary logic keeps occurrences whose end is inside.
        let out = expand_rrule(&ev, ndt("2026-06-10 00:30:00"), ndt("2026-06-11 00:00:00"));
        // The 2026-06-09 23:00 -> 2026-06-10 01:00 occurrence overlaps [00:30,...).
        assert!(out
            .iter()
            .any(|d| d["dtstart"] == "2026-06-09T23:00:00"));
        // And the 2026-06-10 23:00 occurrence (starts in window).
        assert!(out
            .iter()
            .any(|d| d["dtstart"] == "2026-06-10T23:00:00"));
    }

    #[test]
    fn expand_malformed_rrule_falls_back_to_base_when_overlapping() {
        // A garbage RRULE: parse fails. The base event is returned ONLY if it
        // overlaps the window (dtstart < end AND dtend > start).
        let ev = row(
            "2026-06-15 09:00:00",
            "2026-06-15 10:00:00",
            false,
            false,
            "this is not a valid rrule",
        );
        let overlapping =
            expand_rrule(&ev, ndt("2026-06-01 00:00:00"), ndt("2026-07-01 00:00:00"));
        assert_eq!(overlapping.len(), 1);
        assert_eq!(overlapping[0]["is_recurrence"], false);
        assert_eq!(overlapping[0]["uid"], "series-1");
        // Non-overlapping window -> empty (base doesn't intersect).
        let none = expand_rrule(&ev, ndt("2030-01-01 00:00:00"), ndt("2030-02-01 00:00:00"));
        assert!(none.is_empty());
    }

    #[test]
    fn recur_rule_parse_rejects_unsupported_and_missing_freq() {
        // No FREQ -> None.
        assert!(RecurRule::parse("COUNT=3", ndt("2026-06-01 09:00:00")).is_none());
        // Unsupported FREQ -> None.
        assert!(RecurRule::parse("FREQ=HOURLY", ndt("2026-06-01 09:00:00")).is_none());
        // Leading RRULE: prefix is accepted.
        assert!(RecurRule::parse("RRULE:FREQ=DAILY", ndt("2026-06-01 09:00:00")).is_some());
    }

    #[test]
    fn byday_in_month_ordinals() {
        // First Monday of June 2026 is the 1st; last Monday is the 29th.
        assert_eq!(byday_in_month(2026, 6, Some(1), 0), vec![1]);
        assert_eq!(byday_in_month(2026, 6, Some(-1), 0), vec![29]);
        // All Mondays in June 2026: 1, 8, 15, 22, 29.
        assert_eq!(byday_in_month(2026, 6, None, 0), vec![1, 8, 15, 22, 29]);
        // Out-of-range ordinal -> empty.
        assert!(byday_in_month(2026, 6, Some(6), 0).is_empty());
    }

    #[test]
    fn resolve_monthday_negative_and_positive() {
        assert_eq!(resolve_monthday(1, 30), Some(1));
        assert_eq!(resolve_monthday(-1, 30), Some(30));
        assert_eq!(resolve_monthday(-1, 28), Some(28));
        assert_eq!(resolve_monthday(31, 30), None);
        assert_eq!(resolve_monthday(0, 30), None);
    }

    #[test]
    fn add_months_clamps_to_month_length() {
        // Jan 31 + 1 month -> Feb 28 (2026 not a leap year).
        let d = add_months(ndt("2026-01-31 09:00:00"), 1).unwrap();
        assert_eq!(d.format("%Y-%m-%d %H:%M:%S").to_string(), "2026-02-28 09:00:00");
        // Crossing a year boundary preserves time-of-day.
        let d2 = add_months(ndt("2026-12-15 14:30:00"), 1).unwrap();
        assert_eq!(d2.format("%Y-%m-%d %H:%M:%S").to_string(), "2027-01-15 14:30:00");
    }

    #[test]
    fn bysetpos_selects_from_ordered_set() {
        let set: Vec<NaiveDateTime> = ["2026-06-01 09:00:00", "2026-06-08 09:00:00", "2026-06-15 09:00:00"]
            .iter()
            .map(|s| ndt(s))
            .collect();
        // Last element.
        let last = apply_setpos(&set, &[-1]);
        assert_eq!(last, vec![ndt("2026-06-15 09:00:00")]);
        // First element.
        let first = apply_setpos(&set, &[1]);
        assert_eq!(first, vec![ndt("2026-06-01 09:00:00")]);
        // Out of range -> dropped.
        assert!(apply_setpos(&set, &[5]).is_empty());
    }

    // ── ICS import bounded read (193dc2f) ───────────────────────────────────
    //
    // Verify that `crate::src::upload_limits::read_upload_limited` is wired
    // into the import path by exercising it directly with the same cap and
    // label the handler uses (`ICS_MAX_BYTES`, `"ICS file"`).

    use axum::body::Body;
    use axum::extract::Multipart;
    use axum::http::Request;
    use axum::extract::FromRequest;

    fn make_multipart_ics(data: &[u8]) -> (String, Vec<u8>) {
        let boundary = "ics_boundary_test1234";
        let ct = format!("multipart/form-data; boundary={boundary}");
        let mut body = Vec::new();
        body.extend_from_slice(format!("--{boundary}\r\n").as_bytes());
        body.extend_from_slice(
            b"Content-Disposition: form-data; name=\"file\"; filename=\"cal.ics\"\r\n",
        );
        body.extend_from_slice(b"Content-Type: text/calendar\r\n\r\n");
        body.extend_from_slice(data);
        body.extend_from_slice(format!("\r\n--{boundary}--\r\n").as_bytes());
        (ct, body)
    }

    /// Under-cap ICS data is accepted without error.
    #[tokio::test]
    async fn import_ics_bounded_read_under_cap_ok() {
        let data = b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n";
        let (ct, body) = make_multipart_ics(data);
        let req = Request::builder()
            .header("content-type", ct)
            .body(Body::from(body))
            .unwrap();
        let mut mp = Multipart::from_request(req, &()).await.unwrap();
        let field = mp.next_field().await.unwrap().expect("field present");
        let result =
            crate::src::upload_limits::read_upload_limited(field, ICS_MAX_BYTES, "ICS file")
                .await;
        assert!(result.is_ok());
        assert_eq!(result.unwrap(), data);
    }

    /// An oversized ICS body is rejected with 413 and the "ICS file" label.
    #[tokio::test]
    async fn import_ics_bounded_read_over_cap_413() {
        // Use a 1 MB cap (formatted "1 MB") so the body stays under axum's 2 MB
        // default Multipart limit; the live route disables that limit
        // (build_router DefaultBodyLimit::disable) so the real ICS_MAX_BYTES cap
        // applies. The bounding logic is identical. One byte over -> 413.
        const CAP: usize = 1024 * 1024;
        let data: Vec<u8> = vec![b'X'; CAP + 1];
        let (ct, body) = make_multipart_ics(&data);
        let req = Request::builder()
            .header("content-type", ct)
            .body(Body::from(body))
            .unwrap();
        let mut mp = Multipart::from_request(req, &()).await.unwrap();
        let field = mp.next_field().await.unwrap().expect("field present");
        let err =
            crate::src::upload_limits::read_upload_limited(field, CAP, "ICS file")
                .await
                .unwrap_err();
        assert_eq!(err.status_code, 413);
        // Error message must start with the caller-supplied label and contain the
        // formatted cap — mirrors the Python 413 detail format.
        assert!(
            err.detail.starts_with("ICS file exceeds"),
            "detail should start with label: {}",
            err.detail
        );
        assert!(
            err.detail.contains("1 MB"),
            "detail should contain '1 MB': {}",
            err.detail
        );
    }
}
