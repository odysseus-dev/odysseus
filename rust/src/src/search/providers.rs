// src/search/providers.rs  <- src/search/providers.py
//! Search provider implementations: SearXNG, Brave, DuckDuckGo, Google PSE, Tavily, Serper.
//!
//! `httpx` -> `reqwest::blocking` (the `blocking` feature is on; precedent
//! `caldav_sync.rs`). `bs4`/`html.parser` -> `scraper` (precedent
//! `email_thread_parser.rs`). A `429` makes the Python raise `RateLimitError`
//! and immediately catch it in the same function returning `[]`, so the Rust
//! short-circuits to an empty `Vec` at that point (no error propagation).
//!
//! The DuckDuckGo `DDGS` PyPI library has no Rust equivalent; the Python takes
//! the `_html_fallback` path on `ImportError`, so the Rust ALWAYS takes the
//! HTML-fallback path — this is faithful to the Python's import-failure branch,
//! not a fake stub.

use crate::pylog as logger;
use crate::pyos as os;
use crate::src::constants::SEARXNG_INSTANCE;
use crate::src::search::analytics::error_logger;
use crate::src::search::query::build_enhanced_query;
use indexmap::IndexMap;
use once_cell::sync::Lazy;
use scraper::{Html, Selector};
use serde_json::{json, Value};

// logger = logging.getLogger(__name__) -> crate::pylog shim.

/// `REQUEST_TIMEOUT = 20`.
pub const REQUEST_TIMEOUT: u64 = 20;

/// Provider registry — maps setting value to `(label, needs_key, needs_url)`.
///
/// Python `PROVIDER_INFO` is a dict (insertion-ordered); `IndexMap` preserves
/// that order so iteration matches the Python.
pub static PROVIDER_INFO: Lazy<IndexMap<&'static str, (&'static str, bool, bool)>> = Lazy::new(|| {
    let mut m: IndexMap<&'static str, (&'static str, bool, bool)> = IndexMap::new();
    m.insert("searxng", ("SearXNG", false, true));
    m.insert("brave", ("Brave Search", true, false));
    m.insert("duckduckgo", ("DuckDuckGo", false, false));
    m.insert("google_pse", ("Google PSE", true, false));
    m.insert("tavily", ("Tavily", true, false));
    m.insert("serper", ("Serper", true, false));
    m.insert("disabled", ("Disabled", false, false));
    m
});

// ── Settings helpers ──

/// Return search settings from admin config, falling back to env defaults.
pub fn _get_search_settings() -> serde_json::Map<String, Value> {
    // try: from src.settings import load_settings; return load_settings()
    // except Exception: return {}
    // The Rust `load_settings` is infallible (always returns a complete map).
    crate::src::settings::load_settings()
}

/// `(settings.get(key) or "").strip()` — None/non-string/empty -> empty string.
fn settings_str(settings: &serde_json::Map<String, Value>, key: &str) -> String {
    settings
        .get(key)
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string()
}

/// Return the active search API URL from admin settings, falling back to env var.
pub fn _get_search_instance() -> String {
    let settings = _get_search_settings();
    // url = (settings.get("search_url") or "").strip()
    let url = settings_str(&settings, "search_url");
    if !url.is_empty() {
        // return url.rstrip("/")
        return url.trim_end_matches('/').to_string();
    }
    SEARXNG_INSTANCE.clone()
}

/// Return the API key for a specific provider, with legacy fallback.
pub fn _get_provider_key(provider: &str) -> String {
    let settings = _get_search_settings();
    // key_map = {...}; field = key_map.get(provider, "")
    let field = match provider {
        "brave" => "brave_api_key",
        "google_pse" => "google_pse_key",
        "tavily" => "tavily_api_key",
        "serper" => "serper_api_key",
        _ => "",
    };
    if !field.is_empty() {
        let val = settings_str(&settings, field);
        if !val.is_empty() {
            return val;
        }
    }
    // Legacy fallback: old shared search_api_key field.
    settings_str(&settings, "search_api_key")
}

/// Return configured result count, default 5.
pub fn _get_result_count() -> i64 {
    let settings = _get_search_settings();
    // try: return int(settings.get("search_result_count", 5))
    // except (ValueError, TypeError): return 5
    match settings.get("search_result_count") {
        None => 5,
        Some(Value::Number(n)) => {
            // int(int) / int(float-truncates) — Python int() truncates floats.
            if let Some(i) = n.as_i64() {
                i
            } else if let Some(f) = n.as_f64() {
                f.trunc() as i64
            } else {
                5
            }
        }
        Some(Value::String(s)) => s.trim().parse::<i64>().unwrap_or(5),
        // bool -> int() in Python yields 0/1; JSON bool would also work but the
        // settings schema never stores bool here. Default on anything else.
        _ => 5,
    }
}

// ── SafeSearch ──

/// Canonical SafeSearch levels: "strict" (default), "moderate", "off".
/// Each provider has its own knob name and value space — see `_safesearch_for`.
const SAFESEARCH_LEVELS: [&str; 3] = ["strict", "moderate", "off"];

/// Normalize a raw `search_safesearch` setting value into a canonical level.
///
/// Pure helper backing [`_get_safesearch_level`]; takes the raw string so the
/// level logic is testable without touching the live settings file. Mirrors the
/// Python `(settings.get("search_safesearch") or "strict").strip().lower()` plus
/// the alias table.
fn normalize_safesearch_level(raw: &str) -> &'static str {
    let raw = raw.trim().to_lowercase();
    // if raw in _SAFESEARCH_LEVELS: return raw
    if let Some(&level) = SAFESEARCH_LEVELS.iter().find(|&&l| l == raw.as_str()) {
        return level;
    }
    // aliases = {...}; return aliases.get(raw, "strict")
    match raw.as_str() {
        "on" | "high" | "2" => "strict",
        "medium" | "1" | "default" => "moderate",
        "none" | "disabled" | "0" => "off",
        _ => "strict",
    }
}

