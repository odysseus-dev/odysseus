// src/settings.rs  <- src/settings.py
//! Centralized settings and features management.
//!
//! Single source of truth for reading/writing `data/settings.json` and
//! `data/features.json`. Other modules read config through here instead of
//! touching the files directly.
//!
//! This is the small JSON-read slice of the Python module: [`DEFAULT_SETTINGS`]
//! / [`DEFAULT_FEATURES`], the TTL-cached [`load_settings`] / [`load_features`]
//! merge-over-defaults readers, the [`get_setting`] / [`get_feature`] single-key
//! accessors, and the atomic [`save_settings`] / [`save_features`] writers. It
//! unblocks the `vision_enabled` / `vision_model`, `teacher_enabled` /
//! `teacher_model`, and `research_max_tokens` reads scattered across the
//! feature-engine batch.
//!
//! [`get_user_setting`] IS now ported: it resolves a small whitelist of
//! per-user-overridable keys from the caller's prefs (via
//! [`crate::routes::prefs_store::load_for_user`], a default-profile FS+JSON
//! module that is always compiled) and falls back to [`get_setting`] for every
//! other key or an empty/`None` owner. Per-user resolution therefore works on
//! the server hot paths.

use crate::pytime;
use crate::src::constants::{FEATURES_FILE, SETTINGS_FILE};
use once_cell::sync::Lazy;
use serde_json::{Map, Value};
use std::sync::Mutex;

// Tiny TTL cache for settings/features. `get_setting()` is called on hot paths
// (every chat, every preprocess); without this it re-parses the JSON each call.
// Picks up edits within `_CACHE_TTL` seconds, which is fine for human-edited
// config.
const CACHE_TTL: f64 = 2.0;

// `_settings_cache: tuple[float, dict] | None` / likewise for features. The
// Python globals become `Lazy<Mutex<Option<(monotonic, Map)>>>`.
// The `(f64, Map)` tuple mirrors the Python `tuple[float, dict]` cache slot 1:1.
#[allow(clippy::type_complexity)] // mirrors Python `tuple[float, dict] | None`
static SETTINGS_CACHE: Lazy<Mutex<Option<(f64, Map<String, Value>)>>> = Lazy::new(|| Mutex::new(None));
#[allow(clippy::type_complexity)] // mirrors Python `tuple[float, dict] | None`
static FEATURES_CACHE: Lazy<Mutex<Option<(f64, Map<String, Value>)>>> = Lazy::new(|| Mutex::new(None));

/// `_invalidate_caches()` — drop both cached snapshots (called after a save).
pub fn invalidate_caches() {
    *SETTINGS_CACHE.lock().unwrap() = None;
    *FEATURES_CACHE.lock().unwrap() = None;
}

// ── Default values ──

