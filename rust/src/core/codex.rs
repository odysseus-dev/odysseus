// core/codex.rs  — connect via the OpenAI Codex subscription
//! A provider that drives the **`codex` CLI's `app-server`** (the same approach
//! as the t3code repo: "T3 Code starts the server via `codex app-server` per
//! session"). Auth is delegated entirely to the Codex CLI — `codex login` writes
//! ChatGPT-subscription OAuth tokens to `CODEX_HOME/auth.json`, and the
//! app-server uses them — so a logged-in Codex subscription becomes a chat
//! provider here with no API key.
//!
//! Transport: newline-delimited JSON-RPC over the child's stdin/stdout. Requests
//! are `{id, method, params}`; responses `{id, result}` / `{id, error}`;
//! notifications `{method, params}`. The chat flow (verified against codex
//! 0.135.0) is:
//!   initialize -> initialized -> thread/start -> turn/start
//!   -> stream `item/agentMessage/delta {delta}` -> `turn/completed`
//! plus `thread/tokenUsage/updated` for usage and `item/reasoning/*Delta` for
//! reasoning. `stream_chat` re-emits these as the SAME SSE vocabulary as
//! `llm_core::stream_llm` (`data:{"delta":…}` / `{"type":"usage"}` /
//! `event: error` / `[DONE]`), so the chat handler treats Codex and HTTP LLMs
//! identically.
//!
//! ## Full tool utilization (Mode A as a real agent)
//!
//! Codex runs its OWN tools SERVER-SIDE (shell/exec, `apply_patch` file edits,
//! MCP, web search). The thread is started with a **writable** sandbox
//! (`workspace-write`) and an **`on-request`** approval policy so codex actually
//! executes commands and applies patches; the few escalation prompts codex still
//! raises (writes outside cwd, network access) are AUTO-APPROVED by the
//! server→client request handler — replying with a RESULT (`acceptForSession` /
//! permissions echo / `answers:{}`), never a deny, so a turn never blocks on the
//! UI. The maximal `never` + `danger-full-access` posture (no prompts at all) is
//! available behind the env opt-in `ODYSSEUS_CODEX_DANGER_FULL_ACCESS=1`
//! (default OFF). Codex's tool activity is SURFACED to the Odysseus UI by mapping
//! its lifecycle/stream notifications (`item/started`, `item/completed`,
//! `item/commandExecution/outputDelta`, `item/fileChange/patchUpdated`,
//! `item/mcpToolCall/progress`) into the SAME `tool_start` / `tool_output` /
//! `tool_progress` / `agent_step` SSE events the Odysseus agent loop emits, so
//! codex's shell runs and patches render as tool bubbles in the chat UI.
//!
//! Endpoints are addressed by a `codex:` URL scheme: `codex:` (default
//! `~/.codex`) or `codex:/path/to/home` (a specific `CODEX_HOME`, e.g. a second
//! account — see the t3code "shadow home" docs).

use once_cell::sync::Lazy;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::process::Stdio;
use std::sync::Arc;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, Command};
use tokio::sync::{mpsc, Mutex as AsyncMutex};

/// The `codex` binary: `$CODEX_BIN`, else the first existing well-known path,
/// else `codex` (resolved via `PATH`).
pub fn codex_bin() -> String {
    if let Some(b) = crate::pyos::getenv_opt("CODEX_BIN") {
        if !b.is_empty() {
            return b;
        }
    }
    let home = crate::pyos::getenv("HOME", "");
    for cand in [
        format!("{home}/.bun/bin/codex"),
        "/opt/homebrew/bin/codex".to_string(),
        "/usr/local/bin/codex".to_string(),
    ] {
        if std::path::Path::new(&cand).exists() {
            return cand;
        }
    }
    "codex".to_string()
}

/// `endpoint_url == "codex"` or starts with `codex:` → a **Mode A** Codex
/// provider (the `codex app-server` harness). This stays Mode-A-ONLY: because
/// `"codex-responses:"` starts with `"codex-"` and NOT `"codex:"`, a
/// `codex-responses:` URL never matches here — the two schemes are disjoint, so
/// the Mode-A harness path never swallows a Mode-B (API) endpoint.
pub fn is_codex_url(url: &str) -> bool {
    url == "codex" || url.starts_with("codex:")
}

/// `endpoint_url` starts with `codex-responses:` → a **Mode B** Codex provider
/// (the ChatGPT Responses backend over HTTPS, driven by Odysseus's own agent
/// loop). DISTINCT from `is_codex_url`: `"codex-responses:".starts_with("codex:")`
/// is `false`, so the dispatch in `llm_core::stream_llm` keyed on this predicate
/// never collides with the Mode-A harness branch.
pub fn is_codex_responses_url(url: &str) -> bool {
    url.starts_with("codex-responses:")
}

/// Defensively strip a chat/models URL suffix that a mis-built endpoint url may have
/// appended to the scheme. An older `/api/models` served codex endpoints as
/// `{base}/chat/completions`, so existing sessions can carry a mangled
/// `codex:/chat/completions` / `codex-responses:/chat/completions` endpoint_url; without
/// this, the `<home>` tail parses as `/chat/completions` (→ a bogus CODEX_HOME / an
/// `auth.json` at `/chat/completions/auth.json`). Stripping it makes such a url resolve
/// to the DEFAULT home, so already-created sessions heal without a re-create.
fn strip_chat_suffix(s: &str) -> &str {
    let s = s.trim_end_matches('/');
    // The only mangle that ever occurred is `{scheme}/chat/completions` (model_routes
    // appended it to every non-anthropic base). Strip exactly that; don't over-strip
    // generic tails (a legitimate shadow home could end in `/v1`, `/models`, …).
    s.strip_suffix("/chat/completions").map(|p| p.trim_end_matches('/')).unwrap_or(s)
}

