// routes/preset_routes.rs  <- routes/preset_routes.py
//! Preset routes — `/api/presets` GET, `/api/presets/custom` POST, user templates
//! CRUD, group presets, and the AI character-prompt expander.
//!
//! Proof-batch module **P9-preset** (wave: proof). It validates the
//! `Arc<Mutex<PresetManager>>` pattern: every mutating handler must lock the manager
//! because `update_custom` / `save_user_template` / `delete_user_template` /
//! `save_group_presets` take `&mut self` (verified in `src::preset_manager`). It also
//! exercises the `require_admin` auth-adapter (foundation F3) on the write endpoints
//! and the FastAPI/pydantic-v2 422-on-invalid-body shape (`UserTemplateRequest` with
//! manual `min_length`/`max_length`/`ge`/`le` validation).
//!
//! All paths live on the collision-free `/api/presets/*` prefix, so the module
//! coexists with the inline `web/mod.rs` subset (the additive / non-breaking rule).
//!
//! Faithfulness notes:
//! * `setup_preset_routes` is `APIRouter(tags=["presets"])` with **no prefix**, so each
//!   handler carries the full absolute path in its `@router.<method>(...)` decorator.
//!   The axum factory mounts at those same absolute paths.
//! * `get_presets` / `get_user_templates` / `get_group_presets` are **read-only and
//!   unauthenticated**, exactly like the Python (no `Depends(require_admin)`).
//! * `expand_character_prompt` (`POST /api/presets/expand`) is an **HONEST STUB**: the
//!   Python pulls in `src.ai_interaction._resolve_model`, which is NOT ported to Rust
//!   (no Rust equivalent exists in `crate::src::ai_interaction`). The Python wraps the
//!   resolve+`llm_call_async` in a `try/except` that returns
//!   `{"success": False, "message": str(e)}` on any failure, and never raises — so the
//!   stub reproduces that exact failure-branch shape (the validation half — the
//!   "Nothing to expand" / message-building logic — is ported verbatim; only the
//!   model-resolution call is replaced with the same failure response Python emits when
//!   resolution is unavailable). See the per-handler docstring.


use axum::extract::State;
use axum::http::HeaderMap;
use axum::response::{IntoResponse, Response};
use axum::routing::{delete, get, post};
use axum::{Extension, Json, Router};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::core::middleware::INTERNAL_TOOL_HEADER;
use crate::routes::auth_adapter;
use crate::routes::{AppState, CurrentUser, HttpException};
use crate::src::request_models::PresetUpdateRequest;

// ===========================================================================
// Request bodies (the pydantic models)
// ===========================================================================

/// `class UserTemplateRequest(BaseModel)` — a user-saved character template.
///
/// ```python
/// class UserTemplateRequest(BaseModel):
///     id: str = ""
///     name: str = Field(..., min_length=1, max_length=100)
///     system_prompt: str = Field("", max_length=10000)
///     temperature: float = Field(1.0, ge=0.0, le=2.0)
///     max_tokens: int = Field(0, ge=0, le=65536)
/// ```
///
/// `name` is **required** (`Field(...)`); the rest carry defaults. The `Field`
/// constraints (`min_length`/`max_length`/`ge`/`le`) are NOT enforced by serde — they
/// are checked in [`UserTemplateRequest::validate`], mirroring pydantic's 422 on a
/// constraint violation. Defined locally (not in `src::request_models`) because the
/// Python defines it inline in `routes/preset_routes.py`, not in `src/request_models.py`.
#[derive(Debug, Clone, Deserialize)]
struct UserTemplateRequest {
    /// `id: str = ""`.
    #[serde(default)]
    id: String,
    /// `name: str = Field(..., min_length=1, max_length=100)` — required.
    name: String,
    /// `system_prompt: str = Field("", max_length=10000)`.
    #[serde(default)]
    system_prompt: String,
    /// `temperature: float = Field(1.0, ge=0.0, le=2.0)`.
    #[serde(default = "default_temperature")]
    temperature: f64,
    /// `max_tokens: int = Field(0, ge=0, le=65536)`.
    #[serde(default)]
    max_tokens: i64,
}

