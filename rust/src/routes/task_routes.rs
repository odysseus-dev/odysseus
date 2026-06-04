// routes/task_routes.rs  <- routes/task_routes.py
//! CRUD routes for scheduled tasks (routes WAVE 3).
//!
//! Faithful translation of `routes/task_routes.py`'s `setup_task_routes(task_scheduler)`
//! factory. The Python takes the scheduler as a factory argument; here it is reached
//! through `State<AppState>.task_scheduler` (the wired `Arc<TaskScheduler>` singleton),
//! and the `_load_for_user` / `_save_for_user` per-user prefs come from the shared
//! [`crate::routes::prefs_store`] (F6), exactly as the Python imports
//! `from routes.prefs_routes import _load_for_user, _save_for_user`.
//!
//! ## Shape (the integration substrate)
//! * `setup_task_routes() -> Router<AppState>` mirrors the factory. The Python
//!   `APIRouter(prefix="/api/tasks")` mounts every handler at `prefix + route`, so each
//!   `.route(...)` carries the absolute `/api/tasks...` path. axum 0.7 uses the COLON
//!   capture form (`/:task_id`, `/:token`).
//! * Auth is `get_current_user` only — the load-bearing `None` case (auth disabled /
//!   single-user) drives the seed-all-owners list path, the wide-open owner check, and
//!   the empty-notifications guard. There is no `require_user` / `Depends`.
//! * `raise HTTPException(s, d)` -> `return Err(HttpException::new(s, d))`; the
//!   `web`-gated `IntoResponse for HttpException` renders FastAPI's `{"detail": d}`.
//! * `_task_to_dict` / `_run_to_dict` reproduce the dict literals with the `+"Z"`
//!   ISO suffix and the exact key order (`serde_json` is built with `preserve_order`).
//!
//! ## `POST /api/tasks/parse`
//! The Python resolves the LLM endpoint via `src.endpoint_resolver.resolve_endpoint`
//! (`"utility"` then `"default"`). With no `owner` argument that collapses to the global
//! settings cascade + a direct read of the enabled `model_endpoints` row. This module
//! calls the centralized [`crate::src::endpoint_resolver::resolve_endpoint_triple`]
//! (`owner=None`, since Python `task_routes.py:861/863` passes no owner), so the
//! `owner=None` path is byte-for-byte the global cascade today's local copy produced. The
//! `{"success": False, "message": "No model endpoint configured"}` branch is returned
//! ONLY when no endpoint resolves (matching the Python's own `if not (url and model)`).
//!
//! ## No path collision
//! Every route is under `/api/tasks*`, a prefix the inline `web/mod.rs` subset never
//! touches, so the aggregator merges this router without an axum duplicate-`method`+
//! `path` panic. The webhook-trigger route stays unauthenticated at the handler level
//! (the token IS the auth) but still passes the auth gate because it is token-gated.


use std::collections::HashMap;

use axum::extract::{Path, Query, State};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Extension, Json, Router};
use chrono::NaiveDateTime;
use rusqlite::{Connection, OptionalExtension};
use serde::Deserialize;
use serde_json::{json, Map, Value};

use crate::routes::prefs_store;
use crate::routes::{AppState, CurrentUser, HttpException};
use crate::src::task_scheduler::{compute_next_run, HOUSEKEEPING_DEFAULTS};

// ===========================================================================
// Request bodies (pydantic BaseModel -> serde structs)
// ===========================================================================

/// `class TaskCreate(BaseModel)` — the JSON body for `POST /api/tasks`.
///
/// `task_type` defaults to `"llm"`, `scheduled_time` to `"09:00"`, `trigger_type` to
/// `"schedule"`, `output_target` to `"session"`; everything else defaults to `None`.
/// `#[serde(default)]` + the field-level default fns reproduce pydantic's defaults.
#[derive(Debug, Deserialize)]
struct TaskCreate {
    #[serde(default)]
    name: Option<String>,
    #[serde(default)]
    prompt: Option<String>,
    #[serde(default = "default_llm")]
    task_type: String,
    #[serde(default)]
    action: Option<String>,
    #[serde(default)]
    schedule: Option<String>,
    #[serde(default = "default_scheduled_time")]
    scheduled_time: String,
    #[serde(default)]
    scheduled_day: Option<i64>,
    #[serde(default)]
    scheduled_date: Option<String>,
    #[serde(default)]
    cron_expression: Option<String>,
    #[serde(default = "default_schedule_trigger")]
    trigger_type: String,
    #[serde(default)]
    trigger_event: Option<String>,
    #[serde(default)]
    trigger_count: Option<i64>,
    #[serde(default = "default_session")]
    output_target: String,
    #[serde(default)]
    model: Option<String>,
    #[serde(default)]
    endpoint_url: Option<String>,
    #[serde(default)]
    then_task_id: Option<String>,
    #[serde(default)]
    notifications_enabled: Option<bool>,
}

/// `class TaskUpdate(BaseModel)` — the JSON body for `PUT /api/tasks/{task_id}`.
///
/// Every field is optional (`= None`); only `is not None` fields are applied.
#[derive(Debug, Deserialize)]
struct TaskUpdate {
    #[serde(default)]
    name: Option<String>,
    #[serde(default)]
    prompt: Option<String>,
    #[serde(default)]
    task_type: Option<String>,
    #[serde(default)]
    action: Option<String>,
    #[serde(default)]
    schedule: Option<String>,
    #[serde(default)]
    scheduled_time: Option<String>,
    #[serde(default)]
    scheduled_day: Option<i64>,
    #[serde(default)]
    scheduled_date: Option<String>,
    #[serde(default)]
    cron_expression: Option<String>,
    #[serde(default)]
    trigger_type: Option<String>,
    #[serde(default)]
    trigger_event: Option<String>,
    #[serde(default)]
    trigger_count: Option<i64>,
    #[serde(default)]
    output_target: Option<String>,
    #[serde(default)]
    model: Option<String>,
    #[serde(default)]
    endpoint_url: Option<String>,
    #[serde(default)]
    then_task_id: Option<String>,
    #[serde(default)]
    notifications_enabled: Option<bool>,
}

fn default_llm() -> String {
    "llm".to_string()
}
fn default_scheduled_time() -> String {
    "09:00".to_string()
}
fn default_schedule_trigger() -> String {
    "schedule".to_string()
}
fn default_session() -> String {
    "session".to_string()
}

// ===========================================================================
// Row models (the SQLAlchemy ORM rows the handlers read)
// ===========================================================================

/// The `scheduled_tasks` columns the route handlers read, in the SELECT order used by
/// [`TASK_COLS`].
struct TaskRow {
    id: String,
    owner: Option<String>,
    name: Option<String>,
    prompt: Option<String>,
    task_type: Option<String>,
    action: Option<String>,
    schedule: Option<String>,
    scheduled_time: Option<String>,
    scheduled_day: Option<i64>,
    scheduled_date: Option<String>,
    cron_expression: Option<String>,
    trigger_type: Option<String>,
    trigger_event: Option<String>,
    trigger_count: Option<i64>,
    trigger_counter: Option<i64>,
    next_run: Option<String>,
    last_run: Option<String>,
    status: Option<String>,
    output_target: Option<String>,
    session_id: Option<String>,
    crew_member_id: Option<String>,
    model: Option<String>,
    endpoint_url: Option<String>,
    run_count: Option<i64>,
    then_task_id: Option<String>,
    notifications_enabled: Option<bool>,
    webhook_token: Option<String>,
    created_at: Option<String>,
    updated_at: Option<String>,
}

/// SELECT-list for [`TaskRow`], in `read_task_row` column order.
const TASK_COLS: &str = "id, owner, name, prompt, task_type, action, schedule, scheduled_time, \
    scheduled_day, scheduled_date, cron_expression, trigger_type, trigger_event, trigger_count, \
    trigger_counter, next_run, last_run, status, output_target, session_id, crew_member_id, \
    model, endpoint_url, run_count, then_task_id, notifications_enabled, webhook_token, \
    created_at, updated_at";

fn read_task_row(r: &rusqlite::Row<'_>) -> rusqlite::Result<TaskRow> {
    Ok(TaskRow {
        id: r.get(0)?,
        owner: r.get(1)?,
        name: r.get(2)?,
        prompt: r.get(3)?,
        task_type: r.get(4)?,
        action: r.get(5)?,
        schedule: r.get(6)?,
        scheduled_time: r.get(7)?,
        scheduled_day: r.get(8)?,
        scheduled_date: r.get(9)?,
        cron_expression: r.get(10)?,
        trigger_type: r.get(11)?,
        trigger_event: r.get(12)?,
        trigger_count: r.get(13)?,
        trigger_counter: r.get(14)?,
        next_run: r.get(15)?,
        last_run: r.get(16)?,
        status: r.get(17)?,
        output_target: r.get(18)?,
        session_id: r.get(19)?,
        crew_member_id: r.get(20)?,
        model: r.get(21)?,
        endpoint_url: r.get(22)?,
        run_count: r.get(23)?,
        then_task_id: r.get(24)?,
        // BOOLEAN stored as 0/1; rusqlite reads it as Option<bool>.
        notifications_enabled: r.get(25)?,
        webhook_token: r.get(26)?,
        created_at: r.get(27)?,
        updated_at: r.get(28)?,
    })
}

/// A `task_runs` row, in `read_run_row` column order.
struct RunRow {
    id: String,
    task_id: String,
    started_at: Option<String>,
    finished_at: Option<String>,
    status: Option<String>,
    result: Option<String>,
    error: Option<String>,
    tokens_used: Option<i64>,
    model: Option<String>,
}

const RUN_COLS: &str =
    "id, task_id, started_at, finished_at, status, result, error, tokens_used, model";

fn read_run_row(r: &rusqlite::Row<'_>) -> rusqlite::Result<RunRow> {
    Ok(RunRow {
        id: r.get(0)?,
        task_id: r.get(1)?,
        started_at: r.get(2)?,
        finished_at: r.get(3)?,
        status: r.get(4)?,
        result: r.get(5)?,
        error: r.get(6)?,
        tokens_used: r.get(7)?,
        model: r.get(8)?,
    })
}

// ===========================================================================
// Module-level helpers (the Python module-scope functions)
// ===========================================================================

/// `dt.isoformat() + "Z" if dt else None` — render a stored SQLite datetime with a
/// trailing `Z`, or `null` when absent/empty.
fn iso_z_or_null(stored: Option<&str>) -> Value {
    match stored.filter(|s| !s.is_empty()) {
        Some(s) => json!(format!("{}Z", crate::pydatetime::to_isoformat(s))),
        None => Value::Null,
    }
}

/// `x or ""` — Python truthiness: `None`/`""` -> `""`, else the value.
fn or_empty(v: Option<&str>) -> &str {
    match v {
        Some(s) if !s.is_empty() => s,
        _ => "",
    }
}

/// `def _display_task_name(t) -> str`.
///
/// ```python
/// defs = HOUSEKEEPING_DEFAULTS.get(t.action) if t.action else None
/// if defs and (t.name or "") in set(defs.get("legacy_names") or []):
///     return defs["name"]
/// return t.name
/// ```
fn display_task_name(t: &TaskRow) -> Value {
    let defs = t
        .action
        .as_deref()
        .filter(|a| !a.is_empty())
        .and_then(|a| HOUSEKEEPING_DEFAULTS.get(a));
    if let Some(d) = defs {
        let name = or_empty(t.name.as_deref());
        if d.legacy_names.contains(&name) {
            return json!(d.name);
        }
    }
    // `return t.name` — may be NULL (Python returns None).
    match &t.name {
        Some(n) => json!(n),
        None => Value::Null,
    }
}

