// routes/api_token_routes.rs  <- routes/api_token_routes.py
//! API Token management routes — `/api/tokens/*` (routes WAVE 2).
//!
//! Faithful translation of `routes/api_token_routes.py`'s `setup_api_token_routes`
//! factory. `require_admin`-gated handlers performing raw `rusqlite` CRUD on the
//! `api_tokens` table (mirroring the SQLAlchemy `ApiToken` ORM). No helper exists for
//! this table, so the SQL is inlined here exactly as the design directs.
//!
//! ## Scopes & Codex profiles
//! Commit 5939aec (the Codex Agent HTTP surface) added scoped-token issuance: a
//! closed [`ALLOWED_SCOPES`] set, named [`TOKEN_PROFILES`] bundles the Codex plugin
//! mints against (`codex_todos`, `codex_email_drafts`), a [`normalize_scopes`] helper
//! (profile-or-scopes resolution, dedupe, read-before-write ordering, 400 on unknown
//! profile/scope), a `GET /tokens/profiles` discovery endpoint, and a
//! `PATCH /tokens/{token_id}` editor. `POST /tokens` now accepts optional
//! `scopes`/`profile` form fields instead of always minting `chat`.
//!
//! ## Shape (the integration substrate)
//! * `setup_api_token_routes() -> Router<AppState>` mirrors the Python factory,
//!   which takes no args. The Python `APIRouter(prefix="/api", tags=["api_tokens"])`
//!   gives the absolute paths `/api/tokens` (GET, POST), `/api/tokens/profiles`
//!   (GET), and `/api/tokens/{token_id}` (PATCH, DELETE); the `.route(...)` calls use
//!   those verbatim (axum 0.7 `:token_id`). The static `/tokens/profiles` segment is
//!   matched ahead of the `:token_id` capture.
//! * Every handler is gated by `require_admin(request)` (called FIRST, before any
//!   work, matching FastAPI's `require_admin(request)` at the top of each handler).
//!   The gate reads the `X-Odysseus-Internal-Token` header and the stamped
//!   `current_user` from `Option<Extension<CurrentUser>>`, delegating to the
//!   foundation [`auth_adapter::require_admin`]. `Err` -> `HttpException(403,
//!   "Admin only")`.
//! * `raise HTTPException(s, d)` -> `return Err(HttpException::new(s, d))`; the
//!   `web`-gated `IntoResponse for HttpException` renders FastAPI's `{"detail": d}`.
//! * `name: str = Form("")` -> a `multipart/form-data` text field collected with the
//!   same manual collector the sibling `compare_routes` port uses (FastAPI `Form(...)`
//!   accepts both urlencoded and multipart). The `Form("")` default is "absent ->
//!   empty string".
//!
//! ## Token minting
//! `raw_token = "ody_" + secrets.token_urlsafe(32)` (the `ody_` prefix +
//! url-safe-base64 of 32 random bytes, no padding — [`crate::pysecrets::token_urlsafe`]);
//! `token_hash = bcrypt.hashpw(raw_token.encode(), bcrypt.gensalt()).decode()`
//! (`bcrypt::hash` at the default cost, matching `gensalt()`'s default work factor);
//! `token_id = str(uuid.uuid4())[:8]` (the first 8 chars of a v4 UUID string,
//! sliced by char). `token_prefix = raw_token[:8]` is the first 8 chars for display.
//!
//! ## The `invalidate_token_cache` hook (LIVE)
//! `_invalidate_cache(request)` does `getattr(request.app.state,
//! "invalidate_token_cache", None)` and calls it best-effort inside a `try/except`.
//! The axum `AppState` carries exactly this hook as an
//! `Arc<dyn Fn() + Send + Sync>` ([`AppState::invalidate_token_cache`]), wired in
//! `web/mod.rs run()` to the REAL Bearer token-cache dirty-flag setter
//! (`token_cache::invalidate()`). Calling it here marks the in-memory token cache
//! stale so a freshly minted or revoked token takes effect on the next Bearer
//! request — matching Python's `_token_cache_invalidate` (app.py:140-143), which the
//! best-effort call reaches via `request.app.state.invalidate_token_cache`.
//!
//! ## No path collision
//! `/api/tokens` and `/api/tokens/:token_id` are a prefix the inline `web/mod.rs`
//! subset never touches, so the aggregator merges this router without an axum
//! duplicate-`method`+`path` panic.


use std::collections::HashMap;

use axum::extract::{Multipart, Path, State};
use axum::http::HeaderMap;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, patch};
use axum::{Extension, Json, Router};
use serde_json::{json, Value};

use crate::core::middleware::INTERNAL_TOOL_HEADER;
use crate::routes::auth_adapter;
use crate::routes::{AppState, CurrentUser, HttpException};

/// `MAX_NAME_LEN = 100` — token names are truncated to 100 characters.
const MAX_NAME_LEN: usize = 100;
/// `DEFAULT_SCOPES = "chat"` — the comma-separated scope string a token gets at
/// creation (and the fallback when a stored `scopes` column is empty/NULL).
const DEFAULT_SCOPES: &str = "chat";

