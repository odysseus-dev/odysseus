// src/model_context.rs  <- src/model_context.py
//! model_context.py
//!
//! Query and cache model context window sizes from OpenAI-compatible APIs.
//! Provides token estimation for context usage tracking.
//!
//! SCOPE: fully ported. `_is_local_endpoint`, `_lookup_known`, `estimate_tokens`,
//! the `KNOWN_CONTEXT_WINDOWS` table, the per-model `get_context_length` cache,
//! AND the live probe `_query_context_length` (the llama.cpp `/slots` call for
//! local endpoints + the `/v1/models` context-field query, reconciled with the
//! known-windows table). The probe is synchronous like the Python (`httpx.get`);
//! it runs `reqwest::blocking` on a dedicated OS thread so it is safe to call
//! from inside a tokio worker, and its result is cached per model id (only
//! non-default values are cached, allowing a retry next request — exactly the
//! Python). On any transport/parse failure it falls back to the known-windows
//! table / `DEFAULT_CONTEXT`, the same as Python's own except path.

use crate::pylog as logger;
use indexmap::IndexMap;
use once_cell::sync::Lazy;
use std::sync::Mutex;
use url::Url;

const _LOCAL_HOSTS: [&str; 5] = ["localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"];
const _PRIVATE_PREFIXES: [&str; 19] = [
    "10.", "172.16.", "172.17.", "172.18.", "172.19.", "172.20.", "172.21.",
    "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.", "172.28.",
    "172.29.", "172.30.", "172.31.", "192.168.", "100.",
];

/// Strip trailing path suffixes that are endpoint variants of the same base
/// so that two URLs pointing at the same host/prefix compare equal.
///
/// Mirrors Python `_normalize_base_for_compare`.
fn _normalize_base_for_compare(url: &str) -> String {
    let mut u = url.trim().trim_end_matches('/').to_string();
    let suffixes = [
        "/chat/completions",
        "/models",
        "/completions",
        "/v1/messages",
    ];
    for suffix in suffixes {
        if u.ends_with(suffix) {
            u.truncate(u.len() - suffix.len());
            u = u.trim_end_matches('/').to_string();
            // Only strip one suffix (same as Python).
            break;
        }
    }
    u
}

/// Return the stored `endpoint_kind` for the enabled endpoint whose `base_url`
/// best matches `url`. Returns `None` when the DB is unavailable or no row
/// matches.
///
/// Mirrors Python `_configured_endpoint_kind`. The Python version guards with
/// `"core.database" not in sys.modules`; in production the Rust port always has
/// the DB compiled in so the read always runs (any DB error yields `None`). The
/// query runs synchronously on the calling thread — the `Connection`/`Statement`
/// never escape this scope, so `!Send` is a non-issue.
pub fn _configured_endpoint_kind(url: &str) -> Option<String> {
    let target = _normalize_base_for_compare(url);
    if target.is_empty() {
        return None;
    }
    // Test isolation (mirrors Python's `"core.database" not in sys.modules`
    // guard): only touch the DB when the current test set one up via `test_db`
    // (else return `None`). Otherwise this best-effort read would open a sibling
    // test's `DATABASE_URL` and race with its lifecycle. In production the DB is
    // always available, so the read always runs.
    #[cfg(test)]
    crate::core::database::current_thread_test_db()?;
    // Run synchronously on the CALLING thread (not a spawned one): the connection
    // target comes from `db_path()`, which in tests is a thread-local, so the
    // query must execute on the same thread that set the test DB. rusqlite's
    // `Connection`/`Statement` never escape this scope, so `!Send` is a non-issue.
    (move || -> Option<String> {
        let conn = crate::core::database::session_local().ok()?;
        let mut stmt = conn
            .prepare(
                "SELECT base_url, api_key, endpoint_kind \
                 FROM model_endpoints WHERE is_enabled = 1",
            )
            .ok()?;
        let rows: Vec<(String, Option<String>, Option<String>)> = stmt
            .query_map([], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, Option<String>>(1)?,
                    row.get::<_, Option<String>>(2)?,
                ))
            })
            .ok()?
            .flatten()
            .collect();
        for (base_url_raw, api_key, endpoint_kind_raw) in rows {
            let base = _normalize_base_for_compare(&base_url_raw);
            if base.is_empty() {
                continue;
            }
            if target != base && !target.starts_with(&format!("{base}/")) {
                continue;
            }
            // kind = (ep.endpoint_kind or "auto").strip().lower()
            let kind = endpoint_kind_raw
                .as_deref()
                .unwrap_or("auto")
                .trim()
                .to_lowercase();
            if matches!(kind.as_str(), "local" | "api" | "proxy") {
                return Some(kind);
            }
            // Heuristic: api_key present + v1/openai path => proxy.
            if api_key.is_some() {
                if let Ok(parsed) = Url::parse(&base) {
                    let host = parsed.host_str().unwrap_or("").to_lowercase();
                    let path = parsed.path().trim_end_matches('/');
                    let port = parsed.port();
                    if port != Some(11434)
                        && !host.contains("ollama")
                        && (path.ends_with("/v1") || path.contains("/openai"))
                    {
                        return Some("proxy".to_string());
                    }
                }
            }
            return Some("auto".to_string());
        }
        None
    })()
}

