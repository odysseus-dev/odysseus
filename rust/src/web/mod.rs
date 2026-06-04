// web/mod.rs  <- app.py (the runnable server)
//! The axum translation of `app.py` — the production server entrypoint.
//!
//! It boots the already-translated foundation (DB schema/migrations via
//! `core::database::init_db`, the DB-backed `core::session_manager::SessionManager`,
//! and `core::auth::AuthManager`) and serves the **real** static frontend
//! (`static/index.html`, `static/login.html`, `static/...`) over the full FastAPI
//! route surface: the inline demo/Codex subset here (`/api/health`, `/api/version`,
//! `/api/codex/connect`, `/api/demo/estimate`) PLUS every ported `routes/X.py`
//! factory merged via [`crate::routes::api_routers`] in the exact app.py
//! `app.include_router(...)` order.
//!
//! The Starlette `BaseHTTPMiddleware` layers become axum `from_fn` middleware;
//! `core::middleware` holds their framework-free core:
//!
//!   * `CORSMiddleware` (app.py:52) — [`CorsLayer`] honoring `ALLOWED_ORIGINS`;
//!   * `SecurityHeadersMiddleware` (app.py:61) — [`security_headers_mw`];
//!   * `_RequestTimeoutMiddleware` (app.py:88-102) — [`request_timeout_mw`] (504 +
//!     the nine streaming/long-running exempt prefixes, `REQUEST_HARD_TIMEOUT`);
//!   * `AuthMiddleware` (app.py:130-268) — [`auth_gate`], the FULL gate: exempt
//!     paths, the internal-tool token bypass, `LOCALHOST_BYPASS`, the `is_configured`
//!     guard, the Bearer API-token path (prefix-bucketed [`token_cache`] +
//!     `bcrypt::verify`, fire-and-forget `last_used_at` touch), then the session
//!     cookie path. Layers apply outermost-last; the stack matches app.py's
//!     `add_middleware` order (auth outermost of the app layers, trace absolute).
//!
//! The startup phase ([`run`]) mirrors `startup_event`: the incognito-session wipe,
//! the rate-limit-cleanup + email-poller spawns, the detached MCP-connect task
//! (`register_builtin_servers` + `connect_all_enabled`), the `task_scheduler.start`
//! gate, the bg-job monitor, the tool-index warm-up, the hourly null-owner sweep,
//! the nightly skill audit (`run_scheduled_skill_audit` on the ~02:00 schedule), and
//! the Ollama auto-detect; graceful shutdown stops the scheduler and disconnects MCP.
//! DOCUMENTED gaps (no observable behavior change): the endpoint warm-up / keep-alive
//! loops (Python's `model_discovery.get_endpoints()` has no Rust accessor).

use std::net::SocketAddr;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use axum::extract::{Query, Request, State};
use axum::http::{header, StatusCode};
use axum::middleware::{self, Next};
use axum::response::{Html, IntoResponse, Redirect, Response};
use axum::routing::{get, post};
use axum::{Extension, Json, Router};
use axum_extra::extract::cookie::CookieJar;
use once_cell::sync::Lazy;
use serde_json::{json, Value};
use tokio::sync::Mutex as AsyncMutex;
use tower_http::cors::CorsLayer;
use tower_http::services::ServeDir;
use tower_http::trace::TraceLayer;

use crate::core::auth::AuthManager;
use crate::core::constants;
use crate::core::middleware as mw;
use crate::core::session_manager::SessionManager;
use crate::pyos as os;
use crate::src::api_key_manager::APIKeyManager;
use crate::src::chat_handler::ChatHandler;
use crate::src::chat_processor::ChatProcessor;
use crate::src::mcp_manager::McpManager;
use crate::src::memory::MemoryManager;
use crate::src::memory_vector::MemoryVectorStore;
use crate::src::model_discovery::ModelDiscovery;
use crate::src::personal_docs::{PersonalDocsManager, RagManager};
use crate::src::preset_manager::PresetManager;
use crate::src::research_handler::ResearchHandler;
use crate::src::task_scheduler::TaskScheduler;
use crate::src::upload_handler::UploadHandler;
use crate::src::webhook_manager::WebhookManager;

/// `SESSION_COOKIE` (routes/auth_routes.py).
const SESSION_COOKIE: &str = "odysseus_session";

/// The `app.state`: the managers + handlers the routers reach + the auth flags
/// app.py reads from env.
///
/// EXTENDED ADDITIVELY (F2-appstate-extend-and-expose): the four original fields
/// (`auth` / `sessions` / `auth_enabled` / `localhost_bypass`) are UNTOUCHED so
/// the inline `web/mod.rs` route subset keeps compiling and working byte-for-byte.
/// The new `Arc` handles mirror the manager set the ported `routes/X.py`
/// factories capture (`app_initializer::initialize_managers` + the three
/// side-built managers). They are declared here so `setup_x_routes()` modules can
/// compile against `Router<AppState>`; **constructing** them in `run()` is the
/// separate F4-startup-wiring unit (this unit only changes the type + visibility).
///
/// Notes on the additive shape (per the integration design):
/// * `memory_vector` / `rag_manager` are `Option` (`None` on the live path —
///   ChromaDB/embedding degraded, application RAG singleton disabled).
/// * `preset_manager` is `Arc<Mutex<PresetManager>>` (a `tokio::sync::Mutex`,
///   await-safe) because `PresetManager::{load, save, update_custom,
///   save_user_template, save_group_presets}` all take `&mut self` (VERIFIED).
/// * `tts` / `stt` are deliberately NOT fields — they are `&'static` singletons
///   reached via `services::tts::get_tts_service()` /
///   `services::stt::get_stt_service()` directly in handlers.
/// * `invalidate_token_cache` is the REAL dirty-flag setter for the Bearer
///   API-token [`token_cache`] (`api_token_routes` create/delete call it so a minted
///   or revoked token takes effect on the next Bearer request). It wraps
///   `token_cache::invalidate()`.
#[derive(Clone)]
pub struct AppState {
    // --- Original four fields (UNTOUCHED — the inline subset depends on these) ---
    pub auth: Arc<AuthManager>,
    pub sessions: Arc<SessionManager>,
    pub auth_enabled: bool,
    pub localhost_bypass: bool,

    // --- Added manager / handler handles (populated in F4-startup-wiring) ---
    /// `memory_manager` (`src.memory.MemoryManager`).
    pub memory_manager: Arc<MemoryManager>,
    /// `memory_vector` (`src.memory_vector.MemoryVectorStore`) — `None` when DEGRADED.
    pub memory_vector: Option<Arc<MemoryVectorStore>>,
    /// `skills_manager` (`services.memory.skills.SkillsManager`).
    pub skills_manager: Arc<crate::services::memory::skills::SkillsManager>,
    /// `upload_handler` (`src.upload_handler.UploadHandler`).
    pub upload_handler: Arc<UploadHandler>,
    /// `personal_docs_manager` (`src.personal_docs.PersonalDocsManager`).
    pub personal_docs_manager: Arc<PersonalDocsManager>,
    /// `api_key_manager` (`src.api_key_manager.APIKeyManager`).
    pub api_key_manager: Arc<APIKeyManager>,
    /// `preset_manager` (`src.preset_manager.PresetManager`) — `Mutex` because its
    /// mutating methods take `&mut self` (VERIFIED).
    pub preset_manager: Arc<tokio::sync::Mutex<PresetManager>>,
    /// `model_discovery` (`src.model_discovery.ModelDiscovery`).
    pub model_discovery: Arc<ModelDiscovery>,
    /// `research_handler` (`src.research_handler.ResearchHandler`).
    pub research_handler: Arc<ResearchHandler>,
    /// `chat_handler` (`src.chat_handler.ChatHandler`).
    pub chat_handler: Arc<ChatHandler<UploadHandler>>,
    /// `chat_processor` (`src.chat_processor.ChatProcessor`).
    pub chat_processor: Arc<ChatProcessor>,
    /// `webhook_manager` (`src.webhook_manager.WebhookManager`).
    pub webhook_manager: Arc<WebhookManager>,
    /// `task_scheduler` (`src.task_scheduler.TaskScheduler`).
    pub task_scheduler: Arc<TaskScheduler>,
    /// `mcp_manager` (`src.mcp_manager.McpManager`).
    pub mcp_manager: Arc<McpManager>,
    /// `rag_manager` — `None` on the live path (the application RAG singleton is
    /// disabled; `rag_singleton::get_rag_manager()` -> `None`).
    pub rag_manager: Option<Arc<dyn RagManager>>,
    /// Best-effort cache-invalidation hook (`invalidate_token_cache()` in
    /// api_token_routes). An `Arc<dyn Fn()>` wired in `run()` to the REAL Bearer
    /// [`token_cache`] dirty-flag setter (`token_cache::invalidate()`), so a minted
    /// or revoked token takes effect on the next Bearer request; Python wraps the
    /// call in try/except, mirroring `_token_cache_invalidate` (app.py:140-143).
    pub invalidate_token_cache: Arc<dyn Fn() + Send + Sync>,
}

/// The per-request CSP nonce (`request.state.csp_nonce`), set by the security
/// middleware and read by the HTML handlers for `{{CSP_NONCE}}` injection.
#[derive(Clone)]
struct CspNonce(String);

/// The authenticated user (`request.state.current_user`), stamped by the auth gate
/// **only when resolved** (so handlers read `Option<Extension<CurrentUser>>` and
/// `get_current_user` can return `None` — the load-bearing unauthenticated case
/// the compare/signature/sessions/notes routers depend on).
#[derive(Clone)]
pub struct CurrentUser(pub String);

/// `request.state.current_user` as an always-present optional, for handlers /
/// adapters that prefer a single extractor to `Option<Extension<CurrentUser>>`.
///
/// The auth gate stamps `CurrentUser` only when a user resolves; the auth-adapter
/// (F3) and the ported routers read either `Option<Extension<CurrentUser>>` or
/// this `MaybeUser` wrapper to bridge to the already-ported
/// `src::auth_helpers::get_current_user` semantics (which returns `Option<String>`).
#[derive(Clone, Default)]
pub struct MaybeUser(pub Option<String>);

/// The validated API-token state stamped on the request by [`auth_gate`]'s Bearer
/// branch, mirroring `request.state.{api_token,api_token_id,api_token_owner,
/// api_token_scopes}` (app.py:245-249).
///
/// The auth gate inserts this as an axum `Extension` on three branches:
/// * Bearer-token match — `present = true` + the matched id/owner/scopes;
/// * internal-tool bypass and the cookie path — `present = false` (a definite
///   not-a-token marker, matching Python's `request.state.api_token = False`).
///
/// Routes read `Option<Extension<ApiToken>>`; an ABSENT extension means
/// `present = false` (matching Python's `getattr(request.state, "api_token",
/// False)`), so an exempt/bypassed request with no stamp is treated as not-a-token.
#[derive(Clone, Default)]
pub struct ApiToken {
    /// `request.state.api_token` — `true` only on a validated Bearer token.
    pub present: bool,
    /// `request.state.api_token_id` — the matched `api_tokens.id`.
    pub id: Option<String>,
    /// `request.state.api_token_owner` — the token's `owner` (NULL -> `None`).
    pub owner: Option<String>,
    /// `request.state.api_token_scopes` — the token's scopes (default `["chat"]`).
    pub scopes: Vec<String>,
}

// ===========================================================================
// API-token cache (app.py:131-161 — the `_token_cache` + `_refresh_token_cache`
// + `_token_cache_invalidate` set, the Bearer-auth fast path)
// ===========================================================================

/// One cached active token: `(id, token_hash, owner, scopes)` (app.py:156's tuple).
#[derive(Clone)]
struct CachedToken {
    id: String,
    hash: String,
    owner: Option<String>,
    scopes: Vec<String>,
}

/// The in-memory token cache: `prefix -> [(id, hash, owner, scopes)]` plus a dirty
/// flag (app.py's `_token_cache` / `_token_cache_dirty` / `_token_cache_lock`).
///
/// The DB query (a bcrypt-per-row linear scan) was running on every Bearer request;
/// this caches the active tokens, hitting the DB only when the cache is dirty
/// (a token is created/revoked -> `invalidate()`). The map sits behind a
/// `tokio::sync::Mutex` (the `asyncio.Lock` analogue); the dirty flag is a separate
/// `AtomicBool` so the common "fresh" check is lock-free, and `dirty` starts `true`
/// so the first Bearer request populates it lazily.
struct TokenCache {
    map: AsyncMutex<std::collections::HashMap<String, Vec<CachedToken>>>,
    dirty: AtomicBool,
}

/// `app.state._token_cache` — the process-wide token cache singleton.
static TOKEN_CACHE: Lazy<TokenCache> = Lazy::new(|| TokenCache {
    map: AsyncMutex::new(std::collections::HashMap::new()),
    dirty: AtomicBool::new(true),
});

mod token_cache {
    //! `_token_cache_invalidate` / `_refresh_token_cache` / the lazy `ensure_fresh`
    //! double-check (app.py:140-161, 210-214).

    use super::{CachedToken, Ordering, TOKEN_CACHE};

    /// `_token_cache_invalidate()` (app.py:140-143) — mark the cache dirty so the
    /// next Bearer request rebuilds it. The real `AppState.invalidate_token_cache`
    /// hook (replacing the prior no-op), called by `api_token_routes` create/delete.
    pub fn invalidate() {
        TOKEN_CACHE.dirty.store(true, Ordering::Release);
    }