/// Return configured SafeSearch level normalized to a canonical value.
pub fn _get_safesearch_level() -> &'static str {
    let settings = _get_search_settings();
    // raw = (settings.get("search_safesearch") or "strict").strip().lower()
    // `settings_str` already trims; an empty/missing/non-string value yields ""
    // which normalizes to the "strict" default below.
    let raw = settings_str(&settings, "search_safesearch");
    if raw.is_empty() {
        return "strict";
    }
    normalize_safesearch_level(&raw)
}

/// Translate a canonical SafeSearch `level` into a provider-specific value.
///
/// Pure helper backing [`_safesearch_for`]; takes the level explicitly so the
/// per-provider mapping is testable without the live settings file. Returns
/// `None` where the Python returns `None` (provider has no param for that level).
fn safesearch_for_level(provider: &str, level: &str) -> Option<&'static str> {
    match provider {
        // {"strict": "2", "moderate": "1", "off": "0"}[level]
        "searxng" => Some(match level {
            "strict" => "2",
            "moderate" => "1",
            _ => "0",
        }),
        // brave: return level (verbatim canonical level)
        "brave" => Some(match level {
            "moderate" => "moderate",
            "off" => "off",
            _ => "strict",
        }),
        // {"strict": "on", "moderate": "moderate", "off": "off"}[level]
        "duckduckgo_lib" => Some(match level {
            "strict" => "on",
            "moderate" => "moderate",
            _ => "off",
        }),
        // {"strict": "1", "moderate": "-1", "off": "-2"}[level]
        "duckduckgo_html" => Some(match level {
            "strict" => "1",
            "moderate" => "-1",
            _ => "-2",
        }),
        // google_pse / serper: None if level == "off" else "active"
        "google_pse" | "serper" => {
            if level == "off" {
                None
            } else {
                Some("active")
            }
        }
        _ => None,
    }
}

/// Translate the canonical SafeSearch level into provider-specific values.
pub fn _safesearch_for(provider: &str) -> Option<&'static str> {
    let level = _get_safesearch_level();
    safesearch_for_level(provider, level)
}

// ── SearXNG ──

/// `_NEWS_HINTS`.
const NEWS_HINTS: [&str; 7] = ["news", "nyheter", "headlines", "breaking", "latest", "today", "idag"];

/// The instance's DEFAULT general engines (google/duckduckgo/brave/startpage/
/// wikipedia) are routinely rate-limited / CAPTCHA-blocked and return nothing,
/// so a plain general query comes back empty. Pin engines that actually respond
/// (verified working on this instance) so non-news queries get results without
/// enabling any third-party API fallback. Override via the SEARXNG_GENERAL_ENGINES
/// env var if the working set changes.
static GENERAL_ENGINES: Lazy<String> =
    Lazy::new(|| os::getenv("SEARXNG_GENERAL_ENGINES", "bing,mojeek,presearch"));

/// Build a blocking HTTP client with the given timeout (seconds).
fn http_client(timeout_secs: u64) -> Option<reqwest::blocking::Client> {
    reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(timeout_secs))
        .build()
        .ok()
}

/// `r.get("k", "")` over a JSON object value (string-or-empty).
fn jget_str(v: &Value, key: &str) -> String {
    v.get(key).and_then(|x| x.as_str()).unwrap_or("").to_string()
}

