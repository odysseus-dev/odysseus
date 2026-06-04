// routes/mod.rs  <- the Python `routes/` package
//! Mirrors the Python `routes/` package.
//!
//! Most route modules are FastAPI routers (`@router.post(...)`) translated into
//! the axum/tower HTTP tranche. The pure-logic helpers and request validators
//! that were "extracted from route handlers for testability" are translated
//! alongside them — everything compiles in the single build.

use std::fmt;

/// Faithful stand-in for FastAPI's `HTTPException(status_code, detail)`.
///
/// The route validators `raise HTTPException(400, "...")`; the translation
/// returns `Err(HttpException { ... })`. The Python tests assert
/// `pytest.raises(HTTPException)`, which maps to checking for `Err`.
///
/// FastAPI's `detail` is `Any`: most call sites pass a string, but a few
/// (e.g. `tts_routes`/`stt_routes`) pass a dict such as
/// `detail={"message": "..."}`. Starlette's default exception handler wraps
/// whatever it is in `{"detail": <detail>}`. To reproduce that without
/// changing the existing `new(status, String)` signature (which the
/// default-profile `cookbook_helpers` tests rely on), `detail_json` carries an
/// optional structured detail; when it is `Some`, the rendered body uses it in
/// place of the plain `detail` string. See [`HttpException::with_detail`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HttpException {
    pub status_code: u16,
    pub detail: String,
    /// Structured `detail` payload (FastAPI `detail={...}`). `None` for the
    /// common string case; `Some` when a handler raises a dict detail.
    pub detail_json: Option<serde_json::Value>,
}

impl HttpException {
    /// `HTTPException(status_code, detail)` — the string-`detail` case.
    pub fn new(status_code: u16, detail: impl Into<String>) -> Self {
        HttpException {
            status_code,
            detail: detail.into(),
            detail_json: None,
        }
    }

    /// `HTTPException(status_code, detail={...})` — the structured-`detail`
    /// case (e.g. `detail={"message": "TTS service not available"}`).
    ///
    /// The `detail` string is left empty because rendering prefers
    /// `detail_json` whenever it is present; it exists only so [`Display`] and
    /// the default-profile `PartialEq` checks keep working.
    pub fn with_detail(status_code: u16, detail: serde_json::Value) -> Self {
        HttpException {
            status_code,
            detail: String::new(),
            detail_json: Some(detail),
        }
    }
}

impl fmt::Display for HttpException {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match &self.detail_json {
            Some(d) => write!(f, "{}: {}", self.status_code, d),
            None => write!(f, "{}: {}", self.status_code, self.detail),
        }
    }
}

impl std::error::Error for HttpException {}

/// `HTTPException` -> HTTP response, mirroring Starlette's default exception
/// handler: status code = `status_code`, JSON body = `{"detail": <detail>}`.
///
/// When `detail_json` is `Some`, that value is used as the `detail` (the
/// `detail={"message": ...}` dict case); otherwise the plain `detail` string is
/// used. An out-of-range status code falls back to `500`, matching the way a
/// nonsensical status would otherwise blow up Starlette.
///
/// This lets handlers return `Result<impl IntoResponse, HttpException>` and
/// translate `raise HTTPException(s, d)` straight into
/// `return Err(HttpException::new(s, d))`.
impl axum::response::IntoResponse for HttpException {
    fn into_response(self) -> axum::response::Response {
        use axum::http::StatusCode;
        use axum::Json;

        let status =
            StatusCode::from_u16(self.status_code).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
        let detail = match self.detail_json {
            Some(d) => d,
            None => serde_json::Value::String(self.detail),
        };
        (status, Json(serde_json::json!({ "detail": detail }))).into_response()
    }
}

pub mod cookbook_helpers;

// `document_helpers` <- routes/document_helpers.py (wave-6 DOCS). The Pydantic
// request models, doc serializers, owner-gating, and file-locator helpers that
// `document_routes.py` imports. NO router (pure helpers + models), so it is only
// `pub mod`-declared here — it never appears in [`api_routers`]. It reaches the
// DB-backed managers on the always-compiled `AppState` (no cargo feature flags).
pub mod document_helpers;

// `gallery_helpers` <- routes/gallery_helpers.py (wave-6 DOCS). The `GalleryPatch`
// request schema, `_image_to_dict` ordered serializer, and small utilities that
// `gallery_routes.py` imports. NO router (pure helpers + models), so it is only
// `pub mod`-declared here — it never appears in [`api_routers`]. **web + db**.
pub mod gallery_helpers;

// `chat_helpers` <- routes/chat_helpers.py (wave-5 CHAT prerequisite). The shared
// chat helpers — `build_chat_context` / `save_assistant_response` /
// `run_post_response_tasks` / `enforce_chat_privileges` / `normalize_thinking`
// plus the `PresetInfo` / `ChatContext` / `PreprocessedMessage` dataclasses. It
// has NO router (pure helpers + dataclasses), so it is only `pub mod`-declared
// here — it never appears in [`api_routers`]. `chat_routes` (the wave-5 union
// port) binds to its exported helpers/types. **web + db** (it reads the DB for
// the daily-message cap + per-session token totals, and reaches the
// `ChatHandler`/`ChatProcessor` on the always-compiled `AppState`).
pub mod chat_helpers;

// `prefs_store` <- routes/prefs_routes.py (the `_load`/`_save`/`_load_for_user`/
// `_save_for_user` helpers). Foundation unit F6: the shared user-prefs JSON
// store (`data/user_prefs.json`, `_users`-vs-flat + auth-disabled None
// semantics), hoisted out of `prefs_routes` because backup/task/model/chat/
// calendar/skills/auth routers all import it. Pure FS+JSON, default profile.
pub mod prefs_store;

// ---------------------------------------------------------------------------
// Shared web-layer state, re-exported for the ported routers (F2)
// ---------------------------------------------------------------------------

/// Re-export the shared axum app-state types from [`crate::web`] so the ported
/// `setup_x_routes()` factories and the aggregator can name them as
/// `crate::routes::{AppState, CurrentUser, MaybeUser}` without depending on the
/// `web/mod.rs` module layout.
///
/// `AppState` is the additively-extended `app.state` (managers + handlers + auth
/// flags); `CurrentUser` is `request.state.current_user`, stamped by the auth gate
/// only when resolved (so handlers read `Option<Extension<CurrentUser>>`);
/// `MaybeUser` is the always-present optional variant used by the auth-adapter
/// (F3) to bridge to `src::auth_helpers::get_current_user`; `ApiToken` is the
/// validated Bearer-token state (`request.state.{api_token,api_token_id,
/// api_token_owner,api_token_scopes}`) stamped by the auth gate's Bearer branch,
/// read by `webhook_routes::sync_chat` as `Option<Extension<ApiToken>>` (absent =>
/// not a token, matching Python's `getattr(request.state, "api_token", False)`).
pub use crate::web::{ApiToken, AppState, CurrentUser, MaybeUser};

