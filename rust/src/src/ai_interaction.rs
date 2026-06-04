// src/ai_interaction.rs  <- src/ai_interaction.py (dispatch_ai_tool only)
//! Thin shim exposing `dispatch_ai_tool` so `tool_execution::execute_tool_block`'s
//! AI-tool dispatch arm compiles and links without gating the whole branch out.
//!
//! The real `src/ai_interaction.py` is a heavy subsystem (per-session LLM calls,
//! `SessionManager`, the memory store, the UI-control bridge, the teacher
//! escalation path). The `chat_with_model` tool is now LIVE — it ports
//! `_resolve_model` + `do_chat_with_model` (src/ai_interaction.py:61-177). Every
//! OTHER `do_*` helper remains unported, so its arm returns an HONEST "not
//! available in the Rust port yet" error result — never a fabricated success.
//! The `(description, result)` tuple shape and the per-tool `desc` strings mirror
//! `ai_interaction.py::dispatch_ai_tool` exactly (L1741-1799) so the observable
//! description breadcrumbs stay faithful.

use serde_json::{Map, Value};
use serde_json::json;

// WIRED: every recognized `do_*` AI tool is now LIVE — create_session, list_sessions,
// send_to_session, pipeline, manage_session, manage_memory, list_models, ui_control,
// chat_with_model, and ask_teacher (the teacher-escalation LLM flow) are all ported
// below, each wired to its ported dep. The former honest-DEFER `_stub` helper has been
// removed now that no recognized arm uses it (it would only ever be dead code).

/// `dispatch_ai_tool(tool, content, session_id=None, owner=None) -> (description, result_dict)`.
///
/// Reproduces the Python dispatcher's `desc` computation per arm; every
/// recognized AI tool now dispatches to its ported `do_*` implementation. The
/// trailing `else` branch mirrors Python's `"Unknown AI interaction tool: {tool}"`.
pub async fn dispatch_ai_tool(
    tool: &str,
    content: &str,
    _session_id: Option<&str>,
    _owner: Option<&str>,
) -> (String, Map<String, Value>) {
    // Helper for `content.split("\n")[0].strip()[:N]` (char slice).
    fn first_line_strip_take(content: &str, n: usize) -> String {
        let first = content.split('\n').next().unwrap_or("");
        first.trim().chars().take(n).collect()
    }
    // Helper for `content.strip()[:N]` (char slice).
    fn strip_take(content: &str, n: usize) -> String {
        content.trim().chars().take(n).collect()
    }

    let (desc, result): (String, Map<String, Value>) = match tool {
        "chat_with_model" => {
            // model_spec = content.split("\n")[0].strip()[:60]
            let model_spec = first_line_strip_take(content, 60);
            // LIVE: src.ai_interaction.do_chat_with_model (was _stub). Ports
            // _resolve_model + do_chat_with_model below.
            // result = await do_chat_with_model(content, session_id, owner=owner)
            (format!("chat_with_model: {model_spec}"), do_chat_with_model(content, _session_id, _owner).await)
        }
        "create_session" => {
            // name = content.split("\n")[0].strip()[:60]
            let name = first_line_strip_take(content, 60);
            // LIVE: src.ai_interaction.do_create_session (was _stub).
            (format!("create_session: {name}"), do_create_session(content, _owner).await)
        }
        "list_sessions" => {
            // keyword = content.strip()[:40]
            let keyword = strip_take(content, 40);
            let desc = if keyword.is_empty() {
                "list_sessions".to_string()
            } else {
                format!("list_sessions: {keyword}")
            };
            // LIVE: src.ai_interaction.do_list_sessions (was _stub).
            (desc, do_list_sessions(content, _owner).await)
        }
        "send_to_session" => {
            // sid = content.split("\n")[0].strip()[:20]
            let sid = first_line_strip_take(content, 20);
            // LIVE: src.ai_interaction.do_send_to_session (was _stub).
            // result = await do_send_to_session(content, session_id, owner=owner)
            (format!("send_to_session: {sid}"), do_send_to_session(content, _owner).await)
        }
        // LIVE: src.ai_interaction.do_pipeline (was _stub).
        // result = await do_pipeline(content, session_id, owner=owner)
        "pipeline" => ("pipeline: running steps".to_string(), do_pipeline(content, _owner).await),
        "manage_session" => {
            // action = content.split("\n")[0].strip()[:40]
            let action = first_line_strip_take(content, 40);
            // LIVE: src.ai_interaction.do_manage_session (was _stub).
            (format!("manage_session: {action}"), do_manage_session(content, _session_id, _owner).await)
        }
        "manage_memory" => {
            let action = first_line_strip_take(content, 40);
            // LIVE: src.ai_interaction.do_manage_memory (was _stub).
            (format!("manage_memory: {action}"), do_manage_memory(content, _owner).await)
        }
        "list_models" => {
            let keyword = strip_take(content, 40);
            let desc = if keyword.is_empty() {
                "list_models".to_string()
            } else {
                format!("list_models: {keyword}")
            };
            // LIVE: src.ai_interaction.do_list_models (was _stub).
            // result = await do_list_models(content, session_id, owner=owner)
            (desc, do_list_models(content, _owner).await)
        }
        "ui_control" => {
            let action = first_line_strip_take(content, 60);
            // LIVE: src.ai_interaction.do_ui_control (was _stub).
            // result = await do_ui_control(content, session_id, owner=owner)
            (format!("ui_control: {action}"), do_ui_control(content, _session_id, _owner).await)
        }
        "ask_teacher" => {
            // problem = content.split("\n", 1)[-1].strip()[:60]
            // splitn(2) then take the LAST element of the (1- or 2-) split.
            let problem_src = content.splitn(2, '\n').last().unwrap_or("");
            let problem: String = problem_src.trim().chars().take(60).collect();
            // LIVE: src.ai_interaction.do_ask_teacher (was _stub). Ports the
            // teacher-help LLM flow below (src/ai_interaction.py:193-241).
            // result = await do_ask_teacher(content, session_id, owner=owner)
            (format!("ask_teacher: {problem}"), do_ask_teacher(content, _session_id, _owner).await)
        }
        // else: desc = f"unknown ai tool: {tool}"; result = {"error": f"Unknown AI interaction tool: {tool}"}
        _ => {
            let mut m = Map::new();
            m.insert(
                "error".to_string(),
                Value::String(format!("Unknown AI interaction tool: {tool}")),
            );
            (format!("unknown ai tool: {tool}"), m)
        }
    };

    (desc, result)
}

/// `_resolve_model(spec, owner=None) -> (endpoint_url, model_id, headers)` —
/// src/ai_interaction.py:61-138. Spec is "model" or "model@endpoint_name". Raises
/// (PyError::Value) "Model not found" faithful to the Python ValueError. Headers +
/// URLs now route through the shared `endpoint_resolver` helpers (`build_headers`/
/// `build_models_url`/`build_chat_url`) instead of hardcoding `base+"/models"` + a
/// `Bearer` header: `build_headers` emits provider-correct auth (Anthropic gets
/// `x-api-key` + `anthropic-version`, NOT `Authorization: Bearer`), and the URL
/// builders pick the provider-specific routes (Anthropic `/v1/messages` +
/// `/v1/models`, native Ollama `/chat` + `/tags`). `normalize_base` is still shared.
///
/// OWNER SCOPE (upstream 5f58f9a — "scope tool model resolution by owner"): when
/// `owner` is truthy the endpoint query applies `owner_filter(q, ModelEndpoint,
/// owner)`, re-expressed as raw SQL `(owner = ? OR owner IS NULL)` (the SQLAlchemy
/// predicate; `include_shared=True` default keeps NULL-owner 'shared' endpoints
/// visible). An empty-string owner (auth-disabled / single-user mode) is Python-falsy,
/// so the scope is SKIPPED — matching `owner_filter`'s `if not user: return query`.
pub(crate) async fn resolve_model_spec(spec: &str, owner: Option<&str>)
    -> crate::error::PyResult<(String, String, indexmap::IndexMap<String, String>)>
{
    use crate::error::PyError;
    use crate::src::endpoint_resolver::{build_chat_url, build_headers, build_models_url, normalize_base};
    use crate::src::llm_core::{_detect_provider, ANTHROPIC_MODELS};

    // spec = spec.strip(); split on LAST '@'
    let spec = spec.trim();
    let (model_name, target_endpoint_name): (String, Option<String>) =
        match spec.rsplit_once('@') {
            Some((m, ep)) => (m.trim().to_string(), Some(ep.trim().to_string())),
            None => (spec.to_string(), None),
        };

    // query = db.query(ModelEndpoint).filter(is_enabled==True)
    //   [.filter(name.ilike(%target%))][+owner_filter(q, ModelEndpoint, owner)]
    // No ORDER BY (Python .all() => rowid/insert order). SQLite LIKE is
    // ASCII-case-insensitive => ilike. The owner predicate is appended as raw SQL
    // `(owner = ? OR owner IS NULL)` exactly as `endpoint_resolver::resolve_endpoint_row`.
    // An empty-string owner is Python-falsy => unscoped (owner_filter's `if not user`).
    let owner = owner.filter(|o| !o.is_empty());
    let conn = crate::core::database::session_local()
        .map_err(|e| PyError::other(e.to_string()))?;
    // Fetch (base_url, api_key) per enabled endpoint.
    let rows: Vec<(String, Option<String>)> = {
        let collect = |stmt: &mut rusqlite::Statement, params: &[&dyn rusqlite::ToSql]|
            -> rusqlite::Result<Vec<(String, Option<String>)>> {
            let it = stmt.query_map(params, |r| Ok((r.get::<_, String>(0)?, r.get::<_, Option<String>>(1)?)))?;
            it.collect()
        };
        // Build the WHERE incrementally so the (name LIKE) × (owner scope) matrix
        // stays one code path. Params bound positionally in append order.
        let mut sql = String::from("SELECT base_url, api_key FROM model_endpoints WHERE is_enabled = 1");
        let mut params: Vec<Box<dyn rusqlite::ToSql>> = Vec::new();
        if let Some(t) = target_endpoint_name.as_deref().filter(|t| !t.is_empty()) {
            sql.push_str(&format!(" AND name LIKE ?{}", params.len() + 1));
            params.push(Box::new(format!("%{t}%")));
        }
        if let Some(o) = owner {
            sql.push_str(&format!(" AND (owner = ?{n} OR owner IS NULL)", n = params.len() + 1));
            params.push(Box::new(o.to_string()));
        }
        let mut stmt = conn.prepare(&sql).map_err(|e| PyError::other(e.to_string()))?;
        let pref: Vec<&dyn rusqlite::ToSql> = params.iter().map(|b| b.as_ref()).collect();
        collect(&mut stmt, &pref).map_err(|e| PyError::other(e.to_string()))?
    };
    drop(conn); // release the conn BEFORE the await-probe (no conn held across .await)

    // `if not endpoints: raise ValueError("No enabled endpoints found" + matching '{t}')`
    if rows.is_empty() {
        let msg = match &target_endpoint_name {
            Some(t) if !t.is_empty() => format!("No enabled endpoints found matching '{t}'"),
            _ => "No enabled endpoints found".to_string(),
        };
        return Err(PyError::value(msg));
    }

    let model_lower = model_name.to_lowercase();
    for (base_url, enc_key) in rows {
        let base = normalize_base(Some(&base_url));
        let provider = _detect_provider(&base);
        // ep.api_key is the DECRYPTED key (ORM EncryptedText read path). Empty/NULL -> None.
        let api_key: Option<String> = enc_key.filter(|k| !k.is_empty())
            .map(|k| crate::src::secret_storage::decrypt(&k));
        // headers = build_headers(ep.api_key, base) — provider-correct auth
        // (Anthropic: x-api-key + anthropic-version, NOT Authorization: Bearer).
        let headers = build_headers(api_key.as_deref(), &base);

        if provider == "anthropic" {
            // match against ANTHROPIC_MODELS (bidirectional substring, lowercased)
            let matched = ANTHROPIC_MODELS.iter().find(|am| {
                let aml = am.to_lowercase();
                model_lower.contains(&aml) || aml.contains(&model_lower)
            });
            if let Some(am) = matched {
                // return build_chat_url(base), matched, headers
                return Ok((build_chat_url(&base), am.to_string(), headers));
            }
        } else {
            // OpenAI-compatible AND native Ollama: probe the provider's model list.
            // httpx.get(build_models_url(base), headers=headers, timeout=5)
            // reqwest async client, 5s timeout. On ANY error -> model_ids = [] (Python except).
            let model_ids: Vec<String> = async {
                let mut hmap = reqwest::header::HeaderMap::new();
                for (hk, hv) in &headers {
                    if let (Ok(n), Ok(v)) = (
                        reqwest::header::HeaderName::from_bytes(hk.as_bytes()),
                        reqwest::header::HeaderValue::from_str(hv),
                    ) { hmap.insert(n, v); }
                }
                let client = reqwest::Client::builder()
                    .timeout(std::time::Duration::from_secs(5)).build().ok()?;
                let resp = client.get(build_models_url(&base)).headers(hmap).send().await.ok()?;
                if !resp.status().is_success() { return None; } // r.raise_for_status()
                let data: Value = resp.json().await.ok()?;
                // ids = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
                let mut ids: Vec<String> = data.get("data").and_then(Value::as_array).map(|arr| {
                    arr.iter().filter_map(|m| m.get("id").and_then(Value::as_str).map(String::from)).collect()
                }).unwrap_or_default();
                // Native Ollama returns models[].name (or .model) instead of data[].id.
                //   if not model_ids: model_ids = [m.get("name") or m.get("model")
                //       for m in (data.get("models") or []) if m.get("name") or m.get("model")]
                if ids.is_empty() {
                    ids = data.get("models").and_then(Value::as_array).map(|arr| {
                        arr.iter().filter_map(|m| {
                            // m.get("name") or m.get("model") — Python truthiness:
                            // a present-but-empty "name" falls through to "model".
                            m.get("name").and_then(Value::as_str).filter(|s| !s.is_empty())
                                .or_else(|| m.get("model").and_then(Value::as_str).filter(|s| !s.is_empty()))
                                .map(String::from)
                        }).collect()
                    }).unwrap_or_default();
                }
                Some(ids)
            }.await.unwrap_or_default();

            // exact (lowercased ==) first
            for mid in &model_ids {
                if mid.to_lowercase() == model_lower {
                    return Ok((build_chat_url(&base), mid.clone(), headers));
                }
            }
            // then partial (bidirectional substring, lowercased)
            for mid in &model_ids {
                let midl = mid.to_lowercase();
                if model_lower.contains(&midl) || midl.contains(&model_lower) {
                    return Ok((build_chat_url(&base), mid.clone(), headers));
                }
            }
        }
    }
    // raise ValueError(f"Model '{spec}' not found on any configured endpoint")
    Err(PyError::value(format!("Model '{spec}' not found on any configured endpoint")))
}