/// Check if URL points to a local/private/tailscale address.
pub fn _is_local_endpoint(url: &str) -> bool {
    // Upstream a2e691d: check configured endpoint_kind first.
    match _configured_endpoint_kind(url).as_deref() {
        Some("api") | Some("proxy") => return false,
        Some("local") => return true,
        _ => {}
    }
    // try: host = urlparse(url).hostname or ""  / except Exception: return False
    //
    // `urlparse(url).hostname` lowercases the host and is `None` (-> "") for a
    // string with no `//host` authority (e.g. "not-a-url" or ""). The `url`
    // crate's `Url::parse` errors on those, which maps to the Python `except`
    // path / empty host — both yield `False`.
    match Url::parse(url) {
        Ok(u) => {
            let host = u.host_str().unwrap_or("").to_lowercase();
            _LOCAL_HOSTS.contains(&host.as_str())
                || _PRIVATE_PREFIXES.iter().any(|p| host.starts_with(p))
        }
        Err(_) => false,
    }
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
pub const DEFAULT_CONTEXT: i64 = 128000;
pub const REQUEST_TIMEOUT: i64 = 5;

/// Known context windows for major API models (used as fallback when the
/// `/models` endpoint doesn't report context_length). Substring matching — uses
/// the shortest unique prefix so variants get caught.
///
/// `_lookup_known` returns the LONGEST matching key, so a short key never
/// shadows a more specific one (`gpt-4` vs `gpt-4o`, `o1` vs `o1-mini`). On a
/// tie in key length the first inserted key wins, so an `IndexMap` is used to
/// reproduce the Python dict's iteration order.
pub static KNOWN_CONTEXT_WINDOWS: Lazy<IndexMap<&'static str, i64>> = Lazy::new(|| {
    IndexMap::from([
        // --- Anthropic ---
        ("claude-sonnet-4-5", 200000),
        ("claude-sonnet-4-6", 200000),
        ("claude-sonnet-4", 200000),
        ("claude-opus-4", 200000),
        ("claude-haiku-4", 200000),
        ("claude-haiku-3-5", 200000),
        ("claude-3-5-sonnet", 200000),
        ("claude-3-5-haiku", 200000),
        ("claude-3-opus", 200000),
        ("claude-3-sonnet", 200000),
        ("claude-3-haiku", 200000),
        // --- OpenAI ---
        ("gpt-5", 400000),
        ("gpt-4.1", 1047576),
        ("gpt-4.1-mini", 1047576),
        ("gpt-4.1-nano", 1047576),
        ("gpt-4o", 128000),
        ("gpt-4o-mini", 128000),
        ("gpt-4-turbo", 128000),
        ("gpt-4", 8192),
        ("gpt-3.5-turbo", 16385),
        ("o1", 200000),
        ("o1-mini", 128000),
        ("o1-pro", 200000),
        ("o3", 200000),
        ("o3-mini", 200000),
        ("o4-mini", 200000),
        // --- DeepSeek ---
        ("deepseek-chat", 64000),
        ("deepseek-coder", 64000),
        ("deepseek-reasoner", 64000),
        ("deepseek-r1", 64000),
        ("deepseek-v3", 64000),
        ("deepseek-v2", 64000),
        // --- Google ---
        ("gemini-2.5-pro", 1048576),
        ("gemini-2.5-flash", 1048576),
        ("gemini-2.0-flash", 1048576),
        ("gemini-1.5-pro", 1048576),
        ("gemini-1.5-flash", 1048576),
        ("gemma-4", 262144),
        ("gemma-3", 128000),
        ("gemma-2", 8192),
        // --- Mistral ---
        ("mistral-large", 128000),
        ("mistral-medium", 32000),
        ("mistral-small", 32000),
        ("mistral-nemo", 128000),
        ("mistral-7b", 32000),
        ("mixtral", 32000),
        ("codestral", 32000),
        ("pixtral", 128000),
        // --- xAI ---
        ("grok-4", 131072),
        ("grok-3", 131072),
        ("grok-2", 131072),
        // --- Meta / Llama ---
        ("llama-4", 1048576),
        ("llama-3.3", 131072),
        ("llama-3.2", 131072),
        ("llama-3.1", 131072),
        ("llama-3", 131072),
        // --- Qwen ---
        ("qwen3", 131072),
        ("qwen2.5", 131072),
        ("qwen2", 32768),
        ("qwq", 32768),
        // --- Cohere ---
        ("command-r-plus", 128000),
        ("command-r", 128000),
        ("command-a", 256000),
        // --- Perplexity ---
        ("sonar-pro", 200000),
        ("sonar", 128000),
        // --- MiniMax ---
        ("minimax", 1000000),
        // --- Moonshot / Kimi ---
        ("moonshot", 128000),
        ("kimi", 128000),
        // --- Microsoft ---
        ("phi-4", 16000),
        ("phi-3", 128000),
        // --- Nvidia ---
        ("nemotron", 131072),
        // --- Yi ---
        ("yi-large", 32768),
        ("yi-1.5", 16384),
        // --- 01.ai ---
        ("yi-lightning", 16384),
        // --- Nous ---
        ("hermes", 131072),
        ("nous-hermes", 131072),
        // --- Open community ---
        ("dolphin", 32768),
        ("mythomax", 4096),
        ("wizard", 32768),
        ("openchat", 8192),
        ("solar", 32768),
    ])
});