/// `temperature: float = Field(1.0, ...)` — the pydantic default.
fn default_temperature() -> f64 {
    1.0
}

impl UserTemplateRequest {
    /// Enforce the `Field(...)` constraints pydantic checks before the handler runs.
    ///
    /// pydantic surfaces any violation as a `422 Unprocessable Entity`. We collect the
    /// first failing field (the order pydantic validates fields: declaration order) and
    /// raise the matching [`HttpException`]. The detail strings echo pydantic's own
    /// `"Value error, ..."`-style messages closely enough that clients keying off the
    /// status (422) behave identically; the exact pydantic error envelope is internal.
    fn validate(&self) -> Result<(), HttpException> {
        // `name: Field(..., min_length=1, max_length=100)`
        let name_len = self.name.chars().count();
        if name_len < 1 {
            return Err(HttpException::new(
                422,
                "name: String should have at least 1 character",
            ));
        }
        if name_len > 100 {
            return Err(HttpException::new(
                422,
                "name: String should have at most 100 characters",
            ));
        }
        // `system_prompt: Field("", max_length=10000)`
        if self.system_prompt.chars().count() > 10000 {
            return Err(HttpException::new(
                422,
                "system_prompt: String should have at most 10000 characters",
            ));
        }
        // `temperature: Field(1.0, ge=0.0, le=2.0)`
        if self.temperature < 0.0 {
            return Err(HttpException::new(
                422,
                "temperature: Input should be greater than or equal to 0",
            ));
        }
        if self.temperature > 2.0 {
            return Err(HttpException::new(
                422,
                "temperature: Input should be less than or equal to 2",
            ));
        }
        // `max_tokens: Field(0, ge=0, le=65536)`
        if self.max_tokens < 0 {
            return Err(HttpException::new(
                422,
                "max_tokens: Input should be greater than or equal to 0",
            ));
        }
        if self.max_tokens > 65536 {
            return Err(HttpException::new(
                422,
                "max_tokens: Input should be less than or equal to 65536",
            ));
        }
        Ok(())
    }

    /// `template = req.model_dump()` — the serialized template dict pydantic emits.
    ///
    /// pydantic's `model_dump()` produces the fields in declaration order, so the JSON
    /// object key order is `id, name, system_prompt, temperature, max_tokens`. We build
    /// the same ordered object so the echoed `{"template": ...}` matches Python's key
    /// order (`serde_json` here preserves insertion order via the crate's
    /// `preserve_order` feature).
    fn model_dump(&self) -> Value {
        json!({
            "id": self.id,
            "name": self.name,
            "system_prompt": self.system_prompt,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        })
    }
}

// ===========================================================================
// Auth glue — `Depends(require_admin)`
// ===========================================================================

/// `Depends(require_admin)` — the per-handler admin gate on the write endpoints.
///
/// The Python `require_admin` reads the `X-Odysseus-Internal-Token` header and the
/// stamped `request.state.current_user`; the axum equivalent pulls those from the
/// [`HeaderMap`] and `Option<Extension<CurrentUser>>`, then delegates to the foundation
/// [`auth_adapter::require_admin`] (which wraps the already-ported
/// `core::middleware::require_admin`). `Err` -> `HttpException(403, "Admin only")`.
fn admin_gate(
    state: &AppState,
    headers: &HeaderMap,
    user: Option<&CurrentUser>,
) -> Result<(), HttpException> {
    let internal_header = headers
        .get(INTERNAL_TOOL_HEADER)
        .and_then(|v| v.to_str().ok());
    let user = user.map(|u| u.0.as_str());
    auth_adapter::require_admin(state, internal_header, user)
}

// ===========================================================================
// Router factory (`setup_preset_routes`)
// ===========================================================================