// `auth_adapter` (F3) — the axum auth-adapter. Bridges
// `Extension<CurrentUser>` + `State<AppState>` + `ConnectInfo<SocketAddr>` to the
// already-ported `src::auth_helpers` (require_user / require_privilege), wraps
// `core::middleware::require_admin`, and ports the `routes/email_helpers.py`
// ownership surface (require_owner / _assert_owns_account) plus
// `session_routes._verify_session_owner`, mapping every raised `HTTPException` to
// the foundation's [`HttpException`]. Each ported `setup_x_routes` handler calls
// these at the top, the explicit-call analogue of FastAPI `Depends(...)`.
pub mod auth_adapter;

// ---------------------------------------------------------------------------
// The router aggregator (F5-aggregator-and-merge) <- app.py `app.include_router`
// ---------------------------------------------------------------------------
//
// `auth_adapter` (F3) and the per-router `setup_x_routes()` modules are declared
// here `as routers land` (the contract's wording). They were added one wave at a
// time; until a wave's module file existed, its `pub mod` line and its
// `.merge(setup_x_routes())` call stayed commented in [`api_routers`] below so the
// crate kept compiling with zero warnings. All waves have since landed; the crate
// has no cargo feature flags, so every router module is unconditionally compiled.
//
//   // PROOF BATCH (wave: proof) — the first ported `routes/X.py` factories:
//   pub mod search_routes;       // P4
//   pub mod diagnostics_routes;  // P5
//   pub mod hwfit_routes;        // P6
//   pub mod preset_routes;       // P9
//   pub mod cleanup_routes;      // P10
//   pub mod tts_routes;          // P11
//   pub mod stt_routes;          // P12
//   pub mod signature_routes;    // P13

// --- LANDED proof-batch modules (un-commented as each is ported) -----------
/// `routes/emoji_routes.py` (proof-batch P2) — `GET /api/emoji/:code`. Same-origin
/// monochrome-SVG emoji proxy: disk-cache under `data/emoji_cache`, lazy outbound
/// `reqwest` GET to the OpenMoji CDN (200 + `<svg` marker check), transparent
/// `no-store` blank-SVG fallback for unknown/unreachable codepoints. No auth/DB.
pub mod emoji_routes;

/// `routes/font_routes.py` (proof-batch P1) — `GET /api/fonts/custom`.
pub mod font_routes;

/// `routes/search_routes.py` (proof-batch P4) — `GET /api/search/config`,
/// `POST /api/search`, `GET /api/search/providers`, `POST /api/search/query`.
/// No auth; never raises (returns an `error` key). Bridges the body-or-form-or-
/// query `_request_values` sniff to the already-ported `crate::services::search`
/// engine (run off the async runtime via `spawn_blocking`).
pub mod search_routes;

/// `routes/prefs_routes.py` (proof-batch P3) — `GET /api/prefs`,
/// `GET`/`PUT /api/prefs/:key`. Per-user key/value store backed by the shared
/// [`prefs_store`] (F6); reads `Option<Extension<CurrentUser>>` (the `None`
/// auth-disabled case is load-bearing).
pub mod prefs_routes;

/// `routes/preset_routes.py` (proof-batch P9) — `GET /api/presets`,
/// `POST /api/presets/custom`, `GET`/`POST /api/presets/templates`,
/// `DELETE /api/presets/templates/:template_id`, `POST /api/presets/expand`,
/// `GET`/`POST /api/presets/groups`. Validates the `Arc<Mutex<PresetManager>>`
/// pattern (the store's mutating methods take `&mut self`): every mutating handler
/// locks the manager. `require_admin` (F3) gates the write endpoints; the read
/// endpoints are unauthenticated. `UserTemplateRequest` is a local body with manual
/// `min_length`/`max_length`/`ge`/`le` 422-validation (mirroring pydantic). The
/// `/api/presets/expand` handler is an HONEST STUB — the Python's `_resolve_model`
/// dependency is not ported, so it reproduces Python's own `{"success": False,
/// "message": ...}` failure branch. No path collision (`/api/presets/*`).
pub mod preset_routes;

/// `routes/hwfit_routes.py` (proof-batch P6) — `GET /api/hwfit/{system,models,
/// image-models}`. No auth/DB; pure `serde_json::Value` dict transforms over the
/// already-ported [`crate::services::hwfit`] (`detect_system`/`rank_models`/
/// `rank_image_models`), porting `_apply_manual_hardware` + the `gpu_group`/
/// `gpu_count` homogeneous-pool resolution. Many typed query params parsed with
/// FastAPI/Pydantic-v2 bool/int coercion (422 on malformed input).
pub mod hwfit_routes;

/// `routes/vault_routes.py` (proof-batch P7) — `GET`/`POST /api/vault/config`,
/// `POST /api/vault/{login,unlock,lock,logout}`. Vaultwarden / Bitwarden CLI
/// integration: every handler is `require_admin`-gated (the F3 auth-adapter), runs
/// the `bw` CLI via `tokio::process` (with a `BW_SESSION` env var + piped stdin),
/// and persists the session key to the CWD-relative `data/vault.json` with `0o600`
/// permissions. `bw`-not-installed (`rc 127`) is reproduced faithfully.
pub mod vault_routes;

/// `routes/diagnostics_routes.py` (proof-batch P5) — `GET /api/db/stats`,
/// `GET /api/rag/stats`, `GET /api/test/youtube`, `POST /api/test-research`.
/// **web+db** (the `/api/db/stats` handler reads the DB via `get_detailed_stats`).
/// No auth; the broad `try/except -> {"error": ...}` idiom (except `/api/db/stats`,
/// which raises 500). `/api/rag/stats` reproduces the live app's always-`None`
/// `rag_manager` (`{"error": "RAG system not available"}`); `/api/test/youtube`
/// reflects the transcript HONEST STUB; `/api/test-research` is an HONEST STUB (the
/// Python's 3-arg `call_research_service(query, endpoint, model)` was refactored to
/// a private `ResearcherConfig`-based method with no public entrypoint).
pub mod diagnostics_routes;

/// `routes/compare_routes.py` (proof-batch P8) — `POST /api/compare/start`,
/// `POST /api/compare/:comp_id/vote`, `POST /api/compare/record`,
/// `GET /api/compare/history`, `DELETE /api/compare/:comp_id`. **web+db**.
/// Raw `rusqlite` over the `comparisons` table (and the `model_endpoints` API-key
/// lookup) + `session_manager.create_session`/`update_session_headers`;
/// `get_current_user` strict owner scope (the `None` anonymous case is
/// load-bearing); `RecordVoteRequest` JSON body + `Form(...)` multipart;
/// `uuid4` ids + `rand` blind left/right mapping. No path collision (`/api/compare/*`).
pub mod compare_routes;

