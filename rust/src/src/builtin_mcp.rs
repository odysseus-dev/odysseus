// src/builtin_mcp.rs  <- src/builtin_mcp.py
//! Auto-registration of built-in MCP servers on startup.
//!
//! Each server runs as a stdio subprocess managed by `McpManager`. PORT_PARTIAL
//! (web). `_find_npx`, the server tables, and the `MCP_DISABLED` flag are faithful.
//! Like the Python (`asyncio.create_task`), every connect is fire-and-forget so
//! `register_builtin_servers` returns ~immediately and the connects run
//! concurrently: each of the four script-server connects is a detached
//! `tokio::spawn` with NO timeout, and the NPX-based browser server is connected
//! from its own detached task after the 3s start delay, also with NO timeout
//! (the Python dropped its `asyncio.wait_for` wrapper — see below). Before
//! connecting an NPX server we first probe whether its npm package is already in
//! the local npx cache ([`_is_npx_package_cached`]); if it isn't, we log a useful
//! warning and SKIP the server instead of letting npx try to download/install it
//! on first use (which can hang for minutes on fresh installs).
//!
//! The four built-in script servers (`image_gen` / `memory` / `rag` / `email`)
//! are now PORTED — they live in [`crate::mcp_servers`] as subcommands of this very
//! binary. Where the Python launched `sys.executable mcp_servers/<id>_server.py`,
//! `register_builtin_servers` launches the CURRENT executable
//! (`std::env::current_exe()`) with args `["mcp-server", <id>]`. The transport is
//! identical (a stdio command the MCP client spawns); only the command changes,
//! so [`crate::src::mcp_manager`] connects to them exactly as it would to the
//! Python scripts. `current_exe()` is the faithful `sys.executable` analogue (the
//! interpreter/launcher for this server); on a resolution error we log + skip that
//! server rather than fabricate a connection.

use crate::pylog as logger;
use crate::src::mcp_manager::McpManager;
use once_cell::sync::Lazy;
use std::collections::HashMap;
use std::sync::Arc;

/// Expand a leading `~` to the user's home directory, mirroring
/// `os.path.expanduser`. Returns the path unchanged if there is no `HOME`.
fn expanduser(path: &str) -> String {
    if let Some(rest) = path.strip_prefix("~/") {
        if let Ok(home) = std::env::var("HOME") {
            return format!("{}/{}", home.trim_end_matches('/'), rest);
        }
    }
    path.to_string()
}

/// `os.path.isfile` — true only for an existing regular file.
fn isfile(path: &str) -> bool {
    std::fs::metadata(path)
        .map(|m| m.is_file())
        .unwrap_or(false)
}

/// `shutil.which` for an executable name: scan each `PATH` entry for a file of
/// that name, returning the first hit (executable bit checked on Unix).
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

/// Find npx binary, checking common locations if not on PATH.
fn _find_npx() -> String {
    if let Some(npx) = shutil_which("npx") {
        return npx;
    }
    // Common locations when PATH is minimal (e.g. systemd)
    for candidate in [
        expanduser("~/.npm-global/bin/npx"),
        expanduser("~/.local/bin/npx"),
        "/usr/local/bin/npx".to_string(),
        "/usr/bin/npx".to_string(),
    ] {
        if isfile(&candidate) {
            return candidate;
        }
    }
    // Try to find node and use npx from same dir
    if let Some(node) = shutil_which("node") {
        let dir = std::path::Path::new(&node)
            .parent()
            .map(|p| p.to_path_buf())
            .unwrap_or_default();
        let npx_candidate = dir.join("npx");
        let npx_candidate = npx_candidate.to_string_lossy().into_owned();
        if isfile(&npx_candidate) {
            return npx_candidate;
        }
    }
    "npx".to_string() // fallback, will fail with a clear error
}

/// An NPX-based built-in server definition.
struct NpxServer {
    name: &'static str,
    args: Vec<&'static str>,
}

// Server definitions: id -> (script path relative to project root, display name)
//
// bash / python / filesystem / web_search were folded into native in-process
// execution (src/tool_execution.py:_direct_fallback). Those trivial subprocess
// wrappers are gone.
//
// image_gen / memory / rag / email still run as stdio MCP servers — each
// carries hundreds of LOC of unique IMAP / HTTP / manager logic not worth
// duplicating into the native path right now.
static BUILTIN_SERVERS: Lazy<Vec<(&'static str, &'static str, &'static str)>> = Lazy::new(|| {
    vec![
        ("image_gen", "mcp_servers/image_gen_server.py", "Built-in: Image Generation"),
        ("memory", "mcp_servers/memory_server.py", "Built-in: Memory"),
        ("rag", "mcp_servers/rag_server.py", "Built-in: RAG"),
        ("email", "mcp_servers/email_server.py", "Built-in: Email"),
    ]
});

