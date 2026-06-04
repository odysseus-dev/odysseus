// src/llm_core.rs  <- src/llm_core.py
//! Core LLM API helpers.
//!
//! SCOPE: the pure, deterministic helpers are translated here —
//! `_detect_provider`, `normalize_model_id`, `list_model_ids`, and the
//! `LLMConfig` defaults. The network-bound surface of `llm_core.py`
//! (`llm_call`, `llm_call_async`, `stream_llm`, the httpx request/retry loop)
//! is `TODO(web)` and lands with the axum/reqwest tranche.

/// `_host_match(url, *domains)` — True if `url`'s hostname equals any of
/// `domains` or is a subdomain of one. Prefer this over substring matching on the
/// raw URL: the substring form gives wrong answers for unrelated paths/query
/// strings that happen to contain the domain text. The host is lowercased and a
/// trailing dot stripped (`rstrip(".")`), so `api.anthropic.com.` still matches
/// `anthropic.com`. Any parse failure → `false` (Python broad `except`).
pub fn _host_match(url: &str, domains: &[&str]) -> bool {
    if url.is_empty() {
        return false;
    }
    let host = match url::Url::parse(url) {
        Ok(p) => p
            .host_str()
            .map(|s| s.to_lowercase().trim_end_matches('.').to_string())
            .unwrap_or_default(),
        Err(_) => return false,
    };
    if host.is_empty() {
        return false;
    }
    domains
        .iter()
        .any(|d| host == *d || host.ends_with(&format!(".{d}")))
}

/// `_is_ollama_native_url(url)` — True for native Ollama API URLs, including
/// Ollama Cloud (`ollama.com`). A local Ollama host (`localhost` / `127.0.0.1` /
/// `0.0.0.0` / `::1` or port 11434) counts only when the path is `/api` or under
/// `/api/`. Any parse failure → `false` (Python broad `except`).
pub fn _is_ollama_native_url(url: &str) -> bool {
    let parsed = match url::Url::parse(url) {
        Ok(p) => p,
        Err(_) => return false,
    };
    // `host = parsed.hostname or ""` — urlparse lowercases the host; url crate
    // also lowercases. `path = (parsed.path or "").rstrip("/")`.
    let host = parsed.host_str().unwrap_or("").to_lowercase();
    let path = parsed.path().trim_end_matches('/');
    if _host_match(url, &["ollama.com"]) {
        return true;
    }
    let local_ollama_host = matches!(host.as_str(), "localhost" | "127.0.0.1" | "0.0.0.0" | "::1")
        || parsed.port() == Some(11434);
    local_ollama_host && (path == "/api" || path.starts_with("/api/"))
}

/// `_ollama_api_root(url)` — return a native Ollama API root such as
/// `https://ollama.com/api`. Strips a trailing `/chat`, `/tags`, or `/generate`
/// off an `/api/...` path; returns the url unchanged when it already ends in
/// `/api`; for an `ollama.com` host with neither, reconstructs `scheme://netloc`
/// + `/api`; otherwise returns the url unchanged.
pub fn _ollama_api_root(url: &str) -> String {
    // url = (url or "").strip().rstrip("/")
    let url = url.trim().trim_end_matches('/');
    let parsed = url::Url::parse(url).ok();
    let path = parsed
        .as_ref()
        .map(|p| p.path().trim_end_matches('/').to_string())
        .unwrap_or_default();
    if path.ends_with("/api/chat") {
        return url[..url.len() - "/chat".len()].to_string();
    }
    if path.ends_with("/api/tags") {
        return url[..url.len() - "/tags".len()].to_string();
    }
    if path.ends_with("/api/generate") {
        return url[..url.len() - "/generate".len()].to_string();
    }
    if path.ends_with("/api") {
        return url.to_string();
    }
    if _host_match(url, &["ollama.com"]) {
        // root = f"{scheme}://{netloc}" if scheme and netloc else "https://ollama.com"
        let root = match parsed.as_ref() {
            Some(p) if !p.scheme().is_empty() && p.host_str().is_some() => {
                let mut netloc = String::new();
                let user = p.username();
                if !user.is_empty() {
                    netloc.push_str(user);
                    if let Some(pw) = p.password() {
                        netloc.push(':');
                        netloc.push_str(pw);
                    }
                    netloc.push('@');
                }
                netloc.push_str(p.host_str().unwrap_or(""));
                if let Some(port) = p.port() {
                    netloc.push(':');
                    netloc.push_str(&port.to_string());
                }
                format!("{}://{}", p.scheme(), netloc)
            }
            _ => "https://ollama.com".to_string(),
        };
        return format!("{}/api", root.trim_end_matches('/'));
    }
    url.to_string()
}

/// `_normalize_ollama_url(url)` — ensure a native Ollama URL points at
/// `/api/chat`.
pub fn _normalize_ollama_url(url: &str) -> String {
    let base = _ollama_api_root(url);
    format!("{}/chat", base.trim_end_matches('/'))
}

/// Detect the API provider from a configured endpoint URL.
///
/// Matches on hostname (exact or subdomain) rather than substring, so a URL that
/// merely contains a provider's domain in its path or query — or a look-alike
/// host such as `anthropic.com.example` — is not misclassified. Unknown hosts
/// fall back to the OpenAI-compatible default, which the majority of providers
/// implement.
pub fn _detect_provider(url: &str) -> &'static str {
    if _is_ollama_native_url(url) {
        "ollama"
    } else if _host_match(url, &["anthropic.com"]) {
        "anthropic"
    } else if _host_match(url, &["openrouter.ai"]) {
        "openrouter"
    } else if _host_match(url, &["groq.com"]) {
        "groq"
    } else {
        "openai"
    }
}

/// `_provider_headers(provider, headers)` — base headers for an OpenAI-compatible
/// request. Always `Content-Type: application/json`, then any passthrough
/// `headers`, then (for OpenRouter only) the `HTTP-Referer` / `X-OpenRouter-Title`
/// attribution headers via `setdefault` (NOT overriding a caller-supplied value).
/// Returns an `IndexMap` so insertion order matches the Python dict.
pub fn _provider_headers(
    provider: &str,
    headers: Option<&indexmap::IndexMap<String, String>>,
) -> indexmap::IndexMap<String, String> {
    let mut h = indexmap::IndexMap::new();
    h.insert("Content-Type".to_string(), "application/json".to_string());
    if let Some(extra) = headers {
        for (k, v) in extra {
            h.insert(k.clone(), v.clone());
        }
    }
    if provider == "openrouter" {
        // h.setdefault(...) — only insert if absent.
        h.entry("HTTP-Referer".to_string())
            .or_insert_with(|| "https://github.com/pewdiepie-archdaemon/odysseus".to_string());
        h.entry("X-OpenRouter-Title".to_string())
            .or_insert_with(|| "Odysseus".to_string());
    }
    h
}

/// `_provider_label(url)` — human-friendly provider name for error messages.
///
/// Now keys on `_host_match` (hostname exact/subdomain) rather than substring, so
/// a URL that merely contains a provider's domain in its path is not
/// misclassified (FIRST match wins). After the known-provider ladder, an empty
/// host / parse failure yields `"provider"`, a known local host yields
/// `"local endpoint"`, else the bare lowercased hostname.
pub fn _provider_label(url: &str) -> String {
    // if not url: return "provider"
    if url.is_empty() {
        return "provider".to_string();
    }
    if _host_match(url, &["anthropic.com"]) {
        return "Anthropic".to_string();
    }
    if _host_match(url, &["ollama.com"]) {
        return "Ollama Cloud".to_string();
    }
    if _host_match(url, &["x.ai"]) {
        return "xAI".to_string();
    }
    if _host_match(url, &["openai.com"]) {
        return "OpenAI".to_string();
    }
    if _host_match(url, &["openrouter.ai"]) {
        return "OpenRouter".to_string();
    }
    if _host_match(url, &["groq.com"]) {
        return "Groq".to_string();
    }
    if _host_match(url, &["mistral.ai"]) {
        return "Mistral".to_string();
    }
    if _host_match(url, &["deepseek.com"]) {
        return "DeepSeek".to_string();
    }
    if _host_match(url, &["googleapis.com"]) {
        return "Google".to_string();
    }
    if _host_match(url, &["together.xyz", "together.ai"]) {
        return "Together".to_string();
    }
    if _host_match(url, &["fireworks.ai"]) {
        return "Fireworks".to_string();
    }
    if _is_ollama_native_url(url) {
        return "Ollama".to_string();
    }
    // try: host = (urlparse(url).hostname or "").lower(); except: return "provider"
    let host = match url::Url::parse(url) {
        Ok(p) => p.host_str().map(|s| s.to_lowercase()).unwrap_or_default(),
        Err(_) => return "provider".to_string(),
    };
    if matches!(host.as_str(), "localhost" | "127.0.0.1" | "::1" | "0.0.0.0") {
        return "local endpoint".to_string();
    }
    if host.is_empty() {
        "provider".to_string()
    } else {
        host
    }
}

/// `_format_upstream_error(status, body, url)` — turn an upstream HTTP error into
/// a user-readable sentence (`src/llm_core.py:172-211`).
///
/// Auth failures (401/403) become "xAI rejected the API key" etc., so the UI
/// stops showing raw JSON. The body is parsed for a nested `error.message` /
/// `error.detail` (or a bare string `error`); on any parse failure the raw body
/// (trimmed, first 240 chars) becomes the detail — exactly the Python `except`
/// fallback.
pub fn _format_upstream_error(status: u16, body: &str, url: &str) -> String {
    use serde_json::Value;
    let provider = _provider_label(url);
    // Try to pull a message out of the body.
    //   j = json.loads(body) if body else {}
    //   if isinstance(j, dict): err = j.get("error") or j; ...
    //   except Exception: detail = (body or "").strip()[:240]
    let detail: String = (|| {
        if body.is_empty() {
            // json.loads("") raises -> Python's except branch runs on the raw
            // (empty) body; the [:240] of "".strip() is "". Mirror by returning
            // "" via the parse-failure path below.
            return None;
        }
        let j: Value = serde_json::from_str(body).ok()?;
        // Only a dict body is inspected; a JSON list/scalar leaves detail "".
        let obj = j.as_object()?;
        // err = j.get("error") or j  (falsy "error" -> fall back to the whole dict)
        let err = match obj.get("error") {
            Some(e) if !is_falsy(e) => e,
            _ => &j,
        };
        if let Some(eobj) = err.as_object() {
            // detail = (err.get("message") or err.get("detail") or "").strip()
            // Python truthiness selects message-or-detail-or-""; calling `.strip()`
            // on a TRUTHY NON-STRING (a number/list/object) raises AttributeError,
            // which the outer `except` turns into the raw-body[:240] fallback. Skip
            // falsy values (None/false/0/""/[]/{}), strip a selected string, and
            // return None for a truthy non-string so the body fallback fires.
            let selected = eobj
                .get("message")
                .filter(|v| !is_falsy(v))
                .or_else(|| eobj.get("detail").filter(|v| !is_falsy(v)));
            match selected {
                None => Some(String::new()),
                Some(v) => v.as_str().map(|s| s.trim().to_string()),
            }
        } else if let Some(s) = err.as_str() {
            // elif isinstance(err, str): detail = err.strip()
            Some(s.trim().to_string())
        } else {
            Some(String::new())
        }
    })()
    // except Exception: detail = (body or "").strip()[:240]
    .unwrap_or_else(|| body.trim().chars().take(240).collect());

    if status == 401 || status == 403 {
        // msg = f"{provider} rejected the API key"; if 403: "...denied access (403)"
        let mut msg = if status == 403 {
            format!("{provider} denied access (403)")
        } else {
            format!("{provider} rejected the API key")
        };
        if !detail.is_empty() {
            msg += &format!(" — {detail}");
        }
        // ". Check Model Endpoints → {provider} and re-paste the key."
        msg += &format!(". Check Model Endpoints → {provider} and re-paste the key.");
        return msg;
    }
    if status == 404 {
        // f"{provider} returned 404 — check the base URL and model name." (+ f" ({detail})")
        let mut msg = format!("{provider} returned 404 — check the base URL and model name.");
        if !detail.is_empty() {
            msg += &format!(" ({detail})");
        }
        return msg;
    }
    if status == 429 {
        // f"{provider} rate-limited the request (429)." (+ f" {detail}")
        let mut msg = format!("{provider} rate-limited the request (429).");
        if !detail.is_empty() {
            msg += &format!(" {detail}");
        }
        return msg;
    }
    if status >= 500 {
        // f"{provider} is having an outage (HTTP {status})." (+ f" {detail}")
        let mut msg = format!("{provider} is having an outage (HTTP {status}).");
        if !detail.is_empty() {
            msg += &format!(" {detail}");
        }
        return msg;
    }
    // f"{provider} returned HTTP {status}" (+ f": {detail}")
    let mut msg = format!("{provider} returned HTTP {status}");
    if !detail.is_empty() {
        msg += &format!(": {detail}");
    }
    msg
}

/// Python truthiness test used only by `_format_upstream_error`'s `err =
/// j.get("error") or j`: a JSON `null` / `false` / `0` / `""` / `[]` / `{}` is
/// falsy, everything else truthy.
fn is_falsy(v: &serde_json::Value) -> bool {
    use serde_json::Value;
    match v {
        Value::Null => true,
        Value::Bool(b) => !b,
        Value::Number(n) => n.as_f64().map(|f| f == 0.0).unwrap_or(false),
        Value::String(s) => s.is_empty(),
        Value::Array(a) => a.is_empty(),
        Value::Object(o) => o.is_empty(),
    }
}

/// Dead-host cooldown (`src/llm_core.py:43-107`). When a connect to a host fails,
/// it is marked dead for [`DEAD_HOST_COOLDOWN`] seconds so subsequent calls fail
/// instantly instead of waiting on the connect timeout — one unreachable upstream
/// must not jam chat across the rest of the app.
///
/// A SINGLE transient blip should NOT trip a full lockout, so the host is only
/// cooled after [`HOST_FAIL_THRESHOLD`] consecutive failures; any success resets
/// the counter ([`_clear_host_dead`]). The two maps mirror the Python globals
/// `_dead_hosts` (host_key -> unix-ts cooldown expiry) and `_host_fails`
/// (host_key -> consecutive-failure count). Process-wide via `once_cell::Lazy`.
pub const DEAD_HOST_COOLDOWN: f64 = 20.0;
const HOST_FAIL_THRESHOLD: i64 = 2;

static DEAD_HOSTS: once_cell::sync::Lazy<std::sync::Mutex<std::collections::HashMap<String, f64>>> =
    once_cell::sync::Lazy::new(|| std::sync::Mutex::new(std::collections::HashMap::new()));
static HOST_FAILS: once_cell::sync::Lazy<std::sync::Mutex<std::collections::HashMap<String, i64>>> =
    once_cell::sync::Lazy::new(|| std::sync::Mutex::new(std::collections::HashMap::new()));

/// Current unix time in seconds (the `time.time()` the Python compares against).
fn _now_unix() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// `_host_key(url)` — `f"{scheme}://{netloc}"` if both present, else the raw url
/// (`urlsplit` never raises in Python; `url::Url::parse` failure mirrors the
/// no-scheme/no-netloc `else url` branch). `netloc` is `userinfo@host:port`.
pub fn _host_key(url: &str) -> String {
    match url::Url::parse(url) {
        Ok(s) => {
            let scheme = s.scheme();
            let host = s.host_str();
            // urlsplit: netloc empty when there is no host -> fall to `else url`.
            match host {
                Some(h) if !scheme.is_empty() => {
                    // Reconstruct netloc = [userinfo@]host[:port].
                    let mut netloc = String::new();
                    let user = s.username();
                    if !user.is_empty() {
                        netloc.push_str(user);
                        if let Some(pw) = s.password() {
                            netloc.push(':');
                            netloc.push_str(pw);
                        }
                        netloc.push('@');
                    }
                    netloc.push_str(h);
                    if let Some(port) = s.port() {
                        netloc.push(':');
                        netloc.push_str(&port.to_string());
                    }
                    format!("{scheme}://{netloc}")
                }
                _ => url.to_string(),
            }
        }
        Err(_) => url.to_string(),
    }
}

/// `_is_host_dead(url)` — True if the host is within an active cooldown. Expired
/// entries are popped (so the next call attempts again), matching the Python.
pub fn _is_host_dead(url: &str) -> bool {
    let key = _host_key(url);
    let mut dead = DEAD_HOSTS.lock().unwrap();
    match dead.get(&key).copied() {
        None => false,
        Some(exp) => {
            if _now_unix() >= exp {
                dead.remove(&key);
                false
            } else {
                true
            }
        }
    }
}

/// `_mark_host_dead(url)` — record a connect failure. Only actually cools the
/// host after [`HOST_FAIL_THRESHOLD`] consecutive failures. Returns `true` if the
/// host is now cooled (so callers can log accurately), `false` if still within
/// its allowed-failure grace.
pub fn _mark_host_dead(url: &str) -> bool {
    let key = _host_key(url);
    let n = {
        let mut fails = HOST_FAILS.lock().unwrap();
        let n = fails.get(&key).copied().unwrap_or(0) + 1;
        fails.insert(key.clone(), n);
        n
    };
    if n >= HOST_FAIL_THRESHOLD {
        DEAD_HOSTS
            .lock()
            .unwrap()
            .insert(key, _now_unix() + DEAD_HOST_COOLDOWN);
        true
    } else {
        false
    }
}

/// `_clear_host_dead(url)` — a success resets both the cooldown and the
/// consecutive-failure counter.
pub fn _clear_host_dead(url: &str) {
    let key = _host_key(url);
    DEAD_HOSTS.lock().unwrap().remove(&key);
    HOST_FAILS.lock().unwrap().remove(&key);
}

/// `_model_activity` — per-process last-use timestamp per endpoint/model, set on
/// every real upstream request (`note_model_activity`) and read by
/// `seconds_since_model_activity`. Lets background tasks (e.g. the skill audit)
/// defer while the model is actively serving the user.
static MODEL_ACTIVITY: once_cell::sync::Lazy<std::sync::Mutex<std::collections::HashMap<String, f64>>> =
    once_cell::sync::Lazy::new(|| std::sync::Mutex::new(std::collections::HashMap::new()));

/// `_model_activity_key(url, model)` = `f"{url.strip()}|{model.strip()}"`.
fn _model_activity_key(url: &str, model: &str) -> String {
    format!("{}|{}", url.trim(), model.trim())
}

