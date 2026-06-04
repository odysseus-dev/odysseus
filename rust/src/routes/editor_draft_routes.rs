// routes/editor_draft_routes.rs  <- routes/editor_draft_routes.py
//! Editor draft routes — persisted in-progress gallery-editor sessions (routes WAVE 2).
//!
//! The gallery editor (image canvas) lets users layer edits on top of a photo (or a
//! blank canvas). Persisting those layered sessions to the server makes them survive
//! cache clears and roams across devices — unlike the legacy per-image localStorage
//! drafts.
//!
//! Each draft carries:
//!   - id           — opaque uuid (the client never sees gallery-image ids as draft
//!                    ids, so blank-canvas drafts work too)
//!   - source_image_id (nullable) — back-pointer for "this draft started as an edit
//!                    of GalleryImage X"
//!   - payload      — full JSON snapshot (layers as base64 PNG dataURLs, offsets,
//!                    opacities, etc.) the editor knows how to rehydrate
//!   - thumbnail    — small data URL for the landing-list grid
//!
//! Faithful translation of `routes/editor_draft_routes.py`'s `setup_editor_draft_routes`
//! factory. Five handlers over the `editor_drafts` table (raw `rusqlite`, mirroring
//! the SQLAlchemy `EditorDraft` ORM).
//!
//! ## Shape (the integration substrate)
//! * `setup_editor_draft_routes() -> Router<AppState>` mirrors the Python factory,
//!   which takes no args. The Python `APIRouter(tags=["editor-drafts"])` has **no**
//!   `prefix`, so every handler already carries its absolute `/api/editor-drafts...`
//!   path; the `.route(...)` calls use those paths verbatim.
//! * Auth is `get_current_user` only — the load-bearing `None` case (auth disabled /
//!   single-user) is preserved by reading `Option<Extension<CurrentUser>>`. There is
//!   no `require_user` / `Depends`. Ownership is enforced inline via the `_owns`
//!   helper exactly as the Python does: `None` user -> owns everything, otherwise the
//!   row's `owner` must equal the user (a not-found / not-owned row -> 404).
//! * `raise HTTPException(s, d)` -> `return Err(HttpException::new(s, d))`; the
//!   `web`-gated `IntoResponse for HttpException` renders FastAPI's `{"detail": d}`.
//! * `body: DraftCreate` / `body: DraftUpdate` (pydantic) -> local
//!   `#[derive(Deserialize)]` structs read from the JSON body. The required `payload`
//!   on `DraftCreate` is a 422 in FastAPI when absent, which the JSON extractor
//!   surfaces.
//! * `payload` is stored as a JSON string (`json.dumps`) in a TEXT column and
//!   re-parsed (`json.loads`) on read, exactly as the Python does.
//!
//! ## No path collision
//! Every route is under `/api/editor-drafts*`, a prefix the inline `web/mod.rs`
//! subset never touches, so the aggregator merges this router without an axum
//! duplicate-`method`+`path` panic.


use axum::extract::{Path, State};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::{Extension, Json, Router};
use rusqlite::OptionalExtension;
use serde::Deserialize;
use serde_json::{json, Map, Value};

use crate::routes::{AppState, CurrentUser, HttpException};

/// `class DraftCreate(BaseModel)` — the JSON body for `POST /api/editor-drafts`.
///
/// `payload` is required (a missing `payload` is a 422 in FastAPI); `name`,
/// `source_image_id`, `width`, `height`, `thumbnail` are all optional. serde's
/// `#[serde(default)]` reproduces "absent -> None" for the optionals.
#[derive(Debug, Deserialize)]
struct DraftCreate {
    #[serde(default)]
    name: Option<String>,
    #[serde(default)]
    source_image_id: Option<String>,
    #[serde(default)]
    width: Option<i64>,
    #[serde(default)]
    height: Option<i64>,
    payload: Value,
    #[serde(default)]
    thumbnail: Option<String>,
}

/// `class DraftUpdate(BaseModel)` — the JSON body for `PUT /api/editor-drafts/{id}`.
///
/// Every field is optional; only the ones present (`is not None`) are applied. Note
/// `payload` here is `Optional[Dict[str, Any]] = None` — distinct from the required
/// `DraftCreate.payload`. serde's `#[serde(default)]` reproduces "absent -> None".
#[derive(Debug, Deserialize)]
struct DraftUpdate {
    #[serde(default)]
    name: Option<String>,
    #[serde(default)]
    width: Option<i64>,
    #[serde(default)]
    height: Option<i64>,
    #[serde(default)]
    payload: Option<Value>,
    #[serde(default)]
    thumbnail: Option<String>,
}

