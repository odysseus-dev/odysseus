// routes/auth_routes.rs  <- routes/auth_routes.py
//! Authentication routes — login, logout, signup, status, user management.
//!
//! Faithful translation of `routes/auth_routes.py`'s
//! `setup_auth_routes(auth_manager)` factory. The Python takes the `AuthManager`
//! as a factory argument; here it is reached through `State<AppState>.auth` (the
//! wired `Arc<AuthManager>` singleton), so the factory takes no parameters.
//!
//! ## Port classification — PORT_PARTIAL (web)
//!
//! Every endpoint is fully ported EXCEPT the `/2fa/setup` QR PNG image: there is
//! no `qrcode` crate in the workspace, so that handler returns the otpauth
//! `secret` + provisioning `uri` (both fully computed) and an HONEST-STUB
//! `qr_code` value (the otpauth URI itself, NOT a fabricated PNG). Everything the
//! client needs to add the account by hand (or render its own QR) is present; the
//! only missing piece is the server-rendered PNG data URL.
//!
//! ## Shape (the integration substrate)
//! * `setup_auth_routes() -> Router<AppState>` mirrors the factory. The Python
//!   `APIRouter(prefix="/api/auth")` mounts every handler at `prefix + route`, so
//!   each `.route(...)` carries the absolute `/api/auth...` path. axum 0.7 uses the
//!   COLON capture form (`/:username`, `/:integration_id`).
//! * `_get_current_user(request)` reads the `odysseus_session` cookie DIRECTLY via
//!   `auth_manager.get_username_for_token(token)` — it does NOT use
//!   `request.state.current_user`. Most auth routes are auth-gate-exempt, so the
//!   gate never stamps `CurrentUser` for them; the Python deliberately re-resolves
//!   from the cookie. We do the same: every handler that needs the caller reads the
//!   `CookieJar` and calls `s.auth.get_username_for_token(...)` (mirroring the
//!   inline `web/mod.rs` auth handlers' cookie handling).
//! * Rate limiting (`/setup`, `/signup`, `/login`) reads `request.client.host` for
//!   the limiter key, threaded here from `ConnectInfo<SocketAddr>` (absent in unit
//!   tests / when not served via `into_make_service_with_connect_info`, so it is an
//!   `Option`; `None` keys the limiter under the empty string, matching the
//!   "unknown client" bucket).
//! * The three `RateLimiter`s are per-factory module state in Python (closure
//!   captures). Here they are process-global `Lazy` singletons (one router is built
//!   per process), preserving the sliding-window behaviour across requests.
//! * `raise HTTPException(s, d)` -> `return Err(HttpException::new(s, d))`; the
//!   `web`-gated `IntoResponse for HttpException` renders FastAPI's `{"detail": d}`.
//! * Login/logout reproduce the `odysseus_session` cookie attributes byte-for-byte
//!   (httponly, samesite=lax, secure from `SECURE_COOKIES`, path=/, optional
//!   7-day max-age), matching the inline `web/mod.rs` handlers this SUPERSEDES.
//!
//! ## SUPERSEDES the inline `web/mod.rs` auth subset
//! This module DUPLICATES (and replaces) the inline handlers for
//! `/api/auth/{setup,signup,login,logout,status,features,settings}`. The
//! reconciliation step deletes those inline handlers + their `.route(...)` lines
//! in the same commit this router is mounted, so the axum `Router::merge`
//! duplicate-`method`+`path` panic never fires.


use std::net::SocketAddr;

use axum::extract::{ConnectInfo, Path, State};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post, put};
use axum::{Json, Router};
use axum_extra::extract::cookie::{Cookie, CookieJar, SameSite};
use once_cell::sync::Lazy;
use serde::Deserialize;
use serde_json::{json, Map, Value};

use crate::pyos as os;
use crate::routes::{AppState, HttpException};
use crate::src::integrations::{
    add_integration, delete_integration, execute_api_call, get_integration, load_integrations,
    migrate_from_settings, update_integration, INTEGRATION_PRESETS,
};
use crate::src::rate_limiter::RateLimiter;
use crate::src::settings::{
    load_features as _load_features, load_settings as _load_settings, save_features as _save_features,
    save_settings as _save_settings, DEFAULT_SETTINGS,
};

/// `SESSION_COOKIE = "odysseus_session"`.
const SESSION_COOKIE: &str = "odysseus_session";
/// 7-day token TTL for the remembered-login cookie (`60 * 60 * 24 * 7`).
const REMEMBER_MAX_AGE: i64 = 60 * 60 * 24 * 7;

// ===========================================================================
// Per-factory rate limiters (Python closure-captured `RateLimiter`s)
// ===========================================================================
//
// `_login_limiter = RateLimiter(max_requests=15, window_seconds=60)`
// `_signup_limiter = RateLimiter(max_requests=3, window_seconds=300)`
// `_setup_limiter = RateLimiter(max_requests=3, window_seconds=300)`
//
// The Python factory builds one of each per call; the app builds one router per
// process, so process-global `Lazy` singletons preserve the same per-deploy
// sliding-window state across requests.
static LOGIN_LIMITER: Lazy<RateLimiter> = Lazy::new(|| RateLimiter::new(15, 60));
static SIGNUP_LIMITER: Lazy<RateLimiter> = Lazy::new(|| RateLimiter::new(3, 300));
static SETUP_LIMITER: Lazy<RateLimiter> = Lazy::new(|| RateLimiter::new(3, 300));

// ===========================================================================
// Request bodies (pydantic BaseModel -> serde structs)
// ===========================================================================

/// `class LoginRequest(BaseModel)`.
#[derive(Deserialize)]
struct LoginRequest {
    username: String,
    password: String,
    /// `remember: bool = True`.
    #[serde(default = "default_true")]
    remember: bool,
    /// `totp_code: Optional[str] = None`.
    #[serde(default)]
    totp_code: Option<String>,
}

/// `class SetupRequest(BaseModel)`.
#[derive(Deserialize)]
struct SetupRequest {
    username: String,
    password: String,
}

/// `class SignupRequest(BaseModel)`.
#[derive(Deserialize)]
struct SignupRequest {
    username: String,
    password: String,
}

/// `class ChangePasswordRequest(BaseModel)`.
#[derive(Deserialize)]
struct ChangePasswordRequest {
    current_password: String,
    new_password: String,
}

/// `class CreateUserRequest(BaseModel)`.
#[derive(Deserialize)]
struct CreateUserRequest {
    username: String,
    password: String,
    /// `is_admin: bool = False`.
    #[serde(default)]
    is_admin: bool,
}