/// `note_model_activity(url, model)` — record that a real upstream request used
/// this endpoint/model. No-op when either is empty (Python guard).
pub fn note_model_activity(url: &str, model: &str) {
    if url.is_empty() || model.is_empty() {
        return;
    }
    MODEL_ACTIVITY
        .lock()
        .unwrap()
        .insert(_model_activity_key(url, model), _now_unix());
}

/// `seconds_since_model_activity(url, model)` — seconds since the endpoint/model
/// was last used in this process, or `None` if never (the Python `if not ts:
/// return None`). Negative drift is clamped to 0.
pub fn seconds_since_model_activity(url: &str, model: &str) -> Option<f64> {
    let ts = *MODEL_ACTIVITY
        .lock()
        .unwrap()
        .get(&_model_activity_key(url, model))?;
    if ts == 0.0 {
        return None;
    }
    Some((_now_unix() - ts).max(0.0))
}

/// In-process LLM response cache (`_response_cache`, src/llm_core.py:41/123-133).
/// Keyed by `_get_cache_key`; capped at 128 with the oldest 64 evicted on
/// overflow (insertion order → `IndexMap`). Used by `llm_call_async` (NOT by
/// `stream_llm`, matching Python).
static RESPONSE_CACHE: once_cell::sync::Lazy<std::sync::Mutex<indexmap::IndexMap<String, String>>> =
    once_cell::sync::Lazy::new(|| std::sync::Mutex::new(indexmap::IndexMap::new()));

/// `_get_cache_key(url, model, messages, temperature, max_tokens)` — a stable
/// signature over the request. Faithful to the Python (each message's items
/// sorted, top-level keys sorted) via a `BTreeMap` + sorted per-message pairs,
/// then sha256-hex. The exact bytes need not match Python's (the cache is
/// in-process), only be deterministic for identical inputs.
fn _get_cache_key(
    url: &str,
    model: &str,
    messages: &[serde_json::Value],
    temperature: f64,
    max_tokens: i64,
) -> String {
    use sha2::{Digest, Sha256};
    use serde_json::{json, Value};
    // hashable_messages = [tuple(sorted(msg.items())) for msg in messages]
    let hashable: Vec<Value> = messages
        .iter()
        .map(|m| match m.as_object() {
            Some(o) => {
                let mut pairs: Vec<(&String, &Value)> = o.iter().collect();
                pairs.sort_by(|a, b| a.0.cmp(b.0));
                Value::Array(pairs.into_iter().map(|(k, v)| json!([k, v])).collect())
            }
            None => Value::Array(Vec::new()),
        })
        .collect();
    // json.dumps({...}, sort_keys=True) — BTreeMap serializes keys sorted.
    let mut outer: std::collections::BTreeMap<String, Value> = std::collections::BTreeMap::new();
    outer.insert("url".to_string(), Value::from(url));
    outer.insert("model".to_string(), Value::from(model));
    outer.insert("messages".to_string(), Value::Array(hashable));
    outer.insert("temp".to_string(), json!(temperature));
    outer.insert("max_tokens".to_string(), json!(max_tokens));
    let content = serde_json::to_string(&outer).unwrap_or_default();
    let mut h = Sha256::new();
    h.update(content.as_bytes());
    hex::encode(h.finalize())
}

/// `_get_cached_response(cache_key)`.
fn _get_cached_response(cache_key: &str) -> Option<String> {
    RESPONSE_CACHE.lock().unwrap().get(cache_key).cloned()
}

/// `_set_cached_response(cache_key, response)` — evict the oldest 64 when over
/// 128, then store (insertion-order FIFO, exactly the Python).
fn _set_cached_response(cache_key: &str, response: &str) {
    let mut cache = RESPONSE_CACHE.lock().unwrap();
    if cache.len() > 128 {
        let to_remove: Vec<String> = cache.keys().take(64).cloned().collect();
        for k in to_remove {
            cache.shift_remove(&k);
        }
    }
    cache.insert(cache_key.to_string(), response.to_string());
}

/// `_THINKING_MODEL_PATTERNS` — model-name substrings whose families stream a
/// closing `</think>` without an opening `<think>` on the first content token.
const THINKING_MODEL_PATTERNS: [&str; 7] =
    ["qwen3", "qwq", "deepseek-r1", "deepseek-reasoner", "minimax", "m2-reap", "gemma"];

/// `_supports_thinking(model)` — `src/llm_core.py:226-231`. True when the model
/// name (lowercased) contains a known thinking-family pattern.
pub fn _supports_thinking(model: &str) -> bool {
    if model.is_empty() {
        return false;
    }
    let m = model.to_lowercase();
    THINKING_MODEL_PATTERNS.iter().any(|p| m.contains(p))
}

/// Strip provider prefix from model ID for API calls.
///
/// OpenRouter-style 'deepseek/deepseek-chat' becomes 'deepseek-chat' for APIs
/// that don't expect the namespace. Keeps the original if no slash.
pub fn normalize_model_id(model_id: &str) -> String {
    // model_id.split("/")[-1] if model_id and "/" in model_id else model_id
    if !model_id.is_empty() && model_id.contains('/') {
        model_id.rsplit('/').next().unwrap_or(model_id).to_string()
    } else {
        model_id.to_string()
    }
}

/// Return a list of model IDs to try, in priority order.
///
/// For most endpoints this is just `[model]`. For OpenRouter-style model IDs with
/// a provider prefix, also try the bare model name as a fallback.
pub fn list_model_ids(model: &str) -> Vec<String> {
    let mut ids = vec![model.to_string()];
    let bare = normalize_model_id(model);
    if bare != model {
        ids.push(bare);
    }
    ids
}

/// `_model_list_base(url)` — normalize a model/chat URL to the configured
/// endpoint base so the in-DB `base_url` can be matched regardless of which
/// concrete model/chat suffix the caller passed in. Strips a trailing
/// `/models`, `/chat/completions`, `/completions`, or `/v1/messages`, then a
/// trailing native-Ollama `/api/chat`, `/api/tags`, or `/api/generate` (the
/// `"/api" + suffix` Python branch), re-stripping trailing slashes after each
/// removal. `None`/empty input → `""` (the Python `(url or "")`).
pub fn _model_list_base(url: &str) -> String {
    // base = (url or "").strip().rstrip("/")
    let mut base = url.trim().trim_end_matches('/').to_string();
    for suffix in ["/models", "/chat/completions", "/completions", "/v1/messages"] {
        if base.ends_with(suffix) {
            base = base[..base.len() - suffix.len()]
                .trim_end_matches('/')
                .to_string();
        }
    }
    for suffix in ["/chat", "/tags", "/generate"] {
        let api_suffix = format!("/api{suffix}");
        if base.ends_with(&api_suffix) {
            base = base[..base.len() - suffix.len()]
                .trim_end_matches('/')
                .to_string();
        }
    }
    base
}

/// `_parse_model_cache(raw)` — parse a stored `cached_models` / `hidden_models`
/// blob into a de-duplicated, order-preserving list of trimmed non-empty model
/// ids. The DB column is TEXT holding a JSON array string; a parse failure or a
/// non-array value yields `[]` (the Python broad `except` / `isinstance` guard).
/// Each item is `str(item or "").strip()` — a falsy element (null/empty) is
/// dropped, and duplicates (post-trim) are collapsed.
pub fn _parse_model_cache(raw: Option<&str>) -> Vec<String> {
    use serde_json::Value;
    // if not raw: return []
    let raw = match raw {
        Some(s) if !s.is_empty() => s,
        _ => return Vec::new(),
    };
    // models = json.loads(raw) ... except Exception: return []
    let parsed: Value = match serde_json::from_str(raw) {
        Ok(v) => v,
        Err(_) => return Vec::new(),
    };
    // if not isinstance(models, list): return []
    let arr = match parsed.as_array() {
        Some(a) => a,
        None => return Vec::new(),
    };
    let mut out: Vec<String> = Vec::new();
    let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
    for item in arr {
        // mid = str(item or "").strip()  — falsy element → "" → skipped
        let mid = if is_falsy(item) {
            String::new()
        } else {
            match item {
                Value::String(s) => s.clone(),
                other => other.to_string(),
            }
        };
        let mid = mid.trim().to_string();
        if mid.is_empty() || seen.contains(&mid) {
            continue;
        }
        seen.insert(mid.clone());
        out.push(mid);
    }
    out
}

/// `_configured_cached_model_ids(endpoint_url)` — return the cached models of a
/// configured, enabled endpoint whose `base_url` normalizes to the same base as
/// `endpoint_url`, with the endpoint's hidden models filtered out. Returns `[]`
/// when there is no normalized target, no matching enabled endpoint, no cached
/// models, or on any DB error (the Python broad `except`).
///
/// `rusqlite::Connection` is NOT `Send`; this is a sync helper and the
/// connection is opened, used, and dropped entirely within it (no `.await`
/// while it is held). Callers invoke it from the synchronous prefix of an async
/// fn, before any subsequent network `.await`.
pub fn _configured_cached_model_ids(endpoint_url: &str) -> Vec<String> {
    // target = _model_list_base(endpoint_url); if not target: return []
    let target = _model_list_base(endpoint_url);
    if target.is_empty() {
        return Vec::new();
    }
    // Test isolation (mirrors Python's `"core.database" not in sys.modules`
    // guard): only touch the DB when the current test set one up via `test_db`.
    // Otherwise this best-effort cached-model read would open a sibling test's
    // `DATABASE_URL` and race with its lifecycle. In production the DB is always
    // available, so the read runs.
    #[cfg(test)]
    if crate::core::database::current_thread_test_db().is_none() {
        return Vec::new();
    }
    // db = SessionLocal(); ... except Exception: return []
    let conn = match crate::core::database::session_local() {
        Ok(c) => c,
        Err(_) => return Vec::new(),
    };
    // rows = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all()
    let mut stmt = match conn.prepare(
        "SELECT base_url, cached_models, hidden_models FROM model_endpoints WHERE is_enabled = 1",
    ) {
        Ok(s) => s,
        Err(_) => return Vec::new(),
    };
    let rows = stmt.query_map([], |row| {
        Ok((
            row.get::<_, Option<String>>(0)?,
            row.get::<_, Option<String>>(1)?,
            row.get::<_, Option<String>>(2)?,
        ))
    });
    let rows = match rows {
        Ok(r) => r,
        Err(_) => return Vec::new(),
    };
    for row in rows {
        let (base_url, cached_models, hidden_models) = match row {
            Ok(t) => t,
            Err(_) => continue,
        };
        // if _model_list_base(getattr(ep, "base_url", "")) != target: continue
        if _model_list_base(base_url.as_deref().unwrap_or("")) != target {
            continue;
        }
        // models = _parse_model_cache(ep.cached_models or ep.models)
        // (the Rust schema has no separate `models` column; `cached_models` is it)
        let models = _parse_model_cache(cached_models.as_deref());
        if models.is_empty() {
            continue;
        }
        // hidden = set(_parse_model_cache(ep.hidden_models))
        let hidden: std::collections::HashSet<String> =
            _parse_model_cache(hidden_models.as_deref()).into_iter().collect();
        // return [m for m in models if m not in hidden]
        return models.into_iter().filter(|m| !hidden.contains(m)).collect();
    }
    Vec::new()
}

/// Network port of Python `llm_core.list_model_ids(base_chat_url, timeout, headers)`
/// — the `/models` discovery probe. (NOTE: the bare-string `list_model_ids` above
/// and `normalize_model_id` are the Rust port's pure-string fallback helpers,
/// which took those names; this is the distinct NETWORK form.)
///
/// CACHED-FIRST (upstream a2e691d): before any network probe, return the cached
/// model list of a matching configured endpoint when one exists. Large
/// OpenAI-compatible proxy endpoints expose hundreds of models and make
/// `/v1/models` slow; treating them like local model servers made model-picker
/// opens and background probes repeatedly hit `/models`, producing timeouts and
/// making usable endpoints appear offline. Manual Test/Add/Refresh paths still
/// fetch the full list with longer timeouts.
///
/// Anthropic endpoints short-circuit to `ANTHROPIC_MODELS` (no `/models` route).
/// Otherwise GET `<base>/models` (the `/chat/completions`→`/models` rewrite) with
/// the given auth `headers` and a 30s timeout, returning each `data[].id`. Any
/// transport / non-2xx / parse error → `[]` (the Python broad `except`).
pub async fn list_served_model_ids_with_headers(
    base_chat_url: &str,
    headers: &indexmap::IndexMap<String, String>,
) -> Vec<String> {
    // cached = _configured_cached_model_ids(base_chat_url); if cached: return cached
    // Synchronous DB read fully scoped here — the non-Send Connection is dropped
    // before any network `.await` below.
    let cached = _configured_cached_model_ids(base_chat_url);
    if !cached.is_empty() {
        return cached;
    }
    if _detect_provider(base_chat_url) == "anthropic" {
        return ANTHROPIC_MODELS.iter().map(|s| s.to_string()).collect();
    }
    let url = base_chat_url.replace("/chat/completions", "/models");
    let client = match reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(LLMConfig::DEFAULT_TIMEOUT as u64))
        .build()
    {
        Ok(c) => c,
        Err(_) => return Vec::new(),
    };
    // h = {}; if headers: h.update(headers)
    let mut hmap = reqwest::header::HeaderMap::new();
    for (k, v) in headers {
        if let (Ok(name), Ok(val)) = (
            reqwest::header::HeaderName::from_bytes(k.as_bytes()),
            reqwest::header::HeaderValue::from_str(v),
        ) {
            hmap.insert(name, val);
        }
    }
    let resp = match client.get(&url).headers(hmap).send().await {
        Ok(r) => r,
        Err(_) => return Vec::new(),
    };
    if !resp.status().is_success() {
        return Vec::new();
    }
    let data: serde_json::Value = match resp.json().await {
        Ok(v) => v,
        Err(_) => return Vec::new(),
    };
    // [m.get("id") for m in (r.json().get("data") or []) if m.get("id")]
    data.get("data")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|m| {
                    m.get("id")
                        .and_then(|v| v.as_str())
                        .filter(|s| !s.is_empty())
                        .map(|s| s.to_string())
                })
                .collect()
        })
        .unwrap_or_default()
}

/// `list_served_model_ids` with NO auth headers — the Python `normalize_model_id`
/// call path (`headers=None`), so authed/cloud endpoints 401 → `[]` (a no-op
/// normalize, matching Python).
pub async fn list_served_model_ids(base_chat_url: &str) -> Vec<String> {
    list_served_model_ids_with_headers(base_chat_url, &indexmap::IndexMap::new()).await
}

/// `os.path.basename(s.rstrip("/"))` — the part after the last `/`, trailing
/// slashes stripped first. Used by `normalize_model_id_network` and the
/// `action_test_skills` served-model swap.
pub fn basename_rstrip_slash(s: &str) -> &str {
    let t = s.trim_end_matches('/');
    t.rsplit('/').next().unwrap_or(t)
}

/// Network port of Python `normalize_model_id(endpoint_url, requested)`: probe the
/// endpoint's served model ids and canonicalize `requested` to a served id —
/// exact match first, then an `os.path.basename` match. Returns `None` when the
/// probe yields nothing or no served id matches (the Python `return None` arms),
/// which the caller treats as "leave the model id unchanged".
pub async fn normalize_model_id_network(endpoint_url: &str, requested: &str) -> Option<String> {
    let avail = list_served_model_ids(endpoint_url).await;
    if avail.is_empty() {
        return None;
    }
    if avail.iter().any(|a| a == requested) {
        return Some(requested.to_string());
    }
    let req_base = basename_rstrip_slash(requested);
    avail
        .iter()
        .find(|a| basename_rstrip_slash(a) == req_base)
        .cloned()
}

/// `_MAX_COMPLETION_TOKENS_MODELS` + `_uses_max_completion_tokens` — o1/o3/o4/
/// gpt-4.5/gpt-5 use `max_completion_tokens` instead of `max_tokens`.
const MAX_COMPLETION_TOKENS_MODELS: &[&str] = &["o1", "o3", "o4", "gpt-4.5", "gpt-5"];

pub fn _uses_max_completion_tokens(model: &str) -> bool {
    if model.is_empty() {
        return false;
    }
    let m = model.to_lowercase();
    MAX_COMPLETION_TOKENS_MODELS
        .iter()
        .any(|p| m.starts_with(p) || m.contains(&format!("/{p}")))
}

/// `_FIXED_TEMPERATURE_MODELS` — OpenAI reasoning models (o1, o3, o4, gpt-5
/// families) only accept the default temperature; sending any explicit value —
/// even `0.0` — returns HTTP 400 ("Only the default (1) value is supported").
/// For these models the OpenAI-compatible payload omits the `temperature` field
/// entirely. (`gpt-4.5` is intentionally excluded — it is NOT a reasoning model
/// and accepts temperature normally, so unlike `_MAX_COMPLETION_TOKENS_MODELS`
/// this set has no `gpt-4.5`.)
const FIXED_TEMPERATURE_MODELS: &[&str] = &["o1", "o3", "o4", "gpt-5"];

/// `_restricts_temperature(model)` — True when the model rejects any non-default
/// temperature (same `startswith` / `"/{p}" in m` matching as
/// `_uses_max_completion_tokens`).
pub fn _restricts_temperature(model: &str) -> bool {
    if model.is_empty() {
        return false;
    }
    let m = model.to_lowercase();
    FIXED_TEMPERATURE_MODELS
        .iter()
        .any(|p| m.starts_with(p) || m.contains(&format!("/{p}")))
}

/// `ANTHROPIC_MODELS` — the hardcoded fallback model list for Anthropic
/// endpoints (which have no `/models` discovery the way OpenAI-compatible
/// servers do). `model_routes._probe_endpoint` returns `list(ANTHROPIC_MODELS)`
/// when the live `/v1/models` query fails without an API key, and
/// `ping_endpoints` reports `len(ANTHROPIC_MODELS)` as the model count. Order is
/// preserved exactly (it is iterated/serialized).
pub const ANTHROPIC_MODELS: &[&str] = &[
    "claude-opus-4-20250514",
    "claude-opus-4",
    "claude-sonnet-4-20250514",
    "claude-sonnet-4",
    "claude-sonnet-4-5-20250929",
    "claude-sonnet-4-5",
    "claude-haiku-4-20250514",
    "claude-haiku-4",
    "claude-haiku-3-5-20241022",
    "claude-haiku-3-5",
];