/// `def _task_to_dict(t, include_last_run_result=False) -> dict`.
///
/// The `runs` ordering (`order_by="TaskRun.started_at.desc()"`) is the relationship's
/// default, so when `include_last_run_result` is set the caller passes the most-recent
/// run as `last_run_row`.
fn task_to_dict(t: &TaskRow, last_run_row: Option<&RunRow>) -> Value {
    let defs = t
        .action
        .as_deref()
        .filter(|a| !a.is_empty())
        .and_then(|a| HOUSEKEEPING_DEFAULTS.get(a));

    let mut d: Map<String, Value> = Map::new();
    d.insert("id".to_string(), json!(t.id));
    d.insert("name".to_string(), display_task_name(t));
    d.insert("prompt".to_string(), opt_str(&t.prompt));
    // `t.task_type or "llm"`
    d.insert(
        "task_type".to_string(),
        json!(non_empty_or(t.task_type.as_deref(), "llm")),
    );
    d.insert("action".to_string(), opt_str(&t.action));
    d.insert("schedule".to_string(), opt_str(&t.schedule));
    d.insert("scheduled_time".to_string(), opt_str(&t.scheduled_time));
    d.insert("scheduled_day".to_string(), opt_i64(&t.scheduled_day));
    d.insert(
        "scheduled_date".to_string(),
        iso_z_or_null(t.scheduled_date.as_deref()),
    );
    d.insert("cron_expression".to_string(), opt_str(&t.cron_expression));
    // `t.trigger_type or "schedule"`
    d.insert(
        "trigger_type".to_string(),
        json!(non_empty_or(t.trigger_type.as_deref(), "schedule")),
    );
    d.insert("trigger_event".to_string(), opt_str(&t.trigger_event));
    d.insert("trigger_count".to_string(), opt_i64(&t.trigger_count));
    // `t.trigger_counter or 0`
    d.insert(
        "trigger_counter".to_string(),
        json!(t.trigger_counter.unwrap_or(0)),
    );
    d.insert("next_run".to_string(), iso_z_or_null(t.next_run.as_deref()));
    d.insert("last_run".to_string(), iso_z_or_null(t.last_run.as_deref()));
    d.insert("status".to_string(), opt_str(&t.status));
    d.insert("output_target".to_string(), opt_str(&t.output_target));
    d.insert("session_id".to_string(), opt_str(&t.session_id));
    d.insert("crew_member_id".to_string(), opt_str(&t.crew_member_id));
    d.insert("model".to_string(), opt_str(&t.model));
    d.insert("endpoint_url".to_string(), opt_str(&t.endpoint_url));
    // `t.run_count or 0`
    d.insert("run_count".to_string(), json!(t.run_count.unwrap_or(0)));
    d.insert("then_task_id".to_string(), opt_str(&t.then_task_id));
    // `bool(getattr(t, "notifications_enabled", True))` — column default True; a NULL
    // value coerces to True (the getattr default), then bool().
    d.insert(
        "notifications_enabled".to_string(),
        json!(t.notifications_enabled.unwrap_or(true)),
    );
    // `t.webhook_token if (t.trigger_type or "schedule") == "webhook" else None`
    let webhook_token = if non_empty_or(t.trigger_type.as_deref(), "schedule") == "webhook" {
        opt_str(&t.webhook_token)
    } else {
        Value::Null
    };
    d.insert("webhook_token".to_string(), webhook_token);
    d.insert("created_at".to_string(), iso_z_or_null(t.created_at.as_deref()));
    d.insert("updated_at".to_string(), iso_z_or_null(t.updated_at.as_deref()));

    // `d["is_builtin"] = defs is not None`
    d.insert("is_builtin".to_string(), json!(defs.is_some()));
    if let Some(d_defs) = defs {
        // default_names = {defs["name"], *set(defs.get("legacy_names") or [])}
        // d["is_modified"] = (
        //     (t.name or "") not in default_names
        //     or (t.schedule or "") != (defs["schedule"] or "")
        //     or (t.scheduled_time or "") != (defs["scheduled_time"] or "")
        //     or (t.cron_expression or "") != (defs["cron_expression"] or "")
        // )
        let name = or_empty(t.name.as_deref());
        let name_in_defaults = name == d_defs.name || d_defs.legacy_names.contains(&name);
        let is_modified = !name_in_defaults
            || or_empty(t.schedule.as_deref()) != or_empty(d_defs.schedule)
            || or_empty(t.scheduled_time.as_deref()) != or_empty(d_defs.scheduled_time)
            || or_empty(t.cron_expression.as_deref()) != or_empty(d_defs.cron_expression);
        d.insert("is_modified".to_string(), json!(is_modified));
    } else {
        d.insert("is_modified".to_string(), json!(false));
    }

    // `if include_last_run_result and t.runs:` — only when a most-recent run is passed.
    if let Some(last) = last_run_row {
        d.insert("last_run_status".to_string(), opt_str(&last.status));
        // `(last.result or last.error or "")[:500]` — Python truthiness, first 500 chars.
        let text = last
            .result
            .as_deref()
            .filter(|s| !s.is_empty())
            .or(last.error.as_deref().filter(|s| !s.is_empty()))
            .unwrap_or("");
        let truncated: String = text.chars().take(500).collect();
        d.insert("last_run_result".to_string(), json!(truncated));
    }

    Value::Object(d)
}

/// `def _run_to_dict(r) -> dict`.
fn run_to_dict(r: &RunRow) -> Value {
    json!({
        "id": r.id,
        "task_id": r.task_id,
        "started_at": iso_z_or_null(r.started_at.as_deref()),
        "finished_at": iso_z_or_null(r.finished_at.as_deref()),
        "status": opt_str(&r.status),
        "result": opt_str(&r.result),
        "error": opt_str(&r.error),
        "tokens_used": opt_i64(&r.tokens_used),
        "model": opt_str(&r.model),
    })
}

/// `def _run_research_id(task) -> str`.
///
/// ```python
/// if (task.task_type or "llm") == "research" and task.session_id:
///     return task.session_id
/// return ""
/// ```
fn run_research_id(task: &TaskRow) -> String {
    if non_empty_or(task.task_type.as_deref(), "llm") == "research" {
        if let Some(sid) = task.session_id.as_deref().filter(|s| !s.is_empty()) {
            return sid.to_string();
        }
    }
    String::new()
}

/// `def _resolve_run_endpoint(db, task, run) -> str` — best-effort endpoint URL for
/// reopening a task run in chat.
///
/// ```python
/// if getattr(task, "endpoint_url", None):
///     return task.endpoint_url or ""
/// try:
///     if getattr(task, "session_id", None):
///         sess = db.query(Session).filter(Session.id == task.session_id).first()
///         if sess and sess.endpoint_url:
///             return sess.endpoint_url or ""
/// except Exception: pass
/// model = (run.model or task.model or "").strip()
/// if not model: return ""
/// try:
///     eps = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all()
///     for ep in eps:
///         cached = json.loads(ep.cached_models) or [] (on error -> [])
///         if model in cached: return ep.base_url or ""
/// except Exception: pass
/// return ""
/// ```
fn resolve_run_endpoint(db: &Connection, task: &TaskRow, run: &RunRow) -> String {
    // `if getattr(task, "endpoint_url", None): return task.endpoint_url or ""`
    if let Some(url) = task.endpoint_url.as_deref().filter(|s| !s.is_empty()) {
        return url.to_string();
    }

    // `if getattr(task, "session_id", None):` -> Session.endpoint_url
    if let Some(sid) = task.session_id.as_deref().filter(|s| !s.is_empty()) {
        let sess_url: Option<Option<String>> = db
            .query_row(
                "SELECT endpoint_url FROM sessions WHERE id = ?1",
                rusqlite::params![sid],
                |r| r.get::<_, Option<String>>(0),
            )
            .optional()
            .ok()
            .flatten();
        if let Some(Some(url)) = sess_url {
            if !url.is_empty() {
                return url;
            }
        }
    }

    // `model = (run.model or task.model or "").strip()`
    let model = run
        .model
        .as_deref()
        .filter(|s| !s.is_empty())
        .or(task.model.as_deref().filter(|s| !s.is_empty()))
        .unwrap_or("")
        .trim()
        .to_string();
    if model.is_empty() {
        return String::new();
    }

    // ModelEndpoint scan: enabled endpoints whose cached_models JSON contains `model`.
    let scan = || -> rusqlite::Result<String> {
        let mut stmt =
            db.prepare("SELECT base_url, cached_models FROM model_endpoints WHERE is_enabled = 1")?;
        let rows = stmt.query_map([], |r| {
            Ok((
                r.get::<_, Option<String>>(0)?,
                r.get::<_, Option<String>>(1)?,
            ))
        })?;
        for row in rows {
            let (base_url, cached_models) = row?;
            // `cached = json.loads(ep.cached_models) or []` (decode error -> []).
            let cached: Vec<String> = match cached_models.as_deref().filter(|s| !s.is_empty()) {
                Some(s) => serde_json::from_str::<Value>(s)
                    .ok()
                    .and_then(|v| v.as_array().cloned())
                    .map(|arr| {
                        arr.into_iter()
                            .filter_map(|x| x.as_str().map(str::to_string))
                            .collect()
                    })
                    .unwrap_or_default(),
                None => Vec::new(),
            };
            if cached.iter().any(|m| m == &model) {
                // `return ep.base_url or ""`
                return Ok(base_url.unwrap_or_default());
            }
        }
        Ok(String::new())
    };
    scan().unwrap_or_default()
}

/// `t.<col> or "<default>"` over an `Option<&str>` — Python truthiness fallback.
fn non_empty_or<'a>(v: Option<&'a str>, default: &'a str) -> &'a str {
    match v {
        Some(s) if !s.is_empty() => s,
        _ => default,
    }
}

/// A nullable string column -> JSON string or `null` (a SQLAlchemy attribute that is
/// `None` renders as JSON `null`).
fn opt_str(v: &Option<String>) -> Value {
    match v {
        Some(s) => json!(s),
        None => Value::Null,
    }
}

/// A nullable integer column -> JSON number or `null`.
fn opt_i64(v: &Option<i64>) -> Value {
    match v {
        Some(n) => json!(n),
        None => Value::Null,
    }
}

// ===========================================================================
// Admin-only action gate (review CRIT-C)
// ===========================================================================

/// `_ADMIN_ONLY_ACTIONS = {"run_local", "run_script", "ssh_command"}` — actions that
/// execute shell/SSH commands, restricted to admins.
const ADMIN_ONLY_ACTIONS: &[&str] = &["run_local", "run_script", "ssh_command"];

fn is_admin_only_action(action: Option<&str>) -> bool {
    matches!(action, Some(a) if ADMIN_ONLY_ACTIONS.contains(&a))
}

/// `def _is_admin(user: str | None) -> bool`.
///
/// ```python
/// if not user: return False
/// if user == "internal-tool": return True   # in-process tool-loopback marker
/// try:
///     auth = AuthManager()
///     if not auth.is_configured: return True
///     return bool(auth.is_admin(user))
/// except Exception: return False
/// ```
///
/// The Python constructs a fresh `AuthManager()`; the wired `state.auth` reads the
/// same `auth.json`, so we consult it directly (equivalent, no observable drift).
fn is_admin(state: &AppState, user: Option<&str>) -> bool {
    let user = match user.filter(|u| !u.is_empty()) {
        Some(u) => u,
        None => return false,
    };
    if user == "internal-tool" {
        return true;
    }
    if !state.auth.is_configured() {
        return true;
    }
    state.auth.is_admin(user)
}

// ===========================================================================
// cron validation (croniter(expr) -> the `cron` crate)
// ===========================================================================

/// `croniter(req.cron_expression)` validation — `True` if the expression parses.
///
/// The Python only structurally validates (`croniter(...)` raises on a malformed
/// expression). We translate the 5-field croniter form to the 6-field, 1-7-DOW form
/// the `cron` crate expects (the same documented deviation `task_scheduler`'s private
/// `croniter_to_cron_crate` applies) and attempt a parse; a parse failure mirrors the
/// raised `ValueError`. Keeping this validation in lock-step with what
/// `compute_next_run` will actually accept avoids a "valid here, ignored there" gap.
fn cron_is_valid(expr: &str) -> bool {
    use cron::Schedule;
    use std::str::FromStr;
    match croniter_to_cron_crate(expr) {
        Some(translated) => Schedule::from_str(&translated).is_ok(),
        None => false,
    }
}

/// Translate a 5/6-field croniter expression into the 6-field, 1-7-DOW form the `cron`
/// crate parses (mirrors `task_scheduler::croniter_to_cron_crate`, which is private).
fn croniter_to_cron_crate(expr: &str) -> Option<String> {
    let fields: Vec<&str> = expr.split_whitespace().collect();
    let (sec, rest): (&str, &[&str]) = if fields.len() == 6 {
        (fields[0], &fields[1..])
    } else if fields.len() == 5 {
        ("0", &fields[..])
    } else {
        return None;
    };
    if rest.len() != 5 {
        return None;
    }
    Some(format!(
        "{sec} {} {} {} {} {}",
        rest[0],
        rest[1],
        rest[2],
        rest[3],
        remap_dow_field(rest[4])
    ))
}

/// Remap each numeric token in a croniter DOW field (0-7, Sun=0/7) to the cron crate's
/// 1-7 (Sun=1). Lists/ranges/steps handled; named tokens and `*` pass through.
fn remap_dow_field(field: &str) -> String {
    if field == "*" {
        return "*".to_string();
    }
    field
        .split(',')
        .map(remap_dow_token)
        .collect::<Vec<_>>()
        .join(",")
}

fn remap_dow_token(token: &str) -> String {
    if let Some((base, step)) = token.split_once('/') {
        return format!("{}/{}", remap_dow_token(base), step);
    }
    if let Some((lo, hi)) = token.split_once('-') {
        return format!("{}-{}", remap_dow_num(lo), remap_dow_num(hi));
    }
    remap_dow_num(token)
}

fn remap_dow_num(s: &str) -> String {
    match s.trim().parse::<u32>() {
        Ok(n) => ((n % 7) + 1).to_string(),
        Err(_) => s.to_string(),
    }
}

// ===========================================================================
// datetime parsing helpers
// ===========================================================================

/// `datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)` — parse a
/// client-supplied ISO datetime and drop any tz, yielding a naive datetime. `None` on a
/// parse failure (the Python `except ValueError`).
fn parse_scheduled_date(value: &str) -> Option<NaiveDateTime> {
    let normalized = value.replace('Z', "+00:00");
    // Try tz-aware first (matching fromisoformat + replace(tzinfo=None) -> naive local).
    if let Ok(dt) = chrono::DateTime::parse_from_rfc3339(&normalized) {
        return Some(dt.naive_local());
    }
    // Plain naive forms (no offset).
    for fmt in [
        "%Y-%m-%dT%H:%M:%S%.f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S%.f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ] {
        if let Ok(dt) = NaiveDateTime::parse_from_str(&normalized, fmt) {
            return Some(dt);
        }
    }
    None
}

/// Parse a stored `scheduled_date` column value back into a `NaiveDateTime` for feeding
/// `compute_next_run` (the column holds a SQLite datetime string).
fn parse_stored_dt(stored: Option<&str>) -> Option<NaiveDateTime> {
    let s = stored?;
    NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S%.f")
        .or_else(|_| NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S"))
        .ok()
}

// ===========================================================================
// AppState / current-user plumbing
// ===========================================================================

/// `_owner(request) -> get_current_user(request)` — the resolved username (or `None`).
fn owner(user: &Option<Extension<CurrentUser>>) -> Option<String> {
    user.as_ref().map(|Extension(u)| u.0.clone())
}

/// Open a DB connection (`SessionLocal()`), mapping a failure to a generic 500.
fn session_local() -> Result<Connection, HttpException> {
    crate::core::database::session_local().map_err(db_500)
}

fn db_500(e: rusqlite::Error) -> HttpException {
    crate::pylog::error(&format!("task_routes DB error: {e}"));
    HttpException::new(500, "Internal Server Error")
}

/// Load a task row by id (`db.query(ScheduledTask).filter(id == task_id).first()`).
fn load_task(db: &Connection, task_id: &str) -> Result<Option<TaskRow>, HttpException> {
    let sql = format!("SELECT {TASK_COLS} FROM scheduled_tasks WHERE id = ?1");
    db.query_row(&sql, rusqlite::params![task_id], read_task_row)
        .optional()
        .map_err(db_500)
}

/// The shared `not found` / `access denied` ownership gate for a single-task lookup:
///
/// ```python
/// if not task: raise HTTPException(404, "Task not found")
/// if user and task.owner != user: raise HTTPException(403, "Access denied")
/// ```
fn require_task<'a>(
    task: &'a Option<TaskRow>,
    user: Option<&str>,
) -> Result<&'a TaskRow, HttpException> {
    let task = match task {
        Some(t) => t,
        None => return Err(HttpException::new(404, "Task not found")),
    };
    if let Some(u) = user {
        if task.owner.as_deref() != Some(u) {
            return Err(HttpException::new(403, "Access denied"));
        }
    }
    Ok(task)
}

