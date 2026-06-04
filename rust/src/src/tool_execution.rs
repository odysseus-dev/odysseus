// src/tool_execution.rs  <- src/tool_execution.py
//! Tool dispatcher and result formatter for the agent loop.
//! Routes tool blocks to MCP servers or native implementations.
//!
//! Extracted from agent_tools.py.
//!
//! The MCP manager is now the live `Arc<McpManager>`
//! (`agent_tools::get_mcp_manager`), wired in at startup. So `_call_mcp_tool`
//! performs the Python `mcp.call_tool(qualified, args)` against the real manager;
//! the four native tools (bash/python/read_file/write_file) and `web_search`
//! still fall back to `_direct_fallback` when the MCP server reports "not
//! connected" (mirroring Python). `generate_image` (an MCP-only tool with no
//! native fallback) is served by the real `image_gen` MCP server. When NO
//! manager is set the honest soft-fails are preserved verbatim (never a
//! fabricated success): `_direct_fallback` or `{"error": "MCP manager not
//! available for tool '<tool>'", "exit_code": 1}` for `_call_mcp_tool`, and
//! `{"error": "MCP manager not available", "exit_code": 1}` for the `mcp__`
//! dispatch.

use serde_json::{Map, Value};
use std::collections::HashSet;
use std::sync::Arc;

use crate::src::mcp_manager::McpManager;

use crate::pylog as logger;
use crate::src::agent_tools::{ToolBlock, MAX_OUTPUT_CHARS, MAX_READ_CHARS};
use crate::src::search::{comprehensive_web_search, ComprehensiveResult};
use crate::src::tool_security::{is_public_blocked_tool, owner_is_admin_or_single_user};

// MAX_OUTPUT_CHARS = 10_000 / MAX_READ_CHARS = 20_000 live in agent_tools (the
// facade); reused here so the two stay in lock-step (Python re-imports them).

/// `_truncate(text, limit=MAX_OUTPUT_CHARS)` — reuse the facade implementation
/// (char-based slice, identical behavior).
fn _truncate(text: &str, limit: i64) -> String {
    crate::src::agent_tools::_truncate(text, limit)
}

// Bash + python tools used to share a single 60s timeout. That's enough for
// one-shot commands but starves real workloads (pip install, ffmpeg, …). The
// new default is intentionally generous: long enough that real work isn't
// killed mid-flight, but bounded so a runaway process eventually frees the
// worker. The user can cancel sooner via the chat stop button — when the SSE
// stream is torn down, the task running the subprocess is dropped and the child
// is killed (`kill_on_drop(true)`).
pub const DEFAULT_BASH_TIMEOUT: i64 = 60 * 60; // 1 hour
pub const DEFAULT_PYTHON_TIMEOUT: i64 = 60 * 60;

/// How often to push a progress event while a long-running subprocess is still
/// in flight. The frontend cares about "alive" more than "every-byte" — 2s is
/// the sweet spot.
pub const PROGRESS_INTERVAL_S: f64 = 2.0;
/// Tail buffer size — we keep the most recent N lines of stdout + stderr so the
/// progress event includes a "what's it doing right now" snippet.
pub const PROGRESS_TAIL_LINES: i64 = 12;

// ---------------------------------------------------------------------------
// Path confinement for read_file / write_file
// ---------------------------------------------------------------------------
// read_file + write_file are admin-only tools, but the path the agent supplies
// is model-controlled. Prompt-injection in an admin's chat can weaponise "read
// /etc/shadow" or "write ~/.ssh/authorized_keys" without the admin noticing.
//
// Policy:
//   1. Sensitive-subpath deny list — checked FIRST. Blocks .ssh, .gnupg, shell
//      rc files, token/env files even if the root above them is on the
//      allowlist.
//   2. Allowlist — only the directories the agent legitimately needs (project
//      data/, system tmp). $HOME is NOT on the default list.
//   3. Opt-in extra roots — admin can add broader roots via the
//      "tool_path_extra_roots" setting (list of path strings).
// ---------------------------------------------------------------------------

/// `_SENSITIVE_BASENAMES` — a path component matching any of these is blocked.
pub static _SENSITIVE_BASENAMES: once_cell::sync::Lazy<HashSet<&'static str>> =
    once_cell::sync::Lazy::new(|| {
        [
            ".ssh", ".gnupg", ".gitconfig",
            ".bashrc", ".bash_profile", ".bash_logout",
            ".zshrc", ".zprofile", ".zshenv",
            ".profile", ".tcshrc", ".cshrc",
            ".env", ".netrc",
        ]
        .into_iter()
        .collect()
    });

/// `_SENSITIVE_FILE_PATTERNS` — a final-component filename equal to any of these
/// is blocked (Python uses `pat in filenames` where `filenames == {basename}`,
/// i.e. exact-match against the basename).
pub static _SENSITIVE_FILE_PATTERNS: [&str; 5] = [
    "authorized_keys", "id_rsa", "id_ed25519", "id_ecdsa", "known_hosts",
];

/// `_is_sensitive_path(resolved)` — True if *resolved* falls under a sensitive
/// directory or matches a sensitive filename, regardless of what root it sits
/// under. Mirrors `tool_execution.py:_is_sensitive_path`.
pub fn _is_sensitive_path(resolved: &str) -> bool {
    // parts = resolved.split(os.sep)
    let parts: Vec<&str> = resolved.split(std::path::MAIN_SEPARATOR).collect();
    // filenames = {parts[-1]} if parts else set()
    // (split() always yields at least one element, so parts is never empty.)
    let last = parts.last().copied();

    // Check if any path component is a sensitive directory.
    for part in &parts {
        if _SENSITIVE_BASENAMES.contains(*part) {
            return true;
        }
    }

    // Check filename against known sensitive files (`pat in filenames`, where
    // filenames is the single-element set {basename} -> exact-match the basename).
    if let Some(name) = last {
        for pat in _SENSITIVE_FILE_PATTERNS {
            if pat == name {
                return true;
            }
        }
    }

    false
}

/// `_tool_path_roots()` — the directory roots read_file / write_file may touch.
/// Default: project data/ + system temp dirs. Extra roots come from the
/// ``tool_path_extra_roots`` setting. Symlinks are resolved so containment is
/// unambiguous; duplicates are removed (insertion order preserved).
pub fn _tool_path_roots() -> Vec<String> {
    let mut roots: Vec<String> = Vec::new();

    // Project data directory — the agent's primary workspace.
    roots.push(crate::src::constants::DATA_DIR.clone());

    // /tmp (and its macOS realpath /private/tmp).
    roots.push("/tmp".to_string());
    let private_tmp = crate::src::app_helpers::realpath("/tmp");
    if private_tmp != "/tmp" {
        roots.push(private_tmp);
    }

    // $TMPDIR — per-user temp root on macOS (e.g. /var/folders/.../T/).
    if let Ok(tmpdir) = std::env::var("TMPDIR") {
        if !tmpdir.is_empty() {
            roots.push(tmpdir);
        }
    }

    // Opt-in extra roots from settings.
    // extra = get_setting("tool_path_extra_roots"); if isinstance(extra, list): ...
    if let Value::Array(extra) = crate::src::settings::get_setting("tool_path_extra_roots", Value::Null) {
        for r in extra {
            // roots.extend(str(r) for r in extra if r) — skip falsy entries
            // (empty string / null / 0 / false).
            let s = match &r {
                Value::String(s) => s.clone(),
                Value::Null => continue,
                Value::Bool(false) => continue,
                Value::Number(n) if n.as_f64() == Some(0.0) => continue,
                other => other.to_string(),
            };
            if !s.is_empty() {
                roots.push(s);
            }
        }
    }

    // Deduplicate; resolve symlinks so containment is unambiguous.
    let mut seen: HashSet<String> = HashSet::new();
    let mut out: Vec<String> = Vec::new();
    for r in roots {
        // os.path.realpath; the Rust analogue never errors (falls back to the
        // lexical form), so the Python `except OSError: continue` has no hit.
        let real = crate::src::app_helpers::realpath(&r);
        if seen.contains(&real) {
            continue;
        }
        seen.insert(real.clone());
        out.push(real);
    }
    out
}

/// `_resolve_tool_path(raw_path)` — resolve and confine a model-supplied path.
///
/// Order of checks:
///   1. Non-empty path.
///   2. Sensitive-subpath deny list (blocks .ssh, .gnupg, etc. even when the
///      root is on the allowlist).
///   3. Allowlist containment (must land under one of the roots).
///
/// Returns the realpath on success. Returns `Err(message)` on rejection
/// (Python raises `ValueError(message)`). Symlinks are resolved before
/// comparison.
pub fn _resolve_tool_path(raw_path: &str) -> Result<String, String> {
    // if raw_path is None or not str(raw_path).strip(): raise ValueError(...)
    if raw_path.trim().is_empty() {
        return Err("path is required".to_string());
    }
    // expanded = os.path.expanduser(str(raw_path).strip())
    let expanded = expanduser(raw_path.trim());
    // resolved = os.path.realpath(expanded)
    let resolved = crate::src::app_helpers::realpath(&expanded);

    if _is_sensitive_path(&resolved) {
        return Err(format!(
            "path '{raw_path}' is inside a sensitive directory \
(e.g. .ssh, .gnupg) or matches a sensitive filename"
        ));
    }

    for root in _tool_path_roots() {
        if resolved == root {
            return Ok(resolved);
        }
        // try: common = os.path.commonpath([resolved, root]) except ValueError: continue
        if let Some(common) = crate::src::app_helpers::commonpath(&[&resolved, &root]) {
            if common == root {
                return Ok(resolved);
            }
        }
    }
    Err(format!("path '{raw_path}' is outside the allowed roots"))
}

