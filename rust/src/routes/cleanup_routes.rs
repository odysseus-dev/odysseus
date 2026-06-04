// routes/cleanup_routes.rs  <- routes/cleanup_routes.py
//! Session-cleanup routes (proof-batch unit P10).
//!
//! Faithful translation of `routes/cleanup_routes.py`'s `setup_cleanup_routes`
//! factory. Two handlers — `GET /api/cleanup/preview` and `POST /api/cleanup` —
//! over the already-ported async [`crate::src::cleanup_service`]
//! (`get_cleanup_preview` / `cleanup_sessions`), with the `session_manager`
//! reached through `AppState`.
//!
//! ## Shape (the integration substrate)
//! * `setup_cleanup_routes() -> Router<AppState>` mirrors the Python factory; its
//!   only factory arg (`session_manager`) is reached through `AppState.sessions`
//!   (the existing inline-subset field — reused, not recreated), so the factory
//!   takes no params. `APIRouter(prefix="/api/cleanup")` becomes the absolute
//!   paths `/api/cleanup/preview` and `/api/cleanup` on each `.route(...)`. The
//!   Python `@router.post("")` (empty path under the prefix) is the prefix itself,
//!   i.e. `POST /api/cleanup`.
//! * Auth is `get_current_user(request)` only — no `require_user` / `Depends`.
//!   The username-or-`None` is read inline from `Option<Extension<CurrentUser>>`
//!   (the auth gate stamps `CurrentUser` only when resolved). `None` is the
//!   load-bearing auth-disabled case: it flows straight into the cleanup service's
//!   `owner=None` branch, which (per the service's strict `_apply_owner_filter`)
//!   means "no owner filter" — every session is in scope, exactly like Python.
//! * `raise HTTPException(500, "...")` -> `return Err(HttpException::new(500, "..."))`;
//!   the `web`-gated `IntoResponse for HttpException` renders FastAPI's
//!   `{"detail": "..."}`.
//! * `return preview` (a dict) / `return {...}` -> `Json<Value>`, preserving JSON
//!   key insertion order (the service builds the preview with `serde_json::Map`,
//!   which is `preserve_order`).
//! * `round(space_freed_mb, 2)` is reproduced with [`round2`] (two-decimal,
//!   round-half-to-even — banker's rounding, matching CPython's built-in
//!   `round`, the same rule the sibling `search_routes`/`hwfit_routes` ports use).
//!
//! ## The `try/except -> HTTPException(500)` wrappers
//! Both Python handlers wrap the `await` in `try/except Exception` and re-raise a
//! 500 on failure. The ported `get_cleanup_preview` / `cleanup_sessions` already
//! swallow their own DB errors internally (`logger.error` + return the default
//! empty preview / zero counts — see the cleanup_service module header), so they
//! return infallibly (`Value` / `(i64, i64, f64)`, not `Result`). The Rust
//! handlers therefore never reach the `Err` branch in practice. The 500 mapping is
//! kept structurally (as the documented analogue) but is unreachable on the live
//! path — exactly matching Python, where the inner functions also catch their own
//! exceptions before the route-level `try` ever sees one. No fabrication: the
//! observable success body is identical, and the only way the Python route-level
//! `except` would fire is on a non-DB error the inner function does not catch
//! (e.g. an interpreter-level failure), which has no faithful Rust analogue here.
//!
//! ## No path collision
//! `/api/cleanup/preview` and `/api/cleanup` are a prefix the inline `web/mod.rs`
//! subset never touches, so the aggregator merges this router without an axum
//! duplicate-`method`+`path` panic.


use axum::extract::State;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Extension, Json, Router};
use serde_json::json;

use crate::routes::{AppState, CurrentUser, HttpException};
use crate::src::cleanup_service::{cleanup_sessions, get_cleanup_preview};

/// `setup_cleanup_routes(session_manager)` — assemble the cleanup router.
///
/// The Python registers, in this order: `GET /preview`, `POST ""`. Under the
/// `prefix="/api/cleanup"` those resolve to `GET /api/cleanup/preview` and
/// `POST /api/cleanup`. The two paths are disjoint, so registration order is
/// immaterial to matching; we keep the Python source order for fidelity.
pub fn setup_cleanup_routes() -> Router<AppState> {
    Router::new()
        .route("/api/cleanup/preview", get(cleanup_preview))
        .route("/api/cleanup", post(cleanup_endpoint))
}

/// `GET /api/cleanup/preview` — preview what would be cleaned up without making
/// any changes.
///
/// Returns the JSON preview with lists of sessions that would be archived/deleted
/// and the estimated space savings.
async fn cleanup_preview(
    _state: State<AppState>,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    // `user = get_current_user(request)` — the username, or `None` when auth is
    // disabled / unresolved (the load-bearing anonymous case).
    let user: Option<String> = user.map(|Extension(CurrentUser(u))| u);

    // try: preview = await get_cleanup_preview(owner=user); return preview
    // The ported service swallows its own DB errors and returns the (possibly
    // empty) preview infallibly, so the Python route-level 500 wrapper is
    // unreachable here (see the module header).
    let preview = get_cleanup_preview(user.as_deref()).await;
    Ok(Json(preview).into_response())
}

