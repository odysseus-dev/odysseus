// core/codex_responses.rs  — Codex API (Mode B): ChatGPT Responses backend over HTTPS
//! **Mode B** of the Codex integration (the `codex-responses:` URL scheme).
//!
//! Unlike Mode A (`core::codex`, which drives the `codex app-server` subprocess
//! as a full server-side agent), Mode B treats Codex as a plain **model
//! backend**: there is NO `codex` subprocess. Odysseus POSTs directly to the
//! ChatGPT backend Responses API and drives tools with its OWN agent loop
//! (`crate::src::agent_loop` → `stream_llm_with_fallback` → `stream_llm`, which
//! dispatches here). So a Mode-B session is a full agent using ODYSSEUS's tools
//! (bash/python/read/write/…), exactly like the `pi` harness.
//!
//! This is the Rust port of pi's `openai-codex-responses.ts` +
//! `openai-responses-shared.ts`. v1 scope:
//!   * Auth = read `CODEX_HOME/auth.json` (the tokens `codex login` stored) and
//!     self-refresh on expiry / 401 via `POST auth.openai.com/oauth/token`,
//!     persisting the refreshed tokens back atomically.
//!   * `stream_codex_responses(...)` mirrors `llm_core::stream_llm` arg-for-arg
//!     (so the dispatch is a pass-through): chat/completions `messages` + `tools`
//!     IN, the SAME Odysseus SSE string vocabulary OUT (`data:{"delta":…}` /
//!     `{"delta":…,"thinking":true}` / `{"type":"usage",…}` /
//!     `{"type":"tool_calls",…}` / `event: error` / `data:[DONE]`).
//!   * Plain HTTPS SSE only (`store:false`, full `input` every turn). The pi
//!     WebSocket transport (`previous_response_id` continuation, session ws
//!     cache) is OUT OF SCOPE for v1.
//!
//! DEFERRED (future add, recorded here): a standalone browser/device-code OAuth
//! login flow (AUTHORIZE_URL=`https://auth.openai.com/oauth/authorize`,
//! REDIRECT_URI=`http://localhost:1455/auth/callback`,
//! SCOPE=`openid profile email offline_access`, PKCE S256). v1 ONLY reads +
//! refreshes the tokens that the codex CLI already stored. Also deferred: the
//! friendly 429 usage-limit message + the retry/backoff ladder (v1 keeps retries
//! minimal: just the single 401-refresh retry, surfacing other errors via
//! `event: error`).
//!
//! The whole module is always compiled (declared in `core/mod.rs`); the crate
//! has no cargo feature flags.


use crate::error::{PyError, PyResult};
use base64::Engine as _;
use indexmap::IndexMap;
use serde_json::{json, Value};

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/// The ChatGPT-backend Codex Responses endpoint (pi `resolveCodexUrl`).
const RESPONSES_URL: &str = "https://chatgpt.com/backend-api/codex/responses";
/// OAuth token endpoint for the `refresh_token` grant.
const TOKEN_URL: &str = "https://auth.openai.com/oauth/token";
/// The OAuth client id the codex CLI uses (so a refresh against the tokens it
/// stored is accepted).
const CLIENT_ID: &str = "app_EMoamEEZ73f0CkXaXp7hrann";
/// The JWT claim namespace carrying `chatgpt_account_id` (pi `JWT_CLAIM_PATH`).
const JWT_CLAIM_PATH: &str = "https://api.openai.com/auth";
/// The `originator` header. We reuse the codex-CLI's OAuth tokens, so we send
/// the codex CLI's own originator — the value the ChatGPT backend is guaranteed
/// to accept for these tokens (pi uses `pi`; the backend allowlists both).
const ORIGINATOR: &str = "codex_cli_rs";
/// The `User-Agent` header. The Codex/ChatGPT backend rejects requests with no /
/// unrecognized UA; mirror the codex CLI's UA shape (it isn't validated strictly
/// — pi sends `pi (...)` — but a codex-style UA pairs with our originator).
const USER_AGENT: &str = "codex_cli_rs/0.0.0 (Odysseus)";

// ---------------------------------------------------------------------------
// CODEX_HOME / auth.json path resolution
// ---------------------------------------------------------------------------

/// Expand a leading `~` to `$HOME` (the same rule `core::codex::expand_tilde`
/// uses; re-implemented here as that one is private to its module).
fn expand_tilde(p: &str) -> String {
    if let Some(stripped) = p.strip_prefix('~') {
        return format!("{}{}", crate::pyos::getenv("HOME", ""), stripped);
    }
    p.to_string()
}

/// The default `CODEX_HOME`: `$CODEX_HOME` else `~/.codex`.
fn default_home() -> String {
    if let Some(h) = crate::pyos::getenv_opt("CODEX_HOME") {
        if !h.trim().is_empty() {
            return h;
        }
    }
    expand_tilde("~/.codex")
}

/// Resolve the `CODEX_HOME` for this request from the optional `<home>` tail of
/// the `codex-responses:<home>` URL (parsed upstream by
/// `core::codex::codex_responses_home`); `None` → the default home.
fn resolve_home(home: Option<String>) -> String {
    match home {
        Some(h) if !h.trim().is_empty() => h,
        _ => default_home(),
    }
}

/// `<home>/auth.json`.
fn auth_path(home: &str) -> String {
    crate::pyos::path::join(home, "auth.json")
}

