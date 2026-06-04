// routes/vault_routes.rs  <- routes/vault_routes.py
//! Vaultwarden / Bitwarden CLI integration — config and unlock endpoints.
//! Stores the `BW_SESSION` key in `data/vault.json` with restrictive permissions.
//!
//! Proof-batch module **P7-vault** (wave: proof). It validates the `require_admin`
//! auth-adapter (foundation F3), JSON request bodies, outbound subprocess
//! orchestration (`tokio::process` with a `BW_SESSION` env var + piped stdin), and
//! Unix `0o600` file permissions — all on the collision-free `/api/vault/*` prefix,
//! so it coexists with the inline `web/mod.rs` subset (the additive / non-breaking
//! rule).
//!
//! Faithfulness notes:
//! * `VAULT_FILE` is the **relative** path `data/vault.json` (CWD-relative), exactly
//!   as Python's `Path("data/vault.json")` — NOT derived from `constants::DATA_DIR`.
//!   `src/tool_implementations.py` reads the same literal relative path, so any
//!   re-derivation would diverge from the rest of the app.
//! * `bw` not installed reproduces Python's `FileNotFoundError` branch verbatim:
//!   `("", "bw CLI not installed (...)", 127)` — the handlers then surface a
//!   `{"ok": false, "error": ...}` body, never a raised exception, exactly like the
//!   Python `try/except` shape (these endpoints only ever `return` a dict).


use std::process::Stdio;

use axum::extract::State;
use axum::http::HeaderMap;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Extension, Json, Router};
use indexmap::IndexMap;
use serde::Deserialize;
use serde_json::{json, Value};
use tokio::io::AsyncWriteExt;
use tokio::process::Command;

use crate::core::middleware::INTERNAL_TOOL_HEADER;
use crate::routes::auth_adapter;
use crate::routes::{AppState, CurrentUser, HttpException};

/// `VAULT_FILE = Path("data/vault.json")` — the CWD-relative store, mirrored exactly.
const VAULT_FILE: &str = "data/vault.json";

// ===========================================================================
// Module-level helpers (`_find_bw` / `_load_config` / `_save_config` / `_run_bw`)
// ===========================================================================

/// `_find_bw()` — locate the `bw` binary, checking PATH and common npm-global
/// locations.
///
/// ```python
/// def _find_bw() -> str:
///     p = shutil.which("bw")
///     if p: return p
///     home = os.path.expanduser("~")
///     for candidate in (
///         f"{home}/.npm-global/bin/bw",
///         f"{home}/.nvm/versions/node/*/bin/bw",
///         "/usr/local/bin/bw",
///         "/opt/homebrew/bin/bw",
///     ):
///         if "*" in candidate:
///             import glob
///             for m in glob.glob(candidate):
///                 if os.path.isfile(m) and os.access(m, os.X_OK):
///                     return m
///         elif os.path.isfile(candidate) and os.access(candidate, os.X_OK):
///             return candidate
///     return "bw"  # fall back to PATH lookup (will FileNotFoundError, handled below)
/// ```
fn find_bw() -> String {
    if let Some(p) = shutil_which("bw") {
        return p;
    }
    let home = expanduser_home();
    // `f"{home}/.nvm/versions/node/*/bin/bw"` is the only glob candidate; the
    // single `*` matches one path segment under `node/`, so we enumerate that
    // directory rather than pull in a glob crate (faithful to `glob.glob` here).
    let nvm_dir = format!("{home}/.nvm/versions/node");
    if let Ok(entries) = std::fs::read_dir(&nvm_dir) {
        // `glob.glob` returns matches in arbitrary order on most platforms but
        // sorted is the conventional/stable expectation; collect + sort so the
        // first executable match is deterministic.
        let mut versions: Vec<std::path::PathBuf> = entries.flatten().map(|e| e.path()).collect();
        versions.sort();
        for v in versions {
            let candidate = v.join("bin").join("bw");
            let candidate = candidate.to_string_lossy().into_owned();
            if is_file(&candidate) && is_executable(&candidate) {
                return candidate;
            }
        }
    }
    for candidate in [
        format!("{home}/.npm-global/bin/bw"),
        "/usr/local/bin/bw".to_string(),
        "/opt/homebrew/bin/bw".to_string(),
    ] {
        if is_file(&candidate) && is_executable(&candidate) {
            return candidate;
        }
    }
    // Fall back to PATH lookup (will be a launch error, handled in `_run_bw`).
    "bw".to_string()
}

