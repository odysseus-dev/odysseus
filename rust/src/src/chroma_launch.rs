// src/chroma_launch.rs  <- (no direct Python source — replicates docker-compose.yml)
//! Optional launcher for a REAL ChromaDB server — TWO backends, ONE entry point.
//!
//! ## Why this exists (NEW capability, not in the Python)
//!
//! The Python `src/chroma_client.py` always connected to an *externally-managed*
//! ChromaDB service (`docker-compose.yml`'s `chromadb` service, or a host-run
//! container) via `chromadb.HttpClient(host=CHROMADB_HOST, port=CHROMADB_PORT)`.
//! The Rust port keeps the SQLite swap as the default but can ALSO talk to a real
//! Chroma over HTTP (see [`crate::src::vector_store`]). This module gives the port
//! the *option* to LAUNCH that server itself — so the server can run
//! end-to-end against real Chroma with no manual server management.
//!
//! ## Two launcher backends behind one entry point ([`ensure_chroma_running`])
//!
//!   * **DOCKER** (the original, unchanged): a faithful replication of the compose
//!     `chromadb` service — `docker run -d` of `chromadb/chroma:latest`, published
//!     on the host loopback, persisted to the `DATA_DIR/chroma` volume. Reuses a
//!     running container, `docker start`s a stopped one, else `docker run`s a fresh
//!     one; only ever STOPS a container WE started.
//!   * **LOCAL** (NEW, no docker required): the pip-installed full `chromadb`
//!     package ships its own (Rust-based) server, launched with the COLLISION-PROOF
//!     command
//!
//!     ```text
//!     <python> -c "from chromadb.cli.cli import app; app()" run \
//!         --path DATA_DIR/chroma --host localhost --port <PORT>
//!     ```
//!
//!     We deliberately invoke the server via the *full module path* rather than the
//!     bare `chroma` binary: on many machines `chroma` on PATH is alecthomas/chroma
//!     (a Go syntax highlighter), NOT ChromaDB. `python -m chromadb.cli.cli` also
//!     does NOT work (the module has no `__main__` guard, so `-m` exits 0 doing
//!     nothing), hence the explicit `-c "...app()"` form. The interpreter is the
//!     first candidate whose `python -c "import chromadb.cli.cli"` exits 0 (a venv
//!     with only `chromadb-client` LACKS the `.cli` submodule and so is skipped in
//!     favour of a global python with the full package).
//!
//! ## Launcher selection ([`select_launcher`] / `ODYSSEUS_CHROMA_LAUNCHER`)
//!
//!   * `auto` (default): prefer LOCAL when a chromadb-capable python is found, else
//!     DOCKER when docker is found, else an honest error naming both options.
//!   * `local`: force LOCAL (honest error if no capable python is found).
//!   * `docker`: the original docker behavior.
//!
//! Both backends target the SAME reachable server — `http://localhost:<HOSTPORT>`
//! where `HOSTPORT == CHROMADB_PORT` (default `8100`, matching `chroma_client.py`)
//! — and the SAME persisted data dir (`DATA_DIR/chroma`, honoring `ODYSSEUS_DATA_DIR`),
//! so the launcher target and the ChromaHttp backend target ([`crate::src::vector_store`]
//! reads the same `CHROMADB_HOST`/`CHROMADB_PORT`) coincide with zero extra config.
//!
//! ## Lifecycle (both backends)
//!
//!   1. REUSE: if `GET /api/v2/heartbeat` on `localhost:<PORT>` already answers
//!      (local), or our named container is running (docker), reuse it
//!      (`started_by_us = false`) — never spawn/kill what we did not start.
//!   2. START: else launch the server (`started_by_us = true`).
//!   3. READINESS POLL: `GET /api/v2/heartbeat` every ~500ms until 200 or timeout;
//!      on timeout, return the last poll error (never a fabricated readiness).
//!
//! ## Detach vs hold (local backend)
//!
//! The standalone `odysseus chroma start` subcommand DETACHES the local server (its
//! own session via `setsid` + a detached process group), redirects stdout/stderr to
//! a log file under `DATA_DIR/chroma`, and writes a PID file
//! (`DATA_DIR/chroma/launcher.pid`) so a later `stop`/`status` can find it — the
//! server must survive the short-lived subcommand process exiting. The AUTOLAUNCH
//! path (`web::run`) instead HOLDS the child for the server lifetime and kills it on
//! graceful shutdown (only when `started_by_us`) — no detach there.
//!
//! ## Honesty
//!
//! Neither launcher available -> an honest message naming BOTH options + `Err`
//! (never a fabricated success). A readiness timeout returns the last poll error
//! (never a faked "ready"). A non-zero `docker` exit returns its captured stderr.
//! The persisted data lives under the crate-wide `core::constants::DATA_DIR`
//! (honors `ODYSSEUS_DATA_DIR`), beside the rest of the app's data.
//!
//! Always compiled (no cargo feature flags): sits with the chroma_client /
//! vector_store cohort; the readiness poll uses `reqwest::blocking`; the local
//! detach uses `nix` with the `signal`/`process`/`term` cargo features.

use std::path::PathBuf;
use std::process::Command;
use std::time::{Duration, Instant};

use crate::error::{PyError, PyResult};
use crate::pylog as logger;

// ---------------------------------------------------------------------------
// Constants — mirror docker-compose.yml + the standalone-host equivalent
// ---------------------------------------------------------------------------

/// The managed container name (the standalone-host analogue of the compose
/// service name). Stable so a restart of the server reuses the same container.
pub const CONTAINER_NAME: &str = "odysseus-chromadb";

/// The image, verbatim from the compose `chromadb` service.
pub const IMAGE: &str = "chromadb/chroma:latest";

/// The in-container port Chroma listens on (the compose container side of
/// `8100:8000`).
pub const CONTAINER_PORT: u16 = 8000;

/// The default host port (the compose host side of `8100:8000`), which equals the
/// Python `CHROMADB_PORT` default. The actual host port is `CHROMADB_PORT` (see
/// [`host_port`]).
pub const DEFAULT_HOST_PORT: u16 = 8100;

/// The telemetry-disabling env the compose service sets. This is the SINGLE source
/// of truth for the telemetry knob across BOTH launcher backends: the docker backend
/// passes it verbatim as a `-e KEY=VALUE` arg (see [`run_args`]), and the local
/// backend parses it via [`telemetry_env_pair`] into a `Command::env(KEY, VALUE)`
/// entry on the spawned python process (see [`apply_local_env`]) — no second literal.
const TELEMETRY_ENV: &str = "ANONYMIZED_TELEMETRY=FALSE";

/// Split the [`TELEMETRY_ENV`] `KEY=VALUE` constant into a `(key, value)` pair so the
/// LOCAL python spawn can set it on the child process environment (matching the
/// docker `-e KEY=VALUE`). PURE (takes the raw `KEY=VALUE`) and unit-tested so the
/// telemetry parity is a single, testable source of truth: docker uses the literal,
/// local uses this split of the SAME literal. Returns `None` only for a malformed
/// constant (no `=`), which would be a programming error in the const above.
fn telemetry_env_pair(raw: &str) -> Option<(&str, &str)> {
    raw.split_once('=')
}

/// Apply the docker-parity environment to a LOCAL python spawn `Command`: set
/// `ANONYMIZED_TELEMETRY=FALSE` (parsed from the [`TELEMETRY_ENV`] constant) on the
/// child process, replicating what the compose `chromadb` service's `-e` does for the
/// docker backend. Shared by BOTH local spawn paths ([`ensure_local_held`]'s held
/// spawn and [`spawn_local_detached`]) so the single telemetry source of truth is
/// honored identically. A malformed constant (never, in practice) simply skips the
/// env (best-effort — the server still launches), never panics.
fn apply_local_env(cmd: &mut Command) {
    if let Some((key, value)) = telemetry_env_pair(TELEMETRY_ENV) {
        cmd.env(key, value);
    }
}

