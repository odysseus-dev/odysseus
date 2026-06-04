// routes/codex_routes.rs  <- routes/codex_routes.py (the Codex/Claude agent HTTP surface)
//! Codex / Claude integration routes.
//!
//! These are small HTTP surfaces intended for the Codex plugin / MCP bridge and the
//! Claude Code skill. They reuse existing Odysseus helpers and enforce API-token
//! scopes before touching user data.
//!
//! The Python factory (`setup_codex_routes`) closure-captured the email / memory /
//! calendar / document FastAPI endpoints and dispatched into them as the scope-gated
//! `owner`. In the axum port the endpoints are reached through the **owner-taking
//! tool layer** (`do_manage_notes` / `do_manage_calendar` / `do_manage_documents`,
//! which already take `owner` and return a JSON map), the `MemoryManager` on
//! [`AppState`], and the existing email handlers (invoked with the scoped owner
//! injected as `Some(Extension(CurrentUser(owner)))` — the `_as_owner` impersonation
//! analogue). This module exposes two router factories:
//!   * [`codex_router`] — the `/api/codex/*` surface;
//!   * [`claude_router`] — `/api/claude/plugin.zip`.
//!
//! ## Scope-gating (`_scope_owner`)
//! [`scope_owner`] is the auth core: when the caller presents a validated API token,
//! its scopes must intersect the action's allowed set (else 403) and it must carry an
//! owner (else 403). Otherwise the caller falls through to `require_user`
//! (cookie-session auth), whose resolved username is the owner. This is the same
//! shape as the Python `_scope_owner`.
//!
//! ## Documents deviation (faithful, documented)
//! The owner-taking `do_manage_documents` tool implements `list` / `read` / `delete`
//! (+ `tidy`) but NOT `library` or `create`. The Python codex routes call the
//! `documents_library` / `create_document` FastAPI handlers for those. Here:
//!   * `library` maps to the tool's `list` action (the faithful owner-scoped library
//!     listing the tool performs);
//!   * `read` / `delete` map straight to the tool's `read` / `delete`;
//!   * `create` has NO tool action, so it calls the `create_document` route handler
//!     (made `pub(crate)`) with the scoped owner injected — the only owner-scoped
//!     create path.

use std::collections::HashMap;

use axum::body::Body;
use axum::extract::{ConnectInfo, Path, Query, State};
use axum::http::{header, StatusCode};
use axum::response::Response;
use axum::routing::{delete, get, post};
use axum::{Extension, Json, Router};
use serde_json::{json, Map, Value};
use std::net::SocketAddr;

use crate::routes::{ApiToken, AppState, CurrentUser, HttpException};

// ===========================================================================
// Scope-set constants — verbatim from codex_routes.py.
// ===========================================================================

const TODO_READ_SCOPES: &[&str] = &["todos:read", "todos:write"];
const TODO_WRITE_SCOPES: &[&str] = &["todos:write"];
const EMAIL_READ_SCOPES: &[&str] = &["email:read", "email:draft", "email:send"];
const EMAIL_DRAFT_SCOPES: &[&str] = &["email:draft", "email:send"];
const EMAIL_SEND_SCOPES: &[&str] = &["email:send"];
const MEMORY_READ_SCOPES: &[&str] = &["memory:read", "memory:write"];
const MEMORY_WRITE_SCOPES: &[&str] = &["memory:write"];
const CALENDAR_READ_SCOPES: &[&str] = &["calendar:read", "calendar:write"];
const CALENDAR_WRITE_SCOPES: &[&str] = &["calendar:write"];
const DOCS_READ_SCOPES: &[&str] = &["documents:read", "documents:write"];
const DOCS_WRITE_SCOPES: &[&str] = &["documents:write"];
const WRITE_ACTIONS: &[&str] = &[
    "add", "create", "new", "save", "remind", "update", "delete", "toggle_item", "remove",
    "remove_item",
];

// ===========================================================================
// `_scope_owner` (the auth core) + helpers.
// ===========================================================================

/// `host_str` from `ConnectInfo<SocketAddr>` (the `request.client.host` analogue),
/// mirroring the sibling handlers' helper.
fn host_str(ci: &Option<ConnectInfo<SocketAddr>>) -> Option<String> {
    ci.as_ref().map(|ConnectInfo(a)| a.ip().to_string())
}

/// `_scope_owner(request, allowed)` — return the data owner if the caller is allowed
/// for this Codex action.
///
/// * If a validated API token is present: its scopes must intersect `allowed` (else
///   403 `"API token missing required scope: {a or b...}"`, sorted + joined `" or "`),
///   and it must carry a non-empty owner (else 403 `"API token has no owner"`).
/// * Otherwise fall back to `require_user` (cookie-session auth), returning its owner.
fn scope_owner(
    token: &Option<Extension<ApiToken>>,
    state: &AppState,
    user: Option<&str>,
    host: Option<&str>,
    allowed: &[&str],
) -> Result<String, HttpException> {
    // `if getattr(request.state, "api_token", False):` — when a validated token is
    // present, the scope/owner check is a pure function of the token + allowed set
    // and never consults AppState; otherwise fall through to require_user.
    if let Some(Extension(tok)) = token {
        if tok.present {
            return scope_owner_from_token(tok, allowed);
        }
    }
    // `return require_user(request)`
    crate::routes::auth_adapter::require_user(user, state, host)
}

