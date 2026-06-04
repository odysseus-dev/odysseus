// routes/skills_routes.rs  <- routes/skills_routes.py
//! REST API for the Skills system (routes WAVE 3, account/user-data cluster).
//!
//! The on-disk format is SKILL.md (frontmatter + structured body) under
//! `data/skills/<category>/<name>/`. Old shape (`title`, `problem`, `solution`,
//! `steps`) still accepted on input — they're translated to the new fields
//! (`description`, `when_to_use`, `body_extra`, `procedure`).
//!
//! ## Classification: PORT_PARTIAL (feature_gate `web`)
//!
//! Everything synchronous is ported faithfully:
//!   * CRUD: `GET ""` / `POST /add` / `GET /:skill_id` / `PUT /:skill_id` /
//!     `DELETE /:skill_id`
//!   * index: `GET /index`
//!   * markdown: `GET`/`POST /:skill_id/markdown`
//!   * search: `POST /search`
//!   * builtin: `GET /builtin`, `GET`/`PUT`/`DELETE /builtin/:name`
//!
//! over the already-ported [`crate::services::memory::skills::SkillsManager`]
//! (reached through `AppState.skills_manager`), [`crate::services::memory::skill_format`]
//! (`Skill::from_markdown` + `slugify`), the F3 `require_admin` auth-adapter, and the
//! `skill_added` [`crate::src::event_bus::fire_event`] hook.
//!
//! ### Builtin endpoints — sourced from `agent_loop::tool_sections()`
//!
//! The Python `/builtin*` handlers iterate `src.agent_loop.TOOL_SECTIONS` and read
//! `get_builtin_overrides()`. The Rust `TOOL_SECTIONS` is a module-private `static` in
//! [`crate::src::agent_loop`]; a minimal public read-only accessor —
//! [`crate::src::agent_loop::tool_sections`] — exposes it (`&'static IndexMap<&str,
//! &str>`), and [`crate::src::agent_loop::get_builtin_overrides`] is already public. So
//! the read-only `GET /builtin` (`list_builtin_skills`) and `GET /builtin/:name`
//! (`get_builtin_skill`) are now ported FAITHFULLY against those: the section map is
//! iterated and `_clean`ed exactly as Python does, the override lookups are applied, and
//! the `{name!r}`-quoted 404 is reproduced for an unknown built-in name. The Rust
//! `TOOL_SECTIONS` only ever uses single-string keys (never the tuple form the Python
//! `isinstance(key, tuple)` branch guards against), so each key maps to exactly one
//! name. `get_builtin_overrides()` is hardcoded to `{}` in this build (its `src.settings`
//! backing is unported — see its docstring), so `is_overridden` is always `false` and the
//! effective text is always the shipped default; that is the same value the Python
//! `except`-free path computes when no override is stored.
//!
//! The `settings.json` override persistence in `set_builtin_override` /
//! `reset_builtin_override` is ported against [`crate::src::settings`]
//! (`load_settings`/`save_settings` are both ported). The write-side
//! `set_builtin_override` membership check is now wired to the same
//! [`crate::src::agent_loop::tool_sections`] accessor the GETs use: the `valid` set is
//! built by flattening the section keys (single-string keys in Rust, so each key is one
//! name), the `{name!r}`-quoted 404 is reproduced for an unknown name, the JSON body's
//! `text` is validated, and the override is written into the `builtin_tool_overrides`
//! dict and saved. Both write endpoints persist faithfully.
//!
//! ### Background test/audit orchestration — NOW LIVE (Gap #8)
//!
//! `POST /:skill_id/test`, `GET /:skill_id/test-status`, `POST /audit-all`,
//! `GET /audit-all/status`, and `POST /audit-all/cancel` drive the
//! `_run_skill_test_job` / `_run_audit_all_job` pipelines. Those pipelines (the
//! `stream_agent_loop` run, the LLM-as-judge grading, the confidence nudge, the
//! self-edit/teacher/flag ladder, and the two module-global in-memory job stores) are
//! ported in the sibling [`crate::routes::skills_pipeline`] module; this file is the thin
//! HTTP layer over them — it resolves the model, builds the per-request job key, seeds the
//! job, `tokio::spawn`s the long-running driver, and shapes the poll responses. None of the
//! orchestration lives here; it is all reached through `skills_pipeline`'s `pub(crate)` API
//! (`test_key`/`audit_key`, `seed_test_job`/`seed_audit_job`, `get_test_job`/
//! `get_audit_job`, `audit_running`, `cancel_audit_job`, `run_skill_test_job`/
//! `run_audit_all_job`, `resolve_audit_models`, `skill_test_task`).
//!
//! `test_skill` resolves the worker via the real `resolve_endpoint("default", None)`
//! (Python passes no owner, so `get_user_setting` collapses to `get_setting`,
//! byte-faithful) and falls back to the request body's `endpoint_url`/`model`/`headers`
//! only when the resolver returns nothing — exactly the Python `url = url or
//! body.get("endpoint_url")` cascade (skills_routes.py:1285-1288). When everything is
//! still unset it raises the same `HTTPException(400, "No model configured ...")` the
//! Python `if not url or not model:` guard reaches. On success it seeds
//! `_skill_test_jobs[(user, name)]` and spawns `_run_skill_test_job`, returning
//! `{ok, status:"running", skill, model}` immediately — the run executes server-side and
//! survives the modal closing, polled via `GET /:skill_id/test-status`.
//!
//! `audit_all_skills` mirrors the running-job guard (returns the live snapshot instead of
//! starting a second run), resolves worker+teacher via
//! `skills_pipeline::resolve_audit_models` (its `resolve_endpoint("utility")` + the
//! optional Settings → Teacher Model spec), builds the name list per `scope`
//! (`all`/`selected`/`unchecked`), seeds `_skill_audit_jobs[(user,)]`, and spawns
//! `_run_audit_all_job`. The two status pollers read the real store now and shape the
//! Python poll dicts; `audit_cancel` flips the cooperative `cancel` flag.
//!
//! MODEL-DRIFT NORMALIZATION (`list_model_ids` served-model swap, skills_routes.py:1292-1302)
//! is NOT ported: the Rust `crate::src::llm_core::list_model_ids` is a different,
//! network-free string normalizer (no `GET /models` query), so `test_skill` (and the audit
//! resolver) use the resolved model verbatim — byte-equivalent to the Python `except`/
//! empty-`avail` branch where no swap is applied.


use axum::extract::{Path, State};
use axum::http::HeaderMap;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post, put};
use axum::{Extension, Json, Router};
use once_cell::sync::Lazy;
use regex::Regex;
use serde::Deserialize;
use serde_json::{json, Map, Value};

use crate::core::middleware::INTERNAL_TOOL_HEADER;
use crate::routes::auth_adapter;
use crate::routes::skills_pipeline;
use crate::routes::{AppState, CurrentUser, HttpException};
use crate::services::memory::skill_format::{slugify, Skill};

// ===========================================================================
// Request bodies (the pydantic models)
// ===========================================================================

/// `class SkillAddRequest(BaseModel)` — the `/api/skills/add` body.
///
/// New schema fields are preferred; the old (`title`/`problem`/`solution`/`steps`)
/// shape is still accepted and translated by `SkillsManager.add_skill`. The pydantic
/// `Field(..., max_length=...)` constraints are not enforced by serde (pydantic surfaces
/// over-length values as 422, but the frontend never sends them; the manager truncates
/// nothing, so the only observable effect of skipping the check is that an over-length
/// payload is accepted rather than 422'd — recorded here so the divergence is explicit).
#[derive(Debug, Clone, Deserialize)]
struct SkillAddRequest {
    // New schema (preferred)
    /// `name: Optional[str] = Field(None, max_length=80)`.
    #[serde(default)]
    name: Option<String>,
    /// `description: Optional[str] = Field(None, max_length=200)`.
    #[serde(default)]
    description: Option<String>,
    /// `category: str = Field("general", max_length=40)`.
    #[serde(default = "default_category")]
    category: String,
    /// `tags: List[str] = Field(default_factory=list)`.
    #[serde(default)]
    tags: Vec<String>,
    /// `platforms: List[str] = Field(default_factory=list)`.
    #[serde(default)]
    platforms: Vec<String>,
    /// `requires_toolsets: List[str] = Field(default_factory=list)`.
    #[serde(default)]
    requires_toolsets: Vec<String>,
    /// `fallback_for_toolsets: List[str] = Field(default_factory=list)`.
    #[serde(default)]
    fallback_for_toolsets: Vec<String>,
    /// `when_to_use: Optional[str] = Field(None, max_length=2000)`.
    #[serde(default)]
    when_to_use: Option<String>,
    /// `procedure: List[str] = Field(default_factory=list)`.
    #[serde(default)]
    procedure: Vec<String>,
    /// `pitfalls: List[str] = Field(default_factory=list)`.
    #[serde(default)]
    pitfalls: Vec<String>,
    /// `verification: List[str] = Field(default_factory=list)`.
    #[serde(default)]
    verification: Vec<String>,
    /// `status: str = "draft"`.
    #[serde(default = "default_status")]
    status: String,
    /// `version: str = "1.0.0"`.
    #[serde(default = "default_version")]
    version: String,
    /// `confidence: float = 0.8`.
    #[serde(default = "default_confidence")]
    confidence: f64,
    /// `source: str = "user"` — manual adds are human-authored, which exempts them
    /// from auto-dedup and cap-eviction in `add_skill`.
    #[serde(default = "default_source")]
    source: String,
    /// `teacher_model: Optional[str] = None`.
    #[serde(default)]
    teacher_model: Option<String>,
    /// `session_id: Optional[str] = None`.
    #[serde(default)]
    session_id: Option<String>,