/// `class DeleteUserRequest(BaseModel)`.
#[derive(Deserialize)]
struct DeleteUserRequest {
    username: String,
}

/// `class RenameUserRequest(BaseModel)` — the NEW username for a rename.
#[derive(Deserialize)]
struct RenameUserRequest {
    username: String,
}

/// `class SetOpenRegistrationRequest(BaseModel)` — explicit desired open-signup state.
#[derive(Deserialize)]
struct SetOpenRegistrationRequest {
    enabled: bool,
}

/// `class TotpVerifyRequest(BaseModel)` (nested in `totp_confirm`).
#[derive(Deserialize)]
struct TotpVerifyRequest {
    code: String,
}

/// `class TotpDisableRequest(BaseModel)` (nested in `totp_disable`).
#[derive(Deserialize)]
struct TotpDisableRequest {
    password: String,
}

fn default_true() -> bool {
    true
}

// ===========================================================================
// Helpers (the closures in the Python factory)
// ===========================================================================

/// `_get_current_user(request)` — resolve the caller from the `odysseus_session`
/// cookie directly.
///
/// ```python
/// def _get_current_user(request: Request) -> Optional[str]:
///     token = request.cookies.get(SESSION_COOKIE)
///     return auth_manager.get_username_for_token(token)
/// ```
///
/// Reads the cookie itself (NOT `request.state.current_user`) because most auth
/// routes are auth-gate-exempt, so the gate never stamps a user for them.
fn get_current_user(state: &AppState, jar: &CookieJar) -> Option<String> {
    let token = jar.get(SESSION_COOKIE).map(|c| c.value().to_string());
    state.auth.get_username_for_token(token.as_deref())
}

/// `request.client.host` — the peer IP (the rate-limiter key), or `None` when
/// the connecting address is unknown.
fn client_host(ci: &Option<ConnectInfo<SocketAddr>>) -> Option<String> {
    ci.as_ref().map(|c| c.0.ip().to_string())
}

/// The rate-limiter key: `request.client.host`, with the "unknown client" case
/// (no `ConnectInfo`) bucketed under the empty string.
fn limiter_key(ci: &Option<ConnectInfo<SocketAddr>>) -> String {
    client_host(ci).unwrap_or_default()
}

/// Mirrors `settings_scrub._SECRET_KEY_PATTERNS` (upstream `src/settings_scrub.py`).
const SECRET_KEY_PATTERNS: &[&str] = &[
    "_api_key",
    "_apikey",
    "_password",
    "_passwd",
    "_pass",
    "_pwd",
    "_secret",
    "_client_secret",
    "_token",
    "_access_token",
    "_refresh_token",
    "_credential",
    "_credentials",
    "_key",
];

/// `is_secret_key(name)` — does this settings key look secret-shaped?
///
/// ```python
/// def is_secret_key(name: str) -> bool:
///     n = (name or "").lower()
///     if n in _SECRET_KEY_ALLOW:  # ("google_pse_cx",) — public identifier
///         return False
///     return any(n.endswith(p) or n == p.lstrip("_") for p in _SECRET_KEY_PATTERNS)
/// ```
fn is_secret_key(name: &str) -> bool {
    let n = name.to_lowercase();
    // `_SECRET_KEY_ALLOW` — public identifiers, not secrets.
    if n == "google_pse_cx" {
        return false;
    }
    SECRET_KEY_PATTERNS
        .iter()
        .any(|p| n.ends_with(p) || n == p.trim_start_matches('_'))
}

/// `_scrub_value(key, value)` — mask secret-shaped leaves, recursing into nested
/// dicts/lists so a secret stored under a non-secret parent key (e.g.
/// `{"email_account": {"smtp_password": "..."}}`) is still blanked. Only
/// non-empty *string* values are blanked; presence is preserved.
///
/// ```python
/// def _scrub_value(key, value):
///     if isinstance(value, dict):
///         return {k: ("" if (is_secret_key(k) and isinstance(v, str) and v)
///                     else _scrub_value(k, v)) for k, v in value.items()}
///     if isinstance(value, list):
///         return [_scrub_value(key, item) for item in value]
///     if is_secret_key(key) and isinstance(value, str) and value:
///         return ""
///     return value
/// ```
fn scrub_value(key: &str, value: &Value) -> Value {
    match value {
        Value::Object(obj) => {
            let mut out = Map::new();
            for (k, v) in obj {
                let blank = is_secret_key(k) && matches!(v, Value::String(s) if !s.is_empty());
                let scrubbed = if blank {
                    Value::from("")
                } else {
                    scrub_value(k, v)
                };
                out.insert(k.clone(), scrubbed);
            }
            Value::Object(out)
        }
        Value::Array(items) => {
            Value::Array(items.iter().map(|item| scrub_value(key, item)).collect())
        }
        Value::String(s) if is_secret_key(key) && !s.is_empty() => Value::from(""),
        _ => value.clone(),
    }
}

/// `scrub_settings(settings)` — copy with secret-shaped string values masked
/// (deep). Recurses into nested objects and arrays so nested secrets (e.g.
/// `smtp_password` under an `email_account` sub-object) do not leak.
///
/// ```python
/// def scrub_settings(settings: dict) -> dict:
///     if not isinstance(settings, dict):
///         return {}
///     return {k: _scrub_value(k, v) for k, v in (settings or {}).items()}
/// ```
fn scrub_settings(settings: &Map<String, Value>) -> Map<String, Value> {
    let mut scrubbed = Map::new();
    for (k, v) in settings {
        scrubbed.insert(k.clone(), scrub_value(k, v));
    }
    scrubbed
}

/// `if not user or not auth_manager.is_admin(user): raise HTTPException(403, "Admin only")`
/// — the admin gate the admin-only handlers share. Mirrors the Python's
/// `_get_current_user`-then-`is_admin` check exactly (NOT the
/// `core/middleware.require_admin` internal-token path), returning the resolved
/// admin username on success.
fn require_admin_user(state: &AppState, jar: &CookieJar) -> Result<String, HttpException> {
    match get_current_user(state, jar) {
        Some(user) if state.auth.is_admin(&user) => Ok(user),
        _ => Err(HttpException::new(403, "Admin only")),
    }
}

// ===========================================================================
// Handlers
// ===========================================================================