/// `os.path.expanduser` for a leading `~` / `~/`. Returns the path unchanged when
/// there is no `HOME` (mirroring CPython, which leaves `~` untouched then).
fn expanduser(path: &str) -> String {
    if path == "~" || path.starts_with("~/") {
        if let Ok(home) = std::env::var("HOME") {
            if path == "~" {
                return home;
            }
            return format!("{home}{}", &path[1..]);
        }
    }
    path.to_string()
}

/// `get_mcp_manager()` — re-exports the facade global (the live
/// `Arc<McpManager>` set at startup). Python `tool_execution.py:46-48`.
fn get_mcp_manager() -> Option<Arc<McpManager>> {
    crate::src::agent_tools::get_mcp_manager()
}

// ---------------------------------------------------------------------------
// Progress callback type (shared with agent_loop)
// ---------------------------------------------------------------------------
//
// Python's `progress_cb: Optional[Callable[[Dict], Awaitable[None]]]`. The
// payload is always `{"elapsed_s": round(elapsed, 1), "tail": "\n".join(tail)}`.
/// `progress_cb` — an async callback taking a JSON payload, returning `()`.
pub type ProgressCb = std::sync::Arc<
    dyn Fn(Value) -> std::pin::Pin<Box<dyn std::future::Future<Output = ()> + Send>>
        + Send
        + Sync,
>;

// ---------------------------------------------------------------------------
// Streaming subprocess runner
// ---------------------------------------------------------------------------

/// Run a subprocess to completion, streaming progress.
///
/// Reads stdout + stderr line-by-line into ring buffers so a periodic progress
/// callback can emit a "tail" of recent output without waiting for the full
/// result. Returns `(full_stdout, full_stderr, return_code, timed_out)`.
///
/// `timed_out == true` means the process was killed because it ran past
/// `timeout` seconds. Whatever output we'd buffered up to that point is still
/// returned.
///
/// DEVIATION (cancellation): Python catches `asyncio.CancelledError` to kill the
/// child and re-raise so the agent loop's cancellation propagates. In the Rust
/// port the future is simply dropped when the consumer is dropped; `Child` is
/// spawned with `kill_on_drop(true)`, so the orphan-kill happens automatically.
/// The explicit cancellation arm therefore has no analogue and is omitted.
async fn _run_subprocess_streaming(
    mut proc: tokio::process::Child,
    timeout: f64,
    progress_cb: Option<ProgressCb>,
) -> (String, String, Option<i32>, bool) {
    use std::collections::VecDeque;
    use std::sync::{Arc, Mutex};
    use tokio::io::{AsyncBufReadExt, BufReader};

    let started = std::time::Instant::now();
    let stdout_full: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
    let stderr_full: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
    // collections.deque(maxlen=PROGRESS_TAIL_LINES) — shared ring buffer.
    let tail: Arc<Mutex<VecDeque<String>>> =
        Arc::new(Mutex::new(VecDeque::with_capacity(PROGRESS_TAIL_LINES as usize)));

    // `_reader(stream, full_buf, label)` — read lines (errors='replace' via
    // `from_utf8_lossy`), rstrip a trailing newline, append to `full_buf`, push
    // an `! `-prefixed line into the tail for stderr.
    let spawn_reader = |stream: Option<tokio::process::ChildStdout>,
                        stderr: Option<tokio::process::ChildStderr>,
                        full_buf: Arc<Mutex<Vec<String>>>,
                        tail: Arc<Mutex<VecDeque<String>>>,
                        is_err: bool| {
        tokio::spawn(async move {
            // Read raw bytes line-by-line so a non-UTF-8 byte can be replaced
            // rather than abort the read (Python `decode(errors="replace")`).
            let mut lines: Box<dyn tokio::io::AsyncBufRead + Unpin + Send> = if is_err {
                match stderr {
                    Some(s) => Box::new(BufReader::new(s)),
                    None => return,
                }
            } else {
                match stream {
                    Some(s) => Box::new(BufReader::new(s)),
                    None => return,
                }
            };
            let mut raw: Vec<u8> = Vec::new();
            loop {
                raw.clear();
                let n = match lines.read_until(b'\n', &mut raw).await {
                    Ok(n) => n,
                    Err(_) => break,
                };
                if n == 0 {
                    break; // EOF (Python `if not line: break`)
                }
                // line.decode("utf-8", errors="replace").rstrip("\n")
                let mut decoded = String::from_utf8_lossy(&raw).into_owned();
                while decoded.ends_with('\n') {
                    decoded.pop();
                }
                full_buf.lock().unwrap().push(decoded.clone());
                let entry = if is_err {
                    format!("! {decoded}")
                } else {
                    decoded
                };
                let mut t = tail.lock().unwrap();
                if t.len() == PROGRESS_TAIL_LINES as usize {
                    t.pop_front();
                }
                t.push_back(entry);
            }
        })
    };

    let child_stdout = proc.stdout.take();
    let child_stderr = proc.stderr.take();
    let rd_out = spawn_reader(
        child_stdout,
        None,
        Arc::clone(&stdout_full),
        Arc::clone(&tail),
        false,
    );
    let rd_err = spawn_reader(
        None,
        child_stderr,
        Arc::clone(&stderr_full),
        Arc::clone(&tail),
        true,
    );

    // `_progress_emitter` — skip the first push (many commands finish under
    // PROGRESS_INTERVAL_S), then emit `{elapsed_s, tail}` every interval.
    let prog_task = progress_cb.as_ref().map(|cb| {
        let cb = Arc::clone(cb);
        let tail = Arc::clone(&tail);
        let interval = std::time::Duration::from_secs_f64(PROGRESS_INTERVAL_S);
        tokio::spawn(async move {
            // Skip the first push — many commands finish well under
            // PROGRESS_INTERVAL_S and a 0-second "progress" event would just add
            // UI churn.
            tokio::time::sleep(interval).await;
            loop {
                // Progress is best-effort — `cb_call` swallows any error, never
                // letting a UI hiccup break the underlying subprocess (Python's
                // bare `except`).
                let elapsed = started.elapsed().as_secs_f64();
                cb_call(&cb, &tail, elapsed).await;
                tokio::time::sleep(interval).await;
            }
        })
    });

    let mut timed_out = false;
    // await asyncio.wait_for(proc.wait(), timeout=timeout)
    let wait_dur = std::time::Duration::from_secs_f64(timeout);
    let return_code: Option<i32> = match tokio::time::timeout(wait_dur, proc.wait()).await {
        Ok(Ok(status)) => exit_code_of(&status),
        Ok(Err(_)) => None,
        Err(_) => {
            // asyncio.TimeoutError -> kill, then wait up to 2s.
            timed_out = true;
            let _ = proc.start_kill();
            let _ = tokio::time::timeout(std::time::Duration::from_secs(2), proc.wait()).await;
            // Python reads `proc.returncode` after the kill+wait; after a kill
            // there is no clean exit code.
            None
        }
    };

    // finally: cancel the progress emitter; drain the readers (bounded wait).
    if let Some(t) = prog_task {
        t.abort();
    }
    let _ = tokio::time::timeout(std::time::Duration::from_secs(1), rd_out).await;
    let _ = tokio::time::timeout(std::time::Duration::from_secs(1), rd_err).await;

    let out = stdout_full.lock().unwrap().join("\n");
    let err = stderr_full.lock().unwrap().join("\n");
    (out, err, return_code, timed_out)
}

/// `round(x, 1)` for the progress payload. Python uses banker's rounding; the
/// 0.05 tie-break difference here is cosmetic (documented drift).
fn round1(x: f64) -> f64 {
    (x * 10.0).round() / 10.0
}

/// Invoke the progress callback with the current `{elapsed_s, tail}` payload,
/// swallowing any error (best-effort, like Python's bare `except`).
async fn cb_call(
    cb: &ProgressCb,
    tail: &std::sync::Arc<std::sync::Mutex<std::collections::VecDeque<String>>>,
    elapsed: f64,
) {
    let snapshot: Vec<String> = tail.lock().unwrap().iter().cloned().collect();
    let payload = serde_json::json!({
        "elapsed_s": round1(elapsed),
        "tail": snapshot.join("\n"),
    });
    cb(payload).await;
}

// ---------------------------------------------------------------------------
// Admin policy
// ---------------------------------------------------------------------------

/// Tools restricted to admins (mirror of the Python `_ADMIN_TOOLS` set).
pub static _ADMIN_TOOLS: once_cell::sync::Lazy<HashSet<&'static str>> =
    once_cell::sync::Lazy::new(|| {
        [
            "app_api",
            "manage_endpoints",
            "manage_mcp",
            "manage_webhooks",
            "manage_tokens",
            "manage_settings",
            "download_model",
            "serve_model",
            "serve_preset",
            "stop_served_model",
            "cancel_download",
        ]
        .into_iter()
        .collect()
    });

/// `_owner_is_admin(owner)` — mirror route-level admin behavior for agent tool
/// execution.
fn _owner_is_admin(owner: Option<&str>) -> bool {
    owner_is_admin_or_single_user(owner)
}