/// Overall readiness budget. Generous because the docker FIRST run pulls the image
/// (`chromadb/chroma:latest` is not necessarily cached), and `docker run -d`
/// returns before the image is fully pulled + the server is listening. The local
/// server starts much faster but shares this budget for simplicity.
const READINESS_TIMEOUT: Duration = Duration::from_secs(90);

/// Interval between readiness polls.
const POLL_INTERVAL: Duration = Duration::from_millis(500);

/// Per-poll heartbeat HTTP timeout (short — a non-listening port should fail fast
/// so we keep polling).
const POLL_HTTP_TIMEOUT: Duration = Duration::from_secs(2);

/// The `ODYSSEUS_CHROMA_LAUNCHER` env naming the launcher backend
/// ({`auto`, `local`, `docker`}, default `auto`).
const LAUNCHER_ENV: &str = "ODYSSEUS_CHROMA_LAUNCHER";

/// The `ODYSSEUS_CHROMA_PYTHON` env naming an explicit interpreter to probe FIRST
/// for the local backend (before the venv / `python3` / `python` candidates).
const PYTHON_ENV: &str = "ODYSSEUS_CHROMA_PYTHON";

/// `ODYSSEUS_BASE_DIR` (the repo root) — its `venv/bin/python3` is probed after
/// `ODYSSEUS_CHROMA_PYTHON`. Falls back to the current dir when unset.
const BASE_DIR_ENV: &str = "ODYSSEUS_BASE_DIR";

// ---------------------------------------------------------------------------
// Binary detection (mirrors builtin_mcp::_find_npx's posture)
// ---------------------------------------------------------------------------

/// `os.path.isfile` — true only for an existing regular file. A small local copy
/// (like `builtin_mcp::isfile`) so this unit is self-contained.
fn isfile(path: &str) -> bool {
    std::fs::metadata(path).map(|m| m.is_file()).unwrap_or(false)
}

/// `shutil.which(name)` — scan each `PATH` entry for an executable named `name`,
/// returning the first hit (executable bit checked on Unix). A local copy of
/// `builtin_mcp::shutil_which` so this unit does not reach into that module's
/// private helper.
fn shutil_which(name: &str) -> Option<String> {
    let path_var = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path_var) {
        let candidate = dir.join(name);
        let meta = match std::fs::metadata(&candidate) {
            Ok(m) => m,
            Err(_) => continue,
        };
        if !meta.is_file() {
            continue;
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            if meta.permissions().mode() & 0o111 == 0 {
                continue;
            }
        }
        return Some(candidate.to_string_lossy().into_owned());
    }
    None
}

/// Find the docker binary, checking PATH then common install locations (mirrors
/// `builtin_mcp::_find_npx`). `None` when docker cannot be located at all — the
/// caller turns that into an honest docker-absent message.
pub fn find_docker() -> Option<String> {
    if let Some(docker) = shutil_which("docker") {
        return Some(docker);
    }
    for candidate in [
        "/usr/local/bin/docker",
        "/opt/homebrew/bin/docker",
        "/usr/bin/docker",
    ] {
        if isfile(candidate) {
            return Some(candidate.to_string());
        }
    }
    None
}

// ---------------------------------------------------------------------------
// Local-python backend detection
// ---------------------------------------------------------------------------

/// The args that probe whether a python interpreter can import the FULL chromadb
/// server CLI (`chromadb.cli.cli`). A venv carrying only `chromadb-client` LACKS
/// the `.cli` submodule, so the probe FAILS for it and the search falls through to
/// a global python with the full package. Pure (unit-tested) — the arg vector is
/// what gets handed to `<python> <PROBE_ARGS>`.
fn probe_args() -> Vec<String> {
    vec!["-c".to_string(), "import chromadb.cli.cli".to_string()]
}

/// The candidate interpreters to probe, in order:
///   1. `ODYSSEUS_CHROMA_PYTHON` (an explicit override) — verbatim;
///   2. `<BASE_DIR>/venv/bin/python3` (the project venv — typically only the
///      `chromadb-client`, so it FAILS the probe and we fall through);
///   3. `python3` then `python`, each resolved via `PATH` (like `find_docker`),
///      so the bare names become absolute paths (the global interpreter with the
///      full package).
///
/// Pure (no probing) so the ORDER is unit-testable. `base_dir` is injected so the
/// test can pin it; production passes the resolved [`base_dir`].
fn python_candidates(base_dir: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    if let Ok(explicit) = std::env::var(PYTHON_ENV) {
        let explicit = explicit.trim();
        if !explicit.is_empty() {
            out.push(explicit.to_string());
        }
    }
    // `<BASE_DIR>/venv/bin/python3`.
    let venv_python = PathBuf::from(base_dir)
        .join("venv")
        .join("bin")
        .join("python3");
    out.push(venv_python.to_string_lossy().into_owned());
    // Bare names resolved via PATH (absolute) so the spawn is unambiguous; if the
    // name is not on PATH, keep the bare name so the probe simply fails honestly.
    for name in ["python3", "python"] {
        match shutil_which(name) {
            Some(abs) => out.push(abs),
            None => out.push(name.to_string()),
        }
    }
    out
}

/// `ODYSSEUS_BASE_DIR` (the repo root), else the current working directory. Used to
/// locate the project venv interpreter candidate.
fn base_dir() -> String {
    std::env::var(BASE_DIR_ENV)
        .ok()
        .filter(|v| !v.trim().is_empty())
        .unwrap_or_else(|| {
            std::env::current_dir()
                .map(|p| p.to_string_lossy().into_owned())
                .unwrap_or_else(|_| ".".to_string())
        })
}

/// Run the import probe against one interpreter: `<python> -c "import
/// chromadb.cli.cli"`. Returns `true` only on a clean exit 0. A spawn failure
/// (missing interpreter) or a non-zero exit (no full chromadb) is `false`.
fn python_can_run_chroma(python: &str) -> bool {
    Command::new(python)
        .args(probe_args())
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

/// The process-wide cache of the discovered chromadb-capable interpreter. `None`
/// inside the `Option` means "not yet resolved"; `Some(None)` means "resolved, none
/// found"; `Some(Some(path))` is the cached interpreter. Caching avoids re-spawning
/// the probe (which forks a python) on every call.
static CHROMA_PYTHON: once_cell::sync::Lazy<std::sync::Mutex<Option<Option<String>>>> =
    once_cell::sync::Lazy::new(|| std::sync::Mutex::new(None));

/// Detect (and cache) a python interpreter that can run the bundled chromadb
/// server, probing [`python_candidates`] in order. `None` when no candidate can
/// import `chromadb.cli.cli` — the caller turns that into an honest error.
pub fn find_chroma_python() -> Option<String> {
    {
        let cache = CHROMA_PYTHON.lock().unwrap_or_else(|p| p.into_inner());
        if let Some(resolved) = cache.as_ref() {
            return resolved.clone();
        }
    }
    let resolved = python_candidates(&base_dir())
        .into_iter()
        .find(|p| python_can_run_chroma(p));
    let mut cache = CHROMA_PYTHON.lock().unwrap_or_else(|p| p.into_inner());
    *cache = Some(resolved.clone());
    resolved
}

// ---------------------------------------------------------------------------
// Launcher selection
// ---------------------------------------------------------------------------

/// Which launcher backend [`ensure_chroma_running`] uses.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Launcher {
    /// The pip-installed full `chromadb` package's own server (no docker).
    Local,
    /// The `chromadb/chroma:latest` docker container (the original behavior).
    Docker,
}

/// The operator's requested launcher mode (`ODYSSEUS_CHROMA_LAUNCHER`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum LauncherMode {
    /// Prefer local, else docker, else honest error.
    Auto,
    /// Force local (honest error if no capable python).
    Local,
    /// Force docker (the original behavior).
    Docker,
}

