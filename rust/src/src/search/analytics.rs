// src/search/analytics.rs  <- src/search/analytics.py
//! Search analytics, metrics tracking, and exception hierarchy.

use crate::pylog as logger;
use crate::src::constants::BASE_DIR;
use crate::src::search::cache::{bump_metric, CACHE_METRICS};
use serde_json::{json, Map, Value};
use std::fmt;
use std::path::Path;

// The Python sets up a dedicated rotating FileHandler error logger
// (`search_engine_error.log`). That wiring is a logging-config concern; the
// `error_logger` is exposed for callers but only ever receives WARNING+ records.
// We expose a stand-in module whose calls route through `crate::pylog`.
// DEVIATION: the separate on-disk error log file is not created here.

/// `error_logger` — the dedicated WARNING+ logger from `analytics.py`. Exposed
/// as a free-function facade (mirrors `error_logger.error(...)` /
/// `error_logger.warning(...)` call sites in `providers`/`content`/`core`);
/// all records route through `crate::pylog`.
pub mod error_logger {
    use crate::pylog as logger;

    /// `error_logger.error(msg)`.
    pub fn error(msg: &str) {
        logger::error(msg);
    }

    /// `error_logger.warning(msg)`.
    pub fn warning(msg: &str) {
        logger::warning(msg);
    }
}

/// `ANALYTICS_FILE = Path(__file__).resolve().parent.parent / "search_analytics.json"`.
fn analytics_file() -> String {
    Path::new(BASE_DIR.as_str())
        .join("search_analytics.json")
        .to_string_lossy()
        .into_owned()
}

// ----------------------------------------------------------------------
// Custom exception hierarchy
// ----------------------------------------------------------------------
//
// Python uses class inheritance: NetworkError/ParseError/RateLimitError all
// subclass SearchEngineError. Rust has no inheritance, so each becomes its own
// error type. DEVIATION: code that `except SearchEngineError` to catch all four
// must, in Rust, match each type (or a future shared trait); the subclass
// relationship is represented by the shared `SearchEngineKind` marker trait.

/// Marker shared by all search-engine errors (stands in for the Python base
/// class used in `except SearchEngineError`).
pub trait SearchEngineKind: std::error::Error {}

macro_rules! search_error {
    ($name:ident, $doc:literal) => {
        #[doc = $doc]
        #[derive(Debug, Clone, Default)]
        pub struct $name {
            pub message: String,
        }
        impl $name {
            pub fn new(message: impl Into<String>) -> Self {
                $name { message: message.into() }
            }
        }
        impl fmt::Display for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                write!(f, "{}", self.message)
            }
        }
        impl std::error::Error for $name {}
        impl SearchEngineKind for $name {}
    };
}

search_error!(SearchEngineError, "Base class for all search-engine related errors.");
search_error!(NetworkError, "Raised when a network request fails (e.g., timeout, DNS error).");
search_error!(ParseError, "Raised when HTML or other content cannot be parsed.");
search_error!(RateLimitError, "Raised when the remote service returns a rate-limit (HTTP 429).");

// ----------------------------------------------------------------------
// Analytics helpers
// ----------------------------------------------------------------------

/// The default analytics document (six zeroed counters + empty patterns).
fn default_analytics() -> Value {
    json!({
        "total_queries": 0,
        "successful_queries": 0,
        "failed_queries": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "query_patterns": {},
    })
}

/// Load analytics data from the JSON file, creating defaults if missing.
fn _load_analytics() -> Value {
    let path = analytics_file();
    // if not ANALYTICS_FILE.exists():
    if !std::path::Path::new(&path).exists() {
        let default = default_analytics();
        _save_analytics(&default);
        return default;
    }
    // try: return json.load(f)
    match std::fs::read_to_string(&path)
        .ok()
        .and_then(|s| serde_json::from_str::<Value>(&s).ok())
    {
        Some(v) => v,
        None => {
            logger::warning("Failed to load analytics file");
            default_analytics()
        }
    }
}

/// Persist analytics data to the JSON file.
///
/// DEVIATION: Python uses a plain `json.dump(data, f, indent=2)` (not atomic).
/// We mirror the plain write + 2-space indent; `serde_json` pretty output does
/// not ASCII-escape, so non-ASCII query text is written as UTF-8 (CPython would
/// emit `\uXXXX`). This is a metrics file, not byte-critical state.
fn _save_analytics(data: &Value) {
    let path = analytics_file();
    match serde_json::to_string_pretty(data) {
        Ok(s) => {
            if std::fs::write(&path, s).is_err() {
                logger::warning("Failed to write analytics file");
            }
        }
        Err(_) => logger::warning("Failed to write analytics file"),
    }
}