// The Python `DEFAULT_SETTINGS` dict, verbatim, as a JSON document. Embedded as
// a string (parsed once into an ordered `Map`) rather than the `json!` macro —
// the dict is large enough to blow `serde_json`'s macro recursion limit, and a
// parsed literal keeps the key order under `preserve_order` exactly the same way
// while staying a 1:1 transcription of the Python source. Key-by-key annotations
// from the Python live in `DEFAULT_SETTINGS`'s doc comment below.
const DEFAULT_SETTINGS_JSON: &str = r#"{
  "image_gen_enabled": true,
  "image_model": "",
  "image_quality": "medium",
  "vision_model": "",
  "vision_enabled": true,
  "vision_model_fallbacks": [],
  "app_public_url": "",
  "tts_enabled": true,
  "tts_provider": "disabled",
  "tts_model": "tts-1",
  "tts_voice": "alloy",
  "tts_speed": "1",
  "stt_enabled": false,
  "stt_provider": "disabled",
  "stt_model": "base",
  "stt_language": "",
  "search_provider": "searxng",
  "search_fallback_chain": ["duckduckgo"],
  "search_url": "",
  "search_result_count": 5,
  "brave_api_key": "",
  "google_pse_key": "",
  "google_pse_cx": "",
  "tavily_api_key": "",
  "serper_api_key": "",
  "research_endpoint_id": "",
  "research_model": "",
  "research_search_provider": "",
  "research_max_tokens": 16384,
  "agent_max_tool_calls": 0,
  "agent_input_token_budget": 6000,
  "agent_stream_timeout_seconds": 300,
  "task_endpoint_id": "",
  "task_model": "",
  "default_endpoint_id": "",
  "default_model": "",
  "default_model_fallbacks": [],
  "utility_endpoint_id": "",
  "utility_model": "",
  "utility_model_fallbacks": [],
  "teacher_model": "",
  "teacher_enabled": false,
  "skill_autosave_min_confidence": 0.85,
  "skill_max_injected": 3,
  "search_safesearch": "strict",
  "research_extraction_timeout_seconds": 90,
  "research_extraction_concurrency": 3,
  "research_run_timeout_seconds": 1800,
  "agent_input_token_hard_max": 200000,
  "tool_path_extra_roots": [],
  "web_fetch": true,
  "reminder_channel": "browser",
  "reminder_llm_synthesis": false,
  "reminder_ntfy_topic": "Reminders",
  "reminder_email_to": "",
  "urgent_email_prompt": "Flag as urgent: explicit deadlines, time-sensitive requests, work-blocking issues, messages from people I report to, or anything where a delayed reply costs money/trust. Someone waiting outside, at the door, locked out, or unable to get in is urgent now. Newsletters, marketing, automated digests, and FYI-only updates are NOT urgent.",
  "keybinds": {
    "search": "ctrl+k",
    "toggle_sidebar": "ctrl+b",
    "new_session": "ctrl+alt+n",
    "star_session": "ctrl+alt+s",
    "delete_session": "ctrl+alt+d",
    "admin_panel": "ctrl+shift+u",
    "cancel": "escape"
  }
}"#;

// DEFAULT_FEATURES — our version enables every feature by default (the Python
// source ships `deep_research: false`; we turn it on). A saved features.json still
// overrides these via `merge_over_defaults`, so users can disable any of them.
const DEFAULT_FEATURES_JSON: &str = r#"{
  "web_search": true,
  "deep_research": true,
  "memory": true,
  "document_editor": true,
  "rag": true,
  "sensitive_filter": true,
  "gallery": true
}"#;

/// Default settings, in the exact insertion order of the Python `DEFAULT_SETTINGS`
/// dict (`serde_json`'s `preserve_order` feature preserves the parsed key order),
/// so this doubles as the ordered template `load_settings` merges saved values
/// over. Python key annotations:
/// - `vision_model_fallbacks`: ordered Vision-model fallback chain.
/// - `app_public_url`: public base URL for clickable deep-links in alerts.
/// - `search_fallback_chain`: tried when the primary search provider fails.
/// - `default_model_fallbacks` / `utility_model_fallbacks`: ordered chat /
///   utility model fallbacks (`{"endpoint_id","model"}` entries).
/// - `skill_autosave_min_confidence`: min confidence for an auto-written DRAFT
///   skill to be injected (0 disables the gate).
/// - `urgent_email_prompt`: email-triage urgency rules.
/// - `keybinds`: keyboard shortcuts (action -> key combination).
pub static DEFAULT_SETTINGS: Lazy<Map<String, Value>> = Lazy::new(|| {
    match serde_json::from_str::<Value>(DEFAULT_SETTINGS_JSON) {
        Ok(Value::Object(m)) => m,
        _ => unreachable!("DEFAULT_SETTINGS_JSON is a valid JSON object"),
    }
});