// ---------------------------------------------------------------------------
// Cache
// ---------------------------------------------------------------------------
static _CONTEXT_CACHE: Lazy<Mutex<IndexMap<String, i64>>> = Lazy::new(|| Mutex::new(IndexMap::new()));

/// Get the context window size for a model.
///
/// Queries `/v1/models` on the endpoint and looks for context_length /
/// context_window fields. Caches result per model ID. Falls back to
/// DEFAULT_CONTEXT if unavailable.
///
/// TODO(web): the live httpx probe is deferred — see `_query_context_length`.
pub fn get_context_length(endpoint_url: &str, model: &str) -> i64 {
    // configured_kind = _configured_endpoint_kind(endpoint_url)
    // is_local = _is_local_endpoint(endpoint_url)
    // if not is_local and model in _context_cache: return _context_cache[model]
    let configured_kind = _configured_endpoint_kind(endpoint_url);
    let is_local = _is_local_endpoint(endpoint_url);
    if !is_local {
        if let Some(ctx) = _CONTEXT_CACHE.lock().unwrap().get(model) {
            return *ctx;
        }
    }
    let ctx = _query_context_length(endpoint_url, model);
    // Only cache non-default values to allow retry on next request.
    // Local endpoints can restart with a different --max-model-len while keeping
    // the same model id, so always re-query them instead of serving stale cache.
    // Upstream a2e691d: also cache the default for api/proxy endpoints so large
    // proxy catalogs are not re-fetched on every model picker open.
    let is_api_or_proxy = matches!(configured_kind.as_deref(), Some("api") | Some("proxy"));
    if !is_local && (ctx != DEFAULT_CONTEXT || is_api_or_proxy) {
        _CONTEXT_CACHE.lock().unwrap().insert(model.to_string(), ctx);
    }
    logger::info(&format!("Context length for {model}: {ctx}"));
    ctx
}

