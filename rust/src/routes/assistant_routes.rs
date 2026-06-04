// routes/assistant_routes.rs  <- routes/assistant_routes.py
//! Personal assistant routes — resolve the per-user singleton, read/write
//! its settings, and list its scheduled check-in tasks (routes WAVE-3).
//!
//! The personal assistant is just a specially-flagged CrewMember that owns one
//! pinned Session and three daily ScheduledTasks ("Morning/Midday/Evening
//! check-in"). Everything about it is user-editable: name, personality, model,
//! enabled tools, timezone, and the three check-in times/prompts/enabled flags.
//!
//! Faithful translation of `routes/assistant_routes.py`'s `setup_assistant_routes`
//! factory. Six handlers over the `crew_members`, `scheduled_tasks`, and
//! `task_runs` tables (raw `rusqlite`, mirroring the SQLAlchemy `CrewMember` /
//! `ScheduledTask` / `TaskRun` ORM models).
//!
//! ## Shape
//! * `setup_assistant_routes() -> Router<AppState>` mirrors the Python factory.
//!   The Python factory takes the `task_scheduler` arg, but per the integration
//!   contract the scheduler is reached through `State<AppState>` (`s.task_scheduler`)
//!   rather than a captured factory argument. The Python `APIRouter(prefix=
//!   "/api/assistant", tags=["assistant"])` yields the absolute paths used on each
//!   `.route(...)`.
//! * Auth: every handler authenticates with `get_current_user`, mirroring the
//!   Python `_owner(request)` helper which raises `HTTPException(401, "Not
//!   authenticated")` when there is no current user. We read
//!   `Option<Extension<CurrentUser>>` and reproduce that 401. NOTE: the Python
//!   `_owner` treats an empty-string user as falsy (`if not owner:`), so an empty
//!   user also 401s here.
//! * `raise HTTPException(s, d)` -> `return Err(HttpException::new(s, d))`.
//!
//! ## TaskScheduler (single-writer rule)
//! `ensure_assistant_defaults` / `run_task_now` / the free `compute_next_run`
//! function are consumed via the scheduler's already-public API; this module
//! does NOT edit `task_scheduler.rs`.
//!
//! ## available-timezones
//! Python's `zoneinfo.available_timezones()` is reproduced via `chrono_tz::
//! TZ_VARIANTS` (the IANA zone name set), sorted. This is a best-effort parity:
//! both surface the IANA tz database name list the settings dropdown consumes.
//!
//! ## No path collision
//! Every route is under `/api/assistant/*`, a prefix the inline `web/mod.rs`
//! subset never touches, so the aggregator merges this router without an axum
//! duplicate-`method`+`path` panic.


use std::collections::HashSet;

use axum::extract::{Path, State};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Extension, Json, Router};
use chrono::NaiveDateTime;
use rusqlite::OptionalExtension;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::routes::{AppState, CurrentUser, HttpException};
use crate::src::task_scheduler::compute_next_run;

/// `class CheckInUpdate(BaseModel)` — one check-in patch entry in the
/// `AssistantSettingsUpdate.check_ins` list.
///
/// `id` is required (the `ScheduledTask.id`); the rest are optional. serde's
/// `#[serde(default)]` reproduces "absent -> None" for the optionals.
#[derive(Debug, Deserialize)]
struct CheckInUpdate {
    /// `ScheduledTask.id`.
    id: String,
    #[serde(default)]
    name: Option<String>,
    /// `"HH:MM"`.
    #[serde(default)]
    scheduled_time: Option<String>,
    #[serde(default)]
    prompt: Option<String>,
    /// maps to status `"active"`/`"paused"`.
    #[serde(default)]
    enabled: Option<bool>,
}

/// `class AssistantSettingsUpdate(BaseModel)` — the JSON body for
/// `PATCH /api/assistant/settings`. Every field is optional (absent -> None).
#[derive(Debug, Deserialize)]
struct AssistantSettingsUpdate {
    #[serde(default)]
    name: Option<String>,
    #[serde(default)]
    avatar: Option<String>,
    #[serde(default)]
    personality: Option<String>,
    #[serde(default)]
    model: Option<String>,
    #[serde(default)]
    endpoint_url: Option<String>,
    #[serde(default)]
    enabled_tools: Option<Vec<String>>,
    /// convenience toggle.
    #[serde(default)]
    allow_autonomous_email: Option<bool>,
    #[serde(default)]
    timezone: Option<String>,
    #[serde(default)]
    check_ins: Option<Vec<CheckInUpdate>>,
}

/// `_EMAIL_TOOLS = {"send_email", "reply_to_email"}`.
const EMAIL_TOOLS: [&str; 2] = ["send_email", "reply_to_email"];

/// `_SYNTHETIC_OWNERS = frozenset({"internal-tool", "api", "demo", "system", ""})`.
///
/// Synthetic / non-human owners that should NEVER get an assistant + check-in
/// tasks seeded. Hitting any `/assistant` route under one of these used to seed a
/// full CrewMember + Morning/Midday/Evening tasks under that owner, which then
/// double-fired alongside the real user's check-ins.
fn is_synthetic_owner(owner: &str) -> bool {
    owner.is_empty()
        || matches!(owner, "internal-tool" | "api" | "demo" | "system")
}