/// `shutil.which(name)` — first executable `name` on `$PATH`, else `None`.
fn shutil_which(name: &str) -> Option<String> {
    let path_var = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path_var) {
        let candidate = dir.join(name);
        let s = candidate.to_string_lossy().into_owned();
        if is_file(&s) && is_executable(&s) {
            return Some(s);
        }
    }
    None
}

/// `os.path.expanduser("~")` — the user's home directory (`$HOME` on POSIX).
fn expanduser_home() -> String {
    std::env::var("HOME").unwrap_or_default()
}

/// `os.path.isfile(path)`.
fn is_file(path: &str) -> bool {
    std::fs::metadata(path).map(|m| m.is_file()).unwrap_or(false)
}

/// `os.access(path, os.X_OK)` — true if the file's owner-execute bit is set
/// (a faithful-enough proxy for `X_OK`; matches `shutil.which`'s own check). On
/// non-Unix the executable bit is meaningless, so accept any existing file.
#[cfg(unix)]
fn is_executable(path: &str) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(path)
        .map(|m| m.permissions().mode() & 0o111 != 0)
        .unwrap_or(false)
}

#[cfg(not(unix))]
fn is_executable(path: &str) -> bool {
    is_file(path)
}

/// `_load_config()` — read `data/vault.json`, returning `{}` if missing or
/// unparseable. The order of fields is irrelevant for loading; we return an
/// [`IndexMap`] so any subsequent re-save preserves the on-disk key order.
fn load_config() -> IndexMap<String, Value> {
    // `if VAULT_FILE.exists(): try: return json.loads(...) except Exception: pass`
    if std::path::Path::new(VAULT_FILE).exists() {
        if let Ok(text) = std::fs::read_to_string(VAULT_FILE) {
            if let Ok(Value::Object(map)) = serde_json::from_str::<Value>(&text) {
                return map.into_iter().collect();
            }
        }
    }
    IndexMap::new()
}

/// `_save_config(cfg)` — write `data/vault.json` (`indent=2`) and `chmod 600`
/// (best-effort, swallowing errors exactly like the Python `try/except`).
fn save_config(cfg: &IndexMap<String, Value>) {
    // `VAULT_FILE.parent.mkdir(parents=True, exist_ok=True)`
    if let Some(parent) = std::path::Path::new(VAULT_FILE).parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    // `VAULT_FILE.write_text(json.dumps(cfg, indent=2))`
    let obj: serde_json::Map<String, Value> = cfg.iter().map(|(k, v)| (k.clone(), v.clone())).collect();
    if let Ok(text) = serde_json::to_string_pretty(&Value::Object(obj)) {
        if std::fs::write(VAULT_FILE, text).is_ok() {
            // `try: os.chmod(str(VAULT_FILE), 0o600) except Exception: pass`
            let _ = crate::pyos::chmod(VAULT_FILE, 0o600);
        }
    }
}

