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

/// Check if URL points to a local/private/tailscale address.
pub fn _is_local_endpoint(url: &str) -> bool {
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
    // is_local = _is_local_endpoint(endpoint_url)
    // if not is_local and model in _context_cache: return _context_cache[model]
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
    if !is_local && ctx != DEFAULT_CONTEXT {
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