/// Default feature flags, in the Python `DEFAULT_FEATURES` order.
pub static DEFAULT_FEATURES: Lazy<Map<String, Value>> = Lazy::new(|| {
    match serde_json::from_str::<Value>(DEFAULT_FEATURES_JSON) {
        Ok(Value::Object(m)) => m,
        _ => unreachable!("DEFAULT_FEATURES_JSON is a valid JSON object"),
    }
});

// ── Helpers ──

/// `merged = {**DEFAULTS, **saved}` — Python dict-merge: start from the ordered
/// defaults, then overlay saved values. An existing key keeps its position (its
/// value is overwritten); a saved-only key is appended. `serde_json::Map` with
/// `preserve_order` is an `IndexMap`, so `insert` reproduces this exactly.
fn merge_over_defaults(defaults: &Map<String, Value>, saved: &Map<String, Value>) -> Map<String, Value> {
    let mut merged = defaults.clone();
    for (k, v) in saved {
        merged.insert(k.clone(), v.clone());
    }
    merged
}

/// Read+parse a JSON object file, returning `None` on `FileNotFoundError` or
/// `json.JSONDecodeError` (the two exceptions Python swallows into the defaults
/// path). A non-object top-level JSON parses but is `None` here — Python's
/// `{**DEFAULTS, **saved}` would `TypeError` on a list, but in practice the file
/// is always an object; treating non-objects as "use defaults" is the safe,
/// non-panicking equivalent.
fn read_object_file(path: &str) -> Option<Map<String, Value>> {
    let text = std::fs::read_to_string(path).ok()?;
    match serde_json::from_str::<Value>(&text) {
        Ok(Value::Object(m)) => Some(m),
        _ => None,
    }
}

// ── Settings (data/settings.json) ──

/// Load settings merged with defaults. Always returns a complete map.
pub fn load_settings() -> Map<String, Value> {
    // now = time.monotonic(); if cache fresh: return cached
    let now = pytime::monotonic();
    {
        let cache = SETTINGS_CACHE.lock().unwrap();
        if let Some((ts, ref map)) = *cache {
            if (now - ts) < CACHE_TTL {
                return map.clone();
            }
        }
    }
    let merged = match read_object_file(&SETTINGS_FILE) {
        Some(saved) => merge_over_defaults(&DEFAULT_SETTINGS, &saved),
        None => DEFAULT_SETTINGS.clone(),
    };
    *SETTINGS_CACHE.lock().unwrap() = Some((now, merged.clone()));
    merged
}

/// Persist settings to disk (atomic; see `core::atomic_io`).
pub fn save_settings(settings: &Map<String, Value>) -> crate::error::PyResult<()> {
    crate::core::atomic_io::atomic_write_json(&SETTINGS_FILE, &Value::Object(settings.clone()), Some(2))?;
    invalidate_caches();
    Ok(())
}

/// Read a single setting value (`load_settings().get(key, default)`).
///
/// Returns an owned `Value`; pass `Value::Null` for the Python `default=None`.
pub fn get_setting(key: &str, default: Value) -> Value {
    load_settings().get(key).cloned().unwrap_or(default)
}

/// `is_setting_overridden(key)` — true if `key` is explicitly present in the saved
/// settings file (NOT merely a default). `load_settings` merges DEFAULT_SETTINGS
/// with the saved file, so a value can equal its default yet still be explicitly
/// set; this distinguishes the two (used by the adaptive token-budget path).
pub fn is_setting_overridden(key: &str) -> bool {
    match read_object_file(&SETTINGS_FILE) {
        Some(saved) => saved.contains_key(key),
        None => false,
    }
}

/// Per-user settings whitelist (`_PER_USER_KEYS`, src/settings.py:166-175). Only
/// these keys are eligible for per-user override; any other key collapses to the
/// global `get_setting`. A `&[&str]` (membership test only) — order irrelevant.
const PER_USER_KEYS: [&str; 14] = [
    "vision_model", "vision_enabled", "vision_model_fallbacks",
    "image_model", "image_gen_enabled", "image_quality",
    "default_endpoint_id", "default_model", "default_model_fallbacks",
    "utility_endpoint_id", "utility_model", "utility_model_fallbacks",
    "research_endpoint_id", "research_model",
];