/// Parse `ODYSSEUS_CHROMA_LAUNCHER` (lowercased/trimmed) into a [`LauncherMode`].
/// Default (unset or unrecognized) is `auto`, the conservative "let the
/// environment decide" choice. Pure (takes the raw env value) so it is
/// unit-testable without touching the process env.
fn parse_launcher_mode(raw: Option<&str>) -> LauncherMode {
    match raw.map(|s| s.trim().to_ascii_lowercase()).as_deref() {
        Some("local") => LauncherMode::Local,
        Some("docker") => LauncherMode::Docker,
        // "auto", unset, empty, or anything unrecognized -> auto.
        _ => LauncherMode::Auto,
    }
}

/// Resolve the launcher backend from the requested mode + what is actually
/// available. PURE in its inputs (`mode`, `has_python`, `has_docker`) so the full
/// selection matrix is unit-testable without spawning a probe / docker. Returns an
/// honest `Err` naming BOTH options when nothing is available (or the forced mode's
/// requirement is missing).
fn resolve_launcher(mode: LauncherMode, has_python: bool, has_docker: bool) -> PyResult<Launcher> {
    match mode {
        LauncherMode::Local => {
            if has_python {
                Ok(Launcher::Local)
            } else {
                Err(PyError::other(
                    "ODYSSEUS_CHROMA_LAUNCHER=local but no chromadb-capable python was found \
                     (probe `python -c \"import chromadb.cli.cli\"`); install the full `chromadb` \
                     package (`pip install chromadb`) or set ODYSSEUS_CHROMA_PYTHON / \
                     ODYSSEUS_CHROMA_LAUNCHER=docker.",
                ))
            }
        }
        LauncherMode::Docker => {
            if has_docker {
                Ok(Launcher::Docker)
            } else {
                Err(PyError::other(
                    "ODYSSEUS_CHROMA_LAUNCHER=docker but the docker binary was not found on PATH; \
                     install Docker or set ODYSSEUS_CHROMA_LAUNCHER=local (needs the full `chromadb` \
                     python package, no docker required).",
                ))
            }
        }
        LauncherMode::Auto => {
            if has_python {
                Ok(Launcher::Local)
            } else if has_docker {
                Ok(Launcher::Docker)
            } else {
                Err(PyError::other(
                    "Cannot launch ChromaDB: no chromadb-capable python (install the full \
                     `chromadb` package: `pip install chromadb`) AND no docker binary on PATH \
                     (install Docker). Set ODYSSEUS_CHROMA_LAUNCHER=local|docker once one is \
                     available, or point CHROMADB_HOST/PORT at an existing server.",
                ))
            }
        }
    }
}

/// Pick the launcher backend per `ODYSSEUS_CHROMA_LAUNCHER` + availability. Honest
/// `Err` when the requested (or, in `auto`, any) backend is unavailable.
pub fn select_launcher() -> PyResult<Launcher> {
    let mode = parse_launcher_mode(std::env::var(LAUNCHER_ENV).ok().as_deref());
    // Only probe what the mode might need — but `auto` needs both signals to decide,
    // so probe both there. (`find_chroma_python` is cached, so this is cheap.)
    let (has_python, has_docker) = match mode {
        LauncherMode::Local => (find_chroma_python().is_some(), false),
        LauncherMode::Docker => (false, find_docker().is_some()),
        LauncherMode::Auto => (find_chroma_python().is_some(), find_docker().is_some()),
    };
    resolve_launcher(mode, has_python, has_docker)
}

// ---------------------------------------------------------------------------
// Configuration derived from the environment
// ---------------------------------------------------------------------------

/// The host port to expose Chroma on, `CHROMADB_PORT` (default
/// [`DEFAULT_HOST_PORT`] = 8100, matching `chroma_client.py`). A non-numeric value
/// falls back to the default (defensive — the backend would also fail to parse it).
pub fn host_port() -> u16 {
    std::env::var("CHROMADB_PORT")
        .ok()
        .and_then(|v| v.trim().parse::<u16>().ok())
        .unwrap_or(DEFAULT_HOST_PORT)
}

/// The readiness URL the launcher polls and the URL it reports for the running
/// server: `http://localhost:<HOSTPORT>/api/v2/heartbeat`. We always poll
/// `localhost` (both backends publish on the host loopback) regardless of the
/// `CHROMADB_HOST` the backend will later use, because that is where the launched
/// server is reachable from this process.
pub fn heartbeat_url(host_port: u16) -> String {
    format!("http://localhost:{host_port}/api/v2/heartbeat")
}

/// The base URL we report for a running server (the `odysseus chroma start`
/// subcommand prints this).
pub fn reachable_url(host_port: u16) -> String {
    format!("http://localhost:{host_port}")
}

/// The absolute host path that holds the persisted Chroma data. Uses the crate-wide
/// `core::constants::DATA_DIR` (honors `ODYSSEUS_DATA_DIR`) joined with `chroma`,
/// resolved to an absolute path. The docker backend bind-mounts this to
/// `/chroma/chroma`; the local backend passes it to `--path`. SAME dir either way,
/// so a switch of launcher reuses the same persisted collections.
fn data_volume_host_path() -> String {
    let data_dir = crate::core::constants::DATA_DIR.clone();
    let joined = std::path::Path::new(&data_dir).join("chroma");
    // `-v` / `--path` want an absolute path; `std::path::absolute` does not touch the FS.
    match std::path::absolute(&joined) {
        Ok(abs) => abs.to_string_lossy().into_owned(),
        Err(_) => joined.to_string_lossy().into_owned(),
    }
}

/// The PID file the standalone `odysseus chroma start` writes for a detached local
/// server: `DATA_DIR/chroma/launcher.pid`. A later `stop`/`status` reads it.
fn launcher_pid_path() -> PathBuf {
    PathBuf::from(data_volume_host_path()).join("launcher.pid")
}

/// The log file a detached local server's stdout/stderr is redirected to:
/// `DATA_DIR/chroma/launcher.log`.
fn launcher_log_path() -> PathBuf {
    PathBuf::from(data_volume_host_path()).join("launcher.log")
}

// ---------------------------------------------------------------------------
// Docker argument builders (pure — unit-tested without invoking docker)
// ---------------------------------------------------------------------------

/// Build the `docker run -d ...` argument vector for the standalone-host command,
/// faithfully mirroring the compose `chromadb` service — INCLUDING its
/// `restart: unless-stopped` policy (`--restart unless-stopped`), so a standalone
/// docker launch self-heals on crash / daemon restart exactly like the compose
/// service (and exactly like the LOCAL held path's supervisor). Pure so it can be
/// unit-tested without a docker daemon.
fn run_args(host_port: u16, data_host_path: &str) -> Vec<String> {
    vec![
        "run".to_string(),
        "-d".to_string(),
        "--name".to_string(),
        CONTAINER_NAME.to_string(),
        "--restart".to_string(),
        "unless-stopped".to_string(),
        "-p".to_string(),
        format!("{host_port}:{CONTAINER_PORT}"),
        "-v".to_string(),
        format!("{data_host_path}:/chroma/chroma"),
        "-e".to_string(),
        TELEMETRY_ENV.to_string(),
        IMAGE.to_string(),
    ]
}

/// Build the `docker ps -a --filter ...` argument vector that lists ONLY our
/// container's name + status, one TAB-separated line (or nothing if absent).
fn ps_args() -> Vec<String> {
    vec![
        "ps".to_string(),
        "-a".to_string(),
        "--filter".to_string(),
        format!("name=^/{CONTAINER_NAME}$"),
        "--format".to_string(),
        "{{.Names}}\t{{.Status}}".to_string(),
    ]
}