    // Old schema (back-compat)
    /// `title: Optional[str] = Field(None, max_length=200)`.
    #[serde(default)]
    title: Option<String>,
    /// `problem: Optional[str] = Field(None, max_length=2000)`.
    #[serde(default)]
    problem: Option<String>,
    /// `solution: Optional[str] = Field(None, max_length=5000)`.
    #[serde(default)]
    solution: Option<String>,
    /// `steps: List[str] = Field(default_factory=list)`.
    #[serde(default)]
    steps: Vec<String>,
}

fn default_category() -> String {
    "general".to_string()
}
fn default_status() -> String {
    "draft".to_string()
}
fn default_version() -> String {
    "1.0.0".to_string()
}
fn default_confidence() -> f64 {
    0.8
}
fn default_source() -> String {
    "user".to_string()
}

/// `class SkillUpdateRequest(BaseModel)` — the `PUT /api/skills/{skill_id}` body.
///
/// Every field is `Optional[...] = None`; the handler passes `body.dict(exclude_none=True)`
/// to `SkillsManager.update_skill`, so only the keys the caller actually sent are
/// forwarded. The Rust port deserializes into a `serde_json::Value` object (rather than a
/// fixed struct) so it can reproduce `exclude_none=True` precisely: an explicit
/// `null` is dropped, a present scalar/array key is kept, and the resulting [`Map`] is
/// handed to `update_skill` with the same key set the Python builds. See
/// [`build_update_map`].
struct SkillUpdateRequest;

/// `body.dict(exclude_none=True)` — keep only the keys the caller sent with a
/// non-`null` value, restricted to the `SkillUpdateRequest` field set (pydantic ignores
/// unknown keys when building the model, and `exclude_none` drops `None`s).
///
/// The field set mirrors the pydantic model declaration order so the resulting `updates`
/// map iterates in the same order Python's `dict(exclude_none=True)` does (insertion
/// order = declaration order for a pydantic `.dict()`); `update_skill` itself is
/// order-insensitive, but preserving it keeps the port faithful.
fn build_update_map(body: &Value) -> Map<String, Value> {
    // The `SkillUpdateRequest` fields, in declaration order.
    const FIELDS: &[&str] = &[
        "name",
        "description",
        "category",
        "tags",
        "platforms",
        "requires_toolsets",
        "fallback_for_toolsets",
        "when_to_use",
        "procedure",
        "pitfalls",
        "verification",
        "status",
        "version",
        "confidence",
        "body_extra",
        // Old shape
        "title",
        "problem",
        "solution",
        "steps",
    ];
    let mut updates = Map::new();
    let obj = match body.as_object() {
        Some(o) => o,
        None => return updates,
    };
    for key in FIELDS {
        match obj.get(*key) {
            // `exclude_none=True` drops a field whose value is `None`.
            None | Some(Value::Null) => {}
            Some(v) => {
                updates.insert((*key).to_string(), v.clone());
            }
        }
    }
    updates
}

// ===========================================================================
// Auth glue
// ===========================================================================

/// `_owner(request) -> get_current_user(request)` — the closure the Python factory
/// defines. `request.state.current_user`, `None` when auth is disabled / single-user
/// (the load-bearing `None` case the handlers branch on).
fn owner_of(user: Option<&Extension<CurrentUser>>) -> Option<String> {
    user.map(|Extension(CurrentUser(u))| u.clone())
}

/// `_verify_owner(skill, user)` — strict ownership check.
///
/// `if user is None: return` (auth disabled / single-user — owns everything). Otherwise
/// SECURITY: the skill's `owner` field must equal the user exactly; a missing/`null`
/// owner is treated as not-owned (so an un-stamped legacy skill is NOT mutable by an
/// arbitrary authenticated user). A mismatch raises `404 "Skill not found"` (NOT 403, so
/// existence isn't leaked).
fn verify_owner(skill: &Map<String, Value>, user: Option<&str>) -> Result<(), HttpException> {
    let user = match user {
        None => return Ok(()),
        Some(u) => u,
    };
    // `if skill.get("owner") != user: raise HTTPException(404, "Skill not found")`
    let owner = match skill.get("owner") {
        Some(Value::String(o)) => Some(o.as_str()),
        _ => None,
    };
    if owner != Some(user) {
        return Err(HttpException::new(404, "Skill not found"));
    }
    Ok(())
}

/// `_fire_skill_added(user)` — fire the `skill_added` event (the event-driven Skills
/// Audit pipeline subscribes to it). Best-effort; failures are swallowed (Python logs at
/// debug). Mirrors `from src.event_bus import fire_event; fire_event("skill_added", user)`.
fn fire_skill_added(user: Option<&str>) {
    crate::src::event_bus::fire_event("skill_added", user);
}

/// `Depends(require_admin)` — the per-handler admin gate (used by the `/builtin/:name`
/// PUT/DELETE write endpoints). Reads `X-Odysseus-Internal-Token` + the stamped
/// `current_user`, then delegates to the F3 [`auth_adapter::require_admin`].
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
// Router factory (`setup_skills_routes`)
// ===========================================================================

/// `setup_skills_routes(skills_manager) -> APIRouter(prefix="/api/skills", tags=["skills"])`.
///
/// app.py registers this as include-router #7. The Python factory takes `skills_manager`
/// as an argument; the Rust factory takes no params and reaches the manager through
/// `AppState.skills_manager` (the canonical State-not-factory-args convention).
///
/// Route order mirrors the Python decorator order. The static segments (`/index`,
/// `/builtin`, `/add`, `/audit-all`, `/search`) and the dynamic `/:skill_id` coexist:
/// matchit 0.7 (axum 0.7) gives a static segment priority over a sibling param at the
/// same position, so `GET /api/skills/index` resolves to `get_index` while
/// `GET /api/skills/<name>` resolves to `get_skill`. Dynamic captures use the COLON form.
pub fn setup_skills_routes() -> Router<AppState> {
    Router::new()
        // `@router.get("")` -> `/api/skills`
        .route("/api/skills", get(list_skills))
        // `@router.get("/index")`
        .route("/api/skills/index", get(get_index))
        // `@router.get("/builtin")`
        .route("/api/skills/builtin", get(list_builtin_skills))
        // `@router.get/put/delete("/builtin/{name}")`
        .route(
            "/api/skills/builtin/:name",
            get(get_builtin_skill)
                .put(set_builtin_override)
                .delete(reset_builtin_override),
        )
        // `@router.post("/add")`
        .route("/api/skills/add", post(add_skill))
        // `@router.post("/audit-all")`
        .route("/api/skills/audit-all", post(audit_all_skills))
        // `@router.get("/audit-all/status")`
        .route("/api/skills/audit-all/status", get(audit_status))
        // `@router.post("/audit-all/cancel")`
        .route("/api/skills/audit-all/cancel", post(audit_cancel))
        // `@router.post("/search")`
        .route("/api/skills/search", post(search_skills))
        // `@router.get("/{skill_id}")`
        .route("/api/skills/:skill_id", get(get_skill))
        // `@router.get/post("/{skill_id}/markdown")`
        .route(
            "/api/skills/:skill_id/markdown",
            get(get_skill_markdown).post(save_skill_markdown),
        )
        // `@router.post("/{skill_id}/test")`
        .route("/api/skills/:skill_id/test", post(test_skill))
        // `@router.get("/{skill_id}/test-status")`
        .route("/api/skills/:skill_id/test-status", get(test_skill_status))
        // `@router.put("/{skill_id}")` + `@router.delete("/{skill_id}")`
        .route(
            "/api/skills/:skill_id",
            put(update_skill).delete(delete_skill),
        )
}

// ===========================================================================
// Handlers — synchronous (fully ported)
// ===========================================================================

/// `GET /api/skills` — list the caller's skills.
///
/// ```python
/// @router.get("")
/// async def list_skills(request: Request):
///     user = _owner(request)
///     skills = skills_manager.load(owner=user)
///     return {"skills": skills, "count": len(skills)}
/// ```
async fn list_skills(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    let user = owner_of(user.as_ref());
    let skills = state.skills_manager.load(user.as_deref());
    let count = skills.len();
    Ok(Json(json!({
        "skills": skills.into_iter().map(Value::Object).collect::<Vec<_>>(),
        "count": count,
    }))
    .into_response())
}

/// `GET /api/skills/index` — the lightweight `[{name, description, category, status}]`
/// list the agent's system prompt sees.
///
/// ```python
/// @router.get("/index")
/// async def get_index(request: Request):
///     user = _owner(request)
///     idx = skills_manager.index_for(owner=user)
///     return {"index": idx, "count": len(idx)}
/// ```
///
/// `index_for(owner=user)` — the Rust method also takes `active_toolsets` / `platform`,
/// which the Python call leaves at their defaults (`None`), so we pass `None, None`.
async fn get_index(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    let user = owner_of(user.as_ref());
    let idx = state.skills_manager.index_for(user.as_deref(), None, None);
    let count = idx.len();
    Ok(Json(json!({
        "index": idx.into_iter().map(Value::Object).collect::<Vec<_>>(),
        "count": count,
    }))
    .into_response())
}

