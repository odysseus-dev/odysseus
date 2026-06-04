// src/tool_implementations/management_db.rs  <- src/tool_implementations.py
//! The "management_db" group of `do_*` tools: raw-SQL CRUD over the existing
//! SQLite tables (`scheduled_tasks`, `model_endpoints`, `webhooks`,
//! `api_tokens`, `notes`, `calendars`/`calendar_events`).
//!
//! Each tool mirrors its Python counterpart in `src/tool_implementations.py`:
//!   * do_manage_tasks     (L813)
//!   * do_manage_endpoints (L1006)
//!   * do_manage_webhooks  (L1210)
//!   * do_manage_tokens    (L1282)
//!   * do_manage_notes     (L1757)
//!   * do_manage_calendar  (L1951)
//!
//! TOOL RESULT TYPE (per the contract's shared_decisions): every `do_*` returns
//! the Python dict as `serde_json::Map<String, Value>` (insertion-order
//! preserving — `serde_json` has `preserve_order` ON). Internal `try/except`
//! bodies become "catch and return error Map", NOT `?`-propagation: a rusqlite
//! error is converted to `{"error": <e>, "exit_code": 1}` via `super::err_result`,
//! mirroring Python's `except Exception as e: return {"error": str(e), ...}`.
//! The only place we raise is the shared `super::_parse_tool_args` (ValueError on
//! bad JSON), which we catch and turn into `{"error": "Invalid JSON arguments"}`.
//!
//! TIMESTAMPS: Python writes `datetime.utcnow()` into the SQLAlchemy `DateTime`
//! columns; SQLAlchemy serialises that to the SQLite string
//! `"%Y-%m-%d %H:%M:%S%.6f"` (see `crate::pydatetime`). We write the identical
//! string via `pydatetime::utcnow_naive_iso()` and read it back through
//! `pydatetime::to_isoformat` so the `.isoformat()` reproduction matches.
//! SQLAlchemy's `onupdate=` has no SQLite equivalent, so every UPDATE sets
//! `updated_at` explicitly (per the DB ACCESS PATTERN decision).
//!
//! DOCUMENTED DEVIATIONS (DEFERred unported subsystems — honest stubs, never a
//! fabricated success):
//!   * `manage_tasks` next_run computation (`src.task_scheduler.compute_next_run`)
//!     is not ported, so create/edit/resume store `next_run = NULL`. The `run`
//!     action IS wired to `src.event_bus.get_task_scheduler().run_task_now`.
//!     CRUD persists faithfully.
//!   * `manage_webhooks` add-path validators (`src.webhook_manager.validate_events`
//!     / `validate_webhook_url`) are not ported; we inline a faithful minimal
//!     validation (URL must start `http(s)://`; events trimmed) so `add` works.
//!   * `manage_notes` natural-language due-date parse is now WIRED to the
//!     fully-ported `routes.calendar_routes.parse_due_for_user`: a supplied
//!     `due_date` is parsed in the user's tz to ISO-with-offset (Python's
//!     `except`-store-verbatim fallback is preserved because that parser returns
//!     the raw string on failure rather than raising). CRUD persists faithfully.
//!   * `manage_calendar` natural-language / tz-aware date helpers are now WIRED:
//!     `parse_event_dt` delegates to `routes.calendar_routes::parse_dt_pair`,
//!     which ports the full Python chain (strict-ISO fast path → hand-rolled
//!     natural-language phrases → the `dtparse` dateutil fallback). A truly
//!     unparseable date propagates the Python `could not parse datetime: ...`
//!     (and empty-string) failure shape. `_ensure_default_calendar` IS ported
//!     faithfully (create a `Personal` calendar row if none exists for the owner).

use rusqlite::OptionalExtension;
use serde_json::{json, Map, Value};

use crate::pydatetime;
use crate::pylog as logger;

use super::{err_result, _parse_tool_args};

// ---------------------------------------------------------------------------
// Small shared helpers (Python idioms used across this group)
// ---------------------------------------------------------------------------

/// `args.get(key)` as a non-None `Value` reference (Python: `args.get(key)`).
#[inline]
fn arg<'a>(args: &'a Map<String, Value>, key: &str) -> Option<&'a Value> {
    args.get(key).filter(|v| !v.is_null())
}

/// `args.get(key, default_str)` coerced to a Rust `String` when the value is a
/// JSON string. Mirrors Python `.get(key, default)` where the model always
/// passes strings for these fields.
fn arg_str(args: &Map<String, Value>, key: &str) -> Option<String> {
    arg(args, key).and_then(|v| v.as_str().map(|s| s.to_string()))
}

/// `args.get("action", default)` then `.replace("-", "_").strip().lower()` is
/// applied separately where Python does it. This is the plain `.get(... )` path
/// returning the raw action (used by tasks/endpoints/webhooks/tokens which do
/// NOT normalise).
fn raw_action(args: &Map<String, Value>, default: &str) -> String {
    arg_str(args, "action").unwrap_or_else(|| default.to_string())
}

/// `{"error": "Invalid JSON arguments", "exit_code": 1}` — the uniform bad-JSON
/// result every tool in this group returns on a `_parse_tool_args` ValueError.
fn invalid_json() -> Map<String, Value> {
    err_result("Invalid JSON arguments")
}

/// `{"response": <text>, "exit_code": 0, ...extra}` style builder for the common
/// success result. Inserts `response` then `exit_code` (matching Python dict
/// insertion order for the simple two-key results).
fn ok_response(text: impl Into<String>) -> Map<String, Value> {
    let mut m = Map::new();
    m.insert("response".to_string(), Value::String(text.into()));
    m.insert("exit_code".to_string(), Value::from(0));
    m
}

/// Read a SQLite DATETIME column back as Python's `.isoformat()` (or `None`).
/// SQLAlchemy stores `"%Y-%m-%d %H:%M:%S%.6f"`; `pydatetime::to_isoformat`
/// reproduces `datetime.isoformat()` (T-separator, microseconds only when
/// non-zero). The Python here appends `"Z"` to task next_run/last_run.
fn dt_iso_z(stored: &Option<String>) -> Value {
    match stored {
        Some(s) if !s.is_empty() => Value::String(format!("{}Z", pydatetime::to_isoformat(s))),
        _ => Value::Null,
    }
}

// ===========================================================================
// do_manage_tasks  <- L813
// ===========================================================================

/// Handle manage_tasks tool calls: CRUD on scheduled tasks.
pub async fn do_manage_tasks(content: &str, owner: Option<&str>) -> Map<String, Value> {
    let args = match _parse_tool_args(content) {
        Ok(a) => a,
        Err(_) => return invalid_json(),
    };

    let action = raw_action(&args, "list");

    // `run` triggers the scheduler (`run_task_now` is async), so it is handled
    // here in the async fn rather than in the sync `do_manage_tasks_inner`
    // helper. Mirrors src/tool_implementations.py:972-990.
    if action == "run" {
        return match do_manage_tasks_run(&args, owner).await {
            Ok(m) => m,
            Err(e) => {
                logger::error(&format!("manage_tasks error: {e}"));
                err_result(e.to_string())
            }
        };
    }

    match do_manage_tasks_inner(&args, &action, owner) {
        Ok(m) => m,
        Err(e) => {
            // except Exception as e: logger.error(...); return {error,exit_code:1}
            logger::error(&format!("manage_tasks error: {e}"));
            err_result(e.to_string())
        }
    }
}