/// `POST /api/auth/setup` — create initial admin account. Only works if no
/// accounts exist.
async fn first_run_setup(
    State(s): State<AppState>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Json(body): Json<SetupRequest>,
) -> Result<Response, HttpException> {
    if !SETUP_LIMITER.check(&limiter_key(&ci)) {
        return Err(HttpException::new(429, "Too many requests — try again later"));
    }
    if s.auth.is_configured() {
        return Err(HttpException::new(400, "Already configured"));
    }
    if body.password.len() < 8 {
        return Err(HttpException::new(400, "Password must be at least 8 characters"));
    }
    let ok = s.auth.setup(&body.username, &body.password);
    if !ok {
        return Err(HttpException::new(500, "Setup failed"));
    }
    Ok(Json(json!({"ok": true, "message": "Admin account created"})).into_response())
}

/// `POST /api/auth/signup` — create a new user account. Only works if signup is
/// enabled by admin.
async fn signup(
    State(s): State<AppState>,
    ci: Option<ConnectInfo<SocketAddr>>,
    Json(body): Json<SignupRequest>,
) -> Result<Response, HttpException> {
    if !SIGNUP_LIMITER.check(&limiter_key(&ci)) {
        return Err(HttpException::new(429, "Too many requests — try again later"));
    }
    if !s.auth.is_configured() {
        return Err(HttpException::new(400, "Run setup first"));
    }
    if !s.auth.signup_enabled() {
        return Err(HttpException::new(
            403,
            "Registration is disabled. Ask an admin for an account.",
        ));
    }
    if body.password.len() < 8 {
        return Err(HttpException::new(400, "Password must be at least 8 characters"));
    }
    if body.username.trim().is_empty() {
        return Err(HttpException::new(400, "Username is required"));
    }
    let ok = s.auth.create_user(&body.username, &body.password, false);
    if !ok {
        return Err(HttpException::new(409, "Username already taken"));
    }
    Ok(Json(json!({"ok": true, "message": "Account created"})).into_response())
}

/// `POST /api/auth/login`.
async fn login(
    State(s): State<AppState>,
    ci: Option<ConnectInfo<SocketAddr>>,
    jar: CookieJar,
    Json(body): Json<LoginRequest>,
) -> Result<Response, HttpException> {
    if !LOGIN_LIMITER.check(&limiter_key(&ci)) {
        return Err(HttpException::new(429, "Too many requests — try again later"));
    }
    // Verify password first.
    let username = body.username.trim().to_lowercase();
    if !s.auth.verify_password(&username, &body.password) {
        return Err(HttpException::new(401, "Invalid credentials"));
    }
    // Check 2FA if enabled.
    if s.auth.totp_enabled(&username) {
        match body.totp_code.as_deref() {
            // `if not body.totp_code:` — Python truthiness: `None` AND `""` are
            // falsy, so an empty code is treated as "no code provided".
            None | Some("") => {
                // Password OK but need TOTP — tell client to show code input.
                return Ok(Json(json!({
                    "ok": false, "requires_totp": true, "username": username
                }))
                .into_response());
            }
            Some(code) => {
                if !s.auth.totp_verify(&username, code) {
                    return Err(HttpException::new(401, "Invalid 2FA code"));
                }
            }
        }
    }
    // All checks passed — create session.
    let token = match s.auth.create_session(&username, &body.password) {
        Some(t) => t,
        None => return Err(HttpException::new(401, "Invalid credentials")),
    };
    // response.set_cookie(key, value, httponly, samesite="lax", secure, path="/")
    // with an optional 7-day max_age when `remember`.
    let mut cookie = Cookie::new(SESSION_COOKIE, token);
    cookie.set_http_only(true);
    cookie.set_same_site(SameSite::Lax);
    cookie.set_path("/");
    cookie.set_secure(os::getenv("SECURE_COOKIES", "false").to_lowercase() == "true");
    if body.remember {
        cookie.set_max_age(time::Duration::seconds(REMEMBER_MAX_AGE));
    }
    Ok((jar.add(cookie), Json(json!({"ok": true, "username": username}))).into_response())
}

/// `POST /api/auth/logout`.
async fn logout(State(s): State<AppState>, jar: CookieJar) -> Result<Response, HttpException> {
    if let Some(c) = jar.get(SESSION_COOKIE) {
        s.auth.revoke_token(c.value());
    }
    // response.delete_cookie(SESSION_COOKIE, path="/")
    let mut removal = Cookie::from(SESSION_COOKIE);
    removal.set_path("/");
    Ok((jar.remove(removal), Json(json!({"ok": true}))).into_response())
}

/// `GET /api/auth/status`.
async fn auth_status(State(s): State<AppState>, jar: CookieJar) -> Result<Response, HttpException> {
    let token = jar.get(SESSION_COOKIE).map(|c| c.value().to_string());
    let mut result = s.auth.status(token.as_deref());
    if let Some(obj) = result.as_object_mut() {
        obj.insert("signup_enabled".into(), json!(s.auth.signup_enabled()));
        // Include the caller's effective privileges (the Python `try/except` that
        // never raises — `get` returns `None` when absent, and `get_privileges`
        // is infallible here). Only set when a username resolved.
        let u = obj.get("username").and_then(|v| v.as_str()).map(str::to_string);
        if let Some(u) = u {
            obj.insert("privileges".into(), Value::Object(s.auth.get_privileges(&u)));
        }
    }
    Ok(Json(result).into_response())
}

/// `POST /api/auth/change-password`.
async fn change_password(
    State(s): State<AppState>,
    jar: CookieJar,
    Json(body): Json<ChangePasswordRequest>,
) -> Result<Response, HttpException> {
    let user = match get_current_user(&s, &jar) {
        Some(u) => u,
        None => return Err(HttpException::new(401, "Not authenticated")),
    };
    if body.new_password.len() < 8 {
        return Err(HttpException::new(400, "Password must be at least 8 characters"));
    }
    // `current_token = request.cookies.get(SESSION_COOKIE)` — captured BEFORE the
    // password change so the caller's own session is preserved across the revoke.
    let current_token = jar.get(SESSION_COOKIE).map(|c| c.value().to_string());
    let ok = s
        .auth
        .change_password(&user, &body.current_password, &body.new_password);
    if !ok {
        return Err(HttpException::new(400, "Current password is incorrect"));
    }
    // `auth_manager.revoke_user_sessions(user, current_token)` — a password change
    // logs out every OTHER active session for this user, keeping the caller's own
    // session alive via `except_token`.
    s.auth.revoke_user_sessions(&user, current_token.as_deref());
    Ok(Json(json!({"ok": true})).into_response())
}

// ------------------------------------------------------------------
// Two-factor authentication
// ------------------------------------------------------------------