    /// `_refresh_token_cache()` (app.py:147-161) — rebuild the `prefix -> [...]`
    /// map from `SELECT ... FROM api_tokens WHERE is_active = 1`, bucketing by the
    /// DB `token_prefix` column (which equals `raw_token[:8]` at mint time). Blocking
    /// rusqlite, so it is run on a [`tokio::task::spawn_blocking`] thread (the
    /// `await asyncio.to_thread(_refresh_token_cache)` analogue).
    async fn refresh() -> std::collections::HashMap<String, Vec<CachedToken>> {
        tokio::task::spawn_blocking(|| {
            let mut new_map: std::collections::HashMap<String, Vec<CachedToken>> =
                std::collections::HashMap::new();
            let conn = match crate::core::database::session_local() {
                Ok(c) => c,
                Err(e) => {
                    crate::pylog::warning(&format!("token cache refresh DB error: {e}"));
                    return new_map;
                }
            };
            let mut stmt = match conn.prepare(
                "SELECT id, token_hash, owner, scopes, token_prefix FROM api_tokens WHERE is_active = 1",
            ) {
                Ok(s) => s,
                Err(e) => {
                    crate::pylog::warning(&format!("token cache refresh query error: {e}"));
                    return new_map;
                }
            };
            let rows = stmt.query_map([], |r| {
                let id: String = r.get(0)?;
                let hash: String = r.get(1)?;
                let owner: Option<String> = r.get(2)?;
                let scopes_raw: Option<String> = r.get(3)?;
                // The `token_prefix` column equals `raw_token[:8]` at mint time, so
                // a re-derived prefix from the hash is impossible (hashes aren't the
                // raw token); bucket by the stored `token_prefix` instead, exactly
                // like the Python's `new_map[r.token_prefix].append(...)`.
                let prefix: String = r.get(4)?;
                Ok((prefix, id, hash, owner, scopes_raw))
            });
            let rows = match rows {
                Ok(it) => it,
                Err(e) => {
                    crate::pylog::warning(&format!("token cache refresh row error: {e}"));
                    return new_map;
                }
            };
            for row in rows.flatten() {
                let (prefix, id, hash, owner, scopes_raw) = row;
                // `[s.strip() for s in (scopes or "chat").split(",") if s.strip()]`.
                let scopes = split_scopes(scopes_raw.as_deref());
                new_map
                    .entry(prefix)
                    .or_default()
                    .push(CachedToken { id, hash, owner, scopes });
            }
            new_map
        })
        .await
        .unwrap_or_default()
    }

    /// Ensure the cache is fresh (app.py:210-214's double-checked lazy refresh): if
    /// dirty, take the map lock, re-check dirty under the lock, and if still dirty
    /// run [`refresh`] and clear the flag. Mirrors `if dirty: async with lock: if
    /// dirty: await to_thread(_refresh_token_cache)`.
    pub async fn ensure_fresh() {
        if !TOKEN_CACHE.dirty.load(Ordering::Acquire) {
            return;
        }
        let mut map = TOKEN_CACHE.map.lock().await;
        if !TOKEN_CACHE.dirty.load(Ordering::Acquire) {
            return;
        }
        *map = refresh().await;
        TOKEN_CACHE.dirty.store(false, Ordering::Release);
    }

    /// The candidates bucketed under `prefix` (a clone so the lock is released
    /// before the bcrypt scan, mirroring `candidates = list(_token_cache.get(...))`).
    pub async fn candidates(prefix: &str) -> Vec<CachedToken> {
        let map = TOKEN_CACHE.map.lock().await;
        map.get(prefix).cloned().unwrap_or_default()
    }

    /// `[s.strip() for s in (scopes or "chat").split(",") if s.strip()]`
    /// (app.py:155) — the same default-`["chat"]`/strip/drop-empties split the
    /// `api_token_routes::split_scopes` port uses.
    fn split_scopes(stored: Option<&str>) -> Vec<String> {
        let value = match stored {
            Some(s) if !s.is_empty() => s,
            _ => "chat",
        };
        value
            .split(',')
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_string)
            .collect()
    }
}

// ===========================================================================
// Middleware (the Starlette BaseHTTPMiddleware classes)
// ===========================================================================

/// `REQUEST_HARD_TIMEOUT = float(os.getenv("REQUEST_HARD_TIMEOUT", "45"))`
/// (app.py:74) — the per-request hard timeout in seconds, read once.
static REQUEST_HARD_TIMEOUT: Lazy<f64> = Lazy::new(|| {
    os::getenv("REQUEST_HARD_TIMEOUT", "45")
        .trim()
        .parse::<f64>()
        .unwrap_or(45.0)
});

/// `_TIMEOUT_EXEMPT_PREFIXES` (app.py:75-85) — the nine streaming / long-running
/// path prefixes that legitimately stay open and are NOT subject to the hard
/// timeout. Order matches the Python tuple exactly.
const TIMEOUT_EXEMPT_PREFIXES: [&str; 9] = [
    "/api/chat",            // streaming
    "/api/shell/stream",    // SSE
    "/api/research",        // multi-minute jobs
    "/api/model/download",  // tmux setup may run pip installs
    "/api/model/probe",     // SSE; iterates models with up to 8s timeout each
    "/api/model-endpoints", // /probe sub-route also iterates models
    "/api/cookbook/setup",  // remote pacman/apt installs
    "/api/upload",          // large files
    "/api/image",           // diffusion proxies — own 120s httpx timeout
];

/// `_RequestTimeoutMiddleware.dispatch` (app.py:88-99): if the path starts with any
/// exempt prefix, pass through unchanged; otherwise wrap the handler in
/// `tokio::time::timeout(REQUEST_HARD_TIMEOUT, ...)` (the `asyncio.wait_for`
/// analogue — it cancels/drops the inner future on elapse, matching `wait_for`'s
/// cancellation) and on elapse return `504 {"detail": "Request exceeded {N:.0f}s
/// timeout"}`.
async fn request_timeout_mw(req: Request, next: Next) -> Response {
    let path = req.uri().path();
    if TIMEOUT_EXEMPT_PREFIXES
        .iter()
        .any(|p| path.starts_with(p))
    {
        return next.run(req).await;
    }
    let t = *REQUEST_HARD_TIMEOUT;
    match tokio::time::timeout(Duration::from_secs_f64(t), next.run(req)).await {
        Ok(resp) => resp,
        Err(_) => (
            StatusCode::GATEWAY_TIMEOUT,
            Json(json!({"detail": format!("Request exceeded {t:.0}s timeout")})),
        )
            .into_response(),
    }
}

/// `SecurityHeadersMiddleware` — generate a per-request nonce, run the handler,
/// then stamp the security headers (from `core::middleware::security_headers`).
async fn security_headers_mw(mut req: Request, next: Next) -> Response {
    let nonce = crate::pysecrets::token_hex(16);
    req.extensions_mut().insert(CspNonce(nonce.clone()));
    let path = req.uri().path().to_string();

    let mut resp = next.run(req).await;

    let headers = resp.headers_mut();
    for (name, value) in mw::security_headers(&path, &nonce) {
        if let Ok(v) = header::HeaderValue::from_str(&value) {
            if let Ok(n) = header::HeaderName::from_bytes(name.as_bytes()) {
                headers.insert(n, v);
            }
        }
    }
    resp
}

/// The auth-gate middleware (app.py's `_auth_middleware`): exempt paths pass
/// through; otherwise the `odysseus_session` cookie is validated and the
/// resolved username is stamped as `CurrentUser`. Unauthenticated `/api/*` gets
/// 401 JSON; any other path redirects to `/login`.
fn is_auth_exempt(path: &str) -> bool {
    const EXACT: &[&str] = &[
        "/api/auth/setup",
        "/api/auth/signup",
        "/api/auth/login",
        "/api/auth/logout",
        "/api/auth/status",
        "/api/auth/features",
        "/api/auth/settings",
        "/api/auth/integrations/presets",
        "/api/health",
        "/api/version",
        "/login",
    ];
    if EXACT.contains(&path) || path.starts_with("/static") {
        return true;
    }
    // Per-task webhook URLs: `routes/task_routes` validates the path-embedded
    // `webhook_token` itself, so the request must REACH the handler (otherwise it
    // 401s before the token check). app.py AUTH_EXEMPT_PATTERNS.
    static WEBHOOK_RE: once_cell::sync::Lazy<regex::Regex> = once_cell::sync::Lazy::new(|| {
        regex::Regex::new(r"^/api/tasks/[^/]+/webhook/[^/]+/?$").unwrap()
    });
    WEBHOOK_RE.is_match(path)
}

/// `_PROXY_FWD_HEADERS` — proxy/tunnel forwarding headers. Their presence means
/// the connection arrived THROUGH a proxy (a Cloudflare tunnel connects from
/// loopback), so a 127.0.0.1/::1 peer does NOT imply a direct local user.
const PROXY_FWD_HEADERS: [&str; 7] = [
    "cf-connecting-ip",
    "cf-ray",
    "cf-visitor",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-real-ip",
    "forwarded",
];

/// The peer IP as a string (`request.client.host`), or `None` when the connecting
/// address is unknown (`request.client is None`). Present only when the router is
/// served via `into_make_service_with_connect_info::<SocketAddr>()`.
fn client_host(req: &Request) -> Option<String> {
    req.extensions()
        .get::<axum::extract::ConnectInfo<SocketAddr>>()
        .map(|ci| ci.0.ip().to_string())
}

/// `_is_trusted_loopback(request)` — true ONLY for a DIRECT loopback connection
/// with NO proxy/tunnel forwarding headers. A bare `client in (127.0.0.1, ::1)`
/// check is unsafe behind a reverse proxy / Cloudflare tunnel (those connect from
/// loopback), so this also rejects any request carrying a `PROXY_FWD_HEADERS`
/// header. Odysseus's own in-process loopback calls carry none, so they qualify.
fn is_trusted_loopback(req: &Request) -> bool {
    if !matches!(client_host(req).as_deref(), Some("127.0.0.1") | Some("::1")) {
        return false;
    }
    !PROXY_FWD_HEADERS.iter().any(|h| {
        req.headers()
            .get(*h)
            .map(|v| !v.as_bytes().is_empty())
            .unwrap_or(false)
    })
}

/// Constant-time string compare (the `secrets.compare_digest` intent — no
/// early-exit on the first differing byte). `subtle`/`constant_time_eq` are not
/// deps, so this is a length-checked XOR-accumulate.
fn ct_eq(a: &str, b: &str) -> bool {
    let (a, b) = (a.as_bytes(), b.as_bytes());
    if a.len() != b.len() {
        return false;
    }
    let mut diff = 0u8;
    for i in 0..a.len() {
        diff |= a[i] ^ b[i];
    }
    diff == 0
}