/// Parse the optional `CODEX_HOME` out of a `codex:<home>` URL. Empty → default.
pub fn codex_home(url: &str) -> Option<String> {
    let rest = url.strip_prefix("codex:").unwrap_or("");
    let rest = strip_chat_suffix(rest.trim());
    if rest.is_empty() {
        None
    } else {
        Some(expand_tilde(rest))
    }
}

/// Parse the optional `CODEX_HOME` out of a `codex-responses:<home>` URL. The
/// `<home>` tail selects which `auth.json` Mode B reads/refreshes (a second
/// account / shadow home). Empty → `None` (caller falls back to
/// `$CODEX_HOME` else `~/.codex`).
pub fn codex_responses_home(url: &str) -> Option<String> {
    let rest = url.strip_prefix("codex-responses:").unwrap_or("");
    let rest = strip_chat_suffix(rest.trim());
    if rest.is_empty() {
        None
    } else {
        Some(expand_tilde(rest))
    }
}

fn expand_tilde(p: &str) -> String {
    if let Some(stripped) = p.strip_prefix("~") {
        return format!("{}{}", crate::pyos::getenv("HOME", ""), stripped);
    }
    p.to_string()
}

/// Spawn `codex app-server` with the optional `CODEX_HOME`, piped stdio.
fn spawn(home: Option<&str>) -> std::io::Result<Child> {
    let mut cmd = Command::new(codex_bin());
    cmd.arg("app-server")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true);
    if let Some(h) = home {
        cmd.env("CODEX_HOME", h);
    }
    cmd.spawn()
}

async fn write_msg(stdin: &mut ChildStdin, msg: &Value) -> std::io::Result<()> {
    let mut line = msg.to_string();
    line.push('\n');
    stdin.write_all(line.as_bytes()).await?;
    stdin.flush().await
}

/// Read one JSON message (one line), with a per-line timeout. `None` on
/// EOF/timeout/parse error.
async fn next_msg<R: AsyncBufReadExt + Unpin>(reader: &mut R, secs: u64) -> Option<Value> {
    let mut line = String::new();
    let read = tokio::time::timeout(std::time::Duration::from_secs(secs), reader.read_line(&mut line)).await;
    match read {
        Ok(Ok(n)) if n > 0 => serde_json::from_str(line.trim()).ok(),
        _ => None,
    }
}

/// `initialize` + `initialized` handshake. Returns the initialize result.
async fn handshake<R: AsyncBufReadExt + Unpin>(
    stdin: &mut ChildStdin,
    reader: &mut R,
) -> Option<Value> {
    let _ = write_msg(
        stdin,
        &json!({"id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "odysseus", "title": "Odysseus", "version": "1.0.0"},
            "capabilities": {"experimentalApi": true, "optOutNotificationMethods": null},
        }}),
    )
    .await;
    // Read until the id=1 response (skipping any interleaved notifications).
    let mut init_result = None;
    for _ in 0..50 {
        match next_msg(reader, 30).await {
            Some(m) if m.get("id") == Some(&json!(1)) => {
                init_result = m.get("result").cloned();
                break;
            }
            Some(_) => continue,
            None => return None,
        }
    }
    init_result.as_ref()?;
    let _ = write_msg(stdin, &json!({"method": "initialized", "params": null})).await;
    init_result
}

/// Read until the response to `id` arrives (skipping notifications). Returns its
/// `result` (or `None` on error/timeout).
async fn await_result<R: AsyncBufReadExt + Unpin>(reader: &mut R, id: i64) -> Option<Value> {
    for _ in 0..200 {
        match next_msg(reader, 60).await {
            Some(m) if m.get("id") == Some(&json!(id)) => return m.get("result").cloned(),
            Some(_) => continue,
            None => return None,
        }
    }
    None
}

/// Run one request through a fresh handshake and return its `result`. Used for
/// `account/read` and `model/list`.
async fn oneshot(home: Option<&str>, method: &str, params: Value) -> Option<Value> {
    let mut child = spawn(home).ok()?;
    let mut stdin = child.stdin.take()?;
    let stdout = child.stdout.take()?;
    let mut reader = BufReader::new(stdout);
    handshake(&mut stdin, &mut reader).await?;
    write_msg(&mut stdin, &json!({"id": 2, "method": method, "params": params})).await.ok()?;
    let result = await_result(&mut reader, 2).await;
    let _ = child.start_kill();
    result
}

/// `account/read` — the signed-in Codex account: `{type, email, planType}`.
/// `None` if codex isn't installed / not logged in / unreachable.
pub async fn account_read(home: Option<&str>) -> Option<Value> {
    oneshot(home, "account/read", json!({})).await.and_then(|r| r.get("account").cloned())
}

/// `model/list` → the visible model ids (skips `hidden`).
pub async fn list_models(home: Option<&str>) -> Vec<String> {
    let result = match oneshot(home, "model/list", json!({})).await {
        Some(r) => r,
        None => return vec![],
    };
    result
        .get("data")
        .and_then(|d| d.as_array())
        .map(|arr| {
            arr.iter()
                .filter(|m| m.get("hidden").and_then(|h| h.as_bool()) != Some(true))
                .filter_map(|m| m.get("id").and_then(|i| i.as_str()).map(String::from))
                .collect()
        })
        .unwrap_or_default()
}

