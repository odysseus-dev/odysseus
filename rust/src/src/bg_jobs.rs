// src/bg_jobs.rs  <- src/bg_jobs.py
//! Background job execution for the agent's `bash` tool.
//!
//! Long commands (installs, ffmpeg, model downloads) should NOT block the chat
//! stream — a multi-minute held SSE connection is fragile (model-stops-early,
//! timeouts, tab suspend). Instead we launch them **detached** and let an
//! always-on monitor re-invoke the agent when they finish ("auto-continue").
//!
//! Design goals:
//!   * Restart-safe: status is derived from an on-disk exit-code file, not a
//!     live PID, so a uvicorn restart never loses a job or its result.
//!   * Idempotent follow-up: a job stays {done, followed_up: False} until the
//!     agent has actually been re-invoked, so completion can never silently
//!     "do nothing" — the monitor retries on the next tick.
//!   * Bounded: a hard max-runtime marks a runaway job failed and STILL triggers
//!     a follow-up ("timed out"), so you always hear back.
//!
//! This module only owns launch + state. The monitor / agent re-invocation
//! lives in the caller (so this stays import-light and unit-testable).
//!
//! PORT_NOW — faithful port. Backed by the same on-disk store + per-job
//! `.cmd.sh`/`.sh`/`.log`/`.exit` files the Python writes, so the two
//! implementations are wire-compatible against a shared `data/bg_jobs/` tree.
//!
//! Notes on deliberate, documented deviations:
//!   * Records are dynamic-key `serde_json::Value` objects (the crate builds
//!     with `preserve_order`, so dict-style key order is retained), mirroring
//!     the Python `Dict[str, Any]`. The optional `timed_out`/`died` flags are
//!     only present on a record when set, exactly like Python.
//!   * `DATA_DIR`: the Python reads the **plain** `DATA_DIR` env var
//!     (`os.environ.get("DATA_DIR", "data")`), NOT `ODYSSEUS_DATA_DIR` that the
//!     rest of the crate honours via `core::constants::DATA_DIR`. This port
//!     replicates the Python's env-var name (plain `DATA_DIR`, default `"data"`)
//!     so a shared deployment writes to the same place — see [`data_dir`]. This
//!     env-var-name difference vs. the rest of the crate is intentional.
//!   * Detached process groups (`os.setsid` / `os.killpg` / `os.getpgid` /
//!     `os.kill(pid, 0)`) are Unix-only via the `nix` crate. The module is
//!     therefore `#[cfg(unix)]`; on a (currently unsupported) non-Unix target it
//!     would need an honest stub. It ships with the subprocess tooling cohort
//!     (`tool_execution`'s background bash path).

use crate::error::PyResult;
use crate::pytime as time;
use serde_json::{json, Map, Value};
use std::path::{Path, PathBuf};

/// A job that runs longer than this is presumed stuck and reaped (the agent
/// still gets a "timed out" follow-up so nothing hangs forever).
pub const DEFAULT_MAX_RUNTIME_S: i64 = 3600; // 1 hour
/// Cap how much captured output we keep / feed back to the model.
const MAX_OUTPUT_CHARS: usize = 16000;
/// How long a finished-and-followed-up job (record + its
/// `.sh`/`.cmd.sh`/`.log`/`.exit` files) is kept before pruning, so neither the
/// store nor `data/bg_jobs/` grows without bound. The agent has already
/// consumed the result by then.
const RETENTION_S: f64 = 3600.0; // 1 hour after follow-up

/// `_DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))`.
///
/// Deliberately the **plain** `DATA_DIR` env var (default `"data"`), matching
/// the Python source exactly — NOT the crate-wide `ODYSSEUS_DATA_DIR` override.
fn data_dir() -> PathBuf {
    PathBuf::from(crate::pyos::getenv("DATA_DIR", "data"))
}

/// `_JOBS_DIR = _DATA_DIR / "bg_jobs"`.
fn jobs_dir() -> PathBuf {
    data_dir().join("bg_jobs")
}

/// `_STORE = _DATA_DIR / "bg_jobs.json"`.
fn store_path() -> PathBuf {
    data_dir().join("bg_jobs.json")
}