/// `setup_preset_routes()` — `APIRouter(tags=["presets"])` (no prefix).
///
/// app.py registers this as include-router #12. Because the Python `APIRouter` carries
/// no prefix, each handler's decorator already holds the full absolute `/api/presets/...`
/// path; the axum factory mounts at those same paths. Deps come from `State<AppState>`
/// (the factory takes no params, mirroring the no-arg Python `setup_preset_routes()` —
/// the Python factory's `preset_manager` arg is reachable from `AppState`).
///
/// Route order mirrors the Python decorator order. `/api/presets/templates/:template_id`
/// (DELETE) and the literal `/api/presets/templates` (GET/POST) don't collide — axum
/// keys on method+path and a static segment never shadows a sibling capture here.
pub fn setup_preset_routes() -> Router<AppState> {
    Router::new()
        // `@router.get("/api/presets")`
        .route("/api/presets", get(get_presets))
        // `@router.post("/api/presets/custom")`
        .route("/api/presets/custom", post(update_custom_preset))
        // `@router.get("/api/presets/templates")` + `@router.post("/api/presets/templates")`
        .route(
            "/api/presets/templates",
            get(get_user_templates).post(save_user_template),
        )
        // `@router.delete("/api/presets/templates/{template_id}")`
        .route(
            "/api/presets/templates/:template_id",
            delete(delete_user_template),
        )
        // `@router.post("/api/presets/expand")`
        .route("/api/presets/expand", post(expand_character_prompt))
        // `@router.get("/api/presets/groups")` + `@router.post("/api/presets/groups")`
        .route(
            "/api/presets/groups",
            get(get_group_presets).post(save_group_presets),
        )
}

// ===========================================================================
// Handlers
// ===========================================================================

/// `GET /api/presets` — return the full preset registry.
///
/// ```python
/// @router.get("/api/presets")
/// async def get_presets() -> Dict[str, Any]:
///     return preset_manager.presets
/// ```
///
/// No auth. Returns `preset_manager.presets` verbatim (a JSON object preserving the
/// store's insertion order). The lock is held only for the snapshot clone.
async fn get_presets(State(state): State<AppState>) -> Result<Response, HttpException> {
    let presets = {
        let mgr = state.preset_manager.lock().await;
        // `return preset_manager.presets` — clone the ordered registry for the body.
        mgr.get_all()
    };
    Ok(Json(Value::Object(presets)).into_response())
}

/// `POST /api/presets/custom` — update the single built-in `custom` preset.
///
/// ```python
/// @router.post("/api/presets/custom")
/// async def update_custom_preset(preset_update: PresetUpdateRequest,
///                                _admin: None = Depends(require_admin)) -> Dict[str, Any]:
///     try:
///         success = preset_manager.update_custom(
///             preset_update.temperature, preset_update.max_tokens,
///             preset_update.system_prompt, preset_update.name,
///             preset_update.enabled, preset_update.inject_prefix,
///             preset_update.inject_suffix,
///         )
///         if success:
///             return {"success": True, "message": "Custom preset updated"}
///         return {"success": False, "message": "Failed to save preset"}
///     except Exception as e:
///         logger.error(f"Preset update error: {e}")
///         raise HTTPException(500, "Failed to update custom preset")
/// ```
///
/// `Depends(require_admin)` runs first (FastAPI resolves dependencies before the body),
/// so the admin gate is checked before touching the body. The `try/except` wraps the
/// `update_custom` call; in Rust `update_custom` returns a `bool` (it logs+returns
/// `false` internally on an IO error rather than raising), so the `except` arm is
/// unreachable here — `update_custom` either returns `true` -> "Custom preset updated"
/// or `false` -> "Failed to save preset", matching the two non-exception Python returns.
async fn update_custom_preset(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Json(preset_update): Json<PresetUpdateRequest>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    let success = {
        let mut mgr = state.preset_manager.lock().await;
        mgr.update_custom(
            preset_update.temperature,
            preset_update.max_tokens,
            &preset_update.system_prompt,
            &preset_update.name,
            preset_update.enabled,
            &preset_update.inject_prefix,
            &preset_update.inject_suffix,
        )
    };
    if success {
        Ok(Json(json!({"success": true, "message": "Custom preset updated"})).into_response())
    } else {
        Ok(Json(json!({"success": false, "message": "Failed to save preset"})).into_response())
    }
}