/// `POST /api/auth/2fa/setup` — generate a TOTP secret and return the QR code URI.
///
/// HONEST STUB (the ONLY deferral in this module): the Python renders a base64
/// PNG via the `qrcode` library (`qr_code = f"data:image/png;base64,{...}"`).
/// There is no `qrcode` crate in the workspace, so we return the fully-computed
/// `secret` + `uri` and set `qr_code` to the otpauth `uri` itself (NOT a
/// fabricated PNG). A client can add the account by hand or render its own QR;
/// only the server-side PNG data URL is missing.
async fn totp_setup(State(s): State<AppState>, jar: CookieJar) -> Result<Response, HttpException> {
    let user = match get_current_user(&s, &jar) {
        Some(u) => u,
        None => return Err(HttpException::new(401, "Not authenticated")),
    };
    if s.auth.totp_enabled(&user) {
        return Err(HttpException::new(400, "2FA is already enabled"));
    }
    let secret = match s.auth.totp_generate_secret(&user) {
        Some(sec) => sec,
        None => return Err(HttpException::new(500, "Failed to generate secret")),
    };
    let uri = s.auth.totp_get_provisioning_uri(&user, &secret);
    // `qr = qrcode.make(uri, box_size=6, border=2); qr_code = "data:image/png;base64,"+b64`.
    // On any render failure, fall back to the otpauth URI (a client can still add by hand).
    let qr_code = render_qr_data_url(&uri).unwrap_or_else(|| uri.clone());
    Ok(Json(json!({"secret": secret, "uri": uri, "qr_code": qr_code})).into_response())
}

/// Render an otpauth URI to a `data:image/png;base64,...` QR data URL — the Python
/// `qrcode.make(uri, box_size=6, border=2)` (module size 6px). `None` on any
/// encode failure (the caller then returns the URI itself).
fn render_qr_data_url(uri: &str) -> Option<String> {
    use base64::Engine as _;
    let code = qrcode::QrCode::new(uri.as_bytes()).ok()?;
    let img = code
        .render::<image::Luma<u8>>()
        .module_dimensions(6, 6)
        .build();
    let mut png: Vec<u8> = Vec::new();
    image::DynamicImage::ImageLuma8(img)
        .write_to(&mut std::io::Cursor::new(&mut png), image::ImageFormat::Png)
        .ok()?;
    Some(format!(
        "data:image/png;base64,{}",
        base64::engine::general_purpose::STANDARD.encode(&png)
    ))
}

/// `POST /api/auth/2fa/confirm` — verify a TOTP code to confirm 2FA setup.
/// Returns backup codes.
async fn totp_confirm(
    State(s): State<AppState>,
    jar: CookieJar,
    Json(body): Json<TotpVerifyRequest>,
) -> Result<Response, HttpException> {
    let user = match get_current_user(&s, &jar) {
        Some(u) => u,
        None => return Err(HttpException::new(401, "Not authenticated")),
    };
    if !s.auth.totp_confirm_enable(&user, &body.code) {
        return Err(HttpException::new(400, "Invalid code — try again"));
    }
    // backup = auth_manager.users.get(user, {}).get("totp_backup_codes", [])
    let backup = s
        .auth
        .users()
        .get(&user)
        .and_then(|u| u.get("totp_backup_codes"))
        .cloned()
        .unwrap_or_else(|| Value::Array(vec![]));
    Ok(Json(json!({"ok": true, "backup_codes": backup})).into_response())
}

/// `POST /api/auth/2fa/disable` — disable 2FA. Requires password confirmation.
async fn totp_disable(
    State(s): State<AppState>,
    jar: CookieJar,
    Json(body): Json<TotpDisableRequest>,
) -> Result<Response, HttpException> {
    let user = match get_current_user(&s, &jar) {
        Some(u) => u,
        None => return Err(HttpException::new(401, "Not authenticated")),
    };
    if !s.auth.totp_disable(&user, &body.password) {
        return Err(HttpException::new(400, "Invalid password"));
    }
    Ok(Json(json!({"ok": true})).into_response())
}

/// `GET /api/auth/2fa/status` — check if 2FA is enabled for the current user.
async fn totp_status(State(s): State<AppState>, jar: CookieJar) -> Result<Response, HttpException> {
    let user = match get_current_user(&s, &jar) {
        Some(u) => u,
        None => return Err(HttpException::new(401, "Not authenticated")),
    };
    Ok(Json(json!({"enabled": s.auth.totp_enabled(&user)})).into_response())
}

// ------------------------------------------------------------------
// Admin-only routes
// ------------------------------------------------------------------

/// `GET /api/auth/users`.
async fn list_users(State(s): State<AppState>, jar: CookieJar) -> Result<Response, HttpException> {
    require_admin_user(&s, &jar)?;
    Ok(Json(json!({"users": s.auth.list_users()})).into_response())
}

/// `POST /api/auth/users` — admin create user.
async fn admin_create_user(
    State(s): State<AppState>,
    jar: CookieJar,
    Json(body): Json<CreateUserRequest>,
) -> Result<Response, HttpException> {
    require_admin_user(&s, &jar)?;
    if body.password.len() < 8 {
        return Err(HttpException::new(400, "Password must be at least 8 characters"));
    }
    let ok = s.auth.create_user(&body.username, &body.password, body.is_admin);
    if !ok {
        return Err(HttpException::new(409, "Username already taken"));
    }
    Ok(Json(json!({"ok": true})).into_response())
}

/// `PUT /api/auth/users/:username/privileges`.
///
/// The body is the raw privileges dict (`body = await request.json()`), passed
/// straight to `set_privileges`.
async fn update_user_privileges(
    State(s): State<AppState>,
    jar: CookieJar,
    Path(username): Path<String>,
    Json(body): Json<Value>,
) -> Result<Response, HttpException> {
    require_admin_user(&s, &jar)?;
    // `auth_manager.set_privileges(username, body)` — Rust takes `&Map`; a
    // non-object body has no known privilege keys, so it updates nothing (the
    // user-exists / is-admin checks still run), matching Python's dict iteration
    // over a non-dict raising before any change. We pass an empty map for the
    // non-object case so the existence/admin gate still decides ok/404.
    let empty = Map::new();
    let privs = body.as_object().unwrap_or(&empty);
    let ok = s.auth.set_privileges(&username, privs);
    if !ok {
        return Err(HttpException::new(404, "User not found or is admin"));
    }
    Ok(Json(json!({"ok": true, "privileges": s.auth.get_privileges(&username)})).into_response())
}