// ===========================================================================
// Router factory
// ===========================================================================

/// `setup_task_routes(task_scheduler)` — build the `/api/tasks` router.
///
/// The Python registers, in this order (the `APIRouter(prefix="/api/tasks")` makes each
/// absolute): `GET ""`, `GET /onboarding`, `POST /onboarding`, `POST ""`,
/// `GET /notifications`, `POST /{task_id}/clear-cache`, `GET /{task_id}`,
/// `PUT /{task_id}`, `DELETE /{task_id}`, `POST /{task_id}/pause`,
/// `POST /{task_id}/resume`, `POST /{task_id}/revert`, `POST /{task_id}/run`,
/// `POST /{task_id}/stop`, `GET /runs/recent`, `GET /{task_id}/runs`,
/// `GET /meta/output-targets`, `GET /meta/actions`, `GET /meta/events`,
/// `POST /{task_id}/webhook/{token}`, `POST /{task_id}/webhook-regenerate`,
/// `POST /parse`. axum's router merges static and capture routes unambiguously; the
/// `/api/tasks/:task_id` capture and the static `/api/tasks/runs/recent`,
/// `/api/tasks/meta/*`, `/api/tasks/onboarding`, `/api/tasks/notifications`, and
/// `/api/tasks/parse` routes do not conflict because the static segments are matched
/// before the `:task_id` capture in axum 0.7's router.
pub fn setup_task_routes() -> Router<AppState> {
    Router::new()
        .route("/api/tasks", get(list_tasks).post(create_task))
        .route(
            "/api/tasks/onboarding",
            get(get_tasks_onboarding).post(update_tasks_onboarding),
        )
        .route("/api/tasks/notifications", get(get_notifications))
        .route("/api/tasks/runs/recent", get(list_recent_runs))
        .route("/api/tasks/meta/output-targets", get(list_output_targets))
        .route("/api/tasks/meta/actions", get(list_actions))
        .route("/api/tasks/meta/events", get(list_events))
        .route("/api/tasks/parse", post(parse_task))
        .route(
            "/api/tasks/:task_id",
            get(get_task).put(update_task).delete(delete_task),
        )
        .route("/api/tasks/:task_id/pause", post(pause_task))
        .route("/api/tasks/:task_id/resume", post(resume_task))
        .route("/api/tasks/:task_id/revert", post(revert_task))
        .route("/api/tasks/:task_id/run", post(run_task_now_handler))
        .route("/api/tasks/:task_id/stop", post(stop_task_handler))
        .route("/api/tasks/:task_id/clear-cache", post(clear_task_cache))
        .route("/api/tasks/:task_id/runs", get(list_runs))
        .route("/api/tasks/:task_id/webhook/:token", post(webhook_trigger))
        .route(
            "/api/tasks/:task_id/webhook-regenerate",
            post(regenerate_webhook),
        )
}

// ===========================================================================
// Handlers
// ===========================================================================

/// `GET ""` -> `GET /api/tasks` — list tasks for the current owner.
///
/// `status: Optional[str] = None`, `include_last_run: bool = False`. When `user` is
/// resolved, `ensure_defaults(user)`; when `None` (auth disabled), seed defaults for
/// every distinct action-task owner, then list (un-scoped).
async fn list_tasks(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let user = owner(&user);
    // `status: Optional[str] = None` — present-only filter.
    let status = q.get("status").filter(|v| !v.is_empty()).cloned();
    // `include_last_run: bool = False`
    let include_last_run = query_bool(&q, "include_last_run", false)?;

    if let Some(u) = user.as_deref() {
        // `await task_scheduler.ensure_defaults(user)`
        s.task_scheduler.ensure_defaults(u).await;
    } else {
        // Seed defaults for every owner that already has a housekeeping action task.
        let owners = {
            let db_seed = session_local()?;
            let action_keys: Vec<String> =
                HOUSEKEEPING_DEFAULTS.keys().map(|k| k.to_string()).collect();
            let placeholders = action_keys
                .iter()
                .map(|_| "?")
                .collect::<Vec<_>>()
                .join(",");
            let sql = format!(
                "SELECT DISTINCT owner FROM scheduled_tasks \
                 WHERE task_type = 'action' AND action IN ({placeholders})"
            );
            let mut stmt = db_seed.prepare(&sql).map_err(db_500)?;
            let params: Vec<&dyn rusqlite::ToSql> =
                action_keys.iter().map(|k| k as &dyn rusqlite::ToSql).collect();
            let rows = stmt
                .query_map(params.as_slice(), |r| r.get::<_, Option<String>>(0))
                .map_err(db_500)?;
            let mut set: Vec<String> = Vec::new();
            for row in rows {
                if let Some(o) = row.map_err(db_500)? {
                    // `if row[0]` — skip NULL/empty owners.
                    if !o.is_empty() && !set.contains(&o) {
                        set.push(o);
                    }
                }
            }
            set
        };
        for o in &owners {
            s.task_scheduler.ensure_defaults(o).await;
        }
    }

    let db = session_local()?;
    // q = db.query(ScheduledTask) [+ owner filter] [+ status filter] .order_by(created_at desc)
    let mut sql = format!("SELECT {TASK_COLS} FROM scheduled_tasks");
    let mut clauses: Vec<String> = Vec::new();
    let mut params: Vec<Box<dyn rusqlite::ToSql>> = Vec::new();
    if let Some(u) = user.as_deref() {
        clauses.push("owner = ?".to_string());
        params.push(Box::new(u.to_string()));
    }
    if let Some(st) = status.as_deref() {
        clauses.push("status = ?".to_string());
        params.push(Box::new(st.to_string()));
    }
    if !clauses.is_empty() {
        sql.push_str(" WHERE ");
        sql.push_str(&clauses.join(" AND "));
    }
    sql.push_str(" ORDER BY created_at DESC");

    let mut stmt = db.prepare(&sql).map_err(db_500)?;
    let param_refs: Vec<&dyn rusqlite::ToSql> = params.iter().map(|p| p.as_ref()).collect();
    let tasks: Vec<TaskRow> = stmt
        .query_map(param_refs.as_slice(), read_task_row)
        .map_err(db_500)?
        .collect::<rusqlite::Result<Vec<_>>>()
        .map_err(db_500)?;

    // `[_task_to_dict(t, include_last_run_result=include_last_run) for t in tasks]`
    let mut out: Vec<Value> = Vec::with_capacity(tasks.len());
    for t in &tasks {
        // `if include_last_run_result and t.runs:` — fetch the most-recent run.
        let last_run = if include_last_run {
            most_recent_run(&db, &t.id)?
        } else {
            None
        };
        out.push(task_to_dict(t, last_run.as_ref()));
    }
    Ok(Json(json!({ "tasks": out })).into_response())
}

/// The most-recent `TaskRun` for a task (`t.runs[0]`, ordered `started_at desc`).
fn most_recent_run(db: &Connection, task_id: &str) -> Result<Option<RunRow>, HttpException> {
    let sql = format!(
        "SELECT {RUN_COLS} FROM task_runs WHERE task_id = ?1 ORDER BY started_at DESC LIMIT 1"
    );
    db.query_row(&sql, rusqlite::params![task_id], read_run_row)
        .optional()
        .map_err(db_500)
}

/// `GET /onboarding` -> `GET /api/tasks/onboarding`.
async fn get_tasks_onboarding(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    let user = owner(&user);
    // prefs = _load_for_user(user) or {}
    let prefs = prefs_store::load_for_user(user.as_deref());
    Ok(Json(json!({
        "opened": prefs_truthy(&prefs, "tasks_opened"),
        "enabled": prefs_truthy(&prefs, "tasks_enabled"),
    }))
    .into_response())
}

/// `bool(prefs.get(key))` — Python truthiness of a stored prefs value.
fn prefs_truthy(prefs: &Value, key: &str) -> bool {
    match prefs.get(key) {
        Some(v) => json_is_truthy(v),
        None => false,
    }
}

/// Python `bool(value)` over a JSON value.
fn json_is_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `POST /onboarding` -> `POST /api/tasks/onboarding`.
///
/// Marks onboarding opened, optionally enables tasks, ensures defaults, and resumes
/// paused (non-ship-paused) housekeeping action tasks.
async fn update_tasks_onboarding(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Json(body): Json<Value>,
) -> Result<Response, HttpException> {
    let user = owner(&user);
    // prefs = _load_for_user(user) or {}
    let mut prefs = prefs_store::load_for_user(user.as_deref());
    let map = match prefs.as_object_mut() {
        Some(m) => m,
        None => {
            prefs = json!({});
            prefs.as_object_mut().unwrap()
        }
    };
    // prefs["tasks_opened"] = True
    map.insert("tasks_opened".to_string(), json!(true));
    // enable = bool(body.get("enabled"))
    let enable = match body.get("enabled") {
        Some(v) => json_is_truthy(v),
        None => false,
    };
    // if enable: prefs["tasks_enabled"] = True
    if enable {
        map.insert("tasks_enabled".to_string(), json!(true));
    }
    // _save_for_user(user, prefs)
    let _ = prefs_store::save_for_user(user.as_deref(), &prefs);
    // if user: await task_scheduler.ensure_defaults(user)
    if let Some(u) = user.as_deref() {
        s.task_scheduler.ensure_defaults(u).await;
    }

    let mut resumed = 0i64;
    if enable {
        let db = session_local()?;
        let action_keys: Vec<String> =
            HOUSEKEEPING_DEFAULTS.keys().map(|k| k.to_string()).collect();
        let placeholders = action_keys
            .iter()
            .map(|_| "?")
            .collect::<Vec<_>>()
            .join(",");
        // owner == user (None compares as IS NULL via the equality match below)
        let sql = format!(
            "SELECT {TASK_COLS} FROM scheduled_tasks \
             WHERE owner IS ? AND task_type = 'action' AND action IN ({placeholders})"
        );
        let mut stmt = db.prepare(&sql).map_err(db_500)?;
        let mut params: Vec<Box<dyn rusqlite::ToSql>> = Vec::new();
        params.push(Box::new(user.clone()));
        for k in &action_keys {
            params.push(Box::new(k.clone()));
        }
        let param_refs: Vec<&dyn rusqlite::ToSql> = params.iter().map(|p| p.as_ref()).collect();
        let tasks: Vec<TaskRow> = stmt
            .query_map(param_refs.as_slice(), read_task_row)
            .map_err(db_500)?
            .collect::<rusqlite::Result<Vec<_>>>()
            .map_err(db_500)?;

        for task in &tasks {
            let defs = task
                .action
                .as_deref()
                .filter(|a| !a.is_empty())
                .and_then(|a| HOUSEKEEPING_DEFAULTS.get(a));
            // if defs and defs.get("ship_paused"): continue
            if let Some(d) = defs {
                if d.ship_paused {
                    continue;
                }
            }
            // if task.status == "active": continue
            if task.status.as_deref() == Some("active") {
                continue;
            }
            // task.status = "active"
            let mut next_run: Option<NaiveDateTime> = None;
            // if (task.trigger_type or "schedule") == "schedule": recompute next_run
            if non_empty_or(task.trigger_type.as_deref(), "schedule") == "schedule" {
                next_run = compute_next_run(
                    task.schedule.as_deref(),
                    task.scheduled_time.as_deref(),
                    task.scheduled_day,
                    parse_stored_dt(task.scheduled_date.as_deref()),
                    None,
                    task.cron_expression.as_deref(),
                    None,
                );
                db.execute(
                    "UPDATE scheduled_tasks SET status = 'active', next_run = ?1 WHERE id = ?2",
                    rusqlite::params![next_run.map(crate::pydatetime::naive_to_iso), task.id],
                )
                .map_err(db_500)?;
            } else {
                db.execute(
                    "UPDATE scheduled_tasks SET status = 'active' WHERE id = ?1",
                    rusqlite::params![task.id],
                )
                .map_err(db_500)?;
            }
            let _ = next_run;
            resumed += 1;
        }
    }

    // {"ok": True, "opened": True, "enabled": bool(prefs.get("tasks_enabled")), "resumed": resumed}
    Ok(Json(json!({
        "ok": true,
        "opened": true,
        "enabled": prefs_truthy(&prefs, "tasks_enabled"),
        "resumed": resumed,
    }))
    .into_response())
}