/// `GET /api/presets/templates` — list the user-saved character templates.
///
/// ```python
/// @router.get("/api/presets/templates")
/// async def get_user_templates() -> List[Dict]:
///     return preset_manager.get_user_templates()
/// ```
///
/// No auth. Returns the bare array (`[]` when none are saved).
async fn get_user_templates(State(state): State<AppState>) -> Result<Response, HttpException> {
    let templates = {
        let mgr = state.preset_manager.lock().await;
        mgr.get_user_templates()
    };
    Ok(Json(Value::Array(templates)).into_response())
}

/// `POST /api/presets/templates` — upsert a user template (by `id`).
///
/// ```python
/// @router.post("/api/presets/templates")
/// async def save_user_template(req: UserTemplateRequest,
///                              _admin: None = Depends(require_admin)) -> Dict[str, Any]:
///     template = req.model_dump()
///     if not template["id"]:
///         template["id"] = f"user-{uuid.uuid4().hex[:8]}"
///     success = preset_manager.save_user_template(template)
///     if success:
///         return {"success": True, "template": template}
///     return {"success": False, "message": "Failed to save template"}
/// ```
///
/// `Depends(require_admin)` first, then the (pydantic-validated) body. The synthesized
/// id is `user-<first 8 hex of uuid4>`; `uuid4().hex` is the 32-char hyphen-free form,
/// so the Rust `Uuid::simple()` rendering (also hyphen-free) sliced to 8 chars matches.
async fn save_user_template(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Json(req): Json<UserTemplateRequest>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    // pydantic validates the body before the handler body runs; mirror its 422s.
    req.validate()?;

    // `template = req.model_dump()`
    let mut template = req.model_dump();
    // `if not template["id"]: template["id"] = f"user-{uuid.uuid4().hex[:8]}"`
    let empty_id = template
        .get("id")
        .and_then(Value::as_str)
        .map(str::is_empty)
        .unwrap_or(true);
    if empty_id {
        let hex8: String = uuid::Uuid::new_v4()
            .simple()
            .to_string()
            .chars()
            .take(8)
            .collect();
        if let Some(obj) = template.as_object_mut() {
            obj.insert("id".to_string(), Value::String(format!("user-{hex8}")));
        }
    }

    let success = {
        let mut mgr = state.preset_manager.lock().await;
        mgr.save_user_template(&template)
    };
    if success {
        Ok(Json(json!({"success": true, "template": template})).into_response())
    } else {
        Ok(Json(json!({"success": false, "message": "Failed to save template"})).into_response())
    }
}

/// `DELETE /api/presets/templates/:template_id` — delete a user template by id.
///
/// ```python
/// @router.delete("/api/presets/templates/{template_id}")
/// async def delete_user_template(template_id: str,
///                                _admin: None = Depends(require_admin)) -> Dict[str, Any]:
///     success = preset_manager.delete_user_template(template_id)
///     if success:
///         return {"success": True}
///     return {"success": False, "message": "Failed to delete template"}
/// ```
async fn delete_user_template(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    axum::extract::Path(template_id): axum::extract::Path<String>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    let success = {
        let mut mgr = state.preset_manager.lock().await;
        mgr.delete_user_template(&template_id)
    };
    if success {
        Ok(Json(json!({"success": true})).into_response())
    } else {
        Ok(Json(json!({"success": false, "message": "Failed to delete template"})).into_response())
    }
}