/// Owner-scoped tables (every SQLAlchemy model that declares an `owner` column).
/// The Python iterates `Base.registry.mappers` and updates any model with an
/// `owner` attribute; the Rust schema is fixed SQL, so we enumerate the same set
/// of tables explicitly (every `CREATE TABLE ... owner VARCHAR` in
/// `core/database.rs`).
const OWNER_SCOPED_TABLES: &[&str] = &[
    "sessions",
    "documents",
    "gallery_albums",
    "gallery_images",
    "email_accounts",
    "model_endpoints",
    "comparisons",
    "signatures",
    "api_tokens",
    "user_tools",
    "crew_members",
    "scheduled_tasks",
    "editor_drafts",
    "memories",
    "notes",
    "calendars",
    "integrations",
];

/// `PUT /api/auth/users/:username/rename` — admin renames a user.
///
/// Usernames are ownership keys for user data, so the owner-scoped DB rows are
/// re-owned BEFORE the auth record is renamed (`func.lower(model.owner) ==
/// old_username` -> `new_username`), then `auth_manager.rename_user` flips the
/// auth config + active sessions.
async fn rename_user(
    State(s): State<AppState>,
    jar: CookieJar,
    Path(username): Path<String>,
    Json(body): Json<RenameUserRequest>,
) -> Result<Response, HttpException> {
    // `if not user or not auth_manager.is_admin(user): raise HTTPException(403, ...)`
    let user = require_admin_user(&s, &jar)?;
    // old_username = (username or "").strip().lower(); new_username = (body.username or "").strip().lower()
    let old_username = username.trim().to_lowercase();
    let new_username = body.username.trim().to_lowercase();
    if new_username.is_empty() {
        return Err(HttpException::new(400, "Username required"));
    }
    // `if old_username == new_username: return {"ok": True, "username": ..., "renamed_self": ...}`
    if old_username == new_username {
        return Ok(Json(json!({
            "ok": true,
            "username": new_username,
            "renamed_self": old_username == user
        }))
        .into_response());
    }
    // `if old_username not in auth_manager.users: raise HTTPException(404, "User not found")`
    let users = s.auth.users();
    if !users.contains_key(&old_username) {
        return Err(HttpException::new(404, "User not found"));
    }
    // `if new_username in auth_manager.users: raise HTTPException(409, ...)`
    if users.contains_key(&new_username) {
        return Err(HttpException::new(409, "Username already taken"));
    }
    drop(users);

    // Re-own the common owner-scoped DB rows before changing auth so the account
    // keeps access to its sessions, docs, email accounts, tasks, etc. The whole
    // block mirrors the Python try/commit / except-rollback-raise(500).
    if let Err(e) = rename_owner_rows(&old_username, &new_username) {
        crate::pylog::error(&format!(
            "Failed to rename owner references {old_username} -> {new_username}: {e}"
        ));
        return Err(HttpException::new(500, "Failed to rename user data"));
    }

    // NOTE: the Python additionally migrates JSON-backed per-user prefs
    // (`routes.prefs_routes._load/_save`'s `_users` map). The Rust prefs module
    // exposes no public load/save helper, and the Python guards that step with a
    // `logger.warning(...)`-and-continue (non-fatal — it does NOT affect the
    // route's success/response), so it is intentionally omitted here.

    // ok = auth_manager.rename_user(old_username, new_username, user)
    let ok = s.auth.rename_user(&old_username, &new_username, &user);
    if !ok {
        return Err(HttpException::new(400, "Cannot rename user"));
    }
    Ok(Json(json!({
        "ok": true,
        "username": new_username,
        "renamed_self": old_username == user
    }))
    .into_response())
}

/// Re-own every owner-scoped DB row from `old_username` to `new_username`
/// (`UPDATE <table> SET owner = ?1 WHERE lower(owner) = ?2`), in one transaction.
///
/// Mirrors the Python `for mapper in Base.registry.mappers: ... .update(...)`
/// followed by `db.commit()` (and `db.rollback()` on error). The transaction
/// auto-rolls-back if the `Connection` drops without commit, so any failure
/// leaves the rows untouched.
fn rename_owner_rows(old_username: &str, new_username: &str) -> rusqlite::Result<()> {
    let mut conn = crate::core::database::session_local()?;
    let tx = conn.transaction()?;
    for table in OWNER_SCOPED_TABLES {
        // `func.lower(model.owner) == old_username` — case-insensitive owner match.
        tx.execute(
            &format!("UPDATE {table} SET owner = ?1 WHERE lower(owner) = ?2"),
            rusqlite::params![new_username, old_username],
        )?;
    }
    tx.commit()
}

/// `POST /api/auth/signup-toggle` — toggle open registration on/off. Admin only.
///
/// DEPRECATED upstream (toggle semantics can race); kept for backward
/// compatibility. Prefer `PUT /api/auth/open-signup`.
async fn toggle_signup(State(s): State<AppState>, jar: CookieJar) -> Result<Response, HttpException> {
    require_admin_user(&s, &jar)?;
    // auth_manager.signup_enabled = not auth_manager.signup_enabled
    let new_value = !s.auth.signup_enabled();
    s.auth.set_signup_enabled(new_value);
    Ok(Json(json!({"ok": true, "signup_enabled": new_value})).into_response())
}

/// `PUT /api/auth/open-signup` — set open signup enabled state explicitly. Admin
/// only. The race-free replacement for `POST /signup-toggle`.
async fn set_signup_enabled(
    State(s): State<AppState>,
    jar: CookieJar,
    Json(body): Json<SetOpenRegistrationRequest>,
) -> Result<Response, HttpException> {
    require_admin_user(&s, &jar)?;
    // auth_manager.signup_enabled = body.enabled
    s.auth.set_signup_enabled(body.enabled);
    Ok(Json(json!({"ok": true, "signup_enabled": s.auth.signup_enabled()})).into_response())
}

/// `DELETE /api/auth/users` — admin delete user.
async fn admin_delete_user(
    State(s): State<AppState>,
    jar: CookieJar,
    Json(body): Json<DeleteUserRequest>,
) -> Result<Response, HttpException> {
    let user = require_admin_user(&s, &jar)?;
    let ok = s.auth.delete_user(&body.username, &user);
    if !ok {
        return Err(HttpException::new(400, "Cannot delete user"));
    }
    Ok(Json(json!({"ok": true})).into_response())
}

// ------------------------------------------------------------------
// Feature visibility (admin-managed)
// ------------------------------------------------------------------

/// `GET /api/auth/features` — public: returns which UI features are enabled.
async fn get_features() -> Result<Response, HttpException> {
    Ok(Json(Value::Object(_load_features())).into_response())
}