/// `get_user_setting(key, owner="", default=None)` (src/settings.py:178-194).
///
/// Resolve `key` from the caller's per-user prefs first, falling back to the
/// global setting. Only the `PER_USER_KEYS` whitelist is eligible — for any
/// other key (or an empty/None owner) this is equivalent to `get_setting`.
///
/// `owner` is `Option<&str>`: Python's `owner: str = ""` truthiness (`if owner
/// and key in _PER_USER_KEYS`) maps to `Some(o) if !o.is_empty()`. The
/// emptiness gate on the pref value is `prefs[key] not in (None, "")` — ONLY
/// JSON null and the empty string fall through to the global; an empty array /
/// `false` / `0` are RETURNED as-is (this matters for the `*_fallbacks` keys and
/// `*_enabled` flags). `prefs_store::load_for_user` never raises (the Python
/// `try/except Exception: pass` import-cycle guard is unnecessary — it is a
/// plain function call here — but the behavior is identical: a missing/corrupt
/// store yields `{}`, so we fall through to `get_setting`).
pub fn get_user_setting(key: &str, owner: Option<&str>, default: Value) -> Value {
    if let Some(owner) = owner.filter(|o| !o.is_empty()) {
        if PER_USER_KEYS.contains(&key) {
            let prefs = crate::routes::prefs_store::load_for_user(Some(owner));
            if let Some(v) = prefs.get(key) {
                // `if key in prefs and prefs[key] not in (None, ""):`
                let falls_through = v.is_null()
                    || v.as_str().map(|s| s.is_empty()).unwrap_or(false);
                if !falls_through {
                    return v.clone();
                }
            }
        }
    }
    get_setting(key, default)
}

// ── Features (data/features.json) ──

/// Load feature flags merged with defaults.
pub fn load_features() -> Map<String, Value> {
    let now = pytime::monotonic();
    {
        let cache = FEATURES_CACHE.lock().unwrap();
        if let Some((ts, ref map)) = *cache {
            if (now - ts) < CACHE_TTL {
                return map.clone();
            }
        }
    }
    let merged = match read_object_file(&FEATURES_FILE) {
        Some(saved) => merge_over_defaults(&DEFAULT_FEATURES, &saved),
        None => DEFAULT_FEATURES.clone(),
    };
    *FEATURES_CACHE.lock().unwrap() = Some((now, merged.clone()));
    merged
}

/// Persist feature flags to disk (atomic).
pub fn save_features(features: &Map<String, Value>) -> crate::error::PyResult<()> {
    crate::core::atomic_io::atomic_write_json(&FEATURES_FILE, &Value::Object(features.clone()), Some(2))?;
    invalidate_caches();
    Ok(())
}

/// Single feature-flag accessor (`load_features().get(key, default)`). Not a
/// separate Python function, but the natural feature-side mirror of
/// [`get_setting`] for callers that need one flag.
pub fn get_feature(key: &str, default: Value) -> Value {
    load_features().get(key).cloned().unwrap_or(default)
}

#[cfg(test)]
mod tests {
    // Parity checks against the live Python (`src.settings`).
    use super::*;
    use serde_json::json;

    #[test]
    fn urgent_email_prompt_exact_string() {
        // Captured from CPython repr(DEFAULT_SETTINGS['urgent_email_prompt']).
        let expected = "Flag as urgent: explicit deadlines, time-sensitive requests, work-blocking issues, messages from people I report to, or anything where a delayed reply costs money/trust. Someone waiting outside, at the door, locked out, or unable to get in is urgent now. Newsletters, marketing, automated digests, and FYI-only updates are NOT urgent.";
        assert_eq!(
            DEFAULT_SETTINGS.get("urgent_email_prompt").and_then(|v| v.as_str()),
            Some(expected)
        );
    }