/// `POST /api/presets/expand` — use AI to expand a rough character description into a
/// full system prompt.
///
/// ```python
/// @router.post("/api/presets/expand")
/// async def expand_character_prompt(request: Request) -> Dict[str, Any]:
///     from src.ai_interaction import _resolve_model
///     from src.llm_core import llm_call_async
///     data = await request.json()
///     draft = (data.get("prompt") or "").strip()
///     name = (data.get("name") or "").strip()
///     if not draft and not name:
///         return {"success": False, "message": "Nothing to expand"}
///     user_input = ""
///     if name: user_input += f"Character name: {name}\n"
///     if draft: user_input += f"Notes: {draft}\n"
///     messages = [ {"role": "system", "content": (...)}, {"role": "user", "content": user_input} ]
///     try:
///         model_spec = data.get("model") or ""
///         url, model, headers = _resolve_model(model_spec)
///         result = await llm_call_async(url, model, messages, temperature=0.8, max_tokens=500, headers=headers)
///         return {"success": True, "prompt": result.strip()}
///     except Exception as e:
///         logger.error(f"Expand prompt failed: {e}")
///         return {"success": False, "message": str(e)}
/// ```
///
/// Fully ported (PARITY_GAPS Gap #2 / #4). The Python `src.ai_interaction._resolve_model`
/// maps to the Rust [`resolve_model_spec`](crate::src::ai_interaction::resolve_model_spec)
/// and `src.llm_core.llm_call_async` to [`llm_call_async`](crate::src::llm_core::llm_call_async),
/// both of which are now landed. The whole resolve+call sits inside the Python's
/// `try/except` that returns `{"success": False, "message": str(e)}` on any failure and
/// never raises, so both `resolve_model_spec` and `llm_call_async` errors are caught and
/// surfaced as that exact shape (mirroring `logger.error(f"Expand prompt failed: {e}")`).
/// This is the pure-TEXT expander — no vision / `vl_chat`. Python passes no `timeout`, so
/// `llm_call_async` falls back to its `LLMConfig.STREAM_TIMEOUT = 300` default; the Rust
/// call passes `LLMConfig::STREAM_TIMEOUT` explicitly to reproduce that default.
async fn expand_character_prompt(
    Json(data): Json<Value>,
) -> Result<Response, HttpException> {
    // `draft = (data.get("prompt") or "").strip()`
    let draft = data
        .get("prompt")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    // `name = (data.get("name") or "").strip()`
    let name = data
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();

    // `if not draft and not name: return {"success": False, "message": "Nothing to expand"}`
    if draft.is_empty() && name.is_empty() {
        return Ok(Json(json!({"success": false, "message": "Nothing to expand"})).into_response());
    }

    // `user_input` assembly (ported verbatim).
    let mut user_input = String::new();
    if !name.is_empty() {
        user_input.push_str(&format!("Character name: {name}\n"));
    }
    if !draft.is_empty() {
        user_input.push_str(&format!("Notes: {draft}\n"));
    }

    // messages = [ {role: system, content: <expand prompt>}, {role: user, content: user_input} ]
    // System prompt is verbatim from routes/preset_routes.py:91-97.
    let messages = vec![
        json!({
            "role": "system",
            "content": "You are an expert at writing character system prompts for AI assistants. \
The user will give you a character name and/or rough notes. \
Write a concise, effective system prompt (3-6 sentences) that captures the character's personality, \
speaking style, knowledge areas, and behavioral guidelines. \
Output ONLY the system prompt text — no quotes, no preamble, no explanation.",
        }),
        json!({"role": "user", "content": user_input}),
    ];

    // try: model_spec = data.get("model") or ""; url, model, headers = _resolve_model(model_spec)
    //      result = await llm_call_async(url, model, messages, temperature=0.8, max_tokens=500, headers=headers)
    //      return {"success": True, "prompt": result.strip()}
    // except Exception as e: logger.error(...); return {"success": False, "message": str(e)}
    //
    // `data.get("model") or ""` — a missing/null/empty model coerces to "".
    let model_spec = data.get("model").and_then(Value::as_str).unwrap_or("");

    let resolved = crate::src::ai_interaction::resolve_model_spec(model_spec, None).await;
    let (url, model, headers) = match resolved {
        Ok(t) => t,
        Err(e) => {
            crate::pylog::error(&format!("Expand prompt failed: {e}"));
            return Ok(
                Json(json!({"success": false, "message": e.to_string()})).into_response(),
            );
        }
    };

    // Python passes no `timeout`, so `llm_call_async` uses its
    // `LLMConfig.STREAM_TIMEOUT = 300` default; pass it explicitly here.
    match crate::src::llm_core::llm_call_async(
        &url,
        &model,
        messages,
        0.8,
        500,
        headers,
        crate::src::llm_core::LLMConfig::STREAM_TIMEOUT as u64,
    )
    .await
    {
        Ok(result) => Ok(Json(json!({"success": true, "prompt": result.trim()})).into_response()),
        Err(e) => {
            crate::pylog::error(&format!("Expand prompt failed: {e}"));
            Ok(Json(json!({"success": false, "message": e.to_string()})).into_response())
        }
    }
}