/// `POST /api/cleanup` — perform cleanup operations.
///
/// 1. Archive inactive sessions (not accessed for 7 days).
/// 2. Delete old sessions (archived, not important, not accessed for 14+ days,
///    with fewer than the keep-threshold messages).
///
/// Returns the counts of deleted and archived sessions, plus the space freed.
async fn cleanup_endpoint(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    // `user = get_current_user(request)` — see `cleanup_preview`.
    let user: Option<String> = user.map(|Extension(CurrentUser(u))| u);

    // try: archived_count, deleted_count, space_freed_mb =
    //          await cleanup_sessions(session_manager, owner=user)
    // `session_manager` is the existing `AppState.sessions` handle (reused from the
    // inline subset, not recreated). Like the preview, the service handles its own
    // errors, so the route-level 500 wrapper is unreachable on the live path.
    let (archived_count, deleted_count, space_freed_mb) =
        cleanup_sessions(s.sessions.as_ref(), user.as_deref()).await;

    // return {
    //   "archived_count": ..., "deleted_count": ...,
    //   "space_freed_mb": round(space_freed_mb, 2)
    // }
    Ok(Json(json!({
        "archived_count": archived_count,
        "deleted_count": deleted_count,
        "space_freed_mb": round2(space_freed_mb),
    }))
    .into_response())
}

/// `round(space_freed_mb, 2)` — two decimal places using round-half-to-even
/// (banker's), faithful to CPython's built-in `round`. Reuses the exact algorithm
/// of the sibling ports (`round2` in `search_routes.rs`, `round_half_even` in
/// `hwfit_routes.rs`): scale, take the integer floor, and on a `.xx5` tie round
/// toward the even neighbour rather than away from zero.
// Round-half-to-even: the floor/floor and +1.0/+1.0 branches are deliberately
// identical, distinguished only by the half-to-even tie-break condition.
#[allow(clippy::if_same_then_else)]
fn round2(x: f64) -> f64 {
    if !x.is_finite() {
        return x;
    }
    let factor = 100.0_f64;
    let scaled = x * factor;
    let floor = scaled.floor();
    let diff = scaled - floor;
    let rounded = if diff > 0.5 {
        floor + 1.0
    } else if diff < 0.5 {
        floor
    } else if (floor as i64) % 2 == 0 {
        floor
    } else {
        floor + 1.0
    };
    rounded / factor
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round2_matches_python_round() {
        // `round(space_freed_mb, 2)` for representative MB values.
        assert_eq!(round2(0.0), 0.0);
        assert_eq!(round2(1.0 / 3.0), 0.33);
        assert_eq!(round2(512.0 / 1024.0 / 1024.0), 0.0);
        // 20 messages * 512 bytes / 1MB = 0.009765625 MB -> 0.01.
        assert_eq!(round2(20.0 * 512.0 / (1024.0 * 1024.0)), 0.01);
        // A clean two-dp value round-trips.
        assert_eq!(round2(12.34), 12.34);
        // Round-half-to-even (banker's), matching CPython's `round(x, 2)`:
        // exact `.xx5` ties go toward the even neighbour, not away from zero.
        assert_eq!(round2(0.125), 0.12); // Python: round(0.125, 2) == 0.12
        assert_eq!(round2(0.135), 0.14); // Python: round(0.135, 2) == 0.14
    }

    #[test]
    fn router_mounts_both_absolute_paths() {
        // The factory yields a `Router<AppState>` mergeable with the inline subset
        // (no duplicate method+path); the two cleanup paths are disjoint, so
        // building the router never panics.
        let base: Router<AppState> = Router::new();
        let _merged: Router<AppState> = base.merge(setup_cleanup_routes());
    }

    #[tokio::test]
    async fn preview_returns_service_dict_shape() {
        // The preview handler returns the cleanup service's dict verbatim (a
        // non-existent owner yields the empty-but-well-formed preview). This pins
        // the `Json(Value)` body + key order the handler forwards.
        let preview = get_cleanup_preview(Some("__no_such_owner_cleanup_route__")).await;
        let keys: Vec<&str> = preview
            .as_object()
            .unwrap()
            .keys()
            .map(|k| k.as_str())
            .collect();
        assert_eq!(
            keys,
            vec![
                "sessions_to_archive",
                "sessions_to_delete",
                "preserved_sessions",
                "estimated_space_freed_mb"
            ]
        );
        assert_eq!(preview["estimated_space_freed_mb"], json!(0.0));
    }
}