    #[test]
    fn defaults_have_expected_keys_and_order() {
        let d = &*DEFAULT_SETTINGS;
        // First key is image_gen_enabled, value True.
        assert_eq!(d.get("image_gen_enabled"), Some(&json!(true)));
        assert_eq!(d.get("research_max_tokens"), Some(&json!(16384)));
        assert_eq!(d.get("teacher_enabled"), Some(&json!(false)));
        assert_eq!(d.get("vision_enabled"), Some(&json!(true)));
        // Nested keybinds dict preserved.
        assert_eq!(
            d.get("keybinds").and_then(|k| k.get("search")),
            Some(&json!("ctrl+k"))
        );
        // Insertion order: image_gen_enabled is first.
        assert_eq!(d.keys().next().map(String::as_str), Some("image_gen_enabled"));
    }

    #[test]
    fn merge_overlays_saved_over_defaults_in_place() {
        let mut saved = Map::new();
        saved.insert("vision_enabled".to_string(), json!(false));
        saved.insert("brand_new_key".to_string(), json!("x"));
        let merged = merge_over_defaults(&DEFAULT_SETTINGS, &saved);
        // Overridden value, original position.
        assert_eq!(merged.get("vision_enabled"), Some(&json!(false)));
        // New key appended at the end.
        assert_eq!(
            merged.keys().next_back().map(String::as_str),
            Some("brand_new_key")
        );
        // Untouched default still present.
        assert_eq!(merged.get("research_max_tokens"), Some(&json!(16384)));
    }

    #[test]
    fn get_setting_falls_back_to_default_when_absent() {
        // A key that is in neither defaults nor any saved file returns our default.
        let v = get_setting("a_key_that_does_not_exist_anywhere", json!("fallback"));
        assert_eq!(v, json!("fallback"));
    }

    #[test]
    fn read_object_file_missing_is_none() {
        assert!(read_object_file("/nonexistent/path/settings.json").is_none());
    }

