// routes/compare_routes.rs  <- routes/compare_routes.py
//! Model A/B comparison routes (proof-batch unit P8).
//!
//! Faithful translation of `routes/compare_routes.py`'s `setup_compare_routes`
//! factory. Five handlers over the `comparisons` table (raw `rusqlite`, mirroring
//! the SQLAlchemy `Comparison` ORM), plus the `model_endpoints` API-key lookup and
//! `session_manager.create_session` / `update_session_headers` calls.
//!
//! ## Shape (the integration substrate)
//! * `setup_compare_routes() -> Router<AppState>` mirrors the Python factory; the
//!   only Python factory arg (`session_manager`) is reached through `AppState`, so
//!   the factory takes no params. The Python `APIRouter(prefix="/api/compare")`
//!   becomes absolute paths `/api/compare/...` on each `.route(...)`.
//! * Auth is `get_current_user` only — the load-bearing `None` case (anonymous
//!   callers) is preserved by reading `Option<Extension<CurrentUser>>`. There is
//!   no `require_user` / `Depends` here; ownership is enforced inline as
//!   `if user && comp.owner != user -> 404` (strict, NOT the inline `owns()`
//!   predicate), and `/history` filters `WHERE owner = ?` only when a user resolves.
//! * `raise HTTPException(s, d)` -> `return Err(HttpException::new(s, d))`; the
//!   `web`-gated `IntoResponse for HttpException` renders FastAPI's `{"detail": d}`.
//! * `prompt: str = Form(...)` etc. -> `multipart/form-data` parsed with the same
//!   manual field collector the inline `web/mod.rs` subset uses (FastAPI `Form(...)`
//!   accepts both urlencoded and multipart; the frontend posts `FormData`).
//! * `body: RecordVoteRequest` (pydantic) -> a local `#[derive(Deserialize)]`
//!   struct read from the JSON body.
//!
//! ## No path collision
//! Every route is under `/api/compare/*`, a prefix the inline `web/mod.rs` subset
//! never touches, so the aggregator merges this router without an axum
//! duplicate-`method`+`path` panic.


use std::collections::HashMap;

use axum::extract::{Multipart, Path, State};
use axum::response::{IntoResponse, Response};
use axum::routing::{delete, get, post};
use axum::{Extension, Json, Router};
use rusqlite::OptionalExtension;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::routes::{AppState, CurrentUser, HttpException};

/// `class RecordVoteRequest(BaseModel)` — the JSON body for `POST /api/compare/record`.
///
/// `is_blind` defaults to `True` exactly as the pydantic field default; serde's
/// `#[serde(default = ...)]` reproduces "absent -> True". `winner` is a model name
/// or the literal `"tie"`.
#[derive(Debug, Deserialize)]
struct RecordVoteRequest {
    prompt: String,
    models: Vec<String>,
    /// model name or `"tie"`.
    winner: String,
    #[serde(default = "default_true")]
    is_blind: bool,
}

fn default_true() -> bool {
    true
}

/// `setup_compare_routes(session_manager)` — assemble the comparison router.
///
/// The Python registers, in this order:
/// `POST /start`, `POST /{comp_id}/vote`, `POST /record`, `GET /history`,
/// `DELETE /{comp_id}`. axum matches static segments (`/record`, `/history`,
/// `/start`) ahead of the `/:comp_id` captures regardless of registration order,
/// so there is no ambiguity; we keep the Python source order anyway for fidelity.
pub fn setup_compare_routes() -> Router<AppState> {
    Router::new()
        .route("/api/compare/start", post(start_comparison))
        .route("/api/compare/:comp_id/vote", post(vote_comparison))
        .route("/api/compare/record", post(record_comparison))
        .route("/api/compare/history", get(list_comparisons))
        .route("/api/compare/:comp_id", delete(delete_comparison))
}

