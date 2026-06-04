// src/integrations.rs  <- src/integrations.py
//! User-registered HTTP API integrations: a small JSON-file registry plus an
//! async `execute_api_call` that performs the request against a registered
//! integration. Faithful port of `src/integrations.py`.
//!
//! Only `execute_api_call` truly needs HTTP (`httpx` -> `reqwest`); the
//! storage/CRUD/prompt/migration helpers below are `serde_json` + filesystem.
//! API keys are encrypted at rest: `save_integrations` runs each row's
//! `api_key` through `src::secret_storage::encrypt` and writes the file
//! atomically (`core::atomic_io::atomic_write_json`) then chmod 0600;
//! `load_integrations`
//! decrypts for runtime callers, drops non-object rows, and re-saves once if any
//! row still holds a plaintext key (a one-shot encryption migration). The whole
//! module is always compiled (the crate has no cargo feature flags — one plain
//! build).
//!
//! DATA_FILE DRIFT (documented, deliberate): the Python computes
//! `DATA_FILE = os.path.join(dirname(dirname(__file__)), "data", "integrations.json")`,
//! i.e. a *source-relative* path that **ignores `ODYSSEUS_DATA_DIR`** (unlike
//! the rest of the app, which routes through `DATA_DIR`). To preserve parity we
//! derive this path from `crate::core::constants::BASE_DIR` (the repo root, the
//! same dir Python resolves from `__file__`) and join `data/integrations.json`
//! directly — NOT via `DATA_DIR`. `migrate_from_settings` reads
//! `data/settings.json` from the very same BASE_DIR for the same reason.
//!
//! Dict/JSON values are `serde_json::Value` objects; the crate is built with
//! `preserve_order`, so a `serde_json::Map` preserves key-insertion order and
//! `insert` on an existing key keeps its position — matching Python `dict`
//! `update`/`setdefault` semantics exactly (load-bearing for `add_integration`).

use once_cell::sync::Lazy;
use serde_json::{Map, Value};

use crate::pylog as logger;
use crate::pyos as os;

/// `DATA_FILE = os.path.join(dirname(dirname(__file__)), "data", "integrations.json")`.
///
/// Source-relative: ignores `ODYSSEUS_DATA_DIR` (see module note). `BASE_DIR`
/// is the repo root with a trailing `/`, so the join lands at
/// `<repo>/data/integrations.json`.
fn data_file() -> String {
    let data_dir = os::path::join(&crate::core::constants::BASE_DIR, "data");
    os::path::join(&data_dir, "integrations.json")
}

/// `settings_path = os.path.join(dirname(dirname(__file__)), "data", "settings.json")`
/// used by `migrate_from_settings` (same source-relative derivation).
fn settings_file() -> String {
    let data_dir = os::path::join(&crate::core::constants::BASE_DIR, "data");
    os::path::join(&data_dir, "settings.json")
}

// ---------------------------------------------------------------------------
// Presets
// ---------------------------------------------------------------------------