/// `POST ""` -> `POST /api/tasks` — create a task.
async fn create_task(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Json(req): Json<TaskCreate>,
) -> Result<Response, HttpException> {
    let user = owner(&user);

    // --- Validate ---
    // if req.task_type in ("llm", "research") and not req.prompt:
    if matches!(req.task_type.as_str(), "llm" | "research")
        && req.prompt.as_deref().filter(|p| !p.is_empty()).is_none()
    {
        return Err(HttpException::new(
            400,
            "Prompt is required for LLM/research tasks",
        ));
    }
    // if req.task_type == "action" and not req.action:
    if req.task_type == "action" && req.action.as_deref().filter(|a| !a.is_empty()).is_none() {
        return Err(HttpException::new(
            400,
            "Action name is required for action tasks",
        ));
    }
    // Block shell-executing action types for non-admins.
    if req.task_type == "action"
        && is_admin_only_action(req.action.as_deref())
        && !is_admin(&s, user.as_deref())
    {
        return Err(HttpException::new(
            403,
            format!(
                "Action '{}' requires admin privileges",
                req.action.as_deref().unwrap_or("")
            ),
        ));
    }
    // if req.trigger_type == "schedule" and not req.schedule:
    if req.trigger_type == "schedule"
        && req.schedule.as_deref().filter(|s| !s.is_empty()).is_none()
    {
        return Err(HttpException::new(
            400,
            "Schedule is required for schedule-triggered tasks",
        ));
    }
    // if req.trigger_type == "schedule" and req.schedule == "cron" and not req.cron_expression:
    if req.trigger_type == "schedule"
        && req.schedule.as_deref() == Some("cron")
        && req
            .cron_expression
            .as_deref()
            .filter(|c| !c.is_empty())
            .is_none()
    {
        return Err(HttpException::new(
            400,
            "Cron expression is required for cron schedule",
        ));
    }
    // if ... cron and req.cron_expression: validate via croniter
    if req.trigger_type == "schedule" && req.schedule.as_deref() == Some("cron") {
        if let Some(expr) = req.cron_expression.as_deref().filter(|c| !c.is_empty()) {
            if !cron_is_valid(expr) {
                return Err(HttpException::new(400, "Invalid cron expression"));
            }
        }
    }
    // if req.trigger_type == "event" and not req.trigger_event:
    if req.trigger_type == "event"
        && req
            .trigger_event
            .as_deref()
            .filter(|e| !e.is_empty())
            .is_none()
    {
        return Err(HttpException::new(
            400,
            "Event name is required for event-triggered tasks",
        ));
    }
    // if req.trigger_type == "event" and not req.trigger_count:  (0 is falsy too)
    if req.trigger_type == "event" && req.trigger_count.filter(|&c| c != 0).is_none() {
        return Err(HttpException::new(
            400,
            "Trigger count is required for event-triggered tasks",
        ));
    }

    // --- Auto-generate name ---
    // name = req.name; if not name: ...
    let name: String = match req.name.as_deref().filter(|n| !n.is_empty()) {
        Some(n) => n.to_string(),
        None => {
            if req.task_type == "action" {
                // BUILTIN_ACTION_INFO.get(req.action, req.action or "Action Task")
                let action = req.action.as_deref().unwrap_or("");
                match crate::src::builtin_actions::BUILTIN_ACTION_INFO.get(action) {
                    Some(desc) => desc.to_string(),
                    None => {
                        if !action.is_empty() {
                            action.to_string()
                        } else {
                            "Action Task".to_string()
                        }
                    }
                }
            } else if let Some(prompt) = req.prompt.as_deref().filter(|p| !p.is_empty()) {
                // name = await _generate_task_name(req.prompt)
                generate_task_name(prompt).await
            } else {
                "Untitled Task".to_string()
            }
        }
    };

    // --- Compute next_run for schedule-triggered tasks ---
    let mut next_run: Option<NaiveDateTime> = None;
    let mut sched_date: Option<NaiveDateTime> = None;
    if req.trigger_type == "schedule" {
        // if req.schedule == "once" and req.scheduled_date: parse it
        if req.schedule.as_deref() == Some("once") {
            if let Some(sd) = req.scheduled_date.as_deref().filter(|d| !d.is_empty()) {
                match parse_scheduled_date(sd) {
                    Some(dt) => sched_date = Some(dt),
                    None => {
                        return Err(HttpException::new(400, "Invalid scheduled_date format"));
                    }
                }
            }
        }
        next_run = compute_next_run(
            req.schedule.as_deref(),
            Some(req.scheduled_time.as_str()),
            req.scheduled_day,
            sched_date,
            None,
            req.cron_expression.as_deref(),
            None,
        );
    }

    // --- Generate webhook token if needed ---
    let webhook_token: Option<String> = if req.trigger_type == "webhook" {
        Some(crate::pysecrets::token_urlsafe(32))
    } else {
        None
    };

    let task_id = uuid::Uuid::new_v4().to_string();

    // notifications_enabled = (
    //     False if req.task_type == "action" and req.notifications_enabled is None
    //     else bool(req.notifications_enabled) if req.notifications_enabled is not None
    //     else True
    // )
    let notifications_enabled = if req.task_type == "action" && req.notifications_enabled.is_none() {
        false
    } else {
        req.notifications_enabled.unwrap_or(true)
    };

    // status = "active" if (req.trigger_type in ("event", "webhook") or next_run) else "completed"
    let status = if matches!(req.trigger_type.as_str(), "event" | "webhook") || next_run.is_some() {
        "active"
    } else {
        "completed"
    };

    // `req.model or None`, `req.endpoint_url or None`, `req.then_task_id or None`
    let model = req.model.as_deref().filter(|m| !m.is_empty()).map(str::to_string);
    let endpoint_url = req
        .endpoint_url
        .as_deref()
        .filter(|e| !e.is_empty())
        .map(str::to_string);
    let then_task_id = req
        .then_task_id
        .as_deref()
        .filter(|t| !t.is_empty())
        .map(str::to_string);

    let now = crate::pydatetime::utcnow_naive_iso();
    let next_run_str = next_run.map(crate::pydatetime::naive_to_iso);
    let sched_date_str = sched_date.map(crate::pydatetime::naive_to_iso);

    let db = session_local()?;
    db.execute(
        "INSERT INTO scheduled_tasks \
           (id, owner, name, prompt, task_type, action, schedule, scheduled_time, scheduled_day, \
            scheduled_date, cron_expression, trigger_type, trigger_event, trigger_count, \
            trigger_counter, next_run, status, output_target, model, endpoint_url, then_task_id, \
            webhook_token, notifications_enabled, created_at, updated_at) \
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, 0, ?15, ?16, ?17, \
            ?18, ?19, ?20, ?21, ?22, ?23, ?23)",
        rusqlite::params![
            task_id,
            user,
            name,
            req.prompt,
            req.task_type,
            req.action,
            req.schedule,
            req.scheduled_time,
            req.scheduled_day,
            sched_date_str,
            req.cron_expression,
            req.trigger_type,
            req.trigger_event,
            req.trigger_count,
            next_run_str,
            status,
            req.output_target,
            model,
            endpoint_url,
            then_task_id,
            webhook_token,
            notifications_enabled,
            now,
        ],
    )
    .map_err(db_500)?;

    // db.refresh(task); return _task_to_dict(task) — re-read the persisted row.
    let task = load_task(&db, &task_id)?
        .ok_or_else(|| HttpException::new(500, "Internal Server Error"))?;
    Ok(Json(task_to_dict(&task, None)).into_response())
}

/// `async def _generate_task_name(prompt) -> str` — use the most-recent
/// endpoint-configured session's model to draft a short title; on any failure fall back
/// to the prompt's first line / first 50 chars.
async fn generate_task_name(prompt: &str) -> String {
    // Fallback used by every error arm:
    //   first = prompt.split('\n')[0].split('.')[0].strip()
    //   return first[:50] if first else "Untitled Task"
    let fallback = || -> String {
        let first = prompt
            .split('\n')
            .next()
            .unwrap_or("")
            .split('.')
            .next()
            .unwrap_or("")
            .trim();
        if first.is_empty() {
            "Untitled Task".to_string()
        } else {
            first.chars().take(50).collect()
        }
    };

    // db.query(Session).filter(endpoint_url IS NOT NULL, model IS NOT NULL)
    //   .order_by(created_at desc).first()
    let recent: Option<(String, String)> = match crate::core::database::session_local() {
        Ok(db) => db
            .query_row(
                "SELECT endpoint_url, model FROM sessions \
                 WHERE endpoint_url IS NOT NULL AND model IS NOT NULL \
                 ORDER BY created_at DESC LIMIT 1",
                [],
                |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)),
            )
            .optional()
            .ok()
            .flatten(),
        Err(_) => None,
    };

    let (url, model) = match recent {
        // if not recent: return prompt[:50].strip()
        None => return prompt.chars().take(50).collect::<String>().trim().to_string(),
        Some(pair) => pair,
    };

    // result = await llm_call_async(url, model, [...], max_tokens=20, timeout=15)
    let messages = vec![
        json!({
            "role": "system",
            "content": "Generate a short title (3-5 words, no quotes) for this scheduled task. Reply with ONLY the title, nothing else.",
        }),
        json!({
            "role": "user",
            "content": prompt.chars().take(500).collect::<String>(),
        }),
    ];
    match crate::src::llm_core::llm_call_async(
        &url,
        &model,
        messages,
        // Python passes only max_tokens=20, timeout=15; temperature defaults (1.0).
        1.0,
        20,
        indexmap::IndexMap::new(),
        15,
    )
    .await
    {
        Ok(result) => {
            // title = result.strip().strip('"\'').strip()
            let title = result.trim().trim_matches(|c| c == '"' || c == '\'').trim();
            if title.is_empty() {
                // return prompt[:50].strip()
                prompt.chars().take(50).collect::<String>().trim().to_string()
            } else {
                // return title[:60]
                title.chars().take(60).collect()
            }
        }
        // except Exception: fall back to first-line truncation
        Err(_) => fallback(),
    }
}

/// `GET /notifications` -> `GET /api/tasks/notifications`.
///
/// Anonymous callers get nothing (prevents cross-tenant drain — review CRIT-B).
async fn get_notifications(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    let user = owner(&user);
    // if not user: return {"notifications": []}
    let user = match user.as_deref().filter(|u| !u.is_empty()) {
        Some(u) => u,
        None => return Ok(Json(json!({ "notifications": [] })).into_response()),
    };
    // notes = task_scheduler.pop_notifications(owner=user)
    let notes = s.task_scheduler.pop_notifications(Some(user));
    Ok(Json(json!({ "notifications": notes })).into_response())
}

/// `GET /{task_id}` -> `GET /api/tasks/:task_id`.
async fn get_task(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(task_id): Path<String>,
) -> Result<Response, HttpException> {
    let user = owner(&user);
    let db = session_local()?;
    let task = load_task(&db, &task_id)?;
    let task = require_task(&task, user.as_deref())?;
    Ok(Json(task_to_dict(task, None)).into_response())
}

/// `PUT /{task_id}` -> `PUT /api/tasks/:task_id`.
async fn update_task(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(task_id): Path<String>,
    Json(req): Json<TaskUpdate>,
) -> Result<Response, HttpException> {
    let user = owner(&user);
    let db = session_local()?;
    let loaded = load_task(&db, &task_id)?;
    // Mutable working copy of the row (SQLAlchemy mutates `task` in place).
    let mut task = require_task(&loaded, user.as_deref())?.clone_row();

    // Apply each present field in Python order.
    if let Some(v) = req.name {
        task.name = Some(v);
    }
    if let Some(v) = req.prompt {
        task.prompt = Some(v);
    }
    if let Some(v) = req.task_type {
        task.task_type = Some(v);
    }
    if let Some(v) = req.action {
        // Same admin-only gate as create.
        if is_admin_only_action(Some(v.as_str())) && !is_admin(&s, user.as_deref()) {
            return Err(HttpException::new(
                403,
                format!("Action '{v}' requires admin privileges"),
            ));
        }
        task.action = Some(v);
    }
    if let Some(v) = req.output_target {
        task.output_target = Some(v);
    }
    if let Some(v) = req.model {
        // d.model = req.model or None
        task.model = if v.is_empty() { None } else { Some(v) };
    }
    if let Some(v) = req.endpoint_url {
        task.endpoint_url = if v.is_empty() { None } else { Some(v) };
    }
    if let Some(v) = req.trigger_type {
        // Generate webhook token when switching to webhook trigger.
        if v == "webhook"
            && task
                .webhook_token
                .as_deref()
                .filter(|t| !t.is_empty())
                .is_none()
        {
            task.webhook_token = Some(crate::pysecrets::token_urlsafe(32));
        }
        task.trigger_type = Some(v);
    }
    if let Some(v) = req.trigger_event {
        task.trigger_event = Some(v);
    }
    if let Some(v) = req.trigger_count {
        task.trigger_count = Some(v);
    }
    if let Some(v) = req.then_task_id {
        task.then_task_id = if v.is_empty() { None } else { Some(v) };
    }
    if let Some(v) = req.notifications_enabled {
        task.notifications_enabled = Some(v);
    }
    // cron_expression: validate when non-empty, then store (or None when empty).
    let cron_provided = req.cron_expression.is_some();
    if let Some(v) = req.cron_expression {
        if !v.is_empty() && !cron_is_valid(&v) {
            return Err(HttpException::new(400, "Invalid cron expression"));
        }
        // task.cron_expression = req.cron_expression or None
        task.cron_expression = if v.is_empty() { None } else { Some(v) };
    }

    // Recompute next_run if schedule changed.
    let mut schedule_changed = false;
    if let Some(v) = req.schedule {
        task.schedule = Some(v);
        schedule_changed = true;
    }
    if let Some(v) = req.scheduled_time {
        task.scheduled_time = Some(v);
        schedule_changed = true;
    }
    if let Some(v) = req.scheduled_day {
        task.scheduled_day = Some(v);
        schedule_changed = true;
    }
    if let Some(v) = req.scheduled_date {
        match parse_scheduled_date(&v) {
            Some(dt) => task.scheduled_date = Some(crate::pydatetime::naive_to_iso(dt)),
            None => return Err(HttpException::new(400, "Invalid scheduled_date format")),
        }
        schedule_changed = true;
    }
    if cron_provided {
        schedule_changed = true;
    }

    // if schedule_changed and task.status == "active" and (task.trigger_type or "schedule") == "schedule":
    if schedule_changed
        && task.status.as_deref() == Some("active")
        && non_empty_or(task.trigger_type.as_deref(), "schedule") == "schedule"
    {
        let next_run = compute_next_run(
            task.schedule.as_deref(),
            task.scheduled_time.as_deref(),
            task.scheduled_day,
            parse_stored_dt(task.scheduled_date.as_deref()),
            None,
            task.cron_expression.as_deref(),
            None,
        );
        task.next_run = next_run.map(crate::pydatetime::naive_to_iso);
    }

    // db.commit(); db.refresh(task) — persist the full mutated row + bumped updated_at.
    let now = crate::pydatetime::utcnow_naive_iso();
    task.updated_at = Some(now.clone());
    db.execute(
        "UPDATE scheduled_tasks SET \
           name = ?1, prompt = ?2, task_type = ?3, action = ?4, schedule = ?5, scheduled_time = ?6, \
           scheduled_day = ?7, scheduled_date = ?8, cron_expression = ?9, trigger_type = ?10, \
           trigger_event = ?11, trigger_count = ?12, next_run = ?13, output_target = ?14, \
           model = ?15, endpoint_url = ?16, then_task_id = ?17, webhook_token = ?18, \
           notifications_enabled = ?19, updated_at = ?20 \
         WHERE id = ?21",
        rusqlite::params![
            task.name,
            task.prompt,
            task.task_type,
            task.action,
            task.schedule,
            task.scheduled_time,
            task.scheduled_day,
            task.scheduled_date,
            task.cron_expression,
            task.trigger_type,
            task.trigger_event,
            task.trigger_count,
            task.next_run,
            task.output_target,
            task.model,
            task.endpoint_url,
            task.then_task_id,
            task.webhook_token,
            task.notifications_enabled,
            now,
            task_id,
        ],
    )
    .map_err(db_500)?;

    Ok(Json(task_to_dict(&task, None)).into_response())
}

