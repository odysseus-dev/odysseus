// routes/model_routes.rs  <- routes/model_routes.py
//! Routes for model and provider management — `setup_model_routes(model_discovery)`.
//!
//! Faithful translation of `routes/model_routes.py`'s `APIRouter(prefix="/api")`
//! factory. SUPERSEDES the inline `web/mod.rs` model subset (`GET /api/models`,
//! `GET`/`POST /api/model-endpoints`); the RECONCILE step deletes those inline
//! handlers and mounts this module. The `model_discovery` argument is reached
//! through [`AppState`] (`state.model_discovery`), so the factory takes no
//! parameters (mirroring the wave-3 `setup_research_routes` shape).
//!
//! ## Port classification — PORT_PARTIAL (web + db)
//!
//! The curated-list / probe / classification helpers are ported pure-logic; the
//! `model_discovery` handler (`get_providers` / `discover_models`) is already
//! ported ([`crate::src::model_discovery::ModelDiscovery`]); the provider helpers
//! (`_detect_provider` / `_normalize_anthropic_url` / `_build_anthropic_headers` /
//! `_build_anthropic_payload` / `_uses_max_completion_tokens` / `ANTHROPIC_MODELS`)
//! were made `pub` by the PREP step and are reused as-is.
//!
//! ## `owner_filter` re-expression (the master-design note)
//! `src.auth_helpers.owner_filter` is a SQLAlchemy `query.filter(...)` builder that
//! is **un-ported** (`auth_helpers.rs::owner_filter` is `unimplemented!()` — it
//! needs a sea-orm query). The Python uses it in `/api/models` (`_fetch_models`)
//! and `/api/default-chat` to scope the visible endpoint list to "the caller's own
//! rows + null-owner (shared/legacy)". Rather than fake the ORM builder, this port
//! re-expresses that scope as raw SQL (`owner = ?1 OR owner IS NULL`), and the
//! `include_shared=False` variant (the `/default-chat` last-resort) as `owner = ?1`
//! — byte-for-byte the predicate SQLAlchemy's `owner_filter` emits.
//!
//! ## Per-user model cache + single-flight refresh (the shared-state note)
//! The Python factory closes over four module-instance dicts (`_models_cache`,
//! `_probe_failures`, `_refresh_inflight`, `_local_probe_cache`, `_providers_cache`).
//! The axum factory is called once at startup, so these become process-global
//! `Lazy<Mutex<...>>` state — one cache per running server, exactly like one
//! factory call in Python. The background refresh thread becomes a `tokio::spawn`
//! task guarded by the same coarse single-flight flag.
//!
//! ## Outbound HTTP
//! The Python uses synchronous `httpx.get/post` (and an async `httpx.AsyncClient`
//! for `probe-local`). Every handler here is already on the async runtime, so all
//! probes use async `reqwest` (the project-wide `httpx` analogue). The probe SSE
//! handlers stream `text/event-stream` via `async_stream` + `Body::from_stream`,
//! mirroring `StreamingResponse`.
//!
//! ## SUPERSEDES (the inline subset RECONCILE removes from `web/mod.rs`)
//! `GET /api/models`, `GET`/`POST /api/model-endpoints`. NOTE: the inline
//! `create_model_endpoint` calls `auto_register_codex`; `model_routes.py` does NOT
//! register Codex (it has no codex path at all), so RECONCILE must preserve the
//! `GET /api/models` + `GET /api/model-endpoints` Codex auto-registration
//! separately (the inline `list_models` / `list_model_endpoints` `auto_register_codex`
//! call) — it is NOT part of this module's behavior. See the report.

use std::collections::{HashMap, HashSet};

use axum::body::Body;
use axum::extract::{Path, Query, State};
use axum::http::{header, HeaderMap};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, patch, post};
use axum::{Extension, Json, Router};
use indexmap::IndexMap;
use once_cell::sync::Lazy;
use rusqlite::OptionalExtension;
use serde_json::{json, Map, Value};
use std::sync::Mutex;

use crate::core::database::session_local;
use crate::routes::{auth_adapter, AppState, CurrentUser, HttpException};
use crate::src::endpoint_resolver::{
    build_chat_url, build_headers, build_models_url, normalize_base, resolve_url,
};
use crate::src::llm_core::{
    _build_anthropic_headers, _build_anthropic_payload, _build_ollama_payload, _detect_provider,
    _host_match, _normalize_anthropic_url, _restricts_temperature, _uses_max_completion_tokens,
    ANTHROPIC_MODELS,
};

// ===========================================================================
// Endpoint-reference cleanup helpers (settings + per-user prefs).
// ===========================================================================

/// `_SPEECH_ENDPOINT_SETTINGS` — `(provider_key, model_key, default_model, label)`.
const SPEECH_ENDPOINT_SETTINGS: &[(&str, &str, &str, &str)] = &[
    ("tts_provider", "tts_model", "tts-1", "Text to Speech"),
    ("stt_provider", "stt_model", "base", "Speech to Text"),
];

/// `_ENDPOINT_SETTING_FIELDS` — `endpoint_key -> (model_key, label)`. Ordered to
/// match the Python dict iteration order.
const ENDPOINT_SETTING_FIELDS: &[(&str, &str, &str)] = &[
    ("default_endpoint_id", "default_model", "Default Model"),
    ("utility_endpoint_id", "utility_model", "Utility Model"),
    ("research_endpoint_id", "research_model", "Deep Research"),
    ("task_endpoint_id", "task_model", "Background Tasks"),
];

/// `_ENDPOINT_FALLBACK_FIELDS` — `fallback_key -> label`. Ordered to match Python.
const ENDPOINT_FALLBACK_FIELDS: &[(&str, &str)] = &[
    ("default_model_fallbacks", "Default Model Fallbacks"),
    ("utility_model_fallbacks", "Utility Model Fallbacks"),
    ("vision_model_fallbacks", "Vision Model Fallbacks"),
];

/// `settings.get(key) or ""` as a `&str` (only string values are truthy here).
fn settings_str<'a>(settings: &'a Map<String, Value>, key: &str) -> &'a str {
    settings.get(key).and_then(|v| v.as_str()).unwrap_or("")
}

/// `_speech_settings_using_endpoint(settings, ep_id)` — speech settings that
/// reference a model endpoint.
fn speech_settings_using_endpoint(settings: &Map<String, Value>, ep_id: &str) -> Vec<String> {
    let endpoint_ref = format!("endpoint:{ep_id}");
    SPEECH_ENDPOINT_SETTINGS
        .iter()
        .filter(|(provider_key, _, _, _)| settings_str(settings, provider_key) == endpoint_ref)
        .map(|(_, _, _, label)| (*label).to_string())
        .collect()
}

/// `_clear_speech_settings_for_endpoint(settings, ep_id)` — reset speech settings
/// that reference a model endpoint. Returns cleared labels.
fn clear_speech_settings_for_endpoint(settings: &mut Map<String, Value>, ep_id: &str) -> Vec<String> {
    let endpoint_ref = format!("endpoint:{ep_id}");
    let mut cleared = Vec::new();
    for (provider_key, model_key, default_model, label) in SPEECH_ENDPOINT_SETTINGS {
        if settings_str(settings, provider_key) == endpoint_ref {
            settings.insert((*provider_key).to_string(), json!("disabled"));
            settings.insert((*model_key).to_string(), json!(default_model));
            cleared.push((*label).to_string());
        }
    }
    cleared
}

/// `_endpoint_settings_using_endpoint(settings, ep_id, include_speech)` — labels for
/// settings and fallback chains that reference an endpoint.
fn endpoint_settings_using_endpoint(
    settings: &Map<String, Value>,
    ep_id: &str,
    include_speech: bool,
) -> Vec<String> {
    let mut affected = Vec::new();
    for (ep_key, _model_key, label) in ENDPOINT_SETTING_FIELDS {
        if settings_str(settings, ep_key) == ep_id {
            affected.push((*label).to_string());
        }
    }
    for (fallback_key, label) in ENDPOINT_FALLBACK_FIELDS {
        // chain = settings.get(fallback_key) or []
        let chain = settings.get(*fallback_key).and_then(|v| v.as_array());
        let has = chain
            .map(|arr| {
                arr.iter().any(|entry| {
                    entry
                        .as_object()
                        .map(|o| o.get("endpoint_id").and_then(|v| v.as_str()).unwrap_or("") == ep_id)
                        .unwrap_or(false)
                })
            })
            .unwrap_or(false);
        if has {
            affected.push((*label).to_string());
        }
    }
    if include_speech {
        affected.extend(speech_settings_using_endpoint(settings, ep_id));
    }
    affected
}

/// `_clear_endpoint_settings_for_endpoint(settings, ep_id, include_speech)` — remove
/// an endpoint from direct settings and model fallback chains. Returns cleared labels.
fn clear_endpoint_settings_for_endpoint(
    settings: &mut Map<String, Value>,
    ep_id: &str,
    include_speech: bool,
) -> Vec<String> {
    let mut cleared = Vec::new();
    for (ep_key, model_key, label) in ENDPOINT_SETTING_FIELDS {
        if settings_str(settings, ep_key) == ep_id {
            settings.insert((*ep_key).to_string(), json!(""));
            settings.insert((*model_key).to_string(), json!(""));
            cleared.push((*label).to_string());
        }
    }
    for (fallback_key, label) in ENDPOINT_FALLBACK_FIELDS {
        // chain = settings.get(fallback_key); if not isinstance(chain, list): continue
        let chain = match settings.get(*fallback_key).and_then(|v| v.as_array()) {
            Some(c) => c.clone(),
            None => continue,
        };
        let kept: Vec<Value> = chain
            .iter()
            .filter(|entry| {
                !entry
                    .as_object()
                    .map(|o| o.get("endpoint_id").and_then(|v| v.as_str()).unwrap_or("") == ep_id)
                    .unwrap_or(false)
            })
            .cloned()
            .collect();
        if kept.len() != chain.len() {
            settings.insert((*fallback_key).to_string(), Value::Array(kept));
            cleared.push((*label).to_string());
        }
    }
    if include_speech {
        cleared.extend(clear_speech_settings_for_endpoint(settings, ep_id));
    }
    cleared
}

/// `_clear_user_pref_endpoint_refs(all_prefs, ep_id)` — remove endpoint references
/// from scoped (`_users`) or legacy-flat user preferences. Returns the number of
/// affected user pref sets.
fn clear_user_pref_endpoint_refs(all_prefs: &mut Value, ep_id: &str) -> i64 {
    // if not isinstance(all_prefs, dict): return 0
    let root = match all_prefs.as_object_mut() {
        Some(o) => o,
        None => return 0,
    };
    let mut cleared_users: i64 = 0;
    // users = all_prefs.get("_users"); pref_sets = users.values() if dict else [all_prefs]
    let has_users = root.get("_users").map(|v| v.is_object()).unwrap_or(false);
    if has_users {
        if let Some(users) = root.get_mut("_users").and_then(|v| v.as_object_mut()) {
            for prefs in users.values_mut() {
                if let Some(p) = prefs.as_object_mut() {
                    if !clear_endpoint_settings_for_endpoint(p, ep_id, false).is_empty() {
                        cleared_users += 1;
                    }
                }
            }
        }
    } else if !clear_endpoint_settings_for_endpoint(root, ep_id, false).is_empty() {
        cleared_users += 1;
    }
    cleared_users
}

// ===========================================================================
// Docker loopback base-url rewrite.
// ===========================================================================

/// `_ANY_BIND_HOSTS` — bind-any addresses a user might type for a local server.
const ANY_BIND_HOSTS: &[&str] = &["0.0.0.0", "::"];
/// `_LOOPBACK_HOSTS` — loopback hosts plus the bind-any set.
const LOOPBACK_HOSTS: &[&str] = &["localhost", "127.0.0.1", "::1", "0.0.0.0", "::"];

/// `_docker_host_gateway_reachable()` — True when we run inside a container whose
/// host is reachable via `host.docker.internal`. False on native installs and on
/// containers without the mapping.
fn docker_host_gateway_reachable() -> bool {
    // in_container = os.path.exists("/.dockerenv")
    let mut in_container = std::path::Path::new("/.dockerenv").exists();
    if !in_container {
        // try: read /proc/1/cgroup; any of docker/containerd/kubepods
        if let Ok(text) = std::fs::read_to_string("/proc/1/cgroup") {
            in_container = ["docker", "containerd", "kubepods"]
                .iter()
                .any(|t| text.contains(t));
        } else {
            in_container = false;
        }
    }
    if !in_container {
        return false;
    }
    // socket.getaddrinfo("host.docker.internal", None) — succeed -> True
    use std::net::ToSocketAddrs;
    ("host.docker.internal", 0u16).to_socket_addrs().is_ok()
}

/// Parse a base URL's lowercased hostname and optional port (`urlparse`).
fn parsed_host_port(base_url: &str) -> (String, Option<u16>) {
    match url::Url::parse(base_url) {
        Ok(u) => (
            u.host_str().map(|s| s.to_lowercase()).unwrap_or_default(),
            u.port(),
        ),
        Err(_) => (String::new(), None),
    }
}

/// `_container_loopback_reachable(base_url, timeout=0.2)` — True when the requested
/// loopback host:port is already reachable from inside the current container.
fn container_loopback_reachable(base_url: &str) -> bool {
    let (host, port) = parsed_host_port(base_url);
    let port = match port {
        Some(p) => p,
        None => return false,
    };
    if !LOOPBACK_HOSTS.contains(&host.as_str()) {
        return false;
    }
    // probe_host = "::1" if host == "::1" else "127.0.0.1"
    let probe_host = if host == "::1" { "::1" } else { "127.0.0.1" };
    use std::net::ToSocketAddrs;
    let addr = match (probe_host, port).to_socket_addrs().ok().and_then(|mut it| it.next()) {
        Some(a) => a,
        None => return false,
    };
    std::net::TcpStream::connect_timeout(&addr, std::time::Duration::from_millis(200)).is_ok()
}

/// `urlunparse(parsed._replace(netloc=...))` for a loopback rewrite — replace only
/// the host (and keep the original port) in `base_url`.
fn replace_netloc_host(base_url: &str, new_host: &str) -> String {
    match url::Url::parse(base_url) {
        Ok(mut u) => {
            let _ = u.set_host(Some(new_host));
            u.to_string()
        }
        Err(_) => base_url.to_string(),
    }
}

/// `_rewrite_loopback_for_docker(base_url, container_local=False)` — rewrite a
/// loopback model-endpoint URL to `host.docker.internal` when running in Docker.
fn rewrite_loopback_for_docker(base_url: &str, container_local: bool) -> String {
    let (host, _port) = parsed_host_port(base_url);
    if host.is_empty() && url::Url::parse(base_url).is_err() {
        // except Exception: return base_url
        return base_url.to_string();
    }
    if !LOOPBACK_HOSTS.contains(&host.as_str()) {
        return base_url.to_string();
    }
    if container_local {
        if ANY_BIND_HOSTS.contains(&host.as_str()) {
            return replace_netloc_host(base_url, "127.0.0.1");
        }
        return base_url.to_string();
    }
    if ANY_BIND_HOSTS.contains(&host.as_str()) && !docker_host_gateway_reachable() {
        return replace_netloc_host(base_url, "127.0.0.1");
    }
    if container_loopback_reachable(base_url) {
        return base_url.to_string();
    }
    if !docker_host_gateway_reachable() {
        return base_url.to_string();
    }
    replace_netloc_host(base_url, "host.docker.internal")
}

// ===========================================================================
// Model-ID normalization / merge / visibility helpers.
// ===========================================================================

/// `_normalize_model_ids(value)` — coerce a model-ID input (list, JSON-encoded list
/// string, or comma/newline-separated string) into a clean, ordered, de-duplicated
/// list of strings.
fn normalize_model_ids(value: &Value) -> Vec<String> {
    // if value is None: return []
    if value.is_null() {
        return Vec::new();
    }
    // items = value; if isinstance(value, str): ...
    let items: Vec<Value> = match value {
        Value::String(s) => {
            let text = s.trim();
            if text.is_empty() {
                return Vec::new();
            }
            // try: parsed = json.loads(text) except: parsed = None
            let parsed: Option<Value> = serde_json::from_str(text).ok();
            match parsed {
                // items = parsed if isinstance(parsed, list) else re.split(...)
                Some(Value::Array(arr)) => arr,
                _ => text
                    .split(['\n', ','])
                    .map(|p| Value::String(p.to_string()))
                    .collect(),
            }
        }
        Value::Array(arr) => arr.clone(),
        // if not isinstance(items, list): return []
        _ => return Vec::new(),
    };
    let mut out: Vec<String> = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();
    for item in &items {
        // if not isinstance(item, str): continue
        let s = match item.as_str() {
            Some(s) => s.trim(),
            None => continue,
        };
        if s.is_empty() || seen.contains(s) {
            continue;
        }
        seen.insert(s.to_string());
        out.push(s.to_string());
    }
    out
}

/// `_normalize_model_ids` over a raw `Option<&str>` (a DB column / form string). A
/// `None`/missing value behaves like Python `None`.
fn normalize_model_ids_opt(value: Option<&str>) -> Vec<String> {
    match value {
        Some(s) => normalize_model_ids(&Value::String(s.to_string())),
        None => Vec::new(),
    }
}

/// `_merge_model_ids(*lists)` — concatenate model-ID lists, de-duplicating and
/// preserving order.
fn merge_model_ids(lists: &[&[String]]) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();
    for ids in lists {
        for m in ids.iter() {
            if seen.contains(m) {
                continue;
            }
            seen.insert(m.clone());
            out.push(m.clone());
        }
    }
    out
}

/// `_visible_models(cached_models, hidden_models, pinned_models=None)` — merge
/// cached + pinned model IDs, then filter out hidden ones.
fn visible_models(
    cached_models: Option<&str>,
    hidden_models: Option<&str>,
    pinned_models: Option<&str>,
) -> Vec<String> {
    let cached = normalize_model_ids_opt(cached_models);
    let pinned = normalize_model_ids_opt(pinned_models);
    let merged = merge_model_ids(&[&cached, &pinned]);
    // if not hidden_models: return merged
    let hidden_raw = hidden_models.unwrap_or("");
    if hidden_raw.is_empty() {
        return merged;
    }
    let hidden: HashSet<String> = normalize_model_ids_opt(hidden_models).into_iter().collect();
    merged.into_iter().filter(|m| !hidden.contains(m)).collect()
}

// ===========================================================================
// ── Curated model lists per provider ──
// For cloud providers that return 100+ models, only show these by default.
// A model ID matches if it starts with or equals a curated entry.
// ===========================================================================

/// `_PROVIDER_CURATED` — the per-provider curated model lists. Built once as an
/// ordered `IndexMap` so the priority order in each list is preserved (the
/// curated sort below relies on the index of the matching entry).
static PROVIDER_CURATED: Lazy<IndexMap<&'static str, Vec<&'static str>>> = Lazy::new(|| {
    let mut m: IndexMap<&'static str, Vec<&'static str>> = IndexMap::new();
    m.insert(
        "openai",
        vec![
            "gpt-5.2", "gpt-5.2-pro", "gpt-5", "gpt-5-pro", "gpt-5-mini", "gpt-5-nano",
            "gpt-4o", "gpt-4o-mini", "o3", "o4-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
            "gpt-image-1.5", "gpt-image-1", "dall-e-3", "tts-1", "whisper-1",
        ],
    );
    m.insert(
        "anthropic",
        vec![
            "claude-sonnet-4", "claude-opus-4", "claude-haiku-4",
            "claude-sonnet-4-5", "claude-haiku-3-5",
        ],
    );
    m.insert(
        "zai",
        vec![
            "glm-5", "glm-5.1", "glm-5v-turbo", "glm-4.7", "glm-4.7-flash",
            "glm-4.6", "glm-4.6v",
            "glm-4.5", "glm-4.5v", "glm-4.5-air", "glm-4.5-flash",
        ],
    );
    m.insert(
        "zai-coding",
        vec![
            "glm-5.1", "glm-5v-turbo", "glm-5-turbo", "glm-4.7", "glm-4.5-air",
        ],
    );
    m.insert("deepseek", vec!["deepseek-chat", "deepseek-reasoner"]);
    m.insert(
        "groq",
        vec![
            "openai/gpt-oss-120b", "openai/gpt-oss-20b",
            "groq/compound", "groq/compound-mini",
            "llama-3.1-8b-instant",
            "llama-3.3-70b-versatile",
            "llama-4-scout-17b-16e-instruct",
            "llama-4-maverick-17b-128e-instruct",
        ],
    );
    m.insert(
        "mistral",
        vec![
            "mistral-large-latest", "mistral-medium-latest", "mistral-small-latest",
        ],
    );
    m.insert(
        "together",
        vec![
            "meta-llama/Llama-4-Scout-17B-16E-Instruct",
            "meta-llama/Llama-4-Maverick-17B-128E-Instruct",
            "deepseek-ai/DeepSeek-R1",
            "Qwen/Qwen2.5-72B-Instruct-Turbo",
        ],
    );
    m.insert(
        "fireworks",
        vec![
            "accounts/fireworks/models/llama4-scout-instruct-basic",
            "accounts/fireworks/models/llama4-maverick-instruct-basic",
            "accounts/fireworks/models/deepseek-r1",
        ],
    );
    m.insert(
        "google",
        vec![
            "gemini-3.5", "gemini-3.1", "gemini-3",
            "gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash",
        ],
    );
    m.insert(
        "xai",
        vec![
            "grok-4.3", "grok-4", "grok-4-fast", "grok-3", "grok-3-fast",
        ],
    );
    m
});

/// `_HOST_TO_CURATED` — map hostnames → curated-list keys for providers whose
/// `_detect_provider()` returns a generic value (e.g. "openai") but deserve their
/// own curated list. "openrouter" is a sentinel meaning "no curation — show all
/// models as curated". Entries are matched by hostname equality or subdomain suffix
/// (via `_host_match`), so e.g. "deepseek.com" covers api.deepseek.com without
/// matching the substring inside an unrelated URL. Ordered to match the Python tuple.
const HOST_TO_CURATED: &[(&str, &str)] = &[
    ("z.ai", "zai"),
    ("deepseek.com", "deepseek"),
    ("groq.com", "groq"),
    ("mistral.ai", "mistral"),
    ("together.xyz", "together"),
    ("together.ai", "together"),
    ("fireworks.ai", "fireworks"),
    ("googleapis.com", "google"),
    ("x.ai", "xai"),
    ("openrouter.ai", "openrouter"),
    ("ollama.com", "ollama"),
];