/// `routes/cleanup_routes.py` (proof-batch P10) — `GET /api/cleanup/preview`,
/// `POST /api/cleanup`. **web+db**. Two handlers over the already-ported async
/// [`crate::src::cleanup_service`] (`get_cleanup_preview`/`cleanup_sessions`),
/// with `session_manager` reached through the existing `AppState.sessions` field
/// (reused, not recreated); `get_current_user` only (the `None` auth-disabled
/// case flows into the service's `owner=None` no-filter branch); `round(mb, 2)`
/// preserved. No path collision (`/api/cleanup*`).
pub mod cleanup_routes;

/// `routes/tts_routes.py` (proof-batch P11) — `GET /api/tts/stats`,
/// `POST /api/tts/synthesize`, `POST /api/tts/clear-cache`. No auth/DB; reaches
/// the `&'static` [`crate::services::tts::get_tts_service`] singleton directly
/// (TTS is not an `AppState` field, per the design). Magic-byte MP3/WAV sniff +
/// `Content-Disposition` on the raw-audio response. EXERCISES the F1 dict-detail
/// `IntoResponse` path (`raise HTTPException(503, detail={"message": ...})`).
/// No path collision (`/api/tts/*`).
pub mod tts_routes;

/// `routes/stt_routes.py` (proof-batch P12) — `GET /api/stt/stats`,
/// `POST /api/stt/transcribe`. Smallest router: two no-auth handlers over the
/// `&'static` [`crate::services::stt::get_stt_service`] singleton (STT is not an
/// `AppState` field, per the design). Validates the `UploadFile = File(...)` ->
/// `Multipart` single-`file`-field-to-bytes extraction and the dict-detail
/// `HTTPException` path (`raise HTTPException(503, detail={"message": ...})` maps
/// to [`HttpException::with_detail`] -> `{"detail": {"message": ...}}`). No DB.
/// No path collision (`/api/stt/*`).
pub mod stt_routes;

/// `routes/signature_routes.py` (proof-batch P13) — `GET`/`POST /api/signatures`,
/// `DELETE /api/signatures/:sig_id`. **web+db**. Raw `rusqlite` over the
/// `signatures` table (mirroring the SQLAlchemy `Signature` ORM, including the
/// `EncryptedText` `data_png`/`svg` columns — encrypt-on-write / decrypt-on-read via
/// `src::secret_storage`); `get_current_user` owner scope (list strict-filter when a
/// user resolves; delete `403 "Not your signature"`); the `_DATA_URL_RE` prefix strip
/// + strict base64 decode (`400` on a non-base64 / empty payload); `_to_dict` with
/// `isoformat()+"Z"` timestamps. No path collision (`/api/signatures*`).
pub mod signature_routes;

/// `routes/personal_routes.py` (wave-2) — `GET /api/personal`,
/// `POST /api/personal/reload`, `POST /api/personal/add_directory`,
/// `DELETE /api/personal/remove_directory`, `POST /api/personal/upload`,
/// `DELETE /api/personal/file`. Personal-docs management; five handlers are
/// `require_user` + `require_admin`-gated (the upload handler is unauthenticated).
/// The RAG-unavailable branch is the faithful LIVE path (`rag_singleton` is disabled
/// and returns `None`), so add/upload 503 and remove/delete skip the RAG step.
/// No path collision (`/api/personal*`).
pub mod personal_routes;

/// `routes/embedding_routes.py` (wave-2) — `GET /api/embeddings/models`,
/// the folded `{model_name:path}` catch-all (`POST` download / `GET` status /
/// `DELETE`), and `GET`/`POST`/`DELETE /api/embeddings/endpoint`. The `/models/*`
/// quartet is an HONEST STUB reproducing the Python's `fastembed`-not-installed 503;
/// the `/endpoint` trio is fully ported (JSON config file + env mutation + `reqwest`
/// health check + the existing embedding/chroma reset hooks). No path collision
/// (`/api/embeddings/*`).
pub mod embedding_routes;

/// `routes/upload_routes.py` (wave-2) — `POST /api/upload`,
/// `POST /api/upload/cleanup`, `GET /api/upload/stats`, `GET /api/upload/:file_id`,
/// `GET`/`PUT /api/upload/:file_id/vision`. **web+db**. Multipart file upload over
/// `AppState.upload_handler` (per-IP burst cap), admin-gated cleanup/stats, a
/// `FileResponse`-equivalent download (the `?thumb=1` PIL thumbnail is an HONEST
/// DEFER falling through to the full image), and cached vision OCR/description. Also
/// surfaces the standalone [`upload_routes::periodic_rate_limit_cleanup`] background
/// task (the Python factory's 2nd tuple element). No path collision (`/api/upload*`).
pub mod upload_routes;

/// `routes/admin_wipe_routes.py` (wave-2) — `DELETE /api/admin/wipe/:kind`. **web+db**.
/// Admin Danger Zone per-category wipes (`chats`/`memory`/`skills`/`notes`/`tasks`/
/// `documents`/`gallery`/`calendar`) over the eleven SQLite tables in the exact Python
/// wipe order, each inside a `rusqlite::Transaction`. `require_admin`-gated. The
/// memory-vector `clear()` is DEAD CODE in Python (`MemoryVectorStore` has no `clear`),
/// faithfully a no-op here. No path collision (`/api/admin/wipe/*`).
pub mod admin_wipe_routes;

/// `routes/editor_draft_routes.py` (wave-2) — `GET`/`POST /api/editor-drafts`,
/// `GET`/`PUT`/`DELETE /api/editor-drafts/:draft_id`. **web+db**. Persisted gallery-
/// editor sessions over the `editor_drafts` table (raw `rusqlite`); `get_current_user`
/// owner scope (the `None` auth-disabled case owns everything); `payload` stored as a
/// JSON string. No path collision (`/api/editor-drafts*`).
pub mod editor_draft_routes;

/// `routes/backup_routes.py` (wave-2) — `GET /api/export`, `POST /api/import`.
/// **web+db**. Admin-gated export/import of the user's whole dataset (memories,
/// presets, skills, settings, features, preferences). The skills-import branch is an
/// HONEST DEFER reproducing Python's own unhandled `AttributeError` -> 500
/// (`SkillsManager` has no bulk `save`). No path collision (`/api/export`,
/// `/api/import`).
pub mod backup_routes;