/// `INTEGRATION_PRESETS` — preset defaults merged in by `add_integration`.
///
/// Each preset is the literal Python dict, reproduced as a `serde_json::Map`
/// (insertion order preserved). The outer mapping is a `Lazy` `IndexMap`-style
/// ordered map so `preset in INTEGRATION_PRESETS` membership + preset-merge
/// ordering match Python.
pub static INTEGRATION_PRESETS: Lazy<indexmap::IndexMap<&'static str, Map<String, Value>>> =
    Lazy::new(|| {
        let mut presets: indexmap::IndexMap<&'static str, Map<String, Value>> =
            indexmap::IndexMap::new();

        let mut miniflux = Map::new();
        miniflux.insert("name".into(), Value::from("Miniflux"));
        miniflux.insert("auth_type".into(), Value::from("header"));
        miniflux.insert("auth_header".into(), Value::from("X-Auth-Token"));
        miniflux.insert(
            "description".into(),
            Value::from(concat!(
                "Miniflux RSS reader (v1 API). Key endpoints:\n",
                "  GET /v1/feeds — list all feeds\n",
                "  GET /v1/feeds/{id} — get feed details\n",
                "  POST /v1/feeds — create feed {\"feed_url\": \"...\", \"category_id\": N}\n",
                "  PUT /v1/feeds/{id} — update feed\n",
                "  DELETE /v1/feeds/{id} — delete feed\n",
                "  GET /v1/feeds/{id}/entries — list entries for feed\n",
                "  GET /v1/entries — list all entries (params: status, limit, order, direction, category_id)\n",
                "  GET /v1/entries/{id} — get single entry\n",
                "  PUT /v1/entries — update entries {\"entry_ids\": [...], \"status\": \"read|unread\"}\n",
                "  GET /v1/categories — list categories\n",
                "  POST /v1/categories — create category {\"title\": \"...\"}\n",
                "  GET /v1/feeds/{id}/icon — get feed icon\n",
                "  PUT /v1/entries/{id}/bookmark — toggle bookmark"
            )),
        );
        presets.insert("miniflux", miniflux);

        let mut gitea = Map::new();
        gitea.insert("name".into(), Value::from("Gitea"));
        gitea.insert("auth_type".into(), Value::from("header"));
        gitea.insert("auth_header".into(), Value::from("Authorization"));
        gitea.insert(
            "description".into(),
            Value::from(concat!(
                "Gitea git forge API (v1). Auth header value format: 'token YOUR_TOKEN'. Key endpoints:\n",
                "  GET /api/v1/repos/search — search repositories\n",
                "  GET /api/v1/repos/{owner}/{repo} — get repo details\n",
                "  GET /api/v1/repos/{owner}/{repo}/issues — list issues\n",
                "  POST /api/v1/repos/{owner}/{repo}/issues — create issue {\"title\": \"...\"}\n",
                "  GET /api/v1/repos/{owner}/{repo}/pulls — list pull requests\n",
                "  GET /api/v1/repos/{owner}/{repo}/commits — list commits\n",
                "  GET /api/v1/user/repos — list your repos\n",
                "  GET /api/v1/orgs — list organizations\n",
                "  GET /api/v1/repos/{owner}/{repo}/contents/{filepath} — get file content"
            )),
        );
        presets.insert("gitea", gitea);

        let mut linkding = Map::new();
        linkding.insert("name".into(), Value::from("Linkding"));
        linkding.insert("auth_type".into(), Value::from("header"));
        linkding.insert("auth_header".into(), Value::from("Authorization"));
        linkding.insert(
            "description".into(),
            Value::from(concat!(
                "Linkding bookmark manager API. Auth header value format: 'Token YOUR_TOKEN'. Key endpoints:\n",
                "  GET /api/bookmarks/ — list bookmarks (params: q, limit, offset)\n",
                "  GET /api/bookmarks/{id}/ — get bookmark\n",
                "  POST /api/bookmarks/ — create bookmark {\"url\": \"...\", \"title\": \"...\", \"tag_names\": [...]}\n",
                "  PUT /api/bookmarks/{id}/ — update bookmark\n",
                "  DELETE /api/bookmarks/{id}/ — delete bookmark\n",
                "  GET /api/bookmarks/archived/ — list archived bookmarks\n",
                "  GET /api/tags/ — list tags"
            )),
        );
        presets.insert("linkding", linkding);

        let mut homeassistant = Map::new();
        homeassistant.insert("name".into(), Value::from("Home Assistant"));
        homeassistant.insert("auth_type".into(), Value::from("bearer"));
        homeassistant.insert(
            "description".into(),
            Value::from(concat!(
                "Home Assistant smart home API. Key endpoints:\n",
                "  GET /api/ — API status check\n",
                "  GET /api/states — list all entity states\n",
                "  GET /api/states/{entity_id} — get state of entity\n",
                "  POST /api/states/{entity_id} — update entity state\n",
                "  POST /api/services/{domain}/{service} — call service (e.g. light/turn_on)\n",
                "  GET /api/history/period/{timestamp} — get state history\n",
                "  GET /api/logbook/{timestamp} — get logbook entries\n",
                "  POST /api/events/{event_type} — fire event\n",
                "  GET /api/config — get configuration"
            )),
        );
        presets.insert("homeassistant", homeassistant);

        let mut ntfy = Map::new();
        ntfy.insert("name".into(), Value::from("ntfy"));
        ntfy.insert("auth_type".into(), Value::from("none"));
        ntfy.insert(
            "description".into(),
            Value::from(concat!(
                "ntfy push notification service. Key endpoints:\n",
                "  POST /{topic} — send notification. Body is the message text.\n",
                "    Headers: Title (notification title), Priority (1-5), Tags (comma-separated emoji tags)\n",
                "  POST / — send JSON notification {\"topic\": \"...\", \"message\": \"...\", \"title\": \"...\", \"priority\": N}\n",
                "  GET /{topic}/json?poll=1 — poll for messages"
            )),
        );
        presets.insert("ntfy", ntfy);

        let mut vaultwarden = Map::new();
        vaultwarden.insert("name".into(), Value::from("Vaultwarden"));
        vaultwarden.insert("auth_type".into(), Value::from("header"));
        vaultwarden.insert("auth_header".into(), Value::from("Authorization"));
        vaultwarden.insert(
            "description".into(),
            Value::from(concat!(
                "Vaultwarden (Bitwarden-compatible) password manager API. Auth header value format: 'Bearer ACCESS_TOKEN'.\n",
                "To get an access token: POST /identity/connect/token with grant_type=client_credentials&client_id=...&client_secret=...\n",
                "Key endpoints:\n",
                "  GET /api/ciphers — list all vault items (logins, notes, cards, identities)\n",
                "  GET /api/ciphers/{id} — get a single vault item\n",
                "  POST /api/ciphers — create vault item {\"type\": 1, \"name\": \"...\", \"login\": {\"uri\": \"...\", \"username\": \"...\", \"password\": \"...\"}}\n",
                "  PUT /api/ciphers/{id} — update vault item\n",
                "  DELETE /api/ciphers/{id} — delete vault item\n",
                "  GET /api/folders — list folders\n",
                "  POST /api/folders — create folder {\"name\": \"...\"}\n",
                "  GET /api/collections — list collections (org vaults)\n",
                "  POST /api/ciphers/{id}/password-history — get password history\n",
                "  GET /api/sends — list Bitwarden Send items\n",
                "  POST /api/sends — create a Send (secure sharing)\n",
                "  Note: Vault data is end-to-end encrypted. The API returns encrypted fields\n",
                "  that must be decrypted client-side with the user's master key."
            )),
        );
        presets.insert("vaultwarden", vaultwarden);

        let mut freshrss = Map::new();
        freshrss.insert("name".into(), Value::from("FreshRSS"));
        freshrss.insert("auth_type".into(), Value::from("header"));
        freshrss.insert("auth_header".into(), Value::from("Authorization"));
        freshrss.insert(
            "description".into(),
            Value::from(concat!(
                "FreshRSS RSS reader (GReader API). Auth header value format: 'GoogleLogin auth=YOUR_TOKEN'. Key endpoints:\n",
                "  GET /api/greader.php/reader/api/0/subscription/list?output=json — list feeds\n",
                "  GET /api/greader.php/reader/api/0/stream/contents/feed/{feed_id}?output=json&n=20 — get entries\n",
                "  GET /api/greader.php/reader/api/0/tag/list?output=json — list tags/categories\n",
                "  POST /api/greader.php/reader/api/0/edit-tag — mark read/starred\n",
                "  GET /api/greader.php/reader/api/0/unread-count?output=json — unread counts"
            )),
        );
        presets.insert("freshrss", freshrss);

        presets
    });

