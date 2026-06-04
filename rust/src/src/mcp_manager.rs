// src/mcp_manager.rs  <- src/mcp_manager.py
//! Manages connections to MCP (Model Context Protocol) tool servers.
//! Each server exposes tools that are made available to the agent loop.
//!
//! ## PORT_PARTIAL — what is ported and what is deferred
//!
//! The Python `McpManager` leans on the `mcp` Python SDK
//! (`mcp.ClientSession` + `mcp.client.stdio.stdio_client` /
//! `mcp.client.sse.sse_client`). There is no `mcp` Rust crate dependency in this
//! port, so **both transports are hand-rolled**: the stdio transport as
//! newline-delimited JSON-RPC over the child's stdin/stdout (exactly the approach
//! `core/codex.rs` already uses for the `codex app-server`), and the SSE transport
//! as the legacy HTTP+SSE MCP protocol over `reqwest` (GET an `Accept:
//! text/event-stream` stream, an `event: endpoint` frame names the message-POST
//! URL, JSON-RPC requests POST out and their responses come back as `event:
//! message` SSE frames correlated by JSON-RPC id). The wire protocol is the
//! standard MCP one:
//!   `initialize` (request) -> `notifications/initialized` (notification)
//!   -> `tools/list` (request) -> `tools/call` (request).
//! `tools/call` returns MCP content blocks, parsed into a typed [`ContentBlock`]
//! enum (`Text` / `Image` / `Other`) mirroring the Python `_do_call` loop.
//!
//! PORTED (faithful):
//!   - all the pure helpers: `get_all_openai_schemas`, `get_all_tools`,
//!     `is_builtin`, `get_server_status`, `get_all_statuses`,
//!     `get_tool_descriptions_for_prompt` (120-char truncation + the
//!     frozenset-keyed prompt cache, whose key now also folds in the
//!     `_generation` counter so a reconnect invalidates stale descriptions),
//!     and the module-level `_format_mcp_connection_error` (the friendlier
//!     @playwright/mcp connect-failure message).
//!   - `connect_server` dispatch, `_connect_stdio` (hand-rolled JSON-RPC),
//!     `disconnect_server`, `disconnect_all`, `connect_all_enabled` (rusqlite
//!     SELECT on `mcp_servers WHERE is_enabled`, decoding args/env JSON).
//!   - `_connect_sse`: the legacy HTTP+SSE transport, hand-rolled over `reqwest`
//!     (NO `mcp` crate, NO OAuth). GET the SSE stream, read the `event: endpoint`
//!     frame to learn the message-POST URL, then drive the same MCP handshake
//!     (`initialize` -> `notifications/initialized` -> `tools/list`) over POST,
//!     with responses correlated by JSON-RPC id off the SSE stream. The live
//!     session is the [`SseSession`] variant, kept alive by a background reader
//!     task (aborted on drop — the `AsyncExitStack.aclose()` analogue). OAuth /
//!     streamable-HTTP are NOT ported (the Python `sse_client(url)` passes no
//!     auth either).
//!   - `call_tool` / `_do_call` (tools/call + content-block fan-out into the
//!     `agent_tools`-compatible result dict), uniform across stdio and SSE
//!     sessions via the [`Session`] sum type.
//!
//!   - `_reconnect_builtin`: the per-server reconnect for a crashed *builtin*
//!     server. The Rust builtin servers run as subcommands of the current
//!     executable (`["mcp-server", <id>]`, see [`crate::src::builtin_mcp`]), not
//!     `sys.executable mcp_servers/<id>.py`, so this mirrors that launch shape:
//!     it looks the id up in the builtin table, resolves `current_exe()`,
//!     tears down the old session, and reconnects via stdio. This makes
//!     `call_tool`'s builtin auto-reconnect branch functional.
//!
//! DEFERRED (honest stubs — NEVER fake success):
//!   - none remaining: both transports are ported. An unreachable SSE server (or
//!     any GET/POST/handshake failure) still records an `error` status + returns
//!     `false` via `connect_server`'s catch — honest, never faked.
//!
//! CROSS-MODULE: this manager is the shared instance created at
//! `web/mod.rs` startup and is now wired into `crate::src::agent_tools`
//! (the `_MCP_MANAGER` slot holds the real `Arc<McpManager>`) and through it
//! into `tool_execution`, `agent_loop_web`, and `task_scheduler`. The
//! check-in loop in `task_scheduler` reads per-server state through the
//! [`McpManager::connected_server_snapshots`] accessor (the private
//! `tools`/`connections` maps are not exposed directly).

use futures_util::StreamExt;
use indexmap::IndexMap;
use once_cell::sync::Lazy;
use serde_json::{json, Map, Value};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::process::Stdio;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, ChildStdout, Command};
use tokio::sync::oneshot;
use tokio::sync::Mutex as AsyncMutex;

use crate::pylog as logger;

/// `_format_mcp_connection_error(name, command, args, error)` — return a
/// user-actionable MCP connection error message.
///
/// Faithful port of the Python free function: joins `command` + `args` into a
/// command line and, when it mentions `@playwright/mcp` (the Browser MCP
/// package), prepends the raw error with the npx-cache remediation hint;
/// otherwise it returns the raw error unchanged. `name` is part of the Python
/// signature but unused there too (the message keys off the command line), kept
/// here for signature parity.
fn _format_mcp_connection_error(_name: &str, command: &str, args: &[String], error: &str) -> String {
    let raw_error = if error.is_empty() {
        "Unknown error".to_string()
    } else {
        error.to_string()
    };
    // `" ".join([command or "", *args]).strip()`.
    let mut parts: Vec<&str> = vec![command];
    for a in args {
        parts.push(a.as_str());
    }
    let command_line = parts.join(" ");
    let command_line = command_line.trim();
    let lower_command = command_line.to_lowercase();

    if lower_command.contains("@playwright/mcp") {
        return format!(
            "{raw_error}\n\n\
             Browser MCP could not start. On fresh installs, cache the Playwright MCP package once before connecting:\n\n\
             npx -y @playwright/mcp@latest --version\n\n\
             Then restart Odysseus and reconnect the Browser MCP server."
        );
    }

    raw_error
}

// ---------------------------------------------------------------------------
// Hand-rolled stdio JSON-RPC transport (the `mcp` SDK replacement).
// ---------------------------------------------------------------------------

/// One live stdio MCP server subprocess + its JSON-RPC plumbing.
///
/// Mirrors the Python `ClientSession` held in `self._sessions[server_id]`: it
/// owns the child process (`kill_on_drop` so dropping the connection terminates
/// the server — the Rust analogue of `AsyncExitStack.aclose()`), the writable
/// stdin, a buffered stdout reader, and a monotonic JSON-RPC id counter.
struct StdioSession {
    stdin: ChildStdin,
    reader: BufReader<ChildStdout>,
    next_id: i64,
    _child: Child,
}

impl StdioSession {
    fn next_request_id(&mut self) -> i64 {
        let id = self.next_id;
        self.next_id += 1;
        id
    }

    /// Write one newline-delimited JSON-RPC message to the child's stdin.
    async fn write_msg(&mut self, msg: &Value) -> std::io::Result<()> {
        let mut line = msg.to_string();
        line.push('\n');
        self.stdin.write_all(line.as_bytes()).await?;
        self.stdin.flush().await
    }

    /// Read one JSON message (one line) with a per-line timeout. `None` on
    /// EOF / timeout / parse error.
    async fn next_msg(&mut self, secs: u64) -> Option<Value> {
        let mut line = String::new();
        let read = tokio::time::timeout(
            std::time::Duration::from_secs(secs),
            self.reader.read_line(&mut line),
        )
        .await;
        match read {
            Ok(Ok(n)) if n > 0 => serde_json::from_str(line.trim()).ok(),
            _ => None,
        }
    }

    /// Send a JSON-RPC request and read until the matching `id` response arrives
    /// (skipping interleaved notifications), returning its `result`. `Err` on
    /// transport failure, server `error`, or timeout — so a failed `tools/call`
    /// raises like the Python `session.call_tool` does (which `call_tool`
    /// catches to drive the auto-reconnect / error path).
    async fn request(&mut self, method: &str, params: Value) -> Result<Value, String> {
        let id = self.next_request_id();
        self.write_msg(&json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params}))
            .await
            .map_err(|e| format!("MCP write failed: {e}"))?;
        for _ in 0..600 {
            match self.next_msg(120).await {
                Some(m) if m.get("id") == Some(&json!(id)) => {
                    if let Some(err) = m.get("error") {
                        let emsg = err
                            .get("message")
                            .and_then(|x| x.as_str())
                            .unwrap_or("MCP error");
                        return Err(emsg.to_string());
                    }
                    return Ok(m.get("result").cloned().unwrap_or(Value::Null));
                }
                Some(_) => continue, // a notification — skip
                None => return Err("MCP transport closed or timed out".to_string()),
            }
        }
        Err("MCP response not received".to_string())
    }

    /// Fire-and-forget JSON-RPC notification (no id, no response).
    async fn notify(&mut self, method: &str, params: Value) -> std::io::Result<()> {
        self.write_msg(&json!({"jsonrpc": "2.0", "method": method, "params": params}))
            .await
    }
}

// ---------------------------------------------------------------------------
// Hand-rolled SSE/HTTP JSON-RPC transport (the `mcp.client.sse.sse_client`
// replacement — legacy HTTP+SSE, no OAuth).
// ---------------------------------------------------------------------------

/// The correlation map shared between an [`SseSession`] and its reader task:
/// `JSON-RPC id -> the oneshot the waiting caller is parked on`. The reader
/// inserts nothing; `request()` inserts before POSTing and the reader takes the
/// sender back out when the matching `event: message` frame arrives.
type PendingMap = Arc<Mutex<HashMap<i64, oneshot::Sender<Value>>>>;