/// `analytics[key] += n` over the dynamic JSON object.
fn bump(analytics: &mut Value, key: &str, n: i64) {
    let cur = analytics.get(key).and_then(|v| v.as_i64()).unwrap_or(0);
    analytics[key] = json!(cur + n);
}

/// Update analytics for a single query execution.
pub fn _record_query(query: &str, success: bool, cache_hit: bool) {
    let mut analytics = _load_analytics();
    bump(&mut analytics, "total_queries", 1);
    if success {
        bump(&mut analytics, "successful_queries", 1);
    } else {
        bump(&mut analytics, "failed_queries", 1);
    }

    if cache_hit {
        bump(&mut analytics, "cache_hits", 1);
        bump_metric("hits", 1); // cache_metrics["hits"] += 1
    } else {
        bump(&mut analytics, "cache_misses", 1);
        bump_metric("misses", 1); // cache_metrics["misses"] += 1
    }

    // patterns = analytics["query_patterns"]
    // entry = patterns.get(query, {"count": 0, "successes": 0})
    let patterns = analytics
        .get("query_patterns")
        .and_then(|v| v.as_object())
        .cloned()
        .unwrap_or_default();
    let mut entry = patterns
        .get(query)
        .and_then(|v| v.as_object())
        .cloned()
        .unwrap_or_else(|| {
            let mut m = Map::new();
            m.insert("count".to_string(), json!(0));
            m.insert("successes".to_string(), json!(0));
            m
        });
    let count = entry.get("count").and_then(|v| v.as_i64()).unwrap_or(0);
    entry.insert("count".to_string(), json!(count + 1));
    if success {
        let s = entry.get("successes").and_then(|v| v.as_i64()).unwrap_or(0);
        entry.insert("successes".to_string(), json!(s + 1));
    }
    // patterns[query] = entry
    let mut patterns = patterns;
    patterns.insert(query.to_string(), Value::Object(entry));
    analytics["query_patterns"] = Value::Object(patterns);

    _save_analytics(&analytics);
}

/// Return aggregated search analytics.
pub fn get_search_stats() -> Value {
    let analytics = _load_analytics();
    let get_i = |k: &str| analytics.get(k).and_then(|v| v.as_i64()).unwrap_or(0);

    // total = analytics.get("total_queries", 0) or 1
    let total = {
        let t = get_i("total_queries");
        if t == 0 {
            1
        } else {
            t
        }
    };
    let success_rate = get_i("successful_queries") as f64 / total as f64;
    // cache_total = cache_hits + cache_misses or 1
    let cache_total = {
        let c = get_i("cache_hits") + get_i("cache_misses");
        if c == 0 {
            1
        } else {
            c
        }
    };
    let cache_hit_rate = get_i("cache_hits") as f64 / cache_total as f64;

    // Counter({q: data["count"]}).most_common(5)
    let mut counts: Vec<(String, i64)> = analytics
        .get("query_patterns")
        .and_then(|v| v.as_object())
        .map(|o| {
            o.iter()
                .map(|(q, data)| {
                    (q.clone(), data.get("count").and_then(|v| v.as_i64()).unwrap_or(0))
                })
                .collect()
        })
        .unwrap_or_default();
    // most_common: highest count first; Counter ties keep first-seen order, and
    // serde_json (preserve_order) keeps that insertion order, so a stable sort
    // by descending count reproduces it.
    counts.sort_by_key(|a| std::cmp::Reverse(a.1));
    let most_common: Vec<Value> = counts.iter().take(5).map(|(q, _)| json!(q)).collect();

    let metrics = CACHE_METRICS.lock().unwrap();
    let m = |k: &str| *metrics.get(k).unwrap_or(&0);

    json!({
        "most_common_queries": most_common,
        "success_rate": success_rate,
        "cache_hit_rate": cache_hit_rate,
        "total_queries": get_i("total_queries"),
        "successful_queries": get_i("successful_queries"),
        "failed_queries": get_i("failed_queries"),
        "cache_hits": get_i("cache_hits"),
        "cache_misses": get_i("cache_misses"),
        "cache_evictions": m("evictions"),
        "runtime_cache_hits": m("hits"),
        "runtime_cache_misses": m("misses"),
    })
}
