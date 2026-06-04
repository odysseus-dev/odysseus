// src/tool_implementations/cookbook.rs  <- src/tool_implementations.py (Cookbook + Gallery + app_api tools)
//! Port of the "cookbook" tool group from `src/tool_implementations.py`.
//!
//! These tools are all HTTP loopbacks to the local Odysseus app
//! (`http://localhost:7000`) plus a filesystem `/proc` scan. They ride the
//! per-process internal token (`X-Odysseus-Internal-Token`) so the admin-gated
//! cookbook routes let the agent through — see `core/middleware.rs`. Every
//! entry point here does async HTTP (or, for the two sync helpers, is only
//! reachable from an async one); the whole module is always compiled, since the
//! crate has no cargo feature flags.
//!
//! Tool-result convention (shared_decisions): every `do_*` returns the Python
//! dict as a `serde_json::Map<String, Value>` (insertion order preserved via
//! serde_json's `preserve_order` feature). Internal Python `try/except`
//! becomes "catch and return an error Map", never a `?`-propagated `PyError`.
//! The single exception is `super::_parse_tool_args`, which mirrors Python's
//! `_parse_tool_args` raising `ValueError` on bad JSON.
//!
//! DRIFT NOTE: `httpx.AsyncClient(timeout=N)` is reproduced with a fresh
//! `reqwest::Client` per call carrying a total-request `timeout(N)`. Any
//! transport/parse failure maps to the same `{"error": str(e), "exit_code": 1}`
//! shape (or the function's own message) Python produces in its `except`.

use serde_json::{json, Map, Value};

use crate::pylog as logger;

// `_COOKBOOK_BASE`, `_internal_headers`, the `_APP_API_BLOCKLIST_*` tables,
// `_MODEL_PROCESS_PATTERNS`, and `do_app_api` are the CANONICAL "shared"
// cookbook items owned by `tool_implementations/mod.rs` (per the contract
// `tool_groups.shared`). This group file consumes them via `super::` rather
// than redefining them.
use super::{_internal_headers, _parse_tool_args, err_result, _COOKBOOK_BASE, _MODEL_PROCESS_PATTERNS};

// ── Small JSON / Value helpers (Python idiom translations) ──

/// `d.get(key) or ""` for a string-ish field → owned `String` ("" when
/// missing / null / non-string / empty).
fn str_or_empty(v: &Value, key: &str) -> String {
    match v.get(key) {
        Some(Value::String(s)) => s.clone(),
        _ => String::new(),
    }
}

/// `str(d.get(key) or "")` — coerce numbers/strings to a display string, "" for
/// null/missing. Used where Python does `s.get("port") or ""` and later string
/// formats it.
fn str_coerce_or_empty(v: &Value, key: &str) -> String {
    match v.get(key) {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Null) | None => String::new(),
        Some(other) => other.to_string(),
    }
}

/// Truthiness of a Value mirroring Python's `or` / `if x:` on a dict value:
/// false for None/missing, false for empty string, false for `false`, false for
/// `0`/`0.0`, false for empty array/object; true otherwise.
fn truthy(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Number(n)) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

/// `resp.json()` parse guarded by the Python content-type check
/// `r.headers.get("content-type","").startswith("application/json")`. Returns
/// `{}` (empty object) when the header doesn't start with `application/json`,
/// mirroring the Python `... else {}` branch.
async fn json_if_ct(resp: reqwest::Response) -> Value {
    let is_json = resp
        .headers()
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .map(|s| s.starts_with("application/json"))
        .unwrap_or(false);
    if !is_json {
        return Value::Object(Map::new());
    }
    resp.json::<Value>().await.unwrap_or(Value::Object(Map::new()))
}

/// `resp.json()` without the content-type guard (Python `r.json()` directly).
/// Returns `{}` on parse failure (the callers that use this all `or {}` the
/// result or treat parse failure as an empty dict).
async fn json_loose(resp: reqwest::Response) -> Value {
    resp.json::<Value>().await.unwrap_or(Value::Object(Map::new()))
}

/// Build a `reqwest::Client` with a total-request timeout (seconds), mirroring
/// `httpx.AsyncClient(timeout=N)`.
fn client(timeout_secs: u64) -> Result<reqwest::Client, reqwest::Error> {
    reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(timeout_secs))
        .build()
}

/// Apply an ordered slice of `(name, value)` headers onto a `RequestBuilder`
/// (matches the `Vec<(String, String)>` returned by `super::_internal_headers`).
fn with_headers(mut rb: reqwest::RequestBuilder, headers: &[(String, String)]) -> reqwest::RequestBuilder {
    for (k, v) in headers {
        rb = rb.header(k.as_str(), v.as_str());
    }
    rb
}

// ── _cookbook_servers ──

/// Return the cookbook's configured servers + the currently-selected default
/// host. Shape: `{default_host, hosts: [{name, host, platform, env, envPath,
/// port}]}`.
pub async fn _cookbook_servers() -> Map<String, Value> {
    let empty = || {
        let mut m = Map::new();
        m.insert("default_host".to_string(), Value::String(String::new()));
        m.insert("hosts".to_string(), Value::Array(Vec::new()));
        m
    };

    let state: Value = match client(10) {
        Ok(c) => {
            let url = format!("{_COOKBOOK_BASE}/api/cookbook/state");
            match with_headers(c.get(&url), &_internal_headers(None)).send().await {
                Ok(resp) => json_if_ct(resp).await,
                Err(_) => return empty(),
            }
        }
        Err(_) => return empty(),
    };

    let env = state.get("env");
    let env = match env {
        Some(v) if v.is_object() => v,
        _ => return empty(),
    };

    let mut hosts: Vec<Value> = Vec::new();
    if let Some(Value::Array(servers)) = env.get("servers") {
        for s in servers {
            if s.is_object() {
                let mut h = Map::new();
                h.insert("name".to_string(), Value::String(str_or_empty(s, "name")));
                h.insert("host".to_string(), Value::String(str_or_empty(s, "host"))); // "" = Local
                h.insert("platform".to_string(), Value::String(str_or_empty(s, "platform")));
                h.insert("env".to_string(), Value::String(str_or_empty(s, "env")));
                h.insert("envPath".to_string(), Value::String(str_or_empty(s, "envPath")));
                h.insert("port".to_string(), Value::String(str_coerce_or_empty(s, "port")));
                hosts.push(Value::Object(h));
            }
        }
    }

    let default_host = str_or_empty(env, "remoteHost");
    let mut out = Map::new();
    out.insert("default_host".to_string(), Value::String(default_host));
    out.insert("hosts".to_string(), Value::Array(hosts));
    out
}

// ── _resolve_cookbook_host ──

/// Map a friendly server NAME to its ssh host string. If the input already
/// looks like an ssh host (matches a known host), or matches nothing, it's
/// returned unchanged. `local`/`localhost` → `""` (this machine).
pub async fn _resolve_cookbook_host(name_or_host: &str) -> String {
    if name_or_host.is_empty() {
        return String::new();
    }
    let val = name_or_host.trim().to_string();
    let low = val.to_lowercase();
    if matches!(low.as_str(), "local" | "localhost" | "this machine" | "here") {
        return String::new();
    }
    let servers = _cookbook_servers().await;
    let hosts: &[Value] = match servers.get("hosts") {
        Some(Value::Array(a)) => a.as_slice(),
        _ => &[],
    };
    // Exact host match → already an ssh host
    for h in hosts {
        let host = str_or_empty(h, "host");
        if !host.is_empty() && host == val {
            return val;
        }
    }
    // Name match (case-insensitive)
    for h in hosts {
        if str_or_empty(h, "name").to_lowercase() == low {
            return str_or_empty(h, "host"); // "" for the Local entry
        }
    }
    // Substring name match as a fallback
    for h in hosts {
        let name_low = str_or_empty(h, "name").to_lowercase();
        if !low.is_empty() && name_low.contains(&low) {
            return str_or_empty(h, "host");
        }
    }
    // No match — assume a raw host/alias; return as-is.
    val
}

// ── _cookbook_env_for_host ──

/// Resolve env_prefix / gpus / platform / hf_token / ssh_port for a host by
/// looking it up in `cookbook_state.env`. Returns a dict ready to drop into the
/// `/api/model/serve` payload, falling back to the top-level env settings.
pub async fn _cookbook_env_for_host(host: &str) -> Map<String, Value> {
    let headers = _internal_headers(None);
    let state: Value = match client(10) {
        Ok(c) => {
            let url = format!("{_COOKBOOK_BASE}/api/cookbook/state");
            match with_headers(c.get(&url), &headers).send().await {
                Ok(resp) => json_if_ct(resp).await,
                Err(e) => {
                    logger::debug(&format!("cookbook env lookup failed for host={host:?}: {e}"));
                    return Map::new();
                }
            }
        }
        Err(e) => {
            logger::debug(&format!("cookbook env lookup failed for host={host:?}: {e}"));
            return Map::new();
        }
    };
    if !state.is_object() {
        return Map::new();
    }
    let env_root = match state.get("env") {
        Some(v) if v.is_object() => v.clone(),
        _ => return Map::new(),
    };

    // Per-host entry takes precedence over top-level.
    let mut per_host: Value = Value::Object(Map::new());
    if let Some(Value::Array(servers)) = env_root.get("servers") {
        for s in servers {
            if s.is_object() && str_or_empty(s, "host") == host {
                per_host = s.clone();
                break;
            }
        }
    }

    // env_kind = per_host.get("env") or env_root.get("env") or "none"
    let pick = |key: &str, default: &str| -> String {
        let ph = str_or_empty(&per_host, key);
        if !ph.is_empty() {
            return ph;
        }
        let er = str_or_empty(&env_root, key);
        if !er.is_empty() {
            return er;
        }
        default.to_string()
    };
    let env_kind = pick("env", "none");
    let env_path = pick("envPath", "");
    let platform = pick("platform", "linux");
    // ssh_port: per_host.get("sshPort") or env_root.get("sshPort") or ""
    let ssh_port = {
        let ph = str_coerce_or_empty(&per_host, "sshPort");
        if !ph.is_empty() {
            ph
        } else {
            str_coerce_or_empty(&env_root, "sshPort")
        }
    };

    let mut env_prefix = String::new();
    if env_kind == "venv" && !env_path.is_empty() {
        if platform == "windows" {
            let activate = if env_path.ends_with("\\Scripts\\Activate.ps1") {
                env_path.clone()
            } else {
                format!("{}\\Scripts\\Activate.ps1", env_path.trim_end_matches('\\'))
            };
            env_prefix = format!("& {activate}");
        } else {
            let activate = if env_path.ends_with("/bin/activate") {
                env_path.clone()
            } else {
                format!("{}/bin/activate", env_path.trim_end_matches('/'))
            };
            env_prefix = format!("source {activate}");
        }
    } else if env_kind == "conda" && !env_path.is_empty() {
        if platform == "windows" {
            env_prefix = format!("conda activate {env_path}");
        } else {
            env_prefix = format!("eval \"$(conda shell.bash hook)\" && conda activate {env_path}");
        }
    }

    let mut out = Map::new();
    out.insert("env_prefix".to_string(), Value::String(env_prefix));
    out.insert("gpus".to_string(), Value::String(str_or_empty(&env_root, "gpus")));
    out.insert("platform".to_string(), Value::String(platform));
    out.insert("hf_token".to_string(), Value::String(str_or_empty(&env_root, "hfToken")));
    out.insert("ssh_port".to_string(), Value::String(ssh_port));
    out
}