// ---------------------------------------------------------------------------
// Local launch argument builder (pure — unit-tested without spawning python)
// ---------------------------------------------------------------------------

/// Build the COLLISION-PROOF local launch argument vector handed to `<python>`:
///
/// ```text
/// -c "from chromadb.cli.cli import app; app()" run \
///     --path <DATA>/chroma --host localhost --port <PORT>
/// ```
///
/// The `-c` form (rather than `-m chromadb.cli.cli`, which has no `__main__` guard
/// and exits 0 doing nothing, or the bare `chroma` binary, which is frequently a Go
/// syntax highlighter) is what makes this robust. Pure so the full arg vector is
/// unit-testable without spawning python.
fn local_launch_args(host_port: u16, data_path: &str) -> Vec<String> {
    vec![
        "-c".to_string(),
        "from chromadb.cli.cli import app; app()".to_string(),
        "run".to_string(),
        "--path".to_string(),
        data_path.to_string(),
        "--host".to_string(),
        "localhost".to_string(),
        "--port".to_string(),
        host_port.to_string(),
    ]
}

// ---------------------------------------------------------------------------
// Shared blocking HTTP client for the readiness poll
// ---------------------------------------------------------------------------

/// A module-level `reqwest::blocking::Client` for the readiness poll (a sane,
/// short timeout). Lazily built once; reused across polls.
static POLL_CLIENT: once_cell::sync::Lazy<reqwest::blocking::Client> =
    once_cell::sync::Lazy::new(|| {
        reqwest::blocking::Client::builder()
            .timeout(POLL_HTTP_TIMEOUT)
            .build()
            .unwrap_or_else(|_| reqwest::blocking::Client::new())
    });

/// `GET /api/v2/heartbeat` ONCE — `true` on a 200 (already running), `false`
/// otherwise (not listening / non-2xx). Used by the reuse check (a single probe,
/// distinct from [`poll_ready`]'s budgeted loop).
fn heartbeat_ok(port: u16) -> bool {
    POLL_CLIENT
        .get(heartbeat_url(port))
        .send()
        .map(|r| r.status().is_success())
        .unwrap_or(false)
}

// ---------------------------------------------------------------------------
// Lifecycle handle
// ---------------------------------------------------------------------------

/// What kind of ChromaDB server a [`ChromaHandle`] points at: a docker container
/// (identified by name) or a local process (identified by pid). It holds a `pid`
/// (`u32`), NOT a `std::process::Child`, so the handle stays `Clone` (the
/// `web::mod.rs` shutdown wiring moves a `Some(ChromaHandle)` into the shutdown
/// future, and the handle must be cheap to clone like the prior docker-only one).
/// The held child, when one exists (autolaunch holds it for the server lifetime),
/// lives separately in `web::run`'s scope, not in the handle.
#[derive(Debug, Clone)]
pub enum ChromaTarget {
    /// A docker container, by name.
    Docker { name: String },
    /// A local `chromadb` server process, by pid.
    Local { pid: u32 },
}

/// A handle to a running (or reused) ChromaDB server. `started_by_us` records
/// whether THIS process created it; only then will [`stop`] stop it on graceful
/// shutdown.
///
/// [`stop`]: ChromaHandle::stop
#[derive(Debug, Clone)]
pub struct ChromaHandle {
    /// The container name (kept for back-compat: the subcommand prints
    /// `handle.name`). For a local handle this is a synthetic descriptor.
    pub name: String,
    /// The concrete target (docker container or local process).
    pub target: ChromaTarget,
    /// Whether THIS process started the server (vs reused an existing one).
    pub started_by_us: bool,
}

impl ChromaHandle {
    /// Stop the server — ONLY if this process started it. Reusing a pre-existing /
    /// externally-managed server leaves it running (the "don't kill what you didn't
    /// start" posture; for docker it also honors compose's `restart: unless-stopped`).
    /// Best-effort: a failure is logged, never raised (shutdown must not block on a
    /// hiccup).
    pub fn stop(&self) {
        if !self.started_by_us {
            logger::info(&format!(
                "Leaving ChromaDB server '{}' running (not started by us).",
                self.name
            ));
            return;
        }
        match &self.target {
            ChromaTarget::Docker { name } => stop_docker_container(name),
            ChromaTarget::Local { pid } => stop_local_process(*pid),
        }
    }
}

/// `docker stop <name>` (best-effort, logged). Shared by [`ChromaHandle::stop`].
fn stop_docker_container(name: &str) {
    let docker = match find_docker() {
        Some(d) => d,
        None => {
            logger::warning("Cannot stop ChromaDB container: docker binary not found on shutdown.");
            return;
        }
    };
    match run_docker(&docker, &["stop".to_string(), name.to_string()]) {
        Ok(_) => logger::info(&format!("Stopped ChromaDB container '{name}'.")),
        Err(e) => logger::warning(&format!("Failed to stop ChromaDB container '{name}': {e}")),
    }
}

/// SIGTERM a local `chromadb` server pid + a best-effort reap (best-effort, logged).
/// Shared by [`ChromaHandle::stop`] and the `odysseus chroma stop` subcommand.
fn stop_local_process(pid: u32) {
    #[cfg(unix)]
    {
        let raw = match i32::try_from(pid) {
            Ok(p) => p,
            Err(_) => {
                logger::warning(&format!("Cannot stop ChromaDB process: pid {pid} out of range."));
                return;
            }
        };
        let target = nix::unistd::Pid::from_raw(raw);
        match nix::sys::signal::kill(target, nix::sys::signal::Signal::SIGTERM) {
            Ok(()) => logger::info(&format!("Sent SIGTERM to ChromaDB process {pid}.")),
            Err(e) => {
                logger::warning(&format!("Failed to SIGTERM ChromaDB process {pid}: {e}"));
                return;
            }
        }
        // Best-effort short wait for the process to leave the running state. This
        // loop does NOT reap the child (no `waitpid`): when the caller is the parent
        // that still holds the `Child`, a terminated server becomes a `Z <defunct>`
        // zombie that `kill(pid, 0)` keeps reporting as present until it is reaped —
        // so we treat a zombie as "gone" too. (The detached CLI server is reparented
        // to init, which reaps it; the `web::run` held path reaps via `Child::wait`.)
        // Never block shutdown: poll for up to ~2s.
        let deadline = Instant::now() + Duration::from_secs(2);
        while Instant::now() < deadline {
            if nix::sys::signal::kill(target, None).is_err() || pid_is_zombie(pid) {
                break; // gone, or a dead-but-unreaped zombie
            }
            std::thread::sleep(Duration::from_millis(100));
        }
    }
    #[cfg(not(unix))]
    {
        let _ = pid;
        logger::warning("Cannot stop ChromaDB process: signal-based stop is Unix-only.");
    }
}


/// Run `docker <args>` capturing output. Returns the trimmed stdout on success;
/// a non-zero exit (or a spawn failure) is an honest `PyError` carrying stderr —
/// never a silent "success".
fn run_docker(docker: &str, args: &[String]) -> PyResult<String> {
    let output = Command::new(docker)
        .args(args)
        .output()
        .map_err(|e| PyError::other(format!("docker {} spawn failed: {e}", args.join(" "))))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(PyError::other(format!(
            "docker {} failed (status {}): {}",
            args.join(" "),
            output.status,
            stderr.trim()
        )));
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
}

/// Parse the single `docker ps -a --filter ...` line into `(name, status)`.
/// Returns `None` when the container does not exist (no matching line).
fn parse_ps_line(stdout: &str) -> Option<(String, String)> {
    let line = stdout.lines().find(|l| !l.trim().is_empty())?;
    let mut parts = line.splitn(2, '\t');
    let name = parts.next()?.trim().to_string();
    let status = parts.next().unwrap_or("").trim().to_string();
    if name.is_empty() {
        return None;
    }
    Some((name, status))
}