// ---------------------------------------------------------------------------
// Storage
// ---------------------------------------------------------------------------

/// `os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)`.
fn ensure_data_dir() {
    let _ = os::makedirs(&os::path::dirname(&data_file()), true);
}

/// `_encrypt_integration_secrets(integrations)` — return storage-safe copies
/// with each non-empty `api_key` encrypted at rest via `secret_storage::encrypt`.
///
/// `copy = dict(item)` then `copy["api_key"] = encrypt(str(api_key))` only when
/// `api_key` is truthy (a non-empty string). Non-string `api_key` values are
/// left untouched (Python `copy.get("api_key", "")` is falsy for missing keys;
/// a non-string truthy value would `str()`-ify, but persisted integrations only
/// ever store string keys, so we restrict to the string case here).
fn encrypt_integration_secrets(integrations: &[Value]) -> Vec<Value> {
    let mut safe: Vec<Value> = Vec::with_capacity(integrations.len());
    for item in integrations {
        let mut copy = item.as_object().cloned().unwrap_or_default();
        if let Some(api_key) = copy.get("api_key").and_then(|v| v.as_str()) {
            if !api_key.is_empty() {
                let enc = crate::src::secret_storage::encrypt(api_key);
                copy.insert("api_key".to_string(), Value::from(enc));
            }
        }
        safe.push(Value::Object(copy));
    }
    safe
}

/// `_decrypt_integration_secrets(integrations)` — return runtime copies with
/// each non-empty `api_key` decrypted for callers via `secret_storage::decrypt`.
fn decrypt_integration_secrets(integrations: &[Value]) -> Vec<Value> {
    let mut decoded: Vec<Value> = Vec::with_capacity(integrations.len());
    for item in integrations {
        let mut copy = item.as_object().cloned().unwrap_or_default();
        if let Some(api_key) = copy.get("api_key").and_then(|v| v.as_str()) {
            if !api_key.is_empty() {
                let dec = crate::src::secret_storage::decrypt(api_key);
                copy.insert("api_key".to_string(), Value::from(dec));
            }
        }
        decoded.push(Value::Object(copy));
    }
    decoded
}

/// `_has_plaintext_api_key(integrations)` — True if any row carries a truthy
/// `api_key` that is NOT already encrypted (drives the load-time migration).
fn has_plaintext_api_key(integrations: &[Value]) -> bool {
    integrations.iter().any(|item| {
        item.get("api_key")
            .and_then(|v| v.as_str())
            .map(|k| !k.is_empty() && !crate::src::secret_storage::is_encrypted(k))
            .unwrap_or(false)
    })
}

/// `mask_integration_secret(integration)` — return a copy safe for API
/// responses, with the `api_key` masked to its first 4 code points + `****`.
///
/// ```python
/// def mask_integration_secret(integration):
///     safe = dict(integration)
///     api_key = safe.get("api_key", "")
///     if api_key:
///         safe["api_key"] = f"{str(api_key)[:4]}****"
///     return safe
/// ```
///
/// `if api_key:` is Python truthiness of the stored value (a non-empty string);
/// the slice `str(api_key)[:4]` is by code point.
pub fn mask_integration_secret(integration: &Value) -> Value {
    let mut safe = integration.as_object().cloned().unwrap_or_default();
    let masked = safe
        .get("api_key")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .map(|key| {
            let head: String = key.chars().take(4).collect();
            format!("{head}****")
        });
    if let Some(m) = masked {
        safe.insert("api_key".to_string(), Value::from(m));
    }
    Value::Object(safe)
}

/// Load all integrations from disk with secrets decrypted for runtime use.
///
/// `if not os.path.exists(DATA_FILE): return []`, otherwise `json.load`. A read
/// or JSON-decode error logs and returns `[]` (Python catches
/// `json.JSONDecodeError`/`IOError`). A non-list top-level value logs an error
/// and returns `[]` (Python: `isinstance(integrations, list)` guard). Non-object
/// rows are dropped with an error log (`isinstance(item, dict)` filter). Finally
/// if any row still carries a plaintext `api_key`, the list is re-saved (which
/// encrypts it at rest — the migration) and the decrypted copies are returned.
pub fn load_integrations() -> Vec<Value> {
    let file = data_file();
    if !os::path::exists(&file) {
        return Vec::new();
    }
    let raw = match std::fs::read_to_string(&file) {
        Ok(r) => r,
        Err(exc) => {
            logger::error(&format!("Failed to load integrations: {exc}"));
            return Vec::new();
        }
    };
    let parsed: Value = match serde_json::from_str::<Value>(&raw) {
        Ok(v) => v,
        Err(exc) => {
            logger::error(&format!("Failed to load integrations: {exc}"));
            return Vec::new();
        }
    };
    // if not isinstance(integrations, list): log + return []
    let items = match parsed {
        Value::Array(items) => items,
        _ => {
            logger::error("Invalid integrations file shape: expected a list");
            return Vec::new();
        }
    };
    // valid_integrations = [item for item in integrations if isinstance(item, dict)]
    let original_len = items.len();
    let valid: Vec<Value> = items.into_iter().filter(|item| item.is_object()).collect();
    if valid.len() != original_len {
        logger::error("Invalid integrations file rows: ignored non-object entries");
    }
    // if _has_plaintext_api_key(integrations): save_integrations(_decrypt_integration_secrets(integrations))
    if has_plaintext_api_key(&valid) {
        save_integrations(&decrypt_integration_secrets(&valid));
    }
    decrypt_integration_secrets(&valid)
}