// ── _cookbook_register_task ──

/// Append a task entry to `cookbook_state.json` after the agent launches via
/// `/api/model/serve` or `/api/model/download`. Returns `true` on success,
/// `false` if the write failed (best-effort).
pub async fn _cookbook_register_task(
    session_id: &str,
    model: &str,
    host: &str,
    cmd: &str,
    task_type: &str,
) -> bool {
    let headers = _internal_headers(None);
    let state_v: Value = match client(10) {
        Ok(c) => {
            let url = format!("{_COOKBOOK_BASE}/api/cookbook/state");
            match with_headers(c.get(&url), &headers).send().await {
                Ok(resp) => json_if_ct(resp).await,
                Err(e) => {
                    logger::debug(&format!("cookbook state read failed: {e}"));
                    return false;
                }
            }
        }
        Err(e) => {
            logger::debug(&format!("cookbook state read failed: {e}"));
            return false;
        }
    };

    let mut state: Map<String, Value> = match state_v {
        Value::Object(m) => m,
        _ => Map::new(),
    };

    let mut tasks: Vec<Value> = match state.get("tasks") {
        Some(Value::Array(a)) => a.clone(),
        _ => Vec::new(),
    };

    // Skip duplicate (same session_id) entries.
    let dup = tasks.iter().any(|t| {
        t.is_object() && t.get("sessionId").and_then(|v| v.as_str()) == Some(session_id)
    });
    if dup {
        return true;
    }

    let display_name = if model.contains('/') {
        model.rsplit('/').next().unwrap_or(model).to_string()
    } else {
        model.to_string()
    };

    // Placeholder output (CSS hides empty <pre>; give the user something to see).
    let target = if host.is_empty() {
        "local:".to_string()
    } else {
        format!("{host}:")
    };
    let cmd_first = cmd.split_whitespace().next().unwrap_or("");
    // cmd[:200]{'…' if len > 200}
    let cmd_preview = truncate_chars(cmd, 200);
    let placeholder = format!(
        "Launched via agent — waiting for tmux output…\n  session: {session_id}\n  target:  {target}{cmd_first}\n  cmd:     {cmd_preview}"
    );

    let ts = now_ms();
    let task = json!({
        "id": session_id,
        "sessionId": session_id,
        "name": display_name,
        "modelId": model,
        "type": task_type,
        "status": "running",
        "output": placeholder,
        "ts": ts,
        "payload": {"repo_id": model, "remote_host": if host.is_empty() { "" } else { host }, "_cmd": cmd},
        "remoteHost": if host.is_empty() { "" } else { host },
        "sshPort": "",
        "platform": "linux",
        "_serveReady": false,
        "_endpointAdded": false,
    });
    tasks.push(task);
    state.insert("tasks".to_string(), Value::Array(tasks));

    match client(10) {
        Ok(c) => {
            let url = format!("{_COOKBOOK_BASE}/api/cookbook/state");
            match with_headers(c.post(&url), &headers).json(&Value::Object(state)).send().await {
                Ok(resp) => resp.status().as_u16() < 400,
                Err(e) => {
                    logger::debug(&format!("cookbook state write failed: {e}"));
                    false
                }
            }
        }
        Err(e) => {
            logger::debug(&format!("cookbook state write failed: {e}"));
            false
        }
    }
}

/// `cmd[:n] + ("…" if len(cmd) > n else "")` over code points.
fn truncate_chars(s: &str, n: usize) -> String {
    if s.chars().count() > n {
        let head: String = s.chars().take(n).collect();
        format!("{head}…")
    } else {
        s.to_string()
    }
}

/// `int(time.time() * 1000)` — milliseconds since the epoch as an i64.
fn now_ms() -> i64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

// NOTE: `_APP_API_BLOCKLIST_PREFIXES`, `_APP_API_BLOCKLIST_METHOD_PATH`, and
// `do_app_api` are the CANONICAL "shared" items defined in
// `tool_implementations/mod.rs` (contract `tool_groups.shared`: "do_app_api
// (lives here or in cookbook.rs)" — it lives in mod.rs). Not redefined here.


/// Reproduce CPython `repr(s)` for the `{x!r}` interpolations in this module's
/// messages. CPython prefers single quotes and only switches to double quotes
/// if the string contains a `'` but no `"`. (Mirrors the private `py_repr` in
/// `tool_implementations/mod.rs`.)
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

/// `{"output": <msg>, "exit_code": 0}`.
fn ok_output(msg: impl Into<String>) -> Map<String, Value> {
    let mut m = Map::new();
    m.insert("output".to_string(), Value::String(msg.into()));
    m.insert("exit_code".to_string(), Value::from(0));
    m
}


// ── _cookbook_apply_retry_suggestion ──

/// Apply a structured Cookbook diagnosis suggestion to a serve command.
pub fn _cookbook_apply_retry_suggestion(cmd: &str, suggestion: &Map<String, Value>) -> String {
    if cmd.is_empty() || suggestion.is_empty() {
        return cmd.to_string();
    }
    let suggestion_v = Value::Object(suggestion.clone());
    let op = str_or_empty(&suggestion_v, "op");
    match op.as_str() {
        "append" => {
            let arg = str_or_empty(&suggestion_v, "arg");
            let arg = arg.trim();
            if arg.is_empty() || cmd.contains(arg) {
                return cmd.to_string();
            }
            format!("{} {arg}", cmd.trim_end())
        }
        "remove" => {
            let flag = str_or_empty(&suggestion_v, "flag");
            let flag = flag.trim();
            if flag.is_empty() {
                return cmd.to_string();
            }
            // re.sub(rf"\s*{re.escape(flag)}(?:\s+\S+)?", "", cmd).strip()
            let pat = format!(r"\s*{}(?:\s+\S+)?", regex::escape(flag));
            match regex::Regex::new(&pat) {
                Ok(re) => re.replace_all(cmd, "").trim().to_string(),
                Err(_) => cmd.to_string(),
            }
        }
        "replace" => {
            let flag = str_or_empty(&suggestion_v, "flag");
            let flag = flag.trim().to_string();
            // value = str(suggestion.get("value") or "").strip()
            let value = str_coerce_or_empty(&suggestion_v, "value").trim().to_string();
            if flag.is_empty() || value.is_empty() {
                return cmd.to_string();
            }
            let repl = format!("{flag} {value}");
            let escaped = regex::escape(&flag);
            // if re.search(rf"(^|\s){flag}(\s+\S+)?", cmd):
            let search_pat = format!(r"(^|\s){escaped}(\s+\S+)?");
            let has = regex::Regex::new(&search_pat).map(|re| re.is_match(cmd)).unwrap_or(false);
            if has {
                // re.sub(rf"(^|\s){flag}(?:\s+\S+)?", lambda m: (m.group(1) or " ") + repl, cmd).strip()
                let sub_pat = format!(r"(^|\s){escaped}(?:\s+\S+)?");
                match regex::Regex::new(&sub_pat) {
                    Ok(re) => re
                        .replace_all(cmd, |caps: &regex::Captures| {
                            let g1 = caps.get(1).map(|m| m.as_str()).unwrap_or("");
                            let lead = if g1.is_empty() { " " } else { g1 };
                            format!("{lead}{repl}")
                        })
                        .trim()
                        .to_string(),
                    Err(_) => cmd.to_string(),
                }
            } else {
                format!("{} {repl}", cmd.trim_end())
            }
        }
        _ => cmd.to_string(),
    }
}

// ── _scan_running_model_processes ──