/// The token branch of `_scope_owner`: a pure function of the validated token + the
/// action's allowed scope set (no AppState). Returns the token owner, or the 403 the
/// Python raises for a missing scope / missing owner.
fn scope_owner_from_token(tok: &ApiToken, allowed: &[&str]) -> Result<String, HttpException> {
    // `if not scopes.intersection(allowed): raise 403 "...missing required scope..."`
    let has = tok.scopes.iter().any(|s| allowed.contains(&s.as_str()));
    if !has {
        // `required = " or ".join(sorted(allowed))`
        let mut req: Vec<&str> = allowed.to_vec();
        req.sort_unstable();
        return Err(HttpException::new(
            403,
            format!("API token missing required scope: {}", req.join(" or ")),
        ));
    }
    // `owner = ...api_token_owner...; if not owner: raise 403 "API token has no owner"`
    match tok.owner.as_deref() {
        Some(o) if !o.is_empty() => Ok(o.to_string()),
        _ => Err(HttpException::new(403, "API token has no owner")),
    }
}

/// Resolve the optional stamped `CurrentUser` to `Option<String>`.
fn user_str(user: &Option<Extension<CurrentUser>>) -> Option<String> {
    user.as_ref().map(|Extension(CurrentUser(u))| u.clone())
}

// ===========================================================================
// Router factories.
// ===========================================================================

/// `setup_codex_routes(...)` — the `/api/codex/*` router (prefix baked into each
/// path so it merges directly into the aggregator like the sibling factories).
pub fn codex_router() -> Router<AppState> {
    Router::new()
        .route("/api/codex/capabilities", get(capabilities))
        .route("/api/codex/plugin.zip", get(codex_plugin_zip))
        .route("/api/codex/todos", get(list_todos).post(manage_todos))
        .route("/api/codex/emails", get(list_emails))
        .route("/api/codex/emails/:uid", get(read_email))
        .route("/api/codex/emails/draft", post(email_draft))
        .route("/api/codex/emails/send", post(email_send))
        .route("/api/codex/memory", get(memory_list).post(memory_add))
        .route("/api/codex/memory/:memory_id", delete(memory_delete))
        .route(
            "/api/codex/calendar/events",
            get(calendar_list).post(calendar_create),
        )
        .route("/api/codex/calendar/events/:uid", delete(calendar_delete))
        .route(
            "/api/codex/documents",
            get(documents_library).post(documents_create),
        )
        .route(
            "/api/codex/documents/:doc_id",
            get(documents_get).delete(documents_delete),
        )
}

/// `setup_claude_routes()` — serve the Claude Code skill bundle. Claude Code uses the
/// same scope-gated `/api/codex/*` endpoints at runtime; this router only exists to
/// deliver the skill zip via `/api/claude/plugin.zip`.
pub fn claude_router() -> Router<AppState> {
    Router::new().route("/api/claude/plugin.zip", get(claude_plugin_zip))
}

// ===========================================================================
// /api/codex/capabilities — pure logic.
// ===========================================================================

async fn capabilities(token: Option<Extension<ApiToken>>) -> Json<Value> {
    // token_scopes = set(... api_token_scopes ...); has_token = bool(... api_token ...)
    let (has_token, token_scopes): (bool, Vec<String>) = match &token {
        Some(Extension(tok)) if tok.present => (true, tok.scopes.clone()),
        _ => (false, Vec::new()),
    };
    // def scoped(allowed): return bool(scopes & allowed) if has_token else True
    let scoped = |allowed: &[&str]| -> bool {
        if has_token {
            token_scopes.iter().any(|s| allowed.contains(&s.as_str()))
        } else {
            true
        }
    };
    // sorted(token_scopes)
    let mut sorted_scopes = token_scopes.clone();
    sorted_scopes.sort_unstable();

    Json(json!({
        "integration": "codex",
        "token_scopes": sorted_scopes,
        "tools": {
            "todos": {
                "read": scoped(TODO_READ_SCOPES),
                "write": scoped(TODO_WRITE_SCOPES),
                "actions": ["list", "add", "update", "delete", "toggle_item"],
            },
            "email": {
                "read": scoped(EMAIL_READ_SCOPES),
                "draft": scoped(EMAIL_DRAFT_SCOPES),
                "send": scoped(EMAIL_SEND_SCOPES),
                "actions": ["list", "read", "draft", "send"],
            },
            "memory": {
                "read": scoped(MEMORY_READ_SCOPES),
                "write": scoped(MEMORY_WRITE_SCOPES),
                "actions": ["list", "add", "delete"],
                // Memory integration is always wired into the axum AppState.
                "available": true,
            },
            "calendar": {
                "read": scoped(CALENDAR_READ_SCOPES),
                "write": scoped(CALENDAR_WRITE_SCOPES),
                "actions": ["list_events", "create_event", "delete_event"],
                // Calendar integration is always wired (local SQLite-backed tool).
                "available": true,
            },
            "documents": {
                "read": scoped(DOCS_READ_SCOPES),
                "write": scoped(DOCS_WRITE_SCOPES),
                "actions": ["library", "read", "create", "delete"],
                // Documents integration is always wired (local SQLite-backed tool).
                "available": true,
            },
        },
        "safety": {
            "email_send_requires_confirmation": true,
            "destructive_actions_should_confirm": true,
        },
    }))
}