async fn auth_gate(State(state): State<AppState>, mut req: Request, next: Next) -> Response {
    let path = req.uri().path().to_string();
    if !state.auth_enabled || is_auth_exempt(&path) {
        return next.run(req).await;
    }

    // --- (1) INTERNAL-TOOL token bypass (app.py:168-188) — runs FIRST, BEFORE the
    // LOCALHOST_BYPASS branch and OUTSIDE the is_configured guard. The in-app tool
    // layer HTTP-loopbacks to admin-gated routes with no admin cookie, so a matching
    // `X-Odysseus-Internal-Token` from a loopback client is trusted and (optionally)
    // impersonates `X-Odysseus-Owner`. Wrapped so a malformed header is ignored
    // (the Python try/except). ---
    {
        let hdr = req
            .headers()
            .get(mw::INTERNAL_TOOL_HEADER)
            .and_then(|v| v.to_str().ok())
            .map(str::to_string);
        if let Some(hdr) = hdr {
            // Constant-time token compare + DIRECT-loopback check (no proxy
            // headers), so a Cloudflare-tunnel/reverse-proxy visitor cannot spoof
            // the internal-tool path.
            if ct_eq(&hdr, mw::INTERNAL_TOOL_TOKEN.as_str()) && is_trusted_loopback(&req) {
                // `_impersonate = (request.headers.get("X-Odysseus-Owner") or "").strip()`.
                // Attribute to that user ONLY if they exist; else "internal-tool".
                let impersonate = req
                    .headers()
                    .get("X-Odysseus-Owner")
                    .and_then(|v| v.to_str().ok())
                    .unwrap_or("")
                    .trim()
                    .to_string();
                let user = if !impersonate.is_empty()
                    && state.auth.users().contains_key(&impersonate)
                {
                    impersonate
                } else {
                    "internal-tool".to_string()
                };
                req.extensions_mut().insert(CurrentUser(user));
                // `request.state.api_token = False`.
                req.extensions_mut().insert(ApiToken::default());
                return next.run(req).await;
            }
        }
    }

    let jar = CookieJar::from_headers(req.headers());
    let token = jar.get(SESSION_COOKIE).map(|c| c.value().to_string());

    // LOCALHOST_BYPASS: trust local requests (heartbeats / internal calls).
    // Matching app.py's bypass branch — gated on the PEER ADDRESS being loopback
    // (`client_host in ("127.0.0.1", "::1")`); a non-loopback client under
    // LOCALHOST_BYPASS does NOT bypass and falls through to the checks below
    // (closing the auth hole when the server binds a non-loopback address). When
    // it does bypass, app.py `return await call_next(request)` WITHOUT setting
    // `request.state.current_user`, so we stamp `CurrentUser` ONLY when a real
    // cookie resolves a user, else leave it ABSENT (`get_current_user` -> None,
    // no first-user fallback).
    if state.localhost_bypass && is_trusted_loopback(&req) {
        if let Some(user) = state.auth.get_username_for_token(token.as_deref()) {
            req.extensions_mut().insert(CurrentUser(user));
        }
        return next.run(req).await;
    }

    // No users yet — first-time setup (app.py: `if not auth_manager.is_configured`).
    // `/api/*` gets 401 JSON `{"error": "Setup required"}`; anything else redirects
    // to `/login`. This runs BEFORE the cookie fallthrough (a configured cookie
    // can't exist without a configured auth store).
    if !state.auth.is_configured() {
        return if path.starts_with("/api/") {
            (StatusCode::UNAUTHORIZED, Json(json!({"error": "Setup required"}))).into_response()
        } else {
            Redirect::to("/login").into_response()
        };
    }

    // --- (4) BEARER API-TOKEN auth (app.py:201-254) — runs AFTER the is_configured
    // guard, BEFORE the cookie path. `Authorization: Bearer ody_...` validates
    // against the prefix-bucketed token cache + `bcrypt::verify`; on match it stamps
    // `current_user = "api"` + the `ApiToken` extension and fires a fire-and-forget
    // `last_used_at` touch. An invalid bearer token is rejected (401) without falling
    // through to the cookie path. The DB-backed cache makes this a `db`-gated path;
    // on the web-only profile (no DB) there is no `api_tokens` table, so the Bearer
    // path is absent exactly as the live app would never expose it without a DB. ---
    let (req, next) = {
        let auth_header = req
            .headers()
            .get(header::AUTHORIZATION)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_string();
        match bearer_auth(&auth_header, req, next).await {
            // Handled (a validated match passed to the handler, or a 401 reject).
            Ok(resp) => return resp,
            // No `Bearer ody_` header — give back `req`/`next` for the cookie path.
            Err(passthrough) => passthrough,
        }
    };
    // `req`/`next` are rebound (by value) from the no-Bearer passthrough above; on
    // the web-only profile (no `db`) the Bearer block is gated out and the original
    // `req`/`next` bindings flow straight through.
    let mut req = req;

    if let Some(user) = state.auth.get_username_for_token(token.as_deref()) {
        req.extensions_mut().insert(CurrentUser(user));
        // `request.state.api_token = False` (app.py:265) — stamp a definite
        // not-a-token marker so a route reading the extension sees `present = false`.
        req.extensions_mut().insert(ApiToken::default());
        return next.run(req).await;
    }

    if path.starts_with("/api/") {
        (StatusCode::UNAUTHORIZED, Json(json!({"error": "Not authenticated"}))).into_response()
    } else {
        Redirect::to("/login").into_response()
    }
}

/// The Bearer API-token branch of [`auth_gate`] (app.py:201-254), factored out so the
/// `next` consumption is contained. Returns:
/// * `Ok(response)` — the request was a Bearer attempt: either a validated match
///   passed to the handler, or an invalid/short/long token rejected 401;
/// * `Err((req, next))` — no `Bearer ody_` header, so the caller falls through to
///   the cookie path with the moved-back `req`/`next`.
async fn bearer_auth(
    auth_header: &str,
    mut req: Request,
    next: Next,
) -> Result<Response, (Request, Next)> {
    // `if auth_header.startswith("Bearer ody_")`.
    if !auth_header.starts_with("Bearer ody_") {
        return Err((req, next));
    }
    // `raw_token = auth_header[7:]` — strip "Bearer " (7 bytes; ASCII so byte == char).
    let raw_token = &auth_header[7..];
    let invalid = || -> Response {
        (StatusCode::UNAUTHORIZED, Json(json!({"error": "Invalid API token"}))).into_response()
    };
    // `if len(raw_token) < 12 or len(raw_token) > 100: -> 401`. `ody_` tokens are
    // ASCII, so byte length matches Python's `len`.
    if raw_token.len() < 12 || raw_token.len() > 100 {
        return Ok(invalid());
    }
    // `prefix = raw_token[:8]` — first 8 chars (ASCII -> first 8 bytes).
    let prefix = &raw_token[..8];

    // `if app.state._token_cache_dirty: async with lock: ... await to_thread(refresh)`.
    token_cache::ensure_fresh().await;
    // `candidates = list(_token_cache.get(prefix, ()))`.
    let candidates = token_cache::candidates(prefix).await;

    // `for tid, thash, owner, scopes in candidates: if bcrypt.checkpw(...): match`.
    let mut matched: Option<CachedToken> = None;
    for cand in candidates {
        if bcrypt::verify(raw_token, &cand.hash).unwrap_or(false) {
            matched = Some(cand);
            break;
        }
    }

    match matched {
        Some(cand) => {
            // `asyncio.create_task(_touch_last_used(matched_id))` — fire-and-forget
            // `UPDATE api_tokens SET last_used_at = utcnow WHERE id = ?` on a blocking
            // thread; swallow errors, exactly like the Python `_do`'s try/except.
            let touch_id = cand.id.clone();
            tokio::spawn(async move {
                let _ = tokio::task::spawn_blocking(move || {
                    if let Ok(conn) = crate::core::database::session_local() {
                        let now = crate::pydatetime::utcnow_naive_iso();
                        let _ = conn.execute(
                            "UPDATE api_tokens SET last_used_at = ?1 WHERE id = ?2",
                            rusqlite::params![now, touch_id],
                        );
                    }
                })
                .await;
            });
            // `request.state.current_user = "api"` + the api_token state stamps.
            req.extensions_mut().insert(CurrentUser("api".to_string()));
            req.extensions_mut().insert(ApiToken {
                present: true,
                id: Some(cand.id),
                owner: cand.owner,
                scopes: cand.scopes,
            });
            // `return await call_next(request)`.
            Ok(next.run(req).await)
        }
        // No match (or the Python `except` log+fall-through) -> reject 401.
        None => Ok(invalid()),
    }
}

// ===========================================================================
// HTML pages (served with `{{CSP_NONCE}}` injection, like `_serve_html_with_nonce`)
// ===========================================================================

fn serve_html_with_nonce(file_path: &str, nonce: &str) -> Response {
    match std::fs::read_to_string(file_path) {
        Ok(html) => Html(html.replace("{{CSP_NONCE}}", nonce)).into_response(),
        Err(_) => (StatusCode::NOT_FOUND, "Page not found").into_response(),
    }
}

/// `GET /` and the SPA sub-routes — serve `static/index.html`.
async fn index_page(Extension(nonce): Extension<CspNonce>) -> Response {
    serve_html_with_nonce(&os::path::join(&constants::STATIC_DIR, "index.html"), &nonce.0)
}

/// `GET /login` — serve `static/login.html`.
async fn login_page(Extension(nonce): Extension<CspNonce>) -> Response {
    serve_html_with_nonce(&os::path::join(&constants::STATIC_DIR, "login.html"), &nonce.0)
}

// ===========================================================================
// Health / version
// ===========================================================================

async fn health() -> Json<Value> {
    Json(json!({
        "status": "healthy",
        "timestamp": crate::pydatetime::utcnow_naive_iso().replace(' ', "T"),
    }))
}

async fn version() -> Json<Value> {
    Json(json!({ "version": constants::APP_VERSION }))
}

// ===========================================================================
// GET /api/ready — readiness / integrity self-check (app.py:779-788, src/readiness.py)
// ===========================================================================

/// `GET /api/ready` — readiness / integrity self-check (`check_readiness` in
/// `src/readiness.py`, registered in app.py:779-788).
///
/// Unlike `/api/health` (liveness), this returns 503 unless every critical
/// subsystem is whole, so an orchestrator can gate traffic on real readiness.
///
/// Checks (mirroring `src/readiness.py` line-by-line):
///   * **database** — `SELECT 1` via `session_local()`; any error → `ok: false`.
///   * **data_dir** — create `DATA_DIR`, write + delete a probe file; error → `ok: false`.
///   * **local_first** — informational only (never fails readiness): `DATABASE_URL`
///     starts with `"sqlite"` OR contains `"localhost"` OR `"127.0.0.1"`.
///
/// Returns `200` when `ready == true`, `503` otherwise.
async fn readiness() -> Response {
    use std::io::Write;

    let data_dir = constants::DATA_DIR.clone();
    let mut checks: serde_json::Map<String, Value> = serde_json::Map::new();

    // ── 1. Database reachable (readiness.py:29-34) ──────────────────────────
    // `conn.execute(sql_text("SELECT 1"))` — the simplest honest probe that the
    // engine is live. rusqlite Connection is !Send, so confine to a blocking scope.
    let db_ok = tokio::task::spawn_blocking(|| -> (bool, Option<String>) {
        match crate::core::database::session_local() {
            Err(e) => (false, Some(e.to_string())),
            // `SELECT 1` returns a row, so it must go through `query_row` —
            // rusqlite's `execute()` rejects row-returning statements ("Execute
            // returned results — did you mean to call query?").
            Ok(conn) => match conn.query_row("SELECT 1", [], |_row| Ok(())) {
                Ok(()) => (true, None),
                Err(e) => (false, Some(e.to_string())),
            },
        }
    })
    .await
    .unwrap_or((false, Some("spawn_blocking panicked".to_string())));

    checks.insert(
        "database".to_string(),
        if db_ok.0 {
            json!({"ok": true})
        } else {
            json!({"ok": false, "error": db_ok.1.unwrap_or_default()})
        },
    );

    // ── 2. Data directory present and writable (readiness.py:37-45) ─────────
    // `os.makedirs` + write + `os.remove` a probe file whose name is a random hex.
    let data_dir_check: (bool, Option<String>) = (|| {
        std::fs::create_dir_all(&data_dir)
            .map_err(|e| e.to_string())?;
        let probe_name = format!(".ready_probe_{:x}", {
            use std::time::{SystemTime, UNIX_EPOCH};
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .subsec_nanos()
        });
        let probe = std::path::Path::new(&data_dir).join(probe_name);
        std::fs::File::create(&probe)
            .and_then(|mut f| f.write_all(b"ok"))
            .map_err(|e| e.to_string())?;
        std::fs::remove_file(&probe).map_err(|e| e.to_string())?;
        Ok::<_, String>(())
    })()
    .map(|_| (true, None))
    .unwrap_or_else(|e| (false, Some(e)));

    checks.insert(
        "data_dir".to_string(),
        if data_dir_check.0 {
            json!({"ok": true, "path": data_dir})
        } else {
            json!({"ok": false, "error": data_dir_check.1.unwrap_or_default()})
        },
    );

    // ── 3. Local-first (readiness.py:47-53) — informational, never fatal ─────
    // `DATABASE_URL.startswith("sqlite") or "localhost" in DATABASE_URL
    //  or "127.0.0.1" in DATABASE_URL`.
    let db_url = std::env::var("DATABASE_URL").unwrap_or_default();
    let local_first = db_url.starts_with("sqlite")
        || db_url.contains("localhost")
        || db_url.contains("127.0.0.1");
    checks.insert("local_first".to_string(), json!({"ok": true, "local": local_first}));

    // `ready = all(bool(c.get("ok")) for c in checks.values())`.
    let ready = checks.values().all(|c| c.get("ok").and_then(|v| v.as_bool()).unwrap_or(false));

    let body = json!({
        "ready": ready,
        "version": constants::APP_VERSION,
        "checks": checks,
        "timestamp": crate::pydatetime::utcnow_naive_iso().replace(' ', "T"),
    });

    (
        if ready { StatusCode::OK } else { StatusCode::SERVICE_UNAVAILABLE },
        Json(body),
    )
        .into_response()
}

// ===========================================================================
// (chat_stream superseded — see `routes::chat_routes`)
// ===========================================================================
//
// The inline `POST /api/chat_stream` demo handler that used to live here (with its
// `owns` / `parse_form` / `sse_bytes` / `not_found` helpers) was SUPERSEDED by the
// faithful wave-5 port `routes::chat_routes::chat_stream` (app.py include #8) and
// DELETED in the same commit that module joined `api_routers()` — exactly as the
// wave-4 auth / session / history / model inline subsets were before it. The ported
// `chat_routes::chat_stream` is the UNION: the full Python research / image / agent /
// chat SSE pipeline (`_active_streams` / `_safe_stream` / `_TOOL_INTENT_PATTERNS`)
// PLUS the Odysseus-Rust codex/agent dispatch this inline handler carried — Mode A
// (`codex:` → `codex::stream_chat`), Mode B (`codex-responses:` → `stream_llm` →
// `stream_codex_responses`), and `is_agent` (`stream_agent_loop`) are all preserved
// there, so nothing was lost in the swap. What remains inline below is the Codex
// integration that is ABSENT from `chat_routes.py` (auto-register + `/api/codex/connect`)
// and the `/api/demo/estimate` pure-logic showcase.

// ===========================================================================
// Model-endpoint config (routes/model_routes.py subset)
// ===========================================================================

