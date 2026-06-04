// src/tool_implementations/mod.rs  <- src/tool_implementations.py
//! Extracted tool implementation functions (`do_*` and helpers).
//!
//! This is the `tool_implementations` submodule **root**. The Python source is a
//! single flat module (`src/tool_implementations.py`); the Rust port splits the
//! `do_*` groups across sibling files (`documents.rs`, `search_chats.rs`,
//! `management_db.rs`, `management_service.rs`, `cookbook.rs`, `vault.rs`) and
//! re-exports each public `do_*` here so callers use the flat path
//! `crate::src::tool_implementations::do_xxx`, matching the flat Python module.
//!
//! Shared group ported here (per the project Design contract `tool_groups`):
//! `_parse_tool_args`, the `err_result` helper, `_COOKBOOK_BASE`,
//! `_internal_headers`, `_APP_API_BLOCKLIST_PREFIXES`,
//! `_APP_API_BLOCKLIST_METHOD_PATH`, `_MODEL_PROCESS_PATTERNS`, and `do_app_api`.
//!
//! All six group files are now landed and wired below; the `pub mod`
//! declarations + flat re-exports give `tool_execution` (and the
//! `agent_tools` facade) the full `crate::src::tool_implementations::do_*`
//! surface, so the dispatcher resolves every tool. The crate has no cargo
//! feature flags, so every group file (including the pure parsing foundation
//! `tool_parsing` / `tool_schemas`) is unconditionally compiled. The
//! doc/active-state helpers
//! (`set_active_document`/`set_active_model`/`get_active_document`,
//! `parse_edit_blocks`, `parse_suggest_blocks`, `_sniff_doc_language`,
//! `_looks_like_email_document`, `_coerce_email_document_content`) live in the
//! `documents` group and are re-exported here flat to match the Python module.
//!
//! TOOL RESULT TYPE: every `do_*` returns the Python dict as
//! `serde_json::Map<String, Value>` (NOT a typed struct). `serde_json` has
//! `preserve_order` ON, so a `Map` preserves insertion order = Python dict order
//! (load-bearing for `format_tool_result`'s `data` block and content-string
//! reproduction). Internal Python `try/except` becomes "catch and return an error
//! Map", never `?`. The ONLY `PyResult` in this cluster is `_parse_tool_args`
//! (raises `ValueError` on bad JSON).

use serde_json::{Map, Value};

use crate::error::{PyError, PyResult};

// ---------------------------------------------------------------------------
// Group submodules + flat re-exports
// ---------------------------------------------------------------------------
// `tool_execution` imports the full `do_*` set flat from this root, and the
// `agent_tools` facade re-exports `set_active_document`/`set_active_model`/
// `get_active_document` through here. Every group file is landed and wired
// below, so the `tool_execution` dispatcher resolves every tool. The crate has
// no cargo feature flags, so every group file is unconditionally compiled.

pub mod search_chats;
pub use search_chats::do_search_chats;

pub mod management_service;
pub use management_service::{
    do_api_call, do_manage_contact, do_manage_mcp, do_manage_research, do_manage_settings,
    do_manage_skills, do_resolve_contact, do_trigger_research,
};

pub mod vault;
pub use vault::{do_vault_get, do_vault_search, do_vault_unlock};

pub mod cookbook;
pub use cookbook::{
    do_adopt_served_model, do_cancel_download, do_download_model, do_edit_image,
    do_list_cached_models, do_list_cookbook_servers, do_list_downloads, do_list_served_models,
    do_list_serve_presets, do_serve_model, do_serve_preset, do_search_hf_models,
    do_stop_served_model,
};

pub mod management_db;
pub use management_db::{
    do_manage_calendar, do_manage_endpoints, do_manage_notes, do_manage_tasks, do_manage_tokens,
    do_manage_webhooks,
};