/// `GET /api/skills/builtin` — read-only list of the agent's built-in tool capabilities,
/// sourced from `agent_loop.TOOL_SECTIONS` (the same descriptions the model is given).
///
/// ```python
/// @router.get("/builtin")
/// async def list_builtin_skills(request: Request):
///     import re
///     def _clean(raw): ...
///     try:
///         from src.agent_loop import TOOL_SECTIONS, get_builtin_overrides
///     except Exception as e:
///         return {"builtin": [], "count": 0, "error": str(e)}
///     overrides = get_builtin_overrides()
///     out = []
///     for key, raw in TOOL_SECTIONS.items():
///         names = key if isinstance(key, tuple) else (key,)
///         for nm in names:
///             if isinstance(nm, str):
///                 overridden = nm in overrides
///                 eff = overrides.get(nm, raw)
///                 out.append({"name": nm, "description": _clean(eff), "is_overridden": overridden})
///     out.sort(key=lambda x: x["name"])
///     return {"builtin": out, "count": len(out)}
/// ```
///
/// `TOOL_SECTIONS` is reached through [`crate::src::agent_loop::tool_sections`]; the import
/// never fails in Rust, so the `except` fallback shape is unreachable. The Rust map only
/// ever has single-string keys, so the `isinstance(key, tuple)` branch collapses to the
/// `(key,)` single-name path. `get_builtin_overrides()` is hardcoded to `{}` here, so
/// `is_overridden` is always `false` and `eff` is always the shipped default.
async fn list_builtin_skills() -> Result<Response, HttpException> {
    // `from src.agent_loop import TOOL_SECTIONS, get_builtin_overrides`
    let overrides = crate::src::agent_loop::get_builtin_overrides();
    // `out = []`
    let mut out: Vec<Value> = Vec::new();
    // `for key, raw in TOOL_SECTIONS.items():` — Rust keys are always single strings.
    for (nm, raw) in crate::src::agent_loop::tool_sections().iter() {
        // `overridden = nm in overrides`
        let overridden = overrides.contains_key(*nm);
        // `eff = overrides.get(nm, raw)` — override text if present (a string), else the default.
        let eff: &str = match overrides.get(*nm).and_then(Value::as_str) {
            Some(s) => s,
            None => raw,
        };
        // `out.append({"name": nm, "description": _clean(eff), "is_overridden": overridden})`
        out.push(json!({
            "name": *nm,
            "description": clean_builtin_desc(eff),
            "is_overridden": overridden,
        }));
    }
    // `out.sort(key=lambda x: x["name"])`
    out.sort_by(|a, b| {
        let an = a.get("name").and_then(Value::as_str).unwrap_or("");
        let bn = b.get("name").and_then(Value::as_str).unwrap_or("");
        an.cmp(bn)
    });
    let count = out.len();
    Ok(Json(json!({"builtin": out, "count": count})).into_response())
}

/// `GET /api/skills/builtin/{name}` — full text of a built-in tool's instruction block —
/// the override if one is set, plus the shipped default (for the revert button).
///
/// ```python
/// @router.get("/builtin/{name}")
/// async def get_builtin_skill(name: str, request: Request):
///     try:
///         from src.agent_loop import TOOL_SECTIONS, get_builtin_overrides
///     except Exception as e:
///         raise HTTPException(500, str(e))
///     default = None
///     for key, raw in TOOL_SECTIONS.items():
///         names = key if isinstance(key, tuple) else (key,)
///         if name in names: default = raw; break
///     if default is None: raise HTTPException(404, f"No built-in tool named {name!r}")
///     overrides = get_builtin_overrides()
///     return {
///         "name": name,
///         "text": overrides.get(name, default),
///         "default": default,
///         "is_overridden": name in overrides,
///     }
/// ```
///
/// `TOOL_SECTIONS` is reached through [`crate::src::agent_loop::tool_sections`] (import
/// never fails in Rust, so the `except`→500 arm is unreachable). The Rust map only has
/// single-string keys, so the membership test is a direct key lookup. With
/// `get_builtin_overrides()` hardcoded to `{}`, `text` is always the default and
/// `is_overridden` is always `false`. The 404 detail uses Python `repr`-style quoting of
/// `name` (`{name!r}`).
async fn get_builtin_skill(Path(name): Path<String>) -> Result<Response, HttpException> {
    // `from src.agent_loop import TOOL_SECTIONS, get_builtin_overrides`
    let sections = crate::src::agent_loop::tool_sections();
    // `default = None; for key, raw in TOOL_SECTIONS.items(): if name in (key,): default = raw; break`
    let default = sections.get(name.as_str()).copied();
    // `if default is None: raise HTTPException(404, f"No built-in tool named {name!r}")`
    let default = match default {
        Some(d) => d,
        None => {
            return Err(HttpException::new(
                404,
                format!("No built-in tool named {}", py_repr(&name)),
            ))
        }
    };
    // `overrides = get_builtin_overrides()`
    let overrides = crate::src::agent_loop::get_builtin_overrides();
    // `text = overrides.get(name, default)` — override value if present, else the default.
    let text: Value = match overrides.get(&name) {
        Some(v) => v.clone(),
        None => Value::String(default.to_string()),
    };
    // `is_overridden = name in overrides`
    let is_overridden = overrides.contains_key(&name);
    Ok(Json(json!({
        "name": name,
        "text": text,
        "default": default,
        "is_overridden": is_overridden,
    }))
    .into_response())
}

/// `PUT /api/skills/builtin/{name}` — save a user override for a built-in tool's
/// instruction block.
///
/// ```python
/// @router.put("/builtin/{name}")
/// async def set_builtin_override(name: str, request: Request):
///     require_admin(request)
///     from src.agent_loop import TOOL_SECTIONS
///     valid = set()
///     for key in TOOL_SECTIONS: valid.update(key if isinstance(key, tuple) else (key,))
///     if name not in valid: raise HTTPException(404, f"No built-in tool named {name!r}")
///     body = await request.json()
///     text = (body or {}).get("text", "")
///     if not isinstance(text, str) or not text.strip(): raise HTTPException(400, "text is required")
///     ... save_settings(...) ...
///     return {"ok": True, "name": name, "is_overridden": True}
/// ```
///
/// `require_admin` runs first (verbatim). The `valid` set is built from
/// [`crate::src::agent_loop::tool_sections`] by flattening every key (single-string keys
/// in Rust, so each is one name) — the same `isinstance(key, tuple)` flatten the Python
/// does, collapsed to the single-name path. An unknown name raises
/// `404 "No built-in tool named '<name>'"` (`{name!r}` quoting). The JSON body's `text`
/// must be a non-blank string (`400 "text is required"`). The `settings.json` override
/// persistence is ported against [`crate::src::settings`]: the `builtin_tool_overrides`
/// dict is fetched/created (replaced with `{}` if the stored value is not a dict),
/// `ov[name] = text` is set, and the settings are saved.
async fn set_builtin_override(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Path(name): Path<String>,
    Json(body): Json<Value>,
) -> Result<Response, HttpException> {
    // `require_admin(request)`
    admin_gate(&state, &headers, user.as_deref())?;
    // `from src.agent_loop import TOOL_SECTIONS`
    // `valid = set(); for key in TOOL_SECTIONS: valid.update(key if isinstance(key, tuple) else (key,))`
    // Rust keys are always single strings, so each key contributes exactly one name.
    let valid: std::collections::HashSet<&str> =
        crate::src::agent_loop::tool_sections().keys().copied().collect();
    // `if name not in valid: raise HTTPException(404, f"No built-in tool named {name!r}")`
    if !valid.contains(name.as_str()) {
        return Err(HttpException::new(
            404,
            format!("No built-in tool named {}", py_repr(&name)),
        ));
    }
    // `body = await request.json(); text = (body or {}).get("text", "")`
    let text = match body.get("text") {
        Some(Value::String(s)) => s.clone(),
        // `not isinstance(text, str)` (missing key defaults to "", a str whose strip is empty;
        // a present non-string value also fails the isinstance check) -> 400 either way.
        _ => return Err(HttpException::new(400, "text is required")),
    };
    // `if not isinstance(text, str) or not text.strip(): raise HTTPException(400, "text is required")`
    if text.trim().is_empty() {
        return Err(HttpException::new(400, "text is required"));
    }
    // `settings = load_settings()`
    let mut settings = crate::src::settings::load_settings();
    // `ov = settings.get("builtin_tool_overrides"); if not isinstance(ov, dict): ov = {}`
    let mut ov = match settings.get("builtin_tool_overrides") {
        Some(Value::Object(o)) => o.clone(),
        _ => Map::new(),
    };
    // `ov[name] = text`
    ov.insert(name.clone(), Value::String(text));
    // `settings["builtin_tool_overrides"] = ov`
    settings.insert("builtin_tool_overrides".to_string(), Value::Object(ov));
    // `save_settings(settings)`
    let _ = crate::src::settings::save_settings(&settings);
    // `return {"ok": True, "name": name, "is_overridden": True}`
    Ok(Json(json!({"ok": true, "name": name, "is_overridden": true})).into_response())
}

/// `DELETE /api/skills/builtin/{name}` — revert a built-in tool to its shipped block.
///
/// ```python
/// @router.delete("/builtin/{name}")
/// async def reset_builtin_override(name: str, request: Request):
///     require_admin(request)
///     from src.settings import load_settings, save_settings
///     settings = load_settings()
///     ov = settings.get("builtin_tool_overrides")
///     if isinstance(ov, dict) and name in ov:
///         del ov[name]; settings["builtin_tool_overrides"] = ov; save_settings(settings)
///     return {"ok": True, "name": name, "is_overridden": False}
/// ```
///
/// Unlike `set_builtin_override`, `reset_builtin_override` does NOT touch `TOOL_SECTIONS`
/// — it operates purely on `settings.json`. So this one is ported VERBATIM against
/// [`crate::src::settings`]: `require_admin`, then delete the `name` key from the
/// `builtin_tool_overrides` dict if present, save, and return the success shape.
async fn reset_builtin_override(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Path(name): Path<String>,
) -> Result<Response, HttpException> {
    // `require_admin(request)`
    admin_gate(&state, &headers, user.as_deref())?;
    // `settings = load_settings()`
    let mut settings = crate::src::settings::load_settings();
    // `ov = settings.get("builtin_tool_overrides")`
    // `if isinstance(ov, dict) and name in ov:`
    if let Some(Value::Object(mut ov)) = settings.get("builtin_tool_overrides").cloned() {
        if ov.contains_key(&name) {
            // `del ov[name]`
            ov.remove(&name);
            // `settings["builtin_tool_overrides"] = ov`
            settings.insert("builtin_tool_overrides".to_string(), Value::Object(ov));
            // `save_settings(settings)`
            let _ = crate::src::settings::save_settings(&settings);
        }
    }
    Ok(Json(json!({"ok": true, "name": name, "is_overridden": false})).into_response())
}