/// `POST /api/compare/start` — create two ephemeral sessions and a comparison record.
///
/// Returns the comparison ID and the two session IDs so the client can fire two
/// independent SSE streams to `/api/chat_stream`.
async fn start_comparison(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    mp: Multipart,
) -> Result<Response, HttpException> {
    let f = parse_form(mp).await;
    // `prompt: str = Form(...)` — a missing required Form field is a 422 in FastAPI.
    let prompt = require_form(&f, "prompt")?;
    let model_a = require_form(&f, "model_a")?;
    let model_b = require_form(&f, "model_b")?;
    let endpoint_a = require_form(&f, "endpoint_a")?;
    let endpoint_b = require_form(&f, "endpoint_b")?;
    // `is_blind: str = Form("true")` — defaulted string; the Python compares
    // `str(is_blind).lower() == "true"`.
    let is_blind_raw = f.get("is_blind").cloned().unwrap_or_else(|| "true".to_string());

    // `user = getattr(request.state, 'current_user', None)` — read once; the Python
    // re-reads it inside the loop but `request.state` is immutable across that loop,
    // so a single read is identical. `None` is the load-bearing anonymous case.
    let user: Option<String> = user.map(|Extension(CurrentUser(u))| u);

    let comp_id = uuid::Uuid::new_v4().to_string();
    let sid_a = uuid::Uuid::new_v4().to_string();
    let sid_b = uuid::Uuid::new_v4().to_string();

    // Blind mapping: randomly assign left/right.
    //
    // `blind = str(is_blind).lower() == "true"`. Computed BEFORE the session loop
    // (issue #1285) so the helper-session NAMES can be blinded by slot rather than
    // leaking the real model.
    let blind = is_blind_raw.to_lowercase() == "true";
    // `mapping = {"left": "a", "right": "b"}`; if blind and `random() > 0.5`, swap.
    // `random.random()` is `[0.0, 1.0)`; `rand::Rng::gen::<f64>()` is the same range,
    // so the `> 0.5` comparison has identical probability semantics.
    let mapping: Value = if blind {
        use rand::Rng;
        if rand::thread_rng().gen::<f64>() > 0.5 {
            json!({"left": "b", "right": "a"})
        } else {
            json!({"left": "a", "right": "b"})
        }
    } else {
        json!({"left": "a", "right": "b"})
    };

    // Map session IDs to left/right based on blind mapping.
    let left_is_a = mapping["left"] == json!("a");
    let right_is_a = mapping["right"] == json!("a");
    let session_left = if left_is_a { &sid_a } else { &sid_b };
    let session_right = if right_is_a { &sid_a } else { &sid_b };

    // In blind mode, name the helper sessions by their neutral slot
    // ("Model A" / "Model B") instead of the real model. Otherwise the session
    // name leaks the model in the sidebar and GET /api/sessions, de-anonymizing
    // the comparison before the user votes (issue #1285).
    //
    // `slot_name = {session_left: "Model A", session_right: "Model B"}`.
    let mut slot_name: HashMap<&str, &str> = HashMap::new();
    slot_name.insert(session_left.as_str(), "Model A");
    slot_name.insert(session_right.as_str(), "Model B");

    // Create ephemeral sessions (prefixed `[CMP]`).
    for (sid, model, endpoint) in [
        (&sid_a, &model_a, &endpoint_a),
        (&sid_b, &model_b, &endpoint_b),
    ] {
        // `name = f"[CMP] {slot_name[sid]}" if blind else f"[CMP] {model.split('/')[-1]}"`
        // — blind mode uses the neutral slot label; otherwise the segment after the
        // last `/` (the whole string when there is no `/`).
        let name = if blind {
            format!("[CMP] {}", slot_name[sid.as_str()])
        } else {
            let short = model.rsplit('/').next().unwrap_or(model);
            format!("[CMP] {short}")
        };
        if let Err(e) =
            s.sessions
                .create_session(sid, &name, endpoint, model, false, user.as_deref())
        {
            // `session_manager.create_session` raises on a DB error in Python; that
            // would surface as a 500 from FastAPI's default handler.
            return Err(HttpException::new(500, e.to_string()));
        }

        // Copy API key from endpoint config: find the matching `model_endpoints`
        // row by base_url (the endpoint URL run through `normalize_base`), and if it
        // has a key, build the session's auth headers via the endpoint_resolver
        // `build_headers` — selecting the MATCHED ROW's `base_url` for provider
        // detection (so Anthropic endpoints get `x-api-key`/`anthropic-version`
        // rather than a wrong `Authorization: Bearer`).
        //
        // Mirrors the Python:
        //   base = normalize_base(endpoint)
        //   ep = db.query(ModelEndpoint).filter(ModelEndpoint.base_url == base).first()
        //   if ep and ep.api_key:
        //       s.headers = build_headers(ep.api_key, ep.base_url)
        //
        // DEVIATION (documented): the Python sets `s.headers` on the in-memory
        // cached session object ONLY (no DB write). The Rust `SessionManager`
        // exposes `update_session_headers`, which writes both the in-memory cache
        // AND the `sessions.headers` column — the behavior the integration design
        // specifies (it lets the streaming chat read the auth straight off the
        // loaded row). The observable effect on the chat stream is identical (the
        // header is present); the only difference is durability, which is strictly
        // an improvement and matches every other ported create-session path.
        if let Some((base_url, api_key)) = endpoint_api_key(endpoint, user.as_deref()) {
            // `if ep and ep.api_key:` — only act when the matched row has a
            // non-empty (decrypted) key. `build_headers` itself returns `{}` for an
            // empty key, but the Python guard is on `ep.api_key`, so skip the write
            // entirely when there is no key (keeping the session's default headers).
            if let Some(api_key) = api_key.filter(|k| !k.is_empty()) {
                let headers = crate::src::endpoint_resolver::build_headers(
                    Some(&api_key),
                    &base_url,
                );
                s.sessions.update_session_headers(sid, headers);
            }
        }
    }

    // Store comparison record.
    let blind_mapping = serde_json::to_string(&mapping).unwrap_or_else(|_| "{}".to_string());
    let conn = session_local()?;
    let now = crate::pydatetime::utcnow_naive_iso();
    // `Comparison(id, prompt, model_a, model_b, endpoint_a, endpoint_b, is_blind,
    //             blind_mapping, owner)` + the TimestampMixin `created_at`/`updated_at`.
    conn.execute(
        "INSERT INTO comparisons \
           (id, prompt, model_a, model_b, endpoint_a, endpoint_b, is_blind, blind_mapping, owner, \
            created_at, updated_at) \
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?10)",
        rusqlite::params![
            comp_id,
            prompt,
            model_a,
            model_b,
            endpoint_a,
            endpoint_b,
            blind,
            blind_mapping,
            user,
            now,
        ],
    )
    .map_err(db_500)?;

    // In blind mode, withhold the model identities AND the left/right mapping from
    // the response. The client already knows model_a/model_b (it sent them), so
    // returning either would defeat blind mode. They are revealed by
    // POST /api/compare/{id}/vote once the user has voted (#1285).
    //
    // `"model_left": None if blind else (...)`, `"mapping": None if blind else mapping`.
    let model_left: Value = if blind {
        Value::Null
    } else if left_is_a {
        json!(model_a)
    } else {
        json!(model_b)
    };
    let model_right: Value = if blind {
        Value::Null
    } else if right_is_a {
        json!(model_a)
    } else {
        json!(model_b)
    };
    let mapping_out: Value = if blind { Value::Null } else { mapping };

    Ok(Json(json!({
        "id": comp_id,
        "session_left": session_left,
        "session_right": session_right,
        "model_left": model_left,
        "model_right": model_right,
        "is_blind": blind,
        "mapping": mapping_out,
    }))
    .into_response())
}

