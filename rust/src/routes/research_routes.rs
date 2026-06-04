// routes/research_routes.rs  <- routes/research_routes.py
//! Research background task routes — `/api/research/*`.
//!
//! This is the `setup_research_routes(research_handler, session_manager=None)`
//! FastAPI factory, translated to a `Router<AppState>`. Both the
//! `research_handler` and `session_manager` arguments are reached through
//! [`AppState`] (`state.research_handler` / `state.sessions`), so the factory
//! takes no parameters.
//!
//! ## Port classification — PORT_PARTIAL (web + db)
//!
//! The handler (`src/research_handler.py`) is fully ported
//! ([`crate::src::research_handler::ResearchHandler`]); these routes wrap it.
//! Every endpoint requires an authenticated user, ownership is enforced
//! `404`-not-`403` (so the route never leaks the existence of another user's
//! report), and the on-disk JSON store is the one the ported handler writes (the
//! routes go through [`ResearchHandler::session_json_path`] so they read/write the
//! SAME files — honoring the handler's documented `DATA_DIR` deviation rather than
//! the literal source-relative `Path("data/deep_research")`).
//!
//! ### Faithful branches
//! * `GET /api/research/active`, `/status/:sid`, `POST /cancel/:sid`,
//!   `/result/:sid`, `GET /report/:sid` (HTML), `POST /:sid/hide-image`,
//!   `/:sid/unhide-images`, `GET /library`, `/detail/:sid`, `POST /:sid/archive`,
//!   `DELETE /:sid`, `GET /stream/:sid` (SSE), `POST /result-peek/:sid`,
//!   `POST /spinoff/:sid` — all translated line-for-line.
//! * `POST /api/research/start` — `require_privilege(request, "can_use_research")`
//!   plus the `internal-tool` `X-Odysseus-Owner` impersonation override.
//!
//! ### LIVE — the `resolve_endpoint(...)` settings cascade
//! The Python `research_start` / `research_spinoff` resolve the LLM endpoint via
//! `src.endpoint_resolver.resolve_endpoint("research" | "utility" | "default" |
//! "chat")`. That resolver is now ported
//! ([`crate::src::endpoint_resolver::resolve_endpoint`]) and reads the
//! per-user-aware settings (`get_user_setting`, `owner=None` => `get_setting`)
//! plus the `model_endpoints` lookup. Both cascades are wired to it:
//!   * `research_start` (research_routes.py:352-362): `research -> utility ->
//!     default -> chat`, each step guarded by `if not ep_url`, re-binding all
//!     three of (url, model, headers). On a resolved endpoint it applies the
//!     `body.model` override and starts immediately (mirroring the explicit
//!     `endpoint_id` early-return) so it does NOT fall through to a different
//!     first-enabled endpoint.
//!   * `research_spinoff` (research_routes.py:519-524): the `chat -> research ->
//!     utility` `_merge(...)` cascade, each guarded by `if not ep_url or not
//!     ep_model`.
//! owner=None is faithful — Python passes NO owner to these calls (the session's
//! url/model/headers are only fallback values, not the owner). When the cascade
//! resolves nothing, control flows to the branches that were always portable:
//!   * the explicit `body.endpoint_id` branch (raw `model_endpoints` lookup, key
//!     decrypted, `is_enabled` filtered — fully ported), and
//!   * the final "first enabled endpoint in the DB" fallback (fully ported),
//!     then the `HTTPException(400, "No endpoints configured...")` raise.
//!
//! ### No path collision
//! All routes live under `/api/research/*`, which the inline `web/mod.rs` subset
//! never touches, so the aggregator merges this router without an axum
//! duplicate-`method`+`path` panic.


use axum::body::Body;
use axum::extract::{Path, Query, State};
use axum::http::{header, HeaderMap};
use axum::response::{Html, IntoResponse, Response};
use axum::routing::{delete, get, post};
use axum::{Extension, Json, Router};
use serde::Deserialize;
use serde_json::{json, Map, Value};

use crate::core::database::session_local;
use crate::routes::{auth_adapter, AppState, CurrentUser, HttpException};
use crate::src::endpoint_resolver::{
    _first_chat_model, build_chat_url, build_headers, normalize_base, resolve_endpoint,
};
use crate::src::research_handler::StartResearchArgs;

// ---------------------------------------------------------------------------
// Auth + ownership helpers (the closures in the Python factory)
// ---------------------------------------------------------------------------

/// `_require_user(request)` — all research endpoints require an authenticated
/// user.
///
/// Research data isn't owner-scoped in the on-disk JSON beyond the `owner` field,
/// so we at least block anonymous access. Delegates to the foundation
/// [`auth_adapter::require_user`]; `None`/empty raises `HTTPException(401, "Not
/// authenticated")` (in unconfigured single-user loopback mode it returns `""`).
fn require_user(
    state: &AppState,
    headers: &HeaderMap,
    user: Option<&CurrentUser>,
) -> Result<String, HttpException> {
    let user = user.map(|u| u.0.as_str());
    auth_adapter::require_user(user, state, client_host(headers).as_deref())
}

/// `request.client.host` analogue: in axum the peer address is threaded via
/// `ConnectInfo`, but the research helpers only need it for `require_user`'s
/// unconfigured-loopback branch. The auth gate has already resolved the user in
/// the configured case, so passing `None` here matches the Python behavior for a
/// configured deploy (the loopback branch is only reachable first-run, where the
/// auth gate likewise can't stamp a user). `None` keeps `require_user` strict.
fn client_host(_headers: &HeaderMap) -> Option<String> {
    None
}

/// `_owns_in_memory(session_id, user)` — ownership check for an in-flight
/// (in-memory) research task; falls back to the on-disk JSON if the task has
/// already finished.
///
/// ```python
/// entry = research_handler._active_tasks.get(session_id)
/// if entry is not None:
///     return entry.get("owner", "") == user
/// path = Path("data/deep_research") / f"{session_id}.json"
/// if not path.exists():
///     return False
/// try:
///     return json.loads(path.read_text()).get("owner") == user
/// except Exception:
///     return False
/// ```
fn owns_in_memory(state: &AppState, session_id: &str, user: &str) -> bool {
    // `entry = research_handler._active_tasks.get(session_id)`
    if let Some(owner) = state.research_handler.active_owner(session_id) {
        // `if entry is not None: return entry.get("owner", "") == user`
        return owner == user;
    }
    // Task no longer in memory — check the persisted JSON.
    let path = state.research_handler.session_json_path(session_id);
    if !crate::pyos::path::exists(&path) {
        return false;
    }
    // `return json.loads(path.read_text()).get("owner") == user` (False on any error)
    match read_json_object(&path) {
        Some(data) => data.get("owner").and_then(Value::as_str) == Some(user),
        None => false,
    }
}