// ---------------------------------------------------------------------------
// auth.json read / JWT / refresh / write-back
// ---------------------------------------------------------------------------

/// The three token fields Mode B needs out of `auth.json`'s `tokens` object.
#[derive(Debug, Clone)]
pub struct CodexTokens {
    pub access_token: String,
    pub refresh_token: String,
    pub account_id: String,
}

/// Read `<home>/auth.json` and pull out the tokens.
///
/// Returns BOTH the full parsed `Value` (so a later refresh can write it back
/// preserving `auth_mode` / `OPENAI_API_KEY` / `tokens.id_token` / field order)
/// and the extracted [`CodexTokens`].
///
/// Shape (verified on this machine): top keys `[auth_mode, OPENAI_API_KEY(null),
/// tokens, last_refresh]`; `tokens` keys `[id_token, access_token,
/// refresh_token, account_id(36-char UUID)]`. There is NO stored expiry — the
/// JWT `exp` claim drives proactive refresh.
pub fn read_auth(home: &str) -> PyResult<(Value, CodexTokens)> {
    let path = auth_path(home);
    let text = std::fs::read_to_string(&path).map_err(|e| {
        PyError::other(format!(
            "Cannot read Codex auth.json at {path}: {e}. Run `codex login`."
        ))
    })?;
    let value: Value = serde_json::from_str(&text)
        .map_err(|e| PyError::other(format!("Malformed Codex auth.json at {path}: {e}")))?;

    let tokens = value.get("tokens");
    let access_token = tokens
        .and_then(|t| t.get("access_token"))
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .map(String::from)
        .ok_or_else(|| {
            PyError::other(format!(
                "Codex auth.json at {path} has no tokens.access_token. Run `codex login`."
            ))
        })?;
    let refresh_token = tokens
        .and_then(|t| t.get("refresh_token"))
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .map(String::from)
        .ok_or_else(|| {
            PyError::other(format!(
                "Codex auth.json at {path} has no tokens.refresh_token. Run `codex login`."
            ))
        })?;
    // account_id: prefer auth.json tokens.account_id; fall back to the JWT claim.
    let account_id = tokens
        .and_then(|t| t.get("account_id"))
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .map(String::from)
        .or_else(|| jwt_account_id(&access_token))
        .ok_or_else(|| {
            PyError::other(format!(
                "Codex auth.json at {path} has no account_id and the access token carries no \
                 chatgpt_account_id claim."
            ))
        })?;

    Ok((
        value,
        CodexTokens {
            access_token,
            refresh_token,
            account_id,
        },
    ))
}

/// Decode the JWT middle (claims) segment of an access token into a `Value`.
///
/// The token is base64url (`-`/`_`, no padding), so decode with `URL_SAFE` after
/// manually padding to a multiple of 4 — `STANDARD` would intermittently fail on
/// `-`/`_`. `None` on any malformed input (treated as "no claim available").
fn jwt_claims(access_token: &str) -> Option<Value> {
    let seg = access_token.split('.').nth(1)?;
    let mut padded = seg.to_string();
    while padded.len() % 4 != 0 {
        padded.push('=');
    }
    let bytes = base64::engine::general_purpose::URL_SAFE.decode(padded).ok()?;
    serde_json::from_slice(&bytes).ok()
}

/// The `chatgpt_account_id` claim from the access-token JWT, if present.
pub fn jwt_account_id(access_token: &str) -> Option<String> {
    jwt_claims(access_token)?
        .get(JWT_CLAIM_PATH)?
        .get("chatgpt_account_id")?
        .as_str()
        .map(String::from)
}

/// The `exp` (Unix seconds) claim from the access-token JWT, if present. Used
/// for proactive refresh.
pub fn jwt_exp(access_token: &str) -> Option<i64> {
    jwt_claims(access_token)?.get("exp")?.as_i64()
}

/// The response of a successful `refresh_token` grant.
#[derive(Debug, Clone)]
pub struct RefreshResp {
    pub access_token: String,
    pub refresh_token: String,
    #[allow(dead_code)]
    pub expires_in: i64,
    #[allow(dead_code)]
    pub id_token: Option<String>,
}

/// `POST auth.openai.com/oauth/token` with `grant_type=refresh_token`.
///
/// The `refresh_token` MAY rotate — the caller always persists the returned one.
pub async fn refresh_tokens(refresh_token: &str) -> PyResult<RefreshResp> {
    let client = reqwest::Client::builder()
        .connect_timeout(std::time::Duration::from_secs(5))
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .map_err(|e| PyError::other(format!("client: {e}")))?;

    let resp = client
        .post(TOKEN_URL)
        .header(
            reqwest::header::CONTENT_TYPE,
            "application/x-www-form-urlencoded",
        )
        .form(&[
            ("grant_type", "refresh_token"),
            ("refresh_token", refresh_token),
            ("client_id", CLIENT_ID),
        ])
        .send()
        .await
        .map_err(|e| PyError::other(format!("OpenAI Codex token refresh request failed: {e}")))?;

    let status = resp.status();
    let body = resp.text().await.unwrap_or_default();
    if !status.is_success() {
        return Err(PyError::other(format!(
            "OpenAI Codex token refresh failed ({}): {}",
            status.as_u16(),
            body.chars().take(300).collect::<String>()
        )));
    }

    let j: Value = serde_json::from_str(&body).map_err(|e| {
        PyError::other(format!("OpenAI Codex token refresh response not JSON: {e}"))
    })?;
    let access_token = j.get("access_token").and_then(|v| v.as_str());
    let new_refresh = j.get("refresh_token").and_then(|v| v.as_str());
    let expires_in = j.get("expires_in").and_then(|v| v.as_i64());
    let (Some(access_token), Some(new_refresh), Some(expires_in)) =
        (access_token, new_refresh, expires_in)
    else {
        return Err(PyError::other(
            "OpenAI Codex token refresh response missing fields",
        ));
    };

    Ok(RefreshResp {
        access_token: access_token.to_string(),
        refresh_token: new_refresh.to_string(),
        expires_in,
        id_token: j
            .get("id_token")
            .and_then(|v| v.as_str())
            .map(String::from),
    })
}

