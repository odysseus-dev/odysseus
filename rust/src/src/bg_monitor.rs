// src/bg_monitor.rs  <- src/bg_monitor.py
//! Always-on monitor that auto-continues the agent when a background job
//! (see `bg_jobs.rs`) finishes.
//!
//! Reliability is the whole point: completion -> agent re-invocation must never
//! silently no-op. The monitor drains `bg_jobs::pending_followups()` every tick
//! and only calls `mark_followed_up()` AFTER the agent run succeeds — so a
//! transient failure is simply retried on the next tick. A timed-out/dead job
//! still produces a follow-up ("the job failed/timed out"), so the user always
//! hears back.
//!
//! PORT_NOW (unix). The Python `asyncio`-task monitor becomes a `tokio`
//! task. `cfg(unix)` mirrors the `bg_jobs` gate (this
//! monitor consumes `bg_jobs::{pending_followups, result_text, mark_followed_up}`,
//! which are Unix-only via the `nix` subprocess layer).
//!
//! DOCUMENTED DEVIATION — the SessionManager global. The Python pulls the live
//! session manager from `src.ai_interaction.get_session_manager()`. The Rust
//! `ai_interaction` port exposes ONLY `dispatch_ai_tool` (no session-manager
//! accessor), and `core::models::session_manager()` is private and returns the
//! narrow `Arc<dyn SessionPersistence>` (only `_persist_message`, NOT
//! `get_session` / `get_context_messages` / `add_message` / `save_sessions`).
//! This monitor needs the CONCRETE [`SessionManager`]. So this module OWNS a new
//! `Lazy<RwLock<Option<Arc<SessionManager>>>>` global with
//! [`set_session_manager`] / [`get_session_manager`]; `app_initializer` calls the
//! setter alongside `core::models::set_session_manager`. This is a faithful
//! analogue of the Python `ai_interaction` global, just relocated to its primary
//! consumer.

#[cfg(unix)]
use std::sync::{Arc, RwLock};

#[cfg(unix)]
use once_cell::sync::Lazy;
#[cfg(unix)]
use serde_json::{json, Map, Value};

#[cfg(unix)]
use crate::core::models::ChatMessage;
#[cfg(unix)]
use crate::core::session_manager::SessionManager;
#[cfg(unix)]
use crate::pylog as logger;

/// `_monitor_task = None` — the singleton monitor `JoinHandle` (idempotency
/// guard for [`start_bg_monitor`]).
#[cfg(unix)]
static MONITOR_TASK: Lazy<std::sync::Mutex<Option<tokio::task::JoinHandle<()>>>> =
    Lazy::new(|| std::sync::Mutex::new(None));

/// `POLL_INTERVAL_S = 5`.
#[cfg(unix)]
pub const POLL_INTERVAL_S: u64 = 5;

/// `_FOLLOWUP_MAX_ROUNDS = 12`. The follow-up agent run is allowed a few rounds
/// to actually continue the task (e.g. after `pip install` finishes, run the
/// transcription).
#[cfg(unix)]
const FOLLOWUP_MAX_ROUNDS: i64 = 12;

// ---------------------------------------------------------------------------
// Concrete SessionManager global (see the module docstring — the deliberate
// relocation of Python's `ai_interaction.get_session_manager`).
// ---------------------------------------------------------------------------

/// The live, concrete [`SessionManager`] used by the monitor (and set by
/// `app_initializer`). `None` until wired — matching the Python's
/// pre-initialization `_session_manager = None`.
#[cfg(unix)]
static SESSION_MANAGER: Lazy<RwLock<Option<Arc<SessionManager>>>> =
    Lazy::new(|| RwLock::new(None));

/// Register the concrete session manager (the `set_session_manager` analogue,
/// for the concrete type the monitor needs). Called by `app_initializer`
/// alongside `core::models::set_session_manager`.
#[cfg(unix)]
pub fn set_session_manager(manager: Arc<SessionManager>) {
    *SESSION_MANAGER.write().unwrap() = Some(manager);
}

/// `get_session_manager()` — the concrete session manager, or `None` if not yet
/// wired (the monitor then defers, returning `False` so the tick retries).
#[cfg(unix)]
pub fn get_session_manager() -> Option<Arc<SessionManager>> {
    SESSION_MANAGER.read().unwrap().clone()
}