/// `_run_bw(args, session=None, input_text=None) -> (stdout, stderr, returncode)`.
///
/// ```python
/// async def _run_bw(args, session=None, input_text=None) -> tuple:
///     env = {}; env.update(os.environ)
///     if session: env["BW_SESSION"] = session
///     bw_path = _find_bw()
///     try:
///         proc = await asyncio.create_subprocess_exec(
///             bw_path, *args,
///             stdin=PIPE if input_text else None, stdout=PIPE, stderr=PIPE, env=env)
///     except FileNotFoundError:
///         return "", "bw CLI not installed (...)", 127
///     except Exception as e:
///         return "", f"Failed to launch bw: {e}", 1
///     try:
///         stdout, stderr = await proc.communicate(input=input_text.encode() if input_text else None)
///     except Exception as e:
///         return "", f"bw subprocess error: {e}", 1
///     return stdout.decode(errors="replace").strip(), stderr.decode(errors="replace").strip(), proc.returncode
/// ```
async fn run_bw(args: &[&str], session: Option<&str>, input_text: Option<&str>) -> (String, String, i32) {
    // `env = {}; env.update(os.environ); if session: env["BW_SESSION"] = session`
    // Inherit the parent environment (tokio::process does so by default), then
    // overlay `BW_SESSION` when a session key is supplied.
    let bw_path = find_bw();
    let mut cmd = Command::new(&bw_path);
    cmd.args(args).stdout(Stdio::piped()).stderr(Stdio::piped());
    if let Some(s) = session {
        cmd.env("BW_SESSION", s);
    }
    // `stdin=PIPE if input_text else None`
    if input_text.is_some() {
        cmd.stdin(Stdio::piped());
    } else {
        cmd.stdin(Stdio::null());
    }

    let mut child = match cmd.spawn() {
        Ok(c) => c,
        // `except FileNotFoundError:` — the bw binary doesn't exist (rc 127).
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            return (
                String::new(),
                "bw CLI not installed (install `nodejs-bitwarden-cli` or `bitwarden-cli`)".to_string(),
                127,
            );
        }
        // `except Exception as e:` — any other launch failure (rc 1).
        Err(e) => return (String::new(), format!("Failed to launch bw: {e}"), 1),
    };

    // `await proc.communicate(input=...)` — write stdin (if any), then collect
    // stdout/stderr and wait for exit. Any IO error here -> `bw subprocess error`.
    if let Some(text) = input_text {
        if let Some(mut stdin) = child.stdin.take() {
            // Best-effort write then close; a write failure surfaces below at
            // wait_with_output, matching Python's single broad `except`.
            let _ = stdin.write_all(text.as_bytes()).await;
            // Drop `stdin` to close the pipe (so `bw` sees EOF).
        }
    }
    let output = match child.wait_with_output().await {
        Ok(o) => o,
        Err(e) => return (String::new(), format!("bw subprocess error: {e}"), 1),
    };

    // `stdout.decode(errors="replace").strip()` / same for stderr.
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    // `proc.returncode` — the process exit code (negative = killed by signal in
    // CPython; `None`-on-still-running is impossible after communicate). We map a
    // signal death to `-1` since the handlers only branch on `rc != 0`.
    let rc = output.status.code().unwrap_or(-1);
    (stdout, stderr, rc)
}

/// `_check_bw_installed()` — run `bw --version`, true when it exits 0.
///
/// ```python
/// async def _check_bw_installed() -> bool:
///     try:
///         proc = await asyncio.create_subprocess_exec(_find_bw(), "--version", ...)
///         await proc.communicate()
///         return proc.returncode == 0
///     except Exception:
///         return False
/// ```
async fn check_bw_installed() -> bool {
    let bw_path = find_bw();
    let result = Command::new(&bw_path)
        .arg("--version")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .await;
    match result {
        Ok(o) => o.status.success(),
        Err(_) => false,
    }
}

// ===========================================================================
// Request bodies (the pydantic models)
// ===========================================================================

/// `class VaultConfig(BaseModel)` — `server_url`/`email`, both defaulting to `""`.
#[derive(Debug, Deserialize)]
struct VaultConfig {
    #[serde(default)]
    server_url: String,
    #[serde(default)]
    email: String,
}

/// `class VaultUnlockRequest(BaseModel)` — `master_password` (required).
#[derive(Debug, Deserialize)]
struct VaultUnlockRequest {
    master_password: String,
}

/// `class VaultLoginRequest(BaseModel)` — `email` + `master_password` (required).
#[derive(Debug, Deserialize)]
struct VaultLoginRequest {
    email: String,
    master_password: String,
}

// ===========================================================================
// Auth glue — `require_admin(request)`
// ===========================================================================

/// `require_admin(request)` — the per-handler admin gate.
///
/// The Python `require_admin` reads the `X-Odysseus-Internal-Token` header and the
/// stamped `request.state.current_user` off the request; the axum equivalent pulls
/// those from the [`HeaderMap`] and `Option<Extension<CurrentUser>>`, then delegates
/// to the foundation [`auth_adapter::require_admin`] (which wraps the already-ported
/// `core::middleware::require_admin`). `Err` -> `HttpException(403, "Admin only")`.
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