/// `_match_provider_curated(base_url, provider)` — return the curated-list key for
/// a given endpoint.
///
/// Checks path-based overrides first (for hosts serving multiple plans), then
/// matches the base URL's hostname against known providers, and finally falls back
/// to the raw provider string from `_detect_provider()`. `provider == None` (the
/// Python call sites that pass `None`) is modeled as `None` here.
fn match_provider_curated(base_url: &str, provider: Option<&str>) -> Option<String> {
    // Path-based overrides for hosts that serve multiple curated lists.
    let path = url::Url::parse(base_url)
        .ok()
        .map(|u| u.path().to_string())
        .unwrap_or_default();
    if _host_match(base_url, &["z.ai"]) && path.contains("/api/coding") {
        return Some("zai-coding".to_string());
    }
    for (domain, key) in HOST_TO_CURATED {
        if _host_match(base_url, &[domain]) {
            return Some((*key).to_string());
        }
    }
    // return provider
    provider.map(str::to_string)
}

/// `_best_match_idx(mid, curated_list)` — index of the longest matching curated
/// entry, or -1.
fn best_match_idx(mid: &str, curated_list: &[&str]) -> i64 {
    let mut best_i: i64 = -1;
    let mut best_len: usize = 0;
    for (i, entry) in curated_list.iter().enumerate() {
        if (mid == *entry || mid.starts_with(entry)) && entry.len() > best_len {
            best_i = i as i64;
            best_len = entry.len();
        }
    }
    best_i
}

/// `_curate_models(model_ids, provider)` — partition `model_ids` into
/// `(curated, extra)` based on the provider's curated list. If no curated list
/// exists for the provider, returns `(model_ids, [])`.
fn curate_models(model_ids: &[String], provider: Option<&str>) -> (Vec<String>, Vec<String>) {
    // if provider == "openrouter": return model_ids, []
    if provider == Some("openrouter") {
        return (model_ids.to_vec(), Vec::new());
    }
    // curated_list = _PROVIDER_CURATED.get(provider)
    let curated_list = match provider.and_then(|p| PROVIDER_CURATED.get(p)) {
        Some(c) => c,
        // if not curated_list: return model_ids, []
        None => return (model_ids.to_vec(), Vec::new()),
    };
    let mut curated: Vec<String> = Vec::new();
    let mut extra: Vec<String> = Vec::new();
    for mid in model_ids {
        if best_match_idx(mid, curated_list) >= 0 {
            curated.push(mid.clone());
        } else {
            extra.push(mid.clone());
        }
    }
    // curated.sort(key=lambda mid: (_best_match_idx(mid), mid))
    curated.sort_by(|a, b| {
        let ka = best_match_idx(a, curated_list);
        let kb = best_match_idx(b, curated_list);
        ka.cmp(&kb).then_with(|| a.cmp(b))
    });
    (curated, extra)
}

/// `_truthy(value)` — `(value or "").strip().lower() in ("true", "1", "yes", "on")`.
fn truthy(value: Option<&str>) -> bool {
    matches!(
        value.unwrap_or("").trim().to_lowercase().as_str(),
        "true" | "1" | "yes" | "on"
    )
}

// ===========================================================================
// Proxy/API endpoint classification + refresh policy (a2e691d).
//
// Large OpenAI-compatible proxy endpoints expose hundreds of models and make
// `/v1/models` slow. These helpers add an explicit kind ("local"/"api"/"proxy")
// and refresh-mode ("auto"/"manual"/"disabled") so proxies are cached-first,
// excluded from aggressive local probing, and keep their cached list on failure.
// ===========================================================================

/// `_ENDPOINT_KINDS`.
const ENDPOINT_KINDS: &[&str] = &["auto", "local", "api", "proxy"];

/// `_normalize_endpoint_kind(value)` — coerce a kind string into the known set,
/// defaulting to "auto".
fn normalize_endpoint_kind(value: Option<&str>) -> String {
    // kind = str(value or "auto").strip().lower()
    let raw = value.map(|s| s.trim()).unwrap_or("");
    let kind = if raw.is_empty() { "auto".to_string() } else { raw.to_lowercase() };
    if ENDPOINT_KINDS.contains(&kind.as_str()) {
        kind
    } else {
        "auto".to_string()
    }
}

/// `_normalize_refresh_mode(value, endpoint_kind="auto")` — coerce a refresh-mode
/// string. Proxies default to "manual" cached-first; others to "auto".
fn normalize_refresh_mode(value: Option<&str>, endpoint_kind: &str) -> String {
    // mode = str(value or "").strip().lower()
    let mode = value.map(|s| s.trim().to_lowercase()).unwrap_or_default();
    let kind = normalize_endpoint_kind(Some(endpoint_kind));
    if mode == "manual" || mode == "disabled" {
        return mode;
    }
    if mode == "auto" && kind != "proxy" {
        return "auto".to_string();
    }
    // Proxies default to manual cached-first behavior. Normal local/API
    // endpoints keep automatic bounded refreshes.
    if kind == "proxy" {
        "manual".to_string()
    } else {
        "auto".to_string()
    }
}

/// `_endpoint_kind(ep)` — the endpoint's stored kind, normalized.
fn endpoint_kind_of(ep: &Endpoint) -> String {
    normalize_endpoint_kind(ep.endpoint_kind.as_deref())
}

/// `_endpoint_refresh_mode(ep, endpoint_kind=None)` — the endpoint's stored refresh
/// mode, normalized against the (effective or stored) kind.
fn endpoint_refresh_mode(ep: &Endpoint, endpoint_kind: Option<&str>) -> String {
    let kind = match endpoint_kind {
        Some(k) => k.to_string(),
        None => endpoint_kind_of(ep),
    };
    normalize_refresh_mode(ep.model_refresh_mode.as_deref(), &kind)
}

/// `_endpoint_refresh_interval(ep, category)` — background refresh interval (sec):
/// stored value (>=30) or the category default (60 local / 3600 api).
fn endpoint_refresh_interval(ep: &Endpoint, category: &str) -> f64 {
    let val = ep.model_refresh_interval.unwrap_or(0);
    if val > 0 {
        return std::cmp::max(30, val) as f64;
    }
    if category == "local" {
        60.0
    } else {
        3600.0
    }
}

/// `_endpoint_refresh_timeout(ep, category)` — background probe timeout (sec):
/// stored value clamped to [1, 30] or the category default (2.5 local / 2.0 api).
fn endpoint_refresh_timeout(ep: &Endpoint, category: &str) -> f64 {
    let val = ep.model_refresh_timeout.unwrap_or(0);
    if val > 0 {
        return val.clamp(1, 30) as f64;
    }
    if category == "local" {
        2.5
    } else {
        2.0
    }
}

/// `_manual_refresh_timeout(ep, category, requested=None)` — timeout for explicit
/// user-triggered model-list refreshes. A large proxy may legitimately need
/// 15-30s; background refreshes stay short.
fn manual_refresh_timeout(ep: &Endpoint, category: &str, requested: Option<i64>) -> f64 {
    if let Some(v) = parse_positive_int_from_opt(requested, 1, 60) {
        return v as f64;
    }
    let stored = parse_positive_int_from_opt(ep.model_refresh_timeout, 1, 60);
    if category == "local" {
        match stored {
            Some(v) => v as f64,
            None => endpoint_refresh_timeout(ep, category),
        }
    } else {
        // float(max(stored or 30, 30))
        std::cmp::max(stored.unwrap_or(30), 30) as f64
    }
}

/// `_parse_positive_int(raw, minimum, maximum)` over a raw string. Returns `None`
/// when unparseable or below `minimum`; clamps to `maximum`.
fn parse_positive_int(raw: Option<&str>, minimum: i64, maximum: i64) -> Option<i64> {
    let s = raw?.trim();
    let val: i64 = s.parse().ok()?;
    if val < minimum {
        return None;
    }
    Some(std::cmp::min(val, maximum))
}

/// `_parse_positive_int` over an already-numeric value (a DB column / query int).
/// `int(str(raw).strip())` round-trips an integer unchanged, so this applies the
/// same `minimum`/`maximum` gate directly.
fn parse_positive_int_from_opt(raw: Option<i64>, minimum: i64, maximum: i64) -> Option<i64> {
    let val = raw?;
    if val < minimum {
        return None;
    }
    Some(std::cmp::min(val, maximum))
}

/// `_explicit_model_list_timeout(base_url, endpoint_kind="auto", requested=None)` —
/// timeout for explicit user-triggered model-list fetches during setup. proxy/API
/// endpoints get the long 30s budget; locals stay tight.
fn explicit_model_list_timeout(base_url: &str, endpoint_kind: &str, requested: Option<i64>) -> f64 {
    if let Some(v) = parse_positive_int_from_opt(requested, 1, 60) {
        return v as f64;
    }
    let kind = normalize_endpoint_kind(Some(endpoint_kind));
    let category = classify_endpoint_kind(base_url, &kind);
    if kind == "api" || kind == "proxy" || category == "api" {
        return 30.0;
    }
    if is_ollama_base(base_url) {
        3.0
    } else {
        2.0
    }
}

/// `_cached_model_ids(ep)` — sanitized cached-model IDs.
fn cached_model_ids(ep: &Endpoint) -> Vec<String> {
    normalize_model_ids(&opt_str_to_value(ep.cached_models.as_deref()))
}

/// `_hidden_model_ids(ep)` — sanitized hidden-model IDs as a set.
fn hidden_model_ids(ep: &Endpoint) -> HashSet<String> {
    normalize_model_ids(&opt_str_to_value(ep.hidden_models.as_deref()))
        .into_iter()
        .collect()
}

/// `getattr(ep, col, None)` → a JSON value for `_parse_model_list` (Python `None`
/// when the column is NULL).
fn opt_str_to_value(s: Option<&str>) -> Value {
    match s {
        Some(s) => Value::String(s.to_string()),
        None => Value::Null,
    }
}

/// `_is_ollama_base(base_url)` — True for port-11434 / Ollama-named bases.
fn is_ollama_base(base_url: &str) -> bool {
    let (host, port) = parsed_host_port(base_url);
    if port == Some(11434) || host.contains("ollama") {
        return true;
    }
    // except Exception: "ollama" in (base_url or "").lower()
    if host.is_empty() {
        return base_url.to_lowercase().contains("ollama");
    }
    false
}

/// `_effective_endpoint_kind(ep, base_url)` — explicit kind, with a legacy proxy
/// heuristic for keyed `/v1` (or `/openai`) URLs.
fn effective_endpoint_kind(ep: &Endpoint, base_url: &str) -> String {
    let kind = endpoint_kind_of(ep);
    if kind != "auto" {
        return kind;
    }
    let has_key = ep.api_key.as_deref().map(|k| !k.is_empty()).unwrap_or(false);
    if has_key && !is_ollama_base(base_url) {
        let path = url::Url::parse(base_url)
            .ok()
            .map(|u| u.path().trim_end_matches('/').to_string())
            .unwrap_or_default();
        if path.ends_with("/v1") || path.contains("/openai") {
            return "proxy".to_string();
        }
    }
    "auto".to_string()
}

// Prefixes/substrings for models that are NOT chat-completions-capable
const NON_CHAT_PREFIXES: &[&str] = &[
    "dall-e", "tts-", "whisper", "text-embedding", "embedding",
    "davinci", "babbage", "moderation", "omni-moderation",
    "sora", "gpt-image", "chatgpt-image",
];
const NON_CHAT_CONTAINS: &[&str] = &["-realtime", "-transcribe", "-tts", "-codex", "codex-"];
const NON_CHAT_EXACT_PREFIXES: &[&str] = &["gpt-audio", "gpt-3.5-turbo-instruct"];

/// `_is_chat_model(model_id)` — True if the model ID looks like a chat/
/// completions-capable model.
fn is_chat_model(model_id: &str) -> bool {
    let mid = model_id.to_lowercase();
    for prefix in NON_CHAT_PREFIXES {
        if mid.starts_with(prefix) {
            return false;
        }
    }
    for prefix in NON_CHAT_EXACT_PREFIXES {
        if mid.starts_with(prefix) {
            return false;
        }
    }
    for substr in NON_CHAT_CONTAINS {
        if mid.contains(substr) {
            return false;
        }
    }
    true
}

// ---------------------------------------------------------------------------
// `_probe_single_model` — send a realistic completion request to one model.
// ---------------------------------------------------------------------------

/// `_probe_single_model(base, api_key, model_id, timeout=10, with_tools=False)` —
/// send a realistic completion request to a single model. Returns
/// `{status, latency_ms, error?}`.
async fn probe_single_model(
    base: &str,
    api_key: Option<&str>,
    model_id: &str,
    timeout: u64,
    with_tools: bool,
) -> Map<String, Value> {
    let provider = _detect_provider(base);
    let messages = vec![
        json!({"role": "system", "content": "You are a helpful assistant."}),
        json!({"role": "user", "content": "Say OK"}),
    ];
    // Simple tool definition to test tool support
    let test_tools: Option<Vec<Value>> = if with_tools {
        Some(vec![json!({
            "type": "function",
            "function": {"name": "test", "description": "Test tool", "parameters": {"type": "object", "properties": {}}}
        })])
    } else {
        None
    };

    let target_url: String;
    let mut h: IndexMap<String, String>;
    let payload: Value;

    if provider == "anthropic" {
        target_url = _normalize_anthropic_url(base);
        // auth_headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        let mut auth_headers: IndexMap<String, String> = IndexMap::new();
        if let Some(k) = api_key.filter(|k| !k.is_empty()) {
            auth_headers.insert("Authorization".to_string(), format!("Bearer {k}"));
        }
        h = _build_anthropic_headers(&auth_headers);
        let mut p = _build_anthropic_payload(model_id, &messages, 0.0, 5, false, None);
        if test_tools.is_some() {
            // payload["tools"] = [{"name": "test", ... "input_schema": {...}}]
            if let Some(obj) = p.as_object_mut() {
                obj.insert(
                    "tools".to_string(),
                    json!([{"name": "test", "description": "Test tool", "input_schema": {"type": "object", "properties": {}}}]),
                );
            }
        }
        payload = p;
    } else if provider == "ollama" {
        // target_url = build_chat_url(base); h = build_headers + Content-Type
        target_url = build_chat_url(base);
        h = build_headers(api_key, base);
        h.insert("Content-Type".to_string(), "application/json".to_string());
        // payload = _build_ollama_payload(model_id, messages, 0.0, 5, stream=False, tools=_test_tools)
        payload = _build_ollama_payload(
            model_id,
            &messages,
            0.0,
            5,
            false,
            test_tools.as_deref(),
            None,
        );
    } else {
        target_url = build_chat_url(base);
        h = build_headers(api_key, base);
        h.insert("Content-Type".to_string(), "application/json".to_string());
        let max_key = if _uses_max_completion_tokens(model_id) {
            "max_completion_tokens"
        } else {
            "max_tokens"
        };
        let mut p = Map::new();
        p.insert("model".to_string(), json!(model_id));
        p.insert("messages".to_string(), json!(messages.clone()));
        p.insert(max_key.to_string(), json!(5));
        // Reasoning models (o1/o3/o4/gpt-5) reject an explicit temperature, so a
        // probe that hardcodes one falsely reports a working endpoint as failing.
        if !_restricts_temperature(model_id) {
            p.insert("temperature".to_string(), json!(0.0));
        }
        if let Some(tools) = &test_tools {
            p.insert("tools".to_string(), json!(tools));
        }
        payload = Value::Object(p);
    }

    let mut result = Map::new();
    // t0 = time.time(); r = httpx.post(...); latency = round((time.time()-t0)*1000)
    let client = match reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(timeout))
        // httpx defaults to follow_redirects=False; surface 3xx as-is.
        .redirect(reqwest::redirect::Policy::none())
        .build()
    {
        Ok(c) => c,
        Err(e) => {
            // except Exception as e: return {"status": "fail", "error": str(e)[:80]}
            result.insert("status".to_string(), json!("fail"));
            result.insert("error".to_string(), json!(truncate_chars(&e.to_string(), 80)));
            return result;
        }
    };
    let t0 = std::time::Instant::now();
    let mut req = client.post(&target_url);
    for (k, v) in &h {
        req = req.header(k, v);
    }
    let send_result = req.json(&payload).send().await;
    let latency = t0.elapsed().as_millis() as i64;

    match send_result {
        Ok(r) => {
            let status_code = r.status().as_u16();
            // if r.is_success: return {"status": "ok", "latency_ms": latency}
            if r.status().is_success() {
                result.insert("status".to_string(), json!("ok"));
                result.insert("latency_ms".to_string(), json!(latency));
                return result;
            }
            // Extract error detail from response body
            let mut error_msg = format!("HTTP {status_code}");
            if let Ok(body) = r.json::<Value>().await {
                if let Some(err) = body.get("error") {
                    if let Some(obj) = err.as_object() {
                        // err.get("message", error_msg)[:120]
                        let msg = obj
                            .get("message")
                            .and_then(|m| m.as_str())
                            .map(|s| truncate_chars(s, 120))
                            .unwrap_or_else(|| error_msg.clone());
                        error_msg = msg;
                    } else if let Some(s) = err.as_str() {
                        error_msg = truncate_chars(s, 120);
                    }
                }
            }
            result.insert("status".to_string(), json!("fail"));
            result.insert("latency_ms".to_string(), json!(latency));
            result.insert("error".to_string(), json!(error_msg));
            result
        }
        Err(e) if e.is_timeout() => {
            // except httpx.TimeoutException:
            result.insert("status".to_string(), json!("timeout"));
            result.insert("latency_ms".to_string(), json!((timeout * 1000) as i64));
            result.insert("error".to_string(), json!(format!("Timed out ({timeout}s)")));
            result
        }
        Err(e) => {
            // except Exception as e: return {"status": "fail", "error": str(e)[:80]}
            result.insert("status".to_string(), json!("fail"));
            result.insert("error".to_string(), json!(truncate_chars(&e.to_string(), 80)));
            result
        }
    }
}

/// `str(x)[:n]` over Unicode code points (Python slices by char, not byte).
fn truncate_chars(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

// ---------------------------------------------------------------------------
// `_classify_endpoint` — local vs api.
// ---------------------------------------------------------------------------

const LOCAL_HOSTS: &[&str] = &["localhost", "127.0.0.1", "0.0.0.0", "::1"];
const PRIVATE_PREFIXES: &[&str] = &[
    "10.", "172.16.", "172.17.", "172.18.", "172.19.", "172.20.", "172.21.",
    "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.", "172.28.",
    "172.29.", "172.30.", "172.31.", "192.168.",
];

/// `_TAILSCALE_RE` — the Tailscale CGNAT range `100.64.0.0/10`
/// (`^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.`).
fn matches_tailscale(host: &str) -> bool {
    // 100.<second>. where 64 <= second <= 127
    let mut parts = host.split('.');
    if parts.next() != Some("100") {
        return false;
    }
    let second = match parts.next() {
        Some(s) => s,
        None => return false,
    };
    // The regex requires a trailing '.', i.e. a 3rd octet must follow.
    if parts.next().is_none() {
        return false;
    }
    // Match the regex alternation 64..=127 (with the exact digit-shape rules the
    // alternation encodes: 6[4-9] | [7-9]\d | 1[01]\d | 12[0-7]).
    match second.parse::<u32>() {
        Ok(n) => {
            // The regex anchors to the start of the octet and the alternatives each
            // consume a fixed number of digits, then require a '.'; a value like
            // "640" would not match (the '.' must follow the matched digits).
            let len_ok = match n {
                64..=99 => second.len() == 2,
                100..=127 => second.len() == 3,
                _ => false,
            };
            len_ok && (64..=127).contains(&n)
        }
        Err(_) => false,
    }
}

/// `_classify_endpoint(base_url, endpoint_kind="auto")` — return 'local' if the
/// endpoint URL points to a private/local address, else 'api'. An explicit kind
/// short-circuits the URL heuristic (a2e691d): "local" forces 'local', "api"/
/// "proxy" force 'api'. Includes the Tailscale CGNAT range (100.64.0.0/10).
fn classify_endpoint_kind(base_url: &str, endpoint_kind: &str) -> &'static str {
    // kind = _normalize_endpoint_kind(endpoint_kind)
    let kind = normalize_endpoint_kind(Some(endpoint_kind));
    if kind == "local" {
        return "local";
    }
    if kind == "api" || kind == "proxy" {
        return "api";
    }
    // host = urlparse(base_url).hostname or ""
    let host = url::Url::parse(base_url)
        .ok()
        .and_then(|u| u.host_str().map(|s| s.to_string()))
        .unwrap_or_default();
    if LOCAL_HOSTS.contains(&host.as_str()) || PRIVATE_PREFIXES.iter().any(|p| host.starts_with(p)) {
        return "local";
    }
    if matches_tailscale(&host) {
        return "local";
    }
    "api"
}

/// `_classify_endpoint(base_url)` with the default `endpoint_kind="auto"`. Retained
/// for the `"auto"`-kind callers/tests; handler call sites now pass the effective
/// kind via [`classify_endpoint_kind`].
#[allow(dead_code)]
fn classify_endpoint(base_url: &str) -> &'static str {
    classify_endpoint_kind(base_url, "auto")
}

// ---------------------------------------------------------------------------
// `_probe_endpoint` — probe a base URL's /models endpoint.
// ---------------------------------------------------------------------------

