// src/event_bus.rs  <- src/event_bus.py
//! Lightweight event bus for triggering automation tasks based on events
//! like session creation, message sends, etc.

use crate::pylog as logger;
use once_cell::sync::Lazy;
use std::sync::{Arc, RwLock};

use crate::src::task_scheduler::TaskScheduler;

/// `_task_scheduler = None` — the module-level scheduler reference.
///
/// Python keeps a single mutable module global; the faithful Rust analogue is a
/// process-global behind a `RwLock` holding an `Arc<TaskScheduler>` (the
/// scheduler's executor methods take `self: &Arc<Self>`).
static TASK_SCHEDULER: Lazy<RwLock<Option<Arc<TaskScheduler>>>> = Lazy::new(|| RwLock::new(None));

/// Wire up the scheduler reference (called from app.py on startup).
pub fn set_task_scheduler(scheduler: Arc<TaskScheduler>) {
    *TASK_SCHEDULER.write().unwrap() = Some(scheduler);
}

/// Return the current task scheduler instance.
pub fn get_task_scheduler() -> Option<Arc<TaskScheduler>> {
    TASK_SCHEDULER.read().unwrap().clone()
}

/// Fire an event — increments counters and triggers tasks that hit threshold.
///
/// Safe to call from both sync and async contexts.
///
/// Python uses `asyncio.get_running_loop().create_task(...)` when inside a loop
/// (fire-and-forget) and `asyncio.run(...)` otherwise. The Rust analogue:
/// `tokio::runtime::Handle::try_current()` -> `handle.spawn(...)` when inside a
/// runtime; otherwise spin up a current-thread runtime and `block_on(...)`
/// (the `asyncio.run` fallback — "shouldn't happen in FastAPI").
pub fn fire_event(event_name: &str, owner: Option<&str>) {
    let event_name = event_name.to_string();
    let owner = owner.map(|s| s.to_string());
    match tokio::runtime::Handle::try_current() {
        Ok(handle) => {
            handle.spawn(async move {
                _handle_event(&event_name, owner.as_deref()).await;
            });
        }
        Err(_) => {
            // No running loop — run in a new one (shouldn't happen in FastAPI).
            if let Ok(rt) = tokio::runtime::Builder::new_current_thread().enable_all().build() {
                rt.block_on(async move {
                    _handle_event(&event_name, owner.as_deref()).await;
                });
            }
        }
    }
}

/// Resolve ownerless app events to the primary configured user.
///
/// Some event sources run from localhost/internal code paths where request
/// middleware is not present, so they cannot pass a username. Treating that as
/// "all owners" made built-in tasks run once per account. Instead, route those
/// events to the first admin account, matching the legacy-owner migration.
fn _resolve_event_owner(owner: Option<&str>) -> Option<String> {
    let owner = owner.unwrap_or("").trim().to_string();
    if !owner.is_empty() {
        return Some(owner);
    }

    // try: read DATA_DIR/auth.json -> users dict; first is_admin, else first user.
    let resolved = (|| -> Option<String> {
        let auth_path = crate::pyos::path::join(&crate::src::constants::DATA_DIR, "auth.json");
        let text = std::fs::read_to_string(&auth_path).ok()?;
        let data: serde_json::Value = serde_json::from_str(&text).ok()?;
        // `(json.load(f).get("users") or {})` — missing/null -> empty map.
        let users = data.get("users").and_then(|u| u.as_object())?;
        // IndexMap/serde_json preserves the file's key order (`next(iter(users))`).
        for (username, udata) in users {
            if udata.get("is_admin").and_then(|v| v.as_bool()) == Some(true) {
                return Some(username.clone());
            }
        }
        if !users.is_empty() {
            return users.keys().next().cloned();
        }
        None
    })();
    if resolved.is_none() {
        logger::debug("Could not resolve ownerless event owner");
    }
    resolved
}

