// src/task_endpoint.rs  <- src/task_endpoint.py
//! Shared resolver for background-task AI endpoint (auto-naming, memory, sorting).
//!
//! The Python is a one-line delegate:
//! `resolve_endpoint("task", fallback_url, fallback_model, fallback_headers,
//! owner=owner)`. This module mirrors that exactly, forwarding to the SINGLE
//! ported resolver [`crate::src::endpoint_resolver::resolve_endpoint_with_fallback`]
//! rather than re-implementing the settings cascade. That resolver is the
//! faithful port of `src.endpoint_resolver.resolve_endpoint` and carries every
//! observable behavior the previous open-coded cascade dropped:
//! - the per-user `get_user_setting` override layer (so `owner` is threaded into
//!   every `{prefix}_endpoint_id`/`{prefix}_model` read);
//! - the early `if not ep_id and fallback_url and fallback_model` short-circuit;
//! - the `is_enabled == True` + `owner_filter` row lookup (NOT the unfiltered
//!   `endpoint_auth`, which would resolve a disabled / cross-owner endpoint);
//! - the hidden-model discard + first-enabled-chat-model auto-pick;
//! - the `model or fallback_model` final gate.
//!
//! The only impedance mismatch is the headers type: the public Python dict shape
//! is `Map<String, Value>` here, while the resolver returns an
//! `IndexMap<String, String>` — converted at the boundary in both directions.

use serde_json::{Map, Value};