/// Scan `/proc` for running model server processes. Linux-only; returns `[]` on
/// other platforms or if `/proc` isn't accessible. Each match returns a dict
/// shaped like a cookbook task so the caller can merge cleanly.
pub fn _scan_running_model_processes() -> Vec<Value> {
    let proc = std::path::Path::new("/proc");
    if !proc.is_dir() {
        return Vec::new();
    }
    let mut out: Vec<Value> = Vec::new();
    let mut seen_keys: std::collections::HashSet<(String, String)> = std::collections::HashSet::new();

    let entries = match std::fs::read_dir("/proc") {
        Ok(e) => e,
        Err(e) => {
            logger::debug(&format!("_scan_running_model_processes failed: {e}"));
            return out;
        }
    };

    for entry in entries.flatten() {
        let pid_dir = entry.file_name().to_string_lossy().to_string();
        if !pid_dir.chars().all(|c| c.is_ascii_digit()) || pid_dir.is_empty() {
            continue;
        }
        let raw = match std::fs::read(format!("/proc/{pid_dir}/cmdline")) {
            Ok(b) => b,
            Err(_) => continue, // OSError / PermissionError
        };
        if raw.is_empty() {
            continue;
        }
        // cmdline is NUL-separated; join with spaces.
        let replaced: Vec<u8> = raw.iter().map(|&b| if b == 0 { b' ' } else { b }).collect();
        let cmdline = String::from_utf8_lossy(&replaced).trim().to_string();
        if cmdline.is_empty() {
            continue;
        }
        let lower = cmdline.to_lowercase();
        for (label, needles) in _MODEL_PROCESS_PATTERNS {
            if needles.iter().any(|n| lower.contains(&n.to_lowercase())) {
                // Dedupe by (label, first-arg).
                let first_arg = cmdline.split(' ').next().unwrap_or("").to_string();
                let key = (label.to_string(), first_arg);
                if seen_keys.contains(&key) {
                    break;
                }
                seen_keys.insert(key);
                // Try to pluck a model name out of the cmdline.
                let mut model = String::new();
                for tok in cmdline.split_whitespace() {
                    let tl = tok.to_lowercase();
                    if tok.contains('/')
                        && ["model", "checkpoint", ".safetensors", ".gguf", ".bin", "huggingface"]
                            .iter()
                            .any(|s| tl.contains(s))
                    {
                        model = tok.to_string();
                        break;
                    }
                }
                let pid_int: i64 = pid_dir.parse().unwrap_or(0);
                let cmdline_preview = truncate_chars(&cmdline, 140);
                out.push(json!({
                    "session_id": format!("pid-{pid_dir}"),
                    "model": if model.is_empty() { (*label).to_string() } else { model },
                    "phase": "running (external)",
                    "type": "serve",
                    "remote": "local",
                    "pid": pid_int,
                    "label": label,
                    "cmdline_preview": cmdline_preview,
                    "external": true,
                }));
                break;
            }
        }
    }
    out
}

// ── do_download_model ──

/// Download a HuggingFace model via the cookbook API.
pub async fn do_download_model(content: &str, _owner: Option<&str>) -> Map<String, Value> {
    let args = match _parse_tool_args(content) {
        Ok(m) => m,
        Err(_) => return err_result("Invalid JSON arguments"),
    };
    let args_v = Value::Object(args.clone());
    let repo_id = str_or_empty(&args_v, "repo_id");
    if repo_id.is_empty() {
        return err_result("repo_id is required");
    }
    let mut host = str_or_empty(&args_v, "host").trim().to_string();
    if !host.is_empty() {
        host = _resolve_cookbook_host(&host).await;
    }
    // No host + not local → default to the cookbook's selected server.
    let mut host_defaulted = false;
    if host.is_empty() && !truthy(args.get("local")) {
        let servers = _cookbook_servers().await;
        let default = str_or_empty(&Value::Object(servers), "default_host");
        if !default.is_empty() {
            host = default;
            host_defaulted = true;
        }
    }

    let mut payload = Map::new();
    payload.insert("repo_id".to_string(), Value::String(repo_id.clone()));
    if !host.is_empty() {
        payload.insert("remote_host".to_string(), Value::String(host.clone()));
    }
    if truthy(args.get("include")) {
        payload.insert("include".to_string(), args.get("include").cloned().unwrap());
    }
    // Per-host env_prefix + hf_token (same as serve).
    let env_cfg = _cookbook_env_for_host(&host).await;
    let env_cfg_v = Value::Object(env_cfg);
    for key in ["env_prefix", "hf_token", "platform", "ssh_port"] {
        let v = str_or_empty(&env_cfg_v, key);
        if !v.is_empty() {
            payload.insert(key.to_string(), Value::String(v));
        }
    }

    let cl = match client(30) {
        Ok(c) => c,
        Err(e) => return err_result(e.to_string()),
    };
    let url = format!("{_COOKBOOK_BASE}/api/model/download");
    let resp = match with_headers(cl.post(&url), &_internal_headers(None))
        .json(&Value::Object(payload))
        .send()
        .await
    {
        Ok(r) => r,
        Err(e) => return err_result(e.to_string()),
    };
    let data = json_loose(resp).await;

    if truthy(data.get("ok")) {
        // sid = data.get("session_id", "?")
        let sid = data.get("session_id").and_then(|v| v.as_str()).map(|s| s.to_string()).unwrap_or_else(|| "?".to_string());
        let registered = _cookbook_register_task(&sid, &repo_id, &host, &format!("hf download {repo_id}"), "download").await;
        let note = if registered { "" } else { " (state-write failed — download may not show in UI)" };
        let where_ = if host.is_empty() { "local".to_string() } else { host.clone() };
        let default_note = if host_defaulted {
            " (defaulted to the cookbook's selected server — pass host= or local=true to override)"
        } else {
            ""
        };
        let mut out = Map::new();
        out.insert(
            "output".to_string(),
            Value::String(format!("Download started: {repo_id} on {where_} (session: {sid}){note}{default_note}")),
        );
        out.insert("session_id".to_string(), Value::String(sid));
        out.insert("host".to_string(), Value::String(host));
        out.insert("exit_code".to_string(), Value::from(0));
        return out;
    }
    let err = data.get("error").and_then(|v| v.as_str()).unwrap_or("Download failed").to_string();
    err_result(err)
}

// ── do_serve_model ──

/// Start serving a model via the cookbook API.
pub async fn do_serve_model(content: &str, _owner: Option<&str>) -> Map<String, Value> {
    let args = match _parse_tool_args(content) {
        Ok(m) => m,
        Err(_) => return err_result("Invalid JSON arguments"),
    };
    let args_v = Value::Object(args.clone());
    let repo_id = str_or_empty(&args_v, "repo_id");
    let cmd = str_or_empty(&args_v, "cmd");
    if repo_id.is_empty() || cmd.is_empty() {
        return err_result("repo_id and cmd are required");
    }
    let mut host = str_or_empty(&args_v, "host").trim().to_string();
    if !host.is_empty() {
        host = _resolve_cookbook_host(&host).await;
    }
    if host.is_empty() && !truthy(args.get("local")) {
        let servers = _cookbook_servers().await;
        let default = str_or_empty(&Value::Object(servers), "default_host");
        if !default.is_empty() {
            host = default;
        }
    }
    let mut payload = Map::new();
    payload.insert("repo_id".to_string(), Value::String(repo_id.clone()));
    payload.insert("cmd".to_string(), Value::String(cmd.clone()));
    if !host.is_empty() {
        payload.insert("remote_host".to_string(), Value::String(host.clone()));
    }
    let env_cfg = _cookbook_env_for_host(&host).await;
    let env_cfg_v = Value::Object(env_cfg);
    for key in ["env_prefix", "gpus", "hf_token", "platform", "ssh_port"] {
        let v = str_or_empty(&env_cfg_v, key);
        if !v.is_empty() {
            payload.insert(key.to_string(), Value::String(v));
        }
    }

    let cl = match client(30) {
        Ok(c) => c,
        Err(e) => return err_result(e.to_string()),
    };
    let url = format!("{_COOKBOOK_BASE}/api/model/serve");
    let resp = match with_headers(cl.post(&url), &_internal_headers(None))
        .json(&Value::Object(payload))
        .send()
        .await
    {
        Ok(r) => r,
        Err(e) => return err_result(e.to_string()),
    };
    let data = json_loose(resp).await;

    if truthy(data.get("ok")) {
        let sid = data.get("session_id").and_then(|v| v.as_str()).map(|s| s.to_string()).unwrap_or_else(|| "?".to_string());
        let registered = _cookbook_register_task(&sid, &repo_id, &host, &cmd, "serve").await;
        let note = if registered { "" } else { " (state-write failed — task may not show in UI)" };
        let mut out = Map::new();
        out.insert("output".to_string(), Value::String(format!("Serving {repo_id} (session: {sid}){note}")));
        out.insert("session_id".to_string(), Value::String(sid));
        out.insert("exit_code".to_string(), Value::from(0));
        return out;
    }
    let err = data.get("error").and_then(|v| v.as_str()).unwrap_or("Serve failed").to_string();
    err_result(err)
}

// ── do_list_served_models ──