/// `_ollama_normalize_tool_messages(messages)` — adapt Odysseus' canonical
/// OpenAI-style messages to native Ollama `/api/chat`.
///
/// Odysseus carries assistant tool calls in the OpenAI shape, where
/// `function.arguments` is a JSON *string*. Native Ollama expects it to be a JSON
/// *object*; given the string it fails the whole request with HTTP 400, aborting
/// every follow-up round. Parse the arguments back into an object here, on a
/// shallow copy, leaving non-tool messages untouched. The opaque Gemini
/// `extra_content` (thought_signature) is dropped.
fn _ollama_normalize_tool_messages(messages: &[serde_json::Value]) -> Vec<serde_json::Value> {
    use serde_json::{json, Value};
    let mut out: Vec<Value> = Vec::new();
    for m in messages {
        // tcs = m.get("tool_calls") if isinstance(m, dict) else None
        let tcs = m.as_object().and_then(|_| m.get("tool_calls"));
        // if not tcs: out.append(m); continue  (None / empty list are falsy)
        let tcs_arr = match tcs.and_then(|t| t.as_array()) {
            Some(a) if !a.is_empty() => a,
            _ => {
                out.push(m.clone());
                continue;
            }
        };
        let mut new_calls: Vec<Value> = Vec::new();
        for tc in tcs_arr {
            let fn_obj = tc.get("function");
            // args = fn.get("arguments"); if isinstance(args, str): json.loads or {}
            let args: Value = match fn_obj.and_then(|f| f.get("arguments")) {
                Some(Value::String(s)) => {
                    if s.trim().is_empty() {
                        json!({})
                    } else {
                        serde_json::from_str(s).unwrap_or_else(|_| json!({}))
                    }
                }
                Some(other) => other.clone(),
                None => Value::Null,
            };
            // "arguments": args or {}  (falsy args -> {})
            let args = if is_falsy(&args) { json!({}) } else { args };
            let name = fn_obj
                .and_then(|f| f.get("name"))
                .and_then(|n| n.as_str())
                .unwrap_or("");
            let mut call = serde_json::Map::new();
            call.insert(
                "function".to_string(),
                json!({"name": name, "arguments": args}),
            );
            // if tc.get("id"): call["id"] = tc["id"]  (truthy id only)
            if let Some(id) = tc.get("id") {
                if !is_falsy(id) {
                    call.insert("id".to_string(), id.clone());
                }
            }
            new_calls.push(Value::Object(call));
        }
        // nm = dict(m); nm["tool_calls"] = new_calls
        let mut nm = m.as_object().cloned().unwrap_or_default();
        nm.insert("tool_calls".to_string(), Value::Array(new_calls));
        out.push(Value::Object(nm));
    }
    out
}

/// `_build_ollama_payload(...)` — build the JSON payload for Ollama's `/api/chat`
/// endpoint. `num_ctx` sets the input context window; it is only emitted when
/// trusted (> 0 and NOT the `DEFAULT_CONTEXT` fallback). `temperature` is always
/// emitted (Python `if temperature is not None`; the Rust `f64` is never None, so
/// it is always set, exactly like a Python caller passing a concrete float).
#[allow(clippy::too_many_arguments)]
pub fn _build_ollama_payload(
    model: &str,
    messages: &[serde_json::Value],
    temperature: f64,
    max_tokens: i64,
    stream: bool,
    tools: Option<&[serde_json::Value]>,
    num_ctx: Option<i64>,
) -> serde_json::Value {
    use serde_json::{json, Map, Value};
    let mut payload = Map::new();
    payload.insert("model".to_string(), json!(model));
    payload.insert(
        "messages".to_string(),
        Value::Array(_ollama_normalize_tool_messages(messages)),
    );
    payload.insert("stream".to_string(), json!(stream));
    let mut options = Map::new();
    // if temperature is not None: options["temperature"] = temperature
    options.insert("temperature".to_string(), json!(temperature));
    // if max_tokens and max_tokens > 0: options["num_predict"] = max_tokens
    if max_tokens > 0 {
        options.insert("num_predict".to_string(), json!(max_tokens));
    }
    // if num_ctx is not None and num_ctx > 0 and num_ctx != DEFAULT_CONTEXT:
    if let Some(nc) = num_ctx {
        if nc > 0 && nc != crate::src::model_context::DEFAULT_CONTEXT {
            options.insert("num_ctx".to_string(), json!(nc));
        }
    }
    if !options.is_empty() {
        payload.insert("options".to_string(), Value::Object(options));
    }
    // if tools: payload["tools"] = tools  (truthy = non-empty list)
    if let Some(t) = tools {
        if !t.is_empty() {
            payload.insert("tools".to_string(), Value::Array(t.to_vec()));
        }
    }
    Value::Object(payload)
}

/// `_parse_ollama_response(data)` — `message.content or data.response or ""`.
fn _parse_ollama_response(data: &serde_json::Value) -> String {
    // message = data.get("message") or {}; return message.get("content") or data.get("response") or ""
    let content = data
        .get("message")
        .and_then(|m| m.get("content"))
        .and_then(|c| c.as_str())
        .filter(|s| !s.is_empty());
    if let Some(c) = content {
        return c.to_string();
    }
    data.get("response")
        .and_then(|r| r.as_str())
        .unwrap_or("")
        .to_string()
}

/// `_normalize_anthropic_url(url)` — ensure an Anthropic base/chat URL points at
/// `/v1/messages`. Used by `model_routes._probe_single_model` to target the
/// Anthropic messages API.
pub fn _normalize_anthropic_url(url: &str) -> String {
    // url = url.rstrip("/")
    let url = url.trim_end_matches('/');
    if url.ends_with("/v1/messages") {
        url.to_string()
    } else if url.ends_with("/v1") {
        format!("{url}/messages")
    } else {
        format!("{url}/v1/messages")
    }
}

/// `_parse_anthropic_response(data)` — extract the assistant text from an
/// Anthropic `/v1/messages` response (`src/llm_core.py:352-357`). Iterates the
/// `content` array and returns the `text` of the FIRST block whose `type` is
/// `"text"`, else the empty string (which then trips the caller's 502
/// "Unexpected schema" guard, exactly like the OpenAI branch's missing
/// `choices[0].message.content`).
///
/// Consumed only by the `llm_call_async` Anthropic branch (the crate has no
/// feature flags — one plain build — so this is always compiled).
fn _parse_anthropic_response(data: &serde_json::Value) -> String {
    // "".join(block.get("text", "") for block in data.get("content", [])
    //          if isinstance(block, dict) and block.get("type") == "text")
    // The Messages API `content` can hold MORE THAN ONE text block (text split
    // around a tool_use block, or citation-segmented text); concatenate them all
    // instead of returning only the first.
    let mut out = String::new();
    if let Some(blocks) = data.get("content").and_then(|c| c.as_array()) {
        for block in blocks {
            if block.is_object() && block.get("type").and_then(|t| t.as_str()) == Some("text") {
                out.push_str(block.get("text").and_then(|t| t.as_str()).unwrap_or(""));
            }
        }
    }
    out
}

/// `_build_anthropic_headers(headers)` — convert OpenAI-style `Bearer` auth into
/// Anthropic's `x-api-key`, always stamping `Content-Type` and
/// `anthropic-version`. Other headers pass through unchanged. Returns an
/// `IndexMap` so insertion order (Content-Type, anthropic-version, then any
/// passthrough/x-api-key) matches the Python dict.
pub fn _build_anthropic_headers(
    headers: &indexmap::IndexMap<String, String>,
) -> indexmap::IndexMap<String, String> {
    // h = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
    let mut h = indexmap::IndexMap::new();
    h.insert("Content-Type".to_string(), "application/json".to_string());
    h.insert("anthropic-version".to_string(), "2023-06-01".to_string());
    // if headers: for k, v in headers.items(): ...
    for (k, v) in headers {
        // if k.lower() == "authorization" and v.startswith("Bearer "): x-api-key = v[7:]
        if k.to_lowercase() == "authorization" {
            if let Some(key) = v.strip_prefix("Bearer ") {
                h.insert("x-api-key".to_string(), key.to_string());
                continue;
            }
        }
        h.insert(k.clone(), v.clone());
    }
    h
}

/// `_convert_openai_content_to_anthropic(content)` — convert OpenAI multimodal
/// content blocks (`image_url` data-URIs / external URLs) into Anthropic `image`
/// blocks, passing text and unknown blocks through. A non-array content value is
/// returned unchanged. Module-private (only `_build_anthropic_payload` calls it),
/// mirroring the Python helper.
fn _convert_openai_content_to_anthropic(content: &serde_json::Value) -> serde_json::Value {
    use serde_json::{json, Value};
    // if not isinstance(content, list): return content
    let blocks = match content.as_array() {
        Some(b) => b,
        None => return content.clone(),
    };
    let mut converted: Vec<Value> = Vec::new();
    for block in blocks {
        // if not isinstance(block, dict): converted.append(block); continue
        let obj = match block.as_object() {
            Some(o) => o,
            None => {
                converted.push(block.clone());
                continue;
            }
        };
        let btype = obj.get("type").and_then(|t| t.as_str()).unwrap_or("");
        if btype == "image_url" {
            // url = (block.get("image_url") or {}).get("url", "")
            let url = obj
                .get("image_url")
                .and_then(|iu| iu.get("url"))
                .and_then(|u| u.as_str())
                .unwrap_or("");
            if let Some(rest) = url.strip_prefix("data:") {
                // header, b64_data = url.split(",", 1); media_type = header.split(";")[0].replace("data:", "")
                // (rest is the part after "data:"; split on the first comma)
                match rest.split_once(',') {
                    Some((header, b64_data)) => {
                        let media_type = header.split(';').next().unwrap_or("");
                        converted.push(json!({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64_data,
                            },
                        }));
                    }
                    // except (ValueError, IndexError): continue  (no comma → skip block)
                    None => continue,
                }
            } else {
                // External URL — use Anthropic's URL source
                converted.push(json!({
                    "type": "image",
                    "source": {"type": "url", "url": url},
                }));
            }
        } else {
            // "text" and any other block pass through unchanged
            converted.push(block.clone());
        }
    }
    Value::Array(converted)
}

/// `_build_anthropic_payload(model, messages, temperature, max_tokens, stream,
/// tools)` — convert OpenAI-style messages into an Anthropic `/v1/messages`
/// payload. Used by `model_routes._probe_single_model` (with `stream=false` and
/// optional `tools`). System messages are concatenated into `system`; `tool`
/// results and assistant `tool_calls` are translated to Anthropic blocks;
/// multimodal content is converted. `max_tokens <= 0` defaults to 4096.
pub fn _build_anthropic_payload(
    model: &str,
    messages: &[serde_json::Value],
    temperature: f64,
    max_tokens: i64,
    stream: bool,
    tools: Option<&[serde_json::Value]>,
) -> serde_json::Value {
    use serde_json::{json, Map, Value};

    let mut system_parts: Vec<String> = Vec::new();
    let mut chat_messages: Vec<Value> = Vec::new();

    for m in messages {
        let role = m.get("role").and_then(|r| r.as_str()).unwrap_or("");
        if role == "system" {
            // system_parts.append(m["content"])
            system_parts.push(
                m.get("content")
                    .and_then(|c| c.as_str())
                    .unwrap_or("")
                    .to_string(),
            );
        } else if role == "tool" {
            // Convert OpenAI tool result → Anthropic tool_result
            chat_messages.push(json!({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": m.get("tool_call_id").and_then(|t| t.as_str()).unwrap_or(""),
                    "content": m.get("content").and_then(|c| c.as_str()).unwrap_or(""),
                }],
            }));
        } else if role == "assistant"
            && m.get("tool_calls").map(|tc| tc.is_array()).unwrap_or(false)
        {
            // Convert OpenAI assistant tool_calls → Anthropic tool_use blocks
            let mut content: Vec<Value> = Vec::new();
            // if m.get("content"): append text block  (truthy = non-empty string)
            if let Some(text) = m.get("content").and_then(|c| c.as_str()) {
                if !text.is_empty() {
                    content.push(json!({"type": "text", "text": text}));
                }
            }
            if let Some(tcs) = m.get("tool_calls").and_then(|t| t.as_array()) {
                for tc in tcs {
                    let fn_obj = tc.get("function");
                    // args_str = fn.get("arguments", "{}")
                    // args = json.loads(args_str) if isinstance(args_str, str) else args_str
                    //   ... except (json.JSONDecodeError, TypeError): args = {}
                    let input: Value = match fn_obj.and_then(|f| f.get("arguments")) {
                        // string → json.loads, `{}` on parse failure
                        Some(Value::String(s)) => {
                            serde_json::from_str(s).unwrap_or_else(|_| json!({}))
                        }
                        // already-parsed non-string value passes through
                        Some(other) => other.clone(),
                        // missing → default "{}" string → json.loads → {}
                        None => json!({}),
                    };
                    content.push(json!({
                        "type": "tool_use",
                        "id": tc.get("id").and_then(|i| i.as_str()).unwrap_or(""),
                        "name": fn_obj.and_then(|f| f.get("name")).and_then(|n| n.as_str()).unwrap_or(""),
                        "input": input,
                    }));
                }
            }
            chat_messages.push(json!({"role": "assistant", "content": content}));
        } else {
            // Convert multimodal content (image_url → image) for Anthropic
            let content = m.get("content").cloned().unwrap_or(Value::Null);
            chat_messages.push(json!({
                "role": role,
                "content": _convert_openai_content_to_anthropic(&content),
            }));
        }
    }

    // Anthropic only accepts temperature in [0.0, 1.0] and 400s on anything above
    // 1.0. Clamp here (in the Anthropic builder only) so presets/sliders using the
    // wider OpenAI 0.0-2.0 range don't hard-break every Claude request. OpenAI's
    // own path is left untouched. (Python `if temperature is not None`; the Rust
    // f64 is always present.)
    let temperature = temperature.clamp(0.0, 1.0);

    // `tools` is truthy iff present AND non-empty (Python `if tools`); used both by
    // the tools conversion below and by the system prompt-cache decision.
    let tools_truthy = tools.map(|t| !t.is_empty()).unwrap_or(false);

    let mut payload = Map::new();
    payload.insert("model".to_string(), json!(model));
    payload.insert("messages".to_string(), Value::Array(chat_messages));
    // max_tokens if max_tokens and max_tokens > 0 else 4096
    payload.insert(
        "max_tokens".to_string(),
        json!(if max_tokens > 0 { max_tokens } else { 4096 }),
    );
    payload.insert("temperature".to_string(), json!(temperature));
    if !system_parts.is_empty() {
        // Send `system` as a structured text block so we can attach a prompt-cache
        // breakpoint. The agent loop re-sends this same large prefix every round;
        // caching it makes Anthropic re-read it from cache (~90% cheaper, lower
        // TTFB). Skip caching tiny one-off prompts (no reuse, cache-WRITE premium
        // wouldn't pay back). Presence of `tools` means an agentic/multi-round
        // call, where the prefix is always reused.
        let system_text = system_parts.join("\n\n");
        let mut system_block = Map::new();
        system_block.insert("type".to_string(), json!("text"));
        system_block.insert("text".to_string(), json!(system_text));
        if tools_truthy || system_text.chars().count() > 4000 {
            system_block.insert("cache_control".to_string(), json!({"type": "ephemeral"}));
        }
        payload.insert(
            "system".to_string(),
            Value::Array(vec![Value::Object(system_block)]),
        );
    }
    if stream {
        payload.insert("stream".to_string(), json!(true));
    }
    // Convert OpenAI-format tools → Anthropic format
    if let Some(tools) = tools {
        let mut anthropic_tools: Vec<Value> = Vec::new();
        for t in tools {
            if t.get("type").and_then(|ty| ty.as_str()) == Some("function") {
                if let Some(fn_obj) = t.get("function") {
                    anthropic_tools.push(json!({
                        "name": fn_obj.get("name").and_then(|n| n.as_str()).unwrap_or(""),
                        "description": fn_obj.get("description").and_then(|d| d.as_str()).unwrap_or(""),
                        "input_schema": fn_obj.get("parameters").cloned().unwrap_or_else(
                            || json!({"type": "object", "properties": {}})
                        ),
                    }));
                }
            }
        }
        if !anthropic_tools.is_empty() {
            // Cache the tool schemas too — they're stable for the whole agent run.
            // The breakpoint caches all tool defs preceding it in the request.
            if let Some(last) = anthropic_tools.last_mut() {
                if let Some(o) = last.as_object_mut() {
                    o.insert("cache_control".to_string(), json!({"type": "ephemeral"}));
                }
            }
            payload.insert("tools".to_string(), Value::Array(anthropic_tools));
        }
    }
    Value::Object(payload)
}

/// `_as_content_blocks(content)` — coerce a message `content` into a list of
/// content blocks. A list passes through; a non-empty string becomes a single
/// text block; None/empty yields no blocks. Used by `_sanitize_llm_messages`'s
/// consecutive-user merge so multimodal content isn't `str()`-ed away.
fn _as_content_blocks(content: Option<&serde_json::Value>) -> Vec<serde_json::Value> {
    use serde_json::{json, Value};
    match content {
        Some(Value::Array(a)) => a.clone(),
        // `if content:` — truthy non-list. For a string this is non-empty; for any
        // other truthy scalar Python str()'s it. Strings dominate here.
        Some(v) if !is_falsy(v) => {
            let s = match v {
                Value::String(s) => s.clone(),
                other => other.to_string(),
            };
            vec![json!({"type": "text", "text": s})]
        }
        _ => Vec::new(),
    }
}