/// Run the agent loop headless against a session. Returns `(final_prose,
/// tool_events)` — `tool_events` in the same shape the live chat saves, so the
/// frontend rebuilds them as standard agent-thread tool cards.
///
/// ```python
/// async def _drain_agent(sess, messages):
///     from src.agent_loop import stream_agent_loop
///     full = ""
///     tool_events = []
///     round_num = 1
///     async for chunk in stream_agent_loop(...):
///         ...
///     return full, tool_events
/// ```
#[cfg(unix)]
async fn drain_agent(
    sess: &crate::core::models::Session,
    messages: Vec<Value>,
) -> (String, Vec<Value>) {
    use crate::src::agent_loop::{stream_agent_loop, AgentLoopArgs};
    use futures_util::StreamExt;

    let mut full = String::new();
    let mut tool_events: Vec<Value> = Vec::new();
    let mut round_num: i64 = 1;

    // stream_agent_loop(
    //     sess.endpoint_url, sess.model, messages,
    //     headers=getattr(sess, "headers", None),
    //     context_length=getattr(sess, "context_length", 0) or 0,
    //     session_id=sess.id, max_rounds=_FOLLOWUP_MAX_ROUNDS,
    //     owner=getattr(sess, "owner", None),
    // )
    //
    // The Rust `Session` has no `context_length` attribute, so the Python
    // `getattr(sess, "context_length", 0) or 0` is always `0`. `headers` is
    // always present (an `IndexMap`, never `None`), so `getattr(..., None)`
    // collapses to the live map.
    let args = AgentLoopArgs {
        endpoint_url: sess.endpoint_url.clone(),
        model: sess.model.clone(),
        messages,
        headers: sess.headers.clone(),
        context_length: 0,
        session_id: Some(sess.id.clone()),
        max_rounds: FOLLOWUP_MAX_ROUNDS,
        owner: sess.owner.clone(),
        ..Default::default()
    };

    let stream = stream_agent_loop(args);
    futures_util::pin_mut!(stream);
    while let Some(chunk) = stream.next().await {
        // if not chunk.startswith("data: "): continue
        if !chunk.starts_with("data: ") {
            continue;
        }
        // body = chunk[6:].strip()
        let body = chunk[6..].trim();
        // if not body or body == "[DONE]": continue
        if body.is_empty() || body == "[DONE]" {
            continue;
        }
        // try: d = json.loads(body) except (ValueError, TypeError): continue
        let d: Value = match serde_json::from_str(body) {
            Ok(v) => v,
            Err(_) => continue,
        };
        // if not isinstance(d, dict): continue
        let d = match d.as_object() {
            Some(m) => m,
            None => continue,
        };
        if let Some(delta) = d.get("delta") {
            // if "delta" in d: full += d["delta"]
            //
            // Python concatenates `d["delta"]` directly; the live agent loop only
            // ever emits a string delta. A non-string would raise a TypeError in
            // Python (uncaught here -> bubbles to `_run_followup`'s caller); the
            // faithful Rust analogue takes the string when present and otherwise
            // adds nothing (the loop never emits a non-string delta).
            if let Some(s) = delta.as_str() {
                full.push_str(s);
            }
        } else if d.get("type").and_then(|v| v.as_str()) == Some("agent_step") {
            // elif d.get("type") == "agent_step": round_num = d.get("round", round_num)
            if let Some(r) = d.get("round").and_then(|v| v.as_i64()) {
                round_num = r;
            }
        } else if d.get("type").and_then(|v| v.as_str()) == Some("tool_output") {
            // Mirror the live chat's tool_event shape (chat_routes / chatRenderer).
            // tool_events.append({"round", "tool", "command", "output", "exit_code"})
            let mut ev = Map::new();
            ev.insert("round".into(), json!(round_num));
            ev.insert("tool".into(), d.get("tool").cloned().unwrap_or(Value::Null));
            ev.insert("command".into(), d.get("command").cloned().unwrap_or(Value::Null));
            ev.insert("output".into(), d.get("output").cloned().unwrap_or(Value::Null));
            ev.insert("exit_code".into(), d.get("exit_code").cloned().unwrap_or(Value::Null));
            tool_events.push(Value::Object(ev));
        }
    }
    (full, tool_events)
}