/// `_probe_endpoint(base_url, api_key=None, timeout=5)` — probe a base URL's
/// `/models` endpoint and return the list of model IDs. For Anthropic, queries
/// `/v1/models`, falling back to the hardcoded list.
async fn probe_endpoint(base_url: &str, api_key: Option<&str>, timeout: u64) -> Vec<String> {
    // Codex schemes (`codex:` / `codex-responses:`) are routing sentinels, not HTTP
    // bases — never HTTP-probe `{codex:}/models` (reqwest errors with "builder error
    // for url (codex:/models)"). Their models come from `codex::ensure_probed` /
    // `register_codex_providers`; callers fall back to `cached_models` on empty.
    if crate::core::codex::is_codex_url(base_url) || crate::core::codex::is_codex_responses_url(base_url) {
        return Vec::new();
    }
    // base = resolve_url(_normalize_base(base_url))
    let base = resolve_url(&normalize_base(Some(base_url)));
    if _detect_provider(&base) == "anthropic" {
        // url = build_models_url(base)
        let url = build_models_url(&base);
        let client = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(timeout))
            // httpx defaults to follow_redirects=False; surface 3xx as-is.
            .redirect(reqwest::redirect::Policy::none())
            .build();
        let client = match client {
            Ok(c) => c,
            Err(_) => return ANTHROPIC_MODELS.iter().map(|s| s.to_string()).collect(),
        };
        let mut req = client.get(&url).header("anthropic-version", "2023-06-01");
        if let Some(k) = api_key.filter(|k| !k.is_empty()) {
            req = req.header("x-api-key", k);
        }
        match req.send().await {
            Ok(r) => {
                let status = r.status();
                if status.is_client_error() || status.is_server_error() {
                    // r.raise_for_status() → HTTPStatusError
                    if api_key.filter(|k| !k.is_empty()).is_some() {
                        crate::pylog::warning(&format!(
                            "Anthropic /v1/models failed with API key: HTTP {}",
                            status.as_u16()
                        ));
                        return Vec::new();
                    }
                    crate::pylog::warning(&format!(
                        "Anthropic /v1/models failed, using hardcoded list: HTTP {}",
                        status.as_u16()
                    ));
                    return ANTHROPIC_MODELS.iter().map(|s| s.to_string()).collect();
                }
                match r.json::<Value>().await {
                    Ok(data) => {
                        let models = extract_data_ids(&data);
                        if !models.is_empty() {
                            return models;
                        }
                    }
                    Err(e) => {
                        if api_key.filter(|k| !k.is_empty()).is_some() {
                            crate::pylog::warning(&format!(
                                "Anthropic /v1/models failed with API key: {e}"
                            ));
                            return Vec::new();
                        }
                        crate::pylog::warning(&format!(
                            "Anthropic /v1/models failed, using hardcoded list: {e}"
                        ));
                    }
                }
            }
            Err(e) => {
                if api_key.filter(|k| !k.is_empty()).is_some() {
                    crate::pylog::warning(&format!("Anthropic /v1/models failed with API key: {e}"));
                    return Vec::new();
                }
                crate::pylog::warning(&format!(
                    "Anthropic /v1/models failed, using hardcoded list: {e}"
                ));
            }
        }
        return ANTHROPIC_MODELS.iter().map(|s| s.to_string()).collect();
    }

    // url = build_models_url(base); headers = build_headers(api_key, base)
    let url = build_models_url(&base);
    let headers = build_headers(api_key, &base);
    let has_key = api_key.filter(|k| !k.is_empty()).is_some();
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(timeout))
        // httpx defaults to follow_redirects=False; surface 3xx as-is.
        .redirect(reqwest::redirect::Policy::none())
        .build();
    let client = match client {
        // A client build error is an `except Exception` with no api_key fall-through
        // distinction here; fall through to the Ollama / curated fallback path.
        Ok(c) => Some(c),
        Err(e) => {
            if has_key {
                crate::pylog::warning(&format!("Failed to probe {url} with API key: {e}"));
                return Vec::new();
            }
            crate::pylog::warning(&format!("Failed to probe {url}: {e}"));
            None
        }
    };
    if let Some(client) = client {
        let mut req = client.get(&url);
        for (k, v) in &headers {
            req = req.header(k, v);
        }
        match req.send().await {
            Ok(r) => {
                let status = r.status();
                if status.is_client_error() || status.is_server_error() {
                    // r.raise_for_status() → HTTPStatusError
                    if has_key {
                        crate::pylog::warning(&format!(
                            "Failed to probe {url} with API key: HTTP {}",
                            status.as_u16()
                        ));
                        return Vec::new();
                    }
                    crate::pylog::warning(&format!("Failed to probe {url}: HTTP {}", status.as_u16()));
                } else {
                    match r.json::<Value>().await {
                        Ok(data) => {
                            // OpenAI format: {"data": [{"id": "model-name"}]}
                            let mut models = extract_data_ids(&data);
                            // Ollama format: {"models": [{"name": "model-name"}]}
                            if models.is_empty() {
                                models = extract_ollama_names(&data);
                            }
                            if !models.is_empty() {
                                // Z.AI coding plan omits some working models from /models;
                                // append curated-only entries for that endpoint only.
                                let path = url::Url::parse(&base)
                                    .ok()
                                    .map(|u| u.path().to_string())
                                    .unwrap_or_default();
                                if _host_match(&base, &["z.ai"]) && path.contains("/api/coding") {
                                    if let Some(ck) = match_provider_curated(&base, None) {
                                        if let Some(curated) = PROVIDER_CURATED.get(ck.as_str()) {
                                            // for _e in curated: if _e not in set(models) and not
                                            // any(m.startswith(_e) for m in models): models.append(_e)
                                            for e in curated {
                                                let already = models.iter().any(|m| m == e)
                                                    || models.iter().any(|m| m.starts_with(e));
                                                if !already {
                                                    models.push((*e).to_string());
                                                }
                                            }
                                        }
                                    }
                                }
                                return models;
                            }
                        }
                        Err(e) => {
                            if has_key {
                                crate::pylog::warning(&format!("Failed to probe {url} with API key: {e}"));
                                return Vec::new();
                            }
                            crate::pylog::warning(&format!("Failed to probe {url}: {e}"));
                        }
                    }
                }
            }
            Err(e) => {
                if has_key {
                    crate::pylog::warning(&format!("Failed to probe {url} with API key: {e}"));
                    return Vec::new();
                }
                crate::pylog::warning(&format!("Failed to probe {url}: {e}"));
            }
        }
    }

    // Older Ollama builds and some proxies expose native /api/tags even when the
    // OpenAI-compatible /v1/models path is unavailable.
    let (host, port) = parsed_host_port(&base);
    if port == Some(11434) || host.contains("ollama") {
        // root = base[:-3].rstrip("/") if base.endswith("/v1") else base
        let root = if base.ends_with("/v1") {
            base[..base.len() - 3].trim_end_matches('/').to_string()
        } else {
            base.clone()
        };
        let tags_url = format!("{root}/api/tags");
        let tags_client = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(timeout))
            .redirect(reqwest::redirect::Policy::none())
            .build();
        if let Ok(c) = tags_client {
            match c.get(&tags_url).send().await {
                Ok(r) if !(r.status().is_client_error() || r.status().is_server_error()) => {
                    if let Ok(data) = r.json::<Value>().await {
                        let models = extract_ollama_names(&data);
                        if !models.is_empty() {
                            return models;
                        }
                    }
                }
                Ok(r) => {
                    crate::pylog::debug(&format!(
                        "Ollama /api/tags probe failed for {base}: HTTP {}",
                        r.status().as_u16()
                    ));
                }
                Err(e) => {
                    crate::pylog::debug(&format!("Ollama /api/tags probe failed for {base}: {e}"));
                }
            }
        }
    }
    // Fall back to curated list if the provider has a URL-based match.
    curated_fallback(&base)
}

/// `models = [m.get("id") for m in (data.get("data") or []) if m.get("id")]`.
fn extract_data_ids(data: &Value) -> Vec<String> {
    data.get("data")
        .and_then(|d| d.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|m| m.get("id").and_then(|i| i.as_str()).map(String::from))
                .collect()
        })
        .unwrap_or_default()
}

/// `models = [m.get("name") or m.get("model") for m in (data.get("models") or [])
///   if m.get("name") or m.get("model")]`.
fn extract_ollama_names(data: &Value) -> Vec<String> {
    data.get("models")
        .and_then(|d| d.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|m| {
                    let name = m.get("name").and_then(|v| v.as_str());
                    let model = m.get("model").and_then(|v| v.as_str());
                    // m.get("name") or m.get("model") — first non-empty str wins.
                    name.filter(|s| !s.is_empty())
                        .or(model.filter(|s| !s.is_empty()))
                        .map(String::from)
                })
                .collect()
        })
        .unwrap_or_default()
}

/// `curated_key = _match_provider_curated(base, None); fallback =
/// _PROVIDER_CURATED.get(curated_key) if curated_key else None; return
/// list(fallback) or []`.
fn curated_fallback(base: &str) -> Vec<String> {
    let curated_key = match_provider_curated(base, None);
    if let Some(key) = curated_key {
        if let Some(fallback) = PROVIDER_CURATED.get(key.as_str()) {
            crate::pylog::info(&format!("Using curated fallback for {key}: {fallback:?}"));
            return fallback.iter().map(|s| s.to_string()).collect();
        }
    }
    Vec::new()
}

// ---------------------------------------------------------------------------
// `_ping_endpoint` — reachability probe that does not require listed models.
// ---------------------------------------------------------------------------

/// The `{reachable, status_code, error}` dict returned by `_ping_endpoint`.
struct PingResult {
    reachable: bool,
    /// Captured for parity with the Python dict; not yet surfaced in a response
    /// body (the list/status handlers read `reachable`/`error`).
    #[allow(dead_code)]
    status_code: Option<u16>,
    error: Option<String>,
}

impl PingResult {
    /// `{"reachable": ..., "error": ...}` literals used by callers that ignore the
    /// status code (e.g. the create / test handlers' default `ping`).
    fn new(reachable: bool, error: Option<String>) -> Self {
        PingResult {
            reachable,
            status_code: None,
            error,
        }
    }
}

/// `_result_from_response(r)` — the nested classifier in `_ping_endpoint`
/// (a2e691d). Only 2xx is reachable; 3xx is a redirect-fail (with the Odysseus
/// `/login` special-case); everything else is `HTTP {code}`.
fn result_from_response(code: u16, location: &str) -> PingResult {
    if (300..400).contains(&code) {
        if location.starts_with("/login") || location.contains("/login") {
            return PingResult {
                reachable: false,
                status_code: Some(code),
                error: Some(
                    "That is Odysseus, not a model server. Use the Ollama URL, usually http://host.docker.internal:11434/v1 in Docker.".to_string(),
                ),
            };
        }
        return PingResult {
            reachable: false,
            status_code: Some(code),
            error: Some(format!("HTTP {code} redirect")),
        };
    }
    if (200..300).contains(&code) {
        return PingResult {
            reachable: true,
            status_code: Some(code),
            error: None,
        };
    }
    PingResult {
        reachable: false,
        status_code: Some(code),
        error: Some(format!("HTTP {code}")),
    }
}

/// `_ping_endpoint(base_url, api_key=None, timeout=1.5)` — reachability probe that
/// does not require installed/listed models. a2e691d made this cheap: it probes
/// native `/api/version`/`/api/tags` for Ollama-style bases, then falls back to a
/// plain `GET base` health check — it never uses the (potentially huge) `/models`
/// catalog as a generic health check.
// `last_error` is a Python-style accumulator overwritten across fallback paths, so
// some assignments can be dead depending on which path returns first.
#[allow(unused_assignments)]
async fn ping_endpoint(base_url: &str, api_key: Option<&str>, timeout_ms: u64) -> PingResult {
    // base = resolve_url(_normalize_base(base_url))
    let base = resolve_url(&normalize_base(Some(base_url)));
    // headers = build_headers(api_key, base)
    let headers = build_headers(api_key, &base);

    // looks_like_ollama = port == 11434 or "ollama" in hostname
    let (host, port) = parsed_host_port(&base);
    let looks_like_ollama = port == Some(11434) || host.contains("ollama");

    let mut last_error: Option<String> = None;

    // Native Ollama paths first (no /models dependency).
    if looks_like_ollama {
        // root = base; strip a trailing /v1 or /api
        let mut root = base.clone();
        for suffix in ["/v1", "/api"] {
            if root.ends_with(suffix) {
                root = root[..root.len() - suffix.len()].trim_end_matches('/').to_string();
                break;
            }
        }
        for path in ["/api/version", "/api/tags"] {
            let probe_url = format!("{root}{path}");
            let c = reqwest::Client::builder()
                .timeout(std::time::Duration::from_millis(timeout_ms))
                .redirect(reqwest::redirect::Policy::none())
                .build();
            match c {
                Ok(c) => match c.get(&probe_url).send().await {
                    Ok(r) => {
                        let code = r.status().as_u16();
                        let loc = header_location(&r);
                        let result = result_from_response(code, &loc);
                        if result.reachable {
                            return result;
                        }
                        last_error = result.error;
                    }
                    Err(e) => {
                        last_error = Some(truncate_chars(&e.to_string(), 120));
                    }
                },
                Err(e) => {
                    last_error = Some(truncate_chars(&e.to_string(), 120));
                }
            }
        }
    }

    // Plain GET base health check (with provider headers).
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_millis(timeout_ms))
        // httpx defaults to follow_redirects=False; surface 3xx redirects as-is.
        .redirect(reqwest::redirect::Policy::none())
        .build();
    match client {
        Ok(c) => {
            let mut req = c.get(&base);
            for (k, v) in &headers {
                req = req.header(k, v);
            }
            match req.send().await {
                Ok(r) => {
                    let code = r.status().as_u16();
                    let loc = header_location(&r);
                    return result_from_response(code, &loc);
                }
                Err(e) => {
                    last_error = Some(truncate_chars(&e.to_string(), 120));
                }
            }
        }
        Err(e) => {
            last_error = Some(truncate_chars(&e.to_string(), 120));
        }
    }

    PingResult {
        reachable: false,
        status_code: None,
        error: last_error,
    }
}

/// `r.headers.get("location", "")`.
fn header_location(r: &reqwest::Response) -> String {
    r.headers()
        .get("location")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_string()
}

/// `_model_endpoint_error_message(base_url, ping=None)` — provider-aware error
/// message for failed endpoint probes.
fn model_endpoint_error_message(base_url: &str, ping: Option<&PingResult>) -> String {
    let error = ping.and_then(|p| p.error.clone());
    let (host, port) = parsed_host_port(base_url);
    let is_ollama =
        port == Some(11434) || host.contains("ollama") || base_url.to_lowercase().contains("ollama");

    if is_ollama {
        let mut parts: Vec<String> = vec!["No Ollama models found for that endpoint.".to_string()];
        if let Some(e) = &error {
            parts.push(format!("Last probe error: {e}."));
        }
        parts.push("Check that Ollama is running and that the base URL is correct.".to_string());
        parts.push("For native/local installs, use http://localhost:11434/v1.".to_string());
        parts.push(
            "For Docker, use http://host.docker.internal:11434/v1 when Ollama runs on the host."
                .to_string(),
        );
        parts.push("Run `ollama list` to confirm at least one model is installed.".to_string());
        return parts.join(" ");
    }

    if let Some(e) = &error {
        return format!("No models found for that provider/key. Last probe error: {e}.");
    }
    "No models found for that provider/key.".to_string()
}

// ===========================================================================
// Factory-instance shared state (the Python closure dicts → process globals)
// ===========================================================================

/// `_models_cache` — per-user `/api/models` cache. Key = `(owner, is_admin)`.
static MODELS_CACHE: Lazy<Mutex<HashMap<(String, bool), CacheEntry>>> =
    Lazy::new(|| Mutex::new(HashMap::new()));
/// `_MODELS_CACHE_TTL = 30` seconds.
const MODELS_CACHE_TTL: f64 = 30.0;

/// `{"data": ..., "time": ...}` cache entry.
#[derive(Clone)]
struct CacheEntry {
    data: Value,
    time: f64,
}

/// Per-base model-list refresh state (a2e691d), keyed by `_refresh_key(base,
/// api_key)`. Replaces the old `_probe_failures` ep-id map: tracks a per-base
/// single-flight `inflight` flag, last success/failure timestamps, and a
/// consecutive `fail_count` so repeated picker/API opens don't start duplicate
/// `/models` probes and slow/offline providers get an exponential cooldown.
#[derive(Default, Clone)]
struct RefreshState {
    inflight: bool,
    last_success: f64,
    last_failure: f64,
    fail_count: i64,
    /// Captured for parity with the Python dict; not read back currently.
    #[allow(dead_code)]
    last_attempt: f64,
}
/// `_refresh_state` — module-instance refresh-state map.
static REFRESH_STATE: Lazy<Mutex<HashMap<String, RefreshState>>> =
    Lazy::new(|| Mutex::new(HashMap::new()));
/// `_refresh_inflight = {"v": False}` — coarse single-flight guard.
static REFRESH_INFLIGHT: Lazy<Mutex<bool>> = Lazy::new(|| Mutex::new(false));
/// `_REFRESH_FAILURE_BASE` / `_REFRESH_FAILURE_MAX` — exponential backoff bounds.
const REFRESH_FAILURE_BASE: f64 = 300.0;
const REFRESH_FAILURE_MAX: f64 = 3600.0;

/// `_refresh_key(base, api_key)` — `f"{base.rstrip('/')}\x00{api_key or ''}"`.
fn refresh_key(base: &str, api_key: Option<&str>) -> String {
    format!("{}\x00{}", base.trim_end_matches('/'), api_key.unwrap_or(""))
}

/// `_failure_delay(fails)` — exponential backoff cooldown (sec) after `fails`
/// consecutive failures.
fn failure_delay(fails: i64) -> f64 {
    if fails <= 0 {
        return 0.0;
    }
    let factor = 2f64.powi((fails - 1).max(0) as i32);
    (REFRESH_FAILURE_BASE * factor).min(REFRESH_FAILURE_MAX)
}

/// The grouping info returned by `_should_refresh_endpoint` (the Python `info`
/// dict), plus the should-refresh decision.
struct RefreshInfo {
    id: String,
    base: String,
    api_key: Option<String>,
    key: String,
    timeout: f64,
}

/// `_should_refresh_endpoint(ep, now, force=False)` — `(should_refresh, info)`.
/// Proxy/manual/disabled endpoints are skipped unless forced; failing bases get a
/// backoff cooldown; cached bases respect the per-category refresh interval.
fn should_refresh_endpoint(ep: &Endpoint, now: f64, force: bool) -> (bool, RefreshInfo) {
    let base = normalize_base(Some(ep.base_url.as_str()));
    let kind = effective_endpoint_kind(ep, &base);
    let category = classify_endpoint_kind(&base, &kind);
    let mode = endpoint_refresh_mode(ep, Some(&kind));
    let cached = cached_model_ids(ep);
    let key = refresh_key(&base, ep.api_key.as_deref());
    let state = REFRESH_STATE.lock().unwrap().get(&key).cloned().unwrap_or_default();

    let info = RefreshInfo {
        id: ep.id.clone(),
        base: base.clone(),
        api_key: ep.api_key.clone(),
        key: key.clone(),
        timeout: endpoint_refresh_timeout(ep, category),
    };
    if base.is_empty() {
        return (false, info);
    }
    if state.inflight {
        return (false, info);
    }
    if (mode == "manual" || mode == "disabled") && !force {
        return (false, info);
    }
    let fails = state.fail_count;
    if fails > 0 && !force && now - state.last_failure < failure_delay(fails) {
        return (false, info);
    }
    if !cached.is_empty() && !force {
        let interval = endpoint_refresh_interval(ep, category);
        // last_good = last_success or updated_at or created_at
        let mut last_good = state.last_success;
        if last_good == 0.0 {
            last_good = ep.updated_at_ts;
        }
        if last_good == 0.0 {
            last_good = ep.created_at_ts;
        }
        if last_good != 0.0 && now - last_good < interval {
            return (false, info);
        }
    }
    (true, info)
}

/// `_local_probe_cache = {"data": None, "time": 0.0}`.
static LOCAL_PROBE_CACHE: Lazy<Mutex<Option<CacheEntry>>> = Lazy::new(|| Mutex::new(None));
/// `_LOCAL_PROBE_TTL = 8.0` seconds.
const LOCAL_PROBE_TTL: f64 = 8.0;

/// `_providers_cache = {"data": None, "time": 0}`.
static PROVIDERS_CACHE: Lazy<Mutex<Option<CacheEntry>>> = Lazy::new(|| Mutex::new(None));
/// `_PROVIDERS_CACHE_TTL = 30` seconds.
const PROVIDERS_CACHE_TTL: f64 = 30.0;

/// `time.time()` — seconds since the epoch as a float.
fn now_seconds() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// `_invalidate_models_cache()` — clear the per-user `/api/models` cache.
fn invalidate_models_cache() {
    MODELS_CACHE.lock().unwrap().clear();
}

/// `_auth_disabled()` — `os.getenv("AUTH_ENABLED", "true").lower() == "false"`.
/// Re-expressed locally (the `src.auth_helpers` port keeps its helper private);
/// matches the parse used by `core/middleware` and `web/mod.rs`.
fn auth_disabled() -> bool {
    crate::pyos::getenv("AUTH_ENABLED", "true").to_lowercase() == "false"
}

// ===========================================================================
// DB row projections (raw rusqlite, the SQLAlchemy `ModelEndpoint` reads)
// ===========================================================================

/// A `ModelEndpoint` row projection (the columns the routes read). `api_key` is
/// DECRYPTED in place, mirroring the ORM's `EncryptedText` read path.
struct Endpoint {
    id: String,
    name: String,
    base_url: String,
    api_key: Option<String>,
    is_enabled: bool,
    hidden_models: Option<String>,
    cached_models: Option<String>,
    model_type: Option<String>,
    supports_tools: Option<bool>,
    pinned_models: Option<String>,
    /// `endpoint_kind` column ("auto"/"local"/"api"/"proxy") — proxy/API
    /// classification + refresh policy added by a2e691d.
    endpoint_kind: Option<String>,
    /// `model_refresh_mode` column ("auto"/"manual"/"disabled").
    model_refresh_mode: Option<String>,
    /// `model_refresh_interval` column (seconds; NULL → category default).
    model_refresh_interval: Option<i64>,
    /// `model_refresh_timeout` column (seconds; NULL → category default).
    model_refresh_timeout: Option<i64>,
    /// `updated_at` / `created_at` epoch seconds — the `_should_refresh_endpoint`
    /// `last_good` fallback when no in-memory success timestamp exists yet.
    updated_at_ts: f64,
    created_at_ts: f64,
}