/// `POST /api/compare/{comp_id}/vote` — record the user's vote and reveal model
/// names if blind.
// mirrors Python compare_routes.py: the 5-column SELECT row tuple is a faithful 1:1
// of the DB row shape, so the tuple is kept rather than wrapped in a named struct.
#[allow(clippy::type_complexity)]
async fn vote_comparison(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(comp_id): Path<String>,
    mp: Multipart,
) -> Result<Response, HttpException> {
    let _ = &s; // `session_manager` is unused here (mirrors Python: the factory
                // captures it but `vote_comparison` never touches it). Kept so the
                // State extractor wires the same AppState all handlers share.
    let f = parse_form(mp).await;
    // `winner: str = Form(...)` — `"left"`, `"right"`, or `"tie"`.
    let winner = require_form(&f, "winner")?;
    let user: Option<String> = user.map(|Extension(CurrentUser(u))| u);

    let conn = session_local()?;
    // `comp = db.query(Comparison).filter(Comparison.id == comp_id).first()`
    let row: Option<(Option<String>, Option<String>, Option<String>, String, String)> = conn
        .query_row(
            "SELECT owner, winner, blind_mapping, model_a, model_b \
             FROM comparisons WHERE id = ?1",
            rusqlite::params![comp_id],
            |r| {
                Ok((
                    r.get::<_, Option<String>>(0)?,
                    r.get::<_, Option<String>>(1)?,
                    r.get::<_, Option<String>>(2)?,
                    r.get::<_, String>(3)?,
                    r.get::<_, String>(4)?,
                ))
            },
        )
        .optional()
        .map_err(db_500)?;

    // `if not comp: raise HTTPException(404, "Comparison not found")`
    let (owner, existing_winner, blind_mapping_raw, model_a, model_b) = match row {
        Some(r) => r,
        None => return Err(HttpException::new(404, "Comparison not found")),
    };

    // SECURITY: strict ownership — `if user and comp.owner != user: 404`.
    if let Some(u) = user.as_deref() {
        if owner.as_deref() != Some(u) {
            return Err(HttpException::new(404, "Comparison not found"));
        }
    }
    // `if comp.winner: raise HTTPException(400, "Already voted")`
    if existing_winner
        .as_deref()
        .map(|w| !w.is_empty())
        .unwrap_or(false)
    {
        return Err(HttpException::new(400, "Already voted"));
    }

    // `mapping = json.loads(comp.blind_mapping) if comp.blind_mapping else {"left":"a","right":"b"}`
    let mapping: Value = blind_mapping_raw
        .as_deref()
        .filter(|s| !s.is_empty())
        .and_then(|s| serde_json::from_str::<Value>(s).ok())
        .unwrap_or_else(|| json!({"left": "a", "right": "b"}));

    // Resolve the recorded winner from the (possibly blind) mapping.
    let new_winner: String = match winner.as_str() {
        "tie" => "tie".to_string(),
        "left" => mapping["left"].as_str().unwrap_or("").to_string(),
        "right" => mapping["right"].as_str().unwrap_or("").to_string(),
        // `else: raise HTTPException(400, "winner must be 'left', 'right', or 'tie'")`
        _ => {
            return Err(HttpException::new(
                400,
                "winner must be 'left', 'right', or 'tie'",
            ))
        }
    };

    // `comp.winner = ...; comp.voted_at = datetime.utcnow(); db.commit()`.
    // `voted_at` assignment triggers the `onupdate=datetime.utcnow` `updated_at`
    // bump too, so we set both to the same `utcnow`.
    let now = crate::pydatetime::utcnow_naive_iso();
    conn.execute(
        "UPDATE comparisons SET winner = ?1, voted_at = ?2, updated_at = ?2 WHERE id = ?3",
        rusqlite::params![new_winner, now, comp_id],
    )
    .map_err(db_500)?;

    let left_is_a = mapping["left"] == json!("a");
    let right_is_a = mapping["right"] == json!("a");
    Ok(Json(json!({
        "winner": new_winner,
        "model_a": model_a,
        "model_b": model_b,
        "revealed": {
            "left": if left_is_a { &model_a } else { &model_b },
            "right": if right_is_a { &model_a } else { &model_b },
        },
    }))
    .into_response())
}