/// Search using SearXNG JSON API. Returns list of `{title, url, snippet}`.
///
/// `count` defaults to 10, `categories` to "general", `time_filter` to `None`.
pub fn searxng_search_api(
    query: &str,
    count: i64,
    categories: &str,
    time_filter: Option<&str>,
) -> Vec<Value> {
    let instance = _get_search_instance();
    // api_key = "" — the Python hardcodes an empty key (a runtime str), so the
    // Authorization header is never set. We preserve the (unconditionally false)
    // branch; `String` keeps `api_key` a runtime value (matching Python) rather
    // than a const literal.
    let api_key = String::new();
    let mut headers: IndexMap<String, String> = IndexMap::new();
    headers.insert("User-Agent".to_string(), "Mozilla/5.0".to_string());
    if !api_key.is_empty() {
        headers.insert("Authorization".to_string(), format!("Bearer {api_key}"));
    }

    // params = {"q": query, "format": "json", "language": "en"}
    // Use an IndexMap to preserve insertion order (matches Python dict order;
    // also drives the .get("language")/.get("engines") fallback checks below).
    let mut params: IndexMap<String, String> = IndexMap::new();
    params.insert("q".to_string(), query.to_string());
    params.insert("format".to_string(), "json".to_string());
    params.insert("language".to_string(), "en".to_string());
    // "safesearch": _safesearch_for("searxng") — always Some for searxng.
    if let Some(safe) = _safesearch_for("searxng") {
        params.insert("safesearch".to_string(), safe.to_string());
    }

    let q_lc = query.to_lowercase();
    // is_news = time_filter is not None or any(h in q_lc for h in _NEWS_HINTS)
    let is_news = time_filter.is_some() || NEWS_HINTS.iter().any(|h| q_lc.contains(h));
    if is_news && categories == "general" {
        params.insert("categories".to_string(), "news".to_string());
        // if time_filter in ("day", "week", "month", "year"):
        if matches!(time_filter, Some("day") | Some("week") | Some("month") | Some("year")) {
            let tf = time_filter.unwrap();
            let time_range = if tf == "day" || tf == "week" { "week" } else { tf };
            params.insert("time_range".to_string(), time_range.to_string());
        }
    } else {
        params.insert("categories".to_string(), categories.to_string());
        // if categories == "general" and _GENERAL_ENGINES:
        if categories == "general" && !GENERAL_ENGINES.is_empty() {
            params.insert("engines".to_string(), GENERAL_ENGINES.clone());
        }
    }

    // `count` is `usize`-clamped below; results[:count] in Python.
    let take = if count < 0 { 0 } else { count as usize };

    // def _parse_results(results): [...] for r in results[:count] if r.get("url")
    let parse_results = |results: &[Value]| -> Vec<Value> {
        results
            .iter()
            .take(take)
            .filter(|r| !jget_str(r, "url").is_empty())
            .map(|r| {
                json!({
                    "title": jget_str(r, "title"),
                    "url": jget_str(r, "url"),
                    "snippet": jget_str(r, "content"),
                })
            })
            .collect()
    };

    // def _run(search_params): httpx.get(.../search, params, headers, timeout=15)
    //   .raise_for_status(); data = .json(); return (_parse_results(...), data)
    // Returns Err on any HTTP/parse failure so the outer try/except can fall
    // back to the HTML scraper.
    let run = |search_params: &IndexMap<String, String>| -> Result<(Vec<Value>, Value), String> {
        let client = http_client(15).ok_or_else(|| "client build failed".to_string())?;
        let url = format!("{instance}/search");
        let query_pairs: Vec<(&str, &str)> =
            search_params.iter().map(|(k, v)| (k.as_str(), v.as_str())).collect();
        let mut req = client.get(&url).query(&query_pairs);
        for (k, v) in headers.iter() {
            req = req.header(k.as_str(), v.as_str());
        }
        let response = req.send().map_err(|e| e.to_string())?;
        // response.raise_for_status()
        if let Err(e) = response.error_for_status_ref() {
            return Err(e.to_string());
        }
        let data: Value = response.json().map_err(|e| e.to_string())?;
        let results = data.get("results").and_then(|v| v.as_array()).cloned().unwrap_or_default();
        Ok((parse_results(&results), data))
    };

    // Outer try: mirrors the Python `try` body; any Err drops to the HTML
    // fallback below.
    let attempt = || -> Result<Vec<Value>, String> {
        let mut active_params = params.clone();
        let (mut parsed, mut data) = run(&active_params)?;

        if parsed.is_empty() && is_news && categories == "general" {
            // Some self-hosted SearXNG configs have no working news engines.
            let mut fallback: IndexMap<String, String> = IndexMap::new();
            fallback.insert("q".to_string(), query.to_string());
            fallback.insert("format".to_string(), "json".to_string());
            fallback.insert("language".to_string(), "en".to_string());
            fallback.insert("categories".to_string(), "general".to_string());
            // "safesearch": _safesearch_for("searxng") — always Some for searxng.
            if let Some(safe) = _safesearch_for("searxng") {
                fallback.insert("safesearch".to_string(), safe.to_string());
            }
            if !GENERAL_ENGINES.is_empty() {
                fallback.insert("engines".to_string(), GENERAL_ENGINES.clone());
            }
            logger::info(&format!(
                "SearXNG news search returned 0 results for {query:?}; retrying general engines"
            ));
            active_params = fallback;
            let (p, d) = run(&active_params)?;
            parsed = p;
            data = d;
        }

        if parsed.is_empty() && active_params.contains_key("language") {
            let mut fallback = active_params.clone();
            fallback.shift_remove("language");
            logger::info(&format!(
                "SearXNG language-pinned search returned 0 results for {query:?}; retrying without language"
            ));
            active_params = fallback;
            let (p, d) = run(&active_params)?;
            parsed = p;
            data = d;
        }

        if parsed.is_empty() && active_params.contains_key("engines") {
            let mut fallback = active_params.clone();
            fallback.shift_remove("engines");
            logger::info(&format!(
                "SearXNG pinned engines returned 0 results for {query:?}; retrying default engines"
            ));
            let (p, d) = run(&fallback)?;
            parsed = p;
            data = d;
        }

        logger::info(&format!("SearXNG JSON API returned {} results for: {query}", parsed.len()));
        if parsed.is_empty() {
            // unresponsive = data.get("unresponsive_engines") if isinstance(data, dict) else None
            let unresponsive = data.get("unresponsive_engines");
            if let Some(u) = unresponsive {
                if !u.is_null() && u != &json!([]) && u != &json!({}) {
                    logger::info(&format!("SearXNG unresponsive engines for {query:?}: {u}"));
                }
            }
        }
        Ok(parsed)
    };

    match attempt() {
        Ok(parsed) => parsed,
        Err(e) => {
            logger::warning(&format!("SearXNG JSON API search failed: {e}"));
            let html_results = searxng_search(query, count);
            if !html_results.is_empty() {
                logger::info(&format!(
                    "SearXNG HTML fallback returned {} results for: {query}",
                    html_results.len()
                ));
            }
            html_results
        }
    }
}

