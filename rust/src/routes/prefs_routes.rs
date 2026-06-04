// routes/prefs_routes.rs  <- routes/prefs_routes.py
//! User preferences API — per-user key/value store backed by a JSON file.
//!
//! This is the `setup_prefs_routes()` FastAPI factory, mounted at `prefix=
//! "/api/prefs"`. The three handlers are thin: they resolve the current user
//! (`get_current_user`, whose `None` case is load-bearing — auth-disabled mode
//! returns the first user's prefs for backward compat) and delegate to the
//! shared prefs store (foundation unit F6, [`crate::routes::prefs_store`]).
//!
//! ```python
//! def setup_prefs_routes():
//!     router = APIRouter(prefix="/api/prefs", tags=["preferences"])
//!
//!     @router.get("")
//!     async def get_all_prefs(request: Request):
//!         user = get_current_user(request)
//!         return _load_for_user(user)
//!
//!     @router.get("/{key}")
//!     async def get_pref(request: Request, key: str):
//!         user = get_current_user(request)
//!         prefs = _load_for_user(user)
//!         return {"key": key, "value": prefs.get(key)}
//!
//!     @router.put("/{key}")
//!     async def set_pref(request: Request, key: str, body: dict):
//!         user = get_current_user(request)
//!         prefs = _load_for_user(user)
//!         prefs[key] = body.get("value")
//!         _save_for_user(user, prefs)
//!         return {"key": key, "value": prefs[key]}
//!
//!     return router
//! ```
//!
//! The `_load`/`_save`/`_load_for_user`/`_save_for_user` helpers themselves are
//! NOT redefined here — they were hoisted into [`crate::routes::prefs_store`]
//! (F6) because many other routers import them; this module simply calls into
//! that shared store, exactly as the Python module's handlers call its
//! module-level helpers.


use axum::extract::Path;
use axum::routing::get;
use axum::{Extension, Json, Router};
use serde_json::{json, Value};

use crate::routes::prefs_store;
use crate::routes::{AppState, CurrentUser};

/// `get_current_user(request)` — the username `request.state.current_user`, or
/// `None`.
///
/// The auth gate stamps `Extension(CurrentUser)` **only when a user resolves**,
/// so an unauthenticated request (auth disabled / first-run) arrives with the
/// extension absent. `Option<Extension<CurrentUser>>` therefore reproduces the
/// Python `get_current_user` `Optional[str]` directly: `Some(Extension(u))` ->
/// `Some(&u.0)`, absent -> `None`. The `None` case is load-bearing — the prefs
/// store's `_load_for_user(None)` returns the first user's prefs for backward
/// compat.
fn current_user(user: &Option<Extension<CurrentUser>>) -> Option<&str> {
    user.as_ref().map(|Extension(u)| u.0.as_str())
}

/// `@router.get("")` -> `GET /api/prefs` — return all preferences for the
/// current user.
///
/// `return _load_for_user(user)` — the raw prefs object (an `IndexMap`-style
/// JSON object; `serde_json`'s `preserve_order` keeps the on-disk key order, so
/// the JSON body matches Python's `dict` insertion order).
async fn get_all_prefs(user: Option<Extension<CurrentUser>>) -> Json<Value> {
    // user = get_current_user(request)
    let user = current_user(&user);
    // return _load_for_user(user)
    Json(prefs_store::load_for_user(user))
}

/// `@router.get("/{key}")` -> `GET /api/prefs/{key}` — return a single
/// preference value.
///
/// `return {"key": key, "value": prefs.get(key)}` — `prefs.get(key)` is `None`
/// (JSON `null`) when the key is absent, matching Python `dict.get`.
async fn get_pref(user: Option<Extension<CurrentUser>>, Path(key): Path<String>) -> Json<Value> {
    // user = get_current_user(request)
    let user = current_user(&user);
    // prefs = _load_for_user(user)
    let prefs = prefs_store::load_for_user(user);
    // return {"key": key, "value": prefs.get(key)}
    let value = prefs.get(&key).cloned().unwrap_or(Value::Null);
    Json(json!({ "key": key, "value": value }))
}