/// Whether a `docker ps` status string indicates a RUNNING container. Docker
/// reports running containers as `Up …` and stopped ones as `Exited (…) …` /
/// `Created`. Case-insensitive, defensive.
fn status_is_running(status: &str) -> bool {
    status.trim_start().to_lowercase().starts_with("up")
}

// ---------------------------------------------------------------------------
// PID file helpers (local backend, standalone `chroma start`)
// ---------------------------------------------------------------------------

/// Read the launcher PID file, returning the recorded pid (the file holds the bare
/// integer pid). `None` when the file is absent / unreadable / not an integer.
fn read_pid_file() -> Option<u32> {
    let text = std::fs::read_to_string(launcher_pid_path()).ok()?;
    text.trim().parse::<u32>().ok()
}

/// Write the launcher PID file (best-effort — a write failure is logged but does
/// not fail the launch; the server is up either way, only the standalone stop/status
/// loses its PID hint).
fn write_pid_file(pid: u32) {
    let path = launcher_pid_path();
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Err(e) = std::fs::write(&path, pid.to_string()) {
        logger::warning(&format!(
            "Could not write ChromaDB launcher PID file {}: {e}",
            path.display()
        ));
    }
}

// ---------------------------------------------------------------------------
// ensure_chroma_running — THE single entry point (picks the backend)
// ---------------------------------------------------------------------------

/// Ensure a ChromaDB server is running, returning a [`ChromaHandle`]. THE single
/// entry the `chroma start` subcommand + `web::run` autolaunch both call.
///
/// Picks the launcher backend per `ODYSSEUS_CHROMA_LAUNCHER` (+ availability),
/// then:
///   * LOCAL: reuse a server already answering the heartbeat, else DETACH-spawn the
///     bundled `chromadb` server (PID file + log under `DATA_DIR/chroma`) and poll
///     readiness.
///   * DOCKER: the original reuse/start/run + readiness poll.
///
/// Synchronous (call via `tokio::task::spawn_blocking` from async startup). On the
/// LOCAL path this DETACHES the child (so the standalone subcommand can exit while
/// the server keeps running); the autolaunch path that wants to HOLD + kill the
/// child uses [`ensure_local_held`] instead.
pub fn ensure_chroma_running() -> PyResult<ChromaHandle> {
    match select_launcher()? {
        Launcher::Local => ensure_local_detached(),
        Launcher::Docker => ensure_docker(),
    }
}

/// Like [`ensure_chroma_running`] but for the `web::run` AUTOLAUNCH path: it HOLDS
/// any child it spawns (so the caller can kill it on graceful shutdown) instead of
/// detaching. Picks the backend internally per `ODYSSEUS_CHROMA_LAUNCHER`:
///   * LOCAL: spawn (or reuse) via [`ensure_local_held`], returning the held child;
///   * DOCKER: the original [`ensure_docker`] (docker manages the container
///     lifetime itself, so there is no child to hold — returns `None`).
///
/// Returns `(handle, Option<Child>)`. The held `Child` (local, started-by-us only)
/// is owned by the server for its lifetime and dropped/killed on shutdown.
pub fn ensure_chroma_running_held() -> PyResult<(ChromaHandle, Option<std::process::Child>)> {
    match select_launcher()? {
        Launcher::Local => ensure_local_held(),
        Launcher::Docker => Ok((ensure_docker()?, None)),
    }
}

// ---------------------------------------------------------------------------
// Local backend lifecycle
// ---------------------------------------------------------------------------

/// Common local-backend preamble: resolve the interpreter (honest error if none —
/// `select_launcher` already checked, but this is the funnel both detached/held
/// paths share), the port, and the data path; ensure the data dir exists. Returns
/// `(python, port, data_path)`.
fn local_prep() -> PyResult<(String, u16, String)> {
    let python = find_chroma_python().ok_or_else(|| {
        PyError::other(
            "No chromadb-capable python found (probe `python -c \"import chromadb.cli.cli\"`); \
             install the full `chromadb` package (`pip install chromadb`) or set \
             ODYSSEUS_CHROMA_PYTHON.",
        )
    })?;
    let port = host_port();
    let data_path = data_volume_host_path();
    // The server creates this on demand, but pre-creating it keeps the PID/log
    // files' parent present and matches the docker volume dir layout.
    let _ = std::fs::create_dir_all(&data_path);
    Ok((python, port, data_path))
}

/// Ensure the local server is running, DETACHED — for the standalone
/// `odysseus chroma start` subcommand (the server must survive the subcommand
/// process exiting). Reuse a server already answering; else detach-spawn, write the
/// PID file, poll readiness.
fn ensure_local_detached() -> PyResult<ChromaHandle> {
    let (python, port, data_path) = local_prep()?;

    // 1. REUSE: a server already answering the heartbeat is reused (do NOT spawn).
    if heartbeat_ok(port) {
        logger::info(&format!(
            "Reusing ChromaDB server already listening at {} (not started by us).",
            reachable_url(port)
        ));
        // Record the existing pid if a prior launcher left one (cosmetic; the
        // started_by_us=false guard means we never stop it anyway).
        let pid = read_pid_file().unwrap_or(0);
        return Ok(ChromaHandle {
            name: format!("local:{port}"),
            target: ChromaTarget::Local { pid },
            started_by_us: false,
        });
    }

    // 2. SPAWN (detached) + PID file.
    logger::info(&format!(
        "Launching local ChromaDB server via {python} \
         (host port {port}, data {data_path}); logging to {}…",
        launcher_log_path().display()
    ));
    let pid = spawn_local_detached(&python, port, &data_path)?;
    write_pid_file(pid);

    // 3. READINESS POLL.
    poll_ready(port)?;
    logger::info(&format!(
        "Local ChromaDB ready at {} (pid {pid}, started_by_us=true).",
        reachable_url(port)
    ));
    Ok(ChromaHandle {
        name: format!("local:{port}"),
        target: ChromaTarget::Local { pid },
        started_by_us: true,
    })
}

/// Ensure the local server is running, HOLDING the child — for the `web::run`
/// autolaunch path, which keeps the child for the server lifetime and kills it on
/// graceful shutdown. Returns `(handle, Option<Child>)`: the `Child` is `Some` only
/// when WE spawned it (a reused server has no child to hold). Reuse a server already
/// answering; else spawn (NOT detached — held), poll readiness.
///
/// The held child's stdio is inherited from this process (the server is part of the
/// app's lifetime, not detached), so its logs interleave with the app's — the
/// opposite of the detached path which redirects to a file.
pub fn ensure_local_held() -> PyResult<(ChromaHandle, Option<std::process::Child>)> {
    let (python, port, data_path) = local_prep()?;

    if heartbeat_ok(port) {
        logger::info(&format!(
            "Reusing ChromaDB server already listening at {} (not started by us).",
            reachable_url(port)
        ));
        let pid = read_pid_file().unwrap_or(0);
        return Ok((
            ChromaHandle {
                name: format!("local:{port}"),
                target: ChromaTarget::Local { pid },
                started_by_us: false,
            },
            None,
        ));
    }

    logger::info(&format!(
        "Launching (held) local ChromaDB server via {python} (host port {port}, data {data_path})…"
    ));
    let mut cmd = Command::new(&python);
    cmd.args(local_launch_args(port, &data_path));
    apply_local_env(&mut cmd); // docker-parity: ANONYMIZED_TELEMETRY=FALSE on the child
    let child = cmd
        .spawn()
        .map_err(|e| PyError::other(format!("local chroma spawn failed: {e}")))?;
    let pid = child.id();

    poll_ready(port)?;
    logger::info(&format!(
        "Local ChromaDB ready at {} (pid {pid}, started_by_us=true, held).",
        reachable_url(port)
    ));
    Ok((
        ChromaHandle {
            name: format!("local:{port}"),
            target: ChromaTarget::Local { pid },
            started_by_us: true,
        },
        Some(child),
    ))
}