// ---------------------------------------------------------------------------
// MCP-backed tool helpers
// ---------------------------------------------------------------------------

/// Map legacy tool names -> (MCP server_id, MCP tool_name). Kept for parity with
/// the Python `_MCP_TOOL_MAP`; the keys are the routing predicate in the
/// dispatcher (`tool in _MCP_TOOL_MAP`).
pub static _MCP_TOOL_MAP: once_cell::sync::Lazy<
    indexmap::IndexMap<&'static str, (&'static str, &'static str)>,
> = once_cell::sync::Lazy::new(|| {
    indexmap::IndexMap::from([
        ("bash", ("bash", "bash")),
        ("python", ("python", "python")),
        ("read_file", ("filesystem", "read_file")),
        ("write_file", ("filesystem", "write_file")),
        ("web_search", ("web_search", "web_search")),
        ("web_fetch", ("web_fetch", "web_fetch")),
        ("generate_image", ("image_gen", "generate_image")),
    ])
});

/// `_parse_generate_image(content) -> dict`.
pub fn _parse_generate_image(content: &str) -> Map<String, Value> {
    // lines = content.strip().split("\n")
    let lines: Vec<&str> = content.trim().split('\n').collect();
    let mut args = Map::new();
    // args = {"prompt": lines[0].strip() if lines else ""}
    let prompt = lines.first().map(|l| l.trim()).unwrap_or("");
    args.insert("prompt".to_string(), Value::String(prompt.to_string()));
    for (i, key) in ["model", "size", "quality"].iter().enumerate() {
        let idx = i + 1; // enumerate(..., 1)
        if lines.len() > idx && !lines[idx].trim().is_empty() {
            args.insert((*key).to_string(), Value::String(lines[idx].trim().to_string()));
        }
    }
    args
}

/// `_parse_manage_memory(content) -> dict`.
// The nested `if` inside the `"list"` arm mirrors Python's `elif action ==
// "list": if ...:` and is clearer than a match guard, so the lint is allowed.
#[allow(clippy::collapsible_match)]
pub fn _parse_manage_memory(content: &str) -> Map<String, Value> {
    let lines: Vec<&str> = content.trim().split('\n').collect();
    // action = lines[0].strip().lower() if lines else ""
    let action = lines.first().map(|l| l.trim().to_lowercase()).unwrap_or_default();
    let mut args = Map::new();
    args.insert("action".to_string(), Value::String(action.clone()));
    let line_at = |i: usize| -> String { lines.get(i).map(|l| l.trim().to_string()).unwrap_or_default() };
    match action.as_str() {
        "add" => {
            args.insert("text".to_string(), Value::String(line_at(1)));
            if lines.len() > 2 && !lines[2].trim().is_empty() {
                args.insert("category".to_string(), Value::String(lines[2].trim().to_lowercase()));
            }
        }
        "edit" => {
            args.insert("memory_id".to_string(), Value::String(line_at(1)));
            args.insert("text".to_string(), Value::String(line_at(2)));
        }
        "delete" => {
            args.insert("memory_id".to_string(), Value::String(line_at(1)));
        }
        "search" => {
            args.insert("text".to_string(), Value::String(line_at(1)));
        }
        "list" => {
            if lines.len() > 1 && !lines[1].trim().is_empty() {
                args.insert("category".to_string(), Value::String(lines[1].trim().to_lowercase()));
            }
        }
        _ => {}
    }
    args
}

/// `_parse_write_file(content) -> dict`.
pub fn _parse_write_file(content: &str) -> Map<String, Value> {
    // lines = content.split("\n", 1)
    let mut parts = content.splitn(2, '\n');
    let path = parts.next().unwrap_or("");
    let body = parts.next().unwrap_or("");
    let mut m = Map::new();
    m.insert("path".to_string(), Value::String(path.trim().to_string()));
    m.insert("content".to_string(), Value::String(body.to_string()));
    m
}

/// `_build_mcp_args(tool, content)` — convert fenced-block text content to
/// structured MCP arguments. Mirrors the `_MCP_ARG_PARSERS` lambda table with a
/// `match` (no callable map).
pub fn _build_mcp_args(tool: &str, content: &str) -> Map<String, Value> {
    let mut m = Map::new();
    match tool {
        "bash" => {
            m.insert("command".to_string(), Value::String(content.to_string()));
        }
        "python" => {
            m.insert("code".to_string(), Value::String(content.to_string()));
        }
        "web_search" => {
            let q = content.split('\n').next().unwrap_or("").trim();
            m.insert("query".to_string(), Value::String(q.to_string()));
        }
        "web_fetch" => {
            // lambda c: {"url": c.split("\n")[0].strip()}
            let u = content.split('\n').next().unwrap_or("").trim();
            m.insert("url".to_string(), Value::String(u.to_string()));
        }
        "read_file" => {
            let p = content.split('\n').next().unwrap_or("").trim();
            m.insert("path".to_string(), Value::String(p.to_string()));
        }
        "write_file" => return _parse_write_file(content),
        "generate_image" => return _parse_generate_image(content),
        "manage_memory" => return _parse_manage_memory(content),
        _ => {}
    }
    m
}

/// Route a legacy tool call through the MCP manager, with direct fallbacks.
///
/// Mirrors `tool_execution.py:253-274`. When no manager is set, fall back to the
/// native `_direct_fallback` path (or the honest unavailable error). Otherwise
/// build the qualified name + args and call the real `mcp.call_tool`; if the MCP
/// server reports "not connected" (`exit_code == 1` + an `error` containing "not
/// connected"), retry via `_direct_fallback` before returning the MCP result.
pub async fn _call_mcp_tool(
    tool: &str,
    content: &str,
    progress_cb: Option<ProgressCb>,
) -> Map<String, Value> {
    let mcp = match get_mcp_manager() {
        Some(m) => m,
        None => {
            // Python: `return await _direct_fallback(...) or {"error": f"MCP
            // manager not available for tool '{tool}'", "exit_code": 1}`.
            return match _direct_fallback(tool, content, progress_cb).await {
                Some(r) => r,
                None => err_map(&format!("MCP manager not available for tool '{tool}'"), 1),
            };
        }
    };

    // server_id, tool_name = _MCP_TOOL_MAP[tool]  /  qualified = mcp__<s>__<t>
    let qualified = match _MCP_TOOL_MAP.get(tool) {
        Some((s, t)) => format!("mcp__{s}__{t}"),
        None => format!("mcp__{tool}__{tool}"),
    };
    let args = _build_mcp_args(tool, content);

    // result = await mcp.call_tool(qualified, args)
    let result = mcp.call_tool(&qualified, Value::Object(args)).await;

    // If MCP server not connected, try direct fallback:
    //   if isinstance(result, dict) and result.get("exit_code") == 1
    //      and "not connected" in result.get("error", ""):
    let not_connected = result.as_object().is_some_and(|obj| {
        obj.get("exit_code").and_then(|v| v.as_i64()) == Some(1)
            && obj
                .get("error")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .contains("not connected")
    });
    if not_connected {
        if let Some(fallback) = _direct_fallback(tool, content, progress_cb).await {
            return fallback;
        }
    }

    // `call_tool` always returns a `Value::Object`; surface it as the Map the
    // dispatcher expects. A non-object result (impossible here) degrades to an
    // empty map rather than panicking.
    match result {
        Value::Object(m) => m,
        _ => Map::new(),
    }
}

/// Background-launch markers (mirror of Python `_BG_MARKERS`).
pub static _BG_MARKERS: once_cell::sync::Lazy<HashSet<&'static str>> =
    once_cell::sync::Lazy::new(|| {
        [
            "#!bg",
            "#bg",
            "# bg",
            "#background",
            "# background",
            "@background",
            "# @background",
        ]
        .into_iter()
        .collect()
    });

/// `_split_bg_marker(content) -> (bool, str)`.
///
/// If the bash content's first non-empty line is a background marker (e.g.
/// `#!bg`), return `(true, command_without_marker)`; else `(false, content)`.
pub fn _split_bg_marker(content: &str) -> (bool, String) {
    let mut lines: Vec<String> = content.split('\n').map(|s| s.to_string()).collect();
    // skip leading blank lines
    let mut i = 0usize;
    while i < lines.len() && lines[i].trim().is_empty() {
        i += 1;
    }
    if i < lines.len() && _BG_MARKERS.contains(lines[i].trim().to_lowercase().as_str()) {
        // del lines[i]; return True, "\n".join(lines).strip()
        lines.remove(i);
        return (true, lines.join("\n").trim().to_string());
    }
    (false, content.to_string())
}

/// Build a `{"error": msg, "exit_code": code}` result Map.
fn err_map(msg: &str, exit_code: i64) -> Map<String, Value> {
    let mut m = Map::new();
    m.insert("error".to_string(), Value::String(msg.to_string()));
    m.insert("exit_code".to_string(), Value::from(exit_code));
    m
}

/// Build an `{"output": text, "exit_code": code}` result Map.
fn out_map(output: &str, exit_code: i64) -> Map<String, Value> {
    let mut m = Map::new();
    m.insert("output".to_string(), Value::String(output.to_string()));
    m.insert("exit_code".to_string(), Value::from(exit_code));
    m
}