/// Persist integrations list to disk with API keys encrypted at rest.
///
/// `atomic_write_json(DATA_FILE, _encrypt_integration_secrets(integrations),
/// indent=2)` then `safe_chmod(DATA_FILE, 0o600)`.
pub fn save_integrations(integrations: &[Value]) {
    ensure_data_dir();
    let safe = encrypt_integration_secrets(integrations);
    let arr = Value::Array(safe);
    let file = data_file();
    // atomic_write_json(..., indent=2). The atomic writer applies `ensure_ascii`
    // (escaping non-ASCII such as the em-dashes "—" in the preset descriptions to
    // `\uXXXX`), so the on-disk bytes now match Python's default
    // `ensure_ascii=True` exactly — no drift.
    let _ = crate::core::atomic_io::atomic_write_json(&file, &arr, Some(2));
    // safe_chmod(DATA_FILE, 0o600) — POSIX-only lockdown; no-op on Windows. The
    // `pyos::chmod` is the faithful analogue (the Python `safe_chmod` is just
    // `os.chmod` on POSIX / no-op on Windows).
    let _ = os::chmod(&file, 0o600);
}

/// Get a single integration by id.
pub fn get_integration(integration_id: &str) -> Option<Value> {
    load_integrations()
        .into_iter()
        .find(|item| item.get("id").and_then(|v| v.as_str()) == Some(integration_id))
}

/// `data.get(key)` as `&str` (None for missing/non-string), matching how the
/// Python reads string-ish fields off the incoming dict.
fn get_str<'a>(obj: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    obj.get(key).and_then(|v| v.as_str())
}

/// Add a new integration. If `preset` is given, merge preset defaults first.
///
/// Mirrors Python exactly:
/// * start with an empty dict,
/// * if `preset` matches a known preset, `update` with the preset dict then set
///   `preset`,
/// * `update` with the caller's `data` (overwrites, preserving key positions
///   for keys already present),
/// * `setdefault` each required field.
///
/// `data` is taken as a `serde_json::Map` (the request body object).
pub fn add_integration(data: Map<String, Value>) -> Value {
    let mut integration: Map<String, Value> = Map::new();

    // preset_key = data.get("preset")
    let preset_key = get_str(&data, "preset").map(|s| s.to_string());
    if let Some(ref pk) = preset_key {
        if let Some(preset) = INTEGRATION_PRESETS.get(pk.as_str()) {
            // integration.update(INTEGRATION_PRESETS[preset_key])
            for (k, v) in preset {
                integration.insert(k.clone(), v.clone());
            }
            // integration["preset"] = preset_key
            integration.insert("preset".to_string(), Value::from(pk.clone()));
        }
    }

    // integration.update(data)
    for (k, v) in data {
        integration.insert(k, v);
    }

    // setdefault(...) — only insert when the key is absent.
    integration
        .entry("id".to_string())
        .or_insert_with(|| Value::from(uuid4_hex12()));
    integration
        .entry("enabled".to_string())
        .or_insert(Value::from(true));
    integration
        .entry("auth_type".to_string())
        .or_insert(Value::from("none"));
    integration
        .entry("auth_header".to_string())
        .or_insert(Value::from(""));
    integration
        .entry("auth_param".to_string())
        .or_insert(Value::from(""));
    integration
        .entry("description".to_string())
        .or_insert(Value::from(""));
    integration
        .entry("api_key".to_string())
        .or_insert(Value::from(""));
    integration
        .entry("name".to_string())
        .or_insert(Value::from(""));
    integration
        .entry("base_url".to_string())
        .or_insert(Value::from(""));

    let result = Value::Object(integration);

    let mut integrations = load_integrations();
    integrations.push(result.clone());
    save_integrations(&integrations);
    result
}

/// `uuid.uuid4().hex[:12]` — 32 lowercase hex chars (no dashes), sliced to 12.
fn uuid4_hex12() -> String {
    let hex = uuid::Uuid::new_v4().simple().to_string();
    hex[..12].to_string()
}

/// Update fields on an existing integration. Returns the updated integration or
/// None. The `id` field cannot be changed (`data.pop("id", None)`).
pub fn update_integration(integration_id: &str, mut data: Map<String, Value>) -> Option<Value> {
    let mut integrations = load_integrations();
    let mut found_index: Option<usize> = None;
    for (idx, item) in integrations.iter().enumerate() {
        if item.get("id").and_then(|v| v.as_str()) == Some(integration_id) {
            found_index = Some(idx);
            break;
        }
    }
    let idx = found_index?;
    // data.pop("id", None) — prevent id change
    data.remove("id");
    if let Some(Value::Object(item)) = integrations.get_mut(idx) {
        // item.update(data)
        for (k, v) in data {
            item.insert(k, v);
        }
        let updated = Value::Object(item.clone());
        save_integrations(&integrations);
        return Some(updated);
    }
    None
}