/// A `crew_members` row — the columns the assistant routes touch.
///
/// Mirrors the subset of the SQLAlchemy `CrewMember` ORM that `_crew_to_dict`
/// reads plus the fields the PATCH handler mutates.
#[derive(Clone, Debug)]
struct CrewRow {
    id: String,
    name: Option<String>,
    avatar: Option<String>,
    personality: Option<String>,
    model: Option<String>,
    endpoint_url: Option<String>,
    greeting: Option<String>,
    enabled_tools: Option<String>,
    session_id: Option<String>,
    is_default_assistant: bool,
    timezone: Option<String>,
}

const CREW_COLS: &str = "id, name, avatar, personality, model, endpoint_url, greeting, \
    enabled_tools, session_id, is_default_assistant, timezone";

fn map_crew(r: &rusqlite::Row<'_>) -> rusqlite::Result<CrewRow> {
    Ok(CrewRow {
        id: r.get(0)?,
        name: r.get(1)?,
        avatar: r.get(2)?,
        personality: r.get(3)?,
        model: r.get(4)?,
        endpoint_url: r.get(5)?,
        greeting: r.get(6)?,
        enabled_tools: r.get(7)?,
        session_id: r.get(8)?,
        is_default_assistant: r.get::<_, Option<bool>>(9)?.unwrap_or(false),
        timezone: r.get(10)?,
    })
}

/// A `scheduled_tasks` row — the columns the check-in dict + next_run recompute
/// touch (mirrors the subset of the SQLAlchemy `ScheduledTask` ORM used here).
#[derive(Clone, Debug)]
struct TaskRow {
    id: String,
    name: Option<String>,
    prompt: Option<String>,
    schedule: Option<String>,
    scheduled_time: Option<String>,
    scheduled_day: Option<i64>,
    scheduled_date: Option<NaiveDateTime>,
    next_run: Option<String>,
    last_run: Option<String>,
    status: Option<String>,
    run_count: Option<i64>,
    cron_expression: Option<String>,
}

const TASK_COLS: &str = "id, name, prompt, schedule, scheduled_time, scheduled_day, \
    scheduled_date, next_run, last_run, status, run_count, cron_expression";

fn map_task(r: &rusqlite::Row<'_>) -> rusqlite::Result<TaskRow> {
    Ok(TaskRow {
        id: r.get(0)?,
        name: r.get(1)?,
        prompt: r.get(2)?,
        schedule: r.get(3)?,
        scheduled_time: r.get(4)?,
        scheduled_day: r.get(5)?,
        scheduled_date: parse_dt(r.get::<_, Option<String>>(6)?.as_deref()),
        next_run: r.get(7)?,
        last_run: r.get(8)?,
        status: r.get(9)?,
        run_count: r.get(10)?,
        cron_expression: r.get(11)?,
    })
}

