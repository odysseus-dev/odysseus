//! `pydatetime` — the slice of Python's `datetime` the translation uses.
//!
//! SQLAlchemy's SQLite `DateTime` stores values as the string
//! `"%Y-%m-%d %H:%M:%S%.6f"` (space-separated, microsecond precision), and those
//! strings sort lexicographically in timestamp order — which is what the
//! `last_accessed < cutoff` comparisons in `database.py` rely on. These helpers
//! produce that exact format.
//!
//! DEVIATION: `database.py`'s `cleanup_old_sessions` mixes `datetime.utcnow()`
//! (naive UTC) with `datetime.fromtimestamp()` (naive *local*), a latent tz
//! quirk. The translation normalizes everything to naive **UTC**, which is
//! internally consistent and machine-timezone-independent.

use chrono::{DateTime, NaiveDateTime, Timelike, Utc};

/// SQLAlchemy's SQLite datetime string format.
const FMT: &str = "%Y-%m-%d %H:%M:%S%.6f";

/// `datetime.isoformat()` over a value SQLAlchemy stored as a SQLite datetime
/// string (`"%Y-%m-%d %H:%M:%S[.%f]"`).
///
/// SQLAlchemy reads the column back into a `datetime`; `to_dict` then calls
/// `.isoformat()`, which uses a `T` separator and includes microseconds **only
/// when non-zero** (always 6 digits when present). `func.now()` columns store no
/// fraction, so they isoformat without microseconds. This reproduces both. An
/// unparseable value is returned unchanged (best effort).
pub fn to_isoformat(stored: &str) -> String {
    let parsed = NaiveDateTime::parse_from_str(stored, "%Y-%m-%d %H:%M:%S%.f")
        .or_else(|_| NaiveDateTime::parse_from_str(stored, "%Y-%m-%d %H:%M:%S"));
    match parsed {
        Ok(dt) => {
            if dt.nanosecond() == 0 {
                dt.format("%Y-%m-%dT%H:%M:%S").to_string()
            } else {
                // %.6f -> ".ffffff" (6 digits, leading dot), matching isoformat.
                dt.format("%Y-%m-%dT%H:%M:%S%.6f").to_string()
            }
        }
        Err(_) => stored.to_string(),
    }
}

/// `datetime.utcnow()` rendered as the stored string — naive UTC, microseconds.
pub fn utcnow_naive_iso() -> String {
    Utc::now().naive_utc().format(FMT).to_string()
}

/// `datetime.now(timezone.utc)` as a naive-UTC value, for code that needs the
/// datetime itself (e.g. `now + timedelta(microseconds=i)` in
/// `session_manager.replace_messages`) before rendering it with `naive_to_iso`.
pub fn utcnow_naive() -> NaiveDateTime {
    Utc::now().naive_utc()
}

/// Render a naive datetime as the stored SQLAlchemy SQLite datetime string
/// (`"%Y-%m-%d %H:%M:%S%.6f"`), matching `utcnow_naive_iso`.
pub fn naive_to_iso(dt: NaiveDateTime) -> String {
    dt.format(FMT).to_string()
}

/// A naive-UTC datetime string for a Unix epoch timestamp (seconds, fractional).
///
/// Stands in for `datetime.fromtimestamp(ts)` in the cutoff computation, but in
/// UTC (see the module deviation note) so it lines up with `utcnow_naive_iso`.
pub fn from_timestamp_naive_iso(ts: f64) -> String {
    let secs = ts.trunc() as i64;
    let nanos = ((ts.fract()) * 1_000_000_000.0).round() as u32;
    let dt = DateTime::<Utc>::from_timestamp(secs, nanos).unwrap_or_else(|| DateTime::<Utc>::from_timestamp(0, 0).unwrap());
    dt.naive_utc().format(FMT).to_string()
}