/// List running model servers — merges cookbook-tracked tasks with a `/proc`
/// scan for externally-launched LLM/diffusion processes.
pub async fn do_list_served_models(_content: &str, _owner: Option<&str>) -> Map<String, Value> {
    // Cookbook-tracked tasks (best-effort).
    let mut cookbook_tasks: Vec<Value> = Vec::new();
    if let Ok(cl) = client(15) {
        let url = format!("{_COOKBOOK_BASE}/api/cookbook/tasks/status");
        match with_headers(cl.get(&url), &_internal_headers(None)).send().await {
            Ok(resp) => {
                let data = json_loose(resp).await;
                if let Some(Value::Array(t)) = data.get("tasks") {
                    cookbook_tasks = t.clone();
                }
            }
            Err(e) => logger::debug(&format!("cookbook tasks/status fetch failed: {e}")),
        }
    }

    // Local process scan — runs in a worker thread (asyncio.to_thread).
    let external = tokio::task::spawn_blocking(_scan_running_model_processes)
        .await
        .unwrap_or_default();

    let mut merged: Vec<Value> = Vec::new();
    merged.extend(cookbook_tasks.iter().cloned());
    // Dedupe by PID against cookbook tasks.
    let mut cookbook_pids: std::collections::HashSet<i64> = std::collections::HashSet::new();
    for t in &cookbook_tasks {
        if t.is_object() {
            if let Some(pid) = t.get("pid").and_then(|v| v.as_i64()) {
                cookbook_pids.insert(pid);
            }
        }
    }
    for p in &external {
        let pid = p.get("pid").and_then(|v| v.as_i64());
        if pid.map(|n| !cookbook_pids.contains(&n)).unwrap_or(true) {
            merged.push(p.clone());
        }
    }

    if merged.is_empty() {
        return ok_output(
            "No model servers currently running (cookbook task tracker empty; /proc scan found no vLLM / sglang / llama.cpp / Ollama / ComfyUI / A1111 / Fooocus / InvokeAI / TGI / Aphrodite / Triton / Diffusers processes).",
        );
    }

    let cb_n = cookbook_tasks.len();
    let ext_n = external.len();
    let mut header: Vec<String> = Vec::new();
    if cb_n > 0 {
        header.push(format!("{cb_n} cookbook-tracked"));
    }
    if ext_n > 0 {
        header.push(format!("{ext_n} external"));
    }
    let mut lines: Vec<String> = vec![format!("Running: {}.", header.join(", "))];

    for t in &merged {
        // phase = t.get("phase") or t.get("status", "unknown")
        let phase = {
            let p = str_or_empty(t, "phase");
            if !p.is_empty() {
                p
            } else {
                t.get("status").and_then(|v| v.as_str()).unwrap_or("unknown").to_string()
            }
        };
        let model = t.get("model").and_then(|v| v.as_str()).unwrap_or("?").to_string();
        let remote = t.get("remote").and_then(|v| v.as_str()).unwrap_or("local").to_string();
        let sid = t.get("session_id").and_then(|v| v.as_str()).unwrap_or("?").to_string();
        let tag = if truthy(t.get("external")) { " [external]" } else { "" };
        lines.push(format!("- {model}: {phase} ({remote}, session: {sid}){tag}"));

        // diagnosis
        if let Some(diag) = t.get("diagnosis").filter(|d| d.is_object()) {
            let msg = diag.get("message").and_then(|v| v.as_str()).map(|s| s.to_string());
            // f"    diagnosis: {diag.get('message')}" — Python str() of None is "None"
            let msg_disp = match diag.get("message") {
                Some(Value::String(s)) => s.clone(),
                Some(Value::Null) | None => "None".to_string(),
                Some(other) => other.to_string(),
            };
            let _ = msg;
            lines.push(format!("    diagnosis: {msg_disp}"));
            let cmd = str_or_empty(t, "cmd");
            let suggestions: Vec<Value> = match diag.get("suggestions") {
                Some(Value::Array(a)) => a.clone(),
                _ => Vec::new(),
            };
            let mut actionable: Vec<String> = Vec::new();
            for s in suggestions.iter().take(3) {
                let label = {
                    let l = str_or_empty(s, "label");
                    if l.is_empty() { "retry".to_string() } else { l }
                };
                let s_map = match s {
                    Value::Object(m) => m.clone(),
                    _ => Map::new(),
                };
                let retry_cmd = _cookbook_apply_retry_suggestion(&cmd, &s_map);
                let op = str_or_empty(s, "op");
                if !retry_cmd.is_empty()
                    && retry_cmd != cmd
                    && matches!(op.as_str(), "append" | "replace" | "remove")
                {
                    actionable.push(format!("{label}: `{retry_cmd}`"));
                } else {
                    actionable.push(label);
                }
            }
            if !actionable.is_empty() {
                lines.push(format!("    suggestions: {}", actionable.join(" | ")));
            }
        }

        // error log tail
        if t.get("status").and_then(|v| v.as_str()) == Some("error") && truthy(t.get("output_tail")) {
            let tail = str_coerce_or_empty(t, "output_tail");
            let tail = tail.trim();
            if !tail.is_empty() {
                lines.push("    recent log:".to_string());
                let all: Vec<&str> = tail.split('\n').collect();
                let start = all.len().saturating_sub(6);
                for line in &all[start..] {
                    let clipped: String = line.chars().take(220).collect();
                    lines.push(format!("      {clipped}"));
                }
            }
        }

        // external cmdline preview
        if truthy(t.get("external")) && truthy(t.get("cmdline_preview")) {
            let preview = str_or_empty(t, "cmdline_preview");
            lines.push(format!("    cmd: {preview}"));
        }
    }

    let mut out = Map::new();
    out.insert("output".to_string(), Value::String(lines.join("\n")));
    out.insert("tasks".to_string(), Value::Array(merged));
    out.insert("exit_code".to_string(), Value::from(0));
    out
}

// ── _cookbook_kill_session ──

/// Kill a cookbook tmux session — remote-aware — AND mark the task stopped in
/// `cookbook_state.json`. Shared by `stop_served_model` and `cancel_download`.
pub async fn _cookbook_kill_session(
    session_id: &str,
    remote_host: &str,
    ssh_port: &str,
    verb: &str,
) -> Map<String, Value> {
    let headers = _internal_headers(None);
    let mut remote = remote_host.to_string();
    let mut sport = ssh_port.to_string();

    // Look up the task's host + confirm it exists in state.
    let state_v: Value = match client(10) {
        Ok(cl) => {
            let url = format!("{_COOKBOOK_BASE}/api/cookbook/state");
            match with_headers(cl.get(&url), &headers).send().await {
                Ok(resp) => json_loose(resp).await,
                Err(e) => {
                    logger::debug(&format!("cookbook state lookup failed for {session_id}: {e}"));
                    Value::Object(Map::new())
                }
            }
        }
        Err(e) => {
            logger::debug(&format!("cookbook state lookup failed for {session_id}: {e}"));
            Value::Object(Map::new())
        }
    };
    let mut state: Map<String, Value> = match state_v {
        Value::Object(m) => m,
        _ => Map::new(),
    };

    let mut matched_idx: Option<usize> = None;
    if let Some(Value::Array(tasks)) = state.get("tasks") {
        for (i, t) in tasks.iter().enumerate() {
            if t.is_object() {
                let sess_match = t.get("sessionId").and_then(|v| v.as_str()) == Some(session_id)
                    || t.get("id").and_then(|v| v.as_str()) == Some(session_id);
                if sess_match {
                    matched_idx = Some(i);
                    if remote.is_empty() {
                        remote = str_or_empty(t, "remoteHost");
                    }
                    if sport.is_empty() {
                        sport = str_coerce_or_empty(t, "sshPort");
                    }
                    break;
                }
            }
        }
    }

    let (cmd, target_label) = if !remote.is_empty() {
        // _pf = f"-p {shlex.quote(str(sport))} " if sport and str(sport) != "22" else ""
        let pf = if !sport.is_empty() && sport != "22" {
            format!("-p {} ", shlex::try_quote(&sport).map(|c| c.into_owned()).unwrap_or_else(|_| sport.clone()))
        } else {
            String::new()
        };
        let q_remote = shlex::try_quote(&remote).map(|c| c.into_owned()).unwrap_or_else(|_| remote.clone());
        let q_sess = shlex::try_quote(session_id).map(|c| c.into_owned()).unwrap_or_else(|_| session_id.to_string());
        let cmd = format!(
            "ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no {pf}{q_remote} 'tmux kill-session -t {q_sess}'"
        );
        (cmd, format!("{session_id} on {remote}"))
    } else {
        let q_sess = shlex::try_quote(session_id).map(|c| c.into_owned()).unwrap_or_else(|_| session_id.to_string());
        (format!("tmux kill-session -t {q_sess}"), session_id.to_string())
    };

    let cl = match client(15) {
        Ok(c) => c,
        Err(e) => return err_result(e.to_string()),
    };
    let exec_url = format!("{_COOKBOOK_BASE}/api/shell/exec");
    let resp = match with_headers(cl.post(&exec_url), &headers)
        .json(&json!({"command": cmd}))
        .send()
        .await
    {
        Ok(r) => r,
        Err(e) => return err_result(e.to_string()),
    };
    let status = resp.status().as_u16();
    if status >= 400 {
        let text = resp.text().await.unwrap_or_default();
        let preview: String = text.chars().take(200).collect();
        return err_result(format!("shell/exec returned HTTP {status}: {preview}"));
    }
    let data = json_loose(resp).await;

    // kill_failed = data.get("exit_code") not in (None, 0)
    let kill_failed = match data.get("exit_code") {
        None | Some(Value::Null) => false,
        Some(v) => v.as_i64() != Some(0),
    };
    // kill_err = (data.get("stderr") or data.get("error") or "").strip()
    let kill_err = {
        let stderr = str_or_empty(&data, "stderr");
        let e = if !stderr.is_empty() { stderr } else { str_or_empty(&data, "error") };
        e.trim().to_string()
    };
    let kill_err_low = kill_err.to_lowercase();
    let already_gone = ["no server running", "can't find session", "session not found"]
        .iter()
        .any(|s| kill_err_low.contains(s));
    if kill_failed && !already_gone {
        let reason = if kill_err.is_empty() {
            "kill-session returned non-zero".to_string()
        } else {
            kill_err.clone()
        };
        return err_result(format!("Failed to {} {target_label}: {reason}", verb.to_lowercase()));
    }

    // Update state: mark stopped.
    if let Some(idx) = matched_idx {
        if let Some(Value::Array(tasks)) = state.get_mut("tasks") {
            if let Some(Value::Object(t)) = tasks.get_mut(idx) {
                t.insert("status".to_string(), Value::String("stopped".to_string()));
            }
        }
        if let Ok(cl2) = client(10) {
            let url = format!("{_COOKBOOK_BASE}/api/cookbook/state");
            if let Err(e) = with_headers(cl2.post(&url), &headers)
                .json(&Value::Object(state.clone()))
                .send()
                .await
            {
                logger::debug(&format!("failed to mark {session_id} stopped in state: {e}"));
            }
        }
    }

    let suffix = if already_gone { " (was already gone)" } else { "" };
    ok_output(format!("{verb} {target_label}{suffix}"))
}