/// `POST /api/compare/record` — lightweight endpoint to record a comparison vote
/// from the frontend (JSON body).
async fn record_comparison(
    user: Option<Extension<CurrentUser>>,
    Json(body): Json<RecordVoteRequest>,
) -> Result<Response, HttpException> {
    let user: Option<String> = user.map(|Extension(CurrentUser(u))| u);
    let comp_id = uuid::Uuid::new_v4().to_string();

    // `model_a = body.models[0] if len > 0 else ""`, same for `model_b` at index 1.
    let model_a = body.models.first().cloned().unwrap_or_default();
    let model_b = body.models.get(1).cloned().unwrap_or_default();

    // For N>2 models, store the full list as JSON in `blind_mapping`; else NULL.
    let blind_mapping: Option<String> = if body.models.len() > 2 {
        Some(
            serde_json::to_string(&json!({"models": body.models}))
                .unwrap_or_else(|_| "{}".to_string()),
        )
    } else {
        None
    };

    // `prompt=body.prompt[:500]` — first 500 *characters* (Python slices by code
    // point), so truncate on a char boundary, not a byte boundary.
    let prompt: String = body.prompt.chars().take(500).collect();

    let conn = session_local()?;
    let now = crate::pydatetime::utcnow_naive_iso();
    // `Comparison(id, prompt[:500], model_a, model_b, endpoint_a="", endpoint_b="",
    //             winner=body.winner, is_blind=body.is_blind, blind_mapping,
    //             voted_at=datetime.utcnow(), owner)`.
    conn.execute(
        "INSERT INTO comparisons \
           (id, prompt, model_a, model_b, endpoint_a, endpoint_b, winner, is_blind, \
            blind_mapping, voted_at, owner, created_at, updated_at) \
         VALUES (?1, ?2, ?3, ?4, '', '', ?5, ?6, ?7, ?8, ?9, ?8, ?8)",
        rusqlite::params![
            comp_id,
            prompt,
            model_a,
            model_b,
            body.winner,
            body.is_blind,
            blind_mapping,
            now,
            user,
        ],
    )
    .map_err(db_500)?;

    Ok(Json(json!({"status": "ok", "id": comp_id})).into_response())
}