/// In-process execution path for the tools that used to live as stdio MCP
/// servers under `mcp_servers/`. Those servers were deleted in favor of native
/// execution; this is now the canonical path, not a fallback (the name is kept
/// for backwards compat with callers).
///
/// `progress_cb` is called periodically while bash/python subprocesses are still
/// running, with `{elapsed_s, tail}` payloads. Other tools ignore it.
///
/// Returns `Some(result)` for a handled tool (incl. its error map), or `None`
/// for the unhandled `manage_memory`/`generate_image` (which Python leaves to
/// the MCP path) — matching Python's `return None` fallthrough.
pub async fn _direct_fallback(
    tool: &str,
    content: &str,
    progress_cb: Option<ProgressCb>,
) -> Option<Map<String, Value>> {
    use tokio::process::Command;

    // Inherit env + force a sane terminal so subprocesses that touch terminfo
    // don't spam "TERM environment variable not set". COLUMNS/LINES give
    // terminal-width-aware tools reasonable defaults instead of 0×0.
    let apply_env = |cmd: &mut Command| {
        cmd.env("TERM", "xterm-256color");
        cmd.env("COLUMNS", "120");
        cmd.env("LINES", "40");
    };

    // The Python wraps the whole body in try/except returning {"error": f"{tool}:
    // {e}", "exit_code": 1}. The Rust equivalents return error maps inline as the
    // failure occurs; a helper mirrors the generic "{tool}: {e}" path.
    let tool_err = |e: String| -> Option<Map<String, Value>> {
        Some(err_map(&format!("{tool}: {e}"), 1))
    };

    if tool == "bash" {
        let mut cmd = Command::new("/bin/sh");
        cmd.arg("-c").arg(content);
        apply_env(&mut cmd);
        cmd.stdin(std::process::Stdio::null());
        cmd.stdout(std::process::Stdio::piped());
        cmd.stderr(std::process::Stdio::piped());
        cmd.kill_on_drop(true);
        let proc = match cmd.spawn() {
            Ok(p) => p,
            Err(e) => return tool_err(e.to_string()),
        };
        let (stdout, stderr, rc, timed_out) =
            _run_subprocess_streaming(proc, DEFAULT_BASH_TIMEOUT as f64, progress_cb).await;
        if timed_out {
            let mut m = err_map(
                &format!("bash: timed out after {DEFAULT_BASH_TIMEOUT}s — process killed"),
                124,
            );
            m.insert(
                "stdout".to_string(),
                Value::String(_truncate(&stdout, MAX_OUTPUT_CHARS)),
            );
            m.insert(
                "stderr".to_string(),
                Value::String(_truncate(&stderr, MAX_OUTPUT_CHARS)),
            );
            return Some(m);
        }
        return Some(finish_subprocess(stdout, stderr, rc));
    }

    if tool == "python" {
        // -I = isolated mode (skip user site, no PYTHONPATH inheritance).
        let mut cmd = Command::new("python3");
        cmd.arg("-I").arg("-c").arg(content);
        apply_env(&mut cmd);
        cmd.stdin(std::process::Stdio::null());
        cmd.stdout(std::process::Stdio::piped());
        cmd.stderr(std::process::Stdio::piped());
        cmd.kill_on_drop(true);
        let proc = match cmd.spawn() {
            Ok(e) => e,
            Err(e) => return tool_err(e.to_string()),
        };
        let (stdout, stderr, rc, timed_out) =
            _run_subprocess_streaming(proc, DEFAULT_PYTHON_TIMEOUT as f64, progress_cb).await;
        if timed_out {
            let mut m = err_map(
                &format!("python: timed out after {DEFAULT_PYTHON_TIMEOUT}s — process killed"),
                124,
            );
            m.insert(
                "stdout".to_string(),
                Value::String(_truncate(&stdout, MAX_OUTPUT_CHARS)),
            );
            m.insert(
                "stderr".to_string(),
                Value::String(_truncate(&stderr, MAX_OUTPUT_CHARS)),
            );
            return Some(m);
        }
        return Some(finish_subprocess(stdout, stderr, rc));
    }

    if tool == "read_file" {
        // raw_path = content.split("\n", 1)[0].strip()  (only [0] is used, so a
        // plain split's first element is identical)
        let raw_path = content.split('\n').next().unwrap_or("").trim().to_string();
        // path = _resolve_tool_path(raw_path)  /  except ValueError as e: ...
        let path = match _resolve_tool_path(&raw_path) {
            Ok(p) => p,
            Err(e) => return Some(err_map(&format!("read_file: {e}"), 1)),
        };
        // Run blocking read in a thread to keep the loop responsive.
        let path_for_thread = path.clone();
        let read_res = tokio::task::spawn_blocking(move || {
            // Read bytes then lossy-decode (errors='replace'), then take the
            // first MAX_READ_CHARS+1 CHARS.
            match std::fs::read(&path_for_thread) {
                Ok(bytes) => {
                    let decoded = String::from_utf8_lossy(&bytes);
                    // Python opens in TEXT mode -> universal-newline translation:
                    // `\r\n` and a bare `\r` both become `\n` on read. Apply it
                    // before the char-take so content + truncation boundary match.
                    let normalized = decoded.replace("\r\n", "\n").replace('\r', "\n");
                    let limited: String =
                        normalized.chars().take((MAX_READ_CHARS as usize) + 1).collect();
                    Ok(limited)
                }
                Err(e) => Err(e),
            }
        })
        .await;
        let data = match read_res {
            Ok(Ok(d)) => d,
            Ok(Err(e)) => {
                return Some(match e.kind() {
                    std::io::ErrorKind::NotFound => {
                        err_map(&format!("read_file: {path}: not found"), 1)
                    }
                    std::io::ErrorKind::PermissionDenied => {
                        err_map(&format!("read_file: {path}: permission denied"), 1)
                    }
                    _ => err_map(&format!("read_file: {path}: {e}"), 1),
                });
            }
            // spawn_blocking JoinError (panic/cancel) -> generic OSError-ish.
            Err(e) => return Some(err_map(&format!("read_file: {path}: {e}"), 1)),
        };
        // truncated = len(data) > MAX_READ_CHARS  (CHAR count)
        let char_count = data.chars().count() as i64;
        let data = if char_count > MAX_READ_CHARS {
            let head: String = data.chars().take(MAX_READ_CHARS as usize).collect();
            format!("{head}\n... [truncated at {MAX_READ_CHARS} chars]")
        } else {
            data
        };
        return Some(out_map(&data, 0));
    }

    if tool == "write_file" {
        // lines = content.split("\n", 1)
        let mut parts = content.splitn(2, '\n');
        let raw_path = parts.next().unwrap_or("").trim().to_string();
        let body = parts.next().unwrap_or("").to_string();
        // path = _resolve_tool_path(raw_path)  /  except ValueError as e: ...
        let path = match _resolve_tool_path(&raw_path) {
            Ok(p) => p,
            Err(e) => return Some(err_map(&format!("write_file: {e}"), 1)),
        };
        let path_for_thread = path.clone();
        let body_for_thread = body.clone();
        let write_res = tokio::task::spawn_blocking(move || -> std::io::Result<i64> {
            // d = os.path.dirname(path); if d: os.makedirs(d, exist_ok=True)
            let d = crate::pyos::path::dirname(&path_for_thread);
            if !d.is_empty() {
                std::fs::create_dir_all(&d)?;
            }
            std::fs::write(&path_for_thread, body_for_thread.as_bytes())?;
            // return len(body)  -- Python len() = code-point count.
            Ok(body_for_thread.chars().count() as i64)
        })
        .await;
        return Some(match write_res {
            Ok(Ok(size)) => out_map(&format!("Wrote {size} bytes to {path}"), 0),
            Ok(Err(e)) => match e.kind() {
                std::io::ErrorKind::PermissionDenied => {
                    err_map(&format!("write_file: {path}: permission denied"), 1)
                }
                _ => err_map(&format!("write_file: {path}: {e}"), 1),
            },
            Err(e) => err_map(&format!("write_file: {path}: {e}"), 1),
        });
    }

    if tool == "web_search" {
        // Python: web_search arm (tool_execution.py:413-462). Parse the JSON-arg +
        // freshness heuristic, then run `comprehensive_web_search` via the executor
        // under a 30s timeout and format the report (+ embedded SOURCES comment).
        let raw = content.trim();
        let mut query = raw.to_string();
        let mut time_filter: Option<String> = None;
        let mut max_pages: i64 = 5;
        if raw.starts_with('{') {
            if let Ok(Value::Object(parsed)) = serde_json::from_str::<Value>(raw) {
                if parsed.contains_key("query") {
                    query = parsed
                        .get("query")
                        .map(value_to_str)
                        .unwrap_or_default()
                        .trim()
                        .to_string();
                    let tf = parsed
                        .get("time_filter")
                        .filter(|v| !v.is_null())
                        .or_else(|| parsed.get("freshness"));
                    if let Some(Value::String(s)) = tf {
                        let lc = s.to_lowercase();
                        if matches!(lc.as_str(), "day" | "week" | "month" | "year") {
                            time_filter = Some(lc);
                        }
                    }
                    if let Some(mp) = parsed.get("max_pages").and_then(|v| v.as_i64()) {
                        // isinstance(mp, int) and 1 <= mp <= 10
                        if (1..=10).contains(&mp) {
                            max_pages = mp;
                        }
                    }
                }
            }
        }
        if query.is_empty() {
            query = raw.split('\n').next().unwrap_or("").trim().to_string();
        }
        // Auto-detect freshness from query phrasing when not explicit.
        if time_filter.is_none() {
            let q_lc = query.to_lowercase();
            if ["today", "latest", "breaking", "this morning", "right now", "currently"]
                .iter()
                .any(|kw| q_lc.contains(kw))
            {
                time_filter = Some("day".to_string());
            } else if ["this week", "past week", "recent news", "last few days"]
                .iter()
                .any(|kw| q_lc.contains(kw))
            {
                time_filter = Some("week".to_string());
            } else if ["this month", "past month"].iter().any(|kw| q_lc.contains(kw)) {
                time_filter = Some("month".to_string());
            } else if q_lc.contains(" news") || q_lc.starts_with("news ") || q_lc.ends_with(" news") {
                time_filter = Some("week".to_string());
            }
        }
        // Python: text, sources = await asyncio.wait_for(
        //     loop.run_in_executor(None, lambda: comprehensive_web_search(
        //         query, max_pages=max_pages, time_filter=time_filter, return_sources=True)),
        //     timeout=30)
        // `comprehensive_web_search` is synchronous/blocking (reqwest blocking), so
        // run it on a blocking thread under a 30s timeout — mirroring the executor +
        // asyncio.wait_for(timeout=30). max_workers=4 is the Python kwarg default; all
        // filters are None; min_content_length=0; return_sources=True.
        let owned_query = query;
        let owned_time_filter = time_filter;
        let result = tokio::time::timeout(
            std::time::Duration::from_secs(30),
            tokio::task::spawn_blocking(move || {
                comprehensive_web_search(
                    &owned_query,
                    max_pages,
                    4,
                    owned_time_filter.as_deref(),
                    None,
                    None,
                    None,
                    None,
                    0,
                    true,
                )
            }),
        )
        .await;
        // On TimeoutError (outer) or a join error -> the broad except path
        // `{"error": f"web_search: {e}", "exit_code": 1}`.
        let comp = match result {
            Ok(Ok(c)) => c,
            Ok(Err(join_err)) => return tool_err(join_err.to_string()),
            // Python: asyncio.wait_for raises asyncio.TimeoutError whose str() is
            // empty, so the broad except yields `web_search: ` (no detail).
            Err(_elapsed) => return tool_err(String::new()),
        };
        // text, sources = ...  (Report -> no sources)
        let (text, sources) = match comp {
            ComprehensiveResult::WithSources(t, s) => (t, s),
            ComprehensiveResult::Report(t) => (t, Vec::new()),
        };
        // output = text[:MAX_OUTPUT_CHARS] if len(text) > MAX_OUTPUT_CHARS else text
        let mut output: String = if text.chars().count() > MAX_OUTPUT_CHARS as usize {
            text.chars().take(MAX_OUTPUT_CHARS as usize).collect()
        } else {
            text
        };
        // if sources: output += "\n\n<!-- SOURCES:" + json.dumps(sources) + " -->"
        if !sources.is_empty() {
            output.push_str("\n\n<!-- SOURCES:");
            output.push_str(&serde_json::to_string(&sources).unwrap());
            output.push_str(" -->");
        }
        return Some(out_map(&output, 0));
    }

    if tool == "web_fetch" {
        // Lightweight single-URL fetch. Wraps the SSRF-safe fetcher used by deep
        // research, so private/loopback/metadata addresses are already blocked
        // there. Mirrors `tool_execution.py:web_fetch` arm.
        let raw = content.trim();
        let mut url = String::new();
        // Accept either a JSON arg ({"url": "..."}) or a plain URL/domain.
        if raw.starts_with('{') {
            if let Ok(Value::Object(parsed)) = serde_json::from_str::<Value>(raw) {
                // url = str(parsed.get("url") or "").strip()
                let u = parsed.get("url");
                // `or ""`: a falsy value (None / "" / false / 0) -> "".
                url = match u {
                    Some(Value::String(s)) if !s.is_empty() => s.clone(),
                    Some(Value::Bool(true)) => "True".to_string(),
                    Some(Value::Number(n)) if n.as_f64() != Some(0.0) => n.to_string(),
                    Some(Value::Array(a)) if !a.is_empty() => value_to_str(u.unwrap()),
                    Some(Value::Object(o)) if !o.is_empty() => value_to_str(u.unwrap()),
                    _ => String::new(),
                };
                url = url.trim().to_string();
            }
            // except _json.JSONDecodeError: url = "" (already empty)
        }
        if url.is_empty() {
            // Non-JSON (or JSON without a usable url): take the first line only,
            // so a URL followed by commentary still parses.
            url = raw.split('\n').next().unwrap_or("").trim().to_string();
        }
        // Reject anything that isn't a single bare URL/domain token.
        if url.is_empty()
            || url.starts_with('{')
            || url.contains(' ')
            || url.contains('\t')
            || url.contains('\n')
        {
            return Some(err_map(
                "web_fetch: provide a single URL or domain, e.g. example.com",
                1,
            ));
        }
        let low = url.to_lowercase();
        if low.contains("://") && !(low.starts_with("http://") || low.starts_with("https://")) {
            // url[:80] (CHAR slice)
            let head: String = url.chars().take(80).collect();
            return Some(err_map(
                &format!("web_fetch: unsupported URL scheme (only http/https): {head}"),
                1,
            ));
        }
        // Accept bare domains like "example.com" by defaulting to https.
        if !(low.starts_with("http://") || low.starts_with("https://")) {
            url = format!("https://{url}");
        }
        // Python: result = await asyncio.wait_for(
        //     loop.run_in_executor(None, lambda: fetch_webpage_content(url, timeout=10)),
        //     timeout=30)
        // `fetch_webpage_content` is synchronous/blocking, so run it on a blocking
        // thread under a 30s timeout. retry_attempt defaults to 0.
        let owned_url = url.clone();
        let fetched = tokio::time::timeout(
            std::time::Duration::from_secs(30),
            tokio::task::spawn_blocking(move || {
                crate::src::search::fetch_webpage_content(&owned_url, 10, 0)
            }),
        )
        .await;
        let result: Value = match fetched {
            Ok(Ok(v)) => v,
            // except asyncio.TimeoutError: -> "web_fetch: timed out fetching {url}"
            Err(_elapsed) => {
                return Some(err_map(&format!("web_fetch: timed out fetching {url}"), 1));
            }
            // except Exception as e: -> "web_fetch: {url}: {e}" (join error / panic).
            Ok(Err(join_err)) => {
                return Some(err_map(&format!("web_fetch: {url}: {join_err}"), 1));
            }
        };
        // err = result.get("error")
        let err = result.get("error").and_then(|v| v.as_str()).unwrap_or("");
        // text = (result.get("content") or "").strip()
        let text = result
            .get("content")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim();
        // title = result.get("title") or ""
        let title = result.get("title").and_then(|v| v.as_str()).unwrap_or("");

        if text.is_empty() {
            if !err.is_empty() {
                return Some(err_map(&format!("web_fetch: {url}: {err}"), 1));
            }
            // No extractable text: non-HTML body, or a pure client-rendered shell.
            return Some(err_map(
                &format!(
                    "web_fetch: {url}: no readable text content (not HTML, or the page needs JS/login)"
                ),
                1,
            ));
        }

        // header = (f"# {title}\n" if title else "") + f"Source: {url}\n\n"
        let header = if !title.is_empty() {
            format!("# {title}\nSource: {url}\n\n")
        } else {
            format!("Source: {url}\n\n")
        };
        let mut output = format!("{header}{text}");
        // if len(output) > MAX_OUTPUT_CHARS: output = output[:MAX...] + "\n\n[...truncated]"
        if output.chars().count() > MAX_OUTPUT_CHARS as usize {
            let head: String = output.chars().take(MAX_OUTPUT_CHARS as usize).collect();
            output = format!("{head}\n\n[...truncated]");
        }
        return Some(out_map(&output, 0));
    }

    // manage_memory / generate_image still live as MCP servers; the MCP path
    // above handles them. -> return None (fallthrough).
    None
}