/// A `%Y-%m-%dT%H:%M:%S%.6fZ` UTC timestamp (the `last_refresh` format codex
/// uses — note the `Z` suffix, unlike `pydatetime::utcnow_naive_iso()`).
fn now_z() -> String {
    chrono::Utc::now().format("%Y-%m-%dT%H:%M:%S%.6fZ").to_string()
}

/// Persist refreshed tokens back to `<home>/auth.json` atomically.
///
/// RE-READS the file fresh first (the codex CLI may have rewritten it
/// concurrently), mutates only `tokens.access_token` / `tokens.refresh_token` +
/// `last_refresh`, and leaves `auth_mode` / `OPENAI_API_KEY` / `tokens.id_token`
/// untouched. `serde_json`'s `preserve_order` keeps the original field order
/// through the round-trip; the write goes via `atomic_io::atomic_write_json`
/// (temp + fsync + rename).
pub fn persist_tokens(home: &str, refreshed: &RefreshResp) -> PyResult<()> {
    let path = auth_path(home);
    // Re-read fresh so a concurrent codex-CLI write isn't clobbered.
    let text = std::fs::read_to_string(&path)
        .map_err(|e| PyError::other(format!("Cannot re-read Codex auth.json at {path}: {e}")))?;
    let mut value: Value = serde_json::from_str(&text)
        .map_err(|e| PyError::other(format!("Malformed Codex auth.json at {path}: {e}")))?;

    if !value.is_object() {
        return Err(PyError::other(format!(
            "Codex auth.json at {path} is not a JSON object"
        )));
    }
    // Ensure `tokens` exists as an object so the mutation below lands.
    if !value.get("tokens").map(|t| t.is_object()).unwrap_or(false) {
        value["tokens"] = json!({});
    }
    value["tokens"]["access_token"] = json!(refreshed.access_token);
    value["tokens"]["refresh_token"] = json!(refreshed.refresh_token);
    value["last_refresh"] = json!(now_z());

    crate::core::atomic_io::atomic_write_json(&path, &value, Some(2))
}

// ---------------------------------------------------------------------------
// chat/completions  ->  Responses API conversion
// ---------------------------------------------------------------------------

/// Stringify a chat-message `content` value to a single `String`.
///
/// `content` may be a plain string or an array of parts; for array content the
/// `text`/`input_text`/`output_text` parts are joined with `\n` (a defensive
/// fallback — the chat path is text-only).
fn content_to_string(content: Option<&Value>) -> String {
    match content {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Array(parts)) => parts
            .iter()
            .filter_map(|p| {
                p.get("text")
                    .and_then(|t| t.as_str())
                    .map(String::from)
                    .or_else(|| p.as_str().map(String::from))
            })
            .collect::<Vec<_>>()
            .join("\n"),
        _ => String::new(),
    }
}

/// Convert one chat/completions `user` message's content into Responses
/// `input_text` / `input_image` parts.
fn user_content_parts(content: Option<&Value>) -> Vec<Value> {
    match content {
        Some(Value::String(s)) => vec![json!({"type": "input_text", "text": s})],
        Some(Value::Array(parts)) => parts
            .iter()
            .filter_map(|p| {
                let ptype = p.get("type").and_then(|t| t.as_str());
                match ptype {
                    Some("text") | Some("input_text") => p
                        .get("text")
                        .and_then(|t| t.as_str())
                        .map(|t| json!({"type": "input_text", "text": t})),
                    Some("image_url") => {
                        // chat shape: {"type":"image_url","image_url":{"url":…}}
                        // OR a flattened {"image_url": "data:…"}.
                        let url = p
                            .get("image_url")
                            .and_then(|iu| iu.get("url").and_then(|u| u.as_str()).or(iu.as_str()));
                        url.map(|u| {
                            json!({"type": "input_image", "detail": "auto", "image_url": u})
                        })
                    }
                    _ => None,
                }
            })
            .collect(),
        _ => Vec::new(),
    }
}