/// `do_chat_with_model(content, session_id=None, owner=None) -> Dict`
/// (src/ai_interaction.py:144-186). Line1=model_spec, Line2+=message. Returns
/// {"model","response"} or {"error"}. `owner` is threaded into `_resolve_model` so
/// the tool only resolves the caller's own (or shared) endpoints (upstream 5f58f9a).
async fn do_chat_with_model(content: &str, _session_id: Option<&str>, owner: Option<&str>) -> Map<String, Value> {
    // lines = content.strip().split("\n", 1)  (Python splitn 2 on the STRIPPED content)
    let stripped = content.trim();
    let mut it = stripped.splitn(2, '\n');
    let first = it.next().unwrap_or("").trim();
    if first.is_empty() {
        let mut m = Map::new();
        m.insert("error".into(), Value::String("First line must be the model name".into()));
        return m;
    }
    let model_spec = first.to_string();
    let message = it.next().unwrap_or("").trim().to_string();
    if message.is_empty() {
        let mut m = Map::new();
        m.insert("error".into(), Value::String("No message provided (line 2+ is the message)".into()));
        return m;
    }
    // url, model, headers = _resolve_model(model_spec, owner=owner) -> except ValueError: {"error": str(e)}
    // MINOR DRIFT: a DB-layer failure in resolve_model_spec surfaces here as the
    // {"error": <db msg>} (ValueError-branch) shape rather than Python's broad-except
    // {"error": "Failed to get response from..."}; both are {"error":...} so the
    // observable Map shape is preserved.
    let (url, model, headers) = match resolve_model_spec(&model_spec, owner).await {
        Ok(t) => t,
        Err(e) => { let mut m = Map::new(); m.insert("error".into(), Value::String(e.to_string())); return m; }
    };
    // response = await llm_call_async(url, model, [{role:user,content:message}],
    //     headers=headers, timeout=AI_CHAT_TIMEOUT)  with default temp/max_tokens.
    match crate::src::llm_core::llm_call_async(
        &url, &model,
        vec![json!({"role": "user", "content": message})],
        crate::src::llm_core::LLMConfig::DEFAULT_TEMPERATURE, // 1.0
        crate::src::llm_core::LLMConfig::DEFAULT_MAX_TOKENS,  // 0
        headers,
        120, // AI_CHAT_TIMEOUT
    ).await {
        Ok(mut response) => {
            // if len(response) > 10000: response = response[:10000] + "\n... (truncated)"
            // Python slices by code-unit/char; use chars() to stay safe on multibyte.
            if response.chars().count() > 10000 {
                let head: String = response.chars().take(10000).collect();
                response = format!("{head}\n... (truncated)");
            }
            let mut m = Map::new();
            m.insert("model".into(), Value::String(model));
            m.insert("response".into(), Value::String(response));
            m
        }
        Err(e) => {
            // except Exception as e: logger.error(...); {"error": f"Failed to get response from {model_spec}: {e}"}
            crate::pylog::error(&format!("chat_with_model failed: {e}"));
            let mut m = Map::new();
            m.insert("error".into(), Value::String(format!("Failed to get response from {model_spec}: {e}")));
            m
        }
    }
}

/// `_TEACHER_SYSTEM_PROMPT` — src/ai_interaction.py:180-187. The VERBATIM system
/// prompt the teacher model receives in `do_ask_teacher`. Defined here as a
/// private const identical to the Python literal (and to the copy carried in
/// `teacher_escalation.rs:285`, which is module-private to that file).
const _TEACHER_SYSTEM_PROMPT: &str = "You are a senior AI mentor. A less capable model is stuck on a problem and asking for help. Provide clear, actionable guidance:\n1. Brief analysis of the problem\n2. Recommended approach (step by step)\n3. Key things to watch out for\n\nBe concise and practical. No preamble.";

/// `do_ask_teacher(content, session_id=None, owner=None) -> Dict`
/// (src/ai_interaction.py:193-241).
///
/// Ask a more capable model for help. Content format:
///   Line 1: model_name (or 'auto')
///   Line 2+: the problem description
///
/// `session_id` is accepted but unused, matching the Python signature (it carries
/// the param for dispatch-arity parity only). `owner` is threaded into
/// `_resolve_model` so the teacher resolves only the caller's own (or shared)
/// endpoints (upstream 5f58f9a).
async fn do_ask_teacher(content: &str, _session_id: Option<&str>, owner: Option<&str>) -> Map<String, Value> {
    // lines = content.strip().split("\n", 1)
    // model_spec = lines[0].strip() if lines else "auto"   (str.split never yields []
    //   so the `else "auto"` branch is dead in Python; replicate with unwrap_or("auto")).
    // problem = lines[1].strip() if len(lines) > 1 else ""
    let stripped = content.trim();
    let mut it = stripped.splitn(2, '\n');
    let mut model_spec = it.next().unwrap_or("auto").trim().to_string();
    let problem = it.next().unwrap_or("").trim().to_string();

    // if not problem: {"error": "No problem description provided"}
    if problem.is_empty() {
        return err("No problem description provided");
    }

    // if model_spec.lower() in ("auto", ""): model_spec = get_setting("teacher_model", "")
    if matches!(model_spec.to_lowercase().as_str(), "auto" | "") {
        model_spec = crate::src::settings::get_setting("teacher_model", Value::String(String::new()))
            .as_str()
            .unwrap_or("")
            .to_string();
        // if not model_spec: {"error": "No teacher model configured. ..."}
        if model_spec.is_empty() {
            return err("No teacher model configured. Specify a model name or set teacher_model in settings.");
        }
    }

    // try: url, model, headers = _resolve_model(model_spec, owner=owner) except ValueError: {"error": str(e)}
    // MINOR DRIFT (same as do_chat_with_model:281): a DB-layer failure in
    // resolve_model_spec surfaces here as the {"error": <msg>} (ValueError-branch)
    // shape rather than a separate exception class; both are {"error": ...} so the
    // observable Map shape is preserved.
    let (url, model, headers) = match resolve_model_spec(&model_spec, owner).await {
        Ok(t) => t,
        Err(e) => return err(e.to_string()),
    };

    // response = await llm_call_async(url, model, [system=_TEACHER_SYSTEM_PROMPT,
    //   user=f"Problem:\n{problem}"], headers=headers, timeout=AI_CHAT_TIMEOUT)
    // with the default temperature/max_tokens (Python omits them => llm_call_async defaults).
    match crate::src::llm_core::llm_call_async(
        &url,
        &model,
        vec![
            json!({"role": "system", "content": _TEACHER_SYSTEM_PROMPT}),
            json!({"role": "user", "content": format!("Problem:\n{problem}")}),
        ],
        crate::src::llm_core::LLMConfig::DEFAULT_TEMPERATURE, // 1.0
        crate::src::llm_core::LLMConfig::DEFAULT_MAX_TOKENS,  // 0
        headers,
        AI_CHAT_TIMEOUT, // 120
    )
    .await
    {
        Ok(mut response) => {
            // if len(response) > 8000: response = response[:8000] + "\n... (truncated)"
            // NOTE: ask_teacher truncates at 8000 (NOT 10000 like chat_with_model).
            // Python slices by code unit; use chars() to stay safe on multibyte.
            if response.chars().count() > 8000 {
                let head: String = response.chars().take(8000).collect();
                response = format!("{head}\n... (truncated)");
            }
            // return {"model": model, "response": response, "teacher": True}
            let mut m = Map::new();
            m.insert("model".to_string(), Value::String(model));
            m.insert("response".to_string(), Value::String(response));
            m.insert("teacher".to_string(), Value::Bool(true));
            m
        }
        Err(e) => {
            // except Exception as e: logger.error(...); {"error": f"Teacher call failed ({model_spec}): {e}"}
            crate::pylog::error(&format!("ask_teacher failed: {e}"));
            err(format!("Teacher call failed ({model_spec}): {e}"))
        }
    }
}

// ===========================================================================
// AI-to-AI interaction `do_*` ports (src/ai_interaction.py).
// ===========================================================================
//
// Shared constants + helpers. All deps are already ported: the SessionManager is
// reached via the concrete global `bg_monitor::get_session_manager()`, the memory
// store via a fresh disk-backed `MemoryManager::new(&DATA_DIR)`, model resolution
// via `resolve_model_spec`, the LLM call via `llm_core::llm_call_async`, events via
// `event_bus::fire_event`, prefs via `routes::prefs_store::load`, and raw session/
// endpoint DB rows via `core::database::session_local()`.

/// `AI_CHAT_TIMEOUT = 120` — seconds for a single per-session LLM call.
const AI_CHAT_TIMEOUT: u64 = 120;
/// `MAX_PIPELINE_STEPS = 10`.
const MAX_PIPELINE_STEPS: usize = 10;

/// Single-key `{"error": msg}` Map — the failure shape every Python `do_*` returns
/// from its early returns (the tool dispatcher appends `exit_code` downstream, so
/// these helpers do NOT add it — matching the Python dicts which carry only the
/// keys the function builds).
fn err(msg: impl Into<String>) -> Map<String, Value> {
    let mut m = Map::new();
    m.insert("error".to_string(), Value::String(msg.into()));
    m
}

/// `if not _session_manager: return {"error": "Session manager not available"}` —
/// reach the concrete global the rest of the port uses. `None` -> the Python error.
fn session_mgr() -> Result<std::sync::Arc<crate::core::session_manager::SessionManager>, Map<String, Value>> {
    crate::src::bg_monitor::get_session_manager()
        .ok_or_else(|| err("Session manager not available"))
}

/// `do_create_session(content, session_id=None, owner=None)` — ai_interaction.py:379-427.
async fn do_create_session(content: &str, owner: Option<&str>) -> Map<String, Value> {
    let sm = match session_mgr() {
        Ok(s) => s,
        Err(e) => return e,
    };

    // lines = content.strip().split("\n")
    let stripped = content.trim();
    let lines: Vec<&str> = stripped.split('\n').collect();
    if lines.len() < 2 {
        return err("Need 2 lines: session name, then model spec");
    }
    let name = lines[0].trim().to_string();
    let model_spec = lines[1].trim().to_string();
    if name.is_empty() {
        return err("Session name cannot be empty");
    }

    // try: url, model, headers = _resolve_model(model_spec, owner=owner) except ValueError: {"error": str(e)}
    let (url, model, headers) = match resolve_model_spec(&model_spec, owner).await {
        Ok(t) => t,
        Err(e) => return err(e.to_string()),
    };

    // sid = str(uuid.uuid4())[:8]
    let sid: String = uuid::Uuid::new_v4().to_string().chars().take(8).collect();
    if let Err(e) = sm.create_session(&sid, &name, &url, &model, false, owner) {
        // except Exception as e: logger.error(...); {"error": f"Failed to create session: {e}"}
        crate::pylog::error(&format!("create_session failed: {e}"));
        return err(format!("Failed to create session: {e}"));
    }
    // sess = get_session(sid); if sess and headers: sess.headers = headers
    if !headers.is_empty() {
        sm.with_session_mut(&sid, |s| {
            s.headers = headers;
        });
    }
    // fire_event("session_created", owner) — best-effort.
    crate::src::event_bus::fire_event("session_created", owner);

    // return {"session_id": sid, "name": name, "model": model, "endpoint_url": url}
    let mut m = Map::new();
    m.insert("session_id".to_string(), Value::String(sid));
    m.insert("name".to_string(), Value::String(name));
    m.insert("model".to_string(), Value::String(model));
    m.insert("endpoint_url".to_string(), Value::String(url));
    m
}

/// `_rel(ts)` (ai_interaction.py:475-489) — relative-time string for a stored
/// SQLite datetime. `None`/unparseable handled by the caller; here `ts` is the
/// already-parsed naive-UTC value, compared against `datetime.utcnow()`.
fn rel_time(ts: &chrono::NaiveDateTime) -> String {
    let now = crate::pydatetime::utcnow_naive();
    let diff = (now - *ts).num_seconds() as f64;
    if diff < 60.0 {
        "just now".to_string()
    } else if diff < 3600.0 {
        format!("{}m ago", (diff / 60.0) as i64)
    } else if diff < 86400.0 {
        format!("{}h ago", (diff / 3600.0) as i64)
    } else if diff < 86400.0 * 7.0 {
        format!("{}d ago", (diff / 86400.0) as i64)
    } else {
        ts.format("%Y-%m-%d").to_string()
    }
}