/// Search using SearXNG instance - parsing HTML.
///
/// `max_results` defaults to 10.
pub fn searxng_search(query: &str, max_results: i64) -> Vec<Value> {
    let instance = _get_search_instance();
    // api_key = "" — hardcoded empty key (a runtime str in Python); `String`
    // keeps the (unconditionally false) Authorization branch a runtime check.
    let api_key = String::new();
    let mut req_headers: IndexMap<String, String> = IndexMap::new();
    req_headers.insert("User-Agent".to_string(), "Mozilla/5.0".to_string());
    if !api_key.is_empty() {
        req_headers.insert("Authorization".to_string(), format!("Bearer {api_key}"));
    }

    let take = if max_results < 0 { 0 } else { max_results as usize };

    let attempt = || -> Result<Vec<Value>, String> {
        let client = http_client(10).ok_or_else(|| "client build failed".to_string())?;
        let url = format!("{instance}/search");
        // params={"q": query, "safesearch": _safesearch_for("searxng")} — always
        // Some for searxng, so the param is always present.
        let mut query_pairs: Vec<(&str, &str)> = vec![("q", query)];
        let safe_searxng = _safesearch_for("searxng");
        if let Some(safe) = safe_searxng {
            query_pairs.push(("safesearch", safe));
        }
        let mut req = client.get(&url).query(&query_pairs);
        for (k, v) in req_headers.iter() {
            req = req.header(k.as_str(), v.as_str());
        }
        let response = req.send().map_err(|e| e.to_string())?;
        // if response.is_success:
        if !response.status().is_success() {
            // Python only parses on success; otherwise it falls through to
            // `return []` (no exception).
            return Ok(Vec::new());
        }
        let text = response.text().map_err(|e| e.to_string())?;
        let doc = Html::parse_document(&text);
        // article.result, h3 a, p.content
        let result_sel = Selector::parse("article.result").unwrap();
        let title_sel = Selector::parse("h3 a").unwrap();
        let snippet_sel = Selector::parse("p.content").unwrap();
        let mut results: Vec<Value> = Vec::new();
        for article in doc.select(&result_sel).take(take) {
            // title_elem = article.select_one("h3 a"); if not title_elem: continue
            let title_elem = match article.select(&title_sel).next() {
                Some(e) => e,
                None => continue,
            };
            // title = title_elem.get_text(strip=True)
            let title = collapse_text(&title_elem.text().collect::<String>());
            // url = title_elem.get("href", "")
            let href = title_elem.value().attr("href").unwrap_or("").to_string();
            // snippet_elem = article.select_one("p.content")
            let snippet = match article.select(&snippet_sel).next() {
                Some(e) => collapse_text(&e.text().collect::<String>()),
                None => String::new(),
            };
            results.push(json!({"title": title, "url": href, "snippet": snippet}));
        }
        logger::info(&format!("SearXNG search (HTML) returned {} results", results.len()));
        Ok(results)
    };

    match attempt() {
        Ok(results) => results,
        Err(e) => {
            logger::error(&format!("SearXNG search failed: {e}"));
            Vec::new()
        }
    }
}

/// `get_text(strip=True)` — collapse internal whitespace runs to single spaces
/// then strip the ends. BeautifulSoup `get_text(strip=True)` concatenates the
/// stripped text of each descendant string node; for the single-link / single-
/// paragraph cases here, collapsing whitespace + trimming is equivalent.
fn collapse_text(s: &str) -> String {
    s.split_whitespace().collect::<Vec<_>>().join(" ")
}

// ── Brave ──

/// Search using Brave API with key from admin settings or env var.
pub fn brave_search(query: &str, count: i64, time_filter: Option<&str>) -> Vec<Value> {
    // api_key = _get_provider_key("brave") or os.environ.get("DATA_BRAVE_API_KEY") or ""
    let mut api_key = _get_provider_key("brave");
    if api_key.is_empty() {
        api_key = os::getenv("DATA_BRAVE_API_KEY", "");
    }
    let mut config: serde_json::Map<String, Value> = serde_json::Map::new();
    config.insert("brave_api_key".to_string(), json!(api_key));
    _brave_search_impl(query, count, time_filter, Some(&config))
}

/// Core Brave API call. Returns a list of result dicts or an empty list on failure.
pub fn _brave_search_impl(
    query: &str,
    count: i64,
    time_filter: Option<&str>,
    search_config: Option<&serde_json::Map<String, Value>>,
) -> Vec<Value> {
    let enhanced_query = build_enhanced_query(query, time_filter);
    let empty = serde_json::Map::new();
    let config = search_config.unwrap_or(&empty);

    // brave_api_key = config.get("brave_api_key") or os.environ.get("DATA_BRAVE_API_KEY")
    let mut brave_api_key = config
        .get("brave_api_key")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string());
    if brave_api_key.is_none() {
        let env = os::getenv("DATA_BRAVE_API_KEY", "");
        if !env.is_empty() {
            brave_api_key = Some(env);
        }
    }

    let brave_api_key = match brave_api_key {
        Some(k) => k,
        None => {
            logger::warning("Brave API key not found, returning empty results for fallback");
            return Vec::new();
        }
    };

    let take = if count < 0 { 0 } else { count as usize };

    // params = {"q": enhanced_query, "count": count, "safesearch": _safesearch_for("brave")}
    let mut params: Vec<(String, String)> = vec![
        ("q".to_string(), enhanced_query.clone()),
        ("count".to_string(), count.to_string()),
    ];
    // _safesearch_for("brave") always returns Some(level).
    if let Some(safe) = _safesearch_for("brave") {
        params.push(("safesearch".to_string(), safe.to_string()));
    }
    // time_map identical keys -> just add freshness when in {day,week,month,year}.
    if let Some(tf) = time_filter {
        if matches!(tf, "day" | "week" | "month" | "year") {
            params.push(("freshness".to_string(), tf.to_string()));
        }
    }

    logger::info(&format!("Executing Brave search with query: {enhanced_query}"));

    let client = match http_client(REQUEST_TIMEOUT) {
        Some(c) => c,
        None => {
            error_logger::error("NetworkError during Brave search: client build failed");
            return Vec::new();
        }
    };
    let query_pairs: Vec<(&str, &str)> =
        params.iter().map(|(k, v)| (k.as_str(), v.as_str())).collect();
    let response = match client
        .get("https://api.search.brave.com/res/v1/web/search")
        .header("X-Subscription-Token", &brave_api_key)
        .header("Accept", "application/json")
        .query(&query_pairs)
        .send()
    {
        Ok(r) => r,
        // except httpx.RequestError -> NetworkError log + []
        Err(e) => {
            error_logger::error(&format!("NetworkError during Brave search: {e}"));
            return Vec::new();
        }
    };
    // if response.status_code == 429: raise RateLimitError -> caught -> []
    if response.status().as_u16() == 429 {
        error_logger::error("Brave rate limit hit");
        return Vec::new();
    }
    if let Err(e) = response.error_for_status_ref() {
        // raise_for_status() raises HTTPStatusError (a RequestError subclass) ->
        // caught by `except httpx.RequestError` -> NetworkError log + [].
        error_logger::error(&format!("NetworkError during Brave search: {e}"));
        return Vec::new();
    }

    let data: Value = match response.json() {
        Ok(d) => d,
        Err(e) => {
            logger::error(&format!("Failed to parse Brave API response: {e}"));
            return Vec::new();
        }
    };

    let mut results: Vec<Value> = Vec::new();
    // if "web" in data and "results" in data["web"]:
    if let Some(web_results) = data.get("web").and_then(|w| w.get("results")).and_then(|r| r.as_array()) {
        for item in web_results.iter().take(take) {
            let url = jget_str(item, "url");
            if url.is_empty() {
                continue;
            }
            // snippet = item.get("description", "") or item.get("content", "")
            let mut snippet = jget_str(item, "description");
            if snippet.is_empty() {
                snippet = jget_str(item, "content");
            }
            // age = item.get("date", "") if item.get("date") else ""
            let age = jget_str(item, "date");
            results.push(json!({
                "title": jget_str(item, "title"),
                "url": url,
                "snippet": snippet,
                "age": age,
            }));
        }
    }

    logger::info(&format!("Brave search returned {} results", results.len()));
    results
}