/// `proc.returncode` parity: the exit code, or — on Unix, when the process was
/// terminated by a signal — the negative signal number (Python reports `-N`,
/// e.g. SIGSEGV -> -11), which then survives `rc or 0` as a nonzero exit_code.
fn exit_code_of(status: &std::process::ExitStatus) -> Option<i32> {
    if let Some(c) = status.code() {
        return Some(c);
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::ExitStatusExt;
        if let Some(sig) = status.signal() {
            return Some(-sig);
        }
    }
    None
}

/// Shared bash/python success-path result builder:
/// `output = stdout.rstrip(); err = stderr.rstrip(); if err: output = ...; ...`.
fn finish_subprocess(stdout: String, stderr: String, rc: Option<i32>) -> Map<String, Value> {
    let mut output = stdout.trim_end().to_string();
    let err = stderr.trim_end().to_string();
    if !err.is_empty() {
        // (output + "\nSTDERR: " + err).strip() if output else "STDERR: " + err
        output = if !output.is_empty() {
            format!("{output}\nSTDERR: {err}").trim().to_string()
        } else {
            format!("STDERR: {err}")
        };
    }
    let output = _truncate(&output, MAX_OUTPUT_CHARS);
    // {"output": output or "(no output)", "exit_code": rc or 0}
    let out = if output.is_empty() {
        "(no output)".to_string()
    } else {
        output
    };
    // `rc or 0` — Python treats None/0 as falsy -> 0.
    let exit_code = rc.filter(|&c| c != 0).unwrap_or(0) as i64;
    out_map(&out, exit_code)
}

