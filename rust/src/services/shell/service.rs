// services/shell/service.rs  <- the Python `services/shell/service.py`
//! Shell service — safe command execution.
//!
//! Faithful port of `services/shell/service.py`. Two entry points:
//!   * [`ShellService::execute`] — run a command and collect its full output,
//!     bounded by a timeout (the `asyncio.create_subprocess_shell` +
//!     `asyncio.wait_for(proc.communicate(), …)` path).
//!   * [`ShellService::stream`] — run a command and stream `stdout`/`stderr`
//!     lines as they arrive, then a final `{"exit_code": …}` event (the
//!     async-generator `stream()` path).
//!
//! SECURITY: like the Python, this runs **arbitrary, unsandboxed shell
//! commands** through `sh -c` with no allow-list. The faithful translation
//! preserves that behaviour verbatim. ORPHAN: nothing in the codebase
//! instantiates `ShellService` (it is a stand-alone service-layer facade);
//! it is ported for parity with the Python `services/` tree.
//!
//! Port notes:
//!   * Python's `asyncio.create_subprocess_shell(cmd)` runs `cmd` through the
//!     platform shell (`/bin/sh -c` on POSIX). We mirror that with
//!     `tokio::process::Command::new("sh").arg("-c").arg(command)`.
//!   * `bytes.decode(errors="replace")` -> `String::from_utf8_lossy` (both
//!     substitute U+FFFD for invalid sequences).
//!   * Python's `decoded[:self.max_output]` slices by Unicode code point on an
//!     already-decoded `str`; we truncate by `char` count to match.
//!   * `Path.home()` reads `$HOME` on POSIX (with a `pwd` fallback); we read
//!     `HOME`, falling back to `"."` if unset.

use std::process::Stdio;

use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncReadExt, BufReader};
use tokio::process::Command;

use crate::pylog as logger;

/// `dataclass ShellResult` — result of a shell command.
///
/// ```python
/// @dataclass
/// class ShellResult:
///     stdout: str
///     stderr: str
///     exit_code: int
///     timed_out: bool = False
/// ```
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ShellResult {
    pub stdout: String,
    pub stderr: String,
    pub exit_code: i32,
    pub timed_out: bool,
}

/// `class ShellService` — shell execution service.
///
/// ```python
/// service = ShellService()
/// result = await service.execute("ls -la")
/// print(result.stdout)
/// ```
#[derive(Debug, Clone)]
pub struct ShellService {
    /// Default timeout, in seconds (`self.timeout`).
    pub timeout: u64,
    /// Maximum number of output characters retained per stream
    /// (`self.max_output`).
    pub max_output: usize,
    /// Working directory commands run in (`self.cwd`, `Path.home()`).
    pub cwd: String,
}

impl Default for ShellService {
    fn default() -> Self {
        Self::new(30, 200_000)
    }
}

impl ShellService {
    /// `def __init__(self, timeout: int = 30, max_output: int = 200_000)`
    pub fn new(timeout: u64, max_output: usize) -> Self {
        Self {
            timeout,
            max_output,
            // `self.cwd = str(Path.home())`
            cwd: home_dir(),
        }
    }

    /// `async def execute(self, command, timeout=None, cwd=None) -> ShellResult`
    ///
    /// Execute a shell command.
    ///
    /// * `command` — shell command to run
    /// * `timeout` — timeout in seconds (default: `self.timeout`)
    /// * `cwd` — working directory (default: home)
    ///
    /// Returns a [`ShellResult`] with `stdout`, `stderr`, `exit_code`.
    pub async fn execute(
        &self,
        command: &str,
        timeout: Option<u64>,
        cwd: Option<&str>,
    ) -> ShellResult {
        // `timeout = timeout or self.timeout` / `cwd = cwd or self.cwd`
        let timeout = timeout.unwrap_or(self.timeout);
        let cwd = cwd.unwrap_or(&self.cwd);
        let duration = std::time::Duration::from_secs(timeout);

        // `proc = await asyncio.create_subprocess_shell(command, …)`
        let mut child = match Command::new("sh")
            .arg("-c")
            .arg(command)
            .current_dir(cwd)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
        {
            Ok(child) => child,
            // `except Exception as e: return ShellResult("", str(e), -1)`
            Err(e) => {
                return ShellResult {
                    stdout: String::new(),
                    stderr: e.to_string(),
                    exit_code: -1,
                    timed_out: false,
                };
            }
        };

        // `stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout)`
        //
        // `proc.communicate()` reads both pipes to EOF and waits for exit; we
        // drain the piped readers concurrently and `wait()` the child inside a
        // single future so the whole thing can be bounded by one `timeout`.
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
            // Reading both pipes concurrently avoids the classic pipe-buffer
            // deadlock (the same property `proc.communicate()` guarantees).
            tokio::join!(read_out, read_err);
            let status = child.wait().await;
            (out_buf, err_buf, status)
        };