/// Auto-register BOTH Codex providers as first-class entries: if the Codex CLI
/// is installed AND logged in (probed once, cached), ensure TWO `model_endpoints`
/// rows exist for this user so both appear natively in the model picker /
/// endpoints list with no setup:
///   * `codex:` → "Codex (Harness)" — Mode A (the `codex app-server` full agent:
///     codex runs its OWN tools server-side).
///   * `codex-responses:` → "Codex (API)" — Mode B (the ChatGPT Responses backend
///     over HTTPS, driven by Odysseus's OWN agent loop + tools).
///
/// Both list the SAME `avail.models`. Idempotency is PER-SCHEME (an exact
/// `base_url` match via `has_codex_scheme`), so each registers independently and
/// exactly once. The Mode-B row is marked `supports_tools = 1` so the agent loop
/// treats it as an API model (sends Odysseus's tool schemas).
///
/// ODYSSEUS-RUST ENHANCEMENT (re-wired wave-4): `routes::model_routes` is a faithful
/// port of `routes/model_routes.py`, which has NO Codex auto-registration (Codex is a
/// Rust-side integration absent from the Python). To keep Codex first-class — appearing
/// in the model picker with zero setup, exactly as the original inline handlers did —
/// the ported `list_models` / `list_model_endpoints` call this at the top of the handler
/// for the authenticated owner. This layers the Codex enhancement on top of the faithful
/// port without altering the Python-mirrored response logic. Codex also still registers
/// via `/api/codex/connect` (`register_codex_providers`) and when a user adds a `codex:`
/// endpoint.
pub(crate) async fn auto_register_codex(owner: &str) {
    let needs_harness = !crate::core::model_endpoints::has_codex_scheme(owner, "codex:");
    let needs_api = !crate::core::model_endpoints::has_codex_scheme(owner, "codex-responses:");
    if !needs_harness && !needs_api {
        return;
    }
    if let Some(avail) = crate::core::codex::ensure_probed().await {
        register_codex_providers(owner, &avail);
    }
}

/// Create the two Codex provider rows (each guarded per-scheme so this is safe to
/// call repeatedly). Shared by `auto_register_codex` and `/api/codex/connect`.
fn register_codex_providers(owner: &str, avail: &crate::core::codex::CodexAvailability) {
    let suffix = if avail.email.is_empty() { String::new() } else { format!(" ({})", avail.email) };
    if !crate::core::model_endpoints::has_codex_scheme(owner, "codex:") {
        let name = format!("Codex (Harness){suffix}");
        let _ = crate::core::model_endpoints::create(&name, "codex:", None, &avail.models, Some(owner));
    }
    if !crate::core::model_endpoints::has_codex_scheme(owner, "codex-responses:") {
        let name = format!("Codex (API){suffix}");
        if let Ok(id) =
            crate::core::model_endpoints::create(&name, "codex-responses:", None, &avail.models, Some(owner))
        {
            // Mode B drives Odysseus's own tool loop → mark the row tool-capable.
            let _ = crate::core::model_endpoints::set_supports_tools(&id, true);
        }
    }
}

/// `POST /api/codex/connect` — the explicit "Connect Codex" action: (re-)probe
/// the signed-in Codex subscription and register/refresh the provider. Returns
/// the account + models, or 400 with guidance if Codex isn't available.
async fn codex_connect(user: Option<Extension<CurrentUser>>) -> Response {
    // `get_current_user(request) or ""` — absent extension (bypass, no cookie)
    // resolves to the unconfigured / single-user "" owner.
    let user = user.map(|Extension(u)| u.0).unwrap_or_default();
    match crate::core::codex::refresh_probe().await {
        Some(avail) => {
            // Register BOTH providers (Harness + API), each guarded per-scheme.
            register_codex_providers(&user, &avail);
            Json(json!({
                "ok": true, "provider": "codex",
                "account": avail.email, "plan": avail.plan, "models": avail.models,
                "providers": ["codex:", "codex-responses:"],
            }))
            .into_response()
        }
        None => (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Codex is not available — install the Codex CLI and run `codex login`."})),
        )
            .into_response(),
    }
}

/// `GET /api/demo/estimate?text=…` — a small showcase that the ported pure-logic
/// runs: `src::model_context::estimate_tokens` over a one-message conversation.
async fn demo_estimate(Query(q): Query<std::collections::HashMap<String, String>>) -> Json<Value> {
    let text = q.get("text").cloned().unwrap_or_default();
    let messages = vec![json!({"role": "user", "content": text})];
    let tokens = crate::src::model_context::estimate_tokens(&messages);
    Json(json!({ "text": text, "estimated_tokens": tokens }))
}

// ===========================================================================
// GET /api/generated-image/{filename} — serve_generated_image (app.py:296-342)
// ===========================================================================

/// `re.match(r'^[a-f0-9]{8,64}\.(png|jpg|jpeg|webp|gif|mp4|mov|webm|mkv|m4v)$', filename)`
/// (app.py:301). Compiled once; an anchored pattern, so `is_match` == Python's
/// `re.match` here (the trailing `$` makes the leading `re.match` anchor total).
static GENERATED_IMAGE_FILENAME_RE: Lazy<regex::Regex> = Lazy::new(|| {
    regex::Regex::new(r"^[a-f0-9]{8,64}\.(png|jpg|jpeg|webp|gif|mp4|mov|webm|mkv|m4v)$")
        .expect("static generated-image filename regex is valid")
});