/// Check known context windows by substring match.
///
/// Picks the LONGEST matching key so a short key never shadows a more specific
/// one. Without this, `o1` (200k) precedes `o1-mini` (128k) in the table and a
/// first-match return would report o1-mini's window as 200k.
pub fn _lookup_known(model: &str) -> Option<i64> {
    let name = model.to_lowercase();
    // basename = name.split("/")[-1] if "/" in name else name
    let basename = if name.contains('/') {
        name.rsplit('/').next().unwrap_or(&name).to_string()
    } else {
        name.clone()
    };
    // basename = basename.split(":")[0]  — strip :free, :extended etc.
    let basename = basename.split(':').next().unwrap_or(&basename).to_string();
    let mut best_key: Option<&str> = None;
    let mut best_ctx: Option<i64> = None;
    for (key, ctx) in KNOWN_CONTEXT_WINDOWS.iter() {
        if basename.contains(key) || name.contains(key) {
            // if best_key is None or len(key) > len(best_key)
            if best_key.is_none_or(|b| key.len() > b.len()) {
                best_key = Some(*key);
                best_ctx = Some(*ctx);
            }
        }
    }
    best_ctx
}

/// Synchronous HTTP GET → parsed JSON, safe to call from inside a tokio worker.
///
/// `reqwest::blocking` spins its own current-thread runtime, which panics if
/// started from within an existing tokio runtime — so we run it on a fresh OS
/// thread (no ambient runtime there) and join. Returns `None` on any builder /
/// transport error, non-2xx status, or JSON parse failure (the Python broad
/// `except` / `r.is_success` / `raise_for_status` shapes all collapse to None).
fn http_get_json(url: String, timeout_secs: u64) -> Option<serde_json::Value> {
    std::thread::spawn(move || -> Option<serde_json::Value> {
        let client = reqwest::blocking::Client::builder()
            .timeout(std::time::Duration::from_secs(timeout_secs))
            .build()
            .ok()?;
        let resp = client.get(&url).send().ok()?;
        if !resp.status().is_success() {
            return None;
        }
        resp.json::<serde_json::Value>().ok()
    })
    .join()
    .ok()
    .flatten()
}

/// `val and isinstance(val, (int, float)) and val > 0` → `int(val)`. `as_f64`
/// returns `Some` only for JSON numbers (bool/string → None, matching the
/// isinstance gate); truncate toward zero like Python `int()`.
fn json_num_positive(v: &serde_json::Value) -> Option<i64> {
    let n = v.as_f64()?;
    if n > 0.0 {
        Some(n as i64)
    } else {
        None
    }
}