/// `_load()` — read the store, returning `{}` on any error / missing file.
///
/// ```python
/// try:
///     if _STORE.exists():
///         data = json.loads(_STORE.read_text()) or {}
///         if not isinstance(data, dict):
///             return {}
///         return {str(job_id): rec for job_id, rec in data.items() if isinstance(rec, dict)}
/// except Exception:
///     pass
/// return {}
/// ```
///
/// Non-dict (non-object) values at the top level are silently dropped, matching
/// the Python `if isinstance(rec, dict)` filter added to `_load()`.
fn load() -> Map<String, Value> {
    let store = store_path();
    if store.exists() {
        if let Ok(raw) = std::fs::read_to_string(&store) {
            if let Ok(Value::Object(map)) = serde_json::from_str::<Value>(&raw) {
                // `{str(job_id): rec for job_id, rec in data.items() if isinstance(rec, dict)}`
                // Drop any top-level values that are not JSON objects (i.e. not dicts).
                let filtered: Map<String, Value> = map
                    .into_iter()
                    .filter(|(_, v)| v.is_object())
                    .collect();
                return filtered;
            }
        }
    }
    Map::new()
}

/// `_save(jobs)` — `atomic_write_json(str(_STORE), jobs, indent=2)`.
fn save(jobs: &Map<String, Value>) -> PyResult<()> {
    let store = store_path();
    crate::core::atomic_io::atomic_write_json(&store.to_string_lossy(), jobs, Some(2))
}

/// `_pid_alive(pid)` — `os.kill(pid, 0)` existence probe.
///
/// ```python
/// if not pid:
///     return False
/// try:
///     os.kill(pid, 0)
///     return True
/// except (OSError, ProcessLookupError):
///     return False
/// ```
///
/// `nix::sys::signal::kill(pid, None)` performs the same error-checking-only
/// `kill(pid, 0)` syscall (no signal delivered).
fn pid_alive(pid: Option<i64>) -> bool {
    let pid = match pid {
        Some(p) if p != 0 => p,
        _ => return false, // `not pid` — None or 0 -> False
    };
    let raw = match i32::try_from(pid) {
        Ok(p) => p,
        Err(_) => return false,
    };
    nix::sys::signal::kill(nix::unistd::Pid::from_raw(raw), None).is_ok()
}

/// `_kill(pid)` — `os.killpg(os.getpgid(pid), SIGTERM)`, falling back to
/// `os.kill(pid, SIGTERM)`, swallowing every error.
fn kill(pid: Option<i64>) {
    let pid = match pid {
        Some(p) if p != 0 => p,
        _ => return, // `if not pid: return`
    };
    let raw = match i32::try_from(pid) {
        Ok(p) => p,
        Err(_) => return,
    };
    let target = nix::unistd::Pid::from_raw(raw);
    // try: os.killpg(os.getpgid(pid), SIGTERM)
    let killpg_ok = nix::unistd::getpgid(Some(target))
        .and_then(|pgid| nix::sys::signal::killpg(pgid, nix::sys::signal::Signal::SIGTERM))
        .is_ok();
    if !killpg_ok {
        // except Exception: try: os.kill(pid, SIGTERM) except Exception: pass
        let _ = nix::sys::signal::kill(target, nix::sys::signal::Signal::SIGTERM);
    }
}