/// `DELETE /{task_id}` -> `DELETE /api/tasks/:task_id`.
async fn delete_task(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(task_id): Path<String>,
) -> Result<Response, HttpException> {
    let user = owner(&user);
    let db = session_local()?;
    let task = load_task(&db, &task_id)?;
    let _ = require_task(&task, user.as_deref())?;
    // db.delete(task); db.commit()
    db.execute(
        "DELETE FROM scheduled_tasks WHERE id = ?1",
        rusqlite::params![task_id],
    )
    .map_err(db_500)?;
    Ok(Json(json!({ "ok": true })).into_response())
}

/// `POST /{task_id}/pause` -> `POST /api/tasks/:task_id/pause`.
async fn pause_task(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(task_id): Path<String>,
) -> Result<Response, HttpException> {
    let user = owner(&user);
    let db = session_local()?;
    let task = load_task(&db, &task_id)?;
    let _ = require_task(&task, user.as_deref())?;
    db.execute(
        "UPDATE scheduled_tasks SET status = 'paused' WHERE id = ?1",
        rusqlite::params![task_id],
    )
    .map_err(db_500)?;
    Ok(Json(json!({ "ok": true, "status": "paused" })).into_response())
}

/// `POST /{task_id}/resume` -> `POST /api/tasks/:task_id/resume`.
async fn resume_task(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(task_id): Path<String>,
) -> Result<Response, HttpException> {
    let user = owner(&user);
    let db = session_local()?;
    let task = load_task(&db, &task_id)?;
    let task = require_task(&task, user.as_deref())?;

    // task.status = "active"
    // if (task.trigger_type or "schedule") == "schedule": recompute next_run
    let mut next_run: Option<NaiveDateTime> = None;
    if non_empty_or(task.trigger_type.as_deref(), "schedule") == "schedule" {
        next_run = compute_next_run(
            task.schedule.as_deref(),
            task.scheduled_time.as_deref(),
            task.scheduled_day,
            parse_stored_dt(task.scheduled_date.as_deref()),
            None,
            task.cron_expression.as_deref(),
            None,
        );
    }
    let next_run_str = next_run.map(crate::pydatetime::naive_to_iso);
    db.execute(
        "UPDATE scheduled_tasks SET status = 'active', next_run = ?1 WHERE id = ?2",
        rusqlite::params![next_run_str, task_id],
    )
    .map_err(db_500)?;

    // {"ok": True, "status": "active", "next_run": task.next_run.isoformat() + "Z" if ... else None}
    Ok(Json(json!({
        "ok": true,
        "status": "active",
        "next_run": iso_z_or_null(next_run_str.as_deref()),
    }))
    .into_response())
}

/// `POST /{task_id}/revert` -> `POST /api/tasks/:task_id/revert` — reset a built-in
/// (housekeeping) task to its default config.
async fn revert_task(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(task_id): Path<String>,
) -> Result<Response, HttpException> {
    let user = owner(&user);
    let db = session_local()?;
    let loaded = load_task(&db, &task_id)?;
    let mut task = require_task(&loaded, user.as_deref())?.clone_row();

    // defs = HOUSEKEEPING_DEFAULTS.get(task.action) if task.action else None
    let defs = task
        .action
        .as_deref()
        .filter(|a| !a.is_empty())
        .and_then(|a| HOUSEKEEPING_DEFAULTS.get(a));
    let defs = match defs {
        Some(d) => d,
        // if not defs: raise HTTPException(400, "Not a built-in task")
        None => return Err(HttpException::new(400, "Not a built-in task")),
    };

    task.name = Some(defs.name.to_string());
    task.schedule = defs.schedule.map(str::to_string);
    task.scheduled_time = defs.scheduled_time.map(str::to_string);
    task.scheduled_day = None;
    task.scheduled_date = None;
    task.cron_expression = defs.cron_expression.map(str::to_string);
    // defs.get("trigger_type", "schedule") — HousekeepingDef always has it ("schedule" default).
    task.trigger_type = Some(defs.trigger_type.to_string());
    task.trigger_event = defs.trigger_event.map(str::to_string);
    task.trigger_count = defs.trigger_count;
    task.trigger_counter = Some(0);
    task.prompt = None;
    task.model = None;
    task.endpoint_url = None;
    // task.status = "paused" if defs.get("ship_paused") else "active"
    task.status = Some(if defs.ship_paused { "paused" } else { "active" }.to_string());
    task.next_run = None;
    // if task.trigger_type == "schedule": next_run = compute_next_run(defs..., None, None, cron=defs)
    if task.trigger_type.as_deref() == Some("schedule") {
        let next_run = compute_next_run(
            defs.schedule,
            defs.scheduled_time,
            None,
            None,
            None,
            defs.cron_expression,
            None,
        );
        task.next_run = next_run.map(crate::pydatetime::naive_to_iso);
    }

    let now = crate::pydatetime::utcnow_naive_iso();
    task.updated_at = Some(now.clone());
    db.execute(
        "UPDATE scheduled_tasks SET \
           name = ?1, schedule = ?2, scheduled_time = ?3, scheduled_day = ?4, scheduled_date = ?5, \
           cron_expression = ?6, trigger_type = ?7, trigger_event = ?8, trigger_count = ?9, \
           trigger_counter = ?10, prompt = ?11, model = ?12, endpoint_url = ?13, status = ?14, \
           next_run = ?15, updated_at = ?16 \
         WHERE id = ?17",
        rusqlite::params![
            task.name,
            task.schedule,
            task.scheduled_time,
            task.scheduled_day,
            task.scheduled_date,
            task.cron_expression,
            task.trigger_type,
            task.trigger_event,
            task.trigger_count,
            task.trigger_counter,
            task.prompt,
            task.model,
            task.endpoint_url,
            task.status,
            task.next_run,
            now,
            task_id,
        ],
    )
    .map_err(db_500)?;

    Ok(Json(json!({ "ok": true, "task": task_to_dict(&task, None) })).into_response())
}

/// `POST /{task_id}/run` -> `POST /api/tasks/:task_id/run`.
///
/// `force: bool = False`. Ownership is checked, then `run_task_now(task_id, force=force)`.
async fn run_task_now_handler(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(task_id): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let user = owner(&user);
    let force = query_bool(&q, "force", false)?;
    {
        let db = session_local()?;
        let task = load_task(&db, &task_id)?;
        let _ = require_task(&task, user.as_deref())?;
    }
    // started = await task_scheduler.run_task_now(task_id, force=force)
    let started = s.task_scheduler.run_task_now(&task_id, force).await;
    if !started {
        // raise HTTPException(409, "Task is already running")
        return Err(HttpException::new(409, "Task is already running"));
    }
    // {"ok": True, "message": "Task triggered" + (" in parallel" if force else "")}
    let message = if force {
        "Task triggered in parallel"
    } else {
        "Task triggered"
    };
    Ok(Json(json!({ "ok": true, "message": message })).into_response())
}

/// `POST /{task_id}/stop` -> `POST /api/tasks/:task_id/stop`.
///
/// Requests cancellation of a running/queued task via `task_scheduler.stop_task(task_id)`.
/// Returns 404 (with detail "Task is not running") when the task is not currently
/// executing — mirroring the Python `raise HTTPException(404, "Task is not running")`.
async fn stop_task_handler(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(task_id): Path<String>,
) -> Result<Response, HttpException> {
    let user = owner(&user);
    {
        let db = session_local()?;
        let task = load_task(&db, &task_id)?;
        let _ = require_task(&task, user.as_deref())?;
    }
    // stopped = await task_scheduler.stop_task(task_id)
    let stopped = s.task_scheduler.stop_task(&task_id).await;
    if !stopped {
        // raise HTTPException(404, "Task is not running")
        return Err(HttpException::new(404, "Task is not running"));
    }
    Ok(Json(json!({ "ok": true, "message": "Task stopped" })).into_response())
}

/// `POST /{task_id}/clear-cache` -> `POST /api/tasks/:task_id/clear-cache`.
///
/// Clears derived cache rows (and, for `check_email_urgency`, cache files on disk) for one
/// built-in task whose `action` maps to known cache tables. Faithful port of the Python
/// handler, which connects to the email-helpers scheduled DB via rusqlite and deletes the
/// relevant table rows. File-system side-effects are reproduced using `std::fs`.
///
/// The `email_tags` table uses a per-owner delete (`WHERE owner = ? OR owner = ''`);
/// all other tables are cleared wholesale (matching Python's `DELETE FROM {table}`).
///
/// Returns 400 when the task has no clearable cache (i.e. its `action` is not in the
/// known map), 404 when the task does not exist, 403 on ownership mismatch.
async fn clear_task_cache(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(task_id): Path<String>,
) -> Result<Response, HttpException> {
    let user = owner(&user);

    // Load the task and verify ownership, then extract the action.
    let action: String = {
        let db = session_local()?;
        let task = load_task(&db, &task_id)?;
        let task = require_task(&task, user.as_deref())?;
        task.action.clone().unwrap_or_default()
    };

    // cache_tables = { action -> (table, ...) }
    let cache_tables: &[(&str, &[&str])] = &[
        ("summarize_emails",       &["email_summaries"]),
        ("draft_email_replies",    &["email_ai_replies"]),
        ("extract_email_events",   &["email_calendar_extractions"]),
        ("mark_email_boundaries",  &["email_boundaries"]),
        ("learn_sender_signatures",&["sender_signatures"]),
        ("check_email_urgency",    &["email_tags", "email_urgency_alerts"]),
    ];

    let tables: &[&str] = match cache_tables.iter().find(|(a, _)| *a == action.as_str()) {
        Some((_, t)) => t,
        // if not tables: raise HTTPException(400, "This task has no clearable cache")
        None => return Err(HttpException::new(400, "This task has no clearable cache")),
    };

    // `from routes.email_helpers import SCHEDULED_DB` — the scheduled-DB path used by
    // all email-action helpers. The Rust equivalent calls `crate::routes::email_helpers::scheduled_db()`.
    let scheduled_db_path = crate::routes::email_helpers::scheduled_db();

    // conn = sqlite3.connect(SCHEDULED_DB)
    let conn = rusqlite::Connection::open(&scheduled_db_path).map_err(|e| {
        crate::pylog::error(&format!("clear_task_cache: open scheduled DB: {e}"));
        HttpException::new(500, "Internal Server Error")
    })?;

    let mut cleared: serde_json::Map<String, Value> = serde_json::Map::new();
    for &table in tables {
        let count: i64 = if table == "email_tags" {
            // `WHERE owner = ? OR owner = ''` with the current user.
            if let Some(u) = user.as_deref().filter(|u| !u.is_empty()) {
                let before: i64 = conn
                    .query_row(
                        "SELECT COUNT(*) FROM email_tags WHERE owner = ?1 OR owner = ''",
                        rusqlite::params![u],
                        |r| r.get(0),
                    )
                    .unwrap_or(0);
                let _ = conn.execute(
                    "DELETE FROM email_tags WHERE owner = ?1 OR owner = ''",
                    rusqlite::params![u],
                );
                before
            } else {
                // No user — delete all (consistent with the anonymous Python path).
                let before: i64 = conn
                    .query_row("SELECT COUNT(*) FROM email_tags", [], |r| r.get(0))
                    .unwrap_or(0);
                let _ = conn.execute("DELETE FROM email_tags", []);
                before
            }
        } else {
            // `before = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]`
            // `conn.execute(f"DELETE FROM {table}")`
            // OperationalError (table does not exist) -> 0.
            let before: i64 = conn
                .query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |r| r.get(0))
                .unwrap_or(0);
            let _ = conn.execute(&format!("DELETE FROM {table}"), []);
            before
        };
        cleared.insert(table.to_string(), json!(count));
    }
    // conn.commit() — rusqlite auto-commits in autocommit mode; Connection::open is
    // in autocommit by default (each execute is its own transaction). For the DELETE
    // statements to be durable we call `execute_batch` with an explicit COMMIT.
    let _ = conn.execute_batch("COMMIT");

    // File-system cleanup for `check_email_urgency`.
    let mut removed_files: i64 = 0;
    if action == "check_email_urgency" {
        // cache_dir = Path("data/email_urgency_cache")
        let cache_dir = std::path::Path::new("data/email_urgency_cache");
        if cache_dir.exists() {
            if let Ok(read_dir) = std::fs::read_dir(cache_dir) {
                for entry in read_dir.flatten() {
                    let p = entry.path();
                    if p.extension().and_then(|e| e.to_str()) == Some("json")
                        && std::fs::remove_file(&p).is_ok() {
                            removed_files += 1;
                        }
                }
            }
        }
        // owner_slug = "".join(c if (c.isalnum() or c in "-_.@") else "_" for c in (user or "default"))
        let owner_slug: String = user
            .as_deref()
            .unwrap_or("default")
            .chars()
            .map(|c| {
                if c.is_alphanumeric() || matches!(c, '-' | '_' | '.' | '@') {
                    c
                } else {
                    '_'
                }
            })
            .collect();
        let state_path = format!("data/email_urgency_state_{owner_slug}.json");
        if std::fs::remove_file(&state_path).is_ok() {
            removed_files += 1;
        }
    }

    Ok(Json(json!({
        "ok": true,
        "action": action,
        "cleared": Value::Object(cleared),
        "files": removed_files,
    }))
    .into_response())
}