/// One live SSE MCP server connection + its JSON-RPC plumbing.
///
/// The Python analogue is the `ClientSession` + `AsyncExitStack` held in
/// `self._sessions[server_id]` / `self._stacks[server_id]` for an `sse_client`
/// transport. It owns the `reqwest::Client` used for the outbound `tools/call` /
/// `tools/list` POSTs, the resolved absolute message-POST `endpoint`, a monotonic
/// JSON-RPC id counter, the shared `pending` correlation map, and the background
/// `reader_task` that reads the SSE stream and routes responses by id. Dropping
/// the session aborts that task (see `impl Drop`), the Rust analogue of
/// `AsyncExitStack.aclose()`.
struct SseSession {
    client: reqwest::Client,
    endpoint: String,
    next_id: i64,
    pending: PendingMap,
    reader_task: tokio::task::JoinHandle<()>,
}

impl Drop for SseSession {
    /// Killing the reader task on drop mirrors `StdioSession`'s `kill_on_drop`:
    /// removing the session from `self.sessions` (via `disconnect_server` /
    /// `disconnect_all`) always tears the SSE stream down. No separate disconnect
    /// code path is needed.
    fn drop(&mut self) {
        self.reader_task.abort();
    }
}

impl SseSession {
    fn next_request_id(&mut self) -> i64 {
        let id = self.next_id;
        self.next_id += 1;
        id
    }

    /// Send a JSON-RPC request over POST and await the correlated SSE response.
    ///
    /// The `StdioSession::request` analogue: allocate an id, register a oneshot
    /// in `pending`, POST `{jsonrpc, id, method, params}` to `endpoint`, then wait
    /// (with the same 120s timeout the stdio path uses per response) for the
    /// reader task to route the matching `event: message` frame back. A JSON-RPC
    /// `error` member -> `Err(message)`; a POST failure / closed channel / timeout
    /// -> the same transport-error strings the stdio path returns.
    async fn request(&mut self, method: &str, params: Value) -> Result<Value, String> {
        let id = self.next_request_id();
        let (tx, rx) = oneshot::channel();
        self.pending.lock().unwrap().insert(id, tx);

        let body = json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params});
        let send = self.client.post(&self.endpoint).json(&body).send().await;
        if let Err(e) = send {
            // Drop the dangling sender so we never leak a pending entry.
            self.pending.lock().unwrap().remove(&id);
            return Err(format!("MCP write failed: {e}"));
        }

        let resp = match tokio::time::timeout(std::time::Duration::from_secs(120), rx).await {
            Ok(Ok(m)) => m,
            // Channel closed (reader task gone / stream EOF) OR the timeout fired:
            // mirror the stdio path's transport-closed message.
            Ok(Err(_)) | Err(_) => {
                self.pending.lock().unwrap().remove(&id);
                return Err("MCP transport closed or timed out".to_string());
            }
        };

        if let Some(err) = resp.get("error") {
            let emsg = err
                .get("message")
                .and_then(|x| x.as_str())
                .unwrap_or("MCP error");
            return Err(emsg.to_string());
        }
        Ok(resp.get("result").cloned().unwrap_or(Value::Null))
    }

    /// Fire-and-forget JSON-RPC notification (no id, no correlation, no response).
    /// The `StdioSession::notify` analogue, POSTed to the message endpoint.
    async fn notify(&mut self, method: &str, params: Value) -> std::io::Result<()> {
        let body = json!({"jsonrpc": "2.0", "method": method, "params": params});
        self.client
            .post(&self.endpoint)
            .json(&body)
            .send()
            .await
            .map(|_| ())
            .map_err(|e| std::io::Error::other(e.to_string()))
    }
}

/// One live MCP server session, of either transport. Stored uniformly in
/// `self.sessions` so `call_tool` / `_do_call` / `disconnect_server` /
/// `disconnect_all` operate on stdio and SSE servers identically (the Python
/// `self._sessions[server_id]` holds an opaque `ClientSession` for both).
enum Session {
    Stdio(Box<StdioSession>),
    Sse(SseSession),
}

impl Session {
    /// Dispatch a JSON-RPC request to the underlying transport. `_do_call`
    /// (`tools/call`) routes through this so an SSE server is indistinguishable
    /// from a stdio one. (Notifications are only sent during connect, directly on
    /// the concrete `StdioSession` / `SseSession` before it is wrapped here, so no
    /// enum `notify` dispatch is needed.)
    async fn request(&mut self, method: &str, params: Value) -> Result<Value, String> {
        match self {
            Session::Stdio(s) => s.request(method, params).await,
            Session::Sse(s) => s.request(method, params).await,
        }
    }
}

/// One parsed SSE frame: its `event:` name (default `"message"` per the SSE
/// spec) and the joined `data:` payload (multiple `data:` lines joined by `\n`).
struct SseFrame {
    event: String,
    data: String,
}

/// Pull the next complete SSE frame out of `buf`, consuming it. A frame is the
/// run of lines up to (and including) the terminating blank line. Returns `None`
/// when `buf` does not yet hold a full frame (caller should read more bytes).
///
/// Hand-rolled SSE framing (no `eventsource-stream` crate): split on `\n`,
/// strip a trailing `\r`, ignore `:`-comment lines and unknown fields
/// (`id:` / `retry:`), accumulate `event:` and `data:` (multi-`data:` joined by
/// `\n`), per the WHATWG SSE spec.
fn take_sse_frame(buf: &mut String) -> Option<SseFrame> {
    // Find the blank-line frame terminator: "\n\n" (or "\n\r\n" tolerated via the
    // per-line \r strip below, since we scan for the "\n\n" pair after trimming).
    // We scan line-by-line and stop at the first empty line.
    let mut consumed = 0usize;
    let mut event: Option<String> = None;
    let mut data_lines: Vec<String> = Vec::new();
    let mut found_terminator = false;

    for line in buf.split_inclusive('\n') {
        let line_len = line.len();
        let content = line.trim_end_matches('\n').trim_end_matches('\r');
        if content.is_empty() {
            // Blank line: end of frame.
            consumed += line_len;
            found_terminator = true;
            break;
        }
        consumed += line_len;
        if let Some(stripped) = content.strip_prefix(':') {
            let _ = stripped; // comment line — ignore
            continue;
        }
        // `field: value` (a single leading space after the colon is stripped).
        let (field, value) = match content.split_once(':') {
            Some((f, v)) => (f, v.strip_prefix(' ').unwrap_or(v)),
            None => (content, ""), // a bare field name with no colon
        };
        match field {
            "event" => event = Some(value.to_string()),
            "data" => data_lines.push(value.to_string()),
            _ => {} // id / retry / unknown — ignore
        }
    }

    if !found_terminator {
        return None;
    }
    buf.drain(..consumed);
    Some(SseFrame {
        event: event.unwrap_or_else(|| "message".to_string()),
        data: data_lines.join("\n"),
    })
}

/// A discovered MCP tool schema (the dicts stored in `self._tools[server_id]`).
#[derive(Clone)]
struct ToolSchema {
    name: String,
    description: String,
    input_schema: Value,
}

/// A connection-status record (the dicts stored in `self._connections[id]`).
///
/// Kept as a `serde_json::Value` object (preserving insertion order via the
/// `preserve_order` serde feature) so `get_server_status` / `get_all_statuses`
/// return the same dynamic-keyed dict shape the Python does (`status` / `name` /
/// `transport` / `tool_count` / `identity` / `error`).
type ConnRecord = Map<String, Value>;

/// A parsed MCP `tools/call` content block (the Python `_do_call` per-content
/// branch: `text` attr -> Text; `type == "image"` + `data` -> Image; other
/// `data` attr -> Other).
enum ContentBlock {
    Text(String),
    Image { data: String, mime: String },
    Other(String),
}

// ---------------------------------------------------------------------------
// McpManager
// ---------------------------------------------------------------------------

/// Manages MCP server connections and tool routing. Faithful port of the Python
/// `McpManager`, with the dynamic dicts realized as locked maps so a single
/// shared instance (`manager()`) can be driven concurrently.
pub struct McpManager {
    /// `server_id -> ClientSession` (the live stdio subprocess or SSE stream).
    /// Async lock because tool calls and connects await IO. `IndexMap` so the key
    /// iteration order in `disconnect_all` matches Python's
    /// `list(self._sessions.keys())` (a plain insertion-ordered dict). The value
    /// is the [`Session`] sum type so stdio and SSE servers coexist and flow
    /// through `call_tool` / `disconnect_server` uniformly.
    sessions: AsyncMutex<IndexMap<String, Session>>,
    /// `server_id -> connection state` dict. `IndexMap` so `get_all_statuses`
    /// returns keys in Python dict insertion order.
    connections: Mutex<IndexMap<String, ConnRecord>>,
    /// `server_id -> list of tool schemas`. `IndexMap` so `get_all_openai_schemas`
    /// / `get_all_tools` / the prompt grouping iterate servers in Python dict
    /// insertion order.
    tools: Mutex<IndexMap<String, Vec<ToolSchema>>>,
    /// The `get_tool_descriptions_for_prompt` memoization
    /// (`_cached_prompt_desc` / `_cached_prompt_desc_key`).
    prompt_cache: Mutex<Option<(PromptCacheKey, String)>>,
    /// `self._generation` — a monotonic counter bumped on every connect/disconnect
    /// (success OR error). It is folded into the prompt-description cache key so a
    /// reconnect (which may swap the tool set without changing `len(self._tools)`)
    /// invalidates the stale cached descriptions. Atomic so the otherwise-shared
    /// `&self` methods can bump it without holding a lock.
    generation: AtomicU64,
}