// ── DuckDuckGo (free, no key) ──

/// True only for `duckduckgo.com` and its subdomains.
fn _is_duckduckgo_host(host: &str) -> bool {
    // host = (host or "").lower()
    let host = host.to_lowercase();
    host == "duckduckgo.com" || host.ends_with(".duckduckgo.com")
}

/// Resolve a DuckDuckGo `/l/?uddg=<encoded-url>` redirect to its destination.
///
/// Faithful port of the Python `_resolve_ddg_redirect`: normalizes scheme-
/// relative (`//host/...`) and root-relative (`/...`) hrefs, then — if the
/// result is a `duckduckgo.com` `/l` redirect — returns the url-decoded `uddg`
/// query param. Anything else is returned as the normalized URL.
fn _resolve_ddg_redirect(raw: &str) -> String {
    // if not raw: return raw
    if raw.is_empty() {
        return raw.to_string();
    }
    // resolved = raw; if startswith("//"): "https:" + raw; elif startswith("/"):
    //   urljoin("https://html.duckduckgo.com", raw)
    let resolved = if let Some(rest) = raw.strip_prefix("//") {
        // "https:" + resolved (resolved is still the full "//host/..." string)
        format!("https://{rest}")
    } else if raw.starts_with('/') {
        // urljoin("https://html.duckduckgo.com", raw) — the base has no path, so
        // a root-relative href just attaches to the base origin.
        match url::Url::parse("https://html.duckduckgo.com").and_then(|b| b.join(raw)) {
            Ok(u) => u.to_string(),
            // urljoin never raises in Python; on the (practically unreachable)
            // join failure fall back to the raw string.
            Err(_) => raw.to_string(),
        }
    } else {
        raw.to_string()
    };
    // try: parsed = urlparse(resolved); if _is_duckduckgo_host(parsed.hostname)
    //   and parsed.path.rstrip("/") == "/l": qs = parse_qs(parsed.query);
    //   if "uddg" in qs: return qs["uddg"][0]
    // except Exception: pass
    if let Ok(parsed) = url::Url::parse(&resolved) {
        let host = parsed.host_str().unwrap_or("");
        if _is_duckduckgo_host(host) && parsed.path().trim_end_matches('/') == "/l" {
            // parse_qs(query); qs["uddg"][0] — first non-blank `uddg` value.
            // `query_pairs` already percent-decodes values (matching parse_qs).
            // parse_qs defaults to keep_blank_values=False, so a blank value
            // does not register the key; skip empty values to match.
            if let Some((_, v)) = parsed.query_pairs().find(|(k, v)| k == "uddg" && !v.is_empty()) {
                return v.into_owned();
            }
        }
    }
    resolved
}

/// Search using DuckDuckGo. No API key needed.
///
/// The Python tries to import the `duckduckgo_search` (`DDGS`) PyPI library and,
/// on `ImportError`, takes the `_html_fallback` path. There is no Rust DDGS
/// equivalent, so this ALWAYS uses the HTML fallback — faithful to the Python's
/// import-failure branch.
pub fn duckduckgo_search(query: &str, count: i64, _time_filter: Option<&str>) -> Vec<Value> {
    let take = if count < 0 { 0 } else { count as usize };

    // _html_fallback(): httpx.get(html.duckduckgo.com/html/, q) -> scrape
    //   .result / .result__a / .result__snippet
    let html_fallback = || -> Vec<Value> {
        let client = match http_client(REQUEST_TIMEOUT) {
            Some(c) => c,
            None => {
                logger::warning("DuckDuckGo HTML search failed: client build failed");
                return Vec::new();
            }
        };
        // params={"q": query, "kp": _safesearch_for("duckduckgo_html")} — always
        // Some for duckduckgo_html, so kp is always present.
        let mut query_pairs: Vec<(&str, &str)> = vec![("q", query)];
        let kp = _safesearch_for("duckduckgo_html");
        if let Some(kp) = kp {
            query_pairs.push(("kp", kp));
        }
        let response = match client
            .get("https://html.duckduckgo.com/html/")
            .header("User-Agent", "Mozilla/5.0")
            .query(&query_pairs)
            .send()
        {
            Ok(r) => r,
            Err(e) => {
                logger::warning(&format!("DuckDuckGo HTML search failed: {e}"));
                return Vec::new();
            }
        };
        if let Err(e) = response.error_for_status_ref() {
            logger::warning(&format!("DuckDuckGo HTML search failed: {e}"));
            return Vec::new();
        }
        let text = match response.text() {
            Ok(t) => t,
            Err(e) => {
                logger::warning(&format!("DuckDuckGo HTML search failed: {e}"));
                return Vec::new();
            }
        };
        let doc = Html::parse_document(&text);
        let result_sel = Selector::parse(".result").unwrap();
        let link_sel = Selector::parse(".result__a").unwrap();
        let snippet_sel = Selector::parse(".result__snippet").unwrap();
        let mut parsed: Vec<Value> = Vec::new();
        for result in doc.select(&result_sel).take(take) {
            // link = result.select_one(".result__a"); if not link: continue
            let link = match result.select(&link_sel).next() {
                Some(e) => e,
                None => continue,
            };
            // url = _resolve_ddg_redirect(link.get("href", "")); if not url: continue
            let url = _resolve_ddg_redirect(link.value().attr("href").unwrap_or(""));
            if url.is_empty() {
                continue;
            }
            // title = link.get_text(" ", strip=True)
            let title = collapse_text(&link.text().collect::<String>());
            let snippet = match result.select(&snippet_sel).next() {
                Some(e) => collapse_text(&e.text().collect::<String>()),
                None => String::new(),
            };
            parsed.push(json!({"title": title, "url": url, "snippet": snippet}));
        }
        logger::info(&format!("DuckDuckGo HTML search returned {} results", parsed.len()));
        parsed
    };

    // `from duckduckgo_search import DDGS` always fails (no Rust DDGS lib) ->
    // ImportError branch -> _html_fallback().
    logger::warning("duckduckgo-search package not installed; using HTML fallback");
    html_fallback()
}