/// Launch `command` detached. Returns the job record (status='running').
///
/// Output + the final exit code are written to files so status survives a
/// server restart. The process is put in its own session (setsid) so it
/// outlives the request/stream that started it.
pub fn launch(
    command: &str,
    session_id: &str,
    cwd: Option<&str>,
    max_runtime_s: i64,
) -> PyResult<Map<String, Value>> {
    let jobs_dir = jobs_dir();
    // _JOBS_DIR.mkdir(parents=True, exist_ok=True)
    std::fs::create_dir_all(&jobs_dir)?;
    // job_id = uuid.uuid4().hex[:12]
    let job_id: String = uuid::Uuid::new_v4().simple().to_string().chars().take(12).collect();
    let log_path = jobs_dir.join(format!("{job_id}.log"));
    let exit_path = jobs_dir.join(format!("{job_id}.exit"));

    // The user command goes in its OWN script file, run as a child `bash`. This
    // is what isolates it: an `exit` inside it only ends that child (so the
    // wrapper still records the exit code), and — unlike textually wrapping the
    // command in `( … )` — the wrapper can't be broken by an unbalanced paren
    // or a trailing line-continuation in the command. `$?` is the child's real
    // exit status.
    //
    // Paths are shell-quoted (shlex.quote equivalent) so that a DATA_DIR
    // containing spaces does not break the generated wrapper script.
    // Python: lp, xp, cp = (shlex.quote(p.as_posix()) for p in (log_path, exit_path, cmd_path))
    let cmd_path = jobs_dir.join(format!("{job_id}.cmd.sh"));
    // cmd_path.write_text(command + "\n")
    std::fs::write(&cmd_path, format!("{command}\n"))?;
    let lp = posix_shell_quote(&log_path);
    let xp = posix_shell_quote(&exit_path);
    let cp = posix_shell_quote(&cmd_path);
    let wrapper = format!(
        "bash {cp} > {lp} 2>&1\necho $? > {xp}\n",
    );
    let script_path = jobs_dir.join(format!("{job_id}.sh"));
    // script_path.write_text(wrapper)
    std::fs::write(&script_path, &wrapper)?;

    // subprocess.Popen(["bash", script_path], stdout=DEVNULL, stderr=DEVNULL,
    //                  stdin=DEVNULL, cwd=cwd or None, start_new_session=True)
    let pid = spawn_detached(&script_path, cwd)?;

    // rec = { ... }  — key insertion order mirrors the Python literal.
    let mut rec = Map::new();
    rec.insert("id".into(), json!(job_id));
    rec.insert("session_id".into(), json!(session_id));
    rec.insert("command".into(), json!(command));
    rec.insert("status".into(), json!("running")); // running | done | failed
    rec.insert("pid".into(), json!(pid));
    rec.insert("started_at".into(), json!(time::time()));
    rec.insert("ended_at".into(), Value::Null);
    rec.insert("exit_code".into(), Value::Null);
    rec.insert("max_runtime_s".into(), json!(max_runtime_s));
    rec.insert("followed_up".into(), json!(false)); // has the agent been re-invoked?
    rec.insert("log_path".into(), json!(log_path.to_string_lossy()));
    rec.insert("exit_path".into(), json!(exit_path.to_string_lossy()));

    let mut jobs = load();
    jobs.insert(job_id.clone(), Value::Object(rec.clone()));
    save(&jobs)?;
    Ok(rec)
}

/// `subprocess.Popen([...], stdin/out/err=DEVNULL, cwd=cwd, start_new_session=True)`.
///
/// `start_new_session=True` is CPython's `setsid()` in the child after `fork`
/// and before `exec` — reproduced via `pre_exec`. Stdio is fully detached
/// (`/dev/null`) and the parent does NOT wait, exactly like `Popen`. Returns
/// the child PID.
#[cfg(unix)]
fn spawn_detached(script_path: &Path, cwd: Option<&str>) -> PyResult<i64> {
    use std::os::unix::process::CommandExt;
    use std::process::{Command, Stdio};

    let mut cmd = Command::new("bash");
    cmd.arg(script_path)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    // cwd=cwd or None — only set when a non-empty path is supplied.
    if let Some(dir) = cwd {
        if !dir.is_empty() {
            cmd.current_dir(dir);
        }
    }
    // SAFETY: `setsid()` is async-signal-safe; it is the only thing executed in
    // the child between fork and exec, matching CPython's `start_new_session`.
    unsafe {
        cmd.pre_exec(|| {
            nix::unistd::setsid().map(|_| ()).map_err(std::io::Error::from)
        });
    }
    let child = cmd.spawn()?;
    Ok(i64::from(child.id()))
}