pub mod documents;
pub use documents::{
    do_create_document, do_edit_document, do_manage_documents, do_suggest_document,
    do_update_document, get_active_document, parse_edit_blocks, parse_suggest_blocks,
    set_active_document, set_active_model, _coerce_email_document_content,
    _looks_like_email_document, _sniff_doc_language,
};

// ---------------------------------------------------------------------------
// Argument parsing
// ---------------------------------------------------------------------------

/// `_parse_tool_args(content)` — parse a tool-call argument blob.
///
/// Accepts either a JSON string or an already-decoded dict. Unwraps the
/// common `{"body": {...}}` envelope that smaller models emit when they
/// read tool descriptions like "Body is JSON: {...}" literally — they
/// pass `body` as a field name rather than treating it as a noun.
///
/// Returns a dict on success, raises `ValueError` on bad JSON.
///
/// Python's `content` is `str | dict | <other>`. Every `do_*` call site in this
/// cluster holds the raw tool-block string, so the Rust port takes `&str` (the
/// `str` branch of the Python `isinstance` ladder). For the rare path that
/// already holds a decoded object, [`parse_tool_args_value`] mirrors the `dict`
/// branch. (The `else: args = {}` branch is unreachable from a `&str` caller.)
pub fn _parse_tool_args(content: &str) -> PyResult<Map<String, Value>> {
    // args = json.loads(content) if content.strip() else {}
    let args: Value = if content.trim().is_empty() {
        Value::Object(Map::new())
    } else {
        // except (json.JSONDecodeError, TypeError) as e: raise ValueError(str(e))
        serde_json::from_str(content).map_err(|e| PyError::value(e.to_string()))?
    };
    Ok(unwrap_body_envelope(args))
}

/// The `dict`-branch counterpart of [`_parse_tool_args`]: parse an
/// already-decoded value (mirroring `isinstance(content, dict)` /
/// `else: args = {}`), applying the same `{"body": {...}}` unwrap.
pub fn parse_tool_args_value(content: &Value) -> Map<String, Value> {
    let args = match content {
        Value::Object(_) => content.clone(),
        _ => Value::Object(Map::new()),
    };
    unwrap_body_envelope(args)
}

/// Apply the `{"body": {...}}` envelope unwrap shared by both entrypoints.
///
/// Unwrap only if `body` is the sole key and points at a dict — we don't want
/// to clobber a legitimate `body` field on tools where it's a real arg (e.g.
/// send_email body text). Extra safety: only unwrap if the inner dict looks
/// like a tool call (`"action"` present).
fn unwrap_body_envelope(args: Value) -> Map<String, Value> {
    if let Value::Object(map) = args {
        if map.len() == 1 {
            if let Some(Value::Object(inner)) = map.get("body") {
                if inner.contains_key("action") {
                    return inner.clone();
                }
            }
        }
        return map;
    }
    Map::new()
}

// ---------------------------------------------------------------------------
// Shared error-result helper
// ---------------------------------------------------------------------------

/// Build the `{"error": <msg>, "exit_code": 1}` Map that every `do_*` returns
/// on a caught failure (the Python `return {"error": ..., "exit_code": 1}`
/// pattern). Defined once here and reused across all group files.
pub fn err_result(msg: impl Into<String>) -> Map<String, Value> {
    let mut m = Map::new();
    m.insert("error".to_string(), Value::String(msg.into()));
    m.insert("exit_code".to_string(), Value::from(1));
    m
}

// ---------------------------------------------------------------------------
// Cookbook tools — shared constants
// ---------------------------------------------------------------------------

// Cookbook routes loopback. The agent's tool calls run in-process but
// need to reach admin-gated cookbook routes; we ride the per-process
// internal token so require_admin lets us through. See core/middleware.py.
pub const _COOKBOOK_BASE: &str = "http://localhost:7000";