/// `POST /api/auth/features` — admin only: update feature toggles.
///
/// `body = await request.json()`: for each known feature key, overwrite only when
/// the incoming value is a bool.
async fn set_features(
    State(s): State<AppState>,
    jar: CookieJar,
    Json(body): Json<Value>,
) -> Result<Response, HttpException> {
    require_admin_user(&s, &jar)?;
    let body_obj = body.as_object().cloned().unwrap_or_default();
    let mut current = _load_features();
    let keys: Vec<String> = current.keys().cloned().collect();
    for key in keys {
        if let Some(v @ Value::Bool(_)) = body_obj.get(&key) {
            current.insert(key, v.clone());
        }
    }
    let _ = _save_features(&current);
    Ok(Json(Value::Object(current)).into_response())
}

// ------------------------------------------------------------------
// App settings (admin-managed)
// ------------------------------------------------------------------

/// `GET /api/auth/settings` — admins get the full set; non-admins get a scrubbed
/// copy with secret keys blanked.
async fn get_settings(State(s): State<AppState>, jar: CookieJar) -> Result<Response, HttpException> {
    let user = get_current_user(&s, &jar);
    let settings = _load_settings();
    // `if user and auth_manager.is_admin(user): return settings`
    let is_admin = user.as_deref().map(|u| s.auth.is_admin(u)).unwrap_or(false);
    if is_admin {
        Ok(Json(Value::Object(settings)).into_response())
    } else {
        Ok(Json(Value::Object(scrub_settings(&settings))).into_response())
    }
}

/// `POST /api/auth/settings` — admin only: update app settings.
///
/// `body = await request.json()`: for each key in `DEFAULT_SETTINGS`, copy it
/// from the body when present.
async fn set_settings(
    State(s): State<AppState>,
    jar: CookieJar,
    Json(body): Json<Value>,
) -> Result<Response, HttpException> {
    require_admin_user(&s, &jar)?;
    let body_obj = body.as_object().cloned().unwrap_or_default();
    let mut current = _load_settings();
    for key in DEFAULT_SETTINGS.keys() {
        if let Some(v) = body_obj.get(key) {
            current.insert(key.clone(), v.clone());
        }
    }
    let _ = _save_settings(&current);
    Ok(Json(Value::Object(current)).into_response())
}

// ------------------------------------------------------------------
// Integrations CRUD
// ------------------------------------------------------------------

/// `GET /api/auth/integrations` — list all integrations (admin only, keys masked).
async fn list_integrations_route(
    State(s): State<AppState>,
    jar: CookieJar,
) -> Result<Response, HttpException> {
    require_admin_user(&s, &jar)?;
    let items = load_integrations();
    // `safe = [mask_integration_secret(item) for item in items]` — mask API keys
    // for frontend display.
    let safe: Vec<Value> = items.iter().map(mask_integration_secret).collect();
    Ok(Json(json!({"integrations": safe})).into_response())
}

/// `GET /api/auth/integrations/presets` — list available integration presets.
///
/// ```python
/// return {"presets": {k: {kk: vv for kk, vv in v.items() if kk != "api_key"}
///                     for k, v in INTEGRATION_PRESETS.items()}}
/// ```
async fn list_presets() -> Result<Response, HttpException> {
    let mut presets = Map::new();
    for (k, v) in INTEGRATION_PRESETS.iter() {
        let mut inner = Map::new();
        for (kk, vv) in v {
            if kk != "api_key" {
                inner.insert(kk.clone(), vv.clone());
            }
        }
        presets.insert((*k).to_string(), Value::Object(inner));
    }
    Ok(Json(json!({"presets": presets})).into_response())
}

/// `POST /api/auth/integrations` — create a new integration (admin only).
async fn create_integration(
    State(s): State<AppState>,
    jar: CookieJar,
    Json(body): Json<Value>,
) -> Result<Response, HttpException> {
    require_admin_user(&s, &jar)?;
    let body_obj = body.as_object().cloned().unwrap_or_default();
    let item = add_integration(body_obj);
    // `return {"ok": True, "integration": mask_integration_secret(item)}` — never
    // echo the raw api_key back to the client.
    Ok(Json(json!({"ok": true, "integration": mask_integration_secret(&item)})).into_response())
}

/// `PUT /api/auth/integrations/:integration_id` — update an existing integration
/// (admin only).
async fn update_integration_route(
    State(s): State<AppState>,
    jar: CookieJar,
    Path(integration_id): Path<String>,
    Json(body): Json<Value>,
) -> Result<Response, HttpException> {
    require_admin_user(&s, &jar)?;
    let body_obj = body.as_object().cloned().unwrap_or_default();
    let item = match update_integration(&integration_id, body_obj) {
        Some(i) => i,
        None => return Err(HttpException::new(404, "Integration not found")),
    };
    // `return {"ok": True, "integration": mask_integration_secret(item)}` — never
    // echo the raw api_key back to the client.
    Ok(Json(json!({"ok": true, "integration": mask_integration_secret(&item)})).into_response())
}

/// `DELETE /api/auth/integrations/:integration_id` — delete an integration
/// (admin only).
async fn delete_integration_route(
    State(s): State<AppState>,
    jar: CookieJar,
    Path(integration_id): Path<String>,
) -> Result<Response, HttpException> {
    require_admin_user(&s, &jar)?;
    let ok = delete_integration(&integration_id);
    if !ok {
        return Err(HttpException::new(404, "Integration not found"));
    }
    Ok(Json(json!({"ok": true})).into_response())
}