/// Query the model API for context length (faithful port of Python
/// `_query_context_length`): try the llama.cpp `/slots` endpoint first for local
/// endpoints, then the `/v1/models` context fields, then reconcile against the
/// known-windows table.
fn _query_context_length(endpoint_url: &str, model: &str) -> i64 {
    let known = _lookup_known(model);
    let mut api_ctx: Option<i64> = None;
    let configured_kind = _configured_endpoint_kind(endpoint_url);

    // Large OpenAI-compatible proxies can make /models expensive. If the
    // endpoint is explicitly configured as API/proxy, prefer known context
    // metadata (or the default) over downloading the full catalog.
    // Mirrors upstream a2e691d.
    if matches!(configured_kind.as_deref(), Some("api") | Some("proxy")) {
        if let Some(k) = known {
            logger::info(&format!("Using known context window for {model}: {k}"));
            return k;
        }
        return DEFAULT_CONTEXT;
    }

    // Try llama.cpp /slots first — reports the actual serving context.
    if _is_local_endpoint(endpoint_url) {
        // base = url.split("/v1")[0] if "/v1" in url else url.rsplit("/", 1)[0]
        let base = if endpoint_url.contains("/v1") {
            endpoint_url.split("/v1").next().unwrap_or(endpoint_url).to_string()
        } else {
            match endpoint_url.rfind('/') {
                Some(i) => endpoint_url[..i].to_string(),
                None => endpoint_url.to_string(),
            }
        };
        if let Some(slots) = http_get_json(format!("{base}/slots"), REQUEST_TIMEOUT as u64) {
            if let Some(n_ctx) = slots
                .as_array()
                .and_then(|a| a.first())
                .and_then(|s| s.get("n_ctx"))
                .and_then(|v| v.as_i64())
            {
                if n_ctx > 0 {
                    logger::info(&format!("llama.cpp /slots reports n_ctx={n_ctx} for {model}"));
                    return n_ctx;
                }
            }
        }
    }

    // /v1/models lookup.
    let models_url = endpoint_url.replace("/chat/completions", "/models");
    if let Some(data) = http_get_json(models_url, REQUEST_TIMEOUT as u64) {
        let empty: Vec<serde_json::Value> = Vec::new();
        let models_list = data.get("data").and_then(|v| v.as_array()).unwrap_or(&empty);
        let model_base = model.rsplit('/').next().unwrap_or(model);
        for m in models_list {
            let mid = m.get("id").and_then(|v| v.as_str()).unwrap_or("");
            let mid_base = mid.rsplit('/').next().unwrap_or(mid);
            if mid == model || mid_base == model_base {
                for field in [
                    "context_length",
                    "context_window",
                    "max_model_len",
                    "max_context_length",
                    "max_seq_len",
                ] {
                    if let Some(val) = m.get(field).and_then(json_num_positive) {
                        api_ctx = Some(val);
                        break;
                    }
                }
                if api_ctx.is_none() {
                    // meta = m.get("meta") or m.get("model_extra") or {}
                    let meta = m.get("meta").or_else(|| m.get("model_extra"));
                    if let Some(meta) = meta.and_then(|v| v.as_object()) {
                        for field in ["n_ctx", "context_length", "context_window", "max_model_len"] {
                            if let Some(val) = meta.get(field).and_then(json_num_positive) {
                                api_ctx = Some(val);
                                break;
                            }
                        }
                    }
                }
                break;
            }
        }
    }

    // Reconcile: local endpoints trust the API value; cloud uses the larger.
    match (api_ctx, known) {
        (Some(api), Some(known)) => {
            if _is_local_endpoint(endpoint_url) && api < known {
                logger::info(&format!(
                    "Local endpoint reports {api} for {model} (known max: {known}) — using API value"
                ));
                return api;
            }
            if api < known {
                logger::info(&format!(
                    "API reported {api} for {model}, using known {known} instead"
                ));
            }
            api.max(known)
        }
        (Some(api), None) => api,
        (None, Some(known)) => {
            logger::info(&format!("Using known context window for {model}: {known}"));
            known
        }
        (None, None) => DEFAULT_CONTEXT,
    }
}