/// `_internal_headers(owner)` — the loopback auth header pair (+ optional owner
/// impersonation). Mirrors the Python helper that imports
/// `INTERNAL_TOOL_HEADER` / `INTERNAL_TOOL_TOKEN` from `core.middleware`.
///
/// Returned as an ordered `Vec<(String, String)>` so call sites can drop it
/// straight into a `reqwest::header::HeaderMap` (insertion order preserved =
/// Python dict order).
pub fn _internal_headers(owner: Option<&str>) -> Vec<(String, String)> {
    use crate::core::middleware::{INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN};
    let mut headers: Vec<(String, String)> = vec![(
        INTERNAL_TOOL_HEADER.to_string(),
        INTERNAL_TOOL_TOKEN.to_string(),
    )];
    if let Some(o) = owner {
        if !o.is_empty() {
            headers.push(("X-Odysseus-Owner".to_string(), o.to_string()));
        }
    }
    headers
}

// Paths the generic `app_api` tool will refuse to call. Auth/token/user
// administration is too risky to route through an agent surface even
// when the agent is admin-context — accidental "delete account"
// style mistakes have permanent blast radius.
pub const _APP_API_BLOCKLIST_PREFIXES: &[&str] = &[
    "/api/auth",           // login/logout/password
    "/api/users",          // user CRUD (bare /api/users list+create+delete must also block)
    "/api/tokens",         // api token mgmt (bare /api/tokens list+create must also block)
    "/api/admin",          // admin one-shots (wipe etc.)
    "/api/backup/restore", // destructive restore
];

// (method, prefix) pairs to refuse specifically. Used for endpoints
// where GET is fine but writes are destructive — saw the agent wipe
// cookbook_state.json (presets + tasks) by POSTing {"tasks": []} to
// /api/cookbook/state, which overwrote the whole file. Use the
// dedicated preset/task tools instead.
pub const _APP_API_BLOCKLIST_METHOD_PATH: &[(&str, &str)] = &[
    ("GET", "/api/email/accounts"), // owner-filtered in tool context; use list_email_accounts MCP tool
    ("POST", "/api/cookbook/state"), // whole-file overwrite — agent must use serve_preset/serve_model instead
    ("DELETE", "/api/cookbook/state"),
    // Use the named tools (download_model / serve_model) — they handle
    // host-name resolution, per-host env_prefix, AND register the task
    // in cookbook state so it shows in the UI + list_downloads. Hitting
    // the raw endpoint via app_api skips all of that → orphan task.
    ("POST", "/api/model/download"),
    ("POST", "/api/model/serve"),
    // Use trigger_research — it returns a UI hint so the Deep Research
    // sidebar surfaces the session. Raw start works but the agent
    // fumbles the payload + the session doesn't reliably show up.
    ("POST", "/api/research/start"),
    // Use the named tools — they handle owner attribution, natural-
    // language due_date parsing, timezone, dedup, and tag/category
    // normalization. Hitting the raw endpoint via app_api saves a
    // note/event with the wrong fields, no reminder, or the wrong tz.
    ("POST", "/api/notes"),
    ("PUT", "/api/notes"),
    ("DELETE", "/api/notes"),
    ("POST", "/api/calendar/events"),
    ("PUT", "/api/calendar/events"),
    ("DELETE", "/api/calendar/events"),
];

