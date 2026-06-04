// src/endpoint_resolver.rs  <- src/endpoint_resolver.py
//! Unified endpoint resolution for all backend services.
//!
//! Consolidates the 4+ copies of normalize_base / resolve_endpoint logic into
//! one place.
//!
//! SCOPE: the pure URL/header helpers and the DNS/Tailscale `resolve_url` are
//! translated faithfully. The settings + database-backed resolvers
//! (`resolve_endpoint`, `resolve_endpoint_by_id`, the fallback-candidate
//! builders) are the centralized, per-user-aware ports below — they read
//! `ModelEndpoint` rows via `session_local` and admin/per-user settings. They
//! subsume the 8 copies that the route files previously held privately; the only
//! behavior change vs. those copies is that the whitelisted setting reads now go
//! through [`crate::src::settings::get_user_setting`] with an `owner`
//! (`owner=None` preserves the old global behavior byte-for-byte).

use crate::pylog as logger;
use crate::src::llm_core::{_detect_provider, _host_match};
use indexmap::IndexMap;
use once_cell::sync::Lazy;
use serde_json::{json, Value};
use std::collections::HashSet;
use std::net::ToSocketAddrs;
use std::sync::Mutex;
use url::Url;

/// Model-name substrings that are NOT chat/generation models. When an endpoint
/// has no explicit model configured we pick the first CHAT model from its list —
/// never an embedding/tts/etc.
const _NON_CHAT_MODEL: [&str; 10] = [
    "text-embedding", "embedding", "tts-", "whisper", "dall-e",
    "moderation", "rerank", "reranker", "clip", "stable-diffusion",
];

/// First model that isn't an embedding/tts/etc.; falls back to `models[0]`.
pub fn _first_chat_model(models: &[String]) -> Option<String> {
    // for m in (models or []):
    for m in models {
        // if not any(p in str(m).lower() for p in _NON_CHAT_MODEL): return m
        let lower = m.to_lowercase();
        if !_NON_CHAT_MODEL.iter().any(|p| lower.contains(p)) {
            return Some(m.clone());
        }
    }
    // return (models[0] if models else None)
    models.first().cloned()
}

/// Parse a stored model-id list (the `cached_models`/`hidden_models` column).
/// Python stores these as a JSON-encoded string; the legacy code also tolerated
/// an already-decoded list. In Rust the columns are always TEXT, so we only have
/// the string form: parse it as a JSON array, returning `[]` on any error or a
/// non-array shape (Python's broad `except` + `isinstance(..., list)` guard).
fn _parse_model_id_list(raw: Option<&str>) -> Vec<String> {
    // raw = ... or ...; if not raw: return []
    let raw = match raw {
        Some(r) if !r.is_empty() => r,
        _ => return Vec::new(),
    };
    // models = json.loads(raw); except Exception: return []
    let parsed: Value = match serde_json::from_str(raw) {
        Ok(v) => v,
        Err(_) => return Vec::new(),
    };
    // return models if isinstance(models, list) else []
    match parsed.as_array() {
        Some(arr) => arr
            .iter()
            .filter_map(|v| v.as_str().map(|s| s.to_string()))
            .collect(),
        None => Vec::new(),
    }
}

/// `_endpoint_cached_models(ep)` — cached model ids from the current
/// (`cached_models`) or legacy (`models`) endpoint field. The Rust schema has no
/// legacy `models` column, so only `cached_models` is consulted (`getattr(ep,
/// "models", None)` is always `None`).
fn _endpoint_cached_models(ep: &Row) -> Vec<String> {
    _parse_model_id_list(ep.cached_models.as_deref())
}

/// `_endpoint_hidden_models(ep)` — model ids the admin disabled on this endpoint
/// (the UI's hidden list), as a set.
fn _endpoint_hidden_models(ep: &Row) -> HashSet<String> {
    _parse_model_id_list(ep.hidden_models.as_deref())
        .into_iter()
        .collect()
}

/// `_endpoint_enabled_models(ep)` — cached models minus the ones disabled on the
/// endpoint, order preserved. The auto-pick fallback must never select a model
/// the user disabled — a Groq endpoint can list 16 models with only 1 enabled,
/// and picking the raw first one resolves to a model that 400s ("requires terms
/// acceptance").
fn _endpoint_enabled_models(ep: &Row) -> Vec<String> {
    let hidden = _endpoint_hidden_models(ep);
    _endpoint_cached_models(ep)
        .into_iter()
        .filter(|m| !hidden.contains(m))
        .collect()
}