// ── Google Programmable Search Engine ──

/// Search using Google PSE (Custom Search JSON API).
///
/// Requires two keys in settings: `search_api_key` (Google API key) and
/// `google_pse_cx` (Programmable Search Engine ID), or env vars `GOOGLE_API_KEY`
/// and `GOOGLE_PSE_CX`.
pub fn google_pse_search(query: &str, count: i64, time_filter: Option<&str>) -> Vec<Value> {
    let settings = _get_search_settings();
    // api_key = _get_provider_key("google_pse") or os.environ.get("GOOGLE_API_KEY", "")
    let mut api_key = _get_provider_key("google_pse");
    if api_key.is_empty() {
        api_key = os::getenv("GOOGLE_API_KEY", "");
    }
    // cx = (settings.get("google_pse_cx") or "").strip() or os.environ.get("GOOGLE_PSE_CX", "")
    let mut cx = settings_str(&settings, "google_pse_cx");
    if cx.is_empty() {
        cx = os::getenv("GOOGLE_PSE_CX", "");
    }

    if api_key.is_empty() || cx.is_empty() {
        logger::warning("Google PSE: missing API key or CX ID");
        return Vec::new();
    }

    let take = if count < 0 { 0 } else { count as usize };
    // num = min(count, 10) — Google PSE max is 10 per request.
    let num = std::cmp::min(count, 10);

    let mut params: Vec<(String, String)> = vec![
        ("key".to_string(), api_key),
        ("cx".to_string(), cx),
        ("q".to_string(), query.to_string()),
        ("num".to_string(), num.to_string()),
    ];
    // safe = _safesearch_for("google_pse"); if safe: params["safe"] = safe
    // (None when level == "off", so the param is omitted then.)
    if let Some(safe) = _safesearch_for("google_pse") {
        params.push(("safe".to_string(), safe.to_string()));
    }
    // time_map {"day":"d1","week":"w1","month":"m1","year":"y1"}
    if let Some(tf) = time_filter {
        let dr = match tf {
            "day" => Some("d1"),
            "week" => Some("w1"),
            "month" => Some("m1"),
            "year" => Some("y1"),
            _ => None,
        };
        if let Some(dr) = dr {
            params.push(("dateRestrict".to_string(), dr.to_string()));
        }
    }

    let client = match http_client(REQUEST_TIMEOUT) {
        Some(c) => c,
        None => {
            error_logger::error("Google PSE search failed: client build failed");
            return Vec::new();
        }
    };
    let query_pairs: Vec<(&str, &str)> =
        params.iter().map(|(k, v)| (k.as_str(), v.as_str())).collect();
    let response = match client
        .get("https://www.googleapis.com/customsearch/v1")
        .query(&query_pairs)
        .send()
    {
        Ok(r) => r,
        Err(e) => {
            error_logger::error(&format!("Google PSE search failed: {e}"));
            return Vec::new();
        }
    };
    if response.status().as_u16() == 429 {
        error_logger::error("Google PSE rate limit hit");
        return Vec::new();
    }
    if let Err(e) = response.error_for_status_ref() {
        error_logger::error(&format!("Google PSE search failed: {e}"));
        return Vec::new();
    }
    let data: Value = match response.json() {
        Ok(d) => d,
        Err(e) => {
            error_logger::error(&format!("Google PSE search failed: {e}"));
            return Vec::new();
        }
    };

    let mut results: Vec<Value> = Vec::new();
    if let Some(items) = data.get("items").and_then(|v| v.as_array()) {
        for item in items.iter().take(take) {
            let url = jget_str(item, "link");
            if url.is_empty() {
                continue;
            }
            results.push(json!({
                "title": jget_str(item, "title"),
                "url": url,
                "snippet": jget_str(item, "snippet"),
            }));
        }
    }

    logger::info(&format!("Google PSE returned {} results", results.len()));
    results
}

// ── Tavily ──