/// Convert chat/completions `messages` into the Responses API
/// `(instructions, input)` pair (pi `convertResponsesMessages` with
/// `includeSystemPrompt:false`).
///
/// `system` messages are consolidated (joined with `\n\n`) into the top-level
/// `instructions` and EXCLUDED from `input`; if there are none, `instructions`
/// defaults to `"You are a helpful assistant."`. `user` / `assistant` /
/// `tool` messages map to Responses `input` items.
pub fn convert_input(messages: &[Value]) -> (String, Vec<Value>) {
    let mut sys_parts: Vec<String> = Vec::new();
    let mut input: Vec<Value> = Vec::new();

    for m in messages {
        let role = m.get("role").and_then(|r| r.as_str()).unwrap_or("user");
        match role {
            "system" | "developer" => {
                sys_parts.push(content_to_string(m.get("content")));
            }
            "user" => {
                let parts = user_content_parts(m.get("content"));
                if !parts.is_empty() {
                    input.push(json!({"role": "user", "content": parts}));
                }
            }
            "assistant" => {
                // An assistant message may carry BOTH text content and
                // tool_calls — emit the message item first, then the
                // function_call items (pi ordering).
                let text = m
                    .get("content")
                    .and_then(|c| c.as_str())
                    .unwrap_or("")
                    .to_string();
                if !text.is_empty() {
                    let mut item = json!({
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": text, "annotations": []}],
                    });
                    if let Some(id) = m.get("id").and_then(|i| i.as_str()) {
                        item["id"] = json!(id);
                    }
                    input.push(item);
                }
                if let Some(tcs) = m.get("tool_calls").and_then(|t| t.as_array()) {
                    for tc in tcs {
                        let call_id = tc.get("id").and_then(|i| i.as_str()).unwrap_or("");
                        let func = tc.get("function");
                        let name = func
                            .and_then(|f| f.get("name"))
                            .and_then(|n| n.as_str())
                            .unwrap_or("");
                        // `arguments` is already a JSON string in chat shape;
                        // pass it through verbatim (or "" -> "{}").
                        let args = func
                            .and_then(|f| f.get("arguments"))
                            .and_then(|a| a.as_str())
                            .unwrap_or("");
                        let args = if args.is_empty() { "{}" } else { args };
                        input.push(json!({
                            "type": "function_call",
                            "call_id": call_id,
                            "name": name,
                            "arguments": args,
                        }));
                    }
                }
            }
            "tool" => {
                let call_id = m
                    .get("tool_call_id")
                    .and_then(|i| i.as_str())
                    .unwrap_or("");
                let output = content_to_string(m.get("content"));
                input.push(json!({
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output,
                }));
            }
            _ => {}
        }
    }

    let instructions = if sys_parts.is_empty() {
        "You are a helpful assistant.".to_string()
    } else {
        sys_parts.join("\n\n")
    };
    (instructions, input)
}

/// Convert chat/completions function tool schemas into FLAT Responses tools
/// (pi `convertResponsesTools` with `strict:null`).
///
/// Each input tool is `{"type":"function","function":{name,description,parameters}}`;
/// the nested `function` object is unwrapped and lifted to the top level:
/// `{"type":"function","name","description","parameters","strict":null}`.
/// `strict:null` is sent literally (pi sends `null` for codex; the backend
/// tolerates it). Returns `Some(vec)` only when non-empty.
pub fn convert_tools(tools: Option<&[Value]>) -> Option<Vec<Value>> {
    let tools = tools?;
    if tools.is_empty() {
        return None;
    }
    let converted: Vec<Value> = tools
        .iter()
        .filter_map(|t| {
            let func = t.get("function").or(Some(t))?;
            let name = func.get("name").and_then(|n| n.as_str())?;
            let description = func.get("description").cloned().unwrap_or(Value::Null);
            let parameters = func.get("parameters").cloned().unwrap_or(json!({}));
            Some(json!({
                "type": "function",
                "name": name,
                "description": description,
                "parameters": parameters,
                "strict": Value::Null,
            }))
        })
        .collect();
    if converted.is_empty() {
        None
    } else {
        Some(converted)
    }
}

// ---------------------------------------------------------------------------
// Request building
// ---------------------------------------------------------------------------

/// Build the Responses request headers from the access token + account id
/// (pi `buildSSEHeaders`). Mode B ignores the `IndexMap` headers passed to
/// `stream_codex_responses` and builds its OWN Bearer headers here.
fn build_headers(access_token: &str, account_id: &str) -> PyResult<reqwest::header::HeaderMap> {
    use reqwest::header::{HeaderMap, HeaderName, HeaderValue};
    let mut h = HeaderMap::new();
    let bearer = format!("Bearer {access_token}");
    let pairs: [(&str, &str); 7] = [
        ("authorization", &bearer),
        ("chatgpt-account-id", account_id),
        ("openai-beta", "responses=experimental"),
        ("originator", ORIGINATOR),
        ("user-agent", USER_AGENT),
        ("accept", "text/event-stream"),
        ("content-type", "application/json"),
    ];
    for (k, v) in pairs {
        let name = HeaderName::from_bytes(k.as_bytes())
            .map_err(|e| PyError::other(format!("bad header name {k}: {e}")))?;
        let val = HeaderValue::from_str(v)
            .map_err(|e| PyError::other(format!("bad header value for {k}: {e}")))?;
        h.insert(name, val);
    }
    Ok(h)
}

/// Build the Responses request body (pi `buildRequestBody`).
///
/// `temperature < 0.0` is treated as "unset" and omitted (the chat path passes
/// `1.0`; we forward it). `store:false` and the full `input` are sent every
/// turn (no WebSocket continuation in v1).
fn build_body(
    model: &str,
    instructions: &str,
    input: &[Value],
    tools: &Option<Vec<Value>>,
    temperature: f64,
    prompt_cache_key: Option<&str>,
) -> Value {
    let mut body = json!({
        "model": model,
        "store": false,
        "stream": true,
        "instructions": instructions,
        "input": input,
        "include": ["reasoning.encrypted_content"],
        "tool_choice": "auto",
        "parallel_tool_calls": true,
        "reasoning": {"effort": "medium", "summary": "auto"},
    });
    if temperature >= 0.0 {
        body["temperature"] = json!(temperature);
    }
    if let Some(t) = tools {
        body["tools"] = json!(t);
    }
    if let Some(k) = prompt_cache_key.filter(|k| !k.is_empty()) {
        body["prompt_cache_key"] = json!(k);
    }
    body
}