/// Parse a stored SQLite datetime string (`"%Y-%m-%d %H:%M:%S[.%f]"`) into a
/// `NaiveDateTime`, matching the scheduler's own `parse_dt`. Used for
/// `scheduled_date`, which `compute_next_run` expects as `Option<NaiveDateTime>`.
fn parse_dt(s: Option<&str>) -> Option<NaiveDateTime> {
    let s = s?;
    NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S%.f")
        .or_else(|_| NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S"))
        .ok()
}

/// `_crew_to_dict(c: CrewMember) -> dict`.
fn crew_to_dict(c: &CrewRow) -> Value {
    // `try: tools = json.loads(c.enabled_tools) if c.enabled_tools else []`
    // `except Exception: tools = []`.
    let tools: Vec<String> = match c.enabled_tools.as_deref() {
        Some(s) if !s.is_empty() => serde_json::from_str(s).unwrap_or_default(),
        _ => Vec::new(),
    };
    // `any(t in _EMAIL_TOOLS for t in tools)`.
    let allow_autonomous_email = tools.iter().any(|t| EMAIL_TOOLS.contains(&t.as_str()));
    json!({
        "id": c.id,
        "name": c.name,
        "avatar": c.avatar,
        "personality": c.personality,
        "model": c.model,
        "endpoint_url": c.endpoint_url,
        "greeting": c.greeting,
        "enabled_tools": tools,
        "session_id": c.session_id,
        "is_default_assistant": c.is_default_assistant,
        "timezone": c.timezone,
        "allow_autonomous_email": allow_autonomous_email,
    })
}

/// `_task_to_checkin_dict(t: ScheduledTask) -> dict`.
fn task_to_checkin_dict(t: &TaskRow) -> Value {
    json!({
        "id": t.id,
        "name": t.name,
        "scheduled_time": t.scheduled_time,
        "prompt": t.prompt,
        // `(t.status or "active") == "active"`.
        "enabled": t.status.as_deref().unwrap_or("active") == "active",
        // `t.next_run.isoformat() + "Z" if t.next_run else None`.
        "next_run": iso_z_or_null(t.next_run.as_deref()),
        // `t.last_run.isoformat() + "Z" if t.last_run else None`.
        "last_run": iso_z_or_null(t.last_run.as_deref()),
        // `t.run_count or 0`.
        "run_count": t.run_count.unwrap_or(0),
    })
}

/// `(dt.isoformat() + "Z") if dt else None` — render a stored SQLite datetime
/// with Python's `isoformat`, append `Z`, or `null` when absent/empty.
fn iso_z_or_null(stored: Option<&str>) -> Value {
    match stored.filter(|s| !s.is_empty()) {
        Some(s) => json!(format!("{}Z", crate::pydatetime::to_isoformat(s))),
        None => Value::Null,
    }
}

/// `setup_assistant_routes(task_scheduler) -> APIRouter` — assemble the assistant
/// router. The scheduler dep is reached via `State<AppState>` per the integration
/// contract (NOT a captured factory argument).
///
/// The Python registers, with `prefix="/api/assistant"`:
/// `GET /session`, `GET /settings`, `PATCH /settings`, `POST /run/{task_id}`,
/// `GET /run-status/{task_id}`, `GET /available-timezones`.
pub fn setup_assistant_routes() -> Router<AppState> {
    Router::new()
        .route("/api/assistant/session", get(get_assistant_session))
        .route(
            "/api/assistant/settings",
            get(get_assistant_settings).patch(update_assistant_settings),
        )
        .route("/api/assistant/run/:task_id", post(run_check_in_now))
        .route("/api/assistant/run-status/:task_id", get(run_status))
        .route(
            "/api/assistant/available-timezones",
            get(list_timezones),
        )
}

/// `_owner(request) -> str` — resolve the current user, or raise
/// `HTTPException(401, "Not authenticated")` when there is none.
///
/// `if not owner:` is falsy for both `None` and `""`, so an empty user 401s too.
fn owner_or_401(user: &Option<String>) -> Result<String, HttpException> {
    match user.as_deref().filter(|u| !u.is_empty()) {
        Some(u) => Ok(u.to_string()),
        None => Err(HttpException::new(401, "Not authenticated")),
    }
}

/// `_get_or_create(owner) -> CrewMember` — return the per-owner assistant
/// CrewMember, creating it on demand.
///
/// Hard-rejects synthetic owners with `HTTPException(400, f"Cannot seed assistant
/// for {owner!r}")`. The `{owner!r}` uses Python's `repr`, which single-quotes the
/// string — reproduced here. Seeding delegates to the scheduler's
/// `ensure_assistant_defaults` (idempotent). Returns `None` when the seed failed
/// to produce a row (the Python returns the possibly-`None` `crew`).
async fn get_or_create(
    state: &AppState,
    owner: &str,
) -> Result<Option<CrewRow>, HttpException> {
    // `if not owner or owner in _SYNTHETIC_OWNERS:`
    if owner.is_empty() || is_synthetic_owner(owner) {
        return Err(HttpException::new(
            400,
            format!("Cannot seed assistant for {}", py_repr(owner)),
        ));
    }
    // `crew = db.query(CrewMember).filter(... is_default_assistant == True).first()`.
    if let Some(crew) = query_assistant_crew(owner)? {
        return Ok(Some(crew));
    }
    // Seed lazily — idempotent. The Python ignores the return value (`await
    // task_scheduler.ensure_assistant_defaults(owner)`); a seeding error here
    // would surface in Python as an unhandled exception (500). We mirror that.
    state
        .task_scheduler
        .ensure_assistant_defaults(owner)
        .await
        .map_err(|e| {
            crate::pylog::error(&format!("ensure_assistant_defaults failed: {e}"));
            HttpException::new(500, "Internal Server Error")
        })?;
    // Re-read after seeding.
    query_assistant_crew(owner)
}

/// `db.query(CrewMember).filter(CrewMember.owner == owner, CrewMember.
/// is_default_assistant == True).first()`.
fn query_assistant_crew(owner: &str) -> Result<Option<CrewRow>, HttpException> {
    let conn = session_local()?;
    let sql = format!(
        "SELECT {CREW_COLS} FROM crew_members \
         WHERE owner = ?1 AND is_default_assistant = 1 LIMIT 1"
    );
    conn.query_row(&sql, rusqlite::params![owner], map_crew)
        .optional()
        .map_err(db_500)
}

/// `GET /api/assistant/session` — resolve (or lazily create) the pinned Assistant
/// session for this user.
async fn get_assistant_session(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    let user: Option<String> = user.map(|Extension(CurrentUser(u))| u);
    let owner = owner_or_401(&user)?;
    let crew = get_or_create(&s, &owner).await?;
    // `if not crew or not crew.session_id: raise HTTPException(500, ...)`.
    let crew = match crew {
        Some(c) if c.session_id.as_deref().is_some_and(|sid| !sid.is_empty()) => c,
        _ => {
            return Err(HttpException::new(
                500,
                "Assistant session could not be resolved",
            ))
        }
    };
    Ok(Json(json!({
        "session_id": crew.session_id,
        "crew_member_id": crew.id,
        "name": crew.name,
    }))
    .into_response())
}

/// `GET /api/assistant/settings` — return CrewMember fields + the three check-in
/// task rows + task IDs for logs.
async fn get_assistant_settings(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    let user: Option<String> = user.map(|Extension(CurrentUser(u))| u);
    let owner = owner_or_401(&user)?;
    let crew = get_or_create(&s, &owner).await?;
    // `if not crew: raise HTTPException(500, "Assistant not available")`.
    let crew = match crew {
        Some(c) => c,
        None => return Err(HttpException::new(500, "Assistant not available")),
    };
    // `tasks = db.query(ScheduledTask).filter(owner==owner, crew_member_id==crew.id)
    //          .order_by(ScheduledTask.scheduled_time.asc()).all()`.
    let tasks = query_checkin_tasks(&owner, &crew.id)?;
    Ok(Json(settings_payload(&crew, &tasks)).into_response())
}

/// `PATCH /api/assistant/settings` — update CrewMember fields and/or check-in
/// tasks in one call.
async fn update_assistant_settings(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Json(payload): Json<AssistantSettingsUpdate>,
) -> Result<Response, HttpException> {
    let user: Option<String> = user.map(|Extension(CurrentUser(u))| u);
    let owner = owner_or_401(&user)?;
    let crew = get_or_create(&s, &owner).await?;
    // `if not crew: raise HTTPException(500, "Assistant not available")`.
    let crew = match crew {
        Some(c) => c,
        None => return Err(HttpException::new(500, "Assistant not available")),
    };

    let mut conn = session_local()?;

    // The whole Python PATCH body runs inside ONE `SessionLocal()` session and
    // commits exactly once at the end (`db.commit()`), with an implicit rollback
    // on any exception. Mirror that atomicity with a single rusqlite transaction:
    // every read/UPDATE below runs on `tx`, and we `tx.commit()` only after the
    // timezone block. Any early `?` return drops `tx` un-committed -> rollback,
    // so a mid-handler failure persists NOTHING.
    let tx = conn.transaction().map_err(db_500)?;

    // `crew_db = db.query(CrewMember).filter(CrewMember.id == crew.id).first()`.
    let sql = format!("SELECT {CREW_COLS} FROM crew_members WHERE id = ?1");
    let mut crew_db: CrewRow = match tx
        .query_row(&sql, rusqlite::params![crew.id], map_crew)
        .optional()
        .map_err(db_500)?
    {
        Some(c) => c,
        // `if not crew_db: raise HTTPException(404, "Assistant not found")`.
        None => return Err(HttpException::new(404, "Assistant not found")),
    };

    // --- Update CrewMember fields. ---------------------------------------
    // `if payload.name is not None: crew_db.name = payload.name.strip() or crew_db.name`.
    if let Some(name) = payload.name.as_deref() {
        let stripped = name.trim();
        if !stripped.is_empty() {
            crew_db.name = Some(stripped.to_string());
        }
        // else: keep crew_db.name unchanged (the `or crew_db.name` branch).
    }
    if let Some(avatar) = payload.avatar.as_ref() {
        crew_db.avatar = Some(avatar.clone());
    }
    if let Some(personality) = payload.personality.as_ref() {
        crew_db.personality = Some(personality.clone());
    }
    // `crew_db.model = payload.model or None` — empty string -> NULL.
    if let Some(model) = payload.model.as_ref() {
        crew_db.model = empty_to_none(model);
    }
    // `crew_db.endpoint_url = payload.endpoint_url or None`.
    if let Some(endpoint_url) = payload.endpoint_url.as_ref() {
        crew_db.endpoint_url = empty_to_none(endpoint_url);
    }
    // `crew_db.timezone = payload.timezone or None`.
    if let Some(timezone) = payload.timezone.as_ref() {
        crew_db.timezone = empty_to_none(timezone);
    }

    // --- Tool list: either explicit list, or implicit toggle. ------------
    // `if payload.enabled_tools is not None: crew_db.enabled_tools = json.dumps(...)`.
    if let Some(tools) = payload.enabled_tools.as_ref() {
        crew_db.enabled_tools = Some(serde_json::to_string(tools).unwrap_or_else(|_| "[]".into()));
    }
    // `if payload.allow_autonomous_email is not None:`
    if let Some(allow) = payload.allow_autonomous_email {
        // `try: existing = json.loads(crew_db.enabled_tools) if crew_db.enabled_tools else []`
        // `except Exception: existing = []`.
        let mut existing: Vec<String> = match crew_db.enabled_tools.as_deref() {
            Some(s) if !s.is_empty() => serde_json::from_str(s).unwrap_or_default(),
            _ => Vec::new(),
        };
        if allow {
            // `for t in ("send_email", "reply_to_email"): if t not in existing: existing.append(t)`.
            for t in ["send_email", "reply_to_email"] {
                if !existing.iter().any(|e| e == t) {
                    existing.push(t.to_string());
                }
            }
        } else {
            // `existing = [t for t in existing if t not in _EMAIL_TOOLS]`.
            existing.retain(|t| !EMAIL_TOOLS.contains(&t.as_str()));
        }
        crew_db.enabled_tools = Some(serde_json::to_string(&existing).unwrap_or_else(|_| "[]".into()));
    }

    // `crew_db.updated_at = datetime.utcnow()`.
    let crew_updated_at = crate::pydatetime::utcnow_naive_iso();
    // Persist the CrewMember mutations.
    tx.execute(
        "UPDATE crew_members SET name=?1, avatar=?2, personality=?3, model=?4, \
         endpoint_url=?5, timezone=?6, enabled_tools=?7, updated_at=?8 WHERE id=?9",
        rusqlite::params![
            crew_db.name,
            crew_db.avatar,
            crew_db.personality,
            crew_db.model,
            crew_db.endpoint_url,
            crew_db.timezone,
            crew_db.enabled_tools,
            crew_updated_at,
            crew_db.id,
        ],
    )
    .map_err(db_500)?;

    // --- Update check-in tasks. ------------------------------------------
    // `if payload.check_ins:` — Python truthiness: a present, non-empty list.
    if let Some(check_ins) = payload.check_ins.as_ref() {
        if !check_ins.is_empty() {
            let now_utc = crate::pydatetime::utcnow_naive();
            let tz_name = crew_db.timezone.clone(); // `tz_name = crew_db.timezone or None`
            for ci in check_ins {
                // `task = db.query(ScheduledTask).filter(id==ci.id, owner==owner,
                //          crew_member_id==crew_db.id).first()`.
                let sql = format!(
                    "SELECT {TASK_COLS} FROM scheduled_tasks \
                     WHERE id = ?1 AND owner = ?2 AND crew_member_id = ?3"
                );
                let mut task: TaskRow = match tx
                    .query_row(
                        &sql,
                        rusqlite::params![ci.id, owner, crew_db.id],
                        map_task,
                    )
                    .optional()
                    .map_err(db_500)?
                {
                    Some(t) => t,
                    // `if not task: continue`.
                    None => continue,
                };

                // `if ci.name is not None: task.name = ci.name.strip() or task.name`.
                if let Some(name) = ci.name.as_deref() {
                    let stripped = name.trim();
                    if !stripped.is_empty() {
                        task.name = Some(stripped.to_string());
                    }
                }
                let mut time_changed = false;
                // `if ci.scheduled_time is not None and ci.scheduled_time != task.scheduled_time:`.
                if let Some(st) = ci.scheduled_time.as_ref() {
                    if Some(st) != task.scheduled_time.as_ref() {
                        task.scheduled_time = Some(st.clone());
                        time_changed = true;
                    }
                }
                // `if ci.prompt is not None: task.prompt = ci.prompt`.
                if let Some(prompt) = ci.prompt.as_ref() {
                    task.prompt = Some(prompt.clone());
                }
                // `if ci.enabled is not None: task.status = "active" if ci.enabled else "paused"`.
                if let Some(enabled) = ci.enabled {
                    task.status = Some(if enabled { "active" } else { "paused" }.to_string());
                }
                // `if time_changed or ci.enabled is True: task.next_run = compute_next_run(...)`.
                if time_changed || ci.enabled == Some(true) {
                    // `task.schedule or "daily"`.
                    let schedule = task.schedule.clone().filter(|s| !s.is_empty());
                    let schedule = schedule.unwrap_or_else(|| "daily".to_string());
                    let nr = compute_next_run(
                        Some(&schedule),
                        task.scheduled_time.as_deref(),
                        task.scheduled_day,
                        task.scheduled_date,
                        Some(now_utc),
                        task.cron_expression.as_deref(),
                        tz_name.as_deref(),
                    );
                    task.next_run = nr.map(crate::pydatetime::naive_to_iso);
                }
                // `task.updated_at = datetime.utcnow()`.
                let task_updated_at = crate::pydatetime::utcnow_naive_iso();
                tx.execute(
                    "UPDATE scheduled_tasks SET name=?1, scheduled_time=?2, prompt=?3, \
                     status=?4, next_run=?5, updated_at=?6 WHERE id=?7",
                    rusqlite::params![
                        task.name,
                        task.scheduled_time,
                        task.prompt,
                        task.status,
                        task.next_run,
                        task_updated_at,
                        task.id,
                    ],
                )
                .map_err(db_500)?;
            }
        }
    }

    // --- Timezone change shifts the NEXT run of all check-ins. -----------
    // `if payload.timezone is not None:`.
    if payload.timezone.is_some() {
        let now_utc = crate::pydatetime::utcnow_naive();
        let tz_name = crew_db.timezone.clone(); // `tz_name = crew_db.timezone or None`
        let tasks = {
            let sql = format!(
                "SELECT {TASK_COLS} FROM scheduled_tasks \
                 WHERE owner = ?1 AND crew_member_id = ?2"
            );
            let mut stmt = tx.prepare(&sql).map_err(db_500)?;
            let rows = stmt
                .query_map(rusqlite::params![owner, crew_db.id], map_task)
                .map_err(db_500)?
                .collect::<rusqlite::Result<Vec<_>>>()
                .map_err(db_500)?;
            rows
        };
        for t in tasks {
            // `if t.schedule and t.scheduled_time:`.
            let schedule_ok = t.schedule.as_deref().is_some_and(|s| !s.is_empty());
            let time_ok = t.scheduled_time.as_deref().is_some_and(|s| !s.is_empty());
            if schedule_ok && time_ok {
                let nr = compute_next_run(
                    t.schedule.as_deref(),
                    t.scheduled_time.as_deref(),
                    t.scheduled_day,
                    t.scheduled_date,
                    Some(now_utc),
                    t.cron_expression.as_deref(),
                    tz_name.as_deref(),
                );
                let next_run = nr.map(crate::pydatetime::naive_to_iso);
                tx.execute(
                    "UPDATE scheduled_tasks SET next_run = ?1 WHERE id = ?2",
                    rusqlite::params![next_run, t.id],
                )
                .map_err(db_500)?;
            }
        }
    }

    // `db.commit()` — commit the single transaction holding every UPDATE above.
    // Reaching here means no `?` bailed out, so all mutations land atomically.
    tx.commit().map_err(db_500)?;

    // --- Re-read crew_db + tasks to return the fresh state. --------------
    // `crew_out = db.query(CrewMember).filter(CrewMember.id == crew.id).first()`.
    // Runs on `conn` after the committed `tx` is dropped, so it sees the fresh
    // committed state (the Python re-query on the same post-commit session).
    let crew_out = {
        let sql = format!("SELECT {CREW_COLS} FROM crew_members WHERE id = ?1");
        conn.query_row(&sql, rusqlite::params![crew.id], map_crew)
            .map_err(db_500)?
    };
    let tasks_out = query_checkin_tasks(&owner, &crew.id)?;
    Ok(Json(settings_payload(&crew_out, &tasks_out)).into_response())
}

/// `POST /api/assistant/run/{task_id}` — trigger one of the assistant's check-ins
/// immediately (manual test).
async fn run_check_in_now(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(task_id): Path<String>,
) -> Result<Response, HttpException> {
    let user: Option<String> = user.map(|Extension(CurrentUser(u))| u);
    let owner = owner_or_401(&user)?;
    {
        let conn = session_local()?;
        // `task = db.query(ScheduledTask).filter(id==task_id, owner==owner).first()`.
        let crew_member_id: Option<Option<String>> = conn
            .query_row(
                "SELECT crew_member_id FROM scheduled_tasks WHERE id = ?1 AND owner = ?2",
                rusqlite::params![task_id, owner],
                |r| r.get::<_, Option<String>>(0),
            )
            .optional()
            .map_err(db_500)?;
        // `if not task: raise HTTPException(404, "Task not found")`.
        let crew_member_id = match crew_member_id {
            Some(c) => c,
            None => return Err(HttpException::new(404, "Task not found")),
        };
        // `crew = db.query(CrewMember).filter(id==task.crew_member_id,
        //          is_default_assistant == True).first()`.
        //
        // A `None` crew_member_id never matches `CrewMember.id == None` in
        // SQLAlchemy (it emits `WHERE id IS NULL`, which yields nothing); the
        // `is_default_assistant=1` predicate keeps it `None` anyway.
        let crew_exists: Option<i64> = match crew_member_id.as_deref() {
            Some(cid) => conn
                .query_row(
                    "SELECT 1 FROM crew_members WHERE id = ?1 AND is_default_assistant = 1",
                    rusqlite::params![cid],
                    |r| r.get(0),
                )
                .optional()
                .map_err(db_500)?,
            None => None,
        };
        // `if not crew: raise HTTPException(400, "Not an assistant task")`.
        if crew_exists.is_none() {
            return Err(HttpException::new(400, "Not an assistant task"));
        }
    }
    // `started = await task_scheduler.run_task_now(task_id)` — Python default
    // `force=False`.
    let started = s.task_scheduler.run_task_now(&task_id, false).await;
    Ok(Json(json!({ "started": started })).into_response())
}

/// `GET /api/assistant/run-status/{task_id}` — check whether the most recent run
/// of a task has finished.
async fn run_status(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(task_id): Path<String>,
) -> Result<Response, HttpException> {
    let user: Option<String> = user.map(|Extension(CurrentUser(u))| u);
    let user = owner_or_401(&user)?;
    let conn = session_local()?;

    // SECURITY: 404 if the task doesn't belong to this user.
    // `task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()`.
    let task_owner: Option<Option<String>> = conn
        .query_row(
            "SELECT owner FROM scheduled_tasks WHERE id = ?1",
            rusqlite::params![task_id],
            |r| r.get::<_, Option<String>>(0),
        )
        .optional()
        .map_err(db_500)?;
    // `if not task: raise HTTPException(404, "Task not found")`.
    let task_owner = match task_owner {
        Some(o) => o,
        None => return Err(HttpException::new(404, "Task not found")),
    };
    // `if user and task.owner != user: raise HTTPException(404, "Task not found")`.
    // `user` here is always non-empty (owner_or_401), so the `if user` guard holds.
    if task_owner.as_deref() != Some(user.as_str()) {
        return Err(HttpException::new(404, "Task not found"));
    }

    // `run = db.query(TaskRun).filter(TaskRun.task_id == task_id)
    //        .order_by(TaskRun.started_at.desc()).first()`.
    let run_status: Option<String> = conn
        .query_row(
            "SELECT status FROM task_runs WHERE task_id = ?1 \
             ORDER BY started_at DESC LIMIT 1",
            rusqlite::params![task_id],
            |r| r.get::<_, Option<String>>(0),
        )
        .optional()
        .map_err(db_500)?
        .flatten();

    match run_status {
        // `if not run: return {"status": "unknown"}`.
        None => Ok(Json(json!({ "status": "unknown" })).into_response()),
        Some(status) => {
            // `if run.status == "running": return {"status": "running"}`.
            if status == "running" {
                Ok(Json(json!({ "status": "running" })).into_response())
            } else {
                // `return {"status": "done", "result_status": run.status}`.
                Ok(Json(json!({ "status": "done", "result_status": status })).into_response())
            }
        }
    }
}

/// `GET /api/assistant/available-timezones` — return the IANA tz name list used to
/// populate the settings dropdown.
///
/// Python uses `sorted(zoneinfo.available_timezones())`. The Rust analogue collects
/// the IANA names from `chrono_tz::TZ_VARIANTS`, dedups (the variant array can carry
/// alias names), and sorts. On any failure the Python falls back to `["UTC"]`; this
/// path can't fail, but the fallback shape is preserved in spirit (the list always
/// contains `UTC`).
async fn list_timezones() -> Result<Response, HttpException> {
    let mut set: HashSet<&'static str> = HashSet::new();
    for tz in chrono_tz::TZ_VARIANTS.iter() {
        set.insert(tz.name());
    }
    let mut zones: Vec<&'static str> = set.into_iter().collect();
    zones.sort_unstable();
    Ok(Json(json!({ "timezones": zones })).into_response())
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// `db.query(ScheduledTask).filter(owner==owner, crew_member_id==crew_id)
/// .order_by(ScheduledTask.scheduled_time.asc()).all()`.
fn query_checkin_tasks(owner: &str, crew_id: &str) -> Result<Vec<TaskRow>, HttpException> {
    let conn = session_local()?;
    let sql = format!(
        "SELECT {TASK_COLS} FROM scheduled_tasks \
         WHERE owner = ?1 AND crew_member_id = ?2 ORDER BY scheduled_time ASC"
    );
    let mut stmt = conn.prepare(&sql).map_err(db_500)?;
    let rows = stmt
        .query_map(rusqlite::params![owner, crew_id], map_task)
        .map_err(db_500)?
        .collect::<rusqlite::Result<Vec<_>>>()
        .map_err(db_500)?;
    Ok(rows)
}

/// Build the `{"crew": ..., "check_ins": [...], "task_ids": [...]}` settings dict.
fn settings_payload(crew: &CrewRow, tasks: &[TaskRow]) -> Value {
    json!({
        "crew": crew_to_dict(crew),
        "check_ins": tasks.iter().map(task_to_checkin_dict).collect::<Vec<_>>(),
        "task_ids": tasks.iter().map(|t| t.id.clone()).collect::<Vec<_>>(),
    })
}

/// `x or None` for a Python string field — empty string maps to NULL.
fn empty_to_none(s: &str) -> Option<String> {
    if s.is_empty() {
        None
    } else {
        Some(s.to_string())
    }
}

/// Python's `repr` of a string: single-quoted (the simple case used in the
/// `f"Cannot seed assistant for {owner!r}"` message). Synthetic owners are simple
/// ASCII identifiers / the empty string, so a plain single-quote wrap matches.
fn py_repr(s: &str) -> String {
    format!("'{s}'")
}

/// Open a DB connection (`SessionLocal()`), mapping a failure to a 500.
fn session_local() -> Result<rusqlite::Connection, HttpException> {
    crate::core::database::session_local().map_err(db_500)
}

/// Map a `rusqlite::Error` to a 500 `HttpException`, the way an unhandled
/// exception inside a FastAPI handler surfaces.
fn db_500(e: rusqlite::Error) -> HttpException {
    crate::pylog::error(&format!("assistant_routes DB error: {e}"));
    HttpException::new(500, "Internal Server Error")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn crew_fixture(enabled_tools: Option<&str>) -> CrewRow {
        CrewRow {
            id: "crew-1".to_string(),
            name: Some("Assistant".to_string()),
            avatar: Some("robot".to_string()),
            personality: Some("Helpful".to_string()),
            model: Some("gpt-4".to_string()),
            endpoint_url: None,
            greeting: Some("Hi".to_string()),
            enabled_tools: enabled_tools.map(str::to_string),
            session_id: Some("sess-1".to_string()),
            is_default_assistant: true,
            timezone: Some("America/New_York".to_string()),
        }
    }

    #[test]
    fn crew_to_dict_shape_and_key_order() {
        let d = crew_to_dict(&crew_fixture(Some(r#"["send_email","web_search"]"#)));
        let keys: Vec<&str> = d.as_object().unwrap().keys().map(|k| k.as_str()).collect();
        assert_eq!(
            keys,
            vec![
                "id",
                "name",
                "avatar",
                "personality",
                "model",
                "endpoint_url",
                "greeting",
                "enabled_tools",
                "session_id",
                "is_default_assistant",
                "timezone",
                "allow_autonomous_email",
            ]
        );
        assert_eq!(d["enabled_tools"], json!(["send_email", "web_search"]));
        // `any(t in _EMAIL_TOOLS for t in tools)` — send_email present -> true.
        assert_eq!(d["allow_autonomous_email"], json!(true));
        assert_eq!(d["is_default_assistant"], json!(true));
    }

    #[test]
    fn crew_to_dict_bad_or_empty_tools_defaults_to_empty_list() {
        // `except Exception: tools = []` — invalid JSON.
        let d = crew_to_dict(&crew_fixture(Some("not json")));
        assert_eq!(d["enabled_tools"], json!([]));
        assert_eq!(d["allow_autonomous_email"], json!(false));
        // None / empty -> [].
        let d2 = crew_to_dict(&crew_fixture(None));
        assert_eq!(d2["enabled_tools"], json!([]));
        let d3 = crew_to_dict(&crew_fixture(Some("")));
        assert_eq!(d3["enabled_tools"], json!([]));
    }

    #[test]
    fn crew_to_dict_reply_to_email_also_flags_autonomous() {
        let d = crew_to_dict(&crew_fixture(Some(r#"["reply_to_email"]"#)));
        assert_eq!(d["allow_autonomous_email"], json!(true));
    }

    fn task_fixture() -> TaskRow {
        TaskRow {
            id: "task-1".to_string(),
            name: Some("Morning check-in".to_string()),
            prompt: Some("Good morning".to_string()),
            schedule: Some("daily".to_string()),
            scheduled_time: Some("08:00".to_string()),
            scheduled_day: None,
            scheduled_date: None,
            next_run: Some("2026-06-02 12:00:00".to_string()),
            last_run: Some("2026-06-01 12:00:00.500000".to_string()),
            status: Some("active".to_string()),
            run_count: Some(5),
            cron_expression: None,
        }
    }

    #[test]
    fn task_to_checkin_dict_shape_key_order_and_z_suffix() {
        let d = task_to_checkin_dict(&task_fixture());
        let keys: Vec<&str> = d.as_object().unwrap().keys().map(|k| k.as_str()).collect();
        assert_eq!(
            keys,
            vec![
                "id",
                "name",
                "scheduled_time",
                "prompt",
                "enabled",
                "next_run",
                "last_run",
                "run_count",
            ]
        );
        // isoformat (T separator) + "Z"; no fraction when the stored value has none.
        assert_eq!(d["next_run"], json!("2026-06-02T12:00:00Z"));
        // microseconds preserved, then Z appended.
        assert_eq!(d["last_run"], json!("2026-06-01T12:00:00.500000Z"));
        assert_eq!(d["enabled"], json!(true));
        assert_eq!(d["run_count"], json!(5));
    }

    #[test]
    fn task_to_checkin_dict_status_and_run_count_defaults() {
        let mut t = task_fixture();
        // `(t.status or "active") == "active"` — None status -> enabled true.
        t.status = None;
        t.run_count = None;
        t.next_run = None;
        t.last_run = None;
        let d = task_to_checkin_dict(&t);
        assert_eq!(d["enabled"], json!(true));
        assert_eq!(d["run_count"], json!(0));
        assert_eq!(d["next_run"], Value::Null);
        assert_eq!(d["last_run"], Value::Null);

        // A "paused" status -> enabled false.
        let mut t2 = task_fixture();
        t2.status = Some("paused".to_string());
        assert_eq!(task_to_checkin_dict(&t2)["enabled"], json!(false));
    }

    #[test]
    fn synthetic_owners_match_python_frozenset() {
        for o in ["internal-tool", "api", "demo", "system", ""] {
            assert!(is_synthetic_owner(o), "{o:?} should be synthetic");
        }
        assert!(!is_synthetic_owner("alice"));
        assert!(!is_synthetic_owner("user@example.com"));
    }

    #[test]
    fn py_repr_single_quotes() {
        // `f"Cannot seed assistant for {owner!r}"` — repr single-quotes.
        assert_eq!(py_repr("demo"), "'demo'");
        assert_eq!(py_repr(""), "''");
    }

    #[test]
    fn empty_to_none_maps_empty_string_to_null() {
        // `payload.model or None`.
        assert_eq!(empty_to_none(""), None);
        assert_eq!(empty_to_none("gpt-4"), Some("gpt-4".to_string()));
    }

    #[test]
    fn iso_z_or_null_behaviour() {
        assert_eq!(iso_z_or_null(None), Value::Null);
        assert_eq!(iso_z_or_null(Some("")), Value::Null);
        assert_eq!(
            iso_z_or_null(Some("2026-06-01 08:00:00")),
            json!("2026-06-01T08:00:00Z")
        );
    }

    #[test]
    fn owner_or_401_rejects_none_and_empty() {
        assert!(owner_or_401(&None).is_err());
        assert!(owner_or_401(&Some(String::new())).is_err());
        assert_eq!(owner_or_401(&Some("alice".to_string())).unwrap(), "alice");
        let e = owner_or_401(&None).unwrap_err();
        assert_eq!(e.status_code, 401);
        assert_eq!(e.detail, "Not authenticated");
    }

    #[test]
    fn settings_payload_shape() {
        let crew = crew_fixture(Some("[]"));
        let tasks = vec![task_fixture()];
        let d = settings_payload(&crew, &tasks);
        let keys: Vec<&str> = d.as_object().unwrap().keys().map(|k| k.as_str()).collect();
        assert_eq!(keys, vec!["crew", "check_ins", "task_ids"]);
        assert_eq!(d["task_ids"], json!(["task-1"]));
        assert_eq!(d["check_ins"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn available_timezones_contains_utc_and_is_sorted() {
        let mut set: HashSet<&'static str> = HashSet::new();
        for tz in chrono_tz::TZ_VARIANTS.iter() {
            set.insert(tz.name());
        }
        let mut zones: Vec<&'static str> = set.into_iter().collect();
        zones.sort_unstable();
        assert!(zones.contains(&"UTC"));
        assert!(zones.contains(&"America/New_York"));
        // sorted ascending.
        let mut prev = "";
        for z in &zones {
            assert!(*z >= prev);
            prev = z;
        }
    }

    #[test]
    fn parse_dt_handles_fraction_and_plain() {
        assert!(parse_dt(Some("2026-06-01 08:00:00")).is_some());
        assert!(parse_dt(Some("2026-06-01 08:00:00.123456")).is_some());
        assert!(parse_dt(None).is_none());
        assert!(parse_dt(Some("garbage")).is_none());
    }
}