/// `POST /api/skills/add` — create a human-authored skill.
///
/// ```python
/// @router.post("/add")
/// async def add_skill(request: Request, body: SkillAddRequest):
///     user = _owner(request)
///     entry = skills_manager.add_skill(... owner=user, ...)
///     if not entry.get("_deduped"):
///         _fire_skill_added(user)
///     return {"ok": True, "deduped": bool(entry.get("_deduped")), "skill": entry}
/// ```
async fn add_skill(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Json(body): Json<SkillAddRequest>,
) -> Result<Response, HttpException> {
    let user = owner_of(user.as_ref());

    // Translate the pydantic body into the manager's `AddSkill` kwargs bundle. The
    // manager distinguishes "not passed" (`None`, falls back to the old-schema alias)
    // from "passed empty" for `when_to_use`/`procedure`, so those stay `Option`.
    let args = crate::services::memory::skills::AddSkill {
        // New shape
        name: body.name,
        description: body.description,
        category: body.category,
        tags: Some(body.tags),
        platforms: Some(body.platforms),
        requires_toolsets: Some(body.requires_toolsets),
        fallback_for_toolsets: Some(body.fallback_for_toolsets),
        when_to_use: body.when_to_use,
        procedure: Some(body.procedure),
        pitfalls: Some(body.pitfalls),
        verification: Some(body.verification),
        status: body.status,
        version: body.version,
        confidence: body.confidence,
        source: body.source,
        teacher_model: body.teacher_model,
        session_id: body.session_id,
        owner: user.clone(),
        // Old shape (manager translates) — `body.title or ""`, etc.
        title: body.title.unwrap_or_default(),
        problem: body.problem.unwrap_or_default(),
        solution: body.solution.unwrap_or_default(),
        steps: Some(body.steps),
    };
    let entry = state.skills_manager.add_skill(args);

    // `if not entry.get("_deduped"): _fire_skill_added(user)`
    let deduped = matches!(entry.get("_deduped"), Some(Value::Bool(true)));
    if !deduped {
        fire_skill_added(user.as_deref());
    }
    Ok(Json(json!({
        "ok": true,
        "deduped": deduped,
        "skill": Value::Object(entry),
    }))
    .into_response())
}

/// `GET /api/skills/{skill_id}` — fetch a single skill by name or id.
///
/// ```python
/// @router.get("/{skill_id}")
/// async def get_skill(request: Request, skill_id: str):
///     user = _owner(request)
///     skills = skills_manager.load(owner=user)
///     for sk in skills:
///         if sk.get("name") == skill_id or sk.get("id") == skill_id: return sk
///     raise HTTPException(404, "Skill not found")
/// ```
async fn get_skill(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(skill_id): Path<String>,
) -> Result<Response, HttpException> {
    let user = owner_of(user.as_ref());
    let skills = state.skills_manager.load(user.as_deref());
    for sk in &skills {
        if name_or_id_matches(sk, &skill_id) {
            return Ok(Json(Value::Object(sk.clone())).into_response());
        }
    }
    Err(HttpException::new(404, "Skill not found"))
}

/// `GET /api/skills/{skill_id}/markdown` — raw SKILL.md text for the named skill.
///
/// ```python
/// @router.get("/{skill_id}/markdown")
/// async def get_skill_markdown(request: Request, skill_id: str):
///     user = _owner(request)
///     skills = skills_manager.load(owner=user)
///     match = next((s for s in skills if s.get("name") == skill_id or s.get("id") == skill_id), None)
///     if not match: raise HTTPException(404, "Skill not found")
///     _verify_owner(match, user)
///     md = skills_manager.read_skill_md(match.get("name"))
///     if md is None: raise HTTPException(404, "Skill source unavailable (legacy entry?)")
///     return {"name": match.get("name"), "markdown": md}
/// ```
async fn get_skill_markdown(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(skill_id): Path<String>,
) -> Result<Response, HttpException> {
    let user = owner_of(user.as_ref());
    let skills = state.skills_manager.load(user.as_deref());
    let matched = find_skill(&skills, &skill_id)
        .ok_or_else(|| HttpException::new(404, "Skill not found"))?;
    verify_owner(matched, user.as_deref())?;
    let name = get_str(matched, "name");
    // `md = skills_manager.read_skill_md(match.get("name"), owner=user)`
    match state.skills_manager.read_skill_md(&name, user.as_deref()) {
        // `if md is None: raise HTTPException(404, "Skill source unavailable (legacy entry?)")`
        None => Err(HttpException::new(
            404,
            "Skill source unavailable (legacy entry?)",
        )),
        Some(md) => Ok(Json(json!({"name": name, "markdown": md})).into_response()),
    }
}

/// `POST /api/skills/{skill_id}/markdown` — replace SKILL.md with new raw content
/// (parse + validate first).
///
/// ```python
/// @router.post("/{skill_id}/markdown")
/// async def save_skill_markdown(request: Request, skill_id: str):
///     from services.memory.skill_format import Skill, slugify
///     user = _owner(request)
///     body = await request.json()
///     new_content = body.get("markdown")
///     if not isinstance(new_content, str) or not new_content.strip():
///         raise HTTPException(400, "markdown is required")
///     skills = skills_manager.load(owner=user)
///     match = next(...); if not match: raise HTTPException(404, "Skill not found")
///     _verify_owner(match, user)
///     try: sk = Skill.from_markdown(new_content)
///     except Exception as e: raise HTTPException(400, f"Could not parse SKILL.md: {e}")
///     sk.name = slugify(sk.name or match.get("name"))
///     if not sk.owner: sk.owner = match.get("owner") or user
///     ok = skills_manager.update_skill(match.get("name"), {...})
///     if not ok: raise HTTPException(500, "Update failed")
///     if not match.get("audit_verdict"): _fire_skill_added(user)
///     return {"ok": True, "name": sk.name}
/// ```
///
/// `Skill::from_markdown` in Rust is total (never raises — it returns a best-effort
/// `Skill`), so the Python `except` arm is unreachable; the parse always yields a `Skill`.
async fn save_skill_markdown(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(skill_id): Path<String>,
    Json(body): Json<Value>,
) -> Result<Response, HttpException> {
    let user = owner_of(user.as_ref());
    // `new_content = body.get("markdown")`
    // `if not isinstance(new_content, str) or not new_content.strip(): raise HTTPException(400, ...)`
    let new_content = match body.get("markdown") {
        Some(Value::String(s)) if !s.trim().is_empty() => s.clone(),
        _ => return Err(HttpException::new(400, "markdown is required")),
    };
    let skills = state.skills_manager.load(user.as_deref());
    let matched = find_skill(&skills, &skill_id)
        .ok_or_else(|| HttpException::new(404, "Skill not found"))?
        .clone();
    verify_owner(&matched, user.as_deref())?;

    // `sk = Skill.from_markdown(new_content)` — total in Rust, so no parse 400.
    let mut sk = Skill::from_markdown(&new_content, None);
    // `sk.name = slugify(sk.name or match.get("name"))`
    let name_src = if sk.name.is_empty() {
        get_str(&matched, "name")
    } else {
        sk.name.clone()
    };
    sk.name = slugify(&name_src, "skill");
    // `if not sk.owner: sk.owner = match.get("owner") or user`
    if sk.owner.as_deref().map(str::is_empty).unwrap_or(true) {
        let match_owner = match matched.get("owner") {
            Some(Value::String(o)) if !o.is_empty() => Some(o.clone()),
            _ => None,
        };
        sk.owner = match_owner.or_else(|| user.clone());
    }

    // `ok = skills_manager.update_skill(match.get("name"), {...}, owner=user)`
    let updates = skill_to_update_map(&sk);
    let ok = state
        .skills_manager
        .update_skill(&get_str(&matched, "name"), &updates, user.as_deref());
    // `if not ok: raise HTTPException(500, "Update failed")`
    if !ok {
        return Err(HttpException::new(500, "Update failed"));
    }
    // `if not match.get("audit_verdict"): _fire_skill_added(user)`
    if !truthy(matched.get("audit_verdict")) {
        fire_skill_added(user.as_deref());
    }
    Ok(Json(json!({"ok": true, "name": sk.name})).into_response())
}