/// Cache key for `get_tool_descriptions_for_prompt`, mirroring the Python tuple
/// `(frozenset((k, frozenset(v)) ...), len(self._tools), self._generation)`. A
/// `BTreeMap` of sorted-tool-name `Vec`s gives the same order-independent
/// equality a Python `frozenset` of `frozenset`s does; the trailing `u64` is the
/// generation counter, so a reconnect (which bumps the generation even when the
/// tool count is unchanged) invalidates the cached descriptions.
type PromptCacheKey = (BTreeMap<String, Vec<String>>, usize, u64);

impl Default for McpManager {
    fn default() -> Self {
        Self::new()
    }
}

impl McpManager {
    /// `__init__`: empty connection/tool/session maps.
    pub fn new() -> Self {
        McpManager {
            sessions: AsyncMutex::new(IndexMap::new()),
            connections: Mutex::new(IndexMap::new()),
            tools: Mutex::new(IndexMap::new()),
            prompt_cache: Mutex::new(None),
            generation: AtomicU64::new(0),
        }
    }

    /// `connect_server(...)` — connect to an MCP server via stdio or SSE.
    ///
    /// Dispatches on `transport`; an unknown transport logs an error and returns
    /// `false`. Any error during connect is caught, recorded as an `error`
    /// status, and surfaced as `false` (matching the Python try/except).
    #[allow(clippy::too_many_arguments)]
    pub async fn connect_server(
        &self,
        server_id: &str,
        name: &str,
        transport: &str,
        command: Option<&str>,
        args: Vec<String>,
        env: HashMap<String, String>,
        url: Option<&str>,
    ) -> bool {
        // Retain `command`/`args` for the error-path `_format_mcp_connection_error`
        // (the stdio connect consumes `args` by value).
        let command_for_error = command.unwrap_or("").to_string();
        let args_for_error = args.clone();
        let result = match transport {
            "stdio" => {
                self._connect_stdio(server_id, name, command.unwrap_or(""), args, env)
                    .await
            }
            "sse" => self._connect_sse(server_id, name, url.unwrap_or("")).await,
            other => {
                logger::error(&format!("Unknown MCP transport: {other}"));
                Ok(false)
            }
        };
        match result {
            // `if res: self._generation += 1` — bump only on a *successful* connect
            // (a `false`-without-error, e.g. unknown transport, does NOT bump).
            Ok(ok) => {
                if ok {
                    self.generation.fetch_add(1, Ordering::SeqCst);
                }
                ok
            }
            Err(e) => {
                logger::error(&format!(
                    "Failed to connect MCP server {name} ({server_id}): {e}"
                ));
                let error_message =
                    _format_mcp_connection_error(name, &command_for_error, &args_for_error, &e);
                let mut rec = Map::new();
                rec.insert("status".into(), json!("error"));
                rec.insert("error".into(), json!(error_message));
                rec.insert("name".into(), json!(name));
                self.connections
                    .lock()
                    .unwrap()
                    .insert(server_id.to_string(), rec);
                // The Python bumps `_generation` in the except branch too.
                self.generation.fetch_add(1, Ordering::SeqCst);
                false
            }
        }
    }

    /// `_connect_stdio(...)` — connect via stdio transport.
    ///
    /// Spawns `command args...` with the child env set to `{**os.environ, **env}`
    /// (when `env` is non-empty), then performs the MCP handshake
    /// (`initialize` + `notifications/initialized`), discovers tools
    /// (`tools/list`), records the session/tools/connection state, and extracts
    /// identity hints from the env (email/account/user vars) so multiple
    /// instances of the same server can be told apart in tool descriptions.
    async fn _connect_stdio(
        &self,
        server_id: &str,
        name: &str,
        command: &str,
        args: Vec<String>,
        env: HashMap<String, String>,
    ) -> Result<bool, String> {
        let mut cmd = Command::new(command);
        cmd.args(&args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .kill_on_drop(true);
        // `env={**os.environ, **env} if env else None`: only override the env when
        // the caller supplied one (else inherit the parent's environment whole).
        if !env.is_empty() {
            for (k, v) in &env {
                cmd.env(k, v);
            }
        }

        let mut child = cmd
            .spawn()
            .map_err(|e| format!("cannot launch MCP server `{command}`: {e}"))?;
        let stdin = child.stdin.take().ok_or("MCP stdin unavailable")?;
        let stdout = child.stdout.take().ok_or("MCP stdout unavailable")?;
        let mut session = StdioSession {
            stdin,
            reader: BufReader::new(stdout),
            next_id: 1,
            _child: child,
        };

        // ---- initialize handshake ----
        session
            .request(
                "initialize",
                json!({
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "odysseus", "version": "1.0.0"},
                }),
            )
            .await?;
        session
            .notify("notifications/initialized", json!({}))
            .await
            .map_err(|e| format!("MCP initialized notify failed: {e}"))?;

        // ---- tools/list discovery ----
        let tools_result = session.request("tools/list", json!({})).await?;
        let tools = parse_tools_result(&tools_result);
        let tool_count = tools.len();

        // Identity hints from env vars (email/account/user/username), joined.
        let mut identity_hints: Vec<String> = Vec::new();
        for (k, v) in &env {
            let k_lower = k.to_lowercase();
            if ["email_address", "account", "user", "username"]
                .iter()
                .any(|x| k_lower.contains(x))
            {
                identity_hints.push(v.clone());
            }
        }
        let identity = identity_hints.join(", ");

        self.sessions
            .lock()
            .await
            .insert(server_id.to_string(), Session::Stdio(Box::new(session)));
        self.tools
            .lock()
            .unwrap()
            .insert(server_id.to_string(), tools);

        let mut rec = Map::new();
        rec.insert("status".into(), json!("connected"));
        rec.insert("name".into(), json!(name));
        rec.insert("transport".into(), json!("stdio"));
        rec.insert("tool_count".into(), json!(tool_count));
        rec.insert("identity".into(), json!(identity));
        self.connections
            .lock()
            .unwrap()
            .insert(server_id.to_string(), rec);

        logger::info(&format!(
            "MCP server connected: {name} ({server_id}) - {tool_count} tools via stdio"
        ));
        Ok(true)
    }

    /// `_connect_sse(...)` — connect via the legacy HTTP+SSE transport.
    ///
    /// Hand-rolled port of `mcp.client.sse.sse_client(url)` + `ClientSession`
    /// over `reqwest` (NO `mcp` crate, NO OAuth — the Python `sse_client(url)`
    /// passes no auth):
    ///   1. GET `url` with `Accept: text/event-stream`; a non-2xx status or send
    ///      error -> `Err` (caught by `connect_server` into the
    ///      `{status:error, error, name}` + `false` shape — honest, never faked).
    ///   2. Read the SSE stream inline until the first `event: endpoint` frame; its
    ///      `data` is the message-POST path, resolved against `url` to an absolute
    ///      endpoint.
    ///   3. Spawn the background reader task that owns the remaining stream and
    ///      routes each `event: message` JSON-RPC response to its waiting caller by
    ///      id (via the shared `pending` correlation map).
    ///   4. Drive the standard MCP handshake over POST (`initialize` id=1,
    ///      `notifications/initialized`, `tools/list` id=2), reading each response
    ///      off the SSE stream, then record the session/tools/connection state.
    ///
    /// The connection record OMITS the `identity` key (Python `mcp_manager.py`
    /// SSE branch, lines 139-144, has no identity — unlike the stdio branch).
    async fn _connect_sse(&self, server_id: &str, name: &str, url: &str) -> Result<bool, String> {
        let client = reqwest::Client::new();

        // ---- 1. open the SSE stream ----
        let resp = client
            .get(url)
            .header("Accept", "text/event-stream")
            .send()
            .await
            .map_err(|e| format!("cannot open MCP SSE stream `{url}`: {e}"))?;
        if !resp.status().is_success() {
            return Err(format!("MCP SSE stream returned HTTP {}", resp.status()));
        }
        let mut stream = resp.bytes_stream();
        let mut buf = String::new();

        // ---- 2. read frames until the `endpoint` frame ----
        let endpoint_path = loop {
            match take_sse_frame(&mut buf) {
                Some(frame) => {
                    if frame.event == "endpoint" {
                        break frame.data;
                    }
                    // Per the MCP legacy transport, the FIRST frame must be the
                    // endpoint frame; anything else before it is a protocol error.
                    return Err(format!(
                        "MCP SSE: expected `endpoint` frame, got `{}`",
                        frame.event
                    ));
                }
                None => {
                    // Need more bytes to complete a frame.
                    match stream.next().await {
                        Some(Ok(chunk)) => buf.push_str(&String::from_utf8_lossy(&chunk)),
                        Some(Err(e)) => return Err(format!("MCP SSE stream read error: {e}")),
                        None => return Err("MCP SSE stream closed before endpoint".to_string()),
                    }
                }
            }
        };

        // Resolve the (usually relative) message path against the SSE url.
        let endpoint = url::Url::parse(url)
            .and_then(|base| base.join(endpoint_path.trim()))
            .map_err(|e| format!("MCP SSE: bad endpoint `{endpoint_path}`: {e}"))?
            .to_string();

        // ---- 3. spawn the reader task ----
        let pending: PendingMap = Arc::new(Mutex::new(HashMap::new()));
        let pending_for_task = Arc::clone(&pending);
        let reader_task = tokio::spawn(async move {
            // The reader owns the remaining stream + the leftover buffer.
            let mut stream = stream;
            let mut buf = buf;
            loop {
                while let Some(frame) = take_sse_frame(&mut buf) {
                    if frame.event != "message" {
                        continue; // endpoint already consumed; ignore stray events
                    }
                    let msg: Value = match serde_json::from_str(frame.data.trim()) {
                        Ok(v) => v,
                        Err(_) => continue,
                    };
                    // Route by JSON-RPC id; id-less server notifications are dropped
                    // (the Python ClientSession ignores them for our purposes).
                    if let Some(id) = msg.get("id").and_then(|i| i.as_i64()) {
                        if let Some(tx) = pending_for_task.lock().unwrap().remove(&id) {
                            // Route the FULL object so `request()` sees error/result.
                            let _ = tx.send(msg);
                        }
                    }
                }
                match stream.next().await {
                    Some(Ok(chunk)) => buf.push_str(&String::from_utf8_lossy(&chunk)),
                    // EOF / error: dropping `pending_for_task` (and any senders it
                    // still holds) on task exit resolves every parked `request()`
                    // await to the transport-closed Err.
                    _ => break,
                }
            }
        });

        let mut session = SseSession {
            client,
            endpoint,
            next_id: 3, // initialize=1, tools/list=2 are consumed in the handshake
            pending,
            reader_task,
        };

        // ---- 4. MCP handshake over POST (responses via the SSE stream) ----
        session
            .request(
                "initialize",
                json!({
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "odysseus", "version": "1.0.0"},
                }),
            )
            .await?;
        session
            .notify("notifications/initialized", json!({}))
            .await
            .map_err(|e| format!("MCP initialized notify failed: {e}"))?;