// ---------------------------------------------------------------------------
// SSE helpers
// ---------------------------------------------------------------------------

/// `event: error\ndata: {...}\n\n` — emitted (NOT a `data:` chunk) so that
/// `stream_llm_with_fallback` can swallow a pre-content failure and retry the
/// next candidate.
fn err_event(msg: &str, status: u16) -> String {
    format!(
        "event: error\ndata: {}\n\n",
        json!({"error": msg, "status": status})
    )
}

/// A single accumulated function call (keyed by `output_index`).
#[derive(Default, Clone)]
struct ToolAcc {
    call_id: String,
    name: String,
    args: String,
    complete: bool,
}

/// Build the terminal `data: {"type":"tool_calls",...}` frame from the
/// accumulators (BTreeMap order by `output_index`, mirroring `stream_llm`'s
/// `_emit_tool_calls`). Only completed calls are emitted.
fn emit_tool_calls(accs: &std::collections::BTreeMap<i64, ToolAcc>) -> Option<String> {
    let calls: Vec<Value> = accs
        .values()
        .filter(|a| a.complete)
        .map(|a| json!({"id": a.call_id, "name": a.name, "arguments": a.args}))
        .collect();
    if calls.is_empty() {
        return None;
    }
    Some(format!(
        "data: {}\n\n",
        json!({"type": "tool_calls", "calls": calls})
    ))
}

// ---------------------------------------------------------------------------
// The Mode-B streaming entrypoint
// ---------------------------------------------------------------------------