/// `_sanitize_llm_messages(messages)` — strip Odysseus-only metadata before
/// sending messages to providers, repair tool-call adjacency, and merge
/// consecutive user messages.
///
/// Per the OpenAI chat format: user/system messages must have content; a tool
/// message needs content + tool_call_id; an assistant message may carry content,
/// tool_calls, or both (a tool-calls-only assistant message gets an explicit
/// `content: null`). Then orphan/unmatched/duplicate tool results are dropped and
/// unanswered assistant tool_calls pruned (so DeepSeek etc. don't 400). Finally
/// consecutive user messages are merged to satisfy strict role-alternation,
/// preserving multimodal content-block lists.
pub fn _sanitize_llm_messages(messages: &[serde_json::Value]) -> Vec<serde_json::Value> {
    use serde_json::{json, Map, Value};

    const ALLOWED: &[&str] = &[
        "role",
        "content",
        "name",
        "tool_call_id",
        "tool_calls",
        "function_call",
    ];

    // ── Phase 1: whitelist keys, drop None values, validate per-role shape. ──
    let mut cleaned: Vec<Map<String, Value>> = Vec::new();
    for msg in messages {
        let obj = match msg.as_object() {
            Some(o) => o,
            None => continue, // if not isinstance(msg, dict): continue
        };
        // item = {k: v for k, v in msg.items() if k in allowed and v is not None}
        let mut item: Map<String, Value> = Map::new();
        for (k, v) in obj {
            if ALLOWED.contains(&k.as_str()) && !v.is_null() {
                item.insert(k.clone(), v.clone());
            }
        }
        let role = match item.get("role").and_then(|r| r.as_str()) {
            Some(r) if !r.is_empty() => r.to_string(),
            // role falsy (missing/None-stripped/empty string) -> continue
            _ => continue,
        };
        if role == "assistant" {
            // Re-add explicit content=None when tool-calls-only (None was stripped).
            // `item.get("tool_calls")` truthy = present non-empty list.
            let has_tool_calls = item
                .get("tool_calls")
                .map(|t| !is_falsy(t))
                .unwrap_or(false);
            if !item.contains_key("content") && has_tool_calls {
                item.insert("content".to_string(), Value::Null);
            }
            if item.contains_key("content") || has_tool_calls {
                cleaned.push(item);
            }
        } else if role == "tool" {
            if item.contains_key("content") && item.contains_key("tool_call_id") {
                cleaned.push(item);
            }
        } else if item.contains_key("content") {
            cleaned.push(item);
        }
    }

    // ── Phase 2: repair tool-call adjacency. ──
    let mut repaired: Vec<Map<String, Value>> = Vec::new();
    let mut i = 0usize;
    while i < cleaned.len() {
        let msg = &cleaned[i];
        let role = msg.get("role").and_then(|r| r.as_str()).unwrap_or("");

        if role == "tool" {
            // Orphan tool result — no valid assistant tool_calls parent before it.
            crate::pylog::debug("Dropping orphan tool message before provider request");
            i += 1;
            continue;
        }

        // tool_calls = msg.get("tool_calls") if role == "assistant" else None
        let tool_calls: Option<Vec<Value>> = if role == "assistant" {
            msg.get("tool_calls")
                .and_then(|t| t.as_array())
                .filter(|a| !a.is_empty())
                .cloned()
        } else {
            None
        };
        let tool_calls = match tool_calls {
            // `if not tool_calls:` — None or empty list passes through unchanged.
            None => {
                repaired.push(msg.clone());
                i += 1;
                continue;
            }
            Some(tc) => tc,
        };

        // expected = {str(tc["id"]) for tc in tool_calls if tc dict and tc.get("id")}
        let mut expected: std::collections::HashSet<String> = std::collections::HashSet::new();
        for tc in &tool_calls {
            if let Some(o) = tc.as_object() {
                if let Some(id) = o.get("id") {
                    if !is_falsy(id) {
                        expected.insert(json_to_str(id));
                    }
                }
            }
        }
        let mut answered_ids: Vec<String> = Vec::new();
        let mut tool_batch: Vec<Map<String, Value>> = Vec::new();
        let mut j = i + 1;
        while j < cleaned.len() && cleaned[j].get("role").and_then(|r| r.as_str()) == Some("tool") {
            // tid = str(cleaned[j].get("tool_call_id") or "")
            let tid = cleaned[j]
                .get("tool_call_id")
                .filter(|v| !is_falsy(v))
                .map(json_to_str)
                .unwrap_or_default();
            if expected.contains(&tid) && !answered_ids.contains(&tid) {
                answered_ids.push(tid);
                tool_batch.push(cleaned[j].clone());
            } else {
                crate::pylog::debug(
                    "Dropping unmatched/duplicate tool message before provider request",
                );
            }
            j += 1;
        }

        if tool_batch.is_empty() {
            // plain = {k: v for k, v in msg.items() if k != "tool_calls"}
            let mut plain: Map<String, Value> = msg.clone();
            plain.remove("tool_calls");
            // if (plain.get("content") or "").strip(): keep else drop
            let content_str = plain
                .get("content")
                .and_then(|c| c.as_str())
                .unwrap_or("")
                .trim();
            if !content_str.is_empty() {
                repaired.push(plain);
            } else {
                crate::pylog::debug(
                    "Dropping unanswered assistant tool_calls before provider request",
                );
            }
            i = j;
            continue;
        }

        let answered: std::collections::HashSet<&String> = answered_ids.iter().collect();
        // pruned_calls = [tc for tc in tool_calls if tc dict and str(tc.get("id")) in answered]
        let pruned_calls: Vec<Value> = tool_calls
            .iter()
            .filter(|tc| {
                tc.as_object()
                    .map(|o| answered.contains(&json_to_str(o.get("id").unwrap_or(&Value::Null))))
                    .unwrap_or(false)
            })
            .cloned()
            .collect();
        let mut fixed: Map<String, Value> = msg.clone();
        fixed.insert("tool_calls".to_string(), Value::Array(pruned_calls.clone()));
        if !fixed.contains_key("content") {
            fixed.insert("content".to_string(), Value::Null);
        }
        repaired.push(fixed);
        repaired.extend(tool_batch);
        if pruned_calls.len() != tool_calls.len() {
            crate::pylog::debug("Pruned unanswered assistant tool_calls before provider request");
        }
        i = j;
    }

    // ── Phase 3: merge consecutive user messages. ──
    let mut merged: Vec<Map<String, Value>> = Vec::new();
    for item in repaired {
        if merged.is_empty() {
            merged.push(item);
            continue;
        }
        let last_is_user = merged
            .last()
            .and_then(|m| m.get("role"))
            .and_then(|r| r.as_str())
            == Some("user");
        let item_is_user = item.get("role").and_then(|r| r.as_str()) == Some("user");
        if last_is_user && item_is_user {
            let last = merged.last_mut().unwrap();
            let lc = last.get("content").cloned();
            let ic = item.get("content").cloned();
            let lc_is_list = matches!(lc, Some(Value::Array(_)));
            let ic_is_list = matches!(ic, Some(Value::Array(_)));
            if lc_is_list || ic_is_list {
                let mut blocks = _as_content_blocks(lc.as_ref());
                blocks.extend(_as_content_blocks(ic.as_ref()));
                if !blocks.is_empty() {
                    last.insert("content".to_string(), Value::Array(blocks));
                } else {
                    last.remove("content");
                }
            } else {
                // last_str / item_str = str(content) if not None else ""
                let last_str = lc
                    .as_ref()
                    .filter(|v| !v.is_null())
                    .map(value_as_py_str)
                    .unwrap_or_default();
                let item_str = ic
                    .as_ref()
                    .filter(|v| !v.is_null())
                    .map(value_as_py_str)
                    .unwrap_or_default();
                // "\n\n".join(part for part in (last_str, item_str) if part)
                let parts: Vec<&str> = [last_str.as_str(), item_str.as_str()]
                    .into_iter()
                    .filter(|p| !p.is_empty())
                    .collect();
                let new_content = parts.join("\n\n");
                if !new_content.is_empty() {
                    last.insert("content".to_string(), json!(new_content));
                } else {
                    last.remove("content");
                }
            }
        } else {
            merged.push(item);
        }
    }

    merged.into_iter().map(Value::Object).collect()
}

