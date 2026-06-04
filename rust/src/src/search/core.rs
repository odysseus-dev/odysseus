// src/search/core.rs  <- src/search/core.py
//! Core search orchestrators: searxng_search_results, comprehensive_web_search, config, cache invalidation.
//!
//! * `SEARCH_CONFIG` module-global dict -> `once_cell::Lazy<Mutex<serde_json::Map>>`.
//! * `ThreadPoolExecutor(max_workers)` content fan-out -> `std::thread::scope`
//!   over the sync-blocking `fetch_webpage_content`. `as_completed` order is
//!   non-deterministic in Python, so any completion order is parity-safe.
//! * cache datetime now/isoformat/fromisoformat via chrono `Local` (matching
//!   `cache.rs`).

use crate::pylog as logger;
use crate::src::search::analytics::{error_logger, _record_query};
use crate::src::search::cache::{
    cleanup_cache, generate_cache_key, SEARCH_CACHE_DIR, SEARCH_CACHE_INDEX,
};
use crate::src::search::content::{
    extract_key_points, extract_quotes, extract_statistics, fetch_webpage_content, get_tldr,
};
use crate::src::search::providers::{
    _get_provider_key, _get_result_count, _get_search_settings, brave_search, duckduckgo_search,
    google_pse_search, searxng_search_api, serper_search, tavily_search,
};
use crate::src::search::query::cache_duration_for_query;
use crate::src::search::ranking::rank_search_results;
use chrono::{Duration, Local};
use once_cell::sync::Lazy;
use serde_json::{json, Map, Value};
use std::collections::HashSet;
use std::sync::Mutex;

// logger = logging.getLogger(__name__) -> crate::pylog shim.

// ========= CONFIG =========

/// `SEARCH_CONFIG` module-global. Python seeds `{"primary_provider": "searxng"}`
/// and later mutates it (e.g. `brave_api_key`); a `Lazy<Mutex<Map>>` reproduces
/// the shared mutable global.
pub static SEARCH_CONFIG: Lazy<Mutex<Map<String, Value>>> = Lazy::new(|| {
    let mut m = Map::new();
    m.insert("primary_provider".to_string(), json!("searxng"));
    Mutex::new(m)
});

/// True for config keys that hold a credential (e.g. `brave_api_key`).
///
/// `name.endswith(("_api_key", "_key", "_token", "_secret"))`.
fn _is_secret_key(name: &str) -> bool {
    name.ends_with("_api_key")
        || name.ends_with("_key")
        || name.ends_with("_token")
        || name.ends_with("_secret")
}

/// `settings.get("search_provider", "searxng")`.
fn settings_search_provider(settings: &Map<String, Value>) -> String {
    settings
        .get("search_provider")
        .and_then(|v| v.as_str())
        .unwrap_or("searxng")
        .to_string()
}

/// Get current search configuration including active provider info.
///
/// Never returns stored API keys: callers — including the unauthenticated
/// `GET /api/search/config` route — only need key *presence* via `has_api_key`,
/// not the secret itself (#1661).
pub fn get_search_config() -> Value {
    // config = SEARCH_CONFIG.copy()
    let mut config = SEARCH_CONFIG.lock().unwrap().clone();
    let settings = _get_search_settings();
    let provider = settings_search_provider(&settings);
    config.insert("active_provider".to_string(), json!(provider));
    // config["has_api_key"] = bool(_get_provider_key(provider))
    config.insert(
        "has_api_key".to_string(),
        json!(!_get_provider_key(&provider).is_empty()),
    );
    config.insert("result_count".to_string(), json!(_get_result_count()));
    if provider == "searxng" {
        config.insert(
            "search_url".to_string(),
            json!(crate::src::search::providers::_get_search_instance()),
        );
    }
    // Strip any string-valued credential so secrets never reach the response;
    // the boolean has_api_key flag (presence only) is preserved.
    config.retain(|k, v| !(v.is_string() && _is_secret_key(k)));
    Value::Object(config)
}

/// Merge non-secret search config into `SEARCH_CONFIG`.
///
/// Provider API keys are intentionally NOT cached here. They are read on demand
/// from settings/env via `_get_provider_key` (e.g. `brave_search`), so the
/// previous `SEARCH_CONFIG["brave_api_key"] = api_key` cache was never used for
/// search and only leaked the decrypted key through `get_search_config` /
/// `GET /api/search/config` (#1661). `api_key` is accepted for backward
/// compatibility but no longer stored.
///
/// The Python `**kwargs` merge (only non-secret keys via `_is_secret_key`) has
/// no current Rust caller, so only the now-no-op `api_key` parameter is kept.
pub fn update_search_config(api_key: Option<&str>) {
    // api_key is accepted for backward compatibility but no longer stored.
    let _ = api_key;
}

