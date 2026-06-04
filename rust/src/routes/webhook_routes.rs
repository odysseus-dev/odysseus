// routes/webhook_routes.rs  <- routes/webhook_routes.py
//! Webhook, API Token, and sync chat routes — `/api/webhooks/*` + `/api/v1/chat`
//! (routes WAVE 7).
//!
//! Faithful translation of `routes/webhook_routes.py`'s `setup_webhook_routes`
//! factory. The Python factory takes `(webhook_manager, auth_manager,
//! session_manager=None, api_key_manager=None)` and registers, under
//! `APIRouter(prefix="/api", tags=["webhooks"])`:
//!
//! * `GET    /api/webhooks`               — list all webhooks (admin)
//! * `POST   /api/webhooks`               — create a webhook (admin)
//! * `POST   /api/webhooks/{id}/test`     — fire a test ping (admin)
//! * `PATCH  /api/webhooks/{id}`          — toggle `is_active` (admin)
//! * `DELETE /api/webhooks/{id}`          — delete a webhook (admin)
//! * `POST   /api/v1/chat`                — sync chat (API-token-gated)
//!
//! The captured `webhook_manager` / `api_key_manager` are reached via
//! [`AppState`] (`state.webhook_manager`, `state.api_key_manager`), mirroring the
//! sibling wave-4/5 factories whose manager args are threaded through `AppState`.
//! `auth_manager`/`session_manager` are likewise on `AppState` but, for the ported
//! CRUD surface, only `require_admin` (via `state.auth`) is exercised.
//!
//! ## CRUD substrate (raw rusqlite over the `webhooks` table)
//! No `webhooks`-table helper exists, so the SQL is inlined here exactly as the
//! design directs (mirroring `core::database`'s SQLAlchemy `Webhook` ORM). Each
//! handler is gated by `require_admin(request)` (called FIRST, before any work,
//! matching FastAPI's `_require_admin(request)` at the top of each handler — the
//! `core.middleware.require_admin` import). `Err` -> `HttpException(403,
//! "Admin only")`. `raise HTTPException(s, d)` -> `Err(HttpException::new(s, d))`;
//! the `web`-gated `IntoResponse for HttpException` renders `{"detail": d}`.
//!
//! ## Secret encryption at rest
//! On create, a non-empty `secret` is encrypted with the SAME Fernet key as API
//! keys (`api_key_manager.encrypt_api_key`) — `state.api_key_manager` is always
//! present on the Rust `AppState` (`Arc<APIKeyManager>`), so the Python's
//! `elif secret_val:` plaintext-fallback branch (only taken when no
//! `api_key_manager` was passed) is unreachable in the live wiring, exactly as in
//! the live Python app (where `api_key_manager` is always passed). The stored
//! ciphertext is decrypted at delivery time by the already-ported
//! [`crate::src::webhook_manager::WebhookManager`].
//!
//! ## `POST /api/v1/chat` (API-token-GATED — fully ported)
//! The Python handler's first three lines are the API-token gate:
//! `if not getattr(request.state, "api_token", False): raise HTTPException(403,
//! "This endpoint requires an API token")`, then `scopes = set(...api_token_scopes
//! or []); if "chat" not in scopes: raise HTTPException(403, "API token is not
//! scoped for chat")`. The API-token Bearer middleware that stamps those fields is
//! ported (`web::auth_gate`'s Bearer branch -> the [`ApiToken`] extension), so this
//! handler reads `Option<Extension<ApiToken>>` (absent => `present = false`, matching
//! Python's `getattr(..., False)`) and reproduces the two gate raises. Past the gate
//! the full sync-chat pipeline is ported: session resume (with the ownership SECURITY
//! check) / direct-`api_key` endpoint creation / first-enabled-`ModelEndpoint`
//! fallback + `auto` model discovery (the `/models` probe via reqwest, `500` on
//! failure) / `crate::src::llm_core::llm_call_async` for the non-streaming reply /
//! `save_sessions` / the fire-and-forget `chat.completed` webhook, returning
//! `{"response", "session_id", "model"}`. It reuses `AppState.sessions` /
//! `AppState.webhook_manager` / the ported `SessionManager` + `llm_call_async`.
//!
//! ## No path collision
//! `/api/webhooks*` and `/api/v1/chat` are prefixes the inline `web/mod.rs` subset
//! never touches, so the aggregator merges this router without an axum
//! duplicate-`method`+`path` panic.


use std::collections::HashMap;

use axum::extract::{Multipart, Path, State};
use axum::http::HeaderMap;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Extension, Json, Router};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::core::middleware::INTERNAL_TOOL_HEADER;
use crate::core::models::ChatMessage;
use crate::routes::auth_adapter;
use crate::routes::{ApiToken, AppState, CurrentUser, HttpException};
use crate::src::endpoint_resolver::{build_chat_url, build_headers, build_models_url, normalize_base};
use crate::src::url_security::validate_public_http_url;
use crate::src::webhook_manager::{validate_events, validate_webhook_url};

/// `MAX_NAME_LEN = 100`.
const MAX_NAME_LEN: usize = 100;
/// `MAX_URL_LEN = 2048`.
const MAX_URL_LEN: usize = 2048;
/// `MAX_SECRET_LEN = 256`.
const MAX_SECRET_LEN: usize = 256;
/// `MAX_MESSAGE_LEN = 32_000`.
const MAX_MESSAGE_LEN: usize = 32_000;

/// `KNOWN_PROVIDERS` (routes/webhook_routes.py:189-199) — provider name -> base URL,
/// auto-resolved from the `provider` body field or a `model` prefix. Source order is
/// preserved: `ollama` follows `openrouter` and `venice` follows `fireworks`.
const KNOWN_PROVIDERS: &[(&str, &str)] = &[
    ("deepseek", "https://api.deepseek.com/v1"),
    ("openai", "https://api.openai.com/v1"),
    ("mistral", "https://api.mistral.ai/v1"),
    ("groq", "https://api.groq.com/openai/v1"),
    ("together", "https://api.together.xyz/v1"),
    ("openrouter", "https://openrouter.ai/api/v1"),
    ("ollama", "https://ollama.com/api"),
    ("fireworks", "https://api.fireworks.ai/inference/v1"),
    ("venice", "https://api.venice.ai/api/v1"),
];

/// `MODEL_PROVIDER_MAP` (routes/webhook_routes.py:163-173) — model-name prefix ->
/// provider key, for `_resolve_base_url`'s auto-detect. Order matters: the Python
/// `dict` is iterated in insertion order and the FIRST matching prefix wins, so this
/// preserves that exact order.
const MODEL_PROVIDER_MAP: &[(&str, &str)] = &[
    ("deepseek", "deepseek"),
    ("gpt-", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("mistral", "mistral"),
    ("llama", "groq"),
    ("mixtral", "groq"),
];

/// `KNOWN_PROVIDERS[name]` — the base URL for a provider key, if known.
fn known_provider(name: &str) -> Option<&'static str> {
    KNOWN_PROVIDERS.iter().find(|(k, _)| *k == name).map(|(_, v)| *v)
}

/// `_resolve_base_url(model, provider)` (routes/webhook_routes.py:175-184) — try to
/// auto-resolve a base URL from the explicit `provider` name (lower-cased) or, failing
/// that, the FIRST `model`-prefix match (lower-cased). `None` when neither resolves.
fn resolve_base_url(model: Option<&str>, provider: Option<&str>) -> Option<&'static str> {
    // `if provider and provider.lower() in KNOWN_PROVIDERS: return ...`.
    if let Some(p) = provider {
        if let Some(url) = known_provider(&p.to_lowercase()) {
            return Some(url);
        }
    }
    // `if model: for prefix, prov in MODEL_PROVIDER_MAP.items(): if lower.startswith(prefix): return KNOWN_PROVIDERS[prov]`.
    if let Some(m) = model {
        let model_lower = m.to_lowercase();
        for (prefix, prov) in MODEL_PROVIDER_MAP {
            if model_lower.starts_with(prefix) {
                // `prov` is always a valid `KNOWN_PROVIDERS` key, so the lookup
                // cannot miss (matching the Python `KNOWN_PROVIDERS[prov]`).
                return known_provider(prov);
            }
        }
    }
    None
}