/// `@router.put("/{key}")` -> `PUT /api/prefs/{key}` — set a single preference
/// value.
///
/// `body: dict` is the JSON request body; `prefs[key] = body.get("value")`
/// stores `body["value"]` (JSON `null` when the key is absent, exactly like
/// Python `dict.get`), then `_save_for_user` persists and the stored value is
/// echoed back.
async fn set_pref(
    user: Option<Extension<CurrentUser>>,
    Path(key): Path<String>,
    Json(body): Json<Value>,
) -> Json<Value> {
    // user = get_current_user(request)
    let user = current_user(&user);
    // prefs = _load_for_user(user)
    let mut prefs = prefs_store::load_for_user(user);
    // prefs[key] = body.get("value")   (defaults to None/null when absent)
    let value = body.get("value").cloned().unwrap_or(Value::Null);
    // `_load_for_user` always returns an object (the `as_object_copy` guard), so
    // the `as_object_mut` is always `Some`; the closure is a no-op fallback.
    if let Some(map) = prefs.as_object_mut() {
        map.insert(key.clone(), value.clone());
    }
    // _save_for_user(user, prefs)
    //
    // MINOR DRIFT: in Python a `json.dump`/`open` write error would propagate and
    // FastAPI would surface it as a 500. This handler's return type is
    // `Json<Value>` (the Python returns a plain dict, never a `Response`), so a
    // write error is swallowed and the echo still returns 200. The store creates
    // its parent dir on save, so this path is effectively unreachable in practice;
    // promoting it to a `Result<_, HttpException>` 500 is deferred — it would gain
    // nothing observable while complicating the proof module's signature.
    let _ = prefs_store::save_for_user(user, &prefs);
    // return {"key": key, "value": prefs[key]}
    Json(json!({ "key": key, "value": value }))
}

/// `setup_prefs_routes()` — build the `/api/prefs` router.
///
/// `APIRouter(prefix="/api/prefs")` mounts each handler at `prefix + route`, so
/// the absolute paths are `/api/prefs` (the empty-suffix `""` route) and
/// `/api/prefs/:key`. None of these collide with the inline `web/mod.rs` subset.
pub fn setup_prefs_routes() -> Router<AppState> {
    Router::new()
        // @router.get("")  -> prefix + ""  == "/api/prefs"
        .route("/api/prefs", get(get_all_prefs))
        // @router.get("/{key}") + @router.put("/{key}")  -> "/api/prefs/{key}"
        .route("/api/prefs/:key", get(get_pref).put(set_pref))
}

#[cfg(test)]
mod tests {
    //! The three handlers read **only** `Option<Extension<CurrentUser>>`, `Path`,
    //! and `Json` — never `State<AppState>`. So rather than build a full
    //! `AppState` (every manager) just to drive a `Router` end-to-end, the tests
    //! call the handler fns directly with the extractors constructed by hand.
    //! That exercises the exact translated logic (current-user resolution, the
    //! prefs-store delegation, the response shapes) without the heavy state.
    //!
    //! `setup_prefs_routes()` is independently smoke-tested for the right
    //! method+path mounts in [`mount_smoke`].
    use super::*;
    use std::sync::MutexGuard;

    // The prefs store is a single CWD-relative `data/user_prefs.json`, so these
    // tests must serialize and run in an isolated temp CWD. They drive the SAME
    // store as the `prefs_store` unit tests, so both pools share ONE process-global
    // lock ([`crate::routes::prefs_store::TEST_CWD_LOCK`]) — a per-module lock would
    // let the two pools race the (process-global) working directory.
    use crate::routes::prefs_store::TEST_CWD_LOCK as CWD_LOCK;

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

    /// Build the `Option<Extension<CurrentUser>>` the auth gate would deliver:
    /// `Some(u)` -> stamped extension, `None` -> absent (auth-disabled).
    fn ext(user: Option<&str>) -> Option<Extension<CurrentUser>> {
        user.map(|u| Extension(CurrentUser(u.to_string())))
    }

