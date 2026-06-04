// routes/mcp_routes.rs  <- routes/mcp_routes.py
//! MCP (Model Context Protocol) server management routes — `/api/mcp/*`
//! (routes WAVE 7).
//!
//! Faithful translation of `routes/mcp_routes.py`'s `setup_mcp_routes(mcp_manager)`
//! factory. Every handler is `require_admin`-gated and performs raw `rusqlite`
//! CRUD on the `mcp_servers` table (mirroring the SQLAlchemy `McpServer` ORM),
//! delegating connect/disconnect/status/tool-discovery to the already-ported
//! [`crate::src::mcp_manager::McpManager`] reached through `AppState.mcp_manager`
//! (an `Arc<McpManager>`).
//!
//! ## Shape (the integration substrate)
//! * `setup_mcp_routes() -> Router<AppState>` mirrors the Python factory, whose
//!   captured `mcp_manager` arg is reached via `State<AppState>.mcp_manager`
//!   (mirroring `setup_research_routes` / `setup_model_routes`, which likewise read
//!   their captured handlers from `AppState`). The Python
//!   `APIRouter(prefix="/api/mcp", tags=["mcp"])` gives the absolute paths used by
//!   the `.route(...)` calls verbatim (axum 0.7 `:server_id`).
//! * Every handler calls `require_admin(request)` FIRST, before any work, matching
//!   FastAPI's `require_admin(request)` at the top of each handler. The gate reads
//!   the `X-Odysseus-Internal-Token` header + the stamped `current_user` from
//!   `Option<Extension<CurrentUser>>`, delegating to the foundation
//!   [`auth_adapter::require_admin`]. `Err` -> `HttpException(403, "Admin only")`.
//! * `raise HTTPException(s, d)` -> `return Err(HttpException::new(s, d))`; the
//!   `web`-gated `IntoResponse for HttpException` renders FastAPI's `{"detail": d}`.
//! * `name: str = Form(...)` / `transport: str = Form("stdio")` / etc. -> a
//!   `multipart/form-data` (or urlencoded) text field collected with the same
//!   manual collector the sibling `api_token_routes` / `compare_routes` ports use.
//!   Absent-with-default fields fall back to their Python defaults (`""`/`None`).
//!
//! ## JSON column round-trips
//! The `mcp_servers` row stores `args` / `env` / `oauth_config` / `disabled_tools`
//! as JSON-string TEXT columns. The list/CRUD handlers decode them exactly as
//! Python does (`json.loads(col) if col else []`/`{}`/`None`), preserving the
//! `(json.JSONDecodeError | TypeError) -> default` swallowing in `_load_disabled_map`.
//!
//! ## OAuth flow (ported, external-flow caveat)
//! The `/oauth/{authorize,callback,exchange}` trio is ported faithfully:
//!   - `oauth_authorize` is purely local (read the keys file, build the Google
//!     consent URL, redirect-or-paste-back HTML) — fully testable.
//!   - `oauth_callback` / `oauth_exchange` perform the external Google token
//!     exchange via `reqwest` (the `httpx.AsyncClient().post(...)` analogue), then
//!     persist the tokens and connect the server. The external redirect/consent
//!     flow itself is untestable end-to-end (it needs real Google credentials and
//!     a live authorization code), but the code path reproduces Python's behavior
//!     verbatim — including every error branch — so nothing is faked.
//!
//! ## No path collision
//! `/api/mcp/*` is a fresh prefix the inline `web/mod.rs` subset never touches, so
//! the aggregator merges this router without an axum duplicate-`method`+`path`
//! panic. Purely additive (WAVE 7) — no reconciliation.


use std::collections::{HashMap, HashSet};