/// Stream a Mode-B (Codex Responses API) chat/agent turn, yielding the SAME SSE
/// string vocabulary as `llm_core::stream_llm`.
///
/// The signature mirrors `stream_llm` arg-for-arg so the dispatch in
/// `stream_llm` is a pure pass-through. PARITY-ONLY args (accepted to match
/// `stream_llm`, then ignored — documented like `stream_llm` documents its own):
///   * `max_tokens` — the Responses codex backend has no `max_tokens` field.
///   * `headers` — Mode B builds its OWN Bearer headers from `auth.json`.
///   * `prompt_type` — unused (matches `stream_llm`).
///   * `timeout` — applied to the overall request.
///
/// `url` is the raw `codex-responses:` / `codex-responses:<home>` string; the
/// `<home>` tail selects which `auth.json` to read/refresh.
#[allow(clippy::too_many_arguments)]
pub fn stream_codex_responses(
    url: String,
    model: String,
    messages: Vec<Value>,
    temperature: f64,
    max_tokens: i64,
    headers: IndexMap<String, String>,
    timeout: u64,
    prompt_type: Option<String>,
    tools: Option<Vec<Value>>,
) -> impl futures_util::Stream<Item = String> {
    use futures_util::StreamExt;

    // Parity-only args (consume to silence unused warnings; documented above).
    let _ = max_tokens;
    let _ = headers;
    let _ = prompt_type;

    async_stream::stream! {
        // 1. Resolve CODEX_HOME + read tokens.
        let home = resolve_home(crate::core::codex::codex_responses_home(&url));
        let (_auth_value, mut tokens) = match read_auth(&home) {
            Ok(x) => x,
            Err(e) => { yield err_event(&e.to_string(), 401); return; }
        };

        // 2. Proactive refresh: if the access token expires within 60s, refresh
        //    BEFORE the request.
        if let Some(exp) = jwt_exp(&tokens.access_token) {
            let now = chrono::Utc::now().timestamp();
            if now >= exp - 60 {
                match refresh_tokens(&tokens.refresh_token).await {
                    Ok(refreshed) => {
                        let _ = persist_tokens(&home, &refreshed);
                        tokens.refresh_token = refreshed.refresh_token.clone();
                        tokens.access_token = refreshed.access_token.clone();
                        // account_id may now be re-derivable from the new token,
                        // but the stored one stays valid; keep it.
                    }
                    Err(e) => {
                        // Proactive refresh failure is non-fatal: the (possibly
                        // still-valid) current token is tried; a 401 below
                        // triggers the reactive path.
                        crate::pylog::warning(&format!(
                            "[codex-responses] proactive token refresh failed: {e}"
                        ));
                    }
                }
            }
        }

        // 3. Build the request body once (input + tools are turn-stable).
        let (instructions, input) = convert_input(&messages);
        let conv_tools = convert_tools(tools.as_deref());
        // Codex reasoning models reject a `temperature` field; pi omits it for
        // Codex. The chat/agent callers pass 1.0/0.3, so force the build_body
        // negative-sentinel to drop it (the incoming `temperature` is ignored).
        let _ = temperature;
        // prompt_cache_key = the message stream is per-session; without a session
        // id here we omit it (let the backend default). A future add can thread
        // the session id through for cache reuse.
        let body = build_body(&model, &instructions, &input, &conv_tools, -1.0, None);

        let client = match reqwest::Client::builder()
            .connect_timeout(std::time::Duration::from_secs(5))
            .timeout(std::time::Duration::from_secs(timeout.max(1)))
            .build()
        {
            Ok(c) => c,
            Err(e) => { yield err_event(&format!("client: {e}"), 502); return; }
        };

        // 4. POST, with a single 401-refresh-and-retry.
        let mut attempt = 0u8;
        let resp = loop {
            let hmap = match build_headers(&tokens.access_token, &tokens.account_id) {
                Ok(h) => h,
                Err(e) => { yield err_event(&e.to_string(), 502); return; }
            };
            let sent = client.post(RESPONSES_URL).headers(hmap).json(&body).send().await;
            let r = match sent {
                Ok(r) => r,
                Err(e) => { yield err_event(&format!("Cannot reach Codex backend: {e}"), 503); return; }
            };
            if r.status() == reqwest::StatusCode::UNAUTHORIZED && attempt == 0 {
                // Reactive refresh once.
                attempt += 1;
                match refresh_tokens(&tokens.refresh_token).await {
                    Ok(refreshed) => {
                        let _ = persist_tokens(&home, &refreshed);
                        tokens.refresh_token = refreshed.refresh_token.clone();
                        tokens.access_token = refreshed.access_token.clone();
                        continue;
                    }
                    Err(e) => { yield err_event(&format!("Codex 401 and refresh failed: {e}"), 401); return; }
                }
            }
            break r;
        };

        if resp.status() != reqwest::StatusCode::OK {
            let status = resp.status().as_u16();
            let raw = resp.text().await.unwrap_or_default();
            yield err_event(
                &format!("Codex backend error {status}: {}", raw.chars().take(300).collect::<String>()),
                if status == 0 { 502 } else { status },
            );
            return;
        }

        // 5. Parse the Responses SSE stream and map to Odysseus vocabulary.
        // Tool-call accumulators keyed by output_index.
        let mut accs: std::collections::BTreeMap<i64, ToolAcc> = std::collections::BTreeMap::new();

        let mut byte_stream = resp.bytes_stream();
        let mut buf = String::new();
        // Whether the terminal frames were already emitted (so the tail doesn't
        // double-emit on stream close).
        let mut finished = false;

        'outer: while let Some(chunk) = byte_stream.next().await {
            let chunk = match chunk {
                Ok(c) => c,
                Err(e) => { yield err_event(&format!("stream read: {e}"), 502); return; }
            };
            buf.push_str(&String::from_utf8_lossy(&chunk));
            while let Some(nl) = buf.find('\n') {
                let line: String = buf[..nl].trim_end_matches('\r').to_string();
                buf.drain(..=nl);
                let data = match line.strip_prefix("data:") {
                    Some(d) => d.trim(),
                    None => continue, // skip blank / event: / comment lines
                };
                if data.is_empty() || data == "[DONE]" {
                    continue; // codex backend may not send [DONE]
                }
                let ev: Value = match serde_json::from_str(data) {
                    Ok(v) => v,
                    Err(_) => continue,
                };
                let etype = ev.get("type").and_then(|t| t.as_str()).unwrap_or("");

                match etype {
                    // ---- terminal: error events -> event: error, then stop ----
                    "error" => {
                        let code = ev.get("code").and_then(|c| c.as_str()).unwrap_or("");
                        let message = ev.get("message").and_then(|m| m.as_str()).unwrap_or("");
                        yield err_event(&format!("{code}: {message}"), 502);
                        return;
                    }
                    "response.failed" => {
                        let err = ev.get("response").and_then(|r| r.get("error"));
                        let code = err.and_then(|e| e.get("code")).and_then(|c| c.as_str()).unwrap_or("");
                        let message = err.and_then(|e| e.get("message")).and_then(|m| m.as_str()).unwrap_or("Codex response failed");
                        yield err_event(&format!("{code}: {message}"), 502);
                        return;
                    }

                    // ---- response lifecycle ----
                    "response.created" => { /* capture id; no Odysseus emit */ }

                    "response.output_item.added" => {
                        let item = ev.get("item");
                        let itype = item.and_then(|i| i.get("type")).and_then(|t| t.as_str()).unwrap_or("");
                        if itype == "function_call" {
                            let idx = ev.get("output_index").and_then(|i| i.as_i64()).unwrap_or(0);
                            let acc = accs.entry(idx).or_default();
                            if let Some(it) = item {
                                if let Some(c) = it.get("call_id").and_then(|c| c.as_str()) {
                                    acc.call_id = c.to_string();
                                }
                                if let Some(n) = it.get("name").and_then(|n| n.as_str()) {
                                    acc.name = n.to_string();
                                }
                                if let Some(a) = it.get("arguments").and_then(|a| a.as_str()) {
                                    acc.args = a.to_string();
                                }
                            }
                        }
                        // reasoning / message items begin a block; deltas follow.
                    }

                    // ---- assistant text deltas ----
                    "response.output_text.delta" | "response.refusal.delta" => {
                        if let Some(d) = ev.get("delta").and_then(|d| d.as_str()) {
                            if !d.is_empty() {
                                yield format!("data: {}\n\n", json!({"delta": d}));
                            }
                        }
                    }

                    // ---- reasoning deltas (thinking) ----
                    "response.reasoning_text.delta" | "response.reasoning_summary_text.delta" => {
                        if let Some(d) = ev.get("delta").and_then(|d| d.as_str()) {
                            if !d.is_empty() {
                                yield format!("data: {}\n\n", json!({"delta": d, "thinking": true}));
                            }
                        }
                    }
                    "response.reasoning_summary_part.done" => {
                        // paragraph break between summary parts
                        yield format!("data: {}\n\n", json!({"delta": "\n\n", "thinking": true}));
                    }

                    // ---- function-call argument streaming ----
                    "response.function_call_arguments.delta" => {
                        let idx = ev.get("output_index").and_then(|i| i.as_i64()).unwrap_or(0);
                        if let Some(d) = ev.get("delta").and_then(|d| d.as_str()) {
                            accs.entry(idx).or_default().args.push_str(d);
                        }
                    }
                    "response.function_call_arguments.done" => {
                        let idx = ev.get("output_index").and_then(|i| i.as_i64()).unwrap_or(0);
                        if let Some(a) = ev.get("arguments").and_then(|a| a.as_str()) {
                            // The full final JSON string is authoritative over
                            // the delta buffer.
                            accs.entry(idx).or_default().args = a.to_string();
                        }
                    }
                    "response.output_item.done" => {
                        let item = ev.get("item");
                        let itype = item.and_then(|i| i.get("type")).and_then(|t| t.as_str()).unwrap_or("");
                        if itype == "function_call" {
                            let idx = ev.get("output_index").and_then(|i| i.as_i64()).unwrap_or(0);
                            let acc = accs.entry(idx).or_default();
                            if let Some(it) = item {
                                if let Some(c) = it.get("call_id").and_then(|c| c.as_str()) {
                                    if !c.is_empty() { acc.call_id = c.to_string(); }
                                } else if let Some(id) = it.get("id").and_then(|i| i.as_str()) {
                                    if acc.call_id.is_empty() { acc.call_id = id.to_string(); }
                                }
                                if let Some(n) = it.get("name").and_then(|n| n.as_str()) {
                                    if !n.is_empty() { acc.name = n.to_string(); }
                                }
                                if let Some(a) = it.get("arguments").and_then(|a| a.as_str()) {
                                    if !a.is_empty() { acc.args = a.to_string(); }
                                }
                            }
                            if acc.args.is_empty() { acc.args = "{}".to_string(); }
                            acc.complete = true;
                        }
                    }

                    // ---- terminal: completed -> usage, tool_calls, [DONE] ----
                    "response.completed" | "response.done" | "response.incomplete" => {
                        if let Some(usage) = ev.get("response").and_then(|r| r.get("usage")) {
                            let input_tokens = usage.get("input_tokens").and_then(|x| x.as_i64()).unwrap_or(0);
                            let cached = usage
                                .get("input_tokens_details")
                                .and_then(|d| d.get("cached_tokens"))
                                .and_then(|x| x.as_i64())
                                .unwrap_or(0);
                            let output_tokens = usage.get("output_tokens").and_then(|x| x.as_i64()).unwrap_or(0);
                            yield format!(
                                "data: {}\n\n",
                                json!({"type": "usage", "data": {
                                    "input_tokens": (input_tokens - cached).max(0),
                                    "output_tokens": output_tokens,
                                }})
                            );
                        }
                        if let Some(ev_tc) = emit_tool_calls(&accs) {
                            yield ev_tc;
                        }
                        yield "data: [DONE]\n\n".to_string();
                        finished = true;
                        break 'outer;
                    }

                    _ => {}
                }
            }
        }

        // 6. End-of-stream without an explicit terminal event: emit tool_calls
        //    (if any) then [DONE] (mirrors stream_llm's tail behavior).
        if !finished {
            if let Some(ev_tc) = emit_tool_calls(&accs) {
                yield ev_tc;
            }
            yield "data: [DONE]\n\n".to_string();
        }
    }
}

