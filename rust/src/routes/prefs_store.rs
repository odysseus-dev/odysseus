// routes/prefs_store.rs  <- routes/prefs_routes.py (the `_load`/`_save`/
//                            `_load_for_user`/`_save_for_user` helpers)
//! Shared user-preferences JSON store, backed by `data/user_prefs.json`.
//!
//! In Python these four helpers live at module scope in `routes/prefs_routes.py`
//! and are imported by *many* other route modules — `backup_routes`,
//! `task_routes`, `model_routes`, `chat_helpers`, `calendar_routes`,
//! `skills_routes`, `auth_routes` — via
//! `from routes.prefs_routes import _load_for_user, _save_for_user`.
//!
//! To avoid duplicating the `_users`-vs-flat + auth-disabled (`user is None`)
//! backward-compat semantics in every translated router, they are hoisted here
//! as a single shared module (foundation unit F6). `prefs_routes` itself, plus
//! every later importer, re-exports / calls these.
//!
//! Behaviour is a faithful translation of the Python:
//!
//! ```python
//! PREFS_FILE = os.path.join("data", "user_prefs.json")
//!
//! def _load():
//!     try:
//!         with open(PREFS_FILE, "r") as f:
//!             return json.load(f)
//!     except (FileNotFoundError, json.JSONDecodeError):
//!         return {}
//!
//! def _save(prefs):
//!     os.makedirs(os.path.dirname(PREFS_FILE), exist_ok=True)
//!     with open(PREFS_FILE, "w") as f:
//!         json.dump(prefs, f, indent=2)
//!
//! def _load_for_user(user=None):
//!     all_prefs = _load()
//!     if "_users" in all_prefs:
//!         if user is None:
//!             users = all_prefs["_users"]
//!             return dict(next(iter(users.values()), {}))
//!         return dict(all_prefs["_users"].get(user, {}))
//!     return dict(all_prefs)
//!
//! def _save_for_user(user, prefs):
//!     all_prefs = _load()
//!     if user is None:
//!         _save(prefs)
//!         return
//!     if "_users" not in all_prefs:
//!         all_prefs = {"_users": {}}
//!     all_prefs["_users"][user] = prefs
//!     _save(all_prefs)
//! ```
//!
//! Pure FS + JSON logic with no `AppState` dependency, so it lives on the
//! default profile (the `web`-gated routers simply call into it).

use crate::pyos as os;
use serde_json::{json, Value};

/// `PREFS_FILE = os.path.join("data", "user_prefs.json")`.
///
/// CWD-relative, exactly like the Python constant — the process runs from the
/// repo root, so `data/` is resolved against the working directory. (Kept as the
/// joined literal rather than a `&str` const so `os.path.dirname` is applied to
/// the same value the Python computes.)
fn prefs_file() -> String {
    os::path::join("data", "user_prefs.json")
}

/// `_load()` — load the raw prefs file (internal use only).
///
/// Returns `{}` on a missing file *or* a JSON decode error, matching the Python
/// `except (FileNotFoundError, json.JSONDecodeError)` branch. Non-object JSON
/// values (arrays, scalars) are also coerced to `{}`, matching the Python
/// `return data if isinstance(data, dict) else {}` guard.
pub fn load() -> Value {
    // try: with open(PREFS_FILE, "r") as f: return json.load(f)
    let raw = match std::fs::read_to_string(prefs_file()) {
        Ok(s) => s,
        // except FileNotFoundError (and, in practice, any other open error the
        // Python `open` would raise — read errors degrade to the same `{}`).
        Err(_) => return json!({}),
    };
    match serde_json::from_str::<Value>(&raw) {
        // return data if isinstance(data, dict) else {}
        Ok(v) if v.is_object() => v,
        // Non-object payload (array, scalar) -> coerce to {}, same as Python.
        Ok(_) => json!({}),
        // except json.JSONDecodeError
        Err(_) => json!({}),
    }
}

/// `_save(prefs)` — write the raw prefs file with `indent=2`.
///
/// Mirrors `os.makedirs(os.path.dirname(PREFS_FILE), exist_ok=True)` then
/// `json.dump(prefs, f, indent=2)`. `serde_json::to_string_pretty` uses a
/// two-space indent, matching Python's `indent=2`. Errors surface as
/// `io::Error` (the Python `open(..., "w")` / `json.dump` would raise too).
pub fn save(prefs: &Value) -> std::io::Result<()> {
    let path = prefs_file();
    // os.makedirs(os.path.dirname(PREFS_FILE), exist_ok=True)
    os::makedirs(&os::path::dirname(&path), true)?;
    // json.dump(prefs, f, indent=2)
    let text = serde_json::to_string_pretty(prefs).map_err(std::io::Error::other)?;
    std::fs::write(&path, text)
}