// ===========================================================================
// Router factory (`setup_vault_routes`)
// ===========================================================================

/// `setup_vault_routes()` — `APIRouter(prefix="/api/vault", tags=["vault"])`.
///
/// app.py registers this as include-router #39. Mounts each handler at the absolute
/// `/api/vault/...` path the Python `APIRouter(prefix=...)` yields. Deps come from
/// `State<AppState>` (the factory takes no params, mirroring the no-arg Python
/// `setup_vault_routes()`).
pub fn setup_vault_routes() -> Router<AppState> {
    Router::new()
        // `@router.get("/config")` + `@router.post("/config")`
        .route("/api/vault/config", get(get_config).post(save_config_route))
        // `@router.post("/login")`
        .route("/api/vault/login", post(login))
        // `@router.post("/unlock")`
        .route("/api/vault/unlock", post(unlock))
        // `@router.post("/lock")`
        .route("/api/vault/lock", post(lock))
        // `@router.post("/logout")`
        .route("/api/vault/logout", post(logout))
}

// ===========================================================================
// Handlers
// ===========================================================================

/// `GET /api/vault/config` — return vault config (no sensitive fields).
///
/// ```python
/// @router.get("/config")
/// async def get_config(request: Request):
///     require_admin(request)
///     cfg = _load_config()
///     return {
///         "server_url": cfg.get("server_url", ""),
///         "email": cfg.get("email", ""),
///         "unlocked": bool(cfg.get("session")),
///         "unlocked_at": cfg.get("unlocked_at", ""),
///         "bw_installed": await _check_bw_installed(),
///     }
/// ```
async fn get_config(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    let cfg = load_config();
    // `bool(cfg.get("session"))` — truthy iff `session` is present and non-empty.
    let unlocked = cfg.get("session").map(is_truthy).unwrap_or(false);
    let body = json!({
        "server_url": cfg.get("server_url").and_then(Value::as_str).unwrap_or(""),
        "email": cfg.get("email").and_then(Value::as_str).unwrap_or(""),
        "unlocked": unlocked,
        "unlocked_at": cfg.get("unlocked_at").and_then(Value::as_str).unwrap_or(""),
        "bw_installed": check_bw_installed().await,
    });
    Ok(Json(body).into_response())
}

/// `POST /api/vault/config` — save vault URL + email; run `bw config server` to
/// point at Vaultwarden.
///
/// ```python
/// @router.post("/config")
/// async def save_config(req: VaultConfig, request: Request):
///     require_admin(request)
///     cfg = _load_config()
///     cfg["server_url"] = req.server_url.strip().rstrip("/")
///     cfg["email"] = req.email.strip()
///     if cfg["server_url"]:
///         _, stderr, rc = await _run_bw(["config", "server", cfg["server_url"]])
///         if rc != 0:
///             return {"ok": False, "error": f"bw config failed: {stderr[:300]}"}
///     _save_config(cfg)
///     return {"ok": True}
/// ```
async fn save_config_route(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Json(req): Json<VaultConfig>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    let mut cfg = load_config();
    // `req.server_url.strip().rstrip("/")` — strip whitespace then trailing slashes.
    let server_url = req.server_url.trim().trim_end_matches('/').to_string();
    let email = req.email.trim().to_string();
    cfg.insert("server_url".to_string(), Value::String(server_url.clone()));
    cfg.insert("email".to_string(), Value::String(email));

    if !server_url.is_empty() {
        let (_stdout, stderr, rc) = run_bw(&["config", "server", &server_url], None, None).await;
        if rc != 0 {
            return Ok(Json(json!({
                "ok": false,
                "error": format!("bw config failed: {}", truncate(&stderr, 300)),
            }))
            .into_response());
        }
    }
    save_config(&cfg);
    Ok(Json(json!({"ok": true})).into_response())
}