        match tokio::time::timeout(duration, communicate).await {
            Ok((stdout_b, stderr_b, status)) => {
                // `.decode(errors="replace")[:self.max_output]`
                let stdout = truncate_chars(&String::from_utf8_lossy(&stdout_b), self.max_output);
                let stderr = truncate_chars(&String::from_utf8_lossy(&stderr_b), self.max_output);
                // `proc.returncode` — for a signal-terminated process Python
                // returns the NEGATIVE signal number (e.g. SIGKILL -> -9). On
                // Unix `ExitStatus::code()` is `None` for signal exits, so we
                // recover the signal via `signal()` and negate it to match.
                // `-1` stays reserved for the spawn-error / timeout sentinels.
                let exit_code = signal_aware_code(&status);
                ShellResult {
                    stdout,
                    stderr,
                    exit_code,
                    timed_out: false,
                }
            }
            // `except asyncio.TimeoutError:`
            Err(_) => {
                // `proc.kill(); await proc.wait()` (ProcessLookupError ignored).
                let _ = child.start_kill();
                let _ = child.wait().await;
                ShellResult {
                    stdout: String::new(),
                    stderr: format!("Command timed out after {timeout}s"),
                    exit_code: -1,
                    timed_out: true,
                }
            }
        }
    }

    /// `async def stream(self, command, timeout=120) -> AsyncIterator[dict]`
    ///
    /// Execute a command and stream output. Yields:
    ///   * `{"stream": "stdout"|"stderr", "data": line}`
    ///   * `{"exit_code": int}`
    ///
    /// Each `Value` is a `serde_json` object mirroring the Python `dict`s.
    pub fn stream(
        &self,
        command: &str,
        timeout: u64,
    ) -> impl futures_util::Stream<Item = Value> {
        let command = command.to_string();
        let cwd = self.cwd.clone();

        async_stream::stream! {
            // `proc = await asyncio.create_subprocess_shell(command, …, cwd=self.cwd)`
            let mut child = match Command::new("sh")
                .arg("-c")
                .arg(&command)
                .current_dir(&cwd)
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .spawn()
            {
                Ok(child) => child,
                // `except Exception as e: yield stderr; yield {"exit_code": -1}`
                Err(e) => {
                    yield json!({"stream": "stderr", "data": e.to_string()});
                    yield json!({"exit_code": -1});
                    return;
                }
            };

            let stdout_pipe = child.stdout.take();
            let stderr_pipe = child.stderr.take();

            // `q: asyncio.Queue` — the two reader coroutines push
            // `(name, Some(line))` per line and a `(name, None)` sentinel on
            // EOF. We mirror this with an mpsc channel + two reader tasks.
            let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<(&'static str, Option<String>)>();

            // `async def _reader(stream, name): … rstrip("\n")`
            fn spawn_reader<R>(
                pipe: Option<R>,
                name: &'static str,
                tx: tokio::sync::mpsc::UnboundedSender<(&'static str, Option<String>)>,
            ) -> tokio::task::JoinHandle<()>
            where
                R: tokio::io::AsyncRead + Unpin + Send + 'static,
            {
                tokio::spawn(async move {
                    if let Some(pipe) = pipe {
                        let mut reader = BufReader::new(pipe);
                        let mut buf: Vec<u8> = Vec::new();
                        loop {
                            buf.clear();
                            // `line = await stream.readline()`
                            match reader.read_until(b'\n', &mut buf).await {
                                // `if not line: break`
                                Ok(0) | Err(_) => break,
                                Ok(_) => {
                                    // `line.decode(errors="replace").rstrip("\n")`
                                    let mut text = String::from_utf8_lossy(&buf).into_owned();
                                    while text.ends_with('\n') {
                                        text.pop();
                                    }
                                    // `await q.put((name, text))`
                                    if tx.send((name, Some(text))).is_err() {
                                        break;
                                    }
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
            // Drop our own sender clone so the channel can close once both
            // reader tasks (which each hold a clone) finish and drop theirs.
            drop(tx);

            // The `finally:` block cancels the reader tasks; this guard aborts
            // them whenever this generator is dropped or returns.
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

            // `finished = 0; deadline = loop.time() + timeout`
            let mut finished = 0;
            let deadline = tokio::time::Instant::now() + std::time::Duration::from_secs(timeout);
            let mut timed_out = false;

            // `while finished < 2:`
            while finished < 2 {
                // `remaining = deadline - loop.time(); if remaining <= 0: raise TimeoutError()`
                let now = tokio::time::Instant::now();
                if now >= deadline {
                    timed_out = true;
                    break;
                }
                let remaining = deadline - now;

                // `name, text = await wait_for(q.get(), timeout=min(remaining, 2.0))`
                let per_recv = remaining.min(std::time::Duration::from_secs_f64(2.0));
                let got = tokio::time::timeout(per_recv, rx.recv()).await;
                let item = match got {
                    // `except asyncio.TimeoutError: continue`
                    Err(_) => continue,
                    // Channel closed before both sentinels (defensive): stop.
                    Ok(None) => break,
                    Ok(Some(item)) => item,
                };
                let (name, text) = item;

                match text {
                    // `if text is None: finished += 1; continue`
                    None => {
                        finished += 1;
                    }
                    // `yield {"stream": name, "data": text}`
                    Some(text) => {
                        yield json!({"stream": name, "data": text});
                    }
                }
            }

            if timed_out {
                // `except asyncio.TimeoutError:` — kill + wait, then emit.
                let _ = child.start_kill();
                let _ = child.wait().await;
                yield json!({"stream": "stderr", "data": format!("Command timed out after {timeout}s")});
                yield json!({"exit_code": -1});
                return;
            }

            // `await proc.wait(); yield {"exit_code": proc.returncode}`
            // Signal-terminated processes report the negative signal number
            // (Python `proc.returncode`); see `signal_aware_code`.
            let status = child.wait().await;
            let exit_code = signal_aware_code(&status);
            yield json!({"exit_code": exit_code});
        }
    }
}

/// `str(Path.home())` — POSIX `Path.home()` resolves `$HOME` (falling back to
/// `pwd`). We read `HOME`, defaulting to `"."` when unset (the conservative
/// substitute for the `pwd` lookup, logged once for visibility).
fn home_dir() -> String {
    match std::env::var("HOME") {
        Ok(home) if !home.is_empty() => home,
        _ => {
            logger::warning("ShellService: $HOME unset; defaulting cwd to '.'");
            ".".to_string()
        }
    }
}

/// `proc.returncode` — translate a finished child's status into the integer
/// Python's `asyncio` subprocess exposes as `returncode`.
///
/// * Normal exit -> the exit code (`ExitStatus::code()`).
/// * Signal death -> the **negative** signal number (e.g. SIGKILL -> `-9`),
///   matching CPython, which sets `returncode = -signum`. On Unix
///   `ExitStatus::code()` is `None` in this case, so we read the signal via
///   `ExitStatusExt::signal()`.
/// * A failed `wait()` (`Err`) -> `-1`, the same sentinel the spawn-error and
///   timeout paths use.
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

/// Truncate `s` to at most `max` Unicode code points, matching Python's
/// `s[:max]` on an already-decoded `str`.
fn truncate_chars(s: &str, max: usize) -> String {
    match s.char_indices().nth(max) {
        Some((idx, _)) => s[..idx].to_string(),
        None => s.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use futures_util::StreamExt;

    fn svc() -> ShellService {
        // Use a small max_output to exercise truncation, deterministic cwd.
        ShellService {
            timeout: 30,
            max_output: 200_000,
            cwd: ".".to_string(),
        }
    }

    #[tokio::test]
    async fn execute_captures_stdout_and_exit_code() {
        let r = svc().execute("printf 'hello'", None, None).await;
        assert_eq!(r.stdout, "hello");
        assert_eq!(r.stderr, "");
        assert_eq!(r.exit_code, 0);
        assert!(!r.timed_out);
    }

    #[tokio::test]
    async fn execute_captures_stderr_and_nonzero_exit() {
        let r = svc()
            .execute("printf 'oops' 1>&2; exit 3", None, None)
            .await;
        assert_eq!(r.stdout, "");
        assert_eq!(r.stderr, "oops");
        assert_eq!(r.exit_code, 3);
        assert!(!r.timed_out);
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn execute_signal_death_reports_negative_signal() {
        // A process killed by SIGKILL (9) -> Python `proc.returncode == -9`.
        // Must NOT collapse to the `-1` timeout/spawn sentinel.
        let r = svc().execute("kill -9 $$", None, None).await;
        assert_eq!(r.exit_code, -9);
        assert!(!r.timed_out);
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn stream_signal_death_reports_negative_signal() {
        let s = svc().stream("kill -9 $$", 120);
        tokio::pin!(s);
        let mut exit = None;
        while let Some(ev) = s.next().await {
            if let Some(code) = ev.get("exit_code").and_then(|v| v.as_i64()) {
                exit = Some(code);
            }
        }
        assert_eq!(exit, Some(-9));
    }

    #[tokio::test]
    async fn execute_times_out() {
        let r = svc().execute("sleep 5", Some(1), None).await;
        assert!(r.timed_out);
        assert_eq!(r.exit_code, -1);
        assert_eq!(r.stderr, "Command timed out after 1s");
        assert_eq!(r.stdout, "");
    }

    #[tokio::test]
    async fn execute_truncates_output() {
        let svc = ShellService {
            timeout: 30,
            max_output: 5,
            cwd: ".".to_string(),
        };
        let r = svc.execute("printf '1234567890'", None, None).await;
        assert_eq!(r.stdout, "12345");
    }

    #[tokio::test]
    async fn execute_respects_cwd() {
        let r = svc().execute("pwd", None, Some("/")).await;
        assert_eq!(r.stdout.trim_end(), "/");
        assert_eq!(r.exit_code, 0);
    }

    #[tokio::test]
    async fn stream_yields_lines_then_exit_code() {
        let s = svc().stream("printf 'a\\nb\\n'", 120);
        tokio::pin!(s);
        let mut stdout_lines = Vec::new();
        let mut exit = None;
        while let Some(ev) = s.next().await {
            if let Some(line) = ev.get("data").and_then(|v| v.as_str()) {
                if ev.get("stream").and_then(|v| v.as_str()) == Some("stdout") {
                    stdout_lines.push(line.to_string());
                }
            }
            if let Some(code) = ev.get("exit_code").and_then(|v| v.as_i64()) {
                exit = Some(code);
            }
        }
        assert_eq!(stdout_lines, vec!["a".to_string(), "b".to_string()]);
        assert_eq!(exit, Some(0));
    }

    #[tokio::test]
    async fn stream_times_out() {
        let s = svc().stream("sleep 5", 1);
        tokio::pin!(s);
        let mut saw_timeout_msg = false;
        let mut exit = None;
        while let Some(ev) = s.next().await {
            if ev.get("data").and_then(|v| v.as_str()) == Some("Command timed out after 1s") {
                saw_timeout_msg = true;
            }
            if let Some(code) = ev.get("exit_code").and_then(|v| v.as_i64()) {
                exit = Some(code);
            }
        }
        assert!(saw_timeout_msg);
        assert_eq!(exit, Some(-1));
    }
}
