//! `pytime` — the slice of Python's `time` module the translation uses.

use once_cell::sync::Lazy;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

/// `time.time()` — seconds since the Unix epoch as a float.
pub fn time() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

// Reference instant for `monotonic()` — like CPython's clock, the zero point is
// arbitrary; only differences are meaningful.
static MONOTONIC_START: Lazy<Instant> = Lazy::new(Instant::now);

/// `time.monotonic()` — fractional seconds from an arbitrary, never-decreasing
/// reference point. Suitable for measuring elapsed intervals (rate limiting).
pub fn monotonic() -> f64 {
    MONOTONIC_START.elapsed().as_secs_f64()
}