// ── do_stop_served_model ──

/// Stop a running model server by killing its tmux session (remote-aware).
pub async fn do_stop_served_model(content: &str, _owner: Option<&str>) -> Map<String, Value> {
    let args = match _parse_tool_args(content) {
        Ok(m) => m,
        Err(_) => return err_result("Invalid JSON arguments"),
    };
    let args_v = Value::Object(args.clone());
    let session_id = str_or_empty(&args_v, "session_id");
    if session_id.is_empty() {
        return err_result("session_id is required");
    }
    // remote_host = args.get("remote_host") or args.get("host") or ""
    let remote_host = {
        let r = str_or_empty(&args_v, "remote_host");
        if !r.is_empty() { r } else { str_or_empty(&args_v, "host") }
    };
    let ssh_port = str_coerce_or_empty(&args_v, "ssh_port");
    _cookbook_kill_session(&session_id, &remote_host, &ssh_port, "Stopped server").await
}

// ── do_list_downloads ──

/// List in-flight model downloads (filters `/api/cookbook/tasks/status` to
/// `type=download`).
pub async fn do_list_downloads(_content: &str, _owner: Option<&str>) -> Map<String, Value> {
    let cl = match client(15) {
        Ok(c) => c,
        Err(e) => return err_result(e.to_string()),
    };
    let url = format!("{_COOKBOOK_BASE}/api/cookbook/tasks/status");
    let resp = match with_headers(cl.get(&url), &_internal_headers(None)).send().await {
        Ok(r) => r,
        Err(e) => return err_result(e.to_string()),
    };
    let data = json_loose(resp).await;

    let all_tasks: Vec<Value> = match data.get("tasks") {
        Some(Value::Array(a)) => a.clone(),
        _ => Vec::new(),
    };
    let tasks: Vec<Value> = all_tasks
        .into_iter()
        .filter(|t| str_or_empty(t, "type").to_lowercase() == "download")
        .collect();
    if tasks.is_empty() {
        return ok_output("No downloads in progress.");
    }
    let mut lines: Vec<String> = vec![format!("{} download(s) in progress:", tasks.len())];
    for t in &tasks {
        let phase = {
            let p = str_or_empty(t, "phase");
            if !p.is_empty() {
                p
            } else {
                t.get("status").and_then(|v| v.as_str()).unwrap_or("unknown").to_string()
            }
        };
        let model = t.get("model").and_then(|v| v.as_str()).unwrap_or("?").to_string();
        // pct = t.get("progress_percent") or t.get("percent")
        let pct = t.get("progress_percent").filter(|v| truthy(Some(v))).or_else(|| {
            t.get("percent").filter(|v| truthy(Some(v)))
        });
        let pct_str = match pct {
            Some(v) => format!(" {}%", value_display(v)),
            None => String::new(),
        };
        let remote = t.get("remote").and_then(|v| v.as_str()).unwrap_or("local").to_string();
        let sid = t.get("session_id").and_then(|v| v.as_str()).unwrap_or("?").to_string();
        lines.push(format!("- {model}: {phase}{pct_str} ({remote}, session: {sid})"));
    }
    let mut out = Map::new();
    out.insert("output".to_string(), Value::String(lines.join("\n")));
    out.insert("downloads".to_string(), Value::Array(tasks));
    out.insert("exit_code".to_string(), Value::from(0));
    out
}