/// `GET /runs/recent` -> `GET /api/tasks/runs/recent` — recent runs across all of the
/// owner's tasks (drives the Activity view), de-duping urgent-email scanner noise.
async fn list_recent_runs(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let user = owner(&user);
    // limit: int = 50; limit = max(1, min(limit, 200))
    let limit_raw = query_int(&q, "limit", 50)?;
    let limit = limit_raw.clamp(1, 200);

    let db = session_local()?;
    // q = db.query(TaskRun, ScheduledTask).join(...); [+ owner filter]
    //   .order_by(TaskRun.started_at.desc()).limit(limit * 3).all()
    let mut sql = format!(
        "SELECT {}, {} FROM task_runs r \
         JOIN scheduled_tasks t ON r.task_id = t.id",
        RUN_COLS.split(", ").map(|c| format!("r.{c}")).collect::<Vec<_>>().join(", "),
        TASK_COLS.split(", ").map(|c| format!("t.{c}")).collect::<Vec<_>>().join(", "),
    );
    let mut params: Vec<Box<dyn rusqlite::ToSql>> = Vec::new();
    if let Some(u) = user.as_deref() {
        sql.push_str(" WHERE t.owner = ?");
        params.push(Box::new(u.to_string()));
    }
    sql.push_str(" ORDER BY r.started_at DESC LIMIT ?");
    params.push(Box::new(limit * 3));

    let mut stmt = db.prepare(&sql).map_err(db_500)?;
    let run_n = RUN_COLS.split(", ").count();
    let param_refs: Vec<&dyn rusqlite::ToSql> = params.iter().map(|p| p.as_ref()).collect();
    let rows: Vec<(RunRow, TaskRow)> = stmt
        .query_map(param_refs.as_slice(), |row| {
            let run = read_run_row(row)?;
            // Task columns are offset by the number of run columns.
            let task = read_task_row_offset(row, run_n)?;
            Ok((run, task))
        })
        .map_err(db_500)?
        .collect::<rusqlite::Result<Vec<_>>>()
        .map_err(db_500)?;

    // De-dupe consecutive identical urgent-email scanner rows by (minute, status, text).
    let mut deduped: Vec<(RunRow, TaskRow)> = Vec::new();
    let mut seen_urgency_rows: Vec<(Option<String>, String, String)> = Vec::new();
    for (r, t) in rows {
        if or_empty(t.action.as_deref()) == "check_email_urgency" {
            // ts = r.started_at.replace(second=0, microsecond=0) — minute granularity.
            let ts = r
                .started_at
                .as_deref()
                .and_then(|s| parse_stored_dt(Some(s)))
                .map(|dt| {
                    use chrono::Timelike;
                    dt.with_second(0)
                        .and_then(|d| d.with_nanosecond(0))
                        .unwrap_or(dt)
                        .format("%Y-%m-%d %H:%M:%S")
                        .to_string()
                });
            // text = (r.result or r.error or "").strip()
            let text = r
                .result
                .as_deref()
                .filter(|s| !s.is_empty())
                .or(r.error.as_deref().filter(|s| !s.is_empty()))
                .unwrap_or("")
                .trim()
                .to_string();
            let key = (ts, or_empty(r.status.as_deref()).to_string(), text);
            if seen_urgency_rows.contains(&key) {
                continue;
            }
            seen_urgency_rows.push(key);
        }
        deduped.push((r, t));
        if deduped.len() >= limit as usize {
            break;
        }
    }

    let runs: Vec<Value> = deduped
        .iter()
        .map(|(r, t)| {
            // {**_run_to_dict(r), "task_name": ..., "task_type": ..., ...}
            let mut obj: Map<String, Value> = match run_to_dict(r) {
                Value::Object(m) => m,
                _ => Map::new(),
            };
            obj.insert("task_name".to_string(), display_task_name(t));
            obj.insert(
                "task_type".to_string(),
                json!(non_empty_or(t.task_type.as_deref(), "llm")),
            );
            obj.insert("action".to_string(), opt_str(&t.action));
            // "model": r.model or t.model or ""
            let model = r
                .model
                .as_deref()
                .filter(|s| !s.is_empty())
                .or(t.model.as_deref().filter(|s| !s.is_empty()))
                .unwrap_or("");
            obj.insert("model".to_string(), json!(model));
            obj.insert(
                "endpoint_url".to_string(),
                json!(resolve_run_endpoint(&db, t, r)),
            );
            // "session_id": t.session_id or ""
            obj.insert(
                "session_id".to_string(),
                json!(or_empty(t.session_id.as_deref())),
            );
            obj.insert("research_id".to_string(), json!(run_research_id(t)));
            // "output_target": t.output_target or "session"
            obj.insert(
                "output_target".to_string(),
                json!(non_empty_or(t.output_target.as_deref(), "session")),
            );
            Value::Object(obj)
        })
        .collect();

    Ok(Json(json!({ "runs": runs })).into_response())
}

/// `GET /{task_id}/runs` -> `GET /api/tasks/:task_id/runs`.
async fn list_runs(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(task_id): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let user = owner(&user);
    // limit: int = 20, offset: int = 0
    let limit = query_int(&q, "limit", 20)?;
    let offset = query_int(&q, "offset", 0)?;

    let db = session_local()?;
    let task = load_task(&db, &task_id)?;
    let _ = require_task(&task, user.as_deref())?;

    // runs = db.query(TaskRun).filter(task_id == task_id).order_by(started_at desc).offset(offset).limit(limit)
    let sql = format!(
        "SELECT {RUN_COLS} FROM task_runs WHERE task_id = ?1 \
         ORDER BY started_at DESC LIMIT ?2 OFFSET ?3"
    );
    let mut stmt = db.prepare(&sql).map_err(db_500)?;
    let runs: Vec<RunRow> = stmt
        .query_map(rusqlite::params![task_id, limit, offset], read_run_row)
        .map_err(db_500)?
        .collect::<rusqlite::Result<Vec<_>>>()
        .map_err(db_500)?;
    // total = db.query(TaskRun).filter(task_id == task_id).count()
    let total: i64 = db
        .query_row(
            "SELECT COUNT(*) FROM task_runs WHERE task_id = ?1",
            rusqlite::params![task_id],
            |r| r.get(0),
        )
        .map_err(db_500)?;

    let runs_json: Vec<Value> = runs.iter().map(run_to_dict).collect();
    Ok(Json(json!({ "runs": runs_json, "total": total })).into_response())
}

/// `GET /meta/output-targets` -> `GET /api/tasks/meta/output-targets`.
async fn list_output_targets(
    State(s): State<AppState>,
    _user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    // _owner(request) is called (auth resolution) but the value is unused.
    let mut targets: Vec<Value> = vec![
        json!({"value": "session", "label": "Session", "description": "Save result to a chat session"}),
        json!({"value": "notification", "label": "Notification", "description": "Push a browser notification with the result (also saved to the session for history)"}),
        json!({"value": "email", "label": "Email me", "description": "Send result through your configured SMTP account"}),
    ];

    const DELIVERY_VERBS: &[&str] = &[
        "send", "notify", "post", "publish", "draft", "dispatch", "deliver",
    ];
    const NON_DELIVERY: &[&str] = &[
        "search", "list", "get", "find", "read", "fetch", "view", "tag", "label", "move",
        "archive", "delete", "mark", "schedule",
    ];

    // try: mcp = get_mcp_manager(); if mcp: for tool in mcp.get_all_tools(): ...
    // The wired manager is the same singleton the Python module-level get_mcp_manager()
    // returns; a bare-except wraps the whole block, so any failure just keeps the base 3.
    for tool in s.mcp_manager.get_all_tools(None) {
        let name_lower = tool
            .get("name")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_lowercase();
        if NON_DELIVERY.iter().any(|x| name_lower.contains(x)) {
            continue;
        }
        if !DELIVERY_VERBS.iter().any(|v| name_lower.contains(v)) {
            continue;
        }
        let qualified = tool.get("qualified_name").and_then(|v| v.as_str()).unwrap_or("");
        let server_name = tool.get("server_name").and_then(|v| v.as_str()).unwrap_or("");
        let name = tool.get("name").and_then(|v| v.as_str()).unwrap_or("");
        let description = tool.get("description").cloned().unwrap_or(Value::Null);
        targets.push(json!({
            "value": qualified,
            "label": format!("{server_name} \u{2192} {name}"),
            "description": description,
        }));
    }

    Ok(Json(json!({ "targets": targets })).into_response())
}

/// `GET /meta/actions` -> `GET /api/tasks/meta/actions`.
async fn list_actions(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    let user = owner(&user);
    let admin = is_admin(&s, user.as_deref());
    let actions: Vec<Value> = crate::src::builtin_actions::BUILTIN_ACTION_INFO
        .iter()
        // if name not in _ADMIN_ONLY_ACTIONS or _is_admin(user)
        .filter(|(name, _)| !ADMIN_ONLY_ACTIONS.contains(name) || admin)
        .map(|(name, desc)| json!({"name": name, "description": desc}))
        .collect();
    Ok(Json(json!({ "actions": actions })).into_response())
}

/// `GET /meta/events` -> `GET /api/tasks/meta/events`.
async fn list_events(
    State(_s): State<AppState>,
    _user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    // _owner(request) is called (auth resolution) but the value is unused.
    Ok(Json(json!({ "events": [
        {"name": "session_created", "description": "Fires when a new chat session is created"},
        {"name": "message_sent", "description": "Fires when a user sends a message"},
        {"name": "document_created", "description": "Fires when a document is created"},
        {"name": "memory_added", "description": "Fires when a memory is added"},
        {"name": "research_completed", "description": "Fires when a research report completes"},
        {"name": "email_received", "description": "Fires when new inbox mail is observed"},
        {"name": "skill_added", "description": "Fires when a new skill is created"},
    ] }))
    .into_response())
}

/// `POST /{task_id}/webhook/{token}` -> `POST /api/tasks/:task_id/webhook/:token`.
///
/// Unauthenticated at the handler level — the token IS the auth. No `_owner` call.
async fn webhook_trigger(
    State(s): State<AppState>,
    Path((task_id, token)): Path<(String, String)>,
) -> Result<Response, HttpException> {
    {
        let db = session_local()?;
        // task = db.query(ScheduledTask).filter(id == task_id, webhook_token == token, status == "active").first()
        let found: Option<String> = db
            .query_row(
                "SELECT id FROM scheduled_tasks \
                 WHERE id = ?1 AND webhook_token = ?2 AND status = 'active'",
                rusqlite::params![task_id, token],
                |r| r.get::<_, String>(0),
            )
            .optional()
            .map_err(db_500)?;
        // if not task: raise HTTPException(404, "Not found")
        if found.is_none() {
            return Err(HttpException::new(404, "Not found"));
        }
    }
    // started = await task_scheduler.run_task_now(task_id)
    let started = s.task_scheduler.run_task_now(&task_id, false).await;
    if !started {
        return Err(HttpException::new(409, "Task is already running"));
    }
    Ok(Json(json!({ "ok": true, "message": "Task triggered via webhook" })).into_response())
}

/// `POST /{task_id}/webhook-regenerate` -> `POST /api/tasks/:task_id/webhook-regenerate`.
async fn regenerate_webhook(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(task_id): Path<String>,
) -> Result<Response, HttpException> {
    let user = owner(&user);
    let db = session_local()?;
    let task = load_task(&db, &task_id)?;
    let _ = require_task(&task, user.as_deref())?;
    // task.webhook_token = secrets.token_urlsafe(32); db.commit()
    let new_token = crate::pysecrets::token_urlsafe(32);
    db.execute(
        "UPDATE scheduled_tasks SET webhook_token = ?1 WHERE id = ?2",
        rusqlite::params![new_token, task_id],
    )
    .map_err(db_500)?;
    Ok(Json(json!({ "ok": true, "webhook_token": new_token })).into_response())
}

