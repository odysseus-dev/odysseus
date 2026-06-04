// mcp_servers/harness.rs  — the hand-rolled MCP stdio SERVER harness
//! The Rust analogue of `mcp.server.Server` + `mcp.server.stdio.stdio_server`
//! (no single Python file owns this — it is the server-side counterpart that the
//! Python servers get "for free" from the `mcp` SDK's `server.run(...)` loop).
//!
//! There is no `mcp` Rust crate dependency in this port, so — exactly as
//! [`crate::src::mcp_manager`] hand-rolls the CLIENT side and `core/codex.rs`
//! hand-rolls a stdio JSON-RPC client — this module hand-rolls the SERVER side of
//! the very same wire protocol the manager speaks.
//!
//! ## Wire protocol (must match `mcp_manager::StdioSession` byte-for-byte)
//!
//! Framing is **newline-delimited JSON-RPC 2.0**, NOT `Content-Length`:
//!   * `StdioSession::write_msg` writes `msg.to_string()` + a single `'\n'`, and
//!     `next_msg` reads exactly one line (`read_line`) then
//!     `serde_json::from_str(line.trim())`. So the client sends one request per
//!     line and expects one compact JSON response per line, terminated by `'\n'`.
//!
//! The handshake the client drives (see `mcp_manager::_connect_stdio`):
//!   1. request `initialize` with params
//!      `{protocolVersion:"2024-11-05", capabilities:{}, clientInfo:{name,version}}`
//!      — the client only awaits a non-error `result` and ignores its contents.
//!   2. fire-and-forget notification `notifications/initialized` (no `id`) — the
//!      server must NOT reply.
//!   3. request `tools/list` -> server returns `{"tools":[{name,description,inputSchema}, ...]}`.
//!   4. request `tools/call` with params `{name, arguments}` -> server returns
//!      `{"content":[{type:"text",text:...}]}` (the Python servers return a bare
//!      `list[TextContent]` with no `isError`, so we faithfully omit `isError`;
//!      the client treats an absent `isError` as `false`).
//!
//! IDs: the client uses monotonic integer ids starting at 1; the server echoes
//! the request `id` verbatim and puts the payload under `result` (or surfaces a
//! failure under `error.message`, which the client reads). Notifications (no
//! `id`) produce no output. An unknown method (with an id) yields a JSON-RPC
//! method-not-found error (`code: -32601`).

use serde_json::{json, Value};
use std::future::Future;
use std::pin::Pin;
use tokio::io::{AsyncBufRead, AsyncBufReadExt, AsyncWrite, AsyncWriteExt, BufReader};

/// The MCP protocol version this server advertises (matches the
/// `protocolVersion` the client sends in `initialize`).
const PROTOCOL_VERSION: &str = "2024-11-05";

/// The server version reported in `serverInfo` (mirrors the `version` the
/// Python SDK reports via `create_initialization_options`).
const SERVER_VERSION: &str = "1.0.0";

/// A tool definition — the `mcp.types.Tool` shape, serialized into the
/// `tools/list` response as `{name, description, inputSchema}`.
///
/// The Python servers build these as `Tool(name=..., description=...,
/// inputSchema={...})`; here the schema is carried as a [`serde_json::Value`] so
/// each server module can declare it as a literal object.
#[derive(Clone)]
pub struct ToolDef {
    /// The tool name (the `tools/call` `name` argument matches this).
    pub name: String,
    /// Human-readable description shown to the agent.
    pub description: String,
    /// The JSON Schema for the tool's arguments (serialized as `inputSchema`).
    pub input_schema: Value,
}

impl ToolDef {
    /// Convenience constructor mirroring `Tool(name=, description=, inputSchema=)`.
    pub fn new(name: &str, description: &str, input_schema: Value) -> Self {
        ToolDef {
            name: name.to_string(),
            description: description.to_string(),
            input_schema,
        }
    }

    /// Render this tool as the JSON object a `tools/list` entry requires.
    fn to_json(&self) -> Value {
        json!({
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        })
    }
}

/// Build a single `TextContent` block (`{type:"text", text:...}`) — the Rust
/// analogue of `TextContent(type="text", text=...)`. Server handlers return a
/// `Vec<Value>` of these (the `list[TextContent]` analogue).
pub fn text_content(text: impl Into<String>) -> Value {
    json!({"type": "text", "text": text.into()})
}