/// Call a search provider by name. Returns list of results or empty list.
///
/// `time_filter` defaults to `None`.
pub fn _call_provider(
    provider_name: &str,
    query: &str,
    count: i64,
    time_filter: Option<&str>,
) -> Vec<Value> {
    match provider_name {
        // searxng_search_api(query, count, time_filter=time_filter) — categories
        // takes its "general" default.
        "searxng" => searxng_search_api(query, count, "general", time_filter),
        "brave" => brave_search(query, count, time_filter),
        "duckduckgo" => duckduckgo_search(query, count, time_filter),
        "google_pse" => google_pse_search(query, count, time_filter),
        "tavily" => tavily_search(query, count, time_filter),
        "serper" => serper_search(query, count, time_filter),
        _ => Vec::new(),
    }
}

/// `_FALLBACK_ORDER` — the no-key fallback when the primary returns empty.
const FALLBACK_ORDER: [&str; 1] = ["duckduckgo"];

/// Build ordered list: primary first, then fallbacks (skipping primary and
/// dedupes). The fallback list comes from `settings.search_fallback_chain` if
/// the user configured one, otherwise the hardcoded `_FALLBACK_ORDER`.
pub fn _build_provider_chain(primary: &str) -> Vec<String> {
    let mut chain: Vec<String> = vec![primary.to_string()];
    let settings = _get_search_settings();
    // user_chain = settings.get("search_fallback_chain") or []
    let user_chain: Vec<String> = match settings.get("search_fallback_chain") {
        Some(Value::Array(arr)) => arr
            .iter()
            .map(|v| v.as_str().unwrap_or("").to_string())
            .collect(),
        // Tolerate comma-separated form from older payloads.
        Some(Value::String(s)) => s
            .split(',')
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .collect(),
        _ => Vec::new(),
    };
    let fallbacks: Vec<String> = if !user_chain.is_empty() {
        user_chain
    } else {
        FALLBACK_ORDER.iter().map(|s| s.to_string()).collect()
    };
    for fb in fallbacks {
        if !fb.is_empty() && fb != primary && !chain.contains(&fb) && fb != "disabled" {
            chain.push(fb);
        }
    }
    chain
}

// ----------------------------------------------------------------------
// Unified search with caching and retry
// ----------------------------------------------------------------------

/// Perform a web search using configured provider with caching and retry.
///
/// `count` defaults to 10, `time_filter` to `None`.
pub fn searxng_search_results(query: &str, count: i64, time_filter: Option<&str>) -> Vec<Value> {
    let settings = _get_search_settings();
    let search_provider = settings_search_provider(&settings);
    let result_count = _get_result_count();
    // Use configured count if caller used default
    let count = if count == 10 { result_count } else { count };

    // cache_key = generate_cache_key(f"{query}|{count}|{time_filter}")
    let tf_repr = match time_filter {
        Some(t) => t.to_string(),
        None => "None".to_string(),
    };
    let cache_key = generate_cache_key(&format!("{query}|{count}|{tf_repr}"));
    let cache_file = SEARCH_CACHE_DIR.join(format!("{cache_key}.cache"));

    // Check cache
    if cache_file.exists() {
        let read = (|| -> Result<Option<Vec<Value>>, String> {
            let s = std::fs::read_to_string(&cache_file).map_err(|e| e.to_string())?;
            let cached_data: Value = serde_json::from_str(&s).map_err(|e| e.to_string())?;
            let expiry_raw = cached_data.get("expiry").and_then(|v| v.as_str());
            let expiry = match expiry_raw {
                Some(raw) => Some(parse_iso(raw).map_err(|e| e.to_string())?),
                None => None,
            };
            // if expiry and datetime.now() < expiry: hit
            if let Some(expiry) = expiry {
                if Local::now().naive_local() < expiry {
                    logger::debug(&format!("Search cache hit for query: {query}"));
                    let results = cached_data
                        .get("data")
                        .and_then(|v| v.as_array())
                        .cloned()
                        .unwrap_or_default();
                    _record_query(query, !results.is_empty(), true);
                    return Ok(Some(results));
                }
            }
            Ok(None)
        })();
        match read {
            Ok(Some(results)) => return results,
            Ok(None) => {
                let _ = std::fs::remove_file(&cache_file);
                SEARCH_CACHE_INDEX.lock().unwrap().shift_remove(&cache_key);
            }
            Err(e) => {
                logger::warning(&format!("Failed to read search cache for {query}: {e}"));
                let _ = std::fs::remove_file(&cache_file);
                SEARCH_CACHE_INDEX.lock().unwrap().shift_remove(&cache_key);
            }
        }
    }

    logger::debug(&format!("Search cache miss for query: {query}"));

    if search_provider == "disabled" {
        logger::info("Search is disabled via admin settings");
        return Vec::new();
    }

    let provider_chain = _build_provider_chain(&search_provider);

    let mut results: Vec<Value> = Vec::new();
    'outer: for provider_name in &provider_chain {
        for attempt in 0..2 {
            // try: results = _call_provider(...); if results: break
            // The Rust providers swallow their own exceptions (return []), so
            // the Python's `except (NetworkError, ParseError, RateLimitError)`
            // and the broad `except Exception` are effectively no-ops here —
            // _call_provider never panics, so a failed call just yields [].
            logger::info(&format!(
                "Attempting {provider_name} search (attempt {})",
                attempt + 1
            ));
            results = _call_provider(provider_name, query, count, time_filter);
            if !results.is_empty() {
                logger::info(&format!(
                    "{provider_name} search succeeded with {} results",
                    results.len()
                ));
                break;
            }
        }
        if !results.is_empty() {
            break 'outer;
        }
    }

    let success = !results.is_empty();
    _record_query(query, success, false);

    if success {
        results = rank_search_results(query, results);
        let write = (|| -> Result<(), String> {
            // expiry = datetime.now() + _cache_duration_for_query(query)
            let now = Local::now().naive_local();
            let expiry = now + cache_duration_for_query(query);
            let cache_data = json!({
                "timestamp": iso(now),
                "expiry": iso(expiry),
                "data": results,
            });
            let s = serde_json::to_string(&cache_data).map_err(|e| e.to_string())?;
            std::fs::write(&cache_file, s).map_err(|e| e.to_string())?;
            let mut index = SEARCH_CACHE_INDEX.lock().unwrap();
            index.insert(cache_key.clone(), Local::now());
            cleanup_cache(&SEARCH_CACHE_DIR, &mut index, Duration::hours(1));
            Ok(())
        })();
        if let Err(e) = write {
            logger::warning(&format!("Failed to write search cache for {query}: {e}"));
        }
    }

    if !success {
        logger::error(&format!("All search providers failed for query: {query}"));
    }

    results
}