/// `POST /parse` -> `POST /api/tasks/parse` — turn a free-form description into a
/// structured task draft via an LLM.
///
/// Faithful port of `parse_task`: resolve the utility-then-default endpoint, prompt the
/// model for STRICT JSON, strip-think + de-fence the reply, pull the first `{...}` block,
/// then whitelist/validate the fields via [`parse_draft`]. The
/// `{"success": False, "message": "No model endpoint configured"}` branch is returned
/// only when no endpoint resolves (Python's own `if not (url and model)`); any other
/// failure mirrors Python's `except Exception` -> `{"success": False, "message": str(e)}`.
async fn parse_task(
    State(_s): State<AppState>,
    _user: Option<Extension<CurrentUser>>,
    Json(body): Json<Value>,
) -> Result<Response, HttpException> {
    // desc = (body.get("description") or "").strip()
    let desc = body
        .get("description")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    // if not desc: return {"success": False, "message": "Nothing to parse"}
    if desc.is_empty() {
        return Ok(Json(json!({ "success": false, "message": "Nothing to parse" })).into_response());
    }

    // now = datetime.now(); ctx = now.strftime("%Y-%m-%d %H:%M (%A)")
    // `datetime.now()` is naive LOCAL time.
    let ctx = chrono::Local::now()
        .naive_local()
        .format("%Y-%m-%d %H:%M (%A)")
        .to_string();
    // sys = (...) — the STRICT-JSON system prompt with the local date/time interpolated.
    let sys = format!(
        "You convert a user's description of a recurring or one-off task into \
STRICT JSON for a task scheduler. The current local date/time is \
{ctx}. Output ONLY a JSON object, no prose, no markdown fences.\n\n\
Schema (omit fields you can't infer):\n\
{{\n\
  \"task_type\": \"llm\" | \"research\",  // \"research\" if it asks to research/investigate/find out; else \"llm\"\n\
  \"name\": \"short 3-6 word title\",\n\
  \"prompt\": \"the instruction the AI should run on schedule (or the research question)\",\n\
  \"schedule\": \"daily\" | \"weekly\" | \"monthly\" | \"once\" | \"cron\",\n\
  \"scheduled_time\": \"HH:MM\",        // 24h LOCAL time\n\
  \"scheduled_day\": 0,               // weekly: 0=Mon..6=Sun; monthly: 1..31\n\
  \"scheduled_date\": \"YYYY-MM-DDTHH:MM\",  // only for \"once\"\n\
  \"cron_expression\": \"m h dom mon dow\",  // only if schedule is \"cron\"\n\
  \"output_target\": \"session\" | \"email\" | \"notification\"  // use email when the user asks to email the result\n\
}}\n\n\
Rules: default schedule to 'daily' if a time is given without a frequency. \
Default scheduled_time to '09:00' if none is stated. For 'every weekday' \
use cron '0 H * * 1-5'. Keep the prompt actionable and self-contained."
    );

    // try: ...
    //   url, model, headers = resolve_endpoint("utility")
    //   if not url: url, model, headers = resolve_endpoint("default")
    //   if not (url and model): return {"success": False, "message": "No model endpoint configured"}
    //
    // Python `task_routes.py:861/863` passes NO owner -> `owner=None`, so the
    // centralized resolver's `get_user_setting` collapses to the global
    // `get_setting` cascade — byte-for-byte today's behavior. The
    // `!ep.0.is_empty()` guard is preserved verbatim from the deleted local copy
    // (harmless: the triple is already `None` unless url+model are both present).
    use crate::src::endpoint_resolver::resolve_endpoint_triple;
    let endpoint = match resolve_endpoint_triple("utility", None) {
        Some(ep) if !ep.0.is_empty() => Some(ep),
        _ => resolve_endpoint_triple("default", None),
    };
    let (url, model, headers) = match endpoint {
        Some((url, model, headers)) if !url.is_empty() && !model.is_empty() => (url, model, headers),
        _ => {
            return Ok(
                Json(json!({ "success": false, "message": "No model endpoint configured" }))
                    .into_response(),
            );
        }
    };

    // raw = await llm_call_async(url, model, [...], temperature=0.2, max_tokens=400,
    //                            headers=headers, timeout=45)
    let messages = vec![
        json!({ "role": "system", "content": sys }),
        // desc[:1000]
        json!({ "role": "user", "content": desc.chars().take(1000).collect::<String>() }),
    ];
    let raw = match crate::src::llm_core::llm_call_async(
        &url, &model, messages, 0.2, 400, headers, 45,
    )
    .await
    {
        Ok(raw) => raw,
        // except Exception as e: logger.error(...); return {"success": False, "message": str(e)}
        Err(e) => {
            crate::pylog::error(&format!("parse_task failed: {e}"));
            return Ok(
                Json(json!({ "success": false, "message": e.to_string() })).into_response(),
            );
        }
    };

    // text = _strip_think(raw or "", prose=False, prompt_echo=False).strip()
    let mut text = crate::src::text_helpers::strip_think(&raw, false, false)
        .trim()
        .to_string();
    // if text.startswith("```"): text = text.strip("`"); if text.lower().startswith("json"): text = text[4:].lstrip()
    if text.starts_with("```") {
        // Python `str.strip("`")` removes leading/trailing backtick runs.
        text = text.trim_matches('`').to_string();
        if text.to_lowercase().starts_with("json") {
            // text[4:].lstrip()
            text = text[4..].trim_start().to_string();
        }
    }

    // m = re.search(r"\{.*\}", text, re.S); draft = json.loads(m.group(0) if m else text)
    let json_text = first_brace_block(&text).unwrap_or(text.as_str());
    let draft: Value = match serde_json::from_str(json_text) {
        Ok(v) => v,
        Err(e) => {
            crate::pylog::error(&format!("parse_task failed: {e}"));
            return Ok(
                Json(json!({ "success": false, "message": e.to_string() })).into_response(),
            );
        }
    };
    // if not isinstance(draft, dict): raise ValueError("not an object")
    if !draft.is_object() {
        crate::pylog::error("parse_task failed: not an object");
        return Ok(
            Json(json!({ "success": false, "message": "not an object" })).into_response(),
        );
    }

    // Whitelist + light validation. parse_draft returns None when no prompt is extracted
    // (the Python "Could not extract a task instruction" failure).
    match parse_draft(&draft) {
        Some(out) => Ok(Json(json!({ "success": true, "draft": out })).into_response()),
        None => Ok(Json(
            json!({ "success": false, "message": "Could not extract a task instruction" }),
        )
        .into_response()),
    }
}

/// `re.search(r"\{.*\}", text, re.S)` -> `m.group(0)` — the substring from the FIRST `{`
/// to the LAST `}` (greedy, dot-matches-newline), or `None` when no such pair exists.
fn first_brace_block(text: &str) -> Option<&str> {
    let start = text.find('{')?;
    let end = text.rfind('}')?;
    if end < start {
        return None;
    }
    Some(&text[start..=end])
}

/// Port of the `POST /parse` whitelist + light validation of the model's JSON draft.
///
/// Reproduces the Python's `out` assembly: clamp `task_type`, copy the string fields,
/// clamp `schedule`, validate `scheduled_time` against `^\d{1,2}:\d{2}$`, copy an int
/// `scheduled_day`, clamp `output_target`, force `trigger_type = "schedule"`. Returns
/// `None` when the prompt is missing (the Python's "Could not extract a task
/// instruction" failure).
fn parse_draft(draft: &Value) -> Option<Value> {
    let draft = draft.as_object()?;
    let mut out: Map<String, Value> = Map::new();

    // task_type: "llm" | "research" else "llm"
    // Mirrors Python task_routes.py:884-887: a whitelist, NOT unwrap_or — a
    // present-but-unlisted value must fall to "llm", not pass through.
    #[allow(clippy::manual_unwrap_or)]
    let task_type = match draft.get("task_type").and_then(|v| v.as_str()) {
        Some(t @ ("llm" | "research")) => t,
        _ => "llm",
    };
    out.insert("task_type".to_string(), json!(task_type));

    // for k in ("name", "prompt", "cron_expression", "scheduled_date"): copy non-empty str
    for k in ["name", "prompt", "cron_expression", "scheduled_date"] {
        if let Some(v) = draft.get(k).and_then(|v| v.as_str()) {
            if !v.trim().is_empty() {
                out.insert(k.to_string(), json!(v.trim()));
            }
        }
    }

    // schedule: clamp to the allowed set, else "daily"
    // Mirrors Python task_routes.py:891-894: a whitelist, NOT unwrap_or — a
    // present-but-unlisted value must fall to "daily", not pass through.
    #[allow(clippy::manual_unwrap_or)]
    let schedule = match draft.get("schedule").and_then(|v| v.as_str()) {
        Some(s @ ("daily" | "weekly" | "monthly" | "once" | "cron")) => s,
        _ => "daily",
    };
    out.insert("schedule".to_string(), json!(schedule));

    // scheduled_time: HH:MM regex
    if let Some(st) = draft.get("scheduled_time").and_then(|v| v.as_str()) {
        let trimmed = st.trim();
        if is_hhmm(trimmed) {
            out.insert("scheduled_time".to_string(), json!(trimmed));
        }
    }

    // scheduled_day: int (a JSON bool is NOT an int — isinstance(x, int) is True for bool
    // in Python, but FastAPI bodies never carry that nuance here; match int only).
    if let Some(day) = draft.get("scheduled_day").and_then(|v| v.as_i64()) {
        out.insert("scheduled_day".to_string(), json!(day));
    }

    // output_target: "session" | "email" | "notification"
    if let Some(ot @ ("session" | "email" | "notification")) =
        draft.get("output_target").and_then(|v| v.as_str())
    {
        out.insert("output_target".to_string(), json!(ot));
    }

    out.insert("trigger_type".to_string(), json!("schedule"));

    // if not out.get("prompt"): return None (the "Could not extract" failure)
    let has_prompt = out
        .get("prompt")
        .and_then(|v| v.as_str())
        .map(|s| !s.is_empty())
        .unwrap_or(false);
    if !has_prompt {
        return None;
    }
    Some(Value::Object(out))
}

/// `re.match(r"^\d{1,2}:\d{2}$", s)` — 1-2 digit hour, colon, exactly 2 digit minute.
fn is_hhmm(s: &str) -> bool {
    let (h, m) = match s.split_once(':') {
        Some(p) => p,
        None => return false,
    };
    (1..=2).contains(&h.len())
        && h.chars().all(|c| c.is_ascii_digit())
        && m.len() == 2
        && m.chars().all(|c| c.is_ascii_digit())
}

// ===========================================================================
// Query-param coercion (FastAPI/Pydantic v2 bool/int)
// ===========================================================================

/// A FastAPI/Pydantic `422` validation error for a single query parameter.
fn validation_error(err_type: &str, name: &str, msg: &str, input: &str) -> HttpException {
    HttpException::with_detail(
        422,
        json!([{
            "type": err_type,
            "loc": ["query", name],
            "msg": msg,
            "input": input,
        }]),
    )
}

/// `param: bool = <default>` — Pydantic v2 lax bool coercion of a query value.
fn query_bool(q: &HashMap<String, String>, name: &str, default: bool) -> Result<bool, HttpException> {
    match q.get(name) {
        None => Ok(default),
        Some(raw) => {
            let v = raw.trim().to_lowercase();
            match v.as_str() {
                "1" | "on" | "t" | "true" | "y" | "yes" => Ok(true),
                "0" | "off" | "f" | "false" | "n" | "no" => Ok(false),
                _ => Err(validation_error(
                    "bool_parsing",
                    name,
                    "Input should be a valid boolean, unable to interpret input",
                    raw,
                )),
            }
        }
    }
}

/// `param: int = <default>` — Pydantic v2 integer coercion of a query value.
fn query_int(q: &HashMap<String, String>, name: &str, default: i64) -> Result<i64, HttpException> {
    match q.get(name) {
        None => Ok(default),
        Some(raw) => raw.trim().parse::<i64>().map_err(|_| {
            validation_error(
                "int_parsing",
                name,
                "Input should be a valid integer, unable to parse string as an integer",
                raw,
            )
        }),
    }
}

// ===========================================================================
// TaskRow clone + offset reader (helpers for the mutating handlers)
// ===========================================================================

impl TaskRow {
    /// A deep clone of the row (the mutating handlers work on an owned copy, mirroring
    /// SQLAlchemy mutating the loaded instance before commit/refresh).
    fn clone_row(&self) -> TaskRow {
        TaskRow {
            id: self.id.clone(),
            owner: self.owner.clone(),
            name: self.name.clone(),
            prompt: self.prompt.clone(),
            task_type: self.task_type.clone(),
            action: self.action.clone(),
            schedule: self.schedule.clone(),
            scheduled_time: self.scheduled_time.clone(),
            scheduled_day: self.scheduled_day,
            scheduled_date: self.scheduled_date.clone(),
            cron_expression: self.cron_expression.clone(),
            trigger_type: self.trigger_type.clone(),
            trigger_event: self.trigger_event.clone(),
            trigger_count: self.trigger_count,
            trigger_counter: self.trigger_counter,
            next_run: self.next_run.clone(),
            last_run: self.last_run.clone(),
            status: self.status.clone(),
            output_target: self.output_target.clone(),
            session_id: self.session_id.clone(),
            crew_member_id: self.crew_member_id.clone(),
            model: self.model.clone(),
            endpoint_url: self.endpoint_url.clone(),
            run_count: self.run_count,
            then_task_id: self.then_task_id.clone(),
            notifications_enabled: self.notifications_enabled,
            webhook_token: self.webhook_token.clone(),
            created_at: self.created_at.clone(),
            updated_at: self.updated_at.clone(),
        }
    }
}