/// An `editor_drafts` row, as much of it as `_owns`/`_summary` need. Mirrors the
/// columns the SQLAlchemy `EditorDraft` ORM loads when a handler reads `d`.
struct DraftRow {
    id: String,
    owner: Option<String>,
    name: Option<String>,
    source_image_id: Option<String>,
    width: Option<i64>,
    height: Option<i64>,
    thumbnail: Option<String>,
    created_at: Option<String>,
    updated_at: Option<String>,
}

/// `def _owns(d, user) -> bool` — ownership predicate.
///
/// ```python
/// if user is None:
///     return True
/// return (d.owner or None) == user
/// ```
///
/// When `user is None` (auth disabled / single-user) everything is owned. Otherwise
/// the row's owner — coerced from the empty string to `None` via `(d.owner or None)`
/// — must equal the user. An empty-string owner therefore never equals a non-empty
/// user, so `_owns` is `False` there.
fn _owns(d: &DraftRow, user: Option<&str>) -> bool {
    match user {
        None => true,
        Some(u) => owner_or_none(d.owner.as_deref()) == Some(u),
    }
}

/// `(d.owner or None)` — Python's truthiness coercion: `None`/`""` both become
/// `None`, any non-empty string stays itself.
fn owner_or_none(owner: Option<&str>) -> Option<&str> {
    match owner {
        Some(o) if !o.is_empty() => Some(o),
        _ => None,
    }
}

/// `def _summary(d) -> Dict[str, Any]` — list-view representation; **omits** the
/// bulky `payload` field.
///
/// ```python
/// return {
///     "id": d.id,
///     "name": d.name or "Untitled",
///     "source_image_id": d.source_image_id,
///     "width": d.width,
///     "height": d.height,
///     "thumbnail": d.thumbnail,
///     "created_at": d.created_at.isoformat() if d.created_at else None,
///     "updated_at": d.updated_at.isoformat() if d.updated_at else None,
/// }
/// ```
///
/// Key order matches the Python dict literal exactly (`serde_json::json!` preserves
/// insertion order because the crate is built with the `preserve_order` feature).
/// `d.name or "Untitled"` applies Python truthiness: `None`/`""` -> `"Untitled"`.
fn _summary(d: &DraftRow) -> Value {
    json!({
        "id": d.id,
        "name": name_or_untitled(d.name.as_deref()),
        "source_image_id": d.source_image_id,
        "width": d.width,
        "height": d.height,
        "thumbnail": d.thumbnail,
        "created_at": iso_or_null(d.created_at.as_deref()),
        "updated_at": iso_or_null(d.updated_at.as_deref()),
    })
}

/// `d.name or "Untitled"` — Python truthiness: `None` or the empty string fall back
/// to `"Untitled"`.
fn name_or_untitled(name: Option<&str>) -> String {
    match name {
        Some(n) if !n.is_empty() => n.to_string(),
        _ => "Untitled".to_string(),
    }
}

/// `setup_editor_draft_routes()` — assemble the editor-drafts router.
///
/// The Python registers, in this order: `GET /api/editor-drafts`,
/// `GET /api/editor-drafts/{draft_id}`, `POST /api/editor-drafts`,
/// `PUT /api/editor-drafts/{draft_id}`, `DELETE /api/editor-drafts/{draft_id}`. The
/// `APIRouter` has no `prefix`, so the absolute paths are written directly on each
/// `.route(...)`. axum matches the static `/api/editor-drafts` collection route and
/// the `/:draft_id` capture without ambiguity; we keep the Python source order anyway
/// for fidelity. The crate is axum 0.7, so the capture uses the colon form
/// `/:draft_id` (never braces).
pub fn setup_editor_draft_routes() -> Router<AppState> {
    Router::new()
        .route(
            "/api/editor-drafts",
            get(list_drafts).post(create_draft),
        )
        .route(
            "/api/editor-drafts/:draft_id",
            get(get_draft).put(update_draft).delete(delete_draft),
        )
}