/// `_read_output(rec)` — read the log file (replacing invalid bytes) and apply
/// the head+tail truncation when it exceeds [`MAX_OUTPUT_CHARS`].
///
/// ```python
/// try:
///     txt = Path(rec["log_path"]).read_text(errors="replace")
/// except Exception:
///     return ""
/// if len(txt) > _MAX_OUTPUT_CHARS:
///     head = txt[: _MAX_OUTPUT_CHARS // 2]
///     tail = txt[-_MAX_OUTPUT_CHARS // 2:]
///     txt = head + "\n…[truncated]…\n" + tail
/// return txt
/// ```
///
/// `read_text(errors="replace")` -> read bytes + `String::from_utf8_lossy`.
/// `len(txt)` and the slices are **CHARACTER** indices in Python, so the port
/// slices on `chars()`, not bytes.
fn read_output(rec: &Map<String, Value>) -> String {
    let log_path = match rec.get("log_path").and_then(|v| v.as_str()) {
        Some(p) => p,
        None => return String::new(),
    };
    let bytes = match std::fs::read(log_path) {
        Ok(b) => b,
        Err(_) => return String::new(),
    };
    let txt = String::from_utf8_lossy(&bytes).into_owned();

    let chars: Vec<char> = txt.chars().collect();
    if chars.len() > MAX_OUTPUT_CHARS {
        // head = txt[:8000]; tail = txt[-8000:]
        let half = MAX_OUTPUT_CHARS / 2; // 8000
        let head: String = chars[..half].iter().collect();
        let tail: String = chars[chars.len() - half..].iter().collect();
        format!("{head}\n…[truncated]…\n{tail}")
    } else {
        txt
    }
}

/// `_prune(jobs, now)` — drop records (and their on-disk files) for jobs that
/// finished, were followed up, and are older than the retention window.
/// Mutates `jobs`. Returns whether anything was pruned.
fn prune(jobs: &mut Map<String, Value>, now: f64) -> bool {
    // stale = [jid for jid, rec in jobs.items()
    //          if rec.get("followed_up") and rec.get("ended_at")
    //          and (now - rec["ended_at"]) > _RETENTION_S]
    let stale: Vec<String> = jobs
        .iter()
        .filter(|(_, rec)| {
            let followed_up = rec.get("followed_up").map(is_truthy).unwrap_or(false);
            // `rec.get("ended_at")` truthy: a non-null, non-zero number.
            let ended_at = rec.get("ended_at").and_then(|v| v.as_f64());
            match (followed_up, ended_at) {
                (true, Some(ts)) if ts != 0.0 => (now - ts) > RETENTION_S,
                _ => false,
            }
        })
        .map(|(jid, _)| jid.clone())
        .collect();

    let jobs_dir = jobs_dir();
    for jid in &stale {
        // jobs.pop(jid, None)
        jobs.remove(jid);
        // for p in _JOBS_DIR.glob(f"{jid}.*"): p.unlink()  (.sh .cmd.sh .log .exit)
        let prefix = format!("{jid}.");
        if let Ok(entries) = std::fs::read_dir(&jobs_dir) {
            for entry in entries.flatten() {
                if entry.file_name().to_string_lossy().starts_with(&prefix) {
                    let _ = std::fs::remove_file(entry.path());
                }
            }
        }
    }
    !stale.is_empty()
}

/// Reconcile every running job against disk. Marks done/failed (incl. timeout).
/// Idempotent — safe to call from a poll loop. Returns the store.
pub fn refresh() -> Map<String, Value> {
    let mut jobs = load();
    let mut changed = false;
    let now = time::time();

    for (_jid, rec_val) in jobs.iter_mut() {
        let rec = match rec_val.as_object_mut() {
            Some(m) => m,
            None => continue,
        };
        // if rec.get("status") != "running": continue
        if rec.get("status").and_then(|v| v.as_str()) != Some("running") {
            continue;
        }
        let exit_path_str = rec.get("exit_path").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let exit_exists = !exit_path_str.is_empty() && Path::new(&exit_path_str).exists();

        if exit_exists {
            // code = int(exit_path.read_text().strip() or "1")  (1 on parse fail)
            let code: i64 = std::fs::read_to_string(&exit_path_str)
                .ok()
                .and_then(|raw| {
                    let trimmed = raw.trim();
                    if trimmed.is_empty() {
                        Some(1) // `... or "1"` -> int("1")
                    } else {
                        trimmed.parse::<i64>().ok()
                    }
                })
                .unwrap_or(1); // except Exception: code = 1
            rec.insert("exit_code".into(), json!(code));
            rec.insert(
                "status".into(),
                json!(if code == 0 { "done" } else { "failed" }),
            );
            rec.insert("ended_at".into(), json!(now));
            changed = true;
        } else {
            let started_at = rec.get("started_at").and_then(|v| v.as_f64()).unwrap_or(now);
            let max_runtime = rec
                .get("max_runtime_s")
                .and_then(|v| v.as_f64())
                .unwrap_or(DEFAULT_MAX_RUNTIME_S as f64);
            let pid = rec.get("pid").and_then(|v| v.as_i64());

            if (now - started_at) > max_runtime {
                // Runaway / stuck — reap it but STILL surface a follow-up.
                kill(pid);
                rec.insert("status".into(), json!("failed"));
                rec.insert("exit_code".into(), json!(-1));
                rec.insert("ended_at".into(), json!(now));
                rec.insert("timed_out".into(), json!(true));
                changed = true;
            } else if !pid_alive(pid) {
                // (the `and not exit_path.exists()` is already true here)
                // Process vanished without writing an exit code (killed, OOM,
                // crash). Don't leave it "running" forever.
                rec.insert("status".into(), json!("failed"));
                rec.insert("exit_code".into(), json!(-1));
                rec.insert("ended_at".into(), json!(now));
                rec.insert("died".into(), json!(true));
                changed = true;
            }
        }
    }

    if prune(&mut jobs, now) {
        changed = true;
    }
    if changed {
        let _ = save(&jobs);
    }
    jobs
}