/// Spawn the bundled `chromadb` server DETACHED (its own session via `setsid` AND a
/// detached process group), with stdout+stderr redirected to the launcher log file.
/// Returns the child PID. Mirrors `bg_jobs::spawn_detached`'s posture (stdlib
/// `process_group(0)` + `nix::unistd::setsid` in a `pre_exec`, gated `cfg(unix)`);
/// a non-unix host falls back to a plain spawn so the crate still compiles.
fn spawn_local_detached(python: &str, port: u16, data_path: &str) -> PyResult<u32> {
    use std::process::Stdio;

    // Redirect stdout+stderr to the log file (truncate-or-create), so a detached
    // server's output is captured and stdin is detached.
    let log_path = launcher_log_path();
    if let Some(parent) = log_path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let log = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .map_err(|e| {
            PyError::other(format!(
                "could not open ChromaDB launcher log {}: {e}",
                log_path.display()
            ))
        })?;
    let log_err = log
        .try_clone()
        .map_err(|e| PyError::other(format!("could not clone ChromaDB log handle: {e}")))?;

    let mut cmd = Command::new(python);
    cmd.args(local_launch_args(port, data_path))
        .stdin(Stdio::null())
        .stdout(Stdio::from(log))
        .stderr(Stdio::from(log_err));
    apply_local_env(&mut cmd); // docker-parity: ANONYMIZED_TELEMETRY=FALSE on the child

    // Detach into its own SESSION (and thus its own process group, with the child as
    // leader) so a Ctrl-C to the (short-lived) subcommand process does not propagate
    // to the server and it survives the controlling terminal closing — exactly
    // CPython's `start_new_session=True`, mirroring `bg_jobs::spawn_detached`.
    //
    // We use `setsid()` ALONE (NOT the stdlib `process_group(0)` as well): `setsid`
    // already places the child in a brand-new session + process group as the leader,
    // which is the full detach. Calling stdlib `process_group(0)` FIRST would make
    // the child a group leader before exec, and a subsequent `setsid()` then fails
    // with EPERM (setsid refuses to run for an existing process-group leader) — so
    // combining the two is not just redundant, it breaks the spawn on macOS/Linux.
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        // SAFETY: `setsid()` is async-signal-safe; it is the only thing executed in
        // the child between fork and exec (mirroring `bg_jobs::spawn_detached`).
        unsafe {
            cmd.pre_exec(|| nix::unistd::setsid().map(|_| ()).map_err(std::io::Error::from));
        }
    }

    let child = cmd
        .spawn()
        .map_err(|e| PyError::other(format!("local chroma spawn failed: {e}")))?;
    Ok(child.id())
}

// ---------------------------------------------------------------------------
// Docker backend lifecycle (the ORIGINAL behavior, intact)
// ---------------------------------------------------------------------------

/// Ensure a ChromaDB container is running (the docker backend — original behavior).
///
/// Lifecycle:
///   1. REUSE: container exists + running -> reuse (`started_by_us = false`); exists
///      but stopped -> `docker start` (`started_by_us = false`, we did not create it).
///   2. RUN: does not exist -> `docker run -d …` (`started_by_us = true`).
///   3. READINESS POLL: `GET /api/v2/heartbeat` until 200 or timeout.
fn ensure_docker() -> PyResult<ChromaHandle> {
    let docker = find_docker().ok_or_else(|| {
        PyError::other(
            "Docker not found on PATH; cannot launch ChromaDB. Install Docker, set \
             ODYSSEUS_CHROMA_LAUNCHER=local (needs the full `chromadb` python package, no \
             docker required), or point CHROMADB_HOST/PORT at an existing server.",
        )
    })?;

    let port = host_port();

    // 1. Does our container already exist?
    let ps_out = run_docker(&docker, &ps_args())?;
    let existing = parse_ps_line(&ps_out);

    let started_by_us = match existing {
        Some((_name, status)) if status_is_running(&status) => {
            logger::info(&format!(
                "Reusing running ChromaDB container '{CONTAINER_NAME}' ({status})."
            ));
            false
        }
        Some((_name, status)) => {
            // Exists but stopped -> start it. We did NOT create it.
            logger::info(&format!(
                "Starting existing (stopped) ChromaDB container '{CONTAINER_NAME}' ({status})."
            ));
            run_docker(&docker, &["start".to_string(), CONTAINER_NAME.to_string()])?;
            false
        }
        None => {
            // 2. Does not exist -> run it. First run pulls the image.
            let data_host_path = data_volume_host_path();
            logger::info(&format!(
                "Launching ChromaDB container '{CONTAINER_NAME}' \
                 (image {IMAGE}, host port {port}, data {data_host_path}); \
                 pulling chromadb/chroma:latest (first run)…"
            ));
            run_docker(&docker, &run_args(port, &data_host_path))?;
            true
        }
    };

    // 3. Readiness poll: GET /api/v2/heartbeat until 200 or timeout.
    poll_ready(port)?;

    logger::info(&format!(
        "ChromaDB ready at {} (started_by_us={started_by_us}).",
        reachable_url(port)
    ));
    Ok(ChromaHandle {
        name: CONTAINER_NAME.to_string(),
        target: ChromaTarget::Docker {
            name: CONTAINER_NAME.to_string(),
        },
        started_by_us,
    })
}

/// Poll `GET /api/v2/heartbeat` on the host port until a 200 (ready) or the overall
/// budget elapses. On timeout, return the last error so the caller sees WHY it
/// never came up (never a fabricated readiness).
fn poll_ready(port: u16) -> PyResult<()> {
    let url = heartbeat_url(port);
    let deadline = Instant::now() + READINESS_TIMEOUT;
    let mut last_err: String;
    loop {
        match POLL_CLIENT.get(&url).send() {
            Ok(resp) if resp.status().is_success() => return Ok(()),
            Ok(resp) => last_err = format!("heartbeat returned status {}", resp.status()),
            Err(e) => last_err = format!("heartbeat request failed: {e}"),
        }
        if Instant::now() >= deadline {
            return Err(PyError::other(format!(
                "ChromaDB did not become ready at {url} within {}s: {last_err}",
                READINESS_TIMEOUT.as_secs()
            )));
        }
        std::thread::sleep(POLL_INTERVAL);
    }
}

// ---------------------------------------------------------------------------
// status / stop subcommands (selection-aware: handle BOTH backends)
// ---------------------------------------------------------------------------

/// Print the current ChromaDB status (the `odysseus chroma status` subcommand).
///
/// Handles BOTH backends. Reports, in order: the docker container line (when a
/// docker binary is present and our container exists), the local server (a
/// `launcher.pid` + a live heartbeat), and finally whether the heartbeat answers at
/// all. Returns `Ok` even when nothing is found (prints a clear message). Honest
/// `Err` only on an unexpected docker invocation failure.
pub fn status() -> PyResult<()> {
    let port = host_port();
    let mut reported = false;

    // DOCKER: only if docker is present (absence is not an error here — the local
    // backend may be in use). A real docker failure IS surfaced.
    if let Some(docker) = find_docker() {
        let out = run_docker(&docker, &ps_args())?;
        if let Some((name, status)) = parse_ps_line(&out) {
            println!("docker\t{name}\t{status}");
            reported = true;
        }
    }

    // LOCAL: a recorded PID file + (optionally) a live process.
    if let Some(pid) = read_pid_file() {
        let alive = local_pid_alive(pid);
        println!(
            "local\tpid={pid}\t{}",
            if alive { "running" } else { "not running" }
        );
        reported = true;
    }

    // Reachability — the ultimate truth either backend converges on.
    let beat = heartbeat_ok(port);
    println!(
        "heartbeat\t{}\t{}",
        reachable_url(port),
        if beat { "ok" } else { "unreachable" }
    );

    if !reported && !beat {
        println!(
            "No ChromaDB found: no docker container '{CONTAINER_NAME}', no local launcher.pid, \
             and {} is unreachable.",
            reachable_url(port)
        );
    }
    Ok(())
}