/// `ALLOWED_SCOPES` — the closed set of scopes a token may carry. Membership is
/// checked in [`normalize_scopes`]; an unknown scope -> `HTTPException(400)`. The
/// Python is a `set`; we keep the literal entries as a slice and test membership
/// by `.contains(&scope)` (the order here is irrelevant — it only feeds the
/// `sorted(...)` in the `profiles` endpoint via [`allowed_scopes_sorted`]).
const ALLOWED_SCOPES: &[&str] = &[
    "chat",
    "todos:read",
    "todos:write",
    "documents:read",
    "documents:write",
    "email:read",
    "email:draft",
    "email:send",
    "calendar:read",
    "calendar:write",
    "memory:read",
    "memory:write",
];

/// `TOKEN_PROFILES` — named scope bundles the Codex plugin (and the Settings UI)
/// issue tokens against. A `profile` form/JSON field selects one of these instead
/// of an explicit `scopes` string. Kept as an ordered slice of `(key, scopes)` so
/// the JSON object the `profiles` endpoint returns preserves Python `dict` order
/// (`chat`, `codex_todos`, `codex_email_drafts`) — Python 3.7+ dicts are ordered.
const TOKEN_PROFILES: &[(&str, &[&str])] = &[
    ("chat", &["chat"]),
    ("codex_todos", &["todos:read", "todos:write"]),
    (
        "codex_email_drafts",
        &["email:read", "email:draft", "documents:read", "documents:write"],
    ),
];

/// Lookup a profile's scope list by key, mirroring `TOKEN_PROFILES[profile_key]`.
fn token_profile(key: &str) -> Option<&'static [&'static str]> {
    TOKEN_PROFILES
        .iter()
        .find(|(k, _)| *k == key)
        .map(|(_, scopes)| *scopes)
}

/// `sorted(ALLOWED_SCOPES)` — the allowed-scope set rendered as a sorted `Vec`, for
/// the `/tokens/profiles` response. Python `sorted` on strings is lexicographic by
/// Unicode code point, which Rust's default `str` ordering matches.
fn allowed_scopes_sorted() -> Vec<&'static str> {
    let mut v: Vec<&'static str> = ALLOWED_SCOPES.to_vec();
    v.sort_unstable();
    v
}

/// `_normalize_scopes(scopes, profile)` — resolve a requested scope set into the
/// canonical, de-duplicated, read-before-write-ordered list a token is stored with.
///
/// ```python
/// def _normalize_scopes(scopes=None, profile=None) -> list[str]:
///     profile = profile if isinstance(profile, str) else None
///     profile_key = (profile or "").strip()
///     if profile_key:
///         if profile_key not in TOKEN_PROFILES: raise HTTPException(400, "Unknown token profile")
///         requested = list(TOKEN_PROFILES[profile_key])
///     elif isinstance(scopes, list):
///         requested = [str(s).strip() for s in scopes if str(s).strip()]
///     elif isinstance(scopes, str) and scopes:
///         requested = [s.strip() for s in scopes.replace(" ", ",").split(",") if s.strip()]
///     else:
///         requested = [DEFAULT_SCOPES]
///     normalized = []
///     for scope in requested:
///         if scope not in ALLOWED_SCOPES: raise HTTPException(400, f"Unknown token scope: {scope}")
///         if scope not in normalized: normalized.append(scope)
///     def ensure_before(write_scope, read_scope): ...  # insert read just before write
///     ensure_before("todos:write", "todos:read"); ...
///     return normalized or [DEFAULT_SCOPES]
/// ```
///
/// `scopes` arrives here pre-typed: a [`ScopesInput::Str`] (from the POST `Form`
/// field) or a [`ScopesInput::List`] / [`ScopesInput::Absent`] (from a PATCH JSON
/// body, where `payload.get("scopes")` may be a JSON array, a string, or missing).
/// The Python's `isinstance` ladder is reproduced by matching that enum, with the
/// profile branch taking precedence exactly as in the source.
fn normalize_scopes(scopes: ScopesInput, profile: Option<&str>) -> Result<Vec<String>, HttpException> {
    // `profile = profile if isinstance(profile, str) else None` then `(profile or "").strip()`.
    let profile_key = profile.unwrap_or("").trim();

    let requested: Vec<String> = if !profile_key.is_empty() {
        // `if profile_key not in TOKEN_PROFILES: raise HTTPException(400, ...)`.
        match token_profile(profile_key) {
            Some(scopes) => scopes.iter().map(|s| s.to_string()).collect(),
            None => return Err(HttpException::new(400, "Unknown token profile")),
        }
    } else {
        match scopes {
            // `isinstance(scopes, list)` — strip each element, drop falsy ones.
            ScopesInput::List(items) => items
                .iter()
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty())
                .collect(),
            // `isinstance(scopes, str) and scopes` — non-empty string: spaces become
            // commas, split on ',', strip each, drop empties.
            ScopesInput::Str(s) if !s.is_empty() => s
                .replace(' ', ",")
                .split(',')
                .map(str::trim)
                .filter(|s| !s.is_empty())
                .map(str::to_string)
                .collect(),
            // `else: requested = [DEFAULT_SCOPES]` (also the empty-string `str` case,
            // which fails the `and scopes` guard in Python and falls through to else).
            _ => vec![DEFAULT_SCOPES.to_string()],
        }
    };

    // `for scope in requested: if scope not in ALLOWED_SCOPES: raise; dedupe append`.
    let mut normalized: Vec<String> = Vec::new();
    for scope in requested {
        if !ALLOWED_SCOPES.contains(&scope.as_str()) {
            return Err(HttpException::new(400, format!("Unknown token scope: {scope}")));
        }
        if !normalized.contains(&scope) {
            normalized.push(scope);
        }
    }

    // `ensure_before(write, read)` — if `write` is present and `read` is not, insert
    // `read` immediately before `write` (so a read scope is always implied/listed
    // ahead of its write scope). No-op when `read` is already present.
    let ensure_before = |normalized: &mut Vec<String>, write_scope: &str, read_scope: &str| {
        let has_write = normalized.iter().any(|s| s == write_scope);
        let has_read = normalized.iter().any(|s| s == read_scope);
        if !has_write || has_read {
            return;
        }
        if let Some(idx) = normalized.iter().position(|s| s == write_scope) {
            normalized.insert(idx, read_scope.to_string());
        }
    };
    ensure_before(&mut normalized, "todos:write", "todos:read");
    ensure_before(&mut normalized, "documents:write", "documents:read");
    ensure_before(&mut normalized, "calendar:write", "calendar:read");
    ensure_before(&mut normalized, "memory:write", "memory:read");
    ensure_before(&mut normalized, "email:draft", "email:read");

    // `return normalized or [DEFAULT_SCOPES]` — empty list -> ["chat"].
    if normalized.is_empty() {
        Ok(vec![DEFAULT_SCOPES.to_string()])
    } else {
        Ok(normalized)
    }
}