/// Rough token estimate for a list of messages.
///
/// Uses chars * 0.3 which is closer to real BPE tokenizer output than the
/// commonly-cited chars/4 (which underestimates by ~20-30%). Also adds ~4 tokens
/// per message for role/formatting overhead.
pub fn estimate_tokens(messages: &[serde_json::Value]) -> i64 {
    let mut total: i64 = 0;
    for msg in messages {
        total += 4; // per-message overhead (role, separators)
        // content = msg.get("content", "") — absent/None/non-(str|list) -> 0.
        match msg.get("content") {
            Some(serde_json::Value::String(content)) => {
                // total += int(len(content) * 0.3)  — len is over code points,
                // int() truncates toward zero.
                total += (content.chars().count() as f64 * 0.3) as i64;
            }
            Some(serde_json::Value::Array(items)) => {
                for item in items {
                    // if isinstance(item, dict) and item.get("type") == "text":
                    if item.get("type").and_then(|v| v.as_str()) == Some("text") {
                        let text = item.get("text").and_then(|v| v.as_str()).unwrap_or("");
                        total += (text.chars().count() as f64 * 0.3) as i64;
                    }
                }
            }
            _ => {}
        }
    }
    total
}

// ---------------------------------------------------------------------------
// Tests (upstream a2e691d — proxy endpoint refresh)
// ---------------------------------------------------------------------------
#[cfg(test)]
mod tests {
    use super::*;

    // -----------------------------------------------------------------------
    // _normalize_base_for_compare — pure, no DB needed
    // -----------------------------------------------------------------------
    #[test]
    fn normalize_strips_chat_completions() {
        assert_eq!(
            _normalize_base_for_compare("https://api.example.com/v1/chat/completions"),
            "https://api.example.com/v1"
        );
    }

    #[test]
    fn normalize_strips_models() {
        assert_eq!(
            _normalize_base_for_compare("https://api.example.com/v1/models"),
            "https://api.example.com/v1"
        );
    }

    #[test]
    fn normalize_strips_completions() {
        assert_eq!(
            _normalize_base_for_compare("https://api.example.com/v1/completions"),
            "https://api.example.com/v1"
        );
    }

    #[test]
    fn normalize_strips_v1_messages() {
        // The suffix list contains the whole "/v1/messages", so it is stripped
        // entirely (matching Python's `for suffix in (... "/v1/messages")`).
        assert_eq!(
            _normalize_base_for_compare("https://api.anthropic.com/v1/messages"),
            "https://api.anthropic.com"
        );
    }

    #[test]
    fn normalize_strips_trailing_slash() {
        assert_eq!(
            _normalize_base_for_compare("https://api.example.com/v1/"),
            "https://api.example.com/v1"
        );
    }

    #[test]
    fn normalize_passthrough_plain_base() {
        assert_eq!(
            _normalize_base_for_compare("https://api.example.com/v1"),
            "https://api.example.com/v1"
        );
    }

    #[test]
    fn normalize_empty_returns_empty() {
        assert_eq!(_normalize_base_for_compare(""), "");
        assert_eq!(_normalize_base_for_compare("   "), "");
    }

    // -----------------------------------------------------------------------
    // _configured_endpoint_kind — requires temp DB
    // -----------------------------------------------------------------------

    /// Seed a model_endpoints row for testing. Uses the same schema as
    /// `ai_interaction.rs::seed_endpoint` — NO encryption (api_key stored plain).
    fn seed_endpoint(
        conn: &rusqlite::Connection,
        id: &str,
        base_url: &str,
        api_key: Option<&str>,
        endpoint_kind: Option<&str>,
        enabled: bool,
    ) {
        let ts = "2025-01-01T00:00:00";
        conn.execute(
            "INSERT OR REPLACE INTO model_endpoints \
             (id, name, base_url, api_key, endpoint_kind, is_enabled, model_type, created_at, updated_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, 'llm', ?7, ?7)",
            rusqlite::params![id, id, base_url, api_key, endpoint_kind, enabled as i64, ts],
        )
        .unwrap();
    }