use axum::extract::{Multipart, Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{Html, IntoResponse, Redirect, Response};
use axum::routing::{get, patch, post};
use axum::{Extension, Json, Router};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::core::middleware::INTERNAL_TOOL_HEADER;
use crate::pylog as logger;
use crate::routes::auth_adapter;
use crate::routes::{AppState, CurrentUser, HttpException};

/// `setup_mcp_routes()` — assemble the MCP-management router.
///
/// app.py registers this as include-router #34. The Python
/// `APIRouter(prefix="/api/mcp", tags=["mcp"])` registers, in this order:
/// `GET /servers`, `POST /servers`, `POST /servers/{server_id}/reconnect`,
/// `PATCH /servers/{server_id}`, `DELETE /servers/{server_id}`, `GET /tools`,
/// `GET /servers/{server_id}/tools`, `PATCH /servers/{server_id}/tools`,
/// `GET /oauth/authorize/{server_id}`, `GET /oauth/callback`,
/// `POST /oauth/exchange/{server_id}`. Under the `/api/mcp` prefix those resolve to
/// the absolute paths below. The shared-path verbs are grouped on one `.route(...)`
/// call (axum requires a single registration per path).
pub fn setup_mcp_routes() -> Router<AppState> {
    Router::new()
        // `@router.get("/servers")` + `@router.post("/servers")`.
        .route("/api/mcp/servers", get(list_servers).post(add_server))
        // `@router.post("/servers/{server_id}/reconnect")`.
        .route(
            "/api/mcp/servers/:server_id/reconnect",
            post(reconnect_server),
        )
        // `@router.patch("/servers/{server_id}")` + `@router.delete(...)`.
        .route(
            "/api/mcp/servers/:server_id",
            patch(toggle_server).delete(delete_server),
        )
        // `@router.get("/tools")`.
        .route("/api/mcp/tools", get(list_tools))
        // `@router.get("/servers/{server_id}/tools")` + `@router.patch(...)`.
        .route(
            "/api/mcp/servers/:server_id/tools",
            get(list_server_tools).patch(update_disabled_tools),
        )
        // `@router.get("/oauth/authorize/{server_id}")`.
        .route("/api/mcp/oauth/authorize/:server_id", get(oauth_authorize))
        // `@router.get("/oauth/callback")`.
        .route("/api/mcp/oauth/callback", get(oauth_callback))
        // `@router.post("/oauth/exchange/{server_id}")`.
        .route("/api/mcp/oauth/exchange/:server_id", post(oauth_exchange))
}

// ---------------------------------------------------------------------------
// A snapshot of one `mcp_servers` row (the columns every handler reads).
// ---------------------------------------------------------------------------

/// One `mcp_servers` row, snapshotted out of the (non-`Send`-across-await)
/// `rusqlite::Connection` so the connection is dropped before any `.await`.
struct ServerRow {
    id: String,
    name: String,
    transport: String,
    command: Option<String>,
    args: Option<String>,
    env: Option<String>,
    url: Option<String>,
    is_enabled: bool,
    oauth_config: Option<String>,
    disabled_tools: Option<String>,
}

const SELECT_COLS: &str = "id, name, transport, command, args, env, url, is_enabled, \
                           oauth_config, disabled_tools";

fn map_server_row(r: &rusqlite::Row<'_>) -> rusqlite::Result<ServerRow> {
    Ok(ServerRow {
        id: r.get(0)?,
        name: r.get(1)?,
        transport: r.get(2)?,
        command: r.get(3)?,
        args: r.get(4)?,
        env: r.get(5)?,
        url: r.get(6)?,
        is_enabled: r.get(7)?,
        oauth_config: r.get(8)?,
        disabled_tools: r.get(9)?,
    })
}

/// `db.query(McpServer).filter(McpServer.id == server_id).first()` — fetch one row.
fn fetch_server(conn: &rusqlite::Connection, server_id: &str) -> Result<Option<ServerRow>, HttpException> {
    let mut stmt = conn
        .prepare(&format!("SELECT {SELECT_COLS} FROM mcp_servers WHERE id = ?1"))
        .map_err(db_500)?;
    let mut rows = stmt
        .query_map(rusqlite::params![server_id], map_server_row)
        .map_err(db_500)?;
    match rows.next() {
        Some(r) => Ok(Some(r.map_err(db_500)?)),
        None => Ok(None),
    }
}

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

/// `GET /api/mcp/servers` — list all configured MCP servers with connection status.
///
/// ```python
/// require_admin(request)
/// servers = db.query(McpServer).all()
/// result = []
/// for srv in servers:
///     status = mcp_manager.get_server_status(srv.id)
///     oauth_cfg = json.loads(srv.oauth_config) if srv.oauth_config else None
///     needs_oauth = False
///     if oauth_cfg:
///         token_file = os.path.expanduser(oauth_cfg.get("token_file", ""))
///         needs_oauth = token_file and not os.path.exists(token_file)
///     disabled_list = json.loads(srv.disabled_tools) if srv.disabled_tools else []
///     total_tools = status.get("tool_count", 0)
///     result.append({ ... })
/// return result
/// ```
async fn list_servers(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;

    // `servers = db.query(McpServer).all()` — no explicit ordering in the Python.
    let servers: Vec<ServerRow> = {
        let conn = session_local()?;
        let mut stmt = conn
            .prepare(&format!("SELECT {SELECT_COLS} FROM mcp_servers"))
            .map_err(db_500)?;
        let rows = stmt
            .query_map([], map_server_row)
            .map_err(db_500)?
            .collect::<rusqlite::Result<Vec<_>>>()
            .map_err(db_500)?;
        rows
    };

    let mut result: Vec<Value> = Vec::with_capacity(servers.len());
    for srv in &servers {
        let status = state.mcp_manager.get_server_status(&srv.id);
        // `oauth_cfg = json.loads(srv.oauth_config) if srv.oauth_config else None`.
        let oauth_cfg: Option<Value> = parse_json_opt(srv.oauth_config.as_deref());
        // `needs_oauth = False; if oauth_cfg: token_file = expanduser(...); needs_oauth = token_file and not exists(token_file)`.
        let needs_oauth = match &oauth_cfg {
            Some(cfg) => {
                let token_file = expanduser(cfg.get("token_file").and_then(|v| v.as_str()).unwrap_or(""));
                // `token_file and not os.path.exists(token_file)` — empty -> False.
                !token_file.is_empty() && !path_exists(&token_file)
            }
            None => false,
        };
        // `disabled_list = json.loads(srv.disabled_tools) if srv.disabled_tools else []`.
        let disabled_list = parse_json_array(srv.disabled_tools.as_deref());
        // `total_tools = status.get("tool_count", 0)`.
        let total_tools = status.get("tool_count").and_then(|v| v.as_i64()).unwrap_or(0);
        let disabled_count = disabled_list.len() as i64;

        result.push(json!({
            "id": srv.id,
            "name": srv.name,
            "transport": srv.transport,
            "command": srv.command,
            // `json.loads(srv.args) if srv.args else []` / `... else {}`.
            "args": parse_json_or(srv.args.as_deref(), json!([])),
            "env": parse_json_or(srv.env.as_deref(), json!({})),
            "url": srv.url,
            "is_enabled": srv.is_enabled,
            "status": status.get("status").cloned().unwrap_or_else(|| json!("disconnected")),
            "tool_count": total_tools,
            "disabled_tool_count": disabled_count,
            // `max(0, total_tools - len(disabled_list))`.
            "enabled_tool_count": (total_tools - disabled_count).max(0),
            "error": status.get("error").cloned().unwrap_or(Value::Null),
            "has_oauth": oauth_cfg.is_some(),
            "needs_oauth": needs_oauth,
        }));
    }
    Ok(Json(Value::Array(result)).into_response())
}

/// `POST /api/mcp/servers` — add a new MCP server config and attempt connection.
///
/// Admin-only: registering a stdio server is equivalent to executing arbitrary
/// binaries on the host. Validates the transport-specific required field, parses
/// the JSON form fields (swallowing decode errors to defaults, matching Python),
/// optionally writes a Google OAuth credentials file, persists the row, then
/// (unless an OAuth token is still required) attempts the connection.
async fn add_server(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    mp: Multipart,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;

    let form = parse_form(mp).await;
    // `name: str = Form(...)` (required), the rest with Python defaults.
    let name = form.get("name").cloned().unwrap_or_default();
    // `transport: str = Form("stdio")`.
    let transport = form
        .get("transport")
        .cloned()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "stdio".to_string());
    // `command: str = Form(None)` — absent -> None.
    let command: Option<String> = form.get("command").cloned();
    // `args: str = Form("[]")`, `env: str = Form("{}")`.
    let args_raw = form.get("args").cloned().unwrap_or_else(|| "[]".to_string());
    let env_raw = form.get("env").cloned().unwrap_or_else(|| "{}".to_string());
    // `url: str = Form(None)`.
    let url: Option<String> = form.get("url").cloned();
    let oauth_file: Option<String> = form.get("oauth_file").cloned();
    let oauth_config: Option<String> = form.get("oauth_config").cloned();

    // `server_id = str(uuid.uuid4())[:8]`.
    let server_id: String = uuid::Uuid::new_v4().to_string().chars().take(8).collect();

    // Validate.
    // `if transport == "stdio" and not command: raise HTTPException(400, ...)`.
    if transport == "stdio" && command.as_deref().unwrap_or("").is_empty() {
        return Err(HttpException::new(400, "command is required for stdio transport"));
    }
    // `if transport == "sse" and not url: raise HTTPException(400, ...)`.
    if transport == "sse" && url.as_deref().unwrap_or("").is_empty() {
        return Err(HttpException::new(400, "url is required for SSE transport"));
    }

    // Parse JSON fields — `json.loads(args) if args else []`, decode error -> [].
    let parsed_args: Value = if args_raw.is_empty() {
        json!([])
    } else {
        serde_json::from_str(&args_raw).unwrap_or_else(|_| json!([]))
    };
    let mut parsed_env: Value = if env_raw.is_empty() {
        json!({})
    } else {
        serde_json::from_str(&env_raw).unwrap_or_else(|_| json!({}))
    };

    // Parse OAuth config — `json.loads(oauth_config)` swallowing decode errors.
    let parsed_oauth_config: Option<Value> = match oauth_config.as_deref() {
        Some(s) if !s.is_empty() => serde_json::from_str(s).ok(),
        _ => None,
    };

    // Write OAuth credentials file if provided (for Google MCP servers).
    logger::info(&format!("MCP add_server: oauth_file={}", py_repr_opt(&oauth_file)));
    if let Some(of) = oauth_file.as_deref().filter(|s| !s.is_empty()) {
        write_oauth_file(of, &mut parsed_env);
    }

    // Save to DB.
    {
        let conn = session_local()?;
        // TimestampMixin: created_at/updated_at default to utcnow at flush.
        let now = crate::pydatetime::utcnow_naive_iso();
        // `oauth_config=json.dumps(parsed_oauth_config) if parsed_oauth_config else None`.
        let oauth_config_stored: Option<String> =
            parsed_oauth_config.as_ref().map(|v| v.to_string());
        conn.execute(
            "INSERT INTO mcp_servers \
               (id, name, transport, command, args, env, url, is_enabled, oauth_config, \
                disabled_tools, created_at, updated_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, 1, ?8, NULL, ?9, ?9)",
            rusqlite::params![
                server_id,
                name,
                transport,
                command,
                parsed_args.to_string(),
                parsed_env.to_string(),
                url,
                oauth_config_stored,
                now,
            ],
        )
        .map_err(db_500)?;
    }

    // Check if OAuth token already exists — skip connection attempt if not.
    let mut needs_oauth = false;
    if let Some(cfg) = &parsed_oauth_config {
        let token_file = expanduser(cfg.get("token_file").and_then(|v| v.as_str()).unwrap_or(""));
        if !token_file.is_empty() && !path_exists(&token_file) {
            needs_oauth = true;
        }
    }

    let mut connected = false;
    if !needs_oauth {
        // `connected = await mcp_manager.connect_server(...)`.
        connected = state
            .mcp_manager
            .connect_server(
                &server_id,
                &name,
                &transport,
                command.as_deref(),
                json_to_string_vec(&parsed_args),
                json_to_string_map(&parsed_env),
                url.as_deref(),
            )
            .await;
    }

    let status = state.mcp_manager.get_server_status(&server_id);
    Ok(Json(json!({
        "id": server_id,
        "name": name,
        "connected": connected,
        "status": if needs_oauth {
            json!("needs_oauth")
        } else {
            status.get("status").cloned().unwrap_or_else(|| json!("disconnected"))
        },
        "tool_count": status.get("tool_count").cloned().unwrap_or_else(|| json!(0)),
        "error": if needs_oauth {
            json!("OAuth authorization required")
        } else {
            status.get("error").cloned().unwrap_or(Value::Null)
        },
        "needs_oauth": needs_oauth,
    }))
    .into_response())
}