/// `routes/api_token_routes.py` (wave-2) — `GET`/`POST /api/tokens`,
/// `DELETE /api/tokens/:token_id`. **web+db**. Admin-gated raw `rusqlite` CRUD over
/// the `api_tokens` table; `secrets.token_urlsafe`-minted `ody_`-prefixed tokens with
/// `bcrypt` hashes (the plaintext returned once on create). The
/// `invalidate_token_cache` hook is the live `AppState` hook (wired to the Bearer
/// token cache's dirty-flag setter), so create/delete reflect on the next Bearer
/// request. No path collision (`/api/tokens*`).
pub mod api_token_routes;
// `codex_routes` <- routes/codex_routes.py — the `/api/codex` + `/api/claude`
// HTTP surface for the Codex/Claude agent plugin bridge: owner-scoped delegates
// to the email/memory/calendar/document handlers + plugin.zip + capabilities.
pub mod codex_routes;

/// `routes/memory_routes.py` (wave-3) — `GET /api/memory`, `POST /api/memory/add`,
/// `POST /api/memory/search`, `GET /api/memory/timeline`,
/// `GET /api/memory/by-session/:session_id`, `POST /api/memory/extract`,
/// `GET /api/memory/audit`, `POST /api/memory/import`, `GET /api/memory/debug`, and
/// the wildcard `GET`/`PUT`/`DELETE /api/memory/:memory_id` + pin toggle. **web+db**.
/// Memory CRUD / search / timeline over the already-ported [`crate::src::memory::MemoryManager`]
/// and the optional [`crate::src::memory_vector::MemoryVectorStore`] (`AppState.memory_vector`),
/// with `session_manager` for the timeline / by-session name lookups. `get_current_user`
/// owner scope (the `None` auth-disabled / single-user case = "no owner filter" is
/// load-bearing). No path collision (`/api/memory*`).
pub mod memory_routes;

/// `routes/skills_routes.py` (wave-3) — `GET`/`POST /api/skills`, `GET /api/skills/index`,
/// `POST /api/skills/search`, `GET`/`PUT`/`DELETE /api/skills/:skill_id`,
/// `GET`/`POST /api/skills/:skill_id/markdown`, `GET /api/skills/builtin`,
/// `GET`/`PUT`/`DELETE /api/skills/builtin/:name`. **web**. CRUD over the
/// already-ported [`crate::services::memory::skills::SkillsManager`]
/// (`AppState.skills_manager`); `require_admin` gates the writes; fires the
/// `skill_added` [`crate::src::event_bus::fire_event`] hook. The `/builtin*` quartet
/// is an HONEST STUB reproducing the Python's `agent_loop.TOOL_SECTIONS`-unavailable
/// branch (the section table is module-private with no public accessor). No path
/// collision (`/api/skills*`).
pub mod skills_routes;

/// `routes/skills_routes.py` (the test/audit orchestration half) — the skills
/// self-improvement loop: test → judge → self-edit → retry → teacher → flag, plus
/// the nightly scheduled audit. Helper module behind `skills_routes`'s four
/// background endpoints (`POST /:id/test`, `GET /:id/test-status`,
/// `POST /audit-all`, `GET /audit-all/status`, `POST /audit-all/cancel`),
/// `src::builtin_actions`'s test/audit actions, and `web::run`'s nightly loop.
/// Holds the two module-global in-memory job stores. No routes of its own.
pub mod skills_pipeline;

/// `routes/research_routes.py` (wave-3) — `/api/research/*`: `GET /active`,
/// `GET /status/:sid`, `POST /start`, `POST /cancel/:sid`, `GET`/`POST /result/:sid`,
/// `GET /report/:sid` (HTML), `POST /:sid/hide-image`, `POST /:sid/unhide-images`,
/// `GET /library`, `GET /detail/:sid`, `POST /:sid/archive`, `DELETE /:sid`,
/// `GET /stream/:sid` (SSE), `POST /spinoff/:sid`. **web+db**. Wraps the fully-ported
/// [`crate::src::research_handler::ResearchHandler`] (`AppState.research_handler`) over its
/// on-disk JSON store (via `session_json_path`, honoring the handler's `DATA_DIR`
/// deviation); every endpoint `require_user`, ownership `404`-not-`403`. `POST /start`
/// is `require_privilege("can_use_research")` + the `internal-tool` `X-Odysseus-Owner`
/// impersonation override. The `resolve_endpoint(...)` settings cascade is an HONEST
/// DEFER (matching the Python's own failure branch). No path collision (`/api/research/*`).
pub mod research_routes;

/// `routes/task_routes.py` (wave-3) — `GET`/`POST /api/tasks`,
/// `GET`/`PUT`/`DELETE /api/tasks/:task_id`, `POST /api/tasks/:task_id/run`,
/// `GET /api/tasks/:task_id/runs`, `POST /api/tasks/parse`, the notifications
/// poll/ack endpoints, and the `:token` unsubscribe. **web+db**. Scheduled-task CRUD
/// over `AppState.task_scheduler` (the wired `Arc<TaskScheduler>` singleton, public API
/// as-is) + the shared per-user [`prefs_store`] (F6) for `_load_for_user`/`_save_for_user`.
/// `get_current_user` only (the `None` case drives the seed-all-owners list, the
/// wide-open owner check, and the empty-notifications guard). `POST /api/tasks/parse`'s
/// LLM-draft path is an HONEST DEFER reproducing the Python's `{"success": False,
/// "message": "No model endpoint configured"}` branch. No path collision (`/api/tasks*`).
pub mod task_routes;

/// `routes/assistant_routes.py` (wave-3) — `GET`/`PUT /api/assistant`,
/// `POST /api/assistant/reset`, `GET /api/assistant/tasks`, plus the check-in
/// task surface. **web+db**. The personal assistant is a specially-flagged `CrewMember`
/// owning one pinned `Session` + three daily check-in `ScheduledTask`s; six handlers over
/// the `crew_members`, `scheduled_tasks`, and `task_runs` tables (raw `rusqlite`). The
/// `task_scheduler` is reached through `State<AppState>` (not a captured factory arg, per
/// the integration contract). Every handler `_owner(request) = get_current_user` -> `401`
/// on absent/empty user. No path collision (`/api/assistant*`).
pub mod assistant_routes;