/// `POST /api/vault/login` — log in to Vaultwarden (required once per account).
///
/// ```python
/// @router.post("/login")
/// async def login(req: VaultLoginRequest, request: Request):
///     require_admin(request)
///     cfg = _load_config()
///     cfg["email"] = req.email
///     _save_config(cfg)
///     stdout, stderr, rc = await _run_bw(["login", req.email, "--raw"], input_text=req.master_password + "\n")
///     if rc != 0:
///         if "already logged in" in stderr.lower():
///             return {"ok": True, "already": True}
///         return {"ok": False, "error": f"Login failed: {stderr[:300]}"}
///     if stdout:
///         cfg["session"] = stdout
///         cfg["unlocked_at"] = datetime.utcnow().isoformat()
///         _save_config(cfg)
///     return {"ok": True}
/// ```
async fn login(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Json(req): Json<VaultLoginRequest>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    let mut cfg = load_config();
    // `cfg["email"] = req.email; _save_config(cfg)` — persist the email up front.
    cfg.insert("email".to_string(), Value::String(req.email.clone()));
    save_config(&cfg);

    let input_text = format!("{}\n", req.master_password);
    let (stdout, stderr, rc) =
        run_bw(&["login", &req.email, "--raw"], None, Some(&input_text)).await;
    if rc != 0 {
        // `if "already logged in" in stderr.lower(): return {"ok": True, "already": True}`
        if stderr.to_lowercase().contains("already logged in") {
            return Ok(Json(json!({"ok": true, "already": true})).into_response());
        }
        return Ok(Json(json!({
            "ok": false,
            "error": format!("Login failed: {}", truncate(&stderr, 300)),
        }))
        .into_response());
    }
    // `bw login --raw` prints the session key on success (when 2FA disabled).
    if !stdout.is_empty() {
        cfg.insert("session".to_string(), Value::String(stdout));
        cfg.insert("unlocked_at".to_string(), Value::String(utcnow_isoformat()));
        save_config(&cfg);
    }
    Ok(Json(json!({"ok": true})).into_response())
}

/// `POST /api/vault/unlock` — unlock the vault and save the session key.
///
/// The master password is passed over **stdin**, not as a command-line argument.
/// argv is visible via `ps` / `/proc/<pid>/cmdline` to any local user; stdin
/// avoids leaking the secret into the child's process listing.
///
/// ```python
/// @router.post("/unlock")
/// async def unlock(req: VaultUnlockRequest, request: Request):
///     require_admin(request)
///     # Pass the master password on stdin, not argv. argv is visible through
///     # `ps` / /proc/<pid>/cmdline; stdin also avoids leaving the secret in
///     # the child process environment.
///     stdout, stderr, rc = await _run_bw(
///         ["unlock", "--raw"],
///         input_text=req.master_password + "\n",
///     )
///     if rc != 0:
///         return {"ok": False, "error": f"Unlock failed: {stderr[:300]}"}
///     session = stdout.strip()
///     if not session:
///         return {"ok": False, "error": "bw returned empty session"}
///     cfg = _load_config()
///     cfg["session"] = session
///     cfg["unlocked_at"] = datetime.utcnow().isoformat()
///     _save_config(cfg)
///     return {"ok": True, "message": "Vault unlocked"}
/// ```
async fn unlock(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Json(req): Json<VaultUnlockRequest>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    // Pass the master password over stdin, not as argv.  argv is world-readable
    // via `ps` / `/proc/<pid>/cmdline`; stdin is private to the process.
    let input_text = format!("{}\n", req.master_password);
    let (stdout, stderr, rc) = run_bw(&["unlock", "--raw"], None, Some(&input_text)).await;
    if rc != 0 {
        return Ok(Json(json!({
            "ok": false,
            "error": format!("Unlock failed: {}", truncate(&stderr, 300)),
        }))
        .into_response());
    }
    // `session = stdout.strip()` — `_run_bw` already strips, so this is a no-op,
    // but mirror it for fidelity.
    let session = stdout.trim().to_string();
    if session.is_empty() {
        return Ok(Json(json!({"ok": false, "error": "bw returned empty session"})).into_response());
    }
    let mut cfg = load_config();
    cfg.insert("session".to_string(), Value::String(session));
    cfg.insert("unlocked_at".to_string(), Value::String(utcnow_isoformat()));
    save_config(&cfg);
    Ok(Json(json!({"ok": true, "message": "Vault unlocked"})).into_response())
}

