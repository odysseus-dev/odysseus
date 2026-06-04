// routes/shell_routes.rs  <- routes/shell_routes.py
//! Shell routes — user-facing command execution endpoint.
//!
//! Wave-7 port (app.py include #27, `app.include_router(setup_shell_routes())`
//! at app.py line 531). The two shell endpoints (`/api/shell/exec`,
//! `/api/shell/stream`) plus the package endpoints that physically live in this
//! Python module but are namespaced under `/api/cookbook/...`
//! (`GET .../packages`, `POST .../packages/install`, `POST .../rebuild-engine`).
//!
//! ## Security hardening (parity with the upstream `routes/shell_routes.py`)
//!   * `GET /api/cookbook/packages` is admin-gated AND carries a `sec-fetch-site`
//!     cross-site guard ([`reject_cross_site`]) — a browser cross-site navigation
//!     to this shell-touching endpoint is 403'd.
//!   * The remote SSH probes (and `POST /api/cookbook/rebuild-engine`) build a
//!     VALIDATED argv vector ([`ssh_base_argv`]/[`venv_activate_prefix`]) and run
//!     it WITHOUT a local `sh -c` parse, closing the prior command-injection hole
//!     where `host`/`venv` were interpolated into a shell string.
//!
//! ## Admin gate — `_require_admin` is module-LOCAL, NOT `core/middleware.require_admin`
//! `routes/shell_routes.py` defines its OWN `_require_admin(request)` whose
//! semantics DIFFER from `core/middleware.require_admin` (which `cookbook_routes`
//! uses). The distinctions, ported verbatim below:
//!   * `auth_manager is None -> return` (allow). In the axum app `state.auth` is
//!     ALWAYS wired (`Arc<AuthManager>`), exactly as `request.app.state.auth_manager`
//!     is always set, so this branch is dead in practice but is kept for fidelity.
//!   * `user == "internal-tool" -> return` (allow). The Python honours the marker
//!     the AuthMiddleware stamps after validating the internal token + loopback
//!     client. It does NOT separately re-check the `X-Odysseus-Internal-Token`
//!     header (unlike `core/middleware.require_admin`).
//!   * `not user or user == "api" -> raise HTTPException(403, "Admin only")`.
//!     The explicit `user == "api"` rejection is unique to this gate.
//!   * `not auth_manager.is_admin(user) -> raise HTTPException(403, "Admin only")`.
//!   * It does NOT consult `AUTH_ENABLED` nor `is_configured` — so on this gate an
//!     UNCONFIGURED instance with a stamped non-admin user still 403s, matching the
//!     Python exactly.
//!
//! ## TMUX_LOG_DIR — reused from cookbook_routes (its first definer)
//! `routes/shell_routes.py` is where `TMUX_LOG_DIR = Path(tempfile.gettempdir()) /
//! "odysseus-tmux"` is DEFINED in Python; `routes/cookbook_routes.py` imports it
//! from here. In the Rust port the wave-6 `cookbook_routes` module already DEFINED
//! `tmux_log_dir()` (`std::env::temp_dir().join("odysseus-tmux")`) as its first
//! definer. To stay parallel-safe (no cross-file edits) this module DEFINES its own
//! [`tmux_log_dir`] pointing at the byte-identical location. The `/api/cookbook/packages`
//! GET handler uses it for the scan-script scratch dir, same as Python.
//!
//! ## Faithfulness / DEFERRED paths
//! * `/api/shell/exec` (`_exec_shell`) — PORT_NOW. `asyncio.create_subprocess_shell`
//!   -> `tokio::process::Command::new("sh").arg("-c")`, `cwd=Path.home()`.
//! * `/api/shell/stream` plain (non-pty, non-tmux) `generate()` path — PORT_NOW via
//!   `async_stream` + a `_find_line_break`-driven chunk reader (split on `\r` OR
//!   `\n`, NOT `readline`), mirroring the Python byte-for-byte. `kill_on_drop(true)`
//!   gives the `request.is_disconnected() -> proc.kill()` semantics: a dropped SSE
//!   body (client gone) drops the child, which tokio then kills. There is no axum
//!   `is_disconnected()` poll, so the in-loop disconnect check collapses to that
//!   drop behaviour (documented drift, consistent with the rest of the wave).
//! * `/api/shell/stream` `use_pty=True` path (`_generate_pty`) — PORT_NOW (Unix).
//!   `pty.openpty` is Python stdlib (ALWAYS present — there is no faithful no-PTY
//!   branch to emulate), so this is ported for real with `nix` (already a dep):
//!   `nix::pty::openpty(None, None)` (the `term` feature) gives `{master, slave}`
//!   `OwnedFd`s; the slave is cloned into the child's stdin/stdout/stderr and a
//!   `pre_exec` runs `nix::unistd::setsid()` then `ioctl(0, TIOCSCTTY, 0)` (mirroring
//!   `os.setsid` + the controlling-tty grab); a blocking reader thread feeds master
//!   bytes through an mpsc channel into the same `_find_line_break` line splitter the
//!   plain path uses. `#[cfg(unix)]`-gated like `bg_jobs::spawn_detached`; on non-Unix
//!   targets the path stays an honest stub (this crate ships Unix-first).
//! * `/api/shell/stream` `use_tmux=True` path (`_generate_tmux`) — the tmux
//!   orchestration is the SAME pattern wave-6 `cookbook_routes` already ports; the
//!   plain wrapper-script + log-tail streaming IS portable, so it is PORT_NOW here.
//! * `GET /api/cookbook/packages` — PORT_NOW for the PORTABLE checks (`shutil.which`
//!   system binaries, the `llama-server` which-probe, the remote SSH `python3`
//!   probe). The LOCAL `importlib.import_module(pkg)` in-process check has no Rust
//!   analogue (the binary is not a Python interpreter); those local packages read as
//!   `installed: False`, exactly as Python's `except ImportError` arm sets them when
//!   the module is not importable.
//! * `POST /api/cookbook/packages/install` — PORT_NOW for the admin gate +
//!   validation + the no-package / unknown-package error responses (all reachable).
//!   The actual `sys.executable -m pip install` install step has no Rust analogue
//!   (`sys.executable` = the running Python interpreter; the standalone Rust binary
//!   has no such environment to install into) and is DEFERRED — surfaced as the
//!   honest spawn-failure error shape Python returns when the install cannot run.
//!
//! ## SSE byte format
//! Python streams `f"data: {json.dumps({...})}\n\n"`. Like the rest of the wave
//! (research_routes/chat_routes) the port emits the payload via `serde_json`
//! compact form (`,`/`:` separators) rather than `json.dumps`'s `", "`/`": "`
//! spacing — a known whitespace-only drift the JS `JSON.parse` client tolerates;
//! `preserve_order` keeps the key order identical.


use std::collections::HashMap;
use std::path::PathBuf;
use std::process::Stdio;

use axum::body::Body;
use axum::extract::{Query, State};
use axum::http::{header, HeaderMap};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Extension, Json, Router};
use serde::Deserialize;
use serde_json::{json, Value};
use tokio::io::AsyncReadExt;
use tokio::process::Command;

use crate::routes::{AppState, CurrentUser, HttpException};

// ===========================================================================
// Module constants (mirrors of the Python module-level constants)
// ===========================================================================

/// `EXEC_TIMEOUT = 30` — shorter than agent's 60s.
const EXEC_TIMEOUT: u64 = 30;
/// `STREAM_TIMEOUT = 120` — default for short streamed commands.
const STREAM_TIMEOUT: i64 = 120;
/// `MAX_OUTPUT = 200_000` — truncate limit (code points).
const MAX_OUTPUT: usize = 200_000;

/// `known = {...}` from `install_package` — the pip-install allow-list that
/// prevents arbitrary `pip install`. Insertion order is irrelevant (membership
/// only); the contents mirror the Python `known` set exactly (including the
/// `diffusers[torch]` and `vllm` additions).
const INSTALL_ALLOW_LIST: &[&str] = &[
    "rembg[gpu]",
    "hf_transfer",
    "llama-cpp-python[server]",
    "sglang[all]",
    "diffusers",
    "diffusers[torch]",
    "TTS",
    "bark",
    "faster-whisper",
    "playwright",
    "realesrgan",
    "gfpgan",
    "insightface",
    "onnxruntime-gpu",
    "onnxruntime",
    "hdbscan",
    "vllm",
];

/// `TMUX_LOG_DIR = Path(tempfile.gettempdir()) / "odysseus-tmux"`.
///
/// In Python this is shell_routes' constant (cookbook_routes imports it). The
/// Rust wave-6 `cookbook_routes` is the first definer; this points at the
/// byte-identical location. `std::env::temp_dir()` == `tempfile.gettempdir()`
/// (honors `$TMPDIR`).
fn tmux_log_dir() -> PathBuf {
    std::env::temp_dir().join("odysseus-tmux")
}

// ===========================================================================
// `ShellExecRequest` (pydantic BaseModel)
// ===========================================================================

/// ```python
/// class ShellExecRequest(BaseModel):
///     command: str
///     timeout: int | None = None
///     use_pty: bool = False
///     use_tmux: bool = False
/// ```
#[derive(Debug, Deserialize)]
struct ShellExecRequest {
    command: String,
    /// `timeout: int | None = None` — optional override; `0` = no timeout.
    #[serde(default)]
    timeout: Option<i64>,
    /// `use_pty: bool = False`.
    #[serde(default)]
    use_pty: bool,
    /// `use_tmux: bool = False`.
    #[serde(default)]
    use_tmux: bool,
}

// ===========================================================================
// `_require_admin` — module-LOCAL gate (see module docs for the divergence)
// ===========================================================================

/// `_require_admin(request)` (shell_routes.py) — `Ok(())` to allow, else
/// `HttpException::new(403, "Admin only")`. Distinct from
/// `core/middleware.require_admin` — see the module-level docs. Delegates to
/// [`require_admin_with_auth`] (the AppState-free core, for unit tests).
fn require_admin(state: &AppState, user: Option<&str>) -> Result<(), HttpException> {
    // `auth_manager = getattr(request.app.state, "auth_manager", None)`
    // `if not auth_manager: return` — always wired in axum; dead branch kept for
    // fidelity (no `Option<AuthManager>` exists here, so nothing to early-return on).
    require_admin_with_auth(&state.auth, user)
}

/// The AppState-free core of [`require_admin`], taking the resolved
/// `auth_manager`. Mirrors `_require_admin` step for step.
fn require_admin_with_auth(
    auth: &crate::core::auth::AuthManager,
    user: Option<&str>,
) -> Result<(), HttpException> {
    // `if user == "internal-tool": return`
    if user == Some("internal-tool") {
        return Ok(());
    }
    // `if not user or user == "api": raise HTTPException(403, "Admin only")`
    match user {
        None => return Err(HttpException::new(403, "Admin only")),
        Some("") | Some("api") => return Err(HttpException::new(403, "Admin only")),
        Some(_) => {}
    }
    // `if not auth_manager.is_admin(user): raise HTTPException(403, "Admin only")`
    if !auth.is_admin(user.unwrap()) {
        return Err(HttpException::new(403, "Admin only"));
    }
    Ok(())
}

/// Resolve `request.state.current_user` from the optional `Extension<CurrentUser>`.
fn current_user_opt(user: Option<&Extension<CurrentUser>>) -> Option<&str> {
    user.map(|Extension(u)| u.0.as_str())
}