        let tools_result = session.request("tools/list", json!({})).await?;
        let tools = parse_tools_result(&tools_result);
        let tool_count = tools.len();

        self.sessions
            .lock()
            .await
            .insert(server_id.to_string(), Session::Sse(session));
        self.tools
            .lock()
            .unwrap()
            .insert(server_id.to_string(), tools);

        // SSE connection record OMITS `identity` (Python lines 139-144).
        let mut rec = Map::new();
        rec.insert("status".into(), json!("connected"));
        rec.insert("name".into(), json!(name));
        rec.insert("transport".into(), json!("sse"));
        rec.insert("tool_count".into(), json!(tool_count));
        self.connections
            .lock()
            .unwrap()
            .insert(server_id.to_string(), rec);

        logger::info(&format!(
            "MCP server connected: {name} ({server_id}) - {tool_count} tools via SSE"
        ));
        Ok(true)
    }

    /// `disconnect_server(server_id)` — drop the session (a stdio session kills
    /// its subprocess via `kill_on_drop`; an SSE session aborts its reader task
    /// via `impl Drop for SseSession` — both the Rust analogue of
    /// `AsyncExitStack.aclose()`) and forget the tools/connection state.
    pub async fn disconnect_server(&self, server_id: &str) {
        // Dropping the Session terminates its transport (stdio child via
        // kill_on_drop, SSE reader task via its Drop abort).
        // `shift_remove` (not the default swap-remove) preserves the relative
        // order of the remaining entries, matching Python `dict.pop`.
        let _ = self.sessions.lock().await.shift_remove(server_id);
        self.tools.lock().unwrap().shift_remove(server_id);
        self.connections.lock().unwrap().shift_remove(server_id);
        // `self._generation += 1` — every disconnect bumps the generation so the
        // prompt-description cache is invalidated.
        self.generation.fetch_add(1, Ordering::SeqCst);
        logger::info(&format!("MCP server disconnected: {server_id}"));
    }

    /// `disconnect_all()` — disconnect every connected server.
    pub async fn disconnect_all(&self) {
        let ids: Vec<String> = self.sessions.lock().await.keys().cloned().collect();
        for sid in ids {
            self.disconnect_server(&sid).await;
        }
    }

    /// `connect_all_enabled()` — connect to all enabled MCP servers from the DB.
    ///
    /// PORT_VIA_DB: `db.query(McpServer).filter(McpServer.is_enabled == True)`
    /// becomes a raw `SELECT ... FROM mcp_servers WHERE is_enabled = 1`. The
    /// `args` / `env` columns are JSON-decoded (defaulting to `[]` / `{}`), then
    /// each row is connected via `connect_server`.
    pub async fn connect_all_enabled(&self) {
        // Snapshot the rows first so the rusqlite connection (not `Send` across
        // awaits) is dropped before we start awaiting connect_server.
        struct ServerRow {
            id: String,
            name: String,
            transport: String,
            command: Option<String>,
            args: Option<String>,
            env: Option<String>,
            url: Option<String>,
        }
        let rows: Vec<ServerRow> = {
            let conn = match crate::core::database::session_local() {
                Ok(c) => c,
                Err(e) => {
                    logger::error(&format!("connect_all_enabled DB error: {e}"));
                    return;
                }
            };
            let mut stmt = match conn.prepare(
                "SELECT id, name, transport, command, args, env, url \
                 FROM mcp_servers WHERE is_enabled = 1",
            ) {
                Ok(s) => s,
                Err(e) => {
                    logger::error(&format!("connect_all_enabled query error: {e}"));
                    return;
                }
            };
            let mapped = stmt.query_map([], |r| {
                Ok(ServerRow {
                    id: r.get(0)?,
                    name: r.get(1)?,
                    transport: r.get(2)?,
                    command: r.get(3)?,
                    args: r.get(4)?,
                    env: r.get(5)?,
                    url: r.get(6)?,
                })
            });
            match mapped {
                Ok(iter) => iter.filter_map(|x| x.ok()).collect(),
                Err(e) => {
                    logger::error(&format!("connect_all_enabled row error: {e}"));
                    return;
                }
            }
        };

        for srv in rows {
            let args: Vec<String> = srv
                .args
                .as_deref()
                .filter(|s| !s.is_empty())
                .and_then(|s| serde_json::from_str::<Vec<String>>(s).ok())
                .unwrap_or_default();
            let env: HashMap<String, String> = srv
                .env
                .as_deref()
                .filter(|s| !s.is_empty())
                .and_then(|s| serde_json::from_str::<HashMap<String, String>>(s).ok())
                .unwrap_or_default();
            self.connect_server(
                &srv.id,
                &srv.name,
                &srv.transport,
                srv.command.as_deref(),
                args,
                env,
                srv.url.as_deref(),
            )
            .await;
        }
    }

    /// `call_tool(qualified_name, arguments)` — call an MCP tool by its qualified
    /// name (`mcp__{server_id}__{tool_name}`).
    ///
    /// Returns a result dict compatible with the `agent_tools` format. On a call
    /// failure for a *builtin* server, the Python attempts an auto-reconnect; here
    /// `_reconnect_builtin` tears down and re-spawns the builtin subcommand, so a
    /// successful reconnect retries the call and only a failed reconnect degrades
    /// to the "reconnect failed" error path (never a faked success).
    pub async fn call_tool(&self, qualified_name: &str, arguments: Value) -> Value {
        let parts: Vec<&str> = qualified_name.splitn(3, "__").collect();
        if parts.len() != 3 || parts[0] != "mcp" {
            return json!({"error": format!("Invalid MCP tool name: {qualified_name}"), "exit_code": 1});
        }
        let server_id = parts[1].to_string();
        let tool_name = parts[2].to_string();

        // First attempt.
        let first = {
            let mut sessions = self.sessions.lock().await;
            match sessions.get_mut(&server_id) {
                None => {
                    return json!({"error": format!("MCP server not connected: {server_id}"), "exit_code": 1})
                }
                Some(session) => self._do_call(session, &tool_name, arguments.clone()).await,
            }
        };

        match first {
            Ok(result) => result,
            Err(e) => {
                if self.is_builtin(&server_id) {
                    logger::warning(&format!(
                        "MCP call failed for {qualified_name}, attempting reconnect: {e}"
                    ));
                    let reconnected = self._reconnect_builtin(&server_id).await;
                    if reconnected {
                        let mut sessions = self.sessions.lock().await;
                        match sessions.get_mut(&server_id) {
                            Some(session) => {
                                match self._do_call(session, &tool_name, arguments).await {
                                    Ok(result) => result,
                                    Err(e2) => {
                                        logger::error(&format!(
                                            "MCP tool call failed after reconnect: {qualified_name}: {e2}"
                                        ));
                                        json!({"error": e2, "exit_code": 1})
                                    }
                                }
                            }
                            None => {
                                json!({"error": format!("Reconnected but no session for {server_id}"), "exit_code": 1})
                            }
                        }
                    } else {
                        logger::error(&format!("MCP reconnect failed for {server_id}"));
                        json!({"error": format!("MCP server crashed and reconnect failed: {server_id}"), "exit_code": 1})
                    }
                } else {
                    logger::error(&format!("MCP tool call failed: {qualified_name}: {e}"));
                    json!({"error": e, "exit_code": 1})
                }
            }
        }
    }

    /// `_do_call(session, tool_name, arguments)` — execute a single MCP tool call
    /// and shape the result dict.
    ///
    /// Sends `tools/call {name, arguments}` and fans the returned `content`
    /// blocks into output text + an optional `images` list, exactly like the
    /// Python loop: blocks with a `text` field append text; `type == "image"`
    /// blocks with `data` add to `images` and append a `[Screenshot ...]` marker;
    /// other blocks with `data` append its string form. `isError` selects
    /// stdout vs stderr and the exit code.
    async fn _do_call(
        &self,
        session: &mut Session,
        tool_name: &str,
        arguments: Value,
    ) -> Result<Value, String> {
        let result = session
            .request("tools/call", json!({"name": tool_name, "arguments": arguments}))
            .await?;

        let mut output_parts: Vec<String> = Vec::new();
        let mut images: Vec<Value> = Vec::new();
        if let Some(content) = result.get("content").and_then(|c| c.as_array()) {
            for block in content {
                match parse_content_block(block) {
                    Some(ContentBlock::Text(t)) => output_parts.push(t),
                    Some(ContentBlock::Image { data, mime }) => {
                        images.push(json!({"data": data, "mimeType": mime}));
                        output_parts.push(format!("[Screenshot captured ({mime})]"));
                    }
                    Some(ContentBlock::Other(s)) => output_parts.push(s),
                    None => {}
                }
            }
        }

        let output = output_parts.join("\n");
        let is_error = result
            .get("isError")
            .and_then(|e| e.as_bool())
            .unwrap_or(false);

        let mut result_dict = Map::new();
        result_dict.insert(
            "stdout".into(),
            json!(if is_error { "" } else { output.as_str() }),
        );
        result_dict.insert(
            "stderr".into(),
            json!(if is_error { output.as_str() } else { "" }),
        );
        result_dict.insert("exit_code".into(), json!(if is_error { 1 } else { 0 }));
        if !images.is_empty() {
            result_dict.insert("images".into(), Value::Array(images));
        }
        Ok(Value::Object(result_dict))
    }

    /// `_reconnect_builtin(server_id)` — tear down and reconnect a crashed builtin
    /// MCP server.
    ///
    /// The Python re-spawns the server with `sys.executable mcp_servers/<id>.py`
    /// (`src.builtin_mcp._BUILTIN_SERVERS` + `PYTHONPATH`). In this port the four
    /// builtin script servers run as subcommands of THIS binary
    /// (`current_exe() ["mcp-server", <id>]`, see [`crate::src::builtin_mcp`]), so
    /// the reconnect mirrors that launch shape rather than the Python script path:
    ///
    ///   * an id not in the builtin table -> `false` (Python `server_id not in
    ///     _BUILTIN_SERVERS`).
    ///   * resolve `std::env::current_exe()` (the `sys.executable` analogue); a
    ///     resolution error logs + returns `false` rather than fabricating a
    ///     connection.
    ///   * `disconnect_server` the old session, then `connect_server` via stdio
    ///     with `["mcp-server", <id>]` (no env, no url).
    ///
    /// The builtin id -> display name table is duplicated here (the
    /// `builtin_mcp::BUILTIN_SERVERS` static is private to that module); the four
    /// names match `BUILTIN_SERVERS` exactly. Returns the connect result, so
    /// `call_tool`'s auto-reconnect branch retries the call on success and falls
    /// through to the "reconnect failed" error on failure — never a faked success.
    async fn _reconnect_builtin(&self, server_id: &str) -> bool {
        // `if server_id not in _BUILTIN_SERVERS: return False`. The four builtin
        // script-server ids + display names mirror `builtin_mcp::BUILTIN_SERVERS`.
        let name = match server_id {
            "image_gen" => "Built-in: Image Generation",
            "memory" => "Built-in: Memory",
            "rag" => "Built-in: RAG",
            "email" => "Built-in: Email",
            _ => return false,
        };

        // The `sys.executable` analogue: the launcher for this binary, which also
        // hosts the builtin MCP servers as `["mcp-server", <id>]` subcommands.
        let current_exe = match std::env::current_exe() {
            Ok(p) => p.to_string_lossy().into_owned(),
            Err(e) => {
                logger::error(&format!(
                    "Failed to reconnect builtin MCP server {name}: cannot resolve current executable: {e}"
                ));
                return false;
            }
        };

        // Clean up old connection (kills the crashed child via kill_on_drop).
        self.disconnect_server(server_id).await;

        let ok = self
            .connect_server(
                server_id,
                name,
                "stdio",
                Some(&current_exe),
                vec!["mcp-server".to_string(), server_id.to_string()],
                HashMap::new(),
                None,
            )
            .await;
        if ok {
            logger::info(&format!("Reconnected builtin MCP server: {name}"));
        }
        ok
    }

    /// `get_all_openai_schemas(disabled_map)` — all MCP tools in OpenAI
    /// function-calling format, namespaced `mcp__{server_id}__{tool_name}`.
    ///
    /// Skips builtin Python servers (except `builtin_browser`, which needs
    /// function calling), filters disabled tools, and labels descriptions with
    /// `[MCP:{server_name (identity)}]`.
    pub fn get_all_openai_schemas(
        &self,
        disabled_map: Option<&HashMap<String, HashSet<String>>>,
    ) -> Vec<Value> {
        let tools = self.tools.lock().unwrap();
        let connections = self.connections.lock().unwrap();
        let mut schemas = Vec::new();
        for (server_id, server_tools) in tools.iter() {
            if self.is_builtin(server_id) && server_id != "builtin_browser" {
                continue;
            }
            let conn = connections.get(server_id);
            let server_name = conn
                .and_then(|c| c.get("name"))
                .and_then(|n| n.as_str())
                .unwrap_or(server_id.as_str());
            let disabled = disabled_map.and_then(|m| m.get(server_id));
            let identity = conn
                .and_then(|c| c.get("identity"))
                .and_then(|i| i.as_str())
                .unwrap_or("");
            let label = if identity.is_empty() {
                server_name.to_string()
            } else {
                format!("{server_name} ({identity})")
            };

            for tool in server_tools {
                if disabled.map(|d| d.contains(&tool.name)).unwrap_or(false) {
                    continue;
                }
                let qualified = format!("mcp__{server_id}__{}", tool.name);
                let parameters = if tool.input_schema.is_null() {
                    json!({"type": "object", "properties": {}})
                } else {
                    tool.input_schema.clone()
                };
                schemas.push(json!({
                    "type": "function",
                    "function": {
                        "name": qualified,
                        "description": format!("[MCP:{label}] {}", tool.description),
                        "parameters": parameters,
                    },
                }));
            }
        }
        schemas
    }

    /// `get_all_tools(disabled_map)` — a flat list of all discovered tools with
    /// server info (`server_id`, `server_name`, `name`, `qualified_name`,
    /// `description`, `is_disabled`).
    pub fn get_all_tools(
        &self,
        disabled_map: Option<&HashMap<String, HashSet<String>>>,
    ) -> Vec<Value> {
        let tools = self.tools.lock().unwrap();
        let connections = self.connections.lock().unwrap();
        let mut result = Vec::new();
        for (server_id, server_tools) in tools.iter() {
            let conn = connections.get(server_id);
            let server_name = conn
                .and_then(|c| c.get("name"))
                .and_then(|n| n.as_str())
                .unwrap_or(server_id.as_str());
            let disabled = disabled_map.and_then(|m| m.get(server_id));
            for tool in server_tools {
                result.push(json!({
                    "server_id": server_id,
                    "server_name": server_name,
                    "name": tool.name,
                    "qualified_name": format!("mcp__{server_id}__{}", tool.name),
                    "description": tool.description,
                    "is_disabled": disabled.map(|d| d.contains(&tool.name)).unwrap_or(false),
                }));
            }
        }
        result
    }

    /// `is_builtin(server_id)` — a built-in (auto-registered) server: id starts
    /// with `builtin_`, or is one of the fixed builtin set.
    pub fn is_builtin(&self, server_id: &str) -> bool {
        server_id.starts_with("builtin_")
            || matches!(server_id, "image_gen" | "memory" | "rag" | "email")
    }

    /// `get_server_status(server_id)` — connection status for one server, or
    /// `{"status": "disconnected"}` when unknown.
    pub fn get_server_status(&self, server_id: &str) -> Value {
        match self.connections.lock().unwrap().get(server_id) {
            Some(rec) => Value::Object(rec.clone()),
            None => json!({"status": "disconnected"}),
        }
    }

    /// A read-only snapshot of every **non-builtin** server's `(server_id,
    /// tool_names, status, identity)`, for the check-in MCP auto-discovery loop.
    ///
    /// The `task_scheduler` check-in (`task_scheduler.py:1063-1099`) iterates
    /// `mcp._tools` + `mcp._connections` directly, skipping `is_builtin` servers
    /// and reading each connection's `status` + `identity`. Those maps are
    /// private here, so this accessor exposes exactly the slices that loop needs:
    /// one entry per non-builtin server in `self._tools` insertion order, each
    /// carrying the server's tool names (Python `{t["name"] for t in tools}`,
    /// returned as an order-preserving `Vec`), its connection `status` (default
    /// `"disconnected"` when unrecorded), and its `identity` (default `""`). The
    /// consumer is responsible for the `status == "connected"` filter, matching
    /// the Python.
    ///
    /// Locks `tools` then `connections` (the same order as
    /// `get_all_openai_schemas`/`get_all_tools`) to avoid a lock-order inversion.
    pub fn connected_server_snapshots(&self) -> Vec<(String, Vec<String>, String, String)> {
        let tools = self.tools.lock().unwrap();
        let connections = self.connections.lock().unwrap();
        let mut out = Vec::new();
        for (server_id, server_tools) in tools.iter() {
            if self.is_builtin(server_id) {
                continue;
            }
            let conn = connections.get(server_id);
            let status = conn
                .and_then(|c| c.get("status"))
                .and_then(|s| s.as_str())
                .unwrap_or("disconnected")
                .to_string();
            let identity = conn
                .and_then(|c| c.get("identity"))
                .and_then(|i| i.as_str())
                .unwrap_or("")
                .to_string();
            let tool_names: Vec<String> = server_tools.iter().map(|t| t.name.clone()).collect();
            out.push((server_id.clone(), tool_names, status, identity));
        }
        out
    }

    /// `get_all_statuses()` — connection statuses for all servers.
    pub fn get_all_statuses(&self) -> Value {
        let connections = self.connections.lock().unwrap();
        let mut out = Map::new();
        for (sid, rec) in connections.iter() {
            out.insert(sid.clone(), Value::Object(rec.clone()));
        }
        Value::Object(out)
    }

    /// `get_tool_descriptions_for_prompt(disabled_map)` — text describing MCP
    /// tools for the agent system prompt. Cached, keyed by the disabled-map's
    /// content + the number of connected servers (`len(self._tools)`).
    ///
    /// Skips builtin Python servers (except `builtin_browser`), groups remaining
    /// tools by server name, labels each group with its identity, and truncates
    /// each tool description to 120 chars + `...`.
    pub fn get_tool_descriptions_for_prompt(
        &self,
        disabled_map: Option<&HashMap<String, HashSet<String>>>,
    ) -> String {
        let cache_key = self.prompt_cache_key(disabled_map);
        {
            let cache = self.prompt_cache.lock().unwrap();
            if let Some((k, v)) = cache.as_ref() {
                if *k == cache_key {
                    return v.clone();
                }
            }
        }

        let tools = self.get_all_tools(disabled_map);
        if tools.is_empty() {
            // Python returns "" WITHOUT caching when there are no tools.
            return String::new();
        }

        let mut lines: Vec<String> = vec![
            "\n\nYou also have access to external MCP tool servers. These tools are called via native function calling:".to_string(),
        ];

        // Group by server_name, preserving first-seen insertion order.
        let mut order: Vec<String> = Vec::new();
        let mut by_server: HashMap<String, Vec<Value>> = HashMap::new();
        for t in &tools {
            let server_id = t.get("server_id").and_then(|s| s.as_str()).unwrap_or("");
            if self.is_builtin(server_id) && server_id != "builtin_browser" {
                continue;
            }
            if t.get("is_disabled").and_then(|d| d.as_bool()).unwrap_or(false) {
                continue;
            }
            let sn = t
                .get("server_name")
                .and_then(|s| s.as_str())
                .unwrap_or("")
                .to_string();
            by_server.entry(sn.clone()).or_insert_with(|| {
                order.push(sn.clone());
                Vec::new()
            });
            by_server.get_mut(&sn).unwrap().push(t.clone());
        }

        if order.is_empty() {
            // Python returns "" WITHOUT caching when nothing survives filtering.
            return String::new();
        }

        let connections = self.connections.lock().unwrap();
        for server_name in &order {
            let server_tools = &by_server[server_name];
            let sid = server_tools
                .first()
                .and_then(|t| t.get("server_id"))
                .and_then(|s| s.as_str())
                .unwrap_or("");
            let identity = connections
                .get(sid)
                .and_then(|c| c.get("identity"))
                .and_then(|i| i.as_str())
                .unwrap_or("");
            let label = if identity.is_empty() {
                server_name.clone()
            } else {
                format!("{server_name} ({identity})")
            };
            lines.push(format!("\n**{label}:**"));
            for t in server_tools {
                let qualified = t
                    .get("qualified_name")
                    .and_then(|q| q.as_str())
                    .unwrap_or("");
                let description = t.get("description").and_then(|d| d.as_str()).unwrap_or("");
                // `desc[:120] + '...'` slices by Python *characters*, not bytes.
                let desc = if description.chars().count() > 120 {
                    let head: String = description.chars().take(120).collect();
                    format!("{head}...")
                } else {
                    description.to_string()
                };
                lines.push(format!("  - {qualified}: {desc}"));
            }
        }
        drop(connections);

        let result = lines.join("\n");
        *self.prompt_cache.lock().unwrap() = Some((cache_key, result.clone()));
        result
    }

    /// Build the `get_tool_descriptions_for_prompt` cache key (`(frozenset of
    /// (server_id, frozenset(disabled)), len(self._tools), self._generation)`).
    fn prompt_cache_key(
        &self,
        disabled_map: Option<&HashMap<String, HashSet<String>>>,
    ) -> PromptCacheKey {
        let mut key: BTreeMap<String, Vec<String>> = BTreeMap::new();
        if let Some(map) = disabled_map {
            for (k, v) in map {
                let mut names: Vec<String> = v.iter().cloned().collect();
                names.sort();
                key.insert(k.clone(), names);
            }
        }
        let n = self.tools.lock().unwrap().len();
        let gen = self.generation.load(Ordering::SeqCst);
        (key, n, gen)
    }
}