/// `SyncChatRequest` (routes/webhook_routes.py:186-192) — the `/v1/chat` JSON body.
///
/// `message` is required (`Field(...)`); the rest are `Optional[str] = None`. The
/// Pydantic `max_length` validators are reproduced in [`sync_chat`] (a too-long field
/// -> the Python's 422-equivalent, surfaced as the same 400 the body checks use). All
/// fields default to absent so a partial body deserializes (mirroring the optionals).
#[derive(Debug, Default, Deserialize)]
struct SyncChatRequest {
    #[serde(default)]
    message: String,
    #[serde(default)]
    model: Option<String>,
    #[serde(default)]
    session: Option<String>,
    #[serde(default)]
    api_key: Option<String>,
    #[serde(default)]
    base_url: Option<String>,
    #[serde(default)]
    provider: Option<String>,
}

/// `setup_webhook_routes(webhook_manager, auth_manager, session_manager,
/// api_key_manager) -> APIRouter` — assemble the webhook router.
///
/// app.py registers this as include-router #35. The Python
/// `APIRouter(prefix="/api", tags=["webhooks"])` registers, in source order:
/// `GET /webhooks`, `POST /webhooks`, `POST /webhooks/{id}/test`,
/// `PATCH /webhooks/{id}`, `DELETE /webhooks/{id}`, `POST /v1/chat`. Under the
/// `/api` prefix those resolve to the absolute paths below (axum 0.7 `:id`). The
/// GET/POST on `/api/webhooks` share a path; the `{id}` captures are disjoint, so
/// registration order is immaterial to matching — we keep the Python source order.
pub fn setup_webhook_routes() -> Router<AppState> {
    Router::new()
        // `@router.get("/webhooks")` + `@router.post("/webhooks")`.
        .route("/api/webhooks", get(list_webhooks).post(create_webhook))
        // `@router.post("/webhooks/{webhook_id}/test")`.
        .route("/api/webhooks/:webhook_id/test", post(test_webhook))
        // `@router.patch("/webhooks/{webhook_id}")` + `@router.delete(...)`.
        .route(
            "/api/webhooks/:webhook_id",
            axum::routing::patch(toggle_webhook).delete(delete_webhook),
        )
        // `@router.post("/v1/chat")` — sync chat (API-token-gated, fully ported).
        .route("/api/v1/chat", post(sync_chat))
}