/// Build `(developer_instructions, input_text)` from the chat history: system
/// messages become developer instructions; the conversation is flattened into
/// the turn input (prior turns as context + the latest user message). Used to
/// SEED a freshly-started thread with the existing Odysseus history; once the
/// per-session thread exists, later turns send only the new user message
/// (`latest_user_text`) and codex keeps the running history itself.
fn build_input(messages: &[Value]) -> (Option<String>, String) {
    let mut sys: Vec<String> = Vec::new();
    let mut convo: Vec<(String, String)> = Vec::new();
    for m in messages {
        let role = m.get("role").and_then(|r| r.as_str()).unwrap_or("user");
        let content = m.get("content").and_then(|c| c.as_str()).unwrap_or("");
        if role == "system" {
            sys.push(content.to_string());
        } else {
            convo.push((role.to_string(), content.to_string()));
        }
    }
    let dev = if sys.is_empty() { None } else { Some(sys.join("\n\n")) };
    if convo.len() <= 1 {
        let text = convo.last().map(|(_, c)| c.clone()).unwrap_or_default();
        return (dev, text);
    }
    let (last_role, last_content) = convo.last().cloned().unwrap();
    let _ = last_role;
    let mut text = String::from("Continue this conversation. Prior messages:\n\n");
    for (role, content) in &convo[..convo.len() - 1] {
        let label = match role.as_str() {
            "assistant" => "Assistant",
            _ => "User",
        };
        text.push_str(&format!("{label}: {content}\n\n"));
    }
    text.push_str(&format!("Now respond to the latest user message:\n\n{last_content}"));
    (dev, text)
}

/// The latest user message text (sent on every turn after the thread is seeded).
fn latest_user_text(messages: &[Value]) -> String {
    messages
        .last()
        .and_then(|m| m.get("content"))
        .and_then(|c| c.as_str())
        .unwrap_or("")
        .to_string()
}

// ---------------------------------------------------------------------------
// Availability cache — so Codex is a first-class, auto-detected provider.
// ---------------------------------------------------------------------------

/// The signed-in Codex subscription, probed once and cached.
#[derive(Clone)]
pub struct CodexAvailability {
    pub email: String,
    pub plan: String,
    pub models: Vec<String>,
}

// outer Option: "probed yet?"; inner Option: "available?"
static AVAIL: Lazy<AsyncMutex<Option<Option<CodexAvailability>>>> = Lazy::new(|| AsyncMutex::new(None));

/// Probe the default Codex home once (cached). `Some` when the Codex CLI is
/// installed AND logged in. Held across the probe to dedupe concurrent callers.
pub async fn ensure_probed() -> Option<CodexAvailability> {
    let mut g = AVAIL.lock().await;
    if let Some(cached) = g.as_ref() {
        return cached.clone();
    }
    let avail = probe_now().await;
    *g = Some(avail.clone());
    avail
}

/// Force a fresh probe (used by the explicit `/api/codex/connect`).
pub async fn refresh_probe() -> Option<CodexAvailability> {
    let avail = probe_now().await;
    *AVAIL.lock().await = Some(avail.clone());
    avail
}

async fn probe_now() -> Option<CodexAvailability> {
    let account = account_read(None).await?;
    Some(CodexAvailability {
        email: account.get("email").and_then(|e| e.as_str()).unwrap_or("").to_string(),
        plan: account.get("planType").and_then(|p| p.as_str()).unwrap_or("").to_string(),
        models: list_models(None).await,
    })
}

// ---------------------------------------------------------------------------
// Persistent per-session threads.
// ---------------------------------------------------------------------------
//
// Each Odysseus chat session keeps a live `codex app-server` child + one codex
// thread, reused across turns — so codex maintains the running conversation and
// we send only the new user message each turn (not the whole transcript). A
// dedicated reader task fans the child's stdout into a channel; turns are
// serialized per session via the connection's async lock.

struct CodexConn {
    stdin: ChildStdin,
    rx: mpsc::UnboundedReceiver<Value>,
    thread_id: String,
    next_id: i64,
    _child: Child, // kill_on_drop: dies when the conn leaves the map
}

static CONNS: Lazy<AsyncMutex<HashMap<String, Arc<AsyncMutex<CodexConn>>>>> =
    Lazy::new(|| AsyncMutex::new(HashMap::new()));

/// Drop a session's Codex connection (kills its `codex app-server`). Called when
/// the Odysseus session is deleted.
pub async fn drop_conn(session_id: &str) {
    CONNS.lock().await.remove(session_id);
}