/// Delete an integration by id. Returns True if found and deleted.
pub fn delete_integration(integration_id: &str) -> bool {
    let integrations = load_integrations();
    let original_len = integrations.len();
    let filtered: Vec<Value> = integrations
        .into_iter()
        .filter(|i| i.get("id").and_then(|v| v.as_str()) != Some(integration_id))
        .collect();
    if filtered.len() < original_len {
        save_integrations(&filtered);
        true
    } else {
        false
    }
}

// ---------------------------------------------------------------------------
// API execution
// ---------------------------------------------------------------------------

/// `re.sub(r"<[^>]+>", "", html)` then collapse whitespace, mirrored with the
/// `regex` crate.
fn strip_html_tags(html: &str) -> String {
    static TAG_RE: Lazy<regex::Regex> = Lazy::new(|| regex::Regex::new(r"<[^>]+>").unwrap());
    static WS_RE: Lazy<regex::Regex> = Lazy::new(|| regex::Regex::new(r"\s+").unwrap());
    let text = TAG_RE.replace_all(html, "");
    let text = WS_RE.replace_all(&text, " ");
    text.trim().to_string()
}

/// Find integration by id or name (case-insensitive). Tries id across the whole
/// list first, then name.
fn find_integration(identifier: &str) -> Option<Value> {
    let integrations = load_integrations();
    // try id first
    for item in &integrations {
        if item.get("id").and_then(|v| v.as_str()) == Some(identifier) {
            return Some(item.clone());
        }
    }
    // try name
    let lower = identifier.to_lowercase();
    for item in &integrations {
        let name = item.get("name").and_then(|v| v.as_str()).unwrap_or("");
        if name.to_lowercase() == lower {
            return Some(item.clone());
        }
    }
    None
}

/// `^https?://` anchored scheme check used by the path validation.
static PATH_SCHEME_RE: Lazy<regex::Regex> = Lazy::new(|| regex::Regex::new(r"^https?://").unwrap());

/// Python truthiness of an optional dict value, used for `integration.get(key,
/// default)` where `default` is itself a Python bool. `missing` is the truthy
/// result to use when the key is absent (mirrors the `.get(key, default)`
/// default), then the stored value is evaluated for truthiness.
fn is_truthy(v: Option<&Value>, missing: bool) -> bool {
    match v {
        None => missing,
        Some(Value::Bool(b)) => *b,
        Some(Value::Null) => false,
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Number(n)) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

/// `{"error": msg, "exit_code": 1}` result map.
fn error_result(msg: impl Into<String>) -> Map<String, Value> {
    let mut m = Map::new();
    m.insert("error".to_string(), Value::from(msg.into()));
    m.insert("exit_code".to_string(), Value::from(1));
    m
}

/// `value` rendered the way `httpx` flattens query parameters
/// (`primitive_value_to_str`): strings pass through; `True`/`False` become
/// lowercase `"true"`/`"false"`; `None` becomes `""`; numbers stringify. Used to
/// flatten the `params` object into the `(key, value)` pairs `reqwest` sends.
fn query_value_string(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        // httpx's primitive_value_to_str maps Python True/False -> "true"/"false".
        Value::Bool(true) => "true".to_string(),
        Value::Bool(false) => "false".to_string(),
        // httpx maps None -> "".
        Value::Null => String::new(),
        other => other.to_string(),
    }
}