    #[test]
    fn read_object_file_round_trips() {
        let dir = std::env::temp_dir();
        let p = dir.join(format!("odys_settings_test_{}.json", std::process::id()));
        std::fs::write(&p, r#"{"vision_model": "vl-7b", "research_max_tokens": 99}"#).unwrap();
        let m = read_object_file(p.to_str().unwrap()).unwrap();
        assert_eq!(m.get("vision_model"), Some(&json!("vl-7b")));
        let merged = merge_over_defaults(&DEFAULT_SETTINGS, &m);
        assert_eq!(merged.get("research_max_tokens"), Some(&json!(99)));
        // vision_model overridden, default vision_enabled untouched.
        assert_eq!(merged.get("vision_enabled"), Some(&json!(true)));
        let _ = std::fs::remove_file(&p);
    }

    // `get_user_setting` parity tests. These touch the CWD-relative
    // `data/user_prefs.json` (via `prefs_store::load_for_user`), so they MUST take
    // the shared `prefs_store::TEST_CWD_LOCK` and chdir into a fresh temp dir to
    // avoid cross-test CWD races.
    //
    // NOTE: the GLOBAL `get_setting`/`load_settings` side reads
    // [`crate::src::constants::SETTINGS_FILE`], which is derived from
    // `CARGO_MANIFEST_DIR` (the repo `data/settings.json`) — NOT CWD-relative — so
    // a temp `data/settings.json` cannot control it. These tests therefore assert
    // the per-user OVERRIDE vs. the fall-through CONTRACT (compared against the
    // live `get_setting` for the same key), never a hardcoded global value: the
    // contract is "whitelisted pref present -> pref; else -> exactly get_setting".
    use std::sync::MutexGuard;

    struct PrefsCwd {
        _guard: MutexGuard<'static, ()>,
        prev: std::path::PathBuf,
        _dir: tempfile::TempDir,
    }

    impl PrefsCwd {
        fn enter() -> Self {
            let guard = crate::routes::prefs_store::TEST_CWD_LOCK
                .lock()
                .unwrap_or_else(|e| e.into_inner());
            let prev = std::env::current_dir().unwrap();
            let dir = tempfile::tempdir().unwrap();
            std::env::set_current_dir(dir.path()).unwrap();
            PrefsCwd { _guard: guard, prev, _dir: dir }
        }
        fn write_prefs(&self, v: Value) {
            std::fs::create_dir_all("data").unwrap();
            std::fs::write("data/user_prefs.json", serde_json::to_string(&v).unwrap()).unwrap();
        }
    }

    impl Drop for PrefsCwd {
        fn drop(&mut self) {
            let _ = std::env::set_current_dir(&self.prev);
        }
    }

    #[test]
    fn get_user_setting_whitelisted_pref_wins() {
        let cwd = PrefsCwd::enter();
        // Per-user `_users` store: alice picks her own default_model — a
        // distinctive value that cannot be the repo global.
        cwd.write_prefs(json!({"_users": {"alice": {"default_model": "alice-only-model-xyz"}}}));
        // Whitelisted key + owner -> per-user value wins (independent of global).
        assert_eq!(
            get_user_setting("default_model", Some("alice"), json!("DEF")),
            json!("alice-only-model-xyz")
        );
        // A user with no override falls through to EXACTLY the global get_setting.
        assert_eq!(
            get_user_setting("default_model", Some("bob"), json!("DEF")),
            get_setting("default_model", json!("DEF"))
        );
    }

    #[test]
    fn get_user_setting_non_whitelisted_ignores_prefs() {
        let cwd = PrefsCwd::enter();
        // `task_model` is NOT in PER_USER_KEYS; a user pref must be ignored and
        // the result is EXACTLY the global get_setting.
        cwd.write_prefs(json!({"_users": {"alice": {"task_model": "alice-task-should-be-ignored"}}}));
        assert_eq!(
            get_user_setting("task_model", Some("alice"), json!("DEF")),
            get_setting("task_model", json!("DEF"))
        );
    }

    #[test]
    fn get_user_setting_none_owner_collapses_to_global() {
        let cwd = PrefsCwd::enter();
        cwd.write_prefs(json!({"_users": {"alice": {"default_model": "alice-only-model-xyz"}}}));
        // owner=None -> global, never the per-user store.
        assert_eq!(
            get_user_setting("default_model", None, json!("DEF")),
            get_setting("default_model", json!("DEF"))
        );
        // Empty-string owner is also falsy -> global.
        assert_eq!(
            get_user_setting("default_model", Some(""), json!("DEF")),
            get_setting("default_model", json!("DEF"))
        );
    }

    #[test]
    fn get_user_setting_emptiness_gate() {
        let cwd = PrefsCwd::enter();
        // Empty-string pref falls through to the global; empty-array / false prefs
        // are returned AS-IS (the `*_fallbacks` / `*_enabled` keys rely on this).
        cwd.write_prefs(json!({"_users": {"alice": {
            "default_model": "",
            "default_model_fallbacks": [],
            "vision_enabled": false,
        }}}));
        // "" -> falls through to EXACTLY the global get_setting.
        assert_eq!(
            get_user_setting("default_model", Some("alice"), json!("DEF")),
            get_setting("default_model", json!("DEF"))
        );
        // [] -> returned as-is (NOT treated as falsy), so the per-user empty list
        // overrides whatever the global chain is.
        assert_eq!(
            get_user_setting("default_model_fallbacks", Some("alice"), json!(["SENTINEL"])),
            json!([])
        );
        // false -> returned as-is (overrides the global, even if global is true).
        assert_eq!(
            get_user_setting("vision_enabled", Some("alice"), json!("SENTINEL")),
            json!(false)
        );
    }
}