/// Get the session's live Codex connection, creating (spawn + handshake +
/// `thread/start`) it on first use. Returns `(conn, is_new)`.
async fn get_or_create(
    session_id: &str,
    home: Option<&str>,
    model: Option<&str>,
    dev_instructions: Option<&str>,
    cwd: &str,
) -> Result<(Arc<AsyncMutex<CodexConn>>, bool), String> {
    let mut map = CONNS.lock().await;
    if let Some(c) = map.get(session_id) {
        return Ok((c.clone(), false));
    }

    let mut child = spawn(home).map_err(|e| format!("Cannot launch codex: {e}. Is the Codex CLI installed?"))?;
    let mut stdin = child.stdin.take().ok_or("codex stdin unavailable")?;
    let stdout = child.stdout.take().ok_or("codex stdout unavailable")?;
    let mut reader = BufReader::new(stdout);

    handshake(&mut stdin, &mut reader)
        .await
        .ok_or("codex app-server handshake failed (is `codex login` done?)")?;

    // Full-tool posture. Default: a WRITABLE sandbox (`workspace-write`) so codex
    // runs shell/exec and `apply_patch` in-cwd freely, plus an `on-request`
    // approval policy so codex ASKS before out-of-policy escalations (writes
    // outside cwd, network) — which the server→client handler below
    // AUTO-APPROVES and surfaces to the UI. Setting
    // `ODYSSEUS_CODEX_DANGER_FULL_ACCESS=1` switches to the maximal
    // `never` + `danger-full-access` posture (everything runs with NO prompts);
    // this is a security-posture change and is OFF by default.
    let danger_full_access = matches!(
        crate::pyos::getenv("ODYSSEUS_CODEX_DANGER_FULL_ACCESS", "").as_str(),
        "1" | "true" | "True" | "yes"
    );
    let (approval_policy, sandbox) = if danger_full_access {
        ("never", "danger-full-access")
    } else {
        ("on-request", "workspace-write")
    };
    let mut start_params = json!({"cwd": cwd, "approvalPolicy": approval_policy, "sandbox": sandbox});
    if let (Some(obj), Some(d)) = (start_params.as_object_mut(), dev_instructions) {
        obj.insert("developerInstructions".into(), json!(d));
    }
    if let (Some(obj), Some(m)) = (start_params.as_object_mut(), model.filter(|m| !m.is_empty() && *m != "codex")) {
        obj.insert("model".into(), json!(m));
    }
    write_msg(&mut stdin, &json!({"id": 2, "method": "thread/start", "params": start_params}))
        .await
        .map_err(|e| format!("codex thread/start write failed: {e}"))?;
    let thread_id = await_result(&mut reader, 2)
        .await
        .and_then(|r| r.get("thread").and_then(|t| t.get("id")).and_then(|i| i.as_str()).map(String::from))
        .ok_or("codex thread/start failed")?;

    // Fan the child's stdout into a channel for the turn loop to consume.
    let (tx, rx) = mpsc::unbounded_channel();
    tokio::spawn(async move {
        let mut reader = reader;
        let mut line = String::new();
        loop {
            line.clear();
            match reader.read_line(&mut line).await {
                Ok(0) | Err(_) => break, // EOF / child died
                Ok(_) => {
                    if let Ok(v) = serde_json::from_str::<Value>(line.trim()) {
                        if tx.send(v).is_err() {
                            break;
                        }
                    }
                }
            }
        }
    });

    let conn = Arc::new(AsyncMutex::new(CodexConn { stdin, rx, thread_id, next_id: 3, _child: child }));
    map.insert(session_id.to_string(), conn.clone());
    Ok((conn, true))
}