/// `str(v)` for a scalar display: numbers render their JSON form, strings raw.
fn value_display(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

// ── do_cancel_download ──

/// Cancel a model download by killing its tmux session (remote-aware).
pub async fn do_cancel_download(content: &str, _owner: Option<&str>) -> Map<String, Value> {
    let args = match _parse_tool_args(content) {
        Ok(m) => m,
        Err(_) => return err_result("Invalid JSON arguments"),
    };
    let args_v = Value::Object(args.clone());
    let session_id = str_or_empty(&args_v, "session_id");
    if session_id.is_empty() {
        return err_result("session_id is required (from list_downloads)");
    }
    let remote_host = {
        let r = str_or_empty(&args_v, "remote_host");
        if !r.is_empty() { r } else { str_or_empty(&args_v, "host") }
    };
    let ssh_port = str_coerce_or_empty(&args_v, "ssh_port");
    _cookbook_kill_session(&session_id, &remote_host, &ssh_port, "Cancelled download").await
}

// ── do_search_hf_models ──

/// Search HuggingFace via the cookbook `/api/cookbook/hf-latest` endpoint.
pub async fn do_search_hf_models(content: &str, _owner: Option<&str>) -> Map<String, Value> {
    let args = match _parse_tool_args(content) {
        Ok(m) => m,
        Err(_) => return err_result("Invalid JSON arguments"),
    };
    let args_v = Value::Object(args.clone());
    // query = args.get("query","") or args.get("search","")
    let query = {
        let q = str_or_empty(&args_v, "query");
        if !q.is_empty() { q } else { str_or_empty(&args_v, "search") }
    };
    // limit = args.get("limit", 10)  (may be int or other)
    let limit_val = args.get("limit").cloned().unwrap_or(Value::from(10));
    // limit_int for the slice: `limit if isinstance(limit, int) else 10`
    let limit_int: usize = match &limit_val {
        Value::Number(n) if n.is_i64() || n.is_u64() => n.as_i64().filter(|x| *x >= 0).map(|x| x as usize).unwrap_or(10),
        _ => 10,
    };

    let mut params: Vec<(String, String)> = Vec::new();
    if !query.is_empty() {
        params.push(("search".to_string(), query.clone()));
    }
    // if limit: params["limit"] = str(limit)
    if truthy(Some(&limit_val)) {
        params.push(("limit".to_string(), value_display(&limit_val)));
    }

    let cl = match client(30) {
        Ok(c) => c,
        Err(e) => return err_result(e.to_string()),
    };
    let url = format!("{_COOKBOOK_BASE}/api/cookbook/hf-latest");
    let resp = match with_headers(cl.get(&url), &_internal_headers(None)).query(&params).send().await {
        Ok(r) => r,
        Err(e) => return err_result(e.to_string()),
    };
    let data = json_loose(resp).await;

    // models = data.get("models") if isinstance(data, dict) else data
    let models: Vec<Value> = if data.is_object() {
        match data.get("models") {
            Some(Value::Array(a)) => a.clone(),
            _ => Vec::new(),
        }
    } else if let Value::Array(a) = &data {
        a.clone()
    } else {
        Vec::new()
    };

    if models.is_empty() {
        return ok_output(format!("No models found for query: {}", py_repr(&query)));
    }

    let header = if !query.is_empty() {
        format!("Found {} model(s) for {}:", models.len(), py_repr(&query))
    } else {
        format!("{} model(s):", models.len())
    };
    let mut lines: Vec<String> = vec![header];
    for m in models.iter().take(limit_int) {
        if m.is_object() {
            // name = m.get("repo_id") or m.get("modelId") or m.get("id") or "?"
            let name = first_truthy_str(m, &["repo_id", "modelId", "id"]).unwrap_or_else(|| "?".to_string());
            let dl = m.get("downloads").filter(|v| truthy(Some(v)));
            // size = m.get("size_gb") or m.get("needed_vram_gb")
            let size = m.get("size_gb").filter(|v| truthy(Some(v))).or_else(|| {
                m.get("needed_vram_gb").filter(|v| truthy(Some(v)))
            });
            let mut bits: Vec<String> = Vec::new();
            if let Some(sz) = size {
                bits.push(format!("~{}GB", value_display(sz)));
            }
            if let Some(d) = dl {
                bits.push(format!("{} downloads", value_display(d)));
            }
            let tail = if !bits.is_empty() {
                format!(" ({})", bits.join(", "))
            } else {
                String::new()
            };
            lines.push(format!("- {name}{tail}"));
        } else {
            lines.push(format!("- {}", value_display(m)));
        }
    }
    let mut out = Map::new();
    out.insert("output".to_string(), Value::String(lines.join("\n")));
    out.insert("models".to_string(), Value::Array(models));
    out.insert("exit_code".to_string(), Value::from(0));
    out
}

/// `a or b or c` over string-ish dict keys: the first truthy value as a String.
fn first_truthy_str(v: &Value, keys: &[&str]) -> Option<String> {
    for k in keys {
        if let Some(val) = v.get(*k) {
            if truthy(Some(val)) {
                return Some(value_display(val));
            }
        }
    }
    None
}

// ── do_adopt_served_model ──

/// Register an externally-launched model server into the Cookbook so it appears
/// in `list_served_models`, can be stopped, and is added to the user's endpoint
/// list for chat.
pub async fn do_adopt_served_model(content: &str, owner: Option<&str>) -> Map<String, Value> {
    let args = match _parse_tool_args(content) {
        Ok(m) => m,
        Err(_) => return err_result("Invalid JSON arguments"),
    };
    let args_v = Value::Object(args.clone());

    // host = (args.get("host") or args.get("remote_host") or "").strip()
    let host = {
        let h = str_or_empty(&args_v, "host");
        let h = if !h.is_empty() { h } else { str_or_empty(&args_v, "remote_host") };
        h.trim().to_string()
    };
    // sess = (args.get("tmux_session") or args.get("session_id") or "").strip()
    let sess = {
        let s = str_or_empty(&args_v, "tmux_session");
        let s = if !s.is_empty() { s } else { str_or_empty(&args_v, "session_id") };
        s.trim().to_string()
    };
    // model = (args.get("model") or args.get("repo_id") or "").strip()
    let model = {
        let m = str_or_empty(&args_v, "model");
        let m = if !m.is_empty() { m } else { str_or_empty(&args_v, "repo_id") };
        m.trim().to_string()
    };
    // port = args.get("port") or 8000  (then int(port))
    let port: i64 = match args.get("port") {
        Some(v) if truthy(Some(v)) => match v {
            Value::Number(n) => n.as_i64().unwrap_or_else(|| n.as_f64().map(|f| f as i64).unwrap_or(8000)),
            Value::String(s) => s.trim().parse::<i64>().unwrap_or(8000),
            _ => 8000,
        },
        _ => 8000,
    };
    // display_name = (args.get("name") or "").strip() or basename(model)
    let display_name = {
        let n = str_or_empty(&args_v, "name");
        let n = n.trim().to_string();
        if !n.is_empty() {
            n
        } else if model.contains('/') {
            model.rsplit('/').next().unwrap_or(&model).to_string()
        } else {
            model.clone()
        }
    };
    // add_endpoint = args.get("add_endpoint", True)
    let add_endpoint = match args.get("add_endpoint") {
        Some(v) => truthy(Some(v)),
        None => true,
    };

    if sess.is_empty() || model.is_empty() {
        return err_result("tmux_session and model are required");
    }

    let headers = _internal_headers(None);
    let q_sess = shlex::try_quote(&sess).map(|c| c.into_owned()).unwrap_or_else(|_| sess.clone());

    // Verify tmux session exists on the target host.
    let check = if !host.is_empty() {
        let q_host = shlex::try_quote(&host).map(|c| c.into_owned()).unwrap_or_else(|_| host.clone());
        format!("ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no {q_host} 'tmux has-session -t {q_sess} 2>&1'")
    } else {
        format!("tmux has-session -t {q_sess} 2>&1")
    };
    {
        let cl = match client(10) {
            Ok(c) => c,
            Err(e) => return err_result(format!("verify failed: {e}")),
        };
        let url = format!("{_COOKBOOK_BASE}/api/shell/exec");
        match with_headers(cl.post(&url), &headers).json(&json!({"command": check})).send().await {
            Ok(r) => {
                let status = r.status().as_u16();
                let text = r.text().await.unwrap_or_default();
                // data = r.json() if content-type json else {} (we re-parse text)
                let data: Value = serde_json::from_str(&text).unwrap_or(Value::Object(Map::new()));
                let exit_bad = match data.get("exit_code") {
                    None | Some(Value::Null) => false,
                    Some(v) => v.as_i64() != Some(0),
                };
                if status >= 400 || exit_bad {
                    // err = (data.get("stderr") or data.get("error") or r.text[:200]).strip()
                    let stderr = str_or_empty(&data, "stderr");
                    let err = if !stderr.is_empty() {
                        stderr
                    } else {
                        let e2 = str_or_empty(&data, "error");
                        if !e2.is_empty() { e2 } else { text.chars().take(200).collect() }
                    };
                    let err = err.trim();
                    let host_disp = if host.is_empty() { "local" } else { &host };
                    return err_result(format!("tmux session {} not found on {host_disp}: {err}", py_repr(&sess)));
                }
            }
            Err(e) => return err_result(format!("verify failed: {e}")),
        }
    }

    // Best-effort health check.
    let health_cmd = if !host.is_empty() {
        let q_host = shlex::try_quote(&host).map(|c| c.into_owned()).unwrap_or_else(|_| host.clone());
        format!("ssh -o ConnectTimeout=5 {q_host} 'curl -s -m 3 http://localhost:{port}/v1/models'")
    } else {
        format!("curl -s -m 3 http://localhost:{port}/v1/models")
    };
    let mut server_up = false;
    if let Ok(cl) = client(10) {
        let url = format!("{_COOKBOOK_BASE}/api/shell/exec");
        if let Ok(r) = with_headers(cl.post(&url), &headers).json(&json!({"command": health_cmd})).send().await {
            let is_json = r
                .headers()
                .get(reqwest::header::CONTENT_TYPE)
                .and_then(|v| v.to_str().ok())
                .map(|s| s.starts_with("application/json"))
                .unwrap_or(false);
            let body = if is_json {
                let data = r.json::<Value>().await.unwrap_or(Value::Object(Map::new()));
                str_or_empty(&data, "stdout")
            } else {
                String::new()
            };
            server_up = body.contains("\"data\"") || body.contains("\"object\"");
        }
    }

    // Read+modify+write cookbook state (APPEND a task; do NOT overwrite).
    let state_v: Value = match client(10) {
        Ok(cl) => {
            let url = format!("{_COOKBOOK_BASE}/api/cookbook/state");
            match with_headers(cl.get(&url), &headers).send().await {
                Ok(resp) => json_if_ct(resp).await,
                Err(e) => return err_result(format!("could not read cookbook state: {e}")),
            }
        }
        Err(e) => return err_result(format!("could not read cookbook state: {e}")),
    };
    let mut state: Map<String, Value> = match state_v {
        Value::Object(m) => m,
        _ => Map::new(),
    };
    let mut tasks: Vec<Value> = match state.get("tasks") {
        Some(Value::Array(a)) => a.clone(),
        _ => Vec::new(),
    };

    let adopted_already = tasks
        .iter()
        .any(|t| t.is_object() && t.get("sessionId").and_then(|v| v.as_str()) == Some(sess.as_str()));

    if !adopted_already {
        let ts = now_ms();
        let host_disp = if host.is_empty() { "local" } else { &host };
        let new_task = json!({
            "id": sess,
            "sessionId": sess,
            "name": display_name,
            "type": "serve",
            "status": "running",
            "output": format!(
                "Adopted externally-launched session {} on {host_disp}.\nReconnect polling will start streaming tmux output shortly.",
                py_repr(&sess)
            ),
            "ts": ts,
            "payload": {"repo_id": model, "remote_host": if host.is_empty() { "" } else { &host }, "_cmd": "(adopted — launched outside cookbook)"},
            "remoteHost": if host.is_empty() { "" } else { &host },
            "sshPort": "",
            "platform": "linux",
            "_serveReady": server_up,
            "_endpointAdded": false,
            "_adoptedExternally": true,
        });
        tasks.push(new_task);
        state.insert("tasks".to_string(), Value::Array(tasks));
        match client(10) {
            Ok(cl) => {
                let url = format!("{_COOKBOOK_BASE}/api/cookbook/state");
                if let Err(e) = with_headers(cl.post(&url), &headers).json(&Value::Object(state.clone())).send().await {
                    return err_result(format!("could not save cookbook state: {e}"));
                }
            }
            Err(e) => return err_result(format!("could not save cookbook state: {e}")),
        }
    }

    // Optionally register as a chat endpoint.
    let mut endpoint_msg = String::new();
    if add_endpoint {
        // host_only = host.split("@",1)[-1] if host else "localhost"
        let host_only = if !host.is_empty() {
            host.splitn(2, '@').last().unwrap_or(&host).to_string()
        } else {
            "localhost".to_string()
        };
        let endpoint_url = format!("http://{host_only}:{port}/v1");
        let ep_payload = json!({
            "action": "add",
            "name": display_name,
            "endpoint_url": endpoint_url,
            "is_local": false,
        });
        let ep_content = serde_json::to_string(&ep_payload).unwrap_or_else(|_| "{}".to_string());
        // Sibling group (management_db) — same-crate path. ASSUMPTION: do_manage_endpoints
        // exists with signature (content: &str, owner: Option<&str>) -> Map.
        let ep_result = super::do_manage_endpoints(&ep_content, owner).await;
        if !ep_result.contains_key("error") {
            endpoint_msg = format!(" Endpoint {endpoint_url} added as {}.", py_repr(&display_name));
        } else {
            let reason = ep_result
                .get("error")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown")
                .to_string();
            endpoint_msg = format!(" Endpoint registration skipped: {reason}");
        }
    }

    let host_disp = if host.is_empty() { "local".to_string() } else { host.clone() };
    let tracked = if adopted_already {
        "Already tracked — skipped state write. "
    } else {
        "Added to cookbook state. "
    };
    let responding = if server_up {
        "Server responding. "
    } else {
        "Server not responding yet (still loading?). "
    };
    let output = format!(
        "Adopted session {} ({model}) on {host_disp}:{port}. {tracked}{responding}{endpoint_msg}",
        py_repr(&sess)
    )
    .trim()
    .to_string();

    let mut out = Map::new();
    out.insert("output".to_string(), Value::String(output));
    out.insert("session_id".to_string(), Value::String(sess));
    out.insert("host".to_string(), Value::String(host));
    out.insert("port".to_string(), Value::from(port));
    out.insert("server_up".to_string(), Value::Bool(server_up));
    out.insert("exit_code".to_string(), Value::from(0));
    out
}

// ── do_list_cookbook_servers ──

/// List the cookbook's configured servers and which one is the current default.
pub async fn do_list_cookbook_servers(_content: &str, _owner: Option<&str>) -> Map<String, Value> {
    let servers = _cookbook_servers().await;
    let hosts: Vec<Value> = match servers.get("hosts") {
        Some(Value::Array(a)) => a.clone(),
        _ => Vec::new(),
    };
    let default = servers.get("default_host").and_then(|v| v.as_str()).unwrap_or("").to_string();

    if hosts.is_empty() {
        let mut out = Map::new();
        out.insert(
            "output".to_string(),
            Value::String("No cookbook servers configured. Downloads/serves default to localhost.".to_string()),
        );
        out.insert("servers".to_string(), Value::Array(Vec::new()));
        out.insert("default_host".to_string(), Value::String(String::new()));
        out.insert("exit_code".to_string(), Value::from(0));
        return out;
    }

    // default_name = next((h["name"] for h in hosts if h["host"]==default and h["name"]), default or "local")
    let default_name = hosts
        .iter()
        .find_map(|h| {
            let name = str_or_empty(h, "name");
            if str_or_empty(h, "host") == default && !name.is_empty() {
                Some(name)
            } else {
                None
            }
        })
        .unwrap_or_else(|| if default.is_empty() { "local".to_string() } else { default.clone() });

    let mut lines: Vec<String> = vec![format!("{} configured server(s) (default: {default_name}):", hosts.len())];
    for h in &hosts {
        let name = {
            let n = str_or_empty(h, "name");
            if n.is_empty() { "(unnamed)".to_string() } else { n }
        };
        let host = {
            let hh = str_or_empty(h, "host");
            if hh.is_empty() { "local".to_string() } else { hh }
        };
        let mark = if str_or_empty(h, "host") == default { " ← default" } else { "" };
        let env = str_or_empty(h, "env");
        let env_bit = if !env.is_empty() && env != "none" {
            format!(" [{}: {}]", env, str_or_empty(h, "envPath"))
        } else {
            String::new()
        };
        let plat = {
            let p = str_or_empty(h, "platform");
            if !p.is_empty() { format!(" ({p})") } else { String::new() }
        };
        lines.push(format!("- {name} → {host}{plat}{env_bit}{mark}"));
    }
    lines.push("\nRefer to servers by their name (e.g. download_model with host=\"gpu-box\").".to_string());

    let mut out = Map::new();
    out.insert("output".to_string(), Value::String(lines.join("\n")));
    out.insert("servers".to_string(), Value::Array(hosts));
    out.insert("default_host".to_string(), Value::String(default));
    out.insert("exit_code".to_string(), Value::from(0));
    out
}

// ── do_list_serve_presets ──

/// List saved serve presets from `cookbook_state.json`.
pub async fn do_list_serve_presets(_content: &str, _owner: Option<&str>) -> Map<String, Value> {
    let state: Value = match client(10) {
        Ok(cl) => {
            let url = format!("{_COOKBOOK_BASE}/api/cookbook/state");
            match with_headers(cl.get(&url), &_internal_headers(None)).send().await {
                Ok(resp) => json_loose(resp).await,
                Err(e) => return err_result(format!("Failed to fetch cookbook state: {e}")),
            }
        }
        Err(e) => return err_result(format!("Failed to fetch cookbook state: {e}")),
    };

    let presets: Vec<Value> = match state.get("presets") {
        Some(Value::Array(a)) => a.clone(),
        _ => Vec::new(),
    };
    if presets.is_empty() {
        let mut out = Map::new();
        out.insert(
            "output".to_string(),
            Value::String(
                "No serve presets saved. Tell the user to save one from the Cookbook UI first, or use serve_model with explicit repo_id + cmd + host.".to_string(),
            ),
        );
        out.insert("presets".to_string(), Value::Array(Vec::new()));
        out.insert("exit_code".to_string(), Value::from(0));
        return out;
    }

    let mut lines: Vec<String> = vec![format!("{} saved serve preset(s):", presets.len())];
    for p in &presets {
        if !p.is_object() {
            continue;
        }
        let name = p.get("name").and_then(|v| v.as_str()).unwrap_or("?").to_string();
        let model = first_truthy_str(p, &["model", "modelId"]).unwrap_or_else(|| "?".to_string());
        let host = first_truthy_str(p, &["host", "remoteHost"]).unwrap_or_else(|| "local".to_string());
        let port = p.get("port").cloned().unwrap_or(Value::String(String::new()));
        let cmd = str_or_empty(p, "cmd");
        let cmd = cmd.trim();
        let mut bits: Vec<String> = vec![format!("- {name}: {model}"), format!("host={host}")];
        if truthy(Some(&port)) {
            bits.push(format!("port={}", value_display(&port)));
        }
        lines.push(bits.join("  "));
        if !cmd.is_empty() {
            // cmd if len(cmd) < 140 else cmd[:140]+"…"
            let cmd_preview = if cmd.chars().count() < 140 {
                cmd.to_string()
            } else {
                let head: String = cmd.chars().take(140).collect();
                format!("{head}…")
            };
            lines.push(format!("    cmd: {cmd_preview}"));
        }
    }
    let mut out = Map::new();
    out.insert("output".to_string(), Value::String(lines.join("\n")));
    out.insert("presets".to_string(), Value::Array(presets));
    out.insert("exit_code".to_string(), Value::from(0));
    out
}

// ── do_serve_preset ──

/// Launch a saved serve preset by name. Resolves cmd + host + model from
/// `cookbook_state.json`, then calls the standard model/serve endpoint.
pub async fn do_serve_preset(content: &str, _owner: Option<&str>) -> Map<String, Value> {
    let args = match _parse_tool_args(content) {
        Ok(m) => m,
        Err(_) => return err_result("Invalid JSON arguments"),
    };
    let args_v = Value::Object(args.clone());
    // name = (args.get("name") or args.get("preset") or "").strip()
    let name = {
        let n = str_or_empty(&args_v, "name");
        let n = if !n.is_empty() { n } else { str_or_empty(&args_v, "preset") };
        n.trim().to_string()
    };
    if name.is_empty() {
        return err_result("name (preset name) is required. Call list_serve_presets to see what's available.");
    }

    let state: Value = match client(10) {
        Ok(cl) => {
            let url = format!("{_COOKBOOK_BASE}/api/cookbook/state");
            match with_headers(cl.get(&url), &_internal_headers(None)).send().await {
                Ok(resp) => json_loose(resp).await,
                Err(e) => return err_result(format!("Failed to fetch cookbook state: {e}")),
            }
        }
        Err(e) => return err_result(format!("Failed to fetch cookbook state: {e}")),
    };

    let presets: Vec<Value> = match state.get("presets") {
        Some(Value::Array(a)) => a.clone(),
        _ => Vec::new(),
    };
    let lname = name.to_lowercase();
    // Exact name first.
    let mut chosen: Option<Value> = None;
    for p in &presets {
        if p.is_object() && str_or_empty(p, "name").to_lowercase() == lname {
            chosen = Some(p.clone());
            break;
        }
    }
    if chosen.is_none() {
        for p in &presets {
            if p.is_object() && str_or_empty(p, "name").to_lowercase().contains(&lname) {
                chosen = Some(p.clone());
                break;
            }
        }
    }
    let chosen = match chosen {
        Some(c) => c,
        None => {
            // sample = ", ".join(p.get("name") or "?" for p in presets[:8] if dict)
            let sample: Vec<String> = presets
                .iter()
                .take(8)
                .filter(|p| p.is_object())
                .map(|p| {
                    let n = str_or_empty(p, "name");
                    if n.is_empty() { "?".to_string() } else { n }
                })
                .collect();
            let sample_str = if sample.is_empty() { "(none)".to_string() } else { sample.join(", ") };
            return err_result(format!("No preset matching {}. Available: {sample_str}", py_repr(&name)));
        }
    };

    let chosen_name = str_or_empty(&chosen, "name");
    let chosen_name_repr = py_repr(&chosen_name);
    let repo_id = first_truthy_str(&chosen, &["model", "modelId"]).unwrap_or_default();
    let cmd = str_or_empty(&chosen, "cmd").trim().to_string();
    let host = first_truthy_str(&chosen, &["host", "remoteHost"]).unwrap_or_default();
    if repo_id.is_empty() || cmd.is_empty() {
        return err_result(format!("Preset {chosen_name_repr} is missing model or cmd — can't launch."));
    }

    let mut payload = Map::new();
    payload.insert("repo_id".to_string(), Value::String(repo_id.clone()));
    payload.insert("cmd".to_string(), Value::String(cmd.clone()));
    if !host.is_empty() {
        payload.insert("remote_host".to_string(), Value::String(host.clone()));
    }
    let env_cfg = _cookbook_env_for_host(&host).await;
    let env_cfg_v = Value::Object(env_cfg);
    for key in ["env_prefix", "gpus", "hf_token", "platform", "ssh_port"] {
        let v = str_or_empty(&env_cfg_v, key);
        if !v.is_empty() {
            payload.insert(key.to_string(), Value::String(v));
        }
    }

    let cl = match client(30) {
        Ok(c) => c,
        Err(e) => return err_result(e.to_string()),
    };
    let url = format!("{_COOKBOOK_BASE}/api/model/serve");
    let resp = match with_headers(cl.post(&url), &_internal_headers(None)).json(&Value::Object(payload)).send().await {
        Ok(r) => r,
        Err(e) => return err_result(e.to_string()),
    };
    let data = json_loose(resp).await;
    if truthy(data.get("ok")) {
        let sid = data.get("session_id").and_then(|v| v.as_str()).map(|s| s.to_string()).unwrap_or_else(|| "?".to_string());
        let registered = _cookbook_register_task(&sid, &repo_id, &host, &cmd, "serve").await;
        let note = if registered { "" } else { " (state-write failed — task may not show in UI)" };
        let host_disp = if host.is_empty() { "local".to_string() } else { host };
        let mut out = Map::new();
        out.insert(
            "output".to_string(),
            Value::String(format!("Launched preset {chosen_name_repr}: {repo_id} on {host_disp} (session: {sid}){note}")),
        );
        out.insert("session_id".to_string(), Value::String(sid));
        out.insert("exit_code".to_string(), Value::from(0));
        return out;
    }
    let err = data.get("error").and_then(|v| v.as_str()).unwrap_or("Serve failed").to_string();
    err_result(err)
}

// ── do_list_cached_models ──

/// List models already cached locally (or on a remote host).
pub async fn do_list_cached_models(content: &str, _owner: Option<&str>) -> Map<String, Value> {
    let args = if content.trim().is_empty() {
        Map::new()
    } else {
        match _parse_tool_args(content) {
            Ok(m) => m,
            Err(_) => return err_result("Invalid JSON arguments"),
        }
    };
    let args_v = Value::Object(args.clone());

    let raw_host = str_or_empty(&args_v, "host").trim().to_string();
    let host = if !raw_host.is_empty() {
        _resolve_cookbook_host(&raw_host).await
    } else {
        String::new()
    };

    let mut params: Vec<(String, String)> = Vec::new();
    if !host.is_empty() {
        params.push(("host".to_string(), host.clone()));
    }
    if truthy(args.get("model_dir")) {
        params.push(("model_dir".to_string(), str_coerce_or_empty(&args_v, "model_dir")));
    }
    if truthy(args.get("ssh_port")) {
        params.push(("ssh_port".to_string(), str_coerce_or_empty(&args_v, "ssh_port")));
    }
    if truthy(args.get("platform")) {
        params.push(("platform".to_string(), str_coerce_or_empty(&args_v, "platform")));
    }

    let cl = match client(60) {
        Ok(c) => c,
        Err(e) => return err_result(e.to_string()),
    };
    let url = format!("{_COOKBOOK_BASE}/api/model/cached");
    let resp = match with_headers(cl.get(&url), &_internal_headers(None)).query(&params).send().await {
        Ok(r) => r,
        Err(e) => return err_result(e.to_string()),
    };
    let data = json_loose(resp).await;

    // models = data.get("models", []) if isinstance(data, dict) else data
    let models: Vec<Value> = if data.is_object() {
        match data.get("models") {
            Some(Value::Array(a)) => a.clone(),
            _ => Vec::new(),
        }
    } else if let Value::Array(a) = &data {
        a.clone()
    } else {
        Vec::new()
    };

    if models.is_empty() {
        // Fall back to completed Cookbook downloads.
        let mut downloaded: Vec<String> = Vec::new();
        if let Ok(cl2) = client(10) {
            let url = format!("{_COOKBOOK_BASE}/api/cookbook/state");
            if let Ok(resp) = with_headers(cl2.get(&url), &_internal_headers(None)).send().await {
                let state = json_if_ct(resp).await;
                if let Some(Value::Array(tasks)) = state.get("tasks") {
                    for t in tasks {
                        if !t.is_object() || t.get("type").and_then(|v| v.as_str()) != Some("download") {
                            continue;
                        }
                        let st = str_or_empty(t, "status").to_lowercase();
                        if !matches!(st.as_str(), "done" | "completed") {
                            continue;
                        }
                        // task_host = t.get("remoteHost") or payload.remote_host or ""
                        let task_host = {
                            let rh = str_or_empty(t, "remoteHost");
                            if !rh.is_empty() {
                                rh
                            } else {
                                t.get("payload")
                                    .and_then(|p| p.get("remote_host"))
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("")
                                    .to_string()
                            }
                        };
                        if !host.is_empty() && task_host != host {
                            continue;
                        }
                        // repo = modelId or repoId or payload.repo_id or name
                        let repo = {
                            let mut r = first_truthy_str(t, &["modelId", "repoId"]);
                            if r.is_none() {
                                if let Some(p) = t.get("payload") {
                                    if truthy(p.get("repo_id")) {
                                        r = p.get("repo_id").map(value_display);
                                    }
                                }
                            }
                            if r.is_none() && truthy(t.get("name")) {
                                r = t.get("name").map(value_display);
                            }
                            r
                        };
                        if let Some(repo) = repo {
                            if !repo.is_empty() && !downloaded.contains(&repo) {
                                downloaded.push(repo);
                            }
                        }
                    }
                }
            }
        }
        if !downloaded.is_empty() {
            let host_str = if !raw_host.is_empty() || !host.is_empty() {
                format!(" on {}", if !raw_host.is_empty() { &raw_host } else { &host })
            } else {
                String::new()
            };
            let mut lines: Vec<String> = vec![format!(
                "No cache paths were detected{host_str}, but Cookbook has completed download task(s):"
            )];
            lines.extend(downloaded.iter().map(|repo| format!("- {repo} — downloaded via Cookbook task")));
            let model_list: Vec<Value> = downloaded
                .iter()
                .map(|repo| json!({"repo_id": repo, "source": "cookbook_task"}))
                .collect();
            let mut out = Map::new();
            out.insert("output".to_string(), Value::String(lines.join("\n")));
            out.insert("models".to_string(), Value::Array(model_list));
            out.insert("exit_code".to_string(), Value::from(0));
            return out;
        }
        let host_str = if !raw_host.is_empty() || !host.is_empty() {
            format!(" on {}", if !raw_host.is_empty() { &raw_host } else { &host })
        } else {
            String::new()
        };
        return ok_output(format!("No cached models found{host_str}."));
    }

    let mut lines: Vec<String> = vec![format!("{} cached model(s):", models.len())];
    for m in &models {
        let name = m.get("repo_id").and_then(|v| v.as_str()).unwrap_or("?").to_string();
        // sz = m.get("size") or (f"{size_bytes/1024^3:.1f}GB" if size_bytes else "")
        let sz = {
            let s = str_coerce_or_empty(m, "size");
            if truthy(m.get("size")) {
                s
            } else if truthy(m.get("size_bytes")) {
                let bytes = m.get("size_bytes").and_then(|v| v.as_f64()).unwrap_or(0.0);
                format!("{:.1}GB", bytes / (1024f64 * 1024f64 * 1024f64))
            } else {
                String::new()
            }
        };
        let inc = if truthy(m.get("has_incomplete")) { " (incomplete)" } else { "" };
        let kind = if truthy(m.get("is_diffusion")) { " [diffusion]" } else { "" };
        lines.push(format!("- {name}{kind} — {sz}{inc}"));
    }
    let mut out = Map::new();
    out.insert("output".to_string(), Value::String(lines.join("\n")));
    out.insert("models".to_string(), Value::Array(models));
    out.insert("exit_code".to_string(), Value::from(0));
    out
}

// ── do_edit_image ──

/// Edit a gallery image (upscale, rembg, inpaint, harmonize). Note: this hits
/// `http://localhost:7000/api/gallery/<action>` with NO internal headers,
/// matching the Python exactly.
pub async fn do_edit_image(content: &str, _owner: Option<&str>) -> Map<String, Value> {
    let args = match _parse_tool_args(content) {
        Ok(m) => m,
        Err(_) => return err_result("Invalid JSON arguments"),
    };
    let args_v = Value::Object(args.clone());
    let image_id = str_or_empty(&args_v, "image_id");
    let action = str_or_empty(&args_v, "action");
    if image_id.is_empty() || action.is_empty() {
        return err_result("image_id and action are required");
    }
    let mut payload = Map::new();
    payload.insert("image_id".to_string(), Value::String(image_id));
    if truthy(args.get("prompt")) {
        payload.insert("prompt".to_string(), args.get("prompt").cloned().unwrap());
    }
    if truthy(args.get("scale")) {
        payload.insert("scale".to_string(), args.get("scale").cloned().unwrap());
    }

    let cl = match client(120) {
        Ok(c) => c,
        Err(e) => return err_result(e.to_string()),
    };
    // No internal headers (matches Python).
    let url = format!("http://localhost:7000/api/gallery/{action}");
    let resp = match cl.post(&url).json(&Value::Object(payload)).send().await {
        Ok(r) => r,
        Err(e) => return err_result(e.to_string()),
    };
    let data = json_loose(resp).await;

    if truthy(data.get("success")) || truthy(data.get("id")) {
        // f"... New image ID: {data.get('id', '?')}"
        let id = match data.get("id") {
            Some(Value::String(s)) => s.clone(),
            Some(Value::Null) | None => "?".to_string(),
            Some(other) => other.to_string(),
        };
        return ok_output(format!("Image edited ({action}). New image ID: {id}"));
    }
    // data.get("error", f"{action} failed")
    let err = match data.get("error").and_then(|v| v.as_str()) {
        Some(e) => e.to_string(),
        None => format!("{action} failed"),
    };
    err_result(err)
}

// ── Tests ──

#[cfg(test)]
mod tests {
    use super::super::_APP_API_BLOCKLIST_PREFIXES;

    /// Prefix entries must NOT have a trailing slash so that bare paths like
    /// `/api/users` (no trailing slash) are correctly blocked. Python's list
    /// uses no trailing slashes; the Rust port had them erroneously, causing
    /// `path.starts_with("/api/users/")` to pass for `/api/users` and `/api/users?...`.
    #[test]
    fn blocklist_prefixes_have_no_trailing_slash() {
        for prefix in _APP_API_BLOCKLIST_PREFIXES {
            assert!(
                !prefix.ends_with('/'),
                "blocklist prefix {prefix:?} has a trailing slash — the starts_with check \
                 would pass for a bare path like {prefix} (no slash) or a query-string URL",
            );
        }
    }

    /// Paths that MUST be blocked with the current (no-trailing-slash) prefixes.
    #[test]
    fn blocklist_prefixes_block_expected_paths() {
        // Every path here represents a real endpoint that must be refused.
        let must_block = [
            "/api/auth",
            "/api/auth/login",
            "/api/auth/logout",
            "/api/users",
            "/api/users/42",
            "/api/tokens",
            "/api/tokens/abc",
            "/api/admin",
            "/api/admin/wipe",
            "/api/backup/restore",
            "/api/backup/restore/latest",
        ];
        for path in &must_block {
            let blocked = _APP_API_BLOCKLIST_PREFIXES.iter().any(|p| path.starts_with(p));
            assert!(blocked, "path {path:?} should be blocked but is not");
        }
    }

    /// Paths that must NOT be blocked — they share a string prefix with a
    /// blocked segment but are distinct endpoints the agent is allowed to use.
    #[test]
    fn blocklist_prefixes_do_not_over_block() {
        let must_pass = [
            "/api/cookbook/state",
            "/api/notes",
            "/api/calendar/events",
            "/api/research/start",
            "/api/backup",       // GET backup list is fine; only /restore is blocked
            "/api/backup/list",
        ];
        for path in &must_pass {
            let blocked = _APP_API_BLOCKLIST_PREFIXES.iter().any(|p| path.starts_with(p));
            assert!(!blocked, "path {path:?} should NOT be blocked but is");
        }
    }
}