/// `GET /api/compare/history` — list past comparisons (owner-scoped, newest 50).
async fn list_comparisons(user: Option<Extension<CurrentUser>>) -> Result<Response, HttpException> {
    let user: Option<String> = user.map(|Extension(CurrentUser(u))| u);
    let conn = session_local()?;

    // `q = db.query(Comparison); if user: q = q.filter(Comparison.owner == user)`
    // `comps = q.order_by(Comparison.created_at.desc()).limit(50).all()`.
    let sql = if user.is_some() {
        "SELECT id, prompt, model_a, model_b, winner, is_blind, voted_at, created_at \
         FROM comparisons WHERE owner = ?1 ORDER BY created_at DESC LIMIT 50"
    } else {
        "SELECT id, prompt, model_a, model_b, winner, is_blind, voted_at, created_at \
         FROM comparisons ORDER BY created_at DESC LIMIT 50"
    };
    let mut stmt = conn.prepare(sql).map_err(db_500)?;

    let map_row = |r: &rusqlite::Row<'_>| -> rusqlite::Result<Value> {
        let id: String = r.get(0)?;
        let prompt: String = r.get(1)?;
        let model_a: String = r.get(2)?;
        let model_b: String = r.get(3)?;
        let winner: Option<String> = r.get(4)?;
        // `is_blind` is stored as 0/1 (SQLAlchemy Boolean); read as bool.
        let is_blind: bool = r.get(5)?;
        let voted_at: Option<String> = r.get(6)?;
        let created_at: Option<String> = r.get(7)?;
        Ok(json!({
            // `c.prompt[:100]` — first 100 characters.
            "id": id,
            "prompt": prompt.chars().take(100).collect::<String>(),
            "model_a": model_a,
            "model_b": model_b,
            "winner": winner,
            "is_blind": is_blind,
            // `.isoformat() if c.x else None`.
            "voted_at": iso_or_null(voted_at.as_deref()),
            "created_at": iso_or_null(created_at.as_deref()),
        }))
    };

    let rows: Vec<Value> = if let Some(u) = user.as_deref() {
        stmt.query_map(rusqlite::params![u], map_row)
            .map_err(db_500)?
            .collect::<rusqlite::Result<Vec<_>>>()
            .map_err(db_500)?
    } else {
        stmt.query_map([], map_row)
            .map_err(db_500)?
            .collect::<rusqlite::Result<Vec<_>>>()
            .map_err(db_500)?
    };

    Ok(Json(Value::Array(rows)).into_response())
}