/// The pre-typed `scopes` argument to [`normalize_scopes`], reproducing the Python
/// `scopes: str | list[str] | None` union after FastAPI/JSON has decided its type.
/// `Str` is the POST `Form("scopes")` field (a string, possibly empty); `List` is a
/// PATCH JSON array; `Absent` is a missing/`None`/non-string-non-array JSON value
/// (all of which fall to the Python `else` branch).
enum ScopesInput {
    Str(String),
    List(Vec<String>),
    Absent,
}

impl ScopesInput {
    /// Map a JSON value from a PATCH body's `payload.get("scopes")` onto the enum,
    /// mirroring the Python `isinstance` ladder: a JSON array -> `List` (each element
    /// stringified with `str(s)` — only string elements survive `str(s).strip()`
    /// meaningfully, but Python stringifies any element; we keep string elements and
    /// stringify the handful of scalars that round-trip, matching `str(s)`), a JSON
    /// string -> `Str`, anything else (`null`, number, object, missing) -> `Absent`.
    fn from_json(value: Option<&Value>) -> ScopesInput {
        match value {
            Some(Value::Array(items)) => ScopesInput::List(
                items
                    .iter()
                    .map(|v| match v {
                        // `str(s)` for the common cases the UI/clients send.
                        Value::String(s) => s.clone(),
                        Value::Bool(b) => b.to_string(),
                        Value::Number(n) => n.to_string(),
                        other => other.to_string(),
                    })
                    .collect(),
            ),
            Some(Value::String(s)) => ScopesInput::Str(s.clone()),
            _ => ScopesInput::Absent,
        }
    }
}

/// `setup_api_token_routes()` — assemble the API-token router.
///
/// app.py registers this as include-router #36. The Python
/// `APIRouter(prefix="/api", tags=["api_tokens"])` registers, in this order:
/// `GET /tokens`, `GET /tokens/profiles`, `POST /tokens`, `PATCH /tokens/{token_id}`,
/// `DELETE /tokens/{token_id}`. Under the `/api` prefix those resolve to
/// `/api/tokens` (GET, POST), `/api/tokens/profiles` (GET), and
/// `/api/tokens/:token_id` (PATCH, DELETE). The GET/POST and PATCH/DELETE share
/// their paths; `/tokens/profiles` is a *static* segment that axum matches ahead of
/// the `:token_id` capture, so a real token id can never shadow it. Registration
/// order is otherwise immaterial to matching — we keep the Python source order.
pub fn setup_api_token_routes() -> Router<AppState> {
    Router::new()
        // `@router.get("/tokens")` + `@router.post("/tokens")`.
        .route("/api/tokens", get(list_tokens).post(create_token))
        // `@router.get("/tokens/profiles")` — static segment, matched before the
        // `:token_id` capture below.
        .route("/api/tokens/profiles", get(token_profiles))
        // `@router.patch("/tokens/{token_id}")` + `@router.delete("/tokens/{token_id}")`.
        .route(
            "/api/tokens/:token_id",
            patch(update_token).delete(delete_token),
        )
}