// Cache for Tailscale hostname → IP resolution. `None` value = "DNS works, no
// override needed" (Python stores `None` in the dict for that case).
static _TAILSCALE_CACHE: Lazy<Mutex<IndexMap<String, Option<String>>>> =
    Lazy::new(|| Mutex::new(IndexMap::new()));

/// Try to resolve a hostname via 'tailscale status' if DNS fails.
pub fn _resolve_tailscale_host(hostname: &str) -> Option<String> {
    // if hostname in _tailscale_cache: return _tailscale_cache[hostname]
    if let Some(cached) = _TAILSCALE_CACHE.lock().unwrap().get(hostname) {
        return cached.clone();
    }

    // First check if normal DNS works: socket.getaddrinfo(hostname, None, AF_INET)
    if (hostname, 0u16).to_socket_addrs().is_ok() {
        // DNS works, no override needed
        _TAILSCALE_CACHE
            .lock()
            .unwrap()
            .insert(hostname.to_string(), None);
        return None;
    }

    // DNS failed — try tailscale: subprocess.run(["tailscale","status","--json"])
    if let Ok(output) = std::process::Command::new("tailscale")
        .args(["status", "--json"])
        .output()
    {
        if output.status.success() {
            if let Ok(data) = serde_json::from_slice::<serde_json::Value>(&output.stdout) {
                let peers = data.get("Peer").and_then(|p| p.as_object());
                if let Some(peers) = peers {
                    for (_id, peer) in peers {
                        let peer_name = peer
                            .get("HostName")
                            .and_then(|v| v.as_str())
                            .unwrap_or("")
                            .to_lowercase();
                        let dns_name = peer
                            .get("DNSName")
                            .and_then(|v| v.as_str())
                            .unwrap_or("")
                            .split('.')
                            .next()
                            .unwrap_or("")
                            .to_lowercase();
                        if peer_name == hostname.to_lowercase()
                            || dns_name == hostname.to_lowercase()
                        {
                            let addrs = peer.get("TailscaleIPs").and_then(|v| v.as_array());
                            if let Some(addrs) = addrs {
                                if let Some(ip) = addrs.first().and_then(|v| v.as_str()) {
                                    logger::info(&format!(
                                        "Resolved '{hostname}' via Tailscale → {ip}"
                                    ));
                                    _TAILSCALE_CACHE
                                        .lock()
                                        .unwrap()
                                        .insert(hostname.to_string(), Some(ip.to_string()));
                                    return Some(ip.to_string());
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    _TAILSCALE_CACHE
        .lock()
        .unwrap()
        .insert(hostname.to_string(), None);
    None
}

/// If a URL's hostname can't be resolved via DNS, try Tailscale.
pub fn resolve_url(url: &str) -> String {
    // parsed = urlparse(url); hostname = parsed.hostname
    let parsed = match Url::parse(url) {
        Ok(u) => u,
        Err(_) => return url.to_string(),
    };
    let hostname = match parsed.host_str() {
        Some(h) if !h.is_empty() => h.to_string(),
        _ => return url.to_string(), // if not hostname: return url
    };
    let ip = _resolve_tailscale_host(&hostname);
    if let Some(ip) = ip {
        // Replace hostname with IP in the URL (parsed._replace(netloc=...)).
        let mut new = parsed.clone();
        if new.set_host(Some(&ip)).is_ok() {
            return new.to_string();
        }
    }
    url.to_string()
}

/// Strip known API path suffixes from a base URL.
pub fn normalize_base(url: Option<&str>) -> String {
    // url = (url or "").strip().rstrip("/")
    let mut url = url.unwrap_or("").trim().trim_end_matches('/').to_string();
    for suffix in ["/models", "/chat/completions", "/completions", "/v1/messages"] {
        if url.ends_with(suffix) {
            // url = url[: -len(suffix)].rstrip("/")
            let cut = url.len() - suffix.len();
            url = url[..cut].trim_end_matches('/').to_string();
        }
    }
    // for suffix in ["/chat", "/tags", "/generate"]:
    //     if url.endswith("/api" + suffix): url = url[: -len(suffix)].rstrip("/")
    for suffix in ["/chat", "/tags", "/generate"] {
        let api_suffix = format!("/api{suffix}");
        if url.ends_with(&api_suffix) {
            let cut = url.len() - suffix.len();
            url = url[..cut].trim_end_matches('/').to_string();
        }
    }
    url
}

/// Return Anthropic's API root, preserving /v1 for OpenAI-compatible APIs.
fn _anthropic_api_root(base: &str) -> String {
    // base = (base or "").strip().rstrip("/")
    let base = base.trim().trim_end_matches('/').to_string();
    // if _host_match(base, "anthropic.com") and base.endswith("/v1"):
    if _host_match(&base, &["anthropic.com"]) && base.ends_with("/v1") {
        // return base[:-3].rstrip("/")
        return base[..base.len() - 3].trim_end_matches('/').to_string();
    }
    base
}

/// Return the native Ollama API root, adding /api for ollama.com hosts.
///
/// NOTE: this is the LOCAL `endpoint_resolver._ollama_api_root` — a simpler
/// variant than `llm_core::_ollama_api_root`. It only handles a trailing `/api`
/// (returned unchanged) and an `ollama.com` host (reconstructs `scheme://netloc`
/// + `/api`); it does NOT strip `/chat`/`/tags`/`/generate`. Kept separate to
/// preserve Python parity (src/endpoint_resolver.py:_ollama_api_root).
fn _ollama_api_root(base: &str) -> String {
    // base = (base or "").strip().rstrip("/")
    let base = base.trim().trim_end_matches('/').to_string();
    let parsed = Url::parse(&base).ok();
    // path = (parsed.path or "").rstrip("/")
    let path = parsed
        .as_ref()
        .map(|p| p.path().trim_end_matches('/').to_string())
        .unwrap_or_default();
    if path.ends_with("/api") {
        return base;
    }
    if _host_match(&base, &["ollama.com"]) {
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
        // return root.rstrip("/") + "/api"
        return format!("{}/api", root.trim_end_matches('/'));
    }
    base
}

/// Return the correct chat endpoint URL for a given base.
pub fn build_chat_url(base: &str) -> String {
    let base = resolve_url(base);
    let provider = _detect_provider(&base);
    if provider == "anthropic" {
        format!("{}/v1/messages", _anthropic_api_root(&base))
    } else if provider == "ollama" {
        format!("{}/chat", _ollama_api_root(&base))
    } else {
        format!("{base}/chat/completions")
    }
}

/// `build_models_url(base)` — provider-specific model-list endpoint URL for a base.
pub fn build_models_url(base: &str) -> String {
    let base = resolve_url(base);
    let provider = _detect_provider(&base);
    if provider == "anthropic" {
        format!("{}/v1/models", _anthropic_api_root(&base))
    } else if provider == "ollama" {
        format!("{}/tags", _ollama_api_root(&base))
    } else {
        format!("{base}/models")
    }
}

/// Build auth headers for an endpoint.
pub fn build_headers(api_key: Option<&str>, base: &str) -> IndexMap<String, String> {
    // api_key truthiness: None or "" is falsy in Python.
    let api_key = api_key.filter(|k| !k.is_empty());
    let provider = _detect_provider(base);
    let mut headers: IndexMap<String, String> = IndexMap::new();
    if provider == "anthropic" {
        // anthropic-version is ALWAYS set, even without an api_key.
        if let Some(k) = api_key {
            headers.insert("x-api-key".to_string(), k.to_string());
        }
        headers.insert("anthropic-version".to_string(), "2023-06-01".to_string());
        return headers;
    }
    if let Some(k) = api_key {
        headers.insert("Authorization".to_string(), format!("Bearer {k}"));
    }
    if provider == "openrouter" {
        // headers.setdefault(...) — only insert if not already present.
        headers
            .entry("HTTP-Referer".to_string())
            .or_insert_with(|| "https://github.com/pewdiepie-archdaemon/odysseus".to_string());
        headers
            .entry("X-OpenRouter-Title".to_string())
            .or_insert_with(|| "Odysseus".to_string());
    }
    headers
}

// ---------------------------------------------------------------------------
// Settings + database-backed resolvers (centralized; per-user-aware).
//
// These are the ONE source for `resolve_endpoint` / `resolve_endpoint_by_id` /
// the fallback-candidate builders. They subsume the 8 copies the route files
// previously held privately. All are always compiled — the crate has no cargo
// feature flags — and every caller plus `session_local`/`secret_storage` is
// likewise always available.
// ---------------------------------------------------------------------------

/// Faithful `resolve_endpoint` return: `(Optional[str], Optional[str], Optional[Dict])`.
/// url may be Some while model is None (the compact/history call path relies on this);
/// the note/task/session/calendar/builtin/email/poller callers wrap it (see below).
pub type ResolvedEndpoint = (
    Option<String>,
    Option<String>,
    Option<indexmap::IndexMap<String, String>>,
);

/// `resolve_endpoint(setting_prefix, owner=None)` — the no-fallback form every
/// current caller uses (`fallback_url=fallback_model=fallback_headers=None`).
/// Delegates to [`resolve_endpoint_with_fallback`]; with all fallbacks `None` the
/// new `if not ep_id and fallback_url and fallback_model` short-circuit is dormant,
/// so this stays byte-for-byte the previous behavior.
pub fn resolve_endpoint(setting_prefix: &str, owner: Option<&str>) -> ResolvedEndpoint {
    resolve_endpoint_with_fallback(setting_prefix, None, None, None, owner)
}

/// `resolve_endpoint(setting_prefix, fallback_url=None, fallback_model=None,
/// fallback_headers=None, owner=None)` (src/endpoint_resolver.py:205-294).
///
/// Faithful port of the full Python signature. The whitelisted setting reads go
/// through `get_user_setting(key, owner, default)`; `owner=None` collapses to the
/// global `get_setting`. Returns the supplied fallbacks (instead of `(None, None,
/// None)`) whenever settings can't be loaded, no endpoint id resolves, or the row
/// is missing — matching `return fallback_url, fallback_model, fallback_headers`.
pub fn resolve_endpoint_with_fallback(
    setting_prefix: &str,
    fallback_url: Option<&str>,
    fallback_model: Option<&str>,
    fallback_headers: Option<&IndexMap<String, String>>,
    owner: Option<&str>,
) -> ResolvedEndpoint {
    use crate::src::settings::{get_user_setting, load_settings};
    // The fallback triple returned verbatim on the Python early-return paths.
    let fb = || {
        (
            fallback_url.map(|s| s.to_string()),
            fallback_model.map(|s| s.to_string()),
            fallback_headers.cloned(),
        )
    };
    let settings = load_settings();
    // s(key) == (get_user_setting(key, owner, settings.get(key, "")) or "").strip()
    let s = |key: &str| -> String {
        let default = settings.get(key).cloned().unwrap_or(Value::Null);
        get_user_setting(key, owner, default)
            .as_str().unwrap_or("").trim().to_string()
    };

    let mut ep_id = s(&format!("{setting_prefix}_endpoint_id"));
    let mut model = s(&format!("{setting_prefix}_model"));

    // If the specific endpoint is not configured, but the caller provided a valid
    // fallback (e.g. the active session model), use that immediately. Prevents a
    // background task from jumping to the global default_model mid-conversation.
    // `fallback_url and fallback_model` -> both Some and non-empty (Python truthy).
    if ep_id.is_empty()
        && fallback_url.is_some_and(|u| !u.is_empty())
        && fallback_model.is_some_and(|m| !m.is_empty())
    {
        return fb();
    }

    // Unset Utility means "same as Default Chat Model".
    if setting_prefix == "utility" && ep_id.is_empty() {
        ep_id = s("default_endpoint_id");
        model = s("default_model");
    }
    // Non-utility prefix with no id -> utility -> default cascade.
    if ep_id.is_empty() && setting_prefix != "utility" {
        ep_id = s("utility_endpoint_id");
        model = s("utility_model");
        if ep_id.is_empty() {
            ep_id = s("default_endpoint_id");
            model = s("default_model");
        }
    }
    if ep_id.is_empty() {
        return fb();
    }

    // ep = query(ModelEndpoint).filter(id==ep_id, is_enabled==True)[+owner_filter].first()
    let ep = match resolve_endpoint_row(&ep_id, owner) {
        Some(ep) => ep,
        None => return fb(),
    };
    let base = normalize_base(Some(&ep.base_url));
    let chat_url = build_chat_url(&base);
    let headers = build_headers(ep.api_key.as_deref(), &base);

    // Discard a configured model the user has since disabled on the endpoint
    // (stale `default_model` pointing at a now-hidden model). Treat it as unset so
    // the picker below selects a live one instead of a disabled model that 400s.
    if !model.is_empty() && _endpoint_hidden_models(&ep).contains(&model) {
        model.clear();
    }
    // If no (usable) model specified, pick the first enabled chat model.
    if model.is_empty() {
        model = _first_chat_model(&_endpoint_enabled_models(&ep)).unwrap_or_default();
    }

    // return chat_url, model or fallback_model, headers
    let model_opt = if model.is_empty() {
        fallback_model.map(|s| s.to_string())
    } else {
        Some(model)
    };
    (Some(chat_url), model_opt, Some(headers))
}

/// The collapsing convenience the note/task/session/poller/email/calendar/builtin
/// copies returned: `(url, model, headers)` only when ALL THREE are present (the
/// Python callers' `if url and model:` guard, with headers always set alongside).
pub fn resolve_endpoint_triple(setting_prefix: &str, owner: Option<&str>)
    -> Option<(String, String, indexmap::IndexMap<String, String>)>
{
    match resolve_endpoint(setting_prefix, owner) {
        (Some(url), Some(model), Some(headers)) => Some((url, model, headers)),
        _ => None,
    }
}

/// `db.query(ModelEndpoint).filter(id==ep_id, is_enabled==True)[+owner_filter].first()`
/// -> (base_url, DECRYPTED api_key). When `owner` is Some, applies
/// `owner_filter(q, ModelEndpoint, owner)` re-expressed as raw SQL
/// `(owner = ?2 OR owner IS NULL)` (the predicate SQLAlchemy emits; identical to
/// model_routes.rs::lookup_default_endpoint scoped branch). When owner is None,
/// the bare `WHERE id = ?1 AND is_enabled = 1` — byte-for-byte the 8 copies' read.
/// is_enabled = 1 is the real Python filter (endpoint_resolver.py:190) and is
/// ALWAYS applied (this fixes the calendar/builtin/email copies that wrongly used
/// the unfiltered `endpoint_auth`, which would resolve a DISABLED endpoint —
/// matching Python, which never does). api_key decrypted via secret_storage::decrypt
/// (the ORM EncryptedText read path); empty/NULL stored key -> None. Also reads
/// `cached_models` / `hidden_models` (the model-selection auto-pick + hidden-discard
/// inputs); the schema has no legacy `models` column, so `getattr(ep, "models", ...)`
/// is always None.
fn resolve_endpoint_row(ep_id: &str, owner: Option<&str>) -> Option<Row> {
    use rusqlite::OptionalExtension;
    // Python `if owner:` (endpoint_resolver.py:192) treats an empty-string owner as
    // FALSY -> the unscoped query. Normalize `Some("")` to `None` so single-user /
    // auth-disabled mode (owner == "") takes the unscoped path, matching Python and
    // the per-user-read gate in `get_user_setting`.
    let owner = owner.filter(|o| !o.is_empty());
    let conn = crate::core::database::session_local().ok()?;
    // (base_url, api_key, cached_models, hidden_models) — the endpoint row tuple.
    type EndpointRow = (String, Option<String>, Option<String>, Option<String>);
    let map_row = |r: &rusqlite::Row| -> rusqlite::Result<EndpointRow> {
        Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?))
    };
    let (base_url, enc_key, cached_models, hidden_models): EndpointRow = match owner {
        Some(owner) => conn.query_row(
            "SELECT base_url, api_key, cached_models, hidden_models FROM model_endpoints \
             WHERE id = ?1 AND is_enabled = 1 AND (owner = ?2 OR owner IS NULL)",
            rusqlite::params![ep_id, owner],
            map_row,
        ).optional().ok()??,
        None => conn.query_row(
            "SELECT base_url, api_key, cached_models, hidden_models FROM model_endpoints \
             WHERE id = ?1 AND is_enabled = 1",
            rusqlite::params![ep_id],
            map_row,
        ).optional().ok()??,
    };
    let api_key = enc_key.filter(|k| !k.is_empty())
        .map(|k| crate::src::secret_storage::decrypt(&k));
    Some(Row { base_url, api_key, cached_models, hidden_models })
}

struct Row {
    base_url: String,
    api_key: Option<String>,
    cached_models: Option<String>,
    hidden_models: Option<String>,
}

/// `resolve_endpoint_by_id(ep_id, model=None, owner=None) -> Optional[(chat_url,
/// model, headers)]` (src/endpoint_resolver.py:298-339). When `owner` is truthy
/// the query applies `owner_filter` (here the `(owner = ? OR owner IS NULL)`
/// predicate via `resolve_endpoint_row`). Returns None if ep missing/disabled OR
/// no non-empty model after the hidden-discard + enabled auto-pick. Used to turn a
/// configured fallback entry into a dispatch target.
pub fn resolve_endpoint_by_id(ep_id: &str, model: Option<&str>, owner: Option<&str>)
    -> Option<(String, String, indexmap::IndexMap<String, String>)>
{
    if ep_id.is_empty() { return None; }            // `if not ep_id: return None`
    let ep = resolve_endpoint_row(ep_id, owner)?;   // is_enabled=1 [+ owner scope]
    let base = normalize_base(Some(&ep.base_url));
    let chat_url = build_chat_url(&base);
    let headers = build_headers(ep.api_key.as_deref(), &base);
    let mut m = model.unwrap_or("").trim().to_string();
    // Drop a model the user disabled on the endpoint, then pick the first enabled
    // chat model rather than a hidden one.
    if !m.is_empty() && _endpoint_hidden_models(&ep).contains(&m) {
        m.clear();
    }
    if m.is_empty() {
        m = _first_chat_model(&_endpoint_enabled_models(&ep)).unwrap_or_default();
    }
    if m.is_empty() { return None; }                // `if not m: return None`
    Some((chat_url, m, headers))
}

/// `resolve_chat_fallback_candidates(owner=None)` —
/// `_resolve_fallback_candidates("default_model_fallbacks", owner=owner)`
/// (src/endpoint_resolver.py:339-348). Python now accepts an `owner` that is
/// threaded down to `resolve_endpoint_by_id`; every current call site passes
/// nothing (default `None`), so this keeps the zero-arg form and forwards `None`.
pub fn resolve_chat_fallback_candidates()
    -> Vec<(String, String, indexmap::IndexMap<String, String>)>
{
    resolve_fallback_candidates("default_model_fallbacks", None)
}

/// `resolve_utility_fallback_candidates(owner=None)` (src/endpoint_resolver.py:270-279).
/// When the (per-user-resolved) utility endpoint is unset, the utility chain
/// IS the default chain — so read `default_model_fallbacks` instead.
pub fn resolve_utility_fallback_candidates(owner: Option<&str>)
    -> Vec<(String, String, indexmap::IndexMap<String, String>)>
{
    use crate::src::settings::{get_user_setting, load_settings};
    let settings = load_settings();
    let util_id = get_user_setting(
        "utility_endpoint_id", owner,
        settings.get("utility_endpoint_id").cloned().unwrap_or(serde_json::Value::Null),
    ).as_str().unwrap_or("").trim().to_string();
    if util_id.is_empty() {
        return resolve_fallback_candidates("default_model_fallbacks", owner);
    }
    resolve_fallback_candidates("utility_model_fallbacks", owner)
}

/// `resolve_vision_fallback_candidates(owner=None)` —
/// `_resolve_fallback_candidates("vision_model_fallbacks", owner=owner)`
/// (src/endpoint_resolver.py:359-361). Python now accepts an `owner`; every
/// current call site passes nothing (default `None`), so this keeps the zero-arg
/// form and forwards `None`.
pub fn resolve_vision_fallback_candidates()
    -> Vec<(String, String, indexmap::IndexMap<String, String>)>
{
    resolve_fallback_candidates("vision_model_fallbacks", None)
}

/// `_resolve_fallback_candidates(setting_key, owner=None)` (src/endpoint_resolver.py:287-301).
/// Reads the chain via get_user_setting (whitelisted keys default to [] not "")
/// then maps each `{endpoint_id, model}` dict via resolve_endpoint_by_id, skipping
/// non-dict entries and any that don't resolve.
fn resolve_fallback_candidates(setting_key: &str, owner: Option<&str>)
    -> Vec<(String, String, indexmap::IndexMap<String, String>)>
{
    use crate::src::settings::{get_user_setting, load_settings};
    let settings = load_settings();
    // chain = get_user_setting(setting_key, owner, settings.get(setting_key) or []) or []
    let default = match settings.get(setting_key) {
        Some(v) if !v.is_null() => v.clone(),
        _ => json!([]),
    };
    let chain = get_user_setting(setting_key, owner, default);
    let mut out = Vec::new();
    let arr = match chain.as_array() { Some(a) => a, None => return out }; // `or []`
    for entry in arr {
        let obj = match entry.as_object() { Some(o) => o, None => continue }; // skip non-dict
        let eid = obj.get("endpoint_id").and_then(|v| v.as_str()).unwrap_or("");
        let m = obj.get("model").and_then(|v| v.as_str()).unwrap_or("");
        // resolve_endpoint_by_id(..., owner=owner) — owner now threaded through.
        if let Some(r) = resolve_endpoint_by_id(eid, Some(m), owner) {
            out.push(r);
        }
    }
    out
}