/// `_load_for_user(user=None)` — load preferences for a specific user.
///
/// * `_users` present, `user is None` (auth disabled): first user's prefs
///   (insertion order — `serde_json` is built with `preserve_order`, so the
///   first key matches Python's `next(iter(users.values()))`), or `{}` if there
///   are no users.
/// * `_users` present, `user` given: `_users.get(user, {})`.
/// * legacy flat format (no `_users`): the whole object, returned as-is.
///
/// Each branch returns an owned copy (Python `dict(...)`). If the stored value
/// is not an object (which the Python `dict(...)` would reject), the empty
/// object `{}` is returned — the load-path never panics.
pub fn load_for_user(user: Option<&str>) -> Value {
    let all_prefs = load();
    // if "_users" in all_prefs:
    if let Some(users) = all_prefs.get("_users") {
        match user {
            // if user is None: return dict(next(iter(users.values()), {}))
            None => {
                let first = users
                    .as_object()
                    .and_then(|m| m.values().next())
                    .cloned()
                    .unwrap_or_else(|| json!({}));
                as_object_copy(first)
            }
            // return dict(all_prefs["_users"].get(user, {}))
            Some(u) => {
                let v = users.get(u).cloned().unwrap_or_else(|| json!({}));
                as_object_copy(v)
            }
        }
    } else {
        // Legacy flat format — return as-is. return dict(all_prefs)
        as_object_copy(all_prefs)
    }
}

/// `_save_for_user(user, prefs)` — save preferences for a specific user.
///
/// * `user is None` (auth disabled) **and** `_users` map exists: write `prefs`
///   back into the *first* `_users` slot — preserving all other users — exactly
///   like the Python guard added at commit 7c7ac10.  Without an existing map,
///   fall through to a flat save (`_save(prefs)`).
/// * `user is None`, no `_users` map: save `prefs` flat (`_save(prefs)`).
/// * named user: ensure the `_users` wrapper exists (replacing a legacy flat
///   file with `{"_users": {}}`, exactly like the Python `all_prefs = {"_users":
///   {}}` reassignment), set `_users[user] = prefs`, then save the whole thing.
pub fn save_for_user(user: Option<&str>, prefs: &Value) -> std::io::Result<()> {
    let mut all_prefs = load();
    // if user is None:
    let Some(user) = user else {
        // Auth disabled. If the store already has a `_users` map, writing flat
        // would destroy every other user's prefs. Write back into the first slot
        // instead — matching _load_for_user(None)'s read path — and preserve the
        // rest. This mirrors the Python block added in the upstream parity diff:
        //   if "_users" in all_prefs:
        //       users = all_prefs["_users"]
        //       first_key = next(iter(users), None)
        //       if first_key is not None:
        //           users[first_key] = prefs; _save(all_prefs); return
        //   _save(prefs); return
        if let Some(users) = all_prefs.get_mut("_users").and_then(|v| v.as_object_mut()) {
            // first_key = next(iter(users), None)
            if let Some(first_key) = users.keys().next().cloned() {
                users.insert(first_key, prefs.clone());
                return save(&all_prefs);
            }
        }
        return save(prefs);
    };
    // if "_users" not in all_prefs: all_prefs = {"_users": {}}
    if all_prefs.get("_users").and_then(|v| v.as_object()).is_none() {
        all_prefs = json!({ "_users": {} });
    }
    // all_prefs["_users"][user] = prefs
    if let Some(users) = all_prefs
        .get_mut("_users")
        .and_then(|v| v.as_object_mut())
    {
        users.insert(user.to_string(), prefs.clone());
    }
    // _save(all_prefs)
    save(&all_prefs)
}

/// Python `dict(x)`: returns the object verbatim when `x` is a mapping, else the
/// empty object. (Python would raise on a non-mapping; here we degrade to `{}`
/// so a corrupt store never crashes a read — the same defensive spirit as the
/// `_load` decode-error branch.)
fn as_object_copy(v: Value) -> Value {
    if v.is_object() {
        v
    } else {
        json!({})
    }
}