/// `GET /api/tokens` — list all API tokens (admin only, no plaintext token).
///
/// ```python
/// @router.get("/tokens")
/// def list_tokens(request: Request):
///     require_admin(request)
///     with get_db_session() as db:
///         tokens = db.query(ApiToken).all()
///         return [ {...} for t in tokens ]
/// ```
///
/// The dict per token preserves the Python key order exactly: `id`, `name`, `owner`,
/// `token_prefix`, `scopes`, `is_active`, `last_used_at`, `created_at`. `scopes` is
/// derived from the stored column with the `(... or DEFAULT_SCOPES)` empty-string
/// fallback, split on `,`, each entry stripped, empties dropped. `last_used_at` /
/// `created_at` are `.isoformat()` (no `Z` suffix — the Python uses plain
/// `isoformat()`), or `null` when absent.
async fn list_tokens(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;

    let conn = session_local()?;
    // `tokens = db.query(ApiToken).all()` — no explicit ordering in the Python.
    let mut stmt = conn
        .prepare(
            "SELECT id, name, owner, token_prefix, scopes, is_active, last_used_at, created_at \
             FROM api_tokens",
        )
        .map_err(db_500)?;

    let map_row = |r: &rusqlite::Row<'_>| -> rusqlite::Result<Value> {
        let id: String = r.get(0)?;
        let name: Option<String> = r.get(1)?;
        // `getattr(t, "owner", None)` — the column may be NULL.
        let owner: Option<String> = r.get(2)?;
        let token_prefix: Option<String> = r.get(3)?;
        // `getattr(t, "scopes", "")` — may be NULL/empty.
        let scopes_raw: Option<String> = r.get(4)?;
        // `is_active` is stored as 0/1 (SQLAlchemy Boolean); read as bool.
        let is_active: bool = r.get(5)?;
        let last_used_at: Option<String> = r.get(6)?;
        let created_at: Option<String> = r.get(7)?;

        Ok(json!({
            "id": id,
            "name": name,
            "owner": owner,
            "token_prefix": token_prefix,
            "scopes": split_scopes(scopes_raw.as_deref()),
            "is_active": is_active,
            // `t.last_used_at.isoformat() if t.last_used_at else None`.
            "last_used_at": iso_or_null(last_used_at.as_deref()),
            // `t.created_at.isoformat() if t.created_at else None`.
            "created_at": iso_or_null(created_at.as_deref()),
        }))
    };

    let rows: Vec<Value> = stmt
        .query_map([], map_row)
        .map_err(db_500)?
        .collect::<rusqlite::Result<Vec<_>>>()
        .map_err(db_500)?;

    Ok(Json(Value::Array(rows)).into_response())
}

/// `GET /api/tokens/profiles` — the named scope bundles + the full allowed-scope set
/// (admin only). The Settings UI / Codex install flow reads this to populate its
/// profile picker.
///
/// ```python
/// @router.get("/tokens/profiles")
/// def token_profiles(request: Request):
///     require_admin(request)
///     return {"profiles": TOKEN_PROFILES, "allowed_scopes": sorted(ALLOWED_SCOPES)}
/// ```
///
/// `profiles` is a JSON object keyed by profile name (insertion order preserved via
/// the ordered `TOKEN_PROFILES` slice + a `serde_json::Map`); `allowed_scopes` is the
/// lexicographically sorted allowed-scope list (`sorted(ALLOWED_SCOPES)`).
async fn token_profiles(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;

    // `TOKEN_PROFILES` as an ordered JSON object: {name: [scopes...]}.
    let mut profiles = serde_json::Map::new();
    for (key, scopes) in TOKEN_PROFILES {
        profiles.insert((*key).to_string(), json!(scopes));
    }

    Ok(Json(json!({
        "profiles": Value::Object(profiles),
        "allowed_scopes": allowed_scopes_sorted(),
    }))
    .into_response())
}

/// `POST /api/tokens` — mint a new API token (admin only).
///
/// ```python
/// @router.post("/tokens")
/// def create_token(request, name=Form(""), scopes=Form(None), profile=Form(None)):
///     require_admin(request)
///     name = name.strip()[:MAX_NAME_LEN]
///     if not name: raise HTTPException(400, "Token name is required")
///     owner = get_current_user(request)
///     scope_list = _normalize_scopes(scopes, profile)
///     scopes_value = ",".join(scope_list)
///     raw_token = "ody_" + secrets.token_urlsafe(32)
///     token_hash = bcrypt.hashpw(raw_token.encode(), bcrypt.gensalt()).decode()
///     token_id = str(uuid.uuid4())[:8]
///     with get_db_session() as db:
///         db.add(ApiToken(id=token_id, owner=owner, name=name, token_hash=...,
///                         token_prefix=raw_token[:8], scopes=scopes_value, is_active=True))
///     _invalidate_cache(request)
///     return {"id": ..., "name": ..., "owner": ..., "token": raw_token,
///             "token_prefix": raw_token[:8], "scopes": scope_list}
/// ```
///
/// The full plaintext token is returned **once**, here — only the hash is stored.
/// `scopes` / `profile` are optional `Form(None)` fields: `profile` selects a named
/// [`TOKEN_PROFILES`] bundle (taking precedence), else `scopes` is a comma/space-
/// separated string; absent -> the `DEFAULT_SCOPES` fallback. Resolution (including
/// the 400s for unknown profile/scope) goes through [`normalize_scopes`].
async fn create_token(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    mp: Multipart,
) -> Result<Response, HttpException> {
    // `require_admin(request)` first — FastAPI resolves the dependency before the
    // body, so we gate before reading the form. (Reading `user` here, before it is
    // moved into `owner` below, mirrors the Python's single `current_user` source.)
    admin_gate(&state, &headers, user.as_deref())?;

    // `name: str = Form("")` — defaulted form field. A missing field is `""`.
    let form = parse_form(mp).await;
    let raw_name = form.get("name").map(String::as_str).unwrap_or("");

    // `name = name.strip()[:MAX_NAME_LEN]` — strip, then slice to 100 *characters*
    // (Python str slice is by code point), then the empty check.
    let name: String = raw_name.trim().chars().take(MAX_NAME_LEN).collect();
    // `if not name: raise HTTPException(400, "Token name is required")`.
    if name.is_empty() {
        return Err(HttpException::new(400, "Token name is required"));
    }

    // `owner = get_current_user(request)` — the stamped username, or `None` when auth
    // is disabled / unresolved (the load-bearing anonymous case: stored as NULL).
    let owner: Option<String> = user.map(|Extension(CurrentUser(u))| u);

    // `scopes: str = Form(None)` / `profile: str = Form(None)` — optional form fields.
    // An absent field and a present-but-empty field both fall to the Python `else`
    // branch (and an empty `profile` is skipped after `.strip()`), so we treat both
    // uniformly via the form map (`None`/`""`).
    let scope_list = normalize_scopes(
        ScopesInput::Str(form.get("scopes").cloned().unwrap_or_default()),
        form.get("profile").map(String::as_str),
    )?;
    // `scopes_value = ",".join(scope_list)`.
    let scopes_value = scope_list.join(",");

    // `raw_token = "ody_" + secrets.token_urlsafe(32)`.
    let raw_token = format!("ody_{}", crate::pysecrets::token_urlsafe(32));
    // `token_hash = bcrypt.hashpw(raw_token.encode(), bcrypt.gensalt()).decode()`.
    // `bcrypt::hash(_, DEFAULT_COST)` matches `gensalt()`'s default work factor.
    let token_hash = match bcrypt::hash(&raw_token, bcrypt::DEFAULT_COST) {
        Ok(h) => h,
        Err(e) => return Err(HttpException::new(500, format!("Failed to hash token: {e}"))),
    };
    // `token_id = str(uuid.uuid4())[:8]` — first 8 chars of a v4 UUID string.
    let token_id: String = uuid::Uuid::new_v4().to_string().chars().take(8).collect();
    // `raw_token[:8]` — first 8 chars for display.
    let token_prefix: String = raw_token.chars().take(8).collect();

    let conn = session_local()?;
    // TimestampMixin: `created_at`/`updated_at` default to `datetime.utcnow` at flush;
    // `last_used_at` stays NULL (not set by `db.add(ApiToken(...))`).
    let now = crate::pydatetime::utcnow_naive_iso();
    // `db.add(ApiToken(id, owner, name, token_hash, token_prefix, scopes=scopes_value,
    //                  is_active=True))`. A failure surfaces as a 500.
    conn.execute(
        "INSERT INTO api_tokens \
           (id, owner, name, token_hash, token_prefix, scopes, is_active, created_at, updated_at) \
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, 1, ?7, ?7)",
        rusqlite::params![
            token_id,
            owner,
            name,
            token_hash,
            token_prefix,
            scopes_value,
            now,
        ],
    )
    .map_err(db_500)?;

    // `_invalidate_cache(request)` — marks the live Bearer token cache dirty (see
    // header), so the newly minted token is visible on the next Bearer request.
    (state.invalidate_token_cache)();

    // return { ... } — key order matches the Python dict literal exactly.
    Ok(Json(json!({
        "id": token_id,
        "name": name,
        "owner": owner,
        "token": raw_token,
        "token_prefix": token_prefix,
        // `scope_list` — the normalized scopes this token was minted with.
        "scopes": scope_list,
    }))
    .into_response())
}