/// `datetime.fromisoformat` — cache.rs writes via chrono Local naive isoformat.
fn parse_iso(s: &str) -> Result<chrono::NaiveDateTime, String> {
    chrono::NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S%.f")
        .or_else(|_| chrono::NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S"))
        .map_err(|e| e.to_string())
}

/// `datetime.isoformat()` — microsecond precision, matching cache.rs.
fn iso(dt: chrono::NaiveDateTime) -> String {
    dt.format("%Y-%m-%dT%H:%M:%S%.6f").to_string()
}

// ----------------------------------------------------------------------
// Cache invalidation
// ----------------------------------------------------------------------

/// Invalidate cached search results. `None` clears all, otherwise just the given query.
pub fn invalidate_search_cache(query: Option<&str>) {
    match query {
        None => {
            // for file in SEARCH_CACHE_DIR.glob("*.cache"): unlink
            if let Ok(entries) = std::fs::read_dir(&*SEARCH_CACHE_DIR) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    if path.extension().and_then(|e| e.to_str()) == Some("cache") {
                        if let Err(e) = std::fs::remove_file(&path) {
                            error_logger::warning(&format!(
                                "Failed to delete cache file {}: {e}",
                                path.display()
                            ));
                        }
                    }
                }
            }
            SEARCH_CACHE_INDEX.lock().unwrap().clear();
            logger::info("All search cache entries have been cleared.");
        }
        Some(query) => {
            // Match the key the write path stores: searxng_search_results replaces
            // the caller's default count with the configured _get_result_count()
            // (default 5), so a hardcoded "|10|None" never matched a real entry.
            // cache_key = generate_cache_key(f"{query}|{_get_result_count()}|None")
            let cache_key = generate_cache_key(&format!("{query}|{}|None", _get_result_count()));
            let cache_file = SEARCH_CACHE_DIR.join(format!("{cache_key}.cache"));
            if cache_file.exists() {
                match std::fs::remove_file(&cache_file) {
                    Ok(()) => {
                        SEARCH_CACHE_INDEX.lock().unwrap().shift_remove(&cache_key);
                        logger::info(&format!(
                            "Cache entry for query '{query}' has been invalidated."
                        ));
                    }
                    Err(e) => {
                        error_logger::warning(&format!(
                            "Failed to delete cache file for query '{query}': {e}"
                        ));
                    }
                }
            } else {
                logger::info(&format!("No cache entry found for query '{query}'."));
            }
        }
    }
}

// ----------------------------------------------------------------------
// Comprehensive web search (with advanced filtering)
// ----------------------------------------------------------------------

/// `urlparse(url).netloc.lower()` — authority between `//` and the first
/// `/`/`?`/`#`. Empty when there is no `scheme://`.
fn netloc_lower(url: &str) -> String {
    let rest = match url.split_once("://") {
        Some((_s, r)) => r,
        None => return String::new(),
    };
    let end = rest.find(['/', '?', '#']).unwrap_or(rest.len());
    rest[..end].to_lowercase()
}