/// Execute an HTTP request against a registered integration.
///
/// Async, `reqwest`. The `httpx.AsyncClient` defaults to *not* following
/// redirects, so we build the client with `redirect(Policy::none())` for parity.
/// `params` is the optional query-parameter object; `body` the optional JSON
/// body; `extra_headers` optional caller headers. Returns the Python result
/// dict as a `serde_json::Map` (`{"output"|"error", "exit_code"}`).
pub async fn execute_api_call(
    integration_id: &str,
    method: &str,
    path: &str,
    params: Option<Map<String, Value>>,
    body: Option<Value>,
    extra_headers: Option<Map<String, Value>>,
) -> Map<String, Value> {
    let integration = match find_integration(integration_id) {
        Some(i) => i,
        None => {
            return error_result(format!("Integration not found: {integration_id}"));
        }
    };

    // if not integration.get("enabled", True): — Python truthiness of the
    // stored value, defaulting to True (truthy) when the key is absent.
    let enabled = is_truthy(integration.get("enabled"), true);
    if !enabled {
        let name = integration.get("name").and_then(|v| v.as_str()).unwrap_or("");
        return error_result(format!("Integration '{name}' is disabled"));
    }

    // base_url = integration.get("base_url", "").rstrip("/")
    let mut base_url = integration
        .get("base_url")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim_end_matches('/')
        .to_string();
    if base_url.is_empty() {
        return error_result("Integration has no base_url configured");
    }

    // preset = (integration.get("preset") or integration.get("name", "")).lower()
    let preset = {
        let p = integration.get("preset").and_then(|v| v.as_str()).unwrap_or("");
        let chosen = if !p.is_empty() {
            p
        } else {
            integration.get("name").and_then(|v| v.as_str()).unwrap_or("")
        };
        chosen.to_lowercase()
    };
    let strip_suffixes: &[&str] = match preset.as_str() {
        "miniflux" => &["/v1"],
        "gitea" => &["/api/v1", "/api"],
        "linkding" => &["/api"],
        "homeassistant" => &["/api"],
        _ => &[],
    };
    for suf in strip_suffixes {
        if base_url.ends_with(suf) {
            // base_url = base_url[: -len(suf)]
            base_url = base_url[..base_url.len() - suf.len()].to_string();
            break;
        }
    }

    // Validate path
    if !path.starts_with('/') {
        return error_result("Path must start with /");
    }
    if PATH_SCHEME_RE.is_match(path) || path.contains("://") {
        return error_result("Path must not contain a protocol scheme");
    }

    let url = format!("{base_url}{path}");
    let method_upper = method.to_uppercase();

    // Build headers as an order-preserving map keyed by header name, mirroring
    // Python's `headers: Dict[str, str]` semantics: a later assignment with the
    // same key OVERWRITES the earlier one (and keeps its original position),
    // rather than sending a duplicate header. `headers.update(extra_headers)`
    // first, then the auth header is inserted by name.
    let mut headers: indexmap::IndexMap<String, String> = indexmap::IndexMap::new();
    if let Some(extra) = &extra_headers {
        for (k, v) in extra {
            if let Some(s) = v.as_str() {
                headers.insert(k.clone(), s.to_string());
            } else {
                headers.insert(k.clone(), query_value_string(v));
            }
        }
    }

    let api_key = integration.get("api_key").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let auth_type = integration.get("auth_type").and_then(|v| v.as_str()).unwrap_or("none");

    // Working copy of params we may mutate for query-based auth.
    let mut params: Option<Map<String, Value>> = params;

    if auth_type == "header" && !api_key.is_empty() {
        // header_name = integration.get("auth_header") or ""
        let mut header_name = integration
            .get("auth_header")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        if header_name.is_empty() {
            // recompute preset/name lower
            let p = integration.get("preset").and_then(|v| v.as_str()).unwrap_or("");
            let chosen = if !p.is_empty() {
                p
            } else {
                integration.get("name").and_then(|v| v.as_str()).unwrap_or("")
            };
            let preset_inner = chosen.to_lowercase();
            header_name = match preset_inner.as_str() {
                "miniflux" => "X-Auth-Token",
                "linkding" => "Authorization",
                "gitea" => "Authorization",
                _ => "Authorization",
            }
            .to_string();
        }
        headers.insert(header_name, api_key.clone());
    } else if auth_type == "bearer" && !api_key.is_empty() {
        headers.insert("Authorization".to_string(), format!("Bearer {api_key}"));
    } else if auth_type == "query" && !api_key.is_empty() {
        let p = params.get_or_insert_with(Map::new);
        let param_name = integration
            .get("auth_param")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .unwrap_or("api_key")
            .to_string();
        p.insert(param_name, Value::from(api_key.clone()));
    }

    // auth_type == "basic" — expects api_key as "user:password"
    let mut basic_auth: Option<(String, String)> = None;
    if auth_type == "basic" && !api_key.is_empty() {
        // api_key.split(":", 1) — at most 2 parts
        if let Some(colon) = api_key.find(':') {
            let user = api_key[..colon].to_string();
            let pass = api_key[colon + 1..].to_string();
            basic_auth = Some((user, pass));
        }
    }

    // Build the client: httpx.AsyncClient(timeout=30.0) does NOT follow
    // redirects by default — match with redirect Policy::none().
    let client = match reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .redirect(reqwest::redirect::Policy::none())
        .build()
    {
        Ok(c) => c,
        Err(exc) => {
            logger::error(&format!("Unexpected error in execute_api_call: {exc}"));
            return error_result(format!("Unexpected error: {exc}"));
        }
    };

    // method, url
    let req_method = match reqwest::Method::from_bytes(method_upper.as_bytes()) {
        Ok(m) => m,
        Err(exc) => {
            logger::error(&format!("Unexpected error in execute_api_call: {exc}"));
            return error_result(format!("Unexpected error: {exc}"));
        }
    };
    let mut rb = client.request(req_method, &url);

    // params
    if let Some(p) = &params {
        let pairs: Vec<(String, String)> =
            p.iter().map(|(k, v)| (k.clone(), query_value_string(v))).collect();
        rb = rb.query(&pairs);
    }

    // headers
    for (k, v) in &headers {
        rb = rb.header(k.as_str(), v.as_str());
    }

    // json=body if body is not None else None
    if let Some(b) = &body {
        rb = rb.json(b);
    }

    // auth=BasicAuth
    if let Some((user, pass)) = &basic_auth {
        rb = rb.basic_auth(user, Some(pass));
    }

    let name = integration.get("name").and_then(|v| v.as_str()).unwrap_or("").to_string();

    let response = match rb.send().await {
        Ok(r) => r,
        Err(exc) => {
            if exc.is_timeout() {
                // httpx.TimeoutException
                return error_result(format!("Request to {name} timed out"));
            }
            // httpx.RequestError
            return error_result(format!("Request failed: {exc}"));
        }
    };

    let content_type = response
        .headers()
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_string();
    let status = response.status().as_u16();

    // body text
    let raw_text = match response.text().await {
        Ok(t) => t,
        Err(exc) => {
            // Reading the body failed — treat as a request error (httpx would
            // raise a ReadError caught by RequestError).
            return error_result(format!("Request failed: {exc}"));
        }
    };

    // Format response body
    let mut formatted: String = if content_type.contains("application/json") {
        match serde_json::from_str::<Value>(&raw_text) {
            Ok(data) => {
                // json.dumps(data, indent=2, ensure_ascii=False)
                serde_json::to_string_pretty(&data).unwrap_or_else(|_| raw_text.clone())
            }
            Err(_) => raw_text.clone(),
        }
    } else if content_type.contains("text/html") {
        strip_html_tags(&raw_text)
    } else {
        raw_text.clone()
    };

    // Truncate — Python slices by character (str), so count chars.
    if formatted.chars().count() > 12000 {
        let head: String = formatted.chars().take(12000).collect();
        formatted = format!("{head}\n... (truncated)");
    }

    let output = format!("HTTP {status}\n{formatted}");

    if status >= 400 {
        return error_result(output);
    }

    let mut ok = Map::new();
    ok.insert("output".to_string(), Value::from(output));
    ok.insert("exit_code".to_string(), Value::from(0));
    ok
}