/// `POST /api/mcp/servers/{server_id}/reconnect` — reconnect to an MCP server.
async fn reconnect_server(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Path(server_id): Path<String>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;

    // `srv = db.query(McpServer).filter(...).first(); if not srv: 404`.
    let srv = {
        let conn = session_local()?;
        fetch_server(&conn, &server_id)?
    };
    let srv = srv.ok_or_else(|| HttpException::new(404, "Server not found"))?;

    // `await mcp_manager.disconnect_server(server_id)`.
    state.mcp_manager.disconnect_server(&server_id).await;

    let args = parse_json_array(srv.args.as_deref());
    let env = parse_json_object(srv.env.as_deref());
    let connected = state
        .mcp_manager
        .connect_server(
            &server_id,
            &srv.name,
            &srv.transport,
            srv.command.as_deref(),
            value_array_to_string_vec(&args),
            value_object_to_string_map(&env),
            srv.url.as_deref(),
        )
        .await;

    let status = state.mcp_manager.get_server_status(&server_id);
    Ok(Json(json!({
        "connected": connected,
        "status": status.get("status").cloned().unwrap_or_else(|| json!("disconnected")),
        "tool_count": status.get("tool_count").cloned().unwrap_or_else(|| json!(0)),
        "error": status.get("error").cloned().unwrap_or(Value::Null),
    }))
    .into_response())
}

/// `PATCH /api/mcp/servers/{server_id}` — enable or disable an MCP server.
///
/// `is_enabled: str = Form(...)`; `enabled = str(is_enabled).lower() == "true"`.
async fn toggle_server(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Path(server_id): Path<String>,
    mp: Multipart,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;

    let form = parse_form(mp).await;
    // `is_enabled: str = Form(...)` — required.
    let is_enabled_raw = form.get("is_enabled").cloned().unwrap_or_default();

    let srv = {
        let conn = session_local()?;
        let srv = fetch_server(&conn, &server_id)?
            .ok_or_else(|| HttpException::new(404, "Server not found"))?;

        // `enabled = str(is_enabled).lower() == "true"`.
        let enabled = is_enabled_raw.to_lowercase() == "true";
        // `srv.is_enabled = enabled; db.commit()` (TimestampMixin bumps updated_at).
        conn.execute(
            "UPDATE mcp_servers SET is_enabled = ?1, updated_at = ?2 WHERE id = ?3",
            rusqlite::params![enabled, crate::pydatetime::utcnow_naive_iso(), server_id],
        )
        .map_err(db_500)?;
        (srv, enabled)
    };
    let (srv, enabled) = srv;

    if enabled {
        let args = parse_json_array(srv.args.as_deref());
        let env = parse_json_object(srv.env.as_deref());
        state
            .mcp_manager
            .connect_server(
                &server_id,
                &srv.name,
                &srv.transport,
                srv.command.as_deref(),
                value_array_to_string_vec(&args),
                value_object_to_string_map(&env),
                srv.url.as_deref(),
            )
            .await;
    } else {
        state.mcp_manager.disconnect_server(&server_id).await;
    }

    Ok(Json(json!({"id": server_id, "is_enabled": enabled})).into_response())
}

/// `DELETE /api/mcp/servers/{server_id}` — remove an MCP server.
async fn delete_server(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Path(server_id): Path<String>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;

    // `srv = ...first(); if not srv: raise HTTPException(404, "Server not found")`.
    {
        let conn = session_local()?;
        if fetch_server(&conn, &server_id)?.is_none() {
            return Err(HttpException::new(404, "Server not found"));
        }
    }

    // `await mcp_manager.disconnect_server(server_id)`.
    state.mcp_manager.disconnect_server(&server_id).await;

    // `db.delete(srv); db.commit()`.
    {
        let conn = session_local()?;
        conn.execute(
            "DELETE FROM mcp_servers WHERE id = ?1",
            rusqlite::params![server_id],
        )
        .map_err(db_500)?;
    }

    Ok(Json(json!({"status": "deleted"})).into_response())
}

/// `GET /api/mcp/tools` — list all discovered MCP tools across all connected servers.
///
/// ```python
/// require_admin(request)
/// disabled_map = _load_disabled_map()
/// return mcp_manager.get_all_tools(disabled_map)
/// ```
async fn list_tools(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    let disabled_map = load_disabled_map()?;
    let tools = state.mcp_manager.get_all_tools(Some(&disabled_map));
    Ok(Json(Value::Array(tools)).into_response())
}

/// `GET /api/mcp/servers/{server_id}/tools` — list all tools for a specific MCP
/// server with enabled/disabled state.
///
/// ```python
/// srv = ...first(); if not srv: 404
/// disabled_list = json.loads(srv.disabled_tools) if srv.disabled_tools else []
/// disabled_set = set(disabled_list)
/// all_tools = mcp_manager.get_all_tools()
/// server_tools = [t for t in all_tools if t["server_id"] == server_id]
/// for t in server_tools: t["is_disabled"] = t["name"] in disabled_set
/// return server_tools
/// ```
async fn list_server_tools(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Path(server_id): Path<String>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;

    let disabled_set: HashSet<String> = {
        let conn = session_local()?;
        let srv = fetch_server(&conn, &server_id)?
            .ok_or_else(|| HttpException::new(404, "Server not found"))?;
        parse_json_array(srv.disabled_tools.as_deref())
            .into_iter()
            .filter_map(|v| v.as_str().map(str::to_string))
            .collect()
    };

    // `all_tools = mcp_manager.get_all_tools()` (no disabled_map -> every is_disabled=False).
    let all_tools = state.mcp_manager.get_all_tools(None);
    // `server_tools = [t for t in all_tools if t["server_id"] == server_id]`.
    let mut server_tools: Vec<Value> = all_tools
        .into_iter()
        .filter(|t| t.get("server_id").and_then(|v| v.as_str()) == Some(server_id.as_str()))
        .collect();
    // `for t in server_tools: t["is_disabled"] = t["name"] in disabled_set`.
    for t in &mut server_tools {
        let name = t.get("name").and_then(|v| v.as_str()).unwrap_or("").to_string();
        if let Some(obj) = t.as_object_mut() {
            obj.insert("is_disabled".to_string(), json!(disabled_set.contains(&name)));
        }
    }
    Ok(Json(Value::Array(server_tools)).into_response())
}