/// Decode the encrypted `api_key` column → the decrypted value (`None`/`""` →
/// `None`), matching `endpoint_auth`'s `EncryptedText` read.
fn decrypt_key(enc: Option<String>) -> Option<String> {
    enc.filter(|k| !k.is_empty())
        .map(|k| crate::src::secret_storage::decrypt(&k))
}

/// Map a full `model_endpoints` row tuple (selected in canonical column order) to
/// an [`Endpoint`]. `owner` is intentionally NOT projected — none of the route
/// handlers read it (the owner scope is applied in the SQL WHERE clause).
type EndpointTuple = (
    String,
    String,
    String,
    Option<String>,
    Option<bool>,
    Option<String>,
    Option<String>,
    Option<String>,
    Option<bool>,
    Option<String>,
    Option<String>,
    Option<String>,
    Option<i64>,
    Option<i64>,
    Option<String>,
    Option<String>,
);

fn row_to_endpoint(t: EndpointTuple) -> Endpoint {
    Endpoint {
        id: t.0,
        name: t.1,
        base_url: t.2,
        api_key: decrypt_key(t.3),
        is_enabled: t.4.unwrap_or(true),
        hidden_models: t.5,
        cached_models: t.6,
        model_type: t.7,
        supports_tools: t.8,
        pinned_models: t.9,
        endpoint_kind: t.10,
        model_refresh_mode: t.11,
        model_refresh_interval: t.12,
        model_refresh_timeout: t.13,
        updated_at_ts: stored_ts(t.14.as_deref()),
        created_at_ts: stored_ts(t.15.as_deref()),
    }
}

const ENDPOINT_COLS: &str =
    "id, name, base_url, api_key, is_enabled, hidden_models, cached_models, model_type, \
     supports_tools, pinned_models, endpoint_kind, model_refresh_mode, model_refresh_interval, \
     model_refresh_timeout, updated_at, created_at";

fn map_endpoint_row(r: &rusqlite::Row) -> rusqlite::Result<EndpointTuple> {
    Ok((
        r.get(0)?,
        r.get(1)?,
        r.get(2)?,
        r.get(3)?,
        r.get(4)?,
        r.get(5)?,
        r.get(6)?,
        r.get(7)?,
        r.get(8)?,
        r.get(9)?,
        r.get(10)?,
        r.get(11)?,
        r.get(12)?,
        r.get(13)?,
        r.get(14)?,
        r.get(15)?,
    ))
}

/// `_ts(value)` over a stored SQLite datetime string → epoch seconds (`0.0` when
/// NULL/unparseable). Python calls `value.timestamp()` on the SQLAlchemy-read
/// `datetime` (naive *local*); the Rust port normalizes stored datetimes to naive
/// UTC everywhere (see `pydatetime`), so this parses as UTC. The value is only used
/// as a recency-comparison fallback against `time.time()` for `last_good`.
fn stored_ts(stored: Option<&str>) -> f64 {
    let s = match stored {
        Some(s) if !s.is_empty() => s,
        _ => return 0.0,
    };
    let parsed = chrono::NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S%.f")
        .or_else(|_| chrono::NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S"));
    match parsed {
        Ok(dt) => dt.and_utc().timestamp() as f64 + (dt.and_utc().timestamp_subsec_micros() as f64 / 1e6),
        Err(_) => 0.0,
    }
}

/// `db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all()` — all
/// enabled endpoints, no owner scope.
fn all_enabled_endpoints() -> Vec<Endpoint> {
    let conn = match session_local() {
        Ok(c) => c,
        Err(_) => return Vec::new(),
    };
    let sql = format!("SELECT {ENDPOINT_COLS} FROM model_endpoints WHERE is_enabled = 1");
    let mut stmt = match conn.prepare(&sql) {
        Ok(s) => s,
        Err(_) => return Vec::new(),
    };
    let rows = stmt.query_map([], map_endpoint_row);
    match rows {
        Ok(it) => it.filter_map(Result::ok).map(row_to_endpoint).collect(),
        Err(_) => Vec::new(),
    }
}

/// All enabled endpoints visible to `owner`: the owner's own rows + null-owner
/// (legacy/shared). This is the raw-SQL re-expression of `owner_filter(q,
/// ModelEndpoint, owner)` (`owner = ?1 OR owner IS NULL`).
fn owner_scoped_enabled_endpoints(owner: &str) -> Vec<Endpoint> {
    let conn = match session_local() {
        Ok(c) => c,
        Err(_) => return Vec::new(),
    };
    let sql = format!(
        "SELECT {ENDPOINT_COLS} FROM model_endpoints \
         WHERE is_enabled = 1 AND (owner = ?1 OR owner IS NULL)"
    );
    let mut stmt = match conn.prepare(&sql) {
        Ok(s) => s,
        Err(_) => return Vec::new(),
    };
    let rows = stmt.query_map([owner], map_endpoint_row);
    match rows {
        Ok(it) => it.filter_map(Result::ok).map(row_to_endpoint).collect(),
        Err(_) => Vec::new(),
    }
}

/// Load one endpoint by id (no `is_enabled` filter), `None` when missing.
fn endpoint_by_id(id: &str) -> Option<Endpoint> {
    let conn = session_local().ok()?;
    let sql = format!("SELECT {ENDPOINT_COLS} FROM model_endpoints WHERE id = ?1");
    let t: EndpointTuple = conn
        .query_row(&sql, [id], map_endpoint_row)
        .optional()
        .ok()??;
    Some(row_to_endpoint(t))
}

/// `json.loads(s)` over an `Option<String>` → a `Vec<String>` (silently empty on
/// parse failure, matching the Python `try/except` around `json.loads`).
fn parse_str_list(s: &Option<String>) -> Vec<String> {
    s.as_ref()
        .and_then(|c| serde_json::from_str::<Vec<String>>(c).ok())
        .unwrap_or_default()
}

// ===========================================================================
// `_fetch_models` — the owner-scoped picker list.
// ===========================================================================

/// `_fetch_models(owner="", is_admin=False)` — return the model list from cached
/// data (instant). SECURITY: filters endpoints by `owner` (NULL-owner rows treated
/// as legacy/shared). Admins see EVERY endpoint.
fn fetch_models(owner: &str, is_admin: bool) -> Value {
    let mut items: Vec<Value> = Vec::new();

    // q = db.query(ModelEndpoint).filter(is_enabled == True)
    // if owner and not is_admin: q = owner_filter(q, ModelEndpoint, owner)
    let endpoints = if !owner.is_empty() && !is_admin {
        owner_scoped_enabled_endpoints(owner)
    } else {
        all_enabled_endpoints()
    };

    for ep in &endpoints {
        let base = normalize_base(Some(&ep.base_url));
        let provider = _detect_provider(&base);
        // Use cached models
        let mut model_ids = cached_model_ids(ep);
        let ep_model_type = ep
            .model_type
            .clone()
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| "llm".to_string());
        // Filter out hidden (probe-failed) models
        let hidden: HashSet<String> = hidden_model_ids(ep);
        model_ids.retain(|m| !hidden.contains(m));
        // Build correct URL based on provider. Codex schemes (`codex:` /
        // `codex-responses:`) are NOT HTTP bases — they are routing sentinels the
        // chat path consumes verbatim (Mode A spawns `codex app-server`; Mode B reads
        // `~/.codex/auth.json`). Appending `/chat/completions` mangles them into
        // `codex:/chat/completions`, which the picker stores as the session's
        // endpoint_url, so the home/auth-path parser later sees `/chat/completions`
        // (→ "auth.json at /chat/completions/auth.json" / a bad CODEX_HOME). Serve the
        // scheme url untouched.
        let chat_url = if crate::core::codex::is_codex_url(&base)
            || crate::core::codex::is_codex_responses_url(&base)
        {
            base.clone()
        } else if provider == "anthropic" {
            build_chat_url(&base)
        } else {
            format!("{base}/chat/completions")
        };
        let kind = effective_endpoint_kind(ep, &base);
        let category = classify_endpoint_kind(&base, &kind);

        if !model_ids.is_empty() {
            let curated_key = match_provider_curated(&base, None);
            let (curated, extra) = curate_models(&model_ids, curated_key.as_deref());
            items.push(json!({
                "host": "custom",
                "port": 0,
                "url": chat_url,
                "models": curated,
                "models_display": curated.iter().map(|mid| last_segment(mid)).collect::<Vec<_>>(),
                "models_extra": extra,
                "models_extra_display": extra.iter().map(|mid| last_segment(mid)).collect::<Vec<_>>(),
                "endpoint_id": ep.id,
                "endpoint_name": ep.name,
                "category": category,
                "endpoint_kind": kind,
                "model_type": ep_model_type,
            }));
        } else {
            // Endpoint unreachable but still show it greyed out
            items.push(json!({
                "host": "custom",
                "port": 0,
                "url": chat_url,
                "models": [],
                "models_display": [],
                "models_extra": [],
                "models_extra_display": [],
                "endpoint_id": ep.id,
                "endpoint_name": ep.name,
                "category": category,
                "endpoint_kind": kind,
                "model_type": ep_model_type,
                "offline": true,
            }));
        }
    }

    json!({"hosts": [], "items": items})
}

/// `mid.split("/")[-1]` — the last `/`-separated segment.
fn last_segment(mid: &str) -> String {
    mid.rsplit('/').next().unwrap_or(mid).to_string()
}

// ===========================================================================
// `_refresh_caches_bg` — background re-probe of all endpoints.
// ===========================================================================

/// `_refresh_caches_bg(force=False)` — background task: safely refresh model caches
/// with per-base single-flight (a2e691d). The public `/api/models` path stays
/// cached-first. This refresh NEVER clears a non-empty cached model list on
/// timeout/failure, and proxy/manual endpoints are skipped unless `force`.
fn refresh_caches_bg(force: bool) {
    {
        let mut inflight = REFRESH_INFLIGHT.lock().unwrap();
        if *inflight {
            return; // already running
        }
        *inflight = true;
    }

    tokio::spawn(async move {
        // try/finally: always reset every per-base `inflight` flag + the coarse guard.
        let _ = do_refresh(force).await;
        {
            let mut state = REFRESH_STATE.lock().unwrap();
            for st in state.values_mut() {
                st.inflight = false;
            }
        }
        *REFRESH_INFLIGHT.lock().unwrap() = false;
    });
}

/// The `_do()` inner body of `_refresh_caches_bg`.
/// One per-base probe job: `(refresh_key, base_url, api_key, timeout_secs, endpoint_ids)`.
type ProbeInput = (String, String, Option<String>, f64, Vec<String>);

async fn do_refresh(force: bool) {
    // endpoints = db.query(ModelEndpoint).filter(is_enabled == True).all()
    let endpoints = all_enabled_endpoints();
    let now = now_seconds();

    // Group eligible endpoints by base+key (per-base single-flight). The Python
    // `groups` dict keeps insertion order; an IndexMap preserves that.
    struct Group {
        base: String,
        api_key: Option<String>,
        timeout: f64,
        endpoint_ids: Vec<String>,
    }
    let mut groups: IndexMap<String, Group> = IndexMap::new();
    for ep in &endpoints {
        let (ok, info) = should_refresh_endpoint(ep, now, force);
        if !ok {
            continue;
        }
        groups
            .entry(info.key.clone())
            .or_insert_with(|| Group {
                base: info.base.clone(),
                api_key: info.api_key.clone(),
                timeout: info.timeout,
                endpoint_ids: Vec::new(),
            })
            .endpoint_ids
            .push(info.id.clone());
    }

    if groups.is_empty() {
        return;
    }

    // Mark each group inflight before probing.
    {
        let mut state = REFRESH_STATE.lock().unwrap();
        for key in groups.keys() {
            let st = state.entry(key.clone()).or_default();
            st.inflight = true;
            st.last_attempt = now;
        }
    }

    // Bounded parallelism — min(4, len) concurrent probes (the Python
    // ThreadPoolExecutor with max_workers=min(4, len(groups))).
    use futures_util::stream::{self, StreamExt};
    let max_workers = std::cmp::min(4, groups.len());
    let probe_inputs: Vec<ProbeInput> = groups
        .into_iter()
        .map(|(key, g)| (key, g.base, g.api_key, g.timeout, g.endpoint_ids))
        .collect();
    // (key, endpoint_ids, ids)
    let results: Vec<(String, Vec<String>, Vec<String>)> = stream::iter(probe_inputs)
        .map(|(key, base, api_key, timeout, endpoint_ids)| async move {
            // timeout=data.get("timeout") or 2 — round to whole seconds for the probe.
            let to = if timeout > 0.0 { timeout.round() as u64 } else { 2 };
            let ids = probe_endpoint(&base, api_key.as_deref(), to).await;
            (key, endpoint_ids, ids)
        })
        .buffer_unordered(max_workers)
        .collect()
        .await;

    // Persist results + update per-base refresh state. NEVER overwrite a non-empty
    // cached list when the probe returns nothing (cached-models preservation).
    let mut changed = false;
    let conn = match session_local() {
        Ok(c) => c,
        Err(_) => return,
    };
    {
        let mut state = REFRESH_STATE.lock().unwrap();
        for (key, endpoint_ids, ids) in results {
            let st = state.entry(key).or_default();
            if !ids.is_empty() {
                let cached = serde_json::to_string(&ids).unwrap_or_else(|_| "[]".to_string());
                for ep_id in &endpoint_ids {
                    if conn
                        .execute(
                            "UPDATE model_endpoints SET cached_models = ?2 WHERE id = ?1",
                            rusqlite::params![ep_id, cached],
                        )
                        .map(|n| n > 0)
                        .unwrap_or(false)
                    {
                        changed = true;
                    }
                }
                st.last_success = now_seconds();
                st.fail_count = 0;
                st.last_failure = 0.0;
            } else {
                st.last_failure = now_seconds();
                st.fail_count += 1;
            }
            st.inflight = false;
        }
    }
    if changed {
        invalidate_models_cache();
    }
}

// ===========================================================================
// Auth helpers (the `require_admin(request)` gate the Python handlers call)
// ===========================================================================

/// `require_admin(request)` — the per-handler admin gate. Reads the
/// `X-Odysseus-Internal-Token` header + the stamped `current_user`, delegating to
/// the foundation [`auth_adapter::require_admin`].
fn require_admin(
    state: &AppState,
    headers: &HeaderMap,
    user: Option<&CurrentUser>,
) -> Result<(), HttpException> {
    let internal_header = headers
        .get("X-Odysseus-Internal-Token")
        .and_then(|v| v.to_str().ok());
    let user = user.map(|u| u.0.as_str());
    auth_adapter::require_admin(state, internal_header, user)
}

/// `get_current_user(request) or ""` — the stamped user, or `""` when absent.
fn current_user_or_empty(user: Option<&CurrentUser>) -> String {
    user.map(|u| u.0.clone()).unwrap_or_default()
}

// ===========================================================================
// Handlers
// ===========================================================================

/// `@router.get("/models")` — `def api_models(request, refresh=False)`. Per-user
/// (caller sees only their endpoints + legacy/shared null-owner rows). Cached
/// per-user for 30s.
async fn api_models(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    // refresh: bool = False (FastAPI lax bool coercion)
    let refresh = query_bool(&q, "refresh", false)?;
    // owner = get_current_user(request) or ""
    let owner = current_user_or_empty(user.as_ref().map(|Extension(u)| u));

    // Reject anonymous in configured deployments (unless auth is explicitly off).
    if owner.is_empty() && !auth_disabled() && state.auth.is_configured() {
        return Err(HttpException::new(401, "Not authenticated"));
    }
    // ODYSSEUS-RUST ENHANCEMENT (not in model_routes.py): auto-register Codex for the
    // authenticated owner so the Codex providers appear first-class in the picker with
    // zero setup, exactly as the original inline `list_models` handler did. Layered on
    // top of the faithful port; the Python-mirrored response logic below is unchanged.
    crate::web::auto_register_codex(&owner).await;
    // Admins see every endpoint; regular users get the owner-scoped view.
    let is_admin = if !owner.is_empty() {
        state.auth.is_admin(&owner)
    } else {
        false
    };

    let now = now_seconds();
    let cache_key = (owner.clone(), is_admin);
    {
        let cache = MODELS_CACHE.lock().unwrap();
        if let Some(entry) = cache.get(&cache_key) {
            if !refresh && (now - entry.time) < MODELS_CACHE_TTL {
                return Ok(Json(entry.data.clone()).into_response());
            }
        }
    }
    let result = fetch_models(&owner, is_admin);
    MODELS_CACHE.lock().unwrap().insert(
        cache_key,
        CacheEntry {
            data: result.clone(),
            time: now,
        },
    );
    // Kick off background refresh to update caches from live endpoints
    refresh_caches_bg(refresh);
    Ok(Json(result).into_response())
}