/// Sort key for a fetched-content block: its `source_index`, or `fallback`
/// (`len(search_results) + 1`) when the index is missing/null/0.
///
/// `key=lambda c: c.get("source_index") or len(search_results) + 1` — Python's
/// `or` treats both `None` and `0` as falsy, so both route to the fallback.
fn content_sort_key(content: &Value, fallback: i64) -> i64 {
    match content.get("source_index").and_then(|v| v.as_i64()) {
        Some(i) if i != 0 => i,
        _ => fallback,
    }
}

/// Block label for a fetched-content block:
/// `f"[CONTENT {_idx}]" if _idx else "[CONTENT]"` — `None`/`0` -> bare `[CONTENT]`.
fn content_label(content: &Value) -> String {
    match content.get("source_index").and_then(|v| v.as_i64()) {
        Some(i) if i != 0 => format!("[CONTENT {i}]"),
        _ => "[CONTENT]".to_string(),
    }
}

/// Perform comprehensive web search with content fetching and advanced filtering.
///
/// Returns the formatted report `String`, or `(report, sources)` when
/// `return_sources` is set, via the `ComprehensiveResult` enum.
///
/// Python defaults: `max_pages=3`, `max_workers=4`, `time_filter=None`,
/// `domain_whitelist=None`, `domain_blacklist=None`, `content_type=None`,
/// `language=None`, `min_content_length=0`, `return_sources=False`.
#[allow(clippy::too_many_arguments)]
pub fn comprehensive_web_search(
    query: &str,
    max_pages: i64,
    max_workers: usize,
    time_filter: Option<&str>,
    domain_whitelist: Option<&HashSet<String>>,
    domain_blacklist: Option<&HashSet<String>>,
    content_type: Option<&str>,
    language: Option<&str>,
    min_content_length: i64,
    return_sources: bool,
) -> ComprehensiveResult {
    logger::info(&format!("Starting comprehensive search for: {query}"));
    if let Some(tf) = time_filter {
        logger::info(&format!("Applying time filter: {tf}"));
    }

    let settings = _get_search_settings();
    let search_provider = settings_search_provider(&settings);
    let result_count = _get_result_count();

    let wrap = |msg: String| -> ComprehensiveResult {
        if return_sources {
            ComprehensiveResult::WithSources(msg, Vec::new())
        } else {
            ComprehensiveResult::Report(msg)
        }
    };

    if search_provider == "disabled" {
        logger::info("Search is disabled via admin settings");
        return wrap("Web search is disabled by the administrator.".to_string());
    }

    // fetch_count = max(result_count, max_pages)
    let fetch_count = std::cmp::max(result_count, max_pages);

    let provider_chain = _build_provider_chain(&search_provider);

    let mut search_results: Vec<Value> = Vec::new();
    // provider_attempts: provider -> "ok N" | "empty" | "error: ..."; an IndexMap
    // preserves insertion order so the tally string matches the Python dict order.
    let mut provider_attempts: indexmap::IndexMap<String, String> = indexmap::IndexMap::new();
    for provider_name in &provider_chain {
        let mut empty = false;
        for attempt in 0..2 {
            // The Rust providers never raise; a failed call returns []. The
            // Python's per-attempt `last_err` is therefore never set here (the
            // providers internally log their own errors), so the tally records
            // "empty" rather than "error: ..." — matching what actually happens
            // when a provider call fails internally and returns [].
            search_results = _call_provider(provider_name, query, fetch_count, time_filter);
            if !search_results.is_empty() {
                provider_attempts
                    .insert(provider_name.clone(), format!("ok ({})", search_results.len()));
                logger::info(&format!(
                    "Comprehensive search: {provider_name} returned {} results",
                    search_results.len()
                ));
                break;
            }
            // Empty result — try once more.
            empty = true;
            let _ = attempt;
        }
        if !search_results.is_empty() {
            break;
        }
        if empty {
            provider_attempts.insert(provider_name.clone(), "empty".to_string());
        }
    }

    if search_results.is_empty() {
        // tally = ", ".join(f"{p}:{r}" ...) or "no providers configured"
        let tally = if provider_attempts.is_empty() {
            "no providers configured".to_string()
        } else {
            provider_attempts
                .iter()
                .map(|(p, r)| format!("{p}:{r}"))
                .collect::<Vec<_>>()
                .join(", ")
        };
        let any_errors = provider_attempts.values().any(|r| r.starts_with("error"));
        let msg = if any_errors {
            let m = format!("Web search failed — all providers errored or returned empty. Tried: {tally}");
            logger::error(&m);
            m
        } else {
            let m = format!(
                "No search results found. Tried: {tally}. \
All providers returned empty — possibly a niche query or upstream rate-limiting; \
rephrasing or using the browser tool for a specific URL may help."
            );
            logger::warning(&m);
            m
        };
        return wrap(msg);
    }

    let search_results = rank_search_results(query, search_results);

    // url_passes_filters(url)
    let url_passes_filters = |url: &str| -> bool {
        let netloc = netloc_lower(url);
        if let Some(wl) = domain_whitelist {
            if !wl.contains(&netloc) {
                return false;
            }
        }
        if let Some(bl) = domain_blacklist {
            if bl.contains(&netloc) {
                return false;
            }
        }
        if let Some(ct) = content_type.filter(|s| !s.is_empty()) {
            let ct = ct.to_lowercase();
            let url_lc = url.to_lowercase();
            if ct == "article" {
                if !["article", "blog", "news", "post"].iter().any(|k| url_lc.contains(k)) {
                    return false;
                }
            } else if ct == "forum" {
                if !["forum", "discussion", "thread", "topic"].iter().any(|k| url_lc.contains(k)) {
                    return false;
                }
            } else if ct == "academic"
                && !["pdf", "doi", "scholar", "arxiv", "journal", "research"]
                    .iter()
                    .any(|k| url_lc.contains(k))
            {
                return false;
            }
        }
        if let Some(lang) = language.filter(|s| !s.is_empty()) {
            let lang_pat = lang.to_lowercase();
            let url_lc = url.to_lowercase();
            if !(url_lc.contains(&format!("/{lang_pat}/"))
                || url_lc.contains(&format!("?lang={lang_pat}"))
                || url_lc.contains(&format!("&lang={lang_pat}")))
            {
                return false;
            }
        }
        true
    };

    // filtered_urls = [r["url"] for r in search_results[:max_pages] if url_passes_filters(r["url"])]
    let max_pages_n = if max_pages < 0 { 0 } else { max_pages as usize };
    let filtered_urls: Vec<String> = search_results
        .iter()
        .take(max_pages_n)
        .filter_map(|r| {
            let url = r.get("url").and_then(|v| v.as_str()).unwrap_or("").to_string();
            if url_passes_filters(&url) {
                Some(url)
            } else {
                None
            }
        })
        .collect();

    if filtered_urls.is_empty() {
        logger::warning("All URLs filtered out by advanced criteria");
        return wrap("No suitable results after applying filters.".to_string());
    }

    // _source_list = [{"url","title"} for r in search_results if r.get("url")]
    let source_list: Vec<Value> = search_results
        .iter()
        .filter(|r| !r.get("url").and_then(|v| v.as_str()).unwrap_or("").is_empty())
        .map(|r| {
            json!({
                "url": r.get("url").and_then(|v| v.as_str()).unwrap_or(""),
                "title": r.get("title").and_then(|v| v.as_str()).unwrap_or(""),
            })
        })
        .collect();

    // Map each URL to its [i] number in the sources list so fetched content
    // blocks can be labeled with the SAME index the model cites.
    // _url_index = {r["url"]: i for i, r in enumerate(search_results, 1) if r.get("url")}
    // Python dict comprehension keeps the LAST i for a duplicated url; insert in
    // order so a later (higher) index wins, matching that semantics.
    let mut url_index: std::collections::HashMap<String, usize> = std::collections::HashMap::new();
    for (i, r) in search_results.iter().enumerate() {
        let url = r.get("url").and_then(|v| v.as_str()).unwrap_or("");
        if !url.is_empty() {
            url_index.insert(url.to_string(), i + 1);
        }
    }

    // Fetch content in parallel. ThreadPoolExecutor(max_workers) -> a bounded
    // scoped-thread fan-out. `as_completed` order is non-deterministic in Python,
    // so collecting in any order is parity-safe. We chunk the URL list into
    // `max_workers`-sized batches to cap concurrency.
    let workers = if max_workers == 0 { 1 } else { max_workers };
    let mut fetched_content: Vec<Value> = Vec::new();
    let mut idx = 0usize;
    while idx < filtered_urls.len() {
        let end = std::cmp::min(idx + workers, filtered_urls.len());
        let batch = &filtered_urls[idx..end];
        let batch_results: Vec<Option<Value>> = std::thread::scope(|scope| {
            let handles: Vec<_> = batch
                .iter()
                .map(|url| {
                    // Keep the URL the fetch was launched for: redirects can
                    // change result["url"] and completion order is arbitrary, so
                    // the source_index must be looked up by the launch URL.
                    let launch_url = url.clone();
                    let h = scope.spawn(move || {
                        // fetch_webpage_content(url, 8, retry_attempt=0)
                        fetch_webpage_content(&launch_url, 8, 0)
                    });
                    (url.clone(), h)
                })
                .collect();
            handles
                .into_iter()
                .map(|(launch_url, h)| match h.join() {
                    Ok(mut result) => {
                        // if result["success"] and result["content"] and len >= min_content_length
                        let success = result.get("success").and_then(|v| v.as_bool()).unwrap_or(false);
                        let content = result.get("content").and_then(|v| v.as_str()).unwrap_or("");
                        if success
                            && !content.is_empty()
                            && (content.chars().count() as i64) >= min_content_length
                        {
                            // result["source_index"] = _url_index.get(url)
                            // (None -> JSON null when the URL has no mapping).
                            let src_idx = match url_index.get(&launch_url) {
                                Some(i) => json!(*i),
                                None => Value::Null,
                            };
                            if let Some(obj) = result.as_object_mut() {
                                obj.insert("source_index".to_string(), src_idx);
                            }
                            Some(result)
                        } else {
                            None
                        }
                    }
                    Err(_) => {
                        // The Python logs the per-URL exception here; a panicked
                        // fetch thread is the analogue. fetch_webpage_content
                        // itself never panics, so this is unreachable in practice.
                        logger::error("Exception while fetching a page");
                        None
                    }
                })
                .collect()
        });
        for r in batch_results.into_iter().flatten() {
            fetched_content.push(r);
        }
        idx = end;
    }

    logger::info(&format!(
        "Successfully fetched content from {} pages",
        fetched_content.len()
    ));

    // Format results
    let mut output_parts: Vec<String> = Vec::new();
    let sget = |r: &Value, k: &str| -> String {
        r.get(k).and_then(|v| v.as_str()).unwrap_or("").to_string()
    };

    if !search_results.is_empty() {
        output_parts.push("```sources".to_string());
        for (i, result) in search_results.iter().enumerate() {
            output_parts.push(format!("[{}] {}", i + 1, sget(result, "title")));
            output_parts.push(format!("    {}", sget(result, "url")));
            let age = sget(result, "age");
            if !age.is_empty() {
                output_parts.push(format!("    {age}"));
            }
        }
        output_parts.push("```".to_string());
        output_parts.push(String::new());
    }

    let bar = "=".repeat(70);
    output_parts.push(bar.clone());
    output_parts.push("WEB SEARCH RESULTS AND FETCHED CONTENT".to_string());
    output_parts.push(format!("Query: {query}"));
    output_parts.push(format!(
        "Searched {} results, fetched {} pages",
        search_results.len(),
        fetched_content.len()
    ));
    output_parts.push(bar.clone());
    output_parts.push(String::new());

    output_parts.push("SEARCH RESULTS SUMMARY:".to_string());
    output_parts.push("-".repeat(50));
    for (i, result) in search_results.iter().enumerate() {
        output_parts.push(format!("\n[{}] {}", i + 1, sget(result, "title")));
        output_parts.push(format!("    URL: {}", sget(result, "url")));
        // f"    Snippet: {result['snippet'][:200]}..." — slice by chars.
        let snippet = sget(result, "snippet");
        let snippet_200: String = snippet.chars().take(200).collect();
        output_parts.push(format!("    Snippet: {snippet_200}..."));
        let age = sget(result, "age");
        if !age.is_empty() {
            output_parts.push(format!("    Age: {age}"));
        }
    }

    if !fetched_content.is_empty() {
        output_parts.push(format!("\n{bar}"));
        output_parts.push("FETCHED PAGE CONTENT:".to_string());
        output_parts.push("-".repeat(50));

        // Emit blocks in source order, numbered with the same [i] as the
        // sources list, so [CONTENT 2] really is content from source [2].
        // Before this, blocks were numbered 1..N in fetch COMPLETION order,
        // which matched neither the sources list nor each other run to run.
        // fetched_content.sort(key=lambda c: c.get("source_index") or len(search_results) + 1)
        // `source_index` is a truthy 1-based index; None/0 sort to len+1. Python's
        // sort is stable, so equal keys preserve insertion order.
        let sort_fallback = (search_results.len() + 1) as i64;
        fetched_content.sort_by(|a, b| {
            content_sort_key(a, sort_fallback).cmp(&content_sort_key(b, sort_fallback))
        });

        for content in fetched_content.iter() {
            // _idx = content.get("source_index"); _label = f"[CONTENT {_idx}]" if _idx else "[CONTENT]"
            // (`if _idx` is falsy for None *and* 0).
            let label = content_label(content);
            output_parts.push(format!("\n{label} From: {}", sget(content, "url")));
            output_parts.push(format!("Title: {}", sget(content, "title")));
            output_parts.push("-".repeat(30));

            let full_content = sget(content, "content");
            // text = content["content"][:3000]; if len > 3000: text += "... [truncated]"
            let chars: Vec<char> = full_content.chars().collect();
            let mut text: String = chars.iter().take(3000).collect();
            if chars.len() > 3000 {
                text.push_str("... [truncated]");
            }
            output_parts.push(text);

            let key_points = extract_key_points(&full_content);
            if !key_points.is_empty() {
                output_parts.push("\nKey Points:".to_string());
                for pt in key_points.iter().take(5) {
                    output_parts.push(format!("- {pt}"));
                }
            }

            let tldr = get_tldr(&full_content, 3);
            if !tldr.is_empty() {
                output_parts.push("\nTL;DR:".to_string());
                output_parts.push(tldr);
            }

            let quotes = extract_quotes(&full_content);
            if !quotes.is_empty() {
                output_parts.push("\nImportant Quotes:".to_string());
                for q in quotes.iter().take(3) {
                    output_parts.push(format!("\u{201c}{q}\u{201d}"));
                }
            }

            let stats = extract_statistics(&full_content);
            if !stats.is_empty() {
                output_parts.push("\nData / Statistics:".to_string());
                for s in stats.iter().take(5) {
                    output_parts.push(format!("- {s}"));
                }
            }

            output_parts.push(String::new());
        }
    }

    output_parts.push(bar.clone());
    output_parts.push("END OF WEB SEARCH RESULTS".to_string());
    output_parts.push(bar);

    let instructions = "\n\nIMPORTANT INSTRUCTIONS:\n\
1. Use the above web search results and fetched content to answer the user's question\n\
2. Prioritize information from the FETCHED PAGE CONTENT section as it contains actual page data\n\
3. Cross-reference multiple sources when possible\n\
4. If the information is time-sensitive, pay attention to the age of the results\n\
5. Be explicit if the search results don't contain sufficient information to fully answer the question";
    output_parts.push(instructions.to_string());

    let report = output_parts.join("\n");
    if return_sources {
        ComprehensiveResult::WithSources(report, source_list)
    } else {
        ComprehensiveResult::Report(report)
    }
}