/// Process-global lock serializing every test that chdir's around the single
/// CWD-relative `data/user_prefs.json`.
///
/// The prefs store has no path override — it always resolves `data/user_prefs.json`
/// against the working directory — so tests isolate themselves by `chdir`-ing into
/// a fresh temp dir. `current_dir` is process-global, so ALL such tests (this
/// module's *and* `prefs_routes`' handler tests, which drive this same store) must
/// take ONE shared lock; a per-module lock would let the two pools race the CWD.
/// Hence this is hoisted to module scope (`cfg(test)`, `pub(crate)`) so both test
/// modules reference the identical static.
#[cfg(test)]
pub(crate) static TEST_CWD_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::sync::MutexGuard;

    // The store is a single CWD-relative file, so these tests must not run in
    // parallel against the same `data/user_prefs.json`. Each test takes the
    // shared [`super::TEST_CWD_LOCK`], chdir's into a fresh temp dir (so
    // `data/user_prefs.json` is isolated), runs, and the guard is released on
    // drop. `current_dir` is process-global, hence the serializing mutex.
    use super::TEST_CWD_LOCK as CWD_LOCK;

    struct TempCwd {
        _guard: MutexGuard<'static, ()>,
        prev: std::path::PathBuf,
        _dir: tempfile::TempDir,
    }

    impl TempCwd {
        fn enter() -> Self {
            let guard = CWD_LOCK.lock().unwrap_or_else(|e| e.into_inner());
            let prev = std::env::current_dir().unwrap();
            let dir = tempfile::tempdir().unwrap();
            std::env::set_current_dir(dir.path()).unwrap();
            TempCwd {
                _guard: guard,
                prev,
                _dir: dir,
            }
        }
    }

    impl Drop for TempCwd {
        fn drop(&mut self) {
            let _ = std::env::set_current_dir(&self.prev);
        }
    }

    #[test]
    fn load_missing_file_yields_empty_object() {
        let _cwd = TempCwd::enter();
        assert_eq!(load(), json!({}));
    }

    #[test]
    fn load_corrupt_json_yields_empty_object() {
        let _cwd = TempCwd::enter();
        std::fs::create_dir_all("data").unwrap();
        std::fs::write("data/user_prefs.json", "{not valid json").unwrap();
        assert_eq!(load(), json!({}));
    }

    #[test]
    fn save_then_load_roundtrips_flat() {
        let _cwd = TempCwd::enter();
        let prefs = json!({"theme": "dark", "n": 3});
        save(&prefs).unwrap();
        assert_eq!(load(), prefs);
        // indent=2 -> pretty-printed two-space file.
        let raw = std::fs::read_to_string("data/user_prefs.json").unwrap();
        assert!(raw.contains("  \"theme\""), "expected 2-space indent, got: {raw}");
    }

    #[test]
    fn save_creates_data_dir() {
        let _cwd = TempCwd::enter();
        assert!(!std::path::Path::new("data").exists());
        save(&json!({"a": 1})).unwrap();
        assert!(std::path::Path::new("data/user_prefs.json").exists());
    }

    #[test]
    fn load_for_user_flat_legacy_format() {
        let _cwd = TempCwd::enter();
        // No `_users` key -> the whole object is that single user's prefs.
        save(&json!({"theme": "light"})).unwrap();
        assert_eq!(load_for_user(Some("alice")), json!({"theme": "light"}));
        assert_eq!(load_for_user(None), json!({"theme": "light"}));
    }

    #[test]
    fn load_for_user_users_format_by_name() {
        let _cwd = TempCwd::enter();
        save(&json!({"_users": {
            "alice": {"theme": "dark"},
            "bob": {"theme": "light"},
        }}))
        .unwrap();
        assert_eq!(load_for_user(Some("alice")), json!({"theme": "dark"}));
        assert_eq!(load_for_user(Some("bob")), json!({"theme": "light"}));
        // Unknown user -> {}
        assert_eq!(load_for_user(Some("nobody")), json!({}));
    }

    #[test]
    fn load_for_user_none_returns_first_user_in_insertion_order() {
        let _cwd = TempCwd::enter();
        // Auth disabled: user is None -> next(iter(users.values()), {}).
        // preserve_order means "alice" (inserted first) wins.
        save(&json!({"_users": {
            "alice": {"theme": "dark"},
            "bob": {"theme": "light"},
        }}))
        .unwrap();
        assert_eq!(load_for_user(None), json!({"theme": "dark"}));
    }

    #[test]
    fn load_for_user_none_empty_users_yields_empty() {
        let _cwd = TempCwd::enter();
        save(&json!({"_users": {}})).unwrap();
        assert_eq!(load_for_user(None), json!({}));
    }

    #[test]
    fn save_for_user_none_writes_flat() {
        let _cwd = TempCwd::enter();
        save_for_user(None, &json!({"theme": "dark"})).unwrap();
        // Flat: no `_users` wrapper.
        assert_eq!(load(), json!({"theme": "dark"}));
    }

    #[test]
    fn save_for_user_named_creates_users_wrapper() {
        let _cwd = TempCwd::enter();
        save_for_user(Some("alice"), &json!({"theme": "dark"})).unwrap();
        assert_eq!(
            load(),
            json!({"_users": {"alice": {"theme": "dark"}}})
        );
        assert_eq!(load_for_user(Some("alice")), json!({"theme": "dark"}));
    }

    #[test]
    fn save_for_user_named_preserves_other_users() {
        let _cwd = TempCwd::enter();
        save_for_user(Some("alice"), &json!({"theme": "dark"})).unwrap();
        save_for_user(Some("bob"), &json!({"theme": "light"})).unwrap();
        assert_eq!(load_for_user(Some("alice")), json!({"theme": "dark"}));
        assert_eq!(load_for_user(Some("bob")), json!({"theme": "light"}));
    }

    #[test]
    fn save_for_user_named_upgrades_legacy_flat_to_users() {
        let _cwd = TempCwd::enter();
        // Pre-existing legacy flat file...
        save(&json!({"theme": "old"})).unwrap();
        // ...saving for a named user replaces it with the `_users` wrapper
        // (Python: all_prefs = {"_users": {}}; drops the flat content), exactly
        // like the source's reassignment.
        save_for_user(Some("alice"), &json!({"theme": "new"})).unwrap();
        assert_eq!(load(), json!({"_users": {"alice": {"theme": "new"}}}));
    }

    // --- parity change 1: save_for_user(None) with _users map present ---

    #[test]
    fn save_for_user_none_with_users_map_writes_into_first_slot() {
        let _cwd = TempCwd::enter();
        // Pre-populate a multi-user store with alice as the first slot.
        save(&json!({"_users": {
            "alice": {"theme": "dark"},
            "bob": {"theme": "light"},
        }}))
        .unwrap();
        // Auth is disabled (user=None). Previously this would have flattened and
        // destroyed bob's prefs. Now it must write into alice's slot and preserve bob.
        save_for_user(None, &json!({"theme": "solarized"})).unwrap();
        let on_disk = load();
        // The _users wrapper must survive.
        assert!(on_disk.get("_users").is_some(), "_users key must be preserved");
        assert_eq!(
            on_disk["_users"]["alice"],
            json!({"theme": "solarized"}),
            "alice's slot should be updated"
        );
        assert_eq!(
            on_disk["_users"]["bob"],
            json!({"theme": "light"}),
            "bob's slot must not be destroyed"
        );
    }

    #[test]
    fn save_for_user_none_with_users_map_empty_falls_through_to_flat() {
        let _cwd = TempCwd::enter();
        // A _users map that has no entries — first_key is None, so we fall
        // through to the flat-save path.
        save(&json!({"_users": {}})).unwrap();
        save_for_user(None, &json!({"theme": "blue"})).unwrap();
        // flat save replaces the file
        assert_eq!(load(), json!({"theme": "blue"}));
    }

    #[test]
    fn save_for_user_none_no_users_map_still_writes_flat() {
        let _cwd = TempCwd::enter();
        // No _users map at all — legacy / auth-disabled path unchanged.
        save_for_user(None, &json!({"theme": "dark"})).unwrap();
        assert_eq!(load(), json!({"theme": "dark"}));
    }

    // --- parity change 2: load() coerces non-object JSON payloads to {} ---

    #[test]
    fn load_json_array_coerced_to_empty_object() {
        let _cwd = TempCwd::enter();
        std::fs::create_dir_all("data").unwrap();
        std::fs::write("data/user_prefs.json", "[1,2,3]").unwrap();
        // Python: return data if isinstance(data, dict) else {}
        assert_eq!(load(), json!({}));
    }

    #[test]
    fn load_json_scalar_coerced_to_empty_object() {
        let _cwd = TempCwd::enter();
        std::fs::create_dir_all("data").unwrap();
        std::fs::write("data/user_prefs.json", "42").unwrap();
        assert_eq!(load(), json!({}));
    }

    #[test]
    fn load_json_null_coerced_to_empty_object() {
        let _cwd = TempCwd::enter();
        std::fs::create_dir_all("data").unwrap();
        std::fs::write("data/user_prefs.json", "null").unwrap();
        assert_eq!(load(), json!({}));
    }
}