/// `str(v)` for a JSON value, mirroring Python's `str()` on the values that reach
/// `_sanitize_llm_messages`: a plain string is itself; everything else its JSON
/// text (numbers/bools approximate Python's repr closely enough — these are not
/// the load-bearing path, which is strings).
fn value_as_py_str(v: &serde_json::Value) -> String {
    match v {
        serde_json::Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

/// `str(x)` for an id-like JSON value used in tool_call matching: a string is
/// itself, a number/other is its JSON text (Python `str(tc.get("id"))`).
fn json_to_str(v: &serde_json::Value) -> String {
    match v {
        serde_json::Value::String(s) => s.clone(),
        serde_json::Value::Null => "None".to_string(),
        other => other.to_string(),
    }
}

/// `_dedupe_candidates(candidates)` — filter malformed entries (`not c or not
/// c[0] or not c[1]`) and drop a later repeat of an already-seen `(url, model)`
/// route, preserving order (first occurrence wins; headers are not part of the
/// key). The chain is the primary target followed by fallbacks, so a fallback
/// that repeats the live `(url, model)` would otherwise re-attempt the just-failed
/// route and emit a spurious `fallback` notice.
pub fn _dedupe_candidates(
    candidates: Vec<(String, String, indexmap::IndexMap<String, String>)>,
) -> Vec<(String, String, indexmap::IndexMap<String, String>)> {
    let mut seen: std::collections::HashSet<(String, String)> = std::collections::HashSet::new();
    let mut out = Vec::new();
    for c in candidates {
        // if not c or not c[0] or not c[1]: continue
        if c.0.is_empty() || c.1.is_empty() {
            continue;
        }
        let key = (c.0.clone(), c.1.clone());
        if seen.contains(&key) {
            continue;
        }
        seen.insert(key);
        out.push(c);
    }
    out
}

/// Configuration defaults for LLM API calls (the Python `LLMConfig` class
/// attributes). Modeled as associated constants on a zero-sized type so call
/// sites read `LLMConfig::DEFAULT_TIMEOUT`, mirroring `LLMConfig.DEFAULT_TIMEOUT`.
pub struct LLMConfig;

impl LLMConfig {
    pub const DEFAULT_MAX_TOKENS: i64 = 0;
    pub const DEFAULT_TEMPERATURE: f64 = 1.0;
    pub const DEFAULT_TIMEOUT: i64 = 30;
    pub const DEFAULT_STREAM_TIMEOUT: i64 = 300;
    pub const DEFAULT_RETRY_COUNT: i64 = 3;
    pub const DEFAULT_RETRY_DELAY: f64 = 1.0;
    pub const REQUEST_TIMEOUT: i64 = 20;
    pub const OPENAI_COMPAT_PATH: &'static str = "/v1/chat/completions";
    /// `LLMConfig.MAX_RETRIES = 3` — the default `max_retries` for the
    /// `llm_call_async` transient-RequestError retry loop.
    pub const MAX_RETRIES: i64 = 3;
    /// `LLMConfig.RETRY_DELAY = 0.5` — seconds slept between transient retries.
    pub const RETRY_DELAY: f64 = 0.5;
    /// `LLMConfig.STREAM_TIMEOUT = 300` — the default read timeout (seconds) for
    /// `llm_call_async` / `stream_llm`.
    pub const STREAM_TIMEOUT: i64 = 300;
}

// ===========================================================================
// Network surface: the async streaming LLM client.
// ===========================================================================

/// `stream_llm` — POST a streaming chat completion and yield SSE chunks, matching
/// the Python generator's wire format:
///   * `data: {"delta": "text"}\n\n`                  — content tokens
///   * `data: {"delta": "...", "thinking": true}\n\n`  — reasoning tokens
///   * `data: {"type": "usage", "data": {...}}\n\n`    — token usage
///   * `event: error\ndata: {...}\n\n`                 — errors
///   * `data: [DONE]\n\n`                              — end of stream
///
/// SCOPE: BOTH provider branches are reproduced. The OpenAI-compatible branch
/// covers local servers, OpenAI, OpenRouter, vLLM, Ollama, … (`choices[].delta`
/// SSE). The Anthropic-native branch (`_detect_provider == "anthropic"`) targets
/// `/v1/messages` with `stream:true` and parses the Anthropic event SSE
/// (`content_block_start` / `content_block_delta` text+`input_json_delta` /
/// `message_start` / `message_delta` / `message_stop`), translating it into the
/// SAME yield vocabulary (`{"delta"}`, `{"type":"tool_calls"}`, `{"type":"usage"}`,
/// `[DONE]`) — `src/llm_core.py:664-748`. Native `tool_calls` accumulation IS
/// reproduced on both paths (the agent loop in `crate::src::agent_loop` consumes
/// the `tool_call_delta` / `tool_calls` SSE events). The system-message
/// consolidation and `max_completion_tokens` selection are reproduced.
///
/// `prompt_type` mirrors the Python keyword but is unused in the OpenAI-compatible
/// payload (Python never reads it on that branch either); it is kept for
/// signature parity so callers — `stream_llm_with_fallback`, the agent loop —
/// pass it through unchanged. `tools`, when `Some`, is added to the request
/// payload as the OpenAI `tools` array (each entry a function schema).
#[allow(clippy::too_many_arguments)]
pub fn stream_llm(
    url: String,
    model: String,
    messages: Vec<serde_json::Value>,
    temperature: f64,
    max_tokens: i64,
    headers: indexmap::IndexMap<String, String>,
    timeout: u64,
    prompt_type: Option<String>,
    tools: Option<Vec<serde_json::Value>>,
) -> impl futures_util::Stream<Item = String> {
    use futures_util::StreamExt;
    use serde_json::{json, Value};

    async_stream::stream! {
        // PROVIDER DISPATCH (Mode B — Codex API). When the target url begins with
        // `codex-responses:`, this is the ChatGPT Responses backend, NOT an
        // OpenAI-compatible chat/completions endpoint: delegate to the dedicated
        // Mode-B streaming fn (it converts messages+tools -> Responses input/
        // tools, parses the Responses SSE, and yields the SAME vocabulary this
        // fn yields) and return. Keeping this at the very top — before system
        // consolidation — makes the agent loop + chat path provider-agnostic:
        // they keep calling stream_llm; stream_llm routes. The Mode-A harness
        // (`codex:`) is dispatched in `crate::routes::chat_routes::chat_stream` and
        // never reaches here, and because
        // `"codex-responses:".starts_with("codex:") == false` the two are
        // disjoint.
        if crate::core::codex::is_codex_responses_url(&url) {
            let inner = crate::core::codex_responses::stream_codex_responses(
                url, model, messages, temperature, max_tokens, headers, timeout, prompt_type, tools,
            );
            futures_util::pin_mut!(inner);
            while let Some(chunk) = inner.next().await { yield chunk; }
            return;
        }
        let _ = prompt_type; // parity-only; unused on the OpenAI-compatible branch
        // `_thinking_model = _supports_thinking(model)` — computed once; used by the
        // OpenAI branch to repair a stray leading `</think>` on the first content token.
        let thinking_model = _supports_thinking(&model);

        // `provider = _detect_provider(url)` — routes to the native `/v1/messages`
        // (anthropic), native `/api/chat` (ollama), or OpenAI-compatible branch.
        let provider = _detect_provider(&url);

        // `messages_copy = _sanitize_llm_messages(messages)` — strip Odysseus-only
        // metadata, repair tool-call adjacency, merge consecutive user messages.
        let sanitized = _sanitize_llm_messages(&messages);

        // Consolidate multiple system messages into one at the start (some
        // models reject a system message that isn't first).
        let mut sys_parts: Vec<String> = Vec::new();
        let mut non_sys: Vec<Value> = Vec::new();
        for m in &sanitized {
            if m.get("role").and_then(|r| r.as_str()) == Some("system") {
                sys_parts.push(m.get("content").and_then(|c| c.as_str()).unwrap_or("").to_string());
            } else {
                non_sys.push(m.clone());
            }
        }
        let messages_copy: Vec<Value> = if sys_parts.is_empty() {
            non_sys
        } else {
            let mut v = vec![json!({"role": "system", "content": sys_parts.join("\n\n")})];
            v.extend(non_sys);
            v
        };

        // Per-provider target URL, request payload, and header set.
        let (target_url, payload, h): (String, Value, indexmap::IndexMap<String, String>) =
            if provider == "anthropic" {
                let tgt = _normalize_anthropic_url(&url);
                let hh = _build_anthropic_headers(&headers);
                let pl = _build_anthropic_payload(
                    &model,
                    &messages_copy,
                    temperature,
                    max_tokens,
                    true,
                    tools.as_deref(),
                );
                (tgt, pl, hh)
            } else if provider == "ollama" {
                // target_url = _normalize_ollama_url(url); h = Content-Type + headers
                let tgt = _normalize_ollama_url(&url);
                let mut hh = indexmap::IndexMap::new();
                hh.insert("Content-Type".to_string(), "application/json".to_string());
                for (k, v) in &headers {
                    hh.insert(k.clone(), v.clone());
                }
                let num_ctx = crate::src::model_context::get_context_length(&url, &model);
                let pl = _build_ollama_payload(
                    &model,
                    &messages_copy,
                    temperature,
                    max_tokens,
                    true,
                    tools.as_deref(),
                    Some(num_ctx),
                );
                (tgt, pl, hh)
            } else {
                let mut payload = json!({
                    "model": model,
                    "messages": messages_copy,
                    "temperature": temperature,
                    "stream": true,
                });
                // if _restricts_temperature(model): payload.pop("temperature", None)
                if _restricts_temperature(&model) {
                    payload.as_object_mut().unwrap().remove("temperature");
                }
                // if provider not in {"openrouter", "groq"}: stream_options.include_usage
                if provider != "openrouter" && provider != "groq" {
                    payload["stream_options"] = json!({"include_usage": true});
                }
                if max_tokens > 0 {
                    let key = if _uses_max_completion_tokens(&model) { "max_completion_tokens" } else { "max_tokens" };
                    payload[key] = json!(max_tokens);
                }
                // if tools: payload["tools"] = tools  (truthy = non-empty list)
                if let Some(t) = &tools {
                    if !t.is_empty() {
                        payload["tools"] = json!(t);
                    }
                }
                // h = _provider_headers(provider, headers)
                let hh = _provider_headers(provider, Some(&headers));
                (url.clone(), payload, hh)
            };

        // Build the reqwest HeaderMap from the chosen header set (insertion order
        // preserved; HeaderName is case-insensitive so a passthrough Content-Type
        // overrides the default, matching Python `h.update(headers)`).
        let mut hmap = reqwest::header::HeaderMap::new();
        for (k, v) in &h {
            if let (Ok(name), Ok(val)) = (
                reqwest::header::HeaderName::from_bytes(k.as_bytes()),
                reqwest::header::HeaderValue::from_str(v),
            ) {
                hmap.insert(name, val);
            }
        }

        let client = match reqwest::Client::builder()
            .connect_timeout(std::time::Duration::from_secs(3))
            .timeout(std::time::Duration::from_secs(timeout))
            .build()
        {
            Ok(c) => c,
            Err(e) => { yield error_event(&format!("client: {e}"), 502); return; }
        };

        // `if _is_host_dead(target_url): yield event: error (503 cooldown); return`
        // — fail instantly instead of waiting on the connect timeout
        // (`src/llm_core.py:659-661`). The Python uses the SHORT
        // "Upstream {host} unreachable (cooldown active)" wording on the stream
        // path (distinct from the non-streaming "marked unreachable" sentence).
        if _is_host_dead(&target_url) {
            yield format!(
                "event: error\ndata: {}\n\n",
                json!({"error": format!("Upstream {} unreachable (cooldown active)", _host_key(&target_url)), "status": 503})
            );
            return;
        }

        // note_model_activity(target_url, model) — record the live request so
        // background tasks can detect a quiet window (src/llm_core.py:662).
        note_model_activity(&target_url, &model);

        let resp = match client.post(&target_url).headers(hmap).json(&payload).send().await {
            Ok(r) => r,
            Err(e) => {
                // `except (httpx.ConnectError, httpx.ConnectTimeout)`: mark the
                // host dead, then yield `{"error": f"Cannot reach {host}",
                // "status": 503}`. Other transport errors (read-timeout /
                // network) map to 504 / 502 like the Python tail `except` arms.
                if e.is_connect() {
                    let cooled = _mark_host_dead(&target_url);
                    let tail = if cooled {
                        format!(" — host cooled for {DEAD_HOST_COOLDOWN:.0}s")
                    } else {
                        " — transient, will retry".to_string()
                    };
                    crate::pylog::warning(&format!("Stream connect to {target_url} failed: {e}{tail}"));
                    yield format!(
                        "event: error\ndata: {}\n\n",
                        json!({"error": format!("Cannot reach {}", _host_key(&target_url)), "status": 503})
                    );
                } else if e.is_timeout() {
                    // except httpx.ReadTimeout: {"error": "Read timeout", "status": 504}
                    yield error_event("Read timeout", 504);
                } else {
                    // except httpx.NetworkError: {"error": "Network error", "status": 502}
                    yield error_event("Network error", 502);
                }
                return;
            }
        };
        // `_clear_host_dead(target_url)` once the stream opens (send succeeded).
        _clear_host_dead(&target_url);
        // `if r.status_code != 200:` — the stream path keeps the 200-ONLY check
        // (NOT the 2xx range the non-streaming `is_success` uses).
        if resp.status() != reqwest::StatusCode::OK {
            let status = resp.status().as_u16();
            // raw = (await r.aread()).decode(errors="replace")
            let raw = resp.text().await.unwrap_or_default();
            // friendly = _format_upstream_error(r.status_code, raw, target_url)
            let friendly = _format_upstream_error(status, &raw, &target_url);
            yield format!(
                "event: error\ndata: {}\n\n",
                json!({"status": status, "text": friendly, "raw": raw.chars().take(500).collect::<String>()})
            );
            return;
        }

        // ── Native Ollama streaming ── (`src/llm_core.py:1119-1175`). Ollama's
        // `/api/chat` streams newline-delimited JSON objects (NOT SSE `data:`
        // lines); translate them into the SAME OpenAI yield vocabulary. The
        // shared connect/status handling above already covers the Python Ollama
        // branch's connect→503 / status≠200→error-event cases.
        if provider == "ollama" {
            // (id, name, arguments-JSON-string) accumulated across the stream.
            let mut ollama_tool_calls: Vec<Value> = Vec::new();
            let mut byte_stream = resp.bytes_stream();
            let mut buf = String::new();
            'ollama: loop {
                match byte_stream.next().await {
                    Some(Ok(chunk)) => buf.push_str(&String::from_utf8_lossy(&chunk)),
                    Some(Err(e)) => {
                        // Python `except Exception: logger.error; yield error 502`.
                        crate::pylog::error(&format!("Ollama stream error: {e}"));
                        yield format!(
                            "event: error\ndata: {}\n\n",
                            json!({"error": format!("{e}"), "status": 502})
                        );
                        return;
                    }
                    None => break 'ollama, // stream ended without `done`
                }
                while let Some(nl) = buf.find('\n') {
                    let line: String = buf[..nl].trim_end_matches('\r').to_string();
                    buf.drain(..=nl);
                    // if not line: continue
                    if line.is_empty() {
                        continue;
                    }
                    let j: Value = match serde_json::from_str(&line) {
                        Ok(v) => v,
                        Err(_) => continue, // json.JSONDecodeError -> continue
                    };
                    let message = j.get("message");
                    // thinking = message.get("thinking") or ""
                    if let Some(thinking) = message
                        .and_then(|m| m.get("thinking"))
                        .and_then(|t| t.as_str())
                        .filter(|s| !s.is_empty())
                    {
                        yield format!("data: {}\n\n", json!({"delta": thinking, "thinking": true}));
                    }
                    // content = message.get("content") or ""
                    if let Some(content) = message
                        .and_then(|m| m.get("content"))
                        .and_then(|c| c.as_str())
                        .filter(|s| !s.is_empty())
                    {
                        yield format!("data: {}\n\n", json!({"delta": content}));
                    }
                    // for tc in message.get("tool_calls") or []
                    if let Some(tcs) = message.and_then(|m| m.get("tool_calls")).and_then(|t| t.as_array()) {
                        for tc in tcs {
                            let fn_obj = tc.get("function");
                            let name = fn_obj.and_then(|f| f.get("name")).and_then(|n| n.as_str());
                            // if fn.get("name"):  (truthy = non-empty)
                            if let Some(name) = name.filter(|s| !s.is_empty()) {
                                let id = tc
                                    .get("id")
                                    .and_then(|i| i.as_str())
                                    .filter(|s| !s.is_empty())
                                    .map(|s| s.to_string())
                                    .unwrap_or_else(|| format!("call_{}", ollama_tool_calls.len()));
                                // arguments = json.dumps(fn.get("arguments") or {})
                                let args_val = fn_obj
                                    .and_then(|f| f.get("arguments"))
                                    .filter(|v| !is_falsy(v))
                                    .cloned()
                                    .unwrap_or_else(|| json!({}));
                                let args_str = serde_json::to_string(&args_val).unwrap_or_else(|_| "{}".to_string());
                                ollama_tool_calls.push(json!({
                                    "id": id,
                                    "name": name,
                                    "arguments": args_str,
                                }));
                            }
                        }
                    }
                    // if j.get("done"):
                    if j.get("done").and_then(|d| d.as_bool()).unwrap_or(false) {
                        if !ollama_tool_calls.is_empty() {
                            yield format!(
                                "data: {}\n\n",
                                json!({"type": "tool_calls", "calls": ollama_tool_calls})
                            );
                        }
                        let prompt_eval = j.get("prompt_eval_count");
                        let eval = j.get("eval_count");
                        // if prompt_eval_count is not None or eval_count is not None:
                        if (prompt_eval.is_some() && !prompt_eval.unwrap().is_null())
                            || (eval.is_some() && !eval.unwrap().is_null())
                        {
                            yield format!(
                                "data: {}\n\n",
                                json!({"type": "usage", "data": {
                                    "input_tokens": prompt_eval.and_then(|v| v.as_i64()).unwrap_or(0),
                                    "output_tokens": eval.and_then(|v| v.as_i64()).unwrap_or(0),
                                }})
                            );
                        }
                        yield "data: [DONE]\n\n".to_string();
                        return;
                    }
                }
            }
            yield "data: [DONE]\n\n".to_string();
            return;
        }

        // ── Anthropic streaming ── (`src/llm_core.py:664-748`). Parse the Anthropic
        // event SSE and re-emit the SAME vocabulary the OpenAI branch yields.
        if provider == "anthropic" {
            let mut anth_input_tokens: i64 = 0;
            let mut anth_output_tokens: i64 = 0;
            // index -> (id, name, accumulated arguments JSON). BTreeMap keeps the
            // `sorted(_anth_tool_blocks)` order for the final `tool_calls` emit.
            let mut anth_tool_blocks: std::collections::BTreeMap<i64, (String, String, String)> =
                std::collections::BTreeMap::new();
            let mut anth_block_idx: i64 = -1;

            let mut byte_stream = resp.bytes_stream();
            let mut buf = String::new();
            while let Some(chunk) = byte_stream.next().await {
                let chunk = match chunk {
                    Ok(c) => c,
                    Err(e) => { yield error_event(&format!("{e}"), 502); return; }
                };
                buf.push_str(&String::from_utf8_lossy(&chunk));
                while let Some(nl) = buf.find('\n') {
                    let line: String = buf[..nl].trim_end_matches('\r').to_string();
                    buf.drain(..=nl);
                    // SSE allows "data:value" with no space after the colon (the
                    // space is optional per the spec). Some gateways/local servers
                    // omit it; gate on "data:" then strip 5 chars (`line[5:]`).
                    let data = match line.strip_prefix("data:") {
                        Some(d) => d.trim(),
                        None => continue,
                    };
                    // `if not data or not data.startswith("{"): continue`
                    if data.is_empty() || !data.starts_with('{') {
                        continue;
                    }
                    let j: Value = match serde_json::from_str(data) {
                        Ok(v) => v,
                        Err(_) => continue, // json.JSONDecodeError -> continue
                    };
                    let evt = j.get("type").and_then(|t| t.as_str()).unwrap_or("");
                    match evt {
                        "content_block_start" => {
                            let idx = j.get("index").and_then(|x| x.as_i64()).unwrap_or(anth_block_idx + 1);
                            anth_block_idx = idx;
                            let cb = j.get("content_block");
                            let block_type = cb
                                .and_then(|c| c.get("type"))
                                .and_then(|t| t.as_str())
                                .unwrap_or("text");
                            if block_type == "tool_use" {
                                let id = cb
                                    .and_then(|c| c.get("id"))
                                    .and_then(|i| i.as_str())
                                    .map(|s| s.to_string())
                                    .unwrap_or_else(|| format!("call_{idx}"));
                                let name = cb
                                    .and_then(|c| c.get("name"))
                                    .and_then(|n| n.as_str())
                                    .unwrap_or("")
                                    .to_string();
                                anth_tool_blocks.insert(idx, (id, name, String::new()));
                            }
                        }
                        "content_block_delta" => {
                            let delta = j.get("delta");
                            let dtype = delta
                                .and_then(|d| d.get("type"))
                                .and_then(|t| t.as_str())
                                .unwrap_or("");
                            if dtype == "text_delta" {
                                let text = delta
                                    .and_then(|d| d.get("text"))
                                    .and_then(|t| t.as_str())
                                    .unwrap_or("");
                                if !text.is_empty() {
                                    yield format!("data: {}\n\n", json!({"delta": text}));
                                }
                            } else if dtype == "input_json_delta" {
                                let idx = j.get("index").and_then(|x| x.as_i64()).unwrap_or(anth_block_idx);
                                let partial = delta
                                    .and_then(|d| d.get("partial_json"))
                                    .and_then(|p| p.as_str())
                                    .unwrap_or("");
                                // Accumulate tool args; stream deltas for doc tools.
                                // (End the &mut borrow before any `yield`.)
                                let doc_tool: Option<String> = if let Some(tb) = anth_tool_blocks.get_mut(&idx) {
                                    tb.2.push_str(partial);
                                    if !partial.is_empty()
                                        && matches!(
                                            tb.1.as_str(),
                                            "create_document" | "update_document" | "edit_document"
                                        )
                                    {
                                        Some(tb.1.clone())
                                    } else {
                                        None
                                    }
                                } else {
                                    None
                                };
                                if let Some(name) = doc_tool {
                                    yield format!(
                                        "data: {}\n\n",
                                        json!({"type": "tool_call_delta", "index": idx, "name": name, "arg_delta": partial})
                                    );
                                }
                            }
                        }
                        "message_start" => {
                            let usage = j.get("message").and_then(|m| m.get("usage"));
                            anth_input_tokens = usage
                                .and_then(|u| u.get("input_tokens"))
                                .and_then(|t| t.as_i64())
                                .unwrap_or(0);
                            // Surface prompt-cache effectiveness: cache_read > 0 means
                            // the stable system+tools prefix was served from cache.
                            let c_read = usage
                                .and_then(|u| u.get("cache_read_input_tokens"))
                                .and_then(|t| t.as_i64())
                                .unwrap_or(0);
                            let c_write = usage
                                .and_then(|u| u.get("cache_creation_input_tokens"))
                                .and_then(|t| t.as_i64())
                                .unwrap_or(0);
                            if c_read != 0 || c_write != 0 {
                                crate::pylog::info(&format!(
                                    "[anthropic-cache] read={c_read} write={c_write} fresh_input={anth_input_tokens}"
                                ));
                            }
                        }
                        "message_delta" => {
                            anth_output_tokens = j
                                .get("usage")
                                .and_then(|u| u.get("output_tokens"))
                                .and_then(|t| t.as_i64())
                                .unwrap_or(0);
                        }
                        "message_stop" => {
                            // Emit accumulated tool calls in OpenAI-compatible format.
                            if !anth_tool_blocks.is_empty() {
                                let calls: Vec<Value> = anth_tool_blocks
                                    .values()
                                    .map(|(id, name, args)| json!({"id": id, "name": name, "arguments": args}))
                                    .collect();
                                yield format!("data: {}\n\n", json!({"type": "tool_calls", "calls": calls}));
                            }
                            if anth_input_tokens != 0 || anth_output_tokens != 0 {
                                yield format!(
                                    "data: {}\n\n",
                                    json!({"type": "usage", "data": {"input_tokens": anth_input_tokens, "output_tokens": anth_output_tokens}})
                                );
                            }
                            yield "data: [DONE]\n\n".to_string();
                            return;
                        }
                        "error" => {
                            let err_msg = j
                                .get("error")
                                .and_then(|e| e.get("message"))
                                .and_then(|m| m.as_str())
                                .unwrap_or("Unknown error");
                            yield format!("event: error\ndata: {}\n\n", json!({"error": err_msg, "status": 400}));
                            return;
                        }
                        _ => {}
                    }
                }
            }
            // Stream ended without an explicit `message_stop`.
            yield "data: [DONE]\n\n".to_string();
            return;
        }

        // ── OpenAI-compatible streaming ──
        // Accumulate native tool_calls across streaming chunks (Python
        // `_tc_acc: Dict[int, Dict]`, emitted as a `tool_calls` event before
        // `[DONE]`). A BTreeMap keeps the `sorted(_tc_acc)` index order. Each
        // value is (id, name, arguments, extra_content) — `extra_content` carries
        // Gemini 3's opaque thought_signature, echoed back verbatim or it 400s.
        let mut tc_acc: std::collections::BTreeMap<i64, (String, String, String, Option<Value>)> =
            std::collections::BTreeMap::new();
        // `_tc_last_idx` — most-recently-touched slot, for providers (Gemini's
        // OpenAI-compat layer) that omit `index` on parallel tool calls.
        let mut tc_last_idx: i64 = -1;
        // `_first_content_sent` — gate the one-shot `</think>` repair to the FIRST
        // non-empty content token (Python `_first_content_sent`).
        let mut first_content_sent = false;
        // `_emit_tool_calls()` — build the `tool_calls` event string, if any.
        let emit_tool_calls = |acc: &std::collections::BTreeMap<i64, (String, String, String, Option<Value>)>| -> Option<String> {
            if acc.is_empty() {
                return None;
            }
            let calls: Vec<Value> = acc
                .values()
                .map(|(id, name, args, extra)| {
                    let mut m = serde_json::Map::new();
                    m.insert("id".to_string(), json!(id));
                    m.insert("name".to_string(), json!(name));
                    m.insert("arguments".to_string(), json!(args));
                    if let Some(ec) = extra {
                        m.insert("extra_content".to_string(), ec.clone());
                    }
                    Value::Object(m)
                })
                .collect();
            Some(format!("data: {}\n\n", json!({"type": "tool_calls", "calls": calls})))
        };

        // Read the byte stream and split into SSE lines.
        let mut byte_stream = resp.bytes_stream();
        let mut buf = String::new();
        while let Some(chunk) = byte_stream.next().await {
            let chunk = match chunk {
                Ok(c) => c,
                Err(e) => { yield error_event(&format!("stream read: {e}"), 502); return; }
            };
            buf.push_str(&String::from_utf8_lossy(&chunk));
            while let Some(nl) = buf.find('\n') {
                let line: String = buf[..nl].trim_end_matches('\r').to_string();
                buf.drain(..=nl);
                // SSE allows "data:value" with no space after the colon; gating on
                // "data: " silently dropped content+usage from providers that omit
                // it. Gate on "data:" then strip 5 chars (`line[5:]`).
                let data = match line.strip_prefix("data:") {
                    Some(d) => d.trim(),
                    None => continue, // ignore blank / event: / comment lines from upstream
                };
                if data == "[DONE]" {
                    if let Some(ev) = emit_tool_calls(&tc_acc) {
                        yield ev;
                    }
                    yield "data: [DONE]\n\n".to_string();
                    return;
                }
                if data.is_empty() {
                    continue;
                }
                if !data.starts_with('{') {
                    yield format!("data: {}\n\n", json!({"delta": data}));
                    continue;
                }
                let j: Value = match serde_json::from_str(data) {
                    Ok(v) => v,
                    Err(_) => continue,
                };
                let choices = j.get("choices").and_then(|c| c.as_array());
                let delta0 = choices.and_then(|c| c.first()).and_then(|c| c.get("delta"));
                // Capture usage whenever the chunk carries it and the delta has no
                // actual output. Some gateways/local servers attach usage to the
                // FINAL delta (carrying role/finish_reason too, so it is NOT exactly
                // None/{}/{"content": None}); gating on those exact shapes discarded
                // their token counts. Check for real output instead.
                let delta_has_output = match delta0 {
                    Some(d) if d.is_object() => {
                        !is_falsy(d.get("content").unwrap_or(&Value::Null))
                            || !is_falsy(d.get("reasoning_content").unwrap_or(&Value::Null))
                            || !is_falsy(d.get("reasoning").unwrap_or(&Value::Null))
                            || !is_falsy(d.get("tool_calls").unwrap_or(&Value::Null))
                    }
                    _ => false,
                };
                if j.get("usage").is_some() && !delta_has_output {
                    let u = &j["usage"];
                    let mut usage_data = serde_json::Map::new();
                    usage_data.insert(
                        "input_tokens".to_string(),
                        json!(u.get("prompt_tokens").and_then(|x| x.as_i64()).unwrap_or(0)),
                    );
                    usage_data.insert(
                        "output_tokens".to_string(),
                        json!(u.get("completion_tokens").and_then(|x| x.as_i64()).unwrap_or(0)),
                    );
                    // llama.cpp puts a `timings` block alongside `usage` with the
                    // TRUE generation speed; pass it through so the UI shows the real
                    // gen t/s (and prefill t/s) instead of recomputing tokens/wall.
                    if let Some(tm) = j.get("timings").and_then(|t| t.as_object()) {
                        if let Some(pps) = tm.get("predicted_per_second").and_then(|v| v.as_f64()) {
                            if pps != 0.0 {
                                usage_data.insert("gen_tps".to_string(), json!(round2(pps)));
                            }
                        }
                        if let Some(pps) = tm.get("prompt_per_second").and_then(|v| v.as_f64()) {
                            if pps != 0.0 {
                                usage_data.insert("prefill_tps".to_string(), json!(round2(pps)));
                            }
                        }
                    }
                    yield format!(
                        "data: {}\n\n",
                        json!({"type": "usage", "data": Value::Object(usage_data)})
                    );
                } else if let Some(delta) = delta0.filter(|d| d.is_object()) {
                    // reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
                    // vLLM 0.20.2 / NIM emit `reasoning`; older builds `reasoning_content`.
                    let reasoning = delta
                        .get("reasoning_content")
                        .and_then(|r| r.as_str())
                        .filter(|s| !s.is_empty())
                        .or_else(|| {
                            delta.get("reasoning").and_then(|r| r.as_str()).filter(|s| !s.is_empty())
                        });
                    if let Some(r) = reasoning {
                        yield format!("data: {}\n\n", json!({"delta": r, "thinking": true}));
                    }
                    if let Some(content) = delta.get("content").and_then(|c| c.as_str()) {
                        if !content.is_empty() {
                            // Some thinking backends start content with a stray closing
                            // tag; repair ONLY that shape on the first token (do not wrap
                            // every first token — MiniMax etc. stream ordinary answers).
                            let out = if thinking_model
                                && !first_content_sent
                                && content.trim_start().to_lowercase().starts_with("</think")
                            {
                                format!("<think>{content}")
                            } else {
                                content.to_string()
                            };
                            first_content_sent = true;
                            yield format!("data: {}\n\n", json!({"delta": out}));
                        }
                    }
                    // Native tool calls — accumulate across chunks (mirrors the
                    // Python `for tc in delta.get("tool_calls") or []` loop).
                    if let Some(tcs) = delta.get("tool_calls").and_then(|t| t.as_array()) {
                        for tc in tcs {
                            let func = tc.get("function");
                            // raw_idx = tc.get("index"); if None -> Gemini parallel-call
                            // path: a function name marks a NEW call (fresh slot above
                            // any existing key); an arg-only continuation attaches to
                            // the last touched slot.
                            let raw_idx = tc.get("index").and_then(|i| i.as_i64());
                            let idx = match raw_idx {
                                Some(x) => x,
                                None => {
                                    let has_name = func
                                        .and_then(|f| f.get("name"))
                                        .and_then(|n| n.as_str())
                                        .map(|s| !s.is_empty())
                                        .unwrap_or(false);
                                    if has_name || tc_last_idx < 0 {
                                        // next free slot ABOVE any existing key
                                        tc_acc.keys().copied().max().map(|m| m + 1).unwrap_or(0)
                                    } else {
                                        tc_last_idx
                                    }
                                }
                            };
                            tc_last_idx = idx;
                            let entry = tc_acc.entry(idx).or_insert_with(|| {
                                (String::new(), String::new(), String::new(), None)
                            });
                            if let Some(id) = tc.get("id").and_then(|x| x.as_str()) {
                                if !id.is_empty() {
                                    entry.0 = id.to_string();
                                }
                            }
                            // if tc.get("extra_content"): preserve verbatim (Gemini 3
                            // thought_signature). Truthy guard mirrors Python.
                            if let Some(ec) = tc.get("extra_content") {
                                if !is_falsy(ec) {
                                    entry.3 = Some(ec.clone());
                                }
                            }
                            if let Some(name) = func.and_then(|f| f.get("name")).and_then(|n| n.as_str()) {
                                if !name.is_empty() {
                                    entry.1 = name.to_string();
                                }
                            }
                            if let Some(args) = func.and_then(|f| f.get("arguments")).and_then(|a| a.as_str()) {
                                entry.2.push_str(args);
                                // Stream tool arg deltas for doc tools.
                                if !args.is_empty()
                                    && matches!(
                                        entry.1.as_str(),
                                        "create_document" | "update_document" | "edit_document"
                                    )
                                {
                                    yield format!(
                                        "data: {}\n\n",
                                        json!({"type": "tool_call_delta", "index": idx, "name": entry.1, "arg_delta": args})
                                    );
                                }
                            }
                        }
                    }
                } else if let Some(text) = j.get("text").and_then(|t| t.as_str()) {
                    if !text.is_empty() {
                        yield format!("data: {}\n\n", json!({"delta": text}));
                    }
                }
            }
        }
        // End of stream without an explicit [DONE].
        if let Some(ev) = emit_tool_calls(&tc_acc) {
            yield ev;
        }
        yield "data: [DONE]\n\n".to_string();
    }
}

/// `round(x, 2)` — round a float to 2 decimal places for the llama.cpp `timings`
/// passthrough (`gen_tps` / `prefill_tps`). serde_json serializes the result; the
/// exact half-rounding tie-break is not observable for t/s values.
fn round2(x: f64) -> f64 {
    (x * 100.0).round() / 100.0
}

/// `event: error\ndata: {...}\n\n` helper.
fn error_event(msg: &str, status: u16) -> String {
    format!(
        "event: error\ndata: {}\n\n",
        serde_json::json!({"error": msg, "status": status})
    )
}

/// `stream_llm_with_fallback(candidates, messages, **kwargs)` — wrap `stream_llm`
/// with an ordered fallback chain.
///
/// `candidates` is a list of `(url, model, headers)`. Each is tried in order, but
/// only retried on a *pre-content* failure — an `event: error` that arrives
/// before any assistant text / tool-call data has been yielded. Once a candidate
/// has emitted real output we never switch (that would duplicate streamed
/// tokens); a later error from that candidate passes through unchanged.
///
/// Yields the same SSE chunk protocol as `stream_llm`. `temperature`,
/// `max_tokens`, `prompt_type`, `tools`, and `timeout` are the kwargs the agent
/// loop passes (the Python `**kwargs`); modeled here as explicit params.
#[allow(clippy::too_many_arguments)]
pub fn stream_llm_with_fallback(
    candidates: Vec<(String, String, indexmap::IndexMap<String, String>)>,
    messages: Vec<serde_json::Value>,
    temperature: f64,
    max_tokens: i64,
    prompt_type: Option<String>,
    tools: Option<Vec<serde_json::Value>>,
    timeout: u64,
) -> impl futures_util::Stream<Item = String> {
    use futures_util::StreamExt;
    use serde_json::json;

    async_stream::stream! {
        // cands = _dedupe_candidates(candidates) — filter malformed + drop a
        // later repeat of an already-seen (url, model) route.
        let cands = _dedupe_candidates(candidates);
        if cands.is_empty() {
            yield format!(
                "event: error\ndata: {}\n\n",
                json!({"error": "No model endpoint configured", "status": 503})
            );
            return;
        }

        // primary_model = cands[0][1] — the originally-selected model name; the
        // fallback notice tells the client which model was selected vs answered.
        let primary_model = cands[0].1.clone();
        let mut last_error: Option<String> = None;
        let n = cands.len();
        for (i, (url, model, headers)) in cands.into_iter().enumerate() {
            let is_last = i == n - 1;
            let mut emitted = false;
            let mut retried = false;
            let inner = stream_llm(
                url,
                model.clone(),
                messages.clone(),
                temperature,
                max_tokens,
                headers,
                timeout,
                prompt_type.clone(),
                tools.clone(),
            );
            futures_util::pin_mut!(inner);
            while let Some(chunk) = inner.next().await {
                if chunk.starts_with("event: error") {
                    if !emitted && !is_last {
                        // Pre-content failure with fallbacks left — swallow and
                        // move to the next candidate.
                        last_error = Some(chunk);
                        retried = true;
                        if i == 0 {
                            crate::pylog::warning(&format!(
                                "[fallback] primary {model} failed before output; trying fallback"
                            ));
                        } else {
                            crate::pylog::warning(&format!(
                                "[fallback] candidate {model} failed; trying next"
                            ));
                        }
                        break;
                    }
                    yield chunk;
                    continue;
                }
                // Any data chunk other than the terminal [DONE] means real output.
                if chunk.starts_with("data: ") && !chunk.starts_with("data: [DONE]") {
                    // First real output from a NON-primary candidate: tell the
                    // client the selected model failed and another answered.
                    // Without this the fallback is invisible (a misconfigured
                    // provider looks fine because another model silently answered).
                    if !emitted && i > 0 {
                        yield format!(
                            "data: {}\n\n",
                            json!({
                                "type": "fallback",
                                "selected_model": primary_model,
                                "answered_by": model,
                                "reason": _summarize_stream_error(last_error.as_deref()),
                            })
                        );
                    }
                    emitted = true;
                }
                yield chunk;
            }
            if !retried {
                return; // candidate finished (success, or terminal error already sent)
            }
        }
        // Every candidate failed pre-content — surface the last error.
        if let Some(err) = last_error {
            yield err;
        }
    }
}

/// `_summarize_stream_error(err_chunk)` — pull a short human reason out of an
/// `event: error` SSE chunk for the fallback notice. Returns a generic message if
/// it can't be parsed. Mirrors the Python: scan the chunk for a `data: ` line,
/// parse its JSON, build `(f"HTTP {status}: " if status else "") + str(text)`,
/// truncated to 200 chars (or the generic fallback when empty).
fn _summarize_stream_error(err_chunk: Option<&str>) -> String {
    let chunk = match err_chunk {
        Some(c) if !c.is_empty() => c,
        _ => return "primary model failed".to_string(),
    };
    for line in chunk.split('\n') {
        if let Some(payload) = line.strip_prefix("data: ") {
            if let Ok(j) = serde_json::from_str::<serde_json::Value>(payload) {
                // txt = j.get("text") or j.get("error") or ""
                let txt = j
                    .get("text")
                    .filter(|v| !is_falsy(v))
                    .or_else(|| j.get("error").filter(|v| !is_falsy(v)));
                let txt_str = match txt {
                    Some(serde_json::Value::String(s)) => s.clone(),
                    Some(other) => other.to_string(),
                    None => String::new(),
                };
                let status = j.get("status").filter(|v| !v.is_null());
                let prefix = match status {
                    Some(s) => format!("HTTP {}: ", json_to_str(s)),
                    None => String::new(),
                };
                let msg: String =
                    format!("{prefix}{txt_str}").chars().take(200).collect::<String>();
                let msg = msg.trim().to_string();
                return if msg.is_empty() {
                    "primary model failed".to_string()
                } else {
                    msg
                };
            }
            // Python only inspects the FIRST `data: ` line it finds; on JSON parse
            // failure the for-loop's enclosing try/except returns the generic msg.
            break;
        }
    }
    "primary model failed".to_string()
}

/// `llm_call_async(...)` — a non-streaming single chat completion returning the
/// full assistant text (`src/llm_core.py:507-602`). Routes BOTH the
/// OpenAI-compatible and the Anthropic-native providers by `_detect_provider(url)`,
/// exactly like the Python: an `anthropic.com` URL (the `{base}/v1/messages`
/// target `resolve_model_spec` returns for Anthropic models) takes the Anthropic
/// branch — `_normalize_anthropic_url` + `_build_anthropic_headers` (Bearer →
/// `x-api-key`) + `_build_anthropic_payload` (OpenAI→Anthropic message/content
/// conversion, including `image_url` → `image` source blocks) — and the response
/// is parsed via `_parse_anthropic_response`. Every other URL keeps the unchanged
/// OpenAI-compatible payload/headers/`choices[0].message.content` parse.
///
/// Used by the agent loop's verifier subagent, the chat sync path
/// (`routes::webhook_routes::sync_chat` / `routes::chat_routes`), and the
/// grace-synthesis fallback. Faithfully reproduces the Python control flow:
///
///   1. consolidate system messages into one at the start;
///   2. dead-host cooldown gate (`_is_host_dead`) — if cooled, raise
///      `HTTPException(503, "Upstream {host} marked unreachable (cooldown
///      active)")` WITHOUT attempting (`src/llm_core.py:559-560`);
///   3. retry loop `while attempt < max_retries`: a non-2xx response raises
///      `HTTPException(r.status_code, _format_upstream_error(...))` immediately
///      (NOT retried); a CONNECT failure marks the host dead and raises
///      `HTTPException(503, "Cannot reach {host}: {e}")` immediately (NOT
///      retried); any other transient request error sleeps `RETRY_DELAY` and
///      retries, and only after exhausting `max_retries` raises
///      `HTTPException(502, "POST {url} failed after {max_retries} attempts:
///      {e}")`; a success clears the host-dead state (`_clear_host_dead`);
///   4. `r.is_success` is the full 2xx range (`200..=299`, matching httpx) —
///      201/202/204 are SUCCESS, not errors.
///
/// Errors are returned as [`PyError::Upstream`] carrying BOTH the faithful HTTP
/// status and the friendly detail; `Display` yields just the detail so callers
/// that only log/format `{e}` are unchanged, while `sync_chat` reads the carried
/// status directly.
///
/// The in-process response cache (`_get_cache_key` / `_get_cached_response` /
/// `_set_cached_response`) IS reproduced: an identical (url, model, messages,
/// temp, max_tokens) call returns the cached response (cap 128, oldest-64
/// eviction). The Anthropic-native branch IS now reproduced. NOTE that
/// `_build_anthropic_payload` ALWAYS emits `temperature`
/// and defaults `max_tokens` to 4096 when `<= 0` — faithful to the Python
/// `_build_anthropic_payload`, and intentionally unlike the OpenAI branch which
/// omits `max_tokens` when 0. `prompt_type` / `max_retries` are taken from
/// `LLMConfig` (`max_retries = MAX_RETRIES`); callers pass neither, matching
/// their Python call sites.
#[allow(clippy::too_many_arguments)]
pub async fn llm_call_async(
    url: &str,
    model: &str,
    messages: Vec<serde_json::Value>,
    temperature: f64,
    max_tokens: i64,
    headers: indexmap::IndexMap<String, String>,
    timeout: u64,
) -> crate::error::PyResult<String> {
    use crate::error::PyError;
    use serde_json::{json, Value};

    // provider = _detect_provider(url) — computed up front (mirrors Python).
    let provider = _detect_provider(url);

    // messages_copy = _sanitize_llm_messages(messages) — strip Odysseus-only
    // metadata, repair tool-call adjacency, merge consecutive user messages.
    let sanitized = _sanitize_llm_messages(&messages);

    // Consolidate multiple system messages into one at the start.
    let mut sys_parts: Vec<String> = Vec::new();
    let mut non_sys: Vec<Value> = Vec::new();
    for m in &sanitized {
        if m.get("role").and_then(|r| r.as_str()) == Some("system") {
            sys_parts.push(m.get("content").and_then(|c| c.as_str()).unwrap_or("").to_string());
        } else {
            non_sys.push(m.clone());
        }
    }
    let messages_copy: Vec<Value> = if sys_parts.is_empty() {
        non_sys
    } else {
        let mut v = vec![json!({"role": "system", "content": sys_parts.join("\n\n")})];
        v.extend(non_sys);
        v
    };

    // Response cache (src/llm_core.py:535-539): an identical (url, model,
    // messages, temp, max_tokens) call returns the cached response. `if
    // cached_response:` is truthy → an empty cached value re-calls. (stream_llm
    // does NOT cache, matching Python.)
    let cache_key = _get_cache_key(url, model, &messages_copy, temperature, max_tokens);
    if let Some(cached) = _get_cached_response(&cache_key) {
        if !cached.is_empty() {
            crate::pylog::debug(&format!("Returning cached response for key: {cache_key}"));
            return Ok(cached);
        }
    }

    // Build (target_url, header map, payload) per provider.
    //   anthropic → _normalize_anthropic_url + _build_anthropic_headers + _build_anthropic_payload
    //   ollama    → _normalize_ollama_url + Content-Type/headers + _build_ollama_payload
    //   else      → OpenAI-compatible payload + _provider_headers + _restricts_temperature
    let target_url_owned: String;
    let target_url: &str;
    let payload: Value;
    let mut hmap = reqwest::header::HeaderMap::new();

    if provider == "anthropic" {
        target_url_owned = _normalize_anthropic_url(url);
        target_url = target_url_owned.as_str();
        // h = _build_anthropic_headers(headers) — Content-Type + anthropic-version
        // + Bearer→x-api-key, NOT the raw Bearer-passthrough loop.
        for (k, v) in _build_anthropic_headers(&headers) {
            if let (Ok(name), Ok(val)) = (
                reqwest::header::HeaderName::from_bytes(k.as_bytes()),
                reqwest::header::HeaderValue::from_str(&v),
            ) {
                hmap.insert(name, val);
            }
        }
        // payload = _build_anthropic_payload(...) — converts OpenAI image_url
        // content blocks to Anthropic image source blocks, clamps temperature to
        // [0,1], emits cached `system`, defaults max_tokens→4096 when <= 0.
        payload =
            _build_anthropic_payload(model, &messages_copy, temperature, max_tokens, false, None);
    } else if provider == "ollama" {
        // target_url = _normalize_ollama_url(url); h = Content-Type + headers
        target_url_owned = _normalize_ollama_url(url);
        target_url = target_url_owned.as_str();
        hmap.insert(
            reqwest::header::CONTENT_TYPE,
            reqwest::header::HeaderValue::from_static("application/json"),
        );
        for (k, v) in &headers {
            if let (Ok(name), Ok(val)) = (
                reqwest::header::HeaderName::from_bytes(k.as_bytes()),
                reqwest::header::HeaderValue::from_str(v),
            ) {
                hmap.insert(name, val);
            }
        }
        let num_ctx = crate::src::model_context::get_context_length(url, model);
        payload = _build_ollama_payload(
            model,
            &messages_copy,
            temperature,
            max_tokens,
            false,
            None,
            Some(num_ctx),
        );
    } else {
        // target_url = url (OpenAI-compatible branch).
        target_url = url;

        let mut p = json!({
            "model": model,
            "messages": messages_copy,
            "temperature": temperature,
        });
        // if _restricts_temperature(model): payload.pop("temperature", None)
        if _restricts_temperature(model) {
            p.as_object_mut().unwrap().remove("temperature");
        }
        if max_tokens > 0 {
            let key = if _uses_max_completion_tokens(model) {
                "max_completion_tokens"
            } else {
                "max_tokens"
            };
            p[key] = json!(max_tokens);
        }
        payload = p;

        // h = _provider_headers(provider, headers)
        for (k, v) in _provider_headers(provider, Some(&headers)) {
            if let (Ok(name), Ok(val)) = (
                reqwest::header::HeaderName::from_bytes(k.as_bytes()),
                reqwest::header::HeaderValue::from_str(&v),
            ) {
                hmap.insert(name, val);
            }
        }
    }

    // `if _is_host_dead(target_url): raise HTTPException(503, f"Upstream {host}
    // marked unreachable (cooldown active)")` — fail instantly, no attempt.
    if _is_host_dead(target_url) {
        return Err(PyError::upstream(
            503,
            format!(
                "Upstream {} marked unreachable (cooldown active)",
                _host_key(target_url)
            ),
        ));
    }

    // The Python builds a process-wide AsyncClient once; here the connect/read
    // timeouts (connect=3.0, read=timeout) live on a per-call client. Built
    // outside the loop so a builder failure (no Python analog) surfaces once.
    let client = reqwest::Client::builder()
        .connect_timeout(std::time::Duration::from_secs(3))
        .timeout(std::time::Duration::from_secs(timeout))
        .build()
        .map_err(|e| PyError::other(format!("client: {e}")))?;

    let max_retries = LLMConfig::MAX_RETRIES;
    // attempt = 0; while attempt < max_retries: attempt += 1; ...
    let mut attempt: i64 = 0;
    loop {
        attempt += 1;
        // note_model_activity(target_url, model) per attempt (src/llm_core.py:568).
        note_model_activity(target_url, model);
        match client
            .post(target_url)
            .headers(hmap.clone())
            .json(&payload)
            .send()
            .await
        {
            Ok(resp) => {
                let status = resp.status();
                // `if not r.is_success:` — httpx `is_success` is the full 2xx
                // range, so 201/202/204 are SUCCESS, not errors.
                if !(200..=299).contains(&status.as_u16()) {
                    let raw = resp.text().await.unwrap_or_default();
                    let friendly = _format_upstream_error(status.as_u16(), &raw, target_url);
                    crate::pylog::warning(&format!(
                        "LLM async call to {target_url} failed (attempt {attempt}): HTTP {} {friendly}",
                        status.as_u16()
                    ));
                    // `raise HTTPException(r.status_code, friendly)` — NOT retried
                    // (an HTTPException is not an httpx.RequestError).
                    return Err(PyError::upstream(status.as_u16(), friendly));
                }
                // `_clear_host_dead(target_url)` on success.
                _clear_host_dead(target_url);
                let data: Value = match resp.json().await {
                    Ok(d) => d,
                    // The Python `r.json()` failure is caught by the broad
                    // `except Exception` schema arm below it; mirror as the 502
                    // "Unexpected schema" raise rather than a separate 502.
                    Err(e) => {
                        return Err(PyError::upstream(
                            502,
                            format!("Unexpected schema from {target_url}: {e}"),
                        ));
                    }
                };
                // Parse per provider:
                //   anthropic → _parse_anthropic_response(data)
                //   ollama    → _parse_ollama_response(data)
                //   else      → msg = data["choices"][0]["message"];
                //               msg.get("content") or msg.get("reasoning_content") or ""
                // The Python `try` only raises 502 when the access PATH itself
                // raises (e.g. choices[] empty / message missing → KeyError) — an
                // empty parsed string is cached and returned, NOT an error.
                let response: Option<String> = if provider == "anthropic" {
                    // _parse_anthropic_response never raises; "" is a valid result.
                    Some(_parse_anthropic_response(&data))
                } else if provider == "ollama" {
                    // _parse_ollama_response never raises; "" is a valid result.
                    Some(_parse_ollama_response(&data))
                } else {
                    // msg = data["choices"][0]["message"] — None only when the path
                    // is missing (the Python KeyError → 502 branch); a present msg
                    // with empty/absent content yields "" (content or reasoning).
                    data.get("choices")
                        .and_then(|c| c.as_array())
                        .and_then(|c| c.first())
                        .and_then(|c| c.get("message"))
                        .map(|msg| {
                            let content = msg
                                .get("content")
                                .and_then(|c| c.as_str())
                                .filter(|s| !s.is_empty());
                            let reasoning = msg
                                .get("reasoning_content")
                                .and_then(|c| c.as_str())
                                .filter(|s| !s.is_empty());
                            content
                                .or(reasoning)
                                .map(|s| s.to_string())
                                .unwrap_or_default()
                        })
                };
                // `_set_cached_response(cache_key, response)` then `return response`.
                if let Some(ref r) = response {
                    _set_cached_response(&cache_key, r);
                }
                return response.ok_or_else(|| {
                    // `except Exception: raise HTTPException(502, f"Unexpected
                    // schema from {target_url}: {str(data)[:400]}")`.
                    PyError::upstream(
                        502,
                        format!(
                            "Unexpected schema from {target_url}: {}",
                            data.to_string().chars().take(400).collect::<String>()
                        ),
                    )
                });
            }
            Err(e) => {
                // httpx splits the transport error into a CONNECT family
                // (ConnectError/ConnectTimeout) and the broader RequestError.
                // reqwest's `is_connect()` is the parallel of the connect family.
                if e.is_connect() {
                    // `except (httpx.ConnectError, httpx.ConnectTimeout)`:
                    //   _cooled = _mark_host_dead(target_url)
                    //   raise HTTPException(503, f"Cannot reach {host}: {e}")
                    let cooled = _mark_host_dead(target_url);
                    let tail = if cooled {
                        format!(" — host cooled for {DEAD_HOST_COOLDOWN:.0}s")
                    } else {
                        " — transient, will retry".to_string()
                    };
                    crate::pylog::warning(&format!(
                        "LLM async connect to {target_url} failed: {e}{tail}"
                    ));
                    return Err(PyError::upstream(
                        503,
                        format!("Cannot reach {}: {e}", _host_key(target_url)),
                    ));
                }
                // `except (httpx.RequestError, httpx.HTTPStatusError)`: log, and
                // either retry (sleep RETRY_DELAY) or, once exhausted, raise 502.
                crate::pylog::warning(&format!(
                    "LLM async call attempt {attempt} failed: {e}"
                ));
                if attempt >= max_retries {
                    return Err(PyError::upstream(
                        502,
                        format!("POST {target_url} failed after {max_retries} attempts: {e}"),
                    ));
                }
                // await asyncio.sleep(LLMConfig.RETRY_DELAY)
                tokio::time::sleep(std::time::Duration::from_secs_f64(LLMConfig::RETRY_DELAY)).await;
            }
        }
    }
}

/// `llm_call_async_with_fallback(candidates, messages, **kwargs)` — async variant
/// of `llm_call_with_fallback` (`src/llm_core.py:490-505`). Try each
/// `(url, model, headers)` candidate in order, returning the first success; on
/// every failure log and continue, finally re-raising the last error (or a 503
/// when the candidate list is empty / all-`falsy`).
pub async fn llm_call_async_with_fallback(
    candidates: Vec<(String, String, indexmap::IndexMap<String, String>)>,
    messages: Vec<serde_json::Value>,
    temperature: f64,
    max_tokens: i64,
    timeout: u64,
) -> crate::error::PyResult<String> {
    use crate::error::PyError;
    // cands = _dedupe_candidates(candidates) — filter malformed + drop a later
    // repeat of an already-seen (url, model) route.
    let cands = _dedupe_candidates(candidates);
    if cands.is_empty() {
        return Err(PyError::upstream(503, "No model endpoint configured"));
    }
    let mut last_err: Option<PyError> = None;
    for (i, (url, model, headers)) in cands.into_iter().enumerate() {
        match llm_call_async(&url, &model, messages.clone(), temperature, max_tokens, headers, timeout).await {
            Ok(r) => return Ok(r),
            Err(e) => {
                let tag = if i == 0 { "primary" } else { "candidate" };
                crate::pylog::warning(&format!("[fallback] {tag} {model} failed ({e}); trying next"));
                last_err = Some(e);
            }
        }
    }
    Err(last_err.unwrap_or_else(|| PyError::upstream(503, "All fallback candidates failed")))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn response_cache_key_canonical_and_eviction() {
        use serde_json::json;
        // Distinct inputs -> distinct keys.
        let k1 = _get_cache_key("u", "m", &[json!({"role":"user","content":"a"})], 0.7, 100);
        let k2 = _get_cache_key("u", "m", &[json!({"role":"user","content":"b"})], 0.7, 100);
        assert_ne!(k1, k2);
        // Per-message key order is canonicalized (sorted items) — same key.
        let k1b = _get_cache_key("u", "m", &[json!({"content":"a","role":"user"})], 0.7, 100);
        assert_eq!(k1, k1b);
        // set/get round-trip; empty cached value is NOT returned by the caller
        // (truthy check), but the store/get itself is faithful.
        _set_cached_response(&k1, "resp-a");
        assert_eq!(_get_cached_response(&k1).as_deref(), Some("resp-a"));
        // Cap 128 / evict oldest 64: insert 130 unique keys; the earliest is gone,
        // the newest remains.
        for i in 0..130 {
            _set_cached_response(&format!("evk-{i}"), "x");
        }
        assert!(_get_cached_response("evk-0").is_none());
        assert!(_get_cached_response("evk-129").is_some());
    }

    #[test]
    fn basename_rstrip_slash_matches_os_path_basename() {
        // os.path.basename(s.rstrip("/")) — used by normalize_model_id_network's
        // basename fallback match.
        assert_eq!(basename_rstrip_slash("meta-llama/Llama-3"), "Llama-3");
        assert_eq!(basename_rstrip_slash("llama3"), "llama3");
        assert_eq!(basename_rstrip_slash("a/b/c/"), "c");
        assert_eq!(basename_rstrip_slash("Qdrant/all-MiniLM-L6-v2-onnx"), "all-MiniLM-L6-v2-onnx");
    }

    // PART A coverage: the Anthropic conversion path that `llm_call_async` invokes
    // when `_detect_provider(url) == "anthropic"`. These are the load-bearing
    // helpers the new vision wiring depends on. (`_detect_provider` keys on the
    // literal `anthropic.com` substring — IDENTICAL to Python `llm_core.py:145` —
    // NOT on `/v1/messages`, so this is the faithful detection.)
    #[test]
    fn anthropic_branch_helpers_parity() {
        use serde_json::json;

        // _parse_anthropic_response: ALL text blocks concatenate; non-text skipped;
        // "" when none. (The Messages API may split text around a tool_use block.)
        let data = json!({"content": [
            {"type": "tool_use", "id": "x"},
            {"type": "text", "text": "hello vision"},
            {"type": "text", "text": " and more"},
        ]});
        assert_eq!(_parse_anthropic_response(&data), "hello vision and more");
        assert_eq!(_parse_anthropic_response(&json!({"content": []})), "");
        assert_eq!(_parse_anthropic_response(&json!({})), "");

        // _build_anthropic_headers: Bearer -> x-api-key, always Content-Type + version.
        let mut hin: indexmap::IndexMap<String, String> = indexmap::IndexMap::new();
        hin.insert("Authorization".to_string(), "Bearer sk-abc".to_string());
        let h = _build_anthropic_headers(&hin);
        assert_eq!(h.get("x-api-key").map(String::as_str), Some("sk-abc"));
        assert_eq!(h.get("Content-Type").map(String::as_str), Some("application/json"));
        assert_eq!(h.get("anthropic-version").map(String::as_str), Some("2023-06-01"));
        assert!(h.get("Authorization").is_none());

        // _build_anthropic_payload: the SAME OpenAI-shaped vision message a vl_chat
        // caller produces is converted to the Anthropic image/source/base64 block.
        let vision_msgs = vec![json!({
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this image in detail"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}},
            ],
        })];
        // max_tokens = 0 -> defaults to 4096; temperature ALWAYS present (unlike OpenAI branch).
        let payload = _build_anthropic_payload("claude-x", &vision_msgs, 0.7, 0, false, None);
        assert_eq!(payload["model"], json!("claude-x"));
        assert_eq!(payload["max_tokens"], json!(4096));
        assert_eq!(payload["temperature"], json!(0.7));
        let content = &payload["messages"][0]["content"];
        // text block passes through; image_url -> image/source/base64 block.
        assert_eq!(content[0], json!({"type": "text", "text": "Describe this image in detail"}));
        assert_eq!(content[1]["type"], json!("image"));
        assert_eq!(content[1]["source"]["type"], json!("base64"));
        assert_eq!(content[1]["source"]["media_type"], json!("image/png"));
        assert_eq!(content[1]["source"]["data"], json!("aGVsbG8="));
        // A leading system message is hoisted into the top-level `system` field as
        // a LIST of text blocks. A short prompt with no tools is NOT cached.
        let with_sys = vec![
            json!({"role": "system", "content": "be brief"}),
            json!({"role": "user", "content": "hi"}),
        ];
        let p2 = _build_anthropic_payload("claude-x", &with_sys, 0.2, 100, false, None);
        assert_eq!(p2["system"], json!([{"type": "text", "text": "be brief"}]));
        assert_eq!(p2["max_tokens"], json!(100));

        // temperature clamp: a preset above 1.0 (OpenAI 0.0-2.0 range) is clamped
        // to 1.0 in the Anthropic builder only.
        let p3 = _build_anthropic_payload("claude-x", &with_sys, 1.2, 100, false, None);
        assert_eq!(p3["temperature"], json!(1.0));
        // tools present → both the system block AND the last tool carry a cache
        // breakpoint, even for a short system prompt.
        let tools = vec![json!({
            "type": "function",
            "function": {"name": "t", "description": "d", "parameters": {"type": "object"}}
        })];
        let p4 = _build_anthropic_payload("claude-x", &with_sys, 0.2, 100, false, Some(&tools));
        assert_eq!(p4["system"][0]["cache_control"], json!({"type": "ephemeral"}));
        assert_eq!(p4["tools"][0]["cache_control"], json!({"type": "ephemeral"}));
    }

    // `_detect_provider` is HOST-based (via `_host_match`), NOT raw-substring: the
    // old `"anthropic.com" in url` form mis-classified URLs with a provider name in
    // the PATH/query. This locks the new behavior + the security improvement. (The
    // Anthropic-native streaming branch is gated on this == "anthropic"; with
    // host-based detection a local mock can no longer present an anthropic.com host,
    // so the former end-to-end mock test is superseded by this detection lock — the
    // translation branch itself is unchanged.)
    #[test]
    fn detect_provider_is_host_based() {
        assert_eq!(_detect_provider("https://api.anthropic.com/v1/messages"), "anthropic");
        assert_eq!(_detect_provider("https://openrouter.ai/api/v1/chat/completions"), "openrouter");
        assert_eq!(_detect_provider("https://api.groq.com/openai/v1/chat/completions"), "groq");
        assert_eq!(_detect_provider("http://localhost:11434/api/chat"), "ollama");
        assert_eq!(_detect_provider("https://api.openai.com/v1/chat/completions"), "openai");
        // A provider name in the PATH (not the host) must NOT mis-detect.
        assert_eq!(_detect_provider("http://127.0.0.1:8080/anthropic.com"), "openai");
        assert_eq!(_detect_provider("http://example.com/?ref=openrouter.ai"), "openai");
        // _host_match: exact host or true subdomain, not substring.
        assert!(_host_match("https://api.anthropic.com/x", &["anthropic.com"]));
        assert!(!_host_match("https://notanthropic.com/x", &["anthropic.com"]));
    }

    #[tokio::test]
    async fn fallback_empty_or_falsy_candidates_503() {
        use crate::error::PyError;
        // No candidates -> 503 "No model endpoint configured" (before any network).
        let r = llm_call_async_with_fallback(vec![], vec![], 0.7, 100, 5).await;
        assert!(matches!(r, Err(PyError::Upstream { status: 503, .. })));
        // Candidates with empty url/model are filtered out -> same 503.
        let r2 = llm_call_async_with_fallback(
            vec![
                (String::new(), "m".to_string(), indexmap::IndexMap::new()),
                ("http://x".to_string(), String::new(), indexmap::IndexMap::new()),
            ],
            vec![],
            0.7,
            100,
            5,
        )
        .await;
        assert!(matches!(r2, Err(PyError::Upstream { status: 503, .. })));
    }

    #[test]
    fn provider_label_ladder_and_fallback() {
        // FIRST match wins, case-insensitive substring.
        assert_eq!(_provider_label("https://api.anthropic.com/v1/messages"), "Anthropic");
        assert_eq!(_provider_label("https://API.X.AI/v1/chat/completions"), "xAI");
        assert_eq!(_provider_label("https://api.openai.com/v1/chat/completions"), "OpenAI");
        assert_eq!(_provider_label("https://openrouter.ai/api/v1"), "OpenRouter");
        assert_eq!(_provider_label("https://api.groq.com/openai/v1"), "Groq");
        assert_eq!(_provider_label("https://api.deepseek.com/v1"), "DeepSeek");
        assert_eq!(_provider_label("https://generativelanguage.googleapis.com"), "Google");
        assert_eq!(_provider_label("http://localhost:1234/v1/chat/completions"), "local endpoint");
        assert_eq!(_provider_label("http://127.0.0.1:8080/v1"), "local endpoint");
        // urlparse(url).hostname fallback for an unknown host.
        assert_eq!(_provider_label("https://my-vllm.example.net/v1/chat/completions"), "my-vllm.example.net");
        // Unparseable -> "provider".
        assert_eq!(_provider_label("not a url"), "provider");
    }

    #[test]
    fn format_upstream_error_per_status_sentences() {
        // 401 — rejected the API key, with nested error.message detail. (Like the
        // Python, the detail is appended verbatim then ". Check ..." is appended,
        // so a detail that itself ends with "." yields a double period — faithful.)
        let body = r#"{"error":{"message":"User not found"}}"#;
        assert_eq!(
            _format_upstream_error(401, body, "https://api.x.ai/v1/chat/completions"),
            "xAI rejected the API key — User not found. Check Model Endpoints → xAI and re-paste the key."
        );
        // 403 — denied access (403), no detail.
        assert_eq!(
            _format_upstream_error(403, "", "https://api.openai.com/v1/chat/completions"),
            "OpenAI denied access (403). Check Model Endpoints → OpenAI and re-paste the key."
        );
        // 404 — base URL / model name, with detail in parens.
        let b404 = r#"{"error":{"message":"no such model"}}"#;
        assert_eq!(
            _format_upstream_error(404, b404, "https://openrouter.ai/api/v1"),
            "OpenRouter returned 404 — check the base URL and model name. (no such model)"
        );
        // 429 — rate-limited, detail appended after a space.
        let b429 = r#"{"error":{"message":"slow down"}}"#;
        assert_eq!(
            _format_upstream_error(429, b429, "https://api.deepseek.com/v1"),
            "DeepSeek rate-limited the request (429). slow down"
        );
        // 5xx — outage, with the actual status echoed.
        assert_eq!(
            _format_upstream_error(503, "", "https://api.openai.com/v1"),
            "OpenAI is having an outage (HTTP 503)."
        );
        // generic fallback (e.g. 418), detail after a colon, from a bare-string `error`.
        let b418 = r#"{"error":"teapot"}"#;
        assert_eq!(
            _format_upstream_error(418, b418, "https://api.openai.com/v1"),
            "OpenAI returned HTTP 418: teapot"
        );
        // Non-JSON body -> trimmed raw body becomes the detail (first 240 chars).
        assert_eq!(
            _format_upstream_error(500, "  upstream exploded  ", "https://api.openai.com/v1"),
            "OpenAI is having an outage (HTTP 500). upstream exploded"
        );
    }

    #[test]
    fn host_key_scheme_netloc_and_fallback() {
        assert_eq!(
            _host_key("https://api.deepseek.com/v1/chat/completions"),
            "https://api.deepseek.com"
        );
        assert_eq!(_host_key("http://localhost:1234/v1"), "http://localhost:1234");
        assert_eq!(
            _host_key("https://user:pw@host.example:8443/v1"),
            "https://user:pw@host.example:8443"
        );
        // Unparseable -> raw url unchanged.
        assert_eq!(_host_key("not a url"), "not a url");
    }

    #[test]
    fn dead_host_cooldown_threshold_and_clear() {
        // Use a unique host so the process-wide registry stays test-isolated.
        let url = "https://cooldown-test.invalid/v1/chat/completions";
        _clear_host_dead(url);
        assert!(!_is_host_dead(url));
        // First connect failure: below threshold (2) -> not cooled.
        assert!(!_mark_host_dead(url));
        assert!(!_is_host_dead(url));
        // Second consecutive failure -> cooled.
        assert!(_mark_host_dead(url));
        assert!(_is_host_dead(url));
        // A success resets everything.
        _clear_host_dead(url);
        assert!(!_is_host_dead(url));
        // And the failure counter is reset, so one fresh failure is below threshold again.
        assert!(!_mark_host_dead(url));
        _clear_host_dead(url);
    }

    #[test]
    fn host_match_hostname_not_substring() {
        // Exact host and subdomain match.
        assert!(_host_match("https://api.anthropic.com/v1", &["anthropic.com"]));
        assert!(_host_match("https://anthropic.com/v1", &["anthropic.com"]));
        // Trailing-dot FQDN still matches.
        assert!(_host_match("https://api.anthropic.com./v1", &["anthropic.com"]));
        // A look-alike host is NOT a match (the whole point vs substring).
        assert!(!_host_match("https://anthropic.com.example/v1", &["anthropic.com"]));
        // Domain in the PATH is not a host match.
        assert!(!_host_match("https://example.com/anthropic.com", &["anthropic.com"]));
        // Multiple domains; case-insensitive host.
        assert!(_host_match("https://API.TOGETHER.AI/v1", &["together.xyz", "together.ai"]));
        // Empty / unparseable -> false.
        assert!(!_host_match("", &["anthropic.com"]));
        assert!(!_host_match("not a url", &["anthropic.com"]));
    }

    #[test]
    fn detect_provider_hostname_based() {
        assert_eq!(_detect_provider("https://api.anthropic.com/v1/messages"), "anthropic");
        assert_eq!(_detect_provider("https://openrouter.ai/api/v1/chat/completions"), "openrouter");
        assert_eq!(_detect_provider("https://api.groq.com/openai/v1"), "groq");
        assert_eq!(_detect_provider("https://ollama.com/api/chat"), "ollama");
        assert_eq!(_detect_provider("http://localhost:11434/api/chat"), "ollama");
        // Port 11434 + /v1 path: local_ollama_host is True but the path is NOT under
        // /api, so _is_ollama_native_url is False → the OpenAI-compatible default.
        assert_eq!(_detect_provider("http://localhost:11434/v1/chat/completions"), "openai");
        assert_eq!(_detect_provider("http://localhost:8080/v1/chat/completions"), "openai");
        // Look-alike host -> openai default, NOT anthropic.
        assert_eq!(_detect_provider("https://anthropic.com.evil/v1"), "openai");
    }

    #[test]
    fn is_ollama_native_url_cases() {
        assert!(_is_ollama_native_url("https://ollama.com/api/chat"));
        assert!(_is_ollama_native_url("https://ollama.com")); // host match short-circuits
        assert!(_is_ollama_native_url("http://127.0.0.1:11434/api/chat"));
        assert!(_is_ollama_native_url("http://localhost/api"));
        // local host but non-/api path -> not native ollama.
        assert!(!_is_ollama_native_url("http://localhost:11434/v1/chat/completions"));
        // remote OpenAI-compat -> not ollama.
        assert!(!_is_ollama_native_url("https://api.openai.com/v1/chat/completions"));
    }

    #[test]
    fn ollama_url_normalization() {
        assert_eq!(_ollama_api_root("https://ollama.com/api/chat"), "https://ollama.com/api");
        assert_eq!(_ollama_api_root("http://localhost:11434/api/tags"), "http://localhost:11434/api");
        assert_eq!(_ollama_api_root("https://ollama.com"), "https://ollama.com/api");
        assert_eq!(_normalize_ollama_url("https://ollama.com"), "https://ollama.com/api/chat");
        assert_eq!(_normalize_ollama_url("http://localhost:11434/api"), "http://localhost:11434/api/chat");
    }

    #[test]
    fn restricts_temperature_reasoning_models() {
        assert!(_restricts_temperature("o1"));
        assert!(_restricts_temperature("o3-mini"));
        assert!(_restricts_temperature("gpt-5"));
        assert!(_restricts_temperature("openai/o4-mini"));
        // gpt-4.5 is NOT temperature-restricted (excluded on purpose).
        assert!(!_restricts_temperature("gpt-4.5"));
        assert!(!_restricts_temperature("gpt-4o"));
        assert!(!_restricts_temperature(""));
    }

    #[test]
    fn provider_headers_openrouter_attribution() {
        use indexmap::IndexMap;
        // OpenRouter gets attribution headers via setdefault.
        let h = _provider_headers("openrouter", None);
        assert_eq!(h.get("Content-Type").map(String::as_str), Some("application/json"));
        assert_eq!(
            h.get("HTTP-Referer").map(String::as_str),
            Some("https://github.com/pewdiepie-archdaemon/odysseus")
        );
        assert_eq!(h.get("X-OpenRouter-Title").map(String::as_str), Some("Odysseus"));
        // A caller-supplied Referer is NOT overridden (setdefault).
        let mut extra = IndexMap::new();
        extra.insert("HTTP-Referer".to_string(), "https://custom".to_string());
        let h2 = _provider_headers("openrouter", Some(&extra));
        assert_eq!(h2.get("HTTP-Referer").map(String::as_str), Some("https://custom"));
        // Non-openrouter providers get NO attribution headers.
        let h3 = _provider_headers("openai", None);
        assert!(h3.get("HTTP-Referer").is_none());
    }

    #[test]
    fn build_ollama_payload_options_and_tool_args() {
        use serde_json::json;
        // num_ctx == DEFAULT_CONTEXT is NOT emitted; a trusted smaller value is.
        let msgs = vec![json!({"role": "user", "content": "hi"})];
        let p = _build_ollama_payload(
            "llama3",
            &msgs,
            0.7,
            128,
            true,
            None,
            Some(crate::src::model_context::DEFAULT_CONTEXT),
        );
        assert_eq!(p["model"], json!("llama3"));
        assert_eq!(p["stream"], json!(true));
        assert_eq!(p["options"]["temperature"], json!(0.7));
        assert_eq!(p["options"]["num_predict"], json!(128));
        assert!(p["options"].get("num_ctx").is_none());
        let p2 = _build_ollama_payload("llama3", &msgs, 0.7, 0, false, None, Some(4096));
        assert_eq!(p2["options"]["num_ctx"], json!(4096));
        assert!(p2["options"].get("num_predict").is_none());
        // Assistant tool_calls: OpenAI string arguments become a JSON OBJECT, and
        // the per-tool-call `extra_content` (Gemini thought_signature) is dropped
        // — the new call dict carries only `function` (+ `id`).
        let tool_msgs = vec![json!({
            "role": "assistant",
            "tool_calls": [{
                "id": "c1",
                "extra_content": {"sig": "drop-me"},
                "function": {"name": "search", "arguments": "{\"q\": \"x\"}"}
            }],
        })];
        let p3 = _build_ollama_payload("llama3", &tool_msgs, 0.5, 0, false, None, None);
        let tc = &p3["messages"][0]["tool_calls"][0];
        assert_eq!(tc["function"]["arguments"], json!({"q": "x"}));
        assert_eq!(tc["function"]["name"], json!("search"));
        assert_eq!(tc["id"], json!("c1"));
        assert!(tc.get("extra_content").is_none(), "per-call extra_content dropped");
    }

    #[test]
    fn parse_ollama_response_fallbacks() {
        use serde_json::json;
        assert_eq!(_parse_ollama_response(&json!({"message": {"content": "hi"}})), "hi");
        assert_eq!(_parse_ollama_response(&json!({"response": "legacy"})), "legacy");
        // message.content empty -> falls through to response.
        assert_eq!(
            _parse_ollama_response(&json!({"message": {"content": ""}, "response": "r"})),
            "r"
        );
        assert_eq!(_parse_ollama_response(&json!({})), "");
    }

    #[test]
    fn dedupe_candidates_drops_repeats_and_malformed() {
        use indexmap::IndexMap;
        let mk = |u: &str, m: &str| (u.to_string(), m.to_string(), IndexMap::new());
        let cands = vec![
            mk("http://a", "m1"),
            mk("", "m2"),       // empty url -> dropped
            mk("http://b", ""), // empty model -> dropped
            mk("http://a", "m1"), // duplicate of first -> dropped
            mk("http://a", "m2"), // different model -> kept
        ];
        let out = _dedupe_candidates(cands);
        assert_eq!(out.len(), 2);
        assert_eq!(out[0].0, "http://a");
        assert_eq!(out[0].1, "m1");
        assert_eq!(out[1].1, "m2");
    }

    #[test]
    fn sanitize_messages_repairs_and_merges() {
        use serde_json::json;

        // 1. Whitelisting drops Odysseus-only keys; assistant tool-calls-only keeps
        //    an explicit content: null; orphan tool result is dropped.
        let msgs = vec![
            json!({"role": "system", "content": "sys", "odysseus_meta": 1}),
            json!({"role": "user", "content": "hello"}),
            json!({"role": "assistant", "tool_calls": [
                {"id": "t1", "function": {"name": "f", "arguments": "{}"}}
            ]}),
            json!({"role": "tool", "tool_call_id": "t1", "content": "result"}),
            json!({"role": "user", "content": "again"}),
        ];
        let out = _sanitize_llm_messages(&msgs);
        // system + user + assistant(tool_calls,content:null) + tool + user
        assert_eq!(out.len(), 5);
        assert!(out[0].get("odysseus_meta").is_none());
        // assistant kept its tool_calls and got content: null.
        assert_eq!(out[2]["role"], json!("assistant"));
        assert_eq!(out[2]["content"], json!(null));
        assert_eq!(out[3]["role"], json!("tool"));

        // 2. An assistant with UNANSWERED tool_calls and no content is dropped.
        let msgs2 = vec![
            json!({"role": "user", "content": "q"}),
            json!({"role": "assistant", "tool_calls": [
                {"id": "x", "function": {"name": "f", "arguments": "{}"}}
            ]}),
        ];
        let out2 = _sanitize_llm_messages(&msgs2);
        assert_eq!(out2.len(), 1);
        assert_eq!(out2[0]["role"], json!("user"));

        // 3. Orphan tool result (no preceding assistant tool_calls) is dropped.
        let msgs3 = vec![
            json!({"role": "user", "content": "q"}),
            json!({"role": "tool", "tool_call_id": "z", "content": "orphan"}),
        ];
        let out3 = _sanitize_llm_messages(&msgs3);
        assert_eq!(out3.len(), 1);
        assert_eq!(out3[0]["role"], json!("user"));

        // 4. Consecutive user messages merge (string content joined with \n\n).
        let msgs4 = vec![
            json!({"role": "user", "content": "a"}),
            json!({"role": "user", "content": "b"}),
        ];
        let out4 = _sanitize_llm_messages(&msgs4);
        assert_eq!(out4.len(), 1);
        assert_eq!(out4[0]["content"], json!("a\n\nb"));

        // 5. Multimodal merge preserves block lists (does NOT str() them away).
        let msgs5 = vec![
            json!({"role": "user", "content": [{"type": "text", "text": "see"}]}),
            json!({"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:..."}}
            ]}),
        ];
        let out5 = _sanitize_llm_messages(&msgs5);
        assert_eq!(out5.len(), 1);
        assert_eq!(out5[0]["content"].as_array().map(|a| a.len()), Some(2));
        assert_eq!(out5[0]["content"][1]["type"], json!("image_url"));
    }

    // ── Cached-first proxy endpoint refresh (upstream a2e691d) ────────────────

    #[test]
    fn model_list_base_strips_known_suffixes() {
        // Trailing model/chat suffixes are stripped down to the endpoint base.
        assert_eq!(_model_list_base("https://p.example/v1/models"), "https://p.example/v1");
        assert_eq!(
            _model_list_base("https://p.example/v1/chat/completions"),
            "https://p.example/v1"
        );
        assert_eq!(_model_list_base("https://p.example/v1/completions"), "https://p.example/v1");
        assert_eq!(
            _model_list_base("https://a.example/v1/messages"),
            "https://a.example"
        );
        // Native-Ollama `/api/<verb>` suffixes (only when preceded by `/api`).
        assert_eq!(_model_list_base("http://h:11434/api/chat"), "http://h:11434/api");
        assert_eq!(_model_list_base("http://h:11434/api/tags"), "http://h:11434/api");
        assert_eq!(_model_list_base("http://h:11434/api/generate"), "http://h:11434/api");
        // Trailing slashes are normalized; a bare base passes through.
        assert_eq!(_model_list_base("https://p.example/v1/"), "https://p.example/v1");
        assert_eq!(_model_list_base("https://p.example/v1"), "https://p.example/v1");
        // Empty / whitespace input -> "".
        assert_eq!(_model_list_base("   "), "");
        // A `/chat` NOT preceded by `/api` is left intact (no spurious strip).
        assert_eq!(_model_list_base("https://p.example/chat"), "https://p.example/chat");
    }

    #[test]
    fn parse_model_cache_dedupes_and_filters() {
        // JSON array string -> trimmed, de-duplicated, order-preserving ids.
        assert_eq!(
            _parse_model_cache(Some(r#"["a", " b ", "a", "", "c"]"#)),
            vec!["a".to_string(), "b".to_string(), "c".to_string()]
        );
        // None / empty / malformed / non-array -> [].
        assert!(_parse_model_cache(None).is_empty());
        assert!(_parse_model_cache(Some("")).is_empty());
        assert!(_parse_model_cache(Some("not json")).is_empty());
        assert!(_parse_model_cache(Some(r#"{"a": 1}"#)).is_empty());
        // Falsy elements (null/empty) are dropped.
        assert_eq!(
            _parse_model_cache(Some(r#"[null, "x", ""]"#)),
            vec!["x".to_string()]
        );
    }

    #[test]
    fn configured_cached_model_ids_cached_first_and_hidden_filter() {
        // Private, thread-local DB (no global `DATABASE_URL` swap), so this test
        // never races a sibling DB test. The cached-first read runs synchronously
        // on this thread, so it targets this DB via `db_path()`'s test routing.
        let _db = crate::core::database::test_db("llm_core_cached");

        {
            let conn = crate::core::database::session_local().unwrap();
            // Enabled endpoint with cached + one hidden model.
            conn.execute(
                "INSERT INTO model_endpoints \
                 (id, name, base_url, api_key, is_enabled, cached_models, hidden_models, model_type, created_at, updated_at) \
                 VALUES ('ep1', 'p', 'https://proxy.example/v1', NULL, 1, ?1, ?2, 'llm', '2026-01-01', '2026-01-01')",
                rusqlite::params![r#"["m-a", "m-b", "m-c"]"#, r#"["m-b"]"#],
            )
            .unwrap();
            // A disabled endpoint must never contribute.
            conn.execute(
                "INSERT INTO model_endpoints \
                 (id, name, base_url, api_key, is_enabled, cached_models, model_type, created_at, updated_at) \
                 VALUES ('ep2', 'q', 'https://other.example/v1', NULL, 0, ?1, 'llm', '2026-01-01', '2026-01-01')",
                rusqlite::params![r#"["z"]"#],
            )
            .unwrap();
        }

        // Match by normalized base: a `/chat/completions` query hits the `/v1` row,
        // returning cached models minus the hidden one, order preserved.
        assert_eq!(
            _configured_cached_model_ids("https://proxy.example/v1/chat/completions"),
            vec!["m-a".to_string(), "m-c".to_string()]
        );
        // The `/models` form normalizes to the same base.
        assert_eq!(
            _configured_cached_model_ids("https://proxy.example/v1/models"),
            vec!["m-a".to_string(), "m-c".to_string()]
        );
        // No matching enabled endpoint -> [] (the disabled ep2 base is not used).
        assert!(_configured_cached_model_ids("https://other.example/v1/chat/completions").is_empty());
        // Empty target -> [].
        assert!(_configured_cached_model_ids("   ").is_empty());

        // The network probe is cached-first: it returns the cached list WITHOUT any
        // network call (the temp host is unreachable, so a probe would yield []).
        let cached = tokio::runtime::Runtime::new()
            .unwrap()
            .block_on(list_served_model_ids("https://proxy.example/v1/chat/completions"));
        assert_eq!(cached, vec!["m-a".to_string(), "m-c".to_string()]);
    }
}