/// Stream a chat turn through the session's persistent `codex app-server`
/// thread, yielding the same SSE vocabulary as `llm_core::stream_llm`.
///
/// On the first turn the thread is started (seeded with the existing Odysseus
/// history via `build_input`); later turns send only the new user message and
/// codex keeps the running conversation. Turns are serialized per session by the
/// connection lock.
pub fn stream_chat(
    session_id: String,
    home: Option<String>,
    model: Option<String>,
    messages: Vec<Value>,
    cwd: String,
) -> impl futures_util::Stream<Item = String> {
    async_stream::stream! {
        let (dev_instructions, _) = build_input(&messages);
        let (conn, is_new) = match get_or_create(
            &session_id, home.as_deref(), model.as_deref(), dev_instructions.as_deref(), &cwd,
        ).await {
            Ok(x) => x,
            Err(e) => { yield err_event(&e, 503); return; }
        };

        let mut guard = conn.lock().await;
        // Discard any notifications buffered between turns.
        while guard.rx.try_recv().is_ok() {}

        // First turn seeds the thread with the full transcript; later turns send
        // only the new user message (codex already has the history).
        let input_text = if is_new { build_input(&messages).1 } else { latest_user_text(&messages) };
        let turn_id = { let id = guard.next_id; guard.next_id += 1; id };
        let mut turn_params = json!({
            "threadId": guard.thread_id,
            "input": [{"type": "text", "text": input_text}],
        });
        if let (Some(obj), Some(m)) = (turn_params.as_object_mut(), model.as_ref().filter(|m| !m.is_empty() && *m != "codex")) {
            obj.insert("model".into(), json!(m));
        }
        if write_msg(&mut guard.stdin, &json!({"id": turn_id, "method": "turn/start", "params": turn_params})).await.is_err() {
            drop(guard);
            CONNS.lock().await.remove(&session_id); // stale connection — rebuild next turn
            yield err_event("codex turn/start write failed", 502);
            return;
        }

        // Per-turn correlation: codex item id -> (odysseus tool name, command/
        // path summary). `item/started` records it; the incremental
        // `*/outputDelta` / `*/patchUpdated` / `*/progress` notifications carry
        // only an `itemId`, so we look the tool name back up to tag the
        // `tool_output` / `tool_progress` events. Cleared each turn (local).
        let mut tools: HashMap<String, (String, String)> = HashMap::new();

        // Relay notifications until turn/completed (bounded per-message timeout).
        let mut dead = false;
        loop {
            let msg = match tokio::time::timeout(std::time::Duration::from_secs(180), guard.rx.recv()).await {
                Ok(Some(m)) => m,
                Ok(None) => { yield err_event("codex connection closed", 502); dead = true; break; }
                Err(_) => { yield err_event("codex turn timed out", 504); dead = true; break; }
            };
            // ---- Server -> client REQUESTS ({id, method}) ----
            // Full-tool mode: AUTO-APPROVE codex's approval/permission prompts
            // with a RESULT (never a deny), so shell/exec, apply_patch, network
            // escalations, etc. actually run. Replying with `{id, error}` (the old
            // behavior) blocked the whole turn. We also surface a `tool_start` so
            // the user sees the approval happened.
            if let (Some(id), Some(method_v)) = (msg.get("id"), msg.get("method")) {
                let method = method_v.as_str().unwrap_or("");
                let req_params = msg.get("params").cloned().unwrap_or(Value::Null);
                // Surface the approval to the UI first (so the bubble appears).
                if let Some(ev) = approval_tool_start(method, &req_params) {
                    yield ev;
                }
                let accept = approval_accept_payload(method, &req_params);
                let _ = write_msg(&mut guard.stdin, &json!({"id": id, "result": accept})).await;
                continue;
            }
            // ---- A {id, error} RESPONSE to our turn/start request => turn failed.
            if msg.get("id") == Some(&json!(turn_id)) {
                if let Some(err) = msg.get("error") {
                    let m = err.get("message").and_then(|x| x.as_str()).unwrap_or("codex turn failed");
                    yield err_event(m, 502);
                    dead = true;
                    break;
                }
                continue; // a successful turn/start result — nothing to emit.
            }
            let method = msg.get("method").and_then(|m| m.as_str()).unwrap_or("");
            let params = msg.get("params").cloned().unwrap_or(Value::Null);
            match method {
                // ---- Assistant text + reasoning deltas (unchanged) ----
                "item/agentMessage/delta" => {
                    if let Some(d) = params.get("delta").and_then(|d| d.as_str()) {
                        if !d.is_empty() {
                            yield format!("data: {}\n\n", json!({"delta": d}));
                        }
                    }
                }
                "item/reasoning/summaryTextDelta" | "item/reasoning/textDelta" => {
                    if let Some(d) = params.get("delta").and_then(|d| d.as_str()) {
                        if !d.is_empty() {
                            yield format!("data: {}\n\n", json!({"delta": d, "thinking": true}));
                        }
                    }
                }

                // ---- Tool lifecycle: a codex item begins ----
                "item/started" => {
                    if let Some((ev, key, entry)) = item_started_event(params.get("item")) {
                        if let (Some(k), Some(en)) = (key, entry) {
                            tools.insert(k, en);
                        }
                        if let Some(ev) = ev {
                            yield ev;
                        }
                    }
                }

                // ---- Tool streaming: INCREMENTAL output / patch / progress ----
                // These are streaming previews, NOT completions: emit `tool_progress`
                // (the frontend treats every `tool_output` as terminal and would
                // finalize the bubble early). The terminal `tool_output` with the
                // full aggregated output comes from `item/completed`.
                "item/commandExecution/outputDelta" => {
                    if let Some(d) = params.get("delta").and_then(|d| d.as_str()) {
                        if !d.is_empty() {
                            let tool = item_tool_name(&tools, &params, "shell");
                            yield format!("data: {}\n\n", json!({"type": "tool_progress", "tool": tool, "tail": d}));
                        }
                    }
                }
                "item/fileChange/patchUpdated" => {
                    let summary = changes_summary(params.get("changes"));
                    if !summary.is_empty() {
                        let tool = item_tool_name(&tools, &params, "apply_patch");
                        yield format!("data: {}\n\n", json!({"type": "tool_progress", "tool": tool, "tail": summary}));
                    }
                }
                "item/mcpToolCall/progress" => {
                    if let Some(m) = params.get("message").and_then(|x| x.as_str()) {
                        if !m.is_empty() {
                            yield format!("data: {}\n\n", json!({"type": "tool_progress", "output": m}));
                        }
                    }
                }

                // ---- Tool lifecycle: a codex item completes ----
                "item/completed" => {
                    for ev in item_completed_events(params.get("item")) {
                        yield ev;
                    }
                }

                // ---- Usage (unchanged) ----
                "thread/tokenUsage/updated" => {
                    if let Some(total) = params.get("tokenUsage").and_then(|t| t.get("total")) {
                        yield format!("data: {}\n\n", json!({"type": "usage", "data": {
                            "input_tokens": total.get("inputTokens").and_then(|x| x.as_i64()).unwrap_or(0),
                            "output_tokens": total.get("outputTokens").and_then(|x| x.as_i64()).unwrap_or(0),
                        }}));
                    }
                }

                // ---- Terminal (unchanged) ----
                "turn/completed" => {
                    yield "data: [DONE]\n\n".to_string();
                    break;
                }
                "turn/failed" | "thread/error" => {
                    let m = params
                        .get("error")
                        .and_then(|e| e.get("message"))
                        .and_then(|x| x.as_str())
                        .unwrap_or("codex turn failed");
                    yield err_event(m, 502);
                    dead = true;
                    break;
                }
                "error" => {
                    let m = params
                        .get("message")
                        .and_then(|x| x.as_str())
                        .unwrap_or("codex error");
                    yield err_event(m, 502);
                    dead = true;
                    break;
                }
                _ => {}
            }
        }
        // Drop the conn lock BEFORE touching the map (lock-order: never map-then-conn).
        drop(guard);
        if dead {
            CONNS.lock().await.remove(&session_id);
        }
    }
}