/// `PATCH /api/mcp/servers/{server_id}/tools` — bulk update disabled tools list.
///
/// Expects JSON body: `{"disabled": ["tool_name_1", "tool_name_2"]}`.
///
/// ```python
/// srv = ...first(); if not srv: 404
/// body = await request.json()
/// disabled = body.get("disabled", [])
/// if not isinstance(disabled, list): raise HTTPException(400, ...)
/// srv.disabled_tools = json.dumps(disabled) if disabled else None
/// db.commit()
/// return {"id": server_id, "disabled_count": len(disabled)}
/// ```
async fn update_disabled_tools(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Path(server_id): Path<String>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;

    {
        let conn = session_local()?;
        // `srv = ...first(); if not srv: 404` — fetched before reading the body, as
        // in the Python (the body is awaited inside the `if not srv` guard's scope).
        if fetch_server(&conn, &server_id)?.is_none() {
            return Err(HttpException::new(404, "Server not found"));
        }
    }

    // `body = await request.json()` — FastAPI raises on invalid JSON.
    let body: Value = serde_json::from_slice(&raw_body)
        .map_err(|_| HttpException::new(400, "Invalid JSON body"))?;
    // `disabled = body.get("disabled", [])`.
    let disabled = body.get("disabled").cloned().unwrap_or_else(|| json!([]));
    // `if not isinstance(disabled, list): raise HTTPException(400, ...)`.
    let disabled_arr = match disabled.as_array() {
        Some(a) => a.clone(),
        None => return Err(HttpException::new(400, "disabled must be a list of tool names")),
    };

    // `srv.disabled_tools = json.dumps(disabled) if disabled else None`.
    let stored: Option<String> = if disabled_arr.is_empty() {
        None
    } else {
        Some(Value::Array(disabled_arr.clone()).to_string())
    };
    {
        let conn = session_local()?;
        conn.execute(
            "UPDATE mcp_servers SET disabled_tools = ?1, updated_at = ?2 WHERE id = ?3",
            rusqlite::params![stored, crate::pydatetime::utcnow_naive_iso(), server_id],
        )
        .map_err(db_500)?;
    }

    Ok(Json(json!({"id": server_id, "disabled_count": disabled_arr.len()})).into_response())
}

// ── OAuth flow for Google MCP servers ──────────────────────────

/// `GET /api/mcp/oauth/authorize/{server_id}` — show OAuth authorization page with
/// Google sign-in link (purely local: read keys file, build the consent URL).
async fn oauth_authorize(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Path(server_id): Path<String>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;

    let srv = {
        let conn = session_local()?;
        fetch_server(&conn, &server_id)?
    };
    // `if not srv: raise HTTPException(404, "Server not found")`.
    let srv = srv.ok_or_else(|| HttpException::new(404, "Server not found"))?;
    // `if not srv.oauth_config: raise HTTPException(400, "Server has no OAuth config")`.
    let oauth_config = srv
        .oauth_config
        .as_deref()
        .filter(|s| !s.is_empty())
        .ok_or_else(|| HttpException::new(400, "Server has no OAuth config"))?;

    // `oauth_cfg = json.loads(srv.oauth_config)` — FastAPI would 500 on malformed JSON.
    let oauth_cfg: Value =
        serde_json::from_str(oauth_config).map_err(|_| HttpException::new(500, "Internal Server Error"))?;
    let keys_file = expanduser(oauth_cfg.get("keys_file").and_then(|v| v.as_str()).unwrap_or(""));
    // `if not keys_file or not os.path.exists(keys_file): 400`.
    if keys_file.is_empty() || !path_exists(&keys_file) {
        return Err(HttpException::new(400, "OAuth keys file not found"));
    }

    // `with open(keys_file) as f: keys_data = json.load(f)`.
    let keys_data: Value = match std::fs::read_to_string(&keys_file) {
        Ok(s) => serde_json::from_str(&s)
            .map_err(|_| HttpException::new(500, "Internal Server Error"))?,
        Err(_) => return Err(HttpException::new(500, "Internal Server Error")),
    };
    // `keys = keys_data.get("installed") or keys_data.get("web")`.
    let keys = keys_data
        .get("installed")
        .filter(|v| !v.is_null())
        .or_else(|| keys_data.get("web"));
    // `if not keys: raise HTTPException(400, "Invalid OAuth keys file format")`.
    let keys = match keys {
        Some(k) if !k.is_null() => k,
        _ => return Err(HttpException::new(400, "Invalid OAuth keys file format")),
    };

    // `client_id = keys["client_id"]` — KeyError -> 500.
    let client_id = match keys.get("client_id").and_then(|v| v.as_str()) {
        Some(c) => c.to_string(),
        None => return Err(HttpException::new(500, "Internal Server Error")),
    };
    // `scopes = oauth_cfg.get("scopes", [])`.
    let scopes: Vec<String> = oauth_cfg
        .get("scopes")
        .and_then(|v| v.as_array())
        .map(|a| a.iter().filter_map(|x| x.as_str().map(str::to_string)).collect())
        .unwrap_or_default();

    let redirect_uri = "http://localhost:7000/api/mcp/oauth/callback";

    // `auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)`.
    // urllib.parse.urlencode uses quote_plus (spaces -> '+', RFC-3986-ish).
    let scope_joined = scopes.join(" ");
    let params: Vec<(&str, &str)> = vec![
        ("client_id", client_id.as_str()),
        ("redirect_uri", redirect_uri),
        ("response_type", "code"),
        ("scope", scope_joined.as_str()),
        ("access_type", "offline"),
        ("prompt", "consent"),
        ("state", server_id.as_str()),
    ];
    let auth_url = format!(
        "https://accounts.google.com/o/oauth2/v2/auth?{}",
        urlencode(&params)
    );

    // `host = request.headers.get("host", "")`.
    let host = headers
        .get("host")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    // `is_local = host.startswith("localhost") or host.startswith("127.0.0.1")`.
    let is_local = host.starts_with("localhost") || host.starts_with("127.0.0.1");

    if is_local {
        // `return RedirectResponse(auth_url)` — Starlette default status 307.
        Ok(Redirect::temporary(&auth_url).into_response())
    } else {
        // `return HTMLResponse(_oauth_authorize_page(auth_url, server_id, host))`.
        Ok(Html(oauth_authorize_page(&auth_url, &server_id, host)).into_response())
    }
}

/// `GET /api/mcp/oauth/callback` — handle OAuth callback from Google.
///
/// `def oauth_callback(code: str, state: str, request: Request):` — `code`/`state`
/// are required query params (FastAPI 422 if absent).
async fn oauth_callback(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Query(q): Query<OAuthCallbackQuery>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    // `server_id = state`.
    let server_id = q.state;
    Ok(exchange_and_connect(&state, &server_id, &q.code).await)
}

/// `code`/`state` query params for the OAuth callback (both required).
#[derive(Debug, Deserialize)]
struct OAuthCallbackQuery {
    code: String,
    state: String,
}