/// Parse a `tools/list` result's `tools[]` array into the `ToolSchema` list,
/// shared by both `_connect_stdio` and `_connect_sse`. Mirrors the Python loop
/// in both `_connect_stdio`/`_connect_sse` exactly: `tool.name` (default `""`),
/// `tool.description or ""`, and `tool.inputSchema` (default `{}`).
fn parse_tools_result(tools_result: &Value) -> Vec<ToolSchema> {
    let mut tools: Vec<ToolSchema> = Vec::new();
    if let Some(arr) = tools_result.get("tools").and_then(|t| t.as_array()) {
        for tool in arr {
            let name = tool
                .get("name")
                .and_then(|n| n.as_str())
                .unwrap_or("")
                .to_string();
            let description = tool
                .get("description")
                .and_then(|d| d.as_str())
                .unwrap_or("")
                .to_string();
            let input_schema = tool.get("inputSchema").cloned().unwrap_or(json!({}));
            tools.push(ToolSchema {
                name,
                description,
                input_schema,
            });
        }
    }
    tools
}

/// Parse one MCP `tools/call` content block into a typed [`ContentBlock`],
/// mirroring the Python `_do_call` branch logic: a `text` field -> `Text`;
/// `type == "image"` + `data` -> `Image`; any other `data` field -> `Other`.
fn parse_content_block(block: &Value) -> Option<ContentBlock> {
    if let Some(text) = block.get("text").and_then(|t| t.as_str()) {
        return Some(ContentBlock::Text(text.to_string()));
    }
    let btype = block.get("type").and_then(|t| t.as_str()).unwrap_or("");
    if btype == "image" {
        if let Some(data) = block.get("data").and_then(|d| d.as_str()) {
            let mime = block
                .get("mimeType")
                .and_then(|m| m.as_str())
                .unwrap_or("image/png")
                .to_string();
            return Some(ContentBlock::Image {
                data: data.to_string(),
                mime,
            });
        }
    }
    if let Some(data) = block.get("data") {
        // `str(content.data)`: a JSON string renders as-is; anything else as its
        // compact JSON form (the closest faithful analogue of Python `str(...)`).
        let s = match data {
            Value::String(s) => s.clone(),
            other => other.to_string(),
        };
        return Some(ContentBlock::Other(s));
    }
    None
}