/// Finished jobs the agent hasn't been re-invoked for yet. The monitor drains
/// these; [`mark_followed_up`] flips the flag only on success.
pub fn pending_followups() -> Vec<Map<String, Value>> {
    let jobs = refresh();
    // [r for r in jobs.values()
    //  if r.get("status") in ("done", "failed") and not r.get("followed_up")]
    jobs.values()
        .filter_map(|r| r.as_object())
        .filter(|r| {
            let status = r.get("status").and_then(|v| v.as_str());
            let done_or_failed = matches!(status, Some("done") | Some("failed"));
            let followed_up = r.get("followed_up").map(is_truthy).unwrap_or(false);
            done_or_failed && !followed_up
        })
        .cloned()
        .collect()
}

/// `mark_followed_up(job_id)` — flip the flag in the store.
pub fn mark_followed_up(job_id: &str) -> PyResult<()> {
    let mut jobs = load();
    if let Some(rec) = jobs.get_mut(job_id).and_then(|v| v.as_object_mut()) {
        rec.insert("followed_up".into(), json!(true));
        save(&jobs)?;
    }
    Ok(())
}

/// `get(job_id)` — reconcile against disk, then return the record with its
/// `output` field populated (or `None` when the id is unknown).
pub fn get(job_id: &str) -> Option<Map<String, Value>> {
    refresh(); // reconcile against disk so status/exit_code are current
    let rec = load().get(job_id).and_then(|v| v.as_object().cloned())?;
    // rec = dict(rec); rec["output"] = _read_output(rec)
    let mut rec = rec;
    let output = read_output(&rec);
    rec.insert("output".into(), json!(output));
    Some(rec)
}

/// `list_for_session(session_id)`.
pub fn list_for_session(session_id: &str) -> Vec<Map<String, Value>> {
    refresh()
        .values()
        .filter_map(|r| r.as_object())
        .filter(|r| r.get("session_id").and_then(|v| v.as_str()) == Some(session_id))
        .cloned()
        .collect()
}

/// Human/agent-readable summary of a finished job, for the follow-up.
///
/// ```python
/// out = _read_output(rec)
/// if rec.get("timed_out"):
///     head = f"Background job timed out after {rec.get('max_runtime_s')}s."
/// elif rec.get("died"):
///     head = "Background job process died unexpectedly (no exit code)."
/// else:
///     head = f"Background job finished with exit code {rec.get('exit_code')}."
/// return f"{head}\nCommand: {rec.get('command')}\n\nOutput:\n{out or '(no output)'}"
/// ```
pub fn result_text(rec: &Map<String, Value>) -> String {
    let out = read_output(rec);
    let head = if rec.get("timed_out").map(is_truthy).unwrap_or(false) {
        format!(
            "Background job timed out after {}s.",
            py_str(rec.get("max_runtime_s"))
        )
    } else if rec.get("died").map(is_truthy).unwrap_or(false) {
        "Background job process died unexpectedly (no exit code).".to_string()
    } else {
        format!(
            "Background job finished with exit code {}.",
            py_str(rec.get("exit_code"))
        )
    };
    let command = py_str(rec.get("command"));
    let output = if out.is_empty() { "(no output)" } else { &out };
    format!("{head}\nCommand: {command}\n\nOutput:\n{output}")
}