/// Re-invoke the agent in the job's session with the result. Returns `Ok(true)`
/// if the follow-up completed (or there's nothing to do) — i.e. it's safe to
/// mark `followed_up`. Returns `Ok(false)` to retry on the next tick. An `Err`
/// (e.g. a missing session, which the concrete `get_session` raises as a
/// `KeyError`) bubbles to `loop_`'s catch -> log + retry next tick.
///
/// DOCUMENTED PARITY NOTE — the `if not sess:` branch. The Python writes
/// `sess = sm.get_session(rec["session_id"])` then `if not sess: ... return
/// True`, but `SessionManager.get_session` *raises* `KeyError` on a missing
/// session rather than returning a falsy value, so that branch is effectively
/// dead in CPython too: a deleted session raises, the exception propagates to
/// `_loop`'s `except Exception` ("will retry"), and the job is retried every
/// tick. The Rust `get_session` returns `Err` on not-found, which this fn
/// propagates with `?` to exactly the same retry behaviour — preserving the
/// Python's observable outcome.
#[cfg(unix)]
async fn run_followup(rec: &Map<String, Value>) -> crate::error::PyResult<bool> {
    use crate::src::bg_jobs;

    // sm = get_session_manager(); if not sm: return False  (not ready yet — retry)
    let sm = match get_session_manager() {
        Some(m) => m,
        None => return Ok(false),
    };

    let session_id = rec.get("session_id").and_then(|v| v.as_str()).unwrap_or("");

    // sess = sm.get_session(rec["session_id"])
    //
    // Faithful: `get_session` raises `KeyError` on a missing session. We surface
    // that as `Err` (propagated below). The Python's `if not sess: return True`
    // can never fire for a live `SessionManager` (see the fn docstring), but a
    // deleted session still ends up handled identically (retry next tick).
    let sess = sm.get_session(session_id)?;

    // Don't write into a session that's mid-stream. The followup appends to
    // history + save_sessions(); a concurrent live turn does the same, and with
    // no per-session lock the two interleave (reordered/clobbered messages).
    // Defer — return False so we retry on the next tick once the turn finishes.
    //
    // Python wraps this in try/except (a missing `agent_runs` import etc. is
    // swallowed); `is_active` is infallible here.
    if crate::src::agent_runs::is_active(&sess.id) {
        logger::info(&format!(
            "bg-followup: session {} busy (live turn) — deferring job {}",
            sess.id,
            py_str(rec.get("id"))
        ));
        return Ok(false);
    }

    let inject = format!(
        "[Background job {} finished]\n\n{}\n\nContinue the task using this output. \
Don't repeat work that's already done. If the task is now complete, give the user \
the final result.",
        py_str(rec.get("id")),
        bg_jobs::result_text(rec)
    );

    // context = sess.get_context_messages(); context.append({"role": "user", "content": inject})
    let mut context = sess.get_context_messages();
    context.push(json!({"role": "user", "content": inject}));

    // full, tool_events = await _drain_agent(sess, context)
    let (full, tool_events) = drain_agent(&sess, context).await;

    // Persist ONLY the assistant continuation so it renders as a normal agent
    // turn — a standard chat bubble plus `tool_events` that the frontend
    // rebuilds into the usual agent-thread tool cards (chatRenderer:1494). The
    // trigger isn't saved as its own message (it'd be an out-of-place bubble);
    // the raw job output is stashed in metadata for traceability instead.
    let mut metadata = Map::new();
    metadata.insert("tool_events".into(), Value::Array(tool_events.clone()));
    metadata.insert("model".into(), json!(sess.model));
    metadata.insert("bg_job_id".into(), rec.get("id").cloned().unwrap_or(Value::Null));
    // bg_jobs.result_text(rec)[:4000] — CHARACTER slice (Python str), not bytes.
    let result_text = bg_jobs::result_text(rec);
    let bg_result: String = result_text.chars().take(4000).collect();
    metadata.insert("bg_result".into(), json!(bg_result));

    sm.add_message(
        &sess.id,
        ChatMessage::new("assistant", full.clone(), Some(metadata)),
    )?;
    // sm.save_sessions()  (a no-op in the Rust SessionManager — persistence is
    // per-message via `add_message`)
    sm.save_sessions();

    // len(full) / len(tool_events) — Python len() of a str is CHARACTER count.
    logger::info(&format!(
        "bg-followup: auto-continued session {} for job {} ({} chars, {} tools)",
        sess.id,
        py_str(rec.get("id")),
        full.chars().count(),
        tool_events.len()
    ));
    Ok(true)
}