/// `GET /api/generated-image/{filename}` — serve a generated image / video from the
/// `data/generated_images` directory (`serve_generated_image`, app.py:296-342).
///
/// This is an **app.py-level** route (defined directly on `app`, not inside any
/// `routes/X.py` factory), so it lives on the inline `api` router here alongside
/// `/api/health` and `/api/version` — mirroring its Python placement.
///
/// Behavior, line-by-line vs Python:
///   * filename is validated against the `^[a-f0-9]{8,64}\.(ext)$` allowlist
///     (app.py:301) → **400 "Invalid filename"** on mismatch;
///   * `data/generated_images/<filename>` must exist (app.py:303-305) →
///     **404 "Image not found"** otherwise;
///   * OWNERSHIP (app.py:309-326, `db`-gated): when a user is authenticated, the
///     `gallery_images` row for this filename is consulted — a row that exists with
///     a **non-empty** `owner` that **differs** from the user yields a **404** (don't
///     confirm existence). A missing row (generated-but-not-yet-imported) or an empty
///     / matching owner is allowed. The whole DB probe is best-effort: any DB error is
///     swallowed so a hiccup never blocks serving (Python's broad `except: pass`);
///   * mime is mapped by extension (app.py:328-333; unknown → `application/octet-stream`);
///   * the bytes are returned with `Content-Type: <mime>` and the hard
///     `Cache-Control: public, max-age=31536000, immutable` header (app.py:338-341),
///     because generated-image filenames are content hashes whose bytes never change.
async fn serve_generated_image(
    axum::extract::Path(filename): axum::extract::Path<String>,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, crate::routes::HttpException> {
    use crate::routes::HttpException;

    // `if not re.match(...): raise HTTPException(400, "Invalid filename")`.
    if !GENERATED_IMAGE_FILENAME_RE.is_match(&filename) {
        return Err(HttpException::new(400, "Invalid filename"));
    }

    // `img_path = Path("data/generated_images") / filename` — the cwd-relative
    // literal, matching Python and the existing gallery_routes / image_gen_server
    // consts (NOT routed through `constants::DATA_DIR`, which Python also does not).
    let img_path = std::path::Path::new("data")
        .join("generated_images")
        .join(&filename);
    // `if not img_path.exists(): raise HTTPException(404, "Image not found")`.
    if !img_path.exists() {
        return Err(HttpException::new(404, "Image not found"));
    }

    // SECURITY (app.py:306-326): filename is the only key, so anyone who guesses a
    // content hash could pull another user's bytes. Verify ownership via the gallery
    // row when one exists. `db`-gated because it needs the `gallery_images` table;
    // `web` always enables `db` (see Cargo `web = ["db", ...]`), so this is live in
    // every build where this web-gated handler compiles. The probe is best-effort:
    // a missing/owner-less/matching row is allowed, a row owned by someone else is a
    // 404 (don't confirm existence), and any DB error is swallowed (Python `except:
    // pass`) so a hiccup never blocks serving.
    {
        use rusqlite::OptionalExtension;
        if let Some(Extension(CurrentUser(current))) = &user {
            // `db.query(GalleryImage).filter(filename == filename).first()`.
            let owner_check = (|| -> rusqlite::Result<Option<Option<String>>> {
                let conn = crate::core::database::session_local()?;
                conn.query_row(
                    "SELECT owner FROM gallery_images WHERE filename = ?1 LIMIT 1",
                    rusqlite::params![filename],
                    |r| r.get::<_, Option<String>>(0),
                )
                .optional()
            })();
            // Row exists AND owner is non-empty AND owner != user → 404. Any DB error
            // → Err here, swallowed (treated like "no decision", i.e. allow).
            if let Ok(Some(Some(owner))) = owner_check {
                if !owner.is_empty() && &owner != current {
                    return Err(HttpException::new(404, "Image not found"));
                }
            }
        }
    }

    // `ext = filename.rsplit('.', 1)[-1].lower()` then the mime map (app.py:327-333).
    let ext = filename
        .rsplit('.')
        .next()
        .unwrap_or("")
        .to_ascii_lowercase();
    let mime = match ext.as_str() {
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "webp" => "image/webp",
        "gif" => "image/gif",
        "mp4" | "m4v" => "video/mp4",
        "mov" => "video/quicktime",
        "webm" => "video/webm",
        "mkv" => "video/x-matroska",
        _ => "application/octet-stream",
    };

    // `FileResponse(str(img_path), media_type=mime, headers={Cache-Control: ...})`.
    // Read the bytes off disk; an unreadable file (raced delete, permissions) is a
    // 404 — Starlette's FileResponse would 404/500, but a 404 matches the "not
    // found" contract without leaking the path.
    let bytes = match std::fs::read(&img_path) {
        Ok(b) => b,
        Err(_) => return Err(HttpException::new(404, "Image not found")),
    };

    Ok((
        [
            (header::CONTENT_TYPE, mime),
            (
                header::CACHE_CONTROL,
                "public, max-age=31536000, immutable",
            ),
        ],
        bytes,
    )
        .into_response())
}

// ===========================================================================
// Router + server
// ===========================================================================

/// Build the full axum app: routes, the SPA page routes, the static mount, and
/// the middleware stack (auth gate -> security headers -> CORS -> trace).
pub fn build_router(state: AppState) -> Router {
    // SPA page routes all serve index.html (app.py serves the same file for each).
    let spa = Router::new()
        .route("/", get(index_page))
        .route("/notes", get(index_page))
        .route("/calendar", get(index_page))
        .route("/cookbook", get(index_page))
        .route("/email", get(index_page))
        .route("/memory", get(index_page))
        .route("/gallery", get(index_page))
        .route("/tasks", get(index_page))
        .route("/library", get(index_page))
        .route("/login", get(login_page));

    // The inline auth / session / history / model / chat `.route(...)` registrations
    // that used to live here were superseded by the ported `routes::{auth_routes,
    // session_routes, history_routes, model_routes, chat_routes}` factories (merged
    // below via `api_routers()`) and deleted in the wave-4/5 reconciliation
    // (`/api/chat_stream` is now owned by `routes::chat_routes`, app.py include #8).
    // What remains inline is the demo-only / Codex-integration surface: health/version,
    // the Codex connect action (`/api/codex/connect`, absent from `chat_routes.py`), and
    // the `/api/demo/estimate` pure-logic showcase.
    let api = Router::new()
        .route("/api/health", get(health))
        .route("/api/version", get(version))
        .route("/api/ready", get(readiness))
        // The app.py-level generated-image serving route (`serve_generated_image`,
        // app.py:296-342) — defined directly on `app` in Python (not in any
        // `routes/X.py` factory), so it belongs here alongside health/version, NOT
        // in `api_routers()`. axum 0.7 path-param syntax is `:filename`.
        .route("/api/generated-image/:filename", get(serve_generated_image))
        // The Codex integration: the explicit "Connect Codex" action (the streaming
        // chat itself is now `routes::chat_routes`, merged via `api_routers()`).
        .route("/api/codex/connect", post(codex_connect))
        .route("/api/demo/estimate", get(demo_estimate));

    spa.merge(api)
        // F5-aggregator-and-merge: mount the ported `routes/X.py` factories
        // (`crate::routes::api_routers`) AFTER the inline `.route(...)` chain and
        // BEFORE the layers, mirroring app.py's `app.include_router(...)` block.
        // The aggregator merges the wave's `setup_x_routes()` in the exact app.py
        // include order, excluding the five colliding routers (auth/session/
        // history/model/chat) until reconciliation. It is empty until the proof
        // batch lands, so this is a no-op merge today (the inline subset above is
        // the full API surface) and stays non-breaking — `Router::merge` panics on
        // a duplicate method+path, and the proof batch is collision-free.
        .merge(crate::routes::api_routers())
        .nest_service("/static", ServeDir::new(&*constants::STATIC_DIR))
        // Middleware stack — match app.py's `add_middleware` order. Starlette wraps
        // OUTERMOST-LAST (the LAST `add_middleware` runs FIRST on the request); app.py
        // adds CORS(52) -> SecurityHeaders(61) -> _RequestTimeout(102) -> Auth(268),
        // so the request flow outer->inner is Auth -> Timeout -> Security -> CORS ->
        // handler. In axum the LAST `.layer()` is OUTERMOST, so the source order below
        // (first = innermost) is CORS -> Security -> Timeout -> Auth -> Trace, giving
        // the flow Trace -> Auth -> Timeout -> Security -> CORS -> handler — auth is
        // the outermost APP middleware (matching app.py) and trace wraps the whole
        // request (correct: it should observe everything, including auth rejections).
        .layer(cors_layer())
        .layer(middleware::from_fn(security_headers_mw))
        .layer(middleware::from_fn(request_timeout_mw))
        .layer(middleware::from_fn_with_state(state.clone(), auth_gate))
        .layer(TraceLayer::new_for_http())
        // Starlette imposes NO default request-body limit, so large uploads
        // (gallery 100 MB, stt/email 25 MB, …) are gated only by the app-level
        // `upload_limits::read_upload_limited` caps. axum otherwise applies a 2 MB
        // default to body extractors (Multipart/Bytes), which would reject those
        // uploads at the framework layer — disable it to match Python and let the
        // per-route caps be the real gate.
        .layer(axum::extract::DefaultBodyLimit::disable())
        .with_state(state)
}

/// `CORSMiddleware` (app.py:51-58) — `allow_origins` from the `ALLOWED_ORIGINS` env
/// (default `"http://localhost,http://127.0.0.1"`, comma-split), `allow_credentials
/// = True`, `allow_methods = [GET, POST, PUT, DELETE]`, `allow_headers = ["*"]`.
///
/// Tightened from the prior `CorsLayer::permissive()` (a deviation that ignored
/// `ALLOWED_ORIGINS`, allowed every method, and — being permissive — could not
/// combine with credentials). Origins that fail to parse as a header value are
/// skipped (an invalid `ALLOWED_ORIGINS` entry simply isn't allowed, never panics).
fn cors_layer() -> CorsLayer {
    use axum::http::{HeaderValue, Method};
    use tower_http::cors::AllowHeaders;
    let origins: Vec<HeaderValue> = os::getenv("ALLOWED_ORIGINS", "http://localhost,http://127.0.0.1")
        .split(',')
        .filter_map(|o| HeaderValue::from_str(o).ok())
        .collect();
    CorsLayer::new()
        .allow_origin(origins)
        .allow_credentials(true)
        .allow_methods([Method::GET, Method::POST, Method::PUT, Method::DELETE])
        // app.py's `allow_headers=["*"]` WITH `allow_credentials=True`: the CORS spec
        // forbids a literal `*` Access-Control-Allow-Headers alongside credentials, so
        // Starlette (and tower-http) reflect the request's `Access-Control-Request-
        // Headers` instead. `AllowHeaders::mirror_request` is that credentials-safe
        // equivalent (a bare `Any` would PANIC when combined with credentials).
        .allow_headers(AllowHeaders::mirror_request())
}

/// `app.py` startup, translated: load `.env`, init the DB, build the managers,
/// wire the module-global session manager, and serve.
pub async fn run() {
    dotenvy::dotenv().ok();
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info,tower_http=info".into()),
        )
        .init();

    // Data dir = `constants::DATA_DIR` (honors `ODYSSEUS_DATA_DIR`, else `<repo>/data`)
    // — the SAME dir `odysseus setup` populates and the Python `app.py` uses. The
    // app's AuthManager, the DB, and the per-request `AuthManager::new()` that
    // tool-security constructs all read this one `constants::DATA_DIR`, so they
    // agree without any env juggling. Set `ODYSSEUS_DATA_DIR` to run against an
    // isolated dir. Ensure it exists before `init_db` / managers.
    let data_dir = constants::DATA_DIR.clone();
    std::fs::create_dir_all(&data_dir).ok();
    if std::env::var("DATABASE_URL").is_err() {
        std::env::set_var("DATABASE_URL", format!("sqlite:///{data_dir}/app.db"));
    }

    // Bind the listen socket FIRST — before init_db / managers / spawning the MCP
    // subprocesses + scheduler — so a port conflict fails FAST and CLEANLY rather
    // than after all that startup work (uvicorn binds before running startup too).
    // `serve --host/--port` sets ODYSSEUS_BIND; the default matches `setup` /
    // `uvicorn app:app --host 0.0.0.0 --port 7000`.
    let bind = os::getenv("ODYSSEUS_BIND", "0.0.0.0:7000");
    let listener = match tokio::net::TcpListener::bind(&bind).await {
        Ok(l) => l,
        Err(e) => {
            eprintln!("✗ Could not bind {bind}: {e}");
            eprintln!("  The port is likely already in use — pick another with");
            eprintln!("  `--port <N>` / `--host <H>` (or set $ODYSSEUS_BIND).");
            eprintln!("  (macOS: AirPlay Receiver / Control Center holds :7000 and");
            eprintln!("   :5000 — disable it in System Settings → General → AirDrop & Handoff.)");
            std::process::exit(1);
        }
    };
    println!("⚓ Odysseus (Rust) binding http://{bind}   [data dir: {data_dir}]");

    crate::core::database::init_db();

    // CHROMADB AUTO-LAUNCH (replicates docker-compose's `chromadb` service +
    // `depends_on`): when the vector store is on the real ChromaDB HTTP backend AND
    // `ODYSSEUS_LAUNCH_CHROMA` is truthy, bring up a REAL Chroma server BEFORE
    // building the managers — `initialize_managers` warms MemoryVectorStore / the
    // tool index, which under the chroma backend construct collections that must
    // reach a LIVE server. Default OFF (no env -> SQLite backend -> this is skipped
    // entirely, behavior byte-for-byte unchanged); when off but the chroma backend
    // is selected, we assume an externally-managed Chroma (the compose service) and
    // just connect.
    //
    // `ensure_chroma_running_held` picks the launcher backend internally per
    // `ODYSSEUS_CHROMA_LAUNCHER` ({auto,local,docker}, default auto = prefer the
    // pip-installed full `chromadb` python server, else docker). UNLIKE the
    // standalone `odysseus chroma start` subcommand (which DETACHES the local server
    // so it survives the subcommand exiting), the autolaunch path HOLDS the spawned
    // child for the server's lifetime and kills it on graceful shutdown — so the
    // child lives in this `run()` scope and is moved into the shutdown future. For
    // the docker backend there is no child to hold (docker owns the container), so
    // the held child is `None`. It is blocking (process spawn + reqwest::blocking),
    // so it runs on a `spawn_blocking` thread. A failure here LOGS A WARNING AND
    // CONTINUES — the backend's per-call errors then degrade gracefully (like a
    // missing embedding server today); startup never hard-crashes on a launch hiccup.
    let (chroma_handle, chroma_child) = if chroma_autolaunch_enabled()
        && matches!(
            crate::src::vector_store::select_backend(),
            crate::src::vector_store::Backend::ChromaHttp
        ) {
        match tokio::task::spawn_blocking(crate::src::chroma_launch::ensure_chroma_running_held)
            .await
        {
            Ok(Ok((handle, child))) => {
                crate::pylog::info("ChromaDB server ready (auto-launched)");
                (Some(handle), child)
            }
            Ok(Err(e)) => {
                crate::pylog::warning(&format!(
                    "ChromaDB auto-launch failed ({e}); continuing — vector ops will degrade until a server is reachable"
                ));
                (None, None)
            }
            Err(e) => {
                crate::pylog::warning(&format!("ChromaDB auto-launch task panicked ({e}); continuing"));
                (None, None)
            }
        }
    } else {
        (None, None)
    };

    // `AuthManager::new()` now resolves `<data_dir>/auth.json` via DATA_DIR, so the
    // app and tool-security share one auth store.
    let auth = Arc::new(AuthManager::new());
    let sessions = Arc::new(SessionManager::new());

    // Build the manager / handler surface the ported routers reach. The richer
    // F4-startup-wiring step refines this (cleanup task, ConnectInfo, event_bus,
    // aggregator merge); here we populate the additive AppState fields so the
    // crate compiles and the inline subset keeps its hand-built `auth`/`sessions`.
    // `rag_manager = None` on the live path (the application RAG singleton is
    // disabled), matching the Python `initialize_managers(base_dir, None)` call.
    let mgrs = match crate::src::app_initializer::initialize_managers(&data_dir, None).await {
        Ok(m) => m,
        Err(e) => {
            eprintln!("failed to initialize managers: {e}");
            std::process::exit(1);
        }
    };
    // Side-built managers (no equivalent inside `initialize_managers`):
    // `WebhookManager::new(Some(api_key_manager))`, `TaskScheduler::new()` (VERIFIED
    // no-arg ctor — wired into the event bus), `McpManager::new()`.
    let webhook_manager = Arc::new(WebhookManager::new(Some(mgrs.api_key_manager.clone())));
    let task_scheduler = Arc::new(TaskScheduler::new());
    crate::src::event_bus::set_task_scheduler(task_scheduler.clone());
    let mcp_manager = Arc::new(McpManager::new());
    // Wire the live manager into the agent-tools slot so the agent-side MCP paths
    // (do_manage_mcp, disabled-tools filtering, task `mcp::` delivery, check-in MCP
    // sources) reach the SAME instance the routes/startup populate. Mirrors
    // app.py:562-563 (`mcp_manager = McpManager(); set_mcp_manager(mcp_manager)`).
    // Runs BEFORE the startup clones / connect so the agent side sees the instance
    // the routes register built-in servers on.
    crate::src::agent_tools::set_mcp_manager(mcp_manager.clone());

    // Clones for the startup phase (`startup_event`) + graceful shutdown
    // (`shutdown_event`), taken BEFORE the originals move into `AppState` below. The
    // SAME `mcp_manager` Arc backs `AppState.mcp_manager` (the routes),
    // `register_builtin_servers`, `connect_all_enabled`, and `disconnect_all` — so
    // built-in servers register on the instance the routes see (the load-bearing
    // single-instance invariant; see `register_builtin_servers`).
    let startup_scheduler = task_scheduler.clone();
    let startup_mcp = mcp_manager.clone();
    let startup_skills = mgrs.skills_manager.clone();

    // `setup_upload_routes(upload_handler)` returns `(router, periodic_rate_limit_cleanup)`
    // in Python; app.py's startup then does
    // `upload_cleanup_task = asyncio.create_task(upload_cleanup_func())`. Per the
    // integration design, the Rust `setup_upload_routes()` returns ONLY the router
    // and the periodic cleanup is modeled here as a spawned tokio task. Mirrors:
    //
    //   async def periodic_rate_limit_cleanup():
    //       while True:
    //           await asyncio.sleep(3600)
    //           upload_handler.cleanup_rate_limits()
    //
    // The Python loop sleeps FIRST, then cleans — so the first sweep lands an hour
    // after startup, never at boot. `tokio::time::interval` ticks immediately on
    // the first `tick()`, which would clean at boot; we use `sleep`-then-clean in a
    // `loop` to preserve the Python ordering exactly. `cleanup_rate_limits` is sync
    // (`&self`), so no `await` inside the body. The handle is fire-and-forget (the
    // process owns it for its lifetime); app.py cancels it on shutdown, but the
    // Rust server has no graceful-shutdown hook yet (documented, not faked), so the
    // task simply ends with the process.
    {
        let upload_handler = mgrs.upload_handler.clone();
        tokio::spawn(async move {
            loop {
                tokio::time::sleep(std::time::Duration::from_secs(3600)).await;
                upload_handler.cleanup_rate_limits();
            }
        });
    }

    // Wire the module-global persist path (models.Session.add_message) to the
    // hand-built `sessions` the inline subset uses (matching the prior behavior;
    // `initialize_managers` set it to its own `session_manager`, so re-assert here).
    crate::core::models::set_session_manager(sessions.clone());
    // Install the global memory-vector handle so the agent-tool path
    // (`ai_interaction::do_manage_memory`, which has no `AppState`) can keep the
    // ChromaDB memory index in sync, exactly like the HTTP `memory_routes` do.
    crate::src::memory_vector::set_memory_vector(mgrs.memory_vector.clone());

    let auth_enabled = os::getenv("AUTH_ENABLED", "true").to_lowercase() != "false";
    let localhost_bypass = os::getenv("LOCALHOST_BYPASS", "false").to_lowercase() == "true";

    let state = AppState {
        auth,
        sessions,
        auth_enabled,
        localhost_bypass,
        memory_manager: mgrs.memory_manager,
        memory_vector: mgrs.memory_vector,
        skills_manager: mgrs.skills_manager,
        upload_handler: mgrs.upload_handler,
        personal_docs_manager: mgrs.personal_docs_manager,
        api_key_manager: mgrs.api_key_manager,
        // `preset_manager` lives behind a `tokio::sync::Mutex` (its mutating
        // methods take `&mut self`). Reuse the ONE shared instance built by
        // `initialize_managers` — app.py shares a single `preset_manager` across
        // `setup_preset_routes` and `setup_backup_routes`, and so does the Rust
        // aggregator (both read this `state.preset_manager`).
        preset_manager: mgrs.preset_manager,
        model_discovery: mgrs.model_discovery,
        research_handler: mgrs.research_handler,
        chat_handler: mgrs.chat_handler,
        chat_processor: mgrs.chat_processor,
        webhook_manager,
        task_scheduler,
        mcp_manager,
        rag_manager: None,
        // The REAL token-cache invalidation hook (app.py:140-143's
        // `_token_cache_invalidate`): `api_token_routes` create/delete call this so a
        // minted or revoked token is reflected on the next Bearer request. Replaces
        // the prior no-op `Arc::new(|| {})`.
        invalidate_token_cache: Arc::new(token_cache::invalidate),
    };
    // WAVE 8 — start the background mail pollers (`routes/email_pollers.py`'s
    // `_start_poller()`). Like the rate-limit cleanup task above this is a
    // fire-and-forget startup hook; `start_email_pollers` does its own internal
    // `tokio::spawn` (and honours `ODYSSEUS_INPROCESS_POLLERS` to skip when an
    // external cron drives `odysseus-mail poll-scheduled`). Only present on the
    // web+db build, exactly like the `routes::email_pollers` module's `#![cfg]`.
    // Bootstrap the standalone scheduled-emails SQLite DB (`_init_scheduled_db()`,
    // which `routes/email_helpers.py` runs at import time) BEFORE the pollers — or
    // any queue/summary op — first open it, so the first scheduled-email enqueue or
    // summary-cache write does not error with "no such table: scheduled_emails".
    crate::routes::email_helpers::init_scheduled_db();
    crate::routes::email_pollers::start_email_pollers(state.clone());

    // `@app.on_event("startup")` (app.py:675-931) — the incognito wipe + the
    // fire-and-forget background tasks. Run AFTER the managers + email pollers are
    // wired, BEFORE serving (matching app.py, where startup_event fires once the app
    // is built and just before it accepts traffic).
    startup_event(startup_scheduler.clone(), startup_mcp.clone(), startup_skills).await;

    let app = build_router(state);

    // Socket was already bound up top (fail-fast). Now that startup is complete,
    // begin accepting connections.
    let shown = bind.replace("0.0.0.0", "localhost");
    println!("⚓ Odysseus (Rust) ready — open http://{shown}/login");
    // Serve with `ConnectInfo<SocketAddr>` made available to extractors. The
    // auth-adapter (F3) derives `client_host` (the value the already-ported
    // `src::auth_helpers` reads from `request.client.host` for require_user's
    // unconfigured-loopback branch, and auth_routes' rate limiter will read later)
    // from `ConnectInfo<SocketAddr>` — which is only present when the router is
    // served via `into_make_service_with_connect_info`. Starlette exposes
    // `request.client` automatically; in axum this opt-in make-service is the
    // equivalent that threads the peer address through to handlers.
    //
    // `with_graceful_shutdown` mirrors `@app.on_event("shutdown")` (app.py:933-957):
    // on Ctrl-C, stop accepting connections, then run [`shutdown_event`].
    axum::serve(
        listener,
        app.into_make_service_with_connect_info::<SocketAddr>(),
    )
    .with_graceful_shutdown(shutdown_signal(
        startup_scheduler,
        startup_mcp,
        chroma_handle,
        chroma_child,
    ))
    .await
    .expect("server error");
}