/// `PATCH /api/tokens/{token_id}` — update a token's name and/or scopes (admin only).
///
/// ```python
/// @router.patch("/tokens/{token_id}")
/// async def update_token(request: Request, token_id: str):
///     require_admin(request)
///     try: payload = await request.json()
///     except Exception: payload = {}
///     scope_list = _normalize_scopes(payload.get("scopes"))
///     scopes_value = ",".join(scope_list)
///     with get_db_session() as db:
///         token = db.query(ApiToken).filter(ApiToken.id == token_id).first()
///         if not token: raise HTTPException(404, "Token not found")
///         if isinstance(payload.get("name"), str) and payload["name"].strip():
///             token.name = payload["name"].strip()[:MAX_NAME_LEN]
///         token.scopes = scopes_value
///         db.add(token)
///         response = {"id": token_id, "name": ..., "owner": ..., "token_prefix": ..., "scopes": scope_list}
///     _invalidate_cache(request)
///     return response
/// ```
///
/// The body is parsed best-effort: a JSON parse failure yields `{}` (so a missing /
/// malformed body still re-normalizes scopes to `DEFAULT_SCOPES`, the
/// [`normalize_scopes`] fallback). `name` is only touched when it is a non-empty
/// string after `.strip()`. `scopes` is *always* rewritten from the normalized list.
/// The plaintext token is **not** returned (it was only ever shown at creation).
async fn update_token(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Path(token_id): Path<String>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;

    // `try: payload = await request.json() except: payload = {}` — a parse failure (or
    // empty body) degrades to an empty object, exactly like the Python except-clause.
    let payload: Value = serde_json::from_slice(&raw_body).unwrap_or_else(|_| json!({}));

    // `scope_list = _normalize_scopes(payload.get("scopes"))` — no profile here, so the
    // scopes union (array | string | None) is taken straight from the body.
    let scope_list = normalize_scopes(ScopesInput::from_json(payload.get("scopes")), None)?;
    // `scopes_value = ",".join(scope_list)`.
    let scopes_value = scope_list.join(",");

    // `if isinstance(payload.get("name"), str) and payload["name"].strip()` — only a
    // non-empty (post-strip) *string* name triggers an update; truncated to 100 chars.
    let new_name: Option<String> = match payload.get("name") {
        Some(Value::String(s)) if !s.trim().is_empty() => {
            Some(s.trim().chars().take(MAX_NAME_LEN).collect())
        }
        _ => None,
    };

    let conn = session_local()?;
    // `token = db.query(ApiToken).filter(ApiToken.id == token_id).first()` — fetch the
    // current row (we need owner/token_prefix for the response, and existence for 404).
    let existing: Option<(Option<String>, Option<String>, Option<String>)> = conn
        .query_row(
            "SELECT name, owner, token_prefix FROM api_tokens WHERE id = ?1",
            rusqlite::params![token_id],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
        )
        .map(Some)
        .or_else(|e| match e {
            rusqlite::Error::QueryReturnedNoRows => Ok(None),
            other => Err(db_500(other)),
        })?;
    // `if not token: raise HTTPException(404, "Token not found")`.
    let (cur_name, owner, token_prefix) = match existing {
        Some(t) => t,
        None => return Err(HttpException::new(404, "Token not found")),
    };

    // `token.name = ...` (conditional) + `token.scopes = scopes_value` then flush.
    // The response `name` reflects the (possibly) updated value, like `getattr(token,
    // "name", "")` after the in-session assignment.
    let final_name: Option<String> = new_name.clone().or(cur_name);
    if let Some(ref n) = new_name {
        conn.execute(
            "UPDATE api_tokens SET name = ?1, scopes = ?2 WHERE id = ?3",
            rusqlite::params![n, scopes_value, token_id],
        )
        .map_err(db_500)?;
    } else {
        conn.execute(
            "UPDATE api_tokens SET scopes = ?1 WHERE id = ?2",
            rusqlite::params![scopes_value, token_id],
        )
        .map_err(db_500)?;
    }

    // `_invalidate_cache(request)` — a scope change must take effect on the next Bearer
    // request, so mark the live token cache dirty.
    (state.invalidate_token_cache)();

    // return { ... } — key order matches the Python dict literal exactly. The Python
    // `getattr(token, "name"|"token_prefix", "")` defaults never fire (the ORM attrs
    // always exist), so a NULL column surfaces as the attribute value `None` -> JSON
    // `null`; we therefore serialize the `Option`s directly rather than `""`.
    Ok(Json(json!({
        "id": token_id,
        "name": final_name,
        "owner": owner,
        "token_prefix": token_prefix,
        "scopes": scope_list,
    }))
    .into_response())
}