/// `str(value)` for the `web_search` JSON-arg `query` coercion.
fn value_to_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Null => "None".to_string(),
        other => other.to_string(),
    }
}

// ---------------------------------------------------------------------------
// Dispatcher
// ---------------------------------------------------------------------------

/// Execute a single tool block. Returns `(description, result_dict)`.
///
/// `progress_cb` is forwarded to long-running subprocess tools (bash, python) so
/// the agent loop can emit `tool_progress` SSE events while the command is in
/// flight. Ignored by other tools.
pub async fn execute_tool_block(
    block: &ToolBlock,
    session_id: Option<&str>,
    disabled_tools: Option<&HashSet<String>>,
    owner: Option<&str>,
    progress_cb: Option<ProgressCb>,
) -> (String, Map<String, Value>) {
    use crate::src::ai_interaction::dispatch_ai_tool;
    use crate::src::tool_implementations::{
        do_adopt_served_model, do_api_call, do_app_api, do_cancel_download, do_create_document,
        do_download_model, do_edit_document, do_edit_image, do_list_cached_models,
        do_list_cookbook_servers, do_list_downloads, do_list_served_models, do_list_serve_presets,
        do_manage_calendar, do_manage_contact, do_manage_documents, do_manage_endpoints,
        do_manage_mcp, do_manage_notes, do_manage_research, do_manage_settings, do_manage_skills,
        do_manage_tasks, do_manage_tokens, do_manage_webhooks, do_resolve_contact,
        do_search_chats, do_search_hf_models, do_serve_model, do_serve_preset,
        do_stop_served_model, do_suggest_document, do_trigger_research, do_update_document,
        do_vault_get, do_vault_search, do_vault_unlock,
    };

    let tool = block.tool_type.as_str();
    let content = block.content.as_str();

    // Misformatted tool call detection: model put JSON inside ```python``` (or
    // similar) without naming the tool. Return a helpful error so the model
    // retries with the correct format.
    if matches!(tool, "python" | "json" | "xml")
        && content.trim().starts_with('{')
        && content.trim().ends_with('}')
    {
        if let Ok(parsed) = serde_json::from_str::<Value>(content.trim()) {
            if parsed.is_object() {
                let desc = format!("{tool}: misformatted tool call");
                let result = err_map(
                    &format!(
                        "You wrote a JSON object inside a ```{tool}``` block, but that's not a tool call.\n\
To call a tool, use the tool name as the fence tag, e.g.\n\
```resolve_contact\n\
{{\"name\": \"...\"}}\n\
```\n\
or\n\
```send_email\n\
{{\"to\": \"...\", \"subject\": \"...\", \"body\": \"...\"}}\n\
```"
                    ),
                    1,
                );
                return (desc, result);
            }
        }
    }

    // Reject tools that the user has disabled for this request.
    if let Some(disabled) = disabled_tools {
        if disabled.contains(tool) {
            let desc = format!("{tool}: BLOCKED");
            let result = err_map(&format!("Tool '{tool}' is disabled by user."), 1);
            logger::info(&format!("Tool blocked by user: {tool}"));
            return (desc, result);
        }
    }

    if _ADMIN_TOOLS.contains(tool) && !_owner_is_admin(owner) {
        let desc = format!("{tool}: BLOCKED");
        let result = err_map(&format!("Tool '{tool}' requires an admin user."), 1);
        logger::warning(&format!(
            "Admin tool blocked for non-admin owner={owner:?} tool={tool}"
        ));
        return (desc, result);
    }

    if is_public_blocked_tool(Some(tool)) && !_owner_is_admin(owner) {
        let desc = format!("{tool}: BLOCKED");
        let result = err_map(
            &format!(
                "Tool '{tool}' is restricted to admin users on this deployment. \
Ask an admin to perform this action or grant the needed permission."
            ),
            1,
        );
        logger::warning(&format!(
            "Public tool policy blocked owner={owner:?} tool={tool}"
        ));
        return (desc, result);
    }

    // Background execution: a `bash` block whose first line is the `#!bg` marker
    // runs DETACHED via `bg_jobs.launch` — returns a job id immediately so the chat
    // stream isn't held open for a multi-minute install/ffmpeg/download. The
    // always-on monitor re-invokes the agent with the full output when it finishes.
    if tool == "bash" {
        if let Some(sid) = session_id {
            let (is_bg, bg_cmd) = _split_bg_marker(content);
            if is_bg && !bg_cmd.is_empty() {
                // rec = bg_jobs.launch(_bg_cmd, session_id=session_id)
                // Python passes cwd=None + default max_runtime → cwd=None,
                // max_runtime_s=DEFAULT_MAX_RUNTIME_S.
                // short = _bg_cmd.strip().split("\n")[0][:80]
                let short: String = bg_cmd
                    .trim()
                    .split('\n')
                    .next()
                    .unwrap_or("")
                    .chars()
                    .take(80)
                    .collect();
                let desc = format!("bash (background): {short}");
                let rec = match crate::src::bg_jobs::launch(
                    &bg_cmd,
                    sid,
                    None,
                    crate::src::bg_jobs::DEFAULT_MAX_RUNTIME_S,
                ) {
                    Ok(r) => r,
                    Err(e) => {
                        // A launch failure surfaces as a tool error (Python's launch
                        // would raise into execute_tool_block's caller; here we keep
                        // the dispatcher contract by returning an error map).
                        let result = err_map(&format!("bash (background): {e}"), 1);
                        return (desc, result);
                    }
                };
                let job_id: String = rec
                    .get("id")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                // result = {"output": f"Started background job `{rec['id']}`. ...",
                //           "exit_code": 0, "bg_job_id": rec["id"]}
                let output = format!(
                    "Started background job `{job_id}`. It is running detached — \
do NOT wait for it or poll it. You will be automatically re-invoked \
with its full output when it finishes. Continue with other work, or \
end your turn now and resume when the result arrives."
                );
                let mut result = out_map(&output, 0);
                result.insert("bg_job_id".to_string(), serde_json::json!(job_id));
                logger::info(&format!("Tool executed: {desc} -> bg job {job_id}"));
                return (desc, result);
            }
        }
    }

    let (desc, result): (String, Map<String, Value>);

    // Route MCP-extracted tools through the (native) MCP path. Forward the
    // progress callback so long-running subprocess tools can stream progress.
    if _MCP_TOOL_MAP.contains_key(tool) {
        let first_line: String = content.split('\n').next().unwrap_or("").chars().take(80).collect();
        desc = format!("{tool}: {first_line}");
        result = _call_mcp_tool(tool, content, progress_cb).await;
    } else if tool == "create_document" {
        let title: String = content.split('\n').next().unwrap_or("").trim().chars().take(60).collect();
        desc = format!("create_document: {title}");
        result = do_create_document(content, session_id, owner).await;
    } else if tool == "update_document" {
        let first: String = content.split('\n').next().unwrap_or("").chars().take(60).collect();
        desc = format!("update_document: {first}");
        result = do_update_document(content, None, owner).await;
    } else if tool == "edit_document" {
        result = do_edit_document(content, None, owner).await;
        let title = result.get("title").and_then(|v| v.as_str()).unwrap_or("");
        desc = format!("edit_document: {title}");
    } else if tool == "suggest_document" {
        result = do_suggest_document(content, None, owner).await;
        let count = result.get("count").and_then(|v| v.as_i64()).unwrap_or(0);
        desc = format!("suggest_document: {count} suggestions");
    } else if tool == "search_chats" {
        let query = content.split('\n').next().unwrap_or("").trim().to_string();
        let q80: String = query.chars().take(80).collect();
        desc = format!("search_chats: {q80}");
        // do_search_chats(query, limit=20, owner=owner)
        result = do_search_chats(&query, 20, owner).await;
    } else if matches!(
        tool,
        "chat_with_model"
            | "create_session"
            | "list_sessions"
            | "send_to_session"
            | "pipeline"
            | "manage_session"
            | "manage_memory"
            | "list_models"
            | "ui_control"
            | "ask_teacher"
    ) {
        let (d, r) = dispatch_ai_tool(tool, content, session_id, owner).await;
        desc = d;
        result = r;
    } else if tool == "manage_tasks" {
        desc = "manage_tasks".to_string();
        result = do_manage_tasks(content, owner).await;
    } else if tool == "manage_skills" {
        desc = "manage_skills".to_string();
        result = do_manage_skills(content, owner).await;
    } else if tool == "api_call" {
        let first_line: String = content.split('\n').next().unwrap_or("").trim().chars().take(60).collect();
        desc = format!("api_call: {first_line}");
        result = do_api_call(content, owner).await;
    } else if tool == "manage_endpoints" {
        desc = "manage_endpoints".to_string();
        result = do_manage_endpoints(content, owner).await;
    } else if tool == "manage_mcp" {
        desc = "manage_mcp".to_string();
        result = do_manage_mcp(content, owner).await;
    } else if tool == "manage_webhooks" {
        desc = "manage_webhooks".to_string();
        result = do_manage_webhooks(content, owner).await;
    } else if tool == "manage_tokens" {
        desc = "manage_tokens".to_string();
        result = do_manage_tokens(content, owner).await;
    } else if tool == "manage_documents" {
        desc = "manage_documents".to_string();
        result = do_manage_documents(content, owner).await;
    } else if tool == "manage_settings" {
        desc = "manage_settings".to_string();
        result = do_manage_settings(content, owner).await;
    } else if tool == "manage_notes" {
        desc = "manage_notes".to_string();
        result = do_manage_notes(content, owner).await;
    } else if tool == "manage_calendar" {
        desc = "manage_calendar".to_string();
        result = do_manage_calendar(content, owner).await;
    } else if tool == "download_model" {
        desc = "download_model".to_string();
        result = do_download_model(content, owner).await;
    } else if tool == "serve_model" {
        desc = "serve_model".to_string();
        result = do_serve_model(content, owner).await;
    } else if tool == "list_served_models" {
        desc = "list_served_models".to_string();
        result = do_list_served_models(content, owner).await;
    } else if tool == "stop_served_model" {
        desc = "stop_served_model".to_string();
        result = do_stop_served_model(content, owner).await;
    } else if tool == "list_downloads" {
        desc = "list_downloads".to_string();
        result = do_list_downloads(content, owner).await;
    } else if tool == "cancel_download" {
        desc = "cancel_download".to_string();
        result = do_cancel_download(content, owner).await;
    } else if tool == "search_hf_models" {
        desc = "search_hf_models".to_string();
        result = do_search_hf_models(content, owner).await;
    } else if tool == "list_cached_models" {
        desc = "list_cached_models".to_string();
        result = do_list_cached_models(content, owner).await;
    } else if tool == "app_api" {
        desc = "app_api".to_string();
        result = do_app_api(content, owner).await;
    } else if tool == "list_serve_presets" {
        desc = "list_serve_presets".to_string();
        result = do_list_serve_presets(content, owner).await;
    } else if tool == "serve_preset" {
        desc = "serve_preset".to_string();
        result = do_serve_preset(content, owner).await;
    } else if tool == "adopt_served_model" {
        desc = "adopt_served_model".to_string();
        result = do_adopt_served_model(content, owner).await;
    } else if tool == "list_cookbook_servers" {
        desc = "list_cookbook_servers".to_string();
        result = do_list_cookbook_servers(content, owner).await;
    } else if tool == "edit_image" {
        desc = "edit_image".to_string();
        result = do_edit_image(content, owner).await;
    } else if tool == "trigger_research" {
        desc = "trigger_research".to_string();
        result = do_trigger_research(content, owner).await;
    } else if tool == "manage_research" {
        desc = "manage_research".to_string();
        result = do_manage_research(content, owner).await;
    } else if tool == "resolve_contact" {
        desc = "resolve_contact".to_string();
        result = do_resolve_contact(content, owner).await;
    } else if tool == "manage_contact" {
        desc = "manage_contact".to_string();
        result = do_manage_contact(content, owner).await;
    } else if tool == "vault_search" {
        desc = "vault_search".to_string();
        result = do_vault_search(content, owner).await;
    } else if tool == "vault_get" {
        desc = "vault_get".to_string();
        result = do_vault_get(content, owner).await;
    } else if tool == "vault_unlock" {
        desc = "vault_unlock".to_string();
        result = do_vault_unlock(content, owner).await;
    } else if tool.starts_with("mcp__") {
        // MCP tool dispatch (tool_execution.py:713-725). Call the live manager
        // when present; otherwise the honest "MCP manager not available" error.
        if let Some(mcp) = get_mcp_manager() {
            // try: args = json.loads(content) if content.strip().startswith("{")
            //      else {}  except (JSONDecodeError, TypeError): args = {}
            let args: Value = if content.trim().starts_with('{') {
                serde_json::from_str(content).unwrap_or(Value::Object(Map::new()))
            } else {
                Value::Object(Map::new())
            };
            desc = format!("mcp: {tool}");
            // result = await mcp.call_tool(tool, args)  -- always a Value::Object.
            result = match mcp.call_tool(tool, args).await {
                Value::Object(m) => m,
                _ => Map::new(),
            };
        } else {
            desc = format!("mcp: {tool}");
            result = err_map("MCP manager not available", 1);
        }
    } else {
        desc = format!("unknown: {tool}");
        // Python: {"error": f"Unknown tool type: {tool}"}  (NO exit_code key)
        let mut m = Map::new();
        m.insert(
            "error".to_string(),
            Value::String(format!("Unknown tool type: {tool}")),
        );
        result = m;
    }

    // logger.info(f"Tool executed: {desc} -> exit_code={result.get('exit_code', 'n/a')}")
    let ec = result
        .get("exit_code")
        .map(|v| v.to_string())
        .unwrap_or_else(|| "n/a".to_string());
    logger::info(&format!("Tool executed: {desc} -> exit_code={ec}"));
    (desc, result)
}