/// Search using Tavily API. Requires `search_api_key` or `TAVILY_API_KEY` env var.
pub fn tavily_search(query: &str, count: i64, time_filter: Option<&str>) -> Vec<Value> {
    let mut api_key = _get_provider_key("tavily");
    if api_key.is_empty() {
        api_key = os::getenv("TAVILY_API_KEY", "");
    }
    if api_key.is_empty() {
        logger::warning("Tavily: no API key configured");
        return Vec::new();
    }

    let take = if count < 0 { 0 } else { count as usize };

    // payload = {"query": query, "max_results": count, "include_answer": False}
    let mut payload = json!({
        "query": query,
        "max_results": count,
        "include_answer": false,
    });
    if let Some(tf) = time_filter {
        // payload["days"] = {"day":1,"week":7,"month":30,"year":365}[tf]
        let days = match tf {
            "day" => Some(1),
            "week" => Some(7),
            "month" => Some(30),
            "year" => Some(365),
            _ => None,
        };
        if let Some(d) = days {
            payload["days"] = json!(d);
        }
    }

    let client = match http_client(REQUEST_TIMEOUT) {
        Some(c) => c,
        None => {
            error_logger::error("Tavily search failed: client build failed");
            return Vec::new();
        }
    };
    let response = match client
        .post("https://api.tavily.com/search")
        .header("Authorization", format!("Bearer {api_key}"))
        .header("Content-Type", "application/json")
        .json(&payload)
        .send()
    {
        Ok(r) => r,
        Err(e) => {
            error_logger::error(&format!("Tavily search failed: {e}"));
            return Vec::new();
        }
    };
    if response.status().as_u16() == 429 {
        error_logger::error("Tavily rate limit hit");
        return Vec::new();
    }
    if let Err(e) = response.error_for_status_ref() {
        error_logger::error(&format!("Tavily search failed: {e}"));
        return Vec::new();
    }
    let data: Value = match response.json() {
        Ok(d) => d,
        Err(e) => {
            error_logger::error(&format!("Tavily search failed: {e}"));
            return Vec::new();
        }
    };

    let mut results: Vec<Value> = Vec::new();
    if let Some(items) = data.get("results").and_then(|v| v.as_array()) {
        for item in items.iter().take(take) {
            let url = jget_str(item, "url");
            if url.is_empty() {
                continue;
            }
            results.push(json!({
                "title": jget_str(item, "title"),
                "url": url,
                "snippet": jget_str(item, "content"),
                "age": jget_str(item, "published_date"),
            }));
        }
    }

    logger::info(&format!("Tavily returned {} results", results.len()));
    results
}

// ── Serper.dev ──