// ---------------------------------------------------------------------------
// Shared singleton.
// ---------------------------------------------------------------------------

/// A process-wide MCP manager fallback singleton (the Python module keeps one
/// shared instance). The live server instead creates an `Arc<McpManager>` at
/// `web/mod.rs` startup and shares THAT instance with the routes,
/// `register_builtin_servers`, and the `agent_tools` slot; this `Lazy` exists for
/// any code path that wants a manager without that wiring.
static MANAGER: Lazy<McpManager> = Lazy::new(McpManager::new);

/// Access the shared `McpManager` singleton.
pub fn manager() -> &'static McpManager {
    &MANAGER
}

#[cfg(test)]
mod tests {
    use super::*;

    fn disabled(pairs: &[(&str, &[&str])]) -> HashMap<String, HashSet<String>> {
        pairs
            .iter()
            .map(|(k, v)| {
                (
                    k.to_string(),
                    v.iter().map(|s| s.to_string()).collect::<HashSet<String>>(),
                )
            })
            .collect()
    }

    fn seed(mgr: &McpManager, server_id: &str, name: &str, identity: &str, tools: &[(&str, &str)]) {
        let schemas: Vec<ToolSchema> = tools
            .iter()
            .map(|(n, d)| ToolSchema {
                name: n.to_string(),
                description: d.to_string(),
                input_schema: json!({"type": "object", "properties": {"q": {"type": "string"}}}),
            })
            .collect();
        mgr.tools
            .lock()
            .unwrap()
            .insert(server_id.to_string(), schemas);
        let mut rec = Map::new();
        rec.insert("status".into(), json!("connected"));
        rec.insert("name".into(), json!(name));
        rec.insert("identity".into(), json!(identity));
        mgr.connections
            .lock()
            .unwrap()
            .insert(server_id.to_string(), rec);
    }

    #[test]
    fn is_builtin_matches_prefix_and_fixed_set() {
        let mgr = McpManager::new();
        assert!(mgr.is_builtin("builtin_browser"));
        assert!(mgr.is_builtin("builtin_anything"));
        assert!(mgr.is_builtin("memory"));
        assert!(mgr.is_builtin("rag"));
        assert!(mgr.is_builtin("email"));
        assert!(mgr.is_builtin("image_gen"));
        assert!(!mgr.is_builtin("playwright"));
        assert!(!mgr.is_builtin("custom_server"));
    }

    #[test]
    fn openai_schemas_namespace_and_label_and_filter() {
        let mgr = McpManager::new();
        seed(
            &mgr,
            "gmail1",
            "Gmail",
            "alice@example.com",
            &[("send", "Send an email"), ("search", "Search the inbox")],
        );
        let schemas = mgr.get_all_openai_schemas(Some(&disabled(&[("gmail1", &["search"])])));
        assert_eq!(schemas.len(), 1);
        let f = &schemas[0]["function"];
        assert_eq!(f["name"], json!("mcp__gmail1__send"));
        assert_eq!(
            f["description"],
            json!("[MCP:Gmail (alice@example.com)] Send an email")
        );
        // input_schema is forwarded as parameters.
        assert_eq!(f["parameters"]["type"], json!("object"));
    }

    #[test]
    fn openai_schemas_skip_builtin_python_servers_except_browser() {
        let mgr = McpManager::new();
        seed(&mgr, "memory", "Memory", "", &[("recall", "x")]);
        seed(&mgr, "builtin_browser", "Browser", "", &[("open", "y")]);
        let schemas = mgr.get_all_openai_schemas(None);
        // Only builtin_browser survives; the python builtin `memory` is skipped.
        assert_eq!(schemas.len(), 1);
        assert_eq!(schemas[0]["function"]["name"], json!("mcp__builtin_browser__open"));
    }