// Patterns for detecting running LLM/diffusion model servers outside
// the cookbook's task tracker. Each entry: (label, substring-list).
// Match is case-insensitive against the FULL cmdline. First-match wins.
//
// (CANONICAL owner per the contract `tool_groups`: the cookbook group's
// `_scan_running_model_processes` reads this table.)
pub const _MODEL_PROCESS_PATTERNS: &[(&str, &[&str])] = &[
    ("vLLM", &["vllm.entrypoints", "vllm serve", "/vllm/", "vllm-openai"]),
    ("SGLang", &["sglang.launch_server", "sglang/launch_server"]),
    ("llama.cpp", &["llama-server", "llama_cpp_server", "llamacppserver"]),
    ("Ollama", &["ollama serve", "ollama runner", "/ollama "]),
    ("ComfyUI", &["comfyui/main.py", "/ComfyUI/main.py", "ComfyUI"]),
    (
        "A1111 WebUI",
        &[
            "stable-diffusion-webui/webui",
            "stable-diffusion-webui/launch",
            "webui.sh",
        ],
    ),
    ("Fooocus", &["Fooocus/entry_with_update", "Fooocus/launch"]),
    ("InvokeAI", &["invokeai-web", "invokeai.app", "invokeai/api_app"]),
    ("Forge WebUI", &["stable-diffusion-webui-forge", "forge/webui"]),
    ("SD.Next", &["automatic/webui", "sd.next"]),
    ("TGI", &["text-generation-launcher", "text_generation_launcher"]),
    ("Aphrodite", &["aphrodite.endpoints", "aphrodite-engine"]),
    ("Triton", &["tritonserver", "triton/main"]),
    (
        "Diffusers",
        &[
            "diffusers.pipelines",
            "StableDiffusionInpaintPipeline",
            "DiffusionPipeline",
        ],
    ),
];

// ---------------------------------------------------------------------------
// do_app_api — generic loopback to any internal Odysseus API endpoint
// ---------------------------------------------------------------------------