    /// Create a PRIVATE, thread-local temp DB (via `test_db`), run `create_all`,
    /// and seed it with `f`. The returned guard keeps the DB alive and routed for
    /// THIS thread for the test's duration; on drop it restores the thread-local
    /// and removes the file. Because the target is thread-local (not the global
    /// `DATABASE_URL`), these tests never race sibling DB tests.
    fn with_temp_db<F: FnOnce(&rusqlite::Connection)>(
        label: &str,
        f: F,
    ) -> crate::core::database::TestDb {
        let db = crate::core::database::test_db(label);
        let conn = crate::core::database::session_local().unwrap();
        f(&conn);
        db
    }

    #[test]
    fn configured_kind_explicit_api() {
        let _db = with_temp_db("ck_api", |conn| {
            seed_endpoint(conn, "ep1", "https://api.openai.com/v1", Some("sk-x"), Some("api"), true);
        });

        // Chat-completions variant should normalise to the same base.
        assert_eq!(
            _configured_endpoint_kind("https://api.openai.com/v1/chat/completions").as_deref(),
            Some("api")
        );
    }

    #[test]
    fn configured_kind_explicit_proxy() {
        let _db = with_temp_db("ck_proxy", |conn| {
            seed_endpoint(conn, "ep2", "https://proxy.example.com/v1", Some("sk-p"), Some("proxy"), true);
        });

        assert_eq!(
            _configured_endpoint_kind("https://proxy.example.com/v1/chat/completions").as_deref(),
            Some("proxy")
        );
    }

    #[test]
    fn configured_kind_explicit_local() {
        let _db = with_temp_db("ck_local", |conn| {
            seed_endpoint(conn, "ep3", "http://192.168.1.100:8080/v1", None, Some("local"), true);
        });

        assert_eq!(
            _configured_endpoint_kind("http://192.168.1.100:8080/v1").as_deref(),
            Some("local")
        );
    }

    #[test]
    fn configured_kind_none_when_no_match() {
        let _db = with_temp_db("ck_none", |conn| {
            seed_endpoint(conn, "ep4", "https://other.example.com/v1", None, None, true);
        });

        assert_eq!(
            _configured_endpoint_kind("https://totally-different.example.com/v1").as_deref(),
            None
        );
    }

    // -----------------------------------------------------------------------
    // _is_local_endpoint — endpoint_kind overrides URL heuristics
    // -----------------------------------------------------------------------

    #[test]
    fn is_local_endpoint_kind_api_overrides_private_ip() {
        // 192.168.x.x is normally local, but endpoint_kind="api" should return false.
        let _db = with_temp_db("ile_api", |conn| {
            seed_endpoint(
                conn,
                "ep5",
                "http://192.168.1.50/v1",
                Some("sk-k"),
                Some("api"),
                true,
            );
        });

        assert!(!_is_local_endpoint("http://192.168.1.50/v1/chat/completions"));
    }

    #[test]
    fn is_local_endpoint_kind_local_overrides_public_ip() {
        // A public IP marked endpoint_kind="local" should return true.
        let _db = with_temp_db("ile_local", |conn| {
            seed_endpoint(
                conn,
                "ep6",
                "http://203.0.113.5:8080/v1",
                None,
                Some("local"),
                true,
            );
        });

        assert!(_is_local_endpoint("http://203.0.113.5:8080/v1"));
    }

    // -----------------------------------------------------------------------
    // _normalize_base_for_compare: only strips ONE suffix per call
    // -----------------------------------------------------------------------
    #[test]
    fn normalize_only_strips_one_suffix() {
        // If a URL ends with /completions it should strip that, NOT also /v1/messages.
        let result = _normalize_base_for_compare("https://x.com/v1/completions");
        assert_eq!(result, "https://x.com/v1");
        // Not double-stripped to "https://x.com"
        assert!(result.ends_with("/v1"));
    }
}