/// `DELETE /api/tokens/{token_id}` — revoke an API token (admin only).
///
/// ```python
/// @router.delete("/tokens/{token_id}")
/// def delete_token(request: Request, token_id: str):
///     require_admin(request)
///     with get_db_session() as db:
///         deleted = db.query(ApiToken).filter(ApiToken.id == token_id).delete()
///         if not deleted: raise HTTPException(404, "Token not found")
///     _invalidate_cache(request)
///     return {"status": "deleted"}
/// ```
///
/// SQLAlchemy's `.delete()` returns the affected row count; `if not deleted` is the
/// 404 branch. `rusqlite::execute` returns the same count, so `count == 0` -> 404.
async fn delete_token(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Path(token_id): Path<String>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;

    let conn = session_local()?;
    // `deleted = db.query(ApiToken).filter(ApiToken.id == token_id).delete()`.
    let deleted = conn
        .execute(
            "DELETE FROM api_tokens WHERE id = ?1",
            rusqlite::params![token_id],
        )
        .map_err(db_500)?;
    // `if not deleted: raise HTTPException(404, "Token not found")`.
    if deleted == 0 {
        return Err(HttpException::new(404, "Token not found"));
    }

    // `_invalidate_cache(request)` — marks the live Bearer token cache dirty (see
    // header), so the revoked token stops authenticating on the next Bearer request.
    (state.invalidate_token_cache)();

    Ok(Json(json!({"status": "deleted"})).into_response())
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// `require_admin(request)` (core/middleware.py) — the per-handler admin gate.
///
/// Reads the `X-Odysseus-Internal-Token` header and the stamped
/// `request.state.current_user`, then delegates to the foundation
/// [`auth_adapter::require_admin`] (which wraps `core::middleware::require_admin`).
/// `Err` -> `HttpException(403, "Admin only")`.
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

/// `[s.strip() for s in (getattr(t, "scopes", "") or DEFAULT_SCOPES).split(",") if s.strip()]`.
///
/// The stored `scopes` column (or `""`/NULL) falls back to `DEFAULT_SCOPES` when
/// falsy, is split on `,`, each entry is stripped, and empty entries are dropped.
fn split_scopes(stored: Option<&str>) -> Vec<String> {
    // `(value or DEFAULT_SCOPES)` — None/empty -> "chat" (Python `or` on a falsy str).
    let value = match stored {
        Some(s) if !s.is_empty() => s,
        _ => DEFAULT_SCOPES,
    };
    value
        .split(',')
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .collect()
}

/// `dt.isoformat() if dt else None` — render a stored SQLite datetime with Python's
/// `isoformat` (plain, no `Z` suffix), or `null` when absent/empty.
fn iso_or_null(stored: Option<&str>) -> Value {
    match stored.filter(|s| !s.is_empty()) {
        Some(s) => json!(crate::pydatetime::to_isoformat(s)),
        None => Value::Null,
    }
}

/// Collect all `multipart/form-data` text fields into a map — the same collector the
/// sibling `compare_routes` port uses. FastAPI `Form(...)` accepts both
/// `application/x-www-form-urlencoded` and `multipart/form-data`; this reads the
/// multipart shape the token-management UI posts (`FormData`).
async fn parse_form(mut mp: Multipart) -> HashMap<String, String> {
    let mut out = HashMap::new();
    while let Ok(Some(field)) = mp.next_field().await {
        let name = field.name().unwrap_or("").to_string();
        if name.is_empty() {
            continue;
        }
        if let Ok(text) = field.text().await {
            out.insert(name, text);
        }
    }
    out
}

/// Open a DB connection (`get_db_session()` -> `SessionLocal()`), mapping a failure
/// to a 500 (FastAPI's default handler for an unhandled DB error).
fn session_local() -> Result<rusqlite::Connection, HttpException> {
    crate::core::database::session_local().map_err(db_500)
}

/// Map a `rusqlite::Error` to a 500 `HttpException`, the way an unhandled exception
/// inside a FastAPI handler surfaces.
fn db_500(e: rusqlite::Error) -> HttpException {
    crate::pylog::error(&format!("api_token_routes DB error: {e}"));
    HttpException::new(500, "Internal Server Error")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn split_scopes_falls_back_and_strips_and_filters() {
        // `(value or DEFAULT_SCOPES)` — None/empty -> ["chat"].
        assert_eq!(split_scopes(None), vec!["chat".to_string()]);
        assert_eq!(split_scopes(Some("")), vec!["chat".to_string()]);
        // A stored multi-scope string: split on ',', strip each, drop empties.
        assert_eq!(
            split_scopes(Some("chat, memory ,, tools")),
            vec![
                "chat".to_string(),
                "memory".to_string(),
                "tools".to_string()
            ]
        );
        // A single scope round-trips.
        assert_eq!(split_scopes(Some("admin")), vec!["admin".to_string()]);
        // All-empty after stripping -> empty list (matches the `if s.strip()` filter).
        assert_eq!(split_scopes(Some(" , , ")), Vec::<String>::new());
    }

    #[test]
    fn default_scopes_split_is_single_chat() {
        // `DEFAULT_SCOPES.split(",")` in the create response — plain split, no filter.
        let scopes: Vec<&str> = DEFAULT_SCOPES.split(',').collect();
        assert_eq!(scopes, vec!["chat"]);
    }

    #[test]
    fn name_trim_truncate_and_empty_check() {
        // `name.strip()[:MAX_NAME_LEN]` then `if not name`.
        let resolve = |raw: &str| -> String {
            raw.trim().chars().take(MAX_NAME_LEN).collect()
        };
        assert_eq!(resolve("  My Token  "), "My Token");
        assert_eq!(resolve(""), "");
        assert_eq!(resolve("   "), "");
        // Truncation is by character to exactly 100.
        let long = "x".repeat(150);
        let truncated = resolve(&long);
        assert_eq!(truncated.chars().count(), MAX_NAME_LEN);
    }

    #[test]
    fn raw_token_has_ody_prefix_and_prefix_is_first_8() {
        // `raw_token = "ody_" + token_urlsafe(32)`; `token_prefix = raw_token[:8]`.
        let raw_token = format!("ody_{}", crate::pysecrets::token_urlsafe(32));
        assert!(raw_token.starts_with("ody_"));
        let prefix: String = raw_token.chars().take(8).collect();
        assert_eq!(prefix.chars().count(), 8);
        // The prefix carries the `ody_` marker (4 chars) + 4 token chars.
        assert!(prefix.starts_with("ody_"));
    }

    #[test]
    fn token_id_is_first_8_uuid_chars() {
        // `str(uuid.uuid4())[:8]`.
        let token_id: String = uuid::Uuid::new_v4().to_string().chars().take(8).collect();
        assert_eq!(token_id.chars().count(), 8);
        // No hyphen in the first 8 chars of a canonical UUID (8-4-4-4-12).
        assert!(!token_id.contains('-'));
    }

    #[test]
    fn iso_or_null_renders_or_nulls() {
        assert_eq!(iso_or_null(None), Value::Null);
        assert_eq!(iso_or_null(Some("")), Value::Null);
        // Plain isoformat (T separator, no Z suffix — distinct from signatures).
        assert_eq!(
            iso_or_null(Some("2026-06-01 12:30:00")),
            json!("2026-06-01T12:30:00")
        );
        // Microseconds preserved when present.
        assert_eq!(
            iso_or_null(Some("2026-06-01 12:30:00.123456")),
            json!("2026-06-01T12:30:00.123456")
        );
    }

    #[test]
    fn bcrypt_hash_verifies_against_raw_token() {
        // `bcrypt.hashpw(raw_token, gensalt())` then later `bcrypt.checkpw`.
        let raw_token = format!("ody_{}", crate::pysecrets::token_urlsafe(32));
        let hash = bcrypt::hash(&raw_token, bcrypt::DEFAULT_COST).unwrap();
        assert!(bcrypt::verify(&raw_token, &hash).unwrap());
        assert!(!bcrypt::verify("wrong_token", &hash).unwrap());
    }

    #[test]
    fn router_mounts_all_absolute_paths() {
        // The factory yields a `Router<AppState>` mergeable with the inline subset
        // (no duplicate method+path); the token paths (`/api/tokens`,
        // `/api/tokens/profiles`, `/api/tokens/:token_id`) are disjoint, so building
        // the router never panics.
        let base: Router<AppState> = Router::new();
        let _merged: Router<AppState> = base.merge(setup_api_token_routes());
    }

    // --- 5939aec: Codex scoped-token additions ---------------------------------

    #[test]
    fn normalize_scopes_defaults_to_chat() {
        // Absent scopes + no profile -> the `else: requested = [DEFAULT_SCOPES]` branch.
        assert_eq!(
            normalize_scopes(ScopesInput::Absent, None).unwrap(),
            vec!["chat".to_string()]
        );
        // An empty `scopes` string also falls through to the default (fails `and scopes`).
        assert_eq!(
            normalize_scopes(ScopesInput::Str(String::new()), None).unwrap(),
            vec!["chat".to_string()]
        );
    }

    #[test]
    fn normalize_scopes_string_splits_on_comma_and_space() {
        // `scopes.replace(" ", ",").split(",")`, strip, drop empties, then ALLOWED check.
        assert_eq!(
            normalize_scopes(ScopesInput::Str("todos:read todos:write".into()), None).unwrap(),
            vec!["todos:read".to_string(), "todos:write".to_string()]
        );
        // Mixed separators + empties.
        assert_eq!(
            normalize_scopes(ScopesInput::Str(" chat ,, todos:read ".into()), None).unwrap(),
            vec!["chat".to_string(), "todos:read".to_string()]
        );
    }

    #[test]
    fn normalize_scopes_dedupes_preserving_first_position() {
        // `if scope not in normalized: normalized.append(scope)`.
        assert_eq!(
            normalize_scopes(ScopesInput::Str("chat,chat,todos:read".into()), None).unwrap(),
            vec!["chat".to_string(), "todos:read".to_string()]
        );
    }

    #[test]
    fn normalize_scopes_inserts_read_before_write() {
        // `ensure_before("todos:write", "todos:read")` — read inserted just before write.
        assert_eq!(
            normalize_scopes(ScopesInput::Str("todos:write".into()), None).unwrap(),
            vec!["todos:read".to_string(), "todos:write".to_string()]
        );
        // `email:draft` implies `email:read` before it.
        assert_eq!(
            normalize_scopes(ScopesInput::Str("email:draft".into()), None).unwrap(),
            vec!["email:read".to_string(), "email:draft".to_string()]
        );
        // No-op when the read scope is already present (order untouched).
        assert_eq!(
            normalize_scopes(ScopesInput::Str("documents:read,documents:write".into()), None)
                .unwrap(),
            vec!["documents:read".to_string(), "documents:write".to_string()]
        );
    }

    #[test]
    fn normalize_scopes_rejects_unknown_scope() {
        // `if scope not in ALLOWED_SCOPES: raise HTTPException(400, ...)`.
        let err = normalize_scopes(ScopesInput::Str("bogus".into()), None).unwrap_err();
        assert_eq!(err.status_code, 400);
        assert_eq!(err.detail, "Unknown token scope: bogus");
    }

    #[test]
    fn normalize_scopes_profile_takes_precedence_and_validates() {
        // A profile selects its bundle, ignoring any `scopes` argument.
        assert_eq!(
            normalize_scopes(ScopesInput::Str("chat".into()), Some("codex_todos")).unwrap(),
            vec!["todos:read".to_string(), "todos:write".to_string()]
        );
        // `codex_email_drafts` expands to its declared bundle (already read-before-write).
        assert_eq!(
            normalize_scopes(ScopesInput::Absent, Some("codex_email_drafts")).unwrap(),
            vec![
                "email:read".to_string(),
                "email:draft".to_string(),
                "documents:read".to_string(),
                "documents:write".to_string(),
            ]
        );
        // Whitespace-only profile is treated as absent (`.strip()` -> "").
        assert_eq!(
            normalize_scopes(ScopesInput::Str("chat".into()), Some("   ")).unwrap(),
            vec!["chat".to_string()]
        );
        // An unknown profile is a 400.
        let err = normalize_scopes(ScopesInput::Absent, Some("nope")).unwrap_err();
        assert_eq!(err.status_code, 400);
        assert_eq!(err.detail, "Unknown token profile");
    }

    #[test]
    fn scopes_input_from_json_maps_the_isinstance_ladder() {
        // JSON array -> List (stringified per `str(s)`), strings preserved.
        let arr = json!(["todos:read", "todos:write"]);
        assert_eq!(
            normalize_scopes(ScopesInput::from_json(Some(&arr)), None).unwrap(),
            vec!["todos:read".to_string(), "todos:write".to_string()]
        );
        // JSON string -> Str.
        let s = json!("chat");
        assert_eq!(
            normalize_scopes(ScopesInput::from_json(Some(&s)), None).unwrap(),
            vec!["chat".to_string()]
        );
        // null / missing / non-string-non-array -> Absent -> default.
        let n = Value::Null;
        assert_eq!(
            normalize_scopes(ScopesInput::from_json(Some(&n)), None).unwrap(),
            vec!["chat".to_string()]
        );
        assert_eq!(
            normalize_scopes(ScopesInput::from_json(None), None).unwrap(),
            vec!["chat".to_string()]
        );
    }

    #[test]
    fn allowed_scopes_sorted_is_lexicographic() {
        let sorted = allowed_scopes_sorted();
        // Sorted ascending and contains the full closed set.
        let mut expected = ALLOWED_SCOPES.to_vec();
        expected.sort_unstable();
        assert_eq!(sorted, expected);
        assert_eq!(sorted.len(), ALLOWED_SCOPES.len());
        // First entry lexicographically is "calendar:read".
        assert_eq!(sorted.first(), Some(&"calendar:read"));
    }

    #[test]
    fn token_profiles_lookup_matches_python_dict() {
        assert_eq!(token_profile("chat"), Some(&["chat"][..]));
        assert_eq!(
            token_profile("codex_todos"),
            Some(&["todos:read", "todos:write"][..])
        );
        assert_eq!(token_profile("missing"), None);
    }
}