/// `PUT /api/skills/{skill_id}` — patch a skill from the `SkillUpdateRequest` body.
///
/// ```python
/// @router.put("/{skill_id}")
/// async def update_skill(request: Request, skill_id: str, body: SkillUpdateRequest):
///     user = _owner(request)
///     skills = skills_manager.load(owner=user)
///     match = next(...); if not match: raise HTTPException(404, "Skill not found")
///     _verify_owner(match, user)
///     updates = body.dict(exclude_none=True)
///     if not updates: return {"ok": True}
///     ok = skills_manager.update_skill(match.get("name"), updates)
///     if not ok: raise HTTPException(404, "Skill not found")
///     if not match.get("audit_verdict"): _fire_skill_added(user)
///     return {"ok": True}
/// ```
async fn update_skill(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(skill_id): Path<String>,
    Json(body): Json<Value>,
) -> Result<Response, HttpException> {
    let _ = SkillUpdateRequest; // the body shape this handler accepts (see build_update_map).
    let user = owner_of(user.as_ref());
    let skills = state.skills_manager.load(user.as_deref());
    let matched = find_skill(&skills, &skill_id)
        .ok_or_else(|| HttpException::new(404, "Skill not found"))?
        .clone();
    verify_owner(&matched, user.as_deref())?;

    // `updates = body.dict(exclude_none=True)`
    let updates = build_update_map(&body);
    // `if not updates: return {"ok": True}`
    if updates.is_empty() {
        return Ok(Json(json!({"ok": true})).into_response());
    }
    // `ok = skills_manager.update_skill(match.get("name"), updates, owner=user)`
    let ok = state
        .skills_manager
        .update_skill(&get_str(&matched, "name"), &updates, user.as_deref());
    // `if not ok: raise HTTPException(404, "Skill not found")`
    if !ok {
        return Err(HttpException::new(404, "Skill not found"));
    }
    // `if not match.get("audit_verdict"): _fire_skill_added(user)`
    if !truthy(matched.get("audit_verdict")) {
        fire_skill_added(user.as_deref());
    }
    Ok(Json(json!({"ok": true})).into_response())
}

/// `DELETE /api/skills/{skill_id}` — remove a skill.
///
/// ```python
/// @router.delete("/{skill_id}")
/// async def delete_skill(request: Request, skill_id: str):
///     user = _owner(request)
///     skills = skills_manager.load(owner=user)
///     match = next(...); if not match: raise HTTPException(404, "Skill not found")
///     _verify_owner(match, user)
///     ok = skills_manager.delete_skill(match.get("name"))
///     if not ok: raise HTTPException(404, "Skill not found")
///     return {"ok": True}
/// ```
async fn delete_skill(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(skill_id): Path<String>,
) -> Result<Response, HttpException> {
    let user = owner_of(user.as_ref());
    let skills = state.skills_manager.load(user.as_deref());
    let matched = find_skill(&skills, &skill_id)
        .ok_or_else(|| HttpException::new(404, "Skill not found"))?
        .clone();
    verify_owner(&matched, user.as_deref())?;
    // `ok = skills_manager.delete_skill(match.get("name"), owner=user)`
    let ok = state
        .skills_manager
        .delete_skill(&get_str(&matched, "name"), user.as_deref());
    if !ok {
        return Err(HttpException::new(404, "Skill not found"));
    }
    Ok(Json(json!({"ok": true})).into_response())
}

/// `POST /api/skills/search` — relevance search over the caller's skills.
///
/// ```python
/// @router.post("/search")
/// async def search_skills(request: Request):
///     body = await request.json()
///     query = body.get("query", "")
///     if not query.strip(): raise HTTPException(400, "query is required")
///     user = _owner(request)
///     skills = skills_manager.load(owner=user)
///     results = skills_manager.get_relevant_skills(query, skills, max_items=10)
///     return {"skills": results, "query": query, "count": len(results)}
/// ```
///
/// `get_relevant_skills(query, skills, max_items=10)` — the Rust method's `threshold` /
/// `min_confidence` are positional; the Python call leaves them at their defaults
/// (`0.3` / `0.0`), so we pass those.
async fn search_skills(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Json(body): Json<Value>,
) -> Result<Response, HttpException> {
    // `query = body.get("query", "")`
    let query = match body.get("query") {
        Some(Value::String(s)) => s.clone(),
        _ => String::new(),
    };
    // `if not query.strip(): raise HTTPException(400, "query is required")`
    if query.trim().is_empty() {
        return Err(HttpException::new(400, "query is required"));
    }
    let user = owner_of(user.as_ref());
    let skills = state.skills_manager.load(user.as_deref());
    // `get_relevant_skills(query, skills, max_items=10)` — defaults threshold=0.3, min_conf=0.0.
    let results = state
        .skills_manager
        .get_relevant_skills(&query, Some(skills), 0.3, 10, 0.0);
    let count = results.len();
    Ok(Json(json!({
        "skills": results.into_iter().map(Value::Object).collect::<Vec<_>>(),
        "query": query,
        "count": count,
    }))
    .into_response())
}

// ===========================================================================
// Handlers — background test/audit job kickoffs (NOW LIVE via skills_pipeline)
// ===========================================================================

/// `POST /api/skills/{skill_id}/test` — kick off a background skill test (agent run +
/// LLM judge).
///
/// ```python
/// @router.post("/{skill_id}/test")
/// async def test_skill(request: Request, skill_id: str):
///     user = _owner(request)
///     body = await request.json()
///     task = (body.get("task") or "").strip()
///     skills = skills_manager.load(owner=user)
///     match = next(...); if not match: raise HTTPException(404, "Skill not found")
///     _verify_owner(match, user)
///     name = match.get("name")
///     md = skills_manager.read_skill_md(name) or ""
///     if not task: task = _skill_test_task(match)
///     url, model, headers = resolve_endpoint("default")
///     if not url or not model:
///         url = url or ((body.get("endpoint_url") or "").strip() or None)
///         model = model or ((body.get("model") or "").strip() or None)
///         if headers is None and isinstance(body.get("headers"), dict):
///             headers = body.get("headers")
///     if not url or not model: raise HTTPException(400, "No model configured ...")
///     # list_model_ids drift-normalize ...   (SKIPPED — see below)
///     key = (user or "", name)
///     _skill_test_jobs[key] = {"status": "running", "task": task, "model": model,
///         "skill": name, "started": time.time(),
///         "log": [{"type": "skill_test_start", ...}], "verdict": None}
///     asyncio.create_task(_run_skill_test_job(key, name, md, task, url, model, headers, user, skills_manager))
///     return {"ok": True, "status": "running", "skill": name, "model": model}
/// ```
///
/// The skill-load / 404 / `_verify_owner` gates run before model resolution, faithful to
/// Python. The model resolves via `resolve_endpoint("default", None)` (owner=None, Python
/// passes none) with the body's `endpoint_url`/`model`/`headers` as the same fallback
/// cascade. The MODEL-DRIFT `list_model_ids` swap is SKIPPED (the Rust `list_model_ids` is
/// a network-free string normalizer, not a `GET /models` query — byte-equivalent to the
/// Python `except`/empty-`avail` branch where no swap applies). The job is seeded into the
/// `skills_pipeline` test store and `_run_skill_test_job` is spawned with an owned
/// `Arc<SkillsManager>` clone (the spawned future is `'static`), returning immediately.
async fn test_skill(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(skill_id): Path<String>,
    // `body = await request.json()` — an empty / non-JSON body parses to `{}`, matching the
    // Python `.get(...)` reads which tolerate a missing key.
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let body: Value = serde_json::from_slice(&raw_body).unwrap_or_else(|_| json!({}));
    let user = owner_of(user.as_ref());
    // `task = (body.get("task") or "").strip()`
    let mut task = body
        .get("task")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();

    let skills = state.skills_manager.load(user.as_deref());
    // `match = next(...); if not match: raise HTTPException(404, "Skill not found")`
    let matched = find_skill(&skills, &skill_id)
        .ok_or_else(|| HttpException::new(404, "Skill not found"))?
        .clone();
    // `_verify_owner(match, user)`
    verify_owner(&matched, user.as_deref())?;
    // `name = match.get("name")`
    let name = get_str(&matched, "name");
    // `md = skills_manager.read_skill_md(name, owner=user) or ""`
    let md = state
        .skills_manager
        .read_skill_md(&name, user.as_deref())
        .unwrap_or_default();

    // `if not task: task = _skill_test_task(match)`
    if task.is_empty() {
        task = skills_pipeline::skill_test_task(&matched);
    }

    // `url, model, headers = resolve_endpoint("default")` (no owner).
    let (mut url, mut model, mut headers) =
        crate::src::endpoint_resolver::resolve_endpoint("default", None);
    // `if not url or not model:` -> fall back to the request body's endpoint fields.
    if url.is_none() || model.is_none() {
        // `url = url or ((body.get("endpoint_url") or "").strip() or None)`
        if url.is_none() {
            let b = body.get("endpoint_url").and_then(Value::as_str).unwrap_or("").trim();
            if !b.is_empty() {
                url = Some(b.to_string());
            }
        }
        // `model = model or ((body.get("model") or "").strip() or None)`
        if model.is_none() {
            let b = body.get("model").and_then(Value::as_str).unwrap_or("").trim();
            if !b.is_empty() {
                model = Some(b.to_string());
            }
        }
        // `if headers is None and isinstance(body.get("headers"), dict): headers = body.get("headers")`
        if headers.is_none() {
            if let Some(Value::Object(h)) = body.get("headers") {
                let mut m: indexmap::IndexMap<String, String> = indexmap::IndexMap::new();
                for (k, v) in h {
                    if let Value::String(s) = v {
                        m.insert(k.clone(), s.clone());
                    }
                }
                headers = Some(m);
            }
        }
    }
    // `if not url or not model: raise HTTPException(400, "No model configured ...")`
    let (url, model) = match (url, model) {
        (Some(u), Some(m)) if !u.is_empty() && !m.is_empty() => (u, m),
        _ => {
            return Err(HttpException::new(
                400,
                "No model configured — set a Default or Utility model in Settings.",
            ))
        }
    };
    // MODEL-DRIFT `list_model_ids` swap SKIPPED — Rust `list_model_ids` is a network-free
    // string normalizer, not a `GET /models` query; byte-equivalent to the Python
    // `except`/empty-`avail` branch (no swap). `model` is used verbatim.

    // `key = (user or "", name)` + seed `_skill_test_jobs[key]` (running, log[skill_test_start]).
    let key = skills_pipeline::test_key(user.as_deref(), &name);
    skills_pipeline::seed_test_job(&key, &name, &task, &model);

    // `asyncio.create_task(_run_skill_test_job(...))` — the driver is `'static`, so the
    // spawned future owns an `Arc<SkillsManager>` clone (the pipeline fn takes `&sm`).
    let sm = state.skills_manager.clone();
    let headers = headers.unwrap_or_default();
    let owner = user.clone();
    let (job_key, job_name, job_md, job_task, job_url, job_model) =
        (key, name.clone(), md, task, url, model.clone());
    tokio::spawn(async move {
        skills_pipeline::run_skill_test_job(
            &job_key, &job_name, &job_md, &job_task, &job_url, &job_model, headers, owner, &sm,
        )
        .await;
    });

    // `return {"ok": True, "status": "running", "skill": name, "model": model}`
    Ok(Json(json!({"ok": true, "status": "running", "skill": name, "model": model})).into_response())
}