fn err_event(msg: &str, status: u16) -> String {
    format!("event: error\ndata: {}\n\n", json!({"error": msg, "status": status}))
}

// ---------------------------------------------------------------------------
// Server -> client approval requests: auto-approve + surface.
// ---------------------------------------------------------------------------

/// The RESULT payload that AUTO-APPROVES a server→client approval/permission
/// request (so the tool actually runs). Per the codex app-server schema:
///   - `item/commandExecution/requestApproval` -> `{decision:"acceptForSession"}`
///   - `item/fileChange/requestApproval`        -> `{decision:"acceptForSession"}`
///   - `item/permissions/requestApproval`       -> `{permissions:<echo>, scope:"session"}`
///   - `item/tool/requestUserInput`             -> `{answers:{}}`  (non-blocking)
///   - `item/tool/call` (DynamicToolCall)       -> `{contentItems:[], success:false}`
///   - legacy `execCommandApproval`/`applyPatchApproval` -> `{decision:"approved_for_session"}`
///   - anything else                            -> `{decision:"acceptForSession"}` (permissive)
fn approval_accept_payload(method: &str, params: &Value) -> Value {
    match method {
        "item/commandExecution/requestApproval" | "item/fileChange/requestApproval" => {
            json!({"decision": "acceptForSession"})
        }
        "item/permissions/requestApproval" => {
            // Echo back the requested permission profile, granted for the session.
            let permissions = params.get("permissions").cloned().unwrap_or_else(|| json!({}));
            json!({"permissions": permissions, "scope": "session"})
        }
        "item/tool/requestUserInput" => json!({"answers": {}}),
        // DynamicToolCall — Odysseus registers no dynamic tools, so this should
        // never arrive; reply success:false rather than block the turn.
        "item/tool/call" => json!({"contentItems": [], "success": false}),
        // Legacy approval methods on older CLI builds.
        "execCommandApproval" | "applyPatchApproval" => json!({"decision": "approved_for_session"}),
        // Permissive fallback: never deny — a deny would block the turn.
        _ => json!({"decision": "acceptForSession"}),
    }
}

/// A `tool_start` (or `agent_step`) SSE frame surfacing an auto-approved request
/// to the UI, so the user sees the tool gate happened. `None` for requests that
/// don't represent a tool gate worth showing.
fn approval_tool_start(method: &str, params: &Value) -> Option<String> {
    match method {
        "item/commandExecution/requestApproval" | "execCommandApproval" => {
            let command = params
                .get("command")
                .and_then(|c| c.as_str())
                .map(String::from)
                .or_else(|| {
                    // legacy execCommandApproval: command is an argv array.
                    params.get("command").and_then(|c| c.as_array()).map(|a| {
                        a.iter().filter_map(|x| x.as_str()).collect::<Vec<_>>().join(" ")
                    })
                })
                .unwrap_or_default();
            Some(format!(
                "data: {}\n\n",
                json!({"type": "tool_start", "tool": "shell", "command": command, "round": 0})
            ))
        }
        "item/fileChange/requestApproval" | "applyPatchApproval" => Some(format!(
            "data: {}\n\n",
            json!({"type": "tool_start", "tool": "apply_patch", "command": changes_paths(params.get("changes")), "round": 0})
        )),
        _ => None,
    }
}

// ---------------------------------------------------------------------------
// Codex thread-item lifecycle -> Odysseus tool SSE.
// ---------------------------------------------------------------------------

/// Look up the tool name recorded at `item/started` for the item the streaming
/// notification refers to (`itemId`), falling back to `default` when not seen.
fn item_tool_name(
    tools: &HashMap<String, (String, String)>,
    params: &Value,
    default: &str,
) -> String {
    params
        .get("itemId")
        .and_then(|i| i.as_str())
        .and_then(|id| tools.get(id))
        .map(|(t, _)| t.clone())
        .unwrap_or_else(|| default.to_string())
}

/// Map an `item/started` payload to `(tool_start SSE?, item_id?, (tool, summary)?)`.
/// The returned tuple's id+entry is recorded for later `outputDelta` correlation;
/// the SSE (when `Some`) is yielded. Returns `None` for non-tool items
/// (agentMessage/reasoning/plan — handled by the delta arms).
#[allow(clippy::type_complexity)]
fn item_started_event(
    item: Option<&Value>,
) -> Option<(Option<String>, Option<String>, Option<(String, String)>)> {
    let item = item?;
    let item_type = item.get("type").and_then(|t| t.as_str()).unwrap_or("");
    let id = item.get("id").and_then(|i| i.as_str()).map(String::from);
    match item_type {
        "commandExecution" => {
            let command = item.get("command").and_then(|c| c.as_str()).unwrap_or("").to_string();
            let ev = format!(
                "data: {}\n\n",
                json!({"type": "tool_start", "tool": "shell", "command": command, "round": 0})
            );
            Some((Some(ev), id, Some(("shell".to_string(), command))))
        }
        "fileChange" => {
            let paths = changes_paths(item.get("changes"));
            let ev = format!(
                "data: {}\n\n",
                json!({"type": "tool_start", "tool": "apply_patch", "command": paths, "round": 0})
            );
            Some((Some(ev), id, Some(("apply_patch".to_string(), paths))))
        }
        "mcpToolCall" => {
            let server = item.get("server").and_then(|s| s.as_str()).unwrap_or("");
            let tool = item.get("tool").and_then(|t| t.as_str()).unwrap_or("");
            let name = format!("{server}.{tool}");
            let summary = arguments_summary(item.get("arguments"));
            let ev = format!(
                "data: {}\n\n",
                json!({"type": "tool_start", "tool": name, "command": summary.clone(), "round": 0})
            );
            Some((Some(ev), id, Some((name, summary))))
        }
        "webSearch" => {
            let query = item.get("query").and_then(|q| q.as_str()).unwrap_or("").to_string();
            let ev = format!(
                "data: {}\n\n",
                json!({"type": "tool_start", "tool": "web_search", "command": query.clone(), "round": 0})
            );
            Some((Some(ev), id, Some(("web_search".to_string(), query))))
        }
        // agentMessage / reasoning / plan -> no tool_start (delta arms handle them).
        _ => None,
    }
}