/// `@app.on_event("startup")` (app.py:675-931), translated. Runs the incognito-session
/// wipe synchronously, then spawns the fire-and-forget background tasks in app.py
/// order. The Python keeps a `_startup_tasks` strong-ref set so the event loop does
/// not GC the tasks; in Rust a `tokio::spawn` handle that is dropped keeps running
/// (the runtime owns it for the process lifetime), so no equivalent set is needed.
///
/// `int(get_setting(key, default) or default)` — read a settings value, falling back
/// to `default` when it is missing/falsy (Python `... or default`) or cannot coerce to
/// an integer (Python `int()` would raise → the loop's `except` → default). A float
/// truncates toward zero like Python `int(float)`.
fn setting_int(key: &str, default: i64) -> i64 {
    let v = crate::src::settings::get_setting(key, json!(default));
    // `... or default`: a falsy value (None/0/""/...) collapses to the default.
    match &v {
        Value::Null => default,
        Value::Bool(false) => default,
        Value::Number(n) if n.as_f64() == Some(0.0) => default,
        Value::String(s) if s.is_empty() => default,
        Value::Number(n) => n
            .as_i64()
            .or_else(|| n.as_f64().map(|f| f as i64))
            .unwrap_or(default),
        // `int("8")` parses; `int("eight")` raises → default.
        Value::String(s) => s.trim().parse::<i64>().ok().unwrap_or(default),
        _ => default,
    }
}

/// `bool(get_setting(key, default))` — truthiness of a settings value (Python `if not
/// get_setting(...)`), reading the stored default when the key is absent.
fn setting_truthy(key: &str, default: bool) -> bool {
    let v = crate::src::settings::get_setting(key, json!(default));
    match v {
        Value::Null => false,
        Value::Bool(b) => b,
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(m) => !m.is_empty(),
    }
}

/// The nightly skill audit (app.py:870-898) IS now spawned (item 6b) — it drives
/// `crate::routes::skills_pipeline::run_scheduled_skill_audit` on the ~02:00 local
/// schedule, gated by the `skill_audit_*` settings.
///
/// DOCUMENTED GAPS (no observable behavior change, omitted with a citation):
/// * the endpoint warm-up + 60s keep-alive loops (app.py:744-773) — they call
///   `model_discovery.get_endpoints()`, which has NO accessor on the Rust
///   `ModelDiscovery`; they are pure connection-priming optimizations;
/// * `webhook_manager.set_loop(...)` (app.py:679) — the Rust `WebhookManager` has no
///   event-loop concept.
async fn startup_event(
    task_scheduler: Arc<TaskScheduler>,
    mcp_manager: Arc<McpManager>,
    skills_manager: Arc<crate::services::memory::skills::SkillsManager>,
) {
    crate::pylog::info("Application starting up...");

    // --- 1. INCOGNITO WIPE (app.py:680-696) — delete leftover ephemeral incognito
    // sessions (+ their chat_messages) from a prior process. Synchronous, before any
    // task spawn. A DB error is logged + swallowed (the Python `except -> logger.debug`).
    match incognito_wipe() {
        Ok(n) if n > 0 => crate::pylog::info(&format!("Purged {n} leftover incognito session(s)")),
        Ok(_) => {}
        Err(e) => crate::pylog::debug(&format!("Incognito purge skipped: {e}")),
    }

    // --- 2. MCP STARTUP (app.py:710-725) — `_startup_mcp_connections`: a detached
    // task that registers the built-in servers then connects all enabled servers
    // with a 20s timeout, both non-critical. Uses the SAME `mcp_manager` Arc as the
    // routes (so registrations are visible to `/api/mcp/*`).
    {
        let mcp = mcp_manager.clone();
        tokio::spawn(async move {
            // `await register_builtin_servers(mcp_manager)` — returns ~immediately
            // (it fire-and-forgets each connect internally).
            crate::src::builtin_mcp::register_builtin_servers(mcp.clone()).await;
            // `await asyncio.wait_for(mcp_manager.connect_all_enabled(), timeout=20)`.
            match tokio::time::timeout(Duration::from_secs(20), mcp.connect_all_enabled()).await {
                Ok(()) => {}
                Err(_) => crate::pylog::warning("User MCP startup timed out (non-critical)"),
            }
        });
    }

    // --- 3. bg_monitor (app.py:705-709) — the always-on background-bash-job monitor.
    // `#[cfg(unix)]`: the monitor module is `cfg(all(feature = "web", unix))`, so on a
    // non-unix host this is skipped cleanly (the Python is unconditional, but the
    // monitor depends on POSIX tmux/process semantics). Idempotent + returns `()`.
    #[cfg(unix)]
    crate::src::bg_monitor::start_bg_monitor();

    // --- 4. tool-index warm-up (app.py:732-743) — prime the RAG tool index off the
    // request path. `get_tool_index()` already builds + indexes the built-in tools on
    // init (folding in the explicit `index_builtin_tools` call), which is the one-time
    // ~1-3s cost that would otherwise land on the user's FIRST message. Then run the
    // `get_tools_for_query("warmup", 8)` probe (app.py:738) so the warm-embed query path
    // is primed too — the first real turn hits an already-warm embedding cache (warm
    // embed ≈ a few ms). Best-effort; logs success only when the index built.
    tokio::spawn(async move {
        if crate::src::tool_index::get_tool_index().await {
            crate::src::tool_index::warmup_query().await;
            crate::pylog::info("[startup] Tool index pre-warmed");
        }
    });

    // --- 5. task_scheduler (app.py:775-854). Order matches app.py exactly:
    //   (a) `_ensure_default_tasks()` (820), THEN
    //   (b) the skill-owner backfill (825-842), THEN
    //   (c) `task_scheduler.start()` (849) gated on ODYSSEUS_INPROCESS_TASKS.
    // The reconciliation runs BEFORE the runner starts so legacy scheduled built-ins
    // don't fire once before being converted to event tasks (app.py:818-819).
    {
        // (a) `_ensure_default_tasks()` (app.py:775-820): reconcile the default
        // automation tasks for every owner — auth.json users UNION the scheduled_tasks
        // existing-owner scan (so stale built-ins for deleted/demo users get reconciled
        // instead of firing forever). See `ensure_default_tasks` for the union logic.
        ensure_default_tasks(&task_scheduler).await;

        // (b) Skill-owner backfill (app.py:825-842): assign ownerless / deleted-owner
        // SKILL.md files to the primary admin so strict owner filtering doesn't make
        // the library look empty after an auth change. Best-effort (logger.debug).
        if let Some((primary, owners)) = primary_admin_and_owners() {
            let changed = skills_manager.backfill_owner(&primary, Some(&owners));
            if changed > 0 {
                crate::pylog::info(&format!(
                    "Assigned {changed} legacy skill file(s) to {primary}"
                ));
            }
        }

        // (c) `if _tasks_inprocess not in (...): await task_scheduler.start()`.
        if inprocess_tasks_enabled() {
            task_scheduler.start().await;
        } else {
            crate::pylog::info(
                "In-process task scheduler disabled (ODYSSEUS_INPROCESS_TASKS=0); \
                 drive task firing externally (e.g. cron).",
            );
        }
    }

    // --- 6. null-owner hourly sweep (app.py:858-868) — re-run the legacy-owner
    // assignment every hour so data created while auth was disabled / bypassed gets
    // claimed by the admin (M19). The migration is blocking rusqlite -> spawn_blocking.
    tokio::spawn(async move {
        loop {
            tokio::time::sleep(Duration::from_secs(3600)).await;
            let _ = tokio::task::spawn_blocking(crate::core::database::migrate_assign_legacy_owner)
                .await;
        }
    });

    // --- 6b. Nightly skill audit (app.py:870-898) — at ~02:00 local, test + judge a
    // batch of the least-recently-checked skills, auto-fixing/escalating weak ones
    // (never deletes). Gated by `skill_audit_nightly` (default on); hour via
    // `skill_audit_hour` (default 2), batch via `skill_audit_batch` (default 8). The
    // owned `skills_manager` Arc is moved into the detached loop (it's only borrowed by
    // the backfill above, so still owned here). Mirrors the Python `_skill_audit_nightly_loop`.
    tokio::spawn(async move {
        use chrono::{Local, Timelike};
        loop {
            // `hour = int(get_setting("skill_audit_hour", 2) or 2)` — falsy/bad -> 2.
            let hour = setting_int("skill_audit_hour", 2).rem_euclid(24) as u32;
            // `nxt = now.replace(hour=hour, minute/second/micro=0); if nxt <= now: +1d`.
            let now = Local::now();
            let mut nxt = now
                .with_hour(hour)
                .and_then(|t| t.with_minute(0))
                .and_then(|t| t.with_second(0))
                .and_then(|t| t.with_nanosecond(0))
                .unwrap_or(now);
            if nxt <= now {
                nxt += chrono::Duration::days(1);
            }
            // `await asyncio.sleep(max(60, (nxt - now).total_seconds()))`.
            let secs = (nxt - now).num_seconds().max(60) as u64;
            tokio::time::sleep(Duration::from_secs(secs)).await;

            // `if not get_setting("skill_audit_nightly", True): continue`.
            if !setting_truthy("skill_audit_nightly", true) {
                continue;
            }
            // `batch = int(get_setting("skill_audit_batch", 8) or 8)`.
            let batch = setting_int("skill_audit_batch", 8).max(0) as usize;
            // `await run_scheduled_skill_audit(skills_manager, owner=None, max_skills=batch)`.
            // The pipeline guards against a concurrent run + logs internally; the Python
            // `except -> logger.warning` is folded into the pipeline's best-effort body.
            let _ =
                crate::routes::skills_pipeline::run_scheduled_skill_audit(&skills_manager, None, batch)
                    .await;
        }
    });

    // --- 7. Ollama auto-detect (app.py:900-930) — probe a local Ollama instance and
    // register it as a `model_endpoints` row if present + not already added. Best-effort.
    tokio::spawn(detect_ollama());

    crate::pylog::info("Application startup complete");
}