/// Whether a recorded local pid is alive (`kill(pid, 0)`), Unix-only. On non-unix
/// (no signals) fall back to the heartbeat being the source of truth, so report
/// "unknown via signal" as not-alive (the heartbeat line still prints the truth).
fn local_pid_alive(pid: u32) -> bool {
    #[cfg(unix)]
    {
        let raw = match i32::try_from(pid) {
            Ok(raw) => raw,
            Err(_) => return false,
        };
        // `kill(pid, 0)` succeeds for a live process AND for an unreaped zombie (a
        // child that has exited but has not yet been `waitpid`'d). A zombie is not a
        // running server, so reject it: treat a `Z`/defunct process as dead.
        if nix::sys::signal::kill(nix::unistd::Pid::from_raw(raw), None).is_err() {
            return false;
        }
        !pid_is_zombie(pid)
    }
    #[cfg(not(unix))]
    {
        let _ = pid;
        false
    }
}

/// Whether `pid` is in the zombie/defunct state. `ps -o stat=` prints the process
/// STAT code; a leading `Z` marks a defunct (exited-but-unreaped) process. Because
/// such a child still satisfies `kill(pid, 0)`, this is how [`local_pid_alive`] and
/// [`stop_local_process`] distinguish a genuinely-running server from a
/// dead-but-unreaped one. A `ps` failure (or no matching pid) is treated as "not a
/// zombie": the caller has already established the pid is signalable, so it falls
/// back to that. Portable across macOS and Linux (no `/proc` dependency).
#[cfg(unix)]
fn pid_is_zombie(pid: u32) -> bool {
    let output = match Command::new("ps")
        .args(["-o", "stat=", "-p", &pid.to_string()])
        .output()
    {
        Ok(o) => o,
        Err(_) => return false,
    };
    if !output.status.success() {
        return false;
    }
    String::from_utf8_lossy(&output.stdout)
        .trim_start()
        .starts_with('Z')
}

/// Stop the ChromaDB server (the `odysseus chroma stop` subcommand). Unlike
/// [`ChromaHandle::stop`], this is an explicit user request, so it stops the server
/// regardless of who started it.
///
/// Handles BOTH backends: a recorded local `launcher.pid` is SIGTERM'd (and the PID
/// file removed); a docker container is `docker stop`ped. If BOTH exist, both are
/// stopped. Honest `Err` only when NEITHER a local pid nor a docker stop succeeds
/// (so the user is not told "stopped" when nothing was running).
pub fn stop_container() -> PyResult<()> {
    let mut stopped_any = false;
    let mut errors: Vec<String> = Vec::new();

    // LOCAL: a recorded PID file -> SIGTERM + best-effort reap + remove the file.
    if let Some(pid) = read_pid_file() {
        stop_local_process(pid);
        let _ = std::fs::remove_file(launcher_pid_path());
        println!("Stopped local ChromaDB server (pid {pid}).");
        stopped_any = true;
    }

    // DOCKER: stop our named container if docker is present AND the container exists.
    if let Some(docker) = find_docker() {
        // Only attempt a stop when the container actually exists (a `docker stop` of
        // a missing container is a non-zero error we should not surface as the whole
        // command failing when a local server was already stopped above).
        let exists = run_docker(&docker, &ps_args())
            .ok()
            .and_then(|o| parse_ps_line(&o))
            .is_some();
        if exists {
            match run_docker(&docker, &["stop".to_string(), CONTAINER_NAME.to_string()]) {
                Ok(_) => {
                    println!("Stopped ChromaDB container '{CONTAINER_NAME}'.");
                    stopped_any = true;
                }
                Err(e) => errors.push(format!("docker stop: {e}")),
            }
        }
    }

    if stopped_any {
        return Ok(());
    }
    if !errors.is_empty() {
        return Err(PyError::other(format!(
            "chroma stop failed: {}",
            errors.join("; ")
        )));
    }
    Err(PyError::other(format!(
        "Nothing to stop: no local launcher.pid and no docker container '{CONTAINER_NAME}'.",
    )))
}

#[cfg(test)]
mod tests {
    use super::*;

    // --- Docker arg builders (unchanged behavior) ---------------------------

    #[test]
    fn run_args_mirror_compose_service() {
        let args = run_args(8100, "/abs/data/chroma");
        assert_eq!(
            args,
            vec![
                "run".to_string(),
                "-d".to_string(),
                "--name".to_string(),
                "odysseus-chromadb".to_string(),
                // docker-parity with compose's `restart: unless-stopped`.
                "--restart".to_string(),
                "unless-stopped".to_string(),
                "-p".to_string(),
                "8100:8000".to_string(),
                "-v".to_string(),
                "/abs/data/chroma:/chroma/chroma".to_string(),
                "-e".to_string(),
                "ANONYMIZED_TELEMETRY=FALSE".to_string(),
                "chromadb/chroma:latest".to_string(),
            ]
        );
    }

    #[test]
    fn telemetry_env_pair_matches_docker_dash_e() {
        // The local spawn must set the SAME env the docker `-e` passes (single
        // source of truth: TELEMETRY_ENV). apply_local_env splits this pair onto
        // the child process environment.
        assert_eq!(
            telemetry_env_pair(TELEMETRY_ENV),
            Some(("ANONYMIZED_TELEMETRY", "FALSE"))
        );
    }

    #[test]
    fn run_args_honor_custom_port() {
        let args = run_args(9001, "/data/chroma");
        let joined = args.join(" ");
        assert!(joined.contains("-p 9001:8000"));
        assert!(joined.contains("-v /data/chroma:/chroma/chroma"));
    }

    #[test]
    fn ps_args_filter_exact_name() {
        let args = ps_args();
        assert_eq!(args[0], "ps");
        assert!(args.contains(&"-a".to_string()));
        assert!(args.contains(&"name=^/odysseus-chromadb$".to_string()));
        assert!(args.contains(&"{{.Names}}\t{{.Status}}".to_string()));
    }

    #[test]
    fn heartbeat_url_targets_v2_on_localhost() {
        assert_eq!(heartbeat_url(8100), "http://localhost:8100/api/v2/heartbeat");
        assert_eq!(heartbeat_url(9001), "http://localhost:9001/api/v2/heartbeat");
    }

    #[test]
    fn reachable_url_is_host_loopback() {
        assert_eq!(reachable_url(8100), "http://localhost:8100");
    }

    #[test]
    fn host_port_defaults_to_8100() {
        assert_eq!(DEFAULT_HOST_PORT, 8100);
    }

    #[test]
    fn parse_ps_line_extracts_name_and_status() {
        let (name, status) = parse_ps_line("odysseus-chromadb\tUp 3 minutes").expect("a line");
        assert_eq!(name, "odysseus-chromadb");
        assert_eq!(status, "Up 3 minutes");
    }

    #[test]
    fn parse_ps_line_none_when_absent() {
        assert!(parse_ps_line("").is_none());
        assert!(parse_ps_line("   \n  ").is_none());
    }

    #[test]
    fn parse_ps_line_handles_missing_status_column() {
        let (name, status) = parse_ps_line("odysseus-chromadb").expect("name only");
        assert_eq!(name, "odysseus-chromadb");
        assert_eq!(status, "");
    }

    #[test]
    fn status_is_running_distinguishes_up_from_exited() {
        assert!(status_is_running("Up 3 minutes"));
        assert!(status_is_running("up 10 seconds (healthy)"));
        assert!(!status_is_running("Exited (0) 2 minutes ago"));
        assert!(!status_is_running("Created"));
        assert!(!status_is_running(""));
    }