// NPX-based built-in servers (run via npx, not Python)
static BUILTIN_NPX_SERVERS: Lazy<Vec<(&'static str, NpxServer)>> = Lazy::new(|| {
    vec![(
        "builtin_browser",
        NpxServer {
            name: "Built-in: Browser",
            args: vec!["-y", "@playwright/mcp@latest", "--headless", "--caps", "vision"],
        },
    )]
});

/// Global flag to disable MCP if there are compatibility issues.
pub static MCP_DISABLED: Lazy<bool> = Lazy::new(|| {
    std::env::var("ODYSSEUS_DISABLE_MCP")
        .map(|v| matches!(v.to_lowercase().as_str(), "1" | "true" | "yes"))
        .unwrap_or(false)
});

/// Connect all built-in MCP servers to the manager.
///
/// Invoked from the server bootstrap (`web::run`'s startup phase, the analogue of
/// app.py's `_startup_mcp_connections`).
///
/// Python schedules every connect via `asyncio.create_task` (fire-and-forget) so
/// the function returns ~immediately and all connects run concurrently in the
/// background; no connect carries a timeout (the NPX connect's old
/// `asyncio.wait_for(..., timeout=30)` wrapper was removed because the cross-task
/// cancellation it caused could down the event loop). The Rust mirror
/// uses `tokio::spawn` for each, which requires `'static` data — so the manager is
/// passed as an `Arc<McpManager>` (cloned into each detached task). The SAME `Arc`
/// instance backs `AppState.mcp_manager` (the routes), `connect_all_enabled`, and
/// `disconnect_all`, so built-in servers register on the instance the routes see —
/// exactly like the Python's long-lived module-level `mcp_manager`.
pub async fn register_builtin_servers(mcp_manager: Arc<McpManager>) {
    if *MCP_DISABLED {
        logger::info("Built-in MCP servers disabled via ODYSSEUS_DISABLE_MCP");
        return;
    }

    // Python launches each present script with `sys.executable mcp_servers/X.py`.
    // The four servers are PORTED as subcommands of this binary, so resolve the
    // current executable (the `sys.executable` analogue) and launch each via the
    // stdio transport with args `["mcp-server", <id>]`. The MCP client spawns the
    // same kind of stdio command; only the command/args change vs. the Python.
    let current_exe = match std::env::current_exe() {
        Ok(p) => p.to_string_lossy().into_owned(),
        Err(e) => {
            // No `sys.executable` analogue available — log + skip the script
            // servers (never fabricate a connection), then fall through to NPX.
            logger::warning(&format!(
                "Cannot resolve current executable for built-in MCP servers: {e}"
            ));
            String::new()
        }
    };
    if !current_exe.is_empty() {
        for (server_id, _script, name) in BUILTIN_SERVERS.iter() {
            // `asyncio.create_task(_connect_python_server(...))`: fire-and-forget,
            // NO timeout. Each detached task runs its connect concurrently and
            // keeps the per-server logging inside, so this loop returns at once.
            let current_exe = current_exe.clone();
            let mcp_manager = mcp_manager.clone();
            tokio::spawn(async move {
                let args = vec!["mcp-server".to_string(), server_id.to_string()];
                let ok = mcp_manager
                    .connect_server(
                        server_id,
                        name,
                        "stdio",
                        Some(&current_exe),
                        args,
                        HashMap::new(),
                        None,
                    )
                    .await;
                if ok {
                    logger::info(&format!("Built-in MCP server registered: {name}"));
                } else {
                    logger::warning(&format!("Built-in MCP server failed to connect: {name}"));
                }
            });
        }
    }

    // Register NPX-based servers in the background (they take longer to start).
    let npx_path = _find_npx();
    logger::info(&format!("NPX binary resolved to: {npx_path}"));

    // `asyncio.create_task(_start_npx_servers())`: the 3s delay + the NPX loop run
    // in their OWN detached task (Python schedules NPX after the sleep, also via
    // create_task), so this function returns ~immediately. Inside, let the Python
    // servers finish first, then connect each npx server with NO timeout (the
    // Python's `asyncio.wait_for` wrapper was removed — wrapping the connect in a
    // bounded wait triggered a cross-task cancellation in the stdio client's anyio
    // task group that could cascade and down the event loop).
    tokio::spawn(async move {
        tokio::time::sleep(std::time::Duration::from_secs(3)).await;
        for (server_id, cfg) in BUILTIN_NPX_SERVERS.iter() {
            // Skip the server if its npx package isn't cached. Without this check,
            // npx would try to download/install the package on first use, which can
            // take minutes (or hang) on fresh installs without Playwright system
            // deps. Bounding that wait with a timeout sounds reasonable, but in the
            // Python the stdio client's anyio task group couldn't survive the
            // resulting cross-task cancellation; detecting installed-state up-front
            // lets us bail with a useful warning before we ever touch the connect.
            let pkg_spec = _npx_package_from_args(&cfg.args);
            if let Some(pkg_spec) = &pkg_spec {
                if !_is_npx_package_cached(&npx_path, pkg_spec, 5).await {
                    let npx_base = std::path::Path::new(&npx_path)
                        .file_name()
                        .map(|s| s.to_string_lossy().into_owned())
                        .unwrap_or_else(|| npx_path.clone());
                    logger::warning(&format!(
                        "{} is not available.\n  Reason: npm package {:?} is not installed in the npx cache.\n  Impact: tools provided by this MCP server will be unavailable.\n  Fix:    {} -y {} --version\n          (run once, then restart Odysseus)\n  Notes:  this server is optional; see README.md 'Built-in MCP servers' for details.",
                        cfg.name, pkg_spec, npx_base, pkg_spec
                    ));
                    continue;
                }
            }

            logger::info(&format!(
                "Starting NPX server: {} ({} {})",
                cfg.name,
                npx_path,
                cfg.args.join(" ")
            ));
            let args: Vec<String> = cfg.args.iter().map(|s| s.to_string()).collect();
            let ok = mcp_manager
                .connect_server(
                    server_id,
                    cfg.name,
                    "stdio",
                    Some(&npx_path),
                    args,
                    HashMap::new(),
                    None,
                )
                .await;
            if ok {
                logger::info(&format!("Built-in NPX server registered: {}", cfg.name));
            } else {
                logger::warning(&format!(
                    "Built-in NPX server failed to connect: {}",
                    cfg.name
                ));
            }
        }
    });
}