/// `POST /api/mcp/oauth/exchange/{server_id}` — manual code exchange (the user
/// pastes the callback URL from their browser).
///
/// `callback_url: str = Form(...)`.
async fn oauth_exchange(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Path(server_id): Path<String>,
    mp: Multipart,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;

    let form = parse_form(mp).await;
    let callback_url = form.get("callback_url").cloned().unwrap_or_default();

    // ```python
    // try:
    //     parsed = urlparse(callback_url); params = parse_qs(parsed.query)
    //     code = params.get("code", [None])[0]
    //     if not code: return HTMLResponse(_oauth_result_page("Error", "No authorization code found ..."), 400)
    // except Exception: return HTMLResponse(_oauth_result_page("Error", "Invalid URL format."), 400)
    // ```
    let code = parse_code_from_url(&callback_url);
    let code = match code {
        Some(c) if !c.is_empty() => c,
        _ => {
            return Ok(html_status(
                StatusCode::BAD_REQUEST,
                oauth_result_page(
                    "Error",
                    "No authorization code found in the URL. Make sure you copied the full URL from your browser.",
                    false,
                ),
            ));
        }
    };

    Ok(exchange_and_connect(&state, &server_id, &code).await)
}

/// `_exchange_and_connect(server_id, code, request)` — exchange the auth code for
/// tokens and connect the MCP server. Reproduces every Python branch verbatim.
async fn exchange_and_connect(state: &AppState, server_id: &str, code: &str) -> Response {
    // `db = SessionLocal()` ... whole body wrapped in a try/except -> 500 result page.
    let srv = match session_local() {
        Ok(conn) => match fetch_server(&conn, server_id) {
            Ok(s) => s,
            Err(_) => {
                return html_status(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    oauth_result_page("Error", "database error", false),
                )
            }
        },
        Err(_) => {
            return html_status(
                StatusCode::INTERNAL_SERVER_ERROR,
                oauth_result_page("Error", "database error", false),
            )
        }
    };
    // `if not srv: return HTMLResponse(_oauth_result_page("Error", "Server not found."), 404)`.
    let srv = match srv {
        Some(s) => s,
        None => {
            return html_status(
                StatusCode::NOT_FOUND,
                oauth_result_page("Error", "Server not found.", false),
            )
        }
    };
    // `if not srv.oauth_config: return HTMLResponse(_oauth_result_page("Error", "No OAuth config."), 400)`.
    let oauth_config = match srv.oauth_config.as_deref().filter(|s| !s.is_empty()) {
        Some(c) => c,
        None => {
            return html_status(
                StatusCode::BAD_REQUEST,
                oauth_result_page("Error", "No OAuth config.", false),
            )
        }
    };

    // `oauth_cfg = json.loads(srv.oauth_config)`; `keys_file`/`token_file` expanded.
    let oauth_cfg: Value = match serde_json::from_str(oauth_config) {
        Ok(v) => v,
        Err(e) => {
            return oauth_500(&e.to_string());
        }
    };
    let keys_file = expanduser(oauth_cfg.get("keys_file").and_then(|v| v.as_str()).unwrap_or(""));
    let token_file = expanduser(oauth_cfg.get("token_file").and_then(|v| v.as_str()).unwrap_or(""));

    // `with open(keys_file) as f: keys_data = json.load(f)` — file-not-found / bad JSON -> generic 500.
    let keys_data: Value = match std::fs::read_to_string(&keys_file) {
        Ok(s) => match serde_json::from_str(&s) {
            Ok(v) => v,
            Err(e) => return oauth_500(&e.to_string()),
        },
        Err(e) => return oauth_500(&e.to_string()),
    };
    // `keys = keys_data.get("installed") or keys_data.get("web")`.
    let keys = keys_data
        .get("installed")
        .filter(|v| !v.is_null())
        .or_else(|| keys_data.get("web"));
    // `client_id = keys["client_id"]; client_secret = keys["client_secret"]` — KeyError -> 500.
    let (client_id, client_secret) = match keys {
        Some(k) => {
            match (
                k.get("client_id").and_then(|v| v.as_str()),
                k.get("client_secret").and_then(|v| v.as_str()),
            ) {
                (Some(id), Some(secret)) => (id.to_string(), secret.to_string()),
                _ => return oauth_500("'client_id'"),
            }
        }
        None => return oauth_500("'NoneType' object is not subscriptable"),
    };

    let redirect_uri = "http://localhost:7000/api/mcp/oauth/callback";

    // `async with httpx.AsyncClient() as client: resp = await client.post(token_url, data=...)`.
    let token_resp = reqwest::Client::new()
        .post("https://oauth2.googleapis.com/token")
        .form(&[
            ("code", code),
            ("client_id", client_id.as_str()),
            ("client_secret", client_secret.as_str()),
            ("redirect_uri", redirect_uri),
            ("grant_type", "authorization_code"),
        ])
        .send()
        .await;
    let resp = match token_resp {
        Ok(r) => r,
        Err(e) => return oauth_500(&e.to_string()),
    };

    let status_code = resp.status();
    let body_text = resp.text().await.unwrap_or_default();
    // `if resp.status_code != 200: ... return HTMLResponse(... "Google returned an error: {err}", 400)`.
    if status_code.as_u16() != 200 {
        logger::error(&format!("OAuth token exchange failed: {body_text}"));
        return html_status(
            StatusCode::BAD_REQUEST,
            oauth_result_page(
                "Authorization Failed",
                &format!("Google returned an error: {body_text}"),
                false,
            ),
        );
    }

    // `tokens = resp.json()`.
    let tokens: Value = match serde_json::from_str(&body_text) {
        Ok(v) => v,
        Err(e) => return oauth_500(&e.to_string()),
    };
    logger::info(&format!("OAuth tokens received for server {server_id}"));

    // `os.makedirs(os.path.dirname(token_file), exist_ok=True); json.dump(tokens, f, indent=2)`.
    let token_dir = crate::pyos::path::dirname(&token_file);
    if let Err(e) = crate::pyos::makedirs(&token_dir, true) {
        return oauth_500(&e.to_string());
    }
    // `json.dump(tokens, f, indent=2)` — 2-space indent, matching Python's serializer.
    let serialized = match serde_json::to_string_pretty(&tokens) {
        Ok(s) => s,
        Err(e) => return oauth_500(&e.to_string()),
    };
    if let Err(e) = std::fs::write(&token_file, serialized) {
        return oauth_500(&e.to_string());
    }
    logger::info(&format!("Saved OAuth tokens to {token_file}"));

    // Attempt to connect the MCP server now.
    let args = parse_json_array(srv.args.as_deref());
    let env = parse_json_object(srv.env.as_deref());
    let connected = state
        .mcp_manager
        .connect_server(
            server_id,
            &srv.name,
            &srv.transport,
            srv.command.as_deref(),
            value_array_to_string_vec(&args),
            value_object_to_string_map(&env),
            srv.url.as_deref(),
        )
        .await;

    let status = state.mcp_manager.get_server_status(server_id);
    if connected {
        let tool_count = status.get("tool_count").and_then(|v| v.as_i64()).unwrap_or(0);
        Html(oauth_result_page(
            "Authorization Successful",
            &format!("{} connected with {} tools. You can close this window.", srv.name, tool_count),
            true,
        ))
        .into_response()
    } else {
        let err = status
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("unknown error");
        Html(oauth_result_page(
            "Authorized but Connection Failed",
            &format!(
                "Tokens saved, but the server failed to connect: {err}. Try reconnecting from Settings."
            ),
            false,
        ))
        .into_response()
    }
}

// ---------------------------------------------------------------------------
// HTML page builders (the module-level `_oauth_*_page` functions).
// ---------------------------------------------------------------------------