    #[test]
    fn get_all_tools_marks_disabled_and_qualifies() {
        let mgr = McpManager::new();
        seed(&mgr, "srv", "Srv", "", &[("a", "desc a"), ("b", "desc b")]);
        let tools = mgr.get_all_tools(Some(&disabled(&[("srv", &["b"])])));
        assert_eq!(tools.len(), 2);
        let a = tools.iter().find(|t| t["name"] == json!("a")).unwrap();
        assert_eq!(a["qualified_name"], json!("mcp__srv__a"));
        assert_eq!(a["is_disabled"], json!(false));
        let b = tools.iter().find(|t| t["name"] == json!("b")).unwrap();
        assert_eq!(b["is_disabled"], json!(true));
    }

    #[test]
    fn server_status_unknown_is_disconnected() {
        let mgr = McpManager::new();
        assert_eq!(mgr.get_server_status("nope"), json!({"status": "disconnected"}));
        seed(&mgr, "srv", "Srv", "", &[("a", "x")]);
        assert_eq!(
            mgr.get_server_status("srv")["status"],
            json!("connected")
        );
    }

    #[test]
    fn prompt_description_truncates_at_120_chars_and_caches() {
        let mgr = McpManager::new();
        let long = "z".repeat(200);
        seed(&mgr, "srv", "MyServer", "", &[("tool1", long.as_str())]);
        let out = mgr.get_tool_descriptions_for_prompt(None);
        assert!(out.contains("**MyServer:**"));
        assert!(out.contains("mcp__srv__tool1: "));
        // 120 z's + the literal ellipsis.
        assert!(out.contains(&format!("{}...", "z".repeat(120))));
        assert!(!out.contains(&"z".repeat(121)));
        // Second call hits the cache (same key) and returns the same string.
        let out2 = mgr.get_tool_descriptions_for_prompt(None);
        assert_eq!(out, out2);
    }

    #[test]
    fn prompt_description_empty_when_only_builtin_python_servers() {
        let mgr = McpManager::new();
        seed(&mgr, "memory", "Memory", "", &[("recall", "x")]);
        // builtin python server filtered out -> empty (and not cached).
        assert_eq!(mgr.get_tool_descriptions_for_prompt(None), "");
    }

    #[test]
    fn prompt_cache_key_is_order_independent_for_disabled_sets() {
        let mgr = McpManager::new();
        let k1 = mgr.prompt_cache_key(Some(&disabled(&[("s", &["a", "b"])])));
        let k2 = mgr.prompt_cache_key(Some(&disabled(&[("s", &["b", "a"])])));
        assert_eq!(k1, k2);
    }

    #[test]
    fn prompt_cache_key_includes_generation() {
        let mgr = McpManager::new();
        let before = mgr.prompt_cache_key(None);
        // A reconnect bumps `_generation` even when `len(self._tools)` is unchanged.
        mgr.generation.fetch_add(1, Ordering::SeqCst);
        let after = mgr.prompt_cache_key(None);
        assert_ne!(before, after);
        assert_eq!(before.2, 0);
        assert_eq!(after.2, 1);
    }

    #[test]
    fn prompt_description_cache_invalidated_by_generation_bump() {
        // The whole point of folding `_generation` into the cache key: a reconnect
        // can swap a server's tool set (same server, same `len(self._tools)`)
        // without the cache noticing. With the generation in the key, the stale
        // cached description must NOT be returned after a bump.
        let mgr = McpManager::new();
        seed(&mgr, "srv", "MyServer", "", &[("tool1", "old description")]);
        let first = mgr.get_tool_descriptions_for_prompt(None);
        assert!(first.contains("mcp__srv__tool1: old description"));

        // Simulate a reconnect: replace the tool set IN PLACE (server count and
        // disabled map are identical, so only the generation differs) and bump.
        mgr.tools.lock().unwrap().insert(
            "srv".to_string(),
            vec![ToolSchema {
                name: "tool1".to_string(),
                description: "new description".to_string(),
                input_schema: json!({}),
            }],
        );
        mgr.generation.fetch_add(1, Ordering::SeqCst);

        let second = mgr.get_tool_descriptions_for_prompt(None);
        assert!(
            second.contains("mcp__srv__tool1: new description"),
            "generation bump must invalidate the stale cached description, got: {second}"
        );
        assert!(!second.contains("old description"));
    }

    #[test]
    fn format_mcp_connection_error_special_cases_playwright() {
        // A command line mentioning @playwright/mcp gets the npx-cache remediation
        // hint prepended to the raw error.
        let msg = _format_mcp_connection_error(
            "Browser",
            "npx",
            &[
                "-y".to_string(),
                "@playwright/mcp@latest".to_string(),
            ],
            "spawn ENOENT",
        );
        assert!(msg.starts_with("spawn ENOENT"));
        assert!(msg.contains("Browser MCP could not start"));
        assert!(msg.contains("npx -y @playwright/mcp@latest --version"));
        assert!(msg.contains("reconnect the Browser MCP server"));
    }

    #[test]
    fn format_mcp_connection_error_passes_through_other_commands() {
        // A non-playwright command returns the raw error unchanged.
        let msg = _format_mcp_connection_error(
            "Gmail",
            "node",
            &["server.js".to_string()],
            "connection refused",
        );
        assert_eq!(msg, "connection refused");
        // An empty error becomes "Unknown error".
        let msg2 = _format_mcp_connection_error("Gmail", "node", &[], "");
        assert_eq!(msg2, "Unknown error");
    }

    #[tokio::test]
    async fn connect_server_error_path_uses_playwright_formatting() {
        // Driving the public `connect_server` down the error path (a bad stdio
        // command that cannot spawn) with a @playwright/mcp command line records the
        // friendlier formatted message in the connection's `error` field.
        let mgr = McpManager::new();
        let ok = mgr
            .connect_server(
                "browser",
                "Browser",
                "stdio",
                Some("this-binary-does-not-exist-odysseus"),
                vec![
                    "-y".to_string(),
                    "@playwright/mcp@latest".to_string(),
                ],
                HashMap::new(),
                None,
            )
            .await;
        assert!(!ok);
        let status = mgr.get_server_status("browser");
        assert_eq!(status["status"], json!("error"));
        let err = status["error"].as_str().unwrap();
        assert!(
            err.contains("Browser MCP could not start"),
            "error path should use the playwright formatting, got: {err}"
        );
        assert!(err.contains("npx -y @playwright/mcp@latest --version"));
    }

    #[test]
    fn parse_content_block_text_image_other() {
        match parse_content_block(&json!({"type": "text", "text": "hello"})) {
            Some(ContentBlock::Text(t)) => assert_eq!(t, "hello"),
            _ => panic!("expected text"),
        }
        match parse_content_block(&json!({"type": "image", "data": "BASE64", "mimeType": "image/jpeg"})) {
            Some(ContentBlock::Image { data, mime }) => {
                assert_eq!(data, "BASE64");
                assert_eq!(mime, "image/jpeg");
            }
            _ => panic!("expected image"),
        }
        // image without explicit mimeType defaults to image/png.
        match parse_content_block(&json!({"type": "image", "data": "X"})) {
            Some(ContentBlock::Image { mime, .. }) => assert_eq!(mime, "image/png"),
            _ => panic!("expected image"),
        }
        match parse_content_block(&json!({"type": "resource", "data": {"k": 1}})) {
            Some(ContentBlock::Other(s)) => assert!(s.contains("\"k\":1")),
            _ => panic!("expected other"),
        }
        assert!(parse_content_block(&json!({"type": "unknown"})).is_none());
    }

    #[tokio::test]
    async fn call_tool_rejects_bad_qualified_names() {
        let mgr = McpManager::new();
        let r = mgr.call_tool("not_mcp__x__y", json!({})).await;
        assert_eq!(r["exit_code"], json!(1));
        assert!(r["error"].as_str().unwrap().contains("Invalid MCP tool name"));

        let r2 = mgr.call_tool("mcp__only_two", json!({})).await;
        assert_eq!(r2["exit_code"], json!(1));
        assert!(r2["error"].as_str().unwrap().contains("Invalid MCP tool name"));
    }

    #[tokio::test]
    async fn call_tool_unknown_server_is_not_connected() {
        let mgr = McpManager::new();
        let r = mgr.call_tool("mcp__ghost__do", json!({})).await;
        assert_eq!(r["exit_code"], json!(1));
        assert_eq!(
            r["error"],
            json!("MCP server not connected: ghost")
        );
    }

    #[tokio::test]
    async fn reconnect_builtin_rejects_non_builtin_ids() {
        let mgr = McpManager::new();
        // Not one of the four builtin script-server ids (image_gen/memory/rag/
        // email) -> `false` WITHOUT a spawn attempt (Python `server_id not in
        // _BUILTIN_SERVERS`). `builtin_browser` is an NPX server, not a script
        // server, so it is not in the reconnect table either.
        assert!(!mgr._reconnect_builtin("builtin_browser").await);
        assert!(!mgr._reconnect_builtin("playwright").await);
        assert!(!mgr._reconnect_builtin("totally_unknown").await);
    }

    #[test]
    fn connected_server_snapshots_skips_builtins_and_carries_state() {
        let mgr = McpManager::new();
        // A non-builtin connected server with an identity + two tools.
        seed(
            &mgr,
            "gmail1",
            "Gmail",
            "alice@example.com",
            &[("send", "Send an email"), ("search", "Search the inbox")],
        );
        // A builtin python server (filtered out of the snapshot).
        seed(&mgr, "memory", "Memory", "", &[("recall", "x")]);

        let snaps = mgr.connected_server_snapshots();
        // Only the non-builtin server is present.
        assert_eq!(snaps.len(), 1);
        let (sid, tool_names, status, identity) = &snaps[0];
        assert_eq!(sid, "gmail1");
        assert_eq!(tool_names, &vec!["send".to_string(), "search".to_string()]);
        // `seed` records status="connected".
        assert_eq!(status, "connected");
        assert_eq!(identity, "alice@example.com");
    }