// ---------------------------------------------------------------------------
// Result formatting
// ---------------------------------------------------------------------------

/// Keys handled by the dedicated branches in `format_tool_result` — never echo
/// them as raw JSON in the `data` block.
pub static _FORMATTER_HANDLED_KEYS: once_cell::sync::Lazy<HashSet<&'static str>> =
    once_cell::sync::Lazy::new(|| {
        [
            "stdout",
            "stderr",
            "exit_code",
            "content",
            "size",
            "response",
            "results",
            "session_id",
            "name",
            "model",
            "session_name",
            "success",
            "path",
            "action",
            "title",
            "doc_id",
            "version",
            "applied",
            "error",
            "output",
        ]
        .into_iter()
        .collect()
    });

/// Format a tool result into text for feeding back to the LLM.
pub fn format_tool_result(description: &str, result: &Map<String, Value>) -> String {
    let mut parts: Vec<String> = vec![format!("### {description}")];

    // Render a JSON value the way Python's f-string `{value}` would: strings
    // bare, numbers/bools/null in Python form.
    fn render(v: &Value) -> String {
        match v {
            Value::String(s) => s.clone(),
            Value::Null => "None".to_string(),
            Value::Bool(b) => {
                if *b {
                    "True".to_string()
                } else {
                    "False".to_string()
                }
            }
            other => other.to_string(),
        }
    }
    // `result.get(key)` truthiness (Python: non-empty str / non-zero num / true).
    fn truthy(v: Option<&Value>) -> bool {
        match v {
            None | Some(Value::Null) => false,
            Some(Value::Bool(b)) => *b,
            Some(Value::String(s)) => !s.is_empty(),
            Some(Value::Number(n)) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
            Some(Value::Array(a)) => !a.is_empty(),
            Some(Value::Object(o)) => !o.is_empty(),
        }
    }

    if result.contains_key("stdout") {
        if truthy(result.get("stdout")) {
            parts.push(format!(
                "**stdout:**\n```\n{}\n```",
                render(result.get("stdout").unwrap())
            ));
        }
        if truthy(result.get("stderr")) {
            parts.push(format!(
                "**stderr:**\n```\n{}\n```",
                render(result.get("stderr").unwrap())
            ));
        }
        // result.get('exit_code', 'unknown')
        let ec = result.get("exit_code").map(render).unwrap_or_else(|| "unknown".to_string());
        parts.push(format!("**exit_code:** {ec}"));
    } else if result.contains_key("output") {
        // bash/python canonical shape: {"output": ..., "exit_code": ...}
        parts.push(format!("```\n{}\n```", render(result.get("output").unwrap())));
        // if result.get("exit_code") not in (0, None):
        let ec = result.get("exit_code");
        let is_zero_or_none = match ec {
            None | Some(Value::Null) => true,
            Some(Value::Number(n)) => n.as_i64() == Some(0) || n.as_f64() == Some(0.0),
            _ => false,
        };
        if !is_zero_or_none {
            parts.push(format!("**exit_code:** {}", render(ec.unwrap())));
        }
    } else if result.contains_key("content") {
        let size = result.get("size").map(render).unwrap_or_else(|| "?".to_string());
        parts.push(format!(
            "**content ({size} chars):**\n```\n{}\n```",
            render(result.get("content").unwrap())
        ));
    } else if result.contains_key("response") {
        // model = result.get("model", result.get("session_name", ""))
        let model = result
            .get("model")
            .or_else(|| result.get("session_name"))
            .map(render)
            .unwrap_or_default();
        if !model.is_empty() {
            parts.push(format!(
                "**{model} responded:**\n{}",
                render(result.get("response").unwrap())
            ));
        } else {
            parts.push(render(result.get("response").unwrap()));
        }
    } else if result.contains_key("results") {
        parts.push(render(result.get("results").unwrap()));
    } else if result.contains_key("session_id") && result.contains_key("name") {
        let model = result.get("model").map(render).unwrap_or_else(|| "unknown".to_string());
        parts.push(format!(
            "Session created: **{}** (id: `{}`, model: {model})",
            render(result.get("name").unwrap()),
            render(result.get("session_id").unwrap())
        ));
    } else if result.contains_key("success") {
        if truthy(result.get("success")) {
            parts.push(format!(
                "File written: {} ({} bytes)",
                render(result.get("path").unwrap_or(&Value::Null)),
                render(result.get("size").unwrap_or(&Value::Null))
            ));
        } else {
            let err = result.get("error").map(render).unwrap_or_else(|| "unknown".to_string());
            parts.push(format!("Error: {err}"));
        }
    } else if result.contains_key("action") {
        let action = render(result.get("action").unwrap());
        if action == "create" {
            parts.push(format!(
                "Document created: \"{}\" (id: {}, v{})",
                result.get("title").map(render).unwrap_or_default(),
                render(result.get("doc_id").unwrap_or(&Value::Null)),
                render(result.get("version").unwrap_or(&Value::Null))
            ));
        } else if action == "update" {
            parts.push(format!(
                "Document updated: \"{}\" (v{})",
                result.get("title").map(render).unwrap_or_default(),
                render(result.get("version").unwrap_or(&Value::Null))
            ));
        } else if action == "edit" {
            parts.push(format!(
                "Document edited: \"{}\" (v{}, {} edit(s) applied)",
                result.get("title").map(render).unwrap_or_default(),
                result.get("version").map(render).unwrap_or_else(|| "?".to_string()),
                result.get("applied").map(render).unwrap_or_else(|| "0".to_string())
            ));
        }
    } else if result.contains_key("error") {
        parts.push(format!("**Error:** {}", render(result.get("error").unwrap())));
    }

    // Surface any additional structured payload the dedicated branches don't
    // show: {k: v for k, v in result.items() if k not in _FORMATTER_HANDLED_KEYS}.
    // serde_json's preserve_order keeps insertion order = Python dict order.
    let mut extra = Map::new();
    for (k, v) in result.iter() {
        if !_FORMATTER_HANDLED_KEYS.contains(k.as_str()) {
            extra.insert(k.clone(), v.clone());
        }
    }
    if !extra.is_empty() {
        // json.dumps(extra, indent=2, default=str, ensure_ascii=False)
        // to_string_pretty = 2-space indent, no ASCII escaping; default=str is
        // moot for a Map<String,Value> (everything is already serializable).
        if let Ok(mut extra_json) = serde_json::to_string_pretty(&Value::Object(extra)) {
            // Cap to avoid blowing the context window (CHAR slice).
            let char_count = extra_json.chars().count();
            if char_count > 8000 {
                let head: String = extra_json.chars().take(8000).collect();
                extra_json = format!("{head}\n... (truncated, {char_count} chars total)");
            }
            parts.push(format!("**data:**\n```json\n{extra_json}\n```"));
        }
    }

    parts.join("\n")
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    // --- path confinement (read_file / write_file security) ----------------

    #[test]
    fn admin_tools_includes_app_api_and_serve_preset() {
        // Privilege-escalation fix: these must be admin-gated.
        assert!(_ADMIN_TOOLS.contains("app_api"));
        assert!(_ADMIN_TOOLS.contains("serve_preset"));
    }

    #[test]
    fn mcp_tool_map_includes_web_fetch() {
        assert_eq!(_MCP_TOOL_MAP.get("web_fetch"), Some(&("web_fetch", "web_fetch")));
    }

    #[test]
    fn build_mcp_args_web_fetch_extracts_first_line_url() {
        let args = _build_mcp_args("web_fetch", "  https://example.com  \nignored second line");
        assert_eq!(
            args.get("url").and_then(|v| v.as_str()),
            Some("https://example.com")
        );
    }

    #[test]
    fn is_sensitive_path_detects_ssh_dir_and_known_files() {
        // .ssh anywhere in the path -> sensitive.
        assert!(_is_sensitive_path("/home/u/.ssh/config"));
        // sensitive basename file.
        assert!(_is_sensitive_path("/srv/data/id_rsa"));
        assert!(_is_sensitive_path("/anywhere/authorized_keys"));
        // .env directory/file component.
        assert!(_is_sensitive_path("/proj/.env"));
        // a known_hosts elsewhere as a basename.
        assert!(_is_sensitive_path("/x/known_hosts"));
        // benign path.
        assert!(!_is_sensitive_path("/tmp/work/output.txt"));
        // pattern must match the FULL basename, not a substring (Python uses
        // `pat in {basename}` which is exact-match).
        assert!(!_is_sensitive_path("/tmp/id_rsa_backup.txt"));
    }

    #[test]
    fn resolve_tool_path_rejects_empty() {
        let err = _resolve_tool_path("   ").unwrap_err();
        assert_eq!(err, "path is required");
    }

    #[test]
    fn resolve_tool_path_blocks_sensitive_even_under_allowed_root() {
        // /tmp is an allowlist root, but a .ssh subpath under it is still denied
        // (sensitive check runs FIRST).
        let err = _resolve_tool_path("/tmp/.ssh/id_rsa").unwrap_err();
        assert!(
            err.contains("sensitive directory"),
            "expected sensitive-dir rejection, got: {err}"
        );
    }

    #[test]
    fn resolve_tool_path_rejects_outside_roots() {
        // /etc/hosts is neither sensitive (no sensitive component) nor under an
        // allowed root, so it falls through to the containment rejection.
        let err = _resolve_tool_path("/etc/hosts").unwrap_err();
        assert!(
            err.contains("outside the allowed roots"),
            "expected containment rejection, got: {err}"
        );
    }

    #[test]
    fn resolve_tool_path_allows_under_tmp_root() {
        // A benign file under /tmp (an allowlist root) is permitted; the returned
        // path is the realpath (e.g. /private/tmp/... on macOS).
        let ok = _resolve_tool_path("/tmp/odysseus_tool_path_test_file.txt")
            .expect("path under /tmp should be allowed");
        let realtmp = crate::src::app_helpers::realpath("/tmp");
        assert!(
            ok.starts_with(&realtmp) || ok.starts_with("/tmp"),
            "resolved path {ok} not under a tmp root ({realtmp})"
        );
    }

    #[test]
    fn tool_path_roots_contains_data_dir_and_tmp() {
        let roots = _tool_path_roots();
        let data = crate::src::app_helpers::realpath(&crate::src::constants::DATA_DIR);
        assert!(roots.contains(&data), "data dir missing from roots: {roots:?}");
        let realtmp = crate::src::app_helpers::realpath("/tmp");
        assert!(
            roots.iter().any(|r| *r == realtmp || r == "/tmp"),
            "tmp missing from roots: {roots:?}"
        );
    }

    // --- read_file / write_file routing through the resolver ----------------

    #[tokio::test]
    async fn read_file_rejects_sensitive_path() {
        let r = _direct_fallback("read_file", "/tmp/.ssh/id_rsa", None)
            .await
            .expect("read_file is a handled tool");
        assert_eq!(r.get("exit_code").and_then(|v| v.as_i64()), Some(1));
        let e = r.get("error").and_then(|v| v.as_str()).unwrap_or("");
        assert!(e.starts_with("read_file: "), "got: {e}");
        assert!(e.contains("sensitive directory"), "got: {e}");
    }

    #[tokio::test]
    async fn write_file_then_read_file_roundtrip_under_tmp() {
        // Use a unique temp file directly under /tmp (an allowlist root). NEVER
        // touches the repo data/ directory.
        let name = format!(
            "odysseus_tool_exec_test_{}_{}.txt",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        );
        let path = format!("/tmp/{name}");
        let body = "hello from the tool path test";

        let w = _direct_fallback("write_file", &format!("{path}\n{body}"), None)
            .await
            .expect("write_file is a handled tool");
        assert_eq!(
            w.get("exit_code").and_then(|v| v.as_i64()),
            Some(0),
            "write failed: {w:?}"
        );

        let r = _direct_fallback("read_file", &path, None)
            .await
            .expect("read_file is a handled tool");
        assert_eq!(r.get("exit_code").and_then(|v| v.as_i64()), Some(0), "read failed: {r:?}");
        assert_eq!(r.get("output").and_then(|v| v.as_str()), Some(body));

        // Cleanup.
        let _ = std::fs::remove_file(&path);
    }

    // --- web_fetch handler --------------------------------------------------

    #[tokio::test]
    async fn web_fetch_rejects_non_url_token() {
        // Multiple words on the first line -> not a single URL token.
        let r = _direct_fallback("web_fetch", "tell me about cats please", None)
            .await
            .expect("web_fetch is a handled tool");
        assert_eq!(r.get("exit_code").and_then(|v| v.as_i64()), Some(1));
        let e = r.get("error").and_then(|v| v.as_str()).unwrap_or("");
        assert!(e.contains("provide a single URL or domain"), "got: {e}");
    }

    #[tokio::test]
    async fn web_fetch_rejects_unsupported_scheme() {
        let r = _direct_fallback("web_fetch", "ftp://example.com/file", None)
            .await
            .expect("web_fetch is a handled tool");
        assert_eq!(r.get("exit_code").and_then(|v| v.as_i64()), Some(1));
        let e = r.get("error").and_then(|v| v.as_str()).unwrap_or("");
        assert!(e.contains("unsupported URL scheme"), "got: {e}");
    }

    #[tokio::test]
    async fn web_fetch_rejects_empty_json_url() {
        // JSON arg without a usable url, single line -> rejected as non-URL token
        // (the `{`-prefixed string fails the bare-token check).
        let r = _direct_fallback("web_fetch", "{\"foo\": \"bar\"}", None)
            .await
            .expect("web_fetch is a handled tool");
        assert_eq!(r.get("exit_code").and_then(|v| v.as_i64()), Some(1));
        let e = r.get("error").and_then(|v| v.as_str()).unwrap_or("");
        assert!(e.contains("provide a single URL or domain"), "got: {e}");
    }

    #[test]
    fn expanduser_expands_tilde() {
        std::env::set_var("HOME", "/home/tester");
        assert_eq!(expanduser("~"), "/home/tester");
        assert_eq!(expanduser("~/x/y"), "/home/tester/x/y");
        assert_eq!(expanduser("/abs/path"), "/abs/path");
    }
}