/// The handler type: the `@server.call_tool()` analogue.
///
/// Given a tool `name` and its `arguments` (a JSON object), it returns the list
/// of content blocks (`TextContent`-shaped `Value`s). It is boxed-and-pinned so
/// each server can supply an `async move` closure capturing its own managers,
/// mirroring the single decorated `call_tool` coroutine per Python server.
pub type CallToolFuture = Pin<Box<dyn Future<Output = Vec<Value>> + Send>>;
pub type CallToolHandler = Box<dyn Fn(String, Value) -> CallToolFuture + Send + Sync>;

/// Run the MCP stdio server loop over the provided reader/writer.
///
/// This is the generic core — `reader` yields the client's newline-delimited
/// JSON-RPC requests and `writer` receives the newline-delimited responses. The
/// production entry point [`serve_stdio`] wires `tokio::io::stdin()` /
/// `tokio::io::stdout()`; tests drive it over a `tokio::io::duplex` pipe.
///
/// The loop is the inverse of `mcp_manager::StdioSession`: read one line, parse
/// JSON, dispatch on `method`, and (for requests) write one compact JSON line +
/// `'\n'` + flush. A notification (no `id`) produces no output. EOF or a blank
/// line ends the loop, the analogue of the Python `stdio_server` context exiting
/// when the read stream closes.
pub async fn serve<R, W>(
    server_name: &str,
    tools: Vec<ToolDef>,
    handler: CallToolHandler,
    reader: R,
    mut writer: W,
) -> std::io::Result<()>
where
    R: AsyncBufRead + Unpin,
    W: AsyncWrite + Unpin,
{
    let mut reader = reader;
    let mut line = String::new();
    loop {
        line.clear();
        let n = reader.read_line(&mut line).await?;
        if n == 0 {
            // EOF — the client closed stdin. The Python `async with
            // stdio_server()` block exits the same way.
            break;
        }
        let trimmed = line.trim();
        if trimmed.is_empty() {
            // A bare blank line carries no request; ignore it (matches the
            // client's `from_str(line.trim())` which would yield no usable msg).
            continue;
        }
        let msg: Value = match serde_json::from_str(trimmed) {
            Ok(v) => v,
            Err(_) => {
                // Unparseable line — there is no id to echo, so (like a
                // malformed notification) emit nothing and keep reading.
                continue;
            }
        };

        let method = msg.get("method").and_then(|m| m.as_str()).unwrap_or("");
        // Per JSON-RPC, a message WITHOUT an `id` is a notification: never reply.
        let id = msg.get("id").cloned();
        let is_notification = id.is_none();
        let params = msg.get("params").cloned().unwrap_or_else(|| json!({}));

        let response: Option<Value> = match method {
            "initialize" => {
                // The client ignores the result contents; it only needs a
                // non-error `result`. We still report the standard shape.
                id.map(|id| {
                    json!({
                        "jsonrpc": "2.0",
                        "id": id,
                        "result": {
                            "protocolVersion": PROTOCOL_VERSION,
                            "serverInfo": {"name": server_name, "version": SERVER_VERSION},
                            "capabilities": {"tools": {}},
                        }
                    })
                })
            }
            "notifications/initialized" => {
                // A notification: do nothing, send nothing.
                None
            }
            "tools/list" => id.map(|id| {
                let listed: Vec<Value> = tools.iter().map(ToolDef::to_json).collect();
                json!({
                    "jsonrpc": "2.0",
                    "id": id,
                    "result": {"tools": listed},
                })
            }),
            "tools/call" => {
                let tool_name = params
                    .get("name")
                    .and_then(|n| n.as_str())
                    .unwrap_or("")
                    .to_string();
                // Python servers read `arguments` as a dict; default to `{}`.
                let arguments = params.get("arguments").cloned().unwrap_or_else(|| json!({}));
                let content = handler(tool_name, arguments).await;
                // Faithfully omit `isError` (the Python servers return a bare
                // `list[TextContent]`); the client treats absent as `false`.
                id.map(|id| {
                    json!({
                        "jsonrpc": "2.0",
                        "id": id,
                        "result": {"content": content},
                    })
                })
            }
            _ => {
                // Unknown method. With an id, return JSON-RPC method-not-found;
                // without one (a stray notification) stay silent.
                id.map(|id| {
                    json!({
                        "jsonrpc": "2.0",
                        "id": id,
                        "error": {"code": -32601, "message": "Method not found"},
                    })
                })
            }
        };

        // A notification must never produce output, even for `tools/call`-shaped
        // payloads that lack an id (the handler may have run for side effects,
        // matching the SDK, but the wire stays silent).
        if is_notification {
            continue;
        }
        if let Some(resp) = response {
            write_line(&mut writer, &resp).await?;
        }
    }
    Ok(())
}