/// Map an `item/completed` payload to the terminal `tool_output` SSE frame for
/// tool items. Empty for non-tool items (already streamed via the delta arms).
///
/// NOTE: no `agent_step` is emitted per tool — the frontend's `agent_step`
/// handler opens a fresh "Generating response" bubble, so one-per-tool would
/// leave a trail of empty bubbles. codex's own subsequent `agentMessage` deltas
/// land in the active bubble without a boundary frame.
fn item_completed_events(item: Option<&Value>) -> Vec<String> {
    let item = match item {
        Some(i) => i,
        None => return vec![],
    };
    let item_type = item.get("type").and_then(|t| t.as_str()).unwrap_or("");
    match item_type {
        "commandExecution" => {
            let command = item.get("command").and_then(|c| c.as_str()).unwrap_or("");
            let output = item.get("aggregatedOutput").and_then(|o| o.as_str()).unwrap_or("");
            let exit_code = item.get("exitCode").cloned().unwrap_or(Value::Null);
            vec![
                format!(
                    "data: {}\n\n",
                    json!({"type": "tool_output", "tool": "shell", "command": command, "output": output, "exit_code": exit_code})
                ),            ]
        }
        "fileChange" => {
            let output = changes_summary(item.get("changes"));
            let status = item.get("status").cloned().unwrap_or(Value::Null);
            vec![
                format!(
                    "data: {}\n\n",
                    json!({"type": "tool_output", "tool": "apply_patch", "output": output, "status": status})
                ),            ]
        }
        "mcpToolCall" => {
            let server = item.get("server").and_then(|s| s.as_str()).unwrap_or("");
            let tool = item.get("tool").and_then(|t| t.as_str()).unwrap_or("");
            let name = format!("{server}.{tool}");
            // result OR error, whichever is present.
            let output = item
                .get("result")
                .filter(|v| !v.is_null())
                .map(value_to_text)
                .or_else(|| item.get("error").filter(|v| !v.is_null()).map(value_to_text))
                .unwrap_or_default();
            let status = item.get("status").cloned().unwrap_or(Value::Null);
            vec![
                format!(
                    "data: {}\n\n",
                    json!({"type": "tool_output", "tool": name, "output": output, "status": status})
                ),            ]
        }
        // agentMessage / reasoning -> already streamed via delta arms.
        _ => vec![],
    }
}

/// Joined `path` list from a `changes` array (for the tool_start `command`).
fn changes_paths(changes: Option<&Value>) -> String {
    changes
        .and_then(|c| c.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|c| c.get("path").and_then(|p| p.as_str()))
                .collect::<Vec<_>>()
                .join(", ")
        })
        .unwrap_or_default()
}

/// A diff preview from a `changes` array: each entry's `path` header followed by
/// its unified `diff` (for the streaming/terminal apply_patch tool_output).
fn changes_summary(changes: Option<&Value>) -> String {
    changes
        .and_then(|c| c.as_array())
        .map(|arr| {
            arr.iter()
                .map(|c| {
                    let path = c.get("path").and_then(|p| p.as_str()).unwrap_or("");
                    let diff = c.get("diff").and_then(|d| d.as_str()).unwrap_or("");
                    if diff.is_empty() {
                        path.to_string()
                    } else {
                        format!("{path}\n{diff}")
                    }
                })
                .collect::<Vec<_>>()
                .join("\n")
        })
        .unwrap_or_default()
}

/// A compact one-line summary of an MCP tool-call `arguments` value (truncated).
fn arguments_summary(args: Option<&Value>) -> String {
    let s = match args {
        Some(Value::String(s)) => s.clone(),
        Some(v) if !v.is_null() => v.to_string(),
        _ => return String::new(),
    };
    if s.chars().count() > 200 {
        format!("{}…", s.chars().take(200).collect::<String>())
    } else {
        s
    }
}