/// The two-shaped return of `comprehensive_web_search`: a bare report `String`
/// (`return_sources=False`) or `(report, sources)` (`return_sources=True`).
///
/// Python returns `str` or `(str, list)` based on the `return_sources` flag; the
/// Rust models that union explicitly so callers `match` on the shape.
pub enum ComprehensiveResult {
    /// `return_sources=False` — just the formatted report.
    Report(String),
    /// `return_sources=True` — `(report, sources)`.
    WithSources(String, Vec<Value>),
}

impl ComprehensiveResult {
    /// The formatted report text (present in both shapes).
    pub fn report(&self) -> &str {
        match self {
            ComprehensiveResult::Report(s) => s,
            ComprehensiveResult::WithSources(s, _) => s,
        }
    }

    /// Consume into the report `String`.
    pub fn into_report(self) -> String {
        match self {
            ComprehensiveResult::Report(s) => s,
            ComprehensiveResult::WithSources(s, _) => s,
        }
    }

    /// The source list, or an empty slice for the `Report` shape.
    pub fn sources(&self) -> &[Value] {
        match self {
            ComprehensiveResult::Report(_) => &[],
            ComprehensiveResult::WithSources(_, src) => src,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── #1 SECURITY: secret-key detection & stripping ──

    #[test]
    fn is_secret_key_matches_credential_suffixes() {
        // name.endswith(("_api_key", "_key", "_token", "_secret"))
        assert!(_is_secret_key("brave_api_key"));
        assert!(_is_secret_key("search_api_key"));
        assert!(_is_secret_key("google_pse_key"));
        assert!(_is_secret_key("tavily_api_key"));
        assert!(_is_secret_key("serper_api_key"));
        assert!(_is_secret_key("session_token"));
        assert!(_is_secret_key("client_secret"));
        // "has_api_key" also ends with `_api_key`, so the predicate matches it —
        // exactly like Python's `endswith(("_api_key", ...))`. It is NOT leaked,
        // because the retain guard in get_search_config only strips STRING-valued
        // secret keys and `has_api_key` is a bool.
        assert!(_is_secret_key("has_api_key"));
        // Non-secret config keys must NOT match.
        assert!(!_is_secret_key("primary_provider"));
        assert!(!_is_secret_key("active_provider"));
        assert!(!_is_secret_key("result_count"));
        assert!(!_is_secret_key("search_url"));
    }

    #[test]
    fn get_search_config_retain_strips_string_secrets_only() {
        // Mirror the exact predicate get_search_config applies before returning:
        // drop only string-valued secret keys; keep booleans / non-secret strings.
        let mut config = Map::new();
        config.insert("primary_provider".to_string(), json!("searxng"));
        config.insert("active_provider".to_string(), json!("brave"));
        config.insert("has_api_key".to_string(), json!(true));
        config.insert("result_count".to_string(), json!(5));
        config.insert("search_url".to_string(), json!("https://example.test"));
        // A leaked credential (string + secret suffix) must be removed.
        config.insert("brave_api_key".to_string(), json!("super-secret"));
        config.insert("serper_api_key".to_string(), json!("another-secret"));
        // A secret-named key that is NOT a string is preserved (matches Python's
        // `isinstance(v, str) and _is_secret_key(k)` guard).
        config.insert("has_brave_key".to_string(), json!(true));

        config.retain(|k, v| !(v.is_string() && _is_secret_key(k)));

        assert!(!config.contains_key("brave_api_key"));
        assert!(!config.contains_key("serper_api_key"));
        assert_eq!(config.get("primary_provider"), Some(&json!("searxng")));
        assert_eq!(config.get("active_provider"), Some(&json!("brave")));
        assert_eq!(config.get("has_api_key"), Some(&json!(true)));
        assert_eq!(config.get("result_count"), Some(&json!(5)));
        assert_eq!(config.get("search_url"), Some(&json!("https://example.test")));
        assert_eq!(config.get("has_brave_key"), Some(&json!(true)));
    }

    #[test]
    fn get_search_config_never_returns_a_string_secret() {
        // End-to-end on the real function: whatever the ambient settings, the
        // returned object must never carry a string-valued secret key.
        let cfg = get_search_config();
        let obj = cfg.as_object().expect("config is an object");
        for (k, v) in obj {
            assert!(
                !(v.is_string() && _is_secret_key(k)),
                "get_search_config leaked secret key {k:?} = {v:?}",
            );
        }
        // Presence flag is always exposed (boolean, never the secret itself).
        assert!(obj.get("has_api_key").map(|v| v.is_boolean()).unwrap_or(false));
    }

    #[test]
    fn update_search_config_never_stores_raw_key() {
        // Provider keys are read on demand, not cached; update_search_config must
        // not insert a raw credential into SEARCH_CONFIG (the #1661 leak path).
        update_search_config(Some("leaked-brave-key"));
        let cfg = SEARCH_CONFIG.lock().unwrap();
        assert!(!cfg.contains_key("brave_api_key"));
        for (k, v) in cfg.iter() {
            assert!(
                !(v.is_string() && _is_secret_key(k)),
                "update_search_config stored secret key {k:?}",
            );
        }
        // The None call is also a no-op.
        drop(cfg);
        update_search_config(None);
        assert!(!SEARCH_CONFIG.lock().unwrap().contains_key("brave_api_key"));
    }

    // ── #3: fetched-content source-index labeling & ordering ──

    #[test]
    fn content_label_uses_source_index_or_bare() {
        assert_eq!(content_label(&json!({"source_index": 2})), "[CONTENT 2]");
        assert_eq!(content_label(&json!({"source_index": 1})), "[CONTENT 1]");
        // None (null) and 0 are falsy in Python's `if _idx` -> bare label.
        assert_eq!(content_label(&json!({"source_index": Value::Null})), "[CONTENT]");
        assert_eq!(content_label(&json!({"source_index": 0})), "[CONTENT]");
        assert_eq!(content_label(&json!({})), "[CONTENT]");
    }

    #[test]
    fn content_sort_key_routes_falsy_to_fallback() {
        let fallback = 99;
        assert_eq!(content_sort_key(&json!({"source_index": 3}), fallback), 3);
        assert_eq!(content_sort_key(&json!({"source_index": 0}), fallback), fallback);
        assert_eq!(content_sort_key(&json!({"source_index": Value::Null}), fallback), fallback);
        assert_eq!(content_sort_key(&json!({}), fallback), fallback);
    }

    #[test]
    fn fetched_content_sorts_by_source_index_stable() {
        // Simulate fetch-COMPLETION order (arbitrary) and confirm blocks reorder
        // to ascending source_index, with None/0 sinking to the fallback slot
        // and stable order preserved among equal keys.
        let search_results_len = 3usize;
        let fallback = (search_results_len + 1) as i64;
        let mut fetched: Vec<Value> = vec![
            json!({"url": "u3", "source_index": 3}),
            json!({"url": "u_none", "source_index": Value::Null}),
            json!({"url": "u1", "source_index": 1}),
            json!({"url": "u_none2", "source_index": Value::Null}),
            json!({"url": "u2", "source_index": 2}),
        ];
        fetched.sort_by(|a, b| {
            content_sort_key(a, fallback).cmp(&content_sort_key(b, fallback))
        });
        let order: Vec<&str> = fetched
            .iter()
            .map(|c| c.get("url").and_then(|v| v.as_str()).unwrap_or(""))
            .collect();
        // 1,2,3 then the two None entries in their original relative order.
        assert_eq!(order, vec!["u1", "u2", "u3", "u_none", "u_none2"]);
        // Labels follow the index, bare for the None entries.
        assert_eq!(content_label(&fetched[0]), "[CONTENT 1]");
        assert_eq!(content_label(&fetched[2]), "[CONTENT 3]");
        assert_eq!(content_label(&fetched[3]), "[CONTENT]");
    }
}