/// `GET /api/webhooks` — list all webhooks (admin only, secret never exposed).
///
/// ```python
/// @router.get("/webhooks")
/// def list_webhooks(request: Request):
///     _require_admin(request)
///     db = SessionLocal()
///     hooks = db.query(Webhook).all()
///     return [ {...} for w in hooks ]
/// ```
///
/// The dict per webhook preserves the Python key order exactly: `id`, `name`,
/// `url`, `has_secret`, `events`, `is_active`, `last_triggered_at`,
/// `last_status_code`, `last_error`, `created_at`. `has_secret` is `bool(w.secret)`
/// (true only for a non-NULL, non-empty stored secret). `events` is
/// `w.events.split(",") if w.events else []` (no strip/filter — a plain split).
/// `last_triggered_at` / `created_at` are `.isoformat()` (plain, no `Z` suffix), or
/// `null` when absent.
async fn list_webhooks(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;

    let conn = session_local()?;
    // `hooks = db.query(Webhook).all()` — no explicit ordering in the Python.
    let mut stmt = conn
        .prepare(
            "SELECT id, name, url, secret, events, is_active, last_triggered_at, \
             last_status_code, last_error, created_at FROM webhooks",
        )
        .map_err(db_500)?;

    let map_row = |r: &rusqlite::Row<'_>| -> rusqlite::Result<Value> {
        let id: String = r.get(0)?;
        let name: Option<String> = r.get(1)?;
        let url: Option<String> = r.get(2)?;
        let secret: Option<String> = r.get(3)?;
        // `w.events.split(",") if w.events else []` — column is NOT NULL but may
        // be empty; an empty string is falsy in Python -> [].
        let events: Option<String> = r.get(4)?;
        // `is_active` is stored as 0/1 (SQLAlchemy Boolean); read as bool.
        let is_active: bool = r.get(5)?;
        let last_triggered_at: Option<String> = r.get(6)?;
        // `last_status_code` is a nullable Integer.
        let last_status_code: Option<i64> = r.get(7)?;
        let last_error: Option<String> = r.get(8)?;
        let created_at: Option<String> = r.get(9)?;

        Ok(json!({
            "id": id,
            "name": name,
            "url": url,
            // `bool(w.secret)` — true only for a non-empty stored secret.
            "has_secret": secret.as_deref().map(|s| !s.is_empty()).unwrap_or(false),
            // `w.events.split(",") if w.events else []`.
            "events": split_events(events.as_deref()),
            "is_active": is_active,
            // `w.last_triggered_at.isoformat() if w.last_triggered_at else None`.
            "last_triggered_at": iso_or_null(last_triggered_at.as_deref()),
            "last_status_code": last_status_code,
            "last_error": last_error,
            // `w.created_at.isoformat() if w.created_at else None`.
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

/// `POST /api/webhooks` — create a webhook (admin only).
///
/// ```python
/// @router.post("/webhooks")
/// def create_webhook(request, name=Form(""), url=Form(""), secret=Form(""), events=Form("")):
///     _require_admin(request)
///     name = name.strip()[:MAX_NAME_LEN]
///     if not name: raise HTTPException(400, "Webhook name is required")
///     try: url = validate_webhook_url(url)
///     except ValueError as e: raise HTTPException(400, str(e))
///     try: events = validate_events(events)
///     except ValueError as e: raise HTTPException(400, str(e))
///     secret_val = secret.strip()[:MAX_SECRET_LEN] or None
///     encrypted_secret = None
///     if secret_val and api_key_manager: encrypted_secret = api_key_manager.encrypt_api_key(secret_val)
///     elif secret_val: encrypted_secret = secret_val
///     webhook_id = str(uuid.uuid4())[:8]
///     db.add(Webhook(id, name, url, secret=encrypted_secret, events, is_active=True)); db.commit()
///     return {"id": webhook_id, "name": name}
/// ```
async fn create_webhook(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    mp: Multipart,
) -> Result<Response, HttpException> {
    // `_require_admin(request)` first — FastAPI resolves the dependency before the
    // body, so we gate before reading the form.
    admin_gate(&state, &headers, user.as_deref())?;

    // `name/url/secret/events: str = Form("")` — defaulted form fields; a missing
    // field is `""`.
    let form = parse_form(mp).await;
    let raw_name = form.get("name").map(String::as_str).unwrap_or("");
    let raw_url = form.get("url").map(String::as_str).unwrap_or("");
    let raw_secret = form.get("secret").map(String::as_str).unwrap_or("");
    let raw_events = form.get("events").map(String::as_str).unwrap_or("");

    // `name = name.strip()[:MAX_NAME_LEN]` — strip, then slice to 100 *characters*.
    let name: String = raw_name.trim().chars().take(MAX_NAME_LEN).collect();
    // `if not name: raise HTTPException(400, "Webhook name is required")`.
    if name.is_empty() {
        return Err(HttpException::new(400, "Webhook name is required"));
    }

    // `try: url = validate_webhook_url(url) except ValueError as e: raise HTTPException(400, str(e))`.
    let url = validate_webhook_url(raw_url).map_err(|e| HttpException::new(400, e.to_string()))?;
    // `try: events = validate_events(events) except ValueError as e: raise HTTPException(400, str(e))`.
    let events =
        validate_events(raw_events).map_err(|e| HttpException::new(400, e.to_string()))?;

    // `secret_val = secret.strip()[:MAX_SECRET_LEN] or None` — strip + slice, then
    // empty-string -> None (Python `or` on a falsy str).
    let secret_trimmed: String = raw_secret.trim().chars().take(MAX_SECRET_LEN).collect();
    let secret_val: Option<String> = if secret_trimmed.is_empty() {
        None
    } else {
        Some(secret_trimmed)
    };

    // Encrypt the secret at rest using the same Fernet key as API keys. On the
    // Rust `AppState` `api_key_manager` is always present, so the `if secret_val
    // and api_key_manager` branch is the live path; the `elif secret_val` plaintext
    // fallback is unreachable (matching the live Python wiring). The Python calls
    // `api_key_manager.encrypt_api_key(secret_val)` with NO try/except, so a raise
    // here propagates to FastAPI's default exception handler, which returns a GENERIC
    // 500 "Internal Server Error" WITHOUT surfacing the exception text to the client
    // (it is only logged server-side). Mirror that with the crate's established
    // unhandled-500 convention (`encrypt_500`): log the detail, return the generic
    // body — NOT `e.to_string()`, which would leak the internal error.
    let encrypted_secret: Option<String> = match &secret_val {
        Some(s) => Some(
            state
                .api_key_manager
                .encrypt_api_key(s)
                .map_err(encrypt_500)?,
        ),
        None => None,
    };

    // `webhook_id = str(uuid.uuid4())[:8]` — first 8 chars of a v4 UUID string.
    let webhook_id: String = uuid::Uuid::new_v4().to_string().chars().take(8).collect();

    let conn = session_local()?;
    // TimestampMixin: `created_at`/`updated_at` default to `datetime.utcnow` at flush;
    // `last_triggered_at`/`last_status_code`/`last_error` stay NULL.
    let now = crate::pydatetime::utcnow_naive_iso();
    // `db.add(Webhook(id, name, url, secret=encrypted_secret, events, is_active=True))`.
    conn.execute(
        "INSERT INTO webhooks \
           (id, name, url, secret, events, is_active, created_at, updated_at) \
         VALUES (?1, ?2, ?3, ?4, ?5, 1, ?6, ?6)",
        rusqlite::params![webhook_id, name, url, encrypted_secret, events, now],
    )
    .map_err(db_500)?;

    // return {"id": webhook_id, "name": name} — key order matches the Python.
    Ok(Json(json!({"id": webhook_id, "name": name})).into_response())
}

/// `POST /api/webhooks/{webhook_id}/test` — fire a test ping (admin only).
///
/// ```python
/// @router.post("/webhooks/{webhook_id}/test")
/// async def test_webhook(request, webhook_id):
///     _require_admin(request)
///     wh = db.query(Webhook).filter(Webhook.id == webhook_id).first()
///     if not wh: raise HTTPException(404, "Webhook not found")
///     url, secret = wh.url, wh.secret
///     await webhook_manager.deliver_test(webhook_id, url, secret)
///     return {"status": "sent"}
/// ```
///
/// `wh.secret` is the ENCRYPTED-at-rest value; `deliver_test` decrypts it with the
/// same Fernet key (its `_decrypt_secret` path).
async fn test_webhook(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Path(webhook_id): Path<String>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;

    let (url, secret) = {
        let conn = session_local()?;
        // `wh = db.query(Webhook).filter(Webhook.id == webhook_id).first()`.
        let row = conn
            .query_row(
                "SELECT url, secret FROM webhooks WHERE id = ?1",
                rusqlite::params![webhook_id],
                |r| {
                    let url: String = r.get(0)?;
                    let secret: Option<String> = r.get(1)?;
                    Ok((url, secret))
                },
            )
            .optional_row()?;
        // `if not wh: raise HTTPException(404, "Webhook not found")`.
        match row {
            Some(v) => v,
            None => return Err(HttpException::new(404, "Webhook not found")),
        }
    };

    // `await webhook_manager.deliver_test(webhook_id, url, secret)` — the encrypted
    // secret is decrypted inside `deliver_test`.
    state
        .webhook_manager
        .deliver_test(&webhook_id, &url, secret.as_deref())
        .await;

    Ok(Json(json!({"status": "sent"})).into_response())
}

/// `PATCH /api/webhooks/{webhook_id}` — toggle `is_active` (admin only).
///
/// ```python
/// @router.patch("/webhooks/{webhook_id}")
/// def toggle_webhook(request, webhook_id):
///     _require_admin(request)
///     wh = db.query(Webhook).filter(Webhook.id == webhook_id).first()
///     if not wh: raise HTTPException(404, "Webhook not found")
///     wh.is_active = not wh.is_active
///     db.commit()
///     return {"id": webhook_id, "is_active": wh.is_active}
/// ```
///
/// The returned `is_active` is the NEW (flipped) value.
async fn toggle_webhook(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Path(webhook_id): Path<String>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;

    let conn = session_local()?;
    // `wh = db.query(Webhook).filter(Webhook.id == webhook_id).first()`.
    let current: Option<bool> = conn
        .query_row(
            "SELECT is_active FROM webhooks WHERE id = ?1",
            rusqlite::params![webhook_id],
            |r| r.get::<_, bool>(0),
        )
        .optional_row()?;
    // `if not wh: raise HTTPException(404, "Webhook not found")`.
    let current = match current {
        Some(v) => v,
        None => return Err(HttpException::new(404, "Webhook not found")),
    };

    // `wh.is_active = not wh.is_active; db.commit()`.
    let new_active = !current;
    conn.execute(
        "UPDATE webhooks SET is_active = ?1 WHERE id = ?2",
        rusqlite::params![new_active as i64, webhook_id],
    )
    .map_err(db_500)?;

    // return {"id": webhook_id, "is_active": wh.is_active} — the NEW value.
    Ok(Json(json!({"id": webhook_id, "is_active": new_active})).into_response())
}

/// `DELETE /api/webhooks/{webhook_id}` — delete a webhook (admin only).
///
/// ```python
/// @router.delete("/webhooks/{webhook_id}")
/// def delete_webhook(request, webhook_id):
///     _require_admin(request)
///     deleted = db.query(Webhook).filter(Webhook.id == webhook_id).delete()
///     db.commit()
///     if not deleted: raise HTTPException(404, "Webhook not found")
///     return {"status": "deleted"}
/// ```
///
/// SQLAlchemy's `.delete()` returns the affected row count; `if not deleted` is the
/// 404 branch. `rusqlite::execute` returns the same count, so `count == 0` -> 404.
async fn delete_webhook(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Path(webhook_id): Path<String>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;

    let conn = session_local()?;
    // `deleted = db.query(Webhook).filter(Webhook.id == webhook_id).delete()`.
    let deleted = conn
        .execute(
            "DELETE FROM webhooks WHERE id = ?1",
            rusqlite::params![webhook_id],
        )
        .map_err(db_500)?;
    // `if not deleted: raise HTTPException(404, "Webhook not found")`.
    if deleted == 0 {
        return Err(HttpException::new(404, "Webhook not found"));
    }

    Ok(Json(json!({"status": "deleted"})).into_response())
}

/// `POST /api/v1/chat` — sync chat for n8n / Make / Activepieces (API-token-gated).
///
/// ```python
/// @router.post("/v1/chat")
/// async def sync_chat(request: Request, body: SyncChatRequest):
///     if not getattr(request.state, "api_token", False):
///         raise HTTPException(403, "This endpoint requires an API token")
///     scopes = set(getattr(request.state, "api_token_scopes", []) or [])
///     if "chat" not in scopes:
///         raise HTTPException(403, "API token is not scoped for chat")
///     token_owner = getattr(request.state, "api_token_owner", None)
///     ...  # session resume / api_key / endpoint fallback / llm_call_async / fire
/// ```
///
/// The API-token state is stamped by the ported Bearer middleware (`web::auth_gate`
/// -> the [`ApiToken`] extension), so this reads `Option<Extension<ApiToken>>`
/// (absent => `present = false`, the Python `getattr(..., False)` default) and
/// reproduces the two gate raises:
/// * not a token -> `403 "This endpoint requires an API token"`;
/// * token lacks the `chat` scope -> `403 "API token is not scoped for chat"`.
///
/// Past the gate the full pipeline runs:
/// * **Case 1 — resume** (`body.session` + a session manager): `get_session` (404 on
///   miss) + the ownership SECURITY check (`_tok_user = token_owner or current_user`;
///   if both the token's user and the session's owner are set and differ -> 404, so a
///   token holder cannot resume another user's chat by ID);
/// * **Case 2 — direct `api_key`**: resolve `base_url` (explicit > provider > model
///   prefix; 400 if none), create an `"API Chat"` session with `Authorization: Bearer
///   <api_key>` headers, `save_sessions`;
/// * **Case 3 — first-enabled `ModelEndpoint`**: 400 if none; `model == "auto"` probes
///   the endpoint's `/models` (reqwest, `timeout=5`, `raise_for_status`; 500 "Could
///   not discover models from endpoint" on any failure), takes `ids[0]` (else stays
///   `"auto"`); create the `"API Chat"` session (headers only when a key exists);
/// * then append the user message, call `llm_call_async` (the LLMConfig defaults +
///   `timeout=120`), append the assistant reply, `save_sessions`, fire the
///   fire-and-forget `chat.completed` webhook, and return `{"response", "session_id",
///   "model"}`.
async fn sync_chat(
    State(state): State<AppState>,
    tok: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
    Json(body): Json<SyncChatRequest>,
) -> Result<Response, HttpException> {
    // `if not getattr(request.state, "api_token", False)` ... then the `chat` scope
    // check — the two gate raises, factored out so they are unit-testable.
    let tok = tok.map(|Extension(t)| t).unwrap_or_default();
    chat_token_gate(&tok)?;
    // `token_owner = getattr(request.state, "api_token_owner", None)`.
    let token_owner = tok.owner.clone();

    // The Pydantic `max_length` validators (FastAPI's pre-handler 422). Surface them as
    // a 400 rather than fabricating a completion when the body exceeds the limits.
    if body.message.chars().count() > MAX_MESSAGE_LEN
        || body.model.as_deref().map(|s| s.chars().count() > 200).unwrap_or(false)
        || body.session.as_deref().map(|s| s.chars().count() > 100).unwrap_or(false)
        || body.api_key.as_deref().map(|s| s.chars().count() > MAX_SECRET_LEN).unwrap_or(false)
        || body.base_url.as_deref().map(|s| s.chars().count() > MAX_URL_LEN).unwrap_or(false)
        || body.provider.as_deref().map(|s| s.chars().count() > 50).unwrap_or(false)
    {
        return Err(HttpException::new(422, "Invalid request"));
    }

    // `message = body.message.strip(); if not message: raise HTTPException(400, ...)`.
    let message = body.message.trim().to_string();
    if message.is_empty() {
        return Err(HttpException::new(400, "Message is required"));
    }

    // `session_id = body.session; sess = None`.
    let mut session_id: Option<String> = body.session.clone();
    // The resolved session id once a session is established (resume reuses
    // `session_id`; create branches mint a fresh uuid).
    let mut resolved_id: Option<String> = None;
    let mut have_session = false;

    // --- Case 1: Resume an existing session ---
    // `if session_id and session_manager:` — the Rust `AppState.sessions` is always
    // present (the Python `session_manager` is `None` only when unwired), so this is
    // the live resume path whenever a `session` id is supplied. CRUCIAL: Python's
    // `if session_id` is a TRUTHINESS test — an empty string `session=""` is FALSY,
    // so Python SKIPS the resume path and falls through to Case 2 / Case 3. Mirror
    // that by only entering resume for a NON-EMPTY id (`filter(|s| !s.is_empty())`);
    // an empty/missing `session` leaves `have_session = false` and creates a session.
    if let Some(sid) = session_id.clone().filter(|s| !s.is_empty()) {
        // `try: sess = session_manager.get_session(session_id) except: raise HTTPException(404, ...)`.
        let sess = state
            .sessions
            .get_session(&sid)
            .map_err(|_| HttpException::new(404, "Session not found"))?;
        // SECURITY: `_tok_user = token_owner or request.state.user or get_current_user`;
        // for a Bearer request the resolved user is `CurrentUser("api")`, so the token's
        // own `owner` is authoritative and the `CurrentUser` is the fallback.
        let tok_user = token_owner
            .clone()
            .or_else(|| user.as_ref().map(|Extension(u)| u.0.clone()));
        // Strict ownership (see `caller_owns_session`): FAIL CLOSED so a null-owner /
        // cross-owner session can't be resumed by an arbitrary chat-scoped token. The
        // old `_tok_user and _sess_owner and _sess_owner != _tok_user` form skipped the
        // check whenever either was falsy; the new gate requires an exact match and a
        // non-empty caller, denying resume of legacy/migrated null-owner sessions.
        let sess_owner = sess.owner.clone();
        if !caller_owns_session(sess_owner.as_deref(), tok_user.as_deref()) {
            return Err(HttpException::new(404, "Session not found"));
        }
        resolved_id = Some(sid);
        have_session = true;
    }

    // --- Case 2: Direct API key + model (no pre-configured endpoint needed) ---
    // `if not sess and body.api_key:`.
    if !have_session {
        if let Some(raw_key) = body.api_key.as_deref() {
            // `api_key = body.api_key.strip()`.
            let api_key = raw_key.trim().to_string();
            // `model = body.model or "deepseek-chat"`.
            let model = match body.model.as_deref() {
                Some(m) if !m.is_empty() => m.to_string(),
                _ => "deepseek-chat".to_string(),
            };

            // `direct_base_url = body.base_url.strip().rstrip("/") if body.base_url else None`.
            let direct_base_url: Option<String> = body
                .base_url
                .as_deref()
                .map(|b| b.trim().trim_end_matches('/').to_string())
                .filter(|b| !b.is_empty());
            // SECURITY: validate ONLY a token-supplied direct base_url against the SSRF
            // gate (`validate_public_http_url`); auto-resolved known-provider URLs are
            // trusted. `if direct_base_url: try base_url = validate_public_http_url(...)
            // except ValueError as e: detail = str(e).replace("URL", "base_url", 1);
            // raise HTTPException(400, detail)` — only the FIRST "URL" is rewritten.
            let base_url: Option<String> = match direct_base_url {
                Some(b) => Some(validate_public_http_url(&b).map_err(|e| {
                    HttpException::new(400, e.replacen("URL", "base_url", 1))
                })?),
                // `else: base_url = _resolve_base_url(model, body.provider)`.
                None => resolve_base_url(Some(&model), body.provider.as_deref()).map(str::to_string),
            };
            // `if not base_url: raise HTTPException(400, "Could not auto-detect ...")`.
            let base_url = match base_url {
                Some(b) if !b.is_empty() => b,
                _ => {
                    return Err(HttpException::new(
                        400,
                        "Could not auto-detect provider. Pass base_url (e.g. 'https://api.deepseek.com/v1') \
                         or provider ('deepseek', 'openai', 'groq', etc.)",
                    ))
                }
            };

            // `base_url = normalize_base(base_url); endpoint_url = build_chat_url(base_url)`
            // — route through the unified endpoint resolver (strips known API suffixes,
            // then builds the provider-correct chat URL incl. anthropic/ollama shapes).
            let base_url = normalize_base(Some(&base_url));
            let endpoint_url = build_chat_url(&base_url);

            // `sid = str(uuid.uuid4())`.
            let sid = uuid::Uuid::new_v4().to_string();
            // `sess = session_manager.create_session(session_id=sid, name="API Chat",
            //  endpoint_url=endpoint_url, model=model, owner=token_owner)`. The Rust
            // ctor has the additional `rag` arg (Python's `Session` defaults `rag=False`).
            state
                .sessions
                .create_session(&sid, "API Chat", &endpoint_url, &model, false, token_owner.as_deref())
                .map_err(|e| {
                    crate::pylog::error(&format!("sync_chat create_session error: {e}"));
                    HttpException::new(500, "Internal Server Error")
                })?;
            // `sess.headers = build_headers(api_key, base_url)` — Python mutates the
            // IN-MEMORY Session object, and `save_sessions()` is a NO-OP, so the headers
            // are NOT persisted to the DB `sessions.headers` column. Mirror that exactly:
            // set them on the cached session only (`with_session_mut`, in-memory under the
            // lock — no DB write), NOT `update_session_headers` (which writes the row).
            // The subsequent `llm_call_async` reads them back from the cached session.
            // `build_headers` yields the provider-correct auth header set (Bearer for
            // OpenAI-compatible; x-api-key + anthropic-version for anthropic).
            let headers = build_headers(Some(&api_key), &base_url);
            state.sessions.with_session_mut(&sid, |s| {
                s.headers = headers;
            });
            // `session_manager.save_sessions()` — a no-op (headers stay in-memory only).
            state.sessions.save_sessions();
            // `session_id = sid`.
            session_id = Some(sid.clone());
            resolved_id = Some(sid);
            have_session = true;
        }
    }

    // --- Case 3: Fall back to first configured ModelEndpoint ---
    // `if not sess:`.
    if !have_session {
        // `ep = _select_api_chat_fallback_endpoint(db, token_owner)` — OWNER-SCOPED.
        // The previous unscoped `first_enabled()` would let a chat-scoped token fall
        // back onto ANOTHER user's private endpoint and silently spend that owner's
        // API key/quota. Now it selects the first enabled endpoint visible to the
        // token's owner (their own rows + legacy null-owner "shared" rows, owner rows
        // preferred), or — when the token has no owner — a null-owner shared row only.
        let ep = select_api_chat_fallback_endpoint(token_owner.as_deref());
        // `if not ep: raise HTTPException(400, "No session, api_key, or ...")`.
        let (ep_base_url, api_key) = match ep {
            Some(v) => v,
            None => {
                return Err(HttpException::new(
                    400,
                    "No session, api_key, or configured endpoints. \
                     Pass api_key + model, or configure an endpoint in Admin.",
                ))
            }
        };

        // `base_url = normalize_base(ep.base_url); endpoint_url = build_chat_url(base_url)`.
        let base_url = normalize_base(Some(&ep_base_url));
        let endpoint_url = build_chat_url(&base_url);
        // `model = body.model or "auto"`.
        let mut model = match body.model.as_deref() {
            Some(m) if !m.is_empty() => m.to_string(),
            _ => "auto".to_string(),
        };

        // `if model == "auto":` — discover models from the endpoint's `/models`.
        if model == "auto" {
            model = discover_first_model(&base_url, api_key.as_deref()).await?;
        }

        // `sid = str(uuid.uuid4())`.
        let sid = uuid::Uuid::new_v4().to_string();
        // `sess = session_manager.create_session(session_id=sid, name="API Chat",
        //  endpoint_url=endpoint_url, model=model, owner=token_owner)`.
        state
            .sessions
            .create_session(&sid, "API Chat", &endpoint_url, &model, false, token_owner.as_deref())
            .map_err(|e| {
                crate::pylog::error(&format!("sync_chat create_session error: {e}"));
                HttpException::new(500, "Internal Server Error")
            })?;
        // `if api_key: sess.headers = build_headers(api_key, base_url);
        //  session_manager.save_sessions()`. Same in-memory-only semantics as Case 2:
        // Python mutates the cached Session and `save_sessions()` is a NO-OP, so the
        // headers do NOT reach the DB. Use `with_session_mut` (cache-only), NOT
        // `update_session_headers` (which writes the row). NOTE the Python `if api_key:`
        // guard keys off the DECRYPTED endpoint key (falsy/empty -> no headers); the
        // provider-correct `build_headers` is only called inside that guard.
        if let Some(key) = api_key.as_deref().filter(|k| !k.is_empty()) {
            let headers = build_headers(Some(key), &base_url);
            state.sessions.with_session_mut(&sid, |s| {
                s.headers = headers;
            });
            state.sessions.save_sessions();
        }
        // `session_id = sid`.
        session_id = Some(sid.clone());
        resolved_id = Some(sid);
    }

    // Past the three cases a session is always established (each branch sets it or
    // raises). `resolved_id` is the id `add_message`/`save` operate on.
    let sid = resolved_id.expect("a session id is resolved by one of the three branches");

    // --- Send message and get response ---
    // `sess.add_message(ChatMessage("user", message))` — mutate the stored session
    // (history append + DB persist) via the manager, matching the Python's live
    // `sess` (the very object the manager holds).
    state
        .sessions
        .add_message(&sid, ChatMessage::new("user", message.clone(), None))
        .map_err(|e| {
            crate::pylog::error(&format!("sync_chat add_message error: {e}"));
            HttpException::new(500, "Internal Server Error")
        })?;

    // Re-read the now-updated session to build the request + read endpoint/model/headers.
    let sess = state
        .sessions
        .get_session(&sid)
        .map_err(|_| HttpException::new(404, "Session not found"))?;

    // `messages = [{"role": m.role, "content": m.content} for m in sess.history]`.
    let messages: Vec<Value> = sess
        .history
        .iter()
        .map(|m| json!({"role": m.role, "content": m.content}))
        .collect();

    // `reply = await llm_call_async(sess.endpoint_url, sess.model, messages,
    //  headers=sess.headers, timeout=120)` — temperature/max_tokens take the
    // `LLMConfig` defaults (DEFAULT_TEMPERATURE=1.0, DEFAULT_MAX_TOKENS=0). Python
    // calls this with NO try/except, so the `HTTPException` it RAISES propagates with
    // its REAL status (503 cooldown/connect-failure / 502 POST-failed-after-retries /
    // the actual upstream status + friendly detail / 502 schema). `llm_call_async`
    // now returns that status+detail FAITHFULLY via `PyError::Upstream`, so we map
    // it straight to the `HttpException` (see `llm_call_status`).
    let reply = crate::src::llm_core::llm_call_async(
        &sess.endpoint_url,
        &sess.model,
        messages,
        crate::core::constants::DEFAULT_TEMPERATURE,
        crate::core::constants::DEFAULT_MAX_TOKENS,
        sess.headers.clone(),
        120,
    )
    .await
    .map_err(llm_call_status)?;

    // `sess.add_message(ChatMessage("assistant", reply))`.
    state
        .sessions
        .add_message(&sid, ChatMessage::new("assistant", reply.clone(), None))
        .map_err(|e| {
            crate::pylog::error(&format!("sync_chat add_message error: {e}"));
            HttpException::new(500, "Internal Server Error")
        })?;
    // `session_manager.save_sessions()`.
    state.sessions.save_sessions();

    // `asyncio.create_task(webhook_manager.fire("chat.completed", {...}))` — fire and
    // forget. `message[:2000]` / `reply[:2000]` truncate to the first 2000 CHARACTERS
    // (Python slicing is by code point).
    let sid_for_payload = session_id.clone().unwrap_or_else(|| sid.clone());
    let user_message: String = message.chars().take(2000).collect();
    let response_excerpt: String = reply.chars().take(2000).collect();
    state.webhook_manager.fire_and_forget(
        "chat.completed",
        json!({
            "session_id": sid_for_payload,
            "model": sess.model,
            "user_message": user_message,
            "response": response_excerpt,
        }),
    );

    // `return {"response": reply, "session_id": session_id, "model": sess.model}`.
    Ok(Json(json!({
        "response": reply,
        "session_id": session_id,
        "model": sess.model,
    }))
    .into_response())
}

/// `model == "auto"` discovery (routes/webhook_routes.py:327-344): probe the
/// endpoint's models list (`httpx.AsyncClient(timeout=5)`, `raise_for_status`), take
/// the FIRST `data[].id`, and on ANY failure raise `500 "Could not discover models from
/// endpoint"`. Returns `ids[0]` when present, else `"auto"` (the Python's
/// `ids[0] if ids else "auto"`).
///
/// `base_url` is the ALREADY-normalized base; the URL/headers are built through the
/// unified `endpoint_resolver` (`build_models_url`/`build_headers`) so anthropic /
/// ollama endpoints get the provider-correct models URL + auth headers (e.g. ollama's
/// `/tags`, anthropic's `x-api-key` + `anthropic-version`).
///
/// When the OpenAI-style `data[].id` list is empty, fall back to the Ollama-style
/// `models[].name` / `models[].model` (the `if not ids: ids = [m.get("name") or
/// m.get("model") ...]` block).
///
/// This is distinct from `model_endpoints::probe_models` (8s timeout, swallows errors,
/// returns `[]`): here a failure must become the 500 and the timeout is 5s, so the
/// probe is inlined to match the Python exactly.
async fn discover_first_model(
    base_url: &str,
    api_key: Option<&str>,
) -> Result<String, HttpException> {
    // The whole block is the Python `try: ... except Exception: raise HTTPException(500, ...)`.
    let probe = || async {
        // `models_url = build_models_url(base_url)`.
        let models_url = build_models_url(base_url);
        let client = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(5))
            .build()
            .ok()?;
        let mut req = client.get(&models_url);
        // `hdrs = build_headers(api_key, base_url)` — provider-correct auth headers.
        for (k, v) in build_headers(api_key, base_url) {
            req = req.header(k, v);
        }
        let resp = req.send().await.ok()?;
        // `resp.raise_for_status()`.
        let resp = resp.error_for_status().ok()?;
        // `data = resp.json()`.
        let data: Value = resp.json().await.ok()?;
        // `ids = [m.get("id") for m in (data.get("data") or []) if m.get("id")]`.
        let mut ids: Vec<String> = data
            .get("data")
            .and_then(|d| d.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|m| m.get("id").and_then(|i| i.as_str()).map(String::from))
                    .collect()
            })
            .unwrap_or_default();
        // `if not ids: ids = [m.get("name") or m.get("model") for m in
        //  (data.get("models") or []) if m.get("name") or m.get("model")]` — the
        // Ollama-style `/tags` shape (`{"models":[{"name":...,"model":...}]}`). `name`
        // is preferred (Python `a or b`); a non-empty `name` wins, else `model`.
        if ids.is_empty() {
            ids = data
                .get("models")
                .and_then(|d| d.as_array())
                .map(|arr| {
                    arr.iter()
                        .filter_map(|m| {
                            let name = m.get("name").and_then(|v| v.as_str()).filter(|s| !s.is_empty());
                            let model = m.get("model").and_then(|v| v.as_str()).filter(|s| !s.is_empty());
                            name.or(model).map(String::from)
                        })
                        .collect()
                })
                .unwrap_or_default();
        }
        // `model = ids[0] if ids else "auto"`.
        Some(ids.into_iter().next().unwrap_or_else(|| "auto".to_string()))
    };
    probe()
        .await
        .ok_or_else(|| HttpException::new(500, "Could not discover models from endpoint"))
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// The `/v1/chat` API-token gate (routes/webhook_routes.py:196-200): the two raises
/// before any work.
///
/// * `if not getattr(request.state, "api_token", False): raise HTTPException(403,
///   "This endpoint requires an API token")` — an absent extension / explicit
///   `present = false` (cookie / internal-tool / bypass) is not a token;
/// * `scopes = set(getattr(request.state, "api_token_scopes", []) or []); if "chat"
///   not in scopes: raise HTTPException(403, "API token is not scoped for chat")`.
fn chat_token_gate(tok: &ApiToken) -> Result<(), HttpException> {
    if !tok.present {
        return Err(HttpException::new(403, "This endpoint requires an API token"));
    }
    if !tok.scopes.iter().any(|s| s == "chat") {
        return Err(HttpException::new(403, "API token is not scoped for chat"));
    }
    Ok(())
}

/// `_caller_owns_session(sess_owner, caller) -> bool` (routes/webhook_routes.py:46-62)
/// — the strict session-ownership gate for `POST /api/v1/chat`'s Case 1 resume.
///
/// A caller may resume a session ONLY when its owner matches them EXACTLY. FAIL
/// CLOSED on both ends:
/// * `if not caller: return False` — an unresolvable / empty caller is denied;
/// * a null/empty session owner (legacy or migrated rows) is deliberately NOT
///   resumable by an arbitrary token. The old `sess_owner and sess_owner != caller`
///   form skipped the check whenever `sess_owner` was falsy, so any chat-scoped token
///   (e.g. a paired mobile device) could resume such a session, inject a message, and
///   read back its history + reuse the owner's endpoint credentials.
///
/// In Rust both args are `Option<&str>`: `None`/`Some("")` are the Python falsy forms.
/// `caller` falsy -> `false`; otherwise the gate is `sess_owner == Some(caller)`, so a
/// `None`/empty `sess_owner` never matches a non-empty caller.
fn caller_owns_session(sess_owner: Option<&str>, caller: Option<&str>) -> bool {
    // `if not caller: return False`.
    let caller = match caller.filter(|c| !c.is_empty()) {
        Some(c) => c,
        None => return false,
    };
    // `return sess_owner == caller`. A null/empty `sess_owner` is its own distinct
    // value and never equals a non-empty caller -> fail closed.
    sess_owner.filter(|o| !o.is_empty()) == Some(caller)
}

/// `_select_api_chat_fallback_endpoint(db, token_owner)` (routes/webhook_routes.py:31-43)
/// — the first enabled `ModelEndpoint` visible to `token_owner`, as `(base_url,
/// DECRYPTED api_key)`.
///
/// OWNER-SCOPED: an unscoped `.first()` would let a chat-scoped token fall back onto
/// another user's private endpoint and silently spend that owner's API key/quota.
///
/// * When `token_owner` is present (Python `if token_owner:`), apply
///   `owner_filter(query, ModelEndpoint, token_owner)` (the include-shared form ->
///   `(owner = ?1 OR owner IS NULL)`) and `ORDER BY owner DESC, created_at` so the
///   owner's OWN rows are preferred over legacy null-owner ("shared") rows.
/// * When `token_owner` is absent/empty (fails closed), restrict to `owner IS NULL`
///   shared rows only, `ORDER BY created_at`.
///
/// `owner DESC` puts non-NULL owners (the caller's own rows, since the WHERE already
/// limits to `caller`/NULL) before NULL in SQLite's NULLS-LAST default for DESC, so a
/// private row beats a shared row at equal/earlier `created_at` — matching the Python
/// `ModelEndpoint.owner.desc()`. The api_key is decrypted via `secret_storage::decrypt`
/// (the ORM `EncryptedText` read path); an empty/NULL stored key -> `None`. Does NOT
/// validate base_url — admin-configured local/LAN endpoints remain allowed.
fn select_api_chat_fallback_endpoint(token_owner: Option<&str>) -> Option<(String, Option<String>)> {
    // Python `if token_owner:` treats an empty-string owner as FALSY -> the null-owner
    // shared-only branch. Normalize `Some("")` to `None` to match.
    let token_owner = token_owner.filter(|o| !o.is_empty());
    let conn = crate::core::database::session_local().ok()?;
    let map_row = |r: &rusqlite::Row<'_>| -> rusqlite::Result<(String, Option<String>)> {
        Ok((r.get(0)?, r.get(1)?))
    };
    let row: Option<(String, Option<String>)> = match token_owner {
        // `owner_filter(query, ModelEndpoint, token_owner)` (include-shared) +
        // `.order_by(ModelEndpoint.owner.desc(), ModelEndpoint.created_at).first()`.
        Some(owner) => conn
            .query_row(
                "SELECT base_url, api_key FROM model_endpoints \
                 WHERE is_enabled = 1 AND (owner = ?1 OR owner IS NULL) \
                 ORDER BY owner DESC, created_at LIMIT 1",
                rusqlite::params![owner],
                map_row,
            )
            .optional_row()
            .ok()?,
        // `query.filter(ModelEndpoint.owner == None).order_by(ModelEndpoint.created_at).first()`.
        None => conn
            .query_row(
                "SELECT base_url, api_key FROM model_endpoints \
                 WHERE is_enabled = 1 AND owner IS NULL \
                 ORDER BY created_at LIMIT 1",
                rusqlite::params![],
                map_row,
            )
            .optional_row()
            .ok()?,
    };
    let (base, enc_key) = row?;
    let api_key = enc_key
        .filter(|k| !k.is_empty())
        .map(|k| crate::src::secret_storage::decrypt(&k));
    Some((base, api_key))
}

/// Map a `crate::src::llm_core::llm_call_async` `Err` to the FAITHFUL `HTTPException`
/// status + detail the Python `await llm_call_async(...)` (called with NO try/except)
/// would propagate.
///
/// `llm_call_async` now signals every upstream failure as a status-carrying
/// [`PyError::Upstream`](crate::error::PyError::Upstream) — the exact parallel of the
/// Python `raise HTTPException(status, detail)` it ports (503 cooldown / 503
/// connect-failure / the ACTUAL upstream `r.status_code` + friendly detail /
/// 502-after-retries / 502 schema). So the map is a straight pass-through of the
/// carried `(status, detail)`: `/v1/chat` surfaces the exact Python status + friendly
/// sentence, with NO message-prefix guessing.
///
/// Any other variant has no Python analog on this path (e.g. the `"client: {e}"`
/// reqwest-builder failure, which httpx never hits because its client is
/// process-wide); Python would surface it through FastAPI's default handler as a
/// generic **500**, so it maps to 500 without leaking the internal text (logged
/// server-side instead).
fn llm_call_status(e: crate::error::PyError) -> HttpException {
    match e {
        // `raise HTTPException(status, detail)` — propagate both verbatim.
        crate::error::PyError::Upstream { status, detail } => HttpException::new(status, detail),
        // No Python analog (e.g. the reqwest client-builder failure) — FastAPI's
        // default handler returns a generic 500 without leaking the internal text.
        other => {
            crate::pylog::error(&format!("sync_chat llm_call_async error: {other}"));
            HttpException::new(500, "Internal Server Error")
        }
    }
}

/// `_require_admin(request)` (core/middleware.py `require_admin`) — the per-handler
/// admin gate.
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

/// `w.events.split(",") if w.events else []` — the list-webhooks `events` field.
///
/// A NULL or empty stored `events` is falsy in Python -> `[]`. Otherwise a PLAIN
/// `str.split(",")` (no strip, no empty-filter — distinct from `validate_events`'s
/// cleaning split), so embedded empties are preserved as empty strings.
fn split_events(stored: Option<&str>) -> Vec<String> {
    match stored {
        Some(s) if !s.is_empty() => s.split(',').map(str::to_string).collect(),
        _ => Vec::new(),
    }
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
/// sibling `api_token_routes` / `compare_routes` ports use. FastAPI `Form(...)`
/// accepts both `application/x-www-form-urlencoded` and `multipart/form-data`; this
/// reads the multipart shape the webhook-management UI posts (`FormData`).
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

/// Open a DB connection (`SessionLocal()`), mapping a failure to a 500 (FastAPI's
/// default handler for an unhandled DB error).
fn session_local() -> Result<rusqlite::Connection, HttpException> {
    crate::core::database::session_local().map_err(db_500)
}

/// Map a `rusqlite::Error` to a 500 `HttpException`, the way an unhandled exception
/// inside a FastAPI handler surfaces.
fn db_500(e: rusqlite::Error) -> HttpException {
    crate::pylog::error(&format!("webhook_routes DB error: {e}"));
    HttpException::new(500, "Internal Server Error")
}

/// Map an `encrypt_api_key` failure to a 500 `HttpException`, the way the Python's
/// un-try/except `api_key_manager.encrypt_api_key(secret_val)` raise surfaces through
/// FastAPI's default exception handler: a GENERIC `500 "Internal Server Error"` with
/// the exception text logged server-side but NOT surfaced to the client. Same
/// generic-500 convention as [`db_500`].
fn encrypt_500(e: crate::error::PyError) -> HttpException {
    crate::pylog::error(&format!("webhook_routes encrypt error: {e}"));
    HttpException::new(500, "Internal Server Error")
}

/// `.first()`-style optional-row helper: `rusqlite::Error::QueryReturnedNoRows` ->
/// `Ok(None)` (SQLAlchemy `.first()` returns `None` for an empty result set); any
/// other error -> the standard 500 map.
trait OptionalRow<T> {
    fn optional_row(self) -> Result<Option<T>, HttpException>;
}

impl<T> OptionalRow<T> for rusqlite::Result<T> {
    fn optional_row(self) -> Result<Option<T>, HttpException> {
        match self {
            Ok(v) => Ok(Some(v)),
            Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
            Err(e) => Err(db_500(e)),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn split_events_falsy_is_empty() {
        // `w.events.split(",") if w.events else []`.
        assert_eq!(split_events(None), Vec::<String>::new());
        assert_eq!(split_events(Some("")), Vec::<String>::new());
    }

    #[test]
    fn split_events_plain_split_no_strip_or_filter() {
        // PLAIN `str.split(",")` — embedded empties preserved (distinct from
        // `validate_events`'s cleaning split).
        assert_eq!(
            split_events(Some("chat.completed,session.created")),
            vec!["chat.completed".to_string(), "session.created".to_string()]
        );
        // A trailing comma yields a trailing empty string (Python `"a,".split(",")
        // == ["a", ""]`).
        assert_eq!(
            split_events(Some("chat.completed,")),
            vec!["chat.completed".to_string(), "".to_string()]
        );
        // A single value, no comma.
        assert_eq!(
            split_events(Some("webhook.test")),
            vec!["webhook.test".to_string()]
        );
    }

    #[test]
    fn name_trim_truncate_and_empty_check() {
        // `name.strip()[:MAX_NAME_LEN]` then `if not name`.
        let resolve = |raw: &str| -> String { raw.trim().chars().take(MAX_NAME_LEN).collect() };
        assert_eq!(resolve("  My Hook  "), "My Hook");
        assert_eq!(resolve(""), "");
        assert_eq!(resolve("   "), "");
        // Truncation is by character to exactly 100.
        let long = "x".repeat(150);
        assert_eq!(resolve(&long).chars().count(), MAX_NAME_LEN);
    }

    #[test]
    fn secret_trim_truncate_and_or_none() {
        // `secret.strip()[:MAX_SECRET_LEN] or None`.
        let resolve = |raw: &str| -> Option<String> {
            let t: String = raw.trim().chars().take(MAX_SECRET_LEN).collect();
            if t.is_empty() {
                None
            } else {
                Some(t)
            }
        };
        assert_eq!(resolve("  "), None);
        assert_eq!(resolve(""), None);
        assert_eq!(resolve("  s3cr3t  "), Some("s3cr3t".to_string()));
        // Truncation is by character to exactly 256.
        let long = "y".repeat(300);
        assert_eq!(resolve(&long).unwrap().chars().count(), MAX_SECRET_LEN);
    }

    #[test]
    fn has_secret_is_bool_of_nonempty() {
        // `"has_secret": bool(w.secret)`.
        let has = |s: Option<&str>| s.map(|s| !s.is_empty()).unwrap_or(false);
        assert!(!has(None));
        assert!(!has(Some("")));
        assert!(has(Some("gAAAAA...")));
    }

    #[test]
    fn iso_or_null_renders_or_nulls() {
        assert_eq!(iso_or_null(None), Value::Null);
        assert_eq!(iso_or_null(Some("")), Value::Null);
        // Plain isoformat (T separator, no Z suffix — matches the Python).
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
    fn webhook_id_is_first_8_uuid_chars() {
        // `str(uuid.uuid4())[:8]`.
        let webhook_id: String = uuid::Uuid::new_v4().to_string().chars().take(8).collect();
        assert_eq!(webhook_id.chars().count(), 8);
        // No hyphen in the first 8 chars of a canonical UUID (8-4-4-4-12).
        assert!(!webhook_id.contains('-'));
    }

    #[test]
    fn chat_gate_absent_token_is_403() {
        // No extension stamped (cookie / bypass / exempt) -> `getattr(..., False)` ->
        // the not-a-token 403. An ABSENT `Option<Extension<ApiToken>>` collapses to the
        // default (`present = false`) in the handler.
        let err = chat_token_gate(&ApiToken::default()).unwrap_err();
        assert_eq!(err.status_code, 403);
        assert_eq!(err.detail, "This endpoint requires an API token");
    }

    #[test]
    fn chat_gate_token_without_chat_scope_is_403() {
        // A validated token that lacks the `chat` scope -> the scope 403.
        let tok = ApiToken {
            present: true,
            id: Some("abc12345".into()),
            owner: Some("alice".into()),
            scopes: vec!["memory".into()],
        };
        let err = chat_token_gate(&tok).unwrap_err();
        assert_eq!(err.status_code, 403);
        assert_eq!(err.detail, "API token is not scoped for chat");
    }

    #[test]
    fn chat_gate_chat_scoped_token_passes() {
        // A validated `chat`-scoped token clears the gate (Ok) — the handler then runs
        // the full session/endpoint/llm pipeline.
        let tok = ApiToken {
            present: true,
            id: Some("abc12345".into()),
            owner: Some("alice".into()),
            scopes: vec!["chat".into(), "memory".into()],
        };
        assert!(chat_token_gate(&tok).is_ok());
    }

    #[test]
    fn resolve_base_url_explicit_provider_wins() {
        // `if provider and provider.lower() in KNOWN_PROVIDERS: return KNOWN_PROVIDERS[...]`
        // — case-insensitive, and takes precedence over any model prefix.
        assert_eq!(
            resolve_base_url(Some("gpt-4o"), Some("DeepSeek")),
            Some("https://api.deepseek.com/v1")
        );
        // An unknown provider name falls through to the model-prefix scan.
        assert_eq!(
            resolve_base_url(Some("gpt-4o"), Some("nope")),
            Some("https://api.openai.com/v1")
        );
    }

    #[test]
    fn resolve_base_url_model_prefix_and_none() {
        // Model-prefix auto-detect (lower-cased, FIRST match wins).
        assert_eq!(resolve_base_url(Some("DeepSeek-Chat"), None), Some("https://api.deepseek.com/v1"));
        assert_eq!(resolve_base_url(Some("o3-mini"), None), Some("https://api.openai.com/v1"));
        assert_eq!(resolve_base_url(Some("llama-3-70b"), None), Some("https://api.groq.com/openai/v1"));
        assert_eq!(resolve_base_url(Some("mixtral-8x7b"), None), Some("https://api.groq.com/openai/v1"));
        // No provider, no recognized prefix -> None.
        assert_eq!(resolve_base_url(Some("some-unknown-model"), None), None);
        assert_eq!(resolve_base_url(None, None), None);
    }

    #[test]
    fn known_providers_includes_ollama_and_venice() {
        // The two providers added to KNOWN_PROVIDERS (parity with the upstream change).
        assert_eq!(known_provider("ollama"), Some("https://ollama.com/api"));
        assert_eq!(known_provider("venice"), Some("https://api.venice.ai/api/v1"));
        // Resolvable via the explicit `provider` field (case-insensitive).
        assert_eq!(
            resolve_base_url(None, Some("ollama")),
            Some("https://ollama.com/api")
        );
        assert_eq!(
            resolve_base_url(None, Some("Venice")),
            Some("https://api.venice.ai/api/v1")
        );
        // The pre-existing providers are untouched.
        assert_eq!(known_provider("openrouter"), Some("https://openrouter.ai/api/v1"));
        assert_eq!(known_provider("fireworks"), Some("https://api.fireworks.ai/inference/v1"));
    }

    #[test]
    fn caller_owns_session_strict_fail_closed() {
        // `_caller_owns_session(sess_owner, caller)`: a caller may resume ONLY when
        // the session owner matches EXACTLY, failing closed on every falsy edge.

        // Exact match -> allowed.
        assert!(caller_owns_session(Some("alice"), Some("alice")));
        // Owner mismatch -> denied.
        assert!(!caller_owns_session(Some("bob"), Some("alice")));
        // FAIL CLOSED: an unresolvable / empty caller is always denied — even against a
        // null/empty session owner (the key regression the new gate fixes).
        assert!(!caller_owns_session(None, None));
        assert!(!caller_owns_session(Some(""), None));
        assert!(!caller_owns_session(None, Some("")));
        assert!(!caller_owns_session(Some("alice"), None));
        assert!(!caller_owns_session(Some("alice"), Some("")));
        // FAIL CLOSED: a null/empty session owner (legacy / migrated rows) is NOT
        // resumable by an arbitrary chat-scoped token — the OLD `sess_owner and
        // sess_owner != caller` form would have ALLOWED this.
        assert!(!caller_owns_session(None, Some("alice")));
        assert!(!caller_owns_session(Some(""), Some("alice")));
    }

    // ── DB-backed: the owner-scoped Case-3 fallback selector (real temp DB) ──
    use crate::core::database::DB_TEST_LOCK;

    struct TmpDb(std::path::PathBuf);
    impl Drop for TmpDb {
        fn drop(&mut self) {
            let _ = std::fs::remove_file(&self.0);
        }
    }

    fn fresh_temp_db(tag: &str) -> TmpDb {
        let path = std::env::temp_dir().join(format!(
            "odysseus_webhook_routes_{tag}_{}_{}.db",
            std::process::id(),
            uuid::Uuid::new_v4().simple()
        ));
        let _ = std::fs::remove_file(&path);
        std::env::set_var("DATABASE_URL", format!("sqlite:///{}", path.display()));
        crate::core::database::create_all().unwrap();
        TmpDb(path)
    }

    /// Seed a `model_endpoints` row. `api_key` is stored NULL so the selector's
    /// decrypt path is skipped (the encryption key isn't needed for this owner-scope
    /// assertion). `created_at` is supplied explicitly to make the ORDER BY tie-break
    /// deterministic.
    fn seed_endpoint(
        conn: &rusqlite::Connection,
        id: &str,
        base_url: &str,
        enabled: bool,
        owner: Option<&str>,
        created_at: &str,
    ) {
        conn.execute(
            "INSERT INTO model_endpoints \
             (id, name, base_url, api_key, is_enabled, model_type, owner, created_at, updated_at) \
             VALUES (?1, 'ep', ?2, NULL, ?3, 'llm', ?4, ?5, ?5)",
            rusqlite::params![id, base_url, enabled as i64, owner, created_at],
        )
        .unwrap();
    }

    #[test]
    fn select_fallback_owner_scoped() {
        let _guard = DB_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let _db = fresh_temp_db("select_fallback");

        let (base_alice, base_shared, base_bob) = {
            let conn = crate::core::database::session_local().unwrap();
            // alice's own private endpoint, a legacy null-owner shared endpoint (earlier
            // created_at), and bob's private endpoint.
            seed_endpoint(&conn, "ep_shared", "https://shared.example.com/v1", true, None, "2026-01-01T00:00:00");
            seed_endpoint(&conn, "ep_alice", "https://alice.example.com/v1", true, Some("alice"), "2026-02-01T00:00:00");
            seed_endpoint(&conn, "ep_bob", "https://bob.example.com/v1", true, Some("bob"), "2026-03-01T00:00:00");

            // token_owner = alice -> her OWN row is PREFERRED over the shared row
            // (owner DESC tie-break), and bob's private row is never visible.
            let alice = select_api_chat_fallback_endpoint(Some("alice"));
            // token_owner = bob -> his own row.
            let bob = select_api_chat_fallback_endpoint(Some("bob"));
            // No token_owner (fails closed) -> ONLY the null-owner shared row.
            let shared = select_api_chat_fallback_endpoint(None);
            (alice, shared, bob)
        };

        assert_eq!(base_alice.map(|(u, _)| u), Some("https://alice.example.com/v1".to_string()));
        assert_eq!(base_bob.map(|(u, _)| u), Some("https://bob.example.com/v1".to_string()));
        assert_eq!(base_shared.map(|(u, _)| u), Some("https://shared.example.com/v1".to_string()));
    }

    #[test]
    fn select_fallback_no_owner_skips_private_rows() {
        let _guard = DB_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let _db = fresh_temp_db("select_fallback_priv");

        let result = {
            let conn = crate::core::database::session_local().unwrap();
            // Only a private (owned) endpoint exists — an owner-less token must NOT
            // fall back onto it (the SSRF/quota-spend regression the scoping fixes).
            seed_endpoint(&conn, "ep_alice", "https://alice.example.com/v1", true, Some("alice"), "2026-02-01T00:00:00");
            select_api_chat_fallback_endpoint(None)
        };
        assert!(result.is_none());
    }

    #[test]
    fn select_fallback_only_enabled() {
        let _guard = DB_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let _db = fresh_temp_db("select_fallback_enabled");

        let result = {
            let conn = crate::core::database::session_local().unwrap();
            // A DISABLED shared row must be skipped (the `is_enabled = 1` filter).
            seed_endpoint(&conn, "ep_off", "https://off.example.com/v1", false, None, "2026-01-01T00:00:00");
            select_api_chat_fallback_endpoint(None)
        };
        assert!(result.is_none());
    }

    #[test]
    fn llm_call_status_maps_faithful_python_statuses() {
        use crate::error::PyError;
        // A status-carrying `PyError::Upstream` — the parallel of the Python
        // `raise HTTPException(status, detail)` — propagates BOTH the status and
        // the friendly detail verbatim, no message-prefix guessing.

        // 503 connect-failure (`Cannot reach {host}: {e}`).
        let err = llm_call_status(PyError::upstream(
            503,
            "Cannot reach https://api.deepseek.com: connection refused",
        ));
        assert_eq!(err.status_code, 503);
        assert_eq!(
            err.detail,
            "Cannot reach https://api.deepseek.com: connection refused"
        );

        // 503 dead-host cooldown.
        let err = llm_call_status(PyError::upstream(
            503,
            "Upstream https://api.deepseek.com marked unreachable (cooldown active)",
        ));
        assert_eq!(err.status_code, 503);

        // The ACTUAL upstream status + friendly `_format_upstream_error` detail.
        let err = llm_call_status(PyError::upstream(
            429,
            "DeepSeek rate-limited the request (429). slow down",
        ));
        assert_eq!(err.status_code, 429);
        assert_eq!(err.detail, "DeepSeek rate-limited the request (429). slow down");
        let err = llm_call_status(PyError::upstream(
            401,
            "OpenAI rejected the API key — bad key. Check Model Endpoints → OpenAI and re-paste the key.",
        ));
        assert_eq!(err.status_code, 401);

        // 502 schema-parse and 502-after-retries.
        let err = llm_call_status(PyError::upstream(502, "Unexpected schema from http://x/chat: {}"));
        assert_eq!(err.status_code, 502);
        let err = llm_call_status(PyError::upstream(
            502,
            "POST http://x/chat failed after 3 attempts: x",
        ));
        assert_eq!(err.status_code, 502);

        // No Python analog (reqwest client-builder failure) -> generic 500, no leak.
        let err = llm_call_status(PyError::other("client: tls backend init failed"));
        assert_eq!(err.status_code, 500);
        assert_eq!(err.detail, "Internal Server Error");
    }

    #[test]
    fn router_mounts_all_paths() {
        // The factory yields a `Router<AppState>` mergeable with the inline subset
        // (no duplicate method+path); the webhook paths + `/api/v1/chat` are disjoint,
        // so building the router never panics.
        let base: Router<AppState> = Router::new();
        let _merged: Router<AppState> = base.merge(setup_webhook_routes());
    }
}