/// Parse a stored SQLite datetime string into a naive-UTC datetime (mirrors the
/// ORM decode `_rel` receives). Returns `None` on an unparseable / NULL value.
fn parse_db_ts(s: &str) -> Option<chrono::NaiveDateTime> {
    use chrono::NaiveDateTime;
    NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S%.f")
        .or_else(|_| NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S"))
        .or_else(|_| NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S%.f"))
        .or_else(|_| NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S"))
        .ok()
}

/// `do_list_sessions(content, session_id=None, owner=None)` — ai_interaction.py:430-514.
async fn do_list_sessions(content: &str, owner: Option<&str>) -> Map<String, Value> {
    let sm = match session_mgr() {
        Ok(s) => s,
        Err(e) => return e,
    };

    // keyword = content.strip().lower() if content.strip() else None
    let keyword: Option<String> = {
        let s = content.trim();
        if s.is_empty() { None } else { Some(s.to_lowercase()) }
    };

    // Pull every session's timestamps from the DB (the Python `db.query(DbSession).all()`
    // -> {id: row} map). The whole body is the Python `try:` — any DB error becomes
    // {"error": str(e)}.
    let ts_by_id: std::collections::HashMap<String, Option<chrono::NaiveDateTime>> = {
        let conn = match crate::core::database::session_local() {
            Ok(c) => c,
            Err(e) => {
                crate::pylog::error(&format!("list_sessions failed: {e}"));
                return err(e.to_string());
            }
        };
        let mut stmt = match conn.prepare(
            "SELECT id, last_accessed, updated_at, created_at FROM sessions",
        ) {
            Ok(s) => s,
            Err(e) => {
                crate::pylog::error(&format!("list_sessions failed: {e}"));
                return err(e.to_string());
            }
        };
        let mapped = stmt.query_map([], |r| {
            let id: String = r.get(0)?;
            let la: Option<String> = r.get(1)?;
            let ua: Option<String> = r.get(2)?;
            let ca: Option<String> = r.get(3)?;
            Ok((id, la, ua, ca))
        });
        let rows = match mapped {
            Ok(it) => it.collect::<rusqlite::Result<Vec<_>>>(),
            Err(e) => {
                crate::pylog::error(&format!("list_sessions failed: {e}"));
                return err(e.to_string());
            }
        };
        let rows = match rows {
            Ok(r) => r,
            Err(e) => {
                crate::pylog::error(&format!("list_sessions failed: {e}"));
                return err(e.to_string());
            }
        };
        rows.into_iter()
            .map(|(id, la, ua, ca)| {
                // ts = last_accessed or updated_at or created_at (first truthy)
                let pick = la
                    .filter(|s| !s.is_empty())
                    .or(ua.filter(|s| !s.is_empty()))
                    .or(ca.filter(|s| !s.is_empty()));
                (id, pick.as_deref().and_then(parse_db_ts))
            })
            .collect()
    };

    // sessions = get_sessions_for_user(owner)  (owner-scoped, in-memory, insertion order)
    let sessions = sm.get_sessions_for_user(owner);
    let mut rows: Vec<(Option<chrono::NaiveDateTime>, String, crate::core::models::Session)> =
        Vec::new();
    for (sid, sess) in sessions.into_iter() {
        if let Some(kw) = &keyword {
            if !sess.name.to_lowercase().contains(kw) {
                continue;
            }
        }
        let ts = ts_by_id.get(&sid).cloned().flatten();
        rows.push((ts, sid, sess));
    }

    // rows.sort(key=lambda r: r[0] or datetime.min, reverse=True)
    // None sinks to the bottom (== datetime.min on a DESC sort). Stable sort keeps
    // the original (insertion) order among equal keys, matching Python's stable sort.
    rows.sort_by(|a, b| {
        let ka = a.0.unwrap_or(chrono::NaiveDateTime::MIN);
        let kb = b.0.unwrap_or(chrono::NaiveDateTime::MIN);
        kb.cmp(&ka)
    });

    let total = rows.len();
    let mut lines: Vec<String> = Vec::new();
    for (i, (ts, sid, sess)) in rows.iter().enumerate() {
        if i >= 50 {
            lines.push(format!("... and {} more (showing first 50)", total - 50));
            break;
        }
        // safe_name = (name or "Untitled").replace("[","\\[").replace("]","\\]")
        let raw_name = if sess.name.is_empty() { "Untitled" } else { sess.name.as_str() };
        let safe_name = raw_name.replace('[', "\\[").replace(']', "\\]");
        let msg_count = sess.message_count;
        // model = getattr(sess, "model", "unknown") — the default fires only on a
        // MISSING attribute; `Session.model` always exists, so an empty model
        // renders as the empty string (not the word "unknown").
        let model = sess.model.as_str();
        let marker = if i == 0 { " ← most recent" } else { "" };
        let rel = match ts {
            Some(t) => rel_time(t),
            None => "never".to_string(),
        };
        lines.push(format!(
            "- **[{safe_name}](#session-{sid})** (id: `{sid}`, model: {model}, {msg_count} msgs, last active {rel}){marker}"
        ));
    }

    if lines.is_empty() {
        let suffix = match &keyword {
            Some(kw) => format!(" matching '{kw}'"),
            None => String::new(),
        };
        return out("results", format!("No sessions found{suffix}."));
    }

    out(
        "results",
        format!(
            "Found {total} session(s), sorted most-recent first:\n{}\n\nAssistant: when replying to the user, preserve the chat-title markdown links exactly as shown, e.g. `[Chat](#session-id)`. Do not rewrite this as a plain, non-clickable table.",
            lines.join("\n")
        ),
    )
}

/// Single-key `{key: msg}` Map builder for the `{"results": ...}` success shape.
fn out(key: &str, msg: impl Into<String>) -> Map<String, Value> {
    let mut m = Map::new();
    m.insert(key.to_string(), Value::String(msg.into()));
    m
}

/// `do_send_to_session(content, session_id=None, owner=None)` — ai_interaction.py:517-573.
async fn do_send_to_session(content: &str, owner: Option<&str>) -> Map<String, Value> {
    use crate::core::models::ChatMessage;
    let sm = match session_mgr() {
        Ok(s) => s,
        Err(e) => return e,
    };

    // lines = content.strip().split("\n", 1)
    let stripped = content.trim();
    let mut it = stripped.splitn(2, '\n');
    let target_sid = it.next().unwrap_or("").trim().to_string();
    let rest = it.next();
    if rest.is_none() {
        return err("Need 2 lines: session_id, then message");
    }
    let message = rest.unwrap_or("").trim().to_string();

    // sess = get_session(sid); if not sess: {"error": f"Session '{sid}' not found"}
    let sess = match sm.get_session(&target_sid) {
        Ok(s) => s,
        Err(_) => return err(format!("Session '{target_sid}' not found")),
    };

    // Owner-scope: reject access to another user's session.
    //   if owner and getattr(sess, "owner", None) and sess.owner != owner:
    //       return {"error": f"Session '{target_sid}' not found"}
    // All three are Python-truthy: `owner` non-empty, `sess.owner` Some+non-empty,
    // and the two differ. An empty-string owner (auth-disabled mode) is falsy, so
    // the scope check is skipped — matching Python.
    if let Some(o) = owner.filter(|o| !o.is_empty()) {
        if let Some(sess_owner) = sess.owner.as_deref().filter(|s| !s.is_empty()) {
            if sess_owner != o {
                return err(format!("Session '{target_sid}' not found"));
            }
        }
    }

    if message.is_empty() {
        return err("No message provided");
    }

    // context = sess.get_context_messages() + [{"role":"user","content":message}]
    let mut context = sess.get_context_messages();
    context.push(json!({"role": "user", "content": message}));

    match crate::src::llm_core::llm_call_async(
        &sess.endpoint_url,
        &sess.model,
        context,
        crate::src::llm_core::LLMConfig::DEFAULT_TEMPERATURE,
        crate::src::llm_core::LLMConfig::DEFAULT_MAX_TOKENS,
        sess.headers.clone(),
        AI_CHAT_TIMEOUT,
    )
    .await
    {
        Ok(mut response) => {
            // sess.add_message(ChatMessage("user", message)); sess.add_message(... "assistant", response)
            // (persists via the manager — mirrors the two Python sess.add_message calls)
            let _ = sm.add_message(&target_sid, ChatMessage::new("user", message, None));
            let _ = sm.add_message(&target_sid, ChatMessage::new("assistant", response.clone(), None));
            // if len(response) > 10000: response = response[:10000] + "\n... (truncated)"
            if response.chars().count() > 10000 {
                let head: String = response.chars().take(10000).collect();
                response = format!("{head}\n... (truncated)");
            }
            let mut m = Map::new();
            m.insert("session_id".to_string(), Value::String(target_sid));
            m.insert("session_name".to_string(), Value::String(sess.name.clone()));
            m.insert("response".to_string(), Value::String(response));
            m
        }
        Err(e) => {
            crate::pylog::error(&format!("send_to_session failed: {e}"));
            err(format!("Failed to send to session: {e}"))
        }
    }
}

/// `do_pipeline(content, session_id=None, owner=None)` — ai_interaction.py:587-693.
/// `owner` is threaded into each step's `_resolve_model` so a pipeline only
/// resolves the caller's own (or shared) endpoints (upstream 5f58f9a).
async fn do_pipeline(content: &str, owner: Option<&str>) -> Map<String, Value> {
    // Try JSON parse first: {"steps": [...]} or a top-level list.
    let mut steps: Option<Vec<Value>> = None;
    if let Ok(data) = serde_json::from_str::<Value>(content.trim()) {
        if let Some(obj) = data.as_object() {
            if let Some(s) = obj.get("steps").and_then(Value::as_array) {
                steps = Some(s.clone());
            }
        } else if let Some(arr) = data.as_array() {
            steps = Some(arr.clone());
        }
    }

    // Fall back to line format: model | instruction.
    // Python `if not steps:` is truthy for BOTH `None` and an EMPTY list, so an
    // empty `{"steps": []}` / top-level `[]` falls through to the line parse
    // (ai_interaction.py:609) rather than short-circuiting to the
    // "No pipeline steps provided" error.
    if steps.as_ref().map(|s| s.is_empty()).unwrap_or(true) {
        let mut parsed: Vec<Value> = Vec::new();
        for line in content.trim().split('\n') {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            if let Some((model, instr)) = line.split_once('|') {
                parsed.push(json!({"model": model.trim(), "instruction": instr.trim()}));
            } else {
                return err("Each line must be: model | instruction (or use JSON format)");
            }
        }
        steps = Some(parsed);
    }

    let steps = steps.unwrap_or_default();
    if steps.is_empty() {
        return err("No pipeline steps provided");
    }
    if steps.len() > MAX_PIPELINE_STEPS {
        return err(format!("Maximum {MAX_PIPELINE_STEPS} steps allowed"));
    }

    // Resolve all models first (fail fast).
    let mut resolved: Vec<(String, String, indexmap::IndexMap<String, String>, String)> = Vec::new();
    for (i, step) in steps.iter().enumerate() {
        let model_spec = step.get("model").and_then(Value::as_str).unwrap_or("").trim().to_string();
        let instruction = step.get("instruction").and_then(Value::as_str).unwrap_or("").trim().to_string();
        if model_spec.is_empty() || instruction.is_empty() {
            return err(format!("Step {}: both 'model' and 'instruction' are required", i + 1));
        }
        match resolve_model_spec(&model_spec, owner).await {
            Ok((url, model, headers)) => resolved.push((url, model, headers, instruction)),
            Err(e) => return err(format!("Step {}: {e}", i + 1)),
        }
    }

    // Execute the pipeline sequentially.
    let mut step_outputs: Vec<Value> = Vec::new();
    let mut previous_output: Option<String> = None;

    for (i, (url, model, headers, instruction)) in resolved.iter().enumerate() {
        let user_content = match &previous_output {
            Some(prev) => format!("Previous step's output:\n\n{prev}\n\nYour task: {instruction}"),
            None => instruction.clone(),
        };
        let messages = vec![
            json!({"role": "system", "content": format!("You are step {} in a processing pipeline. {instruction}", i + 1)}),
            json!({"role": "user", "content": user_content}),
        ];
        match crate::src::llm_core::llm_call_async(
            url,
            model,
            messages,
            crate::src::llm_core::LLMConfig::DEFAULT_TEMPERATURE,
            crate::src::llm_core::LLMConfig::DEFAULT_MAX_TOKENS,
            headers.clone(),
            AI_CHAT_TIMEOUT,
        )
        .await
        {
            Ok(response) => {
                let output: String = if response.chars().count() > 5000 {
                    response.chars().take(5000).collect()
                } else {
                    response.clone()
                };
                step_outputs.push(json!({
                    "step": (i + 1) as i64,
                    "model": model,
                    "instruction": instruction,
                    "output": output,
                }));
                previous_output = Some(response);
            }
            Err(e) => {
                crate::pylog::error(&format!("pipeline failed at step {}: {e}", step_outputs.len() + 1));
                return err(format!("Pipeline failed at step {}: {e}", step_outputs.len() + 1));
            }
        }
    }

    // Build readable result.
    let mut result_lines: Vec<String> = vec![format!("# Pipeline Results ({} steps)\n", resolved.len())];
    for so in &step_outputs {
        let step = so.get("step").and_then(Value::as_i64).unwrap_or(0);
        let model = so.get("model").and_then(Value::as_str).unwrap_or("");
        let instruction = so.get("instruction").and_then(Value::as_str).unwrap_or("");
        let output = so.get("output").and_then(Value::as_str).unwrap_or("");
        result_lines.push(format!("## Step {step}: {model}"));
        result_lines.push(format!("*Instruction: {instruction}*\n"));
        result_lines.push(output.to_string());
        result_lines.push("\n---\n".to_string());
    }

    let mut m = Map::new();
    m.insert("results".to_string(), Value::String(result_lines.join("\n")));
    m.insert("steps".to_string(), Value::Array(step_outputs));
    m.insert(
        "final_output".to_string(),
        previous_output.map(Value::String).unwrap_or(Value::Null),
    );
    m
}

/// Run an owner-scoped session lookup: `SELECT <cols> FROM sessions WHERE id=?
/// [AND owner=?]`. The owner filter is applied only when `owner is not None`
/// (mirrors the Python `if owner is not None: query.filter(owner == owner)`).
fn session_query_row<T, F>(
    conn: &rusqlite::Connection,
    cols: &str,
    target_sid: &str,
    owner: Option<&str>,
    map: F,
) -> rusqlite::Result<Option<T>>
where
    F: FnOnce(&rusqlite::Row) -> rusqlite::Result<T>,
{
    let res = match owner {
        Some(o) => {
            let sql = format!("SELECT {cols} FROM sessions WHERE id = ?1 AND owner = ?2");
            conn.query_row(&sql, rusqlite::params![target_sid, o], map)
        }
        None => {
            let sql = format!("SELECT {cols} FROM sessions WHERE id = ?1");
            conn.query_row(&sql, rusqlite::params![target_sid], map)
        }
    };
    match res {
        Ok(v) => Ok(Some(v)),
        Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
        Err(e) => Err(e),
    }
}

/// `do_manage_session(content, session_id=None, owner=None)` — ai_interaction.py:693-911.
async fn do_manage_session(
    content: &str,
    session_id: Option<&str>,
    owner: Option<&str>,
) -> Map<String, Value> {
    use crate::core::models::ChatMessage;
    let sm = match session_mgr() {
        Ok(s) => s,
        Err(e) => return e,
    };

    // Accept BOTH JSON args and the legacy line format. Each branch computes the
    // full (action, target_sid, value, list_filter) tuple — no pre-init.
    let raw = content.trim();
    let parsed: Option<Value> = if raw.starts_with('{') {
        serde_json::from_str(raw).ok()
    } else {
        None
    };
    let (action, mut target_sid, value, list_filter): (String, String, Option<String>, String) =
        if let Some(Value::Object(obj)) = parsed.filter(|v| v.is_object()) {
            let action = obj.get("action").and_then(Value::as_str).unwrap_or("").trim().to_lowercase();
            let target_sid = obj
                .get("session_id")
                .or_else(|| obj.get("session"))
                .or_else(|| obj.get("id"))
                .and_then(Value::as_str)
                .unwrap_or("")
                .trim()
                .to_string();
            // _v = value; if None: name or new_name or title or keep_count
            let v = obj.get("value").filter(|v| !v.is_null()).cloned().or_else(|| {
                obj.get("name")
                    .or_else(|| obj.get("new_name"))
                    .or_else(|| obj.get("title"))
                    .or_else(|| obj.get("keep_count"))
                    .filter(|v| !v.is_null())
                    .cloned()
            });
            let value = v.map(|v| json_to_str(&v).trim().to_string());
            let list_filter = obj.get("filter").and_then(Value::as_str).unwrap_or("").trim().to_string();
            (action, target_sid, value, list_filter)
        } else {
            let lines: Vec<&str> = raw.split('\n').collect();
            if lines.is_empty() || lines[0].trim().is_empty() {
                return err("Missing action (rename|archive|delete|important|truncate|fork|list|switch)");
            }
            let action = lines[0].trim().to_lowercase();
            let target_sid = lines.get(1).map(|l| l.trim().to_string()).unwrap_or_default();
            let value = lines.get(2).map(|l| l.trim().to_string());
            let list_filter = lines[1..].join("\n").trim().to_string();
            (action, target_sid, value, list_filter)
        };

    if action.is_empty() {
        return err("Missing action (rename|archive|delete|important|truncate|fork|list|switch)");
    }

    // `list` alias -> do_list_sessions.
    if action == "list" {
        return do_list_sessions(&list_filter, owner).await;
    }

    if target_sid.is_empty() {
        return err("Need a session_id (or 'current' for the active chat)");
    }

    // Allow "current" to refer to the active session.
    if target_sid.eq_ignore_ascii_case("current") {
        if let Some(sid) = session_id {
            target_sid = sid.to_string();
        }
    }

    // switch / open / select / view -> a clickable anchor link.
    if matches!(action.as_str(), "switch" | "open" | "select" | "view") {
        let conn = match crate::core::database::session_local() {
            Ok(c) => c,
            Err(e) => return err(e.to_string()),
        };
        let name = match session_query_row(&conn, "name", &target_sid, owner, |r| r.get::<_, Option<String>>(0)) {
            Ok(Some(n)) => n.filter(|s| !s.is_empty()).unwrap_or_else(|| target_sid.clone()),
            Ok(None) => {
                return err(format!(
                    "Session '{target_sid}' not found. Use list_sessions and pass the exact id it returned."
                ));
            }
            Err(e) => return err(e.to_string()),
        };
        let mut m = Map::new();
        m.insert("action".to_string(), Value::String(action.clone()));
        m.insert("session_id".to_string(), Value::String(target_sid.clone()));
        m.insert("name".to_string(), Value::String(name.clone()));
        m.insert(
            "results".to_string(),
            Value::String(format!("[{name}](#session-{target_sid}) — click to open.")),
        );
        return m;
    }

    // The remaining actions all open a DB connection (Python's `db = SessionLocal()`);
    // the whole block is the Python `try:` -> {"error": str(e)} on any DB error.
    let conn = match crate::core::database::session_local() {
        Ok(c) => c,
        Err(e) => return err(e.to_string()),
    };

    let not_found = || -> Map<String, Value> {
        err(format!(
            "Session '{target_sid}' not found. Use list_sessions and pass the exact id it returned."
        ))
    };

    match action.as_str() {
        "rename" => {
            let new_name = match value.as_deref().filter(|v| !v.is_empty()) {
                Some(v) => v.to_string(),
                None => return err("rename needs a new name (the `value` arg, or line 3 in the legacy format)"),
            };
            match session_query_row(&conn, "id", &target_sid, owner, |r| r.get::<_, String>(0)) {
                Ok(Some(_)) => {}
                Ok(None) => return not_found(),
                Err(e) => return err(e.to_string()),
            }
            if let Err(e) = conn.execute(
                "UPDATE sessions SET name = ?1 WHERE id = ?2",
                rusqlite::params![new_name, target_sid],
            ) {
                return err(e.to_string());
            }
            let _ = sm.update_session_name(&target_sid, &new_name);
            let mut m = Map::new();
            m.insert("action".to_string(), Value::String("rename".to_string()));
            m.insert("session_id".to_string(), Value::String(target_sid.clone()));
            m.insert("name".to_string(), Value::String(new_name.clone()));
            m.insert("results".to_string(), Value::String(format!("Session renamed to '{new_name}'")));
            m
        }
        "archive" | "unarchive" => {
            let archived = action == "archive";
            let name = match session_query_row(&conn, "name", &target_sid, owner, |r| r.get::<_, Option<String>>(0)) {
                Ok(Some(n)) => n.unwrap_or_default(),
                Ok(None) => return not_found(),
                Err(e) => return err(e.to_string()),
            };
            if let Err(e) = conn.execute(
                "UPDATE sessions SET archived = ?1 WHERE id = ?2",
                rusqlite::params![archived as i64, target_sid],
            ) {
                return err(e.to_string());
            }
            let verb = if archived { "archived" } else { "unarchived" };
            let mut m = Map::new();
            m.insert("action".to_string(), Value::String(action.clone()));
            m.insert("session_id".to_string(), Value::String(target_sid.clone()));
            m.insert("results".to_string(), Value::String(format!("Session '{name}' {verb}")));
            m
        }
        "delete" => {
            if Some(target_sid.as_str()) == session_id {
                return err("Cannot delete the current session while chatting in it. Delete other sessions first.");
            }
            let (name, is_important) = match session_query_row(
                &conn,
                "name, is_important",
                &target_sid,
                owner,
                |r| Ok((r.get::<_, Option<String>>(0)?, r.get::<_, i64>(1)?)),
            ) {
                Ok(Some((n, imp))) => (n, imp != 0),
                Ok(None) => {
                    return err(format!(
                        "Session '{target_sid}' not found. Refusing to delete an unknown chat id; use the exact id from list_sessions."
                    ));
                }
                Err(e) => return err(e.to_string()),
            };
            if is_important {
                return err(format!(
                    "Session '{}' is starred/favorited. Unstar it first before deleting.",
                    name.clone().unwrap_or_default()
                ));
            }
            if !sm.delete_session(&target_sid) {
                return err(format!("Session '{target_sid}' was not deleted because it no longer exists."));
            }
            let label = name.filter(|s| !s.is_empty()).unwrap_or_else(|| target_sid.clone());
            let mut m = Map::new();
            m.insert("action".to_string(), Value::String("delete".to_string()));
            m.insert("session_id".to_string(), Value::String(target_sid.clone()));
            m.insert("results".to_string(), Value::String(format!("Session '{label}' deleted")));
            m
        }
        "important" | "unimportant" => {
            let is_important = action == "important";
            let (name, cur_important) = match session_query_row(
                &conn,
                "name, is_important",
                &target_sid,
                owner,
                |r| Ok((r.get::<_, Option<String>>(0)?, r.get::<_, i64>(1)?)),
            ) {
                Ok(Some((n, imp))) => (n.unwrap_or_default(), imp != 0),
                Ok(None) => return not_found(),
                Err(e) => return err(e.to_string()),
            };
            // Prevent the AI from unstarring user-starred sessions.
            if !is_important && cur_important {
                return err(format!(
                    "Session '{name}' is starred by the user. Only the user can unstar sessions manually."
                ));
            }
            if let Err(e) = conn.execute(
                "UPDATE sessions SET is_important = ?1 WHERE id = ?2",
                rusqlite::params![is_important as i64, target_sid],
            ) {
                return err(e.to_string());
            }
            let status = if is_important { "marked as important" } else { "unmarked as important" };
            let mut m = Map::new();
            m.insert("action".to_string(), Value::String(action.clone()));
            m.insert("session_id".to_string(), Value::String(target_sid.clone()));
            m.insert("results".to_string(), Value::String(format!("Session '{name}' {status}")));
            m
        }
        "truncate" => {
            match session_query_row(&conn, "id", &target_sid, owner, |r| r.get::<_, String>(0)) {
                Ok(Some(_)) => {}
                Ok(None) => return not_found(),
                Err(e) => return err(e.to_string()),
            }
            // keep_count default 10; int(value) on failure -> keep default.
            let keep_count = value
                .as_deref()
                .filter(|v| !v.is_empty())
                .and_then(|v| v.parse::<i64>().ok())
                .unwrap_or(10);
            match sm.truncate_messages(&target_sid, keep_count) {
                Ok(true) => {
                    let mut m = Map::new();
                    m.insert("action".to_string(), Value::String("truncate".to_string()));
                    m.insert("session_id".to_string(), Value::String(target_sid.clone()));
                    m.insert(
                        "results".to_string(),
                        Value::String(format!("Session truncated to last {keep_count} messages")),
                    );
                    m
                }
                _ => err(format!("Failed to truncate session '{target_sid}'")),
            }
        }
        "fork" => {
            match session_query_row(&conn, "id", &target_sid, owner, |r| r.get::<_, String>(0)) {
                Ok(Some(_)) => {}
                Ok(None) => return not_found(),
                Err(e) => return err(e.to_string()),
            }
            // keep_count default 0 (= all); int(value) failure -> default.
            let keep_count = value
                .as_deref()
                .filter(|v| !v.is_empty())
                .and_then(|v| v.parse::<i64>().ok())
                .unwrap_or(0);

            // source = get_session(sid); if not source: {"error": f"Session '{sid}' not found"}
            let source = match sm.get_session(&target_sid) {
                Ok(s) => s,
                Err(_) => return err(format!("Session '{target_sid}' not found")),
            };

            let new_sid: String = uuid::Uuid::new_v4().to_string().chars().take(8).collect();
            if let Err(e) = sm.create_session(
                &new_sid,
                &format!("Fork: {}", source.name),
                &source.endpoint_url,
                &source.model,
                false,
                owner,
            ) {
                return err(e.to_string());
            }
            // Copy messages.
            let mut history = source.get_context_messages();
            if keep_count > 0 {
                history.truncate(keep_count as usize);
            }
            let copied = history.len();
            for msg in &history {
                let role = msg.get("role").and_then(Value::as_str).unwrap_or("");
                let content_m = msg.get("content").and_then(Value::as_str).unwrap_or("");
                let _ = sm.add_message(&new_sid, ChatMessage::new(role, content_m, None));
            }
            crate::src::event_bus::fire_event("session_created", owner);

            let mut m = Map::new();
            m.insert("action".to_string(), Value::String("fork".to_string()));
            m.insert("session_id".to_string(), Value::String(new_sid.clone()));
            m.insert("source_session".to_string(), Value::String(target_sid.clone()));
            m.insert("messages_copied".to_string(), Value::from(copied as i64));
            m.insert(
                "results".to_string(),
                Value::String(format!(
                    "Forked session '{}' -> new session {new_sid} ({copied} messages)",
                    source.name
                )),
            );
            m
        }
        other => err(format!(
            "Unknown action '{other}'. Use: list, switch, rename, archive, unarchive, delete, important, unimportant, truncate, fork"
        )),
    }
}

/// `str(value)` for a JSON value — Python `str()` of an arbitrary `value` arg in
/// `do_manage_session`. A JSON string passes through verbatim; numbers/bools use
/// their Python-`str` rendering; other types fall back to compact JSON.
fn json_to_str(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Bool(b) => if *b { "True".to_string() } else { "False".to_string() },
        Value::Number(n) => n.to_string(),
        Value::Null => "None".to_string(),
        other => other.to_string(),
    }
}

/// `do_manage_memory(content, session_id=None, owner=None)` — ai_interaction.py:920-1080.
async fn do_manage_memory(content: &str, owner: Option<&str>) -> Map<String, Value> {
    use crate::src::memory::MemoryManager;
    // if not _memory_manager: {"error": "Memory manager not available"}
    // The Rust manager is a stateless disk wrapper (data/memory.json); a
    // construction error maps onto the Python "not available" guard.
    let mgr = match MemoryManager::new(&crate::core::constants::DATA_DIR) {
        Ok(m) => m,
        Err(_) => return err("Memory manager not available"),
    };

    let stripped = content.trim();
    let lines: Vec<&str> = stripped.split('\n').collect();
    // `if not lines:` — split always yields >= 1 element, so this guard is dead, as
    // in Python (str.split never returns []).
    let action = lines.first().map(|l| l.trim().to_lowercase()).unwrap_or_default();

    match action.as_str() {
        "list" => {
            // category_filter = lines[1].strip().lower() if len>1 and lines[1].strip() else None
            let category_filter: Option<String> = lines
                .get(1)
                .map(|l| l.trim())
                .filter(|l| !l.is_empty())
                .map(|l| l.to_lowercase());
            let mut memories = mgr.load(owner);
            if let Some(cat) = &category_filter {
                memories.retain(|m| {
                    m.get("category").and_then(Value::as_str).unwrap_or("").to_lowercase() == *cat
                });
            }
            if memories.is_empty() {
                let suffix = category_filter
                    .map(|c| format!(" in category '{c}'"))
                    .unwrap_or_default();
                return out("results", format!("No memories found{suffix}."));
            }
            let mut result_lines: Vec<String> = vec![format!("Found {} memory entries:\n", memories.len())];
            for m in memories.iter().take(100) {
                let cat = m.get("category").and_then(Value::as_str).unwrap_or("fact");
                let mid: String = m.get("id").and_then(Value::as_str).unwrap_or("?").chars().take(8).collect();
                let mut text = m.get("text").and_then(Value::as_str).unwrap_or("").to_string();
                if text.chars().count() > 150 {
                    let head: String = text.chars().take(150).collect();
                    text = format!("{head}...");
                }
                result_lines.push(format!("- [{cat}] `{mid}` — {text}"));
            }
            if memories.len() > 100 {
                result_lines.push(format!("... and {} more", memories.len() - 100));
            }
            out("results", result_lines.join("\n"))
        }
        "add" => {
            if lines.len() < 2 {
                return err("Add needs line 2: memory text");
            }
            let text = lines[1].trim().to_string();
            let category = lines
                .get(2)
                .map(|l| l.trim())
                .filter(|l| !l.is_empty())
                .map(|l| l.to_lowercase())
                .unwrap_or_else(|| "fact".to_string());
            if text.is_empty() {
                return err("Memory text cannot be empty");
            }
            // entry = add_entry(text, source="ai_agent", category, owner); load_all -> push -> save
            let entry = match mgr.add_entry(&text, "ai_agent", &category, owner) {
                Ok(e) => e,
                Err(e) => return err(e.to_string()),
            };
            let mut all = mgr.load_all();
            all.push(entry.clone());
            if let Err(e) = mgr.save(&mut all) {
                return err(e.to_string());
            }
            let memory_id = entry.get("id").and_then(Value::as_str).unwrap_or("").to_string();
            // `if _memory_vector and ...: _memory_vector.add(entry["id"], text)`
            // (ai_interaction.py:976-980) — keep the ChromaDB index in sync via the
            // global store; side-effect-only (does NOT affect result_map/exit_code).
            if let Some(mv) = crate::src::memory_vector::memory_vector() {
                if mv.healthy() {
                    let _ = mv.add(&memory_id, &text).await;
                }
            }
            crate::src::event_bus::fire_event("memory_added", owner);
            let mut m = Map::new();
            m.insert("action".to_string(), Value::String("add".to_string()));
            m.insert("memory_id".to_string(), Value::String(memory_id));
            m.insert("results".to_string(), Value::String(format!("Memory added: [{category}] {text}")));
            m
        }
        "edit" => {
            if lines.len() < 3 {
                return err("Edit needs line 2: memory_id, line 3: new text");
            }
            let memory_id = lines[1].trim().to_string();
            let new_text = lines[2].trim().to_string();
            if new_text.is_empty() {
                return err("New text cannot be empty");
            }
            let mut memories = mgr.load_all();
            let mut found = false;
            for m in memories.iter_mut() {
                let id = m.get("id").and_then(Value::as_str).unwrap_or("");
                if id.starts_with(&memory_id) {
                    // Verify ownership.
                    if let Some(o) = owner.filter(|o| !o.is_empty()) {
                        if m.get("owner").and_then(Value::as_str) != Some(o) {
                            return err(format!("Memory '{memory_id}' not found"));
                        }
                    }
                    if let Some(obj) = m.as_object_mut() {
                        obj.insert("text".to_string(), Value::String(new_text.clone()));
                        obj.insert("timestamp".to_string(), Value::from(now_unix()));
                    }
                    found = true;
                    break;
                }
            }
            if !found {
                return err(format!("Memory '{memory_id}' not found"));
            }
            if let Err(e) = mgr.save(&mut memories) {
                return err(e.to_string());
            }
            // `if _memory_vector and ...: _memory_vector.add(full_id, new_text)`
            // (ai_interaction.py:1015-1019) — re-index with the new text.
            if let Some(mv) = crate::src::memory_vector::memory_vector() {
                if mv.healthy() {
                    let _ = mv.add(&memory_id, &new_text).await;
                }
            }
            let mut m = Map::new();
            m.insert("action".to_string(), Value::String("edit".to_string()));
            m.insert("memory_id".to_string(), Value::String(memory_id));
            m.insert("results".to_string(), Value::String(format!("Memory updated: {new_text}")));
            m
        }
        "delete" => {
            if lines.len() < 2 {
                return err("Delete needs line 2: memory_id");
            }
            let memory_id = lines[1].trim().to_string();
            let mut memories = mgr.load_all();
            let original_len = memories.len();
            let mut delete_id: Option<String> = None;
            for m in &memories {
                let id = m.get("id").and_then(Value::as_str).unwrap_or("");
                if id.starts_with(&memory_id) {
                    if let Some(o) = owner.filter(|o| !o.is_empty()) {
                        if m.get("owner").and_then(Value::as_str) != Some(o) {
                            return err(format!("Memory '{memory_id}' not found"));
                        }
                    }
                    delete_id = Some(id.to_string());
                    break;
                }
            }
            if let Some(did) = &delete_id {
                memories.retain(|m| m.get("id").and_then(Value::as_str) != Some(did.as_str()));
            }
            if memories.len() == original_len {
                return err(format!("Memory '{memory_id}' not found"));
            }
            if let Err(e) = mgr.save(&mut memories) {
                return err(e.to_string());
            }
            // `if _memory_vector and ...: _memory_vector.remove(full_id)`
            // (ai_interaction.py:1047-1051) — drop the entry from the ChromaDB index.
            if let Some(mv) = crate::src::memory_vector::memory_vector() {
                if mv.healthy() {
                    if let Some(did) = &delete_id {
                        mv.remove(did);
                    }
                }
            }
            let mut m = Map::new();
            m.insert("action".to_string(), Value::String("delete".to_string()));
            m.insert("memory_id".to_string(), Value::String(memory_id.clone()));
            m.insert("results".to_string(), Value::String(format!("Memory '{memory_id}' deleted")));
            m
        }
        "search" => {
            if lines.len() < 2 {
                return err("Search needs line 2: query");
            }
            let query = lines[1].trim().to_string();
            let memories = mgr.load(owner);
            // get_relevant_memories(query, memories, threshold=0.05, max_items=20)
            let results = mgr.get_relevant_memories(&query, &memories, 0.05, 20);
            if results.is_empty() {
                return out("results", format!("No memories found matching '{query}'."));
            }
            let mut result_lines: Vec<String> = vec![format!("Found {} matching memories:\n", results.len())];
            for m in &results {
                let cat = m.get("category").and_then(Value::as_str).unwrap_or("fact");
                let mid: String = m.get("id").and_then(Value::as_str).unwrap_or("?").chars().take(8).collect();
                let text = m.get("text").and_then(Value::as_str).unwrap_or("");
                result_lines.push(format!("- [{cat}] `{mid}` — {text}"));
            }
            out("results", result_lines.join("\n"))
        }
        other => err(format!("Unknown action '{other}'. Use: list, add, edit, delete, search")),
    }
}

/// `int(time.time())` — current Unix epoch seconds.
fn now_unix() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

/// `do_list_models(content, session_id=None, owner=None)` — ai_interaction.py:1097-1158.
/// OWNER SCOPE (upstream 5f58f9a): the enabled-endpoint query applies
/// `owner_filter(q, ModelEndpoint, owner)` (raw SQL `(owner = ? OR owner IS NULL)`)
/// when `owner` is truthy, so the tool only lists the caller's own (or shared)
/// endpoints. An empty-string owner is Python-falsy => unscoped.
async fn do_list_models(content: &str, owner: Option<&str>) -> Map<String, Value> {
    use crate::src::endpoint_resolver::{build_headers, build_models_url, normalize_base};
    use crate::src::llm_core::{_detect_provider, ANTHROPIC_MODELS};

    // keyword = content.strip().lower() if content.strip() else None
    let keyword: Option<String> = {
        let s = content.trim();
        if s.is_empty() { None } else { Some(s.to_lowercase()) }
    };
    let owner = owner.filter(|o| !o.is_empty());

    // query = db.query(ModelEndpoint).filter(is_enabled==True)[+owner_filter]; query.all()
    let endpoints: Vec<(Option<String>, String, Option<String>)> = {
        let conn = match crate::core::database::session_local() {
            Ok(c) => c,
            Err(e) => {
                crate::pylog::error(&format!("list_models failed: {e}"));
                return err(e.to_string());
            }
        };
        // Append the owner scope as raw SQL when present (mirrors owner_filter).
        let mut sql =
            String::from("SELECT name, base_url, api_key FROM model_endpoints WHERE is_enabled = 1");
        if owner.is_some() {
            sql.push_str(" AND (owner = ?1 OR owner IS NULL)");
        }
        let mut stmt = match conn.prepare(&sql) {
            Ok(s) => s,
            Err(e) => {
                crate::pylog::error(&format!("list_models failed: {e}"));
                return err(e.to_string());
            }
        };
        let row_map = |r: &rusqlite::Row| -> rusqlite::Result<(Option<String>, String, Option<String>)> {
            Ok((
                r.get::<_, Option<String>>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, Option<String>>(2)?,
            ))
        };
        let mapped = match owner {
            Some(o) => stmt.query_map(rusqlite::params![o], row_map),
            None => stmt.query_map([], row_map),
        };
        let collected = match mapped {
            Ok(it) => it.collect::<rusqlite::Result<Vec<_>>>(),
            Err(e) => {
                crate::pylog::error(&format!("list_models failed: {e}"));
                return err(e.to_string());
            }
        };
        match collected {
            Ok(rows) => rows,
            Err(e) => {
                crate::pylog::error(&format!("list_models failed: {e}"));
                return err(e.to_string());
            }
        }
    };

    if endpoints.is_empty() {
        return out("results", "No enabled model endpoints configured.");
    }

    let mut result_lines: Vec<String> = Vec::new();
    let mut total_models = 0_i64;

    for (name, base_url, enc_key) in &endpoints {
        let base = normalize_base(Some(base_url));
        let provider = _detect_provider(&base);
        // ep.api_key is the DECRYPTED key. headers = build_headers(ep.api_key, base)
        // — provider-correct auth (Anthropic gets x-api-key + anthropic-version).
        let api_key: Option<String> = enc_key
            .as_deref()
            .filter(|k| !k.is_empty())
            .map(crate::src::secret_storage::decrypt);
        let headers = build_headers(api_key.as_deref(), &base);

        let mut model_ids: Vec<String> = if provider == "anthropic" {
            ANTHROPIC_MODELS.iter().map(|s| s.to_string()).collect()
        } else {
            // httpx.get(build_models_url(base), headers, timeout=5); on ANY error -> ["(endpoint offline)"]
            async {
                let mut hmap = reqwest::header::HeaderMap::new();
                for (hk, hv) in &headers {
                    if let (Ok(n), Ok(v)) = (
                        reqwest::header::HeaderName::from_bytes(hk.as_bytes()),
                        reqwest::header::HeaderValue::from_str(hv),
                    ) {
                        hmap.insert(n, v);
                    }
                }
                let client = reqwest::Client::builder()
                    .timeout(std::time::Duration::from_secs(5))
                    .build()
                    .ok()?;
                let resp = client.get(build_models_url(&base)).headers(hmap).send().await.ok()?;
                if !resp.status().is_success() {
                    return None;
                }
                let data: Value = resp.json().await.ok()?;
                // ids = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
                let mut ids: Vec<String> = data
                    .get("data")
                    .and_then(Value::as_array)
                    .map(|arr| {
                        arr.iter()
                            .filter_map(|m| m.get("id").and_then(Value::as_str).map(String::from))
                            .collect::<Vec<String>>()
                    })
                    .unwrap_or_default();
                // Native Ollama returns models[].name (or .model) instead of data[].id.
                //   if not model_ids: model_ids = [m.get("name") or m.get("model")
                //       for m in (data.get("models") or []) if m.get("name") or m.get("model")]
                if ids.is_empty() {
                    ids = data
                        .get("models")
                        .and_then(Value::as_array)
                        .map(|arr| {
                            arr.iter()
                                .filter_map(|m| {
                                    // m.get("name") or m.get("model") — present-but-empty
                                    // "name" falls through to "model" (Python truthiness).
                                    m.get("name").and_then(Value::as_str).filter(|s| !s.is_empty())
                                        .or_else(|| m.get("model").and_then(Value::as_str).filter(|s| !s.is_empty()))
                                        .map(String::from)
                                })
                                .collect::<Vec<String>>()
                        })
                        .unwrap_or_default();
                }
                Some(ids)
            }
            .await
            .unwrap_or_else(|| vec!["(endpoint offline)".to_string()])
        };

        // keyword filter: model.lower() contains kw OR name.lower() contains kw
        if let Some(kw) = &keyword {
            let name_lower = name.as_deref().unwrap_or("").to_lowercase();
            model_ids.retain(|m| m.to_lowercase().contains(kw) || name_lower.contains(kw));
        }

        if !model_ids.is_empty() {
            let label = name.as_deref().filter(|n| !n.is_empty()).unwrap_or(&base);
            result_lines.push(format!("\n**{label}** ({provider}):"));
            for mid in &model_ids {
                result_lines.push(format!("  - `{mid}`"));
                total_models += 1;
            }
        }
    }

    if result_lines.is_empty() {
        let suffix = keyword.map(|kw| format!(" matching '{kw}'")).unwrap_or_default();
        return out("results", format!("No models found{suffix}."));
    }

    let header = format!("Available models ({total_models} total):");
    out("results", header + &result_lines.join("\n"))
}

/// `do_ui_control(content, session_id=None, owner=None)` — ai_interaction.py:1249-1533.
/// `owner` is threaded into the `switch_model` action's `_resolve_model` validation
/// so the UI tool only resolves the caller's own (or shared) endpoints (upstream
/// 5f58f9a).
async fn do_ui_control(content: &str, session_id: Option<&str>, owner: Option<&str>) -> Map<String, Value> {
    let stripped = content.trim();
    let lines: Vec<&str> = stripped.split('\n').collect();
    // `if not lines:` is dead (split yields >= 1) — as in Python.
    let line0 = lines.first().map(|l| l.trim()).unwrap_or("");
    // parts = lines[0].strip().split(None, 2)  (whitespace split, max 3 fields)
    let parts = split_whitespace_n(line0, 3);
    let action = parts.first().map(|s| s.to_lowercase()).unwrap_or_default();

    match action.as_str() {
        "toggle" => {
            if parts.len() < 3 {
                return err("toggle needs: toggle <name> <on|off>");
            }
            let mut toggle_name = parts[1].to_lowercase();
            let state = matches!(
                parts[2].to_lowercase().as_str(),
                "on" | "true" | "1" | "yes" | "enable" | "enabled"
            );
            // Friendly aliases.
            let alias = match toggle_name.as_str() {
                "shell" | "terminal" => Some("bash"),
                "search" | "websearch" | "web_search" => Some("web"),
                "deepresearch" | "deep_research" => Some("research"),
                "documents" | "doc" | "docs" => Some("document_editor"),
                "private" => Some("incognito"),
                _ => None,
            };
            if let Some(a) = alias {
                toggle_name = a.to_string();
            }
            let valid = ["bash", "document_editor", "incognito", "rag", "research", "web"];
            if !valid.contains(&toggle_name.as_str()) {
                return err(format!("Unknown toggle '{toggle_name}'. Valid: {}", valid.join(", ")));
            }
            let mut m = Map::new();
            m.insert("ui_event".to_string(), Value::String("toggle".to_string()));
            m.insert("toggle_name".to_string(), Value::String(toggle_name.clone()));
            m.insert("state".to_string(), Value::Bool(state));
            m.insert(
                "results".to_string(),
                Value::String(format!("Toggle '{toggle_name}' set to {}", if state { "on" } else { "off" })),
            );
            m
        }
        "set_mode" => {
            if parts.len() < 2 {
                return err("set_mode needs: set_mode <agent|chat>");
            }
            let mode = parts[1].to_lowercase();
            if mode != "agent" && mode != "chat" {
                return err(format!("Invalid mode '{mode}'. Use: agent, chat"));
            }
            let mut m = Map::new();
            m.insert("ui_event".to_string(), Value::String("set_mode".to_string()));
            m.insert("mode".to_string(), Value::String(mode.clone()));
            m.insert("results".to_string(), Value::String(format!("Mode changed to '{mode}'")));
            m
        }
        "switch_model" => {
            // model_spec = " ".join(parts[1:]) if len(parts)>1 else ""; fallback to lines[1]
            let mut model_spec = if parts.len() > 1 { parts[1..].join(" ") } else { String::new() };
            if model_spec.is_empty() {
                model_spec = lines.get(1).map(|l| l.trim().to_string()).unwrap_or_default();
            }
            if model_spec.is_empty() {
                return err("switch_model needs a model name");
            }
            // Resolve to validate (owner-scoped: upstream 5f58f9a).
            let (url, model_id, headers) = match resolve_model_spec(&model_spec, owner).await {
                Ok(t) => t,
                Err(e) => return err(e.to_string()),
            };
            // Update the current session's model if we have one.
            if let Some(sid) = session_id {
                if let Some(sm) = crate::src::bg_monitor::get_session_manager() {
                    if let Ok(conn) = crate::core::database::session_local() {
                        let _ = conn.execute(
                            "UPDATE sessions SET endpoint_url = ?1, model = ?2 WHERE id = ?3",
                            rusqlite::params![url, model_id, sid],
                        );
                    }
                    sm.with_session_mut(sid, |s| {
                        s.endpoint_url = url.clone();
                        s.model = model_id.clone();
                        if !headers.is_empty() {
                            s.headers = headers.clone();
                        }
                    });
                }
            }
            let mut m = Map::new();
            m.insert("ui_event".to_string(), Value::String("switch_model".to_string()));
            m.insert("model".to_string(), Value::String(model_id.clone()));
            m.insert("endpoint_url".to_string(), Value::String(url));
            m.insert("results".to_string(), Value::String(format!("Model switched to '{model_id}'")));
            m
        }
        "set_theme" => {
            let theme_name = parts.get(1).map(|s| s.to_lowercase()).unwrap_or_default();
            let known_presets = [
                "dark", "light", "midnight", "paper", "cyberpunk", "retrowave",
                "forest", "ocean", "ume", "copper", "terminal", "organs",
                "lavender", "gpt", "claude", "cute",
            ];
            // custom_themes from prefs (best-effort; {} on missing).
            let prefs = crate::routes::prefs_store::load();
            let mut custom_keys: Vec<String> = prefs
                .get("custom-themes")
                .and_then(Value::as_object)
                .map(|o| o.keys().cloned().collect())
                .unwrap_or_default();
            let is_known = known_presets.contains(&theme_name.as_str()) || custom_keys.contains(&theme_name);
            if !is_known {
                custom_keys.sort();
                let custom_label = if custom_keys.is_empty() {
                    String::new()
                } else {
                    format!(" | Custom: {}", custom_keys.join(", "))
                };
                return err(format!(
                    "Unknown theme '{theme_name}'. Available: {}{custom_label}",
                    sorted_join(&known_presets)
                ));
            }
            let mut m = Map::new();
            m.insert("ui_event".to_string(), Value::String("set_theme".to_string()));
            m.insert("theme_name".to_string(), Value::String(theme_name.clone()));
            m.insert("results".to_string(), Value::String(format!("Theme changed to '{theme_name}'")));
            m
        }
        "create_theme" => {
            // Re-split without limit to get all parts.
            let parts: Vec<String> = line0.split_whitespace().map(String::from).collect();
            if parts.len() < 7 {
                return err("create_theme needs: create_theme <name> <bg> <fg> <panel> <border> <accent> (all hex colors). Optional advanced color key=value pairs (userBubbleBg, aiBubbleBg, bubbleBorder, sidebarBg, sectionAccent, brandColor, inputBg, inputBorder, sendBtnBg, sendBtnHover, codeBg, codeFg, toggleBg, toggleActive, accentPrimary, accentError). Optional background EFFECTS: bgPattern=<none|dots|synapse|rain|constellations|perlin-flow|petals|sparkles|embers>, bgEffectColor=#RRGGBB, bgEffectIntensity=<num e.g. 1>, bgEffectSize=<num e.g. 1>, frosted=true|false");
            }
            let name = parts[1].to_lowercase().replace(' ', "-");
            // colors = {bg, fg, panel, border, red}  (insertion order preserved)
            let mut colors = Map::new();
            colors.insert("bg".to_string(), Value::String(parts[2].clone()));
            colors.insert("fg".to_string(), Value::String(parts[3].clone()));
            colors.insert("panel".to_string(), Value::String(parts[4].clone()));
            colors.insert("border".to_string(), Value::String(parts[5].clone()));
            colors.insert("red".to_string(), Value::String(parts[6].clone()));
            // Validate base hex colors (in insertion order: bg, fg, panel, border, red).
            for k in ["bg", "fg", "panel", "border", "red"] {
                let v = colors.get(k).and_then(Value::as_str).unwrap_or("");
                if !is_hex6(v) {
                    return err(format!("Invalid hex color for {k}: '{v}'. Use format #RRGGBB"));
                }
            }
            let adv_keys = [
                "userBubbleBg", "aiBubbleBg", "bubbleBorder", "sidebarBg",
                "sectionAccent", "brandColor", "inputBg", "inputBorder",
                "sendBtnBg", "sendBtnHover", "codeBg", "codeFg",
                "toggleBg", "toggleActive", "accentPrimary", "accentError",
            ];
            let bg_patterns = [
                "none", "dots", "synapse", "rain", "constellations",
                "perlin-flow", "petals", "sparkles", "embers",
            ];
            let mut advanced = Map::new();
            let mut bg = Map::new();
            for part in &parts[7..] {
                let (ak, av) = match part.split_once('=') {
                    Some(t) => t,
                    None => continue,
                };
                if adv_keys.contains(&ak) {
                    if !is_hex6(av) {
                        return err(format!("Invalid hex color for advanced key {ak}: '{av}'. Use format #RRGGBB"));
                    }
                    advanced.insert(ak.to_string(), Value::String(av.to_string()));
                } else if ak == "bgPattern" {
                    if !bg_patterns.contains(&av) {
                        return err(format!("Invalid bgPattern '{av}'. Use one of: {}", sorted_join(&bg_patterns)));
                    }
                    bg.insert("pattern".to_string(), Value::String(av.to_string()));
                } else if ak == "bgEffectColor" {
                    if !is_hex6(av) {
                        return err(format!("Invalid hex color for bgEffectColor: '{av}'. Use format #RRGGBB"));
                    }
                    bg.insert("effectColor".to_string(), Value::String(av.to_string()));
                } else if ak == "bgEffectIntensity" || ak == "bgEffectSize" {
                    match av.parse::<f64>() {
                        Ok(n) => {
                            let key = if ak == "bgEffectIntensity" { "effectIntensity" } else { "effectSize" };
                            bg.insert(
                                key.to_string(),
                                serde_json::Number::from_f64(n).map(Value::Number).unwrap_or(Value::Null),
                            );
                        }
                        Err(_) => return err(format!("Invalid number for {ak}: '{av}'")),
                    }
                } else if ak == "frosted" {
                    let truthy = matches!(av.to_lowercase().as_str(), "true" | "1" | "yes" | "on");
                    bg.insert("frosted".to_string(), Value::Bool(truthy));
                }
            }
            let adv_len = advanced.len();
            if !advanced.is_empty() {
                colors.insert("advanced".to_string(), Value::Object(advanced));
            }
            // results message: advanced + bg suffixes.
            let mut results = format!("Custom theme '{name}' created and applied");
            if adv_len > 0 {
                results.push_str(&format!(" with {adv_len} advanced overrides"));
            }
            if !bg.is_empty() {
                // bg.get("pattern", "frosted" if bg.get("frosted") else "custom")
                let label = bg
                    .get("pattern")
                    .and_then(Value::as_str)
                    .map(String::from)
                    .unwrap_or_else(|| {
                        if bg.get("frosted").and_then(Value::as_bool).unwrap_or(false) {
                            "frosted".to_string()
                        } else {
                            "custom".to_string()
                        }
                    });
                results.push_str(&format!(" + background effect ({label})"));
            }
            let mut m = Map::new();
            m.insert("ui_event".to_string(), Value::String("create_theme".to_string()));
            m.insert("theme_name".to_string(), Value::String(name));
            m.insert("colors".to_string(), Value::Object(colors));
            // bg or None
            m.insert(
                "bg".to_string(),
                if bg.is_empty() { Value::Null } else { Value::Object(bg) },
            );
            m.insert("results".to_string(), Value::String(results));
            m
        }
        "highlight" => {
            let selector = parts.get(1).cloned().unwrap_or_default();
            let label = if parts.len() > 2 { parts[2..].join(" ") } else { String::new() };
            if selector.is_empty() {
                return err("highlight needs: highlight <css-selector> [label]");
            }
            let mut m = Map::new();
            m.insert("ui_event".to_string(), Value::String("highlight".to_string()));
            m.insert("selector".to_string(), Value::String(selector.clone()));
            m.insert("label".to_string(), Value::String(label));
            m.insert("results".to_string(), Value::String(format!("Highlighting '{selector}'")));
            m
        }
        "clear_highlight" => {
            let mut m = Map::new();
            m.insert("ui_event".to_string(), Value::String("clear_highlight".to_string()));
            m.insert("results".to_string(), Value::String("Highlights cleared".to_string()));
            m
        }
        "open_panel" => {
            let panel = parts.get(1).map(|s| s.to_lowercase()).unwrap_or_default();
            let target = match panel.as_str() {
                "documents" | "document" | "doc" | "docs" | "library" | "doclib" => Some("documents"),
                "gallery" | "images" => Some("gallery"),
                "email" | "emails" | "inbox" | "mail" => Some("email"),
                "sessions" | "chats" | "history" => Some("sessions"),
                "notes" | "note" | "todo" | "todos" => Some("notes"),
                "memories" | "memory" | "brain" => Some("memories"),
                "skills" => Some("skills"),
                "settings" | "preferences" => Some("settings"),
                "cookbook" | "models" | "llm" | "serve" | "serving" => Some("cookbook"),
                _ => None,
            };
            let target = match target {
                Some(t) => t,
                None => {
                    return err(format!(
                        "Unknown panel '{panel}'. Valid: documents, gallery, email, sessions, notes, memories, skills, settings, cookbook."
                    ));
                }
            };
            let mut m = Map::new();
            m.insert("ui_event".to_string(), Value::String("open_panel".to_string()));
            m.insert("panel".to_string(), Value::String(target.to_string()));
            m.insert("results".to_string(), Value::String(format!("Opening {target} panel")));
            m
        }
        "open_email_reply" => {
            // reply_parts = lines[0].strip().split()
            let reply_parts: Vec<String> = line0.split_whitespace().map(String::from).collect();
            let uid = reply_parts.get(1).map(|s| s.trim().to_string()).unwrap_or_default();
            let folder = reply_parts.get(2).map(|s| s.trim().to_string()).unwrap_or_else(|| "INBOX".to_string());
            let mut mode = reply_parts.get(3).map(|s| s.trim().to_lowercase()).unwrap_or_else(|| "reply".to_string());
            if uid.is_empty() {
                return err("open_email_reply needs: open_email_reply <uid> [folder] [reply|reply-all|ai-reply]");
            }
            if !matches!(mode.as_str(), "reply" | "reply-all" | "ai-reply") {
                mode = "reply".to_string();
            }
            let folder = if folder.is_empty() { "INBOX".to_string() } else { folder };
            let mut m = Map::new();
            m.insert("ui_event".to_string(), Value::String("open_email_reply".to_string()));
            m.insert("uid".to_string(), Value::String(uid.clone()));
            m.insert("folder".to_string(), Value::String(folder));
            m.insert("mode".to_string(), Value::String(mode));
            m.insert("results".to_string(), Value::String(format!("Opening reply draft for email UID {uid}")));
            m
        }
        "get_toggles" => out(
            "results",
            "Toggle states are managed client-side in localStorage. Available toggles: web, bash, rag, research, incognito, document_editor. Use 'toggle <name> <on|off>' to change them.",
        ),
        other => err(format!(
            "Unknown action '{other}'. Use: toggle, set_mode, switch_model, set_theme, highlight, clear_highlight, get_toggles"
        )),
    }
}

/// `str.split(None, maxsplit)` — split on runs of ASCII whitespace, keeping at most
/// `maxsplit+1` fields (the final field retains its embedded whitespace, like
/// Python's `split(None, 2)`).
fn split_whitespace_n(s: &str, maxsplit: usize) -> Vec<String> {
    let s = s.trim_start();
    let mut out: Vec<String> = Vec::new();
    let mut rest = s;
    while out.len() < maxsplit {
        rest = rest.trim_start();
        if rest.is_empty() {
            break;
        }
        match rest.find(char::is_whitespace) {
            Some(idx) => {
                out.push(rest[..idx].to_string());
                rest = &rest[idx..];
            }
            None => {
                out.push(rest.to_string());
                rest = "";
                break;
            }
        }
    }
    let rest = rest.trim();
    if !rest.is_empty() {
        out.push(rest.to_string());
    }
    out
}

/// `', '.join(sorted(items))` — sorted, comma-space joined.
fn sorted_join(items: &[&str]) -> String {
    let mut v: Vec<&str> = items.to_vec();
    v.sort_unstable();
    v.join(", ")
}

/// `re.match(r'^#[0-9a-fA-F]{6}$', v)` — exactly `#` + 6 hex digits.
fn is_hex6(v: &str) -> bool {
    let bytes = v.as_bytes();
    bytes.len() == 7 && bytes[0] == b'#' && bytes[1..].iter().all(|b| b.is_ascii_hexdigit())
}

/// `do_generate_image(content, session_id=None, owner=None) -> Dict`
/// — src/ai_interaction.py:1530-1734.
///
/// Generates an image using an image-capable model. `content` is the
/// newline-joined `prompt\nmodel\nsize\nquality` form (the chat caller passes
/// `f"{user_msg}\n{sess.model}"`, so only the first two lines are present →
/// size defaults to `1024x1024`, quality to `medium`).
///
/// Returns a `Map` with either the seven success keys
/// (`results`/`image_url`/`image_id`/`image_prompt`/`image_model`/`image_size`/
/// `image_quality`) or a single `error` key — a soft-fail, never a panic.
///
/// Gated `all(web, db)`: it needs the `gallery_images` table for `_save_to_gallery`
/// (the raw-SQL image-endpoint probe in auto-detect + the gallery insert), and its
/// only caller (`routes::chat_routes`) is itself `cfg(all(web, db))`. The fn is
/// simply not compiled under web-only or default builds.
pub(crate) async fn do_generate_image(
    content: &str,
    session_id: Option<&str>,
    owner: Option<&str>,
) -> Map<String, Value> {
    use base64::Engine;

    /// `Path("data/generated_images")` — matches the Python hardcoded path and the
    /// existing `mcp_servers::image_gen_server::IMG_DIR` / `gallery_routes::IMG_DIR`
    /// consts (cwd-relative literal; NOT `constants::DATA_DIR`).
    const IMG_DIR: &str = "data/generated_images";

    // Single-key `{"error": msg}` Map — the failure shape Python returns from
    // every early-return.
    fn err_map(msg: impl Into<String>) -> Map<String, Value> {
        let mut m = Map::new();
        m.insert("error".to_string(), Value::String(msg.into()));
        m
    }

    // === 1. CONTENT PARSE (L1543-1550) ===================================
    // lines = content.strip().split("\n")
    let stripped = content.trim();
    let lines: Vec<&str> = stripped.split('\n').collect();
    // prompt = lines[0].strip() if lines else ""
    let prompt: String = lines.first().map(|l| l.trim()).unwrap_or("").to_string();
    // model_spec = lines[1].strip() if len(lines)>1 and lines[1].strip() else ""
    let mut model_spec: String = lines
        .get(1)
        .map(|l| l.trim())
        .filter(|l| !l.is_empty())
        .unwrap_or("")
        .to_string();
    // size = lines[2].strip() if len(lines)>2 and lines[2].strip() else "1024x1024"
    let mut size: String = lines
        .get(2)
        .map(|l| l.trim())
        .filter(|l| !l.is_empty())
        .unwrap_or("1024x1024")
        .to_string();
    // quality = lines[3].strip() if len(lines)>3 and lines[3].strip() else "medium"
    let mut quality: String = lines
        .get(3)
        .map(|l| l.trim())
        .filter(|l| !l.is_empty())
        .unwrap_or("medium")
        .to_string();

    // if not prompt: return {"error": "Image prompt is required (line 1)"}
    if prompt.is_empty() {
        return err_map("Image prompt is required (line 1)");
    }

    // === 2. SETTINGS DEFAULTS (L1552-1564) ===============================
    // try: _settings = load_settings() except Exception: _settings = {}
    // (load_settings is infallible in Rust — returns {} / DEFAULT_SETTINGS on miss.)
    let settings = crate::src::settings::load_settings();
    // if not model_spec: model_spec = _settings.get("image_model", "")
    if model_spec.is_empty() {
        model_spec = settings
            .get("image_model")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
    }
    // if quality == "medium" and _settings.get("image_quality"): quality = _settings["image_quality"]
    if quality == "medium" {
        if let Some(q) = settings
            .get("image_quality")
            .and_then(Value::as_str)
            .filter(|q| !q.is_empty())
        {
            quality = q.to_string();
        }
    }

    // === 3. AUTO-DETECT (L1566-1603) =====================================
    // DEAD on the chat path (model_spec is always sess.model, non-empty). Ported
    // for fidelity to the Python source of truth.
    if model_spec.is_empty() {
        // for candidate in ("gpt-image-1.5","gpt-image-1","dall-e-3"):
        //   try: _resolve_model(candidate); model_spec = candidate; break
        //   except ValueError: continue
        for candidate in ["gpt-image-1.5", "gpt-image-1", "dall-e-3"] {
            if resolve_model_spec(candidate, owner).await.is_ok() {
                model_spec = candidate.to_string();
                break;
            }
        }
        // Fallback: find any locally registered image-type endpoint (L1585-1611).
        // try: ... except Exception: pass  (swallow all outer errors)
        if model_spec.is_empty() {
            // _img_q = query(ModelEndpoint).filter(is_enabled==True, model_type=="image")
            //   [if owner: _img_q = owner_filter(_img_q, ModelEndpoint, owner)]
            // OWNER SCOPE (upstream 5f58f9a): append `(owner = ? OR owner IS NULL)`
            // when owner is truthy (empty-string => unscoped, owner_filter no-op).
            let img_owner = owner.filter(|o| !o.is_empty());
            let bases: Vec<String> = (|| {
                let conn = crate::core::database::session_local().ok()?;
                let mut sql = String::from(
                    "SELECT base_url FROM model_endpoints \
                     WHERE is_enabled = 1 AND model_type = 'image'",
                );
                if img_owner.is_some() {
                    sql.push_str(" AND (owner = ?1 OR owner IS NULL)");
                }
                let mut stmt = conn.prepare(&sql).ok()?;
                let map = |r: &rusqlite::Row| r.get::<_, String>(0);
                let rows = match img_owner {
                    Some(o) => stmt.query_map(rusqlite::params![o], map),
                    None => stmt.query_map([], map),
                }
                .ok()?
                .collect::<rusqlite::Result<Vec<String>>>()
                .ok()?;
                Some(rows)
            })()
            .unwrap_or_default();

            for base_url in bases {
                // _ibase = base_url.rstrip("/"); if not endswith("/v1"): _ibase += "/v1"
                let mut ibase = base_url.trim_end_matches('/').to_string();
                if !ibase.ends_with("/v1") {
                    ibase.push_str("/v1");
                }
                // _r = httpx.get(_ibase + "/models", timeout=3); _r.raise_for_status()
                // _mids = [m["id"] for m in (_r.json().get("data") or []) if m.get("id")]
                // if _mids: model_spec = _mids[0]; break   (any error -> continue)
                let mids: Vec<String> = async {
                    let client = reqwest::Client::builder()
                        .timeout(std::time::Duration::from_secs(3))
                        .build()
                        .ok()?;
                    let resp = client.get(format!("{ibase}/models")).send().await.ok()?;
                    if !resp.status().is_success() {
                        return None; // r.raise_for_status()
                    }
                    let data: Value = resp.json().await.ok()?;
                    Some(
                        data.get("data")
                            .and_then(Value::as_array)
                            .map(|arr| {
                                arr.iter()
                                    .filter_map(|m| {
                                        m.get("id").and_then(Value::as_str).map(String::from)
                                    })
                                    .collect()
                            })
                            .unwrap_or_default(),
                    )
                }
                .await
                .unwrap_or_default();
                if let Some(first) = mids.into_iter().next() {
                    model_spec = first;
                    break;
                }
            }
        }
        // if not model_spec: return {"error": "No image model found. ..."}
        if model_spec.is_empty() {
            return err_map("No image model found. Configure one in Admin → Image Generation.");
        }
    }

    // === 4. RESOLVE THE ENDPOINT (L1615-1620) ============================
    // try: url, model_id, headers = _resolve_model(model_spec, owner=owner)
    // except ValueError: return {"error": "No endpoint found with image model '{model_spec}'. ..."}
    // Reuses the already-ported generic resolver (now owner-scoped — upstream 5f58f9a).
    // The broad `except ValueError` collapses ANY resolve Err (incl. DB errors) into
    // the one fixed message — still a {"error":..} Map, so the observable shape is preserved.
    let (url, model_id, headers) = match resolve_model_spec(&model_spec, owner).await {
        Ok(t) => t,
        Err(_) => {
            return err_map(format!(
                "No endpoint found with image model '{model_spec}'. \
                 Configure an OpenAI-compatible endpoint with image generation support."
            ));
        }
    };

    // === 5. MODEL-FAMILY FLAGS (L1612-1615) ==============================
    let model_lower = model_id.to_lowercase();
    let is_gpt_image = model_lower.contains("gpt-image");
    let is_dalle = model_lower.contains("dall-e");
    let is_local_diffusion = !is_gpt_image && !is_dalle;

    // === 6. IMAGES URL (L1617-1619) ======================================
    // base_url = url.replace("/chat/completions","").replace("/v1/messages","").rstrip("/")
    let base_url = url
        .replace("/chat/completions", "")
        .replace("/v1/messages", "");
    let base_url = base_url.trim_end_matches('/');
    let images_url = format!("{base_url}/images/generations");

    // === 7. SIZE CLAMP (L1621-1627) ======================================
    let valid_gpt_sizes = ["1024x1024", "1024x1536", "1536x1024", "auto"];
    let valid_dalle3_sizes = ["1024x1024", "1024x1792", "1792x1024"];
    // Mirrors the Python if/elif at ai_interaction.py:1624-1627 1:1: both
    // branches reset to "1024x1024" (the shared default) under distinct
    // conditions — the duplicate body is deliberate, not collapsible.
    #[allow(clippy::if_same_then_else)]
    if is_gpt_image && !valid_gpt_sizes.contains(&size.as_str()) {
        size = "1024x1024".to_string();
    } else if is_dalle && !valid_dalle3_sizes.contains(&size.as_str()) {
        size = "1024x1024".to_string();
    }
    // (local diffusion: no clamp)

    // === 8. PAYLOAD (L1629-1641) =========================================
    let mut payload = Map::new();
    payload.insert("model".to_string(), Value::String(model_id.clone()));
    payload.insert("prompt".to_string(), Value::String(prompt.clone()));
    payload.insert("n".to_string(), Value::from(1));
    payload.insert("size".to_string(), Value::String(size.clone()));
    // GPT image models and local diffusion support quality; DALL-E does not.
    // (NO response_format key — Python's payload does not build one.)
    if is_gpt_image || is_local_diffusion {
        let q = if matches!(quality.as_str(), "low" | "medium" | "high" | "auto") {
            quality.clone()
        } else {
            "medium".to_string()
        };
        payload.insert("quality".to_string(), Value::String(q));
    }
    // payload.get("quality", "medium") — the value bound into the gallery row + the
    // returned image_quality.
    let quality_used = payload
        .get("quality")
        .and_then(Value::as_str)
        .unwrap_or("medium")
        .to_string();

    // logger.info(f"Image generation: model=..., size=..., quality=..., prompt={prompt[:80]}")
    let prompt_head80: String = prompt.chars().take(80).collect();
    crate::pylog::info(&format!(
        "Image generation: model={model_id}, size={size}, quality={quality}, prompt={prompt_head80}"
    ));

    // === 9. HTTP POST (L1645-1657, L1731-1734) ===========================
    // async with httpx.AsyncClient(timeout=Timeout(connect=30, read=300, ...)):
    // reqwest collapses httpx's per-phase splits to a single 300s read + 30s connect
    // (same accepted drift as image_gen_server.rs).
    let client = match reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(300))
        .connect_timeout(std::time::Duration::from_secs(30))
        .build()
    {
        Ok(c) => c,
        // A client-builder failure is the generic `except Exception as e`.
        Err(e) => return err_map(format!("Image generation error: {e}")),
    };
    let mut req = client.post(&images_url);
    for (k, v) in &headers {
        req = req.header(k.as_str(), v.as_str());
    }
    req = req.json(&Value::Object(payload.clone()));

    let resp = match req.send().await {
        Ok(r) => r,
        // except httpx.TimeoutException: ...timed out (300s)...
        Err(e) if e.is_timeout() => {
            return err_map(
                "Image generation timed out (300s). The model may be overloaded \
                 — try again or use quality=low.",
            );
        }
        // except Exception as e: f"Image generation error: {e}"
        Err(e) => return err_map(format!("Image generation error: {e}")),
    };

    // if resp.status_code != 200: ...
    let status = resp.status();
    if status.as_u16() != 200 {
        // error_text = resp.text[:500]
        let body_text = resp.text().await.unwrap_or_default();
        let mut error_text: String = body_text.chars().take(500).collect();
        // try: err_json = resp.json();
        //   error_text = err_json["error"]["message"] if isinstance(err_json["error"],dict)
        //                else str(err_json.get("error", error_text))
        if let Ok(err_json) = serde_json::from_str::<Value>(&body_text) {
            if let Some(err_val) = err_json.get("error") {
                if let Some(obj) = err_val.as_object() {
                    if let Some(msg) = obj.get("message").and_then(Value::as_str) {
                        error_text = msg.to_string();
                    }
                } else {
                    error_text = py_str(err_val);
                }
            }
        }
        return err_map(format!(
            "Image generation failed ({}): {error_text}",
            status.as_u16()
        ));
    }

    // === 10. PARSE DATA (L1659-1664) =====================================
    // data = resp.json()  (non-JSON 200 -> generic except -> "Image generation error: {e}")
    let data: Value = match resp.json().await {
        Ok(v) => v,
        Err(e) => return err_map(format!("Image generation error: {e}")),
    };
    // images = data.get("data", []); if not images: return {"error": "No images returned from API"}
    let images = match data.get("data").and_then(Value::as_array) {
        Some(arr) if !arr.is_empty() => arr,
        _ => return err_map("No images returned from API"),
    };
    let img = &images[0];

    // === 11. B64 / URL HANDLING (L1668-1719) =============================
    // def _save_to_gallery(filename) -> str: insert a GalleryImage row, return id (or "").
    // DIFFERS from image_gen_server::save_to_gallery: includes session_id AND owner
    // (Python L1681-1682 sets them on the chat path).
    let save_to_gallery = |filename: &str| -> String {
        let conn = match crate::core::database::session_local() {
            Ok(c) => c,
            Err(e) => {
                crate::pylog::warning(&format!("Failed to save gallery record: {e}"));
                return String::new();
            }
        };
        let new_id = uuid::Uuid::new_v4().to_string();
        let now = crate::pydatetime::utcnow_naive_iso();
        // session_id / owner bound as Option -> SQL NULL when None.
        match conn.execute(
            "INSERT INTO gallery_images \
               (id, filename, prompt, model, size, quality, session_id, owner, \
                is_active, favorite, created_at, updated_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, 1, 0, ?9, ?9)",
            rusqlite::params![
                new_id,
                filename,
                prompt,
                model_id,
                size,
                quality_used,
                session_id,
                owner,
                now
            ],
        ) {
            Ok(_) => new_id,
            Err(e) => {
                crate::pylog::warning(&format!("Failed to save gallery record: {e}"));
                String::new()
            }
        }
    };

    // image_url = None; image_id = None  (Python). Every reachable success branch
    // assigns image_url (the `else` returns early), so it has no initial value here;
    // image_id stays None in the url-fallback branches (emitted as Null).
    let image_url: Option<String>;
    let mut image_id: Option<String> = None;

    // if img.get("b64_json"): ...
    if let Some(b64) = img.get("b64_json").and_then(Value::as_str) {
        // img_dir.mkdir(parents=True, exist_ok=True)
        if let Err(e) = std::fs::create_dir_all(IMG_DIR) {
            return err_map(format!("Image generation error: {e}"));
        }
        // filename = f"{uuid.uuid4().hex[:12]}.png"
        let hex = uuid::Uuid::new_v4().simple().to_string();
        let filename = format!("{}.png", &hex[..12]);
        // img_path.write_bytes(base64.b64decode(...))
        let decoded = match base64::engine::general_purpose::STANDARD.decode(b64) {
            Ok(bytes) => bytes,
            Err(e) => return err_map(format!("Image generation error: {e}")),
        };
        let img_path = std::path::Path::new(IMG_DIR).join(&filename);
        if let Err(e) = std::fs::write(&img_path, &decoded) {
            return err_map(format!("Image generation error: {e}"));
        }
        // image_url = f"/api/generated-image/{filename}"; image_id = _save_to_gallery(filename)
        image_url = Some(format!("/api/generated-image/{filename}"));
        image_id = Some(save_to_gallery(&filename));
    } else if let Some(external) = img.get("url").and_then(Value::as_str) {
        // elif img.get("url"): download + save locally; else fall back to external URL.
        let dl: Option<Vec<u8>> = async {
            let client = reqwest::Client::builder()
                .timeout(std::time::Duration::from_secs(60))
                .build()
                .ok()?;
            let resp = client.get(external).send().await.ok()?;
            // if dl_resp.status_code == 200: ... else: fall back
            if resp.status().as_u16() != 200 {
                return Some(Vec::new()); // sentinel: status != 200 -> fall back (no bytes)
            }
            resp.bytes().await.ok().map(|b| b.to_vec())
        }
        .await;
        match dl {
            // got bytes (status 200) -> write + gallery
            Some(bytes) if !bytes.is_empty() => {
                if let Err(e) = std::fs::create_dir_all(IMG_DIR) {
                    return err_map(format!("Image generation error: {e}"));
                }
                let hex = uuid::Uuid::new_v4().simple().to_string();
                let filename = format!("{}.png", &hex[..12]);
                let img_path = std::path::Path::new(IMG_DIR).join(&filename);
                if let Err(e) = std::fs::write(&img_path, &bytes) {
                    return err_map(format!("Image generation error: {e}"));
                }
                image_url = Some(format!("/api/generated-image/{filename}"));
                image_id = Some(save_to_gallery(&filename));
            }
            // status != 200 (empty sentinel): image_url = external URL, image_id stays None
            Some(_) => {
                image_url = Some(external.to_string());
            }
            // download error: logger.warning + image_url = external URL, image_id stays None
            None => {
                crate::pylog::warning("Failed to download DALL-E image");
                image_url = Some(external.to_string());
            }
        }
    } else {
        // else: return {"error": "Image API returned unexpected format (no b64_json or url)"}
        return err_map("Image API returned unexpected format (no b64_json or url)");
    }

    // === 12. RETURN DICT (L1721-1729) ====================================
    // All seven keys ALWAYS present on success.
    let prompt_head100: String = prompt.chars().take(100).collect();
    let mut out = Map::new();
    out.insert(
        "results".to_string(),
        Value::String(format!("Generated image for: {prompt_head100}")),
    );
    // image_url is set in every reachable success branch -> always a String here.
    out.insert(
        "image_url".to_string(),
        image_url.map(Value::String).unwrap_or(Value::Null),
    );
    // image_id: the saved id String OR Null in the url-fallback branches
    // (Python returns the image_id key unconditionally — value None there).
    out.insert(
        "image_id".to_string(),
        image_id.map(Value::String).unwrap_or(Value::Null),
    );
    out.insert("image_prompt".to_string(), Value::String(prompt));
    out.insert("image_model".to_string(), Value::String(model_id));
    out.insert("image_size".to_string(), Value::String(size));
    out.insert("image_quality".to_string(), Value::String(quality_used));
    out
}

/// `str(value)` for a JSON value (Python `str()` of the decoded error body) —
/// copied from `mcp_servers::image_gen_server::py_str`. A JSON string passes
/// through verbatim; other types fall back to the compact JSON form.
fn py_str(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

// ===========================================================================
// Focused tests for `resolve_model_spec` spec parsing + match branches.
// ===========================================================================
//
// These exercise the SPEC PARSING (`model@endpoint` rsplit), the `name LIKE`
// endpoint filter, the anthropic-provider hardcoded-list match (no network),
// and the not-found / no-enabled-endpoints error messages — all against a
// SEEDED `model_endpoints` table in a UNIQUE temp DB (NEVER the live data dir).
// `DATABASE_URL` is process-global, so the whole suite is ONE serial test
// (matching `tests/database.rs`'s note on the parallel-env race).
#[cfg(test)]
mod resolve_model_spec_tests {
    use super::*;

    fn now_iso() -> String {
        crate::pydatetime::utcnow_naive_iso()
    }

    /// Seed an enabled `model_endpoints` row (plaintext api_key — `decrypt`
    /// passes legacy plaintext through unchanged).
    fn seed_endpoint(conn: &rusqlite::Connection, id: &str, name: &str, base_url: &str, api_key: Option<&str>, enabled: bool) {
        let ts = now_iso();
        conn.execute(
            "INSERT INTO model_endpoints \
             (id, name, base_url, api_key, is_enabled, model_type, created_at, updated_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, 'llm', ?6, ?6)",
            rusqlite::params![id, name, base_url, api_key, enabled as i64, ts],
        )
        .unwrap();
    }

    #[tokio::test]
    #[allow(clippy::await_holding_lock)] // shared DB_TEST_LOCK must span the awaited DB work
    async fn resolve_model_spec_branches() {
        // Serialize against every other DATABASE_URL/settings-swapping test on the
        // one process-global lock (the env var + settings cache are shared state).
        let _g = crate::core::database::DB_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        // Unique temp DB; clean up before + after. Never the live data dir.
        let dir = std::env::temp_dir();
        let unique = format!(
            "odysseus_resolve_model_{}_{}.db",
            std::process::id(),
            uuid::Uuid::new_v4().simple()
        );
        let path = dir.join(unique);
        // RAII cleanup so a failed assertion (which unwinds past the end of the
        // test) never leaks the temp .db — the trailing remove_file would be skipped.
        struct TmpDb(std::path::PathBuf);
        impl Drop for TmpDb {
            fn drop(&mut self) {
                let _ = std::fs::remove_file(&self.0);
            }
        }
        let _guard = TmpDb(path.clone());
        let _ = std::fs::remove_file(&path);
        std::env::set_var("DATABASE_URL", format!("sqlite:///{}", path.display()));

        // Build the schema (creates model_endpoints among the rest).
        crate::core::database::create_all().unwrap();

        // --- (A) No enabled endpoints at all => "No enabled endpoints found" ---
        let err = resolve_model_spec("claude-opus-4", None).await.unwrap_err();
        assert_eq!(err.to_string(), "No enabled endpoints found");

        // --- (B) "@endpoint" filter with no MATCHING enabled endpoint ---
        {
            let conn = crate::core::database::session_local().unwrap();
            // an Anthropic endpoint named "Anthropic Prod" + a disabled one.
            seed_endpoint(&conn, "ep1", "Anthropic Prod", "https://api.anthropic.com", Some("sk-ant-test"), true);
            seed_endpoint(&conn, "ep2", "Disabled OpenAI", "https://api.openai.com/v1", Some("sk-oai"), false);
        }
        // name LIKE '%nope%' matches nothing enabled.
        let err = resolve_model_spec("claude-opus-4@nope", None).await.unwrap_err();
        assert_eq!(err.to_string(), "No enabled endpoints found matching 'nope'");

        // --- (C) Anthropic provider match: bidirectional substring vs ANTHROPIC_MODELS,
        //         url ends /v1/messages. Headers now come from `build_headers`, which for
        //         an Anthropic endpoint emits ONLY x-api-key + anthropic-version (NO
        //         Authorization: Bearer — that was the old inline behavior, replaced by
        //         the shared helper in the 7c7ac10 upstream-parity update).
        //         "@anthropic" filters to the enabled Anthropic Prod row only (LIKE is
        //         ASCII-case-insensitive). No network is touched on the anthropic branch.
        let (url, model, headers) = resolve_model_spec("claude-opus-4@anthropic", None).await.unwrap();
        assert_eq!(url, "https://api.anthropic.com/v1/messages");
        // First ANTHROPIC_MODELS entry where (spec ⊂ am || am ⊂ spec): for spec
        // "claude-opus-4" the list's FIRST entry "claude-opus-4-20250514" CONTAINS
        // it, so the bidirectional-substring scan returns that (order-faithful to
        // the Python `for am in ANTHROPIC_MODELS` loop).
        assert_eq!(model, "claude-opus-4-20250514");
        assert_eq!(headers.get("Authorization"), None);
        assert_eq!(headers.get("x-api-key").map(String::as_str), Some("sk-ant-test"));
        assert_eq!(headers.get("anthropic-version").map(String::as_str), Some("2023-06-01"));

        // --- (D) Not found: a model the Anthropic list can't match, anthropic-only enabled
        //         endpoint => falls through to the final ValueError.
        //         (No OpenAI-compatible enabled endpoint to probe, so no network call.)
        let err = resolve_model_spec("totally-unknown-model", None).await.unwrap_err();
        assert_eq!(err.to_string(), "Model 'totally-unknown-model' not found on any configured endpoint");
        // temp DB removed by `_guard`'s Drop (covers the panic path too).
    }

    /// Seed an enabled `model_endpoints` row carrying an explicit `owner`.
    fn seed_endpoint_owner(
        conn: &rusqlite::Connection,
        id: &str,
        name: &str,
        base_url: &str,
        api_key: Option<&str>,
        owner: Option<&str>,
    ) {
        let ts = now_iso();
        conn.execute(
            "INSERT INTO model_endpoints \
             (id, name, base_url, api_key, is_enabled, model_type, owner, created_at, updated_at) \
             VALUES (?1, ?2, ?3, ?4, 1, 'llm', ?5, ?6, ?6)",
            rusqlite::params![id, name, base_url, api_key, owner, ts],
        )
        .unwrap();
    }

    // OWNER SCOPE (upstream 5f58f9a — "scope tool model resolution by owner").
    // With three Anthropic endpoints — one owned by "alice", one owned by "bob",
    // one NULL-owner ('shared') — `resolve_model_spec(spec, Some("alice"))` must NOT
    // reach bob's private endpoint, but the shared NULL-owner one stays visible
    // (owner_filter include_shared=True => `(owner = ? OR owner IS NULL)`). The
    // anthropic branch matches off the hardcoded ANTHROPIC_MODELS list, so no
    // network is touched and the assertion is purely about WHICH rows the filter
    // admits.
    #[tokio::test]
    #[allow(clippy::await_holding_lock)] // shared DB_TEST_LOCK must span the awaited DB work
    async fn resolve_model_spec_owner_scoped() {
        let _g = crate::core::database::DB_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let dir = std::env::temp_dir();
        let unique = format!(
            "odysseus_resolve_model_owner_{}_{}.db",
            std::process::id(),
            uuid::Uuid::new_v4().simple()
        );
        let path = dir.join(unique);
        struct TmpDb(std::path::PathBuf);
        impl Drop for TmpDb {
            fn drop(&mut self) {
                let _ = std::fs::remove_file(&self.0);
            }
        }
        let _guard = TmpDb(path.clone());
        let _ = std::fs::remove_file(&path);
        std::env::set_var("DATABASE_URL", format!("sqlite:///{}", path.display()));
        crate::core::database::create_all().unwrap();

        {
            let conn = crate::core::database::session_local().unwrap();
            // alice's private Anthropic endpoint.
            seed_endpoint_owner(&conn, "epA", "Alice Anthropic", "https://api.anthropic.com", Some("sk-alice"), Some("alice"));
            // bob's private Anthropic endpoint (a DIFFERENT base_url so we can tell
            // them apart). It must be a real `*.anthropic.com` host so `_detect_provider`
            // classifies it as anthropic (no network probe) — `_host_match` matches
            // `anthropic.com` + its subdomains, NOT a `.anthropic.example` look-alike.
            seed_endpoint_owner(&conn, "epB", "Bob Anthropic", "https://bob.anthropic.com", Some("sk-bob"), Some("bob"));
        }

        // alice resolves => only her own endpoint admitted (bob's is filtered out).
        let (url, _model, headers) =
            resolve_model_spec("claude-opus-4", Some("alice")).await.unwrap();
        assert_eq!(url, "https://api.anthropic.com/v1/messages");
        assert_eq!(headers.get("x-api-key").map(String::as_str), Some("sk-alice"));

        // bob resolves => HIS endpoint (proves the filter is actually per-owner, not
        // just "first row wins").
        let (burl, _bm, bheaders) =
            resolve_model_spec("claude-opus-4", Some("bob")).await.unwrap();
        assert_eq!(burl, "https://bob.anthropic.com/v1/messages");
        assert_eq!(bheaders.get("x-api-key").map(String::as_str), Some("sk-bob"));

        // Now add a NULL-owner ('shared') endpoint and a SECOND user "carol" with no
        // private endpoint. carol must still resolve via the shared one
        // (include_shared=True => `OR owner IS NULL`).
        {
            let conn = crate::core::database::session_local().unwrap();
            seed_endpoint_owner(&conn, "epS", "Shared Anthropic", "https://shared.anthropic.com", Some("sk-shared"), None);
        }
        let (curl, _cm, cheaders) =
            resolve_model_spec("claude-opus-4", Some("carol")).await.unwrap();
        assert_eq!(curl, "https://shared.anthropic.com/v1/messages");
        assert_eq!(cheaders.get("x-api-key").map(String::as_str), Some("sk-shared"));

        // An empty-string owner is Python-falsy => UNSCOPED: the first inserted row
        // (alice's) wins, proving the scope is skipped (owner_filter `if not user`).
        let (eurl, _em, _eh) = resolve_model_spec("claude-opus-4", Some("")).await.unwrap();
        assert_eq!(eurl, "https://api.anthropic.com/v1/messages");
        // temp DB removed by `_guard`'s Drop.
    }
}

// ===========================================================================
// ADVERSARIAL round-trip for `do_generate_image` against a MOCK OpenAI server.
// ===========================================================================
//
// `#[ignore]` because it mutates two process-global states (the cwd via
// `set_current_dir` so the cwd-relative `data/generated_images` lands under a
// TEMP dir, and `DATABASE_URL`) and binds a TCP listener — run it explicitly
// with `cargo test -- --ignored generate_image_roundtrip`.
// It NEVER touches the live `/Users/einarelen/junk/odysseus/data` dir.
#[cfg(test)]
mod generate_image_roundtrip_tests {
    use super::*;
    use base64::Engine as _;

    /// 1x1 transparent PNG, base64 STANDARD — the canned `b64_json` the mock
    /// returns and the bytes we assert land on disk after decode.
    const TINY_PNG_B64: &str =
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";

    /// A minimal mock OpenAI-compatible server on a raw tokio TCP listener.
    /// Answers `GET /v1/models` (so `resolve_model_spec`'s probe matches the
    /// seeded model id) and `POST /v1/images/generations` (the canned
    /// `{data:[{b64_json}]}`). Records the LAST images-generation request body
    /// so the test can assert the payload shape. Shuts down on a oneshot signal.
    async fn run_mock(
        listener: tokio::net::TcpListener,
        captured: std::sync::Arc<tokio::sync::Mutex<Option<String>>>,
        mut shutdown: tokio::sync::oneshot::Receiver<()>,
    ) {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        loop {
            let accept = listener.accept();
            tokio::select! {
                _ = &mut shutdown => break,
                Ok((mut sock, _)) = accept => {
                    // Read until headers complete (\r\n\r\n), then any declared body.
                    let mut buf = Vec::new();
                    let mut tmp = [0u8; 4096];
                    let body_start;
                    loop {
                        let n = match sock.read(&mut tmp).await { Ok(0) | Err(_) => { body_start = None; break; } Ok(n) => n };
                        buf.extend_from_slice(&tmp[..n]);
                        if let Some(pos) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
                            body_start = Some(pos + 4);
                            break;
                        }
                    }
                    let req = String::from_utf8_lossy(&buf).to_string();
                    let first_line = req.lines().next().unwrap_or("").to_string();
                    let is_images = first_line.contains("POST") && first_line.contains("/images/generations");
                    // For the POST, read the rest of the body per Content-Length.
                    if is_images {
                        let clen = req
                            .lines()
                            .find_map(|l| l.to_ascii_lowercase().strip_prefix("content-length:").map(|v| v.trim().parse::<usize>().ok()))
                            .flatten()
                            .unwrap_or(0);
                        if let Some(bs) = body_start {
                            while buf.len() < bs + clen {
                                let n = match sock.read(&mut tmp).await { Ok(0) | Err(_) => break, Ok(n) => n };
                                buf.extend_from_slice(&tmp[..n]);
                            }
                            let body = String::from_utf8_lossy(&buf[bs..]).to_string();
                            *captured.lock().await = Some(body);
                        }
                    }
                    let body = if first_line.contains("/models") {
                        // resolve_model_spec matches a model id from data[].id.
                        r#"{"data":[{"id":"mock-diffusion-xl"}]}"#.to_string()
                    } else if is_images {
                        format!(r#"{{"data":[{{"b64_json":"{TINY_PNG_B64}"}}]}}"#)
                    } else {
                        "{}".to_string()
                    };
                    let resp = format!(
                        "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                        body.len(), body
                    );
                    let _ = sock.write_all(resp.as_bytes()).await;
                    let _ = sock.flush().await;
                }
            }
        }
    }

    #[tokio::test]
    #[ignore = "mutates process-global cwd + DATABASE_URL and binds a TCP port"]
    // The SELECT row is destructured into a faithful 8-column tuple mirroring the
    // gallery_images schema 1:1; factoring it into a type alias would obscure the
    // direct column-by-column mapping.
    #[allow(clippy::type_complexity)]
    async fn generate_image_roundtrip() {
        // --- temp data dir as the cwd (do_generate_image writes cwd-relative) ---
        let tmp = std::env::temp_dir().join(format!(
            "odysseus_genimg_{}_{}",
            std::process::id(),
            uuid::Uuid::new_v4().simple()
        ));
        std::fs::create_dir_all(&tmp).unwrap();
        struct Cleanup {
            tmp: std::path::PathBuf,
            prev_cwd: std::path::PathBuf,
            db: std::path::PathBuf,
        }
        impl Drop for Cleanup {
            fn drop(&mut self) {
                let _ = std::env::set_current_dir(&self.prev_cwd);
                let _ = std::fs::remove_dir_all(&self.tmp);
                let _ = std::fs::remove_file(&self.db);
            }
        }
        let prev_cwd = std::env::current_dir().unwrap();
        let db_path = tmp.join("test.db");
        let _guard = Cleanup { tmp: tmp.clone(), prev_cwd, db: db_path.clone() };
        std::env::set_current_dir(&tmp).unwrap();
        std::env::set_var("DATABASE_URL", format!("sqlite:///{}", db_path.display()));
        crate::core::database::create_all().unwrap();

        // --- stand up the mock OpenAI-compatible server ---
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let base_url = format!("http://{addr}/v1");
        let captured = std::sync::Arc::new(tokio::sync::Mutex::new(None::<String>));
        let (tx, rx) = tokio::sync::oneshot::channel();
        let server = tokio::spawn(run_mock(listener, captured.clone(), rx));

        // --- seed an enabled image-type endpoint pointing at the mock ---
        {
            let conn = crate::core::database::session_local().unwrap();
            let ts = crate::pydatetime::utcnow_naive_iso();
            conn.execute(
                "INSERT INTO model_endpoints \
                 (id, name, base_url, api_key, is_enabled, model_type, created_at, updated_at) \
                 VALUES (?1, ?2, ?3, ?4, 1, 'image', ?5, ?5)",
                rusqlite::params!["epimg", "Mock Images", base_url, "sk-mock", ts],
            )
            .unwrap();
        }

        // --- call: chat passes f"{user_msg}\n{model}" ---
        let result = do_generate_image(
            "a red apple on a table\nmock-diffusion-xl",
            Some("sess-123"),
            None,
        )
        .await;

        // shut the mock down (assert after, so a failure still cleans up).
        let _ = tx.send(());
        let _ = server.await;

        // --- assert: NOT the "not available" deferral, real success shape ---
        assert!(
            !result.contains_key("error"),
            "expected success, got error: {:?}",
            result.get("error")
        );
        // all SEVEN success keys present.
        for k in [
            "results", "image_url", "image_id", "image_prompt", "image_model",
            "image_size", "image_quality",
        ] {
            assert!(result.contains_key(k), "missing success key {k}: {result:?}");
        }
        assert_eq!(
            result.get("results").and_then(Value::as_str),
            Some("Generated image for: a red apple on a table")
        );
        assert_eq!(
            result.get("image_prompt").and_then(Value::as_str),
            Some("a red apple on a table")
        );
        assert_eq!(
            result.get("image_model").and_then(Value::as_str),
            Some("mock-diffusion-xl")
        );
        // no size/quality lines -> defaults; local diffusion keeps quality.
        assert_eq!(result.get("image_size").and_then(Value::as_str), Some("1024x1024"));
        assert_eq!(result.get("image_quality").and_then(Value::as_str), Some("medium"));

        // image_url = /api/generated-image/<file>; file is 12 hex + .png.
        let image_url = result.get("image_url").and_then(Value::as_str).unwrap();
        let filename = image_url.strip_prefix("/api/generated-image/").unwrap().to_string();
        assert!(
            filename.len() == 16 && filename.ends_with(".png"),
            "unexpected filename {filename}"
        );

        // --- the bytes were base64-decoded + written under the TEMP data dir ---
        let on_disk = std::path::Path::new("data/generated_images").join(&filename);
        let bytes = std::fs::read(&on_disk).expect("decoded image bytes on disk");
        let expected = base64::engine::general_purpose::STANDARD
            .decode(TINY_PNG_B64)
            .unwrap();
        assert_eq!(bytes, expected, "written bytes != decoded b64_json");

        // --- a GalleryImage row was inserted; image_id == its id ---
        let image_id = result.get("image_id").and_then(Value::as_str).unwrap().to_string();
        assert!(!image_id.is_empty(), "image_id should be the gallery row id");
        {
            let conn = crate::core::database::session_local().unwrap();
            let (rid, rfile, rprompt, rmodel, rsize, rqual, rsess, ractive): (
                String, String, String, Option<String>, Option<String>, Option<String>, Option<String>, i64,
            ) = conn
                .query_row(
                    "SELECT id, filename, prompt, model, size, quality, session_id, is_active \
                     FROM gallery_images WHERE filename = ?1",
                    rusqlite::params![filename],
                    |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?, r.get(6)?, r.get(7)?)),
                )
                .expect("gallery row inserted");
            assert_eq!(rid, image_id);
            assert_eq!(rfile, filename);
            assert_eq!(rprompt, "a red apple on a table");
            assert_eq!(rmodel.as_deref(), Some("mock-diffusion-xl"));
            assert_eq!(rsize.as_deref(), Some("1024x1024"));
            assert_eq!(rqual.as_deref(), Some("medium"));
            assert_eq!(rsess.as_deref(), Some("sess-123")); // owner None -> NULL
            assert_eq!(ractive, 1);
        }

        // --- the POSTed payload shape (model/prompt/n/size/+quality, NO response_format) ---
        let body = captured.lock().await.clone().expect("images request captured");
        let payload: Value = serde_json::from_str(&body).expect("payload is JSON");
        assert_eq!(payload.get("model").and_then(Value::as_str), Some("mock-diffusion-xl"));
        assert_eq!(payload.get("prompt").and_then(Value::as_str), Some("a red apple on a table"));
        assert_eq!(payload.get("n").and_then(Value::as_i64), Some(1));
        assert_eq!(payload.get("size").and_then(Value::as_str), Some("1024x1024"));
        assert_eq!(payload.get("quality").and_then(Value::as_str), Some("medium"));
        assert!(
            payload.get("response_format").is_none(),
            "Python payload has NO response_format key"
        );
        // cleanup via `_guard`'s Drop.
    }
}