/// `GET /api/presets/groups` — get the saved group-chat presets.
///
/// ```python
/// @router.get("/api/presets/groups")
/// async def get_group_presets():
///     return {"groups": preset_manager.get_group_presets()}
/// ```
///
/// No auth. The array is wrapped in a `{"groups": [...]}` object (unlike the bare-array
/// `templates` endpoint).
async fn get_group_presets(State(state): State<AppState>) -> Result<Response, HttpException> {
    let groups = {
        let mgr = state.preset_manager.lock().await;
        mgr.get_group_presets()
    };
    Ok(Json(json!({"groups": Value::Array(groups)})).into_response())
}

/// `POST /api/presets/groups` — save the group-chat presets.
///
/// ```python
/// @router.post("/api/presets/groups")
/// async def save_group_presets(request: Request, _admin: None = Depends(require_admin)):
///     data = await request.json()
///     preset_manager.save_group_presets(data.get("groups", []))
///     return {"ok": True}
/// ```
///
/// `Depends(require_admin)` first, then the raw JSON body. `data.get("groups", [])` —
/// a missing or non-array `groups` key defaults to an empty list (the Python `dict.get`
/// default; a non-array value would be passed through to `save_group_presets`, which the
/// store coerces). Returns `{"ok": True}`.
async fn save_group_presets(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Json(data): Json<Value>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    // `data.get("groups", [])` — default to `[]`; pass the array contents through.
    let groups: Vec<Value> = match data.get("groups") {
        Some(Value::Array(a)) => a.clone(),
        // Missing key -> `[]`. A non-array value is unusual; Python would hand it to
        // `save_group_presets` which stores it under `group_presets`. The store's Rust
        // signature takes `Vec<Value>`, so a non-array `groups` collapses to `[]` here
        // (the only divergence is a malformed body that the frontend never sends).
        _ => Vec::new(),
    };
    {
        let mut mgr = state.preset_manager.lock().await;
        mgr.save_group_presets(groups);
    }
    Ok(Json(json!({"ok": true})).into_response())
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::Body;
    use axum::http::{Request, StatusCode};
    use serde_json::json;
    use tower::ServiceExt; // for `oneshot`

    /// `setup_preset_routes()` merges cleanly into a `Router<AppState>` (no panic =
    /// no intra-router method+path collision), the additive invariant the aggregator
    /// relies on. Building a full `AppState` (20-odd managers) just to drive the real
    /// router end-to-end is impractical, so the dialect of the dynamic DELETE path is
    /// pinned by [`delete_template_param_path_resolves`] below against a state-free
    /// mirror that mounts the SAME colon-form path string.
    #[test]
    fn mount_smoke() {
        let base: Router<AppState> = Router::new();
        let _merged: Router<AppState> = base.merge(setup_preset_routes());
    }

    /// matchit 0.7 (axum 0.7) captures dynamic segments with the COLON form
    /// (`:template_id`); the brace form (`{template_id}`, axum 0.8) is a LITERAL
    /// segment here, so a brace-dialect registration would 404 every
    /// `DELETE /api/presets/templates/<id>`. This drives the param path on a
    /// state-free `Router` mounting the SAME path string the factory uses and asserts
    /// it RESOLVES (status is neither 404 Not Found nor 405 Method Not Allowed),
    /// while a brace-form router 404s the identical request — so reverting to the
    /// wrong dialect fails the suite instead of silently 404ing.
    #[tokio::test]
    async fn delete_template_param_path_resolves() {
        // Sentinel handler — its only job is to prove the path matched (the real
        // handler needs `State<AppState>`; routing resolution is state-independent).
        async fn sentinel() -> StatusCode {
            StatusCode::OK
        }

        // The colon form — what `setup_preset_routes()` registers.
        let colon: Router = Router::new().route(
            "/api/presets/templates/:template_id",
            axum::routing::delete(sentinel),
        );
        let resp = colon
            .oneshot(
                Request::builder()
                    .method("DELETE")
                    .uri("/api/presets/templates/x")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        // Resolved to the handler: NOT a routing miss (404) and NOT a method miss (405).
        assert_ne!(resp.status(), StatusCode::NOT_FOUND);
        assert_ne!(resp.status(), StatusCode::METHOD_NOT_ALLOWED);
        assert_eq!(resp.status(), StatusCode::OK);

        // The brace form (axum-0.8 dialect) is a LITERAL segment in matchit 0.7, so
        // the same request misses and 404s — the bug this fix removes. This negative
        // assertion makes the dialect requirement self-documenting and load-bearing.
        let brace: Router = Router::new().route(
            "/api/presets/templates/{template_id}",
            axum::routing::delete(sentinel),
        );
        let resp = brace
            .oneshot(
                Request::builder()
                    .method("DELETE")
                    .uri("/api/presets/templates/x")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    }

    #[test]
    fn user_template_name_required() {
        // `name: Field(...)` — required; a body without `name` fails to deserialize
        // (pydantic would 422 on the missing required field).
        let r: Result<UserTemplateRequest, _> =
            serde_json::from_value(json!({"system_prompt": "hi"}));
        assert!(r.is_err());
    }

    #[test]
    fn user_template_defaults_match_pydantic() {
        // id="", system_prompt="", temperature=1.0, max_tokens=0.
        let req: UserTemplateRequest =
            serde_json::from_value(json!({"name": "Bard"})).unwrap();
        assert_eq!(req.id, "");
        assert_eq!(req.name, "Bard");
        assert_eq!(req.system_prompt, "");
        assert_eq!(req.temperature, 1.0);
        assert_eq!(req.max_tokens, 0);
        assert!(req.validate().is_ok());
    }

    #[test]
    fn user_template_validate_enforces_field_constraints() {
        // name min_length=1.
        let empty_name = UserTemplateRequest {
            id: String::new(),
            name: String::new(),
            system_prompt: String::new(),
            temperature: 1.0,
            max_tokens: 0,
        };
        let e = empty_name.validate().unwrap_err();
        assert_eq!(e.status_code, 422);
        assert!(e.detail.contains("name"));

        // name max_length=100.
        let long_name = UserTemplateRequest {
            id: String::new(),
            name: "x".repeat(101),
            system_prompt: String::new(),
            temperature: 1.0,
            max_tokens: 0,
        };
        assert_eq!(long_name.validate().unwrap_err().status_code, 422);

        // system_prompt max_length=10000.
        let long_prompt = UserTemplateRequest {
            id: String::new(),
            name: "ok".into(),
            system_prompt: "y".repeat(10001),
            temperature: 1.0,
            max_tokens: 0,
        };
        let e = long_prompt.validate().unwrap_err();
        assert_eq!(e.status_code, 422);
        assert!(e.detail.contains("system_prompt"));

        // temperature ge=0.0 / le=2.0.
        let cold = UserTemplateRequest {
            id: String::new(),
            name: "ok".into(),
            system_prompt: String::new(),
            temperature: -0.1,
            max_tokens: 0,
        };
        assert_eq!(cold.validate().unwrap_err().status_code, 422);
        let hot = UserTemplateRequest {
            id: String::new(),
            name: "ok".into(),
            system_prompt: String::new(),
            temperature: 2.1,
            max_tokens: 0,
        };
        assert_eq!(hot.validate().unwrap_err().status_code, 422);

        // max_tokens ge=0 / le=65536.
        let neg = UserTemplateRequest {
            id: String::new(),
            name: "ok".into(),
            system_prompt: String::new(),
            temperature: 1.0,
            max_tokens: -1,
        };
        assert_eq!(neg.validate().unwrap_err().status_code, 422);
        let big = UserTemplateRequest {
            id: String::new(),
            name: "ok".into(),
            system_prompt: String::new(),
            temperature: 1.0,
            max_tokens: 65537,
        };
        assert_eq!(big.validate().unwrap_err().status_code, 422);

        // Boundaries are inclusive (ge/le).
        let edges = UserTemplateRequest {
            id: String::new(),
            name: "x".repeat(100),
            system_prompt: "z".repeat(10000),
            temperature: 2.0,
            max_tokens: 65536,
        };
        assert!(edges.validate().is_ok());
        let zero_edges = UserTemplateRequest {
            id: String::new(),
            name: "a".into(),
            system_prompt: String::new(),
            temperature: 0.0,
            max_tokens: 0,
        };
        assert!(zero_edges.validate().is_ok());
    }

    #[test]
    fn model_dump_key_order_matches_pydantic() {
        let req = UserTemplateRequest {
            id: "user-abc".into(),
            name: "Sage".into(),
            system_prompt: "be wise".into(),
            temperature: 0.7,
            max_tokens: 512,
        };
        let dumped = req.model_dump();
        // Declaration order: id, name, system_prompt, temperature, max_tokens.
        let keys: Vec<&str> = dumped.as_object().unwrap().keys().map(String::as_str).collect();
        assert_eq!(
            keys,
            vec!["id", "name", "system_prompt", "temperature", "max_tokens"]
        );
        assert_eq!(dumped["id"], json!("user-abc"));
        assert_eq!(dumped["temperature"], json!(0.7));
        assert_eq!(dumped["max_tokens"], json!(512));
    }

    #[test]
    fn synthesized_id_shape() {
        // `f"user-{uuid.uuid4().hex[:8]}"` — hyphen-free uuid sliced to 8 hex chars.
        let hex8: String = uuid::Uuid::new_v4()
            .simple()
            .to_string()
            .chars()
            .take(8)
            .collect();
        let id = format!("user-{hex8}");
        assert!(id.starts_with("user-"));
        assert_eq!(id.len(), "user-".len() + 8);
        // The slice is pure lowercase hex.
        assert!(hex8.chars().all(|c| c.is_ascii_hexdigit()));
        assert!(!hex8.contains('-'));
    }

    #[test]
    fn preset_update_request_passes_through_to_update_custom() {
        // Sanity: the PresetUpdateRequest used by /api/presets/custom carries the seven
        // fields update_custom consumes, with pydantic-faithful defaults.
        let req: PresetUpdateRequest = serde_json::from_value(json!({})).unwrap();
        assert_eq!(req.name, "");
        assert!(req.enabled); // Field(True)
        assert_eq!(req.temperature, 1.0);
        assert_eq!(req.max_tokens, 0);
        assert_eq!(req.system_prompt, "");
        assert_eq!(req.inject_prefix, "");
        assert_eq!(req.inject_suffix, "");
    }

    #[test]
    fn groups_get_default_is_empty_array() {
        // `data.get("groups", [])` — missing key defaults to []; an array passes through.
        let missing = json!({});
        let g: Vec<Value> = match missing.get("groups") {
            Some(Value::Array(a)) => a.clone(),
            _ => Vec::new(),
        };
        assert!(g.is_empty());

        let present = json!({"groups": [{"name": "team"}]});
        let g2: Vec<Value> = match present.get("groups") {
            Some(Value::Array(a)) => a.clone(),
            _ => Vec::new(),
        };
        assert_eq!(g2.len(), 1);
        assert_eq!(g2[0]["name"], json!("team"));
    }
}