/// POSIX `shlex.quote` equivalent: single-quote a path for safe shell embedding.
///
/// Python: `shlex.quote(p.as_posix())` — used in the wrapper script so that
/// paths containing spaces (or other shell metacharacters) do not break the
/// generated `.sh` file when `DATA_DIR` contains spaces.
///
/// Implements the same algorithm as CPython's `shlex.quote`:
///   * If the string is empty, return `''`.
///   * If the string contains only safe characters (`[a-zA-Z0-9@%+=:,./-]`),
///     return it as-is (no quoting needed — matches Python's `_find_unsafe`).
///   * Otherwise wrap it in single quotes, replacing any embedded `'` with
///     `'\''` (end-quote, literal apostrophe, re-open-quote).
///
/// The path is converted to a POSIX (forward-slash) string first, matching
/// `.as_posix()` in the Python source.
fn posix_shell_quote(p: &Path) -> String {
    // p.as_posix() — forward slashes (no-op on Unix; converts on Windows).
    let s = p.to_string_lossy().replace('\\', "/");
    if s.is_empty() {
        return "''".to_string();
    }
    // Matches CPython shlex._find_unsafe = re.compile(r'[^\w@%+=:,./-]').
    // `\w` in CPython's re module matches [a-zA-Z0-9_] (ASCII mode, no LOCALE).
    let safe = s.chars().all(|c| c.is_ascii_alphanumeric() || c == '_' || "@%+=:,./-".contains(c));
    if safe {
        s
    } else {
        // "'" + s.replace("'", "'\\''") + "'"
        let escaped = s.replace('\'', "'\\''");
        format!("'{escaped}'")
    }
}