    #[test]
    fn handle_stop_is_noop_when_not_started_by_us() {
        // A reused server's handle must not attempt any stop. The branch is taken on
        // `started_by_us == false` and returns before touching docker / signals.
        let docker = ChromaHandle {
            name: CONTAINER_NAME.to_string(),
            target: ChromaTarget::Docker {
                name: CONTAINER_NAME.to_string(),
            },
            started_by_us: false,
        };
        docker.stop(); // must short-circuit / not panic.
        let local = ChromaHandle {
            name: "local:8100".to_string(),
            target: ChromaTarget::Local { pid: 999_999 },
            started_by_us: false,
        };
        local.stop(); // must short-circuit / not panic (no SIGTERM sent).
    }

    // --- Local backend: the python-probe arg builder ------------------------

    #[test]
    fn probe_args_import_the_full_cli_submodule() {
        // The probe is `python -c "import chromadb.cli.cli"` — the FULL package's
        // server CLI (which a `chromadb-client`-only venv lacks).
        assert_eq!(
            probe_args(),
            vec!["-c".to_string(), "import chromadb.cli.cli".to_string()]
        );
    }

    // --- Local backend: the launch arg vector -------------------------------

    #[test]
    fn local_launch_args_are_collision_proof() {
        // The COLLISION-PROOF command: `-c "from chromadb.cli.cli import app; app()"
        // run --path <data> --host localhost --port <port>`. NOT `-m chromadb.cli.cli`
        // (no __main__ guard) and NOT the bare `chroma` binary (a Go highlighter).
        let args = local_launch_args(8100, "/abs/data/chroma");
        assert_eq!(
            args,
            vec![
                "-c".to_string(),
                "from chromadb.cli.cli import app; app()".to_string(),
                "run".to_string(),
                "--path".to_string(),
                "/abs/data/chroma".to_string(),
                "--host".to_string(),
                "localhost".to_string(),
                "--port".to_string(),
                "8100".to_string(),
            ]
        );
    }

    #[test]
    fn local_launch_args_honor_custom_port_and_path() {
        let args = local_launch_args(9123, "/tmp/cdata/chroma");
        let joined = args.join(" ");
        assert!(joined.contains("--path /tmp/cdata/chroma"));
        assert!(joined.contains("--host localhost"));
        assert!(joined.contains("--port 9123"));
        // The first two tokens are always the collision-proof `-c <code>`.
        assert_eq!(args[0], "-c");
        assert_eq!(args[1], "from chromadb.cli.cli import app; app()");
    }

    // --- Local backend: candidate ORDER -------------------------------------

    #[test]
    fn python_candidates_order_explicit_then_venv_then_path_names() {
        // With ODYSSEUS_CHROMA_PYTHON unset, the order is venv first, then python3,
        // then python. (We do not mutate the process env here — passing an explicit
        // base_dir keeps the venv candidate deterministic.)
        let cands = python_candidates("/base");
        // The venv interpreter is first (when no explicit override is set).
        assert_eq!(cands[0], "/base/venv/bin/python3");
        // The last two are the bare-name resolutions (python3 then python). They may
        // be absolute (resolved via PATH) or the bare name (when not on PATH); either
        // way their basenames are python3 / python in order.
        let last_two = &cands[cands.len() - 2..];
        assert!(last_two[0].ends_with("python3"));
        assert!(last_two[1].ends_with("python"));
        // python3 strictly precedes python.
        let i3 = cands.iter().position(|c| c.ends_with("python3")).unwrap();
        let i = cands.iter().rposition(|c| c.ends_with("python")).unwrap();
        assert!(i3 < i);
    }

    // --- Launcher mode parsing ----------------------------------------------

    #[test]
    fn parse_launcher_mode_matrix() {
        assert_eq!(parse_launcher_mode(Some("local")), LauncherMode::Local);
        assert_eq!(parse_launcher_mode(Some("docker")), LauncherMode::Docker);
        assert_eq!(parse_launcher_mode(Some("auto")), LauncherMode::Auto);
        // Case / whitespace insensitive.
        assert_eq!(parse_launcher_mode(Some("  LOCAL ")), LauncherMode::Local);
        assert_eq!(parse_launcher_mode(Some("Docker")), LauncherMode::Docker);
        // Unset / empty / unrecognized -> auto (the conservative default).
        assert_eq!(parse_launcher_mode(None), LauncherMode::Auto);
        assert_eq!(parse_launcher_mode(Some("")), LauncherMode::Auto);
        assert_eq!(parse_launcher_mode(Some("nonsense")), LauncherMode::Auto);
    }

    // --- Launcher selection logic (pure: mode x availability) ---------------

    #[test]
    fn resolve_launcher_auto_prefers_local_then_docker() {
        // auto + python present -> Local (even if docker is also present).
        assert_eq!(
            resolve_launcher(LauncherMode::Auto, true, true).unwrap(),
            Launcher::Local
        );
        assert_eq!(
            resolve_launcher(LauncherMode::Auto, true, false).unwrap(),
            Launcher::Local
        );
        // auto + no python but docker -> Docker.
        assert_eq!(
            resolve_launcher(LauncherMode::Auto, false, true).unwrap(),
            Launcher::Docker
        );
        // auto + neither -> honest error naming BOTH options.
        let err = resolve_launcher(LauncherMode::Auto, false, false).unwrap_err();
        let msg = format!("{err}");
        assert!(msg.contains("chromadb"));
        assert!(msg.to_lowercase().contains("docker"));
    }

    #[test]
    fn resolve_launcher_forced_local_requires_python() {
        assert_eq!(
            resolve_launcher(LauncherMode::Local, true, false).unwrap(),
            Launcher::Local
        );
        // Forced local with no python -> honest error (does NOT silently fall to docker).
        let err = resolve_launcher(LauncherMode::Local, false, true).unwrap_err();
        assert!(format!("{err}").contains("local"));
    }

    #[test]
    fn resolve_launcher_forced_docker_requires_docker() {
        assert_eq!(
            resolve_launcher(LauncherMode::Docker, false, true).unwrap(),
            Launcher::Docker
        );
        // Forced docker with no docker -> honest error (does NOT silently fall to local).
        let err = resolve_launcher(LauncherMode::Docker, true, false).unwrap_err();
        assert!(format!("{err}").contains("docker"));
    }

    // --- PID file path ------------------------------------------------------

    #[test]
    fn launcher_pid_path_is_under_chroma_data_dir() {
        // The PID file lives at `<DATA_DIR>/chroma/launcher.pid` — beside the data
        // the local --path / docker volume use.
        let pid_path = launcher_pid_path();
        assert!(pid_path.ends_with("launcher.pid"));
        // Its parent is the SAME absolute dir the launch command targets.
        let parent = pid_path.parent().unwrap().to_string_lossy().into_owned();
        assert_eq!(parent, data_volume_host_path());
        // And that dir's basename is `chroma`.
        assert!(parent.ends_with("chroma"));
    }

    #[test]
    fn launcher_log_path_is_under_chroma_data_dir() {
        let log_path = launcher_log_path();
        assert!(log_path.ends_with("launcher.log"));
        assert_eq!(
            log_path.parent().unwrap().to_string_lossy().into_owned(),
            data_volume_host_path()
        );
    }

    // The running test process is itself a live, non-zombie pid we are allowed to
    // signal — so it is a deterministic fixture for the liveness helpers.
    #[cfg(unix)]
    #[test]
    fn pid_is_zombie_false_for_running_self() {
        assert!(!pid_is_zombie(std::process::id()));
    }

    #[cfg(unix)]
    #[test]
    fn local_pid_alive_true_for_running_self() {
        // Alive AND not a zombie -> reported running.
        assert!(local_pid_alive(std::process::id()));
    }
}