/// `async def _loop()` — the monitor tick loop. Drains pending follow-ups every
/// [`POLL_INTERVAL_S`] seconds; marks each followed-up only on success;
/// swallows per-job and per-tick errors so the loop never dies.
#[cfg(unix)]
async fn loop_() {
    use crate::src::bg_jobs;

    loop {
        // try: for rec in bg_jobs.pending_followups(): ...
        // The outer try/except guards the whole tick body (e.g. a DB hiccup in
        // `pending_followups`). `pending_followups` is infallible in Rust, so the
        // outer guard only ever wraps the inner per-job loop here.
        for rec in bg_jobs::pending_followups() {
            // try: if await _run_followup(rec): bg_jobs.mark_followed_up(rec["id"])
            // except Exception as e: logger.warning("... (will retry): ...")
            match run_followup(&rec).await {
                Ok(true) => {
                    let job_id = rec.get("id").and_then(|v| v.as_str()).unwrap_or("");
                    if let Err(e) = bg_jobs::mark_followed_up(job_id) {
                        // `mark_followed_up` raising would also fall into the
                        // per-job `except Exception` in Python (idempotent: leave
                        // followed_up=False so the next tick retries).
                        logger::warning(&format!(
                            "bg-followup failed for {} (will retry): {}",
                            py_str(rec.get("id")),
                            e
                        ));
                    }
                }
                Ok(false) => {
                    // Not ready / deferred — leave followed_up=False (retry next tick).
                }
                Err(e) => {
                    // Idempotent: leave followed_up=False so the next tick retries.
                    logger::warning(&format!(
                        "bg-followup failed for {} (will retry): {}",
                        py_str(rec.get("id")),
                        e
                    ));
                }
            }
        }
        // await asyncio.sleep(POLL_INTERVAL_S)
        tokio::time::sleep(std::time::Duration::from_secs(POLL_INTERVAL_S)).await;
    }
}

/// `start_bg_monitor()` — idempotent. Start the always-on background-job monitor.
///
/// ```python
/// def start_bg_monitor():
///     global _monitor_task
///     if _monitor_task and not _monitor_task.done():
///         return _monitor_task
///     _monitor_task = asyncio.create_task(_loop())
///     logger.info("Background-job monitor started (poll %ds)", POLL_INTERVAL_S)
///     return _monitor_task
/// ```
#[cfg(unix)]
pub fn start_bg_monitor() {
    let mut guard = MONITOR_TASK.lock().unwrap();
    // if _monitor_task and not _monitor_task.done(): return _monitor_task
    if let Some(task) = guard.as_ref() {
        if !task.is_finished() {
            return;
        }
    }
    // _monitor_task = asyncio.create_task(_loop())
    *guard = Some(tokio::spawn(loop_()));
    logger::info(&format!(
        "Background-job monitor started (poll {POLL_INTERVAL_S}s)"
    ));
}

/// `f"{rec.get(key)}"` — Python's `str()` of a possibly-missing dict value, used
/// for the log/inject formatting of `rec["id"]` (a missing key would format as
/// `"None"`; a string renders without quotes). Mirrors `bg_jobs::py_str`.
#[cfg(unix)]
fn py_str(v: Option<&Value>) -> String {
    match v {
        None | Some(Value::Null) => "None".to_string(),
        Some(Value::String(s)) => s.clone(),
        Some(Value::Bool(b)) => {
            if *b {
                "True".to_string()
            } else {
                "False".to_string()
            }
        }
        Some(Value::Number(n)) => {
            if let Some(i) = n.as_i64() {
                i.to_string()
            } else if let Some(u) = n.as_u64() {
                u.to_string()
            } else {
                n.to_string()
            }
        }
        Some(other) => other.to_string(),
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;

    /// The concrete-SM global starts empty (matching Python's
    /// `_session_manager = None`) and round-trips through the setter/getter.
    #[test]
    fn session_manager_global_round_trips() {
        // Note: a prior test in the same binary may have set it; assert the
        // round-trip semantics rather than the absolute initial state.
        let sm = Arc::new(SessionManager::new());
        set_session_manager(Arc::clone(&sm));
        let got = get_session_manager().expect("set then get");
        assert!(Arc::ptr_eq(&got, &sm));
    }

    /// `py_str` matches the Python `f"{rec.get('id')}"` rendering used in the
    /// inject string + log lines.
    #[test]
    fn py_str_renders_like_python() {
        assert_eq!(py_str(None), "None");
        assert_eq!(py_str(Some(&Value::Null)), "None");
        assert_eq!(py_str(Some(&json!("abc123"))), "abc123");
        assert_eq!(py_str(Some(&json!(7))), "7");
    }

    /// `run_followup` returns `Ok(false)` (retry) when no session manager is wired
    /// (Python `if not sm: return False`).
    #[tokio::test]
    async fn run_followup_defers_without_session_manager() {
        // Clear the global so this is deterministic regardless of test order.
        *SESSION_MANAGER.write().unwrap() = None;
        let mut rec = Map::new();
        rec.insert("session_id".into(), json!("nope"));
        rec.insert("id".into(), json!("job1"));
        let r = run_followup(&rec).await.expect("infallible when sm is None");
        assert!(!r, "no session manager -> defer (retry next tick)");
    }
}