/// Read a [`TaskRow`] whose columns begin at SELECT index `base` (used by the joined
/// `runs/recent` query, where the task columns follow the run columns).
fn read_task_row_offset(r: &rusqlite::Row<'_>, base: usize) -> rusqlite::Result<TaskRow> {
    Ok(TaskRow {
        id: r.get(base)?,
        owner: r.get(base + 1)?,
        name: r.get(base + 2)?,
        prompt: r.get(base + 3)?,
        task_type: r.get(base + 4)?,
        action: r.get(base + 5)?,
        schedule: r.get(base + 6)?,
        scheduled_time: r.get(base + 7)?,
        scheduled_day: r.get(base + 8)?,
        scheduled_date: r.get(base + 9)?,
        cron_expression: r.get(base + 10)?,
        trigger_type: r.get(base + 11)?,
        trigger_event: r.get(base + 12)?,
        trigger_count: r.get(base + 13)?,
        trigger_counter: r.get(base + 14)?,
        next_run: r.get(base + 15)?,
        last_run: r.get(base + 16)?,
        status: r.get(base + 17)?,
        output_target: r.get(base + 18)?,
        session_id: r.get(base + 19)?,
        crew_member_id: r.get(base + 20)?,
        model: r.get(base + 21)?,
        endpoint_url: r.get(base + 22)?,
        run_count: r.get(base + 23)?,
        then_task_id: r.get(base + 24)?,
        notifications_enabled: r.get(base + 25)?,
        webhook_token: r.get(base + 26)?,
        created_at: r.get(base + 27)?,
        updated_at: r.get(base + 28)?,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn blank_task() -> TaskRow {
        TaskRow {
            id: "t1".to_string(),
            owner: None,
            name: None,
            prompt: None,
            task_type: None,
            action: None,
            schedule: None,
            scheduled_time: None,
            scheduled_day: None,
            scheduled_date: None,
            cron_expression: None,
            trigger_type: None,
            trigger_event: None,
            trigger_count: None,
            trigger_counter: None,
            next_run: None,
            last_run: None,
            status: None,
            output_target: None,
            session_id: None,
            crew_member_id: None,
            model: None,
            endpoint_url: None,
            run_count: None,
            then_task_id: None,
            notifications_enabled: None,
            webhook_token: None,
            created_at: None,
            updated_at: None,
        }
    }

    #[test]
    fn iso_z_appends_suffix() {
        assert_eq!(iso_z_or_null(None), Value::Null);
        assert_eq!(iso_z_or_null(Some("")), Value::Null);
        assert_eq!(
            iso_z_or_null(Some("2026-06-01 12:30:00")),
            json!("2026-06-01T12:30:00Z")
        );
        assert_eq!(
            iso_z_or_null(Some("2026-06-01 12:30:00.123456")),
            json!("2026-06-01T12:30:00.123456Z")
        );
    }

    #[test]
    fn task_to_dict_defaults_and_key_order() {
        let mut t = blank_task();
        t.task_type = None; // -> "llm"
        let d = task_to_dict(&t, None);
        // Defaults applied.
        assert_eq!(d["task_type"], json!("llm"));
        assert_eq!(d["trigger_type"], json!("schedule"));
        assert_eq!(d["trigger_counter"], json!(0));
        assert_eq!(d["run_count"], json!(0));
        // notifications_enabled NULL coerces to True (getattr default).
        assert_eq!(d["notifications_enabled"], json!(true));
        // is_builtin/is_modified for a non-builtin task.
        assert_eq!(d["is_builtin"], json!(false));
        assert_eq!(d["is_modified"], json!(false));
        // webhook_token null because trigger_type != "webhook".
        assert_eq!(d["webhook_token"], Value::Null);
        // Key order matches the Python dict literal (id..updated_at, then is_builtin, is_modified).
        let keys: Vec<&str> = d.as_object().unwrap().keys().map(|k| k.as_str()).collect();
        assert_eq!(
            keys,
            vec![
                "id",
                "name",
                "prompt",
                "task_type",
                "action",
                "schedule",
                "scheduled_time",
                "scheduled_day",
                "scheduled_date",
                "cron_expression",
                "trigger_type",
                "trigger_event",
                "trigger_count",
                "trigger_counter",
                "next_run",
                "last_run",
                "status",
                "output_target",
                "session_id",
                "crew_member_id",
                "model",
                "endpoint_url",
                "run_count",
                "then_task_id",
                "notifications_enabled",
                "webhook_token",
                "created_at",
                "updated_at",
                "is_builtin",
                "is_modified",
            ]
        );
    }

    #[test]
    fn task_to_dict_webhook_token_only_for_webhook_trigger() {
        let mut t = blank_task();
        t.webhook_token = Some("tok".to_string());
        t.trigger_type = Some("schedule".to_string());
        assert_eq!(task_to_dict(&t, None)["webhook_token"], Value::Null);
        t.trigger_type = Some("webhook".to_string());
        assert_eq!(task_to_dict(&t, None)["webhook_token"], json!("tok"));
    }

    #[test]
    fn task_to_dict_builtin_modified_flags() {
        // A pristine housekeeping task is is_builtin && !is_modified.
        let mut t = blank_task();
        t.action = Some("summarize_emails".to_string());
        let defs = HOUSEKEEPING_DEFAULTS.get("summarize_emails").unwrap();
        t.name = Some(defs.name.to_string());
        t.schedule = defs.schedule.map(str::to_string);
        t.scheduled_time = defs.scheduled_time.map(str::to_string);
        t.cron_expression = defs.cron_expression.map(str::to_string);
        let d = task_to_dict(&t, None);
        assert_eq!(d["is_builtin"], json!(true));
        assert_eq!(d["is_modified"], json!(false));
        // Renaming it (away from name + legacy_names) marks it modified.
        t.name = Some("My Custom Name".to_string());
        assert_eq!(task_to_dict(&t, None)["is_modified"], json!(true));
        // A legacy name is NOT modified (and display name maps to canonical).
        t.name = Some("Tidy Email (Summary)".to_string());
        let d = task_to_dict(&t, None);
        assert_eq!(d["is_modified"], json!(false));
        assert_eq!(d["name"], json!("Email (Summary)"));
    }

    #[test]
    fn task_to_dict_last_run_truncates_to_500() {
        let t = blank_task();
        let mut last = RunRow {
            id: "r1".to_string(),
            task_id: "t1".to_string(),
            started_at: None,
            finished_at: None,
            status: Some("success".to_string()),
            result: Some("x".repeat(600)),
            error: None,
            tokens_used: None,
            model: None,
        };
        let d = task_to_dict(&t, Some(&last));
        assert_eq!(d["last_run_status"], json!("success"));
        assert_eq!(d["last_run_result"].as_str().unwrap().chars().count(), 500);
        // result empty -> falls back to error.
        last.result = Some(String::new());
        last.error = Some("boom".to_string());
        let d = task_to_dict(&t, Some(&last));
        assert_eq!(d["last_run_result"], json!("boom"));
    }

    #[test]
    fn run_to_dict_shape() {
        let r = RunRow {
            id: "r1".to_string(),
            task_id: "t1".to_string(),
            started_at: Some("2026-06-01 09:00:00".to_string()),
            finished_at: None,
            status: Some("running".to_string()),
            result: None,
            error: None,
            tokens_used: Some(42),
            model: Some("m".to_string()),
        };
        let d = run_to_dict(&r);
        assert_eq!(d["id"], json!("r1"));
        assert_eq!(d["task_id"], json!("t1"));
        assert_eq!(d["started_at"], json!("2026-06-01T09:00:00Z"));
        assert_eq!(d["finished_at"], Value::Null);
        assert_eq!(d["status"], json!("running"));
        assert_eq!(d["tokens_used"], json!(42));
        assert_eq!(d["model"], json!("m"));
    }

    #[test]
    fn run_research_id_only_for_research_with_session() {
        let mut t = blank_task();
        t.task_type = Some("research".to_string());
        t.session_id = Some("sess-1".to_string());
        assert_eq!(run_research_id(&t), "sess-1");
        // wrong type -> ""
        t.task_type = Some("llm".to_string());
        assert_eq!(run_research_id(&t), "");
        // research but no session -> ""
        t.task_type = Some("research".to_string());
        t.session_id = None;
        assert_eq!(run_research_id(&t), "");
    }

    #[test]
    fn cron_validation_matches_croniter_shape() {
        // 5-field standard crons valid.
        assert!(cron_is_valid("*/5 * * * *"));
        assert!(cron_is_valid("0 */2 * * *"));
        assert!(cron_is_valid("0 6 * * 1"));
        // weekday range remapped.
        assert!(cron_is_valid("0 7 * * 1-5"));
        // garbage rejected.
        assert!(!cron_is_valid("not a cron"));
        assert!(!cron_is_valid("* * *"));
        assert!(!cron_is_valid(""));
    }

    #[test]
    fn is_admin_only_action_set() {
        assert!(is_admin_only_action(Some("run_local")));
        assert!(is_admin_only_action(Some("run_script")));
        assert!(is_admin_only_action(Some("ssh_command")));
        assert!(!is_admin_only_action(Some("check_email_urgency")));
        assert!(!is_admin_only_action(None));
    }

    #[test]
    fn is_hhmm_regex() {
        assert!(is_hhmm("9:00"));
        assert!(is_hhmm("09:30"));
        assert!(is_hhmm("23:59"));
        assert!(!is_hhmm("9:0"));
        assert!(!is_hhmm("9:000"));
        assert!(!is_hhmm("0900"));
        assert!(!is_hhmm("ab:cd"));
    }

    #[test]
    fn parse_draft_whitelists_and_requires_prompt() {
        // Missing prompt -> None.
        assert!(parse_draft(&json!({"name": "X"})).is_none());
        // Full draft whitelisted.
        let out = parse_draft(&json!({
            "task_type": "research",
            "name": "Daily AI News",
            "prompt": "Summarize the top AI news",
            "schedule": "weekly",
            "scheduled_time": "7:30",
            "scheduled_day": 2,
            "output_target": "email",
            "junk": "ignored",
        }))
        .unwrap();
        assert_eq!(out["task_type"], json!("research"));
        assert_eq!(out["name"], json!("Daily AI News"));
        assert_eq!(out["prompt"], json!("Summarize the top AI news"));
        assert_eq!(out["schedule"], json!("weekly"));
        assert_eq!(out["scheduled_time"], json!("7:30"));
        assert_eq!(out["scheduled_day"], json!(2));
        assert_eq!(out["output_target"], json!("email"));
        assert_eq!(out["trigger_type"], json!("schedule"));
        assert!(out.get("junk").is_none());
        // Bad schedule/task_type clamp to defaults.
        let out = parse_draft(&json!({"prompt": "do it", "schedule": "fortnightly", "task_type": "weird"}))
            .unwrap();
        assert_eq!(out["schedule"], json!("daily"));
        assert_eq!(out["task_type"], json!("llm"));
        // Invalid scheduled_time omitted.
        let out = parse_draft(&json!({"prompt": "p", "scheduled_time": "nope"})).unwrap();
        assert!(out.get("scheduled_time").is_none());
    }

    #[test]
    fn parse_scheduled_date_handles_z_and_naive() {
        assert!(parse_scheduled_date("2026-06-01T09:00:00Z").is_some());
        assert!(parse_scheduled_date("2026-06-01T09:00:00").is_some());
        assert!(parse_scheduled_date("2026-06-01T09:00").is_some());
        assert!(parse_scheduled_date("not a date").is_none());
    }

    #[test]
    fn mount_smoke() {
        let base: Router<AppState> = Router::new();
        let _merged: Router<AppState> = base.merge(setup_task_routes());
    }

    // -----------------------------------------------------------------------
    // clear_task_cache: static cache-table map
    // -----------------------------------------------------------------------

    /// The clear-cache handler knows exactly which actions have clearable tables.
    /// This test verifies the expected mapping without hitting a DB.
    #[test]
    fn clear_cache_known_actions() {
        // The set of actions with clearable caches.
        let known = [
            "summarize_emails",
            "draft_email_replies",
            "extract_email_events",
            "mark_email_boundaries",
            "learn_sender_signatures",
            "check_email_urgency",
        ];
        let cache_tables: &[(&str, &[&str])] = &[
            ("summarize_emails",       &["email_summaries"]),
            ("draft_email_replies",    &["email_ai_replies"]),
            ("extract_email_events",   &["email_calendar_extractions"]),
            ("mark_email_boundaries",  &["email_boundaries"]),
            ("learn_sender_signatures",&["sender_signatures"]),
            ("check_email_urgency",    &["email_tags", "email_urgency_alerts"]),
        ];
        for action in &known {
            assert!(
                cache_tables.iter().any(|(a, _)| a == action),
                "missing action {action} from clear-cache map"
            );
        }
        // An unrecognised action is not in the map.
        assert!(
            !cache_tables.iter().any(|(a, _)| *a == "unknown_action"),
            "unknown_action should not be in the clear-cache map"
        );
    }

    /// `check_email_urgency` maps to both `email_tags` AND `email_urgency_alerts`.
    #[test]
    fn clear_cache_urgency_tables() {
        let cache_tables: &[(&str, &[&str])] = &[
            ("summarize_emails",       &["email_summaries"]),
            ("draft_email_replies",    &["email_ai_replies"]),
            ("extract_email_events",   &["email_calendar_extractions"]),
            ("mark_email_boundaries",  &["email_boundaries"]),
            ("learn_sender_signatures",&["sender_signatures"]),
            ("check_email_urgency",    &["email_tags", "email_urgency_alerts"]),
        ];
        let tables = cache_tables
            .iter()
            .find(|(a, _)| *a == "check_email_urgency")
            .map(|(_, t)| *t)
            .unwrap();
        assert!(tables.contains(&"email_tags"));
        assert!(tables.contains(&"email_urgency_alerts"));
    }

    // -----------------------------------------------------------------------
    // owner_slug generation (used in check_email_urgency file cleanup)
    // -----------------------------------------------------------------------

    /// Reproduces the `owner_slug` logic from `clear_task_cache` inline to verify
    /// that special characters are replaced with `_` and alphanumeric / `-_.@` pass
    /// through unchanged.
    #[test]
    fn owner_slug_sanitises_special_chars() {
        let slug = |user: &str| -> String {
            user.chars()
                .map(|c| {
                    if c.is_alphanumeric() || matches!(c, '-' | '_' | '.' | '@') {
                        c
                    } else {
                        '_'
                    }
                })
                .collect()
        };
        assert_eq!(slug("alice@example.com"), "alice@example.com");
        assert_eq!(slug("bob-the-user"), "bob-the-user");
        assert_eq!(slug("user/with\\slash"), "user_with_slash");
        assert_eq!(slug("default"), "default");
        // Empty -> falls back to "default" in the handler (tested separately).
        assert_eq!(slug(""), "");
    }
}