// ---------------------------------------------------------------------------
// System prompt helper
// ---------------------------------------------------------------------------

/// Return a string describing all enabled integrations for system prompt
/// injection. Returns empty string if no integrations are enabled.
pub fn get_integrations_prompt() -> String {
    let integrations = load_integrations();
    let enabled: Vec<&Value> = integrations
        .iter()
        .filter(|i| is_truthy(i.get("enabled"), true))
        .collect();
    if enabled.is_empty() {
        return String::new();
    }

    let mut lines: Vec<String> =
        vec!["You have access to the following API integrations via the api_call tool:\n".to_string()];
    for integ in enabled {
        // name = integ.get("name", integ.get("id", "unknown"))
        let name = match integ.get("name").and_then(|v| v.as_str()) {
            Some(n) => n.to_string(),
            None => integ
                .get("id")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown")
                .to_string(),
        };
        // f"## {name} (id: {integ['id']})" — Python indexes integ['id']; an
        // integration without an id would KeyError. All persisted integrations
        // carry an id (set in add_integration), so we treat a missing id as "".
        let id = integ.get("id").and_then(|v| v.as_str()).unwrap_or("");
        lines.push(format!("## {name} (id: {id})"));
        let desc = integ.get("description").and_then(|v| v.as_str()).unwrap_or("");
        if !desc.is_empty() {
            lines.push(desc.to_string());
        }
        lines.push(String::new());
    }

    lines.join("\n")
}

// ---------------------------------------------------------------------------
// Migration
// ---------------------------------------------------------------------------