/// `routes/note_routes.py` (wave-3) — `GET`/`POST /api/notes`,
/// `GET`/`PUT`/`DELETE /api/notes/:note_id`, pin/archive/checklist-item toggles,
/// `POST /api/notes/reorder`, and `POST /api/notes/fire-reminder`. **web+db**. Google
/// Keep-style notes/checklists CRUD over the `notes` table (raw `rusqlite`), plus the
/// module-level [`note_routes::dispatch_reminder`] helper (browser / ntfy + optional LLM
/// synthesis) backing the fire-reminder route. The Python factory's `task_scheduler=None`
/// arg + `global _scheduler_ref` becomes reading `State<AppState>.task_scheduler` and
/// threading it into `dispatch_reminder`. `get_current_user` owner scope (the `None`
/// case owns everything). The `channel == "email"` SMTP send is an HONEST DEFER (email
/// cluster, later wave). No path collision (`/api/notes*`).
pub mod note_routes;

// ---------------------------------------------------------------------------
// WAVE 4 — the RECONCILIATION: the four full ports that SUPERSEDE the inline
// `web/mod.rs` auth / session / history / model handler subsets. Mounted here +
// in [`api_routers`] in the same commit the inline collisions are deleted from
// `web/mod.rs`, so `Router::merge` never sees a duplicate `method`+`path`.
// ---------------------------------------------------------------------------

/// `routes/auth_routes.py` (wave-4, app.py include #1) — `setup_auth_routes(auth_manager)`.
/// **web**. SUPERSEDES the inline `web/mod.rs` auth subset (`POST /api/auth/{setup,
/// signup,login,logout}`, `GET /api/auth/{status,features,settings}`) and ports the full
/// surface beyond it: 2FA (`/api/auth/2fa/{setup,confirm,disable,status}`),
/// `change-password`, admin user management (`/api/auth/users[/:username/privileges]`),
/// the signup toggle, the `features`/`settings` POST writers, and the integrations CRUD
/// (`/api/auth/integrations*`). The captured `auth_manager` is reached via
/// `State<AppState>.auth`. No path collision after reconciliation (`/api/auth/*`).
pub mod auth_routes;

/// `routes/session_routes.py` (wave-4, app.py include #4) — `setup_session_routes(
/// session_manager, config, webhook_manager)`. **web+db**. SUPERSEDES the inline
/// `web/mod.rs` session subset (`GET /api/sessions`, `POST /api/session`,
/// `GET /api/history/:sid`, `POST /api/session/:sid/{inject_messages,archive}`,
/// `DELETE`/`PATCH /api/session/:sid`) and ports the full lifecycle: bulk-delete,
/// delete-all, the archived browser, save-now, auto-sort, unarchive, the beacon delete,
/// export, the OpenAI quick-create, mark-important, and context-info. OMITS
/// `/api/session/:sid/compact` (history_routes owns it, last-include-wins) AND defers
/// `GET /api/history/:sid` to `history_routes` (both Python modules register it; history
/// wins — see the merge-order note in [`api_routers`]). No path collision (`/api/session*`,
/// `/api/sessions*`).
pub mod session_routes;

/// `routes/history_routes.py` (wave-4, app.py include #10) — `setup_history_routes(
/// session_manager)`. **web+db**. SUPERSEDES the inline `web/mod.rs` `GET /api/history/:sid`
/// and OWNS it after reconciliation (FastAPI last-include-wins: include #10 > session's #4;
/// both Python modules declare the path). Ports history read + the message-mutation family
/// (`truncate`, `message`, `delete-messages`, `edit-message`, `mark-stopped`,
/// `update-last-meta`, `merge-last-assistant`, `fork`), the conversation-topics summary,
/// and `POST /api/session/:session_id/compact` (the intra-Python duplicate history owns).
/// No path collision (history `/api/history/:session_id` + `/api/session/:session_id/*`
/// mutations are registered exactly once across the wave-4 set).
pub mod history_routes;

/// `routes/model_routes.py` (wave-4, app.py include #17) — `setup_model_routes()` (the
/// Python factory's `model_discovery` arg is reached via `State<AppState>.model_discovery`,
/// mirroring `setup_research_routes`). **web+db**. SUPERSEDES the inline `web/mod.rs` model
/// subset (`GET /api/models`, `GET`/`POST /api/model-endpoints`) and ports the rest of the
/// provider surface: probe-local, ping, probe-selected/probe, providers, discover, the
/// per-endpoint `probe`/`models`/`dependents` reads, default-chat, the endpoint toggle/delete,
/// and the tools list/update. NOTE: `model_routes.py` has NO Codex auto-registration (Codex
/// is a Rust-side integration absent from the Python), so the inline `auto_register_codex`
/// is preserved separately in `web/mod.rs` (kept for `/api/codex/connect`). No path collision
/// (`/api/models`, `/api/model-endpoints*`, `/api/{ping,probe,probe-selected,providers,
/// discover,default-chat,tools}`).
pub mod model_routes;

/// `routes/chat_routes.py` (wave-5, app.py include #8) — `setup_chat_routes()` (the
/// Python factory's nine manager/handler args are reached via [`AppState`], mirroring
/// `setup_research_routes` / `setup_model_routes`). **web+db**. SUPERSEDES the inline
/// `web/mod.rs` `POST /api/chat_stream` (the wave-5 demo stream); RECONCILE deletes
/// that inline handler (+ its `owns`/`parse_form`/`sse_bytes` helpers) and mounts this
/// module at ordinal 8. Ports `POST /api/chat`, `POST /api/chat_stream`,
/// `GET /api/chat/resume/:session_id`, `POST /api/chat/stop/:session_id`,
/// `GET /api/chat/stream_status/:session_id`, `POST /api/inject_context/:session_id`,
/// `GET /api/search`, `POST /api/rewrite`. The `chat_stream` handler is the UNION — the
/// full Python research/image/agent/chat SSE pipeline (`_active_streams`/`_safe_stream`/
/// `_TOOL_INTENT_PATTERNS`) PLUS the Odysseus-Rust codex/agent dispatch folded into the
/// branch selection (Mode A `codex:` → `codex::stream_chat`; Mode B `codex-responses:` →
/// `stream_llm`; `is_agent` → `stream_agent_loop`; else `stream_llm`). NOTE: `GET /api/search`
/// (this module) and `POST /api/search` (search_routes) share a path with different methods
/// — no collision. The Codex `auto_register_codex`/`/api/codex/connect` stays inline (Codex
/// is absent from `chat_routes.py`). Mounted by RECONCILE (not here).
pub mod chat_routes;

// ---------------------------------------------------------------------------
// WAVE 6 — DOCS: document / gallery / cookbook routers. Purely ADDITIVE (no
// inline `web/mod.rs` collision, no reconciliation): each owns a fresh path
// prefix (`/api/document*`, `/api/gallery*`+`/api/image*`, `/api/cookbook*`).
// ---------------------------------------------------------------------------