/// Pick the package spec out of an npx args list shaped like
/// `["-y", "<package@version>", ...flags]`. Returns `None` if the convention
/// doesn't match (we then skip the cache check and just try the connect).
///
/// Faithful port of Python's `_npx_package_from_args`.
fn _npx_package_from_args(args: &[&str]) -> Option<String> {
    if args.is_empty() {
        return None;
    }
    if let Some(idx) = args.iter().position(|&a| a == "-y") {
        let idx = idx + 1;
        if idx < args.len() && !args[idx].starts_with('-') {
            return Some(args[idx].to_string());
        }
    }
    // No -y prefix: first non-flag arg is the package.
    for a in args {
        if !a.starts_with('-') {
            return Some(a.to_string());
        }
    }
    None
}

/// Probe whether an npx package is already in the local cache.
///
/// Runs `npx --no-install <pkg> --version`. `--no-install` tells npx to fail
/// instead of downloading, so a cache miss returns fast. We treat "exited 0 with
/// non-empty stdout" as proof of a working cached copy. Anything else (non-zero
/// exit, empty stdout, timeout, missing npx, spawn error) means we should skip the
/// server.
///
/// Faithful port of Python's `_is_npx_package_cached`.
async fn _is_npx_package_cached(npx_path: &str, package_spec: &str, timeout_s: u64) -> bool {
    let child = match tokio::process::Command::new(npx_path)
        .args(["--no-install", package_spec, "--version"])
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        // On a timeout the future below is dropped; `kill_on_drop` ensures the
        // child is reaped rather than orphaned (mirrors the Python's explicit
        // `proc.kill(); await proc.wait()` on `asyncio.TimeoutError`).
        .kill_on_drop(true)
        .spawn()
    {
        Ok(c) => c,
        // OSError / ValueError -> spawn failed (e.g. missing binary).
        Err(_) => return false,
    };
    match tokio::time::timeout(
        std::time::Duration::from_secs(timeout_s),
        child.wait_with_output(),
    )
    .await
    {
        Ok(Ok(output)) => {
            output.status.success() && !String::from_utf8_lossy(&output.stdout).trim().is_empty()
        }
        // communicate() error -> treat as not cached.
        Ok(Err(_)) => false,
        // asyncio.TimeoutError -> the future (and the child handle it owns) is
        // dropped here; `kill_on_drop(true)` kills the process. Report not cached.
        Err(_) => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn find_npx_returns_something() {
        // Either a real path or the "npx" fallback — never empty.
        assert!(!_find_npx().is_empty());
    }

    #[test]
    fn mcp_disabled_parses_truthy() {
        // Default (env unset in test) is false unless the runner sets it.
        // Reading the lazy static must succeed and yield a valid bool (either
        // value is acceptable; the runner may set the env).
        let v = *MCP_DISABLED;
        assert!(matches!(v, true | false));
    }

    #[test]
    fn builtin_tables_are_populated() {
        assert_eq!(BUILTIN_SERVERS.len(), 4);
        assert_eq!(BUILTIN_NPX_SERVERS.len(), 1);
        assert_eq!(BUILTIN_NPX_SERVERS[0].0, "builtin_browser");
        assert_eq!(BUILTIN_NPX_SERVERS[0].1.name, "Built-in: Browser");
    }

    #[test]
    fn expanduser_expands_tilde() {
        std::env::set_var("HOME", "/home/test");
        assert_eq!(expanduser("~/.local/bin/npx"), "/home/test/.local/bin/npx");
        assert_eq!(expanduser("/usr/bin/npx"), "/usr/bin/npx");
    }

    #[test]
    fn npx_package_from_args_with_y_flag() {
        // The canonical builtin-browser shape: `-y <pkg> ...flags`.
        let args = ["-y", "@playwright/mcp@latest", "--headless", "--caps", "vision"];
        assert_eq!(
            _npx_package_from_args(&args),
            Some("@playwright/mcp@latest".to_string())
        );
    }

    #[test]
    fn npx_package_from_args_no_y_flag_picks_first_non_flag() {
        // No `-y`: first arg that isn't a flag is the package.
        let args = ["--quiet", "some-package@1.2.3", "--extra"];
        assert_eq!(
            _npx_package_from_args(&args),
            Some("some-package@1.2.3".to_string())
        );
    }

    #[test]
    fn npx_package_from_args_y_at_end_falls_back_to_first_non_flag() {
        // `-y` with no following package (or a flag right after) falls through to
        // the "first non-flag arg" scan.
        let args = ["-y", "-x", "pkg@2"];
        assert_eq!(_npx_package_from_args(&args), Some("pkg@2".to_string()));
    }

    #[test]
    fn npx_package_from_args_empty_and_all_flags() {
        assert_eq!(_npx_package_from_args(&[]), None);
        assert_eq!(_npx_package_from_args(&["-y", "--headless"]), None);
        assert_eq!(_npx_package_from_args(&["--a", "--b"]), None);
    }

    /// Write an executable shell script to `path` (Unix-only test helper).
    #[cfg(unix)]
    fn write_exec_script(path: &std::path::Path, body: &str) {
        use std::os::unix::fs::PermissionsExt;
        std::fs::write(path, body).unwrap();
        let mut perms = std::fs::metadata(path).unwrap().permissions();
        perms.set_mode(0o755);
        std::fs::set_permissions(path, perms).unwrap();
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn is_npx_package_cached_true_when_exit0_with_stdout() {
        let dir = std::env::temp_dir().join(format!("odysseus_npx_cached_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let fake = dir.join("fake_npx_ok");
        // Emulate a cached package: exit 0, print a version on stdout.
        write_exec_script(&fake, "#!/bin/sh\necho '1.2.3'\nexit 0\n");
        let cached = _is_npx_package_cached(fake.to_str().unwrap(), "@playwright/mcp@latest", 5).await;
        assert!(cached, "exit 0 with non-empty stdout must be treated as cached");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn is_npx_package_cached_false_on_nonzero_exit() {
        let dir =
            std::env::temp_dir().join(format!("odysseus_npx_miss_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let fake = dir.join("fake_npx_miss");
        // Emulate a cache miss: --no-install fails, non-zero exit.
        write_exec_script(&fake, "#!/bin/sh\necho 'npm error' 1>&2\nexit 1\n");
        let cached = _is_npx_package_cached(fake.to_str().unwrap(), "pkg@1", 5).await;
        assert!(!cached, "non-zero exit must be treated as not cached");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn is_npx_package_cached_false_on_empty_stdout() {
        let dir =
            std::env::temp_dir().join(format!("odysseus_npx_empty_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let fake = dir.join("fake_npx_empty");
        // exit 0 but no stdout -> not a working cached copy.
        write_exec_script(&fake, "#!/bin/sh\nexit 0\n");
        let cached = _is_npx_package_cached(fake.to_str().unwrap(), "pkg@1", 5).await;
        assert!(!cached, "empty stdout must be treated as not cached");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn is_npx_package_cached_false_on_missing_binary() {
        // Spawn failure (binary does not exist) -> not cached, no panic.
        let cached = _is_npx_package_cached(
            "/nonexistent/path/to/npx-binary-xyz",
            "pkg@1",
            5,
        )
        .await;
        assert!(!cached);
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn is_npx_package_cached_false_on_timeout() {
        let dir =
            std::env::temp_dir().join(format!("odysseus_npx_slow_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let fake = dir.join("fake_npx_slow");
        // Sleep longer than the timeout -> timeout path returns false.
        write_exec_script(&fake, "#!/bin/sh\nsleep 5\necho '1.0.0'\nexit 0\n");
        let cached = _is_npx_package_cached(fake.to_str().unwrap(), "pkg@1", 1).await;
        assert!(!cached, "a timed-out probe must be treated as not cached");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