/// `_reject_cross_site(request)` (shell_routes.py) — reject browser cross-site
/// navigations to shell-touching endpoints. `Ok(())` to allow, else
/// `HTTPException(403, "Cross-site request rejected")` when the `sec-fetch-site`
/// request header is exactly `"cross-site"`.
fn reject_cross_site(headers: &HeaderMap) -> Result<(), HttpException> {
    // `if request.headers.get("sec-fetch-site") == "cross-site":`
    if headers
        .get("sec-fetch-site")
        .and_then(|v| v.to_str().ok())
        == Some("cross-site")
    {
        return Err(HttpException::new(403, "Cross-site request rejected"));
    }
    Ok(())
}

/// `_SSH_PORT_RE = re.compile(r"^\d{1,5}$")` — match used to validate an ssh port.
/// True iff `s` is 1-5 ASCII digits (no sign, no leading/trailing junk).
fn ssh_port_re_match(s: &str) -> bool {
    let len = s.len();
    (1..=5).contains(&len) && s.bytes().all(|b| b.is_ascii_digit())
}

/// `_SAFE_VENV_RE = re.compile(r"^[A-Za-z0-9_./~-]+$")` — full-string match.
/// True iff `s` is non-empty and every char is in `[A-Za-z0-9_./~-]`.
fn safe_venv_re_match(s: &str) -> bool {
    !s.is_empty()
        && s.chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | '/' | '~' | '-'))
}

/// `_ssh_base_argv(host, ssh_port)` — build an ssh argv PREFIX for remote probes
/// without local-shell parsing (no `sh -c`). Raises `ValueError` -> the caller
/// turns it into `HTTPException(400, str(e))`.
fn ssh_base_argv(host: &str, ssh_port: Option<&str>) -> Result<Vec<String>, String> {
    // `if not host or not str(host).strip() or str(host).lstrip().startswith("-"):`
    if host.is_empty() || host.trim().is_empty() || host.trim_start().starts_with('-') {
        return Err("invalid ssh host".to_string());
    }
    // `argv = ["ssh", "-o", "ConnectTimeout=6", "-o", "StrictHostKeyChecking=no"]`
    let mut argv = vec![
        "ssh".to_string(),
        "-o".to_string(),
        "ConnectTimeout=6".to_string(),
        "-o".to_string(),
        "StrictHostKeyChecking=no".to_string(),
    ];
    // `if ssh_port and str(ssh_port).strip() not in ("", "22"):`
    if let Some(p) = ssh_port {
        let port = p.trim();
        if !port.is_empty() && port != "22" {
            // `if not _SSH_PORT_RE.match(port) or not (1 <= int(port) <= 65535):`
            let ok = ssh_port_re_match(port)
                && port
                    .parse::<u32>()
                    .map(|n| (1..=65535).contains(&n))
                    .unwrap_or(false);
            if !ok {
                return Err("invalid ssh port".to_string());
            }
            // `argv += ["-p", port]`
            argv.push("-p".to_string());
            argv.push(port.to_string());
        }
    }
    // `argv.append(str(host).strip())`
    argv.push(host.trim().to_string());
    Ok(argv)
}

/// `_venv_activate_prefix(venv)` — return a remote activation prefix while
/// preserving shell expansion of `~`. Raises `ValueError` on an unsafe path ->
/// the caller turns it into `HTTPException(400, str(e))`.
fn venv_activate_prefix(venv: Option<&str>) -> Result<String, String> {
    // `if not venv: return ""`
    let venv = match venv {
        Some(v) if !v.is_empty() => v,
        _ => return Ok(String::new()),
    };
    // `if not _SAFE_VENV_RE.match(venv): raise ValueError("invalid venv path")`
    if !safe_venv_re_match(venv) {
        return Err("invalid venv path".to_string());
    }
    // `act = venv if venv.endswith("/bin/activate") else venv.rstrip("/") + "/bin/activate"`
    let act = if venv.ends_with("/bin/activate") {
        venv.to_string()
    } else {
        format!("{}/bin/activate", venv.trim_end_matches('/'))
    };
    // `return f". {act} && "`
    Ok(format!(". {act} && "))
}

/// `_llama_cpp_rebuild_cmd()` (routes/cookbook_helpers.py) — shell command that
/// clears the Cookbook-managed llama.cpp build (removes the cached
/// `~/bin/llama-server` symlink and `~/llama.cpp/build` directory) so the next
/// llama.cpp serve recompiles from source. Installs/downloads nothing.
///
/// Ported byte-for-byte here because the Rust `cookbook_helpers` port does not
/// yet expose it and this module may not edit a sibling file (parallel-safe).
fn llama_cpp_rebuild_cmd() -> String {
    concat!(
        "mkdir -p \"$HOME/bin\" && ",
        "rm -f \"$HOME/bin/llama-server\" && ",
        "rm -rf \"$HOME/llama.cpp/build\" && ",
        "echo \"[odysseus] Cleared the cached llama.cpp build. ",
        "Re-launch the serve task to rebuild llama-server from source ",
        "(CUDA or HIP will be used if a toolchain is now available).\""
    )
    .to_string()
}

// ===========================================================================
// `_find_line_break` — next `\r`/`\n` terminator
// ===========================================================================

/// `_find_line_break(buf)` — `(index, separator_length)` of the next line
/// terminator in `buf`, or `(-1, 0)`. Treats a `\r\n` pair as a single
/// 2-byte separator. Returns indices into the raw byte slice (matching Python's
/// `bytes.find`).
fn find_line_break(buf: &[u8]) -> (isize, usize) {
    // `ni = buf.find(b"\n"); ri = buf.find(b"\r")`
    let ni = buf.iter().position(|&b| b == b'\n');
    let ri = buf.iter().position(|&b| b == b'\r');
    match (ni, ri) {
        // `if ni == -1 and ri == -1: return -1, 0`
        (None, None) => (-1, 0),
        // `if ni == -1: return ri, 1`
        (None, Some(ri)) => (ri as isize, 1),
        // `if ri == -1: return ni, 1`
        (Some(ni), None) => (ni as isize, 1),
        (Some(ni), Some(ri)) => {
            // `if ri < ni: return ri, (2 if ri + 1 == ni else 1)`
            if ri < ni {
                (ri as isize, if ri + 1 == ni { 2 } else { 1 })
            } else {
                // `return ni, 1`
                (ni as isize, 1)
            }
        }
    }
}

/// `bytes.decode(errors="replace")` then `[:MAX_OUTPUT]` (code-point slice).
fn decode_replace_trunc(bytes: &[u8]) -> String {
    truncate_chars(&String::from_utf8_lossy(bytes), MAX_OUTPUT)
}

/// `str(Path.home())` — POSIX resolves `$HOME`; fall back to `.` when unset.
fn home_dir() -> String {
    match std::env::var("HOME") {
        Ok(h) if !h.is_empty() => h,
        _ => ".".to_string(),
    }
}

/// `proc.returncode` — exit code, or negative signal number for signal death,
/// or `-1` on a `wait()` error (matching CPython's `asyncio` subprocess).
fn signal_aware_code(status: &std::io::Result<std::process::ExitStatus>) -> i32 {
    match status {
        Ok(s) => {
            #[cfg(unix)]
            {
                use std::os::unix::process::ExitStatusExt;
                s.code().unwrap_or_else(|| -s.signal().unwrap_or(1))
            }
            #[cfg(not(unix))]
            {
                s.code().unwrap_or(-1)
            }
        }
        Err(_) => -1,
    }
}

/// `base + secs` where `secs` is a SIGNED seconds count, matching Python's
/// `deadline = loop.time() + timeout` (Python's `+` accepts a negative float).
///
/// A NEGATIVE `secs` must move the instant BACKWARD so the deadline is already
/// in the past and fires immediately (Python: `deadline = loop.time()+timeout`
/// with `timeout<0` yields a past deadline -> `loop.time() > deadline` -> fire).
/// A plain `base + Duration::from_secs(secs as u64)` is WRONG: `secs as u64` on a
/// negative `i64` wraps to a huge value, pushing the deadline ~584 billion years
/// out so it never fires. Saturate at `base` to avoid an underflow panic when the
/// magnitude exceeds the monotonic clock's origin.
fn instant_plus_signed_secs(base: tokio::time::Instant, secs: i64) -> tokio::time::Instant {
    if secs >= 0 {
        base + std::time::Duration::from_secs(secs as u64)
    } else {
        base.checked_sub(std::time::Duration::from_secs(secs.unsigned_abs()))
            .unwrap_or(base)
    }
}

/// Truncate to at most `max` Unicode code points (`s[:max]`).
fn truncate_chars(s: &str, max: usize) -> String {
    match s.char_indices().nth(max) {
        Some((idx, _)) => s[..idx].to_string(),
        None => s.to_string(),
    }
}

/// Last `max` Unicode code points (`s[-max:]`). `max == 0` -> empty string
/// (matching CPython's `s[-0:]`, which is the whole string, is NOT wanted here —
/// every call site passes a positive bound, so `0` returning "" is fine and
/// avoids the `s[-0:]` footgun).
fn tail_chars(s: &str, max: usize) -> String {
    let total = s.chars().count();
    if total <= max {
        return s.to_string();
    }
    let skip = total - max;
    s.chars().skip(skip).collect()
}

/// `str` -> `Ok(Bytes)` for the SSE body stream (infallible).
fn sse_bytes(s: String) -> Result<bytes::Bytes, std::convert::Infallible> {
    Ok(bytes::Bytes::from(s))
}

/// `f"data: {json.dumps(v)}\n\n"` — one SSE event (compact JSON; see module docs).
fn sse_event(v: &Value) -> String {
    format!("data: {v}\n\n")
}

// ===========================================================================
// `_exec_shell` — collect stdout/stderr/exit_code with a timeout
// ===========================================================================

/// `async def _exec_shell(command, timeout=EXEC_TIMEOUT) -> dict` — returns a
/// `{"stdout", "stderr", "exit_code"}` object (insertion order preserved).
async fn exec_shell(command: &str, timeout: u64) -> Value {
    // `proc = await asyncio.create_subprocess_shell(command, …, cwd=Path.home())`
    let mut child = match Command::new("sh")
        .arg("-c")
        .arg(command)
        .current_dir(home_dir())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
    {
        Ok(c) => c,
        // `except Exception as e: return {"stdout": "", "stderr": str(e), "exit_code": -1}`
        Err(e) => {
            return json!({"stdout": "", "stderr": e.to_string(), "exit_code": -1});
        }
    };

    // `stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)`
    let mut stdout_pipe = child.stdout.take();
    let mut stderr_pipe = child.stderr.take();
    let communicate = async {
        let mut out_buf: Vec<u8> = Vec::new();
        let mut err_buf: Vec<u8> = Vec::new();
        let read_out = async {
            if let Some(p) = stdout_pipe.as_mut() {
                let _ = p.read_to_end(&mut out_buf).await;
            }
        };
        let read_err = async {
            if let Some(p) = stderr_pipe.as_mut() {
                let _ = p.read_to_end(&mut err_buf).await;
            }
        };
        tokio::join!(read_out, read_err);
        let status = child.wait().await;
        (out_buf, err_buf, status)
    };

    match tokio::time::timeout(std::time::Duration::from_secs(timeout), communicate).await {
        Ok((stdout_b, stderr_b, status)) => {
            // `.decode(errors="replace")[:MAX_OUTPUT]`
            let stdout = decode_replace_trunc(&stdout_b);
            let stderr = decode_replace_trunc(&stderr_b);
            // `return {"stdout": stdout, "stderr": stderr, "exit_code": proc.returncode}`
            json!({"stdout": stdout, "stderr": stderr, "exit_code": signal_aware_code(&status)})
        }
        // `except asyncio.TimeoutError:` — kill + wait (ProcessLookupError ignored).
        Err(_) => {
            let _ = child.start_kill();
            let _ = child.wait().await;
            json!({
                "stdout": "",
                "stderr": format!("Command timed out after {timeout}s"),
                "exit_code": -1,
            })
        }
    }
}