/// `routes/document_routes.py` (wave-6, app.py include #20) — `setup_document_routes(
/// session_manager, upload_handler)` (both captured args reached via [`AppState`]).
/// **web+db**. Documents / artifacts / canvas CRUD over the `documents` table (raw
/// `rusqlite`), binding to the wave-6 [`document_helpers`] models/serializers/owner
/// gating. The PDF-rasterization paths (`/render-pages`, `/page/{n}.png`,
/// `ai-fill-annotations`) are pdfium-backed via the shared [`crate::src::pdf_render`]
/// module (reproducing PyMuPDF's `get_pixmap` / geometry); they soft-fail with the
/// Python's own lib-missing message only when `libpdfium` cannot be obtained. No
/// path collision (`/api/document*`).
pub mod document_routes;

/// `routes/gallery_routes.py` (wave-6, app.py include #22) — `setup_gallery_routes()`.
/// **web+db**. Image-library CRUD over the `images` table (raw `rusqlite`), binding to
/// the wave-6 [`gallery_helpers`] (`GalleryPatch` / `_image_to_dict`). The ML image ops
/// (rembg / realesrgan / gfpgan) are REAL ONNX ports ([`crate::src::image_models`]) and
/// the PIL rotate/sharpen/inpaint paths are REAL ([`crate::src::image_edit`]); they
/// soft-fail with the Python's own message only when a model cannot be downloaded. A
/// private [`gallery_routes::pyrandom`] submodule mirrors CPython's `random` for
/// byte-exact shuffles. No path collision (`/api/gallery*`,
/// `/api/image*`).
pub mod gallery_routes;

/// `routes/cookbook_routes.py` (wave-6, app.py include #28) — `setup_cookbook_routes()`.
/// **web+db**. Model download / serve / cache + cookbook state sync; binds to the
/// already-ported [`cookbook_helpers`] validators. Drives the `tmux`/process serve
/// lifecycle via `tokio::process`, parsing serve-startup log markers (uvicorn-ready /
/// GPU-OOM / traceback). No path collision (`/api/cookbook*`).
pub mod cookbook_routes;

// ---------------------------------------------------------------------------
// WAVE 7 — calendar / shell / mcp / webhook routers. Purely ADDITIVE (no inline
// `web/mod.rs` collision, no reconciliation): each owns a fresh path prefix
// (`/api/calendar*`, `/api/shell*` + `/api/cookbook/packages*`, `/api/mcp*`,
// `/api/webhooks*` + `/api/v1/chat`).
// ---------------------------------------------------------------------------

/// `routes/calendar_routes.py` (wave-7, app.py include #26) — `setup_calendar_routes()`.
/// **web+db**. Local SQLite-backed calendar CRUD: CalDAV config get/save/test/sync,
/// calendar + event CRUD, ICS import/export (the `icalendar` crate, same as
/// [`crate::src::caldav_sync`]), and the LLM-backed `quick-parse` natural-language
/// event parser. Ships its OWN module-private `_require_user` (FALLBACK_OWNER /
/// single-user semantics), distinct from `auth_adapter::require_user`. Full
/// natural-language date parsing (`_parse_dt`/`_parse_dt_pair`/`parse_due_for_user`)
/// is ported. No path collision (`/api/calendar*`).
pub mod calendar_routes;

/// `routes/shell_routes.py` (wave-7, app.py include #27) — `setup_shell_routes()`.
/// **web+db**. The two shell endpoints (`/api/shell/exec`, `/api/shell/stream`) plus
/// the two package endpoints that physically live in this Python module but are
/// namespaced under `/api/cookbook/packages` (`GET .../packages`,
/// `POST .../packages/install`). Ships its OWN module-LOCAL `_require_admin`, whose
/// semantics DIFFER from `core/middleware.require_admin` (the `user == "api"` reject,
/// no `AUTH_ENABLED`/`is_configured` consult). Reuses the SAME `TMUX_LOG_DIR` path as
/// `cookbook_routes` (defined locally, pointing at the same location — `cookbook_routes`
/// is NOT edited). The PTY/`importlib.reload` paths are HONEST STUBS (un-portable).
/// No path collision (`/api/shell*`, `/api/cookbook/packages*`).
pub mod shell_routes;

/// `routes/mcp_routes.py` (wave-7, app.py include #34) — `setup_mcp_routes()` (the
/// Python factory's captured `mcp_manager` arg reached via `State<AppState>.mcp_manager`,
/// mirroring `setup_research_routes` / `setup_model_routes`). **web+db**. Raw `rusqlite`
/// CRUD over the `mcp_servers` table (mirroring the SQLAlchemy `McpServer` ORM),
/// delegating connect/disconnect/status/tool-discovery to the already-ported
/// [`crate::src::mcp_manager::McpManager`]. Every handler `require_admin`-gated. The
/// OAuth flow is an HONEST STUB (untestable external flow). No path collision
/// (`/api/mcp*`).
pub mod mcp_routes;

/// `routes/webhook_routes.py` (wave-7, app.py include #35) — `setup_webhook_routes()`
/// (the Python factory's `(webhook_manager, auth_manager, session_manager,
/// api_key_manager)` args reached via [`AppState`]). **web+db**. Admin-gated webhook
/// CRUD over the `webhooks` table (raw `rusqlite`, mirroring the SQLAlchemy `Webhook`
/// ORM): list / create / test-ping / toggle / delete. `POST /api/v1/chat` (the sync
/// chat endpoint) is an HONEST STUB reproducing the un-ported API-token Bearer
/// middleware path. No path collision (`/api/webhooks*`, `/api/v1/chat`).
pub mod webhook_routes;

// ---------------------------------------------------------------------------
// WAVE 8 — EMAIL: the mail TRANSPORT half + the email / contacts routers + the
// background pollers. Purely ADDITIVE (no inline `web/mod.rs` collision, no
// reconciliation): the routers own fresh prefixes (`/api/email*`,
// `/api/contacts*`). `email_helpers` (transport) and `email_pollers` (the
// startup background task) expose NO router and are NEVER merged.
// ---------------------------------------------------------------------------

/// `routes/email_helpers.py` (wave-8) — the TRANSPORT half of the mail stack:
/// IMAP/SMTP/MIME (`imap` / `native-tls` / `lettre` / `mail-parser`), the
/// account-config resolver, folder/UID helpers, the `scheduled_emails.db`
/// bootstrap, the AI-pipeline text helpers, and the request/response dataclasses.
/// Imported by [`email_routes`] and [`email_pollers`]. NO router (pure helpers +
/// transport), so it is only `pub mod`-declared here — it never appears in
/// [`api_routers`]. **web + db**.
pub mod email_helpers;