/// Return (endpoint_url, model, headers) for background tasks.
///
/// Reads task_endpoint_id / task_model from settings (per-user override via
/// `owner`, falling back to admin settings).
/// Falls back to the provided values when the setting is empty or the
/// endpoint cannot be resolved.
///
/// `return resolve_endpoint("task", fallback_url, fallback_model,
/// fallback_headers, owner=owner)`.
pub fn resolve_task_endpoint(
    fallback_url: Option<String>,
    fallback_model: Option<String>,
    fallback_headers: Option<Map<String, Value>>,
    owner: Option<&str>,
) -> (Option<String>, Option<String>, Option<Map<String, Value>>) {
    // Python passes the headers dict straight through; the resolver's fallback
    // header type is IndexMap<String,String>, so flatten the JSON dict (string
    // values only — the only shape build_headers ever produces) on the way in.
    let fb_headers: Option<indexmap::IndexMap<String, String>> = fallback_headers.map(|m| {
        m.into_iter()
            .map(|(k, v)| (k, v.as_str().map(|s| s.to_string()).unwrap_or_default()))
            .collect()
    });

    let (url, model, headers) = crate::src::endpoint_resolver::resolve_endpoint_with_fallback(
        "task",
        fallback_url.as_deref(),
        fallback_model.as_deref(),
        fb_headers.as_ref(),
        owner,
    );

    // headers IndexMap<String,String> -> the dict shape Python returns.
    let headers = headers.map(|m| {
        let mut out = Map::new();
        for (k, v) in m {
            out.insert(k, Value::String(v));
        }
        out
    });

    (url, model, headers)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::sync::MutexGuard;

    // This end-to-end test exercises BOTH process-global side channels:
    //   * the CWD-relative per-user prefs store (`data/user_prefs.json`, read by
    //     `settings::get_user_setting` -> `prefs_store::load_for_user`), guarded by
    //     `prefs_store::TEST_CWD_LOCK`; and
    //   * the `DATABASE_URL` env var (read by `model_endpoints::endpoint_auth`),
    //     guarded by `database::DB_TEST_LOCK`.
    // No other test in the crate takes both locks, so taking them in this fixed
    // order (CWD first, then DB) cannot deadlock; the order is documented here so
    // any future double-lock test follows the same convention.
    //
    // NOTE: admin settings (`get_setting`/`load_settings`) read
    // `CARGO_MANIFEST_DIR/data/settings.json`, which is absent in the test
    // sandbox, so every admin `*_endpoint_id` resolves to "" — the only non-empty
    // endpoint id can come from the per-user pref, which is exactly what proves
    // the `owner` is threaded.
    struct Harness {
        _cwd_guard: MutexGuard<'static, ()>,
        _db_guard: MutexGuard<'static, ()>,
        prev_cwd: std::path::PathBuf,
        prev_db: Option<String>,
        _cwd_dir: tempfile::TempDir,
        _db_dir: tempfile::TempDir,
    }

    impl Harness {
        fn enter() -> Self {
            let cwd_guard = crate::routes::prefs_store::TEST_CWD_LOCK
                .lock()
                .unwrap_or_else(|e| e.into_inner());
            let db_guard = crate::core::database::DB_TEST_LOCK
                .lock()
                .unwrap_or_else(|e| e.into_inner());
            let prev_cwd = std::env::current_dir().unwrap();
            let cwd_dir = tempfile::tempdir().unwrap();
            std::env::set_current_dir(cwd_dir.path()).unwrap();
            // The settings file (`src::constants::SETTINGS_FILE`) is an absolute
            // path under the dev data dir, NOT CWD/env-isolated, so other tests'
            // `save_settings` calls leave stale utility/task/default endpoint ids
            // persisted there — which would hijack the resolve_endpoint cascade
            // before it reaches this test's per-user default. Reset the cascade
            // keys to their DEFAULT_SETTINGS empties (save_settings invalidates
            // the cache) so the cascade falls through to the per-user prefs.
            let mut clean = crate::src::settings::load_settings();
            for k in [
                "task_endpoint_id", "utility_endpoint_id", "default_endpoint_id",
                "task_model", "utility_model", "default_model",
            ] {
                clean.insert(k.to_string(), serde_json::json!(""));
            }
            let _ = crate::src::settings::save_settings(&clean);

            let prev_db = std::env::var("DATABASE_URL").ok();
            let db_dir = tempfile::tempdir().unwrap();
            let db_path = db_dir.path().join("app.db");
            std::env::set_var(
                "DATABASE_URL",
                format!("sqlite:///{}", db_path.display()),
            );
            crate::core::database::create_all().unwrap();

            Harness {
                _cwd_guard: cwd_guard,
                _db_guard: db_guard,
                prev_cwd,
                prev_db,
                _cwd_dir: cwd_dir,
                _db_dir: db_dir,
            }
        }

        fn write_prefs(&self, v: Value) {
            std::fs::create_dir_all("data").unwrap();
            std::fs::write("data/user_prefs.json", serde_json::to_string(&v).unwrap()).unwrap();
        }
    }

    impl Drop for Harness {
        fn drop(&mut self) {
            let _ = std::env::set_current_dir(&self.prev_cwd);
            match &self.prev_db {
                Some(v) => std::env::set_var("DATABASE_URL", v),
                None => std::env::remove_var("DATABASE_URL"),
            }
        }
    }

    /// With `owner=Some("alice")` and alice having a per-user `default_endpoint_id`
    /// pref pointing at a real enabled endpoint, the task cascade falls through the
    /// (admin-empty) task/utility ids to alice's per-user default, resolving it.
    /// This is only reachable if `owner` is threaded into every settings read.
    ///
    /// IMPORTANT — `fallback_model` is `None` here on purpose. The Python
    /// `resolve_endpoint` has an early short-circuit (endpoint_resolver.py:241):
    /// `if not ep_id and fallback_url and fallback_model: return fallback...`.
    /// With the admin `task_endpoint_id` empty, supplying BOTH a fallback url AND
    /// model returns the fallback tuple immediately — BEFORE the per-user default
    /// cascade is ever consulted. To exercise (and prove the `owner` threading of)
    /// that cascade faithfully, the caller must leave at least one fallback unset;
    /// we keep `fallback_url=Some("FB_URL")` but `fallback_model=None`, so the
    /// short-circuit is dormant and alice's per-user default resolves.
    #[test]
    fn owner_threads_per_user_default_into_cascade() {
        let h = Harness::enter();
        // A real enabled endpoint in the temp DB.
        let ep_id = crate::core::model_endpoints::create(
            "alice-ep",
            "https://alice.example/v1",
            Some("sk-alice"),
            &["alice-model".to_string()],
            None,
        )
        .unwrap();
        // alice's per-user prefs: whitelisted default_* keys point at that endpoint.
        h.write_prefs(json!({"_users": {"alice": {
            "default_endpoint_id": ep_id,
            "default_model": "alice-model",
        }}}));

        let (url, model, headers) = resolve_task_endpoint(
            Some("FB_URL".into()),
            None,
            None,
            Some("alice"),
        );
        // The per-user default resolved -> NOT the fallback tuple.
        assert_eq!(url.as_deref(), Some("https://alice.example/v1/chat/completions"));
        assert_eq!(model.as_deref(), Some("alice-model"));
        let headers = headers.expect("headers built for resolved endpoint");
        assert_eq!(
            headers.get("Authorization").and_then(|v| v.as_str()),
            Some("Bearer sk-alice")
        );
    }

    /// The SAME prefs/DB state with `owner=None` must NOT see alice's per-user
    /// override: every settings read collapses to the (empty) admin global, the id
    /// stays empty, and the function returns the fallback tuple unchanged.
    #[test]
    fn none_owner_ignores_per_user_default_and_falls_back() {
        let h = Harness::enter();
        let ep_id = crate::core::model_endpoints::create(
            "alice-ep",
            "https://alice.example/v1",
            Some("sk-alice"),
            &["alice-model".to_string()],
            None,
        )
        .unwrap();
        h.write_prefs(json!({"_users": {"alice": {
            "default_endpoint_id": ep_id,
            "default_model": "alice-model",
        }}}));

        let (url, model, headers) = resolve_task_endpoint(
            Some("FB_URL".into()),
            Some("FB_MODEL".into()),
            None,
            None,
        );
        // owner=None never consults the per-user store; admin settings are empty.
        assert_eq!(url.as_deref(), Some("FB_URL"));
        assert_eq!(model.as_deref(), Some("FB_MODEL"));
        assert!(headers.is_none());
    }

    /// A user with no override (and empty admin settings) also falls back — the
    /// owner is threaded but finds nothing, so the fallback tuple is returned.
    #[test]
    fn unknown_owner_with_no_pref_falls_back() {
        let h = Harness::enter();
        h.write_prefs(json!({"_users": {"alice": {"default_model": "alice-model"}}}));

        let (url, model, headers) = resolve_task_endpoint(
            Some("FB_URL".into()),
            Some("FB_MODEL".into()),
            None,
            Some("bob"),
        );
        assert_eq!(url.as_deref(), Some("FB_URL"));
        assert_eq!(model.as_deref(), Some("FB_MODEL"));
        assert!(headers.is_none());
    }
}