// ---------------------------------------------------------------------------
// Tests — pure conversion fns only (no network).
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn convert_input_consolidates_system_to_instructions() {
        let messages = vec![
            json!({"role": "system", "content": "Be terse."}),
            json!({"role": "system", "content": "Be kind."}),
            json!({"role": "user", "content": "Hi"}),
        ];
        let (instr, input) = convert_input(&messages);
        assert_eq!(instr, "Be terse.\n\nBe kind.");
        // System excluded from input; only the user message remains.
        assert_eq!(input.len(), 1);
        assert_eq!(input[0]["role"], "user");
        assert_eq!(input[0]["content"][0]["type"], "input_text");
        assert_eq!(input[0]["content"][0]["text"], "Hi");
    }

    #[test]
    fn convert_input_default_instructions_when_no_system() {
        let messages = vec![json!({"role": "user", "content": "Hi"})];
        let (instr, _) = convert_input(&messages);
        assert_eq!(instr, "You are a helpful assistant.");
    }

    #[test]
    fn convert_input_assistant_tool_calls_then_tool_result() {
        let messages = vec![
            json!({"role": "user", "content": "run ls"}),
            json!({
                "role": "assistant",
                "content": "ok",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{\"cmd\":\"ls\"}"}
                }]
            }),
            json!({"role": "tool", "tool_call_id": "call_1", "content": "a.txt"}),
        ];
        let (_instr, input) = convert_input(&messages);
        // user, assistant message, function_call, function_call_output
        assert_eq!(input.len(), 4);
        assert_eq!(input[1]["type"], "message");
        assert_eq!(input[1]["role"], "assistant");
        assert_eq!(input[1]["content"][0]["type"], "output_text");
        assert_eq!(input[2]["type"], "function_call");
        assert_eq!(input[2]["call_id"], "call_1");
        assert_eq!(input[2]["name"], "bash");
        assert_eq!(input[2]["arguments"], "{\"cmd\":\"ls\"}");
        assert_eq!(input[3]["type"], "function_call_output");
        assert_eq!(input[3]["call_id"], "call_1");
        assert_eq!(input[3]["output"], "a.txt");
    }

    #[test]
    fn convert_input_assistant_empty_args_defaults_to_object() {
        let messages = vec![json!({
            "role": "assistant",
            "tool_calls": [{
                "id": "c2",
                "type": "function",
                "function": {"name": "noop", "arguments": ""}
            }]
        })];
        let (_instr, input) = convert_input(&messages);
        assert_eq!(input.len(), 1);
        assert_eq!(input[0]["arguments"], "{}");
    }

    #[test]
    fn convert_tools_unwraps_function_and_sets_strict_null() {
        let tools = vec![json!({
            "type": "function",
            "function": {
                "name": "bash",
                "description": "Run a shell command",
                "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}}
            }
        })];
        let out = convert_tools(Some(&tools)).expect("non-empty");
        assert_eq!(out.len(), 1);
        assert_eq!(out[0]["type"], "function");
        assert_eq!(out[0]["name"], "bash");
        assert_eq!(out[0]["description"], "Run a shell command");
        assert_eq!(out[0]["parameters"]["type"], "object");
        assert!(out[0]["strict"].is_null());
        // Nested `function` key is gone (flattened).
        assert!(out[0].get("function").is_none());
    }

    #[test]
    fn convert_tools_none_and_empty() {
        assert!(convert_tools(None).is_none());
        assert!(convert_tools(Some(&[])).is_none());
    }

    #[test]
    fn build_body_omits_temperature_when_negative_and_includes_when_set() {
        let (instr, input) = convert_input(&[json!({"role": "user", "content": "hi"})]);
        let body_no_temp = build_body("gpt-5-codex", &instr, &input, &None, -1.0, None);
        assert!(body_no_temp.get("temperature").is_none());
        assert_eq!(body_no_temp["store"], false);
        assert_eq!(body_no_temp["stream"], true);
        assert_eq!(body_no_temp["instructions"], "You are a helpful assistant.");
        assert_eq!(body_no_temp["include"][0], "reasoning.encrypted_content");

        let body_temp = build_body("gpt-5-codex", &instr, &input, &None, 0.7, None);
        assert_eq!(body_temp["temperature"], 0.7);
    }

    #[test]
    fn emit_tool_calls_orders_by_index_and_skips_incomplete() {
        let mut accs: std::collections::BTreeMap<i64, ToolAcc> = std::collections::BTreeMap::new();
        accs.insert(1, ToolAcc { call_id: "b".into(), name: "two".into(), args: "{}".into(), complete: true });
        accs.insert(0, ToolAcc { call_id: "a".into(), name: "one".into(), args: "{}".into(), complete: true });
        accs.insert(2, ToolAcc { call_id: "c".into(), name: "three".into(), args: "{}".into(), complete: false });
        let frame = emit_tool_calls(&accs).expect("some");
        let payload = frame.strip_prefix("data: ").unwrap().trim();
        let v: Value = serde_json::from_str(payload).unwrap();
        let calls = v["calls"].as_array().unwrap();
        assert_eq!(calls.len(), 2); // incomplete skipped
        assert_eq!(calls[0]["id"], "a"); // index 0 first
        assert_eq!(calls[1]["id"], "b");
    }

    #[test]
    fn emit_tool_calls_none_when_empty() {
        let accs: std::collections::BTreeMap<i64, ToolAcc> = std::collections::BTreeMap::new();
        assert!(emit_tool_calls(&accs).is_none());
    }

    #[test]
    fn jwt_decode_uses_url_safe_alphabet() {
        // claims = {"exp":111,"https://api.openai.com/auth":{"chatgpt_account_id":"acct-1"}}
        let claims = json!({"exp": 111, JWT_CLAIM_PATH: {"chatgpt_account_id": "acct-1"}});
        let mid = base64::engine::general_purpose::URL_SAFE_NO_PAD
            .encode(serde_json::to_vec(&claims).unwrap());
        let token = format!("header.{mid}.sig");
        assert_eq!(jwt_account_id(&token).as_deref(), Some("acct-1"));
        assert_eq!(jwt_exp(&token), Some(111));
    }

    #[test]
    fn jwt_decode_malformed_is_none() {
        assert!(jwt_account_id("not-a-jwt").is_none());
        assert!(jwt_exp("a.b").is_none()); // middle segment not valid base64 json
    }

    #[test]
    fn user_multimodal_parts_map_text_and_image() {
        let content = json!([
            {"type": "text", "text": "look"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
        ]);
        let parts = user_content_parts(Some(&content));
        assert_eq!(parts.len(), 2);
        assert_eq!(parts[0]["type"], "input_text");
        assert_eq!(parts[0]["text"], "look");
        assert_eq!(parts[1]["type"], "input_image");
        assert_eq!(parts[1]["detail"], "auto");
        assert_eq!(parts[1]["image_url"], "data:image/png;base64,AAAA");
    }
}