/// `routes/email_routes.py` (wave-8, app.py include #38) — `setup_email_routes()`.
/// **web+db**. The 42 `/api/email/*` handlers (IMAP list/read/search/folders/flags/
/// move/delete/attachments, SMTP send/draft/scheduled, the AI summarize/extract-
/// style/ai-reply pipeline) over the wave-8 [`email_helpers`] transport. Every
/// handler is owner/admin-gated via [`auth_adapter`]. No path collision (`/api/email*`).
pub mod email_routes;

/// `routes/contacts_routes.py` (wave-8, app.py include #40) — `setup_contacts_routes()`.
/// **web+db**. CardDAV contacts integration (`/api/contacts/*`): list/search/add/edit/
/// delete, vCard + CSV import/export, backed by a local Radicale/CardDAV server (sync
/// `httpx` -> `reqwest::blocking` under `spawn_blocking`, `quick-xml`/`csv`) with a
/// `data/contacts.json` fallback. Every handler is `require_admin`-gated. No path
/// collision (`/api/contacts*`).
pub mod contacts_routes;

/// `routes/email_pollers.py` (wave-8) — the background mail loops. NOT a router:
/// exposes [`email_pollers::start_email_pollers`] (the `_start_poller()` analogue),
/// spawned from `web::run()` like the rate-limit cleanup task. Honours
/// `ODYSSEUS_INPROCESS_POLLERS`; spawns ONLY the 30s scheduled-email poller (the
/// legacy auto-summarize loop is intentionally not started, matching the Python).
/// Never merged into [`api_routers`]. **web + db**.
pub mod email_pollers;

/// `app.include_router(...)` — assemble every ported FastAPI router into a single
/// `Router<AppState>`, in the **exact** `app.py` registration order.
///
/// app.py builds one `FastAPI()` and calls `app.include_router(setup_X_routes(...))`
/// ~40 times. The axum analogue merges each `setup_x_routes() -> Router<AppState>`
/// in the same sequence. [`crate::web::build_router`] calls `.merge(api_routers())`
/// **after** the inline `.route(...)` chain (the hand-written demo subset) and
/// **before** the middleware/state layers.
///
/// ## Additive & non-breaking (collision rule)
/// `axum::Router::merge` **panics at startup on a duplicate `method`+`path`**, which
/// is the enforcement mechanism that keeps this additive. Two consequences the
/// design pins down:
///
/// * The **five colliding routers** — `auth_routes`, `session_routes`,
///   `history_routes`, `model_routes`, `chat_routes` — DUPLICATE paths the inline
///   `web/mod.rs` subset already owns (`/api/auth/*`, `/api/sessions`,
///   `/api/session*`, `/api/history/:sid`, `/api/models`, `/api/model-endpoints`,
///   `/api/chat_stream`). They are **excluded** here until the wave-4/5
///   RECONCILIATION (delete the inline handlers in the same commit the full port
///   joins this list). See the reconciliation plan in the design.
/// * The proof batch is chosen to have **zero** path overlap with the inline
///   subset, so the new routers and the inline chain coexist.
///
/// ## Growth
/// Each wave un-comments its `pub mod` line (above) and slots the matching
/// `.merge(setup_x_routes())` call into the correct ordinal below. Until the proof
/// batch lands, this returns an empty `Router::new()` — a no-op merge, so the
/// inline subset is the entire API surface (byte-for-byte unchanged).
///
/// The `app.py` include order (the slots routers fill, with `*` = the five
/// reconciliation-deferred colliding routers, omitted until their wave):
/// ```text
///   1  auth_routes*            21  signature_routes  (P13)
///   2  upload_routes           22  gallery_routes
///   3  emoji_routes   (P2)     23  editor_draft_routes
///   4  session_routes*         24  task_routes
///   5  admin_wipe_routes       25  assistant_routes
///   6  memory_routes           26  calendar_routes
///   7  skills_routes           27  shell_routes
///   8  chat_routes*            28  cookbook_routes
///   9  research_routes         29  hwfit_routes      (P6)
///  10  history_routes*         30  compare_routes    (P8)
///  11  search_routes  (P4)     31  prefs_routes      (P3)
///  12  preset_routes  (P9)     32  backup_routes
///  13  diagnostics_routes (P5) 33  font_routes       (P1)
///  14  cleanup_routes (P10)    34  mcp_routes
///  15  personal_routes         35  webhook_routes
///  16  embedding_routes        36  api_token_routes
///  17  model_routes*           37  note_routes
///  18  tts_routes     (P11)    38  email_routes
///  19  stt_routes     (P12)    39  vault_routes      (P7)
///  20  document_routes         40  contacts_routes
/// ```
pub fn api_routers() -> axum::Router<AppState> {
    // Start from an empty router; each wave's modules merge in below in the
    // `app.py` include order shown above. An empty merge is a no-op, so the
    // inline `web/mod.rs` subset remains the full API surface until routers land.
    #[allow(unused_mut)]
    let mut router: axum::Router<AppState> = axum::Router::new();

    // --- PROOF BATCH (wave: proof) — slotted at their `app.py` ordinals -------
    // Un-comment each `.merge(...)` as the matching module file is written
    // (the `pub mod` lines above gate compilation; both move together per wave):
    //
    //   router = router
    //       .merge(emoji_routes::setup_emoji_routes())          //  3
    //       .merge(preset_routes::setup_preset_routes())        // 12
    //       .merge(diagnostics_routes::setup_diagnostics_routes()) // 13 (web+db)
    //       .merge(cleanup_routes::setup_cleanup_routes())      // 14 (web+db)
    //       .merge(tts_routes::setup_tts_routes())              // 18
    //       .merge(stt_routes::setup_stt_routes())              // 19
    //       .merge(signature_routes::setup_signature_routes())  // 21 (web+db)
    //       .merge(hwfit_routes::setup_hwfit_routes())          // 29

    // LANDED: slotted at their app.py ordinals (emoji 3 < search 11 < preset 12 <
    // tts 18 < stt 19 < hwfit 29 < prefs 31 < font 33 < vault 39), so the merge order
    // mirrors `app.include_router`. As more proof-batch modules land they join the
    // chain at their respective ordinals.
    router = router
        .merge(auth_routes::setup_auth_routes()) // 1   auth_routes (web) — WAVE 4
        .merge(emoji_routes::setup_emoji_routes()) // 3   emoji_routes
        .merge(skills_routes::setup_skills_routes()) // 7   skills_routes (web)
        .merge(search_routes::setup_search_routes()) // 11  search_routes
        .merge(preset_routes::setup_preset_routes()) // 12  preset_routes
        .merge(personal_routes::setup_personal_routes()) // 15  personal_routes
        .merge(embedding_routes::setup_embedding_routes()) // 16  embedding_routes
        .merge(tts_routes::setup_tts_routes()) // 18  tts_routes
        .merge(stt_routes::setup_stt_routes()) // 19  stt_routes
        .merge(hwfit_routes::setup_hwfit_routes()) // 29  hwfit_routes
        .merge(prefs_routes::setup_prefs_routes()) // 31  prefs_routes
        .merge(font_routes::setup_font_routes()) // 33  font_routes
        .merge(vault_routes::setup_vault_routes()); // 39  vault_routes

    // db-backed routers (upload @2, admin_wipe @5, P5 diagnostics @13, P10 cleanup @14,
    // P13 signature @21, editor_draft @23, P8 compare @30, backup @32, api_token @36).
    // The crate has no cargo feature flags, so they are always compiled and merged,
    // exactly like the live Python app exposes `/api/db/stats` or `/api/compare/*`
    // whenever a database is configured. They are slotted in
    // app.py include order (upload 2 < admin_wipe 5 < diagnostics 13 < cleanup 14 <
    // signature 21 < editor_draft 23 < compare 30 < backup 32 < api_token 36) and none
    // collides with the base chain or the inline subset (upload owns `/api/upload*`;
    // admin_wipe owns `/api/admin/wipe/*`; diagnostics owns `/api/db|rag|test/*`;
    // cleanup owns `/api/cleanup*`; signature owns `/api/signatures*`; editor_draft
    // owns `/api/editor-drafts*`; compare owns `/api/compare/*`; backup owns
    // `/api/export`+`/api/import`; api_token owns `/api/tokens*`).
    {
        router = router
            .merge(upload_routes::setup_upload_routes()) // 2  upload_routes
            .merge(session_routes::setup_session_routes()) // 4  session_routes — WAVE 4
            .merge(admin_wipe_routes::setup_admin_wipe_routes()) // 5  admin_wipe_routes
            .merge(memory_routes::setup_memory_routes()) // 6  memory_routes
            .merge(chat_routes::setup_chat_routes()) // 8  chat_routes — WAVE 5
            .merge(research_routes::setup_research_routes()) // 9  research_routes
            .merge(history_routes::setup_history_routes()) // 10 history_routes — WAVE 4
            .merge(diagnostics_routes::setup_diagnostics_routes()) // 13 diagnostics_routes
            .merge(cleanup_routes::setup_cleanup_routes()) // 14 cleanup_routes
            .merge(model_routes::setup_model_routes()) // 17 model_routes — WAVE 4
            .merge(document_routes::setup_document_routes()) // 20 document_routes — WAVE 6
            .merge(signature_routes::setup_signature_routes()) // 21 signature_routes
            .merge(gallery_routes::setup_gallery_routes()) // 22 gallery_routes — WAVE 6
            .merge(editor_draft_routes::setup_editor_draft_routes()) // 23 editor_draft_routes
            .merge(task_routes::setup_task_routes()) // 24 task_routes
            .merge(assistant_routes::setup_assistant_routes()) // 25 assistant_routes
            .merge(calendar_routes::setup_calendar_routes()) // 26 calendar_routes — WAVE 7
            .merge(shell_routes::setup_shell_routes()) // 27 shell_routes — WAVE 7
            .merge(cookbook_routes::setup_cookbook_routes()) // 28 cookbook_routes — WAVE 6
            .merge(compare_routes::setup_compare_routes()) // 30 compare_routes
            .merge(backup_routes::setup_backup_routes()) // 32 backup_routes
            .merge(mcp_routes::setup_mcp_routes()) // 34 mcp_routes — WAVE 7
            .merge(webhook_routes::setup_webhook_routes()) // 35 webhook_routes — WAVE 7
            .merge(api_token_routes::setup_api_token_routes()) // 36 api_token_routes
            .merge(note_routes::setup_note_routes()) // 37 note_routes
            .merge(email_routes::setup_email_routes()) // 38 email_routes — WAVE 8
            .merge(contacts_routes::setup_contacts_routes()) // 40 contacts_routes — WAVE 8
            .merge(codex_routes::codex_router()) // 39 codex_routes — agent HTTP surface
            .merge(codex_routes::claude_router()); // 39b claude skill bundle
    }

    router
}