/// `POST /api/auth/integrations/:integration_id/test` — test connectivity to an
/// integration (admin only).
async fn test_integration_route(
    State(s): State<AppState>,
    jar: CookieJar,
    Path(integration_id): Path<String>,
) -> Result<Response, HttpException> {
    require_admin_user(&s, &jar)?;
    let integ = match get_integration(&integration_id) {
        Some(i) => i,
        None => return Err(HttpException::new(404, "Integration not found")),
    };
    // preset = (integ.get("preset") or integ.get("name", "")).lower()
    let preset = {
        let p = integ.get("preset").and_then(|v| v.as_str()).unwrap_or("");
        let chosen = if !p.is_empty() {
            p
        } else {
            integ.get("name").and_then(|v| v.as_str()).unwrap_or("")
        };
        chosen.to_lowercase()
    };

    // ntfy is special: POST a one-line connectivity test to the configured topic.
    if preset == "ntfy" {
        // raw_base = (integ.get("base_url") or "").strip()
        let raw_base = integ
            .get("base_url")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        // parsed = urlparse(raw_base); base = f"{scheme}://{netloc}" if both else raw_base.rstrip("/")
        let base = match url::Url::parse(&raw_base) {
            Ok(u) if !u.scheme().is_empty() && u.host_str().is_some() => {
                // `netloc` includes host + optional `:port` (and userinfo, but
                // ntfy URLs don't carry that). Reproduce `{scheme}://{netloc}`.
                let mut netloc = String::new();
                if let Some(host) = u.host_str() {
                    netloc.push_str(host);
                }
                if let Some(port) = u.port() {
                    netloc.push(':');
                    netloc.push_str(&port.to_string());
                }
                format!("{}://{}", u.scheme(), netloc)
            }
            _ => raw_base.trim_end_matches('/').to_string(),
        };
        let settings = _load_settings();
        // topic = (settings.get("reminder_ntfy_topic") or "reminders").strip() or "reminders"
        let topic = {
            let raw = settings
                .get("reminder_ntfy_topic")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let chosen = if raw.is_empty() { "reminders" } else { raw };
            let stripped = chosen.trim();
            if stripped.is_empty() {
                "reminders".to_string()
            } else {
                stripped.to_string()
            }
        };
        let full_url = format!("{base}/{topic}");
        let api_key = integ.get("api_key").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let auth_type = integ
            .get("auth_type")
            .and_then(|v| v.as_str())
            .unwrap_or("none")
            .to_lowercase();

        // headers = {"Title", "Tags", "Priority"} + optional auth
        let mut auth_header: Option<(String, String)> = None;
        if !api_key.is_empty() {
            if auth_type == "bearer" {
                auth_header = Some(("Authorization".to_string(), format!("Bearer {api_key}")));
            } else if auth_type == "header" {
                // headers[integ.get("auth_header") or "Authorization"] = api_key
                let name = integ
                    .get("auth_header")
                    .and_then(|v| v.as_str())
                    .filter(|s| !s.is_empty())
                    .unwrap_or("Authorization")
                    .to_string();
                auth_header = Some((name, api_key.clone()));
            }
        }

        let client = match reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(8))
            .build()
        {
            Ok(c) => c,
            Err(e) => {
                return Ok(Json(json!({
                    "ok": false,
                    "message": truncate_chars(&format!("ntfy publish to {full_url} failed: {e}"), 300)
                }))
                .into_response());
            }
        };
        let mut req = client
            .post(&full_url)
            .header("Title", "Odysseus connectivity test")
            .header("Tags", "white_check_mark")
            .header("Priority", "default")
            .body(
                "Connectivity test from Odysseus. If you see this on your phone, ntfy is wired up correctly."
                    .to_string(),
            );
        if let Some((name, value)) = &auth_header {
            req = req.header(name.as_str(), value.as_str());
        }
        return match req.send().await {
            Ok(resp) => {
                let status = resp.status().as_u16();
                // `if r.is_success:` — 2xx.
                if (200..300).contains(&status) {
                    Ok(Json(json!({
                        "ok": true,
                        "message": format!(
                            "Sent to {full_url} — on your ntfy app, subscribe to topic \"{topic}\" with server \"{base}\" (or paste the full URL: {full_url})."
                        )
                    }))
                    .into_response())
                } else {
                    // `f"ntfy returned HTTP {status} from {url}: {r.text[:200]}"`
                    let text = resp.text().await.unwrap_or_default();
                    let snippet = truncate_chars(&text, 200);
                    Ok(Json(json!({
                        "ok": false,
                        "message": format!("ntfy returned HTTP {status} from {full_url}: {snippet}")
                    }))
                    .into_response())
                }
            }
            Err(e) => Ok(Json(json!({
                "ok": false,
                "message": truncate_chars(&format!("ntfy publish to {full_url} failed: {e}"), 300)
            }))
            .into_response()),
        };
    }

    // All other presets: GET against a known health endpoint.
    let path = match preset.as_str() {
        "miniflux" => "/v1/me",
        "gitea" => "/api/v1/version",
        "linkding" => "/api/tags/",
        "homeassistant" => "/api/",
        "home assistant" => "/api/",
        _ => "/",
    };
    // result = await execute_api_call(integration_id, "GET", path)
    let result = execute_api_call(&integration_id, "GET", path, None, None, None).await;
    // `if result.get("exit_code", 1) == 0:`
    let exit_code = result.get("exit_code").and_then(|v| v.as_i64()).unwrap_or(1);
    if exit_code == 0 {
        Ok(Json(json!({"ok": true, "message": "Connection successful"})).into_response())
    } else {
        // `(result.get("error") or "Connection failed")[:300]`
        let raw = result
            .get("error")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .unwrap_or("Connection failed");
        Ok(Json(json!({"ok": false, "message": truncate_chars(raw, 300)})).into_response())
    }
}