/// `do_app_api(content, owner)` — generic loopback to any internal Odysseus API
/// endpoint. Lets the agent reach the full UI-button surface (cookbook, email,
/// notes, calendar, skills, sessions, gallery, research, etc.) without us
/// landing a named tool wrapper for every one.
///
/// Args (JSON):
///   action: "call" (default) | "endpoints"
///   path:   "/api/cookbook/gpus"     # required for call
///   method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE" (default GET)
///   body:   <object>                 # JSON body for POST/PUT/PATCH
///   query:  <object>                 # querystring params
///
/// The `endpoints` action returns the OpenAPI surface (method + path +
/// summary) so the agent can discover what's reachable. A blocklist
/// refuses auth/user/admin paths to keep blast radius bounded.
pub async fn do_app_api(content: &str, owner: Option<&str>) -> Map<String, Value> {
    // try: args = _parse_tool_args(content) if content.strip() else {}
    // except ValueError: return {"error": "Invalid JSON arguments", "exit_code": 1}
    let args: Map<String, Value> = if content.trim().is_empty() {
        Map::new()
    } else {
        match _parse_tool_args(content) {
            Ok(a) => a,
            Err(_) => return err_result("Invalid JSON arguments"),
        }
    };

    // action = (args.get("action") or "call").lower()
    let action = args
        .get("action")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .unwrap_or("call")
        .to_lowercase();
    let base = _COOKBOOK_BASE;

    if action == "endpoints" {
        // Fetch FastAPI's OpenAPI schema so the agent can discover any
        // endpoint without us pre-listing them. Filter by an optional
        // `filter` keyword (substring match on path or summary).
        let kw = args
            .get("filter")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_lowercase();

        let data: Value = match openapi_fetch(base).await {
            Ok(v) => v,
            Err(e) => return err_result(format!("OpenAPI fetch failed: {e}")),
        };

        // rows: List[Dict] = []  — collected as (method, path, summary).
        let mut rows: Vec<(String, String, String)> = Vec::new();
        if let Some(paths) = data.get("paths").and_then(|p| p.as_object()) {
            for (path, methods) in paths {
                let methods = match methods.as_object() {
                    Some(m) => m,
                    None => continue,
                };
                if _APP_API_BLOCKLIST_PREFIXES.iter().any(|p| path.starts_with(p)) {
                    continue;
                }
                for (method, op) in methods {
                    let mlower = method.to_lowercase();
                    if !matches!(
                        mlower.as_str(),
                        "get" | "post" | "put" | "patch" | "delete"
                    ) {
                        continue;
                    }
                    let mupper = method.to_uppercase();
                    if _APP_API_BLOCKLIST_METHOD_PATH
                        .iter()
                        .any(|(m, p)| mupper == *m && path.starts_with(p))
                    {
                        continue;
                    }
                    // summary = (op or {}).get("summary") or (op or {}).get("description") or ""
                    let mut summary = op
                        .get("summary")
                        .and_then(|v| v.as_str())
                        .filter(|s| !s.is_empty())
                        .or_else(|| op.get("description").and_then(|v| v.as_str()))
                        .unwrap_or("")
                        .to_string();
                    // if isinstance(summary, str): summary = summary.strip().split("\n")[0][:140]
                    summary = summary
                        .trim()
                        .split('\n')
                        .next()
                        .unwrap_or("")
                        .chars()
                        .take(140)
                        .collect::<String>();
                    if !kw.is_empty()
                        && !path.to_lowercase().contains(&kw)
                        && !summary.to_lowercase().contains(&kw)
                    {
                        continue;
                    }
                    rows.push((mupper, path.clone(), summary));
                }
            }
        }
        // rows.sort(key=lambda r: (r["path"], r["method"]))
        rows.sort_by(|a, b| a.1.cmp(&b.1).then_with(|| a.0.cmp(&b.0)));

        if rows.is_empty() {
            // f"No endpoints match filter {kw!r}." if kw else "No endpoints found."
            let out = if !kw.is_empty() {
                format!("No endpoints match filter {}.", py_repr(&kw))
            } else {
                "No endpoints found.".to_string()
            };
            let mut m = Map::new();
            m.insert("output".to_string(), Value::String(out));
            m.insert("exit_code".to_string(), Value::from(0));
            return m;
        }

        // lines = [f"{len(rows)} endpoint(s)" + (f" matching {kw!r}" if kw else "") + ":"]
        let header = format!(
            "{} endpoint(s){}:",
            rows.len(),
            if !kw.is_empty() {
                format!(" matching {}", py_repr(&kw))
            } else {
                String::new()
            }
        );
        let mut lines: Vec<String> = vec![header];
        for (method, path, summary) in rows.iter().take(200) {
            // f"  {r['method']:6s} {r['path']}"  — left-justify method to width 6
            let mut line = format!("  {:<6} {}", method, path);
            if !summary.is_empty() {
                line.push_str(&format!("  — {summary}"));
            }
            lines.push(line);
        }
        if rows.len() > 200 {
            lines.push(format!("  ...({} more — filter to narrow)", rows.len() - 200));
        }

        // endpoints rows -> [{"method", "path", "summary"}]
        let endpoints: Vec<Value> = rows
            .iter()
            .map(|(method, path, summary)| {
                let mut r = Map::new();
                r.insert("method".to_string(), Value::String(method.clone()));
                r.insert("path".to_string(), Value::String(path.clone()));
                r.insert("summary".to_string(), Value::String(summary.clone()));
                Value::Object(r)
            })
            .collect();

        let mut m = Map::new();
        m.insert("output".to_string(), Value::String(lines.join("\n")));
        m.insert("endpoints".to_string(), Value::Array(endpoints));
        m.insert("exit_code".to_string(), Value::from(0));
        return m;
    }

    // action == "call"
    // path = args.get("path") or ""
    let mut path = args
        .get("path")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    if path.is_empty() {
        return err_result("path is required (e.g. '/api/cookbook/gpus')");
    }
    if !path.starts_with('/') {
        path = format!("/{path}");
    }
    if _APP_API_BLOCKLIST_PREFIXES.iter().any(|p| path.starts_with(p)) {
        return err_result(format!(
            "Path blocked for safety: {path}. Auth/user/admin endpoints are off-limits via app_api."
        ));
    }

    // method = (args.get("method") or "GET").upper()
    let method = args
        .get("method")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .unwrap_or("GET")
        .to_uppercase();
    if !matches!(
        method.as_str(),
        "GET" | "POST" | "PUT" | "PATCH" | "DELETE"
    ) {
        return err_result(format!("Unsupported method: {method}"));
    }
    if _APP_API_BLOCKLIST_METHOD_PATH
        .iter()
        .any(|(m, p)| method == *m && path.starts_with(p))
    {
        if path.contains("/api/email/accounts") {
            return err_result("Don't use /api/email/accounts via app_api — it is owner-filtered in tool context and may return empty. Use the `list_email_accounts` email tool, then pass `account` to list_emails/read_email.");
        }
        if path.contains("/api/model/download") {
            return err_result("Don't POST /api/model/download directly — use the `download_model` tool (it resolves the server name, sets the venv env_prefix, and registers the task so it shows in the UI).");
        }
        if path.contains("/api/model/serve") {
            return err_result("Don't POST /api/model/serve directly — use the `serve_model` or `serve_preset` tool (handles host resolution, env_prefix, and cookbook tracking).");
        }
        if path.contains("/api/research/start") {
            return err_result("Don't POST /api/research/start directly — use the `trigger_research` tool (it surfaces the session in the Deep Research sidebar).");
        }
        if path.contains("/api/notes") {
            return err_result("Don't hit /api/notes via app_api — use the `manage_notes` tool. It accepts natural-language due_date ('11pm today', 'tomorrow at 9am'), fires reminders from the due_date itself (no separate calendar event), and uses the caller's timezone. The raw endpoint requires ISO-UTC + a separate calendar event, both of which the agent tends to get wrong.");
        }
        if path.contains("/api/calendar/events") {
            return err_result("Don't hit /api/calendar/events via app_api — use the `manage_calendar` tool. It handles tz-aware natural-language datetimes and reminder_minutes correctly. If the user wants a note + reminder, prefer `manage_notes` with due_date — it bundles both.");
        }
        return err_result(format!(
            "{method} {path} is blocked — it overwrites the whole cookbook state file. Use list_serve_presets / serve_preset / serve_model instead."
        ));
    }

    // body = args.get("body"); query = args.get("query") or None
    let body = args.get("body").cloned();
    let query = args
        .get("query")
        .filter(|v| !v.is_null())
        .and_then(|v| v.as_object())
        .cloned();

    // headers = {**_internal_headers(owner=owner), "Content-Type": "application/json"}
    // Pass owner so the backend impersonates the user.
    let mut header_pairs = _internal_headers(owner);
    header_pairs.push(("Content-Type".to_string(), "application/json".to_string()));

    // json=body if body is not None and method in ("POST", "PUT", "PATCH") else None
    let send_body = matches!(&body, Some(b) if !b.is_null())
        && matches!(method.as_str(), "POST" | "PUT" | "PATCH");

    match app_api_request(
        &method,
        &format!("{base}{path}"),
        if send_body { body.as_ref() } else { None },
        query.as_ref(),
        &header_pairs,
    )
    .await
    {
        Ok((status_code, text)) => {
            // Try to parse JSON; fall back to raw text.
            let (payload, preview): (Option<Value>, String) =
                match serde_json::from_str::<Value>(&text) {
                    Ok(p) => {
                        // preview = json.dumps(payload, indent=2, default=str)
                        let mut pv =
                            serde_json::to_string_pretty(&p).unwrap_or_else(|_| text.clone());
                        // if len(preview) > 4000: preview = preview[:4000] + "\n... (truncated)"
                        if pv.chars().count() > 4000 {
                            pv = pv.chars().take(4000).collect::<String>() + "\n... (truncated)";
                        }
                        (Some(p), pv)
                    }
                    Err(_) => (None, text.chars().take(4000).collect::<String>()),
                };

            if status_code >= 400 {
                let mut m = Map::new();
                m.insert(
                    "error".to_string(),
                    Value::String(format!("{method} {path} -> HTTP {status_code}")),
                );
                m.insert("status_code".to_string(), Value::from(status_code));
                m.insert("body".to_string(), Value::String(preview));
                m.insert("exit_code".to_string(), Value::from(1));
                return m;
            }
            let mut m = Map::new();
            m.insert(
                "output".to_string(),
                Value::String(format!("{method} {path} -> {status_code}\n{preview}")),
            );
            m.insert("status_code".to_string(), Value::from(status_code));
            // "json": payload  — payload is `None` (-> JSON null) when the body
            // wasn't JSON, exactly like Python's `payload = None`.
            m.insert("json".to_string(), payload.unwrap_or(Value::Null));
            m.insert("exit_code".to_string(), Value::from(0));
            m
        }
        Err(e) => err_result(format!("{method} {path} failed: {e}")),
    }
}