#[cfg(test)]
mod http_exception_tests {
    use super::HttpException;
    use axum::http::StatusCode;
    use axum::response::IntoResponse;

    async fn body_json(resp: axum::response::Response) -> serde_json::Value {
        let bytes = axum::body::to_bytes(resp.into_body(), usize::MAX)
            .await
            .unwrap();
        serde_json::from_slice(&bytes).unwrap()
    }

    #[tokio::test]
    async fn string_detail_renders_detail_string() {
        let resp = HttpException::new(404, "Not found").into_response();
        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
        assert_eq!(body_json(resp).await, serde_json::json!({"detail": "Not found"}));
    }

    #[tokio::test]
    async fn dict_detail_renders_object() {
        // tts/stt: raise HTTPException(503, detail={"message": "..."})
        let resp =
            HttpException::with_detail(503, serde_json::json!({"message": "TTS service not available"}))
                .into_response();
        assert_eq!(resp.status(), StatusCode::SERVICE_UNAVAILABLE);
        assert_eq!(
            body_json(resp).await,
            serde_json::json!({"detail": {"message": "TTS service not available"}})
        );
    }

    #[tokio::test]
    async fn out_of_range_status_falls_back_to_500() {
        // `StatusCode::from_u16` accepts 100..=999; anything outside that range
        // (here 1000) is rejected and we fall back to 500.
        let resp = HttpException::new(1000, "boom").into_response();
        assert_eq!(resp.status(), StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(body_json(resp).await, serde_json::json!({"detail": "boom"}));
    }
}

#[cfg(test)]
mod aggregator_tests {
    //! F5-aggregator-and-merge: the aggregator must produce a `Router<AppState>`
    //! that merges cleanly with the inline `web/mod.rs` subset. `Router::merge`
    //! panics on a duplicate `method`+`path`, so a successful merge is the proof
    //! of the additive / collision-free invariant — exactly the enforcement
    //! mechanism the design relies on.

    /// `api_routers()` is callable and yields a mergeable router (today empty —
    /// the proof batch hasn't landed, so it is a no-op). This pins the signature
    /// `() -> Router<AppState>` that every wave's `.merge(setup_x_routes())` slots
    /// into, and that `web/mod.rs::build_router` consumes.
    #[test]
    fn api_routers_is_a_mergeable_appstate_router() {
        // Merging the aggregator into a fresh `Router<AppState>` must not panic
        // (no duplicate method+path). With an empty aggregator this is trivially
        // true; as the proof batch lands, this guards against intra-aggregator
        // collisions independent of the inline subset.
        let base: axum::Router<super::AppState> = axum::Router::new();
        let _merged: axum::Router<super::AppState> = base.merge(super::api_routers());
    }
}