/// `_assert_owns_research(session_id, user)` — `404`-not-`403` ownership gate for
/// a research session's on-disk JSON. Use BEFORE returning any data or mutating
/// the file.
///
/// ```python
/// path = Path("data/deep_research") / f"{session_id}.json"
/// if not path.exists():
///     raise HTTPException(404, "Research not found")
/// try:
///     owner = json.loads(path.read_text()).get("owner")
/// except Exception:
///     raise HTTPException(404, "Research not found")
/// if owner != user:
///     raise HTTPException(404, "Research not found")
/// ```
fn assert_owns_research(state: &AppState, session_id: &str, user: &str) -> Result<(), HttpException> {
    let path = state.research_handler.session_json_path(session_id);
    // `if not path.exists(): raise HTTPException(404, "Research not found")`
    if !crate::pyos::path::exists(&path) {
        return Err(HttpException::new(404, "Research not found"));
    }
    // `try: owner = ...; except Exception: raise HTTPException(404, ...)`
    let owner = match read_json_object(&path) {
        Some(data) => data.get("owner").cloned().unwrap_or(Value::Null),
        None => return Err(HttpException::new(404, "Research not found")),
    };
    // `if owner != user: raise HTTPException(404, "Research not found")`
    if owner.as_str() != Some(user) {
        return Err(HttpException::new(404, "Research not found"));
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

/// `@router.get("/api/research/active")` — list all currently active (running)
/// research tasks for this user.
async fn research_active(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    headers: HeaderMap,
) -> Result<Json<Value>, HttpException> {
    // `user = _require_user(request)`
    let user = require_user(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    // The SECURITY owner-filter + status=="running" loop lives in the handler
    // accessor (it reads the private `_active_tasks` registry).
    let active = state.research_handler.active_running(&user);
    Ok(Json(json!({ "active": active })))
}

/// `@router.get("/api/research/status/{session_id}")` — current status for a
/// session.
async fn research_status(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    headers: HeaderMap,
    Path(session_id): Path<String>,
) -> Result<Json<Value>, HttpException> {
    let user = require_user(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    // `if not _owns_in_memory(session_id, user): raise HTTPException(404, ...)`
    if !owns_in_memory(&state, &session_id, &user) {
        return Err(HttpException::new(404, "No research found for this session"));
    }
    // `status = research_handler.get_status(session_id)`
    match state.research_handler.get_status(&session_id) {
        // `if status is None: raise HTTPException(404, ...)`
        None => Err(HttpException::new(404, "No research found for this session")),
        Some(status) => Ok(Json(status)),
    }
}

/// `@router.post("/api/research/cancel/{session_id}")` — cancel running research.
async fn research_cancel(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    headers: HeaderMap,
    Path(session_id): Path<String>,
) -> Result<Json<Value>, HttpException> {
    let user = require_user(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    if !owns_in_memory(&state, &session_id, &user) {
        return Err(HttpException::new(404, "No research found for this session"));
    }
    // `cancelled = research_handler.cancel_research(session_id)`
    let cancelled = state.research_handler.cancel_research(&session_id);
    Ok(Json(json!({ "cancelled": cancelled })))
}

/// `@router.post("/api/research/result/{session_id}")` — consume the completed
/// research result (clears it).
async fn research_result(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    headers: HeaderMap,
    Path(session_id): Path<String>,
) -> Result<Json<Value>, HttpException> {
    let user = require_user(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    if !owns_in_memory(&state, &session_id, &user) {
        return Err(HttpException::new(404, "No research result available"));
    }
    // `result = research_handler.get_result(session_id)`
    let result = match state.research_handler.get_result(&session_id) {
        // `if result is None: raise HTTPException(404, ...)`
        None => return Err(HttpException::new(404, "No research result available")),
        Some(r) => r,
    };
    // `sources = research_handler.get_sources(session_id) or []`
    let sources = state.research_handler.get_sources(&session_id).unwrap_or_default();
    // `raw_findings = research_handler.get_raw_findings(session_id) or []`
    let raw_findings = state.research_handler.get_raw_findings(&session_id).unwrap_or_default();
    // `research_handler.clear_result(session_id)`
    state.research_handler.clear_result(&session_id);
    Ok(Json(json!({
        "result": result,
        "sources": sources,
        "raw_findings": raw_findings,
    })))
}

/// `@router.get("/api/research/report/{session_id}")` — serve the visual HTML
/// report for a completed research session.
async fn research_report(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    headers: HeaderMap,
    Path(session_id): Path<String>,
) -> Result<Response, HttpException> {
    let user = require_user(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    // `_assert_owns_research(session_id, user)`
    assert_owns_research(&state, &session_id, &user)?;
    crate::pylog::info(&format!("Visual report requested for session {session_id}"));
    // `try: html_content = research_handler.get_report_html(session_id)`
    //   The ported `get_report_html` is infallible (it returns `None` on any read
    //   error rather than raising), so the Python `except -> HTTPException(500,
    //   f"Report generation failed: {e}")` arm is unreachable here — the only
    //   error output is the `None -> 404` branch, exactly as Python would surface
    //   a session with no report data.
    let html_content = state.research_handler.get_report_html(&session_id);
    match html_content {
        // `if html_content is None: ... raise HTTPException(404, ...)`
        None => {
            crate::pylog::warning(&format!("No report data found for session {session_id}"));
            Err(HttpException::new(404, "No visual report available for this session"))
        }
        // `return HTMLResponse(content=html_content)`
        Some(html) => Ok(Html(html).into_response()),
    }
}

/// `class HideImageRequest(BaseModel): url: str`.
#[derive(Deserialize)]
struct HideImageRequest {
    url: String,
}

/// `@router.post("/api/research/{session_id}/hide-image")` — mark an image URL as
/// hidden for this research's visual report.
async fn research_hide_image(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    headers: HeaderMap,
    Path(session_id): Path<String>,
    Json(body): Json<HideImageRequest>,
) -> Result<Json<Value>, HttpException> {
    let user = require_user(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    assert_owns_research(&state, &session_id, &user)?;
    // `ok = research_handler.hide_image(session_id, body.url)`
    let ok = state.research_handler.hide_image(&session_id, &body.url);
    if !ok {
        // `if not ok: raise HTTPException(404, "Research not found")`
        return Err(HttpException::new(404, "Research not found"));
    }
    Ok(Json(json!({ "ok": true })))
}

/// `@router.post("/api/research/{session_id}/unhide-images")` — clear the
/// hidden-images list for a research session.
async fn research_unhide_images(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    headers: HeaderMap,
    Path(session_id): Path<String>,
) -> Result<Json<Value>, HttpException> {
    let user = require_user(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    assert_owns_research(&state, &session_id, &user)?;
    // `ok = research_handler.unhide_all_images(session_id)`
    let ok = state.research_handler.unhide_all_images(&session_id);
    if !ok {
        return Err(HttpException::new(404, "Research not found"));
    }
    Ok(Json(json!({ "ok": true })))
}

/// `search`/`sort`/`limit`/`archived` query params for `GET /api/research/library`
/// (FastAPI `Query(...)` defaults: `search=None`, `sort="recent"`, `limit=50`,
/// `archived=False`).
#[derive(Deserialize)]
struct LibraryQuery {
    #[serde(default)]
    search: Option<String>,
    #[serde(default = "default_sort")]
    sort: String,
    #[serde(default = "default_limit")]
    limit: i64,
    #[serde(default)]
    archived: bool,
}

fn default_sort() -> String {
    "recent".to_string()
}
fn default_limit() -> i64 {
    50
}

/// `@router.get("/api/research/library")` — list all completed research for the
/// Library panel.
async fn research_library(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    headers: HeaderMap,
    Query(q): Query<LibraryQuery>,
) -> Result<Json<Value>, HttpException> {
    let user = require_user(&state, &headers, user.as_ref().map(|Extension(u)| u))?;

    // `data_dir = Path("data/deep_research")` — go through the handler's path
    // scheme (the documented DATA_DIR deviation). `glob("*.json")`.
    let mut items: Vec<Value> = Vec::new();
    for path in glob_json(&crate::src::research_handler::RESEARCH_DATA_DIR) {
        // `try: d = json.loads(p.read_text())` — skip on any error.
        let d = match read_json_object(&path) {
            Some(d) => d,
            None => continue,
        };
        // SECURITY: only show research belonging to this user. Legacy JSONs
        // without an `owner` field are hidden.
        // `if d.get("owner") != user: continue`
        if d.get("owner").and_then(Value::as_str) != Some(user.as_str()) {
            continue;
        }
        // `if bool(d.get("archived")) != archived: continue`
        if truthy(d.get("archived")) != q.archived {
            continue;
        }
        // `query = d.get("query", "")`
        let query = d.get("query").and_then(Value::as_str).unwrap_or("");
        // `if search and search.lower() not in query.lower(): continue`
        if let Some(search) = q.search.as_deref() {
            if !search.is_empty() && !query.to_lowercase().contains(&search.to_lowercase()) {
                continue;
            }
        }
        // `sources = d.get("sources", [])`
        let source_count = d
            .get("sources")
            .and_then(Value::as_array)
            .map(|a| a.len())
            .unwrap_or(0);
        // `stats = d.get("stats", {})`
        let stats = d.get("stats");
        let stat = |key: &str| -> Value {
            stats
                .and_then(|s| s.as_object())
                .and_then(|m| m.get(key))
                .cloned()
                .unwrap_or(Value::String(String::new()))
        };
        // `"id": p.stem`
        let id = path_stem(&path);
        items.push(json!({
            "id": id,
            "query": query,
            // `d.get("category") or ""`
            "category": or_empty_str(d.get("category")),
            "source_count": source_count,
            // `d.get("status", "done")`
            "status": d.get("status").cloned().unwrap_or(json!("done")),
            "duration": stat("Duration"),
            "rounds": stat("Rounds"),
            // `d.get("started_at", 0)`
            "started_at": d.get("started_at").cloned().unwrap_or(json!(0)),
            // `d.get("completed_at", 0)`
            "completed_at": d.get("completed_at").cloned().unwrap_or(json!(0)),
            // `bool(d.get("archived"))`
            "archived": truthy(d.get("archived")),
        }));
    }

    // Sort
    match q.sort.as_str() {
        // `items.sort(key=lambda x: x["completed_at"] or 0, reverse=True)`
        "recent" => items.sort_by(|a, b| num_or_zero(&b["completed_at"]).total_cmp(&num_or_zero(&a["completed_at"]))),
        // `items.sort(key=lambda x: x["completed_at"] or 0)`
        "oldest" => items.sort_by(|a, b| num_or_zero(&a["completed_at"]).total_cmp(&num_or_zero(&b["completed_at"]))),
        // `items.sort(key=lambda x: x["source_count"], reverse=True)`
        "most-messages" => {
            items.sort_by(|a, b| num_or_zero(&b["source_count"]).total_cmp(&num_or_zero(&a["source_count"])))
        }
        // `items.sort(key=lambda x: x["query"].lower())`
        "alpha" => items.sort_by(|a, b| {
            a["query"]
                .as_str()
                .unwrap_or("")
                .to_lowercase()
                .cmp(&b["query"].as_str().unwrap_or("").to_lowercase())
        }),
        _ => {}
    }

    // `return {"research": items[:limit], "total": len(items)}`
    let total = items.len();
    let limit = q.limit.max(0) as usize;
    let sliced: Vec<Value> = items.into_iter().take(limit).collect();
    Ok(Json(json!({ "research": sliced, "total": total })))
}

/// `@router.get("/api/research/detail/{session_id}")` — full JSON for a single
/// research result (Library preview panel).
async fn research_detail(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    headers: HeaderMap,
    Path(session_id): Path<String>,
) -> Result<Json<Value>, HttpException> {
    let user = require_user(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    let path = state.research_handler.session_json_path(&session_id);
    // `if not path.exists(): raise HTTPException(404, "Research not found")`
    if !crate::pyos::path::exists(&path) {
        return Err(HttpException::new(404, "Research not found"));
    }
    // `try: data = json.loads(path.read_text()) except Exception as e: raise
    //  HTTPException(500, f"Failed to read research: {e}")`
    let data = match read_json_value(&path) {
        Ok(v) => v,
        Err(e) => return Err(HttpException::new(500, format!("Failed to read research: {e}"))),
    };
    // SECURITY: 404 (not 403) so we don't leak that the report exists.
    // `if data.get("owner") != user: raise HTTPException(404, "Research not found")`
    if data.get("owner").and_then(Value::as_str) != Some(user.as_str()) {
        return Err(HttpException::new(404, "Research not found"));
    }
    Ok(Json(data))
}

/// `archived: bool = Query(True)` for `POST /api/research/{session_id}/archive`.
#[derive(Deserialize)]
struct ArchiveQuery {
    #[serde(default = "default_archived")]
    archived: bool,
}
fn default_archived() -> bool {
    true
}

/// `@router.post("/api/research/{session_id}/archive")` — soft-archive / restore a
/// research report (sets `archived` in its JSON).
async fn research_archive(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    headers: HeaderMap,
    Path(session_id): Path<String>,
    Query(q): Query<ArchiveQuery>,
) -> Result<Json<Value>, HttpException> {
    let user = require_user(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    let path = state.research_handler.session_json_path(&session_id);
    // `if not path.exists(): raise HTTPException(404, "Research not found")`
    if !crate::pyos::path::exists(&path) {
        return Err(HttpException::new(404, "Research not found"));
    }
    // try:
    //     data = json.loads(path.read_text())
    //     if data.get("owner") != user: raise HTTPException(404, ...)
    //     data["archived"] = bool(archived); path.write_text(json.dumps(data))
    // except HTTPException: raise
    // except Exception as e: raise HTTPException(500, f"Failed to update research: {e}")
    let mut data = match read_json_object(&path) {
        Some(d) => d,
        None => return Err(HttpException::new(500, "Failed to update research: invalid JSON")),
    };
    if data.get("owner").and_then(Value::as_str) != Some(user.as_str()) {
        return Err(HttpException::new(404, "Research not found"));
    }
    data.insert("archived".to_string(), json!(q.archived));
    if let Err(e) = write_json_compact(&path, &data) {
        return Err(HttpException::new(500, format!("Failed to update research: {e}")));
    }
    Ok(Json(json!({ "ok": true, "id": session_id, "archived": q.archived })))
}

/// `@router.delete("/api/research/{session_id}")` — delete a research result from
/// disk.
async fn research_delete(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    headers: HeaderMap,
    Path(session_id): Path<String>,
) -> Result<Json<Value>, HttpException> {
    let user = require_user(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    let json_path = state.research_handler.session_json_path(&session_id);
    // `deleted = False`
    let mut deleted = false;
    // `if json_path.exists():`
    if crate::pyos::path::exists(&json_path) {
        // SECURITY: verify ownership before letting the caller delete it.
        // try:
        //     data = json.loads(json_path.read_text())
        //     if data.get("owner") != user: raise HTTPException(404, "Research not found")
        // except HTTPException: raise
        // except Exception: raise HTTPException(404, "Research not found")
        let owner = match read_json_object(&json_path) {
            Some(d) => d.get("owner").cloned().unwrap_or(Value::Null),
            None => return Err(HttpException::new(404, "Research not found")),
        };
        if owner.as_str() != Some(user.as_str()) {
            return Err(HttpException::new(404, "Research not found"));
        }
        // `json_path.unlink(); deleted = True`
        let _ = std::fs::remove_file(&json_path);
        deleted = true;
    }
    Ok(Json(json!({ "deleted": deleted })))
}

// ---------------------------------------------------------------------------
// Panel endpoints — launch research without a chat session
// ---------------------------------------------------------------------------

/// `class ResearchStartRequest(BaseModel)` with the pydantic `Field(...)` bounds
/// (`max_rounds`: `ge=0, le=20`, default `0`; `max_time`: `ge=60, le=1800`,
/// default `300`).
///
/// `serde` deserializes the raw JSON; the `ge`/`le` checks are applied manually
/// in [`research_start`] (returning `422` on a bound violation, matching
/// pydantic's validation error status).
#[derive(Deserialize)]
struct ResearchStartRequest {
    query: String,
    // max_rounds=0 means "Auto" — let the AI decide when to stop, capped at 20.
    #[serde(default)]
    max_rounds: i64,
    #[serde(default)]
    search_provider: Option<String>,
    #[serde(default)]
    endpoint_id: Option<String>,
    #[serde(default)]
    model: Option<String>,
    #[serde(default = "default_max_time")]
    max_time: i64,
    #[serde(default)]
    category: Option<String>,
}
fn default_max_time() -> i64 {
    300
}

/// `@router.post("/api/research/start")` — launch a research job from the
/// dedicated panel.
async fn research_start(
    State(state): State<AppState>,
    user_ext: Option<Extension<CurrentUser>>,
    headers: HeaderMap,
    Json(body): Json<ResearchStartRequest>,
) -> Result<Json<Value>, HttpException> {
    // pydantic `Field(default=0, ge=0, le=20)` / `Field(default=300, ge=60, le=1800)`.
    if !(0..=20).contains(&body.max_rounds) {
        return Err(HttpException::new(422, "max_rounds must be between 0 and 20"));
    }
    if !(60..=1800).contains(&body.max_time) {
        return Err(HttpException::new(422, "max_time must be between 60 and 1800"));
    }

    // `user = require_privilege(request, "can_use_research")`
    let cu = user_ext.as_ref().map(|Extension(u)| u);
    let mut user = auth_adapter::require_privilege(
        cu.map(|u| u.0.as_str()),
        &state,
        client_host(&headers).as_deref(),
        "can_use_research",
    )?;

    // `if user == "internal-tool":` — internal-tool requests may impersonate an
    // owner via the X-Odysseus-Owner header.
    if user == "internal-tool" {
        // `tool_owner = (request.headers.get("X-Odysseus-Owner") or "").strip()`
        let tool_owner = headers
            .get("X-Odysseus-Owner")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .trim()
            .to_string();
        // `if tool_owner and tool_owner not in {"internal-tool","api","demo","system"}:`
        const RESERVED: &[&str] = &["internal-tool", "api", "demo", "system"];
        if !tool_owner.is_empty() && !RESERVED.contains(&tool_owner.as_str()) {
            // `auth_mgr = getattr(request.app.state, "auth_manager", None)` — always wired.
            let auth_mgr = &state.auth;
            // `if auth_mgr is not None and getattr(auth_mgr, "is_configured", False):`
            if auth_mgr.is_configured() {
                // try:
                //     privs = auth_mgr.get_privileges(tool_owner) or {}
                //     if not privs.get("can_use_research", True): raise HTTPException(403, ...)
                // except HTTPException: raise
                // except Exception: pass
                let privs = auth_mgr.get_privileges(&tool_owner);
                let can = privs
                    .get("can_use_research")
                    .and_then(Value::as_bool)
                    .unwrap_or(true);
                if !can {
                    return Err(HttpException::new(
                        403,
                        "Your account is not allowed to can use research.",
                    ));
                }
            }
            // `user = tool_owner`
            user = tool_owner;
        }
    }

    // `session_id = f"rp-{uuid.uuid4().hex[:12]}"`
    let session_id = format!("rp-{}", &uuid::Uuid::new_v4().simple().to_string()[..12]);

    if let Some(endpoint_id) = body.endpoint_id.as_deref().filter(|s| !s.is_empty()) {
        // `if body.endpoint_id:` — explicit endpoint (fully ported via raw query).
        // db.query(ModelEndpoint).filter(id == endpoint_id, is_enabled == True).first()
        let ep = match endpoint_row(endpoint_id, Some(user.as_str())) {
            // `if not ep: raise HTTPException(404, "Endpoint not found or disabled")`
            None => return Err(HttpException::new(404, "Endpoint not found or disabled")),
            Some(ep) => ep,
        };
        // `base = normalize_base(ep.base_url); ep_url = build_chat_url(base)`
        let base = normalize_base(Some(&ep.base_url));
        let ep_url = build_chat_url(&base);
        // `ep_headers = build_headers(ep.api_key, base)`
        let ep_headers = build_headers(ep.api_key.as_deref(), &base);
        // `ep_model = body.model or ""`
        let mut ep_model = body.model.clone().unwrap_or_default();
        // `if not ep_model: models = json.loads(ep.cached_models) if ... ; if models: ep_model = _first_chat_model(models)`
        if ep_model.is_empty() {
            if let Some(m) = ep.cached_models.as_deref().and_then(first_chat_from_cached) {
                ep_model = m;
            }
        }
        return start_and_respond(&state, &session_id, &body, ep_url, ep_model, ep_headers, &user);
    }

    // LIVE: the `resolve_endpoint("research" -> "utility" -> "default" ->
    // "chat")` settings cascade (research_routes.py:352-362). Each step is
    // guarded by `if not ep_url` and re-binds ALL THREE of (url, model, headers).
    // owner=None is faithful — Python passes no owner to these resolve_endpoint
    // calls (sess.* are only fallback_*; owner stays the default None).
    let mut ep_url = String::new();
    let mut ep_model = String::new();
    let mut ep_headers: indexmap::IndexMap<String, String> = indexmap::IndexMap::new();
    for prefix in ["research", "utility", "default", "chat"] {
        // `if not ep_url:` — only re-resolve while still unset.
        if ep_url.is_empty() {
            if let (Some(u), m, h) = resolve_endpoint(prefix, None) {
                ep_url = u;
                ep_model = m.unwrap_or_default();
                ep_headers = h.unwrap_or_default();
            }
        }
    }

    // When the settings cascade resolved an endpoint, apply the `body.model`
    // override (research_routes.py:389-390) and start — mirroring the explicit
    // `endpoint_id` branch's early return rather than falling through to the
    // first-enabled fallback (which would resolve a DIFFERENT endpoint).
    if !ep_url.is_empty() {
        // `if body.model: ep_model = body.model`
        if let Some(m) = body.model.clone().filter(|m| !m.is_empty()) {
            ep_model = m;
        }
        return start_and_respond(&state, &session_id, &body, ep_url, ep_model, ep_headers, &user);
    }

    // `if not ep_url:` — last resort: the first enabled endpoint VISIBLE to the
    // caller (owner-scoped fallback).
    if let Some(ep) = first_enabled_endpoint(Some(user.as_str())) {
        let base = normalize_base(Some(&ep.base_url));
        let ep_url = build_chat_url(&base);
        let ep_headers = build_headers(ep.api_key.as_deref(), &base);
        // `ep_model = ""; if ep.cached_models: ... ep_model = _first_chat_model(models)`
        let mut ep_model = ep
            .cached_models
            .as_deref()
            .and_then(first_chat_from_cached)
            .unwrap_or_default();
        // `if not ep_url: raise HTTPException(400, ...)` — ep_url is set here.
        // `if body.model: ep_model = body.model`
        if let Some(m) = body.model.clone().filter(|m| !m.is_empty()) {
            ep_model = m;
        }
        return start_and_respond(&state, &session_id, &body, ep_url, ep_model, ep_headers, &user);
    }

    // `if not ep_url: raise HTTPException(400, "No endpoints configured...")`
    Err(HttpException::new(
        400,
        "No endpoints configured. Add one in Settings first.",
    ))
}

/// Shared tail of `research_start`: kick off the background research task and
/// return the `{session_id, status, query}` body.
///
/// Mirrors the Python:
/// ```python
/// effective_max_rounds = body.max_rounds if body.max_rounds > 0 else 20
/// research_handler.start_research(session_id=..., query=..., llm_endpoint=...,
///     llm_model=..., max_time=..., llm_headers=..., max_rounds=...,
///     search_provider=body.search_provider or None,
///     category=body.category or None, owner=user)
/// return {"session_id": session_id, "status": "running", "query": body.query}
/// ```
fn start_and_respond(
    state: &AppState,
    session_id: &str,
    body: &ResearchStartRequest,
    ep_url: String,
    ep_model: String,
    ep_headers: indexmap::IndexMap<String, String>,
    user: &str,
) -> Result<Json<Value>, HttpException> {
    // max_rounds=0 → "Auto", let AI decide; pass 20 as the safety cap.
    let effective_max_rounds = if body.max_rounds > 0 { body.max_rounds } else { 20 };
    state.research_handler.start_research(StartResearchArgs {
        session_id: session_id.to_string(),
        query: body.query.clone(),
        llm_endpoint: ep_url,
        llm_model: ep_model,
        max_time: body.max_time,
        llm_headers: ep_headers,
        max_rounds: effective_max_rounds,
        // `body.search_provider or None` / `body.category or None` — empty → None.
        search_provider: body.search_provider.clone().filter(|s| !s.is_empty()),
        category: body.category.clone().filter(|s| !s.is_empty()),
        owner: user.to_string(),
        ..Default::default()
    });
    Ok(Json(json!({
        "session_id": session_id,
        "status": "running",
        "query": body.query,
    })))
}

/// `@router.get("/api/research/stream/{session_id}")` — SSE stream of research
/// progress events.
async fn research_stream(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    headers: HeaderMap,
    Path(session_id): Path<String>,
) -> Result<Response, HttpException> {
    let user = require_user(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    if !owns_in_memory(&state, &session_id, &user) {
        return Err(HttpException::new(404, "No research found for this session"));
    }

    // async def _generate(): poll get_status every 1.5s, emit progress + a final
    // event when the run leaves "running".
    let handler = state.research_handler.clone();
    let sid = session_id.clone();
    let sse = async_stream::stream! {
        let mut last_progress: Option<Value> = None;
        loop {
            // `status = research_handler.get_status(session_id)`
            let status = handler.get_status(&sid);
            let status = match status {
                // `if status is None: yield "data: {...not_found...}"; return`
                None => {
                    yield sse_bytes(&format!("data: {}\n\n", json!({"status": "not_found"})));
                    return;
                }
                Some(s) => s,
            };
            // `st = status.get("status", "")`
            let st = status.get("status").and_then(Value::as_str).unwrap_or("").to_string();
            // `progress = status.get("progress", {})`
            let progress = status.get("progress").cloned().unwrap_or_else(|| json!({}));
            // `if progress != last_progress:`
            if Some(&progress) != last_progress.as_ref() {
                last_progress = Some(progress.clone());
                // `yield f"data: {json.dumps({**progress, 'status': st})}\n\n"`
                let mut merged = match progress.clone() {
                    Value::Object(m) => m,
                    _ => Map::new(),
                };
                merged.insert("status".to_string(), json!(st));
                yield sse_bytes(&format!("data: {}\n\n", Value::Object(merged)));
            }
            // `if st != "running":`
            if st != "running" {
                // `final = {'status': st, 'final': True}`
                let mut final_obj = Map::new();
                final_obj.insert("status".to_string(), json!(st));
                final_obj.insert("final".to_string(), json!(true));
                // `task = research_handler._active_tasks.get(session_id, {})`
                // `if st == "error" and task.get("result"): final['error'] = str(task["result"])[:500]`
                if st == "error" {
                    if let Some(result) = handler.active_result(&sid) {
                        if !result.is_empty() {
                            let truncated: String = result.chars().take(500).collect();
                            final_obj.insert("error".to_string(), json!(truncated));
                        }
                    }
                }
                yield sse_bytes(&format!("data: {}\n\n", Value::Object(final_obj)));
                return;
            }
            // `await asyncio.sleep(1.5)`
            tokio::time::sleep(std::time::Duration::from_millis(1500)).await;
        }
    };

    // StreamingResponse(..., media_type="text/event-stream",
    //   headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    Ok(Response::builder()
        .header(header::CONTENT_TYPE, "text/event-stream")
        .header(header::CACHE_CONTROL, "no-cache")
        .header("X-Accel-Buffering", "no")
        .body(Body::from_stream(sse))
        .unwrap())
}

/// `@router.post("/api/research/result-peek/{session_id}")` — get research result
/// without clearing it (for panel use).
async fn research_result_peek(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    headers: HeaderMap,
    Path(session_id): Path<String>,
) -> Result<Json<Value>, HttpException> {
    let user = require_user(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    if !owns_in_memory(&state, &session_id, &user) {
        return Err(HttpException::new(404, "No research found for this session"));
    }
    // `result = research_handler.get_result(session_id)`
    let result = state.research_handler.get_result(&session_id);
    match result {
        // `if result is None:` — fall back to the on-disk JSON.
        None => {
            let path = state.research_handler.session_json_path(&session_id);
            // `p = Path(...); if p.exists():`
            if crate::pyos::path::exists(&path) {
                // `d = json.loads(p.read_text())` (Python lets a parse error 500; here
                // a non-object yields the empty defaults below — both are unreachable
                // in practice since the handler always writes a valid object).
                let d = read_json_object(&path).unwrap_or_default();
                return Ok(Json(json!({
                    "result": d.get("result").cloned().unwrap_or(json!("")),
                    "sources": d.get("sources").cloned().unwrap_or(json!([])),
                    "raw_findings": d.get("raw_findings").cloned().unwrap_or(json!([])),
                    "category": or_empty_str(d.get("category")),
                })));
            }
            // `raise HTTPException(404, "No research result available")`
            Err(HttpException::new(404, "No research result available"))
        }
        Some(result) => {
            // `sources = research_handler.get_sources(session_id) or []`
            let sources = state.research_handler.get_sources(&session_id).unwrap_or_default();
            // `raw_findings = research_handler.get_raw_findings(session_id) or []`
            let raw_findings = state.research_handler.get_raw_findings(&session_id).unwrap_or_default();
            Ok(Json(json!({
                "result": result,
                "sources": sources,
                "raw_findings": raw_findings,
                "category": "",
            })))
        }
    }
}

/// `@router.post("/api/research/spinoff/{session_id}")` — create a new chat session
/// pre-seeded with this research as context.
async fn research_spinoff(
    State(state): State<AppState>,
    user_ext: Option<Extension<CurrentUser>>,
    headers: HeaderMap,
    Path(session_id): Path<String>,
) -> Result<Json<Value>, HttpException> {
    // `_require_user(request)`
    require_user(&state, &headers, user_ext.as_ref().map(|Extension(u)| u))?;
    // `if session_manager is None: raise HTTPException(500, ...)` — in the Rust app
    // the session manager is always wired (AppState.sessions), so this guard is
    // structurally satisfied; preserved as a comment for parity.

    // Load research data — prefer in-memory result, fall back to disk.
    // `result = research_handler.get_result(session_id)`
    let mut result = state.research_handler.get_result(&session_id);
    // `sources = research_handler.get_sources(session_id) or []`
    let mut sources = state.research_handler.get_sources(&session_id).unwrap_or_default();
    // `query = ""`
    let mut query = String::new();

    // `path = Path(...); if path.exists():`
    let path = state.research_handler.session_json_path(&session_id);
    if crate::pyos::path::exists(&path) {
        // `try: disk = json.loads(path.read_text())`
        if let Some(disk) = read_json_object(&path) {
            // `if not result: result = disk.get("result")`
            if result.as_deref().map(str::is_empty).unwrap_or(true) {
                result = disk.get("result").and_then(Value::as_str).map(String::from);
            }
            // `if not sources: sources = disk.get("sources", []) or []`
            if sources.is_empty() {
                sources = disk
                    .get("sources")
                    .and_then(Value::as_array)
                    .cloned()
                    .unwrap_or_default();
            }
            // `query = disk.get("query", "") or ""`
            query = disk.get("query").and_then(Value::as_str).unwrap_or("").to_string();
        } else {
            crate::pylog::warning("Could not read research JSON for spinoff: invalid JSON");
        }
    }

    // `if not result: raise HTTPException(404, ...)`
    let result = match result.filter(|r| !r.is_empty()) {
        None => return Err(HttpException::new(404, "No research result available for this session")),
        Some(r) => r,
    };

    // Inherit endpoint/model/headers from the source session when possible.
    let mut ep_url = String::new();
    let mut ep_model = String::new();
    let mut ep_headers: indexmap::IndexMap<String, String> = indexmap::IndexMap::new();
    // try: src_sess = session_manager.get_session(session_id); ep_* = src_sess.* ; except KeyError: pass
    if let Ok(src_sess) = state.sessions.get_session(&session_id) {
        ep_url = src_sess.endpoint_url.clone();
        ep_model = src_sess.model.clone();
        ep_headers = src_sess.headers.clone();
    }

    // `_merge(r_url, r_model, r_headers)` (research_routes.py:510-517): fill any
    // still-empty field from the resolved triple.
    let merge = |ep_url: &mut String, ep_model: &mut String, ep_headers: &mut indexmap::IndexMap<String, String>,
                 r_url: &str, r_model: &str, r_headers: &indexmap::IndexMap<String, String>| {
        // `if not ep_url and r_url: ep_url = r_url`
        if ep_url.is_empty() && !r_url.is_empty() {
            *ep_url = r_url.to_string();
        }
        if ep_model.is_empty() && !r_model.is_empty() {
            *ep_model = r_model.to_string();
        }
        if ep_headers.is_empty() && !r_headers.is_empty() {
            *ep_headers = r_headers.clone();
        }
    };

    // LIVE: the `resolve_endpoint("chat" -> "research" -> "utility")` `_merge(...)`
    // cascade (research_routes.py:519-524), each guarded by
    // `if not ep_url or not ep_model`. owner=None is faithful — Python passes no
    // owner to these resolve_endpoint calls.
    for prefix in ["chat", "research", "utility"] {
        // `if not ep_url or not ep_model: _merge(*resolve_endpoint(prefix))`
        if ep_url.is_empty() || ep_model.is_empty() {
            let (u, m, h) = resolve_endpoint(prefix, None);
            let r_url = u.unwrap_or_default();
            let r_model = m.unwrap_or_default();
            let r_headers = h.unwrap_or_default();
            merge(&mut ep_url, &mut ep_model, &mut ep_headers, &r_url, &r_model, &r_headers);
        }
    }

    // `if not ep_url or not ep_model:` — last resort: any enabled endpoint.
    // owner=None here is faithful (this resolver is owner-agnostic, same as its
    // `resolve_endpoint(prefix, None)` calls above); the owner-scoped fallback is
    // only in the `/api/research/start` handler the upstream fix touched.
    if ep_url.is_empty() || ep_model.is_empty() {
        if let Some(ep) = first_enabled_endpoint(None) {
            let base = normalize_base(Some(&ep.base_url));
            let fallback_url = build_chat_url(&base);
            let fallback_headers = build_headers(ep.api_key.as_deref(), &base);
            // `fallback_model = ""; if ep.cached_models: models = json.loads(...); if models: fallback_model = models[0]`
            let fallback_model = ep
                .cached_models
                .as_deref()
                .and_then(|c| serde_json::from_str::<Vec<String>>(c).ok())
                .and_then(|m| m.first().cloned())
                .unwrap_or_default();
            merge(&mut ep_url, &mut ep_model, &mut ep_headers, &fallback_url, &fallback_model, &fallback_headers);
        }
    }

    // `if not ep_url or not ep_model: raise HTTPException(400, ...)`
    if ep_url.is_empty() || ep_model.is_empty() {
        return Err(HttpException::new(400, "No endpoint configured — add one in Settings first"));
    }

    // Create new session
    // `new_sid = str(uuid.uuid4())`
    let new_sid = uuid::Uuid::new_v4().to_string();
    // `user = get_current_user(request)` — the stamped user (may be None/absent).
    let user = user_ext.map(|Extension(u)| u.0);

    // `title_query = (query or "research").strip()`
    let title_query = if query.trim().is_empty() {
        "research".to_string()
    } else {
        query.trim().to_string()
    };
    // `if len(title_query) > 60: title_query = title_query[:57] + "…"` (char-based).
    let title_query = if title_query.chars().count() > 60 {
        let head: String = title_query.chars().take(57).collect();
        format!("{head}\u{2026}")
    } else {
        title_query
    };
    // `new_name = f"Follow-up: {title_query}"`
    let new_name = format!("Follow-up: {title_query}");

    // new_sess = session_manager.create_session(session_id=new_sid, name=new_name,
    //     endpoint_url=ep_url, model=ep_model, rag=False, owner=user)
    if let Err(e) = state.sessions.create_session(&new_sid, &new_name, &ep_url, &ep_model, false, user.as_deref()) {
        return Err(HttpException::new(500, e.to_string()));
    }
    // `if ep_headers: new_sess.headers = ep_headers; session_manager.save_sessions()`
    //   The Rust SessionManager is DB-backed; setting headers is done via
    //   `update_session_headers` (and `save_sessions()` is a no-op).
    if !ep_headers.is_empty() {
        state.sessions.update_session_headers(&new_sid, ep_headers);
    }
    // try: from src.event_bus import fire_event; fire_event("session_created", user)
    // except Exception: logger.debug(...)
    crate::src::event_bus::fire_event("session_created", user.as_deref());

    // Build the priming system message — report only, no sources injected.
    // `date_str = datetime.utcnow().strftime("%Y-%m-%d")`
    let date_str = crate::pydatetime::utcnow_naive().format("%Y-%m-%d").to_string();
    // `query or '(not recorded)'`
    let query_display = if query.is_empty() { "(not recorded)" } else { &query };
    let primer = format!(
        "[Research context — {date_str}]\n\n\
The user previously ran a deep research investigation. Use the report below as \
your primary knowledge base when answering follow-up questions. If the user asks \
something not covered, say so plainly rather than guessing.\n\n\
=== ORIGINAL QUERY ===\n{query_display}\n\n\
=== REPORT ===\n{result}"
    );

    // new_sess.add_message(ChatMessage(role="system", content=primer,
    //     metadata={"research_spinoff_from": session_id}))
    // session_manager.save_sessions()
    let mut metadata = Map::new();
    metadata.insert("research_spinoff_from".to_string(), json!(session_id));
    let _ = state.sessions.add_message(
        &new_sid,
        crate::core::models::ChatMessage::new("system", primer, Some(metadata)),
    );

    // `return {"session_id": new_sid, "name": new_name, "source_count": len(sources)}`
    Ok(Json(json!({
        "session_id": new_sid,
        "name": new_name,
        "source_count": sources.len(),
    })))
}

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------

/// `setup_research_routes(research_handler, session_manager=None)` — assemble the
/// research router.
///
/// `APIRouter(tags=["research"])` has **no** `prefix`, so each handler carries its
/// full absolute `/api/research/...` path (axum 0.7 colon path captures). Both
/// deps come from [`AppState`] (`research_handler` / `sessions`), so the factory
/// takes no parameters. None of these paths collide with the inline `web/mod.rs`
/// subset (which owns no `/api/research/*`).
pub fn setup_research_routes() -> Router<AppState> {
    Router::new()
        .route("/api/research/active", get(research_active))
        .route("/api/research/status/:session_id", get(research_status))
        .route("/api/research/cancel/:session_id", post(research_cancel))
        .route("/api/research/result/:session_id", post(research_result))
        .route("/api/research/report/:session_id", get(research_report))
        .route("/api/research/:session_id/hide-image", post(research_hide_image))
        .route("/api/research/:session_id/unhide-images", post(research_unhide_images))
        .route("/api/research/library", get(research_library))
        .route("/api/research/detail/:session_id", get(research_detail))
        .route("/api/research/:session_id/archive", post(research_archive))
        .route("/api/research/:session_id", delete(research_delete))
        .route("/api/research/start", post(research_start))
        .route("/api/research/stream/:session_id", get(research_stream))
        .route("/api/research/result-peek/:session_id", post(research_result_peek))
        .route("/api/research/spinoff/:session_id", post(research_spinoff))
}

// ---------------------------------------------------------------------------
// Small utilities (the Python json/Path idioms used by the routes)
// ---------------------------------------------------------------------------

/// A minimal `ModelEndpoint` row projection the resolution branches read.
struct EndpointRow {
    base_url: String,
    /// The DECRYPTED api key (matching `ep.api_key` after the ORM's `EncryptedText`
    /// descriptor decrypts it).
    api_key: Option<String>,
    cached_models: Option<String>,
}

/// `db.query(ModelEndpoint).filter(ModelEndpoint.id == id, is_enabled == True).first()`.
/// Returns `None` when missing or disabled. The api key is decrypted in place
/// (mirroring the ORM `EncryptedText` read path used by `endpoint_auth`).
fn endpoint_row(id: &str, owner: Option<&str>) -> Option<EndpointRow> {
    use rusqlite::OptionalExtension;
    let conn = session_local().ok()?;
    // `_owned_enabled_endpoint(db, owner, endpoint_id)` — owner-scoped (`owner_filter`)
    // so a research-privileged user can't resolve ANOTHER user's private endpoint
    // (and its decrypted api_key / internal base_url). Empty/None owner = no-op.
    let owner = owner.filter(|o| !o.is_empty());
    let (base_url, enc_key, cached): (String, Option<String>, Option<String>) = match owner {
        Some(o) => conn
            .query_row(
                "SELECT base_url, api_key, cached_models FROM model_endpoints \
                 WHERE id = ?1 AND is_enabled = 1 AND (owner = ?2 OR owner IS NULL)",
                rusqlite::params![id, o],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )
            .optional()
            .ok()??,
        None => conn
            .query_row(
                "SELECT base_url, api_key, cached_models FROM model_endpoints \
                 WHERE id = ?1 AND is_enabled = 1",
                [id],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )
            .optional()
            .ok()??,
    };
    let api_key = enc_key
        .filter(|k| !k.is_empty())
        .map(|k| crate::src::secret_storage::decrypt(&k));
    Some(EndpointRow {
        base_url,
        api_key,
        cached_models: cached,
    })
}

/// `_owned_enabled_endpoint(db, owner)` — the owner-scoped first-enabled fallback:
/// the caller's own rows + legacy null-owner "shared" rows only (never borrow
/// another user's private endpoint/api_key). Empty/None owner = no-op.
fn first_enabled_endpoint(owner: Option<&str>) -> Option<EndpointRow> {
    use rusqlite::OptionalExtension;
    let conn = session_local().ok()?;
    let owner = owner.filter(|o| !o.is_empty());
    let (base_url, enc_key, cached): (String, Option<String>, Option<String>) = match owner {
        Some(o) => conn
            .query_row(
                "SELECT base_url, api_key, cached_models FROM model_endpoints \
                 WHERE is_enabled = 1 AND (owner = ?1 OR owner IS NULL) \
                 ORDER BY created_at LIMIT 1",
                [o],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )
            .optional()
            .ok()??,
        None => conn
            .query_row(
                "SELECT base_url, api_key, cached_models FROM model_endpoints \
                 WHERE is_enabled = 1 ORDER BY created_at LIMIT 1",
                [],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )
            .optional()
            .ok()??,
    };
    let api_key = enc_key
        .filter(|k| !k.is_empty())
        .map(|k| crate::src::secret_storage::decrypt(&k));
    Some(EndpointRow {
        base_url,
        api_key,
        cached_models: cached,
    })
}

/// `models = json.loads(cached); if models: _first_chat_model(models)`.
fn first_chat_from_cached(cached: &str) -> Option<String> {
    let models: Vec<String> = serde_json::from_str(cached).ok()?;
    if models.is_empty() {
        return None;
    }
    _first_chat_model(&models)
}

/// `for p in data_dir.glob("*.json")` — the `.json` files directly under `dir`.
fn glob_json(dir: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    if let Ok(rd) = std::fs::read_dir(dir) {
        for ent in rd.flatten() {
            let path = ent.path();
            if path.extension().and_then(|e| e.to_str()) == Some("json") {
                if let Some(p) = path.to_str() {
                    out.push(p.to_string());
                }
            }
        }
    }
    out
}

/// `Path(path).stem` — the file name without its final extension.
fn path_stem(path: &str) -> String {
    std::path::Path::new(path)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_string()
}

/// `json.loads(path.read_text())` as an object, or `None` on any error (the
/// Python try/except-swallow pattern).
fn read_json_object(path: &str) -> Option<Map<String, Value>> {
    let text = std::fs::read_to_string(path).ok()?;
    match serde_json::from_str::<Value>(&text) {
        Ok(Value::Object(m)) => Some(m),
        _ => None,
    }
}

/// `json.loads(path.read_text())` as a `Value`, surfacing the error string (for the
/// `/detail` handler's `raise HTTPException(500, f"Failed to read research: {e}")`).
fn read_json_value(path: &str) -> Result<Value, String> {
    let text = std::fs::read_to_string(path).map_err(|e| e.to_string())?;
    serde_json::from_str::<Value>(&text).map_err(|e| e.to_string())
}

/// `path.write_text(json.dumps(data))` — compact JSON (no indent).
fn write_json_compact(path: &str, data: &Map<String, Value>) -> Result<(), String> {
    let text = serde_json::to_string(&Value::Object(data.clone())).map_err(|e| e.to_string())?;
    std::fs::write(path, text).map_err(|e| e.to_string())
}

/// Python truthiness for an optional `Value` (`bool(d.get(...))`).
fn truthy(v: Option<&Value>) -> bool {
    match v {
        Some(Value::Bool(b)) => *b,
        Some(Value::Null) | None => false,
        Some(Value::Number(n)) => n.as_f64().map(|x| x != 0.0).unwrap_or(true),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

/// `d.get(key) or ""` — the value if it is a non-empty string, else `""`.
fn or_empty_str(v: Option<&Value>) -> Value {
    match v {
        Some(Value::String(s)) if !s.is_empty() => Value::String(s.clone()),
        _ => Value::String(String::new()),
    }
}

/// A sort key for `x["completed_at"] or 0` / `x["source_count"]` — the numeric
/// value, treating `null`/non-number/`0` as `0.0` (Python `or 0`).
fn num_or_zero(v: &Value) -> f64 {
    v.as_f64().unwrap_or(0.0)
}

/// `String -> Ok(Bytes)` for the SSE body stream (infallible).
fn sse_bytes(s: &str) -> Result<bytes::Bytes, std::convert::Infallible> {
    Ok(bytes::Bytes::from(s.to_owned()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn truthy_matches_python_bool() {
        assert!(!truthy(None));
        assert!(!truthy(Some(&Value::Null)));
        assert!(!truthy(Some(&json!(false))));
        assert!(truthy(Some(&json!(true))));
        assert!(!truthy(Some(&json!(""))));
        assert!(truthy(Some(&json!("x"))));
        assert!(!truthy(Some(&json!(0))));
        assert!(truthy(Some(&json!(1))));
    }

    #[test]
    fn or_empty_str_only_keeps_nonempty_strings() {
        assert_eq!(or_empty_str(Some(&json!("cat"))), json!("cat"));
        assert_eq!(or_empty_str(Some(&json!(""))), json!(""));
        assert_eq!(or_empty_str(Some(&Value::Null)), json!(""));
        assert_eq!(or_empty_str(None), json!(""));
        // A non-string (e.g. number) collapses to "" — matches `d.get(...) or ""`
        // when the JSON happens to hold a non-string falsy/odd value.
        assert_eq!(or_empty_str(Some(&json!(5))), json!(""));
    }

    #[test]
    fn path_stem_strips_extension() {
        assert_eq!(path_stem("/tmp/data/deep_research/rp-abc123.json"), "rp-abc123");
        assert_eq!(path_stem("rp-xyz.json"), "rp-xyz");
    }

    #[test]
    fn first_chat_from_cached_skips_embeddings() {
        // `_first_chat_model` skips embedding/tts/etc; first chat model wins.
        let cached = serde_json::to_string(&vec!["text-embedding-ada-002", "gpt-4o"]).unwrap();
        assert_eq!(first_chat_from_cached(&cached).as_deref(), Some("gpt-4o"));
        // Empty list -> None.
        assert_eq!(first_chat_from_cached("[]"), None);
        // Invalid JSON -> None (the Python try/except-pass).
        assert_eq!(first_chat_from_cached("not json"), None);
    }

    #[test]
    fn num_or_zero_handles_null_and_numbers() {
        assert_eq!(num_or_zero(&json!(0)), 0.0);
        assert_eq!(num_or_zero(&Value::Null), 0.0);
        assert_eq!(num_or_zero(&json!(12.5)), 12.5);
        assert_eq!(num_or_zero(&json!("x")), 0.0);
    }
}