// ===========================================================================
// /api/codex/todos — the management_db notes tool.
// ===========================================================================

/// `GET /api/codex/todos?archived=&label=` -> `do_manage_notes({action:"list", ...})`.
async fn list_todos(
    State(s): State<AppState>,
    token: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Json<Map<String, Value>>, HttpException> {
    let owner = scope_owner(
        &token,
        &s,
        user_str(&user).as_deref(),
        host_str(&ci).as_deref(),
        TODO_READ_SCOPES,
    )?;
    // archived: bool = False — FastAPI bool coercion (true/1/on/yes).
    let archived = q
        .get("archived")
        .map(|v| matches!(v.trim().to_lowercase().as_str(), "true" | "1" | "on" | "yes"))
        .unwrap_or(false);
    let mut args = Map::new();
    args.insert("action".to_string(), Value::String("list".to_string()));
    args.insert("archived".to_string(), Value::Bool(archived));
    if let Some(label) = q.get("label").filter(|l| !l.is_empty()) {
        args.insert("label".to_string(), Value::String(label.clone()));
    }
    let body = Value::Object(args).to_string();
    Ok(Json(
        crate::src::tool_implementations::management_db::do_manage_notes(&body, Some(&owner)).await,
    ))
}

/// `POST /api/codex/todos` — the scope is `WRITE` for write actions, else `READ`.
async fn manage_todos(
    State(s): State<AppState>,
    token: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    body: Option<Json<Value>>,
) -> Result<Json<Map<String, Value>>, HttpException> {
    // body: dict[str, Any] = Body(default_factory=dict)
    let mut obj = json_object(body);
    // action = str(body.get("action") or "add").replace("-", "_").strip().lower()
    let action = obj
        .get("action")
        .and_then(value_as_str)
        .filter(|a| !a.is_empty())
        .unwrap_or_else(|| "add".to_string())
        .replace('-', "_")
        .trim()
        .to_lowercase();
    let allowed = if WRITE_ACTIONS.contains(&action.as_str()) {
        TODO_WRITE_SCOPES
    } else {
        TODO_READ_SCOPES
    };
    let owner = scope_owner(
        &token,
        &s,
        user_str(&user).as_deref(),
        host_str(&ci).as_deref(),
        allowed,
    )?;
    // args = dict(body); args["action"] = action
    obj.insert("action".to_string(), Value::String(action));
    let body_str = Value::Object(obj).to_string();
    Ok(Json(
        crate::src::tool_implementations::management_db::do_manage_notes(&body_str, Some(&owner))
            .await,
    ))
}

// ===========================================================================
// /api/codex/emails — delegate to the email_routes handlers as the scoped owner.
// ===========================================================================

/// `GET /api/codex/emails` — list. Clamps `limit` to `1..=50`, `offset >= 0`, then
/// delegates to `email_routes::list_emails` with the scoped owner injected (the
/// `_as_owner` impersonation). The handler resolves `require_owner` (which runs
/// `_assert_owns_account` when `account_id` is present), so the ownership check the
/// Python performs is reproduced for free.
async fn list_emails(
    State(s): State<AppState>,
    token: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    // No ConnectInfo here: the token branch never needs host, and the require_user
    // fall-through resolves a cookie-session user without it.
    let host: Option<&str> = None;
    let owner = scope_owner(&token, &s, user_str(&user).as_deref(), host, EMAIL_READ_SCOPES)?;

    // folder/filter/from/account_id/has_attachments pass through; limit/offset clamp.
    let mut fwd: HashMap<String, String> = HashMap::new();
    fwd.insert(
        "folder".to_string(),
        q.get("folder").cloned().unwrap_or_else(|| "INBOX".to_string()),
    );
    fwd.insert(
        "filter".to_string(),
        q.get("filter").cloned().unwrap_or_else(|| "all".to_string()),
    );
    if let Some(v) = q.get("from_addr").or_else(|| q.get("from")) {
        fwd.insert("from".to_string(), v.clone());
    }
    if let Some(v) = q.get("account_id").filter(|a| !a.is_empty()) {
        fwd.insert("account_id".to_string(), v.clone());
    }
    // limit = max(1, min(int(limit or 10), 50)); offset = max(0, int(offset or 0))
    let limit = q.get("limit").and_then(|v| v.parse::<i64>().ok()).unwrap_or(10);
    let limit = limit.clamp(1, 50);
    let offset = q.get("offset").and_then(|v| v.parse::<i64>().ok()).unwrap_or(0).max(0);
    fwd.insert("limit".to_string(), limit.to_string());
    fwd.insert("offset".to_string(), offset.to_string());
    let has_attachments = q.get("has_attachments").and_then(|v| v.parse::<i64>().ok()).unwrap_or(0);
    fwd.insert("has_attachments".to_string(), has_attachments.to_string());

    crate::routes::email_routes::list_emails(
        State(s),
        Some(Extension(CurrentUser(owner))),
        None,
        Query(fwd),
    )
    .await
}

/// `GET /api/codex/emails/{uid}` — read a single message as the scoped owner.
async fn read_email(
    State(s): State<AppState>,
    token: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
    Path(uid): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let owner = scope_owner(&token, &s, user_str(&user).as_deref(), None, EMAIL_READ_SCOPES)?;

    let mut fwd: HashMap<String, String> = HashMap::new();
    fwd.insert(
        "folder".to_string(),
        q.get("folder").cloned().unwrap_or_else(|| "INBOX".to_string()),
    );
    if let Some(v) = q.get("account_id").filter(|a| !a.is_empty()) {
        fwd.insert("account_id".to_string(), v.clone());
    }
    // mark_seen: bool = False (the codex default differs from the route's True).
    let mark_seen = q
        .get("mark_seen")
        .map(|v| matches!(v.trim().to_lowercase().as_str(), "true" | "1" | "on" | "yes"))
        .unwrap_or(false);
    fwd.insert("mark_seen".to_string(), mark_seen.to_string());

    crate::routes::email_routes::read_email_by_uid(
        State(s),
        Some(Extension(CurrentUser(owner))),
        None,
        Path(uid),
        Query(fwd),
    )
    .await
}

/// `POST /api/codex/emails/draft` — save a draft as the scoped owner.
async fn email_draft(
    State(s): State<AppState>,
    token: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
    body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let owner = scope_owner(&token, &s, user_str(&user).as_deref(), None, EMAIL_DRAFT_SCOPES)?;
    crate::routes::email_routes::save_draft(State(s), Some(Extension(CurrentUser(owner))), None, body)
        .await
}

/// `POST /api/codex/emails/send` — send as the scoped owner.
async fn email_send(
    State(s): State<AppState>,
    token: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
    body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let owner = scope_owner(&token, &s, user_str(&user).as_deref(), None, EMAIL_SEND_SCOPES)?;
    crate::routes::email_routes::send_email(State(s), Some(Extension(CurrentUser(owner))), None, body)
        .await
}

// ===========================================================================
// /api/codex/memory — the MemoryManager on AppState (owner-scoped).
// ===========================================================================

/// `GET /api/codex/memory` — list the owner's memories.
async fn memory_list(
    State(s): State<AppState>,
    token: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
) -> Result<Json<Value>, HttpException> {
    let owner = scope_owner(
        &token,
        &s,
        user_str(&user).as_deref(),
        host_str(&ci).as_deref(),
        MEMORY_READ_SCOPES,
    )?;
    // memory_manager.load(owner=user) — the api_get_memory shape.
    Ok(Json(json!({ "memory": s.memory_manager.load(Some(&owner)) })))
}

/// `POST /api/codex/memory` — add a memory (text/category/source/session_id).
/// Mirrors `api_add_memory`'s owner-scoped add path.
async fn memory_add(
    State(s): State<AppState>,
    token: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    body: Option<Json<Value>>,
) -> Result<Json<Value>, HttpException> {
    let owner = scope_owner(
        &token,
        &s,
        user_str(&user).as_deref(),
        host_str(&ci).as_deref(),
        MEMORY_WRITE_SCOPES,
    )?;
    let obj = json_object(body);
    // MemoryAddRequest(text=..., category="fact", source="user", session_id=None)
    let text = obj.get("text").and_then(value_as_str).unwrap_or_default();
    let text = text.trim().to_string();
    // if not memory_data.text: raise HTTPException(400, "Empty memory text")
    if text.is_empty() {
        return Err(HttpException::new(400, "Empty memory text"));
    }
    let category = obj
        .get("category")
        .and_then(value_as_str)
        .filter(|c| !c.is_empty())
        .unwrap_or_else(|| "fact".to_string());
    let source = obj
        .get("source")
        .and_then(value_as_str)
        .filter(|c| !c.is_empty())
        .unwrap_or_else(|| "user".to_string());
    let session_id = obj.get("session_id").and_then(value_as_str).filter(|c| !c.is_empty());

    let owner_ref = Some(owner.as_str());
    let user_mem = s.memory_manager.load(owner_ref);
    // Dedup: if find_duplicates -> "Memory already exists".
    if !s.memory_manager.find_duplicates(&text, Some(&user_mem)).is_empty() {
        return Ok(Json(json!({
            "ok": true,
            "count": user_mem.len(),
            "message": "Memory already exists",
        })));
    }
    let mut new_entry = s
        .memory_manager
        .add_entry(&text, &source, &category, owner_ref)
        .map_err(|e| HttpException::new(500, e.to_string()))?;
    if let Some(session_id) = session_id {
        if let Some(o) = new_entry.as_object_mut() {
            o.insert("session_id".to_string(), Value::String(session_id));
        }
    }
    let mut all_mem = s.memory_manager.load_all();
    all_mem.push(new_entry.clone());
    s.memory_manager
        .save(&mut all_mem)
        .map_err(|e| HttpException::new(500, e.to_string()))?;
    // Sync the vector index if present + healthy (the route's add path).
    if let Some(mv) = s.memory_vector.as_ref() {
        if mv.healthy() {
            let id = new_entry.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string();
            mv.add(&id, &text)
                .await
                .map_err(|e| HttpException::new(500, e.to_string()))?;
        }
    }
    crate::src::event_bus::fire_event("memory_added", owner_ref);
    let count = all_mem
        .iter()
        .filter(|m| m.get("owner").and_then(|v| v.as_str()) == owner_ref)
        .count();
    Ok(Json(json!({ "ok": true, "count": count })))
}

/// `DELETE /api/codex/memory/{memory_id}` — delete an owned memory.
async fn memory_delete(
    State(s): State<AppState>,
    token: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Path(memory_id): Path<String>,
) -> Result<Json<Value>, HttpException> {
    let owner = scope_owner(
        &token,
        &s,
        user_str(&user).as_deref(),
        host_str(&ci).as_deref(),
        MEMORY_WRITE_SCOPES,
    )?;
    let owner_ref = Some(owner.as_str());
    let mut all_mem = s.memory_manager.load_all();
    // target = next((m for m in all_mem if m["id"] == memory_id), None)
    let target = all_mem
        .iter()
        .find(|m| m.get("id").and_then(|v| v.as_str()) == Some(memory_id.as_str()));
    let target = match target {
        Some(t) => t,
        None => {
            return Err(HttpException::new(
                404,
                format!("Memory item {memory_id} not found"),
            ))
        }
    };
    // _verify_memory_owner(target, user): `memory.get("owner") != user` -> 404
    // (a missing/null owner compares unequal to a real username, so legacy null-owner
    // data is NOT owned). Here `owner_ref` is always a real username (token owner /
    // required user), so a null-owner memory 404s exactly as the Python handler.
    if target.get("owner").and_then(|v| v.as_str()) != owner_ref {
        return Err(HttpException::new(404, "Memory not found"));
    }
    all_mem.retain(|m| m.get("id").and_then(|v| v.as_str()) != Some(memory_id.as_str()));
    s.memory_manager
        .save(&mut all_mem)
        .map_err(|e| HttpException::new(500, e.to_string()))?;
    if let Some(mv) = s.memory_vector.as_ref() {
        if mv.healthy() {
            mv.remove(&memory_id);
        }
    }
    Ok(Json(json!({ "ok": true, "message": "Memory deleted successfully" })))
}

// ===========================================================================
// /api/codex/calendar/events — the management_db calendar tool.
// ===========================================================================

/// `GET /api/codex/calendar/events?start=&end=&calendar=` -> `do_manage_calendar`.
async fn calendar_list(
    State(s): State<AppState>,
    token: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Json<Map<String, Value>>, HttpException> {
    let owner = scope_owner(
        &token,
        &s,
        user_str(&user).as_deref(),
        host_str(&ci).as_deref(),
        CALENDAR_READ_SCOPES,
    )?;
    let mut args = Map::new();
    args.insert("action".to_string(), Value::String("list_events".to_string()));
    // start / end are required query params (FastAPI 422 if missing).
    let start = q.get("start").cloned().unwrap_or_default();
    let end = q.get("end").cloned().unwrap_or_default();
    args.insert("start".to_string(), Value::String(start));
    args.insert("end".to_string(), Value::String(end));
    if let Some(cal) = q.get("calendar").filter(|c| !c.is_empty()) {
        args.insert("calendar".to_string(), Value::String(cal.clone()));
    }
    let body = Value::Object(args).to_string();
    Ok(Json(
        crate::src::tool_implementations::management_db::do_manage_calendar(&body, Some(&owner)).await,
    ))
}

/// `POST /api/codex/calendar/events` — create an event (EventCreate fields).
async fn calendar_create(
    State(s): State<AppState>,
    token: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    body: Option<Json<Value>>,
) -> Result<Json<Map<String, Value>>, HttpException> {
    let owner = scope_owner(
        &token,
        &s,
        user_str(&user).as_deref(),
        host_str(&ci).as_deref(),
        CALENDAR_WRITE_SCOPES,
    )?;
    // EventCreate fields (summary, dtstart, dtend, all_day, description, location,
    // calendar_href, rrule, color) map straight onto the tool's create_event args.
    let mut obj = json_object(body);
    obj.insert("action".to_string(), Value::String("create_event".to_string()));
    let body_str = Value::Object(obj).to_string();
    Ok(Json(
        crate::src::tool_implementations::management_db::do_manage_calendar(&body_str, Some(&owner))
            .await,
    ))
}

/// `DELETE /api/codex/calendar/events/{uid}` — delete an event by uid.
async fn calendar_delete(
    State(s): State<AppState>,
    token: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Path(uid): Path<String>,
) -> Result<Json<Map<String, Value>>, HttpException> {
    let owner = scope_owner(
        &token,
        &s,
        user_str(&user).as_deref(),
        host_str(&ci).as_deref(),
        CALENDAR_WRITE_SCOPES,
    )?;
    let body = json!({ "action": "delete_event", "uid": uid }).to_string();
    Ok(Json(
        crate::src::tool_implementations::management_db::do_manage_calendar(&body, Some(&owner)).await,
    ))
}

// ===========================================================================
// /api/codex/documents — the management_db documents tool (+ create handler).
// ===========================================================================

/// `GET /api/codex/documents` — the library listing. The owner-taking tool has no
/// `library` action, so this maps to its `list` action (the faithful owner-scoped
/// library listing). `search` / `language` / `limit` pass through.
async fn documents_library(
    State(s): State<AppState>,
    token: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Json<Map<String, Value>>, HttpException> {
    let owner = scope_owner(
        &token,
        &s,
        user_str(&user).as_deref(),
        host_str(&ci).as_deref(),
        DOCS_READ_SCOPES,
    )?;
    let mut args = Map::new();
    args.insert("action".to_string(), Value::String("list".to_string()));
    if let Some(v) = q.get("search").filter(|x| !x.is_empty()) {
        args.insert("search".to_string(), Value::String(v.clone()));
    }
    if let Some(v) = q.get("language").filter(|x| !x.is_empty()) {
        args.insert("language".to_string(), Value::String(v.clone()));
    }
    // limit: int = 50
    let limit = q.get("limit").and_then(|v| v.parse::<i64>().ok()).unwrap_or(50);
    args.insert("limit".to_string(), Value::from(limit));
    let body = Value::Object(args).to_string();
    Ok(Json(
        crate::src::tool_implementations::documents::do_manage_documents(&body, Some(&owner)).await,
    ))
}

/// `GET /api/codex/documents/{doc_id}` — read a document by id (tool `read`).
async fn documents_get(
    State(s): State<AppState>,
    token: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Path(doc_id): Path<String>,
) -> Result<Json<Map<String, Value>>, HttpException> {
    let owner = scope_owner(
        &token,
        &s,
        user_str(&user).as_deref(),
        host_str(&ci).as_deref(),
        DOCS_READ_SCOPES,
    )?;
    let body = json!({ "action": "read", "document_id": doc_id }).to_string();
    Ok(Json(
        crate::src::tool_implementations::documents::do_manage_documents(&body, Some(&owner)).await,
    ))
}

/// `POST /api/codex/documents` — create a document. The owner-taking tool has no
/// `create` action, so this delegates to the `create_document` route handler with the
/// scoped owner injected (the only owner-scoped create path).
async fn documents_create(
    State(s): State<AppState>,
    token: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
    body: Option<Json<Value>>,
) -> Result<Response, HttpException> {
    let owner = scope_owner(&token, &s, user_str(&user).as_deref(), None, DOCS_WRITE_SCOPES)?;
    // Build a DocumentCreate from the body (Invalid payload -> 400).
    let raw = match body {
        Some(Json(v)) => v,
        None => Value::Object(Map::new()),
    };
    let req: crate::routes::document_helpers::DocumentCreate = serde_json::from_value(raw)
        .map_err(|e| HttpException::new(400, format!("Invalid document payload: {e}")))?;
    crate::routes::document_routes::create_document(
        State(s),
        Some(Extension(CurrentUser(owner))),
        None,
        Json(req),
    )
    .await
}

/// `DELETE /api/codex/documents/{doc_id}` — delete a document by id (tool `delete`).
async fn documents_delete(
    State(s): State<AppState>,
    token: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Path(doc_id): Path<String>,
) -> Result<Json<Map<String, Value>>, HttpException> {
    let owner = scope_owner(
        &token,
        &s,
        user_str(&user).as_deref(),
        host_str(&ci).as_deref(),
        DOCS_WRITE_SCOPES,
    )?;
    let body = json!({ "action": "delete", "document_id": doc_id }).to_string();
    Ok(Json(
        crate::src::tool_implementations::documents::do_manage_documents(&body, Some(&owner)).await,
    ))
}

// ===========================================================================
// plugin.zip — runtime-zipped bundles served as attachments.
// ===========================================================================

/// The repo root (`Path(__file__).parent.parent` in the Python). `BASE_DIR` is the
/// parent of the rust crate (`CARGO_MANIFEST_DIR/..`), i.e. the repo root.
fn repo_root() -> std::path::PathBuf {
    std::path::PathBuf::from(crate::src::constants::BASE_DIR.as_str())
}

/// `GET /api/codex/plugin.zip` — zip `<repo>/integrations/codex` with arc names
/// prefixed `odysseus/`, as an attachment. 404 if the dir is missing.
async fn codex_plugin_zip(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
) -> Result<Response, HttpException> {
    // require_user(request)
    crate::routes::auth_adapter::require_user(
        user_str(&user).as_deref(),
        &s,
        host_str(&ci).as_deref(),
    )?;
    let root = repo_root().join("integrations").join("codex");
    if !root.exists() {
        return Err(HttpException::new(404, "Codex plugin bundle not found"));
    }
    let bytes = build_zip(&root, &root, Some("odysseus"))?;
    zip_response(bytes, "odysseus-codex-plugin.zip")
}

/// `GET /api/claude/plugin.zip` — zip ONLY `<repo>/integrations/claude/skills`, arc
/// names relative to `integrations/claude` (so the leading `skills/` is kept), as an
/// attachment. 404 if the dir is missing.
async fn claude_plugin_zip(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
) -> Result<Response, HttpException> {
    crate::routes::auth_adapter::require_user(
        user_str(&user).as_deref(),
        &s,
        host_str(&ci).as_deref(),
    )?;
    let skills_root = repo_root()
        .join("integrations")
        .join("claude")
        .join("skills");
    if !skills_root.exists() {
        return Err(HttpException::new(404, "Claude skill bundle not found"));
    }
    // bundle_root = skills_root.parent — arc names relative to integrations/claude.
    let bundle_root = skills_root.parent().unwrap_or(&skills_root).to_path_buf();
    let bytes = build_zip(&skills_root, &bundle_root, None)?;
    zip_response(bytes, "odysseus-claude-skill.zip")
}

/// Walk `walk_root` recursively (sorted), skipping dirs / `__pycache__` parts /
/// `.pyc`, and write each file into a deflated zip under
/// `[prefix/]<path relative to arc_base>`. Mirrors the Python `rglob("*")` loop.
fn build_zip(
    walk_root: &std::path::Path,
    arc_base: &std::path::Path,
    prefix: Option<&str>,
) -> Result<Vec<u8>, HttpException> {
    use std::io::Write;

    // sorted(root.rglob("*")) — collect every path then sort lexicographically.
    let mut files: Vec<std::path::PathBuf> = Vec::new();
    collect_files(walk_root, &mut files);
    files.sort();

    let mut cursor = std::io::Cursor::new(Vec::<u8>::new());
    {
        let mut zf = zip::ZipWriter::new(&mut cursor);
        let opts = zip::write::SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Deflated);
        for path in &files {
            // Skip `__pycache__` parts / `.pyc` (dirs are already excluded by collect).
            if path
                .components()
                .any(|c| c.as_os_str() == "__pycache__")
            {
                continue;
            }
            if path.extension().and_then(|e| e.to_str()) == Some("pyc") {
                continue;
            }
            let rel = match path.strip_prefix(arc_base) {
                Ok(r) => r,
                Err(_) => continue,
            };
            let arcname = match prefix {
                Some(p) => format!("{p}/{}", rel.to_string_lossy()),
                None => rel.to_string_lossy().to_string(),
            };
            // Forward-slash separators (zip convention) — to_string_lossy already uses
            // `/` on unix; normalize defensively for any backslash on other platforms.
            let arcname = arcname.replace('\\', "/");
            let bytes = match std::fs::read(path) {
                Ok(b) => b,
                Err(_) => continue,
            };
            if zf.start_file(&arcname, opts).is_err() {
                continue;
            }
            let _ = zf.write_all(&bytes);
        }
        zf.finish()
            .map_err(|e| HttpException::new(500, format!("Zip error: {e}")))?;
    }
    Ok(cursor.into_inner())
}

/// Recursively collect FILE paths under `dir` (dirs themselves are not pushed,
/// matching the Python `if path.is_dir(): continue`).
fn collect_files(dir: &std::path::Path, out: &mut Vec<std::path::PathBuf>) {
    let rd = match std::fs::read_dir(dir) {
        Ok(rd) => rd,
        Err(_) => return,
    };
    for entry in rd.flatten() {
        let p = entry.path();
        if p.is_dir() {
            collect_files(&p, out);
        } else {
            out.push(p);
        }
    }
}

/// Build the `application/zip` attachment response.
fn zip_response(bytes: Vec<u8>, filename: &str) -> Result<Response, HttpException> {
    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "application/zip")
        .header(
            header::CONTENT_DISPOSITION,
            format!("attachment; filename=\"{filename}\""),
        )
        .body(Body::from(bytes))
        .map_err(|e| HttpException::new(500, e.to_string()))
}

// ===========================================================================
// Small body/value helpers.
// ===========================================================================

/// `body: dict[str, Any] = Body(default_factory=dict)` — an absent / non-object body
/// becomes an empty map (the FastAPI default-factory semantics).
fn json_object(body: Option<Json<Value>>) -> Map<String, Value> {
    match body {
        Some(Json(Value::Object(m))) => m,
        _ => Map::new(),
    }
}

/// `str(v)` for the JSON string case (a non-string value yields `None`, matching the
/// Python `body.get("action")` then `str(... or default)` where a dict/list/None all
/// fall through to the default).
fn value_as_str(v: &Value) -> Option<String> {
    v.as_str().map(|s| s.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tok(owner: Option<&str>, scopes: &[&str]) -> ApiToken {
        ApiToken {
            present: true,
            id: None,
            owner: owner.map(|o| o.to_string()),
            scopes: scopes.iter().map(|s| s.to_string()).collect(),
        }
    }

    /// `_scope_owner` 403 when an API token lacks the required scope — sorted + joined.
    #[test]
    fn scope_owner_token_missing_scope_403() {
        let t = tok(Some("alice"), &["chat"]);
        let err = scope_owner_from_token(&t, EMAIL_SEND_SCOPES).unwrap_err();
        assert_eq!(err.status_code, 403);
        // EMAIL_SEND_SCOPES = {"email:send"} -> sorted+joined = "email:send".
        assert_eq!(err.detail, "API token missing required scope: email:send");
    }

    /// `_scope_owner` joins multiple required scopes with `" or "` (sorted).
    #[test]
    fn scope_owner_token_missing_scope_sorted_join() {
        let t = tok(Some("alice"), &["chat"]);
        let err = scope_owner_from_token(&t, EMAIL_READ_SCOPES).unwrap_err();
        // EMAIL_READ_SCOPES = {"email:read","email:draft","email:send"} sorted:
        // ["email:draft","email:read","email:send"].
        assert_eq!(
            err.detail,
            "API token missing required scope: email:draft or email:read or email:send"
        );
    }

    /// `_scope_owner` returns the token owner when scopes intersect.
    #[test]
    fn scope_owner_token_with_scope_returns_owner() {
        let t = tok(Some("bob"), &["email:send"]);
        let owner = scope_owner_from_token(&t, EMAIL_SEND_SCOPES).unwrap();
        assert_eq!(owner, "bob");
    }

    /// A WRITE-only scope still satisfies the READ set (the READ set includes WRITE).
    #[test]
    fn scope_owner_write_scope_satisfies_read_set() {
        let t = tok(Some("carol"), &["calendar:write"]);
        assert_eq!(
            scope_owner_from_token(&t, CALENDAR_READ_SCOPES).unwrap(),
            "carol"
        );
    }

    /// `_scope_owner` 403 "no owner" when a scoped token carries no / empty owner.
    #[test]
    fn scope_owner_token_no_owner_403() {
        let t = tok(None, &["email:send"]);
        let err = scope_owner_from_token(&t, EMAIL_SEND_SCOPES).unwrap_err();
        assert_eq!(err.status_code, 403);
        assert_eq!(err.detail, "API token has no owner");

        let t2 = tok(Some(""), &["email:send"]);
        let err2 = scope_owner_from_token(&t2, EMAIL_SEND_SCOPES).unwrap_err();
        assert_eq!(err2.detail, "API token has no owner");
    }

    /// `capabilities` with no token: every `scoped(...)` is `true`, token_scopes empty.
    #[tokio::test]
    async fn capabilities_no_token_all_true() {
        let Json(v) = capabilities(None).await;
        assert_eq!(v["integration"], "codex");
        assert_eq!(v["token_scopes"], serde_json::json!([]));
        assert_eq!(v["tools"]["todos"]["read"], true);
        assert_eq!(v["tools"]["todos"]["write"], true);
        assert_eq!(v["tools"]["email"]["send"], true);
        assert_eq!(v["tools"]["memory"]["available"], true);
        assert_eq!(v["tools"]["documents"]["actions"], serde_json::json!(["library", "read", "create", "delete"]));
        assert_eq!(v["safety"]["email_send_requires_confirmation"], true);
    }

    /// `capabilities` with a partial token: only the matching scopes are `true`, and
    /// `token_scopes` is sorted.
    #[tokio::test]
    async fn capabilities_partial_token_scoped() {
        let token = Some(Extension(ApiToken {
            present: true,
            id: None,
            owner: Some("alice".to_string()),
            scopes: vec!["todos:read".to_string(), "calendar:write".to_string()],
        }));
        let Json(v) = capabilities(token).await;
        assert_eq!(v["token_scopes"], serde_json::json!(["calendar:write", "todos:read"]));
        assert_eq!(v["tools"]["todos"]["read"], true);
        assert_eq!(v["tools"]["todos"]["write"], false); // needs todos:write
        assert_eq!(v["tools"]["calendar"]["read"], true); // calendar:write ∈ READ set
        assert_eq!(v["tools"]["calendar"]["write"], true);
        assert_eq!(v["tools"]["email"]["read"], false);
        assert_eq!(v["tools"]["documents"]["read"], false);
    }
}
