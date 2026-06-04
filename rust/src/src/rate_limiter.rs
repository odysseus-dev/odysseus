// src/rate_limiter.rs  <- src/rate_limiter.py
//! Generic in-memory rate limiter — sliding window, keyed by IP.

use crate::pytime as time;
use std::collections::HashMap;
use std::sync::Mutex;

/// The mutable state guarded by the Python `threading.Lock()`.
///
/// In CPython the `RateLimiter` object holds all of `_log`/`_last_cleanup`
/// directly and `with self._lock:` serialises every mutation. Rust expresses
/// the same "this data is only touched under the lock" invariant by moving the
/// guarded fields into one struct behind a single `Mutex`.
struct State {
    /// `Dict[str, List[float]]` — per-key list of request timestamps.
    log: HashMap<String, Vec<f64>>,
    /// `time.monotonic()` of the last cleanup sweep.
    last_cleanup: f64,
}

/// Sliding-window rate limiter.
///
/// Usage:
/// ```text
/// limiter = RateLimiter(max_requests=5, window_seconds=60)
/// if not limiter.check(ip):
///     raise HTTPException(429, "Too many requests")
/// ```
pub struct RateLimiter {
    max_requests: i64,
    window: i64,
    /// `_log` + `_last_cleanup`, guarded by the equivalent of `self._lock`.
    inner: Mutex<State>,
    cleanup_interval: i64,
}

impl RateLimiter {
    pub fn new(max_requests: i64, window_seconds: i64) -> Self {
        let window = window_seconds;
        let log: HashMap<String, Vec<f64>> = HashMap::new();
        // self._lock = threading.Lock()  -> the Mutex wrapping `State`.
        let last_cleanup = time::monotonic();
        let cleanup_interval = std::cmp::max(window_seconds * 2, 120);
        RateLimiter {
            max_requests,
            window,
            inner: Mutex::new(State { log, last_cleanup }),
            cleanup_interval,
        }
    }

    /// Return True if the request is allowed, False if rate-limited.
    pub fn check(&self, key: &str) -> bool {
        let now = time::monotonic();
        // with self._lock:
        let mut state = self.inner.lock().unwrap();
        self.maybe_cleanup(&mut state, now);
        let mut timestamps = state.log.get(key).cloned().unwrap_or_default();
        let cutoff = now - self.window as f64;
        // timestamps = [t for t in timestamps if t > cutoff]
        timestamps.retain(|&t| t > cutoff);
        if timestamps.len() as i64 >= self.max_requests {
            state.log.insert(key.to_string(), timestamps);
            return false;
        }
        timestamps.push(now);
        state.log.insert(key.to_string(), timestamps);
        true
    }

    /// Periodically purge stale entries.
    fn maybe_cleanup(&self, state: &mut State, now: f64) {
        if now - state.last_cleanup < self.cleanup_interval as f64 {
            return;
        }
        state.last_cleanup = now;
        let cutoff = now - self.window as f64;
        let stale: Vec<String> = state
            .log
            .iter()
            .filter(|(_k, v)| v.is_empty() || *v.last().unwrap() <= cutoff)
            .map(|(k, _v)| k.clone())
            .collect();
        for k in stale {
            state.log.remove(&k);
        }
    }
}