/// `GET /api/editor-drafts` — list the caller's active drafts, newest first.
///
/// ```python
/// q = db.query(EditorDraft).filter(EditorDraft.is_active == True)
/// if user is not None:
///     q = q.filter(EditorDraft.owner == user)
/// rows = q.order_by(EditorDraft.updated_at.desc()).limit(200).all()
/// return {"drafts": [_summary(d) for d in rows]}
/// ```
///
/// `user is None` (auth disabled) returns every active draft; a resolved user scopes
/// strictly to `owner == user`.
async fn list_drafts(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    let user: Option<String> = user.map(|Extension(CurrentUser(u))| u);
    let conn = session_local()?;

    let sql = if user.is_some() {
        "SELECT id, owner, name, source_image_id, width, height, thumbnail, created_at, updated_at \
         FROM editor_drafts WHERE is_active = 1 AND owner = ?1 \
         ORDER BY updated_at DESC LIMIT 200"
    } else {
        "SELECT id, owner, name, source_image_id, width, height, thumbnail, created_at, updated_at \
         FROM editor_drafts WHERE is_active = 1 \
         ORDER BY updated_at DESC LIMIT 200"
    };
    let mut stmt = conn.prepare(sql).map_err(db_500)?;

    let map_row = |r: &rusqlite::Row<'_>| -> rusqlite::Result<Value> { Ok(_summary(&read_row(r)?)) };

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

    Ok(Json(json!({ "drafts": rows })).into_response())
}

/// `GET /api/editor-drafts/{draft_id}` — fetch one active draft, payload included.
///
/// ```python
/// d = db.query(EditorDraft).filter(
///     EditorDraft.id == draft_id, EditorDraft.is_active == True
/// ).first()
/// if not d or not _owns(d, user):
///     raise HTTPException(404, "Draft not found")
/// try:
///     payload = json.loads(d.payload) if d.payload else {}
/// except Exception:
///     payload = {}
/// return {**_summary(d), "payload": payload}
/// ```
async fn get_draft(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(draft_id): Path<String>,
) -> Result<Response, HttpException> {
    let user: Option<String> = user.map(|Extension(CurrentUser(u))| u);
    let conn = session_local()?;

    // Only active drafts are visible here (`is_active == True` in the filter).
    let row: Option<(DraftRow, Option<String>)> = conn
        .query_row(
            "SELECT id, owner, name, source_image_id, width, height, thumbnail, created_at, updated_at, payload \
             FROM editor_drafts WHERE id = ?1 AND is_active = 1",
            rusqlite::params![draft_id],
            |r| Ok((read_row(r)?, r.get::<_, Option<String>>(9)?)),
        )
        .optional()
        .map_err(db_500)?;

    // `if not d or not _owns(d, user): raise HTTPException(404, "Draft not found")`.
    let (d, payload_raw) = match row {
        Some(r) if _owns(&r.0, user.as_deref()) => r,
        _ => return Err(HttpException::new(404, "Draft not found")),
    };

    // `_load_payload(d.payload)`:
    //   payload = json.loads(raw) if raw else {}
    //   except Exception: return {}
    //   return payload if isinstance(payload, dict) else {}
    //
    // Three fall-through cases all become `{}`:
    //   1. NULL/empty raw string                   -> {}
    //   2. parse error (bare `except Exception`)   -> {}
    //   3. parsed value is not a JSON object/dict  -> {}
    let payload: Value = match payload_raw.as_deref() {
        Some(s) if !s.is_empty() => {
            let v: Value = serde_json::from_str(s).unwrap_or_else(|_| json!({}));
            if v.is_object() { v } else { json!({}) }
        }
        _ => json!({}),
    };

    // `{**_summary(d), "payload": payload}` — the summary fields, then `payload`
    // appended last (Python dict-unpack + new key preserves that order).
    let mut obj: Map<String, Value> = match _summary(&d) {
        Value::Object(m) => m,
        _ => Map::new(),
    };
    obj.insert("payload".to_string(), payload);

    Ok(Json(Value::Object(obj)).into_response())
}