/// If `data/settings.json` has `miniflux_url` and `miniflux_api_key`, create a
/// Miniflux integration and clear those keys from settings.
pub fn migrate_from_settings() {
    let settings_path = settings_file();
    if !os::path::exists(&settings_path) {
        return;
    }

    let raw = match std::fs::read_to_string(&settings_path) {
        Ok(r) => r,
        Err(_) => return, // IOError
    };
    let mut settings: Map<String, Value> = match serde_json::from_str::<Value>(&raw) {
        Ok(Value::Object(m)) => m,
        // json.load returning a non-dict: Python would then `.get` on a non-dict
        // and raise AttributeError; in practice settings.json is an object. A
        // non-object (or decode error) is treated as "nothing to migrate".
        Ok(_) => return,
        Err(_) => return, // json.JSONDecodeError
    };

    let miniflux_url = settings.get("miniflux_url").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let miniflux_key = settings.get("miniflux_api_key").and_then(|v| v.as_str()).unwrap_or("").to_string();

    if miniflux_url.is_empty() || miniflux_key.is_empty() {
        return;
    }

    // Check if a miniflux integration already exists
    let existing = load_integrations();
    for item in &existing {
        if item.get("preset").and_then(|v| v.as_str()) == Some("miniflux") {
            logger::info("Miniflux integration already exists, skipping migration");
            return;
        }
    }

    let mut new_data = Map::new();
    new_data.insert("preset".to_string(), Value::from("miniflux"));
    new_data.insert("base_url".to_string(), Value::from(miniflux_url.trim_end_matches('/').to_string()));
    new_data.insert("api_key".to_string(), Value::from(miniflux_key));
    add_integration(new_data);

    // Clear migrated keys
    settings.remove("miniflux_url");
    settings.remove("miniflux_api_key");
    let serialized =
        serde_json::to_string_pretty(&Value::Object(settings)).unwrap_or_else(|_| "{}".to_string());
    let _ = std::fs::write(&settings_path, serialized);

    logger::info("Migrated Miniflux integration from settings.json");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn presets_have_expected_order_and_membership() {
        // Insertion order matches the Python dict literal.
        let keys: Vec<&str> = INTEGRATION_PRESETS.keys().copied().collect();
        assert_eq!(
            keys,
            vec![
                "miniflux",
                "gitea",
                "linkding",
                "homeassistant",
                "ntfy",
                "vaultwarden",
                "freshrss"
            ]
        );
        // ntfy has auth_type "none" and no auth_header key.
        let ntfy = INTEGRATION_PRESETS.get("ntfy").unwrap();
        assert_eq!(ntfy.get("auth_type").unwrap(), &Value::from("none"));
        assert!(ntfy.get("auth_header").is_none());
    }

    #[test]
    fn strip_html_tags_collapses_whitespace() {
        assert_eq!(
            strip_html_tags("<p>Hello   <b>world</b></p>\n<span>!</span>"),
            "Hello world !"
        );
    }

    #[test]
    fn uuid_hex12_len() {
        assert_eq!(uuid4_hex12().len(), 12);
        assert!(uuid4_hex12().chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn query_value_string_matches_python_str() {
        assert_eq!(query_value_string(&Value::from("abc")), "abc");
        assert_eq!(query_value_string(&Value::from(5)), "5");
        assert_eq!(query_value_string(&Value::from(true)), "true");
        assert_eq!(query_value_string(&Value::from(false)), "false");
        assert_eq!(query_value_string(&Value::Null), "");
    }

    fn integ(api_key: Value) -> Value {
        serde_json::json!({"id": "abc123", "name": "Demo", "api_key": api_key})
    }

    #[test]
    fn mask_integration_secret_masks_first_four_codepoints() {
        // `f"{str(api_key)[:4]}****"`
        let masked = mask_integration_secret(&integ(Value::from("secretvalue")));
        assert_eq!(masked["api_key"], Value::from("secr****"));
        // Other fields are preserved unchanged (dict(integration) copy).
        assert_eq!(masked["id"], Value::from("abc123"));
        assert_eq!(masked["name"], Value::from("Demo"));
    }

    #[test]
    fn mask_integration_secret_slices_by_codepoint() {
        // Unicode: `[:4]` is by code point, not byte.
        let masked = mask_integration_secret(&integ(Value::from("café-token")));
        assert_eq!(masked["api_key"], Value::from("café****"));
    }

    #[test]
    fn mask_integration_secret_leaves_empty_key_untouched() {
        // `if api_key:` — empty string is falsy, so no masking.
        let masked = mask_integration_secret(&integ(Value::from("")));
        assert_eq!(masked["api_key"], Value::from(""));
        // Missing api_key entirely stays missing.
        let no_key = serde_json::json!({"id": "x"});
        let masked2 = mask_integration_secret(&no_key);
        assert_eq!(masked2.get("api_key"), None);
    }

    #[test]
    fn has_plaintext_api_key_detects_unencrypted_rows() {
        // A plaintext (non-`enc:`-prefixed) non-empty key is detected.
        let plain = vec![integ(Value::from("plaintext"))];
        assert!(has_plaintext_api_key(&plain));
        // An already-encrypted key (enc: prefix) is NOT plaintext.
        let enc = vec![integ(Value::from("enc:gAAAAABdummytoken"))];
        assert!(!has_plaintext_api_key(&enc));
        // Empty / missing keys are not plaintext.
        let empty = vec![integ(Value::from("")), serde_json::json!({"id": "n"})];
        assert!(!has_plaintext_api_key(&empty));
        // Mixed: any plaintext row makes the whole list "has plaintext".
        let mixed = vec![integ(Value::from("enc:tok")), integ(Value::from("raw"))];
        assert!(has_plaintext_api_key(&mixed));
    }

    #[test]
    fn encrypt_decrypt_helpers_preserve_non_key_fields_and_skip_empty() {
        // Empty / missing / non-string api_key values are left untouched by both
        // helpers (no encrypt/decrypt call), so no Fernet key is ever needed.
        let rows = vec![
            integ(Value::from("")),
            serde_json::json!({"id": "no-key", "name": "X"}),
            serde_json::json!({"id": "num-key", "api_key": 12345}),
        ];
        let encd = encrypt_integration_secrets(&rows);
        assert_eq!(encd, rows, "untouched rows must round-trip identically");
        let decd = decrypt_integration_secrets(&rows);
        assert_eq!(decd, rows);
    }

    #[test]
    fn decrypt_passes_through_plaintext_without_key() {
        // `secret_storage::decrypt` returns non-`enc:` values unchanged, so this
        // exercises the migration path without forcing key generation.
        let rows = vec![integ(Value::from("plaintext-key"))];
        let decd = decrypt_integration_secrets(&rows);
        assert_eq!(decd[0]["api_key"], Value::from("plaintext-key"));
    }

    #[test]
    fn encrypt_then_decrypt_round_trips_with_temp_data_dir() {
        // Drive a real Fernet round-trip but keep the generated `.app_key` OUT of
        // the live repo `data/` dir by pointing ODYSSEUS_DATA_DIR (which
        // `secret_storage` resolves its key path from) at a unique temp dir.
        // DATA_DIR is a process-wide Lazy: if a sibling test in this binary
        // already resolved it, the env var won't re-apply — but it would then
        // resolve to that sibling's temp dir, never the live repo data dir, so
        // we only assert the round-trip property (encrypted form is opaque and
        // decrypts back to the input), not a fixed location.
        let tmp = std::env::temp_dir().join(format!(
            "odysseus_integ_test_{}_{}",
            std::process::id(),
            uuid4_hex12()
        ));
        std::env::set_var("ODYSSEUS_DATA_DIR", &tmp);

        let rows = vec![integ(Value::from("my-secret-token"))];
        let encd = encrypt_integration_secrets(&rows);
        let enc_key = encd[0]["api_key"].as_str().unwrap();
        // Encrypted at rest: opaque (`enc:` prefix) and not the plaintext.
        assert!(crate::src::secret_storage::is_encrypted(enc_key));
        assert_ne!(enc_key, "my-secret-token");
        // Decrypting the encrypted rows yields the original plaintext back.
        let decd = decrypt_integration_secrets(&encd);
        assert_eq!(decd[0]["api_key"], Value::from("my-secret-token"));

        let _ = std::fs::remove_dir_all(&tmp);
    }
}