/// `GET /api/skills/{skill_id}/test-status` — current background-test state.
///
/// ```python
/// @router.get("/{skill_id}/test-status")
/// async def test_skill_status(request: Request, skill_id: str):
///     user = _owner(request)
///     skills = skills_manager.load(owner=user)
///     match = next(...)
///     name = (match or {}).get("name", skill_id)
///     job = _skill_test_jobs.get((user or "", name))
///     if not job: return {"status": "none"}
///     return {"status": job["status"], "task": job.get("task"), "model": job.get("model"),
///             "log": job.get("log", []), "verdict": job.get("verdict")}
/// ```
///
/// Reads the real `skills_pipeline` test store now. The `name` defaults to `skill_id` when
/// no skill matches (`(match or {}).get("name", skill_id)`) so a poll for a deleted skill
/// still maps to its job key.
async fn test_skill_status(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(skill_id): Path<String>,
) -> Result<Response, HttpException> {
    let user = owner_of(user.as_ref());
    let skills = state.skills_manager.load(user.as_deref());
    // `name = (match or {}).get("name", skill_id)`
    let name = match find_skill(&skills, &skill_id) {
        Some(m) => match m.get("name") {
            Some(Value::String(s)) => s.clone(),
            _ => skill_id.clone(),
        },
        None => skill_id.clone(),
    };
    let key = skills_pipeline::test_key(user.as_deref(), &name);
    // `job = _skill_test_jobs.get(...); if not job: return {"status": "none"}`
    match skills_pipeline::get_test_job(&key) {
        None => Ok(Json(json!({"status": "none"})).into_response()),
        Some(job) => Ok(Json(json!({
            "status": job.get("status").cloned().unwrap_or(Value::Null),
            "task": job.get("task").cloned().unwrap_or(Value::Null),
            "model": job.get("model").cloned().unwrap_or(Value::Null),
            "log": job.get("log").cloned().unwrap_or_else(|| Value::Array(vec![])),
            "verdict": job.get("verdict").cloned().unwrap_or(Value::Null),
        }))
        .into_response()),
    }
}

/// `POST /api/skills/audit-all` — kick off a background audit of skills.
///
/// ```python
/// @router.post("/audit-all")
/// async def audit_all_skills(request: Request):
///     user = _owner(request)
///     body = await request.json() if content-type startswith application/json else {}
///     scope = (body.get("scope") or "all").lower()
///     requested_names = body.get("names")
///     skip_audited = bool(body.get("skip_audited"))
///     key = (user or "",)
///     existing = _skill_audit_jobs.get(key)
///     if existing and existing.get("status") == "running":
///         return {"ok": True, "status": "running", "total": ..., "done": ..., "model": ...}
///     try: url, model, headers, teacher = _resolve_audit_models()
///     except ValueError as e: raise HTTPException(400, str(e))
///     skills = skills_manager.load(owner=user)
///     by_name = {s.get("name"): s for s in skills if s.get("name")}
///     if isinstance(requested_names, list): ... names ...; scope = "selected" if requested_names else scope
///     elif scope == "all": names = [... not skip_audited or not audit_verdict ...]
///     else: scope = "unchecked" if scope == "drafts" else scope; names = [... not published, not audit_verdict ...]
///     if not names: return {"ok": True, "status": "done", "total": 0, "results": [], "log": ["No skills to audit."]}
///     _skill_audit_jobs[key] = {... running ...}
///     task = asyncio.create_task(_run_audit_all_job(key, skills_manager, names, url, model, headers, teacher, user))
///     _skill_audit_jobs[key]["task"] = task
///     return {"ok": True, "status": "running", "total": len(names), "model": model}
/// ```
///
/// The running-job guard returns the live snapshot (no second run). `resolve_audit_models`
/// (in `skills_pipeline`) resolves worker (`resolve_endpoint("utility")`) + optional
/// teacher; its `Err` maps to the same `400`. The name list is built per `scope` exactly as
/// Python. No `task` handle is stored in the job (Rust has no `asyncio.Task.cancel()`
/// analog — cancellation is the cooperative `cancel` flag the loop checks per iteration; see
/// `audit_cancel`).
async fn audit_all_skills(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    // `body = await request.json() if content-type startswith application/json else {}`
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let body: Value = serde_json::from_slice(&raw_body).unwrap_or_else(|_| json!({}));
    let user = owner_of(user.as_ref());
    // `scope = (body.get("scope") or "all").lower()`
    let mut scope = {
        let s = body.get("scope").and_then(Value::as_str).unwrap_or("");
        if s.is_empty() { "all".to_string() } else { s.to_lowercase() }
    };
    // `requested_names = body.get("names")`
    let requested_names = body.get("names");
    // `skip_audited = bool(body.get("skip_audited"))`
    let skip_audited = truthy(body.get("skip_audited"));

    // `existing = _skill_audit_jobs.get(key); if existing and status == "running": return snapshot`
    let key = skills_pipeline::audit_key(user.as_deref());
    if let Some(existing) = skills_pipeline::audit_running(&key) {
        return Ok(Json(json!({
            "ok": true,
            "status": "running",
            "total": existing.get("total").cloned().unwrap_or_else(|| json!(0)),
            "done": existing.get("done").cloned().unwrap_or_else(|| json!(0)),
            "model": existing.get("model").cloned().unwrap_or(Value::Null),
        }))
        .into_response());
    }

    // `try: url, model, headers, teacher = _resolve_audit_models() except ValueError as e: raise 400`
    let (url, model, headers, teacher) = skills_pipeline::resolve_audit_models()
        .await
        .map_err(|e| HttpException::new(400, e))?;

    let skills = state.skills_manager.load(user.as_deref());
    // `by_name = {s.get("name"): s for s in skills if s.get("name")}`
    let mut by_name: std::collections::HashMap<String, &Map<String, Value>> =
        std::collections::HashMap::new();
    for s in &skills {
        if let Some(Value::String(nm)) = s.get("name") {
            if !nm.is_empty() {
                by_name.insert(nm.clone(), s);
            }
        }
    }

    // Build the name list per scope (the 3 Python branches).
    let names: Vec<String> = if let Some(Value::Array(req)) = requested_names {
        // `if isinstance(requested_names, list):`
        let mut names: Vec<String> = Vec::new();
        let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
        for raw in req {
            // `nm = str(raw or "").strip()`
            let nm = match raw {
                Value::String(s) => s.trim().to_string(),
                Value::Null => String::new(),
                other => other.to_string().trim().to_string(),
            };
            // `if not nm or nm in seen or nm not in by_name: continue`
            if nm.is_empty() || seen.contains(&nm) {
                continue;
            }
            let sk = match by_name.get(&nm) {
                Some(sk) => *sk,
                None => continue,
            };
            // `if scope not in ("all", "selected") and (status or "draft") == "published": continue`
            if scope != "all" && scope != "selected" {
                let status = match sk.get("status") {
                    Some(Value::String(s)) if !s.is_empty() => s.as_str(),
                    _ => "draft",
                };
                if status == "published" {
                    continue;
                }
            }
            // `if skip_audited and by_name[nm].get("audit_verdict"): continue`
            if skip_audited && truthy(sk.get("audit_verdict")) {
                continue;
            }
            names.push(nm.clone());
            seen.insert(nm);
        }
        // `scope = "selected" if requested_names else scope` — a non-empty list -> "selected".
        if !req.is_empty() {
            scope = "selected".to_string();
        }
        names
    } else if scope == "all" {
        // `names = [s.get("name") for s in skills if s.get("name") and (not skip_audited or not s.get("audit_verdict"))]`
        skills
            .iter()
            .filter_map(|s| match s.get("name") {
                Some(Value::String(nm)) if !nm.is_empty() => {
                    if !skip_audited || !truthy(s.get("audit_verdict")) {
                        Some(nm.clone())
                    } else {
                        None
                    }
                }
                _ => None,
            })
            .collect()
    } else {
        // `scope = "unchecked" if scope == "drafts" else scope`
        if scope == "drafts" {
            scope = "unchecked".to_string();
        }
        // `names = [s.get("name") for s in skills if s.get("name") and (status or "draft") != "published" and not s.get("audit_verdict")]`
        skills
            .iter()
            .filter_map(|s| match s.get("name") {
                Some(Value::String(nm)) if !nm.is_empty() => {
                    let status = match s.get("status") {
                        Some(Value::String(v)) if !v.is_empty() => v.as_str(),
                        _ => "draft",
                    };
                    if status != "published" && !truthy(s.get("audit_verdict")) {
                        Some(nm.clone())
                    } else {
                        None
                    }
                }
                _ => None,
            })
            .collect()
    };

    // `if not names: return {"ok": True, "status": "done", "total": 0, ...}`
    if names.is_empty() {
        return Ok(Json(json!({
            "ok": true,
            "status": "done",
            "total": 0,
            "results": [],
            "log": ["No skills to audit."],
        }))
        .into_response());
    }

    // Seed `_skill_audit_jobs[key]` (running, scope, model, teacher[1], total, log[...]).
    let teacher_model: Option<&str> = teacher.as_ref().map(|(_, m, _)| m.as_str());
    skills_pipeline::seed_audit_job(&key, &scope, &model, teacher_model, &names);

    // `asyncio.create_task(_run_audit_all_job(...))` — spawn the `'static` driver with an
    // owned `Arc<SkillsManager>` clone (no `task` handle stored; cancel is cooperative).
    let total = names.len();
    let sm = state.skills_manager.clone();
    let owner = user.clone();
    let (job_key, job_url, job_model) = (key, url, model.clone());
    tokio::spawn(async move {
        skills_pipeline::run_audit_all_job(
            &job_key, &sm, names, &job_url, &job_model, headers, teacher, owner,
        )
        .await;
    });

    // `return {"ok": True, "status": "running", "total": len(names), "model": model}`
    Ok(Json(json!({"ok": true, "status": "running", "total": total, "model": model})).into_response())
}