    #[test]
    fn connected_server_snapshots_defaults_status_when_unrecorded() {
        let mgr = McpManager::new();
        // Tools registered but no connection record at all -> default
        // "disconnected" status + empty identity (the consumer's connected
        // filter then drops it, matching the Python).
        mgr.tools.lock().unwrap().insert(
            "ghost".to_string(),
            vec![ToolSchema {
                name: "a".to_string(),
                description: "x".to_string(),
                input_schema: json!({}),
            }],
        );
        let snaps = mgr.connected_server_snapshots();
        assert_eq!(snaps.len(), 1);
        let (sid, _tool_names, status, identity) = &snaps[0];
        assert_eq!(sid, "ghost");
        assert_eq!(status, "disconnected");
        assert_eq!(identity, "");
    }

    #[tokio::test]
    async fn connect_server_unknown_transport_returns_false() {
        let mgr = McpManager::new();
        let ok = mgr
            .connect_server("s", "S", "carrier-pigeon", None, vec![], HashMap::new(), None)
            .await;
        assert!(!ok);
    }

    #[tokio::test]
    async fn connect_sse_unreachable_records_error_status() {
        // The SSE transport is now ported (hand-rolled over reqwest). With no SSE
        // server reachable, the GET fails (connection refused / DNS error) and the
        // connect returns `false`, recording an `error` status via
        // `connect_server`'s catch — honest, never faked. The exact error TEXT is
        // network/reqwest-dependent, so we only assert `ok == false` + the
        // `error` status shape (port-3 of localhost is reliably closed in CI).
        let mgr = McpManager::new();
        let ok = mgr
            .connect_server(
                "s",
                "S",
                "sse",
                None,
                vec![],
                HashMap::new(),
                Some("http://127.0.0.1:3/sse"),
            )
            .await;
        assert!(!ok);
        let status = mgr.get_server_status("s");
        assert_eq!(status["status"], json!("error"));
        assert_eq!(status["name"], json!("S"));
        // An `error` member is present (the reqwest failure string).
        assert!(status.get("error").is_some());
    }

    #[test]
    fn take_sse_frame_parses_event_and_multi_data_and_ignores_comments() {
        // A full frame: event + two data lines (joined by \n) + the blank-line
        // terminator. A leading `:`-comment line is ignored; `id:`/`retry:` too.
        let mut buf = String::from(
            ": keep-alive comment\nid: 7\nevent: message\ndata: {\"a\":1,\ndata: \"b\":2}\nretry: 5000\n\nleftover",
        );
        let frame = take_sse_frame(&mut buf).expect("a complete frame");
        assert_eq!(frame.event, "message");
        assert_eq!(frame.data, "{\"a\":1,\n\"b\":2}");
        // Only the leftover (no full frame yet) remains in the buffer.
        assert_eq!(buf, "leftover");
        // The leftover alone is not a complete frame.
        assert!(take_sse_frame(&mut buf).is_none());
        // A bare data line with no event defaults to the "message" event.
        let mut buf2 = String::from("data: hello\n\n");
        let f2 = take_sse_frame(&mut buf2).expect("frame");
        assert_eq!(f2.event, "message");
        assert_eq!(f2.data, "hello");
    }

    /// A minimal in-process MCP SSE server for the happy-path test. Speaks just
    /// enough HTTP over a raw TCP socket: one GET opens the SSE stream (sends the
    /// `endpoint` frame, then streams `event: message` responses correlated by id),
    /// and each POST to `/message` is a JSON-RPC request whose response is pushed
    /// onto the held-open SSE connection. Returns the bound `127.0.0.1:PORT` base.
    async fn spawn_mock_sse_server() -> String {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        use tokio::net::TcpListener;

        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let base = format!("http://{addr}");

        tokio::spawn(async move {
            // The single SSE writer half, set when the GET connection arrives, so
            // POST handlers can push responses onto it.
            let sse_writer: Arc<AsyncMutex<Option<tokio::net::tcp::OwnedWriteHalf>>> =
                Arc::new(AsyncMutex::new(None));

            loop {
                let (sock, _) = match listener.accept().await {
                    Ok(p) => p,
                    Err(_) => break,
                };
                let sse_writer = Arc::clone(&sse_writer);
                tokio::spawn(async move {
                    let (mut rd, mut wr) = sock.into_split();
                    // Read the request head (headers end at \r\n\r\n).
                    let mut buf = Vec::new();
                    let mut tmp = [0u8; 1024];
                    let header_end = loop {
                        let n = match rd.read(&mut tmp).await {
                            Ok(0) | Err(_) => return,
                            Ok(n) => n,
                        };
                        buf.extend_from_slice(&tmp[..n]);
                        if let Some(pos) = buf
                            .windows(4)
                            .position(|w| w == b"\r\n\r\n")
                        {
                            break pos + 4;
                        }
                    };
                    let head = String::from_utf8_lossy(&buf[..header_end]).to_string();
                    let first_line = head.lines().next().unwrap_or("");

                    if first_line.starts_with("GET ") {
                        // Open the SSE stream: send headers + the endpoint frame.
                        let _ = wr
                            .write_all(
                                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n\
                                  event: endpoint\r\ndata: /message\r\n\r\n",
                            )
                            .await;
                        let _ = wr.flush().await;
                        // Hand the writer to the POST handlers and hold the
                        // connection open.
                        *sse_writer.lock().await = Some(wr);
                        // Keep this task alive so the socket is not dropped.
                        loop {
                            tokio::time::sleep(std::time::Duration::from_secs(3600)).await;
                        }
                    }

                    // Otherwise it is a POST /message — read the JSON body.
                    let content_len = head
                        .lines()
                        .find_map(|l| {
                            l.to_ascii_lowercase()
                                .strip_prefix("content-length:")
                                .map(|v| v.trim().parse::<usize>().unwrap_or(0))
                        })
                        .unwrap_or(0);
                    let mut body = buf[header_end..].to_vec();
                    while body.len() < content_len {
                        let n = match rd.read(&mut tmp).await {
                            Ok(0) | Err(_) => break,
                            Ok(n) => n,
                        };
                        body.extend_from_slice(&tmp[..n]);
                    }
                    let req: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);
                    // Acknowledge the POST itself (the real transport returns 202).
                    let _ = wr
                        .write_all(b"HTTP/1.1 202 Accepted\r\nContent-Length: 0\r\n\r\n")
                        .await;
                    let _ = wr.flush().await;

                    // Build the JSON-RPC response keyed by method, push onto SSE.
                    let id = req.get("id").cloned();
                    let method = req.get("method").and_then(|m| m.as_str()).unwrap_or("");
                    let result = match method {
                        "initialize" => json!({"protocolVersion": "2024-11-05"}),
                        "tools/list" => json!({"tools": [
                            {"name": "echo", "description": "Echo back",
                             "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}}}
                        ]}),
                        "tools/call" => json!({"content": [{"type": "text", "text": "pong"}]}),
                        _ => return, // notifications (no id) get no response
                    };
                    if let Some(id) = id {
                        let resp =
                            json!({"jsonrpc": "2.0", "id": id, "result": result}).to_string();
                        let frame = format!("event: message\r\ndata: {resp}\r\n\r\n");
                        if let Some(sse) = sse_writer.lock().await.as_mut() {
                            let _ = sse.write_all(frame.as_bytes()).await;
                            let _ = sse.flush().await;
                        }
                    }
                });
            }
        });

        base
    }

    #[tokio::test]
    async fn connect_sse_handshake_discovers_tools_and_calls_them() {
        // End-to-end happy path against the in-process mock MCP SSE server: the
        // GET opens the stream + sends the `endpoint` frame, the handshake
        // (initialize / notifications-initialized / tools/list) runs over POST with
        // responses correlated off the SSE stream, tools are discovered, and a
        // subsequent `call_tool` flows through `Session::Sse` identically to stdio.
        let base = spawn_mock_sse_server().await;
        let mgr = McpManager::new();

        let ok = mgr
            .connect_server(
                "remote1",
                "Remote",
                "sse",
                None,
                vec![],
                HashMap::new(),
                Some(&format!("{base}/sse")),
            )
            .await;
        assert!(ok, "SSE connect should succeed against the mock server");

        // Connection record: connected + transport "sse" + tool_count, and (per
        // Python lines 139-144) NO `identity` key.
        let status = mgr.get_server_status("remote1");
        assert_eq!(status["status"], json!("connected"));
        assert_eq!(status["transport"], json!("sse"));
        assert_eq!(status["tool_count"], json!(1));
        assert!(
            status.get("identity").is_none(),
            "SSE record must omit identity to match Python"
        );

        // Tools were discovered and are exposed identically to a stdio server.
        let tools = mgr.get_all_tools(None);
        assert_eq!(tools.len(), 1);
        assert_eq!(tools[0]["qualified_name"], json!("mcp__remote1__echo"));

        // call_tool flows through Session::Sse -> _do_call uniformly.
        let result = mgr
            .call_tool("mcp__remote1__echo", json!({"q": "ping"}))
            .await;
        assert_eq!(result["exit_code"], json!(0));
        assert_eq!(result["stdout"], json!("pong"));

        // Disconnect aborts the reader task (SseSession::drop) and forgets state.
        mgr.disconnect_server("remote1").await;
        assert_eq!(
            mgr.get_server_status("remote1"),
            json!({"status": "disconnected"})
        );
    }
}