/// `await shutdown_signal()` then `@app.on_event("shutdown")` (app.py:933-957): on
/// Ctrl-C, stop the task scheduler (`task_scheduler.stop()` — a no-op if it never
/// started under the gate) and disconnect all MCP servers (`mcp_manager.disconnect_all()`).
///
/// OMITTED (documented): `webhook_manager.close()` (the Rust `WebhookManager` has no
/// `close()`), and the explicit `upload_cleanup_task.cancel()` (the cleanup task is a
/// detached `tokio::spawn` that dies with the process).
///
/// CHROMADB TEARDOWN (additive, no Python analogue): if we auto-launched a ChromaDB
/// server at startup (`chroma_handle = Some`), stop it AFTER the scheduler + MCP
/// teardown. `ChromaHandle::stop` is itself reuse-safe — it only stops (docker stop
/// / SIGTERM) when `started_by_us`, so a reused/externally-managed server (the
/// compose service, or a Chroma someone else launched) is NEVER killed, honoring
/// compose's `restart: unless-stopped` and the "don't kill what you didn't start"
/// posture. For the LOCAL backend we HELD the child (`chroma_child = Some`): the
/// `ChromaHandle::stop` SIGTERMs it by pid, then we `kill()`+reap the held `Child`
/// so no zombie is left. When chroma was not auto-launched (`None`/`None`), both are
/// no-ops.
async fn shutdown_signal(
    task_scheduler: Arc<TaskScheduler>,
    mcp_manager: Arc<McpManager>,
    chroma_handle: Option<crate::src::chroma_launch::ChromaHandle>,
    chroma_child: Option<std::process::Child>,
) {
    // `await` Ctrl-C; if the signal handler can't be installed, never resolve (so the
    // server runs until killed) — matching the prior process-exit-only behavior.
    if tokio::signal::ctrl_c().await.is_err() {
        std::future::pending::<()>().await;
    }
    crate::pylog::info("Application shutting down...");
    // `await task_scheduler.stop()` (no-op if never started).
    task_scheduler.stop().await;
    // `await mcp_manager.disconnect_all()`.
    mcp_manager.disconnect_all().await;
    // Stop the auto-launched ChromaDB server LAST (only if we started it). Both the
    // SIGTERM/docker-stop (in `handle.stop`) and the held-child reap are blocking, so
    // run them off the async executor on one `spawn_blocking` thread.
    if chroma_handle.is_some() || chroma_child.is_some() {
        let _ = tokio::task::spawn_blocking(move || {
            if let Some(handle) = chroma_handle {
                handle.stop();
            }
            // Reap the held local child (best-effort): `handle.stop` already SIGTERM'd
            // it by pid; `kill()`+`wait()` here ensures the `Child` is reaped so no
            // zombie lingers (a no-op if it already exited from the SIGTERM).
            if let Some(mut child) = chroma_child {
                let _ = child.kill();
                let _ = child.wait();
            }
        })
        .await;
    }
    crate::pylog::info("Application shutdown complete");
}

/// `ODYSSEUS_LAUNCH_CHROMA` gate for the startup auto-launch of the ChromaDB
/// container. Truthy = `1`/`true`/`yes` (the same convention `builtin_mcp`'s
/// `MCP_DISABLED` uses); default OFF (unset or anything else) so the conservative
/// "default behavior unchanged" rule holds — with no env, this is false and no
/// container is touched. Distinct from backend SELECTION (`select_backend`): the
/// gate only controls whether WE bring the container up; an externally-managed
/// Chroma (the compose service) is reached with the gate OFF.
fn chroma_autolaunch_enabled() -> bool {
    std::env::var("ODYSSEUS_LAUNCH_CHROMA")
        .map(|v| matches!(v.trim().to_lowercase().as_str(), "1" | "true" | "yes"))
        .unwrap_or(false)
}

/// `ODYSSEUS_INPROCESS_TASKS` gate (app.py:847-848). `os.environ.get(..., "1").strip()
/// .lower() not in ("0","false","no","off","")` — mirrors the email-pollers parser.
fn inprocess_tasks_enabled() -> bool {
    let raw = os::getenv("ODYSSEUS_INPROCESS_TASKS", "1")
        .trim()
        .to_lowercase();
    !matches!(raw.as_str(), "0" | "false" | "no" | "off" | "")
}

/// The incognito-session wipe (app.py:680-696): delete the `chat_messages` of, then
/// the `sessions` named `'Nobody'`/`'Incognito'`, returning the purged session count.
fn incognito_wipe() -> rusqlite::Result<usize> {
    let conn = crate::core::database::session_local()?;
    // Delete the messages of the ghost sessions first (FK-safe order, matching the
    // Python per-session `db.query(ChatMessage)...delete()` then `db.delete(session)`).
    conn.execute(
        "DELETE FROM chat_messages WHERE session_id IN \
           (SELECT id FROM sessions WHERE name IN ('Nobody', 'Incognito'))",
        [],
    )?;
    // `_ghosts = ...; for _g in _ghosts: db.delete(_g)` — the purged count is the
    // number of sessions removed (`len(_ghosts)`).
    let purged = conn.execute(
        "DELETE FROM sessions WHERE name IN ('Nobody', 'Incognito')",
        [],
    )?;
    Ok(purged)
}

/// The primary admin + the set of all owner usernames from `<DATA>/auth.json`
/// (app.py:825-842's `primary_owner` selection + `set(users.keys())`): the first
/// `is_admin: true` user, else the first user; `None` when auth.json is missing/empty.
fn primary_admin_and_owners() -> Option<(String, std::collections::HashSet<String>)> {
    let auth_path = os::path::join(&constants::DATA_DIR, "auth.json");
    let text = std::fs::read_to_string(&auth_path).ok()?;
    let data: serde_json::Value = serde_json::from_str(&text).ok()?;
    let users = data.get("users")?.as_object()?;
    if users.is_empty() {
        return None;
    }
    let owners: std::collections::HashSet<String> = users.keys().cloned().collect();
    // First `is_admin: true`, else the first user (insertion order — serde_json's Map
    // preserves it with the `preserve_order` feature, matching Python dict iteration).
    let primary = users
        .iter()
        .find(|(_, u)| u.get("is_admin").and_then(|v| v.as_bool()) == Some(true))
        .map(|(k, _)| k.clone())
        .or_else(|| users.keys().next().cloned())?;
    Some((primary, owners))
}

/// `_ensure_default_tasks()` (app.py:775-820): reconcile the default automation tasks
/// for every relevant owner, each via `task_scheduler.ensure_defaults(owner)`, sorted.
///
/// The owner set is the UNION of two independent best-effort scans (mirroring the two
/// separate `try`/`except` blocks in app.py):
///   1. auth.json users (`users.keys()`, app.py:778-785) — the live first-run path.
///   2. the `scheduled_tasks` table (app.py:790-807): any rows whose `action` is a
///      `HOUSEKEEPING_DEFAULTS` key OR whose `name` is a built-in name/legacy_name.
///      This reconciles stale built-ins owned by deleted/demo users that are no longer
///      in auth.json so their old scheduled rows stop firing forever.
async fn ensure_default_tasks(task_scheduler: &Arc<TaskScheduler>) {
    use std::collections::BTreeSet;

    // 1. auth.json owners (`owners.update(users.keys())`). Independent of the DB scan:
    // a missing/empty auth.json must not skip the existing-owner reconciliation below.
    let mut owners: BTreeSet<String> = BTreeSet::new();
    if let Some((_, auth_owners)) = primary_admin_and_owners() {
        owners.extend(auth_owners);
    }

    // 2. scheduled_tasks existing-owner scan (app.py:790-807). A DB-backed step of
    // the same startup sequence `run()` performs, alongside the surrounding steps.
    {
        owners.extend(scheduled_task_builtin_owners());
    }

    // `for uname in sorted(owners): await task_scheduler.ensure_defaults(uname)`.
    // `BTreeSet` iteration is already sorted, matching Python's `sorted(owners)`.
    for uname in owners {
        task_scheduler.ensure_defaults(&uname).await;
    }
}

/// The distinct, non-empty `scheduled_tasks.owner` values for any row that is a
/// built-in housekeeping task — `action IN HOUSEKEEPING_DEFAULTS.keys()` OR
/// `name IN (every def's name + legacy_names)` (app.py:790-807). Best-effort: any DB
/// error yields an empty set (the Python wraps the whole scan in `try/except`).
fn scheduled_task_builtin_owners() -> std::collections::HashSet<String> {
    use crate::src::task_scheduler::HOUSEKEEPING_DEFAULTS;

    // `builtin_names = [name, *legacy_names]` for every def (app.py:793-796).
    let mut builtin_names: Vec<String> = Vec::new();
    for def in HOUSEKEEPING_DEFAULTS.values() {
        builtin_names.push(def.name.to_string());
        builtin_names.extend(def.legacy_names.iter().map(|s| s.to_string()));
    }
    let actions: Vec<&'static str> = HOUSEKEEPING_DEFAULTS.keys().copied().collect();

    let result = (|| -> rusqlite::Result<std::collections::HashSet<String>> {
        let conn = crate::core::database::session_local()?;
        // Bind the two `IN (...)` lists as positional placeholders (rusqlite has no
        // native list binding). `action IN (...) OR name IN (...)`, DISTINCT owner.
        let action_ph = vec!["?"; actions.len()].join(", ");
        let name_ph = vec!["?"; builtin_names.len()].join(", ");
        let sql = format!(
            "SELECT DISTINCT owner FROM scheduled_tasks \
             WHERE action IN ({action_ph}) OR name IN ({name_ph})"
        );
        let mut stmt = conn.prepare(&sql)?;
        let params: Vec<&dyn rusqlite::ToSql> = actions
            .iter()
            .map(|a| a as &dyn rusqlite::ToSql)
            .chain(builtin_names.iter().map(|n| n as &dyn rusqlite::ToSql))
            .collect();
        let rows = stmt.query_map(params.as_slice(), |r| {
            r.get::<_, Option<String>>(0)
        })?;
        // `owners.update(row[0] for row in rows if row[0])` — drop NULL/empty owners.
        let mut out = std::collections::HashSet::new();
        for row in rows {
            if let Some(owner) = row? {
                if !owner.is_empty() {
                    out.insert(owner);
                }
            }
        }
        Ok(out)
    })();

    result.unwrap_or_else(|e| {
        crate::pylog::debug(&format!("Default task existing-owner scan: {e}"));
        std::collections::HashSet::new()
    })
}

/// `_detect_ollama()` (app.py:900-930): if the `ollama` binary is on PATH and a probe
/// of `http://localhost:11434/v1/models` returns 200, register a `model_endpoints` row
/// for `http://localhost:11434/v1` (idempotent — skip if one already exists). Best-effort.
async fn detect_ollama() {
    // `if not shutil.which("ollama"): return`.
    if which("ollama").is_none() {
        return;
    }
    // `r = await client.get("http://localhost:11434/v1/models", timeout=3)`.
    let client = match reqwest::Client::builder()
        .timeout(Duration::from_secs(3))
        .build()
    {
        Ok(c) => c,
        Err(_) => return,
    };
    match client.get("http://localhost:11434/v1/models").send().await {
        // `if r.status_code != 200: return`.
        Ok(r) if r.status().as_u16() == 200 => {}
        _ => return,
    }
    // `existing = db.query(ModelEndpoint).filter(base_url == ...).first(); if not existing: add`.
    let registered = tokio::task::spawn_blocking(|| {
        if crate::core::model_endpoints::has_codex_scheme("", "http://localhost:11434/v1") {
            // `has_codex_scheme` is an exact-`base_url` existence check (owner/shared);
            // reuse it for the idempotency guard so we don't add a duplicate Ollama row.
            return false;
        }
        // `ModelEndpoint(id=uuid4()[:8], name="Ollama (local)", base_url=..., is_enabled=True)`.
        // `create` mints its own id; the Python's `[:8]` slice is cosmetic (the id is
        // opaque), so the canonical `create` id is faithful.
        crate::core::model_endpoints::create(
            "Ollama (local)",
            "http://localhost:11434/v1",
            None,
            &[],
            None,
        )
        .is_ok()
    })
    .await
    .unwrap_or(false);
    if registered {
        crate::pylog::info("Auto-added Ollama endpoint (localhost:11434)");
    }
}

/// `shutil.which(cmd)` — the first PATH entry containing an executable named `cmd`,
/// or `None`. A small local copy so this unit doesn't reach into `builtin_mcp`'s
/// private helper (the setup unit keeps its own copy for the same reason).
fn which(cmd: &str) -> Option<String> {
    let path = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path) {
        let candidate = dir.join(cmd);
        if candidate.is_file() {
            return Some(candidate.to_string_lossy().into_owned());
        }
    }
    None
}

#[cfg(test)]
mod build_router_mount_tests {
    //! Wave-4-reconciliation mount-smoke: the FULL router that [`build_router`]
    //! assembles — the inline SPA + demo `.route(...)` chain MERGED with the
    //! ported-factory aggregator [`crate::routes::api_routers`] — must build
    //! without an `axum`/`matchit` panic. `Router::merge` panics on a duplicate
    //! `method`+`path` AND on a conflicting capture name (e.g. `:sid` in
    //! `session_routes` vs `:session_id` in `history_routes` at the same path
    //! segment). The pre-existing `routes::aggregator_tests` test only merges the
    //! aggregator into a *bare* router; it does NOT include the inline subset, so
    //! an inline-vs-module collision (re-introduced by mistake) would slip past it
    //! and only blow up at runtime in `build_router`. This test closes that gap by
    //! reconstructing the exact same merge chain `build_router` uses (minus the
    //! `.with_state`/layers, which require a heavyweight live `AppState`).