/// Python truthiness for a JSON value, as used by `rec.get(...)` boolean checks
/// (`followed_up`, `timed_out`, `died`). `None`/`false`/`0`/`""`/empty are
/// falsy; everything else truthy.
fn is_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `f"{rec.get(key)}"` — Python's `str()` of a possibly-missing dict value.
/// A missing key (`rec.get(...)` -> `None`) formats as `"None"`; strings render
/// without quotes; numbers render their value (integers without a trailing
/// `.0`, matching CPython since these fields hold ints).
fn py_str(v: Option<&Value>) -> String {
    match v {
        None | Some(Value::Null) => "None".to_string(),
        Some(Value::String(s)) => s.clone(),
        Some(Value::Bool(b)) => {
            // Python str(True)/str(False)
            if *b { "True".to_string() } else { "False".to_string() }
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

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// Build a finished-job record matching the Python shape.
    fn rec_with(extra: &[(&str, Value)]) -> Map<String, Value> {
        let mut m = Map::new();
        m.insert("command".into(), json!("echo hi"));
        m.insert("exit_code".into(), json!(0));
        m.insert("max_runtime_s".into(), json!(3600));
        m.insert("log_path".into(), json!("/nonexistent/bg.log"));
        for (k, v) in extra {
            m.insert((*k).to_string(), v.clone());
        }
        m
    }

    #[test]
    fn result_text_exit_code_branch() {
        let rec = rec_with(&[("exit_code", json!(0))]);
        let txt = result_text(&rec);
        assert_eq!(
            txt,
            "Background job finished with exit code 0.\nCommand: echo hi\n\nOutput:\n(no output)"
        );
    }

    #[test]
    fn result_text_timed_out_branch() {
        let rec = rec_with(&[("timed_out", json!(true)), ("max_runtime_s", json!(60))]);
        let txt = result_text(&rec);
        assert!(txt.starts_with("Background job timed out after 60s."));
        assert!(txt.ends_with("Output:\n(no output)"));
    }

    #[test]
    fn result_text_died_branch() {
        let rec = rec_with(&[("died", json!(true))]);
        let txt = result_text(&rec);
        assert!(txt.starts_with("Background job process died unexpectedly (no exit code)."));
    }

    #[test]
    fn py_str_missing_is_none() {
        assert_eq!(py_str(None), "None");
        assert_eq!(py_str(Some(&Value::Null)), "None");
        assert_eq!(py_str(Some(&json!(-1))), "-1");
        assert_eq!(py_str(Some(&json!("x"))), "x");
    }

    #[test]
    fn truthiness_matches_python() {
        assert!(!is_truthy(&Value::Null));
        assert!(!is_truthy(&json!(false)));
        assert!(!is_truthy(&json!(0)));
        assert!(!is_truthy(&json!("")));
        assert!(is_truthy(&json!(true)));
        assert!(is_truthy(&json!(1)));
        assert!(is_truthy(&json!("x")));
    }

    #[test]
    fn read_output_truncates_on_chars() {
        // Build a temp log over MAX_OUTPUT_CHARS and confirm head+tail join.
        let dir = std::env::temp_dir().join(format!("bgjobs_test_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let log = dir.join("trunc.log");
        let body: String = "a".repeat(MAX_OUTPUT_CHARS + 100);
        std::fs::write(&log, &body).unwrap();
        let mut rec = Map::new();
        rec.insert("log_path".into(), json!(log.to_string_lossy()));
        let out = read_output(&rec);
        assert!(out.contains("\n…[truncated]…\n"));
        let half = MAX_OUTPUT_CHARS / 2;
        // head(8000 'a') + marker("…[truncated]…", which itself contains one
        // 'a') + tail(8000 'a') -> 16001, matching CPython's own count exactly.
        assert_eq!(out.chars().filter(|c| *c == 'a').count(), half * 2 + 1);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn read_output_missing_file_is_empty() {
        let mut rec = Map::new();
        rec.insert("log_path".into(), json!("/definitely/not/here.log"));
        assert_eq!(read_output(&rec), "");
        // Missing log_path key -> "".
        assert_eq!(read_output(&Map::new()), "");
    }

    #[test]
    fn pid_alive_zero_and_self() {
        assert!(!pid_alive(None));
        assert!(!pid_alive(Some(0)));
        // Our own PID is alive.
        assert!(pid_alive(Some(i64::from(std::process::id()))));
    }

    // --- posix_shell_quote ---

    #[test]
    fn shell_quote_safe_path_is_unquoted() {
        // A typical simple path needs no quoting.
        let p = Path::new("/tmp/bg_jobs/abc123.log");
        assert_eq!(posix_shell_quote(p), "/tmp/bg_jobs/abc123.log");
    }

    #[test]
    fn shell_quote_path_with_spaces() {
        // Spaces are unsafe — the whole string is single-quoted.
        let p = Path::new("/my data/bg jobs/abc.log");
        let q = posix_shell_quote(p);
        assert!(q.starts_with('\'') && q.ends_with('\''));
        // The actual path content must be preserved inside the quotes.
        assert!(q.contains("my data/bg jobs/abc.log"));
    }

    #[test]
    fn shell_quote_path_with_embedded_single_quote() {
        // Single quotes inside the string are escaped as '\''.
        let p = Path::new("/data/it's/here.log");
        let q = posix_shell_quote(p);
        // Must not be a bare single-quoted string that contains an unescaped '.
        assert!(q.contains("'\\''"), "expected escaped apostrophe in: {q}");
    }

    #[test]
    fn shell_quote_empty_path() {
        let p = Path::new("");
        assert_eq!(posix_shell_quote(p), "''");
    }

    // --- load() non-dict filtering ---

    #[test]
    fn load_drops_non_object_records() {
        // Write a store file where one entry is a dict and another is a scalar.
        // `load()` must silently drop the scalar, returning only the dict.
        let dir = std::env::temp_dir().join(format!("bgjobs_load_test_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let store_file = dir.join("bg_jobs.json");
        // One valid object entry, one bare string, one bare number.
        let content = r#"{"job1": {"id": "job1", "status": "running"}, "bad1": "not-a-dict", "bad2": 42}"#;
        std::fs::write(&store_file, content).unwrap();

        // Override DATA_DIR for this call via env var scoped to a temp env
        // change.  We set it, call load(), then restore (or unset).
        let prev = std::env::var("DATA_DIR").ok();
        std::env::set_var("DATA_DIR", dir.to_str().unwrap());
        let result = load();
        match prev {
            Some(v) => std::env::set_var("DATA_DIR", v),
            None => std::env::remove_var("DATA_DIR"),
        }

        let _ = std::fs::remove_dir_all(&dir);

        assert!(result.contains_key("job1"), "expected job1 in result");
        assert!(!result.contains_key("bad1"), "scalar string must be dropped");
        assert!(!result.contains_key("bad2"), "scalar number must be dropped");
        // The kept record must still be a dict.
        assert!(result["job1"].is_object());
    }
}