/// Process an event: increment counters, fire tasks that hit their threshold.
///
/// The Python uses the SQLAlchemy `ScheduledTask` model; the raw-SQL analogue
/// runs against `scheduled_tasks` directly (the ORM models in `src/database.rs`
/// are not ported — see that module's header).
///
/// DEVIATION (documented): Python holds the `db` session open across the
/// `await _task_scheduler.run_task_now(task.id)` call. `rusqlite::Connection` is
/// not `Send`, and `fire_event` spawns this future on the tokio runtime (which
/// requires `Send`), so the connection is scoped to the synchronous DB phase
/// (counter bumps + `commit()` analogue — each `execute` autocommits) and
/// **dropped before** the firing phase. The observable order is identical:
/// every threshold task's counter/next_run is persisted, then each is fired in
/// order. The `db.close()` in the Python `finally` happens at the same point.
async fn _handle_event(event_name: &str, owner: Option<&str>) {
    let resolved_owner = _resolve_event_owner(owner);
    let scheduler = get_task_scheduler();

    // ── DB phase: load tasks, bump counters, collect the IDs that fire. ──
    // (Each `execute` is its own autocommit — the Python `db.commit()` analogue.)
    // Each entry: (task_id, task_name, threshold).
    let to_fire: Vec<(String, String, i64)> = {
        // db = SessionLocal(); the connection is dropped at the end of this block.
        let conn = match crate::core::database::session_local() {
            Ok(c) => c,
            Err(e) => {
                logger::error(&format!("Error handling event '{event_name}': {e}"));
                return;
            }
        };

        // (task_id, name, trigger_count, trigger_counter) for active event tasks.
        struct TaskRow {
            id: String,
            name: String,
            trigger_count: Option<i64>,
            trigger_counter: Option<i64>,
        }

        let result: rusqlite::Result<Vec<(String, String, i64)>> = (|| {
            // filters: trigger_type='event' AND trigger_event=? AND status='active'
            // AND (owner = ? | owner IS NULL).
            let (sql, owner_param): (String, Vec<String>) = if let Some(o) = &resolved_owner {
                (
                    "SELECT id, name, trigger_count, trigger_counter FROM scheduled_tasks \
                     WHERE trigger_type = 'event' AND trigger_event = ?1 \
                     AND status = 'active' AND owner = ?2"
                        .to_string(),
                    vec![event_name.to_string(), o.clone()],
                )
            } else {
                (
                    "SELECT id, name, trigger_count, trigger_counter FROM scheduled_tasks \
                     WHERE trigger_type = 'event' AND trigger_event = ?1 \
                     AND status = 'active' AND owner IS NULL"
                        .to_string(),
                    vec![event_name.to_string()],
                )
            };
            let tasks: Vec<TaskRow> = {
                let mut stmt = conn.prepare(&sql)?;
                let params = rusqlite::params_from_iter(owner_param.iter());
                let rows = stmt.query_map(params, |r| {
                    Ok(TaskRow {
                        id: r.get(0)?,
                        name: r.get(1)?,
                        trigger_count: r.get(2)?,
                        trigger_counter: r.get(3)?,
                    })
                })?;
                rows.collect::<rusqlite::Result<Vec<_>>>()?
            };
            // `if not tasks: return` — nothing to fire.
            if tasks.is_empty() {
                return Ok(Vec::new());
            }

            let mut fire: Vec<(String, String, i64)> = Vec::new();
            for task in &tasks {
                // threshold = task.trigger_count or 1
                let threshold = match task.trigger_count {
                    Some(n) if n != 0 => n,
                    _ => 1,
                };
                // task.trigger_counter = (task.trigger_counter or 0) + 1
                let new_counter = task.trigger_counter.unwrap_or(0) + 1;

                if new_counter >= threshold {
                    // task.trigger_counter = 0; task.next_run = utcnow(); db.commit()
                    let now = crate::pydatetime::utcnow_naive_iso();
                    conn.execute(
                        "UPDATE scheduled_tasks SET trigger_counter = 0, next_run = ?1 \
                         WHERE id = ?2",
                        rusqlite::params![now, task.id],
                    )?;
                    fire.push((task.id.clone(), task.name.clone(), threshold));
                } else {
                    // db.commit() — persist the bumped counter.
                    conn.execute(
                        "UPDATE scheduled_tasks SET trigger_counter = ?1 WHERE id = ?2",
                        rusqlite::params![new_counter, task.id],
                    )?;
                    logger::debug(&format!(
                        "Event '{event_name}': task '{}' counter {new_counter}/{threshold}",
                        task.name
                    ));
                }
            }
            Ok(fire)
        })();

        match result {
            Ok(f) => f,
            Err(e) => {
                logger::error(&format!("Error handling event '{event_name}': {e}"));
                return;
            }
        }
        // conn dropped here (db.close()).
    };

    // ── Fire phase: hand off to the in-memory scheduler, in order. ──
    for (task_id, task_name, threshold) in &to_fire {
        if let Some(sched) = &scheduler {
            // `if task.next_run and task.next_run > datetime.utcnow()` — we just
            // set next_run to "now", so the deferred branch never trips in
            // practice (the strict `>` comparison fails). Kept faithful.
            logger::info(&format!(
                "Event '{event_name}' triggered task '{task_name}' (every {threshold})"
            ));
            sched.run_task_now(task_id, false).await;
        } else {
            logger::warning(&format!(
                "Event triggered task '{task_name}' but no scheduler available"
            ));
        }
    }
}

#[cfg(test)]
mod web_tests {
    use super::*;

    #[test]
    fn scheduler_global_default_none() {
        // The module global starts unset (Python `_task_scheduler = None`).
        // (Other tests may set it; this only asserts the accessor type works.)
        let _ = get_task_scheduler();
    }

    #[test]
    fn resolve_event_owner_keeps_explicit() {
        // A non-empty owner is returned trimmed, without touching auth.json.
        assert_eq!(_resolve_event_owner(Some("  bob ")), Some("bob".to_string()));
        assert_eq!(_resolve_event_owner(Some("alice")), Some("alice".to_string()));
    }

    #[tokio::test]
    async fn fire_event_unknown_no_panic() {
        // No matching tasks + no scheduler -> a silent no-op (spawned on the
        // current runtime). This just verifies it does not panic.
        fire_event("__no_such_event__", Some("nobody"));
        // Give the spawned task a tick to run.
        tokio::task::yield_now().await;
    }
}