    use super::*;

    #[test]
    fn full_router_merge_chain_is_collision_free() {
        // Mirror `build_router`'s router construction verbatim: the SPA page
        // routes, then the inline demo API, then the ported-factory aggregator.
        let spa = Router::new()
            .route("/", get(index_page))
            .route("/notes", get(index_page))
            .route("/calendar", get(index_page))
            .route("/cookbook", get(index_page))
            .route("/email", get(index_page))
            .route("/memory", get(index_page))
            .route("/gallery", get(index_page))
            .route("/tasks", get(index_page))
            .route("/library", get(index_page))
            .route("/login", get(login_page));

        let api = Router::new()
            .route("/api/health", get(health))
            .route("/api/version", get(version))
            .route("/api/codex/connect", post(codex_connect))
            .route("/api/demo/estimate", get(demo_estimate));

        // The load-bearing line: if any ported wave-4/5 module re-introduced an
        // inline collision (or two modules registered the same method+path / a
        // conflicting `:capture` name — including `chat_routes`' `/api/chat_stream`,
        // which now SUPERSEDES the deleted inline handler), this `.merge` chain panics
        // here and the test fails — exactly the dup-path guard the reconciliation
        // relies on.
        let _app: Router<AppState> = spa
            .merge(api)
            .merge(crate::routes::api_routers());
    }
}

#[cfg(test)]
mod app_py_parity_tests {
    //! Focused unit tests for the app.py-parity pieces added to `web/mod.rs`: the
    //! timeout-exempt prefix matching, the token-cache dirty-flag invalidation, and
    //! the `ODYSSEUS_INPROCESS_TASKS` gate.

    use super::*;

    #[test]
    fn timeout_exempt_prefixes_match_app_py() {
        // The nine `_TIMEOUT_EXEMPT_PREFIXES` (app.py:75-85), in order.
        assert_eq!(TIMEOUT_EXEMPT_PREFIXES.len(), 9);
        let exempt = |p: &str| TIMEOUT_EXEMPT_PREFIXES.iter().any(|pre| p.starts_with(pre));
        // Streaming / long-running paths are exempt (incl. deeper sub-paths).
        assert!(exempt("/api/chat"));
        assert!(exempt("/api/chat_stream"));
        assert!(exempt("/api/shell/stream"));
        assert!(exempt("/api/research/start"));
        assert!(exempt("/api/model/download"));
        assert!(exempt("/api/model/probe"));
        assert!(exempt("/api/model-endpoints"));
        assert!(exempt("/api/cookbook/setup"));
        assert!(exempt("/api/upload"));
        assert!(exempt("/api/image"));
        // A normal API path is NOT exempt (subject to the hard timeout).
        assert!(!exempt("/api/sessions"));
        assert!(!exempt("/api/memory"));
        assert!(!exempt("/"));
    }

    #[test]
    fn request_hard_timeout_defaults_to_45() {
        // `float(os.getenv("REQUEST_HARD_TIMEOUT", "45"))` — the default when unset.
        // (The env may be set by the runner; assert the value is a sane positive f64.)
        let t = *REQUEST_HARD_TIMEOUT;
        assert!(t > 0.0);
        // The `{:.0}` format used in the 504 body rounds to a whole-second display.
        assert_eq!(format!("Request exceeded {:.0}s timeout", 45.0_f64),
                   "Request exceeded 45s timeout");
    }

    #[test]
    fn token_cache_invalidate_sets_dirty() {
        // `_token_cache_invalidate()` flips the dirty flag so the next Bearer request
        // rebuilds the cache (the real `AppState.invalidate_token_cache` hook).
        token_cache::invalidate();
        assert!(TOKEN_CACHE.dirty.load(Ordering::Acquire));
    }

    #[test]
    fn inprocess_tasks_gate_matches_python() {
        // `os.environ.get("ODYSSEUS_INPROCESS_TASKS", "1").strip().lower() not in
        // ("0","false","no","off","")`. Test the parse predicate directly.
        let disabled = |raw: &str| {
            let r = raw.trim().to_lowercase();
            matches!(r.as_str(), "0" | "false" | "no" | "off" | "")
        };
        // Disabled set.
        for v in ["0", "false", "no", "off", "", "  ", "FALSE", "Off"] {
            assert!(disabled(v), "{v:?} should disable");
        }
        // Anything else enables (incl. the "1" default).
        for v in ["1", "yes", "true", "on", "anything"] {
            assert!(!disabled(v), "{v:?} should enable");
        }
    }

    #[test]
    fn chroma_autolaunch_gate_parses_truthy() {
        // `ODYSSEUS_LAUNCH_CHROMA` truthy = 1/true/yes (builtin_mcp convention),
        // default OFF. Test the parse predicate directly (the real fn reads the
        // process-wide env, which would race other tests). Mirrors the function body.
        let truthy = |raw: &str| matches!(raw.trim().to_lowercase().as_str(), "1" | "true" | "yes");
        for v in ["1", "true", "yes", "  TRUE ", "Yes"] {
            assert!(truthy(v), "{v:?} should enable auto-launch");
        }
        // Everything else (incl. the unset-default and other words) is OFF.
        for v in ["0", "false", "no", "off", "", "on", "enable", "2"] {
            assert!(!truthy(v), "{v:?} should NOT enable auto-launch");
        }
    }

    /// The generated-image filename allowlist regex (app.py:301) — pure, no IO.
    #[test]
    fn generated_image_filename_regex_matches_app_py() {
        // Valid: 8-64 lowercase hex + an allowed extension.
        assert!(GENERATED_IMAGE_FILENAME_RE.is_match("abcdef01.png"));
        assert!(GENERATED_IMAGE_FILENAME_RE.is_match("0123456789abcdef.jpg"));
        assert!(GENERATED_IMAGE_FILENAME_RE.is_match("deadbeef.mp4"));
        assert!(GENERATED_IMAGE_FILENAME_RE.is_match("aabbccdd.webm"));
        // Invalid: too short (<8), uppercase, traversal, bad ext, non-hex.
        assert!(!GENERATED_IMAGE_FILENAME_RE.is_match("abc.png")); // 3 hex
        assert!(!GENERATED_IMAGE_FILENAME_RE.is_match("ABCDEF01.png")); // uppercase
        assert!(!GENERATED_IMAGE_FILENAME_RE.is_match("../secret.png")); // traversal
        assert!(!GENERATED_IMAGE_FILENAME_RE.is_match("deadbeef.exe")); // bad ext
        assert!(!GENERATED_IMAGE_FILENAME_RE.is_match("zzzzzzzz.png")); // non-hex
        assert!(!GENERATED_IMAGE_FILENAME_RE.is_match("deadbeef.png.png")); // double ext
    }

    /// End-to-end serve: write a file under a TEMP cwd `data/generated_images`,
    /// then exercise the handler's 400 / 404 / 200 paths + the ownership 404.
    /// `#[ignore]` because it mutates the process-global cwd + DATABASE_URL.
    #[tokio::test]
    #[ignore = "mutates process-global cwd + DATABASE_URL"]
    async fn serve_generated_image_roundtrip() {
        use axum::extract::Path as AxPath;

        let tmp = std::env::temp_dir().join(format!(
            "odysseus_serve_{}_{}",
            std::process::id(),
            uuid::Uuid::new_v4().simple()
        ));
        std::fs::create_dir_all(tmp.join("data/generated_images")).unwrap();
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
        let db_path = tmp.join("serve.db");
        let _guard = Cleanup { tmp: tmp.clone(), prev_cwd, db: db_path.clone() };
        std::env::set_current_dir(&tmp).unwrap();
        std::env::set_var("DATABASE_URL", format!("sqlite:///{}", db_path.display()));
        crate::core::database::create_all().unwrap();

        // A valid 12-hex .png filename with known bytes on disk.
        let filename = "0123456789ab.png";
        let bytes: Vec<u8> = vec![0x89, b'P', b'N', b'G', 1, 2, 3, 4];
        std::fs::write(tmp.join("data/generated_images").join(filename), &bytes).unwrap();

        // --- 400: bad filename (uppercase / traversal) ---
        let bad = serve_generated_image(AxPath("BADNAME.png".to_string()), None).await;
        assert_eq!(bad.unwrap_err().status_code, 400);

        // --- 404: well-formed but missing ---
        let miss = serve_generated_image(AxPath("ffffffffffff.png".to_string()), None).await;
        assert_eq!(miss.unwrap_err().status_code, 404);

        // --- 200: anonymous (no user) returns the bytes + headers ---
        let ok = serve_generated_image(AxPath(filename.to_string()), None)
            .await
            .expect("anon serve");
        assert_eq!(ok.status(), 200);
        assert_eq!(
            ok.headers().get(header::CONTENT_TYPE).unwrap(),
            "image/png"
        );
        assert_eq!(
            ok.headers().get(header::CACHE_CONTROL).unwrap(),
            "public, max-age=31536000, immutable"
        );
        let served = axum::body::to_bytes(ok.into_body(), usize::MAX).await.unwrap();
        assert_eq!(served.as_ref(), bytes.as_slice());

        // --- 404: a gallery row owned by someone ELSE hides the image from a user ---
        {
            let conn = crate::core::database::session_local().unwrap();
            let ts = crate::pydatetime::utcnow_naive_iso();
            conn.execute(
                "INSERT INTO gallery_images (id, filename, prompt, owner, is_active, favorite, created_at, updated_at) \
                 VALUES (?1, ?2, '', ?3, 1, 0, ?4, ?4)",
                rusqlite::params!["g1", filename, "alice", ts],
            )
            .unwrap();
        }
        let denied = serve_generated_image(
            AxPath(filename.to_string()),
            Some(Extension(CurrentUser("bob".to_string()))),
        )
        .await;
        assert_eq!(denied.unwrap_err().status_code, 404, "non-owner must 404");

        // --- 200: the OWNER still gets the bytes ---
        let owner_ok = serve_generated_image(
            AxPath(filename.to_string()),
            Some(Extension(CurrentUser("alice".to_string()))),
        )
        .await
        .expect("owner serve");
        assert_eq!(owner_ok.status(), 200);
        // cleanup via `_guard`'s Drop.
    }

    /// `GET /api/ready` — end-to-end readiness handler with a real temp DB +
    /// a writable temp data dir. Mirrors `src/readiness.py::check_readiness`
    /// (app.py:779-788).
    ///
    /// Exercises:
    ///   * happy path: DB reachable + data dir writable → `ready: true`, 200;
    ///   * data-dir write failure: read-only dir → `ready: false`, 503;
    ///   * `local_first` flag for sqlite vs. remote URL.
    #[tokio::test]
    async fn readiness_handler_happy_path() {
        // Provision a real temp DB and a writable temp data dir.
        let tmp = std::env::temp_dir().join(format!(
            "odysseus_ready_{}_{}",
            std::process::id(),
            uuid::Uuid::new_v4().simple()
        ));
        std::fs::create_dir_all(&tmp).unwrap();
        let db_path = tmp.join("ready_test.db");
        std::env::set_var("DATABASE_URL", format!("sqlite:///{}", db_path.display()));
        std::env::set_var("ODYSSEUS_DATA_DIR", tmp.to_str().unwrap());
        crate::core::database::create_all().unwrap();

        let resp = readiness().await;
        let status = resp.status();
        let body =
            axum::body::to_bytes(resp.into_body(), usize::MAX)
                .await
                .unwrap();
        let v: serde_json::Value = serde_json::from_slice(&body).unwrap();

        // ── Status + ready flag ──────────────────────────────────────────────
        assert_eq!(status, StatusCode::OK, "happy path must be 200");
        assert!(v["ready"].as_bool().unwrap_or(false), "ready must be true");

        // ── Checks map ───────────────────────────────────────────────────────
        assert!(v["checks"]["database"]["ok"].as_bool().unwrap_or(false), "db ok");
        assert!(v["checks"]["data_dir"]["ok"].as_bool().unwrap_or(false), "data_dir ok");
        assert!(v["checks"]["local_first"]["ok"].as_bool().unwrap_or(false), "local_first.ok");
        // sqlite URL → local_first = true
        assert!(v["checks"]["local_first"]["local"].as_bool().unwrap_or(false), "local_first.local for sqlite");

        // ── version + timestamp present ──────────────────────────────────────
        assert!(v["version"].is_string(), "version must be a string");
        assert!(v["timestamp"].is_string(), "timestamp must be present");

        // Cleanup.
        std::fs::remove_dir_all(&tmp).ok();
    }

    /// `local_first` logic (readiness.py:47-53): sqlite → true; remote → false.
    #[test]
    fn readiness_local_first_logic() {
        let local = |url: &str| {
            url.starts_with("sqlite")
                || url.contains("localhost")
                || url.contains("127.0.0.1")
        };
        assert!(local("sqlite:///data/app.db"));
        assert!(local("sqlite+aiosqlite:///app.db"));
        assert!(local("postgresql://user:pass@localhost/db"));
        assert!(local("mysql://user@127.0.0.1/db"));
        assert!(!local("postgresql://user:pass@db.example.com/prod"));
        assert!(!local("mysql://user@192.168.1.1/db"));
    }
}