// ===========================================================================
// Router factory (`setup_shell_routes`)
// ===========================================================================

/// `setup_shell_routes()` — `APIRouter(tags=["shell"])`.
///
/// Mounted as app.py include #27 (line 531). Each handler is at the exact
/// absolute path its Python `@router.post/get(...)` decorator names.
pub fn setup_shell_routes() -> Router<AppState> {
    Router::new()
        // `@router.post("/api/shell/exec")`
        .route("/api/shell/exec", post(shell_exec))
        // `@router.post("/api/shell/stream")`
        .route("/api/shell/stream", post(shell_stream))
        // `@router.get("/api/cookbook/packages")`
        .route("/api/cookbook/packages", get(list_packages))
        // `@router.post("/api/cookbook/packages/install")`
        .route("/api/cookbook/packages/install", post(install_package))
        // `@router.post("/api/cookbook/rebuild-engine")`
        .route("/api/cookbook/rebuild-engine", post(rebuild_engine))
}

// ===========================================================================
// `POST /api/shell/exec`
// ===========================================================================

/// `POST /api/shell/exec` — execute a shell command and return output. Admin only.
async fn shell_exec(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Json(req): Json<ShellExecRequest>,
) -> Result<Response, HttpException> {
    // `_require_admin(request)`
    require_admin(&state, current_user_opt(user.as_ref()))?;
    // `cmd = req.command.strip()`
    let cmd = req.command.trim();
    // `if not cmd: return {"stdout": "", "stderr": "No command provided", "exit_code": 1}`
    if cmd.is_empty() {
        return Ok(Json(json!({"stdout": "", "stderr": "No command provided", "exit_code": 1}))
            .into_response());
    }
    crate::pylog::info(&format!("User shell exec requested: length={}", cmd.chars().count()));
    // `result = await _exec_shell(cmd, timeout=EXEC_TIMEOUT)`
    let result = exec_shell(cmd, EXEC_TIMEOUT).await;
    Ok(Json(result).into_response())
}

// ===========================================================================
// `POST /api/shell/stream`
// ===========================================================================

/// `POST /api/shell/stream` — stream a shell command's output line-by-line via
/// SSE. Admin only.
async fn shell_stream(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Json(req): Json<ShellExecRequest>,
) -> Result<Response, HttpException> {
    // `_require_admin(request)`
    require_admin(&state, current_user_opt(user.as_ref()))?;
    // `cmd = req.command.strip()`
    let cmd = req.command.trim().to_string();

    // `if not cmd: return StreamingResponse(empty(), media_type="text/event-stream")`
    if cmd.is_empty() {
        let body = async_stream::stream! {
            // `yield {"stream": "stderr", "data": "No command provided"}`
            yield sse_bytes(sse_event(&json!({"stream": "stderr", "data": "No command provided"})));
            // `yield {"exit_code": 1}`
            yield sse_bytes(sse_event(&json!({"exit_code": 1})));
        };
        return Ok(sse_response(body));
    }

    // `timeout = req.timeout if req.timeout is not None else STREAM_TIMEOUT`
    let timeout = req.timeout.unwrap_or(STREAM_TIMEOUT);
    let use_pty = req.use_pty;
    let use_tmux = req.use_tmux;
    crate::pylog::info(&format!(
        "User shell stream requested: timeout={} pty={} tmux={} length={}",
        if timeout == 0 { "none".to_string() } else { format!("{timeout}s") },
        py_bool(use_pty),
        py_bool(use_tmux),
        cmd.chars().count(),
    ));

    // `if use_tmux: return StreamingResponse(_generate_tmux(cmd, request), …)`
    if use_tmux {
        return Ok(sse_response(generate_tmux(cmd)));
    }

    // `if use_pty: return StreamingResponse(_generate_pty(cmd, timeout, request), …)`
    if use_pty {
        #[cfg(unix)]
        {
            return Ok(sse_response(generate_pty(cmd, timeout)));
        }
        // Non-Unix: `pty.openpty`/`setsid`/`TIOCSCTTY` have no analogue. This crate
        // ships Unix-first; on other targets keep the honest unavailable shape.
        #[cfg(not(unix))]
        {
            let body = async_stream::stream! {
                yield sse_bytes(sse_event(&json!({
                    "stream": "stderr",
                    "data": "PTY streaming is not available on this server",
                })));
                yield sse_bytes(sse_event(&json!({"exit_code": -1})));
            };
            return Ok(sse_response(body));
        }
    }

    // ── plain `generate()` path (PORT_NOW) ──
    Ok(sse_response(generate_plain(cmd, timeout)))
}

/// `StreamingResponse(body, media_type="text/event-stream")` — content-type only
/// (FastAPI sets no extra SSE headers here; shell_routes.py passes none).
fn sse_response<S>(body: S) -> Response
where
    S: futures_util::Stream<Item = Result<bytes::Bytes, std::convert::Infallible>>
        + Send
        + 'static,
{
    Response::builder()
        .header(header::CONTENT_TYPE, "text/event-stream")
        .body(Body::from_stream(body))
        .unwrap()
}