/// `GET /api/skills/audit-all/status` — current audit-job state.
///
/// ```python
/// @router.get("/audit-all/status")
/// async def audit_status(request: Request):
///     user = _owner(request)
///     job = _skill_audit_jobs.get((user or "",))
///     if not job: return {"status": "none"}
///     return {"status": job["status"], "scope": job.get("scope"), "total": ..., "done": ...,
///             "current": ..., "model": ..., "teacher": ..., "results": ..., "log": ...,
///             "started": ..., "finished": ...}
/// ```
///
/// Reads the real `skills_pipeline` audit store now (the 11-field poll shape).
async fn audit_status(
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    let user = owner_of(user.as_ref());
    let key = skills_pipeline::audit_key(user.as_deref());
    match skills_pipeline::get_audit_job(&key) {
        // `if not job: return {"status": "none"}`
        None => Ok(Json(json!({"status": "none"})).into_response()),
        Some(job) => {
            let g = |k: &str| job.get(k).cloned().unwrap_or(Value::Null);
            Ok(Json(json!({
                "status": g("status"),
                "scope": g("scope"),
                "total": job.get("total").cloned().unwrap_or_else(|| json!(0)),
                "done": job.get("done").cloned().unwrap_or_else(|| json!(0)),
                "current": g("current"),
                "model": g("model"),
                "teacher": g("teacher"),
                "results": job.get("results").cloned().unwrap_or_else(|| Value::Array(vec![])),
                "log": job.get("log").cloned().unwrap_or_else(|| Value::Array(vec![])),
                "started": g("started"),
                "finished": g("finished"),
            }))
            .into_response())
        }
    }
}

/// `POST /api/skills/audit-all/cancel` — request cancellation of the audit job.
///
/// ```python
/// @router.post("/audit-all/cancel")
/// async def audit_cancel(request: Request):
///     user = _owner(request)
///     job = _skill_audit_jobs.get((user or "",))
///     if job:
///         job["cancel"] = True; job["status"] = "cancelled"; job["current"] = None
///         task = job.get("task")
///         if task and not task.done(): task.cancel()
///     return {"ok": True, "status": "cancelled" if job else "none"}
/// ```
///
/// `skills_pipeline::cancel_audit_job` sets the cooperative `cancel` flag (+ flips
/// `status`/`current`) and returns whether a job existed. DOCUMENTED DIVERGENCE: there is
/// no `task.cancel()` hard-abort (Rust stores no `JoinHandle`); the in-flight skill
/// finishes and `run_audit_all_job` stops at the next loop iteration when it sees the flag.
/// The user-visible status still flips to `"cancelled"` immediately.
async fn audit_cancel(
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    let user = owner_of(user.as_ref());
    let key = skills_pipeline::audit_key(user.as_deref());
    // `return {"ok": True, "status": "cancelled" if job else "none"}`
    let existed = skills_pipeline::cancel_audit_job(&key);
    let status = if existed { "cancelled" } else { "none" };
    Ok(Json(json!({"ok": true, "status": status})).into_response())
}

// ===========================================================================
// Small dict-access helpers (bridge the dynamic `dict.get(...)` calls)
// ===========================================================================

/// `s.get("name") == skill_id or s.get("id") == skill_id` — the skill lookup predicate.
fn name_or_id_matches(s: &Map<String, Value>, skill_id: &str) -> bool {
    matches!(s.get("name"), Some(Value::String(v)) if v == skill_id)
        || matches!(s.get("id"), Some(Value::String(v)) if v == skill_id)
}

/// `next((s for s in skills if s.get("name") == skill_id or s.get("id") == skill_id), None)`.
fn find_skill<'a>(skills: &'a [Map<String, Value>], skill_id: &str) -> Option<&'a Map<String, Value>> {
    skills.iter().find(|s| name_or_id_matches(s, skill_id))
}

/// `d.get(key)` -> owned string ("" for missing / non-string / null).
fn get_str(d: &Map<String, Value>, key: &str) -> String {
    match d.get(key) {
        Some(Value::String(s)) => s.clone(),
        _ => String::new(),
    }
}

/// Python truthiness of an optional JSON value (`if not d.get(key)`).
fn truthy(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().map(|f| f != 0.0).unwrap_or(false),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

/// Build the `update_skill` payload from a parsed [`Skill`] — the dict the
/// `save_skill_markdown` handler passes (mirrors the Python dict literal, in declaration
/// order). `teacher_model` is `None` -> JSON `null` (matching `sk.teacher_model` being
/// `None`).
fn skill_to_update_map(sk: &Skill) -> Map<String, Value> {
    let mut m = Map::new();
    m.insert("name".to_string(), Value::String(sk.name.clone()));
    m.insert(
        "description".to_string(),
        Value::String(sk.description.clone()),
    );
    m.insert("version".to_string(), Value::String(sk.version.clone()));
    m.insert("category".to_string(), Value::String(sk.category.clone()));
    m.insert("tags".to_string(), str_vec(&sk.tags));
    m.insert("platforms".to_string(), str_vec(&sk.platforms));
    m.insert(
        "requires_toolsets".to_string(),
        str_vec(&sk.requires_toolsets),
    );
    m.insert(
        "fallback_for_toolsets".to_string(),
        str_vec(&sk.fallback_for_toolsets),
    );
    m.insert("status".to_string(), Value::String(sk.status.clone()));
    m.insert(
        "confidence".to_string(),
        serde_json::Number::from_f64(sk.confidence)
            .map(Value::Number)
            .unwrap_or(Value::Null),
    );
    m.insert("source".to_string(), Value::String(sk.source.clone()));
    m.insert(
        "teacher_model".to_string(),
        match &sk.teacher_model {
            Some(t) => Value::String(t.clone()),
            None => Value::Null,
        },
    );
    m.insert(
        "owner".to_string(),
        match &sk.owner {
            Some(o) => Value::String(o.clone()),
            None => Value::Null,
        },
    );
    m.insert(
        "when_to_use".to_string(),
        Value::String(sk.when_to_use.clone()),
    );
    m.insert("procedure".to_string(), str_vec(&sk.procedure));
    m.insert("pitfalls".to_string(), str_vec(&sk.pitfalls));
    m.insert("verification".to_string(), str_vec(&sk.verification));
    m.insert(
        "body_extra".to_string(),
        Value::String(sk.body_extra.clone()),
    );
    m
}

/// `Vec<String>` -> JSON array of strings.
fn str_vec(v: &[String]) -> Value {
    Value::Array(v.iter().cloned().map(Value::String).collect())
}

// ===========================================================================
// `_clean` (list_builtin_skills) + `{name!r}` quoting (get_builtin_skill)
// ===========================================================================

/// `re.sub(r"```.*?```", "", s, flags=re.S)` — drop code fences (incl. inline
/// ```` ```name``` ````). `(?s)` = Python `re.S` (DOTALL: `.` matches newlines);
/// non-greedy `.*?` matches the Python.
static _CODE_FENCE_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?s)```.*?```").unwrap());
/// `re.sub(r"\s+", " ", s)` — collapse runs of whitespace. The `regex` crate's `\s`
/// is Unicode-aware, matching Python `str` `\s`.
static _WS_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\s+").unwrap());
/// `re.sub(r"^[-–—:\s]+", "", s)` — drop a leftover `- — : ` bullet/colon prefix
/// (ASCII hyphen, en-dash, em-dash, colon, whitespace).
static _BULLET_PREFIX_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^[-–—:\s]+").unwrap());