/// `_oauth_authorize_page(auth_url, server_id, host)` — sign-in link + paste-back form.
///
/// The upstream Python (7c7ac10) escapes all three interpolated values with
/// `html.escape(quote=True)` before inserting them into the HTML. `auth_url` comes
/// from the Google consent URL we build ourselves (contains `&` query separators),
/// `server_id` comes from the OAuth `state` param, and `host` comes directly from
/// the request `Host` header — none of the three is trusted input.
fn oauth_authorize_page(auth_url: &str, server_id: &str, host: &str) -> String {
    // `auth_url = html.escape(auth_url, quote=True)`
    // `server_id = html.escape(server_id, quote=True)`
    // `host = html.escape(host, quote=True)`
    let auth_url = html_escape(auth_url);
    let server_id = html_escape(server_id);
    let host = html_escape(host);
    format!(
        r#"<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"><title>Authorize — Odysseus</title>
<style>
  body {{ font-family: 'Fira Code', monospace; background: #0f0f0f; color: #e0e0e0;
    display: flex; justify-content: center; align-items: center; min-height: 100vh; }}
  .card {{ background: #1a1a1a; border: 1px solid #333; border-radius: 12px;
    padding: 2rem; max-width: 480px; text-align: center; }}
  h2 {{ color: #e06c75; margin-bottom: 0.5rem; font-size: 1.1rem; }}
  p {{ color: #aaa; font-size: 0.82rem; line-height: 1.6; margin: 0.8rem 0; }}
  .step {{ text-align: left; color: #ccc; font-size: 0.82rem; line-height: 1.7; margin: 1rem 0; }}
  .step b {{ color: #e06c75; }}
  a.auth-link {{
    display: inline-block; margin: 1rem 0; padding: 0.6rem 1.5rem;
    background: #e06c75; color: #fff; text-decoration: none; border-radius: 6px;
    font-weight: 600; font-size: 0.9rem;
  }}
  a.auth-link:hover {{ background: #c55; }}
  input[type=text] {{
    width: 100%; padding: 0.5rem; margin: 0.5rem 0;
    background: #0f0f0f; border: 1px solid #333; border-radius: 6px;
    color: #e0e0e0; font-family: 'Fira Code', monospace; font-size: 0.8rem;
  }}
  input:focus {{ outline: none; border-color: #e06c75; }}
  button {{
    padding: 0.5rem 1.5rem; border: none; border-radius: 6px;
    background: #e06c75; color: #fff; font-weight: 600; cursor: pointer;
    font-family: 'Fira Code', monospace; font-size: 0.85rem; margin-top: 0.3rem;
  }}
  button:hover {{ background: #c55; }}
  .divider {{ border-top: 1px solid #333; margin: 1.2rem 0; }}
</style></head>
<body><div class="card">
  <h2>Authorize Google Account</h2>
  <div class="step">
    <b>1.</b> Click the button below to sign in with Google<br>
    <b>2.</b> After approving, your browser will show an error page — that's normal<br>
    <b>3.</b> Copy the full URL from your browser's address bar<br>
    <b>4.</b> Paste it below and click Connect
  </div>
  <a class="auth-link" href="{auth_url}" target="_blank" rel="noopener">Sign in with Google</a>
  <div class="divider"></div>
  <form method="POST" action="http://{host}/api/mcp/oauth/exchange/{server_id}">
    <p>Paste the URL from your browser after signing in:</p>
    <input type="text" name="callback_url" placeholder="http://localhost:7000/api/mcp/oauth/callback?code=..." required>
    <br><button type="submit">Connect</button>
  </form>
</div></body></html>"#
    )
}

/// `_oauth_result_page(title, message, success=False)` — simple result page.
///
/// `safe_title = html.escape(title); safe_message = html.escape(message)` — both
/// escaped via Python's `html.escape` (which escapes `& < > " '` by default).
fn oauth_result_page(title: &str, message: &str, success: bool) -> String {
    let safe_title = html_escape(title);
    let safe_message = html_escape(message);
    let color = if success { "#00661a" } else { "#e06c75" };
    let icon = if success { "&#10003;" } else { "&#10007;" };
    format!(
        r#"<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"><title>{safe_title}</title>
<style>
  body {{ font-family: 'Fira Code', monospace; background: #0f0f0f; color: #e0e0e0;
    display: flex; justify-content: center; align-items: center; min-height: 100vh; }}
  .card {{ background: #1a1a1a; border: 1px solid #333; border-radius: 12px;
    padding: 2rem; max-width: 420px; text-align: center; }}
  .icon {{ font-size: 3rem; color: {color}; margin-bottom: 1rem; }}
  h2 {{ color: {color}; margin-bottom: 0.5rem; font-size: 1.1rem; }}
  p {{ color: #aaa; font-size: 0.85rem; line-height: 1.5; }}
</style></head>
<body><div class="card">
  <div class="icon">{icon}</div>
  <h2>{safe_title}</h2>
  <p>{safe_message}</p>
</div></body></html>"#
    )
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// `require_admin(request)` (core/middleware.py) — the per-handler admin gate.
fn admin_gate(
    state: &AppState,
    headers: &HeaderMap,
    user: Option<&CurrentUser>,
) -> Result<(), HttpException> {
    let internal_header = headers
        .get(INTERNAL_TOOL_HEADER)
        .and_then(|v| v.to_str().ok());
    let user = user.map(|u| u.0.as_str());
    auth_adapter::require_admin(state, internal_header, user)
}

/// `_load_disabled_map()` — per-server disabled tool sets from the DB.
///
/// ```python
/// disabled_map = {}
/// for srv in db.query(McpServer).all():
///     if srv.disabled_tools:
///         try:
///             names = json.loads(srv.disabled_tools)
///             if names: disabled_map[srv.id] = set(names)
///         except (json.JSONDecodeError, TypeError): pass
/// return disabled_map
/// ```
fn load_disabled_map() -> Result<HashMap<String, HashSet<String>>, HttpException> {
    let conn = session_local()?;
    let mut stmt = conn
        .prepare("SELECT id, disabled_tools FROM mcp_servers")
        .map_err(db_500)?;
    let rows: Vec<(String, Option<String>)> = stmt
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))
        .map_err(db_500)?
        .collect::<rusqlite::Result<Vec<_>>>()
        .map_err(db_500)?;

    let mut disabled_map: HashMap<String, HashSet<String>> = HashMap::new();
    for (id, disabled_tools) in rows {
        // `if srv.disabled_tools:` — empty/NULL is falsy.
        if let Some(s) = disabled_tools.as_deref().filter(|s| !s.is_empty()) {
            // `names = json.loads(...)`; decode error (or non-iterable) swallowed.
            if let Ok(parsed) = serde_json::from_str::<Value>(s) {
                if let Some(arr) = parsed.as_array() {
                    // `if names:` — a non-empty list yields a set; an empty list is skipped.
                    let names: HashSet<String> = arr
                        .iter()
                        .filter_map(|v| v.as_str().map(str::to_string))
                        .collect();
                    if !names.is_empty() {
                        disabled_map.insert(id, names);
                    }
                }
            }
        }
    }
    Ok(disabled_map)
}

/// Write a Google OAuth credentials file when `oauth_file` is provided.
///
/// ```python
/// oauth_data = json.loads(oauth_file)
/// oauth_dir = os.path.expanduser(oauth_data.get("dir", ""))
/// oauth_filename = oauth_data.get("filename", "")
/// client_id = oauth_data.get("client_id", ""); client_secret = oauth_data.get("client_secret", "")
/// if oauth_dir and oauth_filename and client_id and client_secret:
///     os.makedirs(oauth_dir, exist_ok=True)
///     creds = {"installed": {...}}
///     with open(os.path.join(oauth_dir, oauth_filename), "w") as f: json.dump(creds, f, indent=2)
///     parsed_env.pop("GOOGLE_CLIENT_ID", None); parsed_env.pop("GOOGLE_CLIENT_SECRET", None)
/// except (json.JSONDecodeError, OSError) as e: logger.warning(...)
/// ```
fn write_oauth_file(oauth_file: &str, parsed_env: &mut Value) {
    let oauth_data: Value = match serde_json::from_str(oauth_file) {
        Ok(v) => v,
        Err(e) => {
            logger::warning(&format!("Failed to write OAuth file: {e}"));
            return;
        }
    };
    let oauth_dir = expanduser(oauth_data.get("dir").and_then(|v| v.as_str()).unwrap_or(""));
    let oauth_filename = oauth_data.get("filename").and_then(|v| v.as_str()).unwrap_or("");
    let client_id = oauth_data.get("client_id").and_then(|v| v.as_str()).unwrap_or("");
    let client_secret = oauth_data.get("client_secret").and_then(|v| v.as_str()).unwrap_or("");

    // `if oauth_dir and oauth_filename and client_id and client_secret:`.
    if oauth_dir.is_empty() || oauth_filename.is_empty() || client_id.is_empty() || client_secret.is_empty() {
        return;
    }

    if let Err(e) = crate::pyos::makedirs(&oauth_dir, true) {
        logger::warning(&format!("Failed to write OAuth file: {e}"));
        return;
    }
    // `creds = {"installed": {client_id, client_secret, redirect_uris, auth_uri, token_uri}}`.
    let creds = json!({
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uris": ["http://localhost"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://accounts.google.com/o/oauth2/token",
        }
    });
    let filepath = crate::pyos::path::join(&oauth_dir, oauth_filename);
    let serialized = match serde_json::to_string_pretty(&creds) {
        Ok(s) => s,
        Err(e) => {
            logger::warning(&format!("Failed to write OAuth file: {e}"));
            return;
        }
    };
    if let Err(e) = std::fs::write(&filepath, serialized) {
        logger::warning(&format!("Failed to write OAuth file: {e}"));
        return;
    }
    logger::info(&format!("Wrote OAuth credentials to {filepath}"));
    // `parsed_env.pop("GOOGLE_CLIENT_ID", None); parsed_env.pop("GOOGLE_CLIENT_SECRET", None)`.
    if let Some(obj) = parsed_env.as_object_mut() {
        obj.remove("GOOGLE_CLIENT_ID");
        obj.remove("GOOGLE_CLIENT_SECRET");
    }
}

/// `urllib.parse.urlparse(callback_url).query` -> `parse_qs(...)["code"][0]`.
///
/// Extracts the first `code` query param from a URL. Returns `None` when there is
/// no query string or no `code` param (the Python `params.get("code", [None])[0]`).
fn parse_code_from_url(callback_url: &str) -> Option<String> {
    // urlparse splits on the first '?'; the query is everything after it
    // (up to a '#' fragment, which parse_qs would otherwise include — urlparse
    // strips the fragment first).
    let after_q = callback_url.split_once('?').map(|(_, q)| q)?;
    let query = after_q.split_once('#').map(|(q, _)| q).unwrap_or(after_q);
    for pair in query.split('&') {
        let (k, v) = match pair.split_once('=') {
            Some((k, v)) => (k, v),
            None => (pair, ""),
        };
        if k == "code" {
            return Some(url_decode(v));
        }
    }
    None
}

/// `urllib.parse.urlencode(params)` — RFC-3986 form encoding with `quote_plus`
/// (space -> `+`). Builds `k=v&k=v` from the ordered pairs.
fn urlencode(params: &[(&str, &str)]) -> String {
    params
        .iter()
        .map(|(k, v)| format!("{}={}", quote_plus(k), quote_plus(v)))
        .collect::<Vec<_>>()
        .join("&")
}

/// `urllib.parse.quote_plus` — percent-encode, with spaces becoming `+`. The safe
/// set is the unreserved RFC-3986 set (`A-Za-z0-9-._~`); everything else is `%XX`.
fn quote_plus(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char)
            }
            b' ' => out.push('+'),
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

/// `urllib.parse.parse_qs` per-value decoding: `+` -> space, then `%XX` -> byte.
fn url_decode(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out: Vec<u8> = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            b'%' if i + 2 < bytes.len() => {
                let hex = &s[i + 1..i + 3];
                match u8::from_str_radix(hex, 16) {
                    Ok(b) => {
                        out.push(b);
                        i += 3;
                    }
                    Err(_) => {
                        out.push(b'%');
                        i += 1;
                    }
                }
            }
            other => {
                out.push(other);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// `html.escape(s)` — escape `& < > " '` (Python's default `quote=True`).
fn html_escape(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&#x27;"),
            other => out.push(other),
        }
    }
    out
}

/// `os.path.expanduser` — expand a leading `~/` to `$HOME`. Returns the path
/// unchanged when there is no leading `~/` or no `HOME`.
fn expanduser(path: &str) -> String {
    if let Some(rest) = path.strip_prefix("~/") {
        if let Ok(home) = std::env::var("HOME") {
            return format!("{}/{}", home.trim_end_matches('/'), rest);
        }
    }
    // bare "~" expands to $HOME in Python too.
    if path == "~" {
        if let Ok(home) = std::env::var("HOME") {
            return home;
        }
    }
    path.to_string()
}

/// `os.path.exists(path)`.
fn path_exists(path: &str) -> bool {
    std::fs::metadata(path).is_ok()
}

/// `repr(x)` for an `Optional[str]` form field, mirroring `f"...={oauth_file!r}"`.
/// `None` renders as `None`; a string renders as a single-quoted Python repr.
fn py_repr_opt(s: &Option<String>) -> String {
    match s {
        None => "None".to_string(),
        Some(v) => format!("'{}'", v.replace('\\', "\\\\").replace('\'', "\\'")),
    }
}

/// `json.loads(col) if col else None` (decode error swallowed elsewhere; here it
/// returns `None` on malformed JSON, matching how the caller treats a missing cfg).
fn parse_json_opt(col: Option<&str>) -> Option<Value> {
    match col.filter(|s| !s.is_empty()) {
        Some(s) => serde_json::from_str(s).ok(),
        None => None,
    }
}

/// `json.loads(col) if col else default` returning the parsed `Value` or `default`.
fn parse_json_or(col: Option<&str>, default: Value) -> Value {
    match col.filter(|s| !s.is_empty()) {
        Some(s) => serde_json::from_str(s).unwrap_or(default),
        None => default,
    }
}

/// `json.loads(col) if col else []` constrained to a JSON array (empty on miss).
fn parse_json_array(col: Option<&str>) -> Vec<Value> {
    match parse_json_or(col, json!([])) {
        Value::Array(a) => a,
        _ => Vec::new(),
    }
}

/// `json.loads(col) if col else {}` constrained to a JSON object (empty on miss).
fn parse_json_object(col: Option<&str>) -> serde_json::Map<String, Value> {
    match parse_json_or(col, json!({})) {
        Value::Object(o) => o,
        _ => serde_json::Map::new(),
    }
}

/// Coerce a parsed JSON array into the `Vec<String>` `connect_server` wants
/// (the Python list is already `List[str]`; non-string entries are dropped).
fn json_to_string_vec(v: &Value) -> Vec<String> {
    v.as_array()
        .map(|a| a.iter().filter_map(|x| x.as_str().map(str::to_string)).collect())
        .unwrap_or_default()
}

fn value_array_to_string_vec(a: &[Value]) -> Vec<String> {
    a.iter().filter_map(|x| x.as_str().map(str::to_string)).collect()
}

/// Coerce a parsed JSON object into the `HashMap<String, String>` env map.
fn json_to_string_map(v: &Value) -> HashMap<String, String> {
    v.as_object()
        .map(|o| {
            o.iter()
                .filter_map(|(k, val)| val.as_str().map(|s| (k.clone(), s.to_string())))
                .collect()
        })
        .unwrap_or_default()
}

fn value_object_to_string_map(o: &serde_json::Map<String, Value>) -> HashMap<String, String> {
    o.iter()
        .filter_map(|(k, val)| val.as_str().map(|s| (k.clone(), s.to_string())))
        .collect()
}

/// Collect all `multipart/form-data` text fields into a map — the same collector
/// the sibling `api_token_routes` port uses. FastAPI `Form(...)` accepts both
/// `application/x-www-form-urlencoded` and `multipart/form-data`.
async fn parse_form(mut mp: Multipart) -> HashMap<String, String> {
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

/// Build an `HTMLResponse(body, status_code=...)` with an explicit status.
fn html_status(status: StatusCode, body: String) -> Response {
    (status, Html(body)).into_response()
}

/// The catch-all `except Exception as e: ... return HTMLResponse(_oauth_result_page("Error", str(e)), 500)`.
fn oauth_500(msg: &str) -> Response {
    logger::error(&format!("OAuth callback error: {msg}"));
    html_status(
        StatusCode::INTERNAL_SERVER_ERROR,
        oauth_result_page("Error", msg, false),
    )
}

/// Open a DB connection (`SessionLocal()`), mapping a failure to a 500.
fn session_local() -> Result<rusqlite::Connection, HttpException> {
    crate::core::database::session_local().map_err(db_500)
}

/// Map a `rusqlite::Error` to a 500 `HttpException`.
fn db_500(e: rusqlite::Error) -> HttpException {
    logger::error(&format!("mcp_routes DB error: {e}"));
    HttpException::new(500, "Internal Server Error")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn quote_plus_matches_urllib() {
        // Spaces -> '+', unreserved chars pass through, reserved -> %XX.
        assert_eq!(quote_plus("a b"), "a+b");
        assert_eq!(quote_plus("a/b:c"), "a%2Fb%3Ac");
        assert_eq!(quote_plus("safe-_.~chars"), "safe-_.~chars");
        // The Google scope string uses spaces between scopes.
        assert_eq!(
            quote_plus("https://www.googleapis.com/auth/gmail.readonly openid"),
            "https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.readonly+openid"
        );
    }

    #[test]
    fn urlencode_builds_ordered_pairs() {
        let params = vec![("client_id", "abc"), ("scope", "a b"), ("state", "srv1")];
        assert_eq!(urlencode(&params), "client_id=abc&scope=a+b&state=srv1");
    }

    #[test]
    fn parse_code_from_url_extracts_first_code() {
        // The realistic Google callback URL the user pastes back.
        assert_eq!(
            parse_code_from_url("http://localhost:7000/api/mcp/oauth/callback?code=4%2F0AeanS&scope=openid"),
            Some("4/0AeanS".to_string())
        );
        // No query at all -> None.
        assert_eq!(parse_code_from_url("http://localhost:7000/x"), None);
        // Query present but no `code` -> None.
        assert_eq!(parse_code_from_url("http://x/cb?state=s&scope=openid"), None);
        // `code` before a fragment (urlparse strips the fragment off the query).
        assert_eq!(
            parse_code_from_url("http://x/cb?code=abc#frag"),
            Some("abc".to_string())
        );
        // Empty code value -> Some("") (the handler then treats it as missing).
        assert_eq!(parse_code_from_url("http://x/cb?code="), Some("".to_string()));
    }

    #[test]
    fn url_decode_handles_plus_and_percent() {
        assert_eq!(url_decode("a+b"), "a b");
        assert_eq!(url_decode("4%2F0A"), "4/0A");
        assert_eq!(url_decode("plain"), "plain");
    }

    #[test]
    fn html_escape_matches_python_default() {
        // Python html.escape with quote=True escapes & < > " ' .
        assert_eq!(html_escape("a<b>&\"'"), "a&lt;b&gt;&amp;&quot;&#x27;");
        assert_eq!(html_escape("plain text"), "plain text");
    }

    /// Regression: `oauth_authorize_page` must html-escape `auth_url`, `server_id`,
    /// and `host` before interpolating them. Upstream Python 7c7ac10 added this fix.
    #[test]
    fn oauth_authorize_page_escapes_all_three_values() {
        // auth_url naturally contains '&' (query-string separators) and '='.
        // server_id and host come from untrusted request data.
        let auth_url = "https://accounts.google.com/o/oauth2/v2/auth?client_id=abc&scope=openid";
        let server_id = r#"<evil>"#;
        let host = r#"localhost:7000"#;

        let page = oauth_authorize_page(auth_url, server_id, host);

        // auth_url: '&' -> '&amp;'
        assert!(
            page.contains("client_id=abc&amp;scope=openid"),
            "auth_url '&' must be escaped to '&amp;' in href"
        );
        // auth_url raw '&' must NOT appear in the href attribute.
        assert!(
            !page.contains("client_id=abc&scope=openid"),
            "raw '&' must not appear in href"
        );

        // server_id: '<' and '>' must be escaped.
        assert!(
            !page.contains("<evil>"),
            "raw server_id '<evil>' must not appear in HTML"
        );
        assert!(
            page.contains("&lt;evil&gt;"),
            "server_id must be escaped to '&lt;evil&gt;'"
        );

        // host: no special chars in this case — verify the escaped value appears.
        assert!(
            page.contains("http://localhost:7000/api/mcp/oauth/exchange/"),
            "escaped host must appear in form action"
        );
    }

    #[test]
    fn parse_json_helpers_default_on_empty_and_bad() {
        assert_eq!(parse_json_or(None, json!([])), json!([]));
        assert_eq!(parse_json_or(Some(""), json!({})), json!({}));
        assert_eq!(parse_json_or(Some("not json"), json!([])), json!([]));
        assert_eq!(parse_json_array(Some("[\"a\", \"b\"]")), vec![json!("a"), json!("b")]);
        assert_eq!(parse_json_array(Some("{}")), Vec::<Value>::new());
        assert!(parse_json_opt(None).is_none());
        assert!(parse_json_opt(Some("")).is_none());
        assert_eq!(parse_json_opt(Some("{\"a\":1}")), Some(json!({"a": 1})));
    }

    #[test]
    fn json_string_coercions_drop_non_strings() {
        assert_eq!(
            json_to_string_vec(&json!(["--port", "8080", 42])),
            vec!["--port".to_string(), "8080".to_string()]
        );
        let map = json_to_string_map(&json!({"KEY": "val", "NUM": 5}));
        assert_eq!(map.get("KEY"), Some(&"val".to_string()));
        assert_eq!(map.get("NUM"), None);
    }

    #[test]
    fn py_repr_opt_renders_none_and_quoted() {
        assert_eq!(py_repr_opt(&None), "None");
        assert_eq!(py_repr_opt(&Some("hi".to_string())), "'hi'");
        assert_eq!(py_repr_opt(&Some("it's".to_string())), "'it\\'s'");
    }

    #[test]
    fn enabled_tool_count_clamps_at_zero() {
        // `max(0, total_tools - len(disabled_list))`.
        let total: i64 = 2;
        let disabled: i64 = 5;
        assert_eq!((total - disabled).max(0), 0);
        let active: i64 = 5;
        let off: i64 = 2;
        assert_eq!((active - off).max(0), 3);
    }

    #[test]
    fn router_mounts_all_mcp_paths() {
        // The factory yields a `Router<AppState>` mergeable with the inline subset;
        // every path is disjoint (axum no duplicate method+path), so building never panics.
        let base: Router<AppState> = Router::new();
        let _merged: Router<AppState> = base.merge(setup_mcp_routes());
    }
}