/// `@router.get("/model-endpoints/probe-local")` — fast parallel reachability
/// check for LOCAL endpoints only. Returns `{ep_id: {alive, latency_ms, error}}`.
async fn probe_local_endpoints(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    require_admin(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    let now = now_seconds();
    {
        let cache = LOCAL_PROBE_CACHE.lock().unwrap();
        if let Some(entry) = cache.as_ref() {
            if (now - entry.time) < LOCAL_PROBE_TTL {
                return Ok(Json(entry.data.clone()).into_response());
            }
        }
    }

    // local_eps: endpoints classified local under their effective kind. proxy/API
    // endpoints (incl. the keyed-/v1 legacy heuristic) are excluded from this
    // aggressive local reachability sweep (a2e691d).
    let endpoints = all_enabled_endpoints();
    let local_eps: Vec<(String, String, Option<String>)> = endpoints
        .into_iter()
        .filter_map(|ep| {
            let base = normalize_base(Some(&ep.base_url));
            let kind = effective_endpoint_kind(&ep, &base);
            if classify_endpoint_kind(&base, &kind) == "local" {
                Some((ep.id, base, ep.api_key))
            } else {
                None
            }
        })
        .collect();

    // Group by base+key so co-located endpoints share one ping (the Python
    // `grouped` dict, insertion-ordered).
    struct LocalGroup {
        base: String,
        api_key: Option<String>,
        endpoint_ids: Vec<String>,
    }
    let mut grouped: IndexMap<String, LocalGroup> = IndexMap::new();
    for (ep_id, base, api_key) in local_eps {
        let key = refresh_key(&base, api_key.as_deref());
        grouped
            .entry(key)
            .or_insert_with(|| LocalGroup {
                base: base.clone(),
                api_key: api_key.clone(),
                endpoint_ids: Vec::new(),
            })
            .endpoint_ids
            .push(ep_id);
    }

    // asyncio.gather(*[_probe_one(data) for data in grouped.values()]) — concurrent
    // 1.5s cheap reachability pings (no /models catalog fetch).
    use futures_util::future::join_all;
    let order: Vec<String> = grouped.keys().cloned().collect();
    let futures = order.iter().map(|key| {
        let g = &grouped[key];
        let base = g.base.clone();
        let api_key = g.api_key.clone();
        async move { probe_local_one(&base, api_key.as_deref()).await }
    });
    let results_list = join_all(futures).await;

    let mut results = Map::new();
    for (key, v) in order.iter().zip(results_list) {
        for eid in &grouped[key].endpoint_ids {
            results.insert(eid.clone(), v.clone());
        }
    }
    let data = Value::Object(results);

    *LOCAL_PROBE_CACHE.lock().unwrap() = Some(CacheEntry {
        data: data.clone(),
        time: now,
    });
    Ok(Json(data).into_response())
}

/// `_probe_one(data)` (the async closure in `probe_local`). a2e691d switched this
/// to a cheap `_ping_endpoint` reachability check (1.5s) instead of a `/models`
/// catalog fetch, so large proxies no longer make local probing slow.
async fn probe_local_one(base: &str, api_key: Option<&str>) -> Value {
    let t0 = std::time::Instant::now();
    let ping = ping_endpoint(base, api_key, 1500).await;
    let lat = t0.elapsed().as_millis() as i64;
    json!({
        "alive": ping.reachable,
        "latency_ms": lat,
        "status_code": match ping.status_code { Some(c) => json!(c), None => Value::Null },
        "error": match ping.error { Some(e) => json!(e), None => Value::Null },
    })
}

/// `@router.get("/ping")` — probe all enabled endpoints and return status +
/// latency.
async fn ping_endpoints(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    require_admin(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    let endpoints = all_enabled_endpoints();

    let mut results: Vec<Value> = Vec::new();
    for ep in &endpoints {
        let base = normalize_base(Some(&ep.base_url));
        let provider = _detect_provider(&base);
        // a2e691d: stop hitting /models per endpoint here (slow for large proxies).
        // Use a uniform cheap reachability ping + the persisted cached count, so a
        // usable endpoint with cached models still reports online even when the
        // health probe momentarily fails.
        let kind = effective_endpoint_kind(ep, &base);
        let cached_count = cached_model_ids(ep).len();
        let mut entry = Map::new();
        entry.insert("id".to_string(), json!(ep.id));
        entry.insert("name".to_string(), json!(ep.name));
        entry.insert("base_url".to_string(), json!(base));
        entry.insert("provider".to_string(), json!(provider));
        entry.insert("category".to_string(), json!(classify_endpoint_kind(&base, &kind)));
        entry.insert("endpoint_kind".to_string(), json!(kind));

        let t0 = std::time::Instant::now();
        let ping = ping_endpoint(&base, ep.api_key.as_deref(), 1500).await;
        entry.insert("latency_ms".to_string(), json!(t0.elapsed().as_millis() as i64));
        entry.insert(
            "status".to_string(),
            json!(if ping.reachable || cached_count > 0 { "online" } else { "offline" }),
        );
        entry.insert(
            "error".to_string(),
            match ping.error {
                Some(e) => json!(e),
                None => Value::Null,
            },
        );
        // model_count = cached_count or (len(ANTHROPIC_MODELS) if anthropic else 0)
        let model_count = if cached_count > 0 {
            cached_count
        } else if provider == "anthropic" {
            ANTHROPIC_MODELS.len()
        } else {
            0
        };
        entry.insert("model_count".to_string(), json!(model_count));
        results.push(Value::Object(entry));
    }

    Ok(Json(json!({"endpoints": results})).into_response())
}

/// `@router.post("/probe-selected")` — probe specific models for compare
/// pre-check. Body: `{models: [{endpoint_id, model}]}`.
async fn probe_selected(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Json(request_body): Json<Value>,
) -> Result<Response, HttpException> {
    require_admin(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    // models_to_probe = request_body.get("models", [])
    let models_to_probe = request_body
        .get("models")
        .and_then(|m| m.as_array())
        .cloned()
        .unwrap_or_default();
    // if not models_to_probe: return {"results": []}
    if models_to_probe.is_empty() {
        return Ok(Json(json!({"results": []})).into_response());
    }

    // endpoints_cache = {}
    let mut endpoints_cache: HashMap<String, (String, Option<String>)> = HashMap::new();
    let mut results: Vec<Value> = Vec::new();
    for item in &models_to_probe {
        let ep_id = item.get("endpoint_id").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let model_id = item.get("model").and_then(|v| v.as_str()).unwrap_or("").to_string();
        if model_id.is_empty() {
            results.push(json!({"model": model_id, "status": "fail", "error": "No model specified"}));
            continue;
        }

        // Cache endpoint lookups
        if !ep_id.is_empty() && !endpoints_cache.contains_key(&ep_id) {
            if let Some(ep) = endpoint_by_id(&ep_id) {
                endpoints_cache.insert(ep_id.clone(), (ep.base_url, ep.api_key));
            }
        }
        // ep_data = endpoints_cache.get(ep_id)
        let ep_data: Option<(String, Option<String>)> = match endpoints_cache.get(&ep_id) {
            Some(d) => Some(d.clone()),
            None => {
                // Try to find by base_url from the model's endpoint field
                let endpoint_url = item.get("endpoint").and_then(|v| v.as_str()).unwrap_or("");
                if !endpoint_url.is_empty() {
                    let api_key = item
                        .get("api_key")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                    Some((endpoint_url.to_string(), Some(api_key)))
                } else {
                    None
                }
            }
        };
        let (base_url, ep_api_key) = match ep_data {
            Some(d) => d,
            None => {
                results.push(json!({"model": model_id, "status": "fail", "error": "Endpoint not found"}));
                continue;
            }
        };

        let base = normalize_base(Some(&base_url));
        let with_tools = item.get("with_tools").and_then(|v| v.as_bool()).unwrap_or(false);
        let mut result = probe_single_model(&base, ep_api_key.as_deref(), &model_id, 8, with_tools).await;
        result.insert("model".to_string(), json!(model_id));
        result.insert("endpoint_id".to_string(), json!(ep_id));
        results.push(Value::Object(result));
    }

    Ok(Json(json!({"results": results})).into_response())
}

/// `@router.get("/probe")` — probe individual models with a tiny completion
/// request. Streams SSE results.
async fn probe_models(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    require_admin(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    let endpoint_id = q.get("endpoint_id").filter(|s| !s.is_empty()).cloned();

    // q = db.query(ModelEndpoint).filter(is_enabled == True)
    // if endpoint_id: q = q.filter(ModelEndpoint.id == endpoint_id)
    let endpoints = all_enabled_endpoints();
    let ep_data: Vec<(String, String, String, Option<String>)> = endpoints
        .into_iter()
        .filter(|ep| match &endpoint_id {
            Some(eid) => &ep.id == eid,
            None => true,
        })
        .map(|ep| (ep.id, ep.name, ep.base_url, ep.api_key))
        .collect();

    // if not ep_data: yield probe_done(total=0, ok=0)
    if ep_data.is_empty() {
        let sse = async_stream::stream! {
            yield sse_bytes(&format!("data: {}\n\n", json!({"type": "probe_done", "total": 0, "ok": 0})));
        };
        return Ok(stream_response(sse));
    }

    let sse = async_stream::stream! {
        let mut total: i64 = 0;
        let mut ok_count: i64 = 0;
        for (ep_id, ep_name, base_url, api_key) in ep_data {
            let base = normalize_base(Some(&base_url));
            let all_models = probe_endpoint(&base, api_key.as_deref(), 5).await;
            // Update cached_models in DB
            if !all_models.is_empty() {
                if let Ok(conn) = session_local() {
                    let cached = serde_json::to_string(&all_models).unwrap_or_else(|_| "[]".to_string());
                    let _ = conn.execute(
                        "UPDATE model_endpoints SET cached_models = ?2 WHERE id = ?1",
                        rusqlite::params![ep_id, cached],
                    );
                }
            }
            if all_models.is_empty() {
                yield sse_bytes(&format!("data: {}\n\n", json!({"type": "probe_start", "endpoint": ep_name, "model_count": 0, "error": "No models found or endpoint offline"})));
                continue;
            }

            let models: Vec<String> = all_models.iter().filter(|m| is_chat_model(m)).cloned().collect();
            let skipped = all_models.len() - models.len();
            yield sse_bytes(&format!("data: {}\n\n", json!({"type": "probe_start", "endpoint": ep_name, "model_count": models.len(), "skipped": skipped})));

            for model_id in &models {
                total += 1;
                let mut result = probe_single_model(&base, api_key.as_deref(), model_id, 8, false).await;
                result.insert("type".to_string(), json!("probe_result"));
                result.insert("endpoint".to_string(), json!(ep_name));
                result.insert("model".to_string(), json!(model_id));
                if result.get("status").and_then(|s| s.as_str()) == Some("ok") {
                    ok_count += 1;
                }
                yield sse_bytes(&format!("data: {}\n\n", Value::Object(result)));
            }
        }
        yield sse_bytes(&format!("data: {}\n\n", json!({"type": "probe_done", "total": total, "ok": ok_count})));
    };
    Ok(stream_response(sse))
}

/// `@router.get("/providers")` — get all available providers (cached for 30s).
async fn providers(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    require_admin(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    let refresh = query_bool(&q, "refresh", false)?;
    let now = now_seconds();
    {
        let cache = PROVIDERS_CACHE.lock().unwrap();
        if let Some(entry) = cache.as_ref() {
            if !refresh && (now - entry.time) < PROVIDERS_CACHE_TTL {
                return Ok(Json(entry.data.clone()).into_response());
            }
        }
    }
    let result = state.model_discovery.get_providers().await;
    *PROVIDERS_CACHE.lock().unwrap() = Some(CacheEntry {
        data: result.clone(),
        time: now,
    });
    Ok(Json(result).into_response())
}

/// `@router.get("/discover")` — scan local network for model servers on common
/// ports.
async fn discover_local(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    require_admin(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    Ok(Json(state.model_discovery.discover_models().await).into_response())
}

// ---- Admin: model endpoints CRUD ----

/// `@router.get("/model-endpoints")` — `def list_model_endpoints(request)`.
async fn list_model_endpoints(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    require_admin(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    // ODYSSEUS-RUST ENHANCEMENT (not in model_routes.py): auto-register Codex for the
    // authenticated owner so the Codex providers appear first-class with zero setup,
    // exactly as the original inline `list_model_endpoints` handler did. Layered on top
    // of the faithful port; the Python-mirrored listing logic below is unchanged.
    {
        let owner = current_user_or_empty(user.as_ref().map(|Extension(u)| u));
        crate::web::auto_register_codex(&owner).await;
    }
    // rows = db.query(ModelEndpoint).order_by(ModelEndpoint.created_at).all()
    // The (non-Send) rusqlite Connection/Statement must NOT enter this handler's
    // async state (there is an `.await` in the loop below). Confine them to an
    // immediately-invoked closure — a separate stack frame — so only the owned
    // `Vec<Endpoint>` escapes; nothing non-Send is held across the await.
    let rows: Vec<Endpoint> = (|| -> Result<Vec<Endpoint>, HttpException> {
        let conn = session_local().map_err(db_err)?;
        let sql = format!("SELECT {ENDPOINT_COLS} FROM model_endpoints ORDER BY created_at");
        let mut stmt = conn.prepare(&sql).map_err(db_err)?;
        // Bind to a local so the `?`-desugared temporary (which borrows stmt) is
        // dropped at this `;`, before stmt/conn drop at the closure end.
        let out: Vec<Endpoint> = stmt
            .query_map([], map_endpoint_row)
            .map_err(db_err)?
            .filter_map(Result::ok)
            .map(row_to_endpoint)
            .collect();
        Ok(out)
    })()?;

    let mut results: Vec<Value> = Vec::new();
    for r in &rows {
        // Use cached model list to avoid slow probe on every load
        let all_models = cached_model_ids(r);
        let hidden: HashSet<String> = hidden_model_ids(r);
        let pinned = normalize_model_ids_opt(r.pinned_models.as_deref());
        let visible = visible_models(
            r.cached_models.as_deref(),
            r.hidden_models.as_deref(),
            r.pinned_models.as_deref(),
        );
        // Endpoint counts as reachable if it has any model — including admin-pinned
        // IDs that a probe would never surface.
        let mut status = if !all_models.is_empty() || !pinned.is_empty() {
            "online"
        } else {
            "offline"
        };
        let mut ping: Option<PingResult> = None;
        if all_models.is_empty() && pinned.is_empty() && r.is_enabled {
            let p = ping_endpoint(&r.base_url, r.api_key.as_deref(), 1000).await;
            if p.reachable {
                status = "empty";
            }
            ping = Some(p);
        }
        let base = normalize_base(Some(&r.base_url));
        let kind = effective_endpoint_kind(r, &base);
        results.push(json!({
            "id": r.id,
            "name": r.name,
            "base_url": r.base_url,
            "has_key": r.api_key.as_deref().map(|k| !k.is_empty()).unwrap_or(false),
            "is_enabled": r.is_enabled,
            "models": visible,
            "pinned_models": pinned,
            "hidden_count": hidden.len(),
            "online": status != "offline",
            "status": status,
            "ping_error": ping.as_ref().and_then(|p| p.error.clone()),
            "model_type": r.model_type.clone().filter(|s| !s.is_empty()).unwrap_or_else(|| "llm".to_string()),
            "supports_tools": r.supports_tools,
            "endpoint_kind": kind,
            "category": classify_endpoint_kind(&base, &kind),
            "model_refresh_mode": endpoint_refresh_mode(r, Some(&kind)),
            "model_refresh_interval": r.model_refresh_interval,
            "model_refresh_timeout": r.model_refresh_timeout,
        }));
    }
    Ok(Json(Value::Array(results)).into_response())
}

/// `@router.post("/model-endpoints")` — `def create_model_endpoint(...)`. Add an
/// endpoint via `Form(...)` fields.
async fn create_model_endpoint(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    mp: axum::extract::Multipart,
) -> Result<Response, HttpException> {
    require_admin(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    let f = parse_form(mp).await;

    // name: str = Form(""), base_url: str = Form(...), ...
    let name = form_get(&f, "name", "");
    // base_url is required (Form(...)) — a missing field is a 422 in FastAPI.
    let base_url_raw = match f.get("base_url") {
        Some(v) => v.clone(),
        None => return Err(missing_form_field("base_url")),
    };
    let api_key = form_get(&f, "api_key", "");
    let skip_probe = form_get(&f, "skip_probe", "false");
    let require_models = form_get(&f, "require_models", "false");
    let model_type = form_get(&f, "model_type", "llm");
    let endpoint_kind_form = form_get(&f, "endpoint_kind", "auto");
    let model_refresh_mode_form = form_get(&f, "model_refresh_mode", "");
    let model_refresh_interval_form = form_get(&f, "model_refresh_interval", "");
    let model_refresh_timeout_form = form_get(&f, "model_refresh_timeout", "");
    let supports_tools = form_get(&f, "supports_tools", "");
    let pinned_models_raw = form_get(&f, "pinned_models", "");
    let container_local = form_get(&f, "container_local", "false");
    let shared = form_get(&f, "shared", "true");

    // base_url = _normalize_base(base_url)
    let mut base_url = normalize_base(Some(&base_url_raw));
    if base_url.is_empty() {
        return Err(HttpException::new(400, "Base URL is required"));
    }
    // Resolve hostname via Tailscale if DNS fails
    base_url = resolve_url(&base_url);
    // In Docker, manually added loopback URLs usually point at a host-local server.
    base_url = rewrite_loopback_for_docker(&base_url, truthy(Some(&container_local)));

    // Auto-generate name from URL if not provided
    let mut name = name;
    if name.trim().is_empty() {
        name = base_url
            .replace("http://", "")
            .replace("https://", "")
            .split('/')
            .next()
            .unwrap_or("")
            .to_string();
    }

    let requested_kind = normalize_endpoint_kind(Some(&endpoint_kind_form));
    let refresh_mode = normalize_refresh_mode(Some(&model_refresh_mode_form), &requested_kind);
    let refresh_interval = parse_positive_int(Some(&model_refresh_interval_form), 30, 86400);
    let refresh_timeout = parse_positive_int(Some(&model_refresh_timeout_form), 1, 60);
    let require_model_list = truthy(Some(&require_models));
    // Proxy/API endpoints always probe at setup so the admin can intentionally
    // import a large model list (with the long explicit timeout), even when the
    // form would otherwise skip probing.
    let should_probe = require_model_list
        || requested_kind == "api"
        || requested_kind == "proxy"
        || !truthy(Some(&skip_probe));
    let explicit_timeout = explicit_model_list_timeout(&base_url, &requested_kind, refresh_timeout);

    let api_key_opt = if api_key.trim().is_empty() {
        None
    } else {
        Some(api_key.trim().to_string())
    };

    // Dedupe: if an endpoint with the same base_url already exists and is reachable
    // by the caller (shared or owned by them), return it instead of creating a
    // duplicate row.
    let caller = {
        let u = current_user_or_empty(user.as_ref().map(|Extension(u)| u));
        if u.is_empty() {
            None
        } else {
            Some(u)
        }
    };
    if let Some(existing) = dedup_lookup_endpoint(&base_url, caller.as_deref()) {
        let existing_view = existing.as_endpoint_view();
        let mut changed = false;
        // Mutable working copies of the columns this branch may update.
        let mut existing_pinned_raw = existing.pinned_models.clone();
        let mut existing_kind_db = existing.endpoint_kind.clone();
        let mut existing_mode_db = existing.model_refresh_mode.clone();
        let mut existing_interval_db = existing.model_refresh_interval;
        let mut existing_timeout_db = existing.model_refresh_timeout;
        let mut existing_api_key_plain = existing.api_key.clone();
        let mut existing_api_key_enc: Option<String> = existing
            .api_key
            .as_deref()
            .filter(|k| !k.is_empty())
            .map(crate::src::secret_storage::encrypt);
        let mut existing_cached_db = existing.cached_models.clone();

        // Persist any incoming pinned IDs. An empty/omitted form field must not
        // wipe previously pinned IDs.
        let incoming_pinned = normalize_model_ids(&Value::String(pinned_models_raw.clone()));
        if !incoming_pinned.is_empty() {
            let prev = normalize_model_ids_opt(existing.pinned_models.as_deref());
            let merged = merge_model_ids(&[&prev, &incoming_pinned]);
            existing_pinned_raw = if merged.is_empty() {
                None
            } else {
                Some(serde_json::to_string(&merged).unwrap_or_else(|_| "[]".to_string()))
            };
            changed = true;
        }
        // Kind to use when probing the existing row.
        let existing_kind_for_probe = if requested_kind != "auto" {
            requested_kind.clone()
        } else {
            effective_endpoint_kind(&existing_view, &base_url)
        };
        // Only set kind when the caller is explicit and the row is still "auto".
        if requested_kind != "auto" && endpoint_kind_of(&existing_view) == "auto" {
            existing_kind_db = Some(requested_kind.clone());
            changed = true;
        }
        // Set refresh mode when the form provided one, or a proxy's stored mode
        // differs from the (default-derived) refresh_mode.
        if !model_refresh_mode_form.is_empty()
            || (requested_kind == "proxy"
                && endpoint_refresh_mode(&existing_view, Some(&requested_kind)) != refresh_mode)
        {
            existing_mode_db = Some(refresh_mode.clone());
            changed = true;
        }
        if let Some(iv) = refresh_interval {
            existing_interval_db = Some(iv);
            changed = true;
        }
        if let Some(to) = refresh_timeout {
            existing_timeout_db = Some(to);
            changed = true;
        }
        // Fill in a missing key (never overwrite an existing one here).
        let incoming_key = api_key.trim();
        if !incoming_key.is_empty() && existing.api_key.as_deref().unwrap_or("").is_empty() {
            existing_api_key_plain = Some(incoming_key.to_string());
            existing_api_key_enc = Some(crate::src::secret_storage::encrypt(incoming_key));
            changed = true;
        }
        // Optionally (re)probe to refresh the cached model list — with the long
        // explicit timeout so a large proxy can finish.
        if should_probe {
            let probe_key = if !incoming_key.is_empty() {
                Some(incoming_key.to_string())
            } else {
                existing_api_key_plain.clone().filter(|k| !k.is_empty())
            };
            let to = explicit_model_list_timeout(&base_url, &existing_kind_for_probe, refresh_timeout);
            let probed = probe_endpoint(&base_url, probe_key.as_deref(), to.round() as u64).await;
            if !probed.is_empty() {
                existing_cached_db = Some(serde_json::to_string(&probed).unwrap_or_else(|_| "[]".to_string()));
                changed = true;
            }
        }
        if changed {
            let conn = session_local().map_err(db_err)?;
            conn.execute(
                "UPDATE model_endpoints SET pinned_models = ?2, endpoint_kind = ?3, \
                 model_refresh_mode = ?4, model_refresh_interval = ?5, model_refresh_timeout = ?6, \
                 api_key = ?7, cached_models = ?8 WHERE id = ?1",
                rusqlite::params![
                    existing.id,
                    existing_pinned_raw,
                    existing_kind_db,
                    existing_mode_db,
                    existing_interval_db,
                    existing_timeout_db,
                    existing_api_key_enc,
                    existing_cached_db,
                ],
            )
            .map_err(db_err)?;
            invalidate_models_cache();
            *LOCAL_PROBE_CACHE.lock().unwrap() = None;
        }
        // Re-read derived state from the (possibly) updated columns.
        let updated_view = Endpoint {
            cached_models: existing_cached_db.clone(),
            pinned_models: existing_pinned_raw.clone(),
            endpoint_kind: existing_kind_db.clone(),
            model_refresh_mode: existing_mode_db.clone(),
            model_refresh_interval: existing_interval_db,
            model_refresh_timeout: existing_timeout_db,
            api_key: existing_api_key_plain.clone(),
            ..existing_view
        };
        let existing_pinned = normalize_model_ids_opt(existing_pinned_raw.as_deref());
        let existing_kind = effective_endpoint_kind(&updated_view, &existing.base_url);
        return Ok(Json(json!({
            "id": existing.id,
            "name": existing.name,
            "base_url": existing.base_url,
            "models": visible_models(
                existing_cached_db.as_deref(),
                existing.hidden_models.as_deref(),
                existing_pinned_raw.as_deref(),
            ),
            "pinned_models": existing_pinned,
            "online": true,
            "status": "online",
            "existing": true,
            "endpoint_kind": existing_kind,
            "category": classify_endpoint_kind(&existing.base_url, &existing_kind),
        }))
        .into_response());
    }

    // Model list fetch with the explicit (per-kind) timeout — a proxy/API gets a
    // 30s budget so a large catalog can be imported intentionally; locals stay tight.
    let model_ids: Vec<String> = if should_probe {
        probe_endpoint(&base_url, api_key_opt.as_deref(), explicit_timeout.round() as u64).await
    } else {
        Vec::new()
    };
    let mut ping = PingResult::new(false, None);
    if (should_probe || requested_kind == "api" || requested_kind == "proxy") && model_ids.is_empty() {
        // ping cap stays short (min(explicit_timeout, 2.0)) — reachability only.
        let ping_to = explicit_timeout.min(2.0);
        ping = ping_endpoint(&base_url, api_key_opt.as_deref(), (ping_to * 1000.0).round() as u64).await;
    }
    if require_model_list && model_ids.is_empty() {
        return Err(HttpException::new(
            400,
            model_endpoint_error_message(&base_url, Some(&ping)),
        ));
    }

    // ep_id = str(uuid.uuid4())[:8]
    let ep_id: String = uuid::Uuid::new_v4().simple().to_string().chars().take(8).collect();

    // _st = True/False/None from supports_tools
    let st_raw = supports_tools.trim().to_lowercase();
    let st: Option<bool> = if matches!(st_raw.as_str(), "true" | "1" | "yes") {
        Some(true)
    } else if matches!(st_raw.as_str(), "false" | "0" | "no") {
        Some(false)
    } else {
        None
    };
    // _shared_flag; _owner_val = None if shared else (get_current_user(request) or None)
    let shared_flag = matches!(shared.trim().to_lowercase().as_str(), "true" | "1" | "yes");
    let owner_val: Option<String> = if shared_flag {
        None
    } else {
        let u = current_user_or_empty(user.as_ref().map(|Extension(u)| u));
        if u.is_empty() {
            None
        } else {
            Some(u)
        }
    };

    let name_trim = name.trim().to_string();
    let mtype = {
        let mt = model_type.trim();
        if mt.is_empty() {
            "llm".to_string()
        } else {
            mt.to_string()
        }
    };
    let pinned = normalize_model_ids(&Value::String(pinned_models_raw.clone()));
    let enc_key = api_key_opt
        .as_deref()
        .filter(|k| !k.is_empty())
        .map(crate::src::secret_storage::encrypt);
    let cached = if model_ids.is_empty() {
        None
    } else {
        Some(serde_json::to_string(&model_ids).unwrap_or_else(|_| "[]".to_string()))
    };
    let pinned_db = if pinned.is_empty() {
        None
    } else {
        Some(serde_json::to_string(&pinned).unwrap_or_else(|_| "[]".to_string()))
    };

    {
        let conn = session_local().map_err(db_err)?;
        let now = crate::pydatetime::utcnow_naive_iso();
        let st_val: Option<i64> = st.map(|b| if b { 1 } else { 0 });
        conn.execute(
            "INSERT INTO model_endpoints \
               (id, name, base_url, api_key, is_enabled, model_type, endpoint_kind, \
                model_refresh_mode, model_refresh_interval, model_refresh_timeout, \
                cached_models, pinned_models, supports_tools, owner, created_at, updated_at) \
             VALUES (?1, ?2, ?3, ?4, 1, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?14)",
            rusqlite::params![
                ep_id,
                name_trim,
                base_url,
                enc_key,
                mtype,
                requested_kind,
                refresh_mode,
                refresh_interval,
                refresh_timeout,
                cached,
                pinned_db,
                st_val,
                owner_val,
                now
            ],
        )
        .map_err(db_err)?;

        // Auto-set as default chat endpoint if none configured yet. Seed the first
        // CHAT model (not raw model_ids[0]) so we don't pin the global default to an
        // embedding/tts/etc. entry a provider happens to list first.
        let mut settings = crate::src::settings::load_settings();
        let has_default = settings
            .get("default_endpoint_id")
            .map(|v| !v.is_null() && v.as_str().map(|s| !s.is_empty()).unwrap_or(true))
            .unwrap_or(false);
        if !has_default {
            settings.insert("default_endpoint_id".to_string(), json!(ep_id));
            settings.insert(
                "default_model".to_string(),
                json!(crate::src::endpoint_resolver::_first_chat_model(&model_ids).unwrap_or_default()),
            );
            let _ = crate::src::settings::save_settings(&settings);
        }
        invalidate_models_cache();
        *LOCAL_PROBE_CACHE.lock().unwrap() = None;
    }

    // Return immediately — probing happens via the separate /probe SSE endpoint.
    let models_out = merge_model_ids(&[&model_ids, &pinned]);
    let online = !model_ids.is_empty() || !pinned.is_empty() || ping.reachable;
    let status = if !model_ids.is_empty() || !pinned.is_empty() {
        "online"
    } else if ping.reachable {
        "empty"
    } else {
        "offline"
    };
    Ok(Json(json!({
        "id": ep_id,
        "name": name_trim,
        "base_url": base_url,
        "models": models_out,
        "pinned_models": pinned,
        "online": online,
        "status": status,
        "ping_error": ping.error,
        "endpoint_kind": requested_kind,
        "category": classify_endpoint_kind(&base_url, &requested_kind),
    }))
    .into_response())
}

/// Dedup projection for `create_model_endpoint` — find an existing endpoint with
/// the same `base_url` reachable by the caller (shared null-owner OR owned by
/// `caller`), preferring an owned row over a shared one (`ORDER BY owner DESC`,
/// which puts non-NULL before NULL in SQLite).
struct DedupEndpoint {
    id: String,
    name: String,
    base_url: String,
    cached_models: Option<String>,
    hidden_models: Option<String>,
    pinned_models: Option<String>,
    /// DECRYPTED api_key (NULL/"" → None), like [`Endpoint::api_key`].
    api_key: Option<String>,
    endpoint_kind: Option<String>,
    model_refresh_mode: Option<String>,
    model_refresh_interval: Option<i64>,
    model_refresh_timeout: Option<i64>,
}

impl DedupEndpoint {
    /// Build a thin [`Endpoint`] view for the `_endpoint_kind` / `_effective_endpoint_kind`
    /// / `_endpoint_refresh_mode` helpers, which read only kind/refresh/api_key fields.
    fn as_endpoint_view(&self) -> Endpoint {
        Endpoint {
            id: self.id.clone(),
            name: self.name.clone(),
            base_url: self.base_url.clone(),
            api_key: self.api_key.clone(),
            is_enabled: true,
            hidden_models: self.hidden_models.clone(),
            cached_models: self.cached_models.clone(),
            model_type: None,
            supports_tools: None,
            pinned_models: self.pinned_models.clone(),
            endpoint_kind: self.endpoint_kind.clone(),
            model_refresh_mode: self.model_refresh_mode.clone(),
            model_refresh_interval: self.model_refresh_interval,
            model_refresh_timeout: self.model_refresh_timeout,
            updated_at_ts: 0.0,
            created_at_ts: 0.0,
        }
    }
}

const DEDUP_COLS: &str = "id, name, base_url, cached_models, hidden_models, pinned_models, \
     api_key, endpoint_kind, model_refresh_mode, model_refresh_interval, model_refresh_timeout";

fn dedup_lookup_endpoint(base_url: &str, caller: Option<&str>) -> Option<DedupEndpoint> {
    let conn = session_local().ok()?;
    let map = |r: &rusqlite::Row| -> rusqlite::Result<DedupEndpoint> {
        Ok(DedupEndpoint {
            id: r.get(0)?,
            name: r.get(1)?,
            base_url: r.get(2)?,
            cached_models: r.get(3)?,
            hidden_models: r.get(4)?,
            pinned_models: r.get(5)?,
            api_key: decrypt_key(r.get(6)?),
            endpoint_kind: r.get(7)?,
            model_refresh_mode: r.get(8)?,
            model_refresh_interval: r.get(9)?,
            model_refresh_timeout: r.get(10)?,
        })
    };
    // (owner IS NULL) OR (owner == caller). When caller is None, the second clause
    // is `owner IS NULL` too (SQLAlchemy `owner == None` → `owner IS NULL`).
    match caller {
        Some(c) => conn
            .query_row(
                &format!(
                    "SELECT {DEDUP_COLS} FROM model_endpoints \
                     WHERE base_url = ?1 AND (owner IS NULL OR owner = ?2) \
                     ORDER BY owner DESC LIMIT 1"
                ),
                rusqlite::params![base_url, c],
                map,
            )
            .optional()
            .ok()?,
        None => conn
            .query_row(
                &format!(
                    "SELECT {DEDUP_COLS} FROM model_endpoints \
                     WHERE base_url = ?1 AND owner IS NULL \
                     ORDER BY owner DESC LIMIT 1"
                ),
                rusqlite::params![base_url],
                map,
            )
            .optional()
            .ok()?,
    }
}

/// `@router.post("/model-endpoints/test")` — `def test_model_endpoint(...)`. Probe a
/// base URL without persisting anything; report reachability + discovered models.
async fn test_model_endpoint(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    mp: axum::extract::Multipart,
) -> Result<Response, HttpException> {
    require_admin(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    let f = parse_form(mp).await;
    // base_url: str = Form(...) — missing -> 422
    let base_url_raw = match f.get("base_url") {
        Some(v) => v.clone(),
        None => return Err(missing_form_field("base_url")),
    };
    let api_key = form_get(&f, "api_key", "");
    let endpoint_kind_form = form_get(&f, "endpoint_kind", "auto");
    let model_refresh_timeout_form = form_get(&f, "model_refresh_timeout", "");

    let mut base_url = normalize_base(Some(&base_url_raw));
    if base_url.is_empty() {
        return Err(HttpException::new(400, "Base URL is required"));
    }
    base_url = resolve_url(&base_url);
    base_url = rewrite_loopback_for_docker(&base_url, false);
    // Use the explicit per-kind model-list timeout so a manual Test of a large
    // proxy gets the long budget (a2e691d).
    let requested_kind = normalize_endpoint_kind(Some(&endpoint_kind_form));
    let configured_timeout = parse_positive_int(Some(&model_refresh_timeout_form), 1, 60);
    let probe_timeout = explicit_model_list_timeout(&base_url, &requested_kind, configured_timeout);
    let api_key_opt = if api_key.trim().is_empty() {
        None
    } else {
        Some(api_key.trim().to_string())
    };
    let models = probe_endpoint(&base_url, api_key_opt.as_deref(), probe_timeout.round() as u64).await;
    let ping = if !models.is_empty() {
        PingResult::new(true, None)
    } else {
        // ping cap stays short (min(probe_timeout, 2.0)).
        let ping_to = probe_timeout.min(2.0);
        ping_endpoint(&base_url, api_key_opt.as_deref(), (ping_to * 1000.0).round() as u64).await
    };
    let status = if !models.is_empty() {
        "online"
    } else if ping.reachable {
        "empty"
    } else {
        "offline"
    };
    Ok(Json(json!({
        "base_url": base_url,
        "online": !models.is_empty() || ping.reachable,
        "status": status,
        "ping_error": ping.error,
        "models": models,
        "count": models.len(),
        "endpoint_kind": requested_kind,
        "category": classify_endpoint_kind(&base_url, &requested_kind),
    }))
    .into_response())
}

/// `@router.get("/model-endpoints/{ep_id}/probe")` — re-probe all models on an
/// endpoint. Updates hidden_models and streams SSE results.
async fn probe_endpoint_models(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Path(ep_id): Path<String>,
) -> Result<Response, HttpException> {
    require_admin(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    // ep = db.query(...).filter(id == ep_id).first(); if not ep: 404
    let ep = endpoint_by_id(&ep_id).ok_or_else(|| HttpException::new(404, "Endpoint not found"))?;
    let ep_name = ep.name.clone();
    let api_key = ep.api_key.clone();

    let base = normalize_base(Some(&ep.base_url));
    let all_models = probe_endpoint(&base, api_key.as_deref(), 5).await;
    let chat_models: Vec<String> = all_models.iter().filter(|m| is_chat_model(m)).cloned().collect();
    let skipped = all_models.len() - chat_models.len();

    let ep_id_for_stream = ep_id.clone();
    let sse = async_stream::stream! {
        yield sse_bytes(&format!("data: {}\n\n", json!({"type": "probe_start", "endpoint": ep_name, "model_count": chat_models.len(), "skipped": skipped})));
        let mut failed: Vec<String> = Vec::new();
        let mut ok_count: i64 = 0;
        for mid in &chat_models {
            let mut result = probe_single_model(&base, api_key.as_deref(), mid, 8, false).await;
            result.insert("model".to_string(), json!(mid));
            result.insert("type".to_string(), json!("probe_result"));
            result.insert("endpoint".to_string(), json!(ep_name));
            if result.get("status").and_then(|s| s.as_str()) == Some("ok") {
                ok_count += 1;
            } else {
                failed.push(mid.clone());
            }
            yield sse_bytes(&format!("data: {}\n\n", Value::Object(result)));
        }

        // Update hidden_models and cached_models in DB. a2e691d: only overwrite
        // cached_models when the probe found models — never clear a usable cached
        // list because a probe momentarily failed.
        if let Ok(conn) = session_local() {
            let hidden_val: Option<String> = if failed.is_empty() {
                None
            } else {
                Some(serde_json::to_string(&failed).unwrap_or_else(|_| "[]".to_string()))
            };
            let _ = conn.execute(
                "UPDATE model_endpoints SET hidden_models = ?2 WHERE id = ?1",
                rusqlite::params![ep_id_for_stream, hidden_val],
            );
            if !all_models.is_empty() {
                let cached_val = serde_json::to_string(&all_models).unwrap_or_else(|_| "[]".to_string());
                let _ = conn.execute(
                    "UPDATE model_endpoints SET cached_models = ?2 WHERE id = ?1",
                    rusqlite::params![ep_id_for_stream, cached_val],
                );
            }
        }
        invalidate_models_cache();

        yield sse_bytes(&format!("data: {}\n\n", json!({"type": "probe_done", "total": all_models.len(), "ok": ok_count, "hidden": failed.len()})));
    };
    Ok(stream_response(sse))
}

/// `@router.get("/model-endpoints/{ep_id}/models")` — list all discovered models
/// for an endpoint with hidden/visible state.
///
/// a2e691d made this cached-first: a normal open serves the persisted cached list
/// (no blocking probe). Only `?refresh=true` triggers a live `_probe_endpoint`,
/// with the longer `_manual_refresh_timeout` so a large proxy can finish. A failed
/// refresh keeps the cached list and reports the failure via response headers.
async fn list_endpoint_models(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Path(ep_id): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    require_admin(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    // refresh: bool = False (FastAPI lax bool coercion).
    let refresh = query_bool(&q, "refresh", false)?;
    // refresh_timeout: Optional[int] = Query(None, ge=1, le=60).
    let refresh_timeout = query_ranged_int_opt(&q, "refresh_timeout", 1, 60)?;

    let ep = endpoint_by_id(&ep_id).ok_or_else(|| HttpException::new(404, "Endpoint not found"))?;
    let hidden: HashSet<String> = hidden_model_ids(&ep);
    // Cached-first: serve the persisted list unless an explicit refresh is asked.
    let mut all_models = cached_model_ids(&ep);

    // Response headers set on the refresh path (X-Model-Refresh-*).
    let mut extra_headers: Vec<(&'static str, String)> = Vec::new();
    if refresh {
        let base = normalize_base(Some(&ep.base_url));
        let kind = effective_endpoint_kind(&ep, &base);
        let category = classify_endpoint_kind(&base, &kind);
        let timeout = manual_refresh_timeout(&ep, category, refresh_timeout);
        // _probe_endpoint never raises here; an empty result models the Python
        // try/except fallthrough (`probed = []`).
        let probed = probe_endpoint(&base, ep.api_key.as_deref(), timeout.round() as u64).await;
        if !probed.is_empty() {
            all_models = probed.clone();
            if let Ok(conn) = session_local() {
                let cached = serde_json::to_string(&all_models).unwrap_or_else(|_| "[]".to_string());
                let _ = conn.execute(
                    "UPDATE model_endpoints SET cached_models = ?2 WHERE id = ?1",
                    rusqlite::params![ep_id, cached],
                );
            }
            invalidate_models_cache();
            extra_headers.push(("X-Model-Refresh-Status", "refreshed".to_string()));
            extra_headers.push(("X-Model-Refresh-Count", probed.len().to_string()));
        } else {
            extra_headers.push(("X-Model-Refresh-Status", "failed".to_string()));
            extra_headers.push((
                "X-Model-Refresh-Warning",
                "Model refresh failed or returned no models; kept cached models.".to_string(),
            ));
        }
    }

    let pinned = normalize_model_ids_opt(ep.pinned_models.as_deref());
    let pinned_set: HashSet<String> = pinned.iter().cloned().collect();
    let merged = merge_model_ids(&[&all_models, &pinned]);
    let out: Vec<Value> = merged
        .iter()
        .map(|m| {
            json!({
                "id": m,
                "display": last_segment(m),
                "is_hidden": hidden.contains(m),
                "is_pinned": pinned_set.contains(m),
            })
        })
        .collect();
    let mut response = Json(Value::Array(out)).into_response();
    for (name, value) in extra_headers {
        if let Ok(hv) = axum::http::HeaderValue::from_str(&value) {
            response.headers_mut().insert(name, hv);
        }
    }
    Ok(response)
}

/// `@router.patch("/model-endpoints/{ep_id}/models")` — bulk update hidden and/or
/// pinned model lists. Body: `{"hidden": [...], "pinned_models": [...]}` — each key
/// updated only when present so callers can patch one list without clobbering the
/// other.
async fn update_hidden_models(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Path(ep_id): Path<String>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    require_admin(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    // ep = ...first(); if not ep: 404 (the lookup happens BEFORE reading the body)
    let ep = endpoint_by_id(&ep_id).ok_or_else(|| HttpException::new(404, "Endpoint not found"))?;
    // body = await request.json()
    let body: Value = serde_json::from_slice(&raw_body).unwrap_or(Value::Null);
    // if not isinstance(body, dict): 400
    let body_obj = match body.as_object() {
        Some(o) => o,
        None => return Err(HttpException::new(400, "Body must be a JSON object")),
    };

    // The new column values default to the existing (only updated keys change).
    let mut hidden_db: Option<String> = ep.hidden_models.clone();
    let mut pinned_db: Option<String> = ep.pinned_models.clone();

    if body_obj.contains_key("hidden") {
        let hidden_val = body_obj.get("hidden").cloned().unwrap_or(Value::Null);
        // if not isinstance(hidden, list): 400
        let hidden_arr = match hidden_val.as_array() {
            Some(a) => a.clone(),
            None => return Err(HttpException::new(400, "hidden must be a list of model IDs")),
        };
        hidden_db = if hidden_arr.is_empty() {
            None
        } else {
            Some(serde_json::to_string(&hidden_arr).unwrap_or_else(|_| "[]".to_string()))
        };
    }
    // Accept either "pinned" or "pinned_models" for the manual IDs list.
    if body_obj.contains_key("pinned_models") || body_obj.contains_key("pinned") {
        let raw = body_obj
            .get("pinned_models")
            .or_else(|| body_obj.get("pinned"))
            .cloned()
            .unwrap_or(Value::Null);
        let pinned = normalize_model_ids(&raw);
        pinned_db = if pinned.is_empty() {
            None
        } else {
            Some(serde_json::to_string(&pinned).unwrap_or_else(|_| "[]".to_string()))
        };
    }

    let conn = session_local().map_err(db_err)?;
    conn.execute(
        "UPDATE model_endpoints SET hidden_models = ?2, pinned_models = ?3 WHERE id = ?1",
        rusqlite::params![ep_id, hidden_db, pinned_db],
    )
    .map_err(db_err)?;
    invalidate_models_cache();
    // hidden_count/pinned_count re-read from the persisted columns.
    let hidden_count = parse_str_list(&hidden_db).len();
    let pinned_count = parse_str_list(&pinned_db).len();
    Ok(Json(json!({"id": ep_id, "hidden_count": hidden_count, "pinned_count": pinned_count})).into_response())
}

/// `@router.get("/default-chat")` — `def get_default_chat(request)`. Resolve the
/// default endpoint + model from the CALLER's per-user prefs ONLY (admins use the
/// global defaults).
async fn get_default_chat(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    // _user = get_current_user(request) or ""
    let user = current_user_or_empty(user.as_ref().map(|Extension(u)| u));
    let settings = crate::src::settings::load_settings();
    // _is_admin = bool(auth_mgr.is_admin(_user)) when _user
    let is_admin = if !user.is_empty() {
        state.auth.is_admin(&user)
    } else {
        false
    };

    let mut ep_id: String;
    let mut model: String;
    let fallbacks: Vec<Value>;
    if !user.is_empty() && !is_admin {
        // _user_prefs = _load_for_user(_user) or {}
        let prefs = crate::routes::prefs_store::load_for_user(Some(&user));
        ep_id = prefs
            .get("default_endpoint_id")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        model = prefs
            .get("default_model")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        fallbacks = prefs
            .get("default_model_fallbacks")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
    } else {
        ep_id = settings
            .get("default_endpoint_id")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        model = settings
            .get("default_model")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        fallbacks = settings
            .get("default_model_fallbacks")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
    }
    let _ = &mut ep_id; // ep_id mutated only via lookups; keep parity naming

    // ep = None; resolve with the owner-scope rule.
    let scoped = !user.is_empty() && !is_admin;
    let mut ep: Option<DefaultChatEndpoint> = None;
    if !ep_id.is_empty() {
        ep = lookup_default_endpoint(&ep_id, scoped.then_some(user.as_str()));
    }
    // Configured fallback chain.
    if ep.is_none() {
        for entry in &fallbacks {
            let obj = match entry.as_object() {
                Some(o) => o,
                None => continue,
            };
            let fid = obj
                .get("endpoint_id")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .trim()
                .to_string();
            if fid.is_empty() {
                continue;
            }
            if let Some(cand) = lookup_default_endpoint(&fid, scoped.then_some(user.as_str())) {
                ep = Some(cand);
                // Use the fallback entry's model. Reset even when empty.
                model = obj
                    .get("model")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .trim()
                    .to_string();
                break;
            }
        }
    }
    // Last resort: first enabled endpoint owned by THIS user (no shared).
    if ep.is_none() {
        ep = first_enabled_default(scoped.then_some(user.as_str()));
    }
    let ep = match ep {
        Some(e) => e,
        None => {
            return Ok(Json(json!({"endpoint_id": "", "endpoint_url": "", "model": ""})).into_response());
        }
    };
    let base = normalize_base(Some(&ep.base_url));
    let chat_url = build_chat_url(&base);
    // if not model and (ep.cached_models or ep.pinned_models): visible = ...; model = visible[0]
    if model.is_empty() && (ep.cached_models.is_some() || ep.pinned_models.is_some()) {
        let visible = visible_models(
            ep.cached_models.as_deref(),
            ep.hidden_models.as_deref(),
            ep.pinned_models.as_deref(),
        );
        if let Some(first) = visible.first() {
            model = first.clone();
        }
    }
    Ok(Json(json!({"endpoint_id": ep.id, "endpoint_url": chat_url, "model": model})).into_response())
}

/// Minimal projection for `/default-chat`'s endpoint resolution.
struct DefaultChatEndpoint {
    id: String,
    base_url: String,
    cached_models: Option<String>,
    hidden_models: Option<String>,
    pinned_models: Option<String>,
}

/// `db.query(ModelEndpoint).filter(id == ep_id, is_enabled == True)[+owner_filter].first()`.
/// `scoped_owner` is `Some(user)` when the owner-scope rule applies (regular,
/// non-admin user), re-expressing `owner_filter(q, ModelEndpoint, user)` as
/// `(owner = ? OR owner IS NULL)`.
fn lookup_default_endpoint(ep_id: &str, scoped_owner: Option<&str>) -> Option<DefaultChatEndpoint> {
    let conn = session_local().ok()?;
    match scoped_owner {
        Some(owner) => conn
            .query_row(
                "SELECT id, base_url, cached_models, hidden_models, pinned_models FROM model_endpoints \
                 WHERE id = ?1 AND is_enabled = 1 AND (owner = ?2 OR owner IS NULL)",
                rusqlite::params![ep_id, owner],
                map_default_chat_row,
            )
            .optional()
            .ok()?,
        None => conn
            .query_row(
                "SELECT id, base_url, cached_models, hidden_models, pinned_models FROM model_endpoints \
                 WHERE id = ?1 AND is_enabled = 1",
                rusqlite::params![ep_id],
                map_default_chat_row,
            )
            .optional()
            .ok()?,
    }
}

/// `db.query(ModelEndpoint).filter(is_enabled == True)[+owner_filter(include_shared=False)].first()`.
/// `scoped_owner` is `Some(user)` for the regular-user last-resort, where
/// `include_shared=False` means `owner = ?` (NO null-owner fallback).
fn first_enabled_default(scoped_owner: Option<&str>) -> Option<DefaultChatEndpoint> {
    let conn = session_local().ok()?;
    match scoped_owner {
        Some(owner) => conn
            .query_row(
                "SELECT id, base_url, cached_models, hidden_models, pinned_models FROM model_endpoints \
                 WHERE is_enabled = 1 AND owner = ?1 LIMIT 1",
                rusqlite::params![owner],
                map_default_chat_row,
            )
            .optional()
            .ok()?,
        None => conn
            .query_row(
                "SELECT id, base_url, cached_models, hidden_models, pinned_models FROM model_endpoints \
                 WHERE is_enabled = 1 LIMIT 1",
                [],
                map_default_chat_row,
            )
            .optional()
            .ok()?,
    }
}

fn map_default_chat_row(r: &rusqlite::Row) -> rusqlite::Result<DefaultChatEndpoint> {
    Ok(DefaultChatEndpoint {
        id: r.get(0)?,
        base_url: r.get(1)?,
        cached_models: r.get(2)?,
        hidden_models: r.get(3)?,
        pinned_models: r.get(4)?,
    })
}

/// `@router.patch("/model-endpoints/{ep_id}")` — `def toggle_model_endpoint(...)`.
/// Optional JSON body for field-targeted updates; no body → toggle is_enabled.
async fn toggle_model_endpoint(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Path(ep_id): Path<String>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    require_admin(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    // body = await request.json() if content-length > 0 else {}
    let content_length: i64 = headers
        .get("content-length")
        .and_then(|v| v.to_str().ok())
        .and_then(|s| s.parse::<i64>().ok())
        .unwrap_or(0);
    let body: Map<String, Value> = if content_length > 0 {
        match serde_json::from_slice::<Value>(&raw_body) {
            Ok(Value::Object(m)) => m,
            _ => Map::new(),
        }
    } else {
        Map::new()
    };

    // ep = ...first(); if not ep: 404
    let ep = endpoint_by_id(&ep_id).ok_or_else(|| HttpException::new(404, "Endpoint not found"))?;

    // Compute the new values.
    let mut new_is_enabled = ep.is_enabled;
    let mut new_supports_tools = ep.supports_tools;
    let mut new_name = ep.name.clone();
    let mut new_model_type = ep.model_type.clone().unwrap_or_default();
    // pinned_models / api_key / base_url default to the existing stored values; each
    // is only changed when its key is present in the body.
    let mut new_pinned_db = ep.pinned_models.clone();
    // The api_key column holds the ENCRYPTED value; ep.api_key here is decrypted.
    let mut new_api_key_db: Option<String> = ep
        .api_key
        .as_deref()
        .filter(|k| !k.is_empty())
        .map(crate::src::secret_storage::encrypt);
    let mut new_base_url = ep.base_url.clone();
    // Proxy/API classification + refresh policy columns (a2e691d) default to the
    // existing stored values; each changes only when its key is present.
    let mut new_endpoint_kind_db = ep.endpoint_kind.clone();
    let mut new_refresh_mode_db = ep.model_refresh_mode.clone();
    let mut new_refresh_interval_db = ep.model_refresh_interval;
    let mut new_refresh_timeout_db = ep.model_refresh_timeout;

    if !body.is_empty() {
        if let Some(v) = body.get("supports_tools") {
            // ep.supports_tools = bool(v) if v in (True, False, "true", "false", 1, 0) else None
            // The `in` membership test admits the literal value, then bool() is applied.
            // In Python any non-empty string is truthy, so bool("false") == True.
            new_supports_tools = match v {
                Value::Bool(b) => Some(*b),
                // bool("true") == True, bool("false") == True (non-empty string is truthy)
                Value::String(s) if s == "true" || s == "false" => Some(true),
                Value::Number(n) if n.as_i64() == Some(1) => Some(true),
                Value::Number(n) if n.as_i64() == Some(0) => Some(false),
                _ => None,
            };
        }
        if let Some(v) = body.get("is_enabled") {
            new_is_enabled = value_truthy(v);
        }
        if let Some(Value::String(s)) = body.get("name") {
            // ep.name = body["name"].strip() or ep.name
            let trimmed = s.trim();
            new_name = if trimmed.is_empty() {
                ep.name.clone()
            } else {
                trimmed.to_string()
            };
        }
        if let Some(Value::String(s)) = body.get("model_type") {
            let trimmed = s.trim();
            new_model_type = if trimmed.is_empty() {
                ep.model_type.clone().unwrap_or_default()
            } else {
                trimmed.to_string()
            };
        }
        if let Some(v) = body.get("pinned_models") {
            let pinned = normalize_model_ids(v);
            new_pinned_db = if pinned.is_empty() {
                None
            } else {
                Some(serde_json::to_string(&pinned).unwrap_or_else(|_| "[]".to_string()))
            };
        }
        if body.contains_key("endpoint_kind") {
            let v = body.get("endpoint_kind").and_then(|v| v.as_str());
            new_endpoint_kind_db = Some(normalize_endpoint_kind(v));
        }
        if body.contains_key("model_refresh_mode") {
            // _normalize_refresh_mode(body.get("model_refresh_mode"), _endpoint_kind(ep))
            // — uses the row's stored kind, which reflects any endpoint_kind just set
            // above (Python mutates ep.endpoint_kind in place).
            let v = body.get("model_refresh_mode").and_then(|v| v.as_str());
            let stored_kind = normalize_endpoint_kind(new_endpoint_kind_db.as_deref());
            new_refresh_mode_db = Some(normalize_refresh_mode(v, &stored_kind));
        }
        if body.contains_key("model_refresh_interval") {
            let v = body.get("model_refresh_interval").map(json_to_string);
            new_refresh_interval_db = parse_positive_int(v.as_deref(), 30, 86400);
        }
        if body.contains_key("model_refresh_timeout") {
            let v = body.get("model_refresh_timeout").map(json_to_string);
            new_refresh_timeout_db = parse_positive_int(v.as_deref(), 1, 60);
        }
        // Allow in-place API-key rotation / base-url correction without nuking
        // session state. Empty string means "clear it".
        if let Some(Value::String(s)) = body.get("api_key") {
            let new_key = s.trim();
            new_api_key_db = if new_key.is_empty() {
                None
            } else {
                Some(crate::src::secret_storage::encrypt(new_key))
            };
        }
        if let Some(Value::String(s)) = body.get("base_url") {
            // _new_base = body["base_url"].strip().rstrip("/"); strip suffixes; _normalize_base
            let mut nb = s.trim().trim_end_matches('/').to_string();
            for suffix in ["/models", "/chat/completions", "/completions", "/v1/messages"] {
                if nb.ends_with(suffix) {
                    nb = nb[..nb.len() - suffix.len()].trim_end_matches('/').to_string();
                }
            }
            nb = normalize_base(Some(&nb));
            if !nb.is_empty() {
                new_base_url = nb;
            }
        }
    } else {
        // ep.is_enabled = not ep.is_enabled
        new_is_enabled = !ep.is_enabled;
    }

    let conn = session_local().map_err(db_err)?;
    let st_val: Option<i64> = new_supports_tools.map(|b| if b { 1 } else { 0 });
    conn.execute(
        "UPDATE model_endpoints SET is_enabled = ?2, supports_tools = ?3, name = ?4, \
         model_type = ?5, pinned_models = ?6, api_key = ?7, base_url = ?8, \
         endpoint_kind = ?9, model_refresh_mode = ?10, model_refresh_interval = ?11, \
         model_refresh_timeout = ?12 WHERE id = ?1",
        rusqlite::params![
            ep_id,
            if new_is_enabled { 1 } else { 0 },
            st_val,
            new_name,
            new_model_type,
            new_pinned_db,
            new_api_key_db,
            new_base_url,
            new_endpoint_kind_db,
            new_refresh_mode_db,
            new_refresh_interval_db,
            new_refresh_timeout_db,
        ],
    )
    .map_err(db_err)?;
    invalidate_models_cache();
    *LOCAL_PROBE_CACHE.lock().unwrap() = None;
    Ok(Json(json!({
        "id": ep_id,
        "is_enabled": new_is_enabled,
        "supports_tools": new_supports_tools,
        "name": new_name,
        "model_type": new_model_type,
        "base_url": new_base_url,
        "pinned_models": normalize_model_ids_opt(new_pinned_db.as_deref()),
        // getattr(ep, col, None) or "auto" — empty string also falls back.
        "endpoint_kind": new_endpoint_kind_db.as_deref().filter(|s| !s.is_empty()).unwrap_or("auto"),
        "model_refresh_mode": new_refresh_mode_db.as_deref().filter(|s| !s.is_empty()).unwrap_or("auto"),
        "model_refresh_interval": new_refresh_interval_db,
        "model_refresh_timeout": new_refresh_timeout_db,
    }))
    .into_response())
}

/// `str(raw).strip()` over a JSON value, for `_parse_positive_int(body.get(...))`.
/// Integers render without a decimal point; a string passes through; other JSON
/// renders close enough that `_parse_positive_int`'s `int(...)` parse still fails
/// (returning `None`), matching Python.
fn json_to_string(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Number(n) => n.to_string(),
        Value::Bool(b) => if *b { "True".to_string() } else { "False".to_string() },
        Value::Null => "None".to_string(),
        other => other.to_string(),
    }
}

/// Python `bool(v)` over a JSON value (used for `is_enabled`): None/false/0/""/
/// empty-collections are falsy, everything else truthy.
fn value_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `_settings_using_endpoint(ep_id)` — labels for settings (and fallback chains and
/// speech settings) that reference this endpoint.
fn settings_using_endpoint(ep_id: &str) -> Vec<String> {
    endpoint_settings_using_endpoint(&crate::src::settings::load_settings(), ep_id, true)
}

/// `_clear_settings_for_endpoint(ep_id)` — clear all settings (incl. fallback chains
/// and speech settings) that reference this endpoint. Returns the cleared labels.
fn clear_settings_for_endpoint(ep_id: &str) -> Vec<String> {
    let mut settings = crate::src::settings::load_settings();
    let cleared = clear_endpoint_settings_for_endpoint(&mut settings, ep_id, true);
    if !cleared.is_empty() {
        let _ = crate::src::settings::save_settings(&settings);
    }
    cleared
}

/// `_clear_user_prefs_for_endpoint(ep_id)` — clear per-user endpoint selections and
/// fallback chains. Returns the number of affected user pref sets.
fn clear_user_prefs_for_endpoint(ep_id: &str) -> i64 {
    let mut all_prefs = crate::routes::prefs_store::load();
    let cleared_users = clear_user_pref_endpoint_refs(&mut all_prefs, ep_id);
    if cleared_users > 0 {
        if let Err(e) = crate::routes::prefs_store::save(&all_prefs) {
            crate::pylog::warning(&format!("Failed to clear user prefs for endpoint {ep_id}: {e}"));
            return 0;
        }
    }
    cleared_users
}

/// `_session_uses_endpoint_url(session_url, base_url)` — True when a stored session's
/// endpoint URL refers to the endpoint being deleted.
fn session_uses_endpoint_url(session_url: &str, base_url: &str) -> bool {
    if session_url.is_empty() || base_url.is_empty() {
        return false;
    }
    let sess = session_url.trim_end_matches('/').to_string();
    let base = normalize_base(Some(base_url)).trim_end_matches('/').to_string();
    let variants: HashSet<String> = [
        base.clone(),
        format!("{base}/chat/completions"),
        build_chat_url(&base).trim_end_matches('/').to_string(),
    ]
    .into_iter()
    .collect();
    variants.contains(&sess) || sess.starts_with(&format!("{base}/"))
}

/// `_clear_sessions_for_endpoint(db, base_url)` — drop stored auth (`headers = {}`)
/// for DB session rows using an endpoint being deleted. Keeps endpoint URL + model
/// intact. Returns the number of rows touched.
fn clear_sessions_for_endpoint(conn: &rusqlite::Connection, base_url: &str) -> i64 {
    let mut cleared: i64 = 0;
    // rows = db.query(DbSession).filter(DbSession.endpoint_url.isnot(None)).all()
    let rows: Vec<(String, String)> = {
        let mut stmt = match conn
            .prepare("SELECT id, endpoint_url FROM sessions WHERE endpoint_url IS NOT NULL")
        {
            Ok(s) => s,
            Err(_) => return 0,
        };
        let mapped = stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)));
        match mapped {
            Ok(it) => it.filter_map(Result::ok).collect(),
            Err(_) => return 0,
        }
    };
    let now = crate::pydatetime::utcnow_naive_iso();
    for (id, endpoint_url) in rows {
        if session_uses_endpoint_url(&endpoint_url, base_url) {
            // row.headers = {}; row.updated_at = datetime.utcnow()
            let _ = conn.execute(
                "UPDATE sessions SET headers = '{}', updated_at = ?2 WHERE id = ?1",
                rusqlite::params![id, now],
            );
            cleared += 1;
        }
    }
    cleared
}

/// `_clear_loaded_sessions_for_endpoint(base_url)` — clear in-memory session auth for
/// any loaded session referencing the endpoint. Returns the number cleared.
fn clear_loaded_sessions_for_endpoint(base_url: &str) -> i64 {
    let manager = match crate::src::bg_monitor::get_session_manager() {
        Some(m) => m,
        None => return 0,
    };
    let mut cleared: i64 = 0;
    // for sess in list(manager.sessions.values()): if matches: sess.headers = {}
    let sessions = manager.get_sessions_for_user(None);
    for (id, sess) in sessions.iter() {
        if session_uses_endpoint_url(&sess.endpoint_url, base_url) {
            manager.with_session_mut(id, |s| s.headers = indexmap::IndexMap::new());
            cleared += 1;
        }
    }
    cleared
}

/// `@router.get("/model-endpoints/{ep_id}/dependents")` — settings that depend on
/// this endpoint.
async fn get_endpoint_dependents(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Path(ep_id): Path<String>,
) -> Result<Response, HttpException> {
    require_admin(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    Ok(Json(json!({"dependents": settings_using_endpoint(&ep_id)})).into_response())
}

/// `@router.delete("/model-endpoints/{ep_id}")` — `def delete_model_endpoint(...)`.
async fn delete_model_endpoint(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Path(ep_id): Path<String>,
) -> Result<Response, HttpException> {
    require_admin(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    // ep = ...first(); if not ep: 404
    let ep = endpoint_by_id(&ep_id).ok_or_else(|| HttpException::new(404, "Endpoint not found"))?;
    // Clean up any settings / per-user prefs / sessions that reference this endpoint.
    let cleared = clear_settings_for_endpoint(&ep_id);
    let cleared_user_preferences = clear_user_prefs_for_endpoint(&ep_id);
    let conn = session_local().map_err(db_err)?;
    let cleared_sessions = clear_sessions_for_endpoint(&conn, &ep.base_url);
    let cleared_loaded_sessions = clear_loaded_sessions_for_endpoint(&ep.base_url);
    conn.execute("DELETE FROM model_endpoints WHERE id = ?1", [&ep_id])
        .map_err(db_err)?;
    invalidate_models_cache();
    *LOCAL_PROBE_CACHE.lock().unwrap() = None;
    Ok(Json(json!({
        "deleted": true,
        "cleared_settings": cleared,
        "cleared_user_preferences": cleared_user_preferences,
        "cleared_sessions": cleared_sessions,
        "cleared_loaded_sessions": cleared_loaded_sessions,
    }))
    .into_response())
}

// ── Tool management ──

/// `@router.get("/tools")` — list all available tools with their enabled/disabled
/// status.
async fn list_tools() -> Result<Response, HttpException> {
    let settings = crate::src::settings::load_settings();
    // disabled = set(settings.get("disabled_tools", []))
    let disabled: HashSet<String> = settings
        .get("disabled_tools")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str().map(String::from))
                .collect()
        })
        .unwrap_or_default();
    // for tag in sorted(TOOL_TAGS): ...
    let mut tags: Vec<&str> = crate::src::agent_tools::TOOL_TAGS.iter().copied().collect();
    tags.sort_unstable();
    let tools: Vec<Value> = tags
        .iter()
        .map(|tag| json!({"id": tag, "enabled": !disabled.contains(*tag)}))
        .collect();
    Ok(Json(json!({"tools": tools})).into_response())
}

/// `@router.post("/tools")` — update which tools are disabled. Body model
/// `ToolsUpdate(disabled: list = [])`.
async fn update_tools(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Json(body): Json<Value>,
) -> Result<Response, HttpException> {
    require_admin(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    // body.disabled (defaults to [])
    let disabled = body
        .get("disabled")
        .cloned()
        .unwrap_or_else(|| Value::Array(vec![]));
    let mut settings = crate::src::settings::load_settings();
    settings.insert("disabled_tools".to_string(), disabled.clone());
    let _ = crate::src::settings::save_settings(&settings);
    Ok(Json(json!({"ok": true, "disabled": disabled})).into_response())
}

// ===========================================================================
// Small shared utilities
// ===========================================================================

/// `String -> Ok(Bytes)` for the SSE body stream (infallible).
fn sse_bytes(s: &str) -> Result<bytes::Bytes, std::convert::Infallible> {
    Ok(bytes::Bytes::from(s.to_owned()))
}

/// Build the `StreamingResponse(..., media_type="text/event-stream")` axum
/// equivalent for the probe SSE handlers.
fn stream_response<S>(sse: S) -> Response
where
    S: futures_util::Stream<Item = Result<bytes::Bytes, std::convert::Infallible>>
        + Send
        + 'static,
{
    Response::builder()
        .header(header::CONTENT_TYPE, "text/event-stream")
        .body(Body::from_stream(sse))
        .unwrap()
}

/// `req.headers.get(...)` style DB-error mapper → a 500 `HttpException` (Starlette
/// would surface an unhandled DB error as a 500). Logs the underlying error.
fn db_err(e: rusqlite::Error) -> HttpException {
    crate::pylog::error(&format!("model_routes DB error: {e}"));
    HttpException::new(500, "Database error")
}

/// FastAPI/Pydantic `422` for a missing required `Form(...)` field, rendered as
/// Starlette's `{"detail": [ {type, loc, msg, input} ]}`.
fn missing_form_field(name: &str) -> HttpException {
    HttpException::with_detail(
        422,
        json!([{
            "type": "missing",
            "loc": ["body", name],
            "msg": "Field required",
            "input": Value::Null,
        }]),
    )
}

/// `param: bool = <default>` — Pydantic v2 lax bool coercion of a query value
/// (mirrors the hwfit `query_bool`). Missing -> `default`; the truthy/falsy sets
/// coerce; anything else is a `422` `bool_parsing` error.
fn query_bool(q: &HashMap<String, String>, name: &str, default: bool) -> Result<bool, HttpException> {
    match q.get(name) {
        None => Ok(default),
        Some(raw) => {
            let v = raw.trim().to_lowercase();
            match v.as_str() {
                "1" | "on" | "t" | "true" | "y" | "yes" => Ok(true),
                "0" | "off" | "f" | "false" | "n" | "no" => Ok(false),
                _ => Err(HttpException::with_detail(
                    422,
                    json!([{
                        "type": "bool_parsing",
                        "loc": ["query", name],
                        "msg": "Input should be a valid boolean, unable to interpret input",
                        "input": raw,
                    }]),
                )),
            }
        }
    }
}

/// `param: Optional[int] = Query(None, ge=lo, le=hi)` — Pydantic v2 ranged optional
/// int. Missing → `None`; a non-integer is a `422` `int_parsing`; out-of-range is a
/// `422` `greater_than_equal` / `less_than_equal`.
fn query_ranged_int_opt(
    q: &HashMap<String, String>,
    name: &str,
    lo: i64,
    hi: i64,
) -> Result<Option<i64>, HttpException> {
    let raw = match q.get(name) {
        None => return Ok(None),
        Some(r) => r,
    };
    let parsed: i64 = match raw.trim().parse() {
        Ok(v) => v,
        Err(_) => {
            return Err(HttpException::with_detail(
                422,
                json!([{
                    "type": "int_parsing",
                    "loc": ["query", name],
                    "msg": "Input should be a valid integer, unable to parse string as an integer",
                    "input": raw,
                }]),
            ))
        }
    };
    if parsed < lo {
        return Err(HttpException::with_detail(
            422,
            json!([{
                "type": "greater_than_equal",
                "loc": ["query", name],
                "msg": format!("Input should be greater than or equal to {lo}"),
                "input": raw,
                "ctx": {"ge": lo},
            }]),
        ));
    }
    if parsed > hi {
        return Err(HttpException::with_detail(
            422,
            json!([{
                "type": "less_than_equal",
                "loc": ["query", name],
                "msg": format!("Input should be less than or equal to {hi}"),
                "input": raw,
                "ctx": {"le": hi},
            }]),
        ));
    }
    Ok(Some(parsed))
}

/// Collect all `multipart/form-data` (or `x-www-form-urlencoded`) fields into a
/// map (text values), like the inline `web/mod.rs::parse_form`.
async fn parse_form(mut mp: axum::extract::Multipart) -> HashMap<String, String> {
    let mut out = HashMap::new();
    while let Ok(Some(field)) = mp.next_field().await {
        let name = field.name().unwrap_or("").to_string();
        if name.is_empty() {
            continue;
        }
        if let Ok(text) = field.text().await {
            out.insert(name, text);
        }
    }
    out
}

/// `field: str = Form("<default>")` — a defaulted form field.
fn form_get(f: &HashMap<String, String>, name: &str, default: &str) -> String {
    f.get(name).cloned().unwrap_or_else(|| default.to_string())
}

// ===========================================================================
// Factory
// ===========================================================================

/// `setup_model_routes(model_discovery) -> APIRouter(prefix="/api")` — assemble the
/// router. The `model_discovery` arg is reached via `State<AppState>`, so this
/// takes no parameters. Routes mounted at their absolute `/api/...` paths (the
/// `prefix="/api"` + each `@router.<m>("<path>")` decorator), in declaration order.
pub fn setup_model_routes() -> Router<AppState> {
    Router::new()
        .route("/api/models", get(api_models))
        .route("/api/model-endpoints/probe-local", get(probe_local_endpoints))
        .route("/api/ping", get(ping_endpoints))
        .route("/api/probe-selected", post(probe_selected))
        .route("/api/probe", get(probe_models))
        .route("/api/providers", get(providers))
        .route("/api/discover", get(discover_local))
        .route("/api/model-endpoints", get(list_model_endpoints).post(create_model_endpoint))
        .route("/api/model-endpoints/test", post(test_model_endpoint))
        .route("/api/model-endpoints/:ep_id/probe", get(probe_endpoint_models))
        .route(
            "/api/model-endpoints/:ep_id/models",
            get(list_endpoint_models).patch(update_hidden_models),
        )
        .route("/api/default-chat", get(get_default_chat))
        .route("/api/model-endpoints/:ep_id", patch(toggle_model_endpoint).delete(delete_model_endpoint))
        .route("/api/model-endpoints/:ep_id/dependents", get(get_endpoint_dependents))
        .route("/api/tools", get(list_tools).post(update_tools))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn curate_models_no_list_returns_all_as_curated() {
        let ids = vec!["model-a".to_string(), "model-b".to_string()];
        let (curated, extra) = curate_models(&ids, Some("unknown-provider"));
        assert_eq!(curated, ids);
        assert!(extra.is_empty());
    }

    #[test]
    fn curate_models_openrouter_sentinel_returns_all() {
        let ids = vec!["x".to_string(), "y".to_string()];
        let (curated, extra) = curate_models(&ids, Some("openrouter"));
        assert_eq!(curated, ids);
        assert!(extra.is_empty());
    }

    #[test]
    fn curate_models_partitions_and_sorts_by_priority() {
        // openai curated order: gpt-5 (index 2) before gpt-4o (index 6).
        let ids = vec![
            "gpt-4o".to_string(),
            "gpt-5".to_string(),
            "some-random-model".to_string(),
        ];
        let (curated, extra) = curate_models(&ids, Some("openai"));
        assert_eq!(curated, vec!["gpt-5".to_string(), "gpt-4o".to_string()]);
        assert_eq!(extra, vec!["some-random-model".to_string()]);
    }

    #[test]
    fn best_match_idx_prefers_longest_entry() {
        let list = ["gpt-5", "gpt-5-pro"];
        // "gpt-5-pro" matches both "gpt-5" (prefix) and "gpt-5-pro" (exact); longest wins.
        assert_eq!(best_match_idx("gpt-5-pro", &list), 1);
        // "gpt-5-nano" only matches "gpt-5".
        assert_eq!(best_match_idx("gpt-5-nano", &list), 0);
        assert_eq!(best_match_idx("claude", &list), -1);
    }

    #[test]
    fn is_chat_model_filters_non_chat() {
        assert!(is_chat_model("gpt-4o"));
        assert!(is_chat_model("claude-sonnet-4-5"));
        assert!(!is_chat_model("dall-e-3"));
        assert!(!is_chat_model("tts-1"));
        assert!(!is_chat_model("whisper-1"));
        assert!(!is_chat_model("text-embedding-3-small"));
        assert!(!is_chat_model("gpt-4o-realtime-preview"));
        assert!(!is_chat_model("gpt-5-codex"));
        assert!(!is_chat_model("gpt-audio"));
        assert!(!is_chat_model("gpt-3.5-turbo-instruct"));
        // gpt-4o-audio-preview is chat (only gpt-audio* is excluded).
        assert!(is_chat_model("gpt-4o-audio-preview"));
    }

    #[test]
    fn match_provider_curated_url_substring_wins() {
        assert_eq!(
            match_provider_curated("https://api.deepseek.com/v1", Some("openai")),
            Some("deepseek".to_string())
        );
        assert_eq!(
            match_provider_curated("https://openrouter.ai/api/v1", Some("openai")),
            Some("openrouter".to_string())
        );
        // No URL match -> falls back to the raw provider.
        assert_eq!(
            match_provider_curated("https://my-host/v1", Some("openai")),
            Some("openai".to_string())
        );
        // provider=None and no URL match -> None.
        assert_eq!(match_provider_curated("https://my-host/v1", None), None);
    }

    #[test]
    fn classify_endpoint_local_vs_api() {
        assert_eq!(classify_endpoint("http://localhost:11434/v1"), "local");
        assert_eq!(classify_endpoint("http://127.0.0.1:8000"), "local");
        assert_eq!(classify_endpoint("http://192.168.1.50:1234/v1"), "local");
        assert_eq!(classify_endpoint("http://10.0.0.5:8080"), "local");
        // Tailscale CGNAT 100.64.0.0/10.
        assert_eq!(classify_endpoint("http://100.64.0.1:8080"), "local");
        assert_eq!(classify_endpoint("http://100.127.255.1:8080"), "local");
        // Outside the range.
        assert_eq!(classify_endpoint("http://100.63.0.1:8080"), "api");
        assert_eq!(classify_endpoint("http://100.128.0.1:8080"), "api");
        assert_eq!(classify_endpoint("https://api.openai.com/v1"), "api");
    }

    #[test]
    fn truthy_matches_python() {
        assert!(truthy(Some("true")));
        assert!(truthy(Some(" YES ")));
        assert!(truthy(Some("1")));
        assert!(truthy(Some("on")));
        assert!(!truthy(Some("false")));
        assert!(!truthy(Some("")));
        assert!(!truthy(None));
    }

    #[test]
    fn last_segment_takes_after_slash() {
        assert_eq!(last_segment("meta-llama/Llama-4"), "Llama-4");
        assert_eq!(last_segment("gpt-4o"), "gpt-4o");
    }

    #[test]
    fn value_truthy_python_semantics() {
        assert!(!value_truthy(&Value::Null));
        assert!(value_truthy(&json!(true)));
        assert!(!value_truthy(&json!(false)));
        assert!(value_truthy(&json!(1)));
        assert!(!value_truthy(&json!(0)));
        assert!(value_truthy(&json!("x")));
        assert!(!value_truthy(&json!("")));
    }

    #[test]
    fn extract_ollama_names_handles_both_keys() {
        let data = json!({"models": [{"name": "llama3"}, {"model": "qwen2"}, {}]});
        assert_eq!(extract_ollama_names(&data), vec!["llama3", "qwen2"]);
    }

    #[test]
    fn normalize_model_ids_coerces_list_json_and_csv() {
        // list of strings (drops non-strings, trims, dedupes, preserves order)
        assert_eq!(
            normalize_model_ids(&json!(["a", " b ", "a", 1, "b"])),
            vec!["a", "b"]
        );
        // JSON-encoded list string
        assert_eq!(normalize_model_ids(&json!("[\"x\", \"y\"]")), vec!["x", "y"]);
        // comma / newline separated string
        assert_eq!(
            normalize_model_ids(&json!("m1, m2\nm3,m1")),
            vec!["m1", "m2", "m3"]
        );
        // empty / null
        assert!(normalize_model_ids(&json!("")).is_empty());
        assert!(normalize_model_ids(&Value::Null).is_empty());
        // non-list/non-string scalar -> []
        assert!(normalize_model_ids(&json!(5)).is_empty());
    }

    #[test]
    fn merge_model_ids_dedupes_in_order() {
        let a = vec!["x".to_string(), "y".to_string()];
        let b = vec!["y".to_string(), "z".to_string()];
        assert_eq!(merge_model_ids(&[&a, &b]), vec!["x", "y", "z"]);
    }

    #[test]
    fn visible_models_merges_pinned_and_filters_hidden() {
        // cached + pinned merged, hidden removed; pinned IDs survive even if absent
        // from cached.
        let out = visible_models(
            Some("[\"a\", \"b\"]"),
            Some("[\"b\"]"),
            Some("[\"deploy-1\"]"),
        );
        assert_eq!(out, vec!["a", "deploy-1"]);
        // no hidden -> straight merge
        let out2 = visible_models(Some("[\"a\"]"), None, Some("[\"a\", \"p\"]"));
        assert_eq!(out2, vec!["a", "p"]);
    }

    #[test]
    fn match_provider_curated_host_and_path() {
        // hostname (subdomain) match, not substring.
        assert_eq!(
            match_provider_curated("https://api.deepseek.com/v1", Some("openai")),
            Some("deepseek".to_string())
        );
        // z.ai coding plan path override.
        assert_eq!(
            match_provider_curated("https://api.z.ai/api/coding/paas/v4", None),
            Some("zai-coding".to_string())
        );
        // z.ai without the coding path -> plain zai.
        assert_eq!(
            match_provider_curated("https://api.z.ai/api/paas/v4", None),
            Some("zai".to_string())
        );
        // ollama.com host maps to the (listless) "ollama" key.
        assert_eq!(
            match_provider_curated("https://ollama.com/api", None),
            Some("ollama".to_string())
        );
    }

    #[test]
    fn rewrite_loopback_container_local_normalizes_bind_any() {
        // container_local + 0.0.0.0 -> 127.0.0.1 (keep port), never docker host.
        assert_eq!(
            rewrite_loopback_for_docker("http://0.0.0.0:1234/v1", true),
            "http://127.0.0.1:1234/v1"
        );
        // container_local + already-loopback -> unchanged.
        assert_eq!(
            rewrite_loopback_for_docker("http://localhost:11434/v1", true),
            "http://localhost:11434/v1"
        );
        // non-loopback host -> always unchanged.
        assert_eq!(
            rewrite_loopback_for_docker("https://api.openai.com/v1", false),
            "https://api.openai.com/v1"
        );
    }

    #[test]
    fn model_endpoint_error_message_ollama_vs_generic() {
        let ollama = model_endpoint_error_message("http://localhost:11434/v1", None);
        assert!(ollama.starts_with("No Ollama models found for that endpoint."));
        assert!(ollama.contains("ollama list"));
        let generic = model_endpoint_error_message("https://api.example.com/v1", None);
        assert_eq!(generic, "No models found for that provider/key.");
        let ping = PingResult::new(false, Some("connect timed out".to_string()));
        let generic_err = model_endpoint_error_message("https://api.example.com/v1", Some(&ping));
        assert_eq!(
            generic_err,
            "No models found for that provider/key. Last probe error: connect timed out."
        );
    }

    #[test]
    fn session_uses_endpoint_url_variants() {
        let base = "http://localhost:11434/v1";
        // the normalized base, the +/chat/completions form, and a child path match.
        assert!(session_uses_endpoint_url("http://localhost:11434/v1", base));
        assert!(session_uses_endpoint_url(
            "http://localhost:11434/v1/chat/completions",
            base
        ));
        assert!(session_uses_endpoint_url(
            "http://localhost:11434/v1/extra",
            base
        ));
        // unrelated endpoint does not match.
        assert!(!session_uses_endpoint_url("https://api.openai.com/v1", base));
        // empty inputs are never a match.
        assert!(!session_uses_endpoint_url("", base));
        assert!(!session_uses_endpoint_url(base, ""));
    }

    #[test]
    fn clear_endpoint_settings_handles_fields_and_fallbacks() {
        let mut settings: Map<String, Value> = serde_json::from_value(json!({
            "default_endpoint_id": "ep1",
            "default_model": "m1",
            "tts_provider": "endpoint:ep1",
            "tts_model": "custom",
            "default_model_fallbacks": [
                {"endpoint_id": "ep1", "model": "a"},
                {"endpoint_id": "ep2", "model": "b"}
            ]
        }))
        .unwrap();
        let cleared = clear_endpoint_settings_for_endpoint(&mut settings, "ep1", true);
        assert!(cleared.contains(&"Default Model".to_string()));
        assert!(cleared.contains(&"Default Model Fallbacks".to_string()));
        assert!(cleared.contains(&"Text to Speech".to_string()));
        assert_eq!(settings.get("default_endpoint_id").unwrap(), &json!(""));
        assert_eq!(settings.get("tts_provider").unwrap(), &json!("disabled"));
        assert_eq!(settings.get("tts_model").unwrap(), &json!("tts-1"));
        // ep2 fallback entry survives.
        let chain = settings.get("default_model_fallbacks").unwrap().as_array().unwrap();
        assert_eq!(chain.len(), 1);
        assert_eq!(chain[0].get("endpoint_id").unwrap(), &json!("ep2"));
    }

    // ---- a2e691d: proxy/API classification + refresh policy ----

    /// Build a bare `Endpoint` for the kind/refresh helpers; only the fields a test
    /// exercises are set.
    fn ep_fixture(
        base_url: &str,
        api_key: Option<&str>,
        endpoint_kind: Option<&str>,
        refresh_mode: Option<&str>,
    ) -> Endpoint {
        Endpoint {
            id: "ep".to_string(),
            name: "n".to_string(),
            base_url: base_url.to_string(),
            api_key: api_key.map(String::from),
            is_enabled: true,
            hidden_models: None,
            cached_models: None,
            model_type: None,
            supports_tools: None,
            pinned_models: None,
            endpoint_kind: endpoint_kind.map(String::from),
            model_refresh_mode: refresh_mode.map(String::from),
            model_refresh_interval: None,
            model_refresh_timeout: None,
            updated_at_ts: 0.0,
            created_at_ts: 0.0,
        }
    }

    #[test]
    fn normalize_endpoint_kind_clamps_to_known_set() {
        assert_eq!(normalize_endpoint_kind(Some(" Proxy ")), "proxy");
        assert_eq!(normalize_endpoint_kind(Some("LOCAL")), "local");
        assert_eq!(normalize_endpoint_kind(Some("api")), "api");
        assert_eq!(normalize_endpoint_kind(Some("")), "auto");
        assert_eq!(normalize_endpoint_kind(None), "auto");
        assert_eq!(normalize_endpoint_kind(Some("garbage")), "auto");
    }

    #[test]
    fn normalize_refresh_mode_proxy_defaults_manual() {
        // explicit manual/disabled always win
        assert_eq!(normalize_refresh_mode(Some("manual"), "auto"), "manual");
        assert_eq!(normalize_refresh_mode(Some("disabled"), "proxy"), "disabled");
        // "auto" for a non-proxy stays auto
        assert_eq!(normalize_refresh_mode(Some("auto"), "local"), "auto");
        // "auto" for a proxy becomes manual (cached-first)
        assert_eq!(normalize_refresh_mode(Some("auto"), "proxy"), "manual");
        // empty/unknown: proxy -> manual, others -> auto
        assert_eq!(normalize_refresh_mode(Some(""), "proxy"), "manual");
        assert_eq!(normalize_refresh_mode(None, "api"), "auto");
        assert_eq!(normalize_refresh_mode(Some("???"), "proxy"), "manual");
    }

    #[test]
    fn classify_endpoint_kind_explicit_overrides_url() {
        // explicit kind short-circuits the URL heuristic
        assert_eq!(classify_endpoint_kind("https://api.openai.com/v1", "local"), "local");
        assert_eq!(classify_endpoint_kind("http://localhost:11434/v1", "api"), "api");
        assert_eq!(classify_endpoint_kind("http://localhost:11434/v1", "proxy"), "api");
        // "auto" falls back to the URL heuristic
        assert_eq!(classify_endpoint_kind("http://localhost:11434/v1", "auto"), "local");
        assert_eq!(classify_endpoint_kind("https://api.openai.com/v1", "auto"), "api");
    }

    #[test]
    fn effective_endpoint_kind_keyed_v1_is_proxy() {
        // explicit kind is returned verbatim
        let ep = ep_fixture("https://api.openai.com/v1", Some("k"), Some("api"), None);
        assert_eq!(effective_endpoint_kind(&ep, "https://api.openai.com/v1"), "api");
        // auto + api_key + /v1 path -> proxy heuristic
        let ep = ep_fixture("https://proxy.example.com/v1", Some("k"), None, None);
        assert_eq!(effective_endpoint_kind(&ep, "https://proxy.example.com/v1"), "proxy");
        // auto + api_key + /openai path -> proxy
        let ep = ep_fixture("https://gw.example.com/openai/deployments", Some("k"), None, None);
        assert_eq!(
            effective_endpoint_kind(&ep, "https://gw.example.com/openai/deployments"),
            "proxy"
        );
        // no key -> stays auto
        let ep = ep_fixture("https://proxy.example.com/v1", None, None, None);
        assert_eq!(effective_endpoint_kind(&ep, "https://proxy.example.com/v1"), "auto");
        // keyed Ollama is NOT treated as a proxy
        let ep = ep_fixture("http://localhost:11434/v1", Some("k"), None, None);
        assert_eq!(effective_endpoint_kind(&ep, "http://localhost:11434/v1"), "auto");
    }

    #[test]
    fn parse_positive_int_gates_and_clamps() {
        assert_eq!(parse_positive_int(Some(" 45 "), 1, 60), Some(45));
        assert_eq!(parse_positive_int(Some("100"), 1, 60), Some(60)); // clamp to max
        assert_eq!(parse_positive_int(Some("0"), 1, 60), None); // below min
        assert_eq!(parse_positive_int(Some(""), 1, 60), None);
        assert_eq!(parse_positive_int(Some("abc"), 1, 60), None);
        assert_eq!(parse_positive_int(None, 1, 60), None);
    }

    #[test]
    fn explicit_model_list_timeout_long_for_proxy_and_api() {
        // explicit kind proxy/api -> 30s
        assert_eq!(explicit_model_list_timeout("https://x/v1", "proxy", None), 30.0);
        assert_eq!(explicit_model_list_timeout("https://x/v1", "api", None), 30.0);
        // auto on a cloud URL classifies api -> 30s
        assert_eq!(explicit_model_list_timeout("https://api.openai.com/v1", "auto", None), 30.0);
        // auto on a local Ollama base -> 3s
        assert_eq!(explicit_model_list_timeout("http://localhost:11434/v1", "auto", None), 3.0);
        // auto on a plain local base -> 2s
        assert_eq!(explicit_model_list_timeout("http://127.0.0.1:8000/v1", "auto", None), 2.0);
        // an explicit requested timeout wins
        assert_eq!(explicit_model_list_timeout("https://x/v1", "proxy", Some(12)), 12.0);
    }

    #[test]
    fn manual_refresh_timeout_proxy_floor_30() {
        // proxy/api endpoint with no stored/requested timeout -> at least 30s
        let ep = ep_fixture("https://proxy/v1", Some("k"), Some("proxy"), None);
        assert_eq!(manual_refresh_timeout(&ep, "api", None), 30.0);
        // a requested override wins
        assert_eq!(manual_refresh_timeout(&ep, "api", Some(45)), 45.0);
        // local endpoint with no stored timeout -> category default (2.5)
        let ep_local = ep_fixture("http://localhost:11434/v1", None, Some("local"), None);
        assert_eq!(manual_refresh_timeout(&ep_local, "local", None), 2.5);
    }

    #[test]
    fn endpoint_refresh_interval_defaults_by_category() {
        let ep = ep_fixture("https://x/v1", None, None, None);
        assert_eq!(endpoint_refresh_interval(&ep, "local"), 60.0);
        assert_eq!(endpoint_refresh_interval(&ep, "api"), 3600.0);
        // stored value >=30 wins; below 30 floors to 30.
        let mut ep2 = ep_fixture("https://x/v1", None, None, None);
        ep2.model_refresh_interval = Some(120);
        assert_eq!(endpoint_refresh_interval(&ep2, "api"), 120.0);
        ep2.model_refresh_interval = Some(5);
        assert_eq!(endpoint_refresh_interval(&ep2, "api"), 30.0);
    }

    #[test]
    fn failure_delay_exponential_capped() {
        assert_eq!(failure_delay(0), 0.0);
        assert_eq!(failure_delay(1), 300.0);
        assert_eq!(failure_delay(2), 600.0);
        assert_eq!(failure_delay(3), 1200.0);
        // capped at REFRESH_FAILURE_MAX (3600).
        assert_eq!(failure_delay(10), 3600.0);
    }

    #[test]
    fn refresh_key_combines_base_and_key() {
        assert_eq!(refresh_key("https://x/v1/", Some("abc")), "https://x/v1\x00abc");
        assert_eq!(refresh_key("https://x/v1", None), "https://x/v1\x00");
        // different keys -> distinct refresh state.
        assert_ne!(refresh_key("https://x/v1", Some("a")), refresh_key("https://x/v1", Some("b")));
    }

    #[test]
    fn is_ollama_base_matches_port_and_name() {
        assert!(is_ollama_base("http://localhost:11434/v1"));
        assert!(is_ollama_base("http://ollama.internal:9999/v1"));
        assert!(!is_ollama_base("https://api.openai.com/v1"));
    }

    #[test]
    fn should_refresh_skips_proxy_unless_forced() {
        let _g = crate::core::database::DB_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        REFRESH_STATE.lock().unwrap().clear();
        // A proxy (manual mode) is skipped on a normal refresh...
        let proxy = ep_fixture("https://proxy.example.com/v1", Some("k"), Some("proxy"), None);
        let (ok, _) = should_refresh_endpoint(&proxy, 1000.0, false);
        assert!(!ok);
        // ...but a forced refresh still runs it.
        let (ok_forced, info) = should_refresh_endpoint(&proxy, 1000.0, true);
        assert!(ok_forced);
        assert_eq!(info.timeout, 2.0); // api category default

        // A local auto endpoint with no cache refreshes immediately.
        REFRESH_STATE.lock().unwrap().clear();
        let local = ep_fixture("http://localhost:11434/v1", None, Some("local"), None);
        let (ok_local, info_local) = should_refresh_endpoint(&local, 1000.0, false);
        assert!(ok_local);
        assert_eq!(info_local.timeout, 2.5); // local category default
    }

    #[test]
    fn should_refresh_respects_failure_cooldown_and_interval() {
        let _g = crate::core::database::DB_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let local = ep_fixture("http://127.0.0.1:8000/v1", None, Some("local"), None);
        let key = refresh_key(&normalize_base(Some(&local.base_url)), local.api_key.as_deref());

        // Recent failure within the backoff window -> skipped (not forced).
        REFRESH_STATE.lock().unwrap().insert(
            key.clone(),
            RefreshState { fail_count: 1, last_failure: 1000.0, ..Default::default() },
        );
        let (ok, _) = should_refresh_endpoint(&local, 1000.0 + 10.0, false);
        assert!(!ok);
        // Past the cooldown -> allowed.
        let (ok2, _) = should_refresh_endpoint(&local, 1000.0 + 301.0, false);
        assert!(ok2);

        // A fresh success within the (60s local) interval skips when cached.
        let mut cached = ep_fixture("http://127.0.0.1:8000/v1", None, Some("local"), None);
        cached.cached_models = Some("[\"m1\"]".to_string());
        REFRESH_STATE.lock().unwrap().insert(
            key.clone(),
            RefreshState { last_success: 2000.0, ..Default::default() },
        );
        let (ok3, _) = should_refresh_endpoint(&cached, 2000.0 + 30.0, false);
        assert!(!ok3);
        // Past the interval -> allowed again.
        let (ok4, _) = should_refresh_endpoint(&cached, 2000.0 + 61.0, false);
        assert!(ok4);
        REFRESH_STATE.lock().unwrap().clear();
    }
}