/// `POST /api/editor-drafts` — create a new draft.
///
/// ```python
/// d = EditorDraft(
///     id=str(uuid.uuid4()), owner=user,
///     name=(body.name or "Untitled")[:200],
///     source_image_id=body.source_image_id, width=body.width, height=body.height,
///     payload=json.dumps(body.payload or {}), thumbnail=body.thumbnail,
/// )
/// db.add(d); db.commit(); db.refresh(d); return _summary(d)
/// ```
///
/// A DB failure -> rollback + `HTTPException(500, "Could not save draft")`.
async fn create_draft(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Json(body): Json<DraftCreate>,
) -> Result<Response, HttpException> {
    let user: Option<String> = user.map(|Extension(CurrentUser(u))| u);

    let id = uuid::Uuid::new_v4().to_string();
    // `name=(body.name or "Untitled")[:200]` — Python truthiness fallback, then the
    // first 200 *characters* (Python slices by code point), so truncate on a char
    // boundary, not a byte boundary.
    let name: String = name_or_untitled(body.name.as_deref())
        .chars()
        .take(200)
        .collect();
    // `payload=json.dumps(body.payload or {})` — Python truthiness: a falsy payload
    // (`{}`, `[]`, `null`, `0`, `""`, `false`) serializes as `{}`; anything truthy
    // serializes as itself.
    let payload_str = serde_json::to_string(&payload_or_empty(&body.payload))
        .unwrap_or_else(|_| "{}".to_string());
    // TimestampMixin: `created_at`/`updated_at` default to `datetime.utcnow` at flush.
    let now = crate::pydatetime::utcnow_naive_iso();

    let conn = session_local()?;
    // `db.add(d); db.commit()`. `is_active` defaults to True. A failure -> rollback +
    // `logger.warning(...)` + `HTTPException(500, "Could not save draft")`.
    if let Err(e) = conn.execute(
        "INSERT INTO editor_drafts \
           (id, owner, name, source_image_id, width, height, payload, thumbnail, is_active, created_at, updated_at) \
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, 1, ?9, ?9)",
        rusqlite::params![
            id,
            user,
            name,
            body.source_image_id,
            body.width,
            body.height,
            payload_str,
            body.thumbnail,
            now,
        ],
    ) {
        crate::pylog::warning(&format!("editor-draft create failed: {e}"));
        return Err(HttpException::new(500, "Could not save draft"));
    }

    // `db.refresh(d); return _summary(d)` — the refreshed row's fields are exactly the
    // values we just inserted, so build the summary from them.
    let d = DraftRow {
        id,
        owner: user,
        name: Some(name),
        source_image_id: body.source_image_id,
        width: body.width,
        height: body.height,
        thumbnail: body.thumbnail,
        created_at: Some(now.clone()),
        updated_at: Some(now),
    };
    Ok(Json(_summary(&d)).into_response())
}

/// `PUT /api/editor-drafts/{draft_id}` — partial update of an active draft.
///
/// ```python
/// d = db.query(EditorDraft).filter(
///     EditorDraft.id == draft_id, EditorDraft.is_active == True
/// ).first()
/// if not d or not _owns(d, user):
///     raise HTTPException(404, "Draft not found")
/// if body.name is not None:      d.name = body.name[:200]
/// if body.width is not None:     d.width = body.width
/// if body.height is not None:    d.height = body.height
/// if body.payload is not None:   d.payload = json.dumps(body.payload)
/// if body.thumbnail is not None: d.thumbnail = body.thumbnail
/// db.commit(); db.refresh(d); return _summary(d)
/// ```
///
/// A `HTTPException` propagates as-is; any other failure -> rollback +
/// `HTTPException(500, "Could not update draft")`.
async fn update_draft(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(draft_id): Path<String>,
    Json(body): Json<DraftUpdate>,
) -> Result<Response, HttpException> {
    let user: Option<String> = user.map(|Extension(CurrentUser(u))| u);
    // `db = SessionLocal()` sits **before** the `try`, so a connection-open failure
    // surfaces as a generic 500, not the route's `except`. The SELECT lookup, however,
    // is **inside** the `try` whose `except Exception` raises
    // `HTTPException(500, "Could not update draft")`, so a lookup failure carries that
    // detail (not a generic 500).
    let conn = session_local()?;

    // Only active drafts are updatable (`is_active == True` in the filter).
    let d: Option<DraftRow> = match conn
        .query_row(
            "SELECT id, owner, name, source_image_id, width, height, thumbnail, created_at, updated_at \
             FROM editor_drafts WHERE id = ?1 AND is_active = 1",
            rusqlite::params![draft_id],
            read_row,
        )
        .optional()
    {
        Ok(d) => d,
        Err(e) => {
            crate::pylog::warning(&format!("editor-draft update failed: {e}"));
            return Err(HttpException::new(500, "Could not update draft"));
        }
    };

    // `if not d or not _owns(d, user): raise HTTPException(404, "Draft not found")`.
    let mut d = match d {
        Some(d) if _owns(&d, user.as_deref()) => d,
        _ => return Err(HttpException::new(404, "Draft not found")),
    };

    // Apply each present field in the Python order. The new in-memory values feed the
    // final `_summary` (SQLAlchemy mutates `d`, then refreshes after commit).
    if let Some(n) = body.name {
        // `d.name = body.name[:200]` — first 200 characters (no truthiness fallback
        // here, unlike create; an empty string IS applied and stored).
        d.name = Some(n.chars().take(200).collect());
    }
    if let Some(w) = body.width {
        d.width = Some(w);
    }
    if let Some(h) = body.height {
        d.height = Some(h);
    }
    // `if body.payload is not None: d.payload = json.dumps(body.payload)`.
    let payload_str: Option<String> = body
        .payload
        .as_ref()
        .map(|p| serde_json::to_string(p).unwrap_or_else(|_| "{}".to_string()));
    if let Some(t) = body.thumbnail {
        d.thumbnail = Some(t);
    }

    // `db.commit()` bumps the TimestampMixin `updated_at` (onupdate=datetime.utcnow);
    // `db.refresh(d)` then reloads it, so `_summary` reports the new `updated_at`.
    let now = crate::pydatetime::utcnow_naive_iso();
    d.updated_at = Some(now.clone());

    // A single UPDATE writes the mutated columns plus the bumped `updated_at`. We
    // always write the (possibly unchanged) in-memory `d` fields so the row matches
    // what SQLAlchemy would flush, and only touch `payload` when it was provided.
    let exec = if let Some(ref p) = payload_str {
        conn.execute(
            "UPDATE editor_drafts \
               SET name = ?1, width = ?2, height = ?3, thumbnail = ?4, payload = ?5, updated_at = ?6 \
             WHERE id = ?7",
            rusqlite::params![d.name, d.width, d.height, d.thumbnail, p, now, draft_id],
        )
    } else {
        conn.execute(
            "UPDATE editor_drafts \
               SET name = ?1, width = ?2, height = ?3, thumbnail = ?4, updated_at = ?5 \
             WHERE id = ?6",
            rusqlite::params![d.name, d.width, d.height, d.thumbnail, now, draft_id],
        )
    };
    if let Err(e) = exec {
        crate::pylog::warning(&format!("editor-draft update failed: {e}"));
        return Err(HttpException::new(500, "Could not update draft"));
    }

    Ok(Json(_summary(&d)).into_response())
}