/// Search using Serper.dev API. Requires `search_api_key` or `SERPER_API_KEY` env var.
pub fn serper_search(query: &str, count: i64, time_filter: Option<&str>) -> Vec<Value> {
    let mut api_key = _get_provider_key("serper");
    if api_key.is_empty() {
        api_key = os::getenv("SERPER_API_KEY", "");
    }
    if api_key.is_empty() {
        logger::warning("Serper: no API key configured");
        return Vec::new();
    }

    let take = if count < 0 { 0 } else { count as usize };

    // payload = {"q": query, "num": count}
    let mut payload = json!({"q": query, "num": count});
    // safe = _safesearch_for("serper"); if safe: payload["safe"] = safe
    // (None when level == "off", so the field is omitted then.)
    if let Some(safe) = _safesearch_for("serper") {
        payload["safe"] = json!(safe);
    }
    if let Some(tf) = time_filter {
        // time_map {"day":"qdr:d","week":"qdr:w","month":"qdr:m","year":"qdr:y"}
        let tbs = match tf {
            "day" => Some("qdr:d"),
            "week" => Some("qdr:w"),
            "month" => Some("qdr:m"),
            "year" => Some("qdr:y"),
            _ => None,
        };
        if let Some(tbs) = tbs {
            payload["tbs"] = json!(tbs);
        }
    }

    let client = match http_client(REQUEST_TIMEOUT) {
        Some(c) => c,
        None => {
            error_logger::error("Serper search failed: client build failed");
            return Vec::new();
        }
    };
    let response = match client
        .post("https://google.serper.dev/search")
        .header("X-API-KEY", &api_key)
        .header("Content-Type", "application/json")
        .json(&payload)
        .send()
    {
        Ok(r) => r,
        Err(e) => {
            error_logger::error(&format!("Serper search failed: {e}"));
            return Vec::new();
        }
    };
    if response.status().as_u16() == 429 {
        error_logger::error("Serper rate limit hit");
        return Vec::new();
    }
    if let Err(e) = response.error_for_status_ref() {
        error_logger::error(&format!("Serper search failed: {e}"));
        return Vec::new();
    }
    let data: Value = match response.json() {
        Ok(d) => d,
        Err(e) => {
            error_logger::error(&format!("Serper search failed: {e}"));
            return Vec::new();
        }
    };

    let mut results: Vec<Value> = Vec::new();
    if let Some(items) = data.get("organic").and_then(|v| v.as_array()) {
        for item in items.iter().take(take) {
            let url = jget_str(item, "link");
            if url.is_empty() {
                continue;
            }
            results.push(json!({
                "title": jget_str(item, "title"),
                "url": url,
                "snippet": jget_str(item, "snippet"),
                "age": jget_str(item, "date"),
            }));
        }
    }

    logger::info(&format!("Serper returned {} results", results.len()));
    results
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── SafeSearch level normalization ──

    #[test]
    fn safesearch_level_canonical_passthrough() {
        // Canonical values pass through unchanged (after strip/lower).
        assert_eq!(normalize_safesearch_level("strict"), "strict");
        assert_eq!(normalize_safesearch_level("moderate"), "moderate");
        assert_eq!(normalize_safesearch_level("off"), "off");
        // strip + lower applied.
        assert_eq!(normalize_safesearch_level("  STRICT  "), "strict");
        assert_eq!(normalize_safesearch_level("Off"), "off");
    }

    #[test]
    fn safesearch_level_aliases() {
        // strict aliases.
        for a in ["on", "high", "2"] {
            assert_eq!(normalize_safesearch_level(a), "strict", "alias {a}");
        }
        // moderate aliases.
        for a in ["medium", "1", "default"] {
            assert_eq!(normalize_safesearch_level(a), "moderate", "alias {a}");
        }
        // off aliases.
        for a in ["none", "disabled", "0"] {
            assert_eq!(normalize_safesearch_level(a), "off", "alias {a}");
        }
    }

    #[test]
    fn safesearch_level_unknown_and_empty_default_to_strict() {
        // Unknown -> "strict" (aliases.get(raw, "strict")).
        assert_eq!(normalize_safesearch_level("bananas"), "strict");
        // Empty raw also defaults to strict.
        assert_eq!(normalize_safesearch_level(""), "strict");
        assert_eq!(normalize_safesearch_level("   "), "strict");
    }

    // ── Per-provider SafeSearch mapping ──

    #[test]
    fn safesearch_for_searxng() {
        assert_eq!(safesearch_for_level("searxng", "strict"), Some("2"));
        assert_eq!(safesearch_for_level("searxng", "moderate"), Some("1"));
        assert_eq!(safesearch_for_level("searxng", "off"), Some("0"));
    }

    #[test]
    fn safesearch_for_brave_passes_level_through() {
        assert_eq!(safesearch_for_level("brave", "strict"), Some("strict"));
        assert_eq!(safesearch_for_level("brave", "moderate"), Some("moderate"));
        assert_eq!(safesearch_for_level("brave", "off"), Some("off"));
    }

    #[test]
    fn safesearch_for_duckduckgo_lib_and_html() {
        // duckduckgo_lib: strict->on, moderate->moderate, off->off
        assert_eq!(safesearch_for_level("duckduckgo_lib", "strict"), Some("on"));
        assert_eq!(safesearch_for_level("duckduckgo_lib", "moderate"), Some("moderate"));
        assert_eq!(safesearch_for_level("duckduckgo_lib", "off"), Some("off"));
        // duckduckgo_html (the kp param): strict->1, moderate->-1, off->-2
        assert_eq!(safesearch_for_level("duckduckgo_html", "strict"), Some("1"));
        assert_eq!(safesearch_for_level("duckduckgo_html", "moderate"), Some("-1"));
        assert_eq!(safesearch_for_level("duckduckgo_html", "off"), Some("-2"));
    }

    #[test]
    fn safesearch_for_google_pse_and_serper() {
        // "active" unless off; None when off.
        for p in ["google_pse", "serper"] {
            assert_eq!(safesearch_for_level(p, "strict"), Some("active"), "{p} strict");
            assert_eq!(safesearch_for_level(p, "moderate"), Some("active"), "{p} moderate");
            assert_eq!(safesearch_for_level(p, "off"), None, "{p} off");
        }
    }

    #[test]
    fn safesearch_for_unknown_provider_is_none() {
        assert_eq!(safesearch_for_level("bing", "strict"), None);
        assert_eq!(safesearch_for_level("", "off"), None);
    }

    // ── DuckDuckGo host predicate ──

    #[test]
    fn is_duckduckgo_host_matches_domain_and_subdomains() {
        assert!(_is_duckduckgo_host("duckduckgo.com"));
        assert!(_is_duckduckgo_host("DuckDuckGo.com")); // lowercased
        assert!(_is_duckduckgo_host("html.duckduckgo.com"));
        assert!(_is_duckduckgo_host("links.duckduckgo.com"));
        // Not a match: lookalikes / different hosts / empty.
        assert!(!_is_duckduckgo_host("notduckduckgo.com"));
        assert!(!_is_duckduckgo_host("duckduckgo.com.evil.com"));
        assert!(!_is_duckduckgo_host("example.com"));
        assert!(!_is_duckduckgo_host(""));
    }

    // ── DuckDuckGo redirect resolution ──

    #[test]
    fn resolve_ddg_redirect_unwraps_uddg() {
        // Scheme-relative redirect with an encoded target.
        let raw = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage%3Fa%3D1&rut=abc";
        assert_eq!(_resolve_ddg_redirect(raw), "https://example.com/page?a=1");

        // Root-relative redirect against the html.duckduckgo.com base.
        let raw = "/l/?uddg=https%3A%2F%2Fnews.example.org%2Fstory";
        assert_eq!(_resolve_ddg_redirect(raw), "https://news.example.org/story");

        // Absolute duckduckgo redirect URL.
        let raw = "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.rust-lang.org%2F";
        assert_eq!(_resolve_ddg_redirect(raw), "https://www.rust-lang.org/");
    }

    #[test]
    fn resolve_ddg_redirect_trailing_slash_on_l_path() {
        // parsed.path.rstrip("/") == "/l" — a trailing slash on /l/ still matches.
        let raw = "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2F";
        assert_eq!(_resolve_ddg_redirect(raw), "https://example.com/");
    }

    #[test]
    fn resolve_ddg_redirect_passthrough_for_direct_urls() {
        // A direct (non-redirect) absolute URL is returned unchanged.
        let raw = "https://example.com/article";
        assert_eq!(_resolve_ddg_redirect(raw), "https://example.com/article");

        // Empty stays empty.
        assert_eq!(_resolve_ddg_redirect(""), "");

        // A duckduckgo URL that is NOT an /l redirect is returned as the
        // normalized URL (no uddg unwrap).
        let raw = "https://duckduckgo.com/about";
        assert_eq!(_resolve_ddg_redirect(raw), "https://duckduckgo.com/about");

        // An /l redirect WITHOUT a uddg param falls through to the resolved URL.
        let raw = "https://duckduckgo.com/l/?rut=abc";
        assert_eq!(_resolve_ddg_redirect(raw), "https://duckduckgo.com/l/?rut=abc");
    }

    #[test]
    fn resolve_ddg_redirect_scheme_relative_non_redirect() {
        // Scheme-relative non-duckduckgo host: just gets the https: scheme.
        let raw = "//example.com/path";
        assert_eq!(_resolve_ddg_redirect(raw), "https://example.com/path");
    }
}