/// `_clean(raw)` from `list_builtin_skills`:
///
/// ```python
/// def _clean(raw: str) -> str:
///     s = raw or ""
///     s = re.sub(r"```.*?```", "", s, flags=re.S)   # drop code fences
///     s = re.sub(r"\s+", " ", s).strip()
///     s = re.sub(r"^[-–—:\s]+", "", s)              # drop leftover bullet prefix
///     return s[:240]
/// ```
///
/// `s[:240]` slices by Python codepoint, so the Rust port truncates to 240 `char`s
/// (not bytes). `.strip()` is Python `str.strip()` (Unicode whitespace both ends),
/// which Rust `str::trim()` matches.
fn clean_builtin_desc(raw: &str) -> String {
    // `s = re.sub(r"```.*?```", "", s, flags=re.S)`
    let s = _CODE_FENCE_RE.replace_all(raw, "");
    // `s = re.sub(r"\s+", " ", s).strip()`
    let s = _WS_RE.replace_all(&s, " ");
    let s = s.trim();
    // `s = re.sub(r"^[-–—:\s]+", "", s)`
    let s = _BULLET_PREFIX_RE.replace(s, "");
    // `return s[:240]` — codepoint slice.
    s.chars().take(240).collect()
}

/// Python `repr()` of a string — for the `f"No built-in tool named {name!r}"` 404
/// detail. Single-quoted unless the value contains a `'` (and no `"`), in which case
/// Python switches to double quotes; control chars are backslash-escaped. Built-in tool
/// names are simple ASCII identifiers, so this is `'<name>'` in practice.
fn py_repr(s: &str) -> String {
    let has_single = s.contains('\'');
    let has_double = s.contains('"');
    let (quote, escape_quote) = if has_single && !has_double {
        ('"', '"')
    } else {
        ('\'', '\'')
    };
    let mut out = String::with_capacity(s.len() + 2);
    out.push(quote);
    for ch in s.chars() {
        match ch {
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

// ===========================================================================
// Tests
// ===========================================================================

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::Body;
    use axum::http::{Request, StatusCode};
    use serde_json::json;
    use tower::ServiceExt; // for `oneshot`

    /// `setup_skills_routes()` merges cleanly into a `Router<AppState>` — no panic means
    /// no intra-router method+path collision (the additive invariant the aggregator
    /// relies on). This exercises the static-vs-param coexistence in particular
    /// (`/index`, `/builtin`, `/add`, `/audit-all`, `/search` alongside `/:skill_id`).
    #[test]
    fn mount_smoke() {
        let base: Router<AppState> = Router::new();
        let _merged: Router<AppState> = base.merge(setup_skills_routes());
    }

    /// matchit 0.7 (axum 0.7) gives a static segment priority over a sibling param, so a
    /// router carrying BOTH `/api/skills/index` and `/api/skills/:skill_id` resolves
    /// `/api/skills/index` to the static handler and `/api/skills/<other>` to the param
    /// handler. This pins that resolution (and the colon dialect) on a state-free mirror
    /// using the SAME path strings the factory registers.
    #[tokio::test]
    async fn static_and_param_segments_coexist() {
        async fn index_h() -> &'static str {
            "index"
        }
        async fn param_h(Path(id): Path<String>) -> String {
            id
        }
        let r: Router = Router::new()
            .route("/api/skills/index", get(index_h))
            .route("/api/skills/:skill_id", get(param_h));

        // Static segment wins.
        let resp = r
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/api/skills/index")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
        let body = axum::body::to_bytes(resp.into_body(), usize::MAX)
            .await
            .unwrap();
        assert_eq!(&body[..], b"index");

        // A non-matching segment falls through to the param handler.
        let resp = r
            .oneshot(
                Request::builder()
                    .uri("/api/skills/my-skill")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
        let body = axum::body::to_bytes(resp.into_body(), usize::MAX)
            .await
            .unwrap();
        assert_eq!(&body[..], b"my-skill");
    }

    #[test]
    fn skill_add_request_defaults_match_pydantic() {
        // Only `name` omitted-OK: every field has a default. `source` defaults to "user".
        let req: SkillAddRequest = serde_json::from_value(json!({})).unwrap();
        assert_eq!(req.name, None);
        assert_eq!(req.category, "general");
        assert_eq!(req.status, "draft");
        assert_eq!(req.version, "1.0.0");
        assert_eq!(req.confidence, 0.8);
        assert_eq!(req.source, "user");
        assert!(req.tags.is_empty());
        assert!(req.steps.is_empty());
    }

    #[test]
    fn update_map_excludes_none_and_keeps_present() {
        // `body.dict(exclude_none=True)` — null/absent dropped, present kept.
        let body = json!({
            "description": "new desc",
            "tags": ["a", "b"],
            "confidence": 0.9,
            "status": null,      // explicit null -> dropped
            "unknown_field": 1,  // not a model field -> ignored
        });
        let updates = build_update_map(&body);
        assert_eq!(updates.len(), 3);
        assert_eq!(updates.get("description"), Some(&json!("new desc")));
        assert_eq!(updates.get("tags"), Some(&json!(["a", "b"])));
        assert_eq!(updates.get("confidence"), Some(&json!(0.9)));
        assert!(!updates.contains_key("status"));
        assert!(!updates.contains_key("unknown_field"));
    }

    #[test]
    fn empty_update_map_is_empty() {
        // All-null body -> empty updates -> handler returns {"ok": True} without writing.
        let body = json!({"status": null, "name": null});
        assert!(build_update_map(&body).is_empty());
        // A body with no model keys at all -> also empty.
        assert!(build_update_map(&json!({"foo": "bar"})).is_empty());
    }

    #[test]
    fn verify_owner_semantics() {
        // None user (auth disabled / single-user) owns everything.
        let mut owned = Map::new();
        owned.insert("owner".to_string(), Value::String("alice".to_string()));
        assert!(verify_owner(&owned, None).is_ok());
        // Matching owner -> ok.
        assert!(verify_owner(&owned, Some("alice")).is_ok());
        // Mismatched owner -> 404 (not 403, to avoid leaking existence).
        let e = verify_owner(&owned, Some("bob")).unwrap_err();
        assert_eq!(e.status_code, 404);
        assert_eq!(e.detail, "Skill not found");
        // Missing owner field + a concrete user -> not-owned -> 404 (strict).
        let no_owner = Map::new();
        assert_eq!(verify_owner(&no_owner, Some("alice")).unwrap_err().status_code, 404);
        // Missing owner field + None user -> ok.
        assert!(verify_owner(&no_owner, None).is_ok());
    }

    #[test]
    fn name_or_id_match_predicate() {
        let mut s = Map::new();
        s.insert("name".to_string(), Value::String("open-pr".to_string()));
        s.insert("id".to_string(), Value::String("abc123".to_string()));
        assert!(name_or_id_matches(&s, "open-pr"));
        assert!(name_or_id_matches(&s, "abc123"));
        assert!(!name_or_id_matches(&s, "nope"));
        let skills = vec![s];
        assert!(find_skill(&skills, "abc123").is_some());
        assert!(find_skill(&skills, "missing").is_none());
    }

    #[test]
    fn truthy_matches_python_audit_verdict_guard() {
        // `if not match.get("audit_verdict"): _fire_skill_added(user)`
        assert!(!truthy(None));
        assert!(!truthy(Some(&Value::Null)));
        assert!(!truthy(Some(&Value::String(String::new())))); // "" is falsy
        assert!(truthy(Some(&Value::String("pass".to_string()))));
        assert!(!truthy(Some(&Value::Bool(false))));
    }

    #[test]
    fn clean_builtin_desc_matches_python() {
        // `_clean` drops code fences (DOTALL), collapses whitespace, strips, then drops a
        // leading bullet/colon prefix, then truncates to 240 codepoints.
        // A one-liner section: "- ```chat_with_model``` — Ask a DIFFERENT AI model ..."
        let raw = "- ```chat_with_model``` — Ask a DIFFERENT AI model and relay its answer.";
        // Code fence removed -> "-  — Ask ..."; ws collapsed + stripped -> "- — Ask ...";
        // bullet prefix "- — " stripped -> "Ask a DIFFERENT AI model and relay its answer."
        assert_eq!(
            clean_builtin_desc(raw),
            "Ask a DIFFERENT AI model and relay its answer."
        );

        // A fenced multi-line block: everything between the first/last ``` is dropped
        // (DOTALL), leaving the trailing prose.
        let raw2 = "```python\n<python code>\n```\nExecute Python code.";
        assert_eq!(clean_builtin_desc(raw2), "Execute Python code.");

        // Empty stays empty.
        assert_eq!(clean_builtin_desc(""), "");

        // 240-codepoint cap (by char, not byte).
        let long: String = "x".repeat(500);
        assert_eq!(clean_builtin_desc(&long).chars().count(), 240);
    }

    #[test]
    fn py_repr_single_quotes_builtin_names() {
        // `f"No built-in tool named {name!r}"` — simple ASCII names are single-quoted.
        assert_eq!(py_repr("bash"), "'bash'");
        assert_eq!(py_repr(""), "''");
        // Contains a single quote (and no double) -> Python switches to double quotes.
        assert_eq!(py_repr("it's"), "\"it's\"");
    }

    #[test]
    fn skill_to_update_map_shape() {
        // The dict literal `save_skill_markdown` passes to update_skill — declaration
        // order + the teacher_model/owner None -> null mapping.
        let sk = Skill {
            name: "my-skill".to_string(),
            description: "d".to_string(),
            tags: vec!["t1".to_string()],
            teacher_model: None,
            owner: Some("alice".to_string()),
            ..Skill::default()
        };
        let m = skill_to_update_map(&sk);
        assert_eq!(m.get("name"), Some(&json!("my-skill")));
        assert_eq!(m.get("tags"), Some(&json!(["t1"])));
        assert_eq!(m.get("teacher_model"), Some(&Value::Null));
        assert_eq!(m.get("owner"), Some(&json!("alice")));
        // Declaration order: name first, body_extra last.
        let keys: Vec<&str> = m.keys().map(String::as_str).collect();
        assert_eq!(keys.first(), Some(&"name"));
        assert_eq!(keys.last(), Some(&"body_extra"));
    }
}