/// `async def generate()` (the non-pty, non-tmux path). Two reader tasks split
/// each pipe on `\r`/`\n` (via `_find_line_break`) and push lines into a channel;
/// the loop drains it with a per-recv timeout, enforcing the overall `timeout`
/// (`0` = no timeout). `kill_on_drop(true)` reproduces the
/// `request.is_disconnected() -> proc.kill()` cleanup on client disconnect.
fn generate_plain(
    cmd: String,
    timeout: i64,
) -> impl futures_util::Stream<Item = Result<bytes::Bytes, std::convert::Infallible>> {
    async_stream::stream! {
        // `proc = await create_subprocess_shell(cmd, …, cwd=Path.home())`
        let mut child = match Command::new("sh")
            .arg("-c")
            .arg(&cmd)
            .current_dir(home_dir())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true)
            .spawn()
        {
            Ok(c) => c,
            // The Python `try:` wraps the spawn; a launch failure hits `except
            // Exception as e:` -> stderr line + `exit_code: -1`.
            Err(e) => {
                yield sse_bytes(sse_event(&json!({"stream": "stderr", "data": e.to_string()})));
                yield sse_bytes(sse_event(&json!({"exit_code": -1})));
                return;
            }
        };

        let stdout_pipe = child.stdout.take();
        let stderr_pipe = child.stderr.take();

        // `q: asyncio.Queue` — two reader coroutines push `(name, Some(line))` per
        // line, then a `(name, None)` EOF sentinel. mpsc channel + two tasks.
        let (tx, mut rx) =
            tokio::sync::mpsc::unbounded_channel::<(&'static str, Option<String>)>();

        // `async def _reader(stream, name):` — chunked read, `_find_line_break` split.
        fn spawn_reader<R>(
            pipe: Option<R>,
            name: &'static str,
            tx: tokio::sync::mpsc::UnboundedSender<(&'static str, Option<String>)>,
        ) -> tokio::task::JoinHandle<()>
        where
            R: tokio::io::AsyncRead + Unpin + Send + 'static,
        {
            tokio::spawn(async move {
                if let Some(mut pipe) = pipe {
                    let mut buf: Vec<u8> = Vec::new();
                    let mut chunk = [0u8; 4096];
                    loop {
                        // `chunk = await stream.read(4096)`
                        let n = match pipe.read(&mut chunk).await {
                            Ok(0) | Err(_) => {
                                // `if not chunk:` — EOF.
                                if !buf.is_empty() {
                                    // `q.put((name, buf.decode(errors="replace").rstrip("\r\n")))`
                                    let mut text = String::from_utf8_lossy(&buf).into_owned();
                                    while text.ends_with('\r') || text.ends_with('\n') {
                                        text.pop();
                                    }
                                    let _ = tx.send((name, Some(text)));
                                }
                                break;
                            }
                            Ok(n) => n,
                        };
                        // `buf += chunk`
                        buf.extend_from_slice(&chunk[..n]);
                        // `while True: idx, sep_len = _find_line_break(buf); …`
                        loop {
                            let (idx, sep_len) = find_line_break(&buf);
                            if idx < 0 {
                                break;
                            }
                            let idx = idx as usize;
                            // `line = buf[:idx].decode(errors="replace")`
                            let line = String::from_utf8_lossy(&buf[..idx]).into_owned();
                            // `buf = buf[idx + sep_len:]`
                            buf.drain(..idx + sep_len);
                            // `if line: await q.put((name, line))`
                            if !line.is_empty() && tx.send((name, Some(line))).is_err() {
                                return;
                            }
                        }
                    }
                }
                // `finally: await q.put((name, None))`
                let _ = tx.send((name, None));
            })
        }

        // `reader_tasks = [create_task(_reader(stdout)), create_task(_reader(stderr))]`
        let out_task = spawn_reader(stdout_pipe, "stdout", tx.clone());
        let err_task = spawn_reader(stderr_pipe, "stderr", tx.clone());
        drop(tx);

        // `finally: for t in reader_tasks: t.cancel()` — abort on drop/return.
        struct ReaderGuard {
            out: tokio::task::JoinHandle<()>,
            err: tokio::task::JoinHandle<()>,
        }
        impl Drop for ReaderGuard {
            fn drop(&mut self) {
                self.out.abort();
                self.err.abort();
            }
        }
        let _guard = ReaderGuard { out: out_task, err: err_task };

        // `finished = 0; deadline = (loop.time() + timeout) if timeout else None`
        // `timeout` is SIGNED: a negative override yields an already-past deadline
        // that fires immediately (see [`instant_plus_signed_secs`]); only `0` means
        // "no deadline" (Python's falsy `if timeout`).
        let mut finished = 0;
        let deadline = if timeout != 0 {
            Some(instant_plus_signed_secs(tokio::time::Instant::now(), timeout))
        } else {
            None
        };
        let mut timed_out = false;

        // `while finished < 2:`
        while finished < 2 {
            // `if deadline: remaining = deadline - now; if remaining <= 0: raise TimeoutError()`
            // `wait = min(remaining, 2.0)` else `wait = 2.0`
            let wait = match deadline {
                Some(d) => {
                    let now = tokio::time::Instant::now();
                    if now >= d {
                        timed_out = true;
                        break;
                    }
                    (d - now).min(std::time::Duration::from_secs_f64(2.0))
                }
                None => std::time::Duration::from_secs_f64(2.0),
            };

            // `name, text = await asyncio.wait_for(q.get(), timeout=wait)`
            match tokio::time::timeout(wait, rx.recv()).await {
                // `except asyncio.TimeoutError:` — the Python here re-checks
                // `request.is_disconnected()` and kills; with `kill_on_drop` a real
                // client disconnect already drops this future + child. Just continue.
                Err(_) => continue,
                // Channel closed before both sentinels (defensive): stop.
                Ok(None) => break,
                Ok(Some((_name, None))) => {
                    // `if text is None: finished += 1; continue`
                    finished += 1;
                }
                Ok(Some((name, Some(text)))) => {
                    // `yield {"stream": name, "data": text}`
                    yield sse_bytes(sse_event(&json!({"stream": name, "data": text})));
                }
            }
        }

        if timed_out {
            // `except asyncio.TimeoutError:` — kill + wait, then emit.
            let _ = child.start_kill();
            let _ = child.wait().await;
            yield sse_bytes(sse_event(&json!({
                "stream": "stderr",
                "data": format!("Command timed out after {timeout}s"),
            })));
            yield sse_bytes(sse_event(&json!({"exit_code": -1})));
            return;
        }

        // `await proc.wait(); yield {"exit_code": proc.returncode}`
        let status = child.wait().await;
        yield sse_bytes(sse_event(&json!({"exit_code": signal_aware_code(&status)})));
    }
}

/// `async def _generate_pty(cmd, timeout, request)` — run `cmd` in a pseudo-TTY so
/// `tqdm`/progress bars (which gate on `isatty()`) work natively, streaming the
/// PTY's master output line-by-line via SSE.
///
/// Python uses `pty.openpty()` + `os.setsid` (preexec) + a `select`-driven
/// blocking read in an executor. The faithful Rust port (Unix-only, like
/// `bg_jobs::spawn_detached`):
///   * `nix::pty::openpty(None, None)` -> `{master, slave}` `OwnedFd`s.
///   * a `std::process::Command` (NOT tokio — we need `pre_exec` + `Stdio::from`
///     of the slave fd) running `sh -c cmd` with `cwd = home`, the slave cloned
///     into stdin/stdout/stderr, and a `pre_exec` doing `setsid()` then
///     `ioctl(0, TIOCSCTTY, 0)` to make the new session's controlling tty the PTY.
///   * the parent's slave fd dropped after spawn (`os.close(slave_fd)`), so the
///     master sees EOF once the child closes all its slave references (i.e. exits).
///   * a dedicated blocking thread over `std::fs::File::from(master)` doing
///     4096-byte reads into a `tokio::sync::mpsc::unbounded_channel<Vec<u8>>`; EOF
///     drops the sender, closing the channel. (Python's master-non-blocking +
///     `select(2s)` is replaced by a blocking read on its own thread — equivalent,
///     no busy-poll; documented drift.)
///
/// The async loop maintains the SIGNED deadline (see [`instant_plus_signed_secs`])
/// and reuses the same `_find_line_break` line splitter as the plain path.
#[cfg(unix)]
fn generate_pty(
    cmd: String,
    timeout: i64,
) -> impl futures_util::Stream<Item = Result<bytes::Bytes, std::convert::Infallible>> {
    use std::io::Read;
    use std::os::unix::process::CommandExt;

    async_stream::stream! {
        // `master_fd, slave_fd = pty.openpty()`
        let pty = match nix::pty::openpty(None, None) {
            Ok(p) => p,
            // openpty failing (no free ptys) is not something Python's `_generate_pty`
            // guards either, but surface the same `except Exception` failure shape the
            // outer Python try/except would emit rather than panic the stream.
            Err(e) => {
                yield sse_bytes(sse_event(&json!({"stream": "stderr", "data": e.to_string()})));
                yield sse_bytes(sse_event(&json!({"exit_code": -1})));
                return;
            }
        };
        let nix::pty::OpenptyResult { master, slave } = pty;

        // Clone the slave into the child's stdin/stdout/stderr (the Python passes the
        // SAME `slave_fd` to all three; `Stdio::from(OwnedFd)` consumes its fd, so
        // each handle needs its own clone of the slave).
        let (slave_in, slave_out, slave_err) = match (
            slave.try_clone(),
            slave.try_clone(),
            slave.try_clone(),
        ) {
            (Ok(a), Ok(b), Ok(c)) => (a, b, c),
            _ => {
                yield sse_bytes(sse_event(&json!({
                    "stream": "stderr",
                    "data": "failed to clone pty slave fd",
                })));
                yield sse_bytes(sse_event(&json!({"exit_code": -1})));
                return;
            }
        };

        // `proc = await create_subprocess_shell(cmd, stdin/out/err=slave_fd,
        //  cwd=Path.home(), preexec_fn=os.setsid)`
        let mut command = std::process::Command::new("sh");
        command
            .arg("-c")
            .arg(&cmd)
            .current_dir(home_dir())
            .stdin(Stdio::from(slave_in))
            .stdout(Stdio::from(slave_out))
            .stderr(Stdio::from(slave_err));
        // SAFETY: only async-signal-safe calls run in the child between fork and
        // exec — `setsid()` (== Python's `os.setsid`) then `ioctl(0, TIOCSCTTY, 0)`
        // to acquire the PTY as the new session's controlling terminal. The ioctl
        // request type differs per platform (`c_ulong` on BSD/macOS, `Ioctl` on
        // Linux); `as _` lets the call infer it from `nix::libc::ioctl`'s signature.
        unsafe {
            command.pre_exec(|| {
                nix::unistd::setsid().map_err(std::io::Error::from)?;
                let rc = nix::libc::ioctl(0, nix::libc::TIOCSCTTY as _, 0);
                if rc != 0 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
        let child = match command.spawn() {
            Ok(c) => c,
            Err(e) => {
                yield sse_bytes(sse_event(&json!({"stream": "stderr", "data": e.to_string()})));
                yield sse_bytes(sse_event(&json!({"exit_code": -1})));
                return;
            }
        };
        // `os.close(slave_fd)` — parent doesn't need the slave side. Dropping our
        // owned slave + the three clones below releases every parent reference so the
        // master read can reach EOF once the child exits.
        drop(slave);

        // Kill-on-drop guard for the `std::process::Child` (std `Command` has no
        // `kill_on_drop`; this mirrors the plain path's `ReaderGuard` cleanup, so a
        // dropped SSE body / early return reaps the child like Python's
        // `request.is_disconnected() -> proc.kill()`). Holds an `Option` so a clean
        // exit can `take()` the child out before reaping it, leaving Drop a no-op
        // (no double-`wait`/kill of a possibly-recycled PID).
        struct ChildGuard(Option<std::process::Child>);
        impl Drop for ChildGuard {
            fn drop(&mut self) {
                if let Some(mut child) = self.0.take() {
                    let _ = child.kill();
                    let _ = child.wait();
                }
            }
        }
        let mut guard = ChildGuard(Some(child));

        // Blocking reader thread over the master fd, feeding 4096-byte chunks into an
        // mpsc channel. EOF (read==0) or a read error drops the sender (channel close).
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<Vec<u8>>();
        let master_file = std::fs::File::from(master);
        std::thread::spawn(move || {
            let mut file = master_file;
            let mut chunk = [0u8; 4096];
            loop {
                match file.read(&mut chunk) {
                    Ok(0) | Err(_) => break, // EOF or fd closed.
                    Ok(n) => {
                        if tx.send(chunk[..n].to_vec()).is_err() {
                            break; // receiver gone (stream dropped).
                        }
                    }
                }
            }
            // Dropping `tx`/`file` here closes the channel and the master fd.
        });

        // `deadline = (loop.time() + timeout) if timeout else None`
        let deadline = if timeout != 0 {
            Some(instant_plus_signed_secs(tokio::time::Instant::now(), timeout))
        } else {
            None
        };

        // `buf = b""`
        let mut buf: Vec<u8> = Vec::new();
        let mut timed_out = false;

        // `while not process_done.is_set():` — drive off the master channel. The
        // channel closing (sender dropped on master EOF) is the EOF signal, which the
        // child only reaches after it has exited and released the slave fd.
        loop {
            // `if deadline and loop.time() > deadline:` -> kill + timeout event.
            let wait = match deadline {
                Some(d) => {
                    let now = tokio::time::Instant::now();
                    if now >= d {
                        timed_out = true;
                        break;
                    }
                    // Python caps each read wait at 2.0s (`asyncio.wait_for(..., 2.0)`).
                    (d - now).min(std::time::Duration::from_secs_f64(2.0))
                }
                None => std::time::Duration::from_secs_f64(2.0),
            };

            // `chunk = await asyncio.wait_for(run_in_executor(_pty_read), 2.0)`
            match tokio::time::timeout(wait, rx.recv()).await {
                // `except asyncio.TimeoutError: continue` — no data this round.
                Err(_) => continue,
                // EOF — process closed the PTY (channel sender dropped). `break`.
                Ok(None) => break,
                Ok(Some(chunk)) => {
                    // `buf += chunk`
                    buf.extend_from_slice(&chunk);
                    // `while True: idx, sep_len = _find_line_break(buf); …`
                    loop {
                        let (idx, sep_len) = find_line_break(&buf);
                        if idx < 0 {
                            break;
                        }
                        let idx = idx as usize;
                        // `line = buf[:idx].decode(errors="replace")`
                        let line = String::from_utf8_lossy(&buf[..idx]).into_owned();
                        // `buf = buf[idx + sep_len:]`
                        buf.drain(..idx + sep_len);
                        // `if line: yield {"stream": "stdout", "data": line}`
                        if !line.is_empty() {
                            yield sse_bytes(sse_event(&json!({"stream": "stdout", "data": line})));
                        }
                    }
                }
            }
        }

        if timed_out {
            // `proc.kill(); await proc.wait()` — take the child so the guard's Drop
            // does not re-reap it.
            if let Some(mut child) = guard.0.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
            // `yield {"stream": "stderr", "data": f"Command timed out after {timeout}s"}`
            yield sse_bytes(sse_event(&json!({
                "stream": "stderr",
                "data": format!("Command timed out after {timeout}s"),
            })));
            yield sse_bytes(sse_event(&json!({"exit_code": -1})));
            return;
        }

        // Drain any remaining PTY output already buffered in the channel (the reader
        // thread may have queued bytes between the last poll and EOF).
        while let Ok(chunk) = rx.try_recv() {
            buf.extend_from_slice(&chunk);
        }

        // `# Flush remaining buffer` — split full lines, then the trailing stripped tail.
        if !buf.is_empty() {
            loop {
                let (idx, sep_len) = find_line_break(&buf);
                if idx < 0 {
                    break;
                }
                let idx = idx as usize;
                let line = String::from_utf8_lossy(&buf[..idx]).into_owned();
                buf.drain(..idx + sep_len);
                if !line.is_empty() {
                    yield sse_bytes(sse_event(&json!({"stream": "stdout", "data": line})));
                }
            }
            // `if buf: text = buf.decode(errors="replace").strip(); if text: yield …`
            if !buf.is_empty() {
                let text = String::from_utf8_lossy(&buf).trim().to_string();
                if !text.is_empty() {
                    yield sse_bytes(sse_event(&json!({"stream": "stdout", "data": text})));
                }
            }
        }

        // `await wait_task; yield {"exit_code": proc.returncode}` — reap the child
        // without blocking the runtime. Take it out of the guard first so Drop is a
        // no-op (the channel already closed on master EOF, which only happens after
        // the child exited, so this `wait()` returns promptly).
        let exit = match guard.0.take() {
            Some(mut child) => {
                let status = tokio::task::spawn_blocking(move || child.wait()).await;
                match status {
                    // `proc.returncode` — code, or negative signal on signal death.
                    Ok(s) => signal_aware_code(&s),
                    // join error -> surface `-1`.
                    Err(_) => -1,
                }
            }
            None => -1,
        };
        yield sse_bytes(sse_event(&json!({"exit_code": exit})));
    }
}

/// `async def _generate_tmux(cmd, request)` — run the command in a tmux session
/// behind a wrapper script and tail its log file as SSE. The tmux session
/// survives a browser disconnect.
fn generate_tmux(
    cmd: String,
) -> impl futures_util::Stream<Item = Result<bytes::Bytes, std::convert::Infallible>> {
    async_stream::stream! {
        let log_dir = tmux_log_dir();
        // `TMUX_LOG_DIR.mkdir(parents=True, exist_ok=True)`
        let _ = std::fs::create_dir_all(&log_dir);
        // `session_id = f"cookbook-{uuid.uuid4().hex[:8]}"`
        let session_id = format!("cookbook-{}", &uuid::Uuid::new_v4().simple().to_string()[..8]);
        let log_path = log_dir.join(format!("{session_id}.log"));
        let script_path = log_dir.join(format!("{session_id}.sh"));

        // Wrapper script (byte-for-byte from the Python f-string).
        let script = format!(
            "#!/bin/bash\n\
             ODYSSEUS_USER_SHELL=\"${{SHELL:-}}\"\n\
             if [ -n \"$ODYSSEUS_USER_SHELL\" ] && [ -x \"$ODYSSEUS_USER_SHELL\" ]; then\n  \
             ODYSSEUS_USER_PATH=\"$(\"$ODYSSEUS_USER_SHELL\" -ic 'printf \"__ODYSSEUS_PATH__%s\\n\" \"$PATH\"' 2>/dev/null | sed -n 's/^__ODYSSEUS_PATH__//p' | tail -n 1 || true)\"\n  \
             if [ -n \"$ODYSSEUS_USER_PATH\" ]; then export PATH=\"$ODYSSEUS_USER_PATH:$PATH\"; fi\n\
             fi\n\
             {cmd} 2>&1 | tee '{log}'\n\
             EC=${{PIPESTATUS[0]}}\n\
             echo ':::EXIT_CODE:::'$EC >> '{log}'\n\
             rm -f '{script}'\n\
             exit $EC\n",
            cmd = cmd,
            log = log_path.to_string_lossy(),
            script = script_path.to_string_lossy(),
        );
        let _ = std::fs::write(&script_path, &script);
        // `script_path.chmod(0o755)`
        let _ = crate::pyos::chmod(&script_path.to_string_lossy(), 0o755);
        crate::pylog::info(&format!(
            "tmux wrapper script created: session={session_id} path={}",
            script_path.to_string_lossy()
        ));

        // `tmux_cmd = f"tmux new-session -d -s {session_id} {shlex.quote(str(script_path))}"`
        let tmux_cmd = format!(
            "tmux new-session -d -s {session_id} {}",
            shquote(&script_path.to_string_lossy())
        );

        // `proc = await create_subprocess_shell(tmux_cmd, …); await proc.wait()`
        let start = Command::new("sh")
            .arg("-c")
            .arg(&tmux_cmd)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .output()
            .await;
        match start {
            // `if proc.returncode != 0:` — read stderr, emit failure, return.
            Ok(out) if !out.status.success() => {
                let stderr = String::from_utf8_lossy(&out.stderr).into_owned();
                yield sse_bytes(sse_event(&json!({
                    "stream": "stderr",
                    "data": format!("Failed to start tmux: {stderr}"),
                })));
                yield sse_bytes(sse_event(&json!({"exit_code": -1})));
                return;
            }
            // A spawn failure (`sh`/`tmux` missing) is NOT caught in Python — it
            // would propagate. Surface the same failure shape so the stream still
            // terminates cleanly rather than panicking.
            Err(e) => {
                yield sse_bytes(sse_event(&json!({
                    "stream": "stderr",
                    "data": format!("Failed to start tmux: {e}"),
                })));
                yield sse_bytes(sse_event(&json!({"exit_code": -1})));
                return;
            }
            Ok(_) => {}
        }

        // `yield {"stream": "stdout", "data": f"Started tmux session: {session_id}"}`
        yield sse_bytes(sse_event(&json!({
            "stream": "stdout",
            "data": format!("Started tmux session: {session_id}"),
        })));

        // Tail the log, streaming new lines.
        let mut lines_sent: usize = 0;
        let mut exit_code: Option<i64> = None;

        loop {
            // NOTE: `request.is_disconnected()` -> with a dropped SSE body this
            // future is simply no longer polled (the tmux session keeps running,
            // matching the Python intent); there is no axum poll for it, so the
            // explicit "Disconnected." line cannot be emitted from here.

            // `if log_path.exists(): lines = read_text().splitlines(); new = lines[lines_sent:]`
            if log_path.exists() {
                if let Ok(text) = read_text_replace(&log_path) {
                    let lines = splitlines(&text);
                    if lines.len() > lines_sent {
                        for line in &lines[lines_sent..] {
                            if let Some(rest) = line.strip_prefix(":::EXIT_CODE:::") {
                                // `exit_code = int(line.split(":::")[-1])` — the trailing
                                // segment after the final `:::`; `except ValueError: -1`.
                                let last = rest.rsplit(":::").next().unwrap_or(rest);
                                exit_code = Some(last.trim().parse::<i64>().unwrap_or(-1));
                            } else {
                                yield sse_bytes(sse_event(&json!({"stream": "stdout", "data": line})));
                            }
                        }
                    }
                    lines_sent = lines.len();
                }
            }

            // `if exit_code is not None: break`
            if exit_code.is_some() {
                break;
            }

            // `check = await create_subprocess_shell("tmux has-session -t {sid} 2>/dev/null"); await check.wait()`
            let check = Command::new("sh")
                .arg("-c")
                .arg(format!("tmux has-session -t {session_id} 2>/dev/null"))
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status()
                .await;
            let alive = matches!(check, Ok(s) if s.success());
            if !alive {
                // Session ended — one final read after a 0.5s settle.
                tokio::time::sleep(std::time::Duration::from_millis(500)).await;
                if log_path.exists() {
                    if let Ok(text) = read_text_replace(&log_path) {
                        let lines = splitlines(&text);
                        if lines.len() > lines_sent {
                            for line in &lines[lines_sent..] {
                                if let Some(rest) = line.strip_prefix(":::EXIT_CODE:::") {
                                    let last = rest.rsplit(":::").next().unwrap_or(rest);
                                    exit_code = Some(last.trim().parse::<i64>().unwrap_or(-1));
                                } else {
                                    yield sse_bytes(sse_event(&json!({"stream": "stdout", "data": line})));
                                }
                            }
                        }
                    }
                }
                // `if exit_code is None: exit_code = 0`
                if exit_code.is_none() {
                    exit_code = Some(0);
                }
                break;
            }

            // `await asyncio.sleep(1.0)`
            tokio::time::sleep(std::time::Duration::from_secs(1)).await;
        }

        // `yield {"exit_code": exit_code}`
        yield sse_bytes(sse_event(&json!({"exit_code": exit_code})));

        // `log_path.unlink(missing_ok=True)` (try/except pass).
        let _ = std::fs::remove_file(&log_path);
    }
}

// ===========================================================================
// `GET /api/cookbook/packages`
// ===========================================================================

/// One package descriptor row (built in the exact Python key order).
fn package_rows() -> Vec<Value> {
    vec![
        // ── System ──
        json!({"name": "tmux", "pip": "", "desc": "Required for Linux/Termux Cookbook background downloads and serves", "category": "System", "target": "remote", "kind": "system", "install_hint": "Run Cookbook server setup, or install tmux with apt/pacman/dnf/apk/zypper."}),
        json!({"name": "docker", "pip": "", "desc": "Required only for Docker-backed launch commands", "category": "System", "target": "remote", "kind": "system", "install_hint": "Install Docker on the selected server and allow this user to run docker."}),
        // ── LLM ──
        json!({"name": "hf_transfer", "pip": "hf_transfer", "desc": "Fast model downloads from HuggingFace", "category": "LLM", "target": "remote"}),
        json!({"name": "llama_cpp", "pip": "llama-cpp-python[server]", "desc": "Serve GGUF models via llama.cpp", "category": "LLM", "target": "remote"}),
        json!({"name": "sglang", "pip": "sglang[all]", "desc": "Serve HF safetensors models via SGLang", "category": "LLM", "target": "remote"}),
        json!({"name": "vllm", "pip": "vllm", "desc": "High-throughput LLM serving engine", "category": "LLM", "target": "remote"}),
        // ── Image ──
        json!({"name": "diffusers", "pip": "diffusers", "desc": "Image generation pipelines (SD, Flux)", "category": "Image", "target": "remote"}),
        json!({"name": "rembg", "pip": "rembg[gpu]", "desc": "AI background removal for image editor", "category": "Image", "target": "local"}),
        json!({"name": "realesrgan", "pip": "realesrgan", "desc": "AI denoise + upscale (Real-ESRGAN). Used by editor's Denoise and Upscale tools.", "category": "Image", "target": "local"}),
        // ── Tools ──
        json!({"name": "playwright", "pip": "playwright", "desc": "Browser automation for web tools", "category": "Tools", "target": "local"}),
    ]
}

/// `GET /api/cookbook/packages` — check which optional packages are installed.
/// Admin only, with a `sec-fetch-site` cross-site guard.
async fn list_packages(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    headers: HeaderMap,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    // `_require_admin(request)`
    require_admin(&state, current_user_opt(user.as_ref()))?;
    // `_reject_cross_site(request)`
    reject_cross_site(&headers)?;
    // `host: str | None = None, ssh_port: str | None = None, venv: str | None = None`
    let host = q.get("host").filter(|s| !s.is_empty()).cloned();
    let ssh_port = q.get("ssh_port").cloned();
    let venv = q.get("venv").filter(|s| !s.is_empty()).cloned();

    // `if ssh_port and str(ssh_port).strip() not in ("", "22"):` validate up-front.
    if let Some(p) = ssh_port.as_deref() {
        let port = p.trim();
        if !port.is_empty() && port != "22" {
            // `if not _SSH_PORT_RE.match(port) or not (1 <= int(port) <= 65535):`
            let ok = ssh_port_re_match(port)
                && port
                    .parse::<u32>()
                    .map(|n| (1..=65535).contains(&n))
                    .unwrap_or(false);
            if !ok {
                return Err(HttpException::new(400, "Invalid ssh_port"));
            }
        }
    }

    let mut packages = package_rows();

    // `remote_names = [name for p if target=="remote" and kind != "system"]`
    let remote_names: Vec<String> = packages
        .iter()
        .filter(|p| {
            p.get("target").and_then(Value::as_str) == Some("remote")
                && p.get("kind").and_then(Value::as_str) != Some("system")
        })
        .filter_map(|p| p.get("name").and_then(Value::as_str).map(String::from))
        .collect();
    // `remote_system_names = [name for p if target=="remote" and kind == "system"]`
    let remote_system_names: Vec<String> = packages
        .iter()
        .filter(|p| {
            p.get("target").and_then(Value::as_str) == Some("remote")
                && p.get("kind").and_then(Value::as_str) == Some("system")
        })
        .filter_map(|p| p.get("name").and_then(Value::as_str).map(String::from))
        .collect();

    let mut remote_status: HashMap<String, bool> = HashMap::new();

    // ── Remote Python-package probe over SSH ──
    if let (Some(host), false) = (host.as_deref(), remote_names.is_empty()) {
        // `names_lit = ",".join(repr(n) for n in remote_names)`
        let names_lit = remote_names.iter().map(|n| py_repr(n)).collect::<Vec<_>>().join(",");
        let py = format!(
            "import importlib.util,json,shutil;\
             names=[{names_lit}];\
             status={{n:(importlib.util.find_spec(n) is not None) for n in names}};\
             status['llama_cpp']=status.get('llama_cpp',False) or shutil.which('llama-server') is not None;\
             print(json.dumps(status))"
        );
        // `src = _venv_activate_prefix(venv)` — `". {act} && "`, or "" / `ValueError`.
        // A bad venv path raises `ValueError` -> `HTTPException(400, str(e))`.
        let src = venv_activate_prefix(venv.as_deref()).map_err(|e| HttpException::new(400, e))?;
        // `inner = f"{src}python3 -c {shlex.quote(py)}"`
        let inner = format!("{src}python3 -c {}", shquote(&py));
        // `argv = _ssh_base_argv(host, ssh_port) + [inner]` — a VALIDATED argv vector,
        // run directly with no local `sh -c` parsing (command-injection fix). A bad
        // host/port raises `ValueError` -> `HTTPException(400, str(e))`.
        let mut argv = ssh_base_argv(host, ssh_port.as_deref()).map_err(|e| HttpException::new(400, e))?;
        argv.push(inner);
        // `proc = await create_subprocess_exec(*argv, …); out = await wait_for(communicate, 12)`
        let mut command = Command::new(&argv[0]);
        command.args(&argv[1..]);
        command.stdout(Stdio::piped()).stderr(Stdio::piped());
        if let Ok(Some(out)) = run_with_timeout(command, 12).await {
            let txt = String::from_utf8_lossy(&out.stdout).trim().to_string();
            // `for line in reversed(txt.splitlines()): if line.startswith("{"): remote_status = json.loads(line)`
            for line in txt.lines().rev() {
                let line = line.trim();
                if line.starts_with('{') {
                    if let Ok(Value::Object(map)) = serde_json::from_str::<Value>(line) {
                        remote_status = map
                            .into_iter()
                            .map(|(k, v)| (k, value_truthy(&v)))
                            .collect();
                    }
                    break;
                }
            }
        }
        // `except Exception: remote_status = {}` already covered by the if-let arms.
    }

    // ── Remote system-binary probe over SSH ──
    if let (Some(host), false) = (host.as_deref(), remote_system_names.is_empty()) {
        let mut checks: Vec<String> = Vec::new();
        for name in &remote_system_names {
            let qn = shquote(name);
            checks.push(format!(
                "if command -v {qn} >/dev/null 2>&1; then echo {qn}=1; else echo {qn}=0; fi"
            ));
        }
        let inner = checks.join(" ; ");
        // `argv = _ssh_base_argv(host, ssh_port) + [inner]` — VALIDATED argv vector,
        // run directly (no `sh -c`). A bad host/port raises `ValueError` -> 400.
        let mut argv = ssh_base_argv(host, ssh_port.as_deref()).map_err(|e| HttpException::new(400, e))?;
        argv.push(inner);
        let mut command = Command::new(&argv[0]);
        command.args(&argv[1..]);
        command.stdout(Stdio::piped()).stderr(Stdio::piped());
        if let Ok(Some(out)) = run_with_timeout(command, 12).await {
            let txt = String::from_utf8_lossy(&out.stdout).trim().to_string();
            for line in txt.lines() {
                // `name, sep, value = line.strip().partition("=")`
                let line = line.trim();
                if let Some((name, value)) = line.split_once('=') {
                    if remote_system_names.iter().any(|n| n == name) {
                        remote_status.insert(name.to_string(), value == "1");
                    }
                }
            }
        }
        // `except Exception: pass`
    }

    // ── Per-package `installed` resolution ──
    for pkg in packages.iter_mut() {
        let name = pkg.get("name").and_then(Value::as_str).unwrap_or("").to_string();
        let target = pkg.get("target").and_then(Value::as_str).map(String::from);
        let kind = pkg.get("kind").and_then(Value::as_str).map(String::from);
        let obj = pkg.as_object_mut().unwrap();

        // `if host and pkg.get("target") == "remote": pkg["installed"] = bool(remote_status.get(name)); continue`
        if host.is_some() && target.as_deref() == Some("remote") {
            let installed = remote_status.get(&name).copied().unwrap_or(false);
            obj.insert("installed".to_string(), Value::Bool(installed));
            continue;
        }
        // `if pkg.get("kind") == "system": pkg["installed"] = shutil.which(name) is not None; continue`
        if kind.as_deref() == Some("system") {
            obj.insert("installed".to_string(), Value::Bool(shutil_which(&name).is_some()));
            continue;
        }
        // Local Python-package check.
        // `if name == "llama_cpp" and shutil.which("llama-server"): installed = True; continue`
        if name == "llama_cpp" && shutil_which("llama-server").is_some() {
            obj.insert("installed".to_string(), Value::Bool(true));
            continue;
        }
        // `importlib.import_module(name)` — no Rust analogue (the binary is not a
        // Python interpreter). Python's `except ImportError` arm sets False; an
        // un-importable module is exactly the result here, so `installed = False`.
        obj.insert("installed".to_string(), Value::Bool(false));
    }

    // `return {"packages": packages}`
    Ok(Json(json!({"packages": packages})).into_response())
}

// ===========================================================================
// `POST /api/cookbook/packages/install`
// ===========================================================================

/// `POST /api/cookbook/packages/install` — install a package via pip. Admin only.
async fn install_package(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    // `_require_admin(request)` — runs FIRST (taking the raw body as `Bytes` never
    // fails extraction, so the admin gate is reached before any body inspection,
    // exactly as Python calls `_require_admin(request)` before `await request.json()`).
    require_admin(&state, current_user_opt(user.as_ref()))?;
    // `body = await request.json()` — Python does this with NO try/except. An empty,
    // syntactically invalid, OR non-object JSON body raises (JSONDecodeError, or an
    // AttributeError on the subsequent `.get`) -> the exception is uncaught -> the
    // app returns 500. Mirror that: a body that is not a JSON OBJECT yields a 500.
    let body: Value = match serde_json::from_slice::<Value>(&raw_body) {
        Ok(v @ Value::Object(_)) => v,
        // Invalid/empty JSON -> `request.json()` raises -> 500.
        // Valid-but-non-object JSON -> `body.get("pip")` raises AttributeError -> 500.
        _ => return Err(HttpException::new(500, "Internal Server Error")),
    };
    // `pip_name = body.get("pip")`
    let pip_name = body.get("pip").and_then(Value::as_str).map(String::from);
    // `if not pip_name: return {"ok": False, "error": "No package specified"}`
    let pip_name = match pip_name.filter(|s| !s.is_empty()) {
        Some(p) => p,
        None => {
            return Ok(Json(json!({"ok": false, "error": "No package specified"})).into_response());
        }
    };
    // `if pip_name not in known: return {"ok": False, "error": f"Unknown package: {pip_name}"}`
    if !INSTALL_ALLOW_LIST.contains(&pip_name.as_str()) {
        return Ok(
            Json(json!({"ok": false, "error": format!("Unknown package: {pip_name}")}))
                .into_response(),
        );
    }
    // `cmd = [sys.executable, "-m", "pip", "install", pip_name]` — DEFERRED.
    // `sys.executable` is the Python interpreter running Odysseus; the standalone
    // Rust binary has no such environment to install into. Python returns
    // `{"ok": False, "error": <stderr tail>}` on a failed install; the honest
    // "cannot run pip" outcome is the same `ok: False` failure shape.
    Ok(Json(json!({
        "ok": false,
        "error": "pip install is not available on this server",
    }))
    .into_response())
}

// ===========================================================================
// `POST /api/cookbook/rebuild-engine`
// ===========================================================================

/// `POST /api/cookbook/rebuild-engine` — clear the cached llama.cpp build so the
/// next serve recompiles. Admin only. Removes the Cookbook-managed
/// `~/bin/llama-server` symlink and `~/llama.cpp/build` directory, locally
/// (`bash -lc cmd`) or on the selected remote server (over a VALIDATED ssh argv).
/// Installs/downloads nothing.
async fn rebuild_engine(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    // `_require_admin(request)` — runs FIRST (the raw body always extracts), so the
    // gate is reached before any body inspection, matching Python.
    require_admin(&state, current_user_opt(user.as_ref()))?;
    // `body = await request.json()` — Python has NO try/except. Invalid/empty or a
    // non-object JSON body raises (JSONDecodeError / AttributeError on `.get`) ->
    // uncaught -> 500. Mirror that: a non-object body yields a 500.
    let body: Value = match serde_json::from_slice::<Value>(&raw_body) {
        Ok(v @ Value::Object(_)) => v,
        _ => return Err(HttpException::new(500, "Internal Server Error")),
    };
    // `engine = str(body.get("engine") or "llamacpp").strip()`
    let engine = body
        .get("engine")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .unwrap_or("llamacpp")
        .trim()
        .to_string();
    // `if engine != "llamacpp": return {"ok": False, "error": f"Unsupported engine: {engine}"}`
    if engine != "llamacpp" {
        return Ok(
            Json(json!({"ok": false, "error": format!("Unsupported engine: {engine}")}))
                .into_response(),
        );
    }
    // `host = str(body.get("remote_host") or "").strip()`
    let host = body
        .get("remote_host")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    // `ssh_port = body.get("ssh_port")` — string or null; numeric is coerced to str
    // by `_ssh_base_argv`'s `str(ssh_port)` — accept either JSON form.
    let ssh_port: Option<String> = match body.get("ssh_port") {
        Some(Value::String(s)) => Some(s.clone()),
        Some(Value::Number(n)) => Some(n.to_string()),
        _ => None,
    };
    // `cmd = _llama_cpp_rebuild_cmd()`
    let cmd = llama_cpp_rebuild_cmd();
    // `argv = (_ssh_base_argv(host, ssh_port) + [cmd]) if host else ["bash", "-lc", cmd]`
    // A bad host/port raises `ValueError` -> `HTTPException(400, str(e))`.
    let argv: Vec<String> = if !host.is_empty() {
        let mut a =
            ssh_base_argv(&host, ssh_port.as_deref()).map_err(|e| HttpException::new(400, e))?;
        a.push(cmd);
        a
    } else {
        vec!["bash".to_string(), "-lc".to_string(), cmd]
    };
    // `proc = await create_subprocess_exec(*argv, …); out, err = await wait_for(communicate, 30)`
    let mut command = Command::new(&argv[0]);
    command.args(&argv[1..]);
    command.stdout(Stdio::piped()).stderr(Stdio::piped());
    match run_with_timeout(command, 30).await {
        // `except asyncio.TimeoutError: return {"ok": False, "error": "Rebuild-engine command timed out."}`
        Ok(None) => Ok(Json(json!({
            "ok": false,
            "error": "Rebuild-engine command timed out.",
        }))
        .into_response()),
        Ok(Some(out)) => {
            if out.status.success() {
                // `return {"ok": True, "output": out.decode(...)[-400:]}`
                let text = String::from_utf8_lossy(&out.stdout).into_owned();
                Ok(Json(json!({"ok": true, "output": tail_chars(&text, 400)})).into_response())
            } else {
                // `return {"ok": False, "error": err.decode(...)[-400:]}`
                let text = String::from_utf8_lossy(&out.stderr).into_owned();
                Ok(Json(json!({"ok": false, "error": tail_chars(&text, 400)})).into_response())
            }
        }
        // A spawn failure (`ssh`/`bash` missing) is NOT caught in Python — it would
        // propagate to a 500. Surface the same shape rather than panicking.
        Err(_) => Err(HttpException::new(500, "Internal Server Error")),
    }
}

// ===========================================================================
// Local helpers (private mirrors of the cookbook_routes utilities)
// ===========================================================================

/// `"true"`/`"false"` — Python `str(bool)` is `"True"`/`"False"`, but the log line
/// interpolates the bool directly (`pty=%s`); CPython renders `True`/`False`.
fn py_bool(b: bool) -> &'static str {
    if b {
        "True"
    } else {
        "False"
    }
}

/// `shutil.which(name)` — first executable match on `PATH` (and CWD on Windows).
fn shutil_which(name: &str) -> Option<String> {
    if name.is_empty() {
        return None;
    }
    let path = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path) {
        let candidate = dir.join(name);
        if is_executable_file(&candidate) {
            return Some(candidate.to_string_lossy().into_owned());
        }
    }
    None
}

#[cfg(unix)]
fn is_executable_file(p: &std::path::Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    match std::fs::metadata(p) {
        Ok(m) => m.is_file() && (m.permissions().mode() & 0o111 != 0),
        Err(_) => false,
    }
}

#[cfg(not(unix))]
fn is_executable_file(p: &std::path::Path) -> bool {
    p.is_file()
}

/// `shlex.quote(s)` — POSIX shell single-quote escaping.
fn shquote(s: &str) -> String {
    if s.is_empty() {
        return "''".to_string();
    }
    // shlex's `_find_unsafe`: anything outside `[A-Za-z0-9_@%+=:,./-]` is unsafe.
    let safe = s
        .bytes()
        .all(|b| b.is_ascii_alphanumeric() || b"_@%+=:,./-".contains(&b));
    if safe {
        return s.to_string();
    }
    // `"'" + s.replace("'", "'\"'\"'") + "'"`
    format!("'{}'", s.replace('\'', "'\"'\"'"))
}

/// `repr(s)` for a Python `str` — choose `'`/`"` like CPython, escape control
/// chars + backslash + the chosen quote. Mirrors `cookbook_routes::py_repr`.
fn py_repr(s: &str) -> String {
    let has_single = s.contains('\'');
    let has_double = s.contains('"');
    // CPython prefers single quotes unless the string has a `'` and no `"`.
    let quote = if has_single && !has_double { '"' } else { '\'' };
    let mut out = String::with_capacity(s.len() + 2);
    out.push(quote);
    for ch in s.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || (c as u32) == 0x7f => {
                out.push_str(&format!("\\x{:02x}", c as u32));
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

/// Python truthiness for a JSON value (`bool(v)`).
fn value_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|x| x != 0.0).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `path.read_text(errors="replace")` — read bytes, lossy-decode.
fn read_text_replace(path: &std::path::Path) -> std::io::Result<String> {
    let bytes = std::fs::read(path)?;
    Ok(String::from_utf8_lossy(&bytes).into_owned())
}

/// `str.splitlines()` — split on the Python universal newline set, no trailing
/// empty element. For the tmux log (only `\n`/`\r\n`/`\r`) the common universal
/// set suffices; mirrors `str.splitlines()` semantics on those boundaries.
fn splitlines(s: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut cur = String::new();
    let bytes: Vec<char> = s.chars().collect();
    let mut i = 0;
    while i < bytes.len() {
        let c = bytes[i];
        match c {
            '\n' => {
                out.push(std::mem::take(&mut cur));
                i += 1;
            }
            '\r' => {
                out.push(std::mem::take(&mut cur));
                // Treat `\r\n` as one boundary.
                if i + 1 < bytes.len() && bytes[i + 1] == '\n' {
                    i += 2;
                } else {
                    i += 1;
                }
            }
            // Other Unicode line separators Python's splitlines also splits on.
            '\x0b' | '\x0c' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}' | '\u{2028}'
            | '\u{2029}' => {
                out.push(std::mem::take(&mut cur));
                i += 1;
            }
            other => {
                cur.push(other);
                i += 1;
            }
        }
    }
    if !cur.is_empty() {
        out.push(cur);
    }
    out
}

/// `await asyncio.wait_for(proc.communicate(), timeout=secs)` — run a configured
/// `Command` (already `sh -c …`), bounded by `secs`. `Ok(None)` on timeout (the
/// child is killed); `Err` on a launch failure.
async fn run_with_timeout(
    mut cmd: Command,
    secs: u64,
) -> std::io::Result<Option<std::process::Output>> {
    cmd.kill_on_drop(true);
    let mut child = cmd.spawn()?;
    let mut stdout_pipe = child.stdout.take();
    let mut stderr_pipe = child.stderr.take();
    let communicate = async {
        let mut out_buf: Vec<u8> = Vec::new();
        let mut err_buf: Vec<u8> = Vec::new();
        let read_out = async {
            if let Some(p) = stdout_pipe.as_mut() {
                let _ = p.read_to_end(&mut out_buf).await;
            }
        };
        let read_err = async {
            if let Some(p) = stderr_pipe.as_mut() {
                let _ = p.read_to_end(&mut err_buf).await;
            }
        };
        tokio::join!(read_out, read_err);
        let status = child.wait().await;
        (out_buf, err_buf, status)
    };
    match tokio::time::timeout(std::time::Duration::from_secs(secs), communicate).await {
        Ok((out_buf, err_buf, status)) => {
            let status = status?;
            Ok(Some(std::process::Output {
                status,
                stdout: out_buf,
                stderr: err_buf,
            }))
        }
        Err(_) => {
            let _ = child.start_kill();
            let _ = child.wait().await;
            Ok(None)
        }
    }
}

// ===========================================================================
// Tests — module-local, no AppState required (additive, parallel-safe)
// ===========================================================================

#[cfg(test)]
mod tests {
    use super::*;
    use futures_util::StreamExt;

    #[test]
    fn find_line_break_matches_python() {
        // No terminator.
        assert_eq!(find_line_break(b"abc"), (-1, 0));
        // `\n` only.
        assert_eq!(find_line_break(b"ab\ncd"), (2, 1));
        // `\r` only.
        assert_eq!(find_line_break(b"ab\rcd"), (2, 1));
        // `\r\n` -> 2-byte separator at the `\r`.
        assert_eq!(find_line_break(b"ab\r\ncd"), (2, 2));
        // `\n` before `\r` -> `\n` wins, 1-byte.
        assert_eq!(find_line_break(b"a\nb\rc"), (1, 1));
        // lone `\r` not immediately followed by `\n` -> 1-byte.
        assert_eq!(find_line_break(b"a\rb\nc"), (1, 1));
    }

    #[test]
    fn splitlines_universal_no_trailing_empty() {
        assert_eq!(splitlines("a\nb\n"), vec!["a", "b"]);
        assert_eq!(splitlines("a\r\nb"), vec!["a", "b"]);
        assert_eq!(splitlines("a\rb"), vec!["a", "b"]);
        assert_eq!(splitlines(""), Vec::<String>::new());
        assert_eq!(splitlines("solo"), vec!["solo"]);
    }

    #[test]
    fn shquote_matches_shlex() {
        assert_eq!(shquote(""), "''");
        assert_eq!(shquote("abc"), "abc");
        assert_eq!(shquote("a b"), "'a b'");
        assert_eq!(shquote("a'b"), "'a'\"'\"'b'");
        assert_eq!(shquote("/path/to/x.sh"), "/path/to/x.sh");
    }

    #[test]
    fn py_repr_matches_cpython() {
        assert_eq!(py_repr("abc"), "'abc'");
        assert_eq!(py_repr("a'b"), "\"a'b\"");
        assert_eq!(py_repr("a\"b"), "'a\"b'");
        assert_eq!(py_repr("a\nb"), "'a\\nb'");
    }

    #[test]
    fn require_admin_rejects_api_and_anonymous() {
        // A fresh (unconfigured) AuthManager — `is_admin(_)` is False for everyone.
        let auth = crate::core::auth::AuthManager::new();
        // None user -> 403.
        let e = require_admin_with_auth(&auth, None).unwrap_err();
        assert_eq!(e.status_code, 403);
        assert_eq!(e.detail, "Admin only");
        // `user == "api"` -> 403.
        assert_eq!(require_admin_with_auth(&auth, Some("api")).unwrap_err().status_code, 403);
        // Empty -> 403.
        assert_eq!(require_admin_with_auth(&auth, Some("")).unwrap_err().status_code, 403);
        // A non-admin (unknown) user -> 403 via `is_admin` False.
        assert_eq!(require_admin_with_auth(&auth, Some("bob")).unwrap_err().status_code, 403);
        // `internal-tool` -> allow.
        assert!(require_admin_with_auth(&auth, Some("internal-tool")).is_ok());
    }

    #[test]
    fn reject_cross_site_only_blocks_cross_site() {
        // No header -> allow.
        let empty = HeaderMap::new();
        assert!(reject_cross_site(&empty).is_ok());
        // `sec-fetch-site: same-origin` -> allow.
        let mut same = HeaderMap::new();
        same.insert("sec-fetch-site", "same-origin".parse().unwrap());
        assert!(reject_cross_site(&same).is_ok());
        // `sec-fetch-site: none` -> allow.
        let mut none = HeaderMap::new();
        none.insert("sec-fetch-site", "none".parse().unwrap());
        assert!(reject_cross_site(&none).is_ok());
        // `sec-fetch-site: cross-site` -> 403.
        let mut cross = HeaderMap::new();
        cross.insert("sec-fetch-site", "cross-site".parse().unwrap());
        let e = reject_cross_site(&cross).unwrap_err();
        assert_eq!(e.status_code, 403);
        assert_eq!(e.detail, "Cross-site request rejected");
    }

    #[test]
    fn ssh_port_re_matches_python() {
        assert!(ssh_port_re_match("1"));
        assert!(ssh_port_re_match("22"));
        assert!(ssh_port_re_match("65535"));
        // 1..=5 digits only.
        assert!(!ssh_port_re_match(""));
        assert!(!ssh_port_re_match("123456")); // 6 digits.
        assert!(!ssh_port_re_match("-1"));
        assert!(!ssh_port_re_match("22a"));
        assert!(!ssh_port_re_match(" 22"));
    }

    #[test]
    fn safe_venv_re_matches_python() {
        assert!(safe_venv_re_match("~/venv"));
        assert!(safe_venv_re_match("/opt/py-3.11/env"));
        assert!(safe_venv_re_match("a_b.c-d/e"));
        assert!(!safe_venv_re_match("")); // `+` requires at least one char.
        assert!(!safe_venv_re_match("with space"));
        assert!(!safe_venv_re_match("rm; rm")); // `;` and space are unsafe.
        assert!(!safe_venv_re_match("a$b"));
    }

    #[test]
    fn ssh_base_argv_builds_validated_vector() {
        // Default port (None) -> no `-p`.
        assert_eq!(
            ssh_base_argv("gpu-box", None).unwrap(),
            vec!["ssh", "-o", "ConnectTimeout=6", "-o", "StrictHostKeyChecking=no", "gpu-box"]
        );
        // Port 22 is treated as default -> no `-p`.
        assert_eq!(
            ssh_base_argv("gpu-box", Some("22")).unwrap(),
            vec!["ssh", "-o", "ConnectTimeout=6", "-o", "StrictHostKeyChecking=no", "gpu-box"]
        );
        // Non-default valid port -> `-p PORT`.
        assert_eq!(
            ssh_base_argv("gpu-box", Some("8022")).unwrap(),
            vec![
                "ssh", "-o", "ConnectTimeout=6", "-o", "StrictHostKeyChecking=no", "-p", "8022",
                "gpu-box"
            ]
        );
        // The host is the LAST argv element, never shell-parsed (injection-safe):
        // a host with spaces/`;` survives as a single argv entry.
        let evil = ssh_base_argv("h; rm -rf /", None).unwrap();
        assert_eq!(evil.last().unwrap(), "h; rm -rf /");
        // Empty / whitespace / `-`-leading host -> ValueError.
        assert_eq!(ssh_base_argv("", None).unwrap_err(), "invalid ssh host");
        assert_eq!(ssh_base_argv("   ", None).unwrap_err(), "invalid ssh host");
        assert_eq!(ssh_base_argv("-oProxyCommand=x", None).unwrap_err(), "invalid ssh host");
        // Bad ports -> ValueError.
        assert_eq!(ssh_base_argv("h", Some("0")).unwrap_err(), "invalid ssh port");
        assert_eq!(ssh_base_argv("h", Some("70000")).unwrap_err(), "invalid ssh port");
        assert_eq!(ssh_base_argv("h", Some("2a")).unwrap_err(), "invalid ssh port");
    }

    #[test]
    fn venv_activate_prefix_matches_python() {
        // None / empty -> "".
        assert_eq!(venv_activate_prefix(None).unwrap(), "");
        assert_eq!(venv_activate_prefix(Some("")).unwrap(), "");
        // Already ends with /bin/activate -> used as-is.
        assert_eq!(
            venv_activate_prefix(Some("~/venv/bin/activate")).unwrap(),
            ". ~/venv/bin/activate && "
        );
        // Otherwise rstrip('/') + /bin/activate.
        assert_eq!(
            venv_activate_prefix(Some("~/venv/")).unwrap(),
            ". ~/venv/bin/activate && "
        );
        assert_eq!(
            venv_activate_prefix(Some("/opt/env")).unwrap(),
            ". /opt/env/bin/activate && "
        );
        // Unsafe path -> ValueError.
        assert_eq!(venv_activate_prefix(Some("a; rm")).unwrap_err(), "invalid venv path");
    }

    #[test]
    fn tail_chars_keeps_last_n_code_points() {
        assert_eq!(tail_chars("abcdef", 3), "def");
        assert_eq!(tail_chars("abc", 10), "abc"); // shorter than bound.
        assert_eq!(tail_chars("", 5), "");
        assert_eq!(tail_chars("abc", 0), "");
        // Code-point aware (not byte-aware): keep whole multi-byte chars.
        assert_eq!(tail_chars("aé😀", 2), "é😀");
    }

    #[test]
    fn llama_cpp_rebuild_cmd_matches_python() {
        let cmd = llama_cpp_rebuild_cmd();
        assert_eq!(
            cmd,
            "mkdir -p \"$HOME/bin\" && \
             rm -f \"$HOME/bin/llama-server\" && \
             rm -rf \"$HOME/llama.cpp/build\" && \
             echo \"[odysseus] Cleared the cached llama.cpp build. \
             Re-launch the serve task to rebuild llama-server from source \
             (CUDA or HIP will be used if a toolchain is now available).\""
        );
    }

    #[test]
    fn install_allow_list_includes_diffusers_torch_and_vllm() {
        // Change #4: the install allow-list gained `diffusers[torch]` and `vllm`.
        assert!(INSTALL_ALLOW_LIST.contains(&"diffusers[torch]"));
        assert!(INSTALL_ALLOW_LIST.contains(&"vllm"));
        // Pre-existing members still present (no regression).
        assert!(INSTALL_ALLOW_LIST.contains(&"diffusers"));
        assert!(INSTALL_ALLOW_LIST.contains(&"hdbscan"));
        // Not an arbitrary-install bypass.
        assert!(!INSTALL_ALLOW_LIST.contains(&"requests"));
    }

    #[tokio::test]
    async fn exec_shell_captures_stdout_and_exit() {
        let r = exec_shell("printf 'hi'", 30).await;
        assert_eq!(r.get("stdout").and_then(Value::as_str), Some("hi"));
        assert_eq!(r.get("stderr").and_then(Value::as_str), Some(""));
        assert_eq!(r.get("exit_code").and_then(Value::as_i64), Some(0));
    }

    #[tokio::test]
    async fn exec_shell_times_out() {
        let r = exec_shell("sleep 5", 1).await;
        assert_eq!(r.get("exit_code").and_then(Value::as_i64), Some(-1));
        assert_eq!(
            r.get("stderr").and_then(Value::as_str),
            Some("Command timed out after 1s")
        );
        assert_eq!(r.get("stdout").and_then(Value::as_str), Some(""));
    }

    #[tokio::test]
    async fn generate_plain_streams_lines_then_exit() {
        let s = generate_plain("printf 'a\\nb\\n'".to_string(), 120);
        tokio::pin!(s);
        let mut stdout_lines: Vec<String> = Vec::new();
        let mut exit: Option<i64> = None;
        while let Some(ev) = s.next().await {
            let bytes = ev.unwrap();
            let text = String::from_utf8_lossy(&bytes);
            let payload = text.trim_start_matches("data: ").trim();
            let v: Value = serde_json::from_str(payload).unwrap();
            if v.get("stream").and_then(Value::as_str) == Some("stdout") {
                if let Some(d) = v.get("data").and_then(Value::as_str) {
                    stdout_lines.push(d.to_string());
                }
            }
            if let Some(c) = v.get("exit_code").and_then(Value::as_i64) {
                exit = Some(c);
            }
        }
        assert_eq!(stdout_lines, vec!["a".to_string(), "b".to_string()]);
        assert_eq!(exit, Some(0));
    }

    #[tokio::test]
    async fn generate_plain_splits_on_carriage_return() {
        // Progress-bar style `\r` updates must each become their own line.
        let s = generate_plain("printf 'x\\ry\\rz\\n'".to_string(), 120);
        tokio::pin!(s);
        let mut lines: Vec<String> = Vec::new();
        while let Some(ev) = s.next().await {
            let bytes = ev.unwrap();
            let text = String::from_utf8_lossy(&bytes);
            let payload = text.trim_start_matches("data: ").trim();
            let v: Value = serde_json::from_str(payload).unwrap();
            if let Some(d) = v.get("data").and_then(Value::as_str) {
                if v.get("stream").and_then(Value::as_str) == Some("stdout") {
                    lines.push(d.to_string());
                }
            }
        }
        assert_eq!(lines, vec!["x".to_string(), "y".to_string(), "z".to_string()]);
    }

    #[tokio::test]
    async fn generate_plain_times_out() {
        let s = generate_plain("sleep 5".to_string(), 1);
        tokio::pin!(s);
        let mut saw_timeout = false;
        let mut exit: Option<i64> = None;
        while let Some(ev) = s.next().await {
            let bytes = ev.unwrap();
            let text = String::from_utf8_lossy(&bytes);
            let payload = text.trim_start_matches("data: ").trim();
            let v: Value = serde_json::from_str(payload).unwrap();
            if v.get("data").and_then(Value::as_str) == Some("Command timed out after 1s") {
                saw_timeout = true;
            }
            if let Some(c) = v.get("exit_code").and_then(Value::as_i64) {
                exit = Some(c);
            }
        }
        assert!(saw_timeout);
        assert_eq!(exit, Some(-1));
    }

    #[test]
    fn instant_plus_signed_secs_positive_and_negative() {
        let base = tokio::time::Instant::now();
        // Positive seconds move FORWARD.
        assert!(instant_plus_signed_secs(base, 5) > base);
        // Zero is a no-op.
        assert_eq!(instant_plus_signed_secs(base, 0), base);
        // NEGATIVE seconds move BACKWARD -> an already-past deadline. The buggy
        // `secs as u64` cast would wrap huge and push it far into the future; the
        // helper instead lands strictly before `base` (so `now >= deadline` fires).
        let past = instant_plus_signed_secs(base, -5);
        assert!(past < base);
    }

    #[tokio::test]
    async fn generate_plain_negative_timeout_fires_immediately() {
        // A negative override makes the deadline already-past -> times out at once,
        // even though the command would otherwise run for 5s.
        let s = generate_plain("sleep 5".to_string(), -1);
        tokio::pin!(s);
        let mut saw_timeout = false;
        let mut exit: Option<i64> = None;
        // Bound the test so a regression (never firing) fails fast instead of hanging.
        let run = async {
            while let Some(ev) = s.next().await {
                let bytes = ev.unwrap();
                let text = String::from_utf8_lossy(&bytes);
                let payload = text.trim_start_matches("data: ").trim();
                let v: Value = serde_json::from_str(payload).unwrap();
                if v.get("data").and_then(Value::as_str) == Some("Command timed out after -1s") {
                    saw_timeout = true;
                }
                if let Some(c) = v.get("exit_code").and_then(Value::as_i64) {
                    exit = Some(c);
                }
            }
        };
        tokio::time::timeout(std::time::Duration::from_secs(3), run)
            .await
            .expect("negative timeout must fire promptly, not wrap huge");
        assert!(saw_timeout);
        assert_eq!(exit, Some(-1));
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn generate_pty_streams_lines_then_exit() {
        let s = generate_pty("printf 'a\\nb\\n'".to_string(), 120);
        tokio::pin!(s);
        let mut stdout_lines: Vec<String> = Vec::new();
        let mut exit: Option<i64> = None;
        while let Some(ev) = s.next().await {
            let bytes = ev.unwrap();
            let text = String::from_utf8_lossy(&bytes);
            let payload = text.trim_start_matches("data: ").trim();
            let v: Value = serde_json::from_str(payload).unwrap();
            if v.get("stream").and_then(Value::as_str) == Some("stdout") {
                if let Some(d) = v.get("data").and_then(Value::as_str) {
                    stdout_lines.push(d.to_string());
                }
            }
            if let Some(c) = v.get("exit_code").and_then(Value::as_i64) {
                exit = Some(c);
            }
        }
        // A PTY echoes input, but with no stdin written the only output is the two
        // printed lines. The terminal may carry trailing `\r`, which the line
        // splitter drops, so each line arrives as its own non-empty stdout event.
        assert!(stdout_lines.contains(&"a".to_string()), "lines: {stdout_lines:?}");
        assert!(stdout_lines.contains(&"b".to_string()), "lines: {stdout_lines:?}");
        assert_eq!(exit, Some(0));
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn generate_pty_negative_timeout_fires_immediately() {
        let s = generate_pty("sleep 5".to_string(), -1);
        tokio::pin!(s);
        let mut saw_timeout = false;
        let mut exit: Option<i64> = None;
        let run = async {
            while let Some(ev) = s.next().await {
                let bytes = ev.unwrap();
                let text = String::from_utf8_lossy(&bytes);
                let payload = text.trim_start_matches("data: ").trim();
                let v: Value = serde_json::from_str(payload).unwrap();
                if v.get("data").and_then(Value::as_str) == Some("Command timed out after -1s") {
                    saw_timeout = true;
                }
                if let Some(c) = v.get("exit_code").and_then(Value::as_i64) {
                    exit = Some(c);
                }
            }
        };
        tokio::time::timeout(std::time::Duration::from_secs(3), run)
            .await
            .expect("negative pty timeout must fire promptly, not wrap huge");
        assert!(saw_timeout);
        assert_eq!(exit, Some(-1));
    }
}