/// `DELETE /api/compare/{comp_id}` — delete a comparison (strict owner scope).
async fn delete_comparison(
    user: Option<Extension<CurrentUser>>,
    Path(comp_id): Path<String>,
) -> Result<Response, HttpException> {
    let user: Option<String> = user.map(|Extension(CurrentUser(u))| u);
    let conn = session_local()?;

    // `comp = db.query(Comparison).filter(Comparison.id == comp_id).first()`
    let owner: Option<Option<String>> = conn
        .query_row(
            "SELECT owner FROM comparisons WHERE id = ?1",
            rusqlite::params![comp_id],
            |r| r.get::<_, Option<String>>(0),
        )
        .optional()
        .map_err(db_500)?;

    // `if not comp: raise HTTPException(404, "Comparison not found")`
    let owner = match owner {
        Some(o) => o,
        None => return Err(HttpException::new(404, "Comparison not found")),
    };
    // SECURITY: strict ownership — `if user and comp.owner != user: 404`.
    if let Some(u) = user.as_deref() {
        if owner.as_deref() != Some(u) {
            return Err(HttpException::new(404, "Comparison not found"));
        }
    }

    // `db.delete(comp); db.commit()`.
    conn.execute(
        "DELETE FROM comparisons WHERE id = ?1",
        rusqlite::params![comp_id],
    )
    .map_err(db_500)?;
    Ok(Json(json!({"status": "deleted"})).into_response())
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Collect all `multipart/form-data` text fields into a map — the same collector
/// the inline `web/mod.rs` subset uses for its `Form(...)` handlers. FastAPI
/// `Form(...)` accepts both `application/x-www-form-urlencoded` and
/// `multipart/form-data`; the compare frontend posts `FormData` (multipart).
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

/// A required `Form(...)` field. A missing required Form field is a 422 in FastAPI
/// (the request-validation error). We surface the same status with the field name
/// so the failure mode matches.
fn require_form(f: &HashMap<String, String>, key: &str) -> Result<String, HttpException> {
    f.get(key)
        .cloned()
        .ok_or_else(|| HttpException::new(422, format!("Field required: {key}")))
}

/// Open a DB connection (`SessionLocal()`), mapping a failure to a 500 (FastAPI's
/// default handler for an unhandled DB error). The Python opens `SessionLocal()`
/// directly; a connection error is unexpected and would bubble to a 500.
fn session_local() -> Result<rusqlite::Connection, HttpException> {
    crate::core::database::session_local().map_err(db_500)
}

/// Map a `rusqlite::Error` to a 500 `HttpException`, the way an unhandled
/// exception inside a FastAPI handler surfaces.
fn db_500(e: rusqlite::Error) -> HttpException {
    crate::pylog::error(&format!("compare_routes DB error: {e}"));
    HttpException::new(500, "Internal Server Error")
}

/// Find the `model_endpoints` row whose `base_url` matches the comparison
/// `endpoint` run through `endpoint_resolver::normalize_base`, and return that
/// matched row's stored `base_url` together with its DECRYPTED `api_key` (if any).
///
/// The caller feeds the returned `base_url` into `build_headers` so the auth
/// shape follows the matched endpoint's provider (Anthropic -> `x-api-key` +
/// `anthropic-version`; otherwise `Authorization: Bearer`).
///
/// Mirrors:
/// ```python
/// base = normalize_base(endpoint)
/// ep = db.query(ModelEndpoint).filter(
///     ModelEndpoint.base_url == base
/// ).first()
/// if ep and ep.api_key:
///     s.headers = build_headers(ep.api_key, ep.base_url)
/// ```
/// `EncryptedText` decrypts on read in SQLAlchemy, so the returned key is the
/// PLAINTEXT key — hence `secret_storage::decrypt` on the stored ciphertext.
/// A missing row -> `None` (the session keeps its default empty headers, exactly
/// as when no endpoint matches); a DB hiccup is also `None` (best-effort, mirroring
/// the Python `try/finally` whose realistic path is just "no row"). The api_key
/// component is `None` for a NULL/empty stored key (the `if ep and ep.api_key`
/// guard is then re-applied by the caller).
fn endpoint_api_key(endpoint: &str, owner: Option<&str>) -> Option<(String, Option<String>)> {
    let base = crate::src::endpoint_resolver::normalize_base(Some(endpoint));
    let conn = crate::core::database::session_local().ok()?;
    // `_owned_endpoint_by_url` — owner-scoped (`owner_filter`): the caller's own
    // rows + legacy null-owner "shared" rows, so a comparison can't borrow ANOTHER
    // user's private endpoint's decrypted api_key / base_url. Empty/None owner is a
    // no-op (single-user / legacy mode).
    let owner = owner.filter(|o| !o.is_empty());
    let row_opt: Option<(String, Option<String>)> = match owner {
        Some(o) => conn
            .query_row(
                "SELECT base_url, api_key FROM model_endpoints \
                 WHERE base_url = ?1 AND (owner = ?2 OR owner IS NULL)",
                rusqlite::params![base, o],
                |r| Ok((r.get::<_, String>(0)?, r.get::<_, Option<String>>(1)?)),
            )
            .optional()
            .ok()?,
        None => conn
            .query_row(
                "SELECT base_url, api_key FROM model_endpoints WHERE base_url = ?1",
                rusqlite::params![base],
                |r| Ok((r.get::<_, String>(0)?, r.get::<_, Option<String>>(1)?)),
            )
            .optional()
            .ok()?,
    };
    let (row_base_url, enc) = row_opt.map(|(b, k)| (b, k.filter(|k| !k.is_empty())))?;
    let api_key = enc.map(|k| crate::src::secret_storage::decrypt(&k));
    Some((row_base_url, api_key))
}

/// `c.<dt>.isoformat() if c.<dt> else None` — render a stored SQLite datetime
/// string with Python's `isoformat`, or `null` when absent.
fn iso_or_null(stored: Option<&str>) -> Value {
    match stored.filter(|s| !s.is_empty()) {
        Some(s) => json!(crate::pydatetime::to_isoformat(s)),
        None => Value::Null,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn record_request_defaults_is_blind_true() {
        // pydantic: `is_blind: bool = True`. Absent -> True.
        let body: RecordVoteRequest =
            serde_json::from_value(json!({"prompt": "hi", "models": ["a", "b"], "winner": "a"}))
                .unwrap();
        assert!(body.is_blind);
        assert_eq!(body.winner, "a");
        assert_eq!(body.models, vec!["a".to_string(), "b".to_string()]);

        // Explicit false is respected.
        let body2: RecordVoteRequest = serde_json::from_value(
            json!({"prompt": "x", "models": ["m"], "winner": "tie", "is_blind": false}),
        )
        .unwrap();
        assert!(!body2.is_blind);
    }

    #[test]
    fn require_form_missing_is_422() {
        let mut f = HashMap::new();
        f.insert("prompt".to_string(), "p".to_string());
        assert_eq!(require_form(&f, "prompt").unwrap(), "p");
        let e = require_form(&f, "model_a").unwrap_err();
        assert_eq!(e.status_code, 422);
        assert!(e.detail.contains("model_a"));
    }

    #[test]
    fn is_blind_string_compare_matches_python() {
        // `str(is_blind).lower() == "true"`.
        assert!("true".to_lowercase() == "true");
        assert!("TRUE".to_lowercase() == "true");
        assert!("false".to_lowercase() != "true");
        assert!("0".to_lowercase() != "true");
    }

    #[test]
    fn cmp_session_name_is_last_path_segment() {
        // `name=f"[CMP] {model.split('/')[-1]}"`.
        let short = "anthropic/claude-3".rsplit('/').next().unwrap();
        assert_eq!(format!("[CMP] {short}"), "[CMP] claude-3");
        // No slash -> the whole string.
        let plain = "gpt-4o".rsplit('/').next().unwrap();
        assert_eq!(format!("[CMP] {plain}"), "[CMP] gpt-4o");
    }

    #[test]
    fn blind_mode_session_name_uses_neutral_slot_not_model() {
        // Issue #1285: in blind mode the helper-session name must be the neutral
        // slot ("Model A"/"Model B") keyed by session id, NOT the real model — so
        // the sidebar / GET /api/sessions can't de-anonymize the comparison.
        let sid_a = "sid-a".to_string();
        let sid_b = "sid-b".to_string();
        // A swapped mapping (random() > 0.5): left -> b, right -> a.
        let mapping = json!({"left": "b", "right": "a"});
        let left_is_a = mapping["left"] == json!("a");
        let right_is_a = mapping["right"] == json!("a");
        let session_left = if left_is_a { &sid_a } else { &sid_b };
        let session_right = if right_is_a { &sid_a } else { &sid_b };

        let mut slot_name: HashMap<&str, &str> = HashMap::new();
        slot_name.insert(session_left.as_str(), "Model A");
        slot_name.insert(session_right.as_str(), "Model B");

        // With the swap, sid_b is the left slot ("Model A") and sid_a the right
        // ("Model B"). The name carries the slot, never the real model.
        assert_eq!(format!("[CMP] {}", slot_name["sid-b"]), "[CMP] Model A");
        assert_eq!(format!("[CMP] {}", slot_name["sid-a"]), "[CMP] Model B");
        // Neither name contains the model identity placeholder a/the real model.
        assert!(!slot_name["sid-a"].contains("claude"));
    }

    #[test]
    fn blind_mode_response_withholds_model_identities_and_mapping() {
        // Issue #1285: the /start response must blank model_left/model_right/mapping
        // in blind mode (revealed only at /vote).
        let model_a = "anthropic/claude".to_string();
        let model_b = "openai/gpt".to_string();
        let mapping = json!({"left": "b", "right": "a"});
        let left_is_a = mapping["left"] == json!("a");
        let right_is_a = mapping["right"] == json!("a");

        for blind in [true, false] {
            let model_left: Value = if blind {
                Value::Null
            } else if left_is_a {
                json!(model_a)
            } else {
                json!(model_b)
            };
            let model_right: Value = if blind {
                Value::Null
            } else if right_is_a {
                json!(model_a)
            } else {
                json!(model_b)
            };
            let mapping_out: Value = if blind { Value::Null } else { mapping.clone() };

            if blind {
                assert_eq!(model_left, Value::Null);
                assert_eq!(model_right, Value::Null);
                assert_eq!(mapping_out, Value::Null);
            } else {
                // Non-blind: identities and mapping are returned as before.
                assert_eq!(model_left, json!("openai/gpt")); // left -> b
                assert_eq!(model_right, json!("anthropic/claude")); // right -> a
                assert_eq!(mapping_out, mapping);
            }
        }
    }

    #[test]
    fn blind_mapping_resolves_left_right() {
        // The vote/reveal logic: given a (possibly swapped) mapping, resolve which
        // physical model each visual side maps to.
        let mapping = json!({"left": "b", "right": "a"});
        assert!(mapping["left"] != json!("a")); // left -> model_b
        assert!(mapping["right"] == json!("a")); // right -> model_a

        // winner="left" with a swapped mapping records the physical "b".
        let recorded = match "left" {
            "tie" => "tie",
            "left" => mapping["left"].as_str().unwrap(),
            "right" => mapping["right"].as_str().unwrap(),
            _ => "",
        };
        assert_eq!(recorded, "b");
    }

    #[test]
    fn endpoint_base_url_uses_normalize_base() {
        // The model_endpoints lookup now matches on `normalize_base(endpoint)`
        // (the same helper every other resolver uses), not a bare
        // `.replace('/chat/completions', '')`. It strips the known API suffix and
        // any trailing slash.
        use crate::src::endpoint_resolver::normalize_base;
        assert_eq!(
            normalize_base(Some("http://h/v1/chat/completions")),
            "http://h/v1"
        );
        // Idempotent when already a base URL.
        assert_eq!(normalize_base(Some("http://h/v1")), "http://h/v1");
        // Trailing slash is trimmed (the old `.replace` would NOT have done this).
        assert_eq!(normalize_base(Some("http://h/v1/")), "http://h/v1");
    }

    #[test]
    fn record_prompt_truncates_to_500_chars() {
        let long = "x".repeat(600);
        let truncated: String = long.chars().take(500).collect();
        assert_eq!(truncated.chars().count(), 500);
    }

    #[test]
    fn iso_or_null_renders_or_nulls() {
        assert_eq!(iso_or_null(None), Value::Null);
        assert_eq!(iso_or_null(Some("")), Value::Null);
        // A stored SQLite datetime renders with Python's isoformat (T separator).
        assert_eq!(
            iso_or_null(Some("2026-06-01 12:30:00")),
            json!("2026-06-01T12:30:00")
        );
    }
}