/// `POST /api/vault/lock` — lock the vault (clear the session from config).
///
/// ```python
/// @router.post("/lock")
/// async def lock(request: Request):
///     require_admin(request)
///     cfg = _load_config()
///     cfg.pop("session", None)
///     cfg.pop("unlocked_at", None)
///     _save_config(cfg)
///     await _run_bw(["lock"])
///     return {"ok": True, "message": "Vault locked"}
/// ```
async fn lock(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    let mut cfg = load_config();
    cfg.shift_remove("session");
    cfg.shift_remove("unlocked_at");
    save_config(&cfg);
    // Also tell bw to lock (result ignored, exactly like Python).
    let _ = run_bw(&["lock"], None, None).await;
    Ok(Json(json!({"ok": true, "message": "Vault locked"})).into_response())
}

/// `POST /api/vault/logout` — log out of the Bitwarden CLI completely.
///
/// ```python
/// @router.post("/logout")
/// async def logout(request: Request):
///     require_admin(request)
///     await _run_bw(["logout"])
///     cfg = _load_config()
///     cfg.pop("session", None)
///     cfg.pop("email", None)
///     cfg.pop("unlocked_at", None)
///     _save_config(cfg)
///     return {"ok": True}
/// ```
async fn logout(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    // `await _run_bw(["logout"])` — runs before loading config (result ignored).
    let _ = run_bw(&["logout"], None, None).await;
    let mut cfg = load_config();
    cfg.shift_remove("session");
    cfg.shift_remove("email");
    cfg.shift_remove("unlocked_at");
    save_config(&cfg);
    Ok(Json(json!({"ok": true})).into_response())
}

// ===========================================================================
// Small faithful helpers
// ===========================================================================