/// `action == "run"` from `do_manage_tasks`. Trigger a task immediately via the
/// scheduler. The DB lookup + access check (sync) mirror Python lines 973-980;
/// the `run_task_now` call (async) mirrors lines 982-990.
async fn do_manage_tasks_run(
    args: &Map<String, Value>,
    owner: Option<&str>,
) -> rusqlite::Result<Map<String, Value>> {
    // run requires task_id; if absent Python returns its specific error first.
    let task_id = match arg_str(args, "task_id") {
        Some(t) if !t.is_empty() => t,
        _ => return Ok(err_result("task_id is required for run")),
    };
    // Task lookup + access check happen before the scheduler call in Python.
    let found: Option<(Option<String>, Option<String>)> = {
        let conn = crate::core::database::session_local()?;
        conn.query_row(
            "SELECT owner, name FROM scheduled_tasks WHERE id = ?1",
            [&task_id],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .optional()?
    };
    let (t_owner, t_name) = match found {
        Some(v) => v,
        None => return Ok(err_result(format!("Task {task_id} not found"))),
    };
    if let (Some(o), Some(to)) = (owner, t_owner.as_deref()) {
        if !to.is_empty() && to != o {
            return Ok(err_result("Access denied"));
        }
    }
    // scheduler = get_task_scheduler(); if scheduler: started = await
    // scheduler.run_task_now(task_id) (force default False -> false).
    match crate::src::event_bus::get_task_scheduler() {
        Some(sched) => {
            if sched.run_task_now(&task_id, false).await {
                Ok(ok_response(format!("Task '{}' triggered", t_name.unwrap_or_default())))
            } else {
                Ok(err_result("Task is already running"))
            }
        }
        None => Ok(err_result("Task scheduler not available")),
    }
}

fn do_manage_tasks_inner(
    args: &Map<String, Value>,
    action: &str,
    owner: Option<&str>,
) -> rusqlite::Result<Map<String, Value>> {
    let conn = crate::core::database::session_local()?;

    match action {
        "list" => {
            // SELECT ... ORDER BY created_at DESC [WHERE owner = ?]
            let (sql, has_owner) = if owner.is_some() {
                (
                    "SELECT id, name, status, task_type, action, trigger_type, schedule, \
                            trigger_event, trigger_count, next_run, last_run, run_count \
                     FROM scheduled_tasks WHERE owner = ?1 ORDER BY created_at DESC"
                        .to_string(),
                    true,
                )
            } else {
                (
                    "SELECT id, name, status, task_type, action, trigger_type, schedule, \
                            trigger_event, trigger_count, next_run, last_run, run_count \
                     FROM scheduled_tasks ORDER BY created_at DESC"
                        .to_string(),
                    false,
                )
            };
            let mut stmt = conn.prepare(&sql)?;
            let map_row = |r: &rusqlite::Row| -> rusqlite::Result<Value> {
                let id: String = r.get(0)?;
                let name: Option<String> = r.get(1)?;
                let status: Option<String> = r.get(2)?;
                let task_type: Option<String> = r.get(3)?;
                let action_col: Option<String> = r.get(4)?;
                let trigger_type: Option<String> = r.get(5)?;
                let schedule: Option<String> = r.get(6)?;
                let trigger_event: Option<String> = r.get(7)?;
                let trigger_count: Option<i64> = r.get(8)?;
                let next_run: Option<String> = r.get(9)?;
                let last_run: Option<String> = r.get(10)?;
                let run_count: Option<i64> = r.get(11)?;
                let mut t = Map::new();
                t.insert("id".to_string(), Value::String(id));
                t.insert("name".to_string(), name.map(Value::String).unwrap_or(Value::Null));
                t.insert("status".to_string(), status.map(Value::String).unwrap_or(Value::Null));
                // t.task_type or "llm"
                t.insert(
                    "task_type".to_string(),
                    Value::String(task_type.filter(|s| !s.is_empty()).unwrap_or_else(|| "llm".to_string())),
                );
                t.insert("action".to_string(), action_col.map(Value::String).unwrap_or(Value::Null));
                // t.trigger_type or "schedule"
                t.insert(
                    "trigger_type".to_string(),
                    Value::String(
                        trigger_type
                            .filter(|s| !s.is_empty())
                            .unwrap_or_else(|| "schedule".to_string()),
                    ),
                );
                t.insert("schedule".to_string(), schedule.map(Value::String).unwrap_or(Value::Null));
                t.insert(
                    "trigger_event".to_string(),
                    trigger_event.map(Value::String).unwrap_or(Value::Null),
                );
                t.insert(
                    "trigger_count".to_string(),
                    trigger_count.map(Value::from).unwrap_or(Value::Null),
                );
                t.insert("next_run".to_string(), dt_iso_z(&next_run));
                t.insert("last_run".to_string(), dt_iso_z(&last_run));
                // t.run_count or 0
                t.insert("run_count".to_string(), Value::from(run_count.unwrap_or(0)));
                Ok(Value::Object(t))
            };
            let rows: Vec<Value> = if has_owner {
                stmt.query_map([owner.unwrap()], map_row)?.filter_map(|r| r.ok()).collect()
            } else {
                stmt.query_map([], map_row)?.filter_map(|r| r.ok()).collect()
            };
            let n = rows.len();
            let mut m = Map::new();
            m.insert("response".to_string(), Value::String(format!("Found {n} tasks")));
            m.insert("tasks".to_string(), Value::Array(rows));
            m.insert("exit_code".to_string(), Value::from(0));
            Ok(m)
        }

        "create" => {
            let task_type = arg_str(args, "task_type").unwrap_or_else(|| "llm".to_string());
            let trigger_type = arg_str(args, "trigger_type").unwrap_or_else(|| "schedule".to_string());

            // if task_type in ("llm","research") and not args.get("prompt")
            if matches!(task_type.as_str(), "llm" | "research") && arg(args, "prompt").is_none() {
                return Ok(err_result("Prompt is required for llm/research tasks"));
            }
            // if task_type == "action" and not args.get("action_name")
            if task_type == "action" && arg(args, "action_name").is_none() {
                return Ok(err_result("action_name is required for action tasks"));
            }

            // next_run: DEFER compute_next_run -> NULL even for schedule triggers.
            // DEFER: src.task_scheduler.compute_next_run is not ported.
            let next_run: Option<String> = None;

            let task_id = uuid::Uuid::new_v4().to_string();
            // name = args.get("name") or args.get("prompt", args.get("action_name","Task"))[:50]
            let name = match arg_str(args, "name") {
                Some(n) if !n.is_empty() => n,
                _ => {
                    // args.get("prompt", <fallback>); the [:50] applies to whatever
                    // .get returns (prompt present -> prompt; else action_name|"Task").
                    let base = match args.get("prompt") {
                        Some(Value::String(p)) => p.clone(),
                        Some(other) if !other.is_null() => other.to_string(),
                        _ => arg_str(args, "action_name").unwrap_or_else(|| "Task".to_string()),
                    };
                    base.chars().take(50).collect()
                }
            };

            let schedule = if trigger_type == "schedule" { arg_str(args, "schedule") } else { None };
            let scheduled_time = if trigger_type == "schedule" {
                Some(arg_str(args, "scheduled_time").unwrap_or_else(|| "09:00".to_string()))
            } else {
                None
            };
            let scheduled_day: Option<i64> = arg(args, "scheduled_day").and_then(|v| v.as_i64());
            // Python: trigger_event=args.get("trigger_event") — NO trigger_type gate
            // (unlike schedule/scheduled_time, which are nulled off the schedule path).
            let trigger_event = arg_str(args, "trigger_event");
            let trigger_count: Option<i64> = arg(args, "trigger_count").and_then(|v| v.as_i64());
            let output_target = arg_str(args, "output_target").unwrap_or_else(|| "session".to_string());

            let now = pydatetime::utcnow_naive_iso();
            conn.execute(
                "INSERT INTO scheduled_tasks \
                   (id, owner, name, prompt, task_type, action, schedule, scheduled_time, \
                    scheduled_day, trigger_type, trigger_event, trigger_count, trigger_counter, \
                    next_run, status, output_target, run_count, created_at, updated_at) \
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, 0, ?13, 'active', ?14, 0, ?15, ?15)",
                rusqlite::params![
                    task_id,
                    owner,
                    name,
                    arg_str(args, "prompt"),
                    task_type,
                    arg_str(args, "action_name"),
                    schedule,
                    scheduled_time,
                    scheduled_day,
                    trigger_type,
                    trigger_event,
                    trigger_count,
                    next_run,
                    output_target,
                    now,
                ],
            )?;
            let mut m = Map::new();
            m.insert(
                "response".to_string(),
                Value::String(format!("Created task '{name}' (id: {task_id})")),
            );
            m.insert("task_id".to_string(), Value::String(task_id));
            m.insert("exit_code".to_string(), Value::from(0));
            Ok(m)
        }

        "edit" => {
            let task_id = match arg_str(args, "task_id") {
                Some(t) if !t.is_empty() => t,
                _ => return Ok(err_result("task_id is required for edit")),
            };
            // SELECT existing task (owner check + name for response).
            // mirrors Python's positional SELECT row tuple (tool_implementations.py do_manage_tasks edit)
            #[allow(clippy::type_complexity)]
            let found: Option<(Option<String>, Option<String>, Option<String>, Option<String>, Option<String>)> =
                conn.query_row(
                    "SELECT owner, name, trigger_type, scheduled_time, scheduled_day \
                     FROM scheduled_tasks WHERE id = ?1",
                    [&task_id],
                    |r| {
                        Ok((
                            r.get::<_, Option<String>>(0)?,
                            r.get::<_, Option<String>>(1)?,
                            r.get::<_, Option<String>>(2)?,
                            r.get::<_, Option<String>>(3)?,
                            r.get::<_, Option<i64>>(4)?.map(|d| d.to_string()),
                        ))
                    },
                )
                .optional()?;
            let (t_owner, t_name, _t_trig, _t_sched_time, _t_sched_day) = match found {
                Some(v) => v,
                None => return Ok(err_result(format!("Task {task_id} not found"))),
            };
            // if owner and task.owner and task.owner != owner
            if let (Some(o), Some(to)) = (owner, t_owner.as_deref()) {
                if !to.is_empty() && to != o {
                    return Ok(err_result("Access denied"));
                }
            }

            let mut changed: Vec<String> = Vec::new();
            // Build SET clauses with the same field/condition order as Python.
            let mut sets: Vec<String> = Vec::new();
            let mut params: Vec<Box<dyn rusqlite::ToSql>> = Vec::new();

            let mut push_set = |col: &str, val: Value, changed: &mut Vec<String>, label: &str| {
                changed.push(label.to_string());
                match val {
                    Value::String(s) => params.push(Box::new(s)),
                    Value::Number(n) if n.is_i64() => params.push(Box::new(n.as_i64().unwrap())),
                    Value::Bool(b) => params.push(Box::new(b)),
                    Value::Null => params.push(Box::new(Option::<String>::None)),
                    other => params.push(Box::new(other.to_string())),
                }
                sets.push(format!("{col} = ?{}", params.len()));
            };

            // for field in ("name","prompt","output_target")
            for field in ["name", "prompt", "output_target"] {
                if let Some(v) = arg(args, field) {
                    push_set(field, v.clone(), &mut changed, field);
                }
            }
            // task_type -> column task_type, label "task_type"
            if let Some(v) = arg(args, "task_type") {
                push_set("task_type", v.clone(), &mut changed, "task_type");
            }
            // action_name -> column action, label "action"
            if let Some(v) = arg(args, "action_name") {
                push_set("action", v.clone(), &mut changed, "action");
            }
            // trigger_type
            if let Some(v) = arg(args, "trigger_type") {
                push_set("trigger_type", v.clone(), &mut changed, "trigger_type");
            }
            // trigger_event
            if let Some(v) = arg(args, "trigger_event") {
                push_set("trigger_event", v.clone(), &mut changed, "trigger_event");
            }
            // trigger_count
            if let Some(v) = arg(args, "trigger_count") {
                push_set("trigger_count", v.clone(), &mut changed, "trigger_count");
            }
            // schedule fields: schedule, scheduled_time, scheduled_day
            let mut schedule_changed = false;
            for field in ["schedule", "scheduled_time", "scheduled_day"] {
                if let Some(v) = arg(args, field) {
                    push_set(field, v.clone(), &mut changed, field);
                    schedule_changed = true;
                }
            }

            // if schedule_changed and effective trigger_type == "schedule":
            //   task.next_run = compute_next_run(...)  -> DEFER -> set NULL.
            // DEFER: src.task_scheduler.compute_next_run is not ported.
            if schedule_changed {
                // We can't recompute; faithfully clear next_run (would otherwise
                // hold a stale time). Effective trigger_type defaults to schedule.
                let eff_trig = arg_str(args, "trigger_type").or(_t_trig).unwrap_or_else(|| "schedule".to_string());
                if eff_trig == "schedule" {
                    sets.push("next_run = NULL".to_string());
                }
            }

            // Always set updated_at (SQLAlchemy onupdate has no SQLite analogue).
            let now = pydatetime::utcnow_naive_iso();
            params.push(Box::new(now));
            sets.push(format!("updated_at = ?{}", params.len()));

            params.push(Box::new(task_id.clone()));
            let where_idx = params.len();
            let sql = format!(
                "UPDATE scheduled_tasks SET {} WHERE id = ?{}",
                sets.join(", "),
                where_idx
            );
            let param_refs: Vec<&dyn rusqlite::ToSql> = params.iter().map(|b| b.as_ref()).collect();
            conn.execute(&sql, param_refs.as_slice())?;

            let display = t_name.unwrap_or_default();
            Ok(ok_response(format!(
                "Updated task '{display}': {}",
                changed.join(", ")
            )))
        }

        "delete" => {
            let task_id = match arg_str(args, "task_id") {
                Some(t) if !t.is_empty() => t,
                _ => return Ok(err_result("task_id is required for delete")),
            };
            let found: Option<(Option<String>, Option<String>)> = conn
                .query_row(
                    "SELECT owner, name FROM scheduled_tasks WHERE id = ?1",
                    [&task_id],
                    |r| Ok((r.get(0)?, r.get(1)?)),
                )
                .optional()?;
            let (t_owner, t_name) = match found {
                Some(v) => v,
                None => return Ok(err_result(format!("Task {task_id} not found"))),
            };
            if let (Some(o), Some(to)) = (owner, t_owner.as_deref()) {
                if !to.is_empty() && to != o {
                    return Ok(err_result("Access denied"));
                }
            }
            let name = t_name.unwrap_or_default();
            conn.execute("DELETE FROM scheduled_tasks WHERE id = ?1", [&task_id])?;
            Ok(ok_response(format!("Deleted task '{name}'")))
        }

        "pause" | "resume" => {
            let task_id = match arg_str(args, "task_id") {
                Some(t) if !t.is_empty() => t,
                _ => return Ok(err_result(format!("task_id is required for {action}"))),
            };
            let found: Option<(Option<String>, Option<String>, Option<String>)> = conn
                .query_row(
                    "SELECT owner, name, trigger_type FROM scheduled_tasks WHERE id = ?1",
                    [&task_id],
                    |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
                )
                .optional()?;
            let (t_owner, t_name, _t_trig) = match found {
                Some(v) => v,
                None => return Ok(err_result(format!("Task {task_id} not found"))),
            };
            if let (Some(o), Some(to)) = (owner, t_owner.as_deref()) {
                if !to.is_empty() && to != o {
                    return Ok(err_result("Access denied"));
                }
            }
            let now = pydatetime::utcnow_naive_iso();
            if action == "pause" {
                conn.execute(
                    "UPDATE scheduled_tasks SET status = 'paused', updated_at = ?1 WHERE id = ?2",
                    rusqlite::params![now, task_id],
                )?;
            } else {
                // resume: status='active'; if schedule trigger, recompute next_run
                // (DEFER compute_next_run -> NULL).
                // DEFER: src.task_scheduler.compute_next_run is not ported.
                conn.execute(
                    "UPDATE scheduled_tasks SET status = 'active', next_run = NULL, updated_at = ?1 WHERE id = ?2",
                    rusqlite::params![now, task_id],
                )?;
            }
            let name = t_name.unwrap_or_default();
            Ok(ok_response(format!("Task '{name}' {action}d")))
        }

        // "run" is handled by the async `do_manage_tasks_run` (scheduler call is
        // async) before this sync helper is reached.
        other => Ok(err_result(format!("Unknown action: {other}"))),
    }
}

// ===========================================================================
// do_manage_endpoints  <- L1006
// ===========================================================================

/// Manage model endpoints: list, add, delete, enable, disable.
pub async fn do_manage_endpoints(content: &str, _owner: Option<&str>) -> Map<String, Value> {
    let args = match _parse_tool_args(content) {
        Ok(a) => a,
        Err(_) => return invalid_json(),
    };
    let action = raw_action(&args, "list");

    match do_manage_endpoints_inner(&args, &action) {
        Ok(m) => m,
        Err(e) => {
            logger::error(&format!("manage_endpoints error: {e}"));
            err_result(e.to_string())
        }
    }
}

fn do_manage_endpoints_inner(
    args: &Map<String, Value>,
    action: &str,
) -> rusqlite::Result<Map<String, Value>> {
    let conn = crate::core::database::session_local()?;

    match action {
        "list" => {
            // db.query(ModelEndpoint).all() — NO owner filter (matches Python).
            let mut stmt = conn.prepare(
                "SELECT id, name, base_url, is_enabled FROM model_endpoints",
            )?;
            let rows: Vec<Value> = stmt
                .query_map([], |r| {
                    let id: String = r.get(0)?;
                    let name: Option<String> = r.get(1)?;
                    let base_url: Option<String> = r.get(2)?;
                    let is_enabled: Option<bool> = r.get(3)?;
                    let mut m = Map::new();
                    m.insert("id".to_string(), Value::String(id));
                    m.insert("name".to_string(), name.map(Value::String).unwrap_or(Value::Null));
                    m.insert("base_url".to_string(), base_url.map(Value::String).unwrap_or(Value::Null));
                    m.insert(
                        "is_enabled".to_string(),
                        is_enabled.map(Value::Bool).unwrap_or(Value::Null),
                    );
                    Ok(Value::Object(m))
                })?
                .filter_map(|r| r.ok())
                .collect();
            let n = rows.len();
            let mut m = Map::new();
            m.insert("response".to_string(), Value::String(format!("{n} endpoints")));
            m.insert("endpoints".to_string(), Value::Array(rows));
            m.insert("exit_code".to_string(), Value::from(0));
            Ok(m)
        }

        "add" => {
            let name = arg_str(args, "name").unwrap_or_default();
            let base_url = arg_str(args, "base_url").unwrap_or_default();
            let api_key = arg_str(args, "api_key").unwrap_or_default();
            if base_url.is_empty() {
                return Ok(err_result("base_url is required"));
            }
            // eid = str(uuid4())[:8]
            let eid: String = uuid::Uuid::new_v4().to_string().chars().take(8).collect();
            // name or base_url
            let display = if name.is_empty() { base_url.clone() } else { name.clone() };
            let now = pydatetime::utcnow_naive_iso();
            conn.execute(
                "INSERT INTO model_endpoints \
                   (id, name, base_url, api_key, is_enabled, created_at, updated_at) \
                 VALUES (?1, ?2, ?3, ?4, 1, ?5, ?5)",
                rusqlite::params![eid, display, base_url, api_key, now],
            )?;
            Ok(ok_response(format!("Added endpoint '{display}' (id: {eid})")))
        }

        "delete" => {
            let eid = arg_str(args, "endpoint_id").unwrap_or_default();
            let name: Option<Option<String>> = conn
                .query_row(
                    "SELECT name FROM model_endpoints WHERE id = ?1",
                    [&eid],
                    |r| r.get(0),
                )
                .optional()?;
            let name = match name {
                Some(n) => n.unwrap_or_default(),
                None => return Ok(err_result(format!("Endpoint {eid} not found"))),
            };
            conn.execute("DELETE FROM model_endpoints WHERE id = ?1", [&eid])?;
            Ok(ok_response(format!("Deleted endpoint '{name}'")))
        }

        "enable" | "disable" => {
            let eid = arg_str(args, "endpoint_id").unwrap_or_default();
            let name: Option<Option<String>> = conn
                .query_row(
                    "SELECT name FROM model_endpoints WHERE id = ?1",
                    [&eid],
                    |r| r.get(0),
                )
                .optional()?;
            let name = match name {
                Some(n) => n.unwrap_or_default(),
                None => return Ok(err_result(format!("Endpoint {eid} not found"))),
            };
            let enabled = action == "enable";
            let now = pydatetime::utcnow_naive_iso();
            conn.execute(
                "UPDATE model_endpoints SET is_enabled = ?1, updated_at = ?2 WHERE id = ?3",
                rusqlite::params![enabled, now, eid],
            )?;
            Ok(ok_response(format!("Endpoint '{name}' {action}d")))
        }

        other => Ok(err_result(format!("Unknown action: {other}"))),
    }
}

// ===========================================================================
// do_manage_webhooks  <- L1210
// ===========================================================================

/// Manage webhooks: list, add, delete, enable, disable, test.
pub async fn do_manage_webhooks(content: &str, _owner: Option<&str>) -> Map<String, Value> {
    let args = match _parse_tool_args(content) {
        Ok(a) => a,
        Err(_) => return invalid_json(),
    };
    let action = raw_action(&args, "list");

    match do_manage_webhooks_inner(&args, &action) {
        Ok(m) => m,
        Err(e) => {
            logger::error(&format!("manage_webhooks error: {e}"));
            err_result(e.to_string())
        }
    }
}

/// Inline replacement for `src.webhook_manager.validate_webhook_url` (NOT ported).
/// Faithful minimal validation: the URL must be a non-empty http(s):// URL.
fn _validate_webhook_url(url: &str) -> Result<String, String> {
    let u = url.trim();
    if u.is_empty() {
        return Err("url is required".to_string());
    }
    if !(u.starts_with("http://") || u.starts_with("https://")) {
        return Err("Webhook URL must start with http:// or https://".to_string());
    }
    Ok(u.to_string())
}

/// Inline replacement for `src.webhook_manager.validate_events` (NOT ported).
/// Faithful minimal validation: trim the comma-separated event list, drop blanks.
fn _validate_events(events: &str) -> Result<String, String> {
    let cleaned: Vec<&str> = events
        .split(',')
        .map(|e| e.trim())
        .filter(|e| !e.is_empty())
        .collect();
    if cleaned.is_empty() {
        return Err("at least one event is required".to_string());
    }
    Ok(cleaned.join(","))
}

fn do_manage_webhooks_inner(
    args: &Map<String, Value>,
    action: &str,
) -> rusqlite::Result<Map<String, Value>> {
    let conn = crate::core::database::session_local()?;

    match action {
        "list" => {
            // db.query(Webhook).all() — NO owner filter (matches Python).
            let mut stmt =
                conn.prepare("SELECT id, name, url, events, is_active FROM webhooks")?;
            let rows: Vec<Value> = stmt
                .query_map([], |r| {
                    let id: String = r.get(0)?;
                    let name: Option<String> = r.get(1)?;
                    let url: Option<String> = r.get(2)?;
                    let events: Option<String> = r.get(3)?;
                    let is_active: Option<bool> = r.get(4)?;
                    let mut m = Map::new();
                    m.insert("id".to_string(), Value::String(id));
                    m.insert("name".to_string(), name.map(Value::String).unwrap_or(Value::Null));
                    m.insert("url".to_string(), url.map(Value::String).unwrap_or(Value::Null));
                    m.insert("events".to_string(), events.map(Value::String).unwrap_or(Value::Null));
                    m.insert(
                        "is_active".to_string(),
                        is_active.map(Value::Bool).unwrap_or(Value::Null),
                    );
                    Ok(Value::Object(m))
                })?
                .filter_map(|r| r.ok())
                .collect();
            let n = rows.len();
            let mut m = Map::new();
            m.insert("response".to_string(), Value::String(format!("{n} webhooks")));
            m.insert("webhooks".to_string(), Value::Array(rows));
            m.insert("exit_code".to_string(), Value::from(0));
            Ok(m)
        }

        "add" => {
            let name = arg_str(args, "name").unwrap_or_default();
            let url = arg_str(args, "url").unwrap_or_default();
            let events = arg_str(args, "events").unwrap_or_else(|| "chat.completed".to_string());
            if url.is_empty() {
                return Ok(err_result("url is required"));
            }
            // try: url = validate_webhook_url(url); events = validate_events(events)
            // except ValueError as e: return {error: str(e), exit_code: 1}
            let url = match _validate_webhook_url(&url) {
                Ok(u) => u,
                Err(e) => return Ok(err_result(e)),
            };
            let events = match _validate_events(&events) {
                Ok(e) => e,
                Err(e) => return Ok(err_result(e)),
            };
            let wid: String = uuid::Uuid::new_v4().to_string().chars().take(8).collect();
            let display = if name.is_empty() { url.clone() } else { name.clone() };
            let now = pydatetime::utcnow_naive_iso();
            conn.execute(
                "INSERT INTO webhooks (id, name, url, events, is_active, created_at, updated_at) \
                 VALUES (?1, ?2, ?3, ?4, 1, ?5, ?5)",
                rusqlite::params![wid, display, url, events, now],
            )?;
            Ok(ok_response(format!("Added webhook '{display}'")))
        }

        "delete" => {
            let wid = arg_str(args, "webhook_id").unwrap_or_default();
            let name: Option<Option<String>> = conn
                .query_row("SELECT name FROM webhooks WHERE id = ?1", [&wid], |r| r.get(0))
                .optional()?;
            let name = match name {
                Some(n) => n.unwrap_or_default(),
                None => return Ok(err_result(format!("Webhook {wid} not found"))),
            };
            conn.execute("DELETE FROM webhooks WHERE id = ?1", [&wid])?;
            Ok(ok_response(format!("Deleted webhook '{name}'")))
        }

        "enable" | "disable" => {
            let wid = arg_str(args, "webhook_id").unwrap_or_default();
            let name: Option<Option<String>> = conn
                .query_row("SELECT name FROM webhooks WHERE id = ?1", [&wid], |r| r.get(0))
                .optional()?;
            let name = match name {
                Some(n) => n.unwrap_or_default(),
                None => return Ok(err_result(format!("Webhook {wid} not found"))),
            };
            let active = action == "enable";
            let now = pydatetime::utcnow_naive_iso();
            conn.execute(
                "UPDATE webhooks SET is_active = ?1, updated_at = ?2 WHERE id = ?3",
                rusqlite::params![active, now, wid],
            )?;
            Ok(ok_response(format!("Webhook '{name}' {action}d")))
        }

        other => Ok(err_result(format!("Unknown action: {other}"))),
    }
}

// ===========================================================================
// do_manage_tokens  <- L1282
// ===========================================================================

/// Manage API tokens: list, create, delete.
pub async fn do_manage_tokens(content: &str, _owner: Option<&str>) -> Map<String, Value> {
    let args = match _parse_tool_args(content) {
        Ok(a) => a,
        Err(_) => return invalid_json(),
    };
    let action = raw_action(&args, "list");

    match do_manage_tokens_inner(&args, &action) {
        Ok(m) => m,
        Err(e) => {
            logger::error(&format!("manage_tokens error: {e}"));
            err_result(e.to_string())
        }
    }
}

fn do_manage_tokens_inner(
    args: &Map<String, Value>,
    action: &str,
) -> rusqlite::Result<Map<String, Value>> {
    let conn = crate::core::database::session_local()?;

    match action {
        "list" => {
            // db.query(ApiToken).all() — NO owner filter (matches Python).
            let mut stmt =
                conn.prepare("SELECT id, name, token_prefix, is_active FROM api_tokens")?;
            let rows: Vec<Value> = stmt
                .query_map([], |r| {
                    let id: String = r.get(0)?;
                    let name: Option<String> = r.get(1)?;
                    let token_prefix: Option<String> = r.get(2)?;
                    let is_active: Option<bool> = r.get(3)?;
                    let mut m = Map::new();
                    m.insert("id".to_string(), Value::String(id));
                    m.insert("name".to_string(), name.map(Value::String).unwrap_or(Value::Null));
                    // t.token_prefix + "..."  (Python concatenates; prefix is NOT NULL)
                    m.insert(
                        "token_prefix".to_string(),
                        Value::String(format!("{}...", token_prefix.unwrap_or_default())),
                    );
                    m.insert(
                        "is_active".to_string(),
                        is_active.map(Value::Bool).unwrap_or(Value::Null),
                    );
                    Ok(Value::Object(m))
                })?
                .filter_map(|r| r.ok())
                .collect();
            let n = rows.len();
            let mut m = Map::new();
            m.insert("response".to_string(), Value::String(format!("{n} API tokens")));
            m.insert("tokens".to_string(), Value::Array(rows));
            m.insert("exit_code".to_string(), Value::from(0));
            Ok(m)
        }

        "create" => {
            let name = arg_str(args, "name").unwrap_or_else(|| "API Token".to_string());
            // raw_token = secrets.token_urlsafe(32)
            let raw_token = crate::pysecrets::token_urlsafe(32);
            // token_hash = bcrypt.hashpw(raw_token, bcrypt.gensalt())
            let token_hash = match bcrypt::hash(&raw_token, bcrypt::DEFAULT_COST) {
                Ok(h) => h,
                Err(e) => return Ok(err_result(e.to_string())),
            };
            let tid: String = uuid::Uuid::new_v4().to_string().chars().take(8).collect();
            // token_prefix = raw_token[:8]  (char slice)
            let prefix: String = raw_token.chars().take(8).collect();
            let now = pydatetime::utcnow_naive_iso();
            conn.execute(
                "INSERT INTO api_tokens \
                   (id, name, token_hash, token_prefix, is_active, created_at, updated_at) \
                 VALUES (?1, ?2, ?3, ?4, 1, ?5, ?5)",
                rusqlite::params![tid, name, token_hash, prefix, now],
            )?;
            let mut m = Map::new();
            m.insert("response".to_string(), Value::String(format!("Created token '{name}'")));
            m.insert("token".to_string(), Value::String(raw_token));
            m.insert("exit_code".to_string(), Value::from(0));
            Ok(m)
        }

        "delete" => {
            let tid = arg_str(args, "token_id").unwrap_or_default();
            let name: Option<Option<String>> = conn
                .query_row("SELECT name FROM api_tokens WHERE id = ?1", [&tid], |r| r.get(0))
                .optional()?;
            let name = match name {
                Some(n) => n.unwrap_or_default(),
                None => return Ok(err_result(format!("Token {tid} not found"))),
            };
            conn.execute("DELETE FROM api_tokens WHERE id = ?1", [&tid])?;
            Ok(ok_response(format!("Deleted token '{name}'")))
        }

        other => Ok(err_result(format!("Unknown action: {other}"))),
    }
}

// ===========================================================================
// do_manage_notes  <- L1757
// ===========================================================================

/// `_norm_note_title(value)` — lowercased, strip leading "reminder:", collapse
/// whitespace. Matches the nested Python closure (re.sub patterns).
fn _norm_note_title(value: &str) -> String {
    use once_cell::sync::Lazy;
    use regex::Regex;
    // r"^\s*reminder\s*:\s*"
    static LEADING_REMINDER: Lazy<Regex> =
        Lazy::new(|| Regex::new(r"^\s*reminder\s*:\s*").unwrap());
    // r"\s+"
    static WS: Lazy<Regex> = Lazy::new(|| Regex::new(r"\s+").unwrap());
    let text = value.trim().to_lowercase();
    let text = LEADING_REMINDER.replace(&text, "").into_owned();
    WS.replace_all(&text, " ").into_owned()
}

/// Handle manage_notes tool calls: CRUD on notes and checklists.
pub async fn do_manage_notes(content: &str, owner: Option<&str>) -> Map<String, Value> {
    let args = match _parse_tool_args(content) {
        Ok(a) => a,
        Err(_) => return invalid_json(),
    };

    // action = (args.get("action") or "").replace("-","_").strip().lower()
    let raw = arg_str(&args, "action").unwrap_or_default();
    let mut action = raw.replace('-', "_").trim().to_lowercase();
    // _NOTE_ACTION_ALIASES
    action = match action.as_str() {
        "create" | "new" | "save" | "remind" => "add".to_string(),
        "remove" => "delete".to_string(),
        "remove_item" => "toggle_item".to_string(),
        _ => action,
    };

    match do_manage_notes_inner(&args, &action, owner) {
        Ok(m) => m,
        Err(e) => {
            logger::error(&format!("manage_notes error: {e}"));
            err_result(e.to_string())
        }
    }
}

fn do_manage_notes_inner(
    args: &Map<String, Value>,
    action: &str,
    owner: Option<&str>,
) -> rusqlite::Result<Map<String, Value>> {
    let conn = crate::core::database::session_local()?;

    match action {
        "list" => {
            // q = query(Note); if owner is not None: filter owner; if label: filter;
            // filter archived == show_archived; order by pinned DESC, updated_at DESC.
            let label = arg_str(args, "label").filter(|s| !s.is_empty());
            // show_archived = args.get("archived", False) -> truthiness as bool
            let show_archived = args
                .get("archived")
                .map(value_truthy)
                .unwrap_or(false);

            let mut sql = String::from(
                "SELECT id, title, content, items, note_type, pinned, label \
                 FROM notes WHERE archived = ?1",
            );
            let mut params: Vec<Box<dyn rusqlite::ToSql>> = vec![Box::new(show_archived)];
            if let Some(o) = owner {
                params.push(Box::new(o.to_string()));
                sql.push_str(&format!(" AND owner = ?{}", params.len()));
            }
            if let Some(l) = &label {
                params.push(Box::new(l.clone()));
                sql.push_str(&format!(" AND label = ?{}", params.len()));
            }
            sql.push_str(" ORDER BY pinned DESC, updated_at DESC");

            let mut stmt = conn.prepare(&sql)?;
            let param_refs: Vec<&dyn rusqlite::ToSql> = params.iter().map(|b| b.as_ref()).collect();
            // mirrors Python's positional SELECT row tuple (tool_implementations.py do_manage_notes list)
            #[allow(clippy::type_complexity)]
            let rows: Vec<(String, Option<String>, Option<String>, Option<String>, Option<String>, Option<bool>, Option<String>)> =
                stmt.query_map(param_refs.as_slice(), |r| {
                    Ok((
                        r.get(0)?,
                        r.get(1)?,
                        r.get(2)?,
                        r.get(3)?,
                        r.get(4)?,
                        r.get(5)?,
                        r.get(6)?,
                    ))
                })?
                .filter_map(|r| r.ok())
                .collect();

            if rows.is_empty() {
                return Ok(ok_response("No notes found."));
            }
            let mut lines: Vec<String> = Vec::new();
            for (id, title, content_col, items, note_type, pinned, lbl) in &rows {
                let pin = if pinned.unwrap_or(false) { " [PINNED]" } else { "" };
                let typ = if note_type.as_deref() == Some("checklist") {
                    " [checklist]"
                } else {
                    ""
                };
                let lbl_str = match lbl {
                    Some(l) if !l.is_empty() => format!(" #{l}"),
                    _ => String::new(),
                };
                let title_disp = match title {
                    Some(t) if !t.is_empty() => t.clone(),
                    _ => "(untitled)".to_string(),
                };
                let id8: String = id.chars().take(8).collect();
                lines.push(format!("- [{id8}] **{title_disp}**{pin}{typ}{lbl_str}"));
                if note_type.as_deref() == Some("checklist") {
                    if let Some(items_json) = items.as_deref().filter(|s| !s.is_empty()) {
                        if let Ok(Value::Array(arr)) = serde_json::from_str::<Value>(items_json) {
                            for (i, item) in arr.iter().enumerate() {
                                let done = item
                                    .get("done")
                                    .map(value_truthy)
                                    .unwrap_or(false);
                                let mark = if done { "x" } else { " " };
                                let text = item.get("text").and_then(|v| v.as_str()).unwrap_or("");
                                lines.push(format!("  [{mark}] {i}: {text}"));
                            }
                        }
                        // JSONDecodeError/TypeError -> pass (skip silently)
                    }
                } else if let Some(c) = content_col.as_deref().filter(|s| !s.is_empty()) {
                    // n.content[:80].replace("\n", " ")  (char slice then replace)
                    let snippet: String = c.chars().take(80).collect::<String>().replace('\n', " ");
                    lines.push(format!("  {snippet}"));
                }
            }
            // return {"results": "\n".join(lines)}  -- note: NO exit_code key.
            let mut m = Map::new();
            m.insert("results".to_string(), Value::String(lines.join("\n")));
            Ok(m)
        }

        "add" => {
            // title = (args.get("title") or "").strip()
            let mut title = arg_str(args, "title").unwrap_or_default().trim().to_string();
            // content_raw = args.get("content")  (keep "" so the INSERT stores ""
            // verbatim like Python; only the fallback DECISION uses truthiness).
            let mut content_raw = arg_str(args, "content");
            // text_raw = args.get("text") or args.get("body")
            let text_raw = arg_str(args, "text")
                .filter(|s| !s.is_empty())
                .or_else(|| arg_str(args, "body"));
            // Python `not content_raw` is falsy for None AND "" — so an empty
            // content still lets the text/body fallback fire.
            let content_falsy = content_raw.as_deref().map(str::is_empty).unwrap_or(true);
            if title.is_empty() && content_falsy {
                if let Some(tr) = &text_raw {
                    title = tr.trim().to_string();
                }
            } else if content_falsy {
                if let Some(tr) = &text_raw {
                    content_raw = Some(tr.clone());
                }
            }
            // items_raw = args.get("items"); items_json = json.dumps(items_raw) if not None
            let items_raw = args.get("items").filter(|v| !v.is_null());
            let items_json = items_raw.map(|v| serde_json::to_string(v).unwrap_or_else(|_| "null".to_string()));
            // note_type = args.get("note_type", "checklist" if items_raw else "note")
            let note_type = arg_str(args, "note_type").unwrap_or_else(|| {
                if items_raw.is_some() {
                    "checklist".to_string()
                } else {
                    "note".to_string()
                }
            });

            // due_raw = args.get("due_date"); WIRED to the now-ported
            // `routes.calendar_routes.parse_due_for_user` (tool_implementations.py:1840-1847):
            // a non-empty due_date is parsed in the user's tz to ISO-with-offset.
            // Python wraps the call in `try/except -> store verbatim`, but
            // `parse_due_for_user` never panics and already returns the raw
            // string on parse failure (calendar_routes.rs:347/352/409), so the
            // observable "trust the model" fallback is preserved.
            let due_iso = arg_str(args, "due_date")
                .filter(|s| !s.is_empty())
                .map(|s| crate::routes::calendar_routes::parse_due_for_user(&s));

            // Dedup: if due_iso and title, look for an existing un-archived note
            // with the same due_date + normalised title.
            if let (Some(due), false) = (&due_iso, title.is_empty()) {
                let mut sql = String::from(
                    "SELECT id, title FROM notes WHERE archived = 0 AND due_date = ?1",
                );
                let mut params: Vec<Box<dyn rusqlite::ToSql>> = vec![Box::new(due.clone())];
                if let Some(o) = owner {
                    params.push(Box::new(o.to_string()));
                    sql.push_str(&format!(" AND owner = ?{}", params.len()));
                }
                sql.push_str(" LIMIT 25");
                let mut stmt = conn.prepare(&sql)?;
                let param_refs: Vec<&dyn rusqlite::ToSql> = params.iter().map(|b| b.as_ref()).collect();
                let existing: Vec<(String, Option<String>)> = stmt
                    .query_map(param_refs.as_slice(), |r| Ok((r.get(0)?, r.get(1)?)))?
                    .filter_map(|r| r.ok())
                    .collect();
                let target = _norm_note_title(&title);
                for (eid, etitle) in &existing {
                    if _norm_note_title(etitle.as_deref().unwrap_or("")) == target {
                        let id8: String = eid.chars().take(8).collect();
                        let label = etitle.clone().filter(|s| !s.is_empty()).unwrap_or_else(|| title.clone());
                        let mut m = Map::new();
                        m.insert(
                            "response".to_string(),
                            Value::String(format!("Reminder already exists: \"{label}\" (id: {id8})")),
                        );
                        m.insert("note_id".to_string(), Value::String(eid.clone()));
                        m.insert("duplicate".to_string(), Value::Bool(true));
                        m.insert("exit_code".to_string(), Value::from(0));
                        return Ok(m);
                    }
                }
            }

            let note_id = uuid::Uuid::new_v4().to_string();
            let color = arg_str(args, "color");
            let label = arg_str(args, "label");
            // pinned = args.get("pinned", False) truthiness
            let pinned = args.get("pinned").map(value_truthy).unwrap_or(false);
            let session_id = arg_str(args, "session_id");
            let now = pydatetime::utcnow_naive_iso();
            // content stored: content_raw (Option). title stored even if "".
            conn.execute(
                "INSERT INTO notes \
                   (id, owner, title, content, items, note_type, color, label, pinned, \
                    archived, due_date, source, session_id, created_at, updated_at) \
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, 0, ?10, 'agent', ?11, ?12, ?12)",
                rusqlite::params![
                    note_id,
                    owner,
                    title,
                    content_raw,
                    items_json,
                    note_type,
                    color,
                    label,
                    pinned,
                    due_iso,
                    session_id,
                    now,
                ],
            )?;
            let id8: String = note_id.chars().take(8).collect();
            let title_disp = if title.is_empty() { "(untitled)".to_string() } else { title.clone() };
            Ok(ok_response(format!("Note created: \"{title_disp}\" (id: {id8})")))
        }

        "update" => {
            // note = query(Note).filter(Note.id.startswith(note_id)).first() if note_id
            let note_id = arg_str(args, "id").unwrap_or_default();
            if note_id.is_empty() {
                return Ok(err_result(format!("Note '{note_id}' not found")));
            }
            let found: Option<(String, Option<String>, Option<String>)> = conn
                .query_row(
                    "SELECT id, owner, title FROM notes WHERE id LIKE ?1 || '%' LIMIT 1",
                    [&note_id],
                    |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
                )
                .optional()?;
            let (real_id, n_owner, n_title) = match found {
                Some(v) => v,
                None => return Ok(err_result(format!("Note '{note_id}' not found"))),
            };
            // if owner is not None and note.owner and note.owner != owner: "Note not found"
            if let (Some(o), Some(no)) = (owner, n_owner.as_deref()) {
                if !no.is_empty() && no != o {
                    return Ok(err_result("Note not found"));
                }
            }

            let mut sets: Vec<String> = Vec::new();
            let mut params: Vec<Box<dyn rusqlite::ToSql>> = Vec::new();
            // for field in ("title","content","note_type","color","label","due_date")
            for field in ["title", "content", "note_type", "color", "label", "due_date"] {
                if let Some(v) = arg(args, field) {
                    params.push(string_param(v));
                    sets.push(format!("{field} = ?{}", params.len()));
                }
            }
            // if "items" in args and not None: note.items = json.dumps(args["items"])
            if let Some(v) = arg(args, "items") {
                let items_json = serde_json::to_string(v).unwrap_or_else(|_| "null".to_string());
                params.push(Box::new(items_json));
                sets.push(format!("items = ?{}", params.len()));
            }
            // if "pinned" in args: note.pinned = args["pinned"]
            if args.contains_key("pinned") {
                let pinned = args.get("pinned").map(value_truthy).unwrap_or(false);
                params.push(Box::new(pinned));
                sets.push(format!("pinned = ?{}", params.len()));
            }
            // if "archived" in args: note.archived = args["archived"]
            if args.contains_key("archived") {
                let archived = args.get("archived").map(value_truthy).unwrap_or(false);
                params.push(Box::new(archived));
                sets.push(format!("archived = ?{}", params.len()));
            }
            // Always set updated_at.
            let now = pydatetime::utcnow_naive_iso();
            params.push(Box::new(now));
            sets.push(format!("updated_at = ?{}", params.len()));

            params.push(Box::new(real_id.clone()));
            let where_idx = params.len();
            let sql = format!("UPDATE notes SET {} WHERE id = ?{}", sets.join(", "), where_idx);
            let param_refs: Vec<&dyn rusqlite::ToSql> = params.iter().map(|b| b.as_ref()).collect();
            conn.execute(&sql, param_refs.as_slice())?;

            // response uses note.title — but Python reads the post-set value. We
            // recompute: if title was updated use it, else the original.
            let disp = arg_str(args, "title")
                .or(n_title)
                .filter(|s| !s.is_empty())
                .unwrap_or_else(|| "(untitled)".to_string());
            Ok(ok_response(format!("Note updated: \"{disp}\"")))
        }

        "delete" => {
            let note_id = arg_str(args, "id").unwrap_or_default();
            if note_id.is_empty() {
                return Ok(err_result(format!("Note '{note_id}' not found")));
            }
            let found: Option<(String, Option<String>, Option<String>)> = conn
                .query_row(
                    "SELECT id, owner, title FROM notes WHERE id LIKE ?1 || '%' LIMIT 1",
                    [&note_id],
                    |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
                )
                .optional()?;
            let (real_id, n_owner, n_title) = match found {
                Some(v) => v,
                None => return Ok(err_result(format!("Note '{note_id}' not found"))),
            };
            if let (Some(o), Some(no)) = (owner, n_owner.as_deref()) {
                if !no.is_empty() && no != o {
                    return Ok(err_result("Note not found"));
                }
            }
            // title = note.title (then deleted)
            let title_disp = n_title.filter(|s| !s.is_empty()).unwrap_or_else(|| "(untitled)".to_string());
            conn.execute("DELETE FROM notes WHERE id = ?1", [&real_id])?;
            Ok(ok_response(format!("Deleted note: \"{title_disp}\"")))
        }

        "toggle_item" => {
            let note_id = arg_str(args, "id").unwrap_or_default();
            // index = args.get("index", 0)
            let index = args.get("index").and_then(|v| v.as_i64()).unwrap_or(0);
            if note_id.is_empty() {
                return Ok(err_result(format!("Note '{note_id}' not found")));
            }
            let found: Option<(String, Option<String>, Option<String>)> = conn
                .query_row(
                    "SELECT id, owner, items FROM notes WHERE id LIKE ?1 || '%' LIMIT 1",
                    [&note_id],
                    |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
                )
                .optional()?;
            let (real_id, n_owner, n_items) = match found {
                Some(v) => v,
                None => return Ok(err_result(format!("Note '{note_id}' not found"))),
            };
            if let (Some(o), Some(no)) = (owner, n_owner.as_deref()) {
                if !no.is_empty() && no != o {
                    return Ok(err_result("Note not found"));
                }
            }
            // if not note.items: error
            let items_str = match n_items.filter(|s| !s.is_empty()) {
                Some(s) => s,
                None => return Ok(err_result("Note has no checklist items")),
            };
            // items = json.loads(note.items)  -- on bad JSON Python raises -> outer except.
            let mut items: Vec<Value> = match serde_json::from_str::<Value>(&items_str) {
                Ok(Value::Array(a)) => a,
                Ok(_) => Vec::new(),
                Err(e) => return Err(rusqlite::Error::ToSqlConversionFailure(Box::new(e))),
            };
            let len = items.len() as i64;
            if index < 0 || index >= len {
                return Ok(err_result(format!(
                    "Item index {index} out of range (0-{})",
                    len - 1
                )));
            }
            let idx = index as usize;
            // items[index]["done"] = not items[index].get("done", False)
            let cur_done = items[idx].get("done").map(value_truthy).unwrap_or(false);
            let new_done = !cur_done;
            if let Value::Object(o) = &mut items[idx] {
                o.insert("done".to_string(), Value::Bool(new_done));
            }
            let new_items = serde_json::to_string(&Value::Array(items.clone())).unwrap_or_else(|_| "[]".to_string());
            let now = pydatetime::utcnow_naive_iso();
            conn.execute(
                "UPDATE notes SET items = ?1, updated_at = ?2 WHERE id = ?3",
                rusqlite::params![new_items, now, real_id],
            )?;
            let mark = if new_done { "done" } else { "undone" };
            let text = items[idx].get("text").and_then(|v| v.as_str()).unwrap_or("");
            Ok(ok_response(format!("Item '{text}' marked {mark}")))
        }

        other => Ok(err_result(format!(
            "Unknown action: {other}. Use list/add/update/delete/toggle_item"
        ))),
    }
}

// ===========================================================================
// do_manage_calendar  <- L1951
// ===========================================================================

const FALLBACK_OWNER_DEFAULT: &str = "owner@localhost";

/// `routes.calendar_routes.FALLBACK_OWNER` —
/// `os.environ.get("ODYSSEUS_FALLBACK_OWNER", "owner@localhost")`.
fn fallback_owner() -> String {
    crate::pyos::getenv("ODYSSEUS_FALLBACK_OWNER", FALLBACK_OWNER_DEFAULT)
}

/// SQLite DATETIME store-string format (`crate::pydatetime::FMT`, private there).
const SQLITE_DT_FMT: &str = "%Y-%m-%d %H:%M:%S%.6f";

/// Result of parsing an event datetime: the naive value (stored format) plus
/// whether the input carried an explicit timezone (`is_utc`).
struct ParsedDt {
    /// Naive datetime rendered as the SQLite store string.
    stored: String,
    /// True iff the source carried explicit tz (Z / +HH:MM); the stored value
    /// is then naive UTC.
    is_utc: bool,
    /// The underlying naive datetime (for arithmetic + isoformat reproduction).
    dt: chrono::NaiveDateTime,
}

/// `routes.calendar_routes._parse_dt_pair` — now WIRED to the fully-ported
/// parser in `crate::routes::calendar_routes` (strict-ISO fast path → the
/// hand-rolled natural-language phrases → the `dtparse` dateutil fallback).
///
/// Delegates entirely to `calendar_routes::parse_dt_pair`, which returns
/// `(naive_dt, is_utc)` exactly like the Python:
///   * 10-char date `YYYY-MM-DD` -> naive midnight, is_utc=false.
///   * full ISO datetime, tz-aware (`...Z` / `...+09:00`) -> naive UTC, is_utc=true.
///   * full ISO datetime, naive -> naive, is_utc=false.
///   * natural language ("tomorrow at 1pm", "next friday") and dateutil-fuzzy
///     absolute formats -> naive-local, is_utc=false.
/// The empty-string + `could not parse datetime: ...` failure shapes are
/// preserved by propagating the delegate's `Err`.
fn parse_event_dt(raw: &str) -> Result<ParsedDt, String> {
    let (naive, is_utc) = crate::routes::calendar_routes::parse_dt_pair(raw)?;
    Ok(ParsedDt {
        stored: naive.format(SQLITE_DT_FMT).to_string(),
        is_utc,
        dt: naive,
    })
}

/// `_reminder_minutes(raw_args)` — coalesce the reminder aliases, parse durations.
fn reminder_minutes(args: &Map<String, Value>) -> Option<i64> {
    use once_cell::sync::Lazy;
    use regex::Regex;
    // r"\b(remind|reminder|alarm)\b" (re.I)
    static REMIND_WORD: Lazy<Regex> =
        Lazy::new(|| Regex::new(r"(?i)\b(remind|reminder|alarm)\b").unwrap());
    static MIN_RE: Lazy<Regex> =
        Lazy::new(|| Regex::new(r"(?i)(\d+)\s*(?:m|min|minute|minutes)\b").unwrap());
    static HR_RE: Lazy<Regex> =
        Lazy::new(|| Regex::new(r"(?i)(\d+)\s*(?:h|hr|hour|hours)\b").unwrap());

    // raw = first non-empty of the aliases.
    let mut raw: Option<Value> = ["reminder_minutes", "remind_before_minutes", "alarm_minutes", "reminder", "alarm"]
        .iter()
        .find_map(|k| args.get(*k).filter(|v| !v.is_null() && !is_empty_str(v)).cloned());

    // if raw in (None, ""): scan description for reminder words.
    if raw.is_none() {
        let desc = args.get("description").and_then(|v| v.as_str()).unwrap_or("");
        if REMIND_WORD.is_match(desc) {
            raw = Some(Value::String(desc.to_string()));
        }
    }
    // if raw in (None, "", False): return None
    let raw = match raw {
        None => return None,
        Some(Value::Bool(false)) => return None,
        Some(Value::String(s)) if s.is_empty() => return None,
        Some(v) => v,
    };
    // if raw is True: return 10
    if let Value::Bool(true) = raw {
        return Some(10);
    }
    // if isinstance(raw, (int, float)): return max(0, int(raw))
    if let Some(n) = raw.as_i64() {
        return Some(n.max(0));
    }
    if let Some(f) = raw.as_f64() {
        return Some((f as i64).max(0));
    }
    // text = str(raw).strip().lower()
    let text = match &raw {
        Value::String(s) => s.trim().to_lowercase(),
        other => other.to_string().trim().to_lowercase(),
    };
    if matches!(text.as_str(), "none" | "no" | "off" | "false") {
        return None;
    }
    if let Some(c) = MIN_RE.captures(&text) {
        if let Ok(n) = c[1].parse::<i64>() {
            return Some(n.max(0));
        }
    }
    if let Some(c) = HR_RE.captures(&text) {
        if let Ok(n) = c[1].parse::<i64>() {
            return Some((n * 60).max(0));
        }
    }
    // text.isdigit()
    if !text.is_empty() && text.chars().all(|ch| ch.is_ascii_digit()) {
        if let Ok(n) = text.parse::<i64>() {
            return Some(n.max(0));
        }
    }
    None
}

/// `_event_description(raw_args, minutes_before)` — drop a description that is
/// nothing but a reminder phrase when a reminder is set.
fn event_description(args: &Map<String, Value>, minutes_before: Option<i64>) -> String {
    use once_cell::sync::Lazy;
    use regex::Regex;
    let desc = args.get("description").and_then(|v| v.as_str()).unwrap_or("").to_string();
    if minutes_before.is_none() {
        return desc;
    }
    // r"^\s*(?:remind(?:er)?|alarm)\s*:?\s*\d+\s*(?:m|min|minute|minutes|h|hr|hour|hours)\b.*$" (re.I)
    static REMINDER_ONLY: Lazy<Regex> = Lazy::new(|| {
        Regex::new(
            r"(?i)^\s*(?:remind(?:er)?|alarm)\s*:?\s*\d+\s*(?:m|min|minute|minutes|h|hr|hour|hours)\b.*$",
        )
        .unwrap()
    });
    if REMINDER_ONLY.is_match(&desc) {
        String::new()
    } else {
        desc
    }
}

/// `routes.calendar_routes._ensure_default_calendar(db, owner)` — return the
/// owner's calendar id, creating a `Personal` calendar row if none exists.
/// PORT_NOW (pure DB).
fn ensure_default_calendar(
    conn: &rusqlite::Connection,
    owner: Option<&str>,
) -> rusqlite::Result<(String, String)> {
    // owner = owner or FALLBACK_OWNER
    let eff_owner = match owner {
        Some(o) if !o.is_empty() => o.to_string(),
        _ => fallback_owner(),
    };
    let existing: Option<(String, String)> = conn
        .query_row(
            "SELECT id, name FROM calendars WHERE owner = ?1 LIMIT 1",
            [&eff_owner],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .optional()?;
    if let Some((id, name)) = existing {
        return Ok((id, name));
    }
    let id = uuid::Uuid::new_v4().to_string();
    let now = pydatetime::utcnow_naive_iso();
    conn.execute(
        "INSERT INTO calendars (id, owner, name, color, source, created_at, updated_at) \
         VALUES (?1, ?2, 'Personal', '#5b8abf', 'local', ?3, ?3)",
        rusqlite::params![id, eff_owner, now],
    )?;
    Ok((id, "Personal".to_string()))
}

/// Handle manage_calendar tool calls: list/create/update/delete events (local SQLite).
pub async fn do_manage_calendar(content: &str, owner: Option<&str>) -> Map<String, Value> {
    let args = match _parse_tool_args(content) {
        Ok(a) => a,
        Err(_) => return invalid_json(),
    };

    // action = (args.get("action") or "list_events").replace("-","_").strip().lower()
    let raw = arg_str(&args, "action").unwrap_or_default();
    let raw = if raw.is_empty() { "list_events".to_string() } else { raw };
    let mut action = raw.replace('-', "_").trim().to_lowercase();
    action = match action.as_str() {
        "create" => "create_event".to_string(),
        "update" => "update_event".to_string(),
        "delete" => "delete_event".to_string(),
        "list" => "list_events".to_string(),
        _ => action,
    };

    match do_manage_calendar_inner(&args, &action, owner) {
        Ok(m) => m,
        Err(e) => {
            // Python: db.rollback(); logger.error; return {error, exit_code:1}
            logger::error(&format!("manage_calendar error: {e}"));
            err_result(e.to_string())
        }
    }
}

fn do_manage_calendar_inner(
    args: &Map<String, Value>,
    action: &str,
    owner: Option<&str>,
) -> rusqlite::Result<Map<String, Value>> {
    use chrono::{Duration, NaiveDateTime, Utc};
    let conn = crate::core::database::session_local()?;

    // owner_clause for the calendar-join queries: when owner is Some, scope to it.
    let owner_filter = owner.filter(|o| !o.is_empty());

    match action {
        "list_calendars" => {
            ensure_default_calendar(&conn, owner)?;
            // _calendar_query().all()  (owner-scoped if owner is not None)
            let (sql, scoped) = if owner_filter.is_some() {
                ("SELECT name, id FROM calendars WHERE owner = ?1".to_string(), true)
            } else {
                ("SELECT name, id FROM calendars".to_string(), false)
            };
            let mut stmt = conn.prepare(&sql)?;
            let map_row = |r: &rusqlite::Row| -> rusqlite::Result<(String, String)> {
                Ok((r.get(0)?, r.get(1)?))
            };
            let cals: Vec<(String, String)> = if scoped {
                stmt.query_map([owner_filter.unwrap()], map_row)?.filter_map(|r| r.ok()).collect()
            } else {
                stmt.query_map([], map_row)?.filter_map(|r| r.ok()).collect()
            };
            // result = [{"name": c.name, "href": c.id} for c in cals]
            let result: Vec<Value> = cals
                .iter()
                .map(|(name, id)| json!({"name": name, "href": id}))
                .collect();
            let response_text = if !result.is_empty() {
                let mut lines = vec![format!("Found {} calendar(s):", result.len())];
                for (name, id) in &cals {
                    let id8: String = id.chars().take(8).collect();
                    lines.push(format!("- {name} ({id8})"));
                }
                lines.join("\n")
            } else {
                "No calendars found.".to_string()
            };
            let mut m = Map::new();
            m.insert("response".to_string(), Value::String(response_text));
            m.insert("calendars".to_string(), Value::Array(result));
            m.insert("exit_code".to_string(), Value::from(0));
            Ok(m)
        }

        "list_events" => {
            // start_dt / end_dt via _parse_dt (ISO subset).
            let start_dt: NaiveDateTime = match arg_str(args, "start") {
                Some(s) => match parse_event_dt(&s) {
                    Ok(p) => p.dt,
                    Err(e) => return Ok(err_result(format!("Invalid date format: {e}"))),
                },
                None => {
                    // datetime.utcnow().replace(hour=0,...) — today's midnight UTC.
                    Utc::now()
                        .naive_utc()
                        .date()
                        .and_hms_opt(0, 0, 0)
                        .unwrap()
                }
            };
            let end_dt: NaiveDateTime = match arg_str(args, "end") {
                Some(s) => match parse_event_dt(&s) {
                    Ok(p) => p.dt,
                    Err(e) => return Ok(err_result(format!("Invalid date format: {e}"))),
                },
                None => start_dt + Duration::days(14),
            };
            let start_store = start_dt.format(SQLITE_DT_FMT).to_string();
            let end_store = end_dt.format(SQLITE_DT_FMT).to_string();

            // SELECT events JOIN calendars, dtstart<end, dtend>start, status!='cancelled'
            let calendar_filter = arg_str(args, "calendar").filter(|s| !s.is_empty());
            let mut sql = String::from(
                "SELECT e.uid, e.summary, e.dtstart, e.dtend, e.all_day, e.description, \
                        e.location, c.name, e.calendar_id, e.event_type, e.importance, e.is_utc \
                 FROM calendar_events e JOIN calendars c ON e.calendar_id = c.id \
                 WHERE e.dtstart < ?1 AND e.dtend > ?2 AND e.status != 'cancelled'",
            );
            let mut params: Vec<Box<dyn rusqlite::ToSql>> = vec![Box::new(end_store), Box::new(start_store)];
            if let Some(o) = owner_filter {
                params.push(Box::new(o.to_string()));
                sql.push_str(&format!(" AND c.owner = ?{}", params.len()));
            }
            if let Some(cf) = &calendar_filter {
                // (calendar_id == cf) OR (c.name == cf)
                params.push(Box::new(cf.clone()));
                let i1 = params.len();
                params.push(Box::new(cf.clone()));
                let i2 = params.len();
                sql.push_str(&format!(" AND (e.calendar_id = ?{i1} OR c.name = ?{i2})"));
            }
            sql.push_str(" ORDER BY e.dtstart");

            let mut stmt = conn.prepare(&sql)?;
            let param_refs: Vec<&dyn rusqlite::ToSql> = params.iter().map(|b| b.as_ref()).collect();
            let rows: Vec<EventRow> = stmt
                .query_map(param_refs.as_slice(), EventRow::from_row)?
                .filter_map(|r| r.ok())
                .collect();

            let mut events: Vec<Value> = Vec::new();
            for ev in &rows {
                let (s, e) = if ev.all_day {
                    (
                        fmt_date(&ev.dtstart),
                        fmt_date(&ev.dtend),
                    )
                } else {
                    let suffix = if ev.is_utc { "Z" } else { "" };
                    (
                        format!("{}{suffix}", pydatetime::to_isoformat(&ev.dtstart)),
                        format!("{}{suffix}", pydatetime::to_isoformat(&ev.dtend)),
                    )
                };
                events.push(json!({
                    "uid": ev.uid,
                    "summary": ev.summary,
                    "dtstart": s,
                    "dtend": e,
                    "all_day": ev.all_day,
                    "description": ev.description,
                    "location": ev.location,
                    "calendar": ev.calendar_name,
                    "calendar_href": ev.calendar_id,
                    "event_type": ev.event_type,
                    "importance": ev.importance,
                }));
            }
            let start_date = start_dt.format("%Y-%m-%d").to_string();
            let end_date = end_dt.format("%Y-%m-%d").to_string();
            let response_text = if events.is_empty() {
                format!("No events between {start_date} and {end_date}.")
            } else {
                let mut lines = vec![format!(
                    "Found {} event(s) between {start_date} and {end_date}:",
                    events.len()
                )];
                for ev in &events {
                    let when = ev.get("dtstart").and_then(|v| v.as_str()).unwrap_or("");
                    let all_day = ev.get("all_day").and_then(|v| v.as_bool()).unwrap_or(false);
                    let dtend = ev.get("dtend").and_then(|v| v.as_str()).unwrap_or("");
                    let when_str = if all_day {
                        format!("{when} (all day)")
                    } else {
                        format!("{when} -> {dtend}")
                    };
                    let summary = ev.get("summary").and_then(|v| v.as_str()).unwrap_or("");
                    let uid = ev.get("uid").and_then(|v| v.as_str()).unwrap_or("");
                    let mut line = format!("- {when_str}: [{summary}](#event-{uid})");
                    let event_type = ev.get("event_type").and_then(|v| v.as_str()).unwrap_or("");
                    if !event_type.is_empty() {
                        line.push_str(&format!(" #{event_type}"));
                    }
                    let importance = ev.get("importance").and_then(|v| v.as_str()).unwrap_or("");
                    if !importance.is_empty() && importance != "normal" {
                        line.push_str(&format!(" !{importance}"));
                    }
                    let location = ev.get("location").and_then(|v| v.as_str()).unwrap_or("");
                    if !location.is_empty() {
                        line.push_str(&format!(" @ {location}"));
                    }
                    let calendar = ev.get("calendar").and_then(|v| v.as_str()).unwrap_or("");
                    if !calendar.is_empty() {
                        line.push_str(&format!(" ({calendar})"));
                    }
                    let description = ev.get("description").and_then(|v| v.as_str()).unwrap_or("");
                    if !description.is_empty() {
                        let mut desc = description.trim().replace('\n', " ");
                        if desc.chars().count() > 120 {
                            desc = format!("{}...", desc.chars().take(117).collect::<String>());
                        }
                        line.push_str(&format!("\n    {desc}"));
                    }
                    lines.push(line);
                }
                lines.join("\n")
            };
            let mut m = Map::new();
            m.insert("response".to_string(), Value::String(response_text));
            m.insert("events".to_string(), Value::Array(events));
            m.insert("exit_code".to_string(), Value::from(0));
            Ok(m)
        }

        "create_event" => {
            let summary = arg_str(args, "summary").filter(|s| !s.is_empty());
            // dtstart_str = dtstart | start | start_time | when
            let dtstart_str = arg_str(args, "dtstart")
                .filter(|s| !s.is_empty())
                .or_else(|| arg_str(args, "start").filter(|s| !s.is_empty()))
                .or_else(|| arg_str(args, "start_time").filter(|s| !s.is_empty()))
                .or_else(|| arg_str(args, "when").filter(|s| !s.is_empty()));
            let (summary, dtstart_str) = match (summary, dtstart_str) {
                (Some(s), Some(d)) => (s, d),
                _ => return Ok(err_result("summary and dtstart are required")),
            };

            // Resolve calendar: href OR name OR short-id prefix; else default.
            let cal_href = arg_str(args, "calendar_href")
                .filter(|s| !s.is_empty())
                .or_else(|| arg_str(args, "calendar").filter(|s| !s.is_empty()));
            let cal_id: String = match &cal_href {
                Some(h) => {
                    // by id exact
                    let by_id: Option<String> = lookup_calendar_id(&conn, owner_filter, "id = ?", h)?;
                    let found = if by_id.is_some() {
                        by_id
                    } else {
                        // by name (case-insensitive: ilike); fall to id prefix.
                        let by_name = lookup_calendar_id(&conn, owner_filter, "lower(name) = lower(?)", h)?;
                        if by_name.is_some() {
                            by_name
                        } else {
                            lookup_calendar_id(&conn, owner_filter, "id LIKE ? || '%'", h)?
                        }
                    };
                    match found {
                        Some(id) => id,
                        None => ensure_default_calendar(&conn, owner)?.0,
                    }
                }
                None => ensure_default_calendar(&conn, owner)?.0,
            };

            // all_day = bool(args.get("all_day", False))
            let all_day = args.get("all_day").map(value_truthy).unwrap_or(false);
            // dtstart parse
            let dtstart = match parse_event_dt(&dtstart_str) {
                Ok(p) => p,
                Err(e) => return Ok(err_result(format!("Could not parse dtstart {dtstart_str:?}: {e}"))),
            };
            let mut dtstart_is_utc = dtstart.is_utc;
            // dtend
            let dtend_raw = arg_str(args, "dtend")
                .filter(|s| !s.is_empty())
                .or_else(|| arg_str(args, "end").filter(|s| !s.is_empty()))
                .or_else(|| arg_str(args, "end_time").filter(|s| !s.is_empty()));
            let dtend_naive: NaiveDateTime = if let Some(de) = dtend_raw {
                match parse_event_dt(&de) {
                    Ok(p) => {
                        dtstart_is_utc = dtstart_is_utc || p.is_utc;
                        p.dt
                    }
                    Err(e) => return Ok(err_result(format!("Could not parse dtend {de:?}: {e}"))),
                }
            } else {
                // duration: "1h", "30m", "90min", "1hr30m"
                let dur = arg_str(args, "duration").unwrap_or_default().trim().to_lowercase();
                let mut delta: Option<Duration> = None;
                if !dur.is_empty() {
                    use once_cell::sync::Lazy;
                    use regex::Regex;
                    static H: Lazy<Regex> = Lazy::new(|| Regex::new(r"(\d+)\s*(?:h|hr|hours?)").unwrap());
                    static M: Lazy<Regex> = Lazy::new(|| Regex::new(r"(\d+)\s*(?:m|min|minutes?)").unwrap());
                    let h = H.captures(&dur).and_then(|c| c[1].parse::<i64>().ok()).unwrap_or(0);
                    let m = M.captures(&dur).and_then(|c| c[1].parse::<i64>().ok()).unwrap_or(0);
                    let secs = h * 3600 + m * 60;
                    if secs > 0 {
                        delta = Some(Duration::seconds(secs));
                    }
                }
                if let Some(d) = delta {
                    dtstart.dt + d
                } else if all_day {
                    dtstart.dt + Duration::days(1)
                } else {
                    dtstart.dt + Duration::hours(1)
                }
            };

            // Dedup: same dtstart + lower(summary) + status != 'cancelled'.
            let dtstart_store = dtstart.stored.clone();
            let existing: Option<EventDedupRow> = {
                let mut sql = String::from(
                    "SELECT e.uid, e.summary, e.location, e.dtstart, e.all_day, e.is_utc \
                     FROM calendar_events e JOIN calendars c ON e.calendar_id = c.id \
                     WHERE e.dtstart = ?1 AND e.status != 'cancelled' AND lower(e.summary) = lower(?2)",
                );
                let mut params: Vec<Box<dyn rusqlite::ToSql>> =
                    vec![Box::new(dtstart_store.clone()), Box::new(summary.clone())];
                if let Some(o) = owner_filter {
                    params.push(Box::new(o.to_string()));
                    sql.push_str(&format!(" AND c.owner = ?{}", params.len()));
                }
                sql.push_str(" LIMIT 1");
                let mut stmt = conn.prepare(&sql)?;
                let pr: Vec<&dyn rusqlite::ToSql> = params.iter().map(|b| b.as_ref()).collect();
                stmt.query_row(pr.as_slice(), EventDedupRow::from_row).optional()?
            };

            let minutes_before = reminder_minutes(args);

            if let Some(ex) = existing {
                let mut reminder_note_id: Option<String> = None;
                let mut reminder_skipped_reason: Option<String> = None;
                if let Some(mb) = minutes_before {
                    let (nid, reason) = create_calendar_reminder(
                        &conn,
                        owner,
                        owner_filter,
                        &ex.summary,
                        &ex.location,
                        &ex.dtstart,
                        ex.all_day,
                        mb,
                        ex.is_utc,
                    )?;
                    reminder_note_id = nid;
                    reminder_skipped_reason = reason;
                    // db.commit() is implicit (autocommit per statement in rusqlite).
                }
                let reminder_text = match minutes_before {
                    None => String::new(),
                    Some(mb) => {
                        if reminder_note_id.is_some() {
                            format!("; reminder set {mb} min before")
                        } else {
                            format!(
                                "; reminder not set ({})",
                                reminder_skipped_reason.clone().unwrap_or_else(|| "reminder time already passed".to_string())
                            )
                        }
                    }
                };
                let mut m = Map::new();
                m.insert(
                    "response".to_string(),
                    Value::String(format!("Event already exists: '{summary}' on {dtstart_str}{reminder_text}")),
                );
                m.insert("uid".to_string(), Value::String(ex.uid));
                m.insert(
                    "reminder_note_id".to_string(),
                    reminder_note_id.map(Value::String).unwrap_or(Value::Null),
                );
                m.insert(
                    "reminder_skipped_reason".to_string(),
                    reminder_skipped_reason.map(Value::String).unwrap_or(Value::Null),
                );
                m.insert("duplicate".to_string(), Value::Bool(true));
                m.insert("exit_code".to_string(), Value::from(0));
                return Ok(m);
            }

            // event_type: event_type | tag | category | type, else None.
            let event_type = arg_str(args, "event_type")
                .filter(|s| !s.is_empty())
                .or_else(|| arg_str(args, "tag").filter(|s| !s.is_empty()))
                .or_else(|| arg_str(args, "category").filter(|s| !s.is_empty()))
                .or_else(|| arg_str(args, "type").filter(|s| !s.is_empty()));
            let importance = arg_str(args, "importance")
                .filter(|s| !s.is_empty())
                .unwrap_or_else(|| "normal".to_string());

            let uid = uuid::Uuid::new_v4().to_string();
            let location = arg_str(args, "location").unwrap_or_default();
            let desc = event_description(args, minutes_before);
            let rrule = arg_str(args, "rrule").unwrap_or_default();
            let is_utc_col = dtstart_is_utc && !all_day;
            let dtend_store = dtend_naive.format(SQLITE_DT_FMT).to_string();
            let now = pydatetime::utcnow_naive_iso();
            conn.execute(
                "INSERT INTO calendar_events \
                   (uid, calendar_id, summary, description, location, dtstart, dtend, all_day, \
                    is_utc, rrule, event_type, importance, status, created_at, updated_at) \
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, 'confirmed', ?13, ?13)",
                rusqlite::params![
                    uid,
                    cal_id,
                    summary,
                    desc,
                    location,
                    dtstart_store,
                    dtend_store,
                    all_day,
                    is_utc_col,
                    rrule,
                    event_type,
                    importance,
                    now,
                ],
            )?;

            let mut reminder_note_id: Option<String> = None;
            let mut reminder_skipped_reason: Option<String> = None;
            if let Some(mb) = minutes_before {
                let (nid, reason) = create_calendar_reminder(
                    &conn,
                    owner,
                    owner_filter,
                    &summary,
                    &location,
                    &dtstart_store,
                    all_day,
                    mb,
                    dtstart_is_utc && !all_day,
                )?;
                reminder_note_id = nid;
                reminder_skipped_reason = reason;
            }

            let tag_blurb = match &event_type {
                Some(t) => format!(" [{t}]"),
                None => String::new(),
            };
            let reminder_blurb = match minutes_before {
                None => String::new(),
                Some(mb) => {
                    if reminder_note_id.is_some() {
                        format!(" with reminder {mb} min before")
                    } else {
                        format!(
                            " without reminder ({})",
                            reminder_skipped_reason.clone().unwrap_or_else(|| "reminder time already passed".to_string())
                        )
                    }
                }
            };
            let mut m = Map::new();
            m.insert(
                "response".to_string(),
                Value::String(format!(
                    "Created event [{summary}](#event-{uid}){tag_blurb} on {dtstart_str}{reminder_blurb}"
                )),
            );
            m.insert("uid".to_string(), Value::String(uid.clone()));
            m.insert("anchor".to_string(), Value::String(format!("[{summary}](#event-{uid})")));
            m.insert(
                "reminder_note_id".to_string(),
                reminder_note_id.map(Value::String).unwrap_or(Value::Null),
            );
            m.insert(
                "reminder_skipped_reason".to_string(),
                reminder_skipped_reason.map(Value::String).unwrap_or(Value::Null),
            );
            m.insert("exit_code".to_string(), Value::from(0));
            Ok(m)
        }

        "update_event" => {
            let uid = match arg_str(args, "uid").filter(|s| !s.is_empty()) {
                Some(u) => u,
                None => return Ok(err_result("uid is required")),
            };
            // _event_query().filter(uid).first()
            let exists = event_exists(&conn, owner_filter, &uid)?;
            if !exists {
                return Ok(err_result(format!("Event {uid} not found")));
            }
            let mut sets: Vec<String> = Vec::new();
            let mut params: Vec<Box<dyn rusqlite::ToSql>> = Vec::new();
            if let Some(v) = arg(args, "summary") {
                params.push(string_param(v));
                sets.push(format!("summary = ?{}", params.len()));
            }
            if let Some(v) = arg(args, "description") {
                params.push(string_param(v));
                sets.push(format!("description = ?{}", params.len()));
            }
            if let Some(v) = arg(args, "location") {
                params.push(string_param(v));
                sets.push(format!("location = ?{}", params.len()));
            }
            if let Some(v) = arg(args, "dtstart") {
                match parse_event_dt(v.as_str().unwrap_or("")) {
                    Ok(p) => {
                        params.push(Box::new(p.stored));
                        sets.push(format!("dtstart = ?{}", params.len()));
                    }
                    Err(e) => return Ok(err_result(e)),
                }
            }
            if let Some(v) = arg(args, "dtend") {
                match parse_event_dt(v.as_str().unwrap_or("")) {
                    Ok(p) => {
                        params.push(Box::new(p.stored));
                        sets.push(format!("dtend = ?{}", params.len()));
                    }
                    Err(e) => return Ok(err_result(e)),
                }
            }
            if let Some(v) = arg(args, "all_day") {
                params.push(Box::new(value_truthy(v)));
                sets.push(format!("all_day = ?{}", params.len()));
            }
            // _tag = event_type|tag|category|type; if not None: ev.event_type = _tag or None
            let tag = arg_str(args, "event_type")
                .or_else(|| arg_str(args, "tag"))
                .or_else(|| arg_str(args, "category"))
                .or_else(|| arg_str(args, "type"));
            if let Some(t) = tag {
                // _tag or None: empty string -> NULL
                if t.is_empty() {
                    sets.push("event_type = NULL".to_string());
                } else {
                    params.push(Box::new(t));
                    sets.push(format!("event_type = ?{}", params.len()));
                }
            }
            if let Some(v) = arg(args, "importance") {
                params.push(string_param(v));
                sets.push(format!("importance = ?{}", params.len()));
            }
            // Always set updated_at.
            let now = pydatetime::utcnow_naive_iso();
            params.push(Box::new(now));
            sets.push(format!("updated_at = ?{}", params.len()));

            params.push(Box::new(uid.clone()));
            let where_idx = params.len();
            let sql = format!("UPDATE calendar_events SET {} WHERE uid = ?{}", sets.join(", "), where_idx);
            let pr: Vec<&dyn rusqlite::ToSql> = params.iter().map(|b| b.as_ref()).collect();
            conn.execute(&sql, pr.as_slice())?;
            Ok(ok_response(format!("Updated event {uid}")))
        }

        "delete_event" => {
            let uid = match arg_str(args, "uid").filter(|s| !s.is_empty()) {
                Some(u) => u,
                None => return Ok(err_result("uid is required")),
            };
            let exists = event_exists(&conn, owner_filter, &uid)?;
            if !exists {
                return Ok(err_result(format!("Event {uid} not found")));
            }
            conn.execute("DELETE FROM calendar_events WHERE uid = ?1", [&uid])?;
            Ok(ok_response(format!("Deleted event {uid}")))
        }

        other => Ok(err_result(format!(
            "Unknown action: {other}. Use list_events, create_event, update_event, delete_event, list_calendars"
        ))),
    }
}

/// Row for the `list_events` SELECT.
struct EventRow {
    uid: String,
    summary: String,
    dtstart: String,
    dtend: String,
    all_day: bool,
    description: String,
    location: String,
    calendar_name: String,
    calendar_id: String,
    event_type: String,
    importance: String,
    is_utc: bool,
}

impl EventRow {
    fn from_row(r: &rusqlite::Row) -> rusqlite::Result<Self> {
        Ok(EventRow {
            uid: r.get(0)?,
            summary: r.get::<_, Option<String>>(1)?.unwrap_or_default(),
            dtstart: r.get(2)?,
            dtend: r.get(3)?,
            all_day: r.get::<_, Option<bool>>(4)?.unwrap_or(false),
            description: r.get::<_, Option<String>>(5)?.unwrap_or_default(),
            location: r.get::<_, Option<String>>(6)?.unwrap_or_default(),
            calendar_name: r.get::<_, Option<String>>(7)?.unwrap_or_default(),
            calendar_id: r.get::<_, Option<String>>(8)?.unwrap_or_default(),
            event_type: r.get::<_, Option<String>>(9)?.unwrap_or_default(),
            // importance or "normal"
            importance: r
                .get::<_, Option<String>>(10)?
                .filter(|s| !s.is_empty())
                .unwrap_or_else(|| "normal".to_string()),
            is_utc: r.get::<_, Option<bool>>(11)?.unwrap_or(false),
        })
    }
}

/// Row for the create_event dedup SELECT.
struct EventDedupRow {
    uid: String,
    summary: String,
    location: String,
    dtstart: String,
    all_day: bool,
    is_utc: bool,
}

impl EventDedupRow {
    fn from_row(r: &rusqlite::Row) -> rusqlite::Result<Self> {
        Ok(EventDedupRow {
            uid: r.get(0)?,
            summary: r.get::<_, Option<String>>(1)?.unwrap_or_default(),
            location: r.get::<_, Option<String>>(2)?.unwrap_or_default(),
            dtstart: r.get(3)?,
            all_day: r.get::<_, Option<bool>>(4)?.unwrap_or(false),
            is_utc: r.get::<_, Option<bool>>(5)?.unwrap_or(false),
        })
    }
}

/// `_calendar_query().filter(<clause>).first()` -> calendar id, owner-scoped.
fn lookup_calendar_id(
    conn: &rusqlite::Connection,
    owner_filter: Option<&str>,
    clause: &str,
    value: &str,
) -> rusqlite::Result<Option<String>> {
    let mut sql = format!("SELECT id FROM calendars WHERE {clause}");
    let mut params: Vec<Box<dyn rusqlite::ToSql>> = vec![Box::new(value.to_string())];
    if let Some(o) = owner_filter {
        params.push(Box::new(o.to_string()));
        sql.push_str(&format!(" AND owner = ?{}", params.len()));
    }
    sql.push_str(" LIMIT 1");
    let pr: Vec<&dyn rusqlite::ToSql> = params.iter().map(|b| b.as_ref()).collect();
    conn.query_row(&sql, pr.as_slice(), |r| r.get::<_, String>(0)).optional()
}

/// `_event_query().filter(uid).first()` existence check (owner-scoped via join).
fn event_exists(
    conn: &rusqlite::Connection,
    owner_filter: Option<&str>,
    uid: &str,
) -> rusqlite::Result<bool> {
    let mut sql = String::from(
        "SELECT 1 FROM calendar_events e JOIN calendars c ON e.calendar_id = c.id WHERE e.uid = ?1",
    );
    let mut params: Vec<Box<dyn rusqlite::ToSql>> = vec![Box::new(uid.to_string())];
    if let Some(o) = owner_filter {
        params.push(Box::new(o.to_string()));
        sql.push_str(&format!(" AND c.owner = ?{}", params.len()));
    }
    sql.push_str(" LIMIT 1");
    let pr: Vec<&dyn rusqlite::ToSql> = params.iter().map(|b| b.as_ref()).collect();
    Ok(conn.query_row(&sql, pr.as_slice(), |_| Ok(())).optional()?.is_some())
}

/// `_create_calendar_reminder(...)` — create a Note reminder (or return the id of
/// an existing duplicate). `dtstart_store` is the stored datetime string.
/// Returns `(note_id, skipped_reason)`.
#[allow(clippy::too_many_arguments)]
fn create_calendar_reminder(
    conn: &rusqlite::Connection,
    owner: Option<&str>,
    owner_filter: Option<&str>,
    summary: &str,
    location: &str,
    dtstart_store: &str,
    all_day: bool,
    minutes_before: i64,
    is_utc: bool,
) -> rusqlite::Result<(Option<String>, Option<String>)> {
    use chrono::{Duration, NaiveDateTime, Utc};
    // Parse the stored dtstart back to a NaiveDateTime.
    let dtstart = NaiveDateTime::parse_from_str(dtstart_store, "%Y-%m-%d %H:%M:%S%.f")
        .or_else(|_| NaiveDateTime::parse_from_str(dtstart_store, "%Y-%m-%d %H:%M:%S"))
        .unwrap_or_else(|_| Utc::now().naive_utc());
    let mut remind_at = dtstart - Duration::minutes(minutes_before);
    // now = datetime.utcnow() if is_utc else datetime.now()
    // (server-local vs UTC). We use naive UTC for both — documented drift: when
    // is_utc is False the Python compares against the server's LOCAL clock, but
    // the stored dtstart for naive events was written from a naive parse, so the
    // comparison stays internally consistent under naive-UTC.
    let now = Utc::now().naive_utc();
    if dtstart <= now {
        return Ok((None, Some("event already passed".to_string())));
    }
    if remind_at <= now {
        remind_at = now;
    }
    // start_fmt
    let start_fmt = if all_day {
        dtstart.format("%a %b %d").to_string()
    } else {
        dtstart.format("%a %b %d %H:%M").to_string()
    };
    let loc = if location.is_empty() { String::new() } else { format!(" @ {location}") };
    let text = format!("{summary}{loc} — {start_fmt}");
    // due_date = remind_at.isoformat() + ("Z" if is_utc else "")
    let due_date = format!("{}{}", pydatetime::to_isoformat(&remind_at.format("%Y-%m-%d %H:%M:%S%.6f").to_string()), if is_utc { "Z" } else { "" });
    let expected_title = format!("Reminder: {summary}");

    // Dedup against existing un-archived notes with the same due_date + title.
    let target_title = strip_reminder_prefix(&expected_title);
    {
        let mut sql = String::from("SELECT id, title FROM notes WHERE archived = 0 AND due_date = ?1");
        let mut params: Vec<Box<dyn rusqlite::ToSql>> = vec![Box::new(due_date.clone())];
        if let Some(o) = owner_filter {
            params.push(Box::new(o.to_string()));
            sql.push_str(&format!(" AND owner = ?{}", params.len()));
        }
        sql.push_str(" LIMIT 25");
        let mut stmt = conn.prepare(&sql)?;
        let pr: Vec<&dyn rusqlite::ToSql> = params.iter().map(|b| b.as_ref()).collect();
        let rows: Vec<(String, Option<String>)> = stmt
            .query_map(pr.as_slice(), |r| Ok((r.get(0)?, r.get(1)?)))?
            .filter_map(|r| r.ok())
            .collect();
        for (eid, etitle) in rows {
            if strip_reminder_prefix(etitle.as_deref().unwrap_or("")) == target_title {
                return Ok((Some(eid), Some("duplicate reminder already exists".to_string())));
            }
        }
    }

    let note_id = uuid::Uuid::new_v4().to_string();
    let items_json = serde_json::to_string(&json!([{"text": text, "done": false, "checked": false}]))
        .unwrap_or_else(|_| "[]".to_string());
    let now_iso = pydatetime::utcnow_naive_iso();
    conn.execute(
        "INSERT INTO notes \
           (id, owner, title, items, note_type, label, due_date, source, pinned, archived, created_at, updated_at) \
         VALUES (?1, ?2, ?3, ?4, 'todo', 'calendar', ?5, 'calendar', 0, 0, ?6, ?6)",
        rusqlite::params![note_id, owner, expected_title, items_json, due_date, now_iso],
    )?;
    Ok((Some(note_id), None))
}

/// `re.sub(r"^\s*reminder\s*:\s*", "", s.strip().lower())`.
fn strip_reminder_prefix(s: &str) -> String {
    use once_cell::sync::Lazy;
    use regex::Regex;
    static RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^\s*reminder\s*:\s*").unwrap());
    RE.replace(&s.trim().to_lowercase(), "").into_owned()
}

// ---------------------------------------------------------------------------
// Misc value helpers
// ---------------------------------------------------------------------------

/// Python truthiness for a JSON value (used where Python does `if value:` or
/// passes a `.get(...)` straight into a boolean column).
fn value_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `raw in (None, "")` test for the reminder-alias coalesce.
fn is_empty_str(v: &Value) -> bool {
    matches!(v, Value::String(s) if s.is_empty())
}

/// Bind a JSON value as a SQL param, preserving its scalar type (string / int /
/// bool / null). Used by the dynamic UPDATE builders.
fn string_param(v: &Value) -> Box<dyn rusqlite::ToSql> {
    match v {
        Value::String(s) => Box::new(s.clone()),
        Value::Number(n) if n.is_i64() => Box::new(n.as_i64().unwrap()),
        Value::Number(n) if n.is_f64() => Box::new(n.as_f64().unwrap()),
        Value::Bool(b) => Box::new(*b),
        Value::Null => Box::new(Option::<String>::None),
        other => Box::new(other.to_string()),
    }
}

/// `dt.strftime("%Y-%m-%d")` over a stored datetime string (all-day events).
fn fmt_date(stored: &str) -> String {
    use chrono::NaiveDateTime;
    let parsed = NaiveDateTime::parse_from_str(stored, "%Y-%m-%d %H:%M:%S%.f")
        .or_else(|_| NaiveDateTime::parse_from_str(stored, "%Y-%m-%d %H:%M:%S"));
    match parsed {
        Ok(dt) => dt.format("%Y-%m-%d").to_string(),
        Err(_) => stored.chars().take(10).collect(),
    }
}