/// Write one newline-delimited compact JSON-RPC message and flush.
///
/// The exact inverse of `mcp_manager::StdioSession::write_msg`: compact JSON
/// (no pretty-printing) followed by a single `'\n'`, then flush so the client's
/// `read_line` unblocks immediately.
async fn write_line<W>(writer: &mut W, msg: &Value) -> std::io::Result<()>
where
    W: AsyncWrite + Unpin,
{
    let mut out = msg.to_string();
    out.push('\n');
    writer.write_all(out.as_bytes()).await?;
    writer.flush().await
}

/// Production entry point: run [`serve`] over `tokio::io::stdin()` /
/// `tokio::io::stdout()`.
///
/// This is the `async with stdio_server() as (read_stream, write_stream):
/// await server.run(...)` analogue — stdout is the JSON-RPC channel, so the
/// server must keep it clean (any logging goes to stderr elsewhere).
pub async fn serve_stdio(
    server_name: &str,
    tools: Vec<ToolDef>,
    handler: CallToolHandler,
) -> std::io::Result<()> {
    let reader = BufReader::new(tokio::io::stdin());
    let writer = tokio::io::stdout();
    serve(server_name, tools, handler, reader, writer).await
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt, BufReader};

    /// A minimal one-tool server for the round-trip tests: an `echo` tool that
    /// returns its `text` argument back as a single TextContent block.
    fn echo_tools() -> Vec<ToolDef> {
        vec![ToolDef::new(
            "echo",
            "Echo back the text",
            json!({
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            }),
        )]
    }

    fn echo_handler() -> CallToolHandler {
        Box::new(|name: String, args: Value| {
            Box::pin(async move {
                if name != "echo" {
                    return vec![text_content(format!("Unknown tool: {name}"))];
                }
                let text = args.get("text").and_then(|t| t.as_str()).unwrap_or("");
                vec![text_content(format!("echo: {text}"))]
            })
        })
    }

    /// Drive a full handshake over an in-memory duplex pipe and collect the
    /// server's response lines. `requests` are sent one-per-line (the client's
    /// framing); the server is fed EOF after the last request.
    async fn drive(requests: &[Value]) -> Vec<Value> {
        // (server_read, client_write) and (client_read, server_write).
        let (client_to_server, server_in) = tokio::io::duplex(64 * 1024);
        let (server_out, client_from_server) = tokio::io::duplex(64 * 1024);

        let server = tokio::spawn(async move {
            serve(
                "test_server",
                echo_tools(),
                echo_handler(),
                BufReader::new(server_in),
                server_out,
            )
            .await
            .unwrap();
        });

        // Write all client requests then drop the writer to signal EOF.
        let mut client_w = client_to_server;
        for req in requests {
            let mut line = req.to_string();
            line.push('\n');
            client_w.write_all(line.as_bytes()).await.unwrap();
        }
        client_w.flush().await.unwrap();
        drop(client_w); // EOF -> server loop ends.

        // Read everything the server wrote and split into JSON lines.
        let mut buf = Vec::new();
        let mut reader = client_from_server;
        reader.read_to_end(&mut buf).await.unwrap();
        server.await.unwrap();

        String::from_utf8(buf)
            .unwrap()
            .lines()
            .filter(|l| !l.trim().is_empty())
            .map(|l| serde_json::from_str::<Value>(l).expect("server wrote valid JSON line"))
            .collect()
    }

    #[tokio::test]
    async fn initialize_returns_non_error_result_with_serverinfo() {
        let resps = drive(&[json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "odysseus", "version": "1.0.0"}}
        })])
        .await;
        assert_eq!(resps.len(), 1);
        let r = &resps[0];
        assert_eq!(r["id"], json!(1));
        assert!(r.get("error").is_none());
        assert_eq!(r["result"]["protocolVersion"], json!("2024-11-05"));
        assert_eq!(r["result"]["serverInfo"]["name"], json!("test_server"));
        assert_eq!(r["result"]["serverInfo"]["version"], json!("1.0.0"));
        // capabilities.tools present (the standard advertise shape).
        assert!(r["result"]["capabilities"]["tools"].is_object());
    }

    #[tokio::test]
    async fn initialized_notification_produces_no_output() {
        // A notification (no id) must yield no response line at all.
        let resps = drive(&[json!({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {}
        })])
        .await;
        assert!(resps.is_empty());
    }

    #[tokio::test]
    async fn tools_list_returns_the_tool_defs() {
        let resps = drive(&[json!({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})]).await;
        assert_eq!(resps.len(), 1);
        let tools = resps[0]["result"]["tools"].as_array().unwrap();
        assert_eq!(tools.len(), 1);
        assert_eq!(tools[0]["name"], json!("echo"));
        assert_eq!(tools[0]["description"], json!("Echo back the text"));
        // The schema is forwarded verbatim under `inputSchema`.
        assert_eq!(tools[0]["inputSchema"]["type"], json!("object"));
        assert_eq!(tools[0]["inputSchema"]["properties"]["text"]["type"], json!("string"));
    }

    #[tokio::test]
    async fn tools_call_dispatches_to_handler_and_returns_content() {
        let resps = drive(&[json!({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"text": "hi"}}
        })])
        .await;
        assert_eq!(resps.len(), 1);
        let content = resps[0]["result"]["content"].as_array().unwrap();
        assert_eq!(content.len(), 1);
        assert_eq!(content[0]["type"], json!("text"));
        assert_eq!(content[0]["text"], json!("echo: hi"));
        // isError is faithfully omitted.
        assert!(resps[0]["result"].get("isError").is_none());
    }

    #[tokio::test]
    async fn unknown_method_with_id_returns_method_not_found() {
        let resps = drive(&[json!({"jsonrpc": "2.0", "id": 9, "method": "no/such/method", "params": {}})]).await;
        assert_eq!(resps.len(), 1);
        assert_eq!(resps[0]["id"], json!(9));
        assert_eq!(resps[0]["error"]["code"], json!(-32601));
        assert_eq!(resps[0]["error"]["message"], json!("Method not found"));
        assert!(resps[0].get("result").is_none());
    }

    #[tokio::test]
    async fn full_handshake_sequence_echoes_ids_verbatim() {
        // initialize -> initialized (no reply) -> tools/list -> tools/call.
        let resps = drive(&[
            json!({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json!({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}),
            json!({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
            json!({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "echo", "arguments": {"text": "x"}}}),
        ])
        .await;
        // Exactly three responses (the notification is silent).
        assert_eq!(resps.len(), 3);
        assert_eq!(resps[0]["id"], json!(1));
        assert_eq!(resps[1]["id"], json!(2));
        assert_eq!(resps[2]["id"], json!(3));
        // Responses arrive in request order (single-threaded line loop).
        assert!(resps[0]["result"].get("serverInfo").is_some());
        assert!(resps[1]["result"].get("tools").is_some());
        assert_eq!(resps[2]["result"]["content"][0]["text"], json!("echo: x"));
    }

    #[tokio::test]
    async fn unknown_method_notification_is_silent() {
        // No id on an unknown method -> no error line either.
        let resps = drive(&[json!({"jsonrpc": "2.0", "method": "no/such", "params": {}})]).await;
        assert!(resps.is_empty());
    }

    #[tokio::test]
    async fn tools_call_missing_arguments_defaults_to_empty_object() {
        // No `arguments` -> handler still runs with `{}`; echo yields empty text.
        let resps = drive(&[json!({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "echo"}
        })])
        .await;
        assert_eq!(resps.len(), 1);
        assert_eq!(resps[0]["result"]["content"][0]["text"], json!("echo: "));
    }
}