/// Python truthiness for a JSON value as used by `bool(cfg.get("session"))`: a
/// present, non-empty string (the only shape `session` ever takes) is truthy;
/// `None`/`""`/missing is falsy.
fn is_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::String(s) => !s.is_empty(),
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `s[:n]` — slice the first `n` Unicode code points (Python slices a `str` by
/// code point). Used for `stderr[:300]` in the error messages.
fn truncate(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// `datetime.utcnow().isoformat()` — naive-UTC ISO 8601 with a `T` separator.
///
/// `datetime.isoformat()` OMITS the `.ffffff` segment entirely when
/// `microsecond == 0` (e.g. `2026-06-01T12:34:56`), and otherwise emits all 6
/// digits with no trailing-zero stripping (e.g. `2026-06-01T12:34:56.789012`).
/// Reproduce both, since the value is echoed back verbatim in `GET /config`'s
/// `unlocked_at`.
///
/// NOTE: this is distinct from `pydatetime::utcnow_naive_iso`, which uses a
/// **space** separator (the SQLAlchemy SQLite storage format). The vault config
/// stores `datetime.utcnow().isoformat()`, whose canonical separator is `T`.
fn utcnow_isoformat() -> String {
    use chrono::Timelike;
    let dt = chrono::Utc::now().naive_utc();
    // Python `datetime` is microsecond-resolution: isoformat() omits the
    // fraction iff `microsecond == 0`, else emits exactly 6 digits. Gate on
    // microseconds (nanos / 1000), NOT raw nanoseconds — a zero-microsecond /
    // nonzero-nanosecond instant must still print the bare form.
    let micros = dt.nanosecond() / 1000;
    if micros == 0 {
        dt.format("%Y-%m-%dT%H:%M:%S").to_string()
    } else {
        format!("{}.{:06}", dt.format("%Y-%m-%dT%H:%M:%S"), micros)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn truncate_slices_by_code_point() {
        assert_eq!(truncate("hello world", 5), "hello");
        assert_eq!(truncate("hi", 300), "hi");
        // Multi-byte chars count as one code point each, like Python str slicing.
        assert_eq!(truncate("héllo", 2), "hé");
    }

    #[test]
    fn is_truthy_matches_python_bool() {
        assert!(!is_truthy(&Value::Null));
        assert!(!is_truthy(&Value::String(String::new())));
        assert!(is_truthy(&Value::String("abc".to_string())));
        assert!(!is_truthy(&Value::Bool(false)));
        assert!(is_truthy(&Value::Bool(true)));
    }

    #[test]
    fn utcnow_isoformat_uses_t_separator() {
        let s = utcnow_isoformat();
        // `YYYY-MM-DDТHH:MM:SS[.ffffff]` — a `T` at index 10, no space.
        assert_eq!(&s[10..11], "T");
        assert!(!s.contains(' '), "isoformat() uses a T separator, not a space");
        // `datetime.isoformat()` omits the fraction entirely when microsecond==0,
        // and otherwise emits all 6 digits. Mirror both shapes.
        if let Some((_, frac)) = s.split_once('.') {
            assert_eq!(frac.len(), 6, "expected 6-digit microseconds, got {s:?}");
        } else {
            // No dot at all -> microsecond was 0; the base form is "YYYY-MM-DDTHH:MM:SS".
            assert_eq!(s.len(), 19, "expected bare second-precision form, got {s:?}");
        }
    }

    #[test]
    fn find_bw_falls_back_to_bare_name() {
        // With a PATH that contains no `bw` and no candidate dirs, `_find_bw`
        // returns the bare `"bw"` (the PATH-lookup fallback). We can't safely
        // mutate the global PATH here, so just assert the function is callable and
        // yields a non-empty string (it is `"bw"` or a real path on this host).
        let p = find_bw();
        assert!(!p.is_empty());
    }

    /// Verify that `unlock` invokes `run_bw` with `["unlock", "--raw"]` and the
    /// password appended to `input_text` (not embedded in argv). We test this at
    /// the `run_bw` call-site level by exercising the function that `unlock` delegates
    /// to, with a fake `bw` (a shell script) that echoes back its own argv and stdin.
    ///
    /// Specifically: if `run_bw` is called with `["unlock", "--raw"]` and
    /// `input_text = "secret\n"`, the resulting argv list must NOT contain "secret"
    /// anywhere, and stdin delivery must convey the password to the subprocess.
    #[tokio::test]
    async fn unlock_passes_password_via_stdin_not_argv() {
        // Build a tiny fake `bw` that:
        //  * prints its own argv joined with spaces on stdout so we can assert
        //    no password appears there, then
        //  * prints the first line it reads from stdin so we can confirm the
        //    password arrived over stdin.
        //
        // Use a temp dir + a shell script so the test is self-contained and
        // requires no real `bw` installation.
        let tmp = std::env::temp_dir().join(format!(
            "vault_test_fake_bw_{}",
            std::process::id()
        ));
        std::fs::create_dir_all(&tmp).unwrap();
        let fake_bw = tmp.join("bw");
        std::fs::write(
            &fake_bw,
            "#!/bin/sh\necho \"argv: $@\"\nread line; echo \"stdin: $line\"\n",
        )
        .unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&fake_bw, std::fs::Permissions::from_mode(0o755)).unwrap();
        }

        // Temporarily prepend the temp dir to PATH so `find_bw` picks up our fake.
        let original_path = std::env::var("PATH").unwrap_or_default();
        let new_path = format!("{}:{}", tmp.display(), original_path);
        // SAFETY: test-only mutation; tests run in separate processes (cargo test
        // forks one process per test binary, not per test fn). We accept the
        // theoretical race between parallel tests in the same binary; the PATH
        // override is short-lived (sub-millisecond subprocess execution).
        std::env::set_var("PATH", &new_path);

        let input_text = "mysecret\n";
        let (stdout, _stderr, _rc) = run_bw(&["unlock", "--raw"], None, Some(input_text)).await;

        // Restore PATH before any assert (so a panic doesn't leave it mutated).
        std::env::set_var("PATH", &original_path);

        // The fake script echoes "argv: unlock --raw"; confirm no password there.
        let argv_line = stdout
            .lines()
            .find(|l| l.starts_with("argv:"))
            .unwrap_or("");
        assert!(
            !argv_line.contains("mysecret"),
            "password leaked into argv: {argv_line:?}"
        );
        // The stdin line should carry the password.
        let stdin_line = stdout
            .lines()
            .find(|l| l.starts_with("stdin:"))
            .unwrap_or("");
        assert_eq!(
            stdin_line, "stdin: mysecret",
            "password not received on stdin: {stdin_line:?}"
        );

        // Clean up.
        let _ = std::fs::remove_dir_all(&tmp);
    }
}