    #[tokio::test]
    async fn get_all_prefs_named_user_empty_when_unset() {
        let _cwd = TempCwd::enter();
        // Unknown user -> {} (prefs_store::load_for_user).
        let Json(body) = get_all_prefs(ext(Some("alice"))).await;
        assert_eq!(body, json!({}));
    }

    #[tokio::test]
    async fn put_then_get_roundtrips_for_named_user() {
        let _cwd = TempCwd::enter();
        // PUT /api/prefs/theme  body={"value":"dark"}
        let Json(body) = set_pref(
            ext(Some("alice")),
            Path("theme".to_string()),
            Json(json!({"value": "dark"})),
        )
        .await;
        assert_eq!(body, json!({"key": "theme", "value": "dark"}));

        // GET /api/prefs/theme
        let Json(body) = get_pref(ext(Some("alice")), Path("theme".to_string())).await;
        assert_eq!(body, json!({"key": "theme", "value": "dark"}));

        // GET /api/prefs (all) — stored under the `_users` wrapper for alice.
        let Json(body) = get_all_prefs(ext(Some("alice"))).await;
        assert_eq!(body, json!({"theme": "dark"}));
    }

    #[tokio::test]
    async fn get_pref_missing_key_yields_null_value() {
        let _cwd = TempCwd::enter();
        // {"key": "nope", "value": None}  (Python dict.get -> None)
        let Json(body) = get_pref(ext(Some("alice")), Path("nope".to_string())).await;
        assert_eq!(body, json!({"key": "nope", "value": null}));
    }

    #[tokio::test]
    async fn put_without_value_key_stores_null() {
        let _cwd = TempCwd::enter();
        // body has no "value" key -> body.get("value") is None -> stored null.
        let Json(body) = set_pref(
            ext(Some("alice")),
            Path("x".to_string()),
            Json(json!({})),
        )
        .await;
        assert_eq!(body, json!({"key": "x", "value": null}));
        // The null was persisted.
        let Json(body) = get_pref(ext(Some("alice")), Path("x".to_string())).await;
        assert_eq!(body, json!({"key": "x", "value": null}));
    }

    #[tokio::test]
    async fn auth_disabled_none_user_uses_flat_store() {
        let _cwd = TempCwd::enter();
        // No CurrentUser extension -> get_current_user returns None -> the
        // auth-disabled save path writes flat (`_save(prefs)`).
        let Json(body) = set_pref(
            ext(None),
            Path("lang".to_string()),
            Json(json!({"value": "en"})),
        )
        .await;
        assert_eq!(body, json!({"key": "lang", "value": "en"}));

        // Stored flat (no `_users` wrapper).
        assert_eq!(prefs_store::load(), json!({"lang": "en"}));

        // GET all (None) returns the flat object.
        let Json(body) = get_all_prefs(ext(None)).await;
        assert_eq!(body, json!({"lang": "en"}));
    }

    #[tokio::test]
    async fn auth_disabled_none_user_reads_first_user_for_backward_compat() {
        let _cwd = TempCwd::enter();
        // Pre-seed a `_users` store; None reads the first user's prefs.
        prefs_store::save(&json!({"_users": {
            "alice": {"theme": "dark"},
            "bob": {"theme": "light"},
        }}))
        .unwrap();
        let Json(body) = get_all_prefs(ext(None)).await;
        assert_eq!(body, json!({"theme": "dark"}));
    }

    /// `setup_prefs_routes()` mounts exactly `GET /api/prefs`, `GET /api/prefs/:key`,
    /// and `PUT /api/prefs/:key` — and merges cleanly into a `Router<AppState>`
    /// (no panic = no intra-router collision), the additive invariant the
    /// aggregator relies on.
    #[test]
    fn mount_smoke() {
        let base: Router<AppState> = Router::new();
        let _merged: Router<AppState> = base.merge(setup_prefs_routes());
    }
}