/// Render an arbitrary JSON value as display text: strings as-is, else compact
/// JSON. Used for MCP result/error bodies in `tool_output`.
fn value_to_text(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        _ => v.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn url_schemes_are_disjoint() {
        assert!(is_codex_url("codex"));
        assert!(is_codex_url("codex:"));
        assert!(is_codex_url("codex:/home/x/.codex"));
        // Mode-B scheme must NOT match the Mode-A predicate.
        assert!(!is_codex_url("codex-responses:"));
        assert!(!is_codex_url("codex-responses:/home/x/.codex"));
        assert!(is_codex_responses_url("codex-responses:"));
        assert!(!is_codex_responses_url("codex:"));
    }

    #[test]
    fn home_parsers_default_for_clean_scheme_and_heal_mangled_urls() {
        // Clean scheme → default home (None), so resolution falls back to ~/.codex.
        assert_eq!(codex_home("codex:"), None);
        assert_eq!(codex_home("codex"), None);
        assert_eq!(codex_responses_home("codex-responses:"), None);
        // A real shadow-home tail is still honored.
        assert_eq!(codex_home("codex:/tmp/alt"), Some("/tmp/alt".to_string()));
        assert_eq!(codex_responses_home("codex-responses:/tmp/alt"), Some("/tmp/alt".to_string()));
        // Mangled urls an older `/api/models` produced (`{scheme}/chat/completions`)
        // must heal to the default home, NOT parse `/chat/completions` as the home.
        assert_eq!(codex_home("codex:/chat/completions"), None);
        assert_eq!(codex_responses_home("codex-responses:/chat/completions"), None);
    }

    #[test]
    fn approval_payloads_are_accepts_not_denies() {
        let p = Value::Null;
        assert_eq!(
            approval_accept_payload("item/commandExecution/requestApproval", &p),
            json!({"decision": "acceptForSession"})
        );
        assert_eq!(
            approval_accept_payload("item/fileChange/requestApproval", &p),
            json!({"decision": "acceptForSession"})
        );
        assert_eq!(
            approval_accept_payload("item/tool/requestUserInput", &p),
            json!({"answers": {}})
        );
        assert_eq!(
            approval_accept_payload("item/tool/call", &p),
            json!({"contentItems": [], "success": false})
        );
        assert_eq!(
            approval_accept_payload("execCommandApproval", &p),
            json!({"decision": "approved_for_session"})
        );
        // Permissive fallback — never a deny.
        assert_eq!(
            approval_accept_payload("some/unknown/method", &p),
            json!({"decision": "acceptForSession"})
        );
    }

    #[test]
    fn permissions_request_echoes_profile_with_session_scope() {
        let params = json!({"permissions": {"network": "enabled", "fs": "write"}});
        assert_eq!(
            approval_accept_payload("item/permissions/requestApproval", &params),
            json!({"permissions": {"network": "enabled", "fs": "write"}, "scope": "session"})
        );
    }

    #[test]
    fn command_execution_started_emits_shell_tool_start() {
        let item = json!({"type": "commandExecution", "id": "i1", "command": "ls -la"});
        let (ev, id, entry) = item_started_event(Some(&item)).expect("tool item");
        assert_eq!(id.as_deref(), Some("i1"));
        assert_eq!(entry.unwrap(), ("shell".to_string(), "ls -la".to_string()));
        let ev = ev.expect("tool_start");
        assert!(ev.contains("\"type\":\"tool_start\""));
        assert!(ev.contains("\"tool\":\"shell\""));
        assert!(ev.contains("ls -la"));
    }

    #[test]
    fn file_change_started_joins_paths() {
        let item = json!({
            "type": "fileChange", "id": "i2",
            "changes": [{"path": "a.rs", "diff": "@@"}, {"path": "b.rs", "diff": "@@"}],
        });
        let (ev, _id, entry) = item_started_event(Some(&item)).expect("tool item");
        assert_eq!(entry.unwrap().0, "apply_patch");
        let ev = ev.expect("tool_start");
        assert!(ev.contains("\"tool\":\"apply_patch\""));
        assert!(ev.contains("a.rs, b.rs"));
    }

    #[test]
    fn agent_message_started_is_not_a_tool() {
        let item = json!({"type": "agentMessage", "id": "i3", "text": ""});
        assert!(item_started_event(Some(&item)).is_none());
    }

    #[test]
    fn command_completed_emits_terminal_tool_output_only() {
        let item = json!({
            "type": "commandExecution", "id": "i1", "command": "echo hi",
            "aggregatedOutput": "hi\n", "exitCode": 0, "status": "completed",
        });
        let evs = item_completed_events(Some(&item));
        // Exactly one terminal tool_output (no agent_step — it would open an
        // empty "Generating response" bubble; see item_completed_events docs).
        assert_eq!(evs.len(), 1);
        assert!(evs[0].contains("\"type\":\"tool_output\""));
        assert!(evs[0].contains("\"exit_code\":0"));
        assert!(evs[0].contains("hi"));
        assert!(!evs[0].contains("agent_step"));
    }

    #[test]
    fn mcp_completed_prefers_result_then_error() {
        let with_result = json!({
            "type": "mcpToolCall", "id": "m1", "server": "fs", "tool": "read",
            "result": {"content": [{"text": "ok"}]}, "status": "completed",
        });
        let evs = item_completed_events(Some(&with_result));
        assert!(evs[0].contains("\"tool\":\"fs.read\""));
        assert!(evs[0].contains("ok"));

        let with_error = json!({
            "type": "mcpToolCall", "id": "m2", "server": "fs", "tool": "read",
            "error": "boom", "status": "failed",
        });
        let evs = item_completed_events(Some(&with_error));
        assert!(evs[0].contains("boom"));
    }

    #[test]
    fn item_tool_name_correlates_by_item_id() {
        let mut tools = HashMap::new();
        tools.insert("i1".to_string(), ("shell".to_string(), "ls".to_string()));
        let params = json!({"itemId": "i1", "delta": "out"});
        assert_eq!(item_tool_name(&tools, &params, "shell"), "shell");
        // Unknown id falls back to default.
        let params2 = json!({"itemId": "zzz"});
        assert_eq!(item_tool_name(&tools, &params2, "apply_patch"), "apply_patch");
    }
}