/// `DELETE /api/editor-drafts/{draft_id}` — soft-delete (set `is_active = False`).
///
/// ```python
/// d = db.query(EditorDraft).filter(EditorDraft.id == draft_id).first()
/// if not d or not _owns(d, user):
///     raise HTTPException(404, "Draft not found")
/// d.is_active = False
/// db.commit()
/// return {"status": "deleted", "id": draft_id}
/// ```
///
/// NOTE: unlike list/get/update, the delete lookup has **no** `is_active` filter —
/// it finds the row regardless of active state (deleting an already-deleted draft
/// just re-stamps it). A `HTTPException` propagates as-is; any other failure ->
/// rollback + `HTTPException(500, str(e))`.
async fn delete_draft(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(draft_id): Path<String>,
) -> Result<Response, HttpException> {
    let user: Option<String> = user.map(|Extension(CurrentUser(u))| u);
    // `db = SessionLocal()` sits **before** the `try`, so a connection-open failure
    // surfaces as a generic 500. The SELECT lookup, however, is **inside** the `try`
    // whose `except Exception as e` raises `HTTPException(500, str(e))`, so a lookup
    // failure carries the raw error string (== Python `str(e)`).
    let conn = session_local()?;

    // `d = db.query(EditorDraft).filter(EditorDraft.id == draft_id).first()` — no
    // is_active filter. We only need `owner` for the `_owns` check.
    let owner: Option<Option<String>> = match conn
        .query_row(
            "SELECT owner FROM editor_drafts WHERE id = ?1",
            rusqlite::params![draft_id],
            |r| r.get::<_, Option<String>>(0),
        )
        .optional()
    {
        Ok(o) => o,
        Err(e) => return Err(HttpException::new(500, e.to_string())),
    };

    // `if not d or not _owns(d, user): raise HTTPException(404, "Draft not found")`.
    let owner = match owner {
        Some(o) => o,
        None => return Err(HttpException::new(404, "Draft not found")),
    };
    let probe = DraftRow {
        id: draft_id.clone(),
        owner,
        name: None,
        source_image_id: None,
        width: None,
        height: None,
        thumbnail: None,
        created_at: None,
        updated_at: None,
    };
    if !_owns(&probe, user.as_deref()) {
        return Err(HttpException::new(404, "Draft not found"));
    }

    // `d.is_active = False; db.commit()`. A failure -> rollback + `HTTPException(500, str(e))`.
    if let Err(e) = conn.execute(
        "UPDATE editor_drafts SET is_active = 0 WHERE id = ?1",
        rusqlite::params![draft_id],
    ) {
        return Err(HttpException::new(500, e.to_string()));
    }

    Ok(Json(json!({ "status": "deleted", "id": draft_id })).into_response())
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// `body.payload or {}` — Python truthiness over an arbitrary JSON value: a falsy
/// value (`{}`, `[]`, `null`, `0`, `0.0`, `""`, `false`) becomes `{}`; anything
/// truthy passes through unchanged.
fn payload_or_empty(payload: &Value) -> Value {
    if json_is_truthy(payload) {
        payload.clone()
    } else {
        json!({})
    }
}

/// Python's `bool(value)` over a JSON value: `None`/`False`/`0`/`0.0`/`""`/`[]`/`{}`
/// are falsy; everything else is truthy.
fn json_is_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// Read the common `_summary` columns off a row (the first nine selected columns, in
/// the order `id, owner, name, source_image_id, width, height, thumbnail,
/// created_at, updated_at`).
fn read_row(r: &rusqlite::Row<'_>) -> rusqlite::Result<DraftRow> {
    Ok(DraftRow {
        id: r.get(0)?,
        owner: r.get(1)?,
        name: r.get(2)?,
        source_image_id: r.get(3)?,
        width: r.get(4)?,
        height: r.get(5)?,
        thumbnail: r.get(6)?,
        created_at: r.get(7)?,
        updated_at: r.get(8)?,
    })
}

/// `dt.isoformat() if dt else None` — render a stored SQLite datetime with Python's
/// `isoformat` (T separator, no `Z` suffix — `_summary` uses the plain form), or
/// `null` when absent/empty.
fn iso_or_null(stored: Option<&str>) -> Value {
    match stored.filter(|s| !s.is_empty()) {
        Some(s) => json!(crate::pydatetime::to_isoformat(s)),
        None => Value::Null,
    }
}

/// Open a DB connection (`SessionLocal()`), mapping a failure to a 500 (FastAPI's
/// default handler for an unhandled DB error).
fn session_local() -> Result<rusqlite::Connection, HttpException> {
    crate::core::database::session_local().map_err(db_500)
}

/// Map a `rusqlite::Error` to a 500 `HttpException`, the way an unhandled exception
/// inside a FastAPI handler surfaces.
fn db_500(e: rusqlite::Error) -> HttpException {
    crate::pylog::error(&format!("editor_draft_routes DB error: {e}"));
    HttpException::new(500, "Internal Server Error")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn row(owner: Option<&str>, name: Option<&str>) -> DraftRow {
        DraftRow {
            id: "d1".to_string(),
            owner: owner.map(str::to_string),
            name: name.map(str::to_string),
            source_image_id: None,
            width: None,
            height: None,
            thumbnail: None,
            created_at: None,
            updated_at: None,
        }
    }

    #[test]
    fn owns_none_user_owns_everything() {
        // `if user is None: return True`.
        assert!(_owns(&row(Some("alice"), None), None));
        assert!(_owns(&row(None, None), None));
        assert!(_owns(&row(Some(""), None), None));
    }

    #[test]
    fn owns_resolved_user_matches_owner() {
        // `(d.owner or None) == user`.
        assert!(_owns(&row(Some("alice"), None), Some("alice")));
        assert!(!_owns(&row(Some("bob"), None), Some("alice")));
        // `(d.owner or None)` coerces "" -> None, which never equals a non-empty user.
        assert!(!_owns(&row(Some(""), None), Some("alice")));
        assert!(!_owns(&row(None, None), Some("alice")));
    }

    #[test]
    fn name_or_untitled_truthiness() {
        // `d.name or "Untitled"`.
        assert_eq!(name_or_untitled(Some("My Draft")), "My Draft");
        assert_eq!(name_or_untitled(Some("")), "Untitled");
        assert_eq!(name_or_untitled(None), "Untitled");
    }

    #[test]
    fn summary_shape_and_key_order_omits_payload() {
        let d = DraftRow {
            id: "d1".to_string(),
            owner: Some("alice".to_string()),
            name: Some("Sketch".to_string()),
            source_image_id: Some("img-7".to_string()),
            width: Some(800),
            height: Some(600),
            thumbnail: Some("data:image/png;base64,AA".to_string()),
            created_at: Some("2026-06-01 12:30:00".to_string()),
            updated_at: Some("2026-06-01 12:45:00.500000".to_string()),
        };
        let s = _summary(&d);
        assert_eq!(
            s,
            json!({
                "id": "d1",
                "name": "Sketch",
                "source_image_id": "img-7",
                "width": 800,
                "height": 600,
                "thumbnail": "data:image/png;base64,AA",
                // plain isoformat (T separator), microseconds only when non-zero.
                "created_at": "2026-06-01T12:30:00",
                "updated_at": "2026-06-01T12:45:00.500000",
            })
        );
        // No `payload` key in the summary.
        assert!(s.get("payload").is_none());
        // Key order matches the Python dict literal exactly.
        let keys: Vec<&str> = s.as_object().unwrap().keys().map(|k| k.as_str()).collect();
        assert_eq!(
            keys,
            vec![
                "id",
                "name",
                "source_image_id",
                "width",
                "height",
                "thumbnail",
                "created_at",
                "updated_at"
            ]
        );
    }

    #[test]
    fn summary_nulls_missing_fields_and_defaults_name() {
        let d = row(None, None);
        let s = _summary(&d);
        assert_eq!(s["name"], json!("Untitled"));
        assert_eq!(s["source_image_id"], Value::Null);
        assert_eq!(s["width"], Value::Null);
        assert_eq!(s["height"], Value::Null);
        assert_eq!(s["thumbnail"], Value::Null);
        assert_eq!(s["created_at"], Value::Null);
        assert_eq!(s["updated_at"], Value::Null);
    }

    #[test]
    fn get_draft_payload_appended_last() {
        // `{**_summary(d), "payload": payload}` — payload is the final key.
        let d = row(Some("alice"), Some("X"));
        let mut obj = match _summary(&d) {
            Value::Object(m) => m,
            _ => Map::new(),
        };
        obj.insert("payload".to_string(), json!({"layers": []}));
        let keys: Vec<&str> = obj.keys().map(|k| k.as_str()).collect();
        assert_eq!(keys.last(), Some(&"payload"));
        assert_eq!(obj["payload"], json!({"layers": []}));
    }

    #[test]
    fn payload_or_empty_python_truthiness() {
        // `body.payload or {}`.
        assert_eq!(payload_or_empty(&json!({})), json!({}));
        assert_eq!(payload_or_empty(&json!([])), json!({}));
        assert_eq!(payload_or_empty(&Value::Null), json!({}));
        assert_eq!(payload_or_empty(&json!(0)), json!({}));
        assert_eq!(payload_or_empty(&json!("")), json!({}));
        assert_eq!(payload_or_empty(&json!(false)), json!({}));
        // Truthy values pass through.
        assert_eq!(
            payload_or_empty(&json!({"layers": [1, 2]})),
            json!({"layers": [1, 2]})
        );
        assert_eq!(payload_or_empty(&json!([1])), json!([1]));
        assert_eq!(payload_or_empty(&json!("x")), json!("x"));
        assert_eq!(payload_or_empty(&json!(1)), json!(1));
    }

    #[test]
    fn json_truthiness_matches_python_bool() {
        assert!(!json_is_truthy(&Value::Null));
        assert!(!json_is_truthy(&json!(false)));
        assert!(json_is_truthy(&json!(true)));
        assert!(!json_is_truthy(&json!(0)));
        assert!(!json_is_truthy(&json!(0.0)));
        assert!(json_is_truthy(&json!(1)));
        assert!(json_is_truthy(&json!(-1)));
        assert!(!json_is_truthy(&json!("")));
        assert!(json_is_truthy(&json!("a")));
        assert!(!json_is_truthy(&json!([])));
        assert!(json_is_truthy(&json!([0])));
        assert!(!json_is_truthy(&json!({})));
        assert!(json_is_truthy(&json!({"k": 1})));
    }

    #[test]
    fn name_truncates_to_200_chars_on_create() {
        // `(body.name or "Untitled")[:200]` — first 200 code points.
        let long: String = "x".repeat(250);
        let name: String = name_or_untitled(Some(&long)).chars().take(200).collect();
        assert_eq!(name.chars().count(), 200);
        // A multi-byte boundary is respected (we slice by char, not byte).
        let emoji: String = "🙂".repeat(250);
        let cut: String = name_or_untitled(Some(&emoji)).chars().take(200).collect();
        assert_eq!(cut.chars().count(), 200);
    }

    #[test]
    fn draft_create_requires_payload_only() {
        // Only `payload` is required; the rest default to None.
        let body: DraftCreate = serde_json::from_value(json!({"payload": {"a": 1}})).unwrap();
        assert!(body.name.is_none());
        assert!(body.source_image_id.is_none());
        assert!(body.width.is_none());
        assert!(body.height.is_none());
        assert!(body.thumbnail.is_none());
        assert_eq!(body.payload, json!({"a": 1}));

        // A missing `payload` is a deserialization error (a 422 in FastAPI).
        assert!(serde_json::from_value::<DraftCreate>(json!({"name": "x"})).is_err());

        // All fields populated.
        let full: DraftCreate = serde_json::from_value(json!({
            "name": "Sketch",
            "source_image_id": "img-1",
            "width": 800,
            "height": 600,
            "payload": {"layers": []},
            "thumbnail": "data:image/png;base64,AA"
        }))
        .unwrap();
        assert_eq!(full.name.as_deref(), Some("Sketch"));
        assert_eq!(full.source_image_id.as_deref(), Some("img-1"));
        assert_eq!(full.width, Some(800));
        assert_eq!(full.height, Some(600));
        assert_eq!(full.thumbnail.as_deref(), Some("data:image/png;base64,AA"));
    }

    #[test]
    fn draft_update_all_optional() {
        // Empty body is valid (no fields applied).
        let empty: DraftUpdate = serde_json::from_value(json!({})).unwrap();
        assert!(empty.name.is_none());
        assert!(empty.width.is_none());
        assert!(empty.height.is_none());
        assert!(empty.payload.is_none());
        assert!(empty.thumbnail.is_none());

        // `payload` here is optional (distinct from DraftCreate's required payload).
        let upd: DraftUpdate = serde_json::from_value(json!({
            "name": "Renamed",
            "payload": {"v": 2}
        }))
        .unwrap();
        assert_eq!(upd.name.as_deref(), Some("Renamed"));
        assert_eq!(upd.payload, Some(json!({"v": 2})));
        assert!(upd.width.is_none());

        // An explicit empty-object payload is `Some({})`, distinct from absent (None).
        let upd2: DraftUpdate = serde_json::from_value(json!({"payload": {}})).unwrap();
        assert_eq!(upd2.payload, Some(json!({})));
    }

    #[test]
    fn iso_or_null_renders_or_nulls() {
        assert_eq!(iso_or_null(None), Value::Null);
        assert_eq!(iso_or_null(Some("")), Value::Null);
        // plain isoformat — no Z suffix (distinct from signature_routes' iso_z_or_null).
        assert_eq!(
            iso_or_null(Some("2026-06-01 12:30:00")),
            json!("2026-06-01T12:30:00")
        );
        assert_eq!(
            iso_or_null(Some("2026-06-01 12:30:00.123456")),
            json!("2026-06-01T12:30:00.123456")
        );
    }

    #[test]
    fn get_draft_payload_parse_fallback() {
        // `_load_payload(raw)`:
        //   payload = json.loads(raw) if raw else {}
        //   except Exception: return {}
        //   return payload if isinstance(payload, dict) else {}
        let load_payload = |raw: Option<&str>| -> Value {
            match raw {
                Some(s) if !s.is_empty() => {
                    let v: Value = serde_json::from_str(s).unwrap_or_else(|_| json!({}));
                    if v.is_object() { v } else { json!({}) }
                }
                _ => json!({}),
            }
        };
        // NULL / empty -> {}
        assert_eq!(load_payload(None), json!({}));
        assert_eq!(load_payload(Some("")), json!({}));
        // Unparseable -> bare except -> {}
        assert_eq!(load_payload(Some("not json")), json!({}));
        // Valid object -> passes through.
        assert_eq!(load_payload(Some(r#"{"layers":[1]}"#)), json!({"layers": [1]}));
        assert_eq!(load_payload(Some(r#"{}"#)), json!({}));
        // Non-dict stored values -> isinstance(payload, dict) is False -> {}
        assert_eq!(load_payload(Some(r#"[1,2,3]"#)), json!({})); // array
        assert_eq!(load_payload(Some(r#""hello""#)), json!({})); // string
        assert_eq!(load_payload(Some(r#"42"#)), json!({}));      // number
        assert_eq!(load_payload(Some(r#"true"#)), json!({}));    // bool
        assert_eq!(load_payload(Some(r#"null"#)), json!({}));    // null
    }
}