// ---------------------------------------------------------------------------
// do_app_api HTTP helpers (always compiled)
// ---------------------------------------------------------------------------

/// `httpx.AsyncClient(timeout=15).get(f"{base}/openapi.json", headers=...).json()`.
async fn openapi_fetch(base: &str) -> Result<Value, String> {
    use crate::core::middleware::{INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN};
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(15))
        .build()
        .map_err(|e| e.to_string())?;
    let resp = client
        .get(format!("{base}/openapi.json"))
        .header(INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN.as_str())
        .send()
        .await
        .map_err(|e| e.to_string())?;
    // Python `resp.json()` — parse the body as JSON (raises on bad JSON).
    let text = resp.text().await.map_err(|e| e.to_string())?;
    serde_json::from_str::<Value>(&text).map_err(|e| e.to_string())
}

/// `httpx.AsyncClient(timeout=60).request(method, url, json=body, params=query,
/// headers=headers)` → returns `(status_code, body_text)`.
async fn app_api_request(
    method: &str,
    url: &str,
    body: Option<&Value>,
    query: Option<&Map<String, Value>>,
    header_pairs: &[(String, String)],
) -> Result<(u16, String), String> {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(60))
        .build()
        .map_err(|e| e.to_string())?;

    let m = reqwest::Method::from_bytes(method.as_bytes()).map_err(|e| e.to_string())?;
    let mut req = client.request(m, url);

    let mut hmap = reqwest::header::HeaderMap::new();
    for (k, v) in header_pairs {
        if let (Ok(name), Ok(val)) = (
            reqwest::header::HeaderName::from_bytes(k.as_bytes()),
            reqwest::header::HeaderValue::from_str(v),
        ) {
            hmap.insert(name, val);
        }
    }
    req = req.headers(hmap);

    if let Some(b) = body {
        req = req.json(b);
    }
    // params=query — httpx serializes each k/v as a query string entry.
    if let Some(q) = query {
        let params: Vec<(String, String)> = q
            .iter()
            .map(|(k, v)| {
                let sv = match v {
                    Value::String(s) => s.clone(),
                    Value::Null => String::new(),
                    other => other.to_string(),
                };
                (k.clone(), sv)
            })
            .collect();
        req = req.query(&params);
    }

    let resp = req.send().await.map_err(|e| e.to_string())?;
    let status = resp.status().as_u16();
    let text = resp.text().await.map_err(|e| e.to_string())?;
    Ok((status, text))
}

/// Reproduce CPython `repr(s)` for the simple string case used by `do_app_api`
/// (`f"...{kw!r}..."`). CPython prefers single quotes and only switches to
/// double quotes if the string contains a `'` but no `"`.
fn py_repr(s: &str) -> String {
    let has_single = s.contains('\'');
    let has_double = s.contains('"');
    let (quote, escape_quote): (char, char) = if has_single && !has_double {
        ('"', '"')
    } else {
        ('\'', '\'')
    };
    let mut out = String::new();
    out.push(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == escape_quote => {
                out.push('\\');
                out.push(c);
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}