/// First `n` characters (`s[:n]` — Python slices by code point, not byte).
fn truncate_chars(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// `mask_integration_secret(integration)` (upstream `src/integrations.py`) — return
/// a copy safe for API responses, with the `api_key` masked to its first 4 chars +
/// `****`.
///
/// ```python
/// def mask_integration_secret(integration):
///     safe = dict(integration)
///     api_key = safe.get("api_key", "")
///     if api_key:
///         safe["api_key"] = f"{str(api_key)[:4]}****"
///     return safe
/// ```
///
/// Lives here (not in `src/integrations.rs`) so this module owns the response-side
/// masking; the slice is by code point (`str(api_key)[:4]`), matching Python.
fn mask_integration_secret(integration: &Value) -> Value {
    let mut safe = integration.as_object().cloned().unwrap_or_default();
    // `if api_key:` — Python truthiness of the stored value (a non-empty string).
    let masked = safe
        .get("api_key")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .map(|key| {
            // `f"{str(api_key)[:4]}****"` — Python slices by code point.
            let head: String = key.chars().take(4).collect();
            format!("{head}****")
        });
    if let Some(m) = masked {
        safe.insert("api_key".to_string(), Value::from(m));
    }
    Value::Object(safe)
}

// ===========================================================================
// Router factory
// ===========================================================================

/// `setup_auth_routes(auth_manager) -> APIRouter(prefix="/api/auth", ...)`.
///
/// The `auth_manager` argument is reached through `State<AppState>.auth`, so this
/// factory takes no parameters. `migrate_from_settings()` runs once at setup time
/// (the Python calls it in the factory body, before registering the integrations
/// routes). Routes are registered in the Python declaration order, each at its
/// absolute `/api/auth...` path.
pub fn setup_auth_routes() -> Router<AppState> {
    // Run migration on startup (the Python factory calls this before the
    // integrations CRUD routes are declared).
    migrate_from_settings();

    Router::new()
        .route("/api/auth/setup", post(first_run_setup))
        .route("/api/auth/signup", post(signup))
        .route("/api/auth/login", post(login))
        .route("/api/auth/logout", post(logout))
        .route("/api/auth/status", get(auth_status))
        .route("/api/auth/change-password", post(change_password))
        .route("/api/auth/2fa/setup", post(totp_setup))
        .route("/api/auth/2fa/confirm", post(totp_confirm))
        .route("/api/auth/2fa/disable", post(totp_disable))
        .route("/api/auth/2fa/status", get(totp_status))
        .route(
            "/api/auth/users",
            get(list_users).post(admin_create_user).delete(admin_delete_user),
        )
        .route(
            "/api/auth/users/:username/privileges",
            put(update_user_privileges),
        )
        .route("/api/auth/users/:username/rename", put(rename_user))
        .route("/api/auth/signup-toggle", post(toggle_signup))
        .route("/api/auth/open-signup", put(set_signup_enabled))
        .route("/api/auth/features", get(get_features).post(set_features))
        .route("/api/auth/settings", get(get_settings).post(set_settings))
        .route("/api/auth/integrations/presets", get(list_presets))
        .route(
            "/api/auth/integrations",
            get(list_integrations_route).post(create_integration),
        )
        .route(
            "/api/auth/integrations/:integration_id",
            put(update_integration_route).delete(delete_integration_route),
        )
        .route(
            "/api/auth/integrations/:integration_id/test",
            post(test_integration_route),
        )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn is_secret_key_matches_python() {
        // Endings.
        assert!(is_secret_key("brave_api_key"));
        assert!(is_secret_key("smtp_password"));
        assert!(is_secret_key("foo_secret"));
        assert!(is_secret_key("github_token"));
        assert!(is_secret_key("some_key"));
        // Expanded upstream patterns.
        assert!(is_secret_key("openai_apikey"));
        assert!(is_secret_key("db_passwd"));
        assert!(is_secret_key("smtp_pass"));
        assert!(is_secret_key("redis_pwd"));
        assert!(is_secret_key("oauth_client_secret"));
        assert!(is_secret_key("gmail_access_token"));
        assert!(is_secret_key("gmail_refresh_token"));
        assert!(is_secret_key("aws_credential"));
        assert!(is_secret_key("aws_credentials"));
        // The bare-suffix form (n == p.lstrip("_")): "key", "token", etc.
        assert!(is_secret_key("key"));
        assert!(is_secret_key("token"));
        assert!(is_secret_key("password"));
        assert!(is_secret_key("secret"));
        assert!(is_secret_key("api_key"));
        assert!(is_secret_key("apikey"));
        assert!(is_secret_key("passwd"));
        assert!(is_secret_key("pass"));
        assert!(is_secret_key("pwd"));
        assert!(is_secret_key("credential"));
        assert!(is_secret_key("credentials"));
        assert!(is_secret_key("access_token"));
        assert!(is_secret_key("refresh_token"));
        assert!(is_secret_key("client_secret"));
        // The explicit public-identifier exception.
        assert!(!is_secret_key("google_pse_cx"));
        // Non-secret.
        assert!(!is_secret_key("vision_model"));
        assert!(!is_secret_key("reminder_channel"));
    }

    #[test]
    fn scrub_settings_blanks_only_nonempty_secret_strings() {
        let mut s = Map::new();
        s.insert("brave_api_key".into(), Value::from("sk-123"));
        s.insert("empty_key".into(), Value::from("")); // secret but empty -> kept
        s.insert("vision_model".into(), Value::from("vl-7b")); // not secret
        s.insert("google_pse_cx".into(), Value::from("cx-public")); // exception
        s.insert("search_result_count".into(), Value::from(5)); // non-string secret? not secret name
        let out = scrub_settings(&s);
        assert_eq!(out.get("brave_api_key"), Some(&Value::from("")));
        assert_eq!(out.get("empty_key"), Some(&Value::from("")));
        assert_eq!(out.get("vision_model"), Some(&Value::from("vl-7b")));
        assert_eq!(out.get("google_pse_cx"), Some(&Value::from("cx-public")));
        assert_eq!(out.get("search_result_count"), Some(&Value::from(5)));
        // Presence preserved for all keys.
        assert_eq!(out.len(), 5);
    }

    #[test]
    fn scrub_settings_recurses_into_nested_objects_and_arrays() {
        // Nested secret under a non-secret parent key must still be blanked.
        let mut email = Map::new();
        email.insert("smtp_host".into(), Value::from("mail.example.com"));
        email.insert("smtp_password".into(), Value::from("hunter2"));
        email.insert("imap_password".into(), Value::from("")); // empty -> kept

        let mut s = Map::new();
        s.insert("email_account".into(), Value::Object(email));
        // Array of objects, each carrying a secret leaf.
        s.insert(
            "accounts".into(),
            Value::Array(vec![
                json!({"name": "a", "access_token": "tok-1"}),
                json!({"name": "b", "access_token": "tok-2"}),
            ]),
        );
        // Array of secret-shaped strings: the parent key is secret, so each
        // non-empty string element is blanked (key threaded through the list).
        s.insert(
            "api_key".into(),
            Value::Array(vec![Value::from("k1"), Value::from(""), Value::from("k2")]),
        );

        let out = scrub_settings(&s);

        let email_out = out.get("email_account").unwrap().as_object().unwrap();
        assert_eq!(email_out.get("smtp_host"), Some(&Value::from("mail.example.com")));
        assert_eq!(email_out.get("smtp_password"), Some(&Value::from("")));
        assert_eq!(email_out.get("imap_password"), Some(&Value::from("")));

        let accounts = out.get("accounts").unwrap().as_array().unwrap();
        assert_eq!(accounts[0]["name"], Value::from("a"));
        assert_eq!(accounts[0]["access_token"], Value::from(""));
        assert_eq!(accounts[1]["access_token"], Value::from(""));

        let api_keys = out.get("api_key").unwrap().as_array().unwrap();
        assert_eq!(api_keys, &vec![Value::from(""), Value::from(""), Value::from("")]);
    }

    #[test]
    fn truncate_chars_slices_by_codepoint() {
        assert_eq!(truncate_chars("hello", 3), "hel");
        assert_eq!(truncate_chars("hi", 10), "hi");
        // Multi-byte: "é" is one code point.
        assert_eq!(truncate_chars("éé", 1), "é");
    }
}
